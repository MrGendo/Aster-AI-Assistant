# 主板 v0：意图理解 → 简单规划 → 顺序执行 → 交付
# Phase 1 策略（spec D5）：三类任务模板兜底 + LLM 意图理解可选；
# 不做并行/评审/动态重规划（Stage 3），也不为展示智能而过度拆分（PRD §4.3）
import asyncio
import json
import re
import sqlite3
import time

from .. import tsm
from ..db import connect, tx
from ..log import get_logger
from ..memory import service as memory_svc
from ..providers import router as provider
from ..runtime import registry, runner

log = get_logger("orchestrator")

GITHUB_RE = re.compile(r"https?://github\.com/[\w.\-]+/[\w.\-]+/?", re.IGNORECASE)

# 意图 → (能力名, 附加输入抽取函数)
TEMPLATES = {
    "repo_analysis": "repo_analysis",
    "text_stats": "text_stats",
    "hot_news": "hot_news_digest",
}


def classify(goal: str) -> str:
    """意图分类：先用确定性规则（快且零成本），LLM 可用且规则未命中时再问模型"""
    if GITHUB_RE.search(goal):
        return "repo_analysis"
    if re.search(r"热点|日报", goal) and len(goal) > 12:
        return "hot_news"
    if re.search(r"统计|字数|行数|词频", goal) and len(goal) < 60:
        return "text_stats"
    if provider.available():
        try:
            answer = provider.chat(
                [{"role": "user", "content": goal}],
                system=(
                    "你是任务意图分类器。只输出以下之一，不要解释：\n"
                    "repo_analysis（分析 GitHub 仓库）/ text_stats（文本统计）/ chat（其他所有）"
                ),
                max_tokens=20,
            )
            label = answer.strip().lower()
            if label in ("repo_analysis", "text_stats", "chat"):
                return label
        except Exception as e:  # 模型失败不阻塞：回落 chat
            log.warning("意图分类模型调用失败，回落 chat: %s", e)
    return "chat"


def create_task(conn: sqlite3.Connection, goal: str, project_id=None) -> int:
    now = int(time.time())
    intent = classify(goal)
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO tasks(goal, intent, status, project_id, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (goal.strip(), intent, tsm.PENDING, project_id, now, now),
        )
        task_id = cur.lastrowid
        # 规划：Phase 1 即固定单步（能力型）或零步（chat 型）
        if intent in TEMPLATES:
            conn.execute(
                "INSERT INTO task_steps(task_id, seq, step_desc, status) VALUES(?,?,?,?)",
                (task_id, 1, f"调用能力 {TEMPLATES[intent]}", "pending"),
            )
    return task_id


def request_cancel(conn: sqlite3.Connection, task_id: int) -> bool:
    """PRD §4.4：只置请求标志，状态由执行循环在实际停止后落 cancelled"""
    with tx(conn):
        cur = conn.execute(
            "UPDATE tasks SET cancel_requested=1, updated_at=? WHERE id=? AND status IN "
            "(?,?,?,?)",
            (int(time.time()), task_id, tsm.PENDING, tsm.PLANNING, tsm.EXECUTING, tsm.VERIFYING),
        )
    return cur.rowcount > 0


def _set_status(conn, task_id: int, nxt: str) -> None:
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    tsm.transition(row["status"], nxt)  # 非法迁移直接抛错，暴露调用方 bug
    with tx(conn):
        conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                     (nxt, int(time.time()), task_id))


async def execute(task_id: int) -> None:
    """任务执行主循环（后台任务）。每一步之间检查取消请求与审批状态。"""
    conn = connect()
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return
        intent = task["intent"]

        # chat 型：无步骤，直接给交付（Phase 1 的对话即任务）
        if intent not in TEMPLATES:
            _set_status(conn, task_id, tsm.PLANNING)  # 仍走规划态：状态语义对 UI 一致
            _set_status(conn, task_id, tsm.EXECUTING)
            if conn.execute("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()[0]:
                _set_status(conn, task_id, tsm.CANCELLED)
                return
            goal = task["goal"]
            injection = memory_svc.context_injection(conn, goal)
            try:
                if provider.available():
                    answer = provider.chat(
                        [{"role": "user", "content": goal}],
                        system=("你是 Aster，一个务实、诚实的任务助手。"
                                + ("无法完成任务时明确说明，不伪装成功。" )
                                + (f"\n\n{injection}" if injection else "")),
                    )
                else:
                    answer = (
                        "LLM 尚未配置：请在设置页填写 GLM/Anthropic 密钥后重试。"
                        "（提示：可直接分析 GitHub 仓库链接，该能力不依赖模型）"
                    )
                # chat 的验证即"有非空回答"（轻量但语义完整：不验证不算成功）
                _set_status(conn, task_id, tsm.VERIFYING)
                if answer and answer.strip():
                    _finish(conn, task_id, summary=answer)
                else:
                    _fail(conn, task_id, "模型返回为空，任务未通过验证")
            except Exception as e:
                _fail(conn, task_id, f"对话失败：{e}")
            return

        # 能力型任务
        _set_status(conn, task_id, tsm.PLANNING)
        _set_status(conn, task_id, tsm.EXECUTING)
        cap_name = TEMPLATES[intent]
        cap = registry.get_active(conn, cap_name)
        step = conn.execute(
            "SELECT * FROM task_steps WHERE task_id=? ORDER BY seq LIMIT 1", (task_id,)
        ).fetchone()
        if not cap:
            _fail(conn, task_id, f"能力 {cap_name} 不可用（不存在或未启用）")
            return

        payload = _build_payload(intent, task["goal"])
        if payload.get("_empty"):
            # 没有条目 → 尝试用 news_fetch 抓取（两能力串联的最小协作：抓原料→筛选排序）
            # 用户消息里的方向词（如"ai agent方向的"）作为抓取检索词
            fetch_cap = registry.get_active(conn, "news_fetch")
            if not fetch_cap:
                _fail(conn, task_id,
                      "没有从你的消息中提取到热点条目，且热点抓取能力不可用。请直接粘贴条目。")
                return
            query = re.sub(r"^(帮我|请)?(把|将|用)?.{0,12}?(热点|新闻)?(整理|汇总|生成|做成)(成)?(一份)?(今日|今天|行业|AI)?(热点)?日报", "", task["goal"]).strip("，,。：: ")
            # HN 是英文源：方向词交 LLM 提取纯主题检索词（不带任务语义）
            topic_cn = query or "科技热点"
            if query and provider.available():
                try:
                    q = provider.chat(
                        [{"role": "user", "content": task["goal"]}],
                        system="提取用户想了解的主题的英文检索词（1-4个单词，只要主题本身，"
                               "不要任务动词如 digest/generator/news/summarize）。只输出检索词。",
                        max_tokens=20,
                    ).strip().strip('"`\n')
                    if 2 < len(q) < 60:
                        query = q
                except Exception:
                    pass
            fetch_res = await asyncio.to_thread(
                runner.run_capability, conn, fetch_cap, task_id, None,
                {"query": query or None, "limit": 30}, timeout_s=60,
            )
            items = fetch_res.output.get("items") if fetch_res.ok else None
            if not items:
                # 定向检索零命中 → 回退抓 HN 首页（泛热点），仍失败才认输
                fetch_res = await asyncio.to_thread(
                    runner.run_capability, conn, fetch_cap, task_id, None,
                    {"query": None, "limit": 30}, timeout_s=60,
                )
                items = fetch_res.output.get("items") if fetch_res.ok else None
                query = None
            if items:
                payload = {"topic": topic_cn, "top_n": 5, "items": items}
            else:
                _fail(conn, task_id, f"条目抓取失败：{fetch_res.error or '无结果'}。也可直接粘贴新闻条目重试。")
                return
        with tx(conn):
            conn.execute(
                "UPDATE task_steps SET status='running', input_json=?, started_at=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), int(time.time()), step["id"]),
            )
        result = await asyncio.to_thread(
            runner.run_capability, conn, cap, task_id, step["id"], payload
        )

        # 步骤收尾：被网关拦截（待审批/拒绝）→ 等待用户或失败，不自动重试
        with tx(conn):
            conn.execute(
                "UPDATE task_steps SET status=?, output_ref=?, error=?, finished_at=? WHERE id=?",
                ("done" if result.ok else "failed",
                 str(result.output.get("_output_path", "")) or None,
                 result.error or None, int(time.time()), step["id"]),
            )
        if result.ok:
            # Phase 1 验证：结构化输出的关键字段齐全才算过验证（PRD：未验证不显示成功）
            ok_verify = _verify(intent, result.output)
            _set_status(conn, task_id, tsm.VERIFYING)
            if ok_verify:
                summary = _deliver(intent, result.output)
                _finish(conn, task_id, summary=summary, ref=str(result.output.get("_output_path", "")))
            else:
                _fail(conn, task_id, "结果未通过结构校验（关键字段缺失），已保留原始输出供查看")
        elif result.pending_approval:
            # PRD §4.4：等审批不是失败——任务转"等待用户确认"，批准后重试即可续跑
            _set_status(conn, task_id, tsm.AWAITING_USER)
            with tx(conn):
                conn.execute("UPDATE tasks SET error=?, updated_at=? WHERE id=?",
                             (result.error, int(time.time()), task_id))
        else:
            _fail(conn, task_id, result.error)
    except Exception as e:
        log.exception("任务 %s 执行异常", task_id)
        try:
            _fail(conn, task_id, f"系统异常：{e}")
        except Exception:
            pass
    finally:
        conn.close()


def _build_payload(intent: str, goal: str) -> dict:
    if intent == "repo_analysis":
        return {"repo_url": GITHUB_RE.search(goal).group(0).rstrip("/")}
    if intent == "hot_news":
        # 用 LLM 把用户粘贴的热点文本结构化为条目；没有可提取内容则如实失败（不编新闻）
        body = re.sub(r"^.{0,40}?[:：]", "", goal, count=1).strip() or goal
        if provider.available():
            try:
                raw = provider.chat(
                    [{"role": "user", "content": body}],
                    system="从文本中提取新闻/热点条目，输出 JSON：{\"items\":[{\"title\":\"\",\"source\":\"\",\"category\":\"\",\"score\":热度数字0-100,\"age_hours\":发布距今小时数}]}。"
                           "score/age_hours 缺失时按内容合理估计。若文本中没有任何具体条目，输出 {\"items\":[]}。只输出 JSON。",
                    max_tokens=1500,
                )
                items = json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("items", [])
                if items:
                    return {"topic": "行业热点", "top_n": 5, "items": items}
            except Exception as e:
                log.warning("热点条目结构化失败: %s", e)
        return {"_empty": True}  # 标记无条目：由执行层给出诚实失败说明
    if intent == "text_stats":
        # 去掉指令词，取余下文本为对象
        text = re.sub(r"^(帮我|请)?(统计|分析)?(一下)?(这段文本|这段话|文本|字数|行数|词频)?[:：，,\s]*", "", goal).strip()
        return {"text": text or goal}
    return {}


def _verify(intent: str, output: dict) -> bool:
    """轻量结构校验：旗舰能力要求证据分级的两侧字段都存在"""
    if intent == "repo_analysis":
        return "verified" in output and "inferred" in output
    if intent == "text_stats":
        return "chars" in output
    if intent == "hot_news":
        return "digest_markdown" in output and "top_items" in output
    return True


def _deliver(intent: str, output: dict) -> str:
    """交付摘要：结果优先、直给结论（PRD §4.5）"""
    if intent == "hot_news":
        return (f"{output.get('digest_markdown', '')}\n"
                f"（输入 {output.get('total_input')} 条，去重移除 {output.get('removed_duplicates')} 条同题报道）")
    if intent == "repo_analysis":
        v = output.get("verified", {})
        inf = output.get("inferred", {})
        lines = [
            f"项目定位：{inf.get('positioning', '（未识别）')}",
            f"语言构成：{v.get('languages', '')}",
            f"测试情况：{v.get('tests', '')}；许可证：{v.get('license', '未检出')}",
            f"最近提交：{v.get('last_commit', '')}",
            f"规模：约 {v.get('total_files', '?')} 个文件",
        ]
        risks = output.get("risks") or []
        if risks:
            lines.append("风险与限制：" + "；".join(risks[:3]))
        lines.append("（完整报告见任务详情，事实与推测已分级标注）")
        return "\n".join(lines)
    if intent == "text_stats":
        return (f"字符 {output.get('chars')}，行数 {output.get('lines')}，"
                f"分词 {output.get('tokens')} 个，高频词：{output.get('top_tokens', '')}")
    return str(output)[:500]


def _finish(conn, task_id: int, summary: str, ref: str = "") -> None:
    _set_status(conn, task_id, tsm.COMPLETED)
    with tx(conn):
        conn.execute(
            "UPDATE tasks SET result_summary=?, result_ref=?, updated_at=? WHERE id=?",
            (summary, ref or None, int(time.time()), task_id),
        )


def _fail(conn, task_id: int, error: str) -> None:
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row["status"] in tsm.TERMINAL:
        return
    with tx(conn):
        conn.execute(
            "UPDATE tasks SET status=?, error=?, updated_at=? WHERE id=?",
            (tsm.FAILED, error, int(time.time()), task_id),
        )
