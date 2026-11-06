-- 0009：回归评测与质量报告（设计：docs/superpowers/specs/2026-11-06-qa-platform-design.md）
CREATE TABLE IF NOT EXISTS eval_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability TEXT NOT NULL,             -- 目标能力；'*' 为全局用例（原 replay_set 种子迁入）
    name TEXT NOT NULL,
    goal TEXT NOT NULL,                   -- 走完整任务通道的目标文本
    expect TEXT NOT NULL,                 -- 验收标准（replay_grade llm-rubric 用）
    enabled INTEGER NOT NULL DEFAULT 1,
    last_result_json TEXT,                -- 最近一次执行结果（status/summary/graded）
    finished_at INTEGER,
    source TEXT DEFAULT 'manual',         -- manual / lesson（教训沉淀）
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_cap ON eval_cases(capability, enabled);

-- 任务质量报告：验证链/证据数/未验证事项的结构化聚合（结果页质量卡用）
ALTER TABLE tasks ADD COLUMN quality_json TEXT;

-- replay_set 三条种子迁入全局用例（原表保留，evolution 回放实验仍读它）
INSERT OR IGNORE INTO eval_cases(capability, name, goal, expect, source, created_at)
SELECT '*', name, task_goal,
       '输出切题且包含具体事实或结果，不是拒绝、报错或空洞回复', 'replay_seed', strftime('%s','now')
FROM replay_set;
