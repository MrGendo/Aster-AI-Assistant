# 仓库质量评分 worker：OpenSSF Scorecard 公开 API（Linux 基金会标准）
# 定位：给候选/评估提供"高质量"的客观维度（维护/安全/测试/依赖），免密钥只读
# 边界：仅白名单主机 api.securityscorecards.dev；"实用与否"仍由 LLM 评估判断
import json
import re
import sys
import urllib.request

BASE = "https://api.securityscorecards.dev/projects/github.com/"
HOST_ALLOWLIST = ("api.securityscorecards.dev",)

# 展示时优先呈现的检查项（Scorecard 全量 18+ 项的精简视图）
KEY_CHECKS = ("Maintained", "CI-Tests", "Code-Review", "Security-Policy",
              "Token-Permissions", "Dependency-Update-Tool", "Fuzzing", "Packaging")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    m = re.match(r"https://github\.com/([\w.\-]+/[\w.\-]+)", (payload.get("repo_url") or "").strip())
    if not m:
        print(json.dumps({"error": "需要 https://github.com/<owner>/<repo> 链接"}, ensure_ascii=False))
        return 4

    req = urllib.request.Request(BASE + m.group(1),
                                 headers={"User-Agent": "aster-capability/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # 404 = 该仓库从未被扫描过（Scorecard 按需扫描主流仓库），如实说明
        print(json.dumps({"error": f"Scorecard 未收录该仓库（HTTP {e.code}）——"
                                   f"小众/新建仓库常见，不代表质量差"}, ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps({"error": f"查询失败：{e}"}, ensure_ascii=False))
        return 2

    checks = {c["name"]: c for c in data.get("checks", [])}
    key = {}
    for name in KEY_CHECKS:
        c = checks.get(name)
        if c:
            reasons = ";".join(r.get("reason", "")[:40] for r in (c.get("reasons") or [])[:2])
            key[name] = {"score": c.get("score"), "reasons": reasons}
    print(json.dumps({
        "repo": m.group(1),
        "score": data.get("score"),          # 0-10 聚合分（未加权原始分）
        "key_checks": key,
        "scanned_at": data.get("date"),
        "source": "OpenSSF Scorecard（客观扫描，非主观评价）",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
