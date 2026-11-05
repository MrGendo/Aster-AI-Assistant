-- 0006：待办管理（设计：docs/superpowers/specs/2026-11-05-todo-management-design.md）
-- 结构字段只留 priority 与 due（todo.txt 哲学：其余归自由文本靠搜索）；
-- task_id 回链"转任务"执行后的 Aster 任务
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    priority TEXT,                 -- A-Z，可空
    due TEXT,                      -- YYYY-MM-DD，可空
    done INTEGER NOT NULL DEFAULT 0,
    task_id INTEGER REFERENCES tasks(id),
    source TEXT NOT NULL DEFAULT 'user',  -- user / agent（对话路由写入）
    created_at INTEGER NOT NULL,
    done_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_todos_done ON todos(done, id);
