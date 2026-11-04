# 发现与评估 API（Stage 2）：候选看板 / 评估 / 草稿 / 隔离测试 / 接入 / 版本 / 巡检
import json
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..db import connect, tx
from ..evaluation import lifecycle, pipeline
from ..integration import scaffold
from ..log import get_logger

log = get_logger("api:discovery")
router = APIRouter(tags=["discovery"])


@router.post("/candidates")
def submit_candidate(body: dict, bg: BackgroundTasks) -> dict:
    """提交仓库链接：登记候选（幂等）→ 后台跑评估流水线"""
    url = (body.get("repo_url") or "").strip().rstrip("/")
    if not url.startswith("https://github.com/"):
        raise HTTPException(422, "需要 https://github.com/<owner>/<repo> 链接")
    conn = connect()
    try:
        cid = pipeline.register_candidate(conn, url, trigger="user")
        bg.add_task(_run_evaluate, cid)
        return {"id": cid}
    finally:
        conn.close()


async def _run_evaluate(cid: int) -> None:
    conn = connect()
    try:
        await pipeline.evaluate(conn, cid)
    except Exception as e:
        log.exception("候选 %s 评估失败", cid)
        _record_lesson(cid, "evaluate", str(e))
    finally:
        conn.close()


def _record_lesson(cid: int, stage: str, reason: str) -> None:
    conn = connect()
    try:
        row = conn.execute("SELECT repo_url FROM candidates WHERE id=?", (cid,)).fetchone()
        if row:
            with tx(conn):
                conn.execute(
                    "INSERT INTO integration_lessons(repo_url, stage_failed, reason, created_at) "
                    "VALUES(?,?,?,?)", (row["repo_url"], stage, reason[:500], int(time.time())))
    finally:
        conn.close()


@router.get("/candidates")
def list_candidates() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT c.*, (SELECT recommendation FROM evaluation_reports r "
            "  WHERE r.candidate_id=c.id ORDER BY r.id DESC LIMIT 1) AS recommendation "
            "FROM candidates c ORDER BY c.updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/candidates/{cid}/report")
def candidate_report(cid: int) -> dict:
    conn = connect()
    try:
        r = conn.execute(
            "SELECT * FROM evaluation_reports WHERE candidate_id=? ORDER BY id DESC LIMIT 1",
            (cid,),
        ).fetchone()
        if not r:
            raise HTTPException(404, "尚无评估报告（评估可能仍在进行）")
        d = dict(r)
        for k in ("dimensions_json", "evidence_json", "peers_json"):
            d[k] = json.loads(d.get(k) or "{}")
        return d
    finally:
        conn.close()


@router.post("/candidates/{cid}/scaffold")
def gen_scaffold(cid: int) -> dict:
    """生成适配草稿（调用用户 claude 计划；运行草稿另行 L2 审批）"""
    conn = connect()
    try:
        return scaffold.generate(conn, cid)
    finally:
        conn.close()


@router.get("/candidates/{cid}/draft")
def get_draft(cid: int) -> dict:
    from ..integration.scaffold import DRAFT_DIR
    meta = DRAFT_DIR / str(cid) / "meta.json"
    script = DRAFT_DIR / str(cid) / "adapter.py"
    if not meta.exists():
        raise HTTPException(404, "草稿不存在")
    return json.loads(meta.read_text()) | {"script": script.read_text()}


@router.post("/candidates/{cid}/test_adapter")
def test_adapter(cid: int) -> dict:
    """隔离测试（L2）：执行经人工审阅的草稿并记录 candidate_tests"""
    conn = connect()
    try:
        result = scaffold.run_isolated_test(conn, cid)
        if result.get("ok"):
            try:
                pipeline.advance(conn, cid, lifecycle.ISOLATED_TEST)
                pipeline.advance(conn, cid, lifecycle.INTEGRATION_EVAL)
                pipeline.advance(conn, cid, lifecycle.PENDING_APPROVAL)
            except lifecycle.IllegalLifecycle as e:
                log.warning("生命周期推进跳过: %s", e)
        return result
    finally:
        conn.close()


@router.post("/candidates/{cid}/integrate")
def integrate(cid: int) -> dict:
    """正式接入：candidate→capability（首个版本），生命周期走完并登记版本历史"""
    conn = connect()
    try:
        cand = conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        if not cand:
            raise HTTPException(404, "候选不存在")
        if cand["lifecycle"] != lifecycle.PENDING_APPROVAL:
            raise HTTPException(409, f"候选处于 {cand['lifecycle']}，须先完成隔离测试并等待批准")
        from ..integration.scaffold import DRAFT_DIR
        meta = json.loads((DRAFT_DIR / str(cid) / "meta.json").read_text())

        # 产物入库为 capability（candidate 状态）+ 版本记录；随后置 active 并完成生命周期
        now = int(time.time())
        name = meta["name"]
        with tx(conn):
            conn.execute(
                "INSERT OR IGNORE INTO capabilities(name, kind, descriptor_json, status, source, "
                "version, risk_level, entry, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (name, "tool",
                 json.dumps({"name": meta.get("title", name), "purpose": meta.get("purpose", ""),
                             "input": "见 input_example", "output": "worker stdout JSON",
                             "cost": "本地执行（隔离测试已验证）", "limits": "生成代码，经人工审阅",
                             "origin": cand["repo_url"]}, ensure_ascii=False),
                 "candidate", f"github:{cand['repo_url']}", "0.1.0", "L1",
                 f"_drafts/{cid}/adapter.py", now, now),
            )
            cap_id = conn.execute("SELECT id FROM capabilities WHERE name=?", (name,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO capability_versions(capability_id, version, entry, descriptor_json, "
                "status, created_at) VALUES(?,?,?,?,?,?)",
                (cap_id, "0.1.0", f"_drafts/{cid}/adapter.py",
                 json.dumps(meta, ensure_ascii=False), "candidate", now),
            )
        pipeline.advance(conn, cid, lifecycle.INTEGRATION_VERIFIED)
        pipeline.advance(conn, cid, lifecycle.ACTIVE)
        with tx(conn):
            conn.execute("UPDATE capabilities SET status='active', updated_at=? WHERE id=?",
                         (now, cap_id))
            conn.execute("UPDATE capability_versions SET status='active' WHERE capability_id=? "
                         "AND version='0.1.0'", (cap_id,))
        return {"ok": True, "capability": name, "note": "已接入为正式能力（首个版本）"}
    finally:
        conn.close()


@router.post("/candidates/{cid}/archive")
def archive_candidate(cid: int, body: dict | None = None) -> dict:
    """归档（拒绝/放弃），原因记入经验库"""
    conn = connect()
    try:
        cand = conn.execute("SELECT lifecycle FROM candidates WHERE id=?", (cid,)).fetchone()
        if not cand:
            raise HTTPException(404, "候选不存在")
        try:
            pipeline.advance(conn, cid, lifecycle.ARCHIVED)
        except lifecycle.IllegalLifecycle:
            with tx(conn):
                conn.execute("UPDATE candidates SET lifecycle=?, updated_at=? WHERE id=?",
                             (lifecycle.ARCHIVED, int(time.time()), cid))
        if body and body.get("reason"):
            with tx(conn):
                conn.execute(
                    "INSERT INTO integration_lessons(repo_url, stage_failed, reason, created_at) "
                    "SELECT repo_url, lifecycle, ?, ? FROM candidates WHERE id=?",
                    (body["reason"][:500], int(time.time()), cid))
        return {"ok": True}
    finally:
        conn.close()


@router.get("/capabilities/{name}/versions")
def cap_versions(name: str) -> list[dict]:
    conn = connect()
    try:
        cap = conn.execute("SELECT id FROM capabilities WHERE name=?", (name,)).fetchone()
        if not cap:
            raise HTTPException(404, "能力不存在")
        rows = conn.execute(
            "SELECT * FROM capability_versions WHERE capability_id=? ORDER BY id DESC",
            (cap["id"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/capabilities/{name}/rollback/{version_id}")
def rollback(name: str, version_id: int) -> dict:
    """回滚到历史版本：当前版本归档，目标版本恢复 active 并写回 capabilities"""
    conn = connect()
    try:
        cap = conn.execute("SELECT * FROM capabilities WHERE name=?", (name,)).fetchone()
        target = conn.execute("SELECT * FROM capability_versions WHERE id=? AND capability_id=?",
                              (version_id, cap["id"])).fetchone()
        if not target:
            raise HTTPException(404, "目标版本不存在")
        now = int(time.time())
        with tx(conn):
            conn.execute("UPDATE capability_versions SET status='archived' WHERE capability_id=? "
                         "AND status='active'", (cap["id"],))
            conn.execute("UPDATE capability_versions SET status='active' WHERE id=?", (version_id,))
            conn.execute(
                "UPDATE capabilities SET entry=?, version=?, descriptor_json=?, updated_at=? "
                "WHERE id=?",
                (target["entry"], target["version"], target["descriptor_json"], now, cap["id"]),
            )
        return {"ok": True, "version": target["version"]}
    finally:
        conn.close()


@router.get("/watchlist")
def get_watchlist() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM watchlist ORDER BY id")]
    finally:
        conn.close()


@router.post("/watchlist")
def add_watch(body: dict) -> dict:
    url = (body.get("repo_url") or "").strip().rstrip("/")
    if not url.startswith("https://github.com/"):
        raise HTTPException(422, "需要 GitHub 仓库链接")
    conn = connect()
    try:
        with tx(conn):
            conn.execute(
                "INSERT OR IGNORE INTO watchlist(repo_url, note) VALUES(?,?)",
                (url, body.get("note", "")),
            )
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/watchlist/{wid}")
def del_watch(wid: int) -> dict:
    conn = connect()
    try:
        with tx(conn):
            conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))
        return {"ok": True}
    finally:
        conn.close()


@router.get("/lessons")
def lessons() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM integration_lessons ORDER BY id DESC LIMIT 50")]
    finally:
        conn.close()
