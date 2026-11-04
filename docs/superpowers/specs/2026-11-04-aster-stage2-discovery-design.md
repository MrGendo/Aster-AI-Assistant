# Aster Stage 2 设计（开源项目比较与能力接入 · Spec）

- 日期：2026-11-04
- 需求输入：`product_requirement.md` §5（发现与比较）、§6（能力构建与管理）、§8.3（元能力闭环）、§12.3（外部项目安全评估）、§19 第二阶段
- 前置：Phase 1 已交付（主板/能力运行时/治理/四页 UI），`docs/task-log/2026-11-04.md` 有实测数据
- 状态：**待用户批准执行**

## 1. Stage 2 目标（对齐 PRD §19 第二阶段）

让系统**有依据地**寻找、评估和吸收新能力：

1. 按任务需求自动发现候选开源项目（任务缺口触发）
2. 用户推荐项目的完整评估流水线（Phase 1 的初步分析升级为评估）
3. 同类项目识别与横向比较（不依赖单一热度）
4. 证据/风险/成本/推荐理由齐备的评估报告（PRD §5.7 十项）
5. 候选生命周期状态机（PRD §5.8 九阶段）与隔离测试
6. 能力版本管理（升级先验证、可回滚）
7. 元能力起步：用 claude headless 辅助生成能力适配代码（L2 强审批 + 人工审阅）
8. 主动跟踪（watchlist + 轻量调度循环）

**明确不做**：无审批自动执行外部代码、多能力协作编排（S3）、主板进化（S4）。

## 2. Phase 1 实测回顾（1-9，数据来自 data/aster.db）

| 指标 | 实测 | 结论 |
|---|---|---|
| 意图分类正确率 | 6/6（含用户实际提交的 AIHOT 仓库） | 规则优先策略够用，LLM 兜底未误触发 |
| 能力运行 | 3/3 exit=0，2.4-3.0s | 运行时稳定 |
| chat 成功率 | 1/3（2 次失败均为开发期状态机 bug，修复后通过） | 状态机测试已覆盖该类路径 |
| 审计 | 3 条调用记录可查 | 链路完整 |
| 待改进 | 任务详情里步骤信息薄（单步任务）；SSE 实时性未压测 | Stage 2 顺带补强 |

## 3. 核心设计

### 3.1 发现（Discovery）

- 数据源：`gh` CLI（本机已装且已认证，搜索走 `gh api search/repositories`）——**连通性列为 2-1 首验项**；备选 GitHub 匿名 API（限速 10 次/分，记录在案）
- 触发方式：① 任务失败/能力缺口时主板发起（评估"缺口"由 LLM 判断 + 规则兜底：task error 关键词）② 用户推荐链接（沿用）③ watchlist 定期巡检（轻量调度循环，自研 sleep-loop，沿用旧方案"自研调度"决策）

### 3.2 候选生命周期（PRD §5.8 落地）

```
discovered → screened → deep_analysis → compared → isolated_test
          → integration_eval → pending_approval → integration_verified → active / archived
```

- 落库为 `candidates` 表（状态机同任务状态机模式：迁移表 + 单测）
- **每一级推进都必须有对应产物**（screened=筛选理由、compared=评估报告、isolated_test=测试记录），无产物不得推进——防止"发现即能力"

### 3.3 同类比较与评估报告

- 同类识别：候选元数据（topics/语言/描述/README 关键词）→ LLM 判定"同一问题/部分重叠/不同问题"（PRD §5.4：不同问题不强排）；Phase 2 不上向量（torch 441MB 事实仍有效）
- 报告 = PRD §5.5 十二维评估模板 + 证据分级（verified=代码/提交/实测，inferred=自述/推测）+ 推荐（接入/测试/观察/暂缓/拒绝 + 理由）
- 评估执行者：`repo_analysis` 能力升级版（比较模式：多仓并列采集）+ LLM 综合评判
- 报告落 `evaluation_reports` 表，UI 展示"结论先行、证据可展开"（PRD §10.6）

### 3.4 接入与隔离（安全边界是本阶段成败关键）

接入三形态（按风险升序）：

| 形态 | 说明 | 门槛 |
|---|---|---|
| A 知识记录 | 只入评估档案，不可执行 | 报告批准即可 |
| B 包装为能力 worker | claude headless 生成适配脚本（沿用 PoC-5 已验证语义：隔离宿主规则、取末个 JSON）| 生成属 L2；产物只能进 candidate，**人工审阅代码后**方可进入隔离测试 |
| C MCP server 接入 | 候选本身是 MCP server | 同 B + 独立审批 |

- **沙箱的诚实边界**：Phase 2 沙箱 = 独立目录 + 子进程超时 + 网络出站不隔离（macOS 无容器下的现实约束，如实告知用户）；任何"运行候选项目自身代码"的动作一律 L2 审批；容器化（Docker）列为可选增强，不承诺
- 能力版本：`capability_versions` 表；升级=新版本 candidate 验证通过后切换 active 旧版归档；回滚=一键恢复历史版本记录

### 3.5 元能力（PRD §8 起步）

- 首个元能力：`adapter_scaffold`——给定候选项目分析结果，生成能力包装脚本草稿（调用 claude -p，白名单只读工具）
- 元能力产物**永远不是正式能力**：candidate → 人工审阅 → 隔离测试 → 批准
- 记录接入失败原因到 `integration_lessons`（经验复用的最小起点，PRD §8.4）

### 3.6 数据模型（迁移 0002）

```
candidates(id, repo_url, name, lifecycle, trigger, discovered_at, metadata_json, …)
evaluation_reports(id, candidate_id, dimensions_json, evidence_json, recommendation, created_at)
candidate_tests(id, candidate_id, kind, result_json, exit_code, created_at)
capability_versions(id, capability_id, version, entry, descriptor_json, status, created_at)
watchlist(id, repo_url, note, last_checked_at, frequency)
integration_lessons(id, repo_url, stage_failed, reason, created_at)
```

### 3.7 UI 增量

新增「发现与评估」页（PRD §10.2 第五页）：提交链接 / 候选看板（生命周期状态列）/ 报告阅读（结论先行）/ 接入审批卡。能力中心增加版本历史与回滚入口。

## 4. 关键决策

| # | 决策 | 依据 |
|---|---|---|
| S1 | 发现数据源用 gh CLI（已认证） | 本机事实；匿名 API 限速做备选 |
| S2 | 同类比较不上向量，用元数据+LLM | torch 内存事实；候选数量级小（个位数） |
| S3 | 运行外部代码 = 永远 L2 + 无网络隔离的沙箱边界如实声明 | PRD §12.3；macOS 现实约束 |
| S4 | 适配代码由 claude headless 生成但必须人工审阅 | PoC-5 宿主规则继承教训 + PRD §6.3 步骤 9 |
| S5 | 调度用自研 sleep-loop（watchlist 巡检） | 延续旧方案决策；需求极简（小时级） |

## 5. 验收（PRD §21 对应行）

1. 同类比较：对任一需求能给出候选差异、推荐依据与不确定性说明，不只有 stars 排名
2. 用户推荐：提交链接 → 评估流水线全程状态可见；**未获批准的候选永远不可被任务调用**（有测试）
3. 能力接入：新能力走完 生命周期+隔离测试+批准 才 active（有测试：candidate 调用被拒）
4. 版本管理：升级留历史、回滚可执行
5. 元能力：一次真实的 adapter 生成→审阅→测试→接入全流程留痕（可展示给用户检查）

## 6. 风险

| 风险 | 应对 |
|---|---|
| gh 搜索限速/不可用 | 2-1 首验；备选匿名 API + 限速退避 |
| LLM 评判不稳定 | 报告模板固定十二维，LLM 只填依据不做自由发挥；关键结论标注置信度 |
| 自动生成代码质量差 | 定位为"草稿"：人工审阅是流程硬环节，不追求全自动 |
| 范围蔓延（S3/S4 提前） | 计划显式排除；每片验收对照本 spec §1 |
