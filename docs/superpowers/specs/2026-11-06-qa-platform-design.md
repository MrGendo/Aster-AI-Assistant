# 设计：质量平台化三件套（对齐抖音研发中台 JD）

日期：2026-11-06 ｜ 状态：执行中（用户已批准方向）

背景：岗位三点=全流程质量保障/AI 质量建设/方法论沉淀。三笔真功能让项目与 JD 1:1 贴合，
全部复用既有机制（run_replay 引擎、llm_verify、integration_lessons），不造假。

## A. 能力回归评测与准出门禁（贴 JD ② ①）

- migration 0009：`eval_cases`（id, capability, name, goal, expect, enabled, last_result_json, finished_at）；
  replay_set 三条种子迁入为 capability='*' 全局用例（原表保留给 evolution 兼容）；tasks 增 quality_json
- 评测执行 `evaluation/regression.py`：逐用例走完整任务通道 + replay_grade 质量评分（同 run_replay 模式），
  结果回写 last_result_json；`regression_ok(conn, capability)`：该能力（含全局）用例最近一次全过
  且时间晚于能力最后变更 → 准出凭据
- 门禁接线：
  - 能力 enable：有启用用例但凭据过期/失败 → 409「先跑回归门禁」（规则化准出）
  - proposal apply：应用后后台自动跑全局回归，通过率低于 `evo.regression_floor`（默认 0.6）
    → 自动回滚 + 通知（风险闭环）
- API：GET/POST/DELETE /api/eval/cases、POST /api/eval/run、GET /api/lessons

## B. 计划质量评审 + 任务质量报告（贴 JD ①）

- 计划评审：make_plan 通过硬校验后，LLM 按检查单评审（产出步缺 verify / L1 步无 fallback /
  验收标准模糊），发现问题带问题清单重生成一次（仍走硬校验）；token 入账
- 质量报告：任务终态聚合 quality_json——验证链（结构校验/步骤评审/交付终检 各自结论）、
  证据来源数、未验证事项；ResultView 渲染「质量报告」卡

## C. 教训 → 回归用例沉淀（贴 JD ③）

- POST /api/lessons/{id}/to_case：把 integration_lessons 记录转为 eval_case
  （LLM 辅助生成 goal+expect，失败则要求手工参数）；发现与评估 tab 加教训列表与按钮
- 闭环：踩坑 → 教训入库 → 一键变回归用例 → 变更时门禁自动守住（经验驱动→规则化）

## 验证标准

1. 单测：regression_ok 三态（无凭据/过期/通过）、门禁 409、质量报告聚合、计划评审修补一次上限
2. E2E：给 text_stats 建 2 条用例跑通评测（低成本）；门禁拦截过期 enable；教训转用例全链路；
   一次多步任务产出质量报告卡
3. 既有 53 测试零回归
