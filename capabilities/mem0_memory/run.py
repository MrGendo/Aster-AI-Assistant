# 语义记忆 worker（mem0）：补足记忆系统的向量检索短板
# 配置路线（实测打通）：LLM=anthropic/glm-4.7 经 ANTHROPIC_BASE_URL（claude 计划同源），
# 嵌入=本地 bge-small-zh-v1.5（HF 缓存零下载），存储=chroma（data/mem0_store）
# 输入: {"action": "add|search", "text"|"query": "...", "user_id": "aster"}
import json
import os
import sys
from pathlib import Path


def _mem():
    # mem0 2.x 的自定义端点走环境变量（BaseLlmConfig 无 base_url 字段，实测结论）
    env = json.loads((Path.home() / ".claude/settings.json").read_text()).get("env", {})
    os.environ["ANTHROPIC_BASE_URL"] = env.get("ANTHROPIC_BASE_URL",
                                               "https://open.bigmodel.cn/api/anthropic")
    os.environ["ANTHROPIC_AUTH_TOKEN"] = env["ANTHROPIC_AUTH_TOKEN"]
    from mem0 import Memory
    return Memory.from_config({
        "llm": {"provider": "litellm", "config": {"model": "anthropic/glm-4.7", "temperature": 0}},
        "embedder": {"provider": "huggingface", "config": {"model": "BAAI/bge-small-zh-v1.5"}},
        "vector_store": {"provider": "chroma",
                         "config": {"collection_name": "aster_mem0", "path": "data/mem0_store"}},
    })


def main() -> int:
    try:
        p = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    action, uid = p.get("action", "search"), p.get("user_id", "aster")
    try:
        m = _mem()
        if action == "add":
            r = m.add(p.get("text", ""), user_id=uid)
            out = {"added": True, "detail": r if isinstance(r, str) else "已入库（含去重/更新）"}
        else:
            r = m.search(p.get("query", ""), filters={"user_id": uid},
                         limit=int(p.get("limit", 5)))
            res = r.get("results") if isinstance(r, dict) else r
            out = {"results": [{"memory": x.get("memory", ""), "score": x.get("score")}
                               for x in (res or [])]}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"error": f"mem0 操作失败：{e}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
