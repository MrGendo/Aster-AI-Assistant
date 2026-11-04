# 回放质量评分 worker（promptfoo）：把回放任务结果按验收标准打分
# 机制（实测收敛）：echo provider 承接存量输出 + llm-rubric 由 GLM 评判；
# --output 必须给文件路径（promptfoo 传格式关键字会触发其自带的迷惑报错）
# 输入: {"items": [{"name", "output", "criteria"}]}
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PF_BIN = Path(__file__).resolve().parents[2] / "tools/pf/node_modules/.bin/promptfoo"

TEMPLATE = """description: aster replay grading
prompts:
  - "{{{{output}}}}"
providers:
  - id: echo
tests:
{tests}
"""


def main() -> int:
    try:
        p = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    items = p.get("items") or []
    if not items:
        print(json.dumps({"error": "items 为空"}, ensure_ascii=False))
        return 4

    blocks = []
    for it in items:
        out = str(it.get("output", "")).replace('"', '\\"')[:800]
        crit = str(it.get("criteria", "输出切题且含具体事实，不是拒绝或空洞回复"))[:200]
        blocks.append(
            f'  - vars:\n      output: "{out}"\n    assert:\n'
            f'      - type: llm-rubric\n        value: "{crit}"\n'
        )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(TEMPLATE.format(tests="".join(blocks)))
        cfg = f.name
    out_path = cfg.replace(".yaml", ".result.json")

    env = json.loads((Path.home() / ".claude/settings.json").read_text()).get("env", {})
    proc = subprocess.run(
        [str(PF_BIN), "eval", "-c", cfg, "-o", out_path],
        capture_output=True, text=True, timeout=600,
        env={**os.environ,
             "ANTHROPIC_BASE_URL": env.get("ANTHROPIC_BASE_URL",
                                           "https://open.bigmodel.cn/api/anthropic"),
             "ANTHROPIC_API_KEY": env["ANTHROPIC_AUTH_TOKEN"],
             # 评分调用 GLM 需直连；本地无代理需求时也旁路以防万一
             "NO_PROXY": "localhost,127.0.0.1,open.bigmodel.cn"},
    )
    os.unlink(cfg)
    if proc.returncode != 0 or not Path(out_path).exists():
        print(json.dumps({"error": f"promptfoo 执行失败: {proc.stderr[-200:]}"},
                         ensure_ascii=False))
        return 2

    d = json.loads(Path(out_path).read_text())
    results = []
    for i, r in enumerate(d.get("results", {}).get("results", [])):
        a = r.get("gradingResult") or {}
        reason = ((a.get("componentResults") or [{}])[0].get("reason") or "")[:150]
        results.append({"name": items[i].get("name", f"item{i}"),
                        "pass": bool(a.get("pass")), "reason": reason})
    os.unlink(out_path)
    ok_n = sum(1 for x in results if x["pass"])
    print(json.dumps({"quality_pass_rate": f"{ok_n}/{len(results)}", "detail": results},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
