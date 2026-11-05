# Stage 3 规划器：目标 + 能力清单 → 多步计划（DAG-lite，顺序引用即无环）
# 铁律：计划必过硬校验；非法/解析失败一律返回 None，调用方回退模板路径
import json
import re

from ..db import sqlite3
from ..log import get_logger
from ..providers import router as provider
from ..runtime import registry

log = get_logger("planner")

MAX_STEPS = 6  # 失控闸一：步数上限（PRD §7.6）

# run_if 条件语法：$N.key（真值）/ == / != / contains 字面值（P1 条件步骤）
RUN_IF_RE = re.compile(r"^\$(\d+)\.(\w+)(?:\s*(==|!=|contains)\s*(.+))?$")

PLAN_PROMPT = """你是任务规划器。基于目标与可用能力清单，产出多步协作计划。
只输出 JSON：{{"steps":[{{"seq":1,"capability":"能力名","desc":"这步做什么",
 "input":{{"键":"字面值 或 $步骤序号.输出键"}},"output_key":"本步产出的键名",
 "fallback":["备用能力名"],"on_fail":"abort|skip|retry",
 "run_if":"$前步序号.输出键 == 字面值（可选，条件不满足自动跳过该步）",
 "verify":"通过标准（llm_verify 步必填）"}}]}}
规则：只使用清单内的能力；每步必须声明 output_key；input 引用格式为 $步骤序号.输出键，
且输出键必须是被引用步声明的 output_key（引用整份输出就用那步的 output_key）；不超过 {n} 步；
简单目标用单步；无法用清单能力完成的部分不要编造步骤。
on_fail 策略：核心产出步骤用 abort（失败终止）；富化/评分等辅助步骤用 skip（失败跳过不阻断）；
网络类不稳定步骤可用 retry（失败重试一次）。
run_if 用于"上游结果不同走不同分支"（如 $1.found == 否 则跳过深加工）；不需要条件就不填。
质量门：产出报告/综合结论/对外引用数据的步骤之后，必须跟一个 llm_verify 评审步
（capability 填 llm_verify，verify 写明基于用户目标的通过标准，input 引用该步产出）。
目标：{goal}
可用能力（含近期成功率）：{caps}"""

REPLAN_PROMPT = """你是任务重规划器。原计划执行中失败，基于已完成产出重新规划**剩余**步骤。
只输出 JSON（同计划格式）。
规则：新步骤 seq 从 {next_seq} 开始连续编号（避免与已完成步骤撞号）；
引用已完成步骤的产出处用 $原序号.输出键（见下方已完成清单）；只产出剩余工作；
失败根因要规避（换能力或换输入）；不超过 {n} 步；每步声明 output_key 与 on_fail。
原目标：{goal}
已完成步骤（seq: 描述 → 可引用键）：{done}
失败步骤：{failed}
原计划未执行的步骤（供参考，可改写）：{remaining}
可用能力（含近期成功率）：{caps}"""


def _cap_lines(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT c.name, c.descriptor_json, c.risk_level, s.ok, s.calls "
        "FROM capabilities c LEFT JOIN capability_stats s ON s.capability_id=c.id "
        "WHERE c.status='active'"
    ).fetchall()
    lines = []
    for r in rows:
        d = json.loads(r["descriptor_json"])
        rate = f"{r['ok']}/{r['calls']}" if r["calls"] else "新"
        lines.append(f"- {r['name']}：{d.get('purpose', '')}｜输入 {d.get('input', '')}"
                     f"｜成功率 {rate}")
    return "\n".join(lines)


def validate_plan(plan: dict, conn: sqlite3.Connection,
                  known_keys: dict[int, set] | None = None) -> list[str]:
    """硬校验：能力必须 active、引用必须指向更早步骤（或已知已完成步骤）、步数上限。
    known_keys 供重规划用：允许引用已完成步骤的输出键。返回步骤或空列表。"""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        return []
    seen_keys: dict[int, set] = dict(known_keys or {})
    checked = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            return []
        # llm_verify 是评审伪能力：不在注册表，但必须带评审标准
        if str(st.get("capability")) == "llm_verify":
            if not st.get("verify"):
                return []
        elif not registry.get_active(conn, str(st.get("capability", ""))):
            return []
        # 失败策略归一化（宽容）：LLM 常自创变体（如 skip_to_next）——
        # 含 skip 归 skip、含 retry 归 retry、其余归 abort；不为一个字段报废整份计划
        raw_fail = str(st.get("on_fail") or "abort")
        st["on_fail"] = ("skip" if "skip" in raw_fail
                         else "retry" if "retry" in raw_fail else "abort")
        # run_if 条件语法硬校验：非法条件在计划期就拒（运行时才炸会浪费整轮执行）
        if st.get("run_if") and not RUN_IF_RE.match(str(st["run_if"]).strip()):
            return []
        # 输入引用：$N.key 必须指向更早步骤且键名须为该步声明的 output_key
        for v in (st.get("input") or {}).values():
            m = re.match(r"^\$(\d+)\.(\w+)$", str(v))
            if m and (int(m.group(1)) not in seen_keys
                      or m.group(2) not in seen_keys[int(m.group(1))]):
                return []
        seq = st.get("seq") or (i + 1)
        checked.append(st)
        seen_keys[seq] = {str(st.get("output_key") or "output")}
    return checked


def make_plan(conn: sqlite3.Connection, goal: str,
               task_id: int | None = None) -> list[dict] | None:
    """LLM 生成计划 → 硬校验 → 合法步骤列表；任何失败返回 None（调用方走模板）"""
    if not provider.available():
        return None
    try:
        raw, used = provider.chat_with_usage(
            [{"role": "user", "content": goal}],
            system=PLAN_PROMPT.format(n=MAX_STEPS, goal=goal, caps=_cap_lines(conn)),
            max_tokens=1200,
        )
        plan = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception as e:
        log.info("计划生成失败（将回退模板）: %s", e)
        return None
    steps = validate_plan(plan, conn)
    if steps and task_id and used:
        from ..orchestrator.core import _add_usage
        _add_usage(conn, task_id, tokens=used)
    return steps or None


def replan(conn: sqlite3.Connection, goal: str, done: list[dict],
           failed: dict, remaining: list[dict], next_seq: int,
           task_id: int | None = None) -> list[dict] | None:
    """重规划：失败后基于已完成产出生成剩余步骤。新步 seq 从 next_seq 续编（防撞号），
    引用已完成步用原 seq。done 项形如 {"seq","desc","keys":[输出键]}；失败返回 None。"""
    if not provider.available():
        return None
    done_lines = [f"{d['seq']}: {d['desc']} → 可引用键 {','.join(d['keys'])}" for d in done]
    try:
        raw, used = provider.chat_with_usage(
            [{"role": "user", "content": goal}],
            system=REPLAN_PROMPT.format(
                n=MAX_STEPS, next_seq=next_seq, goal=goal,
                done="\n".join(done_lines) or "（无）",
                failed=f"步骤{failed.get('seq')}（{failed.get('desc')}）失败：{failed.get('error')}",
                remaining=json.dumps(
                    [{k: st.get(k) for k in ("capability", "desc", "input", "output_key")}
                     for st in remaining], ensure_ascii=False),
                caps=_cap_lines(conn)),
            max_tokens=1200,
        )
        plan = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        # 键名宽容：LLM 常把 steps 写成 plan（实测两次拒因之一）
        if isinstance(plan, dict) and not plan.get("steps") and isinstance(plan.get("plan"), list):
            plan = {"steps": plan["plan"]}
    except Exception as e:
        log.info("重规划失败: %s", e)
        return None
    known = {d["seq"]: set(d["keys"]) for d in done}
    steps = validate_plan(plan, conn, known_keys=known)
    if task_id and used:
        from ..orchestrator.core import _add_usage
        _add_usage(conn, task_id, tokens=used)
    if not steps:
        # 拒因留痕：没有它只能盲猜 LLM 违了哪条契约
        log.info("重规划被硬校验拒绝，原始计划：%s", json.dumps(plan, ensure_ascii=False)[:500])
    return steps or None
