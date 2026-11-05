---
name: project-aster-status
description: Aster 已转向「自进化智能体平台」（2026-11-04 新 PRD），新 Phase 1 spec/plan 待批准与三项拍板
metadata:
  type: project
---

Aster 于 2026-11-04 **整体转向**：需求源为 `product_requirement.md`（自进化智能体平台，四阶段）。**Phase 1 主体已完成并端到端跑通**（1-0~1-7 勾选）：FastAPI 主板（server/aster）+ 子进程能力运行时 + SQLite 九表 + Vue3 四页 UI（web/，服务托管于 http://127.0.0.1:8317/）+ 治理网关（L0/L1/L2+审批+审计）+ GLM provider（密钥在 Keychain，自举自 claude CLI）。旗舰能力 repo_analysis 实测通过（**git clone 直连可用**）；15 项单测全过。旧方案归档在 `docs/archive/butler-v1/`。**待办**：Phase 1 全收口（e810738）。**Stage 2 已收口**（efc8675 + 0258097）：AIHOT 全链路接入实测完成——评估（同类比较+证据分级）→ claude 生成适配草稿（hot_news_digest，抽取 AIHOT 策展内核）→ 用户审阅 → L2 审批隔离测试 → 接入 active（v0.1.0 版本记录）→ watchlist 巡检。能力中心现有 3 个 active 能力。遗留：2-8 lessons 待首次失败触发；回滚待第二版本。**Stage 3（多能力协作编排）未启动，待用户批准 spec 后才动**。

**Why:** SDD 规约与用户"按新 PRD 重做"指令；形态/归档/GLM 三项均为用户拍板结果。

**How to apply:** 服务启动 `.venv/bin/python server/run.py`（127.0.0.1:8317），前端改动 `cd web && pnpm build`；Stage 2（开源比较与自动接入）未经用户批准不得开始；需求判定只看 `product_requirement.md`（§20 边界清单防扩权）；每片勾选+任务日志；测试 `cd server && ../.venv/bin/python -m pytest tests/ -q`。

**2026-11-05 deep_research 集成**（候选#13 gpt-researcher ★30489 Apache-2.0，doc-first 关卡后接入）：第 11 个能力，L1 门禁，GLM(anthropic路由)+本地bge+duckduckgo 免费检索；任务#70 端到端 11861 字/11 引用/$0.12/146s。关键坑：glm-4.7 thinking 块需压平 ChatAnthropic；gpt-researcher 检索器急切导入需 langchain-mcp-adapters；duckduckgo 拆包需 ddgs。教训：重启服务必须 kill $(lsof -ti :8317 -sTCP:LISTEN)，不加 -sTCP:LISTEN 会杀错进程导致旧代码继续服务（本次误判路由失效 20 分钟）。
