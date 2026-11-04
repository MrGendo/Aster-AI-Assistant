# 记忆 API：CRUD + 确认闸（PRD §11.3 用户可查改删）
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import connect
from ..memory import service as mem

router = APIRouter(tags=["memories"])


class MemoryIn(BaseModel):
    scope: str
    content: str
    source_type: str = "manual"
    source_ref: str | None = None


@router.get("/memories")
def list_mem(scope: str | None = None, status: str | None = None) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in mem.list_memories(conn, scope, status)]
    finally:
        conn.close()


@router.post("/memories")
def add_mem(body: MemoryIn) -> dict:
    if body.scope not in mem.SCOPES:
        raise HTTPException(422, f"scope 必须是 {mem.SCOPES} 之一")
    conn = connect()
    try:
        # 手工创建即用户确认（auto_activate），任务沉淀才走 candidate
        mid = mem.add(conn, body.scope, body.content, body.source_type, body.source_ref,
                      auto_activate=True)
        return {"id": mid}
    finally:
        conn.close()


@router.post("/memories/{mid}/confirm")
def confirm_mem(mid: int) -> dict:
    conn = connect()
    try:
        return {"ok": mem.confirm(conn, mid)}
    finally:
        conn.close()


@router.post("/memories/{mid}/reject")
def reject_mem(mid: int) -> dict:
    conn = connect()
    try:
        return {"ok": mem.reject(conn, mid)}
    finally:
        conn.close()


@router.patch("/memories/{mid}")
def update_mem(mid: int, body: dict) -> dict:
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(422, "content 不能为空")
    conn = connect()
    try:
        if not mem.update(conn, mid, content):
            raise HTTPException(404, "记忆不存在或已删除")
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/memories/{mid}")
def delete_mem(mid: int) -> dict:
    conn = connect()
    try:
        return {"ok": mem.reject(conn, mid)}
    finally:
        conn.close()
