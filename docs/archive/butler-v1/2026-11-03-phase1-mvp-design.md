# Phase 1 MVP 设计（Spec）

- 日期：2026-11-03
- 依据：`docs/01-product-requirements.md` §7.1 MVP 范围、`docs/03-system-architecture.md`、`docs/04-data-model-and-api.md`、Phase 0 PoC 结论（`poc/README.md`）
- 状态：**待用户批准执行**

## 1. 目标

交付可连续日用的 MVP：悬浮球 + 聊天（GLM/Anthropic 可配）+ 记忆（确认式入库）+ 待办与提醒 + 简单调度 + 安全底座（三级权限/Keychain/审计/紧急停止）。**语雀已阻断出局；知识库与 Claude Code 集成属 Phase 2。**

## 2. 仓库结构（从零建主干，poc/ 保留作参考不并入）

```
Aster-AI_Assistant/
├── apps/desktop/          # Tauri 2 + Vue 3 桌面壳（全新脚手架，移植 poc 的窗口/生命周期配置）
│   ├── src/               # Vue：ball / chat / tasks / settings 四视图
│   └── src-tauri/         # 双窗口 + 核心服务生命周期（沿用 poc lib.rs 模式）+ 通知/自启插件
├── core/                  # Python 包：aster-core（FastAPI 单服务）
│   ├── aster_core/
│   │   ├── api/           # 路由：chat/tasks/memories/approvals/system/ws
│   │   ├── agent/         # 原生循环 + 上下文组装
│   │   ├── gateway/       # 工具网关：风险评级/审批/审计
│   │   ├── tools/         # 内置工具（L0 检索类起步）
│   │   ├── memory/        # 记忆服务 + FTS5（jieba 双模式配方）
│   │   ├── todo/          # 待办 + NL 解析
│   │   ├── scheduler/     # 自研轮询调度器 + misfire 恢复
│   │   ├── security/      # token 握手/Keychain(keyring)/目录白名单/急停
│   │   ├── db/            # SQLite WAL + 迁移（PRAGMA user_version）
│   │   └── providers/     # ModelRouter + GLM 兼容/Anthropic provider
│   └── migrations/0001_init.sql
├── data/                  # dev 数据目录（ASTER_DATA_DIR 可覆写；正式默认平台路径）
└── docs/ poc/             # 设计文档与 PoC 记录
```

## 3. 关键设计决策（全部有 PoC 或用户决策背书）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 桌面壳全新脚手架于 `apps/desktop`，悬浮球/双窗口/sidecar 生命周期配置从 `poc/floating-ball` 移植 | PoC-1/2 已验证模式 |
| D2 | FTS5 中文检索用 **jieba 双模式（cut+cut_for_search）并集入索引 + 查询 OR + BM25**；trigram 不用 | PoC-3：10/10、P@5=0.95 |
| D3 | 调度器自研（schedules 表 + 30s 轮询 + misfire 策略），不引 APScheduler | 用户确认 + 02-§9 |
| D4 | ModelRouter provider 全配置化：**GLM Anthropic 兼容端点 / Anthropic 官方** 可作主备；不硬编码任何后端假设 | 用户环境事实（claude CLI 实为 glm-5.3）+ PoC-5 |
| D5 | Phase 1 **不含 embedding/向量**（记忆检索用 FTS5 关键词 + 时间衰减）；向量组件 Phase 2 独立进程化 | PoC-4：torch 基线 441MB |
| D6 | 工具网关 L2 永不自动为代码级硬约束；审批超时默认拒绝 | 06-§3.2 |
| D7 | Python 依赖管理 venv + pip + requirements 锁定（不装 uv） | 用户确认 |
| D8 | 对话/编程任务的宿主规则隔离：核心服务派发子进程时注入任务专用 system 指令并显式禁用全局规则继承（Phase 1 先做提示词级，Phase 2 随 ClaudeCodeAdapter 深化） | PoC-5 发现：宿主 CLAUDE.md 会被继承导致停在 SDD 审批关卡 |

## 4. 数据库迁移范围（0001_init.sql）

`conversations / messages / memories（+FTS5 虚拟表）/ tasks / schedules / task_runs / tool_calls / approvals / agents（初始一条"管家"）/ settings`——字段以 `docs/04` §2 为准；启动恢复流程按 04-§3 状态机实现（`interrupted` 分支先落地）。

## 5. API 实现范围

`docs/04` §4 中 Phase 1 相关路由：chat(SSE)、conversations、tasks(+nl)、runs、memories(+confirm)、approvals、system(status/pause/resume/export)、ws 事件总线。coding/yuque/knowledge 路由不实现。

## 6. 验收

- `docs/01-§11` 中除语雀/编程任务外的全部条目；
- 连续 14 天 dev 模式日用（`pnpm tauri dev` + 本地核心服务），无致命伤；
- 安全清单 S-1~S-5（06-§10）全过。

## 7. 待用户拍板（进入执行前）

- **Q1 ModelRouter 默认主 provider**：A. GLM 兼容端点（用你现有 GLM 计划的 API Key，与 claude CLI 同源） B. Anthropic 官方 API Key（两态都实现，只定默认与初始配置）。
- **Q2 悬浮球目测**（Phase 0 遗留）：透明无白底 / 拖动流畅 / 穿透切换 是否 OK（应用仍在运行，控制台可试）。
