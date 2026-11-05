# 执行计划：deep_research 能力

设计文档：`docs/superpowers/specs/2026-11-05-deep-research-capability-design.md`

## 任务清单

- [x] 1. 安装依赖：`pip install gpt-researcher duckduckgo-search`，冒烟 `import gpt_researcher`（检查与 mem0/browser-use 的版本冲突）
- [x] 2. 新建 `capabilities/deep_research/`（run.py）：环境注入 GLM/本地 embedding/duckduckgo，15 分钟超时，报告落 data/reports/
- [x] 3. registry 注册能力（L1），路由规则：classify 增加 深度调研/全面调研 关键词
- [x] 4. 单测：worker 参数校验、路由命中/让路、载荷抽取（31 passed；另修 phoenix pytest 插件与 mcp 降级冲突）
- [x] 5. 端到端实测：真实主题出带引用中文报告，验证下载（任务 #70：11861 字 / 11 来源 / $0.12 / 下载 200）
- [x] 6. 评估登记：candidate_tests 写入实测结论；候选 #13 lifecycle → integration_verified
- [x] 7. 任务日志归档 + 提交

## 回滚

能力为新增（registry 可禁用），不改既有路径；卸载依赖即可回退。
