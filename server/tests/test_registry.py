# 数据层与注册表测试：迁移幂等、内置能力登记、candidate 隔离
from aster.db import connect, current_version, migrate
from aster.runtime import registry


def setup_function(_):
    global conn
    conn = connect()
    migrate(conn)


def teardown_function(_):
    conn.close()


def test_migration_sets_version_and_idempotent():
    v = current_version(conn)
    assert v >= 1  # 随迁移增加而增长，不写死具体版本号
    migrate(conn)  # 重跑不报错、不重复
    assert current_version(conn) == v


def test_core_tables_exist():
    for t in ("tasks", "task_steps", "capabilities", "runs", "tool_calls",
              "approvals", "memories", "projects", "settings"):
        assert conn.execute(f"SELECT name FROM sqlite_master WHERE name='{t}'").fetchone()


def test_builtin_caps_registered_active():
    registry.ensure_builtin(conn)
    repo = registry.get_active(conn, "repo_analysis")
    assert repo is not None and repo["risk_level"] == "L0"
    assert registry.get_active(conn, "text_stats") is not None


def test_candidate_not_callable():
    """candidate 状态的能力必须不可调用（与正式能力严格隔离）"""
    registry.ensure_builtin(conn)
    with conn:
        conn.execute("UPDATE capabilities SET status='candidate' WHERE name='repo_analysis'")
    assert registry.get_active(conn, "repo_analysis") is None
