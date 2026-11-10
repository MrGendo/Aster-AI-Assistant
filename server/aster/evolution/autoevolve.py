# 自动进化模式：task_gap 闭环（PRD 原始设想，设计见 docs/superpowers/specs/2026-11-06-autoevolve-design.md）
# 请求缺能力 → GitHub 搜索 → 评估吸收 → 用新能力完成原请求；8 阶段即任务步骤，进度在聊天卡可见
# 可续跑：按 task_steps 已完成状态跳过（审批通过后的重试不重跑前五阶段——
# retry 端点对 evolve 意图保留步骤，重跑从断点继续）
import asyncio
import json
import time

from ..db import tx
from ..discovery.gh import search_repos
from ..log import get_logger

log = get_logger("autoevolve")

STAGES = [
    ("明确缺口", "提取需求描述与英文检索词"),
    ("GitHub 搜索", "按检索词找高星候选（top5）"),
    ("候选初筛", "星数/许可证/活跃度规则闸，择优"),
    ("文档研读", "README 理解摘要与陷阱清单（doc-first 平台版）"),
    ("适配生成", "登记候选（task_gap）并生成适配草稿"),
    ("人工审批", "L2 门禁：聊天卡内联批准"),
    ("接入验证", "候选能力转正（active）"),
    ("原请求重放", "用新能力直接执行原始请求"),
]

# 初筛规则闸：宁可错过不可错收（daytona 死仓库教训——错收引入死重依赖）
# 星数门槛 5000：用户 2026-11-06 定标（低于此直接丢弃，不进评估）
MIN_STARS = 5000
BAD_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "AGPL-1.0", "NOASSERTION", "未知", None}


def detect_gap(conn, message: str) -> dict | None:
    """缺口检测：全标签分类落到 chat 时的兜底闸——是"现有能力做不了、但有现成开源库可补"
    的任务缺口吗？返回 {need, query} 或 None（普通对话照常）。"""
    from ..providers import router as provider
    if not provider.available():
        return None
    caps = "\n".join(f"- {r['name']}：{json.loads(r['descriptor_json']).get('purpose', '')[:50]}"
                     for r in conn.execute(
                         "SELECT name, descriptor_json FROM capabilities WHERE status='active'"))
    try:
        raw = provider.chat(
            [{"role": "user", "content": message}],
            system="判断这条消息是否是\"平台现有能力清单做不了、但存在现成开源库可补充\"的任务请求。\n"
                   f"现有能力：\n{caps}\n"
                   '是任务缺口输出 JSON：{"gap":true,"need":"缺什么能力（中文一句话）",'
                   '"query":"GitHub 英文检索词（1-4 个英文词）"}；'
                   '普通对话/现有能力已覆盖/需要原创代码而非开源库 → {"gap":false}。只输出 JSON。',
            max_tokens=200)
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        if d.get("gap") and d.get("query"):
            return {"need": d.get("need") or message[:60], "query": str(d["query"]).strip()}
    except Exception as e:
        log.info("缺口检测失败（按普通对话处理）: %s", e)
    return None


def sanitize_query(message: str, query: str) -> str:
    """检索词质检：gh 搜索喂中文原句会返回关键词碰瓷的垃圾结果（实测：政治碰瓷库）；
    纯中文且无英文技术词时会得到空串（实测：「转成拼音」零结果）。三级兜底：
    英文检索词直用 → 抽消息英文技术词 → LLM 翻译成英文领域检索词。"""
    import re as _re
    q = (query or "").strip()
    if q and not _re.search(r"[一-鿿]", q):
        return q
    tokens = [t for t in _re.findall(r"[A-Za-z][A-Za-z0-9+#._-]{1,}", message)
              if t.lower() not in {"http", "https", "github", "com", "www"}]
    if tokens:
        return " ".join(tokens[:4]) + " library"
    try:
        from ..providers import router as provider
        if provider.available():
            raw = provider.chat(
                [{"role": "user", "content": message}],
                system="把这句话的核心技术需求翻译成 GitHub 英文检索词（1-4 个词，"
                       "领域惯用词，如 pinyin/markdown/converter）。只输出检索词。",
                max_tokens=30).strip().strip('"`')
            if raw and 2 < len(raw) < 60:
                return raw
    except Exception:
        pass
    return q


def _relevant(need: str, cand: dict) -> bool:
    """语义适配闸（实测教训：检索词 chinese pinyin converter 撞回 HowToCook★103k
    做饭指南——星数/许可证/活跃三闸全绿但驴唇不对马嘴）。一次 LLM 判断候选与需求
    是否同一件事；失败时保守放行（误杀由文档研读与人工审批兜底）。"""
    from ..providers import router as provider
    if not provider.available():
        return True
    try:
        raw = provider.chat(
            [{"role": "user",
              "content": f"需求：{need}\n候选：{cand.get('name')}——{(cand.get('description') or '')[:120]}"}],
            system="这个开源仓库的核心功能是否正是满足该需求的能力来源？"
                   '是输出 {"fit":true}，不是/仅名字沾边输出 {"fit":false}。只输出 JSON。',
            max_tokens=60)
        return bool(json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("fit"))
    except Exception:
        return True


def _screen(cands: list[dict], need: str = "") -> tuple[dict | None, str]:
    """三重闸：星数/许可证/活跃度规则 + 语义适配（防高星蹭词库）。返回 (入选, 理由)"""
    if not cands:
        return None, "GitHub 搜索零结果"
    ranked = sorted(cands, key=lambda c: -(c.get("stars") or 0))
    for c in ranked:
        why = []
        if (c.get("stars") or 0) < MIN_STARS:
            why.append(f"星数 {c.get('stars')} < {MIN_STARS}")
        if c.get("license") in BAD_LICENSES:
            why.append(f"许可证 {c.get('license')} 不可用或未知")
        if str(c.get("pushed_at") or "")[:4] < "2025":
            why.append(f"最后推送 {str(c.get('pushed_at'))[:10]}，超过一年未活跃")
        if not why and need and not _relevant(need, c):
            why.append("语义不适配（高星蹭词，非该需求的能力来源）")
        if not why:
            return c, f"{c.get('stars')} 星 · {c.get('license')} · 活跃 · 语义适配"
        log.info("候选 %s 被筛除：%s", c.get("name"), "；".join(why))
    return None, f"top{len(ranked)} 候选全被筛除（星数/许可证/活跃度/语义适配不达标），该需求暂无合格开源候选"


def ensure_steps(conn, task_id: int) -> None:
    """8 阶段落为任务步骤（幂等：已有则不重插）"""
    if conn.execute("SELECT 1 FROM task_steps WHERE task_id=?", (task_id,)).fetchone():
        return
    with tx(conn):
        for i, (name, desc) in enumerate(STAGES, 1):
            conn.execute("INSERT INTO task_steps(task_id, seq, step_desc, status) "
                         "VALUES(?,?,?,?)", (task_id, i, f"{name}：{desc}", "pending"))


async def run(conn, task_id: int, message: str) -> None:
    """进化任务主体：按阶段推进，已完成（done）的阶段直接跳过——审批通过后
    的重试从断点续跑，不重烧前五阶段的搜索与生成。"""
    from .. import tsm
    from ..orchestrator import core as orch
    from ..providers import router as provider

    def _row(seq: int):
        return conn.execute("SELECT * FROM task_steps WHERE task_id=? AND seq=?",
                            (task_id, seq)).fetchone()

    def _done(seq: int, output: str) -> None:
        with tx(conn):
            conn.execute("UPDATE task_steps SET status='done', output_ref=?, finished_at=? "
                         "WHERE id=?", (str(output)[:2000], int(time.time()), _row(seq)["id"]))

    def _fail(seq: int, reason: str) -> None:
        with tx(conn):
            conn.execute("UPDATE task_steps SET status='failed', error=?, finished_at=? WHERE id=?",
                         (reason, int(time.time()), _row(seq)["id"]))
        orch._fail(conn, task_id, f"阶段「{STAGES[seq - 1][0]}」失败：{reason}")

    # 断点上下文：前次运行的关键产物从步骤输出恢复（重试不重跑）
    ctx = json.loads(conn.execute("SELECT plan_json FROM tasks WHERE id=?",
                                  (task_id,)).fetchone()["plan_json"] or "{}")
    need, query = ctx.get("need"), ctx.get("query")
    cand_id, requirements = ctx.get("cand_id"), ctx.get("requirements") or []
    chosen = ({"repo_url": ctx["chosen_repo"], "name": str(ctx["chosen_repo"]).split("/")[-1]}
              if ctx.get("chosen_repo") else None)

    orch._set_status(conn, task_id, tsm.EXECUTING)
    for seq, (name, _) in enumerate(STAGES, 1):
        row = _row(seq)
        if row and row["status"] == "done":          # 断点续跑：跳过已完成阶段（产物走 ctx 恢复）
            continue
        try:
            if seq == 1:
                gap = {"need": need, "query": query}
                if not (need and query):
                    gap = detect_gap(conn, message) or {}
                    need, query = gap.get("need", message[:60]), sanitize_query(message, gap.get("query", ""))
                query = sanitize_query(message, query)
                with tx(conn):
                    conn.execute("UPDATE tasks SET plan_json=? WHERE id=?",
                                 (json.dumps({"need": need, "query": query}, ensure_ascii=False),
                                  task_id))
                _done(1, f"需求：{need}\n检索词：{query}")
            elif seq == 2:
                # 多轮检索（实测收敛策略）：精确短语常只命中同名小库（"chinese pinyin
                # converter"→★9 级同名库，python-pinyin★5391 不含该词不在 top5）；
                # 逐词单搜（pinyin）才能让高星主库浮上来。原词→每个词各搜一轮
                attempts = [query] + (query.split() if " " in query else [])
                seen_lists, chosen, why = [], None, ""
                for qi, q in enumerate(dict.fromkeys(attempts)):
                    cands = await asyncio.to_thread(search_repos, q, 5)
                    seen_lists.append(f"【{q}】\n" + "\n".join(
                        f"- {c['name']} ★{c['stars']} {c['license']}：{(c['description'] or '')[:50]}"
                        for c in cands) or "（零结果）")
                    chosen, why = _screen(cands, need)
                    if chosen:
                        ctx["chosen_repo"] = chosen["repo_url"]
                        with tx(conn):
                            conn.execute("UPDATE tasks SET plan_json=? WHERE id=?",
                                         (json.dumps(ctx, ensure_ascii=False), task_id))
                        break
                _done(2, "\n\n".join(seen_lists))
                if not chosen:
                    return _fail(3, why + "——两轮检索均无合格候选，该需求暂无 5000★ 级开源方案")
            elif seq == 3:
                _done(3, f"入选：{chosen['repo_url']}（{why}）")
            elif seq == 4:
                from ..runtime import registry, runner
                wcap = registry.get_active(conn, "web_fetch")
                res = await asyncio.to_thread(
                    runner.run_capability, wcap, task_id, _row(4)["id"],
                    {"urls": [chosen["repo_url"]]}, timeout_s=60)
                pages = (res.output.get("pages") if res.ok else None) or []
                readme = max(((p.get("text") or "") for p in pages), key=len, default="")[:8000]
                digest = ("README 抓取为空——可行性未知，适配按保守路径生成" if not readme else
                          provider.chat(
                              [{"role": "user", "content": f"仓库：{chosen['repo_url']}\nREADME：\n{readme[:6000]}"}],
                              system="产出接入理解摘要（doc-first）：①解决什么 ②核心 API/CLI 用法 "
                                     "③依赖与运行要求 ④已知短板/陷阱 ⑤作为命令行工人接入可行性。中文，300 字内。",
                              max_tokens=600))
                _done(4, digest)
            elif seq == 5:
                from ..integration import scaffold
                with tx(conn):
                    cur = conn.execute(
                        "INSERT INTO candidates(repo_url, name, trigger, trigger_ref, metadata_json, "
                        "created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(repo_url) DO UPDATE SET updated_at=excluded.updated_at "
                        "RETURNING id",
                        (chosen["repo_url"], chosen["repo_url"].split("/")[-1], "task_gap",
                         str(task_id), json.dumps(chosen, ensure_ascii=False),
                         int(time.time()), int(time.time())))
                    cand_id = cur.fetchone()["id"]
                result = await asyncio.to_thread(scaffold.generate, conn, cand_id)
                if not result.get("ok"):
                    return _fail(5, f"适配草稿生成失败：{result.get('error', '未知原因')}")
                with tx(conn):
                    ctx.update({"cand_id": cand_id,
                                "requirements": result.get("requirements") or [],
                                "cap_name": result.get("name", "")})
                    conn.execute("UPDATE tasks SET plan_json=? WHERE id=?",
                                 (json.dumps(ctx, ensure_ascii=False), task_id))
                _done(5, f"候选#{cand_id} 草稿就绪：{result.get('name')}（{result.get('draft_path', '')}）"
                      + (f"，依赖 {','.join(result['requirements'])}" if result.get("requirements") else ""))
            elif seq == 6:
                # 幂等闸：本任务已有 L2 审批结论则不再重开审批单（否则批准→重试→
                # 阶段6重跑又建新单，无限审批循环——实测踩坑）
                prev = conn.execute(
                    "SELECT a.decision FROM approvals a JOIN tool_calls tc ON tc.id=a.tool_call_id "
                    "WHERE tc.task_id=? ORDER BY a.id DESC LIMIT 1", (task_id,)).fetchone()
                if prev:
                    if prev["decision"] == "rejected":
                        return _fail(6, "用户拒绝吸收该开源项目，进化终止")
                    if prev["decision"] == "approved":
                        _done(6, "L2 审批已通过（历史单）")
                        continue
                # 登记候选能力行（幂等）：scaffold 只落文件不落库，登记原本在
                # integrate 端点里——自动进化链此前漏了这步（实测：阶段6找不到能力行）
                mrow = conn.execute("SELECT name FROM candidates WHERE id=?", (cand_id,)).fetchone()
                meta_path = __import__("pathlib").Path(
                    __import__("aster.config", fromlist=["DATA_DIR"]).DATA_DIR
                ) / "drafts" / str(cand_id) / "meta.json"
                meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                cap_name = ctx.get("cap_name") or meta.get("name") or "evolved_cap"
                with tx(conn):
                    conn.execute(
                        "INSERT OR IGNORE INTO capabilities(name, kind, descriptor_json, status, "
                        "source, version, risk_level, entry, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (cap_name, "tool",
                         json.dumps({"name": meta.get("title", cap_name),
                                     "purpose": meta.get("purpose", ""),
                                     "input": '{"input": "自然语言需求原文"}',
                                     "output": "worker stdout JSON",
                                     "cost": "本地执行", "limits": "自动进化生成，经 L2 审批",
                                     "origin": (mrow["name"] if mrow else ""),
                                     "requirements": requirements}, ensure_ascii=False),
                         "candidate", f"github:{mrow['name'] if mrow else 'autoevolve'}",
                         "0.1.0", "L1", f"_drafts/{cand_id}/adapter.py", int(time.time()),
                         int(time.time())))
                cap = conn.execute(
                    "SELECT id, name FROM capabilities WHERE name=?", (cap_name,)).fetchone()
                if not cap:
                    return _fail(6, "候选能力登记失败")
                with tx(conn):
                    conn.execute("UPDATE task_steps SET status='running' WHERE id=?", (_row(6)["id"],))
                    cur_tc = conn.execute(
                        "INSERT INTO tool_calls(capability, risk, status, args_digest, task_id, started_at) "
                        "VALUES('autoevolve','L2','pending',?,?,?) RETURNING id",
                        (f"吸收 {chosen['repo_url']} 为新能力 {cap['name']}", task_id, int(time.time()))
                    ).fetchone()
                    conn.execute("INSERT INTO approvals(tool_call_id, risk, reason, requested_at) "
                                 "VALUES(?,?,?,?)",
                                 (cur_tc["id"], "L2",
                                  f"自动进化：吸收 {chosen['repo_url']} 为新能力「{cap['name']}」"
                                  f"以完成「{message[:40]}」。"
                                  + (f"需安装依赖：{','.join(requirements)}。" if requirements else "")
                                  + "批准后接入并重放原请求。",
                                  int(time.time())))
                try:
                    orch._set_status(conn, task_id, tsm.AWAITING_USER)
                except Exception:
                    pass  # 已在 awaiting（并发重试踩过状态）——迁移失败不掩蔽审批单
                with tx(conn):
                    conn.execute("UPDATE tasks SET error=?, updated_at=? WHERE id=?",
                                 ("等待批准：吸收开源项目为新能力（L2，批准后自动续跑）",
                                  int(time.time()), task_id))
                return  # 暂停；批准→重试→本函数自断点续跑
            elif seq == 7:
                # 已获批（重试进来才到这）：先装依赖（审批理由已列明，L2 人批背书）再转正
                if requirements:
                    import subprocess as _sp
                    import sys as _sys
                    py = _sys.executable
                    proc = _sp.run([py, "-m", "pip", "install", "-q", *requirements],
                                   capture_output=True, text=True, timeout=300)
                    if proc.returncode != 0:
                        return _fail(7, f"依赖安装失败：{proc.stderr[-200:]}")
                cap = conn.execute(
                    "SELECT name, entry FROM capabilities WHERE entry LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"_drafts/{cand_id}/%",)).fetchone()
                if not cap:
                    return _fail(7, "候选能力行丢失")
                with tx(conn):
                    conn.execute("UPDATE capabilities SET status='active', updated_at=? WHERE entry=?",
                                 (int(time.time()), cap["entry"]))
                    conn.execute("UPDATE candidates SET lifecycle='integration_verified', updated_at=? "
                                 "WHERE id=?", (int(time.time()), cand_id))
                _done(7, f"能力「{cap['name']}」已接入（active）")
            elif seq == 8:
                # 真重放：新能力直接吃原始请求（适配器统一约定 input 键）
                from ..runtime import registry, runner
                cap = conn.execute(
                    "SELECT name FROM capabilities WHERE entry LIKE ? LIMIT 1",
                    (f"_drafts/{cand_id}/%",)).fetchone()
                cap_row = registry.get_active(conn, cap["name"])
                res = await asyncio.to_thread(
                    runner.run_capability, cap_row, task_id, _row(8)["id"],
                    {"input": message, "text": message}, timeout_s=300)
                if res.ok:
                    out = res.output if isinstance(res.output, str) else json.dumps(res.output, ensure_ascii=False)
                    report = {"type": "autoevolve", "new_capability": cap["name"],
                              "absorbed": chosen["repo_url"], "stages": len(STAGES)}
                    with tx(conn):
                        conn.execute("UPDATE tasks SET quality_json=? WHERE id=?",
                                     (json.dumps(report, ensure_ascii=False), task_id))
                    _done(8, str(out)[:1500])
                    orch._set_status(conn, task_id, tsm.VERIFYING)  # TSM：须经 verifying 再 completed
                    orch._finish(conn, task_id,
                                 summary=f"自动进化完成：吸收「{chosen['repo_url']}」为新能力"
                                         f"「{cap['name']}」，原请求执行结果：\n{str(out)[:800]}")
                else:
                    _fail(8, f"新能力执行原请求失败：{res.error}——能力已接入，可检查输入格式后重试")
                return
        except Exception as e:
            log.exception("进化任务 %s 阶段 %s 异常", task_id, name)
            return _fail(seq, f"{type(e).__name__}: {e}")
    # 循环走完仍未 return（理论不到这）：诚实兜底
    orch._fail(conn, task_id, "进化流程异常结束")
