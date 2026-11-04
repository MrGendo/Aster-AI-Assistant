# Aster 平台 Phase 1 任务计划

- 日期：2026-11-04
- Spec：`docs/superpowers/specs/2026-11-04-aster-platform-phase1-design.md`
- 状态：**待用户批准执行**（含 Spec §10 Q1/Q2/Q3 待拍板项）

## 任务清单（每片完成即系统可运行、可验证）

- [x] 1-0 转向清理：旧 docs/01-06 + poc + 旧 spec/plan 归档至 `docs/archive/butler-v1/`（剔除可再生成的 venv/target/node_modules）；新结构 server/ web/ capabilities/ data/ 落地
- [x] 1-1 服务骨架：FastAPI + config + 日志 + SQLite 迁移器（0001 全部九表）+ 任务九态状态机，15 项单元测试全过
- [x] 1-2 能力运行时 v0：descriptor 规范（PRD 6.4 精简版）+ 注册表 + 子进程 runner（进程组/超时/输出捕获/沙箱目录）+ candidate 不可调用隔离（有测试）
- [x] 1-3 主板 v0：规则优先 + LLM 兜底的意图分类 + 三模板顺序调度 + SSE 进度 + 结构校验（不验证不成功）+ 交付摘要
- [x] 1-4 旗舰能力 repo-analysis：**git clone 直连验证通过**（此前唯一连通性风险解除）；sqlite-vec 仓库实测 3 秒出六维报告，证据分级（verified/inferred）落位；定位启发式修掉徽章行误抓
- [x] 1-5 治理底座：L0/L1/L2 网关（审批复用/超时拒绝/暂停挂起，全部有测试）+ 审计 append-only（API 可查）+ Keychain 密钥（GLM 配置从 claude CLI 自举成功，密钥仅存钥匙串）+ 暂停/恢复
- [x] 1-6 记忆基础：三类 CRUD + 确认闸语义（手工=确认，任务沉淀=candidate）+ 上下文注入（活跃记忆 top-N）
- [x] 1-7 Web UI：Vue3 四页 + SSE 任务事件 + 深浅色 + PRD §10.7 视觉基线；构建产物由服务托管 http://127.0.0.1:8317/
- [x] 1-8 验收：单测 15/15 与三条任务路径 e2e（repo_analysis/text_stats/chat-live-GLM）已过；易用性走查经用户确认（2026-11-04，"可以了"）；初始提交 e810738 留档
- [ ] 1-9 阶段回顾：Stage 2（开源比较与自动接入）spec 草案——**待用户说开始再动，产出后停等批准**

## 执行约束

- 每完成一片：勾选 + `docs/task-log/` 追加（目标/方式/文件行号/验证输出）；
- 编码遵守用户 CLAUDE.md《编程与注释规约》：最简安全实现、中文注释解释"为什么"；
- 新需求一律对照 `product_requirement.md` 校验，不私自扩大 Stage 1 范围（PRD §20 边界清单为准）；
- 不可逆操作（删除旧产物、真实外部调用）按拍板结果与授权执行。
