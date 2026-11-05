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
# planner 延迟导入（避免与 core 循环依赖）
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
    "mem0_add": "mem0_memory",
    "mem0_search": "mem0_memory",
    "repo_quality": "repo_quality",
    "browser": "browser_browse",
    "deep_research": "deep_research",
}


def classify(goal: str) -> str:
    """意图分类：先用确定性规则（快且零成本），LLM 可用且规则未命中时再问模型"""
    # 规划执行前缀绝对优先：显式多步协作不许被单能力规则劫持（实测教训）
    if goal.startswith("规划执行：") or goal.startswith("规划执行:"):
        return "planned"
    # 复合目标连接词：单能力规则遇到它们让路给规划器（"全面调研"已归 deep_research 规则）
    COMPOSITE = bool(re.search(r"同时|然后|最后|并且|综合|分别", goal))
    if GITHUB_RE.search(goal) and re.search(r"质量|评分", goal) and not COMPOSITE:
        return "repo_quality"
    if re.match(r"^(记住|记一下)", goal):
        return "mem0_add"
    if re.match(r"^(回忆|你记得|查一下记忆)", goal):
        return "mem0_search"
    # 待办：前缀强意图（"待办：X"/"添加待办 X"）；列出用固定短语。
    # 完成勾选故意不做对话路由——口语指代易错位，UI 点击才可靠
    if (re.match(r"^(待办|todo)[:：]", goal, re.IGNORECASE)
            or re.match(r"^(添加|记个|记一个|新增|加一个)(一个)?待办", goal)):
        return "todo_add"
    if re.search(r"看看待办|待办列表|我的待办|列出待办", goal):
        return "todo_list"
    # 深度调研：自主联网调研主题出报告——该能力自带研究规划，不拆多步路径。
    # "全面调研"从复合连接词移出归本规则（本身就是单能力强意图）；带仓库链接/顺序复合仍让路
    if (re.search(r"深度调研|深入调研|全面调研", goal)
            and not GITHUB_RE.search(goal)
            and not re.search(r"同时|然后|再|最后|并且|综合|分别", goal)):
        return "deep_research"
    URL_RE = re.compile(r"https?://\S+")
    if URL_RE.search(goal) and re.search(r"浏览|打开|截图|看看这个网页", goal):
        return "browser"
    if GITHUB_RE.search(goal) and re.search(r"摘要|总结|翻译|写成|点评", goal):
        return "planned"  # 复合目标（分析+加工）→ 多步协作路径
    if GITHUB_RE.search(goal):
        return "repo_analysis"
    # 报告意图优先于热点：含"报告"的热点素材应产报告而非日报（实测路由教训）
    if re.search(r"(调研|生成|输出|写).{0,6}报告|报告[:：]", goal) and not GITHUB_RE.search(goal):
        return "report"
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
    """PRD §4.4：只置请求标志，状态由执行循环在实际停止后落 cancelled。
    awaiting_user 例外——没有执行循环在跑，置了标志也没人消费，直接落终态 cancelled
    （停在审批口的任务用户拒绝等待时只能干看着，实测踩到）"""
    now = int(time.time())
    with tx(conn):
        cur = conn.execute(
            "UPDATE tasks SET status='cancelled', updated_at=? WHERE id=? AND status=?",
            (now, task_id, tsm.AWAITING_USER),
        )
        if cur.rowcount:
            return True
        cur = conn.execute(
            "UPDATE tasks SET cancel_requested=1, updated_at=? WHERE id=? AND status IN "
            "(?,?,?,?)",
            (now, task_id, tsm.PENDING, tsm.PLANNING, tsm.EXECUTING, tsm.VERIFYING),
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

        # 计划路径：LLM 产出多步协作计划；规划失败回退 chat（任务不因规划器而失败）
        if intent == "planned":
            ok = await _execute_planned(conn, task)
            if ok is not None:
                return
            intent = "chat"  # 回退：按对话路径交付
            with tx(conn):
                conn.execute("UPDATE tasks SET intent='chat' WHERE id=?", (task_id,))
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

        # 报告型：GLM 按结构化模板生成报告并落盘 data/reports/（进程内伪能力）
        if intent == "report":
            _set_status(conn, task_id, tsm.PLANNING)
            _set_status(conn, task_id, tsm.EXECUTING)
            materials = re.sub(r"^(帮我|请)?(用|把|将|根据|基于)?(.{0,12}?)?(调研|生成|输出|写)(一份|一个)?(.{0,8}?)报告[:：，,\s]*", "", task["goal"]).strip() or task["goal"]
            try:
                md = _make_report(task["goal"], materials)
                from ..config import DATA_DIR
                rp = DATA_DIR / "reports"
                rp.mkdir(exist_ok=True)
                path = rp / f"report_task{task_id}_{int(time.time())}.md"
                path.write_text(md, encoding="utf-8")
                _set_status(conn, task_id, tsm.VERIFYING)
                if len(md) > 200:
                    _finish(conn, task_id, summary=f"{md[:1200]}\n\n（完整报告已存 {path}）",
                            ref=str(path))
                else:
                    _fail(conn, task_id, "报告生成过短，未通过验证")
            except Exception as e:
                _fail(conn, task_id, f"报告生成失败：{e}")
            return

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
        # 没有条目 → 尝试用 news_fetch 抓取（两能力串联的最小协作：抓原料→筛选排序）
        # 用户消息里的方向词（如"ai agent方向的"）作为抓取检索词
        if payload.get("_empty"):
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
                runner.run_capability, fetch_cap, task_id, None,
                {"query": query or None, "limit": 30}, timeout_s=60,
            )
            items = fetch_res.output.get("items") if fetch_res.ok else None
            if not items:
                # 定向检索零命中 → 回退抓 HN 首页（泛热点），仍失败才认输
                fetch_res = await asyncio.to_thread(
                    runner.run_capability, fetch_cap, task_id, None,
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
            runner.run_capability, cap, task_id, step["id"], payload,
            # 深度调研自带多轮检索+写作，普通能力 5 分钟上限对它不够
            timeout_s=900 if intent == "deep_research" else 300,
        )
        # 结果落盘路径回填任务，详情页/答案卡可加载完整 JSON（事件卡渲染依赖它）
        with tx(conn):
            conn.execute("UPDATE tasks SET result_ref=?, updated_at=? WHERE id=?",
                         (result.output_path or None, int(time.time()), task_id))

        # 步骤收尾：被网关拦截（待审批/拒绝）→ 等待用户或失败，不自动重试
        with tx(conn):
            conn.execute(
                "UPDATE task_steps SET status=?, output_ref=?, error=?, finished_at=? WHERE id=?",
                ("done" if result.ok else "failed",
                 str(result.output.get("_output_path", "")) or None,
                 result.error or None, int(time.time()), step["id"]),
            )
        if result.ok:
            # 热点富化：先抓原文（v2，失败静默降级标题版），再生成中文标题与摘要
            if intent == "hot_news":
                result.output = await _prefetch_pages(conn, task_id, result.output)
                result.output = _enrich_hot_news(result.output, result.output_path)
            # Phase 1 验证：结构化输出的关键字段齐全才算过验证（PRD：未验证不显示成功）
            ok_verify = _verify(intent, result.output)
            _set_status(conn, task_id, tsm.VERIFYING)
            if ok_verify:
                summary = _deliver(intent, result.output)
                # ref 用 runner 的真实落盘路径（_finish 不能再用旧空值覆盖掉它）；
                # 深度调研的下载件是 markdown 报告本体而非 JSON 明细
                ref = (result.output.get("report_path")
                       if intent == "deep_research" else result.output_path) or ""
                _finish(conn, task_id, summary=summary, ref=ref)
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


async def _execute_planned(conn, task) -> bool | None:
    """计划路径执行：规划 → 建步 → 顺序执行（占位符数据流/接管/评审/统计）。
    返回 True=已终态收尾，False=规划失败需回退 chat，None 不应发生。"""
    from . import planner as planner_mod

    goal = task["goal"].removeprefix("规划执行：").removeprefix("规划执行:")
    _set_status(conn, task["id"], tsm.PLANNING)
    steps = await asyncio.to_thread(planner_mod.make_plan, conn, goal)
    if not steps:
        log.info("任务 %s 规划失败，回退对话路径", task["id"])
        return False

    # 计划落库（含依赖与输出键，UI/审计可查）
    import time as _t
    with tx(conn):
        conn.execute("UPDATE tasks SET plan_json=?, updated_at=? WHERE id=?",
                     (json.dumps(steps, ensure_ascii=False), int(_t.time()), task["id"]))
        for i, st in enumerate(steps, 1):
            # 嵌入感知的依赖提取：$N 可能出现在值的任意位置（与执行层替换同源的问题）
            deps = sorted({int(n) for n in
                           re.findall(r"\$(\d+)\.",
                                      json.dumps(st.get("input") or {}, ensure_ascii=False))})
            conn.execute(
                "INSERT INTO task_steps(task_id, seq, step_desc, status, output_key, depends_on, "
                "input_json) VALUES(?,?,?,?,?,?,?)",
                (task["id"], i,
                 f"{st.get('desc', '')}［能力 {st['capability']}{'←依赖步' + str(deps) if deps else ''}］",
                 "pending", st.get("output_key") or "output",
                 json.dumps(deps), json.dumps(st.get("input", {}), ensure_ascii=False)),
            )
    _set_status(conn, task["id"], tsm.EXECUTING)

    PARALLEL_CAP = 3  # 并发上限：单用户机器的保守值（防协作失控闸之一）

    outputs: dict[int, dict] = {}
    step_rows = conn.execute(
        "SELECT * FROM task_steps WHERE task_id=? ORDER BY seq", (task["id"],)
    ).fetchall()
    row_by_seq = {r["seq"]: r for r in step_rows}
    deadline = time.time() + 1800  # 失控闸二：总超时 30 分钟

    def _deps_done(st: dict) -> bool:
        return all(int(m.group(1)) in outputs
                   for v in (st.get("input") or {}).values()
                   if (m := re.match(r"^\$(\d+)\.", str(v))))

    async def _run_one(st: dict) -> "runner.RunResult":
        """单个步骤执行（能力运行自开连接，与并行兄弟步骤互不干扰）"""
        row = row_by_seq[st["seq"]]

        def _resolve(m):
            # 嵌入式引用替换（实测教训：规划器常把 $1.key 写进句子中间而非整值）
            n, key = int(m.group(1)), m.group(2)
            val = outputs.get(n, {}).get(key)
            if isinstance(val, dict) and key in val:
                val = val[key]
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            return str(val) if val is not None else "（上游输出缺失）"

        payload = {}
        for k, v in (st.get("input") or {}).items():
            payload[k] = re.sub(r"\$(\d+)\.(\w+)", _resolve, str(v)) if isinstance(v, str) \
                else (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        if st["capability"] == "llm_verify":
            return await asyncio.to_thread(_llm_verify, st.get("verify", "输出质量合格"),
                                           goal, outputs)
        if st["capability"] == "llm_task":
            return await asyncio.to_thread(_llm_task, payload)
        result = None
        for cand in [st["capability"]] + list(st.get("fallback") or []):
            cap = registry.get_active(conn, cand)
            if not cap:
                continue
            result = await asyncio.to_thread(
                runner.run_capability, cap, task["id"], row["id"], payload)
            if result.ok or result.pending_approval:
                return result
            log.info("步骤 %s 能力 %s 失败，尝试接管", st["seq"], cand)
        return result or runner.RunResult(False, 0, {}, "无可用能力（含 fallback）")

    remaining = {st["seq"]: st for st in steps}
    while remaining:
        if time.time() > deadline:
            _fail(conn, task["id"], "任务超过总时限（30 分钟）被中止")
            return True
        if conn.execute("SELECT cancel_requested FROM tasks WHERE id=?",
                        (task["id"],)).fetchone()[0]:
            _set_status(conn, task["id"], tsm.CANCELLED)
            return True
        # 就绪集：依赖全部完成的步骤，按并发上限切片并行
        ready = [st for st in remaining.values() if _deps_done(st)]
        if not ready:
            _fail(conn, task["id"], "计划存在无法满足的依赖（疑似环）")
            return True
        batch = ready[:PARALLEL_CAP]
        results = await asyncio.gather(*[_run_one(st) for st in batch])
        # fail-fast：批次内任一失败/挂起即终止（先落同批成功者的输出再判断）
        stopped = None
        for st, result in zip(batch, results):
            row = row_by_seq[st["seq"]]
            if result and result.ok:
                outputs[st["seq"]] = {st.get("output_key") or "output": result.output}
                with tx(conn):
                    conn.execute("UPDATE task_steps SET status='done', output_ref=?, "
                                 "finished_at=? WHERE id=?",
                                 (str(result.output)[:2000], int(time.time()), row["id"]))
            elif result and result.pending_approval:
                stopped = ("awaiting", st, result)
                break
            else:
                stopped = ("failed", st, result)
                break
        if stopped:
            kind, st, result = stopped
            row = row_by_seq[st["seq"]]
            if kind == "awaiting":
                _set_status(conn, task["id"], tsm.AWAITING_USER)
                with tx(conn):
                    conn.execute("UPDATE tasks SET error=?, updated_at=? WHERE id=?",
                                 (result.error, int(time.time()), task["id"]))
                    conn.execute("UPDATE task_steps SET status='pending', error=? WHERE id=?",
                                 (result.error, row["id"]))
            else:
                reason = result.error if result else "无可用能力"
                with tx(conn):
                    conn.execute("UPDATE task_steps SET status='failed', error=?, finished_at=? "
                                 "WHERE id=?", (reason, int(time.time()), row["id"]))
                _fail(conn, task["id"], f"步骤 {st['seq']} 失败：{reason}")
            return True
        for st in batch:
            remaining.pop(st["seq"])

    # 交付：最后一步输出即交付物；评审步通过与否已在循环内判定
    _set_status(conn, task["id"], tsm.VERIFYING)
    last = outputs[max(outputs)]
    summary = last.get("digest_markdown") or last.get("text") or last.get("summary") \
        or last.get("result") or json.dumps(last, ensure_ascii=False)[:600]
    _finish(conn, task["id"], summary=summary)
    return True


async def _prefetch_pages(conn, task_id: int, output: dict) -> dict:
    """抓取榜单前 3 条的原文（web_fetch 能力），供摘要基于事实而非仅标题。
    任何失败静默跳过——原文是增强，不是依赖。"""
    items = output.get("top_items") or []
    targets = [it for it in items[:3] if it.get("url")]
    if not targets:
        return output
    cap = registry.get_active(conn, "web_fetch")
    if not cap:
        return output
    try:
        res = await asyncio.to_thread(
            runner.run_capability, cap, task_id, None,
            {"urls": [it["url"] for it in targets]}, timeout_s=60,
        )
        if not res.ok:
            return output
        by_url = {p.get("url"): p for p in res.output.get("pages", []) if p.get("ok")}
        for it in items:
            page = by_url.get(it.get("url"))
            if page:
                it["page_text"] = (page.get("text") or "")[:1500]
    except Exception as e:
        log.info("原文抓取跳过: %s", e)
    return output


def _make_report(goal: str, materials: str) -> str:
    """结构化报告生成：固定模板约束（标题/日期/摘要/正文/结论/来源分级/未验证事项）"""
    md = provider.chat(
        [{"role": "user", "content": f"原始请求：{goal}\n\n素材：\n{materials[:8000]}"}],
        system="你是分析报告撰写人。基于给定素材输出 Markdown 报告，结构固定："
               "# 标题\n> 日期与主题一行\n## 摘要（3句内）\n## 正文（2-4个小节，"
               "只使用素材中的事实，标注【素材】）\n## 结论与建议\n"
               "## 来源与证据分级（哪些来自素材事实、哪些是推断）\n## 未验证事项。\n"
               "素材没有的内容写明'素材未提供'，禁止编造数据。直接输出报告正文。",
        max_tokens=3000,
    )
    return md.strip()


def _enrich_hot_news(output: dict, output_path: str = "") -> dict:
    """热点榜单富化：逐条生成中文标题（翻译）+ 一句话摘要（≤60字）。
    成功后回写落盘文件——详情页/答案卡从文件加载，不回写则 UI 拿不到富化结果。
    失败静默降级回原始榜单——富化是增强不是前置条件。"""
    items = output.get("top_items") or []
    if not items or not provider.available():
        return output
    try:
        raw = provider.chat(
            [{"role": "user", "content": json.dumps(
                [{"idx": i, "title": it.get("title", ""), "source": it.get("source", ""),
                  "page_text": it.get("page_text", "")}
                 for i, it in enumerate(items)], ensure_ascii=False)}],
            system="为每个条目输出 JSON：{\"items\":[{\"idx\":0,\"title_cn\":\"标题的忠实中文翻译\","
                   "\"summary_cn\":\"一句话中文摘要≤60字\",\"based_on\":\"原文|标题\"}]}。"
                   "规则：给了 page_text 的条目摘要必须基于原文事实并在 based_on 标“原文”；"
                   "没有原文的只依据标题，标“标题”，不得编造。只输出 JSON。",
            max_tokens=1500,
        )
        enriched = json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("items", [])
        for e in enriched:
            i = e.get("idx")
            if isinstance(i, int) and 0 <= i < len(items):
                if e.get("title_cn"):
                    items[i]["title_cn"] = e["title_cn"]
                if e.get("summary_cn"):
                    items[i]["summary_cn"] = e["summary_cn"]
                    items[i]["summary_based"] = e.get("based_on", "标题")
        # 榜单 markdown 同步用中文标题重建，保持两处一致
        lines = [f"# {output.get('topic', '热点')} 日报"]
        for n, it in enumerate(items, 1):
            title = it.get("title_cn") or it.get("title", "")
            extra = f"——{it['summary_cn']}" if it.get("summary_cn") else ""
            lines.append(f"{n}. {title}（{it.get('source', '')}，热度 {it.get('weighted_score', 0)}）{extra}")
        output["digest_markdown"] = "\n".join(lines)
        output["enriched"] = True
        if output_path:
            from pathlib import Path
            Path(output_path).write_text(
                json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.info("热点富化失败，降级原榜单: %s", e)
    return output


def _llm_task(payload: dict) -> "runner.RunResult":
    """通用文本加工步：prompt 作系统指令，text 作待加工内容"""
    from ..runtime.runner import RunResult
    try:
        text = payload.get("text", "")
        if isinstance(text, (dict, list)):
            text = json.dumps(text, ensure_ascii=False)
        ans = provider.chat(
            [{"role": "user", "content": str(text)[:12000]}],
            system=str(payload.get("prompt") or "整理要点，输出简洁中文"),
        )
        return RunResult(bool(ans.strip()), 0, {"text": ans.strip()})
    except Exception as e:
        return RunResult(False, 0, {}, f"文本加工失败：{e}")


def _llm_verify(criteria: str, goal: str, outputs: dict) -> "runner.RunResult":
    """评审步（PRD §7.5）：LLM 按预设标准独立检查，不通过即失败——不伪装成功"""
    from ..runtime.runner import RunResult
    try:
        raw = provider.chat(
            [{"role": "user",
              "content": f"目标：{goal}\n上游产出：{json.dumps(outputs, ensure_ascii=False)[:6000]}"}],
            system=f"你是独立评审。按标准判断产出是否合格：{criteria}\n"
                   f'只输出 JSON：{{"pass": true/false, "reason": "理由"}}',
            max_tokens=300,
        )
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        ok = bool(d.get("pass"))
        return RunResult(ok, 0, {"pass": ok, "reason": d.get("reason", "")},
                         "" if ok else f"评审未通过：{d.get('reason', '')}")
    except Exception as e:
        return RunResult(False, 0, {}, f"评审执行失败：{e}")


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
    if intent == "mem0_add":
        return {"action": "add", "text": re.sub(r"^(记住|记一下)[:：，,\s]*", "", goal)}
    if intent == "deep_research":
        # 剥掉指令词取纯主题（如"帮我深度调研：Rust 生态趋势"→"Rust 生态趋势"）
        topic = re.sub(r"^(帮我|请)?(对我|对|关于)?(做|进行|来)?(一次|一个)?"
                       r"(深度|深入|全面)?调研[:：，,\s]*", "", goal).strip()
        return {"topic": topic or goal}
    if intent == "mem0_search":
        return {"action": "search",
                "query": re.sub(r"^(回忆|你记得|查一下记忆)(一下)?[:：，,\s]*", "", goal)}
    if intent == "repo_quality":
        return {"repo_url": GITHUB_RE.search(goal).group(0).rstrip("/")}
    if intent == "browser":
        url = re.search(r"https?://\S+", goal).group(0)
        instruction = re.sub(r"https?://\S+", "", goal)
        instruction = re.sub(r"^(帮我|请)?(用浏览器)?(浏览|打开|看看)(这个)?(网页)?", "",
                             instruction).strip("，,。:： ")
        return {"url": url, "instruction": instruction or "读取页面主要内容并总结要点"}
    return {}


def _verify(intent: str, output: dict) -> bool:
    """轻量结构校验：旗舰能力要求证据分级的两侧字段都存在"""
    if intent == "repo_analysis":
        return "verified" in output and "inferred" in output
    if intent == "text_stats":
        return "chars" in output
    if intent == "hot_news":
        return "digest_markdown" in output and "top_items" in output
    if intent in ("mem0_add", "mem0_search"):
        return "added" in output or "results" in output
    if intent == "repo_quality":
        # Scorecard 未收录（404）也是合格的诚实产出——error 字段本身就是结果
        return "score" in output or "error" in output
    if intent == "browser":
        return "result" in output
    if intent == "deep_research":
        # 报告文件与引用来源是证据底线：缺来源的报告无法核验，不算通过
        return bool(output.get("report_path")) and bool(output.get("sources"))
    return True


def _deliver(intent: str, output: dict) -> str:
    """交付摘要：结果优先、直给结论（PRD §4.5）"""
    if intent == "hot_news":
        return (f"{output.get('digest_markdown', '')}\n"
                f"（输入 {output.get('total_input')} 条，去重移除 {output.get('removed_duplicates')} 条同题报道）")
    if intent == "mem0_add":
        return f"已记住：{output.get('detail', '入库完成')}"
    if intent == "mem0_search":
        hits = output.get("results") or []
        if not hits:
            return "语义记忆里没有相关内容"
        return "召回记忆：\n" + "\n".join(f"- {h['memory']}（相似度 {h.get('score', 0):.2f}）"
                                        for h in hits[:5])
    if intent == "repo_quality":
        if "error" in output:
            return f"质量评分不可用：{output['error']}"
        checks = output.get("key_checks") or {}
        lines = [f"OpenSSF Scorecard 综合分：{output.get('score')}（{output.get('repo')}）"]
        for k, v in list(checks.items())[:6]:
            lines.append(f"  {k}: {v['score']}")
        return "\n".join(lines)
    if intent == "browser":
        return f"[浏览器] {output.get('result', '')[:800]}"
    if intent == "deep_research":
        # 摘要先行 + 来源数（PRD 证据分级：报告可下载，来源可核验）
        srcs = output.get("sources") or []
        return (f"{output.get('summary', '调研完成')}\n\n"
                f"（全文报告可下载：{output.get('report_path', '').split('/')[-1]}；"
                f"引用来源 {len(srcs)} 条）")
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
