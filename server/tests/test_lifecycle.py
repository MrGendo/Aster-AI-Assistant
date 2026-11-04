# 候选生命周期测试：迁移合法性 + 产物门槛 + 未批准候选不可转正
import pytest

from aster.db import connect, migrate
from aster.evaluation import lifecycle, pipeline


@pytest.fixture()
def conn():
    c = connect()
    migrate(c)
    for t in ("integration_lessons", "watchlist", "capability_versions", "candidate_tests",
              "evaluation_reports", "candidates"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    yield c
    c.close()


def test_lifecycle_happy_path_needs_artifacts(conn):
    cid = pipeline.register_candidate(conn, "https://github.com/a/b")
    assert conn.execute("SELECT lifecycle FROM candidates WHERE id=?", (cid,)).fetchone()[0] == "discovered"

    # screened/deep_analysis 无需独立产物
    pipeline.advance(conn, cid, lifecycle.SCREENED)
    pipeline.advance(conn, cid, lifecycle.DEEP_ANALYSIS)

    # compared 必须先有 evaluation_reports——空手推进必须被拒
    with pytest.raises(lifecycle.IllegalLifecycle):
        pipeline.advance(conn, cid, lifecycle.COMPARED)
    with conn:
        conn.execute("INSERT INTO evaluation_reports(candidate_id, dimensions_json, "
                     "evidence_json, recommendation, reason, created_at) VALUES(?,?,?,?,?,?)",
                     (cid, "{}", "{}", "test", "t", 1))
    pipeline.advance(conn, cid, lifecycle.COMPARED)

    # isolated_test 必须先有 candidate_tests
    with pytest.raises(lifecycle.IllegalLifecycle):
        pipeline.advance(conn, cid, lifecycle.ISOLATED_TEST)
    with conn:
        conn.execute("INSERT INTO candidate_tests(candidate_id, kind, result_json, exit_code, "
                     "created_at) VALUES(?,?,?,?,?)", (cid, "adapter_run", "{}", 0, 1))
    pipeline.advance(conn, cid, lifecycle.ISOLATED_TEST)
    pipeline.advance(conn, cid, lifecycle.INTEGRATION_EVAL)
    pipeline.advance(conn, cid, lifecycle.PENDING_APPROVAL)
    pipeline.advance(conn, cid, lifecycle.INTEGRATION_VERIFIED)
    pipeline.advance(conn, cid, lifecycle.ACTIVE)


def test_illegal_lifecycle_jump(conn):
    cid = pipeline.register_candidate(conn, "https://github.com/a/c")
    with pytest.raises(lifecycle.IllegalLifecycle):
        lifecycle.transition("discovered", "active")  # 不允许发现即转正
    assert not lifecycle.can_integrate("pending_approval")
    assert lifecycle.can_integrate("active")


def test_register_candidate_idempotent(conn):
    a = pipeline.register_candidate(conn, "https://github.com/a/d")
    b = pipeline.register_candidate(conn, "https://github.com/a/d/")
    assert a == b
