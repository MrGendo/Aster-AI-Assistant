# 回归评测 API：用例 CRUD / 执行 / 准出凭据 / 教训沉淀（QA 平台化 §A §C）
from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..db import connect, tx
from ..evaluation import regression

router = APIRouter(tags=["evals"])


@router.get("/eval/cases")
def list_cases(capability: str | None = None) -> list[dict]:
    conn = connect()
    try:
        if capability:
            rows = conn.execute(
                "SELECT * FROM eval_cases WHERE capability IN ('*', ?) ORDER BY id",
                (capability,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM eval_cases ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/eval/cases")
def add_case(body: dict) -> dict:
    import time as _t
    cap = (body.get("capability") or "*").strip()
    name = (body.get("name") or "").strip()
    goal = (body.get("goal") or "").strip()
    if not name or not goal:
        raise HTTPException(422, "name 与 goal 必填")
    conn = connect()
    try:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO eval_cases(capability, name, goal, expect, source, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (cap, name, goal, (body.get("expect") or regression.DEFAULT_EXPECT).strip(),
                 body.get("source") or "manual", int(_t.time())))
        return dict(conn.execute("SELECT * FROM eval_cases WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


@router.delete("/eval/cases/{cid}")
def del_case(cid: int) -> dict:
    conn = connect()
    try:
        with tx(conn):
            cur = conn.execute("DELETE FROM eval_cases WHERE id=?", (cid,))
        return {"ok": cur.rowcount > 0}
    finally:
        conn.close()


@router.post("/eval/run")
async def run_cases(body: dict, bg: BackgroundTasks) -> dict:
    """执行回归（真实任务通道，费用敏感）。capability 缺省全局 '*'"""
    from ..orchestrator import core as orch  # noqa: F401（说明：run_eval 内部使用）
    conn = connect()
    try:
        capability = (body.get("capability") or "*").strip()
        return await regression.run_eval(conn, capability)
    finally:
        conn.close()


@router.get("/eval/gate/{capability}")
def gate_status(capability: str) -> dict:
    conn = connect()
    try:
        ok, reason = regression.regression_ok(conn, capability)
        return {"capability": capability, "ok": ok, "reason": reason}
    finally:
        conn.close()


@router.get("/lessons")
def list_lessons() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM integration_lessons ORDER BY id DESC LIMIT 50").fetchall()]
    finally:
        conn.close()


@router.post("/lessons/{lid}/to_case")
def lesson_to_case(lid: int, body: dict | None = None) -> dict:
    """教训 → 回归用例（经验驱动→规则化）：LLM 从教训生成 goal+expect，失败可手工传参"""
    import time as _t
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM integration_lessons WHERE id=?", (lid,)).fetchone()
        if not row:
            raise HTTPException(404, "教训不存在")
        body = body or {}
        goal, expect = body.get("goal"), body.get("expect")
        if not (goal and expect):
            from ..providers import router as provider
            if not provider.available():
                raise HTTPException(422, "模型不可用且未手工提供 goal/expect")
            raw = provider.chat(
                [{"role": "user",
                  "content": f"仓库：{row['repo_url']}\n失败阶段：{row['stage_failed']}\n原因：{row['reason']}"}],
                system="把这条集成教训转成一条可回归执行的验收用例。只输出 JSON："
                       '{"goal":"可直接作为任务目标执行的中文指令（如：分析某公开仓库）",'
                       '"expect":"验收标准（怎样算通过）"}。goal 必须能用现有能力独立完成。',
                max_tokens=300)
            import json as _j
            d = _j.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            goal, expect = d["goal"], d["expect"]
        capability = (body.get("capability") or "*").strip()
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO eval_cases(capability, name, goal, expect, source, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (capability, f"教训#{lid}", goal, expect, "lesson", int(_t.time())))
        return dict(conn.execute("SELECT * FROM eval_cases WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()
