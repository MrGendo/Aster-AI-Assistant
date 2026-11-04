# 任务 API：创建/查询/取消/重试 + SSE 进度
import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from ..db import connect
from ..orchestrator import core as orchestrator

router = APIRouter(tags=["tasks"])


@router.post("/tasks")
def create_task(body: dict, bg: BackgroundTasks) -> dict:
    goal = (body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(422, "goal 不能为空")
    if len(goal) > 4000:
        raise HTTPException(422, "goal 过长")
    conn = connect()
    try:
        task_id = orchestrator.create_task(conn, goal, body.get("project_id"))
    finally:
        conn.close()
    bg.add_task(_run_task, task_id)
    return {"id": task_id}


async def _run_task(task_id: int) -> None:
    await orchestrator.execute(task_id)


@router.get("/tasks")
def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT id, goal, intent, status, cancel_requested, result_summary, error, "
                "created_at, updated_at FROM tasks WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, goal, intent, status, cancel_requested, result_summary, error, "
                "created_at, updated_at FROM tasks ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict:
    conn = connect()
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        steps = conn.execute(
            "SELECT * FROM task_steps WHERE task_id=? ORDER BY seq", (task_id,)
        ).fetchall()
        d = dict(task)
        d["steps"] = [dict(s) for s in steps]
        if d.get("result_ref"):
            try:
                d["result_detail"] = json.loads(open(d["result_ref"]).read())
            except (OSError, json.JSONDecodeError):
                pass
        return d
    finally:
        conn.close()


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int) -> dict:
    conn = connect()
    try:
        ok = orchestrator.request_cancel(conn, task_id)
        return {"ok": ok, "note": "已记录取消请求；状态将在执行实际停止后更新" if ok else "任务已结束或不可取消"}
    finally:
        conn.close()


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: int, bg: BackgroundTasks) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        if row["status"] not in ("failed", "cancelled", "partial", "awaiting_user", "pending"):
            # awaiting_user：审批后续跑；pending：后台任务崩溃后的自愈重试（都曾是真的坑）
            raise HTTPException(409, "仅失败/取消/部分完成/等待确认/待处理的任务可重试")
        from .. import tsm as tsm_mod
        with conn:
            # 复用原任务：回置待处理并清掉错误与取消标志（新一次执行的历史在 runs 里可追溯）
            conn.execute(
                "UPDATE tasks SET status=?, error=NULL, cancel_requested=0, updated_at=? WHERE id=?",
                (tsm_mod.PENDING, int(__import__("time").time()), task_id),
            )
            conn.execute("DELETE FROM task_steps WHERE task_id=?", (task_id,))
            goal = conn.execute("SELECT goal FROM tasks WHERE id=?", (task_id,)).fetchone()["goal"]
        bg.add_task(_recreate_and_run, task_id, goal)
        return {"ok": True, "id": task_id}
    finally:
        conn.close()


async def _recreate_and_run(task_id: int, goal: str) -> None:
    # 简化重试：直接按原目标重新规划执行（沿用同一任务行）
    import time as _t
    conn = connect()
    try:
        intent = orchestrator.classify(goal)
        with conn:
            conn.execute("UPDATE tasks SET intent=?, updated_at=? WHERE id=?",
                         (intent, int(_t.time()), task_id))
            if intent in orchestrator.TEMPLATES:
                conn.execute(
                    "INSERT INTO task_steps(task_id, seq, step_desc, status) VALUES(?,?,?,?)",
                    (task_id, 1, f"调用能力 {orchestrator.TEMPLATES[intent]}", "pending"),
                )
    finally:
        conn.close()
    await orchestrator.execute(task_id)


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: int) -> StreamingResponse:
    """SSE：轮询任务行变化推送（Phase 1 轮询够用，后续换事件总线）"""
    import time

    async def gen():
        last = None
        deadline = time.time() + 3600
        while time.time() < deadline:
            conn = connect()
            try:
                row = conn.execute(
                    "SELECT status, result_summary, error, updated_at FROM tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
            finally:
                conn.close()
            if row:
                snap = dict(row)
                if snap != last:
                    last = snap
                    yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                from ..tsm import TERMINAL
                if snap["status"] in TERMINAL:
                    yield "data: [DONE]\n\n"
                    return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")
