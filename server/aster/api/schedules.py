# 定时调度 API：schedules CRUD + notifications
from fastapi import APIRouter, HTTPException

from .. import scheduler as job_sched
from ..db import connect, tx

router = APIRouter(tags=["schedules"])


def _row(conn, sid):
    r = conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "调度不存在")
    return r


@router.get("/schedules")
def list_schedules() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM schedules ORDER BY id DESC").fetchall()]
    finally:
        conn.close()


@router.post("/schedules")
def create_schedule(body: dict) -> dict:
    """创建调度（解析逻辑在 scheduler.create，与对话入口共用）"""
    goal = (body.get("goal") or "").strip()
    when = (body.get("when") or body.get("goal") or "").strip()
    kind = body.get("kind") or "task"
    if not goal or kind not in ("task", "alarm"):
        raise HTTPException(422, "需要 goal 与 kind(task|alarm)")
    row, err = job_sched.create(kind, goal, when)
    if err:
        raise HTTPException(422, err)
    return row


@router.patch("/schedules/{sid}")
def toggle_schedule(sid: int, body: dict) -> dict:
    import json as _json
    conn = connect()
    try:
        row = _row(conn, sid)
        if "enabled" not in body:
            raise HTTPException(422, "只支持 enabled 开关")
        enabled = 1 if body["enabled"] else 0
        tj = _json.loads(row["trigger_json"])
        next_ts = job_sched.set_enabled(sid, tj, bool(enabled))
        with tx(conn):
            conn.execute("UPDATE schedules SET enabled=?, next_run_at=? WHERE id=?",
                         (enabled, next_ts, sid))
        return dict(_row(conn, sid))
    finally:
        conn.close()


@router.delete("/schedules/{sid}")
def delete_schedule(sid: int) -> dict:
    conn = connect()
    try:
        _row(conn, sid)
        with tx(conn):
            conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
        job_sched.remove(sid)
        return {"ok": True}
    finally:
        conn.close()


@router.get("/notifications")
def list_notifications(unread: int = 0, limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        where = "WHERE read=0" if unread else ""
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


@router.post("/notifications/read")
def read_notifications(body: dict) -> dict:
    """按 id 批量已读；ids 为空数组则全部已读"""
    conn = connect()
    try:
        with tx(conn):
            if body.get("ids"):
                marks = ",".join("?" * len(body["ids"]))
                conn.execute(f"UPDATE notifications SET read=1 WHERE id IN ({marks})",
                             tuple(body["ids"]))
            else:
                conn.execute("UPDATE notifications SET read=1")
        return {"ok": True}
    finally:
        conn.close()
