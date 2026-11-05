# 多轮对话服务：会话线程 + 历史上下文 + 记忆注入
# 定位：chat 型交互的载体——任务型输入不走这里（由单一入口分流）
import sqlite3
import time

from ..db import tx
from ..memory import service as memory_svc
from ..providers import router as provider

HISTORY_WINDOW = 10  # 携带最近 10 条消息（≈5 轮），再早的靠记忆系统兜底


def get_or_create(conn: sqlite3.Connection, conversation_id: int | None) -> int:
    if conversation_id:
        row = conn.execute("SELECT id FROM conversations WHERE id=?",
                           (conversation_id,)).fetchone()
        if row:
            return row["id"]
    now = int(time.time())
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO conversations(title, created_at) VALUES(?,?)", ("对话", now))
    return cur.lastrowid


def history(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
        (conversation_id, HISTORY_WINDOW),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def converse(conn: sqlite3.Connection, conversation_id: int, message: str) -> str:
    """一轮对话：存用户消息 → 组上下文（历史+记忆注入）→ GLM 回复 → 存回复"""
    with tx(conn):
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) VALUES(?,?,?,?)",
            (conversation_id, "user", message, int(time.time())))
        # 首条消息即标题（截断）
        conn.execute("UPDATE conversations SET title=? WHERE id=? AND title='对话'",
                     (message[:24], conversation_id))
    messages = history(conn, conversation_id)
    injection = memory_svc.context_injection(conn, message)
    system = ("你是 Aster，务实、诚实的个人智能助手。用中文回答；无法确定的事直说，"
              "不编造。可以参考历史对话与已知用户背景。"
              + (f"\n\n{injection}" if injection else ""))
    reply = provider.chat(messages, system=system)
    with tx(conn):
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) VALUES(?,?,?,?)",
            (conversation_id, "assistant", reply, int(time.time())))
    return reply


def thread(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? "
        "ORDER BY id LIMIT 200", (conversation_id,))]
