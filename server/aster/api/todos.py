# 待办 API：增删改查 + 转任务（待办交给 agent 执行并回链）
from fastapi import APIRouter, BackgroundTasks, HTTPException

from .. import todos as todo_logic
from ..db import connect, tx

router = APIRouter(tags=["todos"])


@router.get("/todos")
def list_todos() -> dict:
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM todos").fetchall()]
        pending = sorted((r for r in rows if not r["done"]),
                         key=todo_logic.sort_key)
        # 完成态学 todo.txt：沉底且最近完成在前
        done = sorted((r for r in rows if r["done"]),
                      key=lambda r: -(r["done_at"] or 0))
        return {"pending": pending, "done": done}
    finally:
        conn.close()


@router.post("/todos")
def add_todo(body: dict) -> dict:
    text = (body.get("content") or "").strip()
    if not text or len(text) > 500:
        raise HTTPException(422, "待办内容为空或过长")
    # 输入框与对话同一语法：!(A)/!A 优先级、@日期/截止日期/今天明天后天
    parsed = todo_logic.parse_content(text)
    import time
    conn = connect()
    try:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO todos(content, priority, due, source, created_at) "
                "VALUES(?,?,?,?,?)",
                (parsed["content"], parsed["priority"], parsed["due"],
                 body.get("source") or "user", int(time.time())),
            )
            tid = cur.lastrowid
        return dict(conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone())
    finally:
        conn.close()


@router.patch("/todos/{tid}")
def update_todo(tid: int, body: dict) -> dict:
    import time
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "待办不存在")
        sets, args = [], []
        if "content" in body:
            text = (body.get("content") or "").strip()
            if not text:
                raise HTTPException(422, "待办内容不能为空")
            # 编辑时同样支持语法（重抽优先级/日期）
            parsed = todo_logic.parse_content(text)
            sets += ["content=?", "priority=?", "due=?"]
            args += [parsed["content"], parsed["priority"], parsed["due"]]
        if "done" in body:
            now = int(time.time())
            sets.append("done=?")
            args.append(1 if body["done"] else 0)
            sets.append("done_at=?")
            args.append(now if body["done"] else None)
        if not sets:
            raise HTTPException(422, "没有可更新字段")
        with tx(conn):
            conn.execute(f"UPDATE todos SET {','.join(sets)} WHERE id=?", (*args, tid))
        return dict(conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone())
    finally:
        conn.close()


@router.delete("/todos/{tid}")
def delete_todo(tid: int) -> dict:
    conn = connect()
    try:
        with tx(conn):
            cur = conn.execute("DELETE FROM todos WHERE id=?", (tid,))
        return {"ok": cur.rowcount > 0}
    finally:
        conn.close()


@router.post("/todos/{tid}/run")
def run_todo(tid: int, bg: BackgroundTasks) -> dict:
    """转任务：按待办内容走既有分类执行链（不绕网关；L1 能力照常审批）"""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "待办不存在")
        if row["done"]:
            raise HTTPException(409, "已完成待办无需转任务")
        from ..orchestrator import core as orchestrator
        task_id = orchestrator.create_task(conn, row["content"])
        with tx(conn):
            conn.execute("UPDATE todos SET task_id=? WHERE id=?", (task_id, tid))
        bg.add_task(_execute, task_id)
        return {"ok": True, "task_id": task_id}
    finally:
        conn.close()


async def _execute(task_id: int) -> None:
    from ..orchestrator import core as orchestrator
    await orchestrator.execute(task_id)
