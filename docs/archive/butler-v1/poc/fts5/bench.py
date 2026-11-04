# PoC-3：FTS5 中文检索三方案对比
# 方案A trigram 分词器 / 方案B jieba 预分词列(默认unicode61) / 方案C unicode61 裸文本(基线)
# 评分方式：每查询取 top-5，与人工标注的标准答案对比，算 precision@5 与 recall
import sqlite3

import jieba

# 20 条中文知识样例（覆盖 Aster 场景真实语料形态）
DOCS = [
    "SQLite WAL 模式与 checkpoint 机制详解，写入性能与一致性权衡",
    "MySQL 主从复制延迟排查笔记：Seconds_Behind_Master 与网络抖动",
    "Tauri 2 透明窗口配置与 macos-private-api 实践记录",
    "Electron 打包体积优化：asar 拆分与按需加载",
    "语雀开放 API 的 Token 认证与限流处理，429 退避策略",
    "GitHub Actions 缓存加速 CI 构建，pnpm store 与 cargo target",
    "BM25 全文检索排序原理：词频、逆文档频率与长度归一化",
    "向量嵌入模型选型：bge 与 text-embedding 系列对比测试",
    "macOS 钥匙串 keyring 命令行用法：add-generic-password 与 find-generic-password",
    "Linux systemd 服务开机自启配置与故障自动重启",
    "FastAPI 依赖注入与中间件实践，请求级状态管理",
    "Spring Boot 事务传播行为总结：REQUIRED 与 REQUIRES_NEW",
    "提示注入攻击的防御分层设计：内容隔离与工具审批",
    "XSS 与 CSP 内容安全策略配置，script-src 白名单",
    "cron 表达式语法与常见陷阱：时区与月末匹配",
    "APScheduler misfire 策略解读：grace time 与 coalesce",
    "苹果 M4 芯片 MPS 后端跑 PyTorch 记录，吞吐对比 CPU",
    "CUDA 显存不足的排查手段：碎片化与峰值估算",
    "Reciprocal Rank Fusion 融合公式推导与检索结果合并",
    "个人知识管理的 PARA 方法论实践体会",
]

# 10 个查询与标准答案（doc 下标），刻意含 2 字短词与中英混合等难例
QUERIES = [
    ("全文检索排序", {6}),
    ("透明窗口", {2}),
    ("token认证", {4}),
    ("开机自启", {9}),
    ("向量模型选型", {7}),
    ("限流", {4}),          # 2字词：trigram 无法命中（<3字符）
    ("misfire", {15}),      # 纯英文词
    ("MPS 后端", {16}),
    ("融合公式", {18}),
    ("钥匙串", {8}),        # 2字词：trigram 无法命中
]


def jieba_join(text: str) -> str:
    # 检索模式分词后空格拼接，FTS5 默认 unicode61 按空白切 token
    return " ".join(jieba.cut_for_search(text))


def query(conn: sqlite3.Connection, table: str, match: str) -> list[int]:
    try:
        rows = conn.execute(
            f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT 5",
            (match,),
        ).fetchall()
        return [r[0] - 1 for r in rows]
    except sqlite3.OperationalError:
        return []


def main():
    conn = sqlite3.connect(":memory:")

    # 方案A：trigram（原样入库；查询字符串加引号避免语法冲突）
    conn.execute("CREATE VIRTUAL TABLE t_a USING fts5(c, tokenize='trigram')")
    conn.executemany("INSERT INTO t_a(c) VALUES (?)", [(d,) for d in DOCS])

    # 方案B：jieba 预分词列（词间空格）
    conn.execute("CREATE VIRTUAL TABLE t_b USING fts5(c)")
    conn.executemany(
        "INSERT INTO t_b(c) VALUES (?)", [(jieba_join(d),) for d in DOCS]
    )

    # 方案C：unicode61 裸文本（基线，中文无空格基本不可分）
    conn.execute("CREATE VIRTUAL TABLE t_c USING fts5(c)")
    conn.executemany("INSERT INTO t_c(c) VALUES (?)", [(d,) for d in DOCS])

    methods = {
        "A_trigram": lambda q: (
            query(conn, "t_a", f'"{q}"') if len(q) >= 3 else None  # None=该方案物理上无法查询
        ),
        "B_jieba": lambda q: query(conn, "t_b", jieba_join(q)),
        "C_unicode61": lambda q: query(conn, "t_c", jieba_join(q)),
    }

    stats = {m: {"recall": [], "p5": [], "skip": 0} for m in methods}
    print(f"{'查询':<10} {'标准答案':<8} | " + " | ".join(f"{m:<22}" for m in methods))
    print("-" * 100)
    for q, truth in QUERIES:
        cells = []
        for m, fn in methods.items():
            got = fn(q)
            if got is None:
                cells.append(f"{'—不能查(<3字)—':<22}")
                stats[m]["skip"] += 1
                continue
            hit = set(got) & truth
            rec = len(hit) / len(truth)
            prec = len(hit) / len(got) if got else 0.0
            stats[m]["recall"].append(rec)
            stats[m]["p5"].append(prec)
            mark = "√" if rec == 1 else ("△" if rec > 0 else "×")
            cells.append(f"{mark} hit={sorted(hit)} got={got[:3]}")
        print(f"{q:<10} {str(sorted(truth)):<8} | " + " | ".join(cells))

    print("-" * 100)
    for m, s in stats.items():
        n = len(s["recall"])
        if n:
            print(
                f"{m:<12} 平均recall={sum(s['recall'])/n:.2f}  平均P@5={sum(s['p5'])/n:.2f}"
                f"  无法查询数={s['skip']}"
            )
        else:
            print(f"{m:<12} 全部无法查询")


if __name__ == "__main__":
    main()
