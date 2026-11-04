# PoC-4：sqlite-vec + 本地中文 embedding（bge-small-zh-v1.5）在 M4 上的实测
# 指标：模型加载耗时 / 千条编码耗时 / 常驻内存增量 / vec0 写入耗时 / KNN 延迟 / 语义命中抽查
import resource
import sqlite3
import time

import sqlite_vec
from sentence_transformers import SentenceTransformer

DIMS = 512  # bge-small-zh-v1.5 输出维度

# 25 个主题模板 × 40 变体 = 1000 条短文本（模拟知识块粒度）
TEMPLATES = [
    "SQLite 的 WAL 模式在写入密集场景下的表现",
    "MySQL 主从复制延迟的排查思路",
    "Tauri 2 透明窗口的配置方法",
    "Electron 应用瘦身实践",
    "语雀开放 API 的认证方式",
    "BM25 检索排序的原理",
    "向量检索与嵌入模型的关系",
    "macOS 钥匙串的命令行操作",
    "systemd 服务配置开机自启",
    "FastAPI 中间件的编写",
    "Spring Boot 事务传播机制",
    "提示注入攻击的常见防御手段",
    "CSP 内容安全策略的配置要点",
    "cron 表达式的时区陷阱",
    "调度任务错过触发窗口怎么办",
    "PyTorch 在苹果芯片上使用 MPS",
    "CUDA 显存不足的排查",
    "多路检索结果的融合方法",
    "个人知识管理的分类方法",
    "记忆系统的去重与冲突处理",
    "工具调用的权限分级设计",
    "审计日志的不可篡改性",
    "子进程超时与取消的信号语义",
    "自然语言解析待办事项的字段",
    "知识文档的哈希去重策略",
]


def build_texts() -> list[str]:
    texts = []
    for i, t in enumerate(TEMPLATES):
        for j in range(40):
            texts.append(f"{t}（样例 {i}-{j}）")
    return texts


def rss_mb() -> float:
    # macOS 的 ru_maxrss 单位是字节，Linux 是 KB——按平台换算
    import sys

    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1048576 if sys.platform == "darwin" else v / 1024


def main():
    print(f"[0] 进程基线内存: {rss_mb():.0f} MB")

    t0 = time.time()
    model = SentenceTransformer("BAAI/bge-small-zh-v1.5", device="mps")
    print(f"[1] 模型加载: {time.time()-t0:.1f}s, 内存 {rss_mb():.0f} MB")

    texts = build_texts()
    assert len(texts) == 1000

    t1 = time.time()
    emb = model.encode(
        texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False
    )
    dt = time.time() - t1
    print(f"[2] 1000 条编码: {dt:.1f}s（{1000/dt:.0f} 条/秒）, 内存 {rss_mb():.0f} MB")

    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE vec USING vec0(docid INTEGER PRIMARY KEY, embedding float[{DIMS}] distance_metric=cosine)"
    )
    t2 = time.time()
    conn.executemany(
        "INSERT INTO vec(docid, embedding) VALUES (?, ?)",
        [(i, sqlite_vec.serialize_float32(emb[i].tolist())) for i in range(1000)],
    )
    print(f"[3] vec0 写入 1000 条: {time.time()-t2:.2f}s")

    # KNN 延迟：20 次查询取均值（含模板改写，贴近真实使用）
    probes = [f"{t} 的最佳实践是什么" for t in TEMPLATES[:20]]
    p_emb = model.encode(probes, normalize_embeddings=True)
    lat = []
    for k in range(20):
        t3 = time.time()
        rows = conn.execute(
            "SELECT docid, distance FROM vec WHERE embedding MATCH ? AND k = 5 ORDER BY distance",
            (sqlite_vec.serialize_float32(p_emb[k].tolist()),),
        ).fetchall()
        lat.append(time.time() - t3)
        # 抽查：top1 应落在同主题的 40 条变体区间内
        topic = TEMPLATES.index(TEMPLATES[k])
        lo, hi = topic * 40, topic * 40 + 40
        assert lo <= rows[0][0] < hi, f"查询{k} top1 落错主题: {rows[0]}"
    print(f"[4] KNN 延迟: avg {sum(lat)/len(lat)*1000:.1f}ms, max {max(lat)*1000:.1f}ms, 语义 top1 命中 20/20")
    print(f"[5] 峰值内存: {rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
