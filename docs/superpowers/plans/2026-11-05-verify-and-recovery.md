# 执行计划：验证评估节点 + 失败策略与重规划

设计文档：`docs/superpowers/specs/2026-11-05-verify-and-recovery-design.md`

## 任务清单

- [ ] 1. planner 契约：高风险步骤强制 llm_verify 步 + on_fail 字段（skip/retry 指引）+ replan 接口
- [ ] 2. 执行循环：on_fail 三策略（abort/skip/retry）+ skipped 状态落库
- [ ] 3. 重规划：abort 失败触发 planner.replan（保留 outputs、续 seq、每任务一次）
- [ ] 4. 交付终检：report/deep_research/hot_news 意图 _llm_verify 门（通过 completed / 未过 partial）
- [ ] 5. 前端：skipped 状态文案与 DAG 灰色渲染
- [ ] 6. 单测：策略路径/重规划上限/终检触发表/契约校验
- [ ] 7. 端到端：必然失败任务真实重规划改道；报告任务真实过终检
- [ ] 8. 任务日志归档 + 提交

## 回滚

执行循环与 planner 改动集中在 core.py/planner.py，git revert 即回 fail-fast 原行为。
