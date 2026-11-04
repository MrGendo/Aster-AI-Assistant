-- 0001：Phase 1 全部核心表
-- 时间戳统一 UTC unix 秒；status 字面值见 aster.tsm / 各模块说明

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal TEXT NOT NULL,                -- 用户原始目标表述
  intent TEXT,                       -- 意图分类: repo_analysis / text_stats / chat
  plan_json TEXT,                    -- 规划结果（步骤描述数组）
  status TEXT NOT NULL DEFAULT 'pending',
  cancel_requested INTEGER NOT NULL DEFAULT 0,  -- 取消请求与实际停止分离（PRD 4.4）
  project_id INTEGER,
  result_summary TEXT,               -- 交付摘要
  result_ref TEXT,                   -- 交付物路径/引用
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at);

CREATE TABLE IF NOT EXISTS task_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  seq INTEGER NOT NULL,
  capability_id INTEGER REFERENCES capabilities(id),
  step_desc TEXT,
  input_json TEXT,
  output_ref TEXT,                   -- 大输出落盘后的文件引用
  status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/done/failed/skipped
  error TEXT,
  started_at INTEGER, finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id, seq);

CREATE TABLE IF NOT EXISTS capabilities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,                -- tool/task/agent/workflow/meta/system（PRD 6.2 六类的 Phase 1 精简）
  descriptor_json TEXT NOT NULL,     -- 统一能力说明（PRD 6.4 精简版）
  status TEXT NOT NULL DEFAULT 'candidate',  -- candidate/active/disabled/archived
  source TEXT NOT NULL DEFAULT 'builtin',
  version TEXT NOT NULL DEFAULT '0.1.0',
  risk_level TEXT NOT NULL DEFAULT 'L0',
  entry TEXT NOT NULL,               -- worker 脚本相对 capabilities/ 的路径
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER REFERENCES tasks(id),
  step_id INTEGER REFERENCES task_steps(id),
  capability_id INTEGER REFERENCES capabilities(id),
  exit_code INTEGER,
  duration_ms INTEGER,
  output_path TEXT,
  error TEXT,
  started_at INTEGER NOT NULL,
  finished_at INTEGER
);

-- 审计与审批（append-only：应用层不提供更新/删除路径）
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  task_id INTEGER,
  capability TEXT NOT NULL,
  risk TEXT NOT NULL,
  status TEXT NOT NULL,              -- approved_auto/pending/approved/rejected/timeout/success/failed
  args_digest TEXT,
  result_digest TEXT,
  started_at INTEGER NOT NULL,
  finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tool_call_id INTEGER NOT NULL UNIQUE REFERENCES tool_calls(id),
  risk TEXT NOT NULL,
  reason TEXT,                       -- 展示给用户的原因与影响说明
  requested_at INTEGER NOT NULL,
  decided_at INTEGER,
  decided_by TEXT,                   -- user / timeout
  decision TEXT                      -- approved / rejected
);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,               -- preference / background / project
  content TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'manual',   -- manual / task / confirm
  source_ref TEXT,                   -- 来源任务或会话标识
  confidence REAL NOT NULL DEFAULT 1.0,
  status TEXT NOT NULL DEFAULT 'candidate',     -- candidate/active/superseded/deleted
  valid_until INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, status);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT,
  boundary_json TEXT,                -- 项目数据边界（路径等），Phase 1 仅存储
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL                -- JSON 标量或对象
);
