-- 0008：成本护栏（设计：docs/superpowers/specs/2026-11-05-batch-hardening-design.md）
-- tokens_used 记主板侧真实 LLM 用量；cost_usd 记子进程能力回报的美元成本（如 deep_research）
ALTER TABLE tasks ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0;
