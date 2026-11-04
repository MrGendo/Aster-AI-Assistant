# 网关测试：L0 自动 / L1 审批流 / 拒绝 / 暂停 / 审批复用
import time

import pytest

from aster.db import connect, migrate
from aster.gateway import core as gw


@pytest.fixture()
def conn():
    c = connect()
    migrate(c)
    # 每个用例前清空数据，保证相互隔离（否则暂停标志/审批单会跨用例泄漏）
    for t in ("approvals", "tool_calls", "runs", "task_steps", "tasks", "settings"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    yield c
    c.close()


def test_l0_auto_allowed_with_audit(conn):
    assert gw.check(conn, "repo_analysis", "L0", task_id=1) == "allowed"
    row = conn.execute(
        "SELECT * FROM tool_calls WHERE capability='repo_analysis'"
    ).fetchone()
    assert row["status"] == "approved_auto"


def test_l1_requires_approval_then_allowed(conn):
    assert gw.check(conn, "file_write", "L1", task_id=2) == "pending"
    assert gw.check(conn, "file_write", "L1", task_id=2) == "pending"  # 复用同一审批单
    aid = conn.execute("SELECT id FROM approvals ORDER BY id DESC LIMIT 1").fetchone()["id"]
    assert gw.decide(conn, aid, "approved")
    assert gw.check(conn, "file_write", "L1", task_id=2) == "allowed"


def test_l2_rejected_stays_denied(conn):
    assert gw.check(conn, "yuque_write", "L2", task_id=3) == "pending"
    aid = conn.execute("SELECT id FROM approvals ORDER BY id DESC LIMIT 1").fetchone()["id"]
    gw.decide(conn, aid, "rejected")
    assert gw.check(conn, "yuque_write", "L2", task_id=3) == "denied"


def test_pause_blocks_l1_but_not_l0(conn):
    gw.set_setting(conn, "system.paused", "1")
    assert gw.check(conn, "anything", "L1", task_id=4) == "paused"
    assert gw.check(conn, "read_only", "L0", task_id=4) == "allowed"


def test_approval_timeout_expires_as_rejected(conn):
    gw.check(conn, "big_op", "L2", task_id=5)
    with conn:
        conn.execute("UPDATE approvals SET requested_at=?", (int(time.time()) - gw.APPROVAL_TIMEOUT_S - 1,))
    assert gw.expire_stale(conn) == 1
    assert gw.check(conn, "big_op", "L2", task_id=5) == "denied"
