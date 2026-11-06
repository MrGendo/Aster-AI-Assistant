# 进化中心 API：提案/实验/应用/回滚（PRD §14.2）
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException

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
async def experiment(pid: int) -> dict:
    """回放实验 v2：真实执行回放集（会产生能力/LLM 调用）"""
    conn = connect()
    try:
        return await evo.run_replay(conn, pid)
    finally:
        conn.close()


@router.post("/proposals/{pid}/apply")
def apply_proposal(pid: int, bg: BackgroundTasks, body: dict | None = None) -> dict:
    actor = "human" if (body or {}).get("human") else "system"
    conn = connect()
    try:
        r = evo.apply(conn, pid, actor=actor)
        # 准出闭环（QA 平台化）：应用成功后后台自动跑全局回归，
        # 通过率低于 evo.regression_floor（默认 0.6）→ 自动回滚 + 通知
        if r.get("ok"):
            bg.add_task(_post_apply_regression, pid)
        return r
    finally:
        conn.close()


async def _post_apply_regression(pid: int) -> None:
    import asyncio as _a
    import json as _j
    conn = connect()
    try:
        from ..evaluation import regression
        result = await regression.run_eval(conn, "*")
        if not result.get("ok"):
            return
        floor_row = conn.execute(
            "SELECT value FROM settings WHERE key='evo.regression_floor'").fetchone()
        try:
            floor = float(floor_row["value"]) if floor_row else 0.6
        except (TypeError, ValueError):
            floor = 0.6
        ran, ok_n = result["ran"], int(result["pass_rate"].split("/")[0])
        rate = ok_n / ran if ran else 1.0
        if rate < floor:
            evo.rollback(conn, pid)
            from .. import scheduler as job_sched
            job_sched._notify("task_done",
                              f"提案 #{pid} 应用后回归通过率 {result['pass_rate']} 低于门槛 "
                              f"{floor:.0%}，已自动回滚")
        else:
            with tx(conn):
                conn.execute(
                    "UPDATE improvement_proposals SET experiment_result_json=? WHERE id=?",
                    (_j.dumps({"post_apply_regression": result["pass_rate"],
                               "quality": result.get("quality_pass_rate")},
                              ensure_ascii=False), pid))
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
