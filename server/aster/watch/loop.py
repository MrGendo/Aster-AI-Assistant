# watchlist 巡检：自研轻量调度（sleep-loop，沿用既有"自研调度"决策）
# 变更探测用 git ls-remote（免认证、无限速），变更即生成一条可见任务提醒用户
import json
import subprocess
import threading
import time

from ..db import connect
from ..log import get_logger

log = get_logger("watch")
CHECK_INTERVAL_S = 600  # 巡检节奏：每 10 分钟扫一遍到期项


def _head_of(repo_url: str) -> str | None:
    try:
        out = subprocess.run(["git", "ls-remote", repo_url, "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.split()[0] if out.returncode == 0 and out.stdout else None
    except subprocess.TimeoutExpired:
        return None


def check_once() -> int:
    """扫描到期项，发现变更则登记提醒任务。返回变更数。"""
    conn = connect()
    changed = 0
    try:
        now = int(time.time())
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE last_checked_at IS NULL "
            "OR last_checked_at + frequency_s < ?", (now,),
        ).fetchall()
        for w in rows:
            head = _head_of(w["repo_url"])
            if head and w["last_head"] and head != w["last_head"]:
                # 写通知而非建任务：chat 意图任务无人执行会堆积成僵尸（实测 19 条卡 pending）
                from ..scheduler import _notify
                _notify("watch", f"关注仓库有更新：{w['repo_url']}（HEAD {head[:8]}）")
                changed += 1
            if head:
                with conn:
                    conn.execute("UPDATE watchlist SET last_head=?, last_checked_at=? WHERE id=?",
                                 (head, now, w["id"]))
    finally:
        conn.close()
    return changed


def start() -> None:
    """启动后台巡检线程（应用启动时调用一次）"""
    def _loop():
        while True:
            try:
                check_once()
            except Exception as e:  # 巡检永不冒泡打断主服务
                log.warning("巡检异常: %s", e)
            time.sleep(CHECK_INTERVAL_S)

    threading.Thread(target=_loop, daemon=True, name="watch-loop").start()
    log.info("watchlist 巡检已启动（间隔 %ss）", CHECK_INTERVAL_S)
