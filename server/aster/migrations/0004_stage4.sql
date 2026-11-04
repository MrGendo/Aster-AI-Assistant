-- 0004：Stage 4 受控进化
CREATE TABLE IF NOT EXISTS improvement_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evidence_json TEXT NOT NULL,        -- 问题证据快照（统计/失败任务摘录）
  hypothesis TEXT NOT NULL,           -- 归因假设
  change_json TEXT NOT NULL,          -- 参数变更清单（可逆）
  scope TEXT NOT NULL,                -- 影响范围描述
  risk TEXT NOT NULL DEFAULT 'medium',-- low/medium/high
  acceptance_frozen TEXT NOT NULL,    -- 验收标准：落库后系统侧只读，仅人工可改
  status TEXT NOT NULL DEFAULT 'draft',  -- draft/experimented/approved/applied/rolled_back/rejected
  experiment_result_json TEXT,
  snapshot_json TEXT,                 -- 应用前配置快照（回滚依据）
  applied_at INTEGER,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_set (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  task_goal TEXT NOT NULL,
  category TEXT DEFAULT 'general',
  is_control INTEGER NOT NULL DEFAULT 0,  -- 对照集：不参与改进调试
  last_result_json TEXT
);
