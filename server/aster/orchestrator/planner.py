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

PLAN_PROMPT = """你是任务规划器。基于目标与可用能力清单，产出多步协作计划。
只输出 JSON：{{"steps":[{{"seq":1,"capability":"能力名","desc":"这步做什么",
 "input":{{"键":"字面值 或 $步骤序号.输出键"}},"output_key":"本步产出的键名",
 "fallback":["备用能力名"],"verify":"通过标准（可选，需要检查输出质量时填）}}]}}
规则：只使用清单内的能力；每步必须声明 output_key；input 引用格式为 $步骤序号.输出键，
且输出键必须是被引用步声明的 output_key（引用整份输出就用那步的 output_key）；不超过 {n} 步；
简单目标用单步；无法用清单能力完成的部分不要编造步骤。
目标：{goal}
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


def validate_plan(plan: dict, conn: sqlite3.Connection) -> list[str]:
    """硬校验：能力必须 active、引用必须指向更早步骤、步数上限。返回步骤或空列表。"""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        return []
    seen_keys: dict[int, set] = {}
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


def make_plan(conn: sqlite3.Connection, goal: str) -> list[dict] | None:
    """LLM 生成计划 → 硬校验 → 合法步骤列表；任何失败返回 None（调用方走模板）"""
    if not provider.available():
        return None
    try:
        raw = provider.chat(
            [{"role": "user", "content": goal}],
            system=PLAN_PROMPT.format(n=MAX_STEPS, goal=goal, caps=_cap_lines(conn)),
            max_tokens=1200,
        )
        plan = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception as e:
        log.info("计划生成失败（将回退模板）: %s", e)
        return None
    steps = validate_plan(plan, conn)
    return steps or None
