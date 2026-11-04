# 能力中心 API：列表/详情/启停（候选与正式状态对 UI 透明呈现）
from fastapi import APIRouter, HTTPException

from ..db import connect

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities")
def list_caps() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, name, kind, descriptor_json, status, source, version, risk_level, "
            "created_at, updated_at FROM capabilities ORDER BY status, name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/capabilities/{name}/enable")
def enable_cap(name: str) -> dict:
    return _set_status(name, "active")


@router.post("/capabilities/{name}/disable")
def disable_cap(name: str) -> dict:
    return _set_status(name, "disabled")


def _set_status(name: str, status: str) -> dict:
    conn = connect()
    try:
        import time
        from ..db import tx
        with tx(conn):
            cur = conn.execute(
                "UPDATE capabilities SET status=?, updated_at=? WHERE name=?",
                (status, int(time.time()), name),
            )
        if cur.rowcount == 0:
            raise HTTPException(404, f"能力 {name} 不存在")
        return {"ok": True, "name": name, "status": status}
    finally:
        conn.close()
