-- 0003：Stage 3 协作——步骤依赖与数据流 + 能力统计
ALTER TABLE task_steps ADD COLUMN output_key TEXT;
ALTER TABLE task_steps ADD COLUMN depends_on TEXT;   -- JSON 数组：依赖步的 seq

CREATE TABLE IF NOT EXISTS capability_stats (
  capability_id INTEGER PRIMARY KEY REFERENCES capabilities(id),
  calls INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 0,
  avg_ms INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
