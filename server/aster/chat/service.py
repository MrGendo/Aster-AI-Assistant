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


def _capability_manifest(conn: sqlite3.Connection) -> str:
    """平台能力清单注入 chat 人格——没有它模型会以"裸模型"身份否认平台实际
    拥有的能力（实测：用户要定时器，平台明明有闹钟却答"我做不到"）"""
    import json as _json
    rows = conn.execute(
        "SELECT name, descriptor_json, status FROM capabilities WHERE status='active'").fetchall()
    lines = [f"- {r['name']}：{_json.loads(r['descriptor_json']).get('purpose', '')[:60]}"
             for r in rows]
    return (
        "你运行在 Aster 平台上，以下是平台【实际拥有】的能力，回答前先对照——"
        "用户想要的事若平台做得到，绝不允许说\"我无法/我没有这个功能\"，"
        "而应告诉用户直接的说法（平台按句式自动路由）：\n" + "\n".join(lines) +
        "\n常用触发说法：\"提醒我 30 分钟后休息\"（闹钟）｜\"每天 9 点给我热点日报\"（定时任务）"
        "｜\"帮我加个待办：明天交报告 (A)\"｜\"深度调研：某主题\"（联网调研出带引用报告）"
        "｜\"分析 https://github.com/xx/yy\"（仓库分析）｜\"规划执行：A 然后 B\"（多能力协作）。\n"
        "平台边界（如实说明，不夸大）：能自动进化运行参数、能走评估流程吸收开源项目为新能力；"
        "但不能凭空编写全新功能代码。")


def converse(conn: sqlite3.Connection, conversation_id: int, message: str) -> str:
    """一轮对话：存用户消息 → 组上下文（历史+记忆注入+能力清单）→ GLM 回复 → 存回复"""
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
              "不编造。可以参考历史对话与已知用户背景。\n\n"
              + _capability_manifest(conn)
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


def list_conversations(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """会话列表（倒序）：标题 + 最后活跃时间 + 消息数——首页历史会话面板用"""
    rows = conn.execute(
        "SELECT c.id, c.title, c.created_at, "
        "(SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) AS n, "
        "(SELECT max(created_at) FROM messages m WHERE m.conversation_id=c.id) AS last_at "
        "FROM conversations c ORDER BY COALESCE(last_at, c.created_at) DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]
