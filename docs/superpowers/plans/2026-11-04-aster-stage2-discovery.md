# Aster Stage 2 任务计划

- 日期：2026-11-04
- Spec：`docs/superpowers/specs/2026-11-04-aster-stage2-discovery-design.md`
- 状态：**待用户批准执行**

## 任务清单

- [x] 2-0 迁移 0002 六表 + 候选生命周期状态机（产物门槛硬校验有测试：空手推进被拒）
- [x] 2-1 发现通道：gh CLI 连通验证 ✅（实测返回 topics/stars）；搜索封装（URL 编码 bug 已修复并实测）；任务缺口触发（gap_query 规则+LLM 兜底，代码完成）
- [x] 2-2 评估流水线：**两次实测通过**——sqlite-vec（36s，verified 6/inferred 3）、yuque-mcp-server（12s，同类比较 2 仓：官方生态仓+社区替代仓，推荐 test/置信 0.82，理由含"无实测数据不宜直接集成"的保守倾向）
- [x] 2-3 候选看板与报告 UI：「发现与评估」页已构建上线（结论先行/十二维折叠/草稿审阅/全流程动作按钮）
- [ ] 2-4 隔离测试：代码完成（L2 网关联动 + candidate_tests 落库），**未实测**（依赖先走一次草稿流程）
- [ ] 2-5 能力版本管理：API 完成（versions/rollback），接入时建版本记录，**未实测**
- [ ] 2-6 元能力 adapter_scaffold：代码完成（claude 调用 + 宿主规则隔离提示词 + 末个 JSON 解析），**未实测**（会产生 claude 调用，留给用户 UI 走查时触发）
- [ ] 2-7 watchlist 巡检：循环已随服务启动（daemon 线程 + git ls-remote 探测），check_once **未实测**
- [ ] 2-8 经验沉淀：lessons 记录（评估失败/归档原因自动入库）与查询 API 完成，**未实测**
- [ ] 2-9 验收：待 2-4~2-6 经一次真实草稿流程实测后收口（spec §5 五项中三项已有测试与实测背书，接入链路待走通）

## 执行约束

- 每片完成：勾选 + 任务日志；测试全绿才勾
- claude headless 调用前确认费用额度（GLM 计划内）；运行外部代码前 L2 审批不得豁免
- 与 Stage 3/4 相关的想法一律记录不实施
