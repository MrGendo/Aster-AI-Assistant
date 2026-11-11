# 系统 API：状态/暂停恢复/审批/审计
from fastapi import APIRouter, HTTPException

from ..db import connect
from ..gateway import core as gateway

router = APIRouter(tags=["system"])


@router.get("/system/status")
def status() -> dict:
    conn = connect()
    try:
        from ..providers import router as provider
        # 启用数＝status='active' 的能力条数（enable/disable 端点即写 active/disabled）
        enabled = conn.execute(
            "SELECT COUNT(*) FROM capabilities WHERE status='active'"
        ).fetchone()[0]
        return {
            "paused": gateway.is_paused(conn),
            "provider_ready": provider.available(),
            "provider": _safe_provider_info(conn),
            "enabledCapabilities": enabled,
            "db": str(__import__("aster.config", fromlist=["DB_PATH"]).DB_PATH),
        }
    finally:
        conn.close()


def _safe_provider_info(conn) -> dict:
    """只暴露非敏感信息：是否有密钥、端点域名、模型名"""
    from ..providers.router import provider_config
    cfg = provider_config()
    key = cfg["api_key"]
    base = cfg["base_url"] or "官方默认"
    return {"has_key": bool(key), "key_tail": key[-4:] if key else "", "base_url": base,
            "model": cfg["model"]}


@router.post("/system/pause")
def pause() -> dict:
    """紧急停止：L1/L2 全部挂起（L0 只读仍放行），重启后保持暂停待确认"""
    conn = connect()
    try:
        gateway.set_setting(conn, "system.paused", "1")
        return {"ok": True, "paused": True}
    finally:
        conn.close()


@router.post("/system/resume")
def resume() -> dict:
    conn = connect()
    try:
        gateway.set_setting(conn, "system.paused", "0")
        return {"ok": True, "paused": False}
    finally:
        conn.close()


@router.get("/approvals")
def approvals(status: str = "pending") -> list[dict]:
    conn = connect()
    try:
        if status == "pending":
            rows = conn.execute(
                "SELECT a.id, a.risk, a.reason, a.requested_at, tc.capability, tc.task_id "
                "FROM approvals a JOIN tool_calls tc ON tc.id=a.tool_call_id "
                "WHERE a.decision IS NULL ORDER BY a.id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT a.*, tc.capability, tc.task_id FROM approvals a "
                "JOIN tool_calls tc ON tc.id=a.tool_call_id ORDER BY a.id DESC LIMIT 100"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/approvals/{aid}/decide")
def decide_approval(aid: int, body: dict) -> dict:
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision 必须是 approved/rejected")
    conn = connect()
    try:
        if not gateway.decide(conn, aid, decision):
            raise HTTPException(404, "审批单不存在")
        return {"ok": True}
    finally:
        conn.close()


@router.get("/audit")
def audit(limit: int = 100, capability: str | None = None) -> list[dict]:
    conn = connect()
    try:
        if capability:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE capability=? ORDER BY id DESC LIMIT ?",
                (capability, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tool_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
