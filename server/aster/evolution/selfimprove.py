# 平台自改（self-improve）：平台自身功能需求的自动实现闭环
# 用户 2026-11-11 需求："放到待办也没有自动进化解决"——补第四档：平台给自己改代码。
# 安全镣铐（不可协商）：
#   ① 只在隔离 git 分支上实现，main 上的代码在审批前零改动
#   ② pytest + 前端构建双门，任一失败废弃分支诚实失败
#   ③ L2 人工审批看 diff 统计与测试结论，批准才合并
#   ④ 应用后由守护脚本重建重启并健康检查，失败自动回滚合并并通知
import asyncio
import json
import subprocess
import time

from ..db import tx
from ..log import get_logger

log = get_logger("selfimprove")

STAGES = [
    ("需求分析", "转成改动说明与验收标准"),
    ("隔离分支", "git 分支 evolve/self-<任务>，main 零风险"),
    ("自动实现", "claude 无头在分支上写码"),
    ("质量门", "pytest + 前端构建，失败即废弃"),
    ("人工审批", "L2：看 diff 统计与测试结论"),
    ("应用与重启", "合并 main → 重建 → 重启 → 健康检查"),
]


def _sh(cmd: list[str], cwd: str = None, timeout: int = 120,
        env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=env)


def ensure_steps(conn, task_id: int) -> None:
    if conn.execute("SELECT 1 FROM task_steps WHERE task_id=?", (task_id,)).fetchone():
        return
    with tx(conn):
        for i, (name, desc) in enumerate(STAGES, 1):
            conn.execute("INSERT INTO task_steps(task_id, seq, step_desc, status) "
                         "VALUES(?,?,?,?)", (task_id, i, f"{name}：{desc}", "pending"))


async def run(conn, task_id: int, message: str) -> None:
    """自改主体：六阶段推进；审批后由守护脚本完成应用与重启（本进程会被重启，
    所以阶段6的收尾状态由脚本回写——任务可能停在 executing，重启后健康即成功）。"""
    from .. import tsm
    from ..config import ROOT
    from ..orchestrator import core as orch
    from ..providers import router as provider

    def _row(seq):
        return conn.execute("SELECT * FROM task_steps WHERE task_id=? AND seq=?",
                            (task_id, seq)).fetchone()

    def _done(seq, out=""):
        with tx(conn):
            conn.execute("UPDATE task_steps SET status='done', output_ref=?, finished_at=? "
                         "WHERE id=?", (str(out)[:2000], int(time.time()), _row(seq)["id"]))

    def _fail(seq, reason):
        with tx(conn):
            conn.execute("UPDATE task_steps SET status='failed', error=?, finished_at=? WHERE id=?",
                         (reason, int(time.time()), _row(seq)["id"]))
        orch._fail(conn, task_id, f"阶段「{STAGES[seq - 1][0]}」失败：{reason}")

    ctx = json.loads(conn.execute("SELECT plan_json FROM tasks WHERE id=?",
                                  (task_id,)).fetchone()["plan_json"] or "{}")
    branch = f"evolve/self-{task_id}"

    orch._set_status(conn, task_id, tsm.EXECUTING)
    for seq, (name, _) in enumerate(STAGES, 1):
        row = _row(seq)
        if row and row["status"] == "done":
            continue
        try:
            if seq == 1:
                spec = ctx.get("spec")
                if not spec:
                    raw = provider.chat(
                        [{"role": "user", "content": message}],
                        system="把这条对 Aster 平台自身的功能需求转成实现说明。只输出 JSON："
                               '{"spec":"改动说明（改哪些文件做什么，中文）",'
                               '"accept":"验收标准（怎么验证实现了）"}',
                        max_tokens=400)
                    spec = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
                    with tx(conn):
                        conn.execute("UPDATE tasks SET plan_json=? WHERE id=?",
                                     (json.dumps(spec, ensure_ascii=False), task_id))
                _done(1, f"改动：{spec['spec'][:300]}\n验收：{spec['accept'][:150]}")
            elif seq == 2:
                r = _sh(["git", "checkout", "-b", branch], cwd=str(ROOT))
                if r.returncode != 0:  # 分支已存在（重试续跑）：直接切过去
                    r = _sh(["git", "checkout", branch], cwd=str(ROOT))
                    if r.returncode != 0:
                        return _fail(2, f"建分支失败：{r.stderr[-150:]}")
                _done(2, f"分支 {branch}")
            elif seq == 3:
                spec = json.loads(conn.execute("SELECT plan_json FROM tasks WHERE id=?",
                                               (task_id,)).fetchone()["plan_json"])
                prompt = (
                    f"在当前仓库实现这个平台功能需求，改动最小化。需求：{message}\n"
                    f"实现说明：{spec.get('spec', message)}\n"
                    "要求：遵循仓库既有代码风格与中文注释规约；不新增依赖除非必需；"
                    "改完确保逻辑自洽。这是已获用户授权的自动化实现任务：直接改文件，"
                    "禁止走 SDD/审批流程、禁止创建 docs 文档、禁止 git 操作。"
                    "完成后只输出一行改动摘要。")
                r = _sh(["claude", "-p", prompt, "--dangerously-skip-permissions"],
                        cwd=str(ROOT), timeout=1200)
                if r.returncode != 0:
                    _sh(["git", "checkout", "main", "&&", "git", "branch", "-D", branch], cwd=str(ROOT))
                    return _fail(3, f"claude 实现失败（退出码 {r.returncode}）：{r.stderr[-200:]}")
                c = _sh(["git", "add", "-A"], cwd=str(ROOT))
                cm = _sh(["git", "-c", "user.name=Aster自进化", "-c", "user.email=aster@local",
                          "commit", "-m", f"自改(#{task_id}): {message[:60]}"], cwd=str(ROOT))
                if cm.returncode != 0 and "nothing to commit" not in (cm.stdout + cm.stderr):
                    return _fail(3, f"提交失败：{cm.stderr[-150:]}")
                _done(3, (r.stdout or "已实现").strip()[:500])
            elif seq == 4:
                pt = _sh([str(ROOT / ".venv/bin/python"), "-m", "pytest", "server/tests", "-q"],
                         cwd=str(ROOT), env={**__import__("os").environ,
                                             "PYTHONPATH": "server"}, timeout=600)
                tests_ok = pt.returncode == 0
                bd = _sh(["pnpm", "build"], cwd=str(ROOT / "web"), timeout=300)
                build_ok = bd.returncode == 0
                diff = _sh(["git", "diff", "main...HEAD", "--stat"], cwd=str(ROOT))
                if not (tests_ok and build_ok):
                    _sh(["git", "checkout", "main"], cwd=str(ROOT))
                    _sh(["git", "branch", "-D", branch], cwd=str(ROOT))
                    why = ("测试未过" if not tests_ok else "") + ("；构建未过" if not build_ok else "")
                    return _fail(4, f"质量门未通过（{why.lstrip('；')}），分支已废弃。"
                                   f"测试尾行：{pt.stdout.strip().splitlines()[-1] if pt.stdout else '?'}")
                _done(4, f"测试通过（{pt.stdout.strip().splitlines()[-1] if pt.stdout else ''}）"
                         f"· 构建通过\ndiff:\n{diff.stdout[:600]}")
            elif seq == 5:
                st4 = _row(4)["output_ref"] or ""
                prev = conn.execute(
                    "SELECT a.decision FROM approvals a JOIN tool_calls tc ON tc.id=a.tool_call_id "
                    "WHERE tc.task_id=? ORDER BY a.id DESC LIMIT 1", (task_id,)).fetchone()
                if prev:
                    if prev["decision"] == "rejected":
                        _sh(["git", "checkout", "main"], cwd=str(ROOT))
                        _sh(["git", "branch", "-D", branch], cwd=str(ROOT))
                        return _fail(5, "用户拒绝该自改，分支已废弃")
                    if prev["decision"] == "approved":
                        _done(5, "审批已通过（历史单）")
                        continue
                with tx(conn):
                    conn.execute("UPDATE task_steps SET status='running' WHERE id=?", (_row(5)["id"],))
                    cur_tc = conn.execute(
                        "INSERT INTO tool_calls(capability, risk, status, args_digest, task_id, started_at) "
                        "VALUES('self_improve','L2','pending',?,?,?) RETURNING id",
                        (f"平台自改：{message[:60]}", task_id, int(time.time()))).fetchone()
                    conn.execute("INSERT INTO approvals(tool_call_id, risk, reason, requested_at) "
                                 "VALUES(?,?,?,?)",
                                 (cur_tc["id"], "L2",
                                  f"平台自改：由 claude 在分支 {branch} 实现「{message[:50]}」。"
                                  f"{st4[:300]} 批准后合并 main 并自动重启。",
                                  int(time.time())))
                try:
                    orch._set_status(conn, task_id, tsm.AWAITING_USER)
                except Exception:
                    pass
                with tx(conn):
                    conn.execute("UPDATE tasks SET error=?, updated_at=? WHERE id=?",
                                 ("等待批准：平台自改（L2，批准后自动合并重启）", int(time.time()), task_id))
                return
            elif seq == 6:
                # 已获批：合并 + 触发守护脚本（脚本负责重建/重启/健康检查/回写状态，
                # 本进程即将被重启，不能在此等待结果）
                mg = _sh(["git", "checkout", "main"], cwd=str(ROOT))
                mg2 = _sh(["git", "merge", "--no-ff", branch, "-m",
                           f"merge 自改(#{task_id}): {message[:50]}"], cwd=str(ROOT))
                if mg2.returncode != 0:
                    return _fail(6, f"合并失败：{mg2.stderr[-150:]}")
                script = ROOT / "tools" / "selfapply.sh"
                subprocess.Popen(
                    ["bash", str(script), str(task_id)],
                    stdout=open("/tmp/selfapply.log", "w"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # 独立进程组：服务重启不影响脚本
                    cwd=str(ROOT))
                _done(6, "已合并 main，守护脚本正在重建重启（结果见通知）")
                # 不落终态：脚本健康检查后回写 completed/failed
                with tx(conn):
                    conn.execute("UPDATE tasks SET result_summary=?, updated_at=? WHERE id=?",
                                 (f"平台自改已合并，正在重启应用…（需求：{message[:60]}）",
                                  int(time.time()), task_id))
                return
        except Exception as e:
            log.exception("自改任务 %s 阶段 %s 异常", task_id, name)
            return _fail(seq, f"{type(e).__name__}: {e}")
    orch._fail(conn, task_id, "自改流程异常结束")
