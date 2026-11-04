# 热点抓取 worker：从 Hacker News（Algolia 公开 API）拉取条目
# 定位：hot_news_digest 的上游供料能力——负责"弄到条目"，不负责筛选排序
# 边界：只访问 hn.algolia.com 一个白名单主机；只读；无副作用
import json
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "https://hn.algolia.com/api/v1/search"
HOST_ALLOWLIST = ("hn.algolia.com",)  # 防注入后改目标：主机硬编码，不接受输入指定


def fetch(query: str | None, limit: int) -> list[dict]:
    url = f"{BASE}?tags=story&hitsPerPage={min(limit, 50)}"
    if query:
        from urllib.parse import quote
        url += f"&query={quote(query)}"
    # 只读白名单 GET：与 git clone 同级的外部读取（L0）
    req = urllib.request.Request(url, headers={"User-Agent": "aster-capability/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    now = datetime.now(timezone.utc)
    items = []
    for h in data.get("hits", []):
        created = datetime.fromtimestamp(h.get("created_at_i", 0), tz=timezone.utc)
        age_h = max(0.0, (now - created).total_seconds() / 3600)
        points = h.get("points") or 0
        items.append({
            "title": h.get("title") or "",
            "source": "HackerNews",
            "category": query or "科技热点",
            "score": min(100, points),          # HN points 直接当热度（封顶100）
            "age_hours": round(age_h, 1),
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
        })
    return [i for i in items if i["title"]]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    # 主机白名单校验：即使输入塞了别的 URL 也只认 BASE
    if urllib.request.urlparse(BASE).hostname not in HOST_ALLOWLIST:
        print("配置错误：目标主机不在白名单", file=sys.stderr)
        return 2
    try:
        items = fetch((payload.get("query") or "").strip() or None, int(payload.get("limit", 30)))
    except Exception as e:
        print(json.dumps({"error": f"抓取失败：{e}"}, ensure_ascii=False))
        return 2
    print(json.dumps({"items": items, "count": len(items)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
