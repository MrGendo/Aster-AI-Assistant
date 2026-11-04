# 开源发现：gh CLI 封装 + 任务缺口触发（Stage 2 数据源，连通性已验证 2026-11-04）
import json
import re
import subprocess

from ..log import get_logger

log = get_logger("discovery")


def search_repos(query: str, limit: int = 5) -> list[dict]:
    """GitHub 仓库搜索（gh 已认证）。返回标准化元数据列表。"""
    from urllib.parse import quote
    try:
        out = subprocess.run(
            ["gh", "api", f"search/repositories?q={quote(query)}&per_page={limit}&sort=stars"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            log.warning("gh 搜索失败: %s", out.stderr.strip()[:200])
            return []
        items = json.loads(out.stdout).get("items", [])
        return [
            {
                "repo_url": f"https://github.com/{it['full_name']}",
                "name": it["full_name"],
                "stars": it["stargazers_count"],
                "language": it.get("language") or "未知",
                "topics": it.get("topics", [])[:5],
                "description": (it.get("description") or "")[:200],
                "pushed_at": it.get("pushed_at", ""),
                "license": (it.get("license") or {}).get("spdx_id", "未知"),
            }
            for it in items
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        log.warning("gh 搜索异常: %s", e)
        return []


# 任务失败 → 检索词的粗规则（LLM 兜底可用时优先 LLM）
_GAP_PATTERNS = [
    (re.compile(r"能力|capability|无法完成|not supported", re.I), True),
]


def gap_query(task_goal: str, task_error: str) -> str | None:
    """从失败任务提取候选检索词：优先 LLM 抽词，失败则用目标里的关键词拼接"""
    from ..providers import router as provider
    text = f"任务目标：{task_goal}\n失败原因：{task_error}"
    if provider.available():
        try:
            q = provider.chat(
                [{"role": "user", "content": text}],
                system="从任务目标与失败原因中提取用于 GitHub 仓库搜索的英文检索词，"
                       "只输出检索词本身（不超过5个单词），不要解释。",
                max_tokens=30,
            )
            q = q.strip().strip('"`\n')
            if 2 < len(q) < 80:
                return q
        except Exception:
            pass
    # 兜底：目标里的英文词
    words = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", task_goal)
    return " ".join(words[:3]) if words else None
