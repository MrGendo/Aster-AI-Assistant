# SQLite 访问与迁移器
# 为什么不用 ORM：表少、查询简单，裸 SQL + 字典行最直观，也避免单人项目背 ORM 升级包袱
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, MIGRATIONS_DIR
from .log import get_logger

log = get_logger("db")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL：读写并发下 UI 轮询不会阻塞任务写入
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """写事务统一入口：异常回滚，成功提交"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    """顺序执行 migrations/ 下未被应用的脚本；user_version 即迁移序号（文件名前缀）"""
    applied = current_version(conn)
    scripts = sorted(Path(MIGRATIONS_DIR).glob("*.sql"))
    for script in scripts:
        version = int(script.name.split("_")[0])
        if version <= applied:
            continue
        sql = script.read_text(encoding="utf-8")
        with tx(conn):
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version={version}")
        log.info("已应用迁移 %s", script.name)
