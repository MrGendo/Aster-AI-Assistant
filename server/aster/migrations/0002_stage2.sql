-- 0002：Stage 2 候选/评估/版本/巡检/经验

CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_url TEXT NOT NULL UNIQUE,
  name TEXT,
  lifecycle TEXT NOT NULL DEFAULT 'discovered',
  trigger TEXT NOT NULL DEFAULT 'user',      -- user / task_gap / watchlist
  trigger_ref TEXT,                          -- 触发来源（任务 id 等）
  metadata_json TEXT,                        -- gh 元数据：stars/topics/language/描述
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_lifecycle ON candidates(lifecycle, updated_at);

CREATE TABLE IF NOT EXISTS evaluation_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  dimensions_json TEXT NOT NULL,             -- 十二维评估（PRD §5.5）
  evidence_json TEXT NOT NULL,               -- verified / inferred 两级证据
  peers_json TEXT,                           -- 参与比较的同类仓库
  recommendation TEXT NOT NULL,              -- integrate / test / watch / hold / reject
  reason TEXT NOT NULL,
  confidence REAL DEFAULT 0.6,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  kind TEXT NOT NULL,                        -- adapter_run / smoke / manual
  result_json TEXT,
  exit_code INTEGER,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  capability_id INTEGER NOT NULL REFERENCES capabilities(id),
  version TEXT NOT NULL,
  entry TEXT NOT NULL,
  descriptor_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate',  -- candidate / active / archived
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_url TEXT NOT NULL UNIQUE,
  note TEXT,
  last_head TEXT,                            -- 最近一次看到的 HEAD commit
  last_checked_at INTEGER,
  frequency_s INTEGER NOT NULL DEFAULT 3600
);

CREATE TABLE IF NOT EXISTS integration_lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_url TEXT NOT NULL,
  stage_failed TEXT NOT NULL,                -- 失败阶段（lifecycle 值或 adapter/test）
  reason TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
