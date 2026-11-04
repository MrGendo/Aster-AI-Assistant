# 任务状态机测试：合法路径全覆盖 + 取消语义 + 非法迁移拒绝
import pytest

from aster import tsm


def test_happy_path_capability_task():
    """能力型任务主干：pending → planning → executing → verifying → completed"""
    cur = tsm.PENDING
    for nxt in (tsm.PLANNING, tsm.EXECUTING, tsm.VERIFYING, tsm.COMPLETED):
        cur = tsm.transition(cur, nxt)
    assert cur == tsm.COMPLETED


def test_awaiting_user_roundtrip():
    """等待确认后可继续执行"""
    cur = tsm.transition(tsm.EXECUTING, tsm.AWAITING_USER)
    assert tsm.transition(cur, tsm.EXECUTING) == tsm.EXECUTING


def test_cancel_from_any_active_state():
    for state in tsm.ACTIVE:
        assert tsm.can_transition(state, tsm.CANCELLED), state


def test_terminal_no_exit():
    for state in tsm.TERMINAL:
        assert tsm.TRANSITIONS[state] == set()


def test_illegal_transition_raises():
    with pytest.raises(tsm.IllegalTransition):
        tsm.transition(tsm.COMPLETED, tsm.EXECUTING)
    with pytest.raises(tsm.IllegalTransition):
        tsm.transition(tsm.PENDING, tsm.VERIFYING)  # 不能跳过规划直接验证


def test_cancel_semantics_pending_means_not_stopped():
    """取消语义核心：仍在运行时只置标志，不落 cancelled"""
    assert tsm.cancel_target(tsm.EXECUTING, has_unfinished_work=True) is None
    assert tsm.cancel_target(tsm.EXECUTING, has_unfinished_work=False) == tsm.CANCELLED
    assert tsm.cancel_target(tsm.COMPLETED, has_unfinished_work=False) is None
