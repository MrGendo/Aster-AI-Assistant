# 元能力 adapter_scaffold：用 claude headless 生成能力包装草稿
# PoC-5 教训固化：提示词显式禁用宿主全局规则（SDD/建 docs），输出按"末个 JSON"解析
import json
import re
import subprocess
import time
from pathlib import Path

from ..config import DATA_DIR
from ..log import get_logger

log = get_logger("scaffold")

DRAFT_DIR = DATA_DIR / "drafts"

PROMPT = """你要为 Aster 平台把下面的开源项目包装成一个能力 worker。只输出一个 JSON 对象，不要 markdown 代码围栏，不要任何额外文字：
{{"capability_name": "snake_case名称", "title": "中文能力名", "purpose": "用途一句话",
  "input_example": {{"key": "value"}}, "script": "完整的 python worker 脚本源码"}}
worker 协议：从 stdin 读 JSON，向 stdout 打印结果 JSON（最后一行），退出码 0=成功。
脚本要求：只用标准库；不访问网络除非输入明确要求；不产生任何文件副作用；脚本不超过 80 行，
开头 3-5 行中文注释说明做什么与为什么（要点式，不写长文）。
目标项目：{repo}
项目分析事实：{facts}
这是一次已获用户批准的自动化代码生成任务：直接输出结果，禁止走 SDD 流程、禁止要求审批、禁止创建 docs 目录。"""


def generate(conn, candidate_id: int) -> dict:
    """生成适配草稿并落盘。返回 {ok, draft_path?, name?, error?}"""
    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not cand:
        return {"ok": False, "error": "候选不存在"}
    report = conn.execute(
        "SELECT * FROM evaluation_reports WHERE candidate_id=? ORDER BY id DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    facts = json.loads(report["evidence_json"]) if report else {}

    try:
        proc = subprocess.run(
            ["claude", "-p", PROMPT.format(repo=cand["repo_url"],
                                           facts=json.dumps(facts, ensure_ascii=False)[:6000])],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "claude 生成超时（300s）"}
    if proc.returncode != 0:
        return {"ok": False, "error": f"claude 退出码 {proc.returncode}: {proc.stderr[:200]}"}

    raw = proc.stdout
    # 原文永远先落盘：解析失败也有完整材料可人工处理（错误信息如实指向文件）
    draft_dir = DRAFT_DIR / str(candidate_id)
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "raw.txt").write_text(raw, encoding="utf-8")

    try:
        # 容忍 markdown 围栏与前后杂讯：截取首个 { 到末个 } 再解析
        data = json.loads(raw[raw.index('{"capability_name"'): raw.rindex("}") + 1])
    except (ValueError, IndexError):
        return {"ok": False,
                "error": "claude 输出无法解析为约定 JSON，原文已存 data/drafts/%d/raw.txt" % candidate_id,
                "raw": raw[:500]}

    name = re.sub(r"\W+", "_", data.get("capability_name", "")).strip("_").lower()
    if not name:
        return {"ok": False, "error": "生成结果缺少能力名"}
    draft_dir = DRAFT_DIR / str(candidate_id)
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "adapter.py").write_text(data["script"], encoding="utf-8")
    (draft_dir / "meta.json").write_text(
        json.dumps({**data, "name": name, "generated_at": int(time.time())},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("候选 %s 适配草稿已生成: %s", candidate_id, name)
    return {"ok": True, "name": name, "title": data.get("title", name),
            "input_example": data.get("input_example", {}),
            "draft_path": str(draft_dir / "adapter.py")}


def run_isolated_test(conn, candidate_id: int) -> dict:
    """隔离测试：执行人工审阅通过后的适配草稿（L2 强审批——这是运行生成代码的唯一入口）。
    沙箱边界：独立目录 + 超时；无网络隔离（macOS 无容器，已在 spec 向用户声明）。"""
    from ..gateway import core as gateway

    cand = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    draft_dir = DRAFT_DIR / str(candidate_id)
    meta_path, script = draft_dir / "meta.json", draft_dir / "adapter.py"
    if not (meta_path.exists() and script.exists()):
        return {"ok": False, "error": "草稿不存在，先执行生成与人工审阅"}
    meta = json.loads(meta_path.read_text())
    verdict = gateway.check(conn, f"adapter:{cand['repo_url']}", "L2",
                            args_digest=f"候选{candidate_id}隔离测试")
    if verdict != "allowed":
        return {"ok": False, "error": "需要用户批准（L2：运行生成的适配代码）" if verdict == "pending"
                else f"网关拒绝（{verdict}）"}

    sandbox = draft_dir / "sandbox"
    sandbox.mkdir(exist_ok=True)
    import os
    from ..config import WORKER_PYTHON
    started = time.time()
    proc = subprocess.run(
        [str(WORKER_PYTHON), str(script)],
        input=json.dumps(meta.get("input_example", {})), capture_output=True, text=True,
        timeout=60, env={**os.environ, "ASTER_SANDBOX": str(sandbox)},
    )
    result = {"exit_code": proc.returncode, "stderr": proc.stderr[:500], "stdout_tail": proc.stdout[-500:]}
    ok = proc.returncode == 0
    with conn:
        conn.execute(
            "INSERT INTO candidate_tests(candidate_id, kind, result_json, exit_code, created_at) "
            "VALUES(?,?,?,?,?)",
            (candidate_id, "adapter_run", json.dumps(result, ensure_ascii=False),
             proc.returncode, int(time.time())),
        )
    result["ok"] = ok
    result["duration_ms"] = int((time.time() - started) * 1000)
    return result
