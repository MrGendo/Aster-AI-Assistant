# 仓库初步分析 worker（Phase 1 旗舰能力）
# 输入: stdin JSON {repo_url}
# 输出: stdout 末行 JSON {verified:{...}, inferred:{...}, risks:[...]}——证据分级是硬要求（PRD §5.6）
# 退出码: 0 成功 / 2 克隆失败 / 3 仓库过大 / 4 链接非法
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VALID_URL = re.compile(r"^https://github\.com/[\w.\-]+/[\w.\-]+$")
MAX_REPO_MB = 200

# 语言判定只按扩展名粗分——报告里明示这是「推测」而非逐文件解析
LANG_BY_EXT = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin",
    ".swift": "Swift", ".c": "C", ".h": "C", ".cpp": "C++", ".rb": "Ruby", ".php": "PHP",
    ".vue": "Vue", ".md": "Markdown", ".sql": "SQL", ".sh": "Shell",
}


def clone(url: str, sandbox: Path) -> tuple[bool, str]:
    dest = sandbox / "repo"
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "克隆超时（>300 秒），仓库可能过大或网络不通"
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return False, err[-1] if err else "git clone 失败（原因未知）"
    return True, ""


def analyze(dest: Path) -> dict:
    files = [p for p in dest.rglob("*") if p.is_file() and ".git/" not in str(p)]
    total_mb = sum(f.stat().st_size for f in files) / 1048576
    if total_mb > MAX_REPO_MB:
        raise TooLarge(f"仓库工作区约 {total_mb:.0f}MB，超过 {MAX_REPO_MB}MB 上限")

    exts = Counter(p.suffix.lower() for p in files if p.suffix)
    langs = Counter()
    for ext, cnt in exts.items():
        lang = LANG_BY_EXT.get(ext)
        if lang:
            langs[lang] += cnt
    lang_str = "、".join(f"{l}({c})" for l, c in langs.most_common(5)) or "未识别"

    # 测试证据：测试文件/目录的存在性（不等于测试质量——如实措辞）
    test_files = [p for p in files if "test" in p.name.lower() or "tests" in str(p.parent).lower()]
    tests_desc = f"检出 {len(test_files)} 个测试相关文件" if test_files else "未检出测试文件（Phase 1 未验证测试可运行性）"

    # 许可证：仅识别常见文件与名称，不解读条款
    license_name = "未检出"
    for p in files:
        if p.name.upper().startswith("LICENSE") or p.name.upper() == "COPYING":
            license_name = f"存在 {p.name}（条款需人工确认）"
            break

    # 最近提交（浅克隆即最新一条）
    try:
        last = subprocess.run(["git", "-C", str(dest), "log", "-1", "--format=%ci %s"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except subprocess.TimeoutExpired:
        last = "读取失败"

    readme = ""
    for name in ("README.md", "README_zh.md", "README_cn.md", "README"):
        p = dest / name
        if p.exists():
            readme = p.read_text(encoding="utf-8", errors="ignore")[:3000]
            break

    # 推测部分：来自 README 的项目自述，一律标注为未经证实的项目声明
    # 启发式：优先标题行，跳过徽章/图片/链接行（README 首行常是徽章墙）
    positioning = ""
    body_lines = []
    for line in readme.splitlines():
        s = line.strip()
        if not s or s.startswith(("![", "[![")) or "](" in s or s.startswith(("<", "---")):
            continue
        if s.startswith("#"):
            body_lines.append(s.lstrip("# ").strip())
        else:
            body_lines.append(s.strip("*- "))
    for cand in body_lines:
        if len(cand) > 15:
            positioning = cand[:200]
            break

    risks = []
    if total_mb > 100:
        risks.append(f"工作区较大（{total_mb:.0f}MB），接入成本可能偏高")
    if not test_files:
        risks.append("未检出测试文件，可靠性存疑")
    if license_name == "未检出":
        risks.append("未检出许可证文件，使用前需确认授权")
    if langs and langs.most_common(1)[0][0] == "Markdown":
        risks.append("以文档为主，代码成分低")

    return {
        "verified": {  # 以下均为从代码/克隆中直接观测到的事实
            "languages": lang_str,
            "total_files": len(files),
            "workspace_mb": round(total_mb, 1),
            "tests": tests_desc,
            "license": license_name,
            "last_commit": last[:120],
        },
        "inferred": {  # 以下为基于项目自述的推测，未经实测验证
            "positioning": positioning or "（README 未提供可识别的定位描述）",
            "readme_head": readme[:500],
        },
        "risks": risks,
    }


class TooLarge(Exception):
    pass


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    url = (payload.get("repo_url") or "").strip().rstrip("/")
    if not VALID_URL.match(url):
        print(json.dumps({"error": f"仅支持 https://github.com/<owner>/<repo> 形式的公开仓库链接，收到: {url}"},
                         ensure_ascii=False))
        return 4

    sandbox = Path(os.environ.get("ASTER_SANDBOX", "."))
    ok, err = clone(url, sandbox)
    if not ok:
        print(json.dumps({"error": f"克隆失败：{err}。可能原因：仓库不存在/私有/网络受限"}, ensure_ascii=False))
        return 2

    try:
        report = analyze(sandbox / "repo")
    except TooLarge as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
