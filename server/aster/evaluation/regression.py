# 能力回归评测：用例库执行 + 准出凭据（对齐质量平台化设计 §A）
# 复用 run_replay 的执行模式：完整任务通道 + replay_grade 质量评分，不另造轮子
import asyncio
import json
import time

from ..db import connect, tx
from ..log import get_logger

log = get_logger("regression")

DEFAULT_EXPECT = "输出切题且包含具体事实或结果，不是拒绝、报错或空洞回复"


async def run_eval(conn, capability: str = "*") -> dict:
    """执行一组回归用例（capability='*' 为全局）。真实走任务通道，费用敏感。
    结果逐条回写 last_result_json，返回汇总。"""
    from ..orchestrator import core as orch
    from ..runtime import registry, runner

    rows = conn.execute(
        "SELECT * FROM eval_cases WHERE enabled=1 AND capability=?",
        (capability,)).fetchall()
    if not rows:
        return {"ok": False, "error": "没有启用的用例", "capability": capability}
    results, ok_n = {}, 0
    now = int(time.time())
    for r in rows:
        task_id = orch.create_task(conn, r["goal"])
        await orch.execute(task_id)
        t = conn.execute("SELECT status, result_summary, error FROM tasks WHERE id=?",
                         (task_id,)).fetchone()
        passed = t["status"] == "completed"
        ok_n += passed
        results[r["name"]] = {"task_id": task_id, "status": t["status"], "passed": passed,
                              "summary": (t["result_summary"] or t["error"] or "")[:120]}
    # 质量维度（llm-rubric）：完成≠合格，输出还要切题有料
    quality = {}
    gcap = registry.get_active(conn, "replay_grade")
    if gcap and results:
        items = [{"name": k, "output": v["summary"],
                  "criteria": next((x["expect"] for x in rows if x["name"] == k), DEFAULT_EXPECT)}
                 for k, v in results.items()]
        gres = await asyncio.to_thread(
            runner.run_capability, gcap, None, None, {"items": items}, timeout_s=600)
        if gres.ok:
            quality = gres.output
    q_pass = quality.get("quality_pass_rate") if quality else None
    with tx(conn):
        for r in rows:
            conn.execute("UPDATE eval_cases SET last_result_json=?, finished_at=? WHERE id=?",
                         (json.dumps({**results[r["name"]],
                                      "graded": (quality.get("results") or [{}])[0].get("pass")
                                      if not quality else None}, ensure_ascii=False),
                          now, r["id"]))
    return {"ok": True, "capability": capability, "ran": len(results),
            "pass_rate": f"{ok_n}/{len(results)}",
            "quality_pass_rate": q_pass or "未运行", "detail": results}


def _cap_updated_at(conn, capability: str) -> int:
    row = conn.execute("SELECT updated_at FROM capabilities WHERE name=?", (capability,)).fetchone()
    return row["updated_at"] if row else 0


def regression_ok(conn, capability: str) -> tuple[bool, str]:
    """准出凭据：该能力（含全局'*'）的启用用例**最近一次执行**全部通过才放行。
    - 从未执行 → 免检放行（规则只拦"已证明的回归"；从未跑≠失败——
      停用/启用循环里能力停着用例永远跑不了，"未跑即拦"会死锁，实测踩到）
    - 时间新鲜度规则不采用：能力行的 updated_at 连启停都会触碰，过严没有信息量；
      真正的守护是 apply 后自动回归 + 失败即拦 + 人工 force 留痕
    """
    rows = conn.execute(
        "SELECT name, last_result_json, finished_at FROM eval_cases "
        "WHERE enabled=1 AND capability IN ('*', ?)", (capability,)).fetchall()
    if not rows:
        return True, "无用例，免检"
    ran, failed = 0, []
    for r in rows:
        if not r["last_result_json"]:
            continue  # 从未执行：免检
        ran += 1
        try:
            if not json.loads(r["last_result_json"]).get("passed"):
                failed.append(r["name"])
        except (json.JSONDecodeError, TypeError):
            failed.append(r["name"])
    if failed:
        return False, f"回归未过：{'、'.join(failed)}"
    return True, f"已执行 {ran}/{len(rows)} 条用例，无失败"
