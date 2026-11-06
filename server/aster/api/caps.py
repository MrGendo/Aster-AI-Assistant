# 能力中心 API：列表/详情/启停（候选与正式状态对 UI 透明呈现）
from fastapi import APIRouter, HTTPException

from ..db import connect
from ..evaluation import regression

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
def enable_cap(name: str, force: int = 0) -> dict:
    # 准出门禁（QA 平台化）：最近一次回归失败的能力不可启用；
    # force=1 人工越过（合法通道：越过记录进审计日志，事后补回归）
    conn = connect()
    try:
        ok, reason = regression.regression_ok(conn, name)
    finally:
        conn.close()
    if not ok:
        if not force:
            raise HTTPException(409, f"回归门禁未通过：{reason}。请先执行 /api/eval/run 或 force=1 人工越过（留痕）")
        import time as _t
        from ..db import connect as _c, tx as _tx
        c2 = _c()
        try:
            with _tx(c2):
                c2.execute(
                    "INSERT INTO tool_calls(capability, risk, status, args_digest, "
                    "started_at, finished_at) VALUES('gate_override','L1','success',?,?,?)",
                    (f"人工越过 {name} 启用门禁：{reason}", int(_t.time()), int(_t.time())))
        finally:
            c2.close()
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
