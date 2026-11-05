-- 0007：定时推送与闹钟（设计：docs/superpowers/specs/2026-11-05-chat-first-home-and-scheduler-design.md）
-- schedules 是唯一事实源，APScheduler 用 MemoryJobStore（重启后从本表全量重注册）
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- task=定时任务 / alarm=闹钟（一次性）
    goal TEXT NOT NULL,                 -- task: 任务目标 / alarm: 提醒文本
    trigger_json TEXT NOT NULL,         -- {"type":"cron|interval|date", ...}
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at INTEGER,
    next_run_at INTEGER,
    last_task_id INTEGER REFERENCES tasks(id),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- alarm / task_done
    text TEXT NOT NULL,
    ref_task_id INTEGER REFERENCES tasks(id),
    read INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications(read, id);
