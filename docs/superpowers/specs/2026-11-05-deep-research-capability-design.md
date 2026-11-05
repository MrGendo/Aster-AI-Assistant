# 设计：deep_research 能力（gpt-researcher 集成）

日期：2026-11-05 ｜ 候选 #13 ｜ 状态：待批准

## 一、文档研读结论（doc-first 关卡产出）

**读了什么**：pip-package / deep_research / llms / supported-llms / search-engines / retriever-plugins / config 全套官方文档 + `llm_provider/generic/base.py` 源码。

**优势**：
- 成熟调研流水线：研究问题生成 → 检索 → 抓取 → 向量去重 → 分主题综合 → 带引用报告；★30489，Apache-2.0，活跃维护
- langchain 基座 + `ChatAnthropic(**kwargs)` 不显式传 base_url → anthropic SDK 读 `ANTHROPIC_BASE_URL` 环境变量——与 browser-use 同一接法，已验证可行
- `RETRIEVER=duckduckgo` 免费无 key；`LANGUAGE=chinese` 原生中文报告；`get_research_sources()` 返回引用来源（契合 PRD 证据分级）
- 库形态集成干净：`GPTResearcher(query, report_type)` → `await conduct_research()` → `await write_report()`

**短板与陷阱（文档明示）**：
1. 默认 OpenAI + Tavily 双 key，不显式配置直接崩
2. EMBEDDING 默认 openai 端点——GLM token 在该端点 429（此前实测），必须换本地 embedding（huggingface provider 复用 mem0 已装的 bge-small-zh-v1.5）
3. 官方声明"针对 GPT 优化，其他模型可能上下文超限/意外响应"——GLM 需调高 SMART_TOKEN_LIMIT 并小迭代打样
4. 依赖重（langchain 全家桶），与现有 langchain-anthropic/mem0 的版本兼容需装完冒烟实测
5. 普通 research_report 约 2-3 分钟；deep 模式约 5 分钟/$0.4——首版不启用 deep
6. duckduckgo 检索器需额外 `pip install duckduckgo-search`

## 二、集成方案

**新能力**：`deep_research`（子进程工人，JSON stdin→stdout 协议，与其他 10 个能力同构）

- **输入**：`{topic: 调研主题, report_type?: "research_report", max_words?: 1500}`
- **输出**：`{report_path: data/reports/dr-*.md, sources: [{url,title}...], costs, summary}` + 状态码 0/2/3/4
- **环境注入**（adapter 内构造，密钥从 provider 表/Keychain 取，不落代码）：
  - `ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic`、`ANTHROPIC_AUTH_TOKEN=<GLM>`
  - `FAST_LLM/SMART_LLM/STRATEGIC_LLM=anthropic:<glm-model>`
  - `EMBEDDING=huggingface:BAAI/bge-small-zh-v1.5`（本地，免 key）
  - `RETRIEVER=duckduckgo`、`LANGUAGE=chinese`、`MAX_ITERATIONS=2`、`SMART_TOKEN_LIMIT=16000`
  - `NO_PROXY` 含 localhost/open.bigmodel.cn
- **超时**：15 分钟，进程组 kill（runner 已有机制）
- **门禁**：L1（联网检索 + 付费 token），默认走审批，不进 l1_auto 白名单
- **路由**：classify 增加"深度调研/全面调研某个主题"关键词 → deep_research；与现有 report 伪能力的分工——report=基于已有任务素材汇总，deep_research=自主联网新调研
- **报告落盘**：data/reports/，走既有下载链路（GET /api/tasks/{id}/file）

**不做的**：deep 模式（成本时长不可控，观察名单）、MCP retriever、多检索器组合、前端新页面（复用 ResultView 的 markdown 渲染）。

## 三、验证标准

1. 冒烟：.venv 安装后 `import gpt_researcher` 无版本冲突
2. 单测：adapter 参数校验、输出解析、超时路径
3. 端到端：真实主题（如"Rust 生态 2026 趋势"）跑通，产出带引用的中文报告，DAG/下载正常
4. 评估登记：candidate_tests 记录实测结论进平台库
