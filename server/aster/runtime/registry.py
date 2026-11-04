# 能力注册表：统一 descriptor（PRD §6.4 精简版）+ candidate/active 状态隔离
import json
import sqlite3
import time

from ..db import tx

# 注册表内置的两个能力（Phase 1）；descriptor 字段即 PRD 6.4 的 Phase 1 子集
BUILTIN = [
    {
        "name": "repo_analysis",
        "kind": "task",
        "risk_level": "L0",  # 浅克隆只读分析（spec §8：L0 自动放行）
        "entry": "repo_analysis/run.py",
        "descriptor": {
            "name": "仓库初步分析",
            "purpose": "对公开 GitHub 仓库做定位/结构/测试/许可/活跃度/风险的初步分析，输出证据分级报告",
            "not_for": "私有仓库、深度代码审查（属后续阶段的评估能力）",
            "input": "repo_url（https://github.com/... 链接）",
            "output": "JSON 报告（verified 事实与 inferred 推测严格分开）",
            "cost": "网络浅克隆，约 5-60 秒；仓库超过 200MB 拒绝",
            "limits": "不做代码执行、不改任何文件；克隆仅落在系统沙箱目录",
        },
    },
    {
        "name": "text_stats",
        "kind": "tool",
        "risk_level": "L0",
        "entry": "text_stats/run.py",
        "descriptor": {
            "name": "文本统计",
            "purpose": "字符/行数/分词统计等确定性文本处理，无网络无模型",
            "not_for": "语义类任务",
            "input": "text（任意文本）",
            "output": "统计 JSON",
            "cost": "毫秒级，零成本",
            "limits": "纯本地计算",
        },
    },
    {
        "name": "repo_quality",
        "kind": "tool",
        "risk_level": "L0",
        "entry": "repo_quality/run.py",
        "descriptor": {
            "name": "仓库质量评分（OpenSSF Scorecard）",
            "purpose": "查询仓库的客观健康度：维护状态/CI 测试/代码评审/安全策略等，0-10 聚合分",
            "not_for": "主观实用性判断（归 LLM 评估）；未被 Scorecard 收录的小众仓库",
            "input": "{\"repo_url\": \"https://github.com/...\"}",
            "output": "score + 关键检查项明细",
            "cost": "一次只读 API 调用，秒级，免密钥",
            "limits": "仅白名单主机；数据来自 OpenSSF 定期扫描",
        },
    },
    {
        "name": "web_fetch",
        "kind": "tool",
        "risk_level": "L0",  # 只读 GET；SSRF 防护在 worker 内强制（内网段拒绝+逐跳校验）
        "entry": "web_fetch/run.py",
        "descriptor": {
            "name": "网页抓取",
            "purpose": "抓取网页正文（≤3000字）供摘要富化等下游使用",
            "not_for": "需要 JS 渲染的页面、下载二进制",
            "input": "{\"urls\": [\"https://...\"]}（≤5 条）",
            "output": "{\"pages\": [{url, ok, title, text}]}",
            "cost": "每页一次只读请求，秒级",
            "limits": "仅 text/html 与 text/plain；512KB 上限；内网/保留地址一律拒绝",
        },
    },
    {
        "name": "llm_task",
        "kind": "task",
        "risk_level": "L0",  # 进程内模型调用（同 chat），无子进程无副作用
        "entry": "(in-process)",
        "descriptor": {
            "name": "通用文本加工（LLM）",
            "purpose": "摘要/翻译/改写/整理等任意文本加工，作为协作链的通用加工步骤",
            "not_for": "确定性计算、需要精确计数的任务",
            "input": "{\"prompt\": \"加工指令\", \"text\": \"待加工内容（可 $N.key 引用上游）\"}",
            "output": "{\"text\": \"加工结果\"}",
            "cost": "一次模型调用",
            "limits": "不访问网络不读写文件；输出为生成内容需评审步把关",
        },
    },
    {
        "name": "news_fetch",        "kind": "tool",
        "risk_level": "L0",
        "entry": "news_fetch/run.py",
        "descriptor": {
            "name": "热点抓取（Hacker News）",
            "purpose": "从 HN 公开 API 拉取条目作为热点日报的原料；支持按关键词检索",
            "not_for": "中文站内热点（数据源后续扩展）",
            "input": "query（可选关键词）、limit（默认30）",
            "output": "条目数组 JSON",
            "cost": "一次只读 HTTP 请求，秒级",
            "limits": "仅白名单主机 hn.algolia.com；只读",
        },
    },
]


def ensure_builtin(conn: sqlite3.Connection) -> None:
    """启动时登记/刷新内置能力；内置能力默认 active（自研、白名单内、已随代码评审）"""
    now = int(time.time())
    for cap in BUILTIN:
        row = conn.execute("SELECT id FROM capabilities WHERE name=?", (cap["name"],)).fetchone()
        with tx(conn):
            if row:
                conn.execute(
                    "UPDATE capabilities SET descriptor_json=?, entry=?, updated_at=? WHERE id=?",
                    (json.dumps(cap["descriptor"], ensure_ascii=False), cap["entry"], now, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO capabilities(name, kind, descriptor_json, status, source, "
                    "version, risk_level, entry, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        cap["name"], cap["kind"],
                        json.dumps(cap["descriptor"], ensure_ascii=False),
                        "active", "builtin", "0.1.0", cap["risk_level"],
                        cap["entry"], now, now,
                    ),
                )


def get_active(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """取用能力必须限定 active 状态——candidate 与正式能力在调用侧严格隔离（PRD §5.8）"""
    return conn.execute(
        "SELECT * FROM capabilities WHERE name=? AND status='active'", (name,)
    ).fetchone()
