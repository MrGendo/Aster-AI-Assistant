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
