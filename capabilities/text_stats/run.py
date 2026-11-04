# 文本统计 worker：确定性纯本地计算，无网络无模型（Phase 1 的零成本基线能力）
import json
import sys
from collections import Counter


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    text = payload.get("text") or ""
    # 分词用 jieba：与主服务同 venv，保持行为一致
    import jieba
    tokens = [t for t in jieba.cut(text) if t.strip()]
    words = [t for t in tokens if any(c.isalnum() for c in t)]
    top = "、".join(f"{w}×{c}" for w, c in Counter(words).most_common(8))
    print(json.dumps({
        "chars": len(text),
        "lines": text.count("\n") + (1 if text else 0),
        "tokens": len(tokens),
        "top_tokens": top,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
