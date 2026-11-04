# 工具网关：所有能力执行前的统一关卡（PRD §12.2/12.6）
# 风险三级：L0 只读自动放行 / L1 白名单内写默认需确认（可按能力开自动）/ L2 外部副作用必须人工批准
# L2 永不自动是硬编码约束——防注入后自我提权，不提供任何配置出口
import sqlite3
import time

from ..db import tx

APPROVAL_TIMEOUT_S = 600  # 审批超时视为拒绝（fail-safe），默认 10 分钟


def _get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with tx(conn):
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def is_paused(conn: sqlite3.Connection) -> bool:
    return _get_setting(conn, "system.paused") == "1"


def _record_call(conn, task_id, capability, risk, status, args_digest=""):
    """审计落库（append-only），返回 tool_call id"""
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO tool_calls(task_id, capability, risk, status, args_digest, started_at) "
            "VALUES(?,?,?,?,?,?)",
            (task_id, capability, risk, status, args_digest, int(time.time())),
        )
    return cur.lastrowid


def check(
    conn: sqlite3.Connection,
    capability: str,
    risk: str,
    task_id: int | None = None,
    args_digest: str = "",
) -> str:
    """执行前检查。返回 allowed / pending / denied / paused 之一。

    - allowed：直接执行，审计记 approved_auto
    - pending：生成审批单，等待用户决定（或超时拒绝）
    - denied：审批已被拒绝或已超时
    - paused：系统处于紧急停止态（L1/L2 挂起，L0 仍放行）
    """
    if risk == "L0":
        _record_call(conn, task_id, capability, risk, "approved_auto", args_digest)
        return "allowed"

    if is_paused(conn):
        return "paused"

    # 查最近一次同任务同能力同风险的审批结论：已批→放行、已拒/超时→拒绝、未决→继续等
    # （不做 status 过滤：审批落定后 tool_calls.status 已变，按决定字段判断才可靠）
    row = conn.execute(
        "SELECT tc.id, a.decision FROM tool_calls tc "
        "JOIN approvals a ON a.tool_call_id = tc.id "
        "WHERE tc.task_id IS ? AND tc.capability=? AND tc.risk=? "
        "ORDER BY tc.id DESC LIMIT 1",
        (task_id, capability, risk),
    ).fetchone()
    if row:
        if row["decision"] == "approved":
            return "allowed"
        if row["decision"] == "rejected":
            return "denied"
        return "pending"  # 未决定 → 继续等待，不重复建单

    call_id = _record_call(conn, task_id, capability, risk, "pending", args_digest)
    with tx(conn):
        conn.execute(
            "INSERT INTO approvals(tool_call_id, risk, reason, requested_at) VALUES(?,?,?,?)",
            (call_id, risk, f"能力 {capability}（风险 {risk}）请求执行，请确认", int(time.time())),
        )
    return "pending"


def decide(conn: sqlite3.Connection, approval_id: int, decision: str) -> bool:
    """用户审批决定；顺带回写关联 tool_calls 状态"""
    row = conn.execute(
        "SELECT a.tool_call_id FROM approvals a WHERE a.id=?", (approval_id,)
    ).fetchone()
    if not row:
        return False
    with tx(conn):
        conn.execute(
            "UPDATE approvals SET decision=?, decided_at=?, decided_by='user' WHERE id=?",
            (decision, int(time.time()), approval_id),
        )
        conn.execute(
            "UPDATE tool_calls SET status=? WHERE id=?",
            ("approved" if decision == "approved" else "rejected", row["tool_call_id"]),
        )
    return True


def expire_stale(conn: sqlite3.Connection) -> int:
    """超时未决的审批单标记拒绝并返回数量（启动或轮询时调用）"""
    now = int(time.time())
    rows = conn.execute(
        "SELECT id FROM approvals WHERE decision IS NULL AND requested_at < ?",
        (now - APPROVAL_TIMEOUT_S,),
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE approvals SET decision='rejected', decided_at=?, decided_by='timeout' WHERE id=?",
            (now, r["id"]),
        )
    if rows:
        conn.commit()
    return len(rows)
