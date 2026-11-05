# 设计：三问题修复 + P1（run_if / 记忆蒸馏）

日期：2026-11-05 ｜ 状态：执行中（用户已在消息中明确授权范围）

## 1. LLM 兜底分类器扩全意图

现状只认 repo_analysis/text_stats/chat。改为输出全标签集（含一句职责描述）：
repo_analysis / repo_quality / text_stats / hot_news / report / deep_research /
mem0_add / mem0_search / browser / planned / chat。输出不在集合内回落 chat。

## 2. 成本护栏（token 与美元双预算）

- provider 新增 `chat_with_usage()`（anthropic 响应自带 usage，现被丢弃），chat() 保持兼容委托
- migration 0008：tasks 增 `tokens_used INTEGER DEFAULT 0`、`cost_usd REAL DEFAULT 0`
- 记账点：llm_task / llm_verify / classify 兜底 / 规划与重规划 的真实 token；
  deep_research 子进程回报的 costs（美元）记入 cost_usd
- 熔断点：执行循环每步执行前与重规划前检查——超 `budget.max_tokens`（默认 30 万）
  或 `budget.max_cost_usd`（默认 $1.0）→ 诚实中止"超出预算熔断"+通知；设置页可调（settings 表）
- 边界如实声明：deep_research 成本事后可知，熔断防的是后续步骤/重试叠加，不是单次预拦

## 3. run_if 条件步骤（P1）

- planner 契约新增可选 `"run_if": "$N.key == 字面值"`（也支持 != / contains / 裸真值判断）
- 运行时：就绪批选择时求值——条件为假 → 步骤标 skipped（理由落 error 字段），计划继续
- validate_plan：run_if 必须匹配语法（拒绝裸胡写）；不合法即拒计划

## 4. 任务结果回流记忆（P1）

- execute() 终态收尾（completed/partial 且 intent != chat）时：LLM 蒸馏
  "目标+结论+关键数字"成 ≤120 字事实句 → 经 mem0_memory 能力（L0）入库
- 蒸馏失败静默降级（不因记忆影响交付）；设置 `memory.distill` 默认开

## 5. 重规划闭环复现（验证项）

构造"web_fetch 抓内网地址被 SSRF 防护拒绝"的规划任务——步骤真实失败（不涉审批）→
重规划改道 → 新步骤执行 → 交付。以任务终态与重规划日志为证。

## 验证标准

- 单测：分类器标签集校验、run_if 求值三态、预算熔断触发、蒸馏守门（chat 不蒸馏）
- 端到端：重规划闭环任务成功交付；一次完成任务后 mem0 检索能召回其结论；
  预算调低后任务如实熔断
- 既有 48 测试零回归
