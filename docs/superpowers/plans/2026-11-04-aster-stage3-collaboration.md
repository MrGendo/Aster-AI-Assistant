# Aster Stage 3 任务计划

- 日期：2026-11-04
- Spec：`docs/superpowers/specs/2026-11-04-aster-stage3-collaboration-design.md`
- 状态：**待用户批准执行**

## 任务清单

- [ ] 3-0 迁移 0003（task_steps 加 output_key/depends_on；capability_stats）+ 步骤 DAG 合法性校验单测（环/引用/active 检查）
- [ ] 3-1 规划器：LLM 产出计划 JSON + 硬校验 + 模板回退（现有三条模板路径保留为回退）
- [ ] 3-2 执行器 v1：拓扑序执行 + "$stepN.key" 占位符数据流 + 步数/总超时上限 + awaiting_user 语义
- [ ] 3-3 失败接管：步骤级 fallback 链 + 降级审计记录
- [ ] 3-4 评审步：LLM verify（pass/fail+理由）接入验证态
- [ ] 3-5 capability_stats 统计与规划提示词注入；能力中心成功率展示
- [ ] 3-6 UI：任务详情依赖渲染；组合失败自动入 lessons
- [ ] 3-7 验收：spec §5 五项演示（含单能力 vs 协作链对比）→ 阶段回顾
- [ ] 3-8 阶段回顾 → Stage 4（主板受控进化）spec 草案 → 停等批准

## 执行约束

- 每片勾选+任务日志；测试全绿才勾；LLM 计划永不绕过硬校验与网关
- v2 项（候选比较/迭代/条件分支/真并发）不做，想法记录不实施
