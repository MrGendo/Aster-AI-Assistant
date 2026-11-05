# 定时调度：APScheduler 封装 + 中文时间语义解析
# 设计：docs/superpowers/specs/2026-11-05-chat-first-home-and-scheduler-design.md
# 关键约定（APScheduler 3.x 手册）：
#   - schedules 表是唯一事实源，调度器用 MemoryJobStore，重启后全量重注册
#   - add_job 必须显式 id + replace_existing=True（文档明示：否则每次重启叠加副本）
#   - misfire_grace_time=300 + coalesce=True：停机错过合并补跑一次，不堆积
import json
import re
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .db import connect, tx

_sched: BackgroundScheduler | None = None

# ---------- 中文时间语义（规则层；LLM 兜底在 api 层） ----------
_DAILY_RE = re.compile(
    r"每\s*天\s*(?:早上|清晨|上午|下午|晚上|凌晨)?\s*(\d{1,2})\s*[点:：时]\s*(半|(\d{1,2})\s*分?)?")
_EVERY_MIN_RE = re.compile(r"每\s*(\d+)\s*分钟")
_EVERY_HOUR_RE = re.compile(r"每\s*(\d+)\s*小时")
_AFTER_MIN_RE = re.compile(r"(\d+)\s*分钟\s*后")
_AFTER_HOUR_RE = re.compile(r"(\d+)\s*小时\s*后")
_DAY_AT_RE = re.compile(r"(今天|明天|后天)?\s*(?:早上|上午|下午|晚上)?\s*(\d{1,2})\s*[点:：时]\s*(半|(\d{1,2})\s*分?)?")
_ABS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})")
_PERIODIC_HINT = re.compile(r"每天|每周|每隔|每\s*\d+\s*分钟|每\s*\d+\s*小时|定期")


def _minute_tail(m: re.Match) -> int:
    """解析"半"/"N分"尾巴为分钟数"""
    if m.group(2) == "半":
        return 30
    return int(m.group(3) or 0) if m.group(3) else 0


def parse_when(text: str) -> dict | None:
    """中文时间语义 → trigger_json；规则不中返回 None（由调用方决定 LLM 兜底或报错）"""
    now = datetime.now()
    if m := _DAILY_RE.search(text):
        hour = int(m.group(1))
        if re.search(r"下午|晚上", text) and hour < 12:
            hour += 12
        return {"type": "cron", "hour": hour, "minute": _minute_tail(m)}
    if m := _EVERY_MIN_RE.search(text):
        return {"type": "interval", "minutes": int(m.group(1))}
    if m := _EVERY_HOUR_RE.search(text):
        return {"type": "interval", "hours": int(m.group(1))}
    if m := _ABS_RE.search(text):
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
        if dt <= now:
            return None
        return {"type": "date", "run_at": dt.timestamp()}
    if m := _AFTER_MIN_RE.search(text):
        return {"type": "date", "run_at": (now + timedelta(minutes=int(m.group(1)))).timestamp()}
    if m := _AFTER_HOUR_RE.search(text):
        return {"type": "date", "run_at": (now + timedelta(hours=int(m.group(1)))).timestamp()}
    if m := _DAY_AT_RE.search(text):
        hour = int(m.group(2))
        if re.search(r"下午|晚上", text) and hour < 12:
            hour += 12
        day_offset = {"明天": 1, "后天": 2}.get(m.group(1) or "", 0)
        dt = (now + timedelta(days=day_offset)).replace(hour=hour, minute=_minute_tail(m),
                                                        second=0, microsecond=0)
        if dt <= now:  # 今天的时刻已过 → 顺延明天（打过的闹钟不该哑火）
            dt += timedelta(days=1)
        return {"type": "date", "run_at": dt.timestamp()}
    return None


def looks_periodic(text: str) -> bool:
    """是否带周期语义（定时任务入口判据）"""
    return bool(_PERIODIC_HINT.search(text))


def build_trigger(tj: dict):
    if tj["type"] == "cron":
        return CronTrigger(hour=tj["hour"], minute=tj.get("minute", 0))
    if tj["type"] == "interval":
        return IntervalTrigger(**({"minutes": tj["minutes"]} if "minutes" in tj
                                  else {"hours": tj["hours"]}))
    return DateTrigger(run_date=datetime.fromtimestamp(tj["run_at"]))


def describe_trigger(tj: dict) -> str:
    """给人看的触发描述（列表页/回执用）"""
    if tj["type"] == "cron":
        return f"每天 {tj['hour']:02d}:{tj.get('minute', 0):02d}"
    if tj["type"] == "interval":
        n, unit = ("minutes" in tj and (tj["minutes"], "分钟")) or (tj["hours"], "小时")
        return f"每隔 {n} {unit}"
    return datetime.fromtimestamp(tj["run_at"]).strftime("%m-%d %H:%M")


# ---------- 调度器 ----------
def start() -> None:
    global _sched
    if _sched:
        return
    _sched = BackgroundScheduler(
        job_defaults={"misfire_grace_time": 300, "coalesce": True})
    _sched.start()
    sync_all()


def stop() -> None:
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None


def add(row_id: int, tj: dict, enabled: bool = True) -> int | None:
    """注册一个调度并返回下次运行时间戳；禁用态不注册"""
    if not _sched or not enabled:
        return None
    job = _sched.add_job(_fire, trigger=build_trigger(tj), id=str(row_id),
                         replace_existing=True, kwargs={"row_id": row_id})
    return int(job.next_run_time.timestamp()) if job.next_run_time else None


def remove(row_id: int) -> None:
    if _sched:
        try:
            _sched.remove_job(str(row_id))
        except Exception:
            pass  # 已随 date 触发自然移除


def set_enabled(row_id: int, tj: dict, enabled: bool) -> int | None:
    remove(row_id)
    return add(row_id, tj, enabled)


def sync_all() -> None:
    """启动时全量重注册（事实源是 schedules 表，MemoryJobStore 里的旧态不可信）"""
    if not _sched:
        return
    conn = connect()
    try:
        rows = conn.execute("SELECT id, trigger_json, enabled FROM schedules").fetchall()
    finally:
        conn.close()
    for r in rows:
        tj = json.loads(r["trigger_json"])
        next_ts = add(r["id"], tj, bool(r["enabled"]))
        if next_ts:
            _update_row(r["id"], next_run_at=next_ts)


def _update_row(row_id: int, **fields) -> None:
    conn = connect()
    try:
        with tx(conn):
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE schedules SET {sets} WHERE id=?",
                         (*fields.values(), row_id))
    finally:
        conn.close()


def _fire(row_id: int) -> None:
    """到点回调：task → 建任务走执行链（门禁照常）；alarm → 落通知。一次性触发后自禁用"""
    import asyncio
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (row_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["enabled"]:
        return
    tj = json.loads(row["trigger_json"])
    task_id = None
    if row["kind"] == "task":
        from .orchestrator import core as orchestrator
        conn = connect()
        try:
            task_id = orchestrator.create_task(conn, row["goal"])
        finally:
            conn.close()
        asyncio.run(orchestrator.execute(task_id))  # 线程里新开事件循环（调度线程独立）
        # L1 能力会停在审批口（awaiting_user）——如实通知"待批准"，不谎报完成
        st, summary = _task_status(task_id)
        if st == "awaiting_user":
            _notify("task_done", f"定时任务等待你的批准：{row['goal'][:50]}（任务 #{task_id}）", task_id)
        else:
            _notify("task_done", f"定时任务执行完毕（#{task_id} {st}）：{row['goal'][:40]}\n{summary}", task_id)
    else:
        _notify("alarm", f"⏰ {row['goal']}")
    # 回写运行状态；一次性（date）触发后自动禁用，防止语义上的"复活"
    next_ts = add(row_id, tj, enabled=tj["type"] != "date")
    _update_row(row_id, last_run_at=int(time.time()), last_task_id=task_id,
                enabled=0 if tj["type"] == "date" else 1,
                next_run_at=next_ts)


def _task_status(task_id: int) -> tuple[str, str]:
    conn = connect()
    try:
        row = conn.execute("SELECT status, substr(result_summary,1,120) AS s FROM tasks WHERE id=?",
                           (task_id,)).fetchone()
        return (row["status"], row["s"] or "") if row else ("unknown", "")
    finally:
        conn.close()


def _notify(kind: str, text: str, task_id: int | None = None) -> None:
    conn = connect()
    try:
        with tx(conn):
            conn.execute("INSERT INTO notifications(kind, text, ref_task_id, created_at) "
                         "VALUES(?,?,?,?)", (kind, text, task_id, int(time.time())))
    finally:
        conn.close()


def create(kind: str, goal: str, when_text: str, use_llm: bool = True) -> tuple[dict | None, str | None]:
    """创建调度的统一入口（API 与对话共用）。返回 (行, 错误信息)。
    时间解析顺序：规则 → LLM 兜底 → 报错引导用户给出明确时间"""
    tj = parse_when(when_text)
    if tj is None and use_llm:
        from .providers import router as provider
        if provider.available():
            try:
                raw = provider.chat(
                    [{"role": "user", "content": when_text}],
                    system="从这句话提取定时语义，只输出 JSON："
                           '{"type":"cron","hour":9,"minute":30}（每天定时）｜'
                           '{"type":"interval","minutes":30} 或 {"type":"interval","hours":2}（周期）｜'
                           '{"type":"date","run_at":unix时间戳}（一次性，用未来时间）。'
                           "提取不出就输出 null。只输出 JSON 或 null。",
                    max_tokens=120,
                ).strip()
                if raw and raw != "null":
                    cand = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
                    if cand.get("type") in ("cron", "interval", "date"):
                        tj = cand
            except Exception:
                pass  # LLM 兜底失败不阻塞：落到错误引导
    if tj is None:
        return None, "没能理解时间，请用：每天 9:30 / 每 30 分钟 / 明天 15 点 / 2026-11-08 09:00"
    conn = connect()
    try:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO schedules(kind, goal, trigger_json, created_at) VALUES(?,?,?,?)",
                (kind, goal, json.dumps(tj, ensure_ascii=False), int(time.time())))
            sid = cur.lastrowid
        next_ts = add(sid, tj)
        with tx(conn):
            conn.execute("UPDATE schedules SET next_run_at=? WHERE id=?", (next_ts, sid))
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        return dict(row), None
    finally:
        conn.close()
