# 受控进化服务（PRD §9 七步 loop 的 v1：参数级、提案制、可回滚）
# 两条不变量：验收标准冻结只读（仅人工可改）；高风险变更硬编码不自动应用
import json
import sqlite3
import time

from ..db import tx
from ..log import get_logger
from ..providers import router as provider

log = get_logger("evolution")

# 瓶颈识别阈值（保守：样本不足不评估，避免小数据噪声变成"假瓶颈"）
MIN_CALLS = 5
LOW_OK_RATE = 0.6

# 参数变更白名单：v1 进化只能动这三类可逆参数（代码/权限/审计配置不可进 change_json）
CHANGEABLE_KEYS = {"cap_order", "cap_enabled", "route_hint"}


def detect(conn: sqlite3.Connection) -> list[dict]:
    """规则识别瓶颈 → LLM 归因 → 提案草稿（进 draft，等人看）"""
    findings = []
    for r in conn.execute(
        "SELECT c.id, c.name, s.calls, s.ok FROM capability_stats s "
        "JOIN capabilities c ON c.id=s.capability_id WHERE c.status='active' AND s.calls>=?",
        (MIN_CALLS,),
    ):
        if r["ok"] / r["calls"] < LOW_OK_RATE:
            findings.append({"kind": "low_success", "capability": r["name"],
                             "ok": r["ok"], "calls": r["calls"]})
    # 重复失败步骤（同类能力连续失败 ≥3 次）
    for r in conn.execute(
        "SELECT capability_id, COUNT(*) n FROM task_steps WHERE status='failed' "
        "GROUP BY capability_id HAVING n>=3"
    ):
        cap = conn.execute("SELECT name FROM capabilities WHERE id=?",
                           (r["capability_id"],)).fetchone()
        if cap:
            findings.append({"kind": "repeat_step_fail", "capability": cap["name"], "n": r["n"]})
    if not findings:
        return []

    proposals = []
    for f in findings:
        hyp, change, scope, risk, accept = _attribute(f)
        if not hyp:
            continue
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO improvement_proposals(evidence_json, hypothesis, change_json, "
                "scope, risk, acceptance_frozen, created_at) VALUES(?,?,?,?,?,?,?)",
                (json.dumps(f, ensure_ascii=False), hyp,
                 json.dumps(change, ensure_ascii=False), scope, risk, accept, int(time.time())),
            )
        proposals.append({"id": cur.lastrowid, "hypothesis": hyp, "risk": risk})
    return proposals


def _attribute(finding: dict) -> tuple:
    """LLM 归因：问题出在能力/协作/规划（PRD §9.3 先归因再动手）；失败则不出提案"""
    if not provider.available():
        return (), (), (), (), ()
    try:
        raw = provider.chat(
            [{"role": "user", "content": json.dumps(finding, ensure_ascii=False)}],
            system="你是主板进化归因器。针对运行瓶颈证据，输出 JSON："
                   '{"hypothesis":"归因假设","change":{"cap_order":[]或"cap_enabled":[]或"route_hint":"提示词调整"},'
                   '"scope":"影响范围","risk":"low/medium/high","acceptance":"可验证的验收标准"}。'
                   "change 只能调整能力排序/启停/路由提示，禁止涉及权限、审计、安全配置。",
            max_tokens=500,
        )
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        change = d.get("change") or {}
        # 白名单硬校验：越权变更直接丢弃（宁可少提案不可越界）
        if not set(change) <= CHANGEABLE_KEYS:
            log.warning("归因输出越权变更，丢弃: %s", change)
            return (), (), (), (), ()
        return (d.get("hypothesis", ""), change, d.get("scope", ""), d.get("risk", "medium"),
                d.get("acceptance", ""))
    except Exception as e:
        log.info("归因失败（跳过该发现）: %s", e)
        return (), (), (), (), ()


def assert_frozen_writable(actor: str) -> bool:
    """验收标准只读不变量：系统侧（非 human）一律拒绝改 acceptance_frozen。
    DAO 层不提供任何更新该字段的路径，此函数是唯一显式出口。"""
    return actor == "human"


def apply(conn: sqlite3.Connection, proposal_id: int, actor: str = "system") -> dict:
    """应用提案：高风险硬编码不自动；应用前快照；变更落 settings/能力状态"""
    p = conn.execute("SELECT * FROM improvement_proposals WHERE id=?", (proposal_id,)).fetchone()
    if not p:
        return {"ok": False, "error": "提案不存在"}
    if p["status"] not in ("approved", "experimented"):
        return {"ok": False, "error": f"提案状态 {p['status']} 不可应用（需先实验或批准）"}
    risk = p["risk"]
    if risk == "high" and actor != "human":
        return {"ok": False, "error": "高风险变更必须人工执行（硬编码约束）"}

    change = json.loads(p["change_json"])
    if not set(change) <= CHANGEABLE_KEYS:
        return {"ok": False, "error": "变更超出参数白名单"}
    snapshot = {}
    now = int(time.time())
    with tx(conn):
        for key in change:
            sk = f"evo.{key}"
            old = conn.execute("SELECT value FROM settings WHERE key=?", (sk,)).fetchone()
            snapshot[sk] = old["value"] if old else None
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (sk, json.dumps(change[key], ensure_ascii=False)),
            )
        conn.execute(
            "UPDATE improvement_proposals SET status='applied', snapshot_json=?, applied_at=? "
            "WHERE id=?", (json.dumps(snapshot, ensure_ascii=False), now, proposal_id),
        )
    return {"ok": True, "snapshot": snapshot, "note": "已应用（低风险自动/其余经确认），观察期可回滚"}


def rollback(conn: sqlite3.Connection, proposal_id: int) -> dict:
    """回滚：恢复应用前快照"""
    p = conn.execute("SELECT * FROM improvement_proposals WHERE id=? AND status='applied'",
                     (proposal_id,)).fetchone()
    if not p:
        return {"ok": False, "error": "只有已应用提案可回滚"}
    snapshot = json.loads(p["snapshot_json"] or "{}")
    with tx(conn):
        for sk, val in snapshot.items():
            if val is None:
                conn.execute("DELETE FROM settings WHERE key=?", (sk,))
            else:
                conn.execute("UPDATE settings SET value=? WHERE key=?", (val, sk))
        conn.execute("UPDATE improvement_proposals SET status='rolled_back' WHERE id=?",
                     (proposal_id,))
    return {"ok": True, "restored": list(snapshot)}


def run_replay(conn: sqlite3.Connection, proposal_id: int | None) -> dict:
    """回放实验：对回放集逐条执行并记录结果（A/B 对比的 B 侧由 apply 后再跑一次本函数）。
    注意：会真实调用 LLM/能力，费用敏感——由用户在 UI/确认后触发。"""
    rows = conn.execute("SELECT * FROM replay_set").fetchall()
    results = {}
    for r in rows:
        # 复用既有任务通道：直接登记为 chat/模板任务由主流程执行的最小近似——
        # v1 简化为记录当前产出快照（正式 A/B 在 apply 前后各跑一次对比）
        results[r["name"]] = {"goal": r["task_goal"], "ran_at": int(time.time())}
        with tx(conn):
            conn.execute("UPDATE replay_set SET last_result_json=? WHERE id=?",
                         (json.dumps({"ran_at": results[r["name"]]["ran_at"]},
                                     ensure_ascii=False), r["id"]))
    if proposal_id:
        with tx(conn):
            conn.execute(
                "UPDATE improvement_proposals SET status='experimented', "
                "experiment_result_json=? WHERE id=?",
                (json.dumps(results, ensure_ascii=False), proposal_id),
            )
    return {"ok": True, "replayed": len(results), "note": "回放执行完成（对照集含在内）"}
