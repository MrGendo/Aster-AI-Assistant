# 深度调研能力 worker（gpt-researcher）：自主联网调研并产出带引用的中文报告
# 配置路线（文档研读关卡结论，docs/superpowers/specs/2026-11-05-deep-research-capability-design.md）：
#   1. GLM 走 anthropic provider + ANTHROPIC_BASE_URL 环境变量（源码实证 ChatAnthropic(**kwargs)
#      不显式传 base_url，SDK 从环境变量读取——与 browser_browse 同一接法）
#   2. 检索用 duckduckgo（免费免 key）；embedding 用本地 bge（OpenAI 端点 GLM token 无效，实测 429）
#   3. 官方声明对 GPT 优化：SMART_TOKEN_LIMIT 上调防截断；MAX_ITERATIONS=2 控时长成本
#   4. 首版不启用 deep 递归模式（约 5 分钟/$0.4，成本时长不可控）
# 风险说明：联网检索 + 付费 token 消耗，属 L1：默认需确认
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# 报告落盘目录（与服务端 report 伪能力同一位置，走既有下载链路）
REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"


def main() -> int:
    try:
        p = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    topic = (p.get("topic") or p.get("query") or "").strip()
    if len(topic) < 4:
        print(json.dumps({"error": "需要有效的调研主题（≥4 字符）"}, ensure_ascii=False))
        return 4
    report_type = p.get("report_type") or "research_report"
    max_words = min(int(p.get("max_words", 1500)), 3000)

    # GLM 密钥从 claude CLI 设置复用（与 browser_browse 同源），不落代码
    env = json.loads((Path.home() / ".claude/settings.json").read_text()).get("env", {})
    os.environ["ANTHROPIC_BASE_URL"] = env.get("ANTHROPIC_BASE_URL",
                                               "https://open.bigmodel.cn/api/anthropic")
    os.environ["ANTHROPIC_API_KEY"] = env["ANTHROPIC_AUTH_TOKEN"]
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,open.bigmodel.cn,hf-mirror.com"
    os.environ.update({
        "FAST_LLM": "anthropic:glm-4.7",
        "SMART_LLM": "anthropic:glm-4.7",
        "STRATEGIC_LLM": "anthropic:glm-4.7",
        # 本地嵌入：mem0 已装 sentence-transformers + 本地 bge 权重，免 key 免网
        "EMBEDDING": "huggingface:BAAI/bge-small-zh-v1.5",
        "RETRIEVER": "duckduckgo",
        "LANGUAGE": "chinese",
        "MAX_ITERATIONS": "2",
        "SMART_TOKEN_LIMIT": "16000",
        "FAST_TOKEN_LIMIT": "4000",
        "TOTAL_WORDS": str(max_words),
    })

    async def run() -> dict:
        # GLM glm-4.7 默认带 thinking 块 → ChatAnthropic 返回 content 为
        # [thinking块, text块] 列表，而 gpt-researcher 全按纯字符串处理（正则直接
        # TypeError，实测堆栈 agent_creator.extract_json_with_regex）。导入前替换为
        # 压平版：gpt-researcher 在函数内 from langchain_anthropic import ChatAnthropic，
        # 运行时取模块属性，会命中本补丁
        import langchain_anthropic
        from langchain_anthropic import ChatAnthropic as _RawChatAnthropic

        class _FlatChatAnthropic(_RawChatAnthropic):
            def _flat(self, r):
                if isinstance(r.content, list):
                    r.content = "".join(
                        b.get("text", "") for b in r.content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                return r

            async def ainvoke(self, *a, **kw):
                return self._flat(await super().ainvoke(*a, **kw))

            def invoke(self, *a, **kw):
                return self._flat(super().invoke(*a, **kw))

        langchain_anthropic.ChatAnthropic = _FlatChatAnthropic

        from gpt_researcher import GPTResearcher
        researcher = GPTResearcher(query=topic, report_type=report_type)
        await researcher.conduct_research()
        report = await researcher.write_report()
        sources = researcher.get_research_sources() or []
        # 引用来源扁平化：实测为逐条 {"url": ...}（skills/researcher.py add_research_sources），
        # 兼容分组结构并按 url 去重保序，只留 url/title 供结果页渲染与核验
        flat, seen = [], set()
        for item in sources:
            items = item.get("sources") if isinstance(item, dict) and "sources" in item \
                else ([item] if isinstance(item, dict) else [])
            for s in items:
                u = (s.get("url") or "").strip()
                if u and u not in seen:
                    seen.add(u)
                    flat.append({"url": u, "title": s.get("title")})
        return {"report": report, "sources": flat, "costs": researcher.get_costs()}

    try:
        out = asyncio.run(run())
    except Exception as e:
        print(json.dumps({"error": f"调研失败：{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 2
    if not (out["report"] or "").strip():
        print(json.dumps({"error": "调研完成但报告为空"}, ensure_ascii=False))
        return 2

    # 报告落盘（markdown），文件名带时间戳防覆盖
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"dr-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.md"
    body = f"# 调研报告：{topic}\n\n{out['report']}\n\n## 引用来源\n"
    for i, s in enumerate(out["sources"], 1):
        title = (s.get("title") or s.get("url") or "")[:80]
        body += f"{i}. [{title}]({s.get('url')})\n"
    path.write_text(body, encoding="utf-8")

    print(json.dumps({
        "topic": topic,
        "report_path": str(path),
        "report_chars": len(out["report"]),
        "sources": out["sources"][:20],
        "costs": out["costs"],
        "summary": out["report"][:300],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
