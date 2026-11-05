# 单一入口：消息先进分类——任务型建任务（走原执行链），对话型挂会话线程
from fastapi import APIRouter, BackgroundTasks

from ..chat import service as chat_svc
from ..db import connect
from ..orchestrator import core as orchestrator
from ..providers import router as provider

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat_entry(body: dict, bg: BackgroundTasks) -> dict:
    message = (body.get("message") or "").strip()
    conversation_id = body.get("conversation_id")
    if not message or len(message) > 4000:
        return {"type": "error", "error": "消息为空或过长"}

    # 任务型输入（链接/热点/统计/记忆/规划等）照旧走任务链
    intent = orchestrator.classify(message)
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


@router.get("/chat/{conversation_id}")
def chat_thread(conversation_id: int) -> dict:
    conn = connect()
    try:
        return {"conversation_id": conversation_id,
                "messages": chat_svc.thread(conn, conversation_id)}
    finally:
        conn.close()
