# 执行计划：deep_research 能力

设计文档：`docs/superpowers/specs/2026-11-05-deep-research-capability-design.md`

## 任务清单

- [ ] 1. 安装依赖：`pip install gpt-researcher duckduckgo-search`，冒烟 `import gpt_researcher`（检查与 mem0/browser-use 的版本冲突）
- [ ] 2. 新建 `capabilities/deep_research/`（adapter.py + manifest）：环境注入 GLM/本地 embedding/duckduckgo，15 分钟超时，报告落 data/reports/
- [ ] 3. registry 注册能力（L1），路由规则：classify 增加 深度调研/全面调研 关键词
- [ ] 4. 单测：adapter 参数校验与输出解析、路由命中
- [ ] 5. 端到端实测：真实主题出带引用中文报告，验证 DAG 展示与下载
- [ ] 6. 评估登记：candidate_tests 写入实测结论；候选 #13 lifecycle → integrated
- [ ] 7. 任务日志归档 + 提交

## 回滚

能力为新增（registry 可禁用），不改既有路径；卸载依赖即可回退。
