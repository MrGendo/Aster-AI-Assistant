# 单一入口：消息先进分类——任务型建任务（走原执行链），对话型挂会话线程
import json
import re
import time

from fastapi import APIRouter, BackgroundTasks

from .. import todos as todo_logic
from ..chat import service as chat_svc
from ..db import connect, tx
from ..orchestrator import core as orchestrator
from ..providers import router as provider

router = APIRouter(tags=["chat"])


def _todo_add(message: str) -> str:
    """对话加待办：剥前缀后走统一语法解析，入库后回执"""
    text = re.sub(
        r"^(帮我|请|给我)?(待办|todo)[:：]"
        r"|^(帮我|请|给我)?(添加|加|记|新增)(一个|条|个)?待办[:：，,\s]*"
        r"|^(帮我|请)?提醒我[:：，,\s]*",
        "", message, flags=re.IGNORECASE).strip()
    parsed = todo_logic.parse_content(text)
    conn = connect()
    try:
        with tx(conn):
            conn.execute("INSERT INTO todos(content, priority, due, source, created_at) "
                         "VALUES(?,?,?,?,?)",
                         (parsed["content"], parsed["priority"], parsed["due"],
                          "agent", int(time.time())))
    finally:
        conn.close()
    meta = " · ".join(x for x in (
        f"优先级 {parsed['priority']}" if parsed["priority"] else "",
        f"截止 {parsed['due']}" if parsed["due"] else "",
    ) if x)
    return f"已记待办：{parsed['content']}" + (f"（{meta}）" if meta else "")


def _todo_list() -> str:
    """对话列待办：进行中前 5 条（系统排序：逾期>今日>优先级），附完成计数"""
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM todos WHERE done=0").fetchall()]
        done_n = conn.execute("SELECT count(*) FROM todos WHERE done=1").fetchone()[0]
    finally:
        conn.close()
    if not rows:
        return "没有进行中的待办。" + (f"（已完成 {done_n} 条）" if done_n else "")
    rows.sort(key=todo_logic.sort_key)
    today = time.strftime("%Y-%m-%d")
    lines = []
    for i, r in enumerate(rows[:5], 1):
        mark = ""
        if r["due"]:
            mark = " ⚠️逾期" if r["due"] < today else (f" · 今天到期" if r["due"] == today else "")
        pri = f"[{r['priority']}]" if r["priority"] else ""
        lines.append(f"{i}. {pri}{r['content']}{mark}")
    more = f"（还有 {len(rows) - 5} 条）" if len(rows) > 5 else ""
    return "进行中待办：\n" + "\n".join(lines) + more + (f"\n已完成 {done_n} 条" if done_n else "")


@router.post("/chat")
async def chat_entry(body: dict, bg: BackgroundTasks) -> dict:
    message = (body.get("message") or "").strip()
    conversation_id = body.get("conversation_id")
    if not message or len(message) > 4000:
        return {"type": "error", "error": "消息为空或过长"}

    # 任务型输入（链接/热点/统计/记忆/规划等）照旧走任务链
    intent = orchestrator.classify(message)
    # 待办是轻量 DB 操作，不走任务机：直接执行并回执（不产生任务噪音）
    if intent == "todo_add":
        return {"type": "todo", "reply": _todo_add(message)}
    if intent == "todo_list":
        return {"type": "todo", "reply": _todo_list()}
    # 闹钟/定时任务：同样轻量，建调度后回执（时间没解析出来时引导用户补时间）
    if intent in ("alarm", "schedule"):
        from .. import scheduler as job_sched
        kind = "alarm" if intent == "alarm" else "task"
        content = re.sub(r"^(帮我|请)?提醒我[:：，,\s]*", "", message).strip() or message
        goal = content if kind == "alarm" else message  # 定时任务保留全句（后续分类依赖"日报"等词）
        row, err = job_sched.create(kind, goal, message)
        if err:
            return {"type": "todo", "reply": err + "（例如：提醒我明天 9 点开会）"}
        tj = json.loads(row["trigger_json"])
        nxt = (f"，下次 {time.strftime('%m-%d %H:%M', time.localtime(row['next_run_at']))}"
               if row["next_run_at"] else "")
        label = "闹钟已设" if kind == "alarm" else "定时任务已建"
        return {"type": "todo", "reply": f"{label}：{row['goal'][:60]}（{job_sched.describe_trigger(tj)}{nxt}）"}
    if intent != "chat":
        conn = connect()
        try:
            task_id = orchestrator.create_task(conn, message)
        finally:
            conn.close()
        bg.add_task(_run, task_id)
        return {"type": "task", "id": task_id}

    if not provider.available():
        return {"type": "error",
                "error": "对话需要模型，当前未配置（任务型能力不受影响）"}

    conn = connect()
    try:
        cid = chat_svc.get_or_create(conn, conversation_id)
        reply = chat_svc.converse(conn, cid, message)
        return {"type": "chat", "conversation_id": cid, "reply": reply}
    finally:
        conn.close()


async def _run(task_id: int) -> None:
    await orchestrator.execute(task_id)


@router.get("/chat/latest")
def chat_latest() -> dict:
    """恢复最近会话（首页加载时续聊；无会话返回 null）"""
    conn = connect()
    try:
        row = conn.execute("SELECT id FROM conversations ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"conversation_id": None, "messages": []}
        msgs = [dict(m) for m in conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? "
            "ORDER BY id DESC LIMIT 30", (row["id"],)).fetchall()][::-1]
        return {"conversation_id": row["id"], "messages": msgs}
    finally:
        conn.close()


@router.get("/chat/{conversation_id}")
def chat_thread(conversation_id: int) -> dict:
    conn = connect()
    try:
        return {"conversation_id": conversation_id,
                "messages": chat_svc.thread(conn, conversation_id)}
    finally:
        conn.close()
