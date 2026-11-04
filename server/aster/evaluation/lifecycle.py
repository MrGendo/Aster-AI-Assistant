# 候选项目生命周期状态机（PRD §5.8）
# 硬原则：每级推进都要求有对应产物（由 pipeline 层校验，这里只管迁移合法性）
DISCOVERED = "discovered"          # 刚发现，未筛选
SCREENED = "screened"              # 通过初步筛选（附理由）
DEEP_ANALYSIS = "deep_analysis"    # 深度分析完成
COMPARED = "compared"              # 同类比较完成（附报告）
ISOLATED_TEST = "isolated_test"    # 隔离测试完成（附测试记录）
INTEGRATION_EVAL = "integration_eval"  # 接入评估完成
PENDING_APPROVAL = "pending_approval"  # 等待用户批准接入
INTEGRATION_VERIFIED = "integration_verified"  # 接入后验证通过
ACTIVE = "active"                  # 正式启用
ARCHIVED = "archived"              # 归档（拒绝/放弃/被替代）

TRANSITIONS = {
    DISCOVERED: {SCREENED, ARCHIVED},
    SCREENED: {DEEP_ANALYSIS, ARCHIVED},
    DEEP_ANALYSIS: {COMPARED, ARCHIVED},
    COMPARED: {ISOLATED_TEST, ARCHIVED},
    ISOLATED_TEST: {INTEGRATION_EVAL, ARCHIVED},
    INTEGRATION_EVAL: {PENDING_APPROVAL, ARCHIVED},
    PENDING_APPROVAL: {INTEGRATION_VERIFIED, ARCHIVED},
    INTEGRATION_VERIFIED: {ACTIVE, ARCHIVED},
    ACTIVE: {ARCHIVED},
    ARCHIVED: set(),
}

# 推进到该状态时必须已存在的产物表（pipeline 校验用）
REQUIRED_ARTIFACT = {
    SCREENED: None,                    # 筛选理由直接写 candidates.metadata/更新说明，无独立表
    DEEP_ANALYSIS: None,               # 深度分析复用 tasks（repo_analysis 任务产物）
    COMPARED: "evaluation_reports",
    ISOLATED_TEST: "candidate_tests",
    INTEGRATION_VERIFIED: "candidate_tests",
}


class IllegalLifecycle(Exception):
    pass


def transition(cur: str, nxt: str) -> str:
    if nxt not in TRANSITIONS.get(cur, set()):
        raise IllegalLifecycle(f"非法生命周期迁移: {cur} -> {nxt}")
    return nxt


def can_integrate(lifecycle: str) -> bool:
    """只有走完全流程的候选才允许转正——candidate 调用隔离的兜底判断"""
    return lifecycle == INTEGRATION_VERIFIED or lifecycle == ACTIVE
