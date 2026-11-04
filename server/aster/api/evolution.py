# 进化中心 API：提案/实验/应用/回滚（PRD §14.2）
import json

from fastapi import APIRouter, HTTPException

from ..db import connect, tx
from ..evolution import service as evo

router = APIRouter(tags=["evolution"])


@router.post("/evolution/analyze")
def analyze() -> dict:
    """触发一次瓶颈识别（规则+LLM 归因，产出 draft 提案）"""
    conn = connect()
    try:
        return {"ok": True, "proposals": evo.detect(conn)}
    finally:
        conn.close()


@router.get("/proposals")
def proposals() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM improvement_proposals ORDER BY id DESC LIMIT 50")]
    finally:
        conn.close()


@router.post("/proposals/{pid}/experiment")
def experiment(pid: int) -> dict:
    """回放实验（会产生真实能力/LLM 调用，费用敏感）"""
    conn = connect()
    try:
        return evo.run_replay(conn, pid)
    finally:
        conn.close()


@router.post("/proposals/{pid}/apply")
def apply_proposal(pid: int, body: dict | None = None) -> dict:
    actor = "human" if (body or {}).get("human") else "system"
    conn = connect()
    try:
        return evo.apply(conn, pid, actor=actor)
    finally:
        conn.close()


@router.post("/proposals/{pid}/rollback")
def rollback_proposal(pid: int) -> dict:
    conn = connect()
    try:
        return evo.rollback(conn, pid)
    finally:
        conn.close()


@router.patch("/proposals/{pid}/acceptance")
def edit_acceptance(pid: int, body: dict) -> dict:
    """验收标准唯一人工修改出口（系统侧只读不变量的显式例外口）"""
    if not (body.get("acceptance") or "").strip():
        raise HTTPException(422, "acceptance 不能为空")
    conn = connect()
    try:
        with tx(conn):
            cur = conn.execute(
                "UPDATE improvement_proposals SET acceptance_frozen=? WHERE id=?",
                (body["acceptance"].strip(), pid),
            )
        return {"ok": bool(cur.rowcount), "by": "human"}
    finally:
        conn.close()


@router.get("/replay-set")
def replay_set() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM replay_set")]
    finally:
        conn.close()
