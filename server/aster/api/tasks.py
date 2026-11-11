# 任务 API：创建/查询/取消/重试 + SSE 进度
import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

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
def list_tasks(status: str | None = None, query: str | None = None, limit: int = 50) -> list[dict]:
    """列表：可按状态过滤、按关键词搜目标/结果/错误文本"""
    conn = connect()
    try:
        sql = ("SELECT id, goal, intent, status, cancel_requested, result_summary, error, "
               "created_at, updated_at FROM tasks WHERE 1=1")
        args: list = []
        if status:
            sql += " AND status=?"; args.append(status)
        if query:
            sql += " AND (goal LIKE ? OR result_summary LIKE ? OR error LIKE ?)"
            args += [f"%{query}%"] * 3
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
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
        if not ok:
            return {"ok": False, "note": "任务已结束或不可取消"}
        # awaiting_user 无执行循环，取消即时生效——文案按实际结果说
        st = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        return {"ok": True,
                "note": "已取消" if st and st["status"] == "cancelled"
                else "已记录取消请求；状态将在执行实际停止后更新"}
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
            raise HTTPException(409, "仅失败/取消/部分完成/等待确认/待处理的任务可重试")
        if orchestrator.is_running(task_id):
            raise HTTPException(409, "任务正在执行中，等它到终态或审批口再重试")
        from .. import tsm as tsm_mod
        with conn:
            # 复用原任务：回置待处理并清掉错误与取消标志（新一次执行的历史在 runs 里可追溯）
            conn.execute(
                "UPDATE tasks SET status=?, error=NULL, cancel_requested=0, updated_at=? WHERE id=?",
                (tsm_mod.PENDING, int(__import__("time").time()), task_id),
            )
            # 删步骤前先解绑 runs 引用（网关拦截也落 runs.step_id，直接删会撞外键——与 DELETE 任务同款坑）
            conn.execute(
                "UPDATE runs SET step_id=NULL WHERE step_id IN "
                "(SELECT id FROM task_steps WHERE task_id=?)", (task_id,),
            )
            # evolve 意图保留步骤：断点续跑依赖已完成阶段的状态（审批通过后的
            # 重试若清步骤会重烧搜索/文档/生成五个阶段并循环要审批）
            _intent = conn.execute("SELECT intent FROM tasks WHERE id=?",
                                   (task_id,)).fetchone()["intent"]
            if _intent not in ("evolve", "self_improve"):
                conn.execute("DELETE FROM task_steps WHERE task_id=?", (task_id,))
            else:
                conn.execute("UPDATE task_steps SET status='pending' WHERE task_id=? AND status IN "
                             "('running','failed')", (task_id,))
            goal = conn.execute("SELECT goal FROM tasks WHERE id=?", (task_id,)).fetchone()["goal"]
        bg.add_task(_recreate_and_run, task_id, goal)
        return {"ok": True, "id": task_id}
    finally:
        conn.close()


async def _recreate_and_run(task_id: int, goal: str) -> None:
    # 简化重试：直接按原目标重新规划执行（沿用同一任务行）。
    # evolve 例外：保留库内 intent 与已完成步骤——断点续跑（重分类会把进化请求
    # 重新落到 chat/deep_research，且清步骤=重烧五阶段+循环要审批）
    import time as _t
    conn = connect()
    try:
        current = conn.execute("SELECT intent FROM tasks WHERE id=?", (task_id,)).fetchone()
        if current and current["intent"] in ("evolve", "self_improve"):
            intent = current["intent"]
        else:
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


def _delete_one(conn, task_id: int) -> None:
    """单任务删除内核（单删/批删共用）：审计行保留，任务/步骤引用置空解除外键"""
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    if row["status"] in ("executing", "planning", "verifying"):
        raise HTTPException(409, f"任务 {task_id} 运行中，请先取消再删除")
    with conn:
        # 注意 runs.step_id 也引用 task_steps——漏掉它会 FK 失败（实测教训）
        conn.execute("UPDATE runs SET task_id=NULL, step_id=NULL WHERE task_id=?", (task_id,))
        conn.execute("UPDATE tool_calls SET task_id=NULL WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM task_steps WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int) -> dict:
    conn = connect()
    try:
        _delete_one(conn, task_id)
        return {"ok": True}
    finally:
        conn.close()


@router.post("/tasks/batch_delete")
def batch_delete(body: dict) -> dict:
    """批量删除：逐个走同一校验（运行中拒绝），单条失败不影响其余，返回逐项结果"""
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids or len(ids) > 200:
        raise HTTPException(422, "ids 需为非空数组（≤200）")
    conn = connect()
    results = []
    try:
        for tid in ids:
            try:
                _delete_one(conn, int(tid))
                results.append({"id": tid, "ok": True})
            except HTTPException as e:
                results.append({"id": tid, "ok": False, "error": e.detail})
        ok_n = sum(1 for r in results if r["ok"])
        return {"ok": True, "deleted": ok_n, "failed": len(results) - ok_n, "detail": results}
    finally:
        conn.close()


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: dict) -> dict:
    """编辑任务目标（仅非运行中状态；改完可重试生效）"""
    goal = (body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(422, "goal 不能为空")
    conn = connect()
    try:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        if row["status"] in ("executing", "planning", "verifying"):
            raise HTTPException(409, "运行中的任务不可编辑")
        with conn:
            conn.execute("UPDATE tasks SET goal=?, updated_at=? WHERE id=?",
                         (goal, int(__import__("time").time()), task_id))
        return {"ok": True}
    finally:
        conn.close()


@router.get("/steps/{step_id}/io")
def step_io(step_id: int) -> dict:
    """步骤级输入输出明细（trace 视图数据源）：输入取 input_json，输出读落盘文件前 2000 字"""
    import json as _json
    from pathlib import Path
    conn = connect()
    try:
        s = conn.execute("SELECT * FROM task_steps WHERE id=?", (step_id,)).fetchone()
        if not s:
            raise HTTPException(404, "步骤不存在")
        output = ""
        if s["output_ref"]:
            try:
                raw = Path(s["output_ref"]).read_text(encoding="utf-8", errors="ignore")
                output = raw[:2000]
            except OSError:
                # 计划路径的 output_ref 是内联文本而非文件——直接展示本体
                output = str(s["output_ref"])[:2000]
        return {
            "id": s["id"], "seq": s["seq"], "desc": s["step_desc"], "status": s["status"],
            "input": s["input_json"] or "", "output": output, "error": s["error"],
            "started_at": s["started_at"], "finished_at": s["finished_at"],
        }
    finally:
        conn.close()


@router.get("/tasks/{task_id}/file")
def task_file(task_id: int) -> FileResponse:
    """下载任务的落盘产物（报告/结果 JSON）。路径服务端从库里取并校验在数据目录内——
    不接受客户端传路径，杜绝目录穿越。"""
    from pathlib import Path
    from ..config import DATA_DIR
    conn = connect()
    try:
        row = conn.execute("SELECT result_ref FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or not row["result_ref"]:
            raise HTTPException(404, "该任务无可下载产物")
        p = Path(row["result_ref"]).resolve()
        if not str(p).startswith(str(DATA_DIR.resolve())):
            raise HTTPException(403, "路径越界")
        if not p.exists():
            raise HTTPException(404, "文件不存在")
        return FileResponse(p, filename=p.name)
    finally:
        conn.close()


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
