# 评估流水线：候选 → 多仓事实采集 → 十二维报告（PRD §5.5/§5.7）
# 采集复用 Phase 1 的 repo_analysis 能力（每仓一次）；报告由 LLM 按固定模板填充，
# LLM 只负责组织依据与结论，不自由发挥——评判稳定性靠模板约束
import asyncio
import json
import sqlite3
import time

from ..db import tx
from ..discovery import gh
from ..log import get_logger
from ..providers import router as provider
from ..runtime import registry, runner
from . import lifecycle

log = get_logger("evaluation")

REPORT_TEMPLATE = """你是开源项目评估师。基于给定的事实材料，按以下 JSON 结构输出评估报告（只输出 JSON）：
{{
  "dimensions": {{
    "功能匹配度": "…", "实际效果": "未实测（Phase 2 无实测数据时如实标注）",
    "主板适配度": "…", "可靠性": "…", "安全性": "…", "维护状况": "…",
    "部署成本": "…", "运行成本": "…", "扩展能力": "…", "使用体验": "…",
    "许可与合规": "…", "未来价值": "…"
  }},
  "evidence": {{"verified": ["来自代码/提交/元数据的事实…"], "inferred": ["来自自述或推测…"]}},
  "recommendation": "integrate|test|watch|hold|reject 之一",
  "reason": "推荐理由（2-3 句，说明不确定性）",
  "confidence": 0.0到1.0
}}
规则：材料里没有的依据写"材料未提供"，不得编造；热度（stars）只是维护状况的旁证，不是推荐主因。"""

MAX_PEERS = 2  # 同类比较仓上限（含主角共 3 仓采集，控制时间与费用）


def register_candidate(conn, repo_url: str, trigger="user", trigger_ref=None,
                       metadata=None) -> int:
    """登记候选（幂等：已存在则返回原 id 并可更新元数据）"""
    url = repo_url.strip().rstrip("/")
    now = int(time.time())
    row = conn.execute("SELECT id FROM candidates WHERE repo_url=?", (url,)).fetchone()
    if row:
        return row["id"]
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO candidates(repo_url, name, trigger, trigger_ref, metadata_json, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (url, url.rsplit("/", 1)[-1], trigger, trigger_ref,
             json.dumps(metadata or {}, ensure_ascii=False), now, now),
        )
    return cur.lastrowid


def advance(conn, candidate_id: int, nxt: str) -> bool:
    """生命周期推进：迁移合法性 + 产物存在性双校验（无产物不得推进）"""
    row = conn.execute("SELECT lifecycle FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        return False
    lifecycle.transition(row["lifecycle"], nxt)
    table = lifecycle.REQUIRED_ARTIFACT.get(nxt)
    if table:
        has = conn.execute(
            f"SELECT 1 FROM {table} WHERE candidate_id=? LIMIT 1", (candidate_id,)
        ).fetchone()
        if not has:
            raise lifecycle.IllegalLifecycle(f"推进到 {nxt} 需要存在 {table} 产物，先完成对应步骤")
    with tx(conn):
        conn.execute("UPDATE candidates SET lifecycle=?, updated_at=? WHERE id=?",
                     (nxt, int(time.time()), candidate_id))
    return True


def _collect_facts(conn, repo_url: str) -> dict:
    """用 repo_analysis 能力采集单仓事实（失败返回 {"error": …}，流水线不中断）"""
    cap = registry.get_active(conn, "repo_analysis")
    if not cap:
        return {"error": "repo_analysis 能力不可用"}
    res = runner.run_capability(conn, cap, task_id=None, step_id=None,
                                payload={"repo_url": repo_url}, timeout_s=240)
    return res.output if res.ok else {"error": res.error}


async def evaluate(conn: sqlite3.Connection, candidate_id: int) -> dict:
    """完整评估：主角仓 + 最多 2 个同类仓采集 → LLM 报告 → 落库 + 推进生命周期。
    纯同步子步骤用 to_thread 包装，避免阻塞事件循环。"""
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not cand:
        return {"error": "候选不存在"}

    # 1) 同类发现（主角仓关键词 → gh 搜索，剔除自身）
    meta = json.loads(cand["metadata_json"] or "{}")
    if not meta:
        meta = (await asyncio.to_thread(gh.search_repos, cand["repo_url"].rsplit("/", 1)[-1], 1)
                or [{}])[0]
    topic_q = " ".join(meta.get("topics", [])[:3]) or meta.get("name", "")
    peers = [p for p in await asyncio.to_thread(gh.search_repos, topic_q, MAX_PEERS + 1)
             if p["repo_url"] != cand["repo_url"]][:MAX_PEERS]

    # 2) 事实采集：主角 + peers（串行，控制负载）
    facts = {cand["repo_url"]: await asyncio.to_thread(_collect_facts, conn, cand["repo_url"])}
    for p in peers:
        facts[p["repo_url"]] = await asyncio.to_thread(_collect_facts, conn, p["repo_url"])

    # 3) LLM 报告（固定模板；无 provider 时降级为"仅事实罗列"）
    material = json.dumps({"主角仓": cand["repo_url"], "元数据": meta,
                           "同类仓": peers, "采集事实": facts}, ensure_ascii=False, indent=1)[:12000]
    if provider.available():
        try:
            raw = provider.chat([{"role": "user", "content": material}],
                                system=REPORT_TEMPLATE, max_tokens=2500)
            report = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except (ValueError, Exception) as e:
            log.warning("LLM 报告生成失败，降级事实版: %s", e)
            report = _facts_only_report(facts, cand["repo_url"])
    else:
        report = _facts_only_report(facts, cand["repo_url"])

    # 4) 落库 + 推进（discovered→…→compared 一次走完，产物已齐）
    with tx(conn):
        conn.execute(
            "INSERT INTO evaluation_reports(candidate_id, dimensions_json, evidence_json, "
            "peers_json, recommendation, reason, confidence, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (candidate_id,
             json.dumps(report.get("dimensions", {}), ensure_ascii=False),
             json.dumps(report.get("evidence", {}), ensure_ascii=False),
             json.dumps([p["repo_url"] for p in peers], ensure_ascii=False),
             report.get("recommendation", "hold"),
             report.get("reason", ""), report.get("confidence", 0.5), int(time.time())),
        )
    for nxt in (lifecycle.SCREENED, lifecycle.DEEP_ANALYSIS, lifecycle.COMPARED):
        advance(conn, candidate_id, nxt)
    return report


def _facts_only_report(facts: dict, main_url: str) -> dict:
    """无 LLM 时的降级报告：只呈现已采集事实，推荐一律 hold（不假装评估）"""
    verified = []
    for url, f in facts.items():
        for k, v in (f.get("verified") or {}).items():
            verified.append(f"{url}: {k}={v}")
    return {
        "dimensions": {"说明": "LLM 未配置，以下仅为采集事实，未做综合评估"},
        "evidence": {"verified": verified[:20], "inferred": []},
        "recommendation": "hold",
        "reason": "模型未配置或生成失败，仅提供事实材料，请在配置模型后重新评估",
        "confidence": 0.3,
    }
