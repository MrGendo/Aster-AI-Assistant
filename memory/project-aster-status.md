---
name: project-aster-status
description: Aster 已转向「自进化智能体平台」（2026-11-04 新 PRD），新 Phase 1 spec/plan 待批准与三项拍板
metadata:
  type: project
---

Aster 于 2026-11-04 **整体转向**：需求源为 `product_requirement.md`（自进化智能体平台，四阶段）。**Phase 1 主体已完成并端到端跑通**（1-0~1-7 勾选）：FastAPI 主板（server/aster）+ 子进程能力运行时 + SQLite 九表 + Vue3 四页 UI（web/，服务托管于 http://127.0.0.1:8317/）+ 治理网关（L0/L1/L2+审批+审计）+ GLM provider（密钥在 Keychain，自举自 claude CLI）。旗舰能力 repo_analysis 实测通过（**git clone 直连可用**）；15 项单测全过。旧方案归档在 `docs/archive/butler-v1/`。**待办**：1-8 用户浏览器易用性走查（PRD §10.8）→ 1-9 阶段回顾与 Stage 2 spec；git 仓库零提交待用户决定。

**Why:** SDD 规约与用户"按新 PRD 重做"指令；形态/归档/GLM 三项均为用户拍板结果。

**How to apply:** 服务启动 `.venv/bin/python server/run.py`（127.0.0.1:8317），前端改动 `cd web && pnpm build`；Stage 2（开源比较与自动接入）未经用户批准不得开始；需求判定只看 `product_requirement.md`（§20 边界清单防扩权）；每片勾选+任务日志；测试 `cd server && ../.venv/bin/python -m pytest tests/ -q`。
