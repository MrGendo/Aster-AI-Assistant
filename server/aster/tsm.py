# 任务状态机（PRD §4.4 九态）
# 关键语义：cancel_requested 是独立标志，cancelled 只能在执行真正停止后进入——
# 「已收到取消请求」和「执行已经停止」必须区分，不能提前显示已取消
from typing import Optional

# 九态
PENDING = "pending"            # 待处理
PLANNING = "planning"          # 规划中
EXECUTING = "executing"        # 执行中
AWAITING_USER = "awaiting_user"  # 等待用户确认
VERIFYING = "verifying"        # 验证中
COMPLETED = "completed"        # 已完成
PARTIAL = "partial"            # 部分完成
FAILED = "failed"              # 失败
CANCELLED = "cancelled"        # 已取消（实际停止后才允许）

TERMINAL = {COMPLETED, PARTIAL, FAILED, CANCELLED}

# 合法迁移表
TRANSITIONS: dict[str, set[str]] = {
    PENDING: {PLANNING, CANCELLED, FAILED},  # FAILED：输入校验阶段即失败的情况
    PLANNING: {EXECUTING, AWAITING_USER, FAILED, CANCELLED},
    EXECUTING: {VERIFYING, AWAITING_USER, PARTIAL, FAILED, CANCELLED},
    AWAITING_USER: {EXECUTING, CANCELLED},
    VERIFYING: {COMPLETED, PARTIAL, FAILED, CANCELLED},  # 验证中也允许取消（验证仍属运行中）
    PARTIAL: set(),
    COMPLETED: set(),
    FAILED: set(),
    CANCELLED: set(),
}

# 「正在运行」的状态集合：处于这些状态时取消需要先请求、待执行环节停下
ACTIVE = {PENDING, PLANNING, EXECUTING, VERIFYING}


class IllegalTransition(Exception):
    pass


def can_transition(cur: str, nxt: str) -> bool:
    return cur in TRANSITIONS and nxt in TRANSITIONS[cur]


def transition(cur: str, nxt: str) -> str:
    """校验并返回新状态；非法迁移直接抛错（调用方 bug 应尽早暴露）"""
    if not can_transition(cur, nxt):
        raise IllegalTransition(f"非法状态迁移: {cur} -> {nxt}")
    return nxt


def cancel_target(cur: str, has_unfinished_work: bool) -> Optional[str]:
    """取消语义：运行中只置 cancel_requested 标志，由执行环节确认停止后才落 cancelled"""
    if cur in TERMINAL:
        return None  # 已终态，无需处理
    if has_unfinished_work:
        return None  # 仍在跑：只置标志，不改状态
    return CANCELLED
