# 规划器硬校验测试：非法计划必须全部被拒（规划器再不稳也砸不垮执行）
import pytest

from aster.db import connect, migrate
from aster.orchestrator import planner
from aster.runtime import registry


@pytest.fixture()
def conn():
    c = connect()
    migrate(c)
    registry.ensure_builtin(c)
    yield c
    c.close()


def _step(seq=1, cap="repo_analysis", ref=None, out="report", verify=None):
    return {"seq": seq, "capability": cap, "desc": "t",
            "input": {"x": ref} if ref else {"x": "literal"},
            "output_key": out, **({"verify": verify} if verify else {})}


def test_valid_two_step_plan(conn):
    steps = planner.validate_plan({"steps": [
        _step(1, out="report"),
        _step(2, "llm_task", ref="$1.report", out="summary"),
    ]}, conn)
    assert len(steps) == 2


def test_rejects_unknown_or_inactive_capability(conn):
    assert planner.validate_plan({"steps": [_step(1, cap="no_such_cap")]}, conn) == []
    with conn:
        conn.execute("UPDATE capabilities SET status='disabled' WHERE name='repo_analysis'")
    assert planner.validate_plan({"steps": [_step(1)]}, conn) == []
    with conn:
        conn.execute("UPDATE capabilities SET status='active' WHERE name='repo_analysis'")


def test_rejects_bad_reference_key_and_forward_reference(conn):
    # 引用键不是被引用步声明的 output_key → 拒
    assert planner.validate_plan({"steps": [
        _step(1, out="report"), _step(2, "llm_task", ref="$1.wrong_key", out="s"),
    ]}, conn) == []
    # 前向引用（引用尚未出现的步骤）→ 拒
    assert planner.validate_plan({"steps": [
        _step(1, "llm_task", ref="$2.report", out="s"), _step(2, out="report"),
    ]}, conn) == []


def test_rejects_over_limit_and_verify_without_criteria(conn):
    many = [_step(i, "text_stats", out=f"o{i}") for i in range(1, 8)]
    assert planner.validate_plan({"steps": many}, conn) == []
    assert planner.validate_plan({"steps": [_step(1, "llm_verify", out="v")]}, conn) == []
    assert planner.validate_plan({"steps": [
        _step(1, out="report"),
        _step(2, "llm_verify", ref="$1.report", out="v", verify="摘要不超过80字"),
    ]}, conn) != []
