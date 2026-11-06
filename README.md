# Aster · 自进化多能力 Agent 平台

> 一句话：**主板调度、工人干活、门禁管风险、评估闭环负责进化**——所有结论可回溯到审计与证据的本地 Agent 平台。

Aster 是一个跑在本地的自进化智能体平台：FastAPI 主板统一负责意图路由、多能力编排与安全治理，子进程工人承载各项能力；通过"发现 → 评估 → 文档关卡 → 审批 → 隔离测试 → 接入 → 巡检"的进化闭环持续吸收开源项目为平台新能力。

## 架构

```
┌─ 前端 SPA（Vue3，8 个视图）────────────────────────────┐
│ 对话首页 │ 待办 │ 定时 │ 任务中心 │ 能力中心             │
│ 发现与评估 │ 成长与进化 │ 设置      + 侧栏通知           │
└───────────────────┬───────────────────────────────────┘
                    │ 单一入口 POST /api/chat（自然语言）
                    ▼
┌─ 主板 orchestrator ──────────────────────────────────┐
│ 意图分类（规则优先 → LLM 全标签兜底）                    │
│   ├ 待办 / 闹钟 / 定时任务（轻量直执）                   │
│   ├ 任务型 → 单能力模板 或 LLM 多步规划（DAG 数据流）     │
│   │    分层并行 + fallback 接管 + on_fail 策略          │
│   │    + 失败重规划 + run_if 条件步骤                    │
│   └ 对话型 → 多轮会话线程（上下文窗口 + 语义记忆注入）     │
└───────────────────┬───────────────────────────────────┘
                    ▼
┌─ 治理与质量 ──────────────────────────────────────────┐
│ 三级门禁：L0 自动 / L1 审批 / L2 永不自动（硬编码）       │
│ 质量门：llm_verify 步骤评审 + 交付终检（未过如实降级）    │
│ 成本护栏：token/美元双预算熔断（可调）                    │
│ 全链路审计：tool_calls 落库，任务结论蒸馏入语义记忆       │
└───────────────────┬───────────────────────────────────┘
                    ▼
        子进程工人（JSON stdin→stdout，独立 venv 隔离）
```

## 内置能力（11 个）

| 能力 | 说明 | 来源 |
|---|---|---|
| repo_analysis | GitHub 仓库定位/结构/许可/活跃度/风险分析（证据分级） | 自研 |
| repo_quality | OpenSSF Scorecard 健康度评分 | 自研 |
| web_fetch | 网页抓取（SSRF 内网拦截 + 逐跳重定向校验） | 自研 |
| browser_browse | JS 动态页面读取与交互（browser-use） | 吸收 |
| news_fetch / hot_news_digest | HN 抓取 → 聚合去重 → 中文热点日报 | 自研 + AIHOT 策展内核 |
| deep_research | 自主联网调研，产出带引用来源的中文报告（gpt-researcher） | 吸收 |
| mem0_memory | 语义记忆读写（本地 bge 嵌入 + chroma） | 吸收 |
| llm_task / llm_verify | 通用文本加工 / 独立评审伪能力 | 自研 |
| replay_grade | 回放质量评分（promptfoo llm-rubric） | 吸收 |

外部能力吸收遵循**文档关卡**：先完整读透官方文档、产出理解摘要与陷阱清单，再动手集成——所有返工教训沉淀入库。

## 快速开始

```bash
# 1. 依赖（Python 3.10+）
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt 2>/dev/null || .venv/bin/pip install fastapi uvicorn anthropic apscheduler markdown
.venv/bin/pip install browser-use mem0ai promptfoo gpt-researcher ddgs langchain-mcp-adapters langchain-anthropic langchain-huggingface sentence-transformers

# 2. deep_research 用独立 venv（gpt-researcher 依赖阵营与 browser-use 互斥，架构级隔离）
.venv/bin/python -m venv .venvs/deep_research
.venvs/deep_research/bin/pip install gpt-researcher ddgs langchain-mcp-adapters langchain-anthropic langchain-huggingface sentence-transformers

# 3. 模型配置：GLM 走 anthropic 兼容路由，复用本机 ~/.claude/settings.json 的
#    ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN（无则自行设置环境变量）

# 4. 前端构建
cd web && pnpm install && pnpm build && cd ..

# 5. 启动（SQLite 迁移自动执行，当前 0008）
.venv/bin/python server/run.py
# 打开 http://127.0.0.1:8317
```

试试这些输入（对话首页一条流全搞定）：

```
分析 https://github.com/asg017/sqlite-vec
深度调研：MCP 生态 2026 现状
帮我加个待办：明天整理周报 (A)
提醒我 30 分钟后休息
每天早上 9 点给我热点日报
规划执行：分析 xxx 仓库，然后总结项目定位
```

## 测试

```bash
PYTHONPATH=server .venv/bin/python -m pytest server/tests -q   # 53 例
```

覆盖：路由契约、规划校验（on_fail/run_if/引用合法性）、失败策略、时间语义解析、预算熔断、迁移与生命周期。

## 设计立场

- **计划先行，不放自治**：不做"模型自己决定下一步调什么"的开放循环——门禁可审批、审计可回溯、预算可熔断是底线；工人内部可以是受限 agent（如 browser-use 带步数上限与只读约束）
- **未验证不显示成功**：结构校验、步骤评审、交付终检三层；评审不过如实降级 partial，不伪装
- **证据分级**：评估外部项目从"项目自述"到"源码实证"五级；本仓库自己的交付说明也遵守同一标准——所有量化结论均有任务实测记录（见 `docs/task-log/`）

## 目录结构

```
server/aster/        # 主板：orchestrator(gateway/runtime/providers/scheduler/evaluation)
capabilities/        # 子进程工人（每个能力一个目录，JSON 协议）
web/                 # Vue3 + Vite SPA（对话首页/DAG 调用图/待办/定时）
docs/                # PRD、SDD 设计与计划、集成规则、任务日志
memory/              # 项目记忆（进化教训与状态）
```

## License

待定（代码为个人项目，如需引用请先联系作者）。
