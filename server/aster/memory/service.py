# 记忆服务（PRD §11 Phase 1 三类：偏好/背景/项目）
# 原则：不做无门槛入库——candidate 状态 + 确认闸，来源可追溯
import sqlite3
import time

from ..db import tx

SCOPES = ("preference", "background", "project")


def add(conn, scope: str, content: str, source_type="manual", source_ref=None,
        confidence=1.0, auto_activate=False) -> int:
    assert scope in SCOPES
    now = int(time.time())
    # 手工创建视为用户确认，直接 active；任务沉淀类一律 candidate 等确认
    status = "active" if auto_activate else "candidate"
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO memories(scope, content, source_type, source_ref, confidence, status, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (scope, content.strip(), source_type, source_ref, confidence, status, now, now),
        )
    return cur.lastrowid


def confirm(conn, memory_id: int) -> bool:
    return _set_status(conn, memory_id, "active")


def reject(conn, memory_id: int) -> bool:
    return _set_status(conn, memory_id, "deleted")


def _set_status(conn, memory_id: int, status: str) -> bool:
    with tx(conn):
        cur = conn.execute(
            "UPDATE memories SET status=?, updated_at=? WHERE id=?",
            (status, int(time.time()), memory_id),
        )
    return cur.rowcount > 0


def update(conn, memory_id: int, content: str) -> bool:
    with tx(conn):
        cur = conn.execute(
            "UPDATE memories SET content=?, updated_at=? WHERE id=? AND status!='deleted'",
            (content.strip(), int(time.time()), memory_id),
        )
    return cur.rowcount > 0


def list_memories(conn, scope=None, status=None) -> list[sqlite3.Row]:
    q = "SELECT * FROM memories WHERE 1=1"
    args: list = []
    if scope:
        q += " AND scope=?"; args.append(scope)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY updated_at DESC LIMIT 200"
    return conn.execute(q, args).fetchall()


def search_active(conn, keyword: str, limit: int = 10) -> list[sqlite3.Row]:
    """注入检索：Phase 1 用 LIKE 关键词匹配（FTS5/jieba 配方在记忆量起来后启用）"""
    return conn.execute(
        "SELECT * FROM memories WHERE status='active' AND content LIKE ? "
        "ORDER BY updated_at DESC LIMIT ?",
        (f"%{keyword}%", limit),
    ).fetchall()


def context_injection(conn, goal: str, budget: int = 6) -> str:
    """组装注入上下文：活跃记忆按相关度+新鲜度取 top-N（Phase 1 无向量，取最近+关键词命中）"""
    rows = conn.execute(
        "SELECT scope, content FROM memories WHERE status='active' "
        "ORDER BY updated_at DESC LIMIT ?",
        (budget,),
    ).fetchall()
    if not rows:
        return ""
    lines = [f"[{r['scope']}] {r['content']}" for r in rows]
    return "已知用户背景与偏好（供理解任务时参考，不代表当前任务的事实）：\n" + "\n".join(lines)
