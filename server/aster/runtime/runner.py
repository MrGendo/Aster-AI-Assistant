# 能力运行时：worker 子进程隔离执行
# 协议：JSON 进 stdin → JSON 出 stdout，退出码表达结果类别；进程组终止防孤儿（Phase 0 PoC-2 治理模式）
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from ..config import CAPABILITIES_DIR, DATA_DIR, RUNS_DIR, SANDBOX_DIR, WORKER_PYTHON
from ..db import tx
from ..gateway import core as gateway
from ..log import get_logger

log = get_logger("runner")

# worker 退出码约定（与 capabilities/*/run.py 保持一致）
EXIT_OK = 0
EXIT_INVALID_INPUT = 4
EXIT_TOO_LARGE = 3
EXIT_FAILED = 2


class RunResult:
    def __init__(self, ok: bool, exit_code: int, output: dict, error: str = "", duration_ms: int = 0):
        self.ok, self.exit_code, self.output, self.error = ok, exit_code, output, error
        self.duration_ms = duration_ms


def run_capability(
    conn,
    cap_row,
    task_id: int | None,
    step_id: int | None,
    payload: dict,
    timeout_s: int = 300,
) -> RunResult:
    """执行一个能力。执行前过网关（风险分级/审批/暂停检查），结果与审计落库。

    返回 RunResult；被网关拦下时 ok=False 且 error 给出可直接展示给用户的原因。
    """
    name, risk, entry = cap_row["name"], cap_row["risk_level"], cap_row["entry"]

    verdict = gateway.check(conn, name, risk, task_id, json.dumps(payload, ensure_ascii=False)[:200])
    if verdict == "paused":
        return RunResult(False, 0, {}, "系统处于暂停状态，该能力暂不执行（可在设置中恢复）")
    if verdict == "pending":
        return RunResult(False, 0, {}, f"能力 {name}（风险 {risk}）需要你的确认，请在待办中批准")
    if verdict == "denied":
        return RunResult(False, 0, {}, f"能力 {name} 的执行已被拒绝或审批超时")

    # 每次运行独立沙箱与输出目录，互不污染
    run_stamp = f"t{task_id or 'adhoc'}_{int(time.time() * 1000)}"
    sandbox = SANDBOX_DIR / name / run_stamp
    out_dir = RUNS_DIR / run_stamp
    sandbox.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    run_id = None
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO runs(task_id, step_id, capability_id, started_at) VALUES(?,?,?,?)",
            (task_id, step_id, cap_row["id"], int(started)),
        )
        run_id = cur.lastrowid

    # 入口解析：_drafts/ 前缀指向数据目录下的适配草稿（生成能力），其余为内置能力
    if entry.startswith("_drafts/"):
        script = DATA_DIR / "drafts" / entry[len("_drafts/"):]
    else:
        script = CAPABILITIES_DIR / entry

    proc = subprocess.Popen(
        [str(WORKER_PYTHON), str(script)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,  # 独立进程组：超时可整组终止
        env={**os.environ, "ASTER_SANDBOX": str(sandbox)},
    )
    try:
        stdout, stderr = proc.communicate(json.dumps(payload, ensure_ascii=False), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # 先礼后兵：SIGTERM 整组，5 秒后 SIGKILL（取消语义与 PoC-5 的 143 一致）
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        stdout, stderr = "", "执行超时被终止"

    duration_ms = int((time.time() - started) * 1000)

    # 输出协议：stdout 最后一行 JSON 为结果体；此前允许 worker 打印日志行
    output, error = {}, stderr.strip()
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                output = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    ok = proc.returncode == EXIT_OK and bool(output)

    # 大输出落盘，库里只存引用（PRD §16.2 交付物管理）
    output_path = out_dir / "result.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    with tx(conn):
        conn.execute(
            "UPDATE runs SET exit_code=?, duration_ms=?, output_path=?, error=?, finished_at=? WHERE id=?",
            (proc.returncode, duration_ms, str(output_path), error[:500] or None, int(time.time()), run_id),
        )
        conn.execute(
            "UPDATE tool_calls SET status=?, finished_at=? WHERE id=( "
            "SELECT id FROM tool_calls WHERE run_id=? ORDER BY id DESC LIMIT 1)",
            ("success" if ok else "failed", int(time.time()), run_id),
        )
    if not ok and not error:
        error = f"能力 {name} 执行失败（exit={proc.returncode}）"
    log.info("能力 %s 完成 ok=%s duration=%sms", name, ok, duration_ms)
    return RunResult(ok, proc.returncode, output, error, duration_ms)
