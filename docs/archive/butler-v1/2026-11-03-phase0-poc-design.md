# Phase 0 地基 PoC 设计（Spec）

- 日期：2026-11-03
- 依据：`docs/02-technical-research.md` §11 PoC 清单（PoC-8 已取消）、`docs/05-development-roadmap.md` Phase 0
- 状态：**待用户批准执行**
- 说明：本会话无 `superpower` 技能可调用，按 SDD 规约的目录与结构要求手工建立本文件

## 1. 目标与非目标

**目标**：在写任何主干代码前，用一次性验证代码把设计中的高不确定项全部证实/证伪。任何一项不达标 → 回到设计修订，不带病进 Phase 1。

**非目标**：不做产品功能、不写主干代码、不考虑代码复用与优雅度（`poc/` 下代码用完即弃或仅作参考）、不接入真实私人数据。

## 2. 方法与目录约定

```
poc/
├── README.md            # 总结论：每项 PoC 的结果、数据、结论（Go/No-Go）
├── floating-ball/       # PoC-1 Tauri 透明悬浮球
├── sidecar/             # PoC-2 Python 服务 + sidecar 生命周期
├── fts5/                # PoC-3 FTS5 中文方案对比（脚本，无服务）
├── vec/                 # PoC-4 sqlite-vec + bge embedding 实测
├── cc-headless/         # PoC-5 Claude Code headless 验证（脚本）
├── yuque-mcp/           # PoC-6 语雀官方 MCP 连通（脚本）
└── macos/               # PoC-7 通知/自启/LSUIElement 行为记录
```

- 每个子目录自带最小运行说明；Python 侧共用一个 venv（`poc/.venv`）；
- 所有结果与数据记入 `poc/README.md`，作为 Phase 1 spec 的输入。

## 3. 各 PoC 设计要点与通过标准

### PoC-1 Tauri 透明悬浮球（对应 02-§1）

- 脚手架：`pnpm create tauri-app`（Vue 3 + TypeScript 模板）；
- 验证项：`macos-private-api` feature + `transparent/alwaysOnTop/skipTaskbar` 窗口配置；鼠标拖动（前端 pointer 事件 + 窗口 setPosition）；`set_ignore_mouse_events` 穿透切换；六态用纯 CSS 帧占位切换。
- **通过标准**：透明无白底不闪烁；拖动流畅（目测 60fps）；置顶不被普通窗口遮挡；穿透/恢复切换生效。
- **失败预案**：降级为"不透明异形窗口（圆角 + 阴影 + 透明 PNG 背景）"，重新评估 02-§1 结论后再议。

### PoC-2 核心服务 + sidecar 生命周期（对应 02-§10）

- Python：FastAPI + uvicorn，`/health` 端点；启动经 stdin 读一次性 token，请求校验 `X-Aster-Token`；
- Tauri：shell 插件 sidecar 拉起核心服务；轮询 `/health`；检测退出自动重启。
- **通过标准**：壳启动 → 服务就绪 < 5s；`kill -9` 服务进程后 5s 内自动重启；无 token 请求 403；壳退出后无孤儿进程。

### PoC-3 FTS5 中文检索（对应 02-§7）

- 对比三方案：trigram（冒烟已通过）/ 预分词列（jieba 分词后入 FTS5，原始列保存原文）/ unicode61 裸跑（基线）；
- 样例集：20 条中文知识标题/段落 + 10 个查询词（含无空格连写、长短词混合），人工评分准召。
- **通过标准**：至少一方案目测达标（预期：预分词列与 trigram 各有优劣，选主用 + 记录权衡）。

### PoC-4 sqlite-vec + 本地 embedding（对应 02-§8，已定案本地）

- `sentence-transformers` 加载 bge-small-zh-v1.5（备选 bge-m3），M4 走 MPS；pip 锁 `sqlite-vec` 版本；
- HuggingFace 直连不通时使用 `HF_ENDPOINT=https://hf-mirror.com` 镜像；
- 实测：1000 条短文本批量嵌入耗时、进程常驻内存增量、vec0 表 KNN 查询延迟。
- **通过标准**：千条 < 2 分钟；常驻内存增量 < 300MB；KNN < 50ms。

### PoC-5 Claude Code headless（对应 02-§5.2）

- 用一个小型本地测试仓库（脚本自建 git repo，含几个文件），依次验证：readonly 分析（`--allowedTools Read Grep`）、write 任务（目录内改文件）、结构化输出（`--json-schema`）、取消（SIGTERM → 退出码 143）、`--output-format json` 读 `total_cost_usd` 与 `session_id`、`--resume` 续跑一轮。
- **通过标准**：上述六项全部与官方文档语义一致；readonly 跑完 `git status` 干净。
- **成本提示**：约消耗用户 claude 账户小额费用（预估 < $0.5，以 readonly 小任务为主）。

### PoC-6 语雀官方 MCP（对应 02-§5.1）

- `npx -y yuque-mcp@latest` stdio 拉起，Python MCP client 调 `search` / `get_doc`；
- **前置依赖：需要用户提供 `YUQUE_PERSONAL_TOKEN`**（语雀 → 设置 → Token 创建，仅放 Keychain/环境变量，不写任何文件）。
- **通过标准**：搜索返回真实结果；读取文档正文成功；记录实际工具名清单与 README 声称的 19 个差异。

### PoC-7 通知/自启/LSUIElement（对应 02-§10）

- 在 PoC-1 的壳上加 notification 插件与 autostart 插件，记录未签名场景行为（横幅是否可见/有无声音/点击是否可达）；LSUIElement 经 Info.plist `LSUIElement=true`（dev 模式验证 `set_activation_policy` API）。
- **通过标准**：行为如实记录成表；用户已接受降级（面板内提醒兜底），故本项为"记录型"验收，不设否决线。

## 4. 依赖与安装范围（执行期会发生的事）

- `poc/` 内：pnpm/npm 包、cargo crates（Tauri 脚手架）、pip 包（fastapi/uvicorn/sentence-transformers/torch(MPS)/sqlite-vec/mcp/jieba）——全部装在项目目录或 venv 内，不动系统全局；
- 不装 `uv`（用 venv+pip 替代）；不初始化产品数据库（PoC 用内存库或临时文件库）；
- 需要用户提供：语雀 Token（仅 PoC-6）；claude CLI 已登录（PoC-5 直接用现有登录态）。

## 5. 完成定义（DoD）

`poc/README.md` 内七项结论齐全（PoC-1/2/3/4/5/6/7 各含数据与 Go/No-Go），据此修订 02/03 文档的【待验证】项为【已确认】，并产出 Phase 1 的 spec/plan 供下一轮审批。
