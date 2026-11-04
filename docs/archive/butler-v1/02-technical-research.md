# Aster 技术调研报告

- 版本：v0.1（设计阶段草案）
- 日期：2026-11-03
- 调研方法：官方文档/官方仓库一手来源核对（WebFetch 在本网络环境被拦截，实际通过 webReader 通道抓取；GitHub 内容经 raw.githubusercontent.com 核对）
- 标注约定：【已确认】= 一手来源验证（附链接，验证日期 2026-11-03）；【设计建议】= 本报告的推荐及权衡；【待验证】= 需 PoC 落实

---

## 0. 结论速览

| # | 议题 | 结论 | 信心 |
|---|---|---|---|
| 1 | 桌面壳 | **Tauri 2**（2.10.2 稳定版），前端 **Vue 3** | 高（透明窗口细节需 PoC） |
| 2 | 核心语言 | **Python**（FastAPI 单服务） | 高 |
| 3 | Agent 编排 | **原生自研轻量循环**（不用 LangGraph） | 高 |
| 4 | 工具/外部集成 | **语雀官方 MCP Server + 官方 API；Claude Code CLI headless 子进程** | 高（语雀已因会员要求阻断，降级可选） |
| 5 | 存储 | **SQLite（WAL）+ FTS5 + sqlite-vec**；知识本体 Markdown | 高（sqlite-vec pre-v1 需锁版本） |
| 6 | 检索 | **BM25（FTS5）+ 向量双路融合**，MVP 不上 rerank 模型 | 中高 |
| 7 | 调度 | **自研 SQLite 轮询调度器**（不用 APScheduler） | 中高 |
| 8 | 模型接入 | **Provider 适配层：Anthropic API 为主 + 可配置备用** | 高 |
| 9 | macOS 集成 | Tauri 插件（托盘/通知/自启）+ `macos-private-api`（透明）+ Keychain | 中高（需 PoC） |
| 10 | 质量门禁 | PoC 清单见 §11，进入编码前逐项打勾 | — |

> **决策状态（用户确认 2026-11-03）**：① #7 调度器定案自研轻量轮询（PoC-8 取消）；② embedding 定案本地（bge + M4 MPS），PoC-4 仅验证速度与内存；③ 未签名场景通知降级可接受（面板内提醒兜底）；④ 开发期以 `tauri dev` 模式日用，`.app` 打包后置。

> **环境基线（本机实测 2026-11-03）**：node v22.23.1 / pnpm 11.9.0 / rustc 1.97.0 / Python 3.14.7（内置 SQLite 3.53.4）/ CLI sqlite3 3.43.2 / claude CLI 2.1.234 / git 2.39.5；`uv` 未安装（以 venv+pip 替代或执行期安装）。FTS5 trigram 中文匹配冒烟测试通过（PoC-3 前置条件成立）。**用户日常 `claude` CLI 经 Anthropic 兼容端点接 GLM 后端（实测 glm-5.3）**——ModelRouter 与 ClaudeCodeAdapter 均不得假定 Anthropic 官方后端，provider 全配置化。

---

## 1. 桌面壳：Tauri 2 vs Electron

**【已确认】Tauri 2 现状**（来源：crates.io/crates/tauri，验证 2026-11-03）：

- 最新稳定版 **2.10.2**（2026-02-04 发布），2.9.5（2025-12），迭代活跃（2026 年内已发 10+ 版）。
- Cargo features 中确认存在：`macos-private-api`（macOS 透明窗口/隐藏等私有 API 支持）、`tray-icon`（托盘）、`tracing`（日志）。
- 官方插件生态含 shell（sidecar 外挂进程）、notification（通知）、autostart（自启）、global-shortcut、clipboard 等（来源：v2.tauri.app/plugin/）。
- Tauri 2 stable 自 2024-10 发布，移动端支持（iOS/Android）已并入 2.x——本项目用不到，但说明主线稳定。

**对比：**

| 维度 | Tauri 2 | Electron |
|---|---|---|
| 安装包/内存 | ~10MB 级、内存低（系统 WebKit） | ~100MB+、每窗口一个 Chromium |
| macOS 集成 | 原生 Rust 侧，可调用私有 API（透明窗口） | 需 extraResources/原生模块折腾 |
| 与 Python sidecar | tauri-plugin-shell 原生支持 sidecar | 手写 spawn + 生命周期管理 |
| 生态/资料 | 较新，坑要自己踩（见风险） | 极成熟 |
| 前端框架 | 任意（我们用 Vue 3） | 任意 |

**已知风险【已确认存在，影响待 PoC】**：透明窗口相关 issue 在 Tauri 仓库长期存在（如 #9408、#10318、#12800 等），**但主要是 Windows 平台**（webview 重绘/白屏）；macOS 侧普遍可用但需开启 `macos-private-api`，个别子窗口/失焦场景有报告。我们是 macOS 单平台，风险收敛，但**悬浮球是核心体验，必须 Phase 0 PoC**。

**结论【设计建议】**：选 **Tauri 2**。理由：内存占用（常驻挂机场景敏感）、sidecar 机制与"Python 核心服务"架构天然契合、Rust 壳层后期可做更深的系统集成。Electron 的唯一显著优势（资料多）不足以抵消其常驻开销。

---

## 2. 前端：Vue 3 vs React vs 其他

**结论【设计建议】**：**Vue 3 + Vite + TypeScript + Pinia**。

- 本项目前端是"本地工具面板"：表单、列表、流式聊天渲染、Mermaid/Markdown 展示，Vue 3 的响应式与单文件组件对这类 UI 开发效率高；
- 用户主环境有 Vue/小程序 UI/UX 背景偏好（现有技能与经验沉淀），单人维护选自己最熟的栈；
- React 无不可替代优势；Svelte 更轻但生态与招式深度不及；纯 SwiftUI 则与前端团队栈割裂且失去跨端可能性。
- 关键库均成熟【已确认，属公知事实】：Vue 3 current stable、Pinia、`@vueuse/core`、markdown-it / mermaid（渲染知识文档）。

---

## 3. 核心后端语言：Python vs Go vs 双语言

**结论【设计建议】**：**Python 3.12+（FastAPI + uvicorn）单核心服务**，放弃双语言。

理由：

1. **AI 生态压倒性优势**：Anthropic 官方 Python SDK、MCP 官方 Python SDK、sentence-transformers（本地 embedding）、sqlite-vec（pip 直装）全部第一手支持 Python；
2. Go 的优势（并发性能、单二进制分发）对"单用户本地服务"不构成瓶颈——我们的并发是"1 个用户 + 少量子进程"；
3. 双语言（如 Rust 壳 + Go 核心）会让单人维护成本翻倍，违反"复杂度可控"原则；
4. 性能敏感点（桌面壳、透明窗口）已由 Tauri 的 Rust 层承担，Python 只做 IO 编排，不跑数值计算（embedding 走模型服务/库的 C 后端）。

代价与对策：打包分发需带 Python 运行时（用 `uv`/pyinstaller 或 Tauri sidecar + bundled python），Phase 0 验证 sidecar 打包链路。

---

## 4. Agent 编排：原生自研 vs LangGraph/框架

**结论【设计建议】**：**MVP 自研原生 Agent 循环**（一个 `while` 主循环：组上下文 → 调模型 → 解析工具调用 → 工具网关执行 → 回填，~300 行核心代码），不引入 LangGraph。

理由：

1. 我们的核心需求（工具审批插桩、审计、风险分级、任务状态机）**必须在工具执行路径上有完全控制权**——框架的封装层反而挡路；
2. 单用户场景不需要框架解决的分布式/持久化图执行问题；
3. 上下文组装（记忆注入 + 检索注入 + 预算裁剪）是本产品核心差异化，自己写才能精细控制；
4. Claude Agent SDK（Python）虽提供现成 agent loop【已确认：code.claude.com/docs/en/agent-sdk/overview】，但它是"以 Claude Code 为库"的形态，绑定 Claude Code 工具集，作为**编程适配器**用（§5），不作总编排——总编排需要跨 provider 与自定义工具网关。

**多 Agent 路径**：P2 阶段在同一进程内实现多角色（管家/知识/编程/研究/复核），共享工具网关与审计——自研循环下这只是"多个配置实例"，无框架迁移成本。**不采用独立服务多进程**（单机单用户无必要）。

---

## 5. 外部能力集成：MCP vs 官方 API vs CLI

### 5.1 语雀

**【已确认】官方能力三件套**（验证 2026-11-03）：

| 能力 | 形态 | 要点 |
|---|---|---|
| 官方 REST API | `https://www.yuque.com/api/v2`，Token 认证（个人设置 → Token） | 文档：www.yuque.com/yuque/developer/api（该页面 noindex，部分内容需登录态查看） |
| 官方 MCP Server | github.com/yuque/yuque-mcp-server（npm `yuque-mcp`） | **19 个工具**：搜索、知识库 CRUD、文档 CRUD（创建/更新/删除）、目录（TOC）管理、小记、画板；stdio 传输，`npx -y yuque-mcp@latest`；`yuque_update_doc` 不能同时改 body 与 title/slug/public；存在 429 限流与 410 废弃端点；未覆盖：评论、附件上传、权限管理、段落级编辑 |
| 官方 Claude Code 插件 | github.com/yuque/yuque-plugin | `claude plugin marketplace add yuque/yuque-plugin`；插件 README 称 25 工具（MCP README 称 19，版本演进中的差异，以 npm 最新版为准【待验证】） |

**结论【设计建议】**：**首选官方 MCP Server 作为语雀工具层**（Python MCP SDK 作 client，stdio 拉起），原因：官方维护、工具语义现成、与未来任何 MCP 客户端复用。**辅以直接 REST 调用**处理 MCP 未覆盖的端点（如附件）。写入操作一律套上我们的 L2 审批门（在工具网关层，不动 MCP server 本身）。

> **【已确认阻断，用户验证 2026-11-03】**：语雀官方 MCP / 开放 API 需**超级会员**账号方可使用，用户无此会员。语雀集成整体降级为"可选，待会员变更后启用"——`YuqueAdapter` 接口保留在架构中（默认禁用），相关需求/任务移出主线阶段；cookie 型社区方案因合规与脆弱性否决。

### 5.2 Claude Code（编程代理）

**【已确认】headless 模式能力**（来源：code.claude.com/docs/en/headless，验证 2026-11-03）：

```bash
claude -p "<任务>" \
  --output-format stream-json --verbose \   # 事件流进度
  --permission-mode dontAsk \               # 无人值守（默认 Manual，v2.1.302+）
  --permission-prompts none \               # 跳过人工确认
  --allowedTools "Read" "Grep" "Bash(git diff:*)" \  # 前缀白名单
  --json-schema <file> \                    # 结构化输出
  --max-budget-usd 2.0 \                    # 预算上限
  --resume <session-id>                     # 续跑
```

- 退出码：0 成功 / 非 0 失败 / SIGTERM → 143（取消语义明确）；
- JSON 输出含 `total_cost_usd`、`session_id`；stream-json 的 `system/init` 事件带会话元数据，子 Agent 事件有 `parent_tool_use_id`；
- 工作目录即 `cwd` 传参，天然支持"目标仓库"指定。

**【已确认】Claude Agent SDK**：Python `claude-agent-sdk` / TS `@anthropic-ai/claude-agent-sdk` 提供同源能力；官方文档明确：其他语言/需要解耦时，`claude -p --output-format json` 子进程是推荐路径。

**结论【设计建议】**：MVP 用 **CLI 子进程适配器**（`ClaudeCodeAdapter`：spawn + stream-json 解析 + 超时/取消/预算/审计），不嵌 Agent SDK。理由：子进程隔离最干净（崩溃不连累核心服务）、退出码/信号语义即取消语义、避免 SDK 与我们工具网关的双重权限体系打架。SDK 方式留作 P2 优化项。

**关于"接管 VS Code 插件会话"**：`--resume` 针对的是 headless 自己的 session id【已确认】；接管用户交互中的 VS Code 插件会话没有官方支持路径，列为 **P2 默认不做**。

### 5.3 MCP 协议本身

**【已确认】**：MCP 官方 Python SDK（github.com/modelcontextprotocol/python-sdk，`pip install "mcp[cli]"`，Python 3.10+）**同一包同时提供 server 与 client**，传输支持 stdio / Streamable HTTP / SSE（旧）。规范站 spec.modelcontextprotocol.io。

**结论【设计建议】**：本项目**只做 MCP client（消费方）**——拉起语雀 MCP server 等；Aster 自身暂不暴露 MCP server（P2 可评估：把 Aster 工具暴露给其他 MCP 客户端）。

---

## 6. 存储：SQLite vs PostgreSQL vs Markdown+Git

**【已确认】sqlite-vec**（github.com/asg017/sqlite-vec）：纯 C、零依赖、`pip install sqlite-vec`、`vec0` 虚拟表 + KNN 查询；**pre-v1**（作者明示 1.0 前会有 breaking changes）。

**对比：**

| 方案 | 评估 |
|---|---|
| **SQLite（WAL）** | 单文件、零运维、读写性能对本量级绰绰有余；FTS5 内建 BM25；配合 sqlite-vec 一个库搞定三类检索。**选定** |
| PostgreSQL | 单机常驻服务 + 独立进程运维，对单用户是纯负担；向量还得加 pgvector。否决 |
| 纯 Markdown + Git | 适合**知识本体**（人类可读、可 diff、可迁移），但查询能力弱——**作为知识文档存储层而非数据库**，配合 SQLite 索引 |

**结论【设计建议】**：**混合**——SQLite 单库（WAL 模式）存全部结构化数据（会话/记忆/任务/调度/审计/配置）+ FTS5 全文索引 + sqlite-vec 向量表；**知识文档本体 = Markdown 文件树**（`data/knowledge/`，建议 P1 起加 git init 做版本史），SQLite 存元数据与索引，内容哈希关联。sqlite-vec 锁定版本并在迁移注释中标注 pre-v1 风险。

---

## 7. 检索：全文 / 向量 / 混合 / 重排

**结论【设计建议】**：

- **第一层**：FTS5 BM25 关键词检索（中文分词是难点，用 `trigram` tokenizer 或 Simple/jieba 分词预处理【待验证：FTS5 中文方案 PoC，候选：unicode61 + 预分词列 / trigram / sqlite-fts5 扩展】）；
- **第二层**：sqlite-vec 向量 KNN（embedding 模型选型见 §8 与 PoC 清单）；
- **融合**：RRF（Reciprocal Rank Fusion，倒数排名融合，公式简单无需调参）合并两路结果；
- **重排（rerank）**：MVP 不上——单用户查询频率低，两路融合已够；P2 若检索质量不足，再评估本地 bge-reranker 或 API rerank。

知识/记忆/语雀检索共用这一套检索服务，按 `source` 字段区分来源。

---

## 8. 模型接入：多 Provider 与故障切换

**【已确认】Claude 模型与价格**（来源：claude-api skill 内置参考，数据日期 2026-10-06）：

| 模型 | 输入/输出 $/MTok | 备注 |
|---|---|---|
| claude-opus-5-5 | 4 / 20 | 复杂任务 |
| claude-sonnet-5-5 | 2 / 10 | 主力 |
| claude-haiku-5-5 | 0.10 / 0.50 | 轻/分类/摘要 |
| claude-fable-5-1 | 10 / 50 | 旗舰 |

编程任务走 Claude Code（用户已有订阅/凭证），管家对话走 Anthropic API（官方 Python SDK）。

**结论【设计建议】**：`ModelProvider` 适配层：

```
ModelRouter ──> AnthropicProvider(官方SDK, 默认)
            └─> OpenAICompatProvider(自定义base_url, 可选备用)
```

- 配置驱动（模型映射：聊天/摘要/嵌入 各自可配）；
- 失败策略：单 provider 内指数退避（2/4/8s，最多 3 次）→ 切备用 provider → 全失败则任务标记 `failed_provider` 并通知用户；
- Embedding 选型【已定案】：**A：本地 `sentence-transformers` + 中文模型（bge-small-zh / bge-m3，M4 MPS 推理，零成本私享）**（用户确认 2026-11-03；本地优先原则 + 知识库内容隐私）。PoC-4 仅需验证批量速度与常驻内存。

---

## 9. 调度：持久化定时器与停机恢复

**【已确认】APScheduler**（apscheduler.readthedocs.io/en/master，验证 2026-11-03）：master 文档已是重构后新架构（Scheduler + 数据存储 + 事件代理 + 任务/调度/执行三元模型；`misfire_grace_time`、`coalesce: latest|earliest|all`、`max_running_jobs`、SQLAlchemy 持久化存储、多调度器共享存储）。**版本状态注意**：文档 master 分支对应新架构，PyPI 当前稳定版是 3.x 还是 4.x 正式版需锁定确认【待验证】——3.x（BackgroundScheduler + SQLAlchemyJobStore）与 4.x API 不兼容，教程资料混杂。

**结论【设计建议】**：**自研轻量调度器**（~150 行）：

```
schedules 表(next_run_at, trigger, misfire_policy, status)
  + 30s 轮询循环 + asyncio 任务池
```

理由：

1. 我们的触发需求极简单（一次性 / 固定间隔 / cron 三种，cron 解析用 `croniter` 库）；
2. **misfire/补执行语义必须与任务状态机（04 文档）深度耦合**（missed/interrupted/补跑标注），借框架反而要做双向同步；
3. APScheduler 3.x/4.x 过渡期 + jobstore pickle 序列化的历史包袱，不如 150 行透明代码可控；
4. 单机单进程、任务量 < 10³，30s 轮询 + 索引扫描毫无压力。

停机恢复：启动时 `UPDATE schedules SET status='misfire_check'` 对 `next_run_at < now` 者按 `misfire_policy`（grace 内补跑 / 标记 missed / 跳到下个周期）处理——细节见 03/04 文档。

---

## 10. macOS 集成：后台、自启、通知、透明窗口、权限

**【已确认】Tauri 2 官方插件与机制**（v2.tauri.app，验证 2026-11-03）：

| 需求 | 方案 | 状态 |
|---|---|---|
| 透明窗口 | `macos-private-api` feature + `transparent: true`（macOS 走 NSWindow 私有 API 路径） | 【已确认存在】【待验证】实际效果 PoC |
| 始终置顶 | `alwaysOnTop: true` | 标准能力 |
| 点击穿透 | `set_ignore_mouse_events` | 【已确认】tao/wry 层支持（tauri#184 讨论即为该 API） |
| 托盘 | `tray-icon` feature + TrayIcon API | 官方支持 |
| 通知 | tauri-plugin-notification（macOS 走 UserNotifications，需签名后才能弹横幅——**未签名开发版会静默降级为无声横幅**【待验证】PoC 确认行为） | 官方插件 |
| 开机自启 | tauri-plugin-autostart（macOS 底层 SMAppService/LaunchAgents） | 官方插件 |
| 无 Dock 图标 | `LSUIElement` / Tauri `activationPolicy` 配置 | 【待验证】细节 PoC |
| 密钥 | macOS Keychain：Rust 侧 `security-framework` 或核心服务侧 `keyring`（Python）二选一，**倾向 Python 侧 `keyring`**（与核心服务密钥读取统一） | 【已确认】keyring 库支持 macOS Keychain |
| 权限申请 | MVP 不申请辅助功能/屏幕录制等系统权限；通知权限首次弹窗授权 | — |

**进程模型【设计建议】**：

- Tauri 壳（UI + 托盘 + 悬浮球）为"前台进程"，退出即退出应用；
- Python 核心服务作为 **sidecar 子进程**由壳拉起（崩溃由壳监测重启）；核心服务自身也可独立运行（headless 模式，方便测试与 CLI 调试）；
- 通信：本机 `127.0.0.1` 固定端口 REST + WebSocket/SSE（流式）；**绑定 127.0.0.1 + 启动时随机 token 握手**防本机其他进程偷调（详见 06 安全文档）。

---

## 11. 风险登记与 PoC 清单（进入编码前完成）

| # | PoC 项 | 验证内容 | 通过标准 | 对应风险 |
|---|---|---|---|---|
| PoC-1 | Tauri 透明悬浮球 | `macos-private-api` + transparent + alwaysOnTop + 拖动 + 穿透切换，M4/macOS 当前版本 | 无白底闪烁、拖动 60fps、穿透生效 | 悬浮球是核心体验 |
| PoC-2 | Python sidecar 链路 | Tauri 拉起/重启/关闭 Python 服务，REST+WS 打通 | 杀死核心服务 5s 内自动重启 | 架构地基 |
| PoC-3 | FTS5 中文检索 | trigram vs 预分词列，中文查询准召 | 20 条中文样例目测达标 | 中文是主语言 |
| PoC-4 | sqlite-vec + 本地 embedding | bge-m3/small-zh 在 M4（MPS）批量嵌入速度与内存 | 千条文档 < 2 分钟、常驻内存增量 < 300MB | 向量检索可行性 |
| PoC-5 | Claude Code headless | 真实仓库跑只读分析 + 写任务（目录白名单）+ 取消（退出码 143）+ total_cost_usd 读取 | **已过（2026-11-03 实测，后端 glm-5.3）**：七项全过；新增发现见 `poc/README.md`（宿主 CLAUDE.md 规则继承需隔离、stdout 多行噪声解析、非 Anthropic 后端全程兼容） | 编程集成核心 |
| PoC-6 | 语雀官方 MCP | npx 拉起、client 调 search/create、429 行为 | **阻断**：需超级会员（用户确认 2026-11-03），集成降级为可选 | 语雀集成核心 |
| PoC-7 | 通知 + 自启 + LSUIElement | 未签名场景通知是否可达；重启后自启 | 通知弹出可见；自启成功；若降级则如实记录行为（用户已接受面板内提醒兜底） | 打包与体验 |
| ~~PoC-8~~ | **已取消**——调度器自研方案经用户确认（2026-11-03）；若 Phase 3 出现复杂调度需求再评估 | — | — | — |

---

## 12. 引用来源汇总（验证日期均为 2026-11-03）

1. Claude Code headless：https://code.claude.com/docs/en/headless
2. Claude Agent SDK：https://code.claude.com/docs/en/agent-sdk/overview
3. 语雀开放 API：https://www.yuque.com/yuque/developer/api（noindex 页面，内容部分需登录）
4. 语雀官方 MCP：https://github.com/yuque/yuque-mcp-server（README 经 raw 核对）
5. 语雀官方 Claude Code 插件：https://github.com/yuque/yuque-plugin（README 经 raw 核对）
6. MCP Python SDK：https://github.com/modelcontextprotocol/python-sdk
7. MCP 规范：https://spec.modelcontextprotocol.io
8. Tauri 版本与特性：https://crates.io/api/v1/crates/tauri ；https://v2.tauri.app/start/
9. APScheduler：https://apscheduler.readthedocs.io/en/master/userguide.html
10. sqlite-vec：https://github.com/asg017/sqlite-vec
11. Claude 模型与价格：claude-api skill 内置参考（数据日期 2026-10-06）

> 注：本会话网络环境 WebFetch 域名校验全面失败，以上内容均经 webReader 通道抓取核对；github.com 页面经 raw.githubusercontent.com 的 README 原文核对，仓库元数据经 crates.io API 核对。
