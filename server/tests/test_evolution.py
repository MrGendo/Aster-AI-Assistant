# 进化服务不变量测试：白名单越界拒收 / 高风险不自动 / 验收标准只读 / 回滚恢复
import json

import pytest

from aster.db import connect, migrate
from aster.evolution import service as evo
from aster.runtime import registry


@pytest.fixture()
def conn():
    c = connect()
    migrate(c)
    registry.ensure_builtin(c)
    for t in ("improvement_proposals", "replay_set"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM settings WHERE key LIKE 'evo.%'")
    c.commit()
    yield c
    c.close()


def _proposal(conn, risk="low", change=None, status="approved"):
    with conn:
        cur = conn.execute(
            "INSERT INTO improvement_proposals(evidence_json, hypothesis, change_json, scope, "
            "risk, acceptance_frozen, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
            ('{"kind":"low_success"}', "测试假设", json.dumps(change or {"cap_order": ["b", "a"]}),
             "能力排序", risk, "成功率回升到 80%", status, 1),
        )
    return cur.lastrowid


def test_apply_rejects_out_of_whitelist_change(conn):
    pid = _proposal(conn, change={"audit_enabled": False})  # 越权：动审计
    r = evo.apply(conn, pid)
    assert not r["ok"] and "白名单" in r["error"]


def test_high_risk_never_auto_applies(conn):
    pid = _proposal(conn, risk="high")
    r = evo.apply(conn, pid, actor="system")
    assert not r["ok"] and "人工" in r["error"]
    assert evo.apply(conn, pid, actor="human")["ok"]  # 人工放行


def test_apply_then_rollback_restores_snapshot(conn):
    pid = _proposal(conn)
    assert evo.apply(conn, pid)["ok"]
    row = conn.execute("SELECT value FROM settings WHERE key='evo.cap_order'").fetchone()
    assert row is not None
    r = evo.rollback(conn, pid)
    assert r["ok"] and "evo.cap_order" in r["restored"]
    assert conn.execute("SELECT value FROM settings WHERE key='evo.cap_order'").fetchone() is None


def test_acceptance_frozen_actor_guard(conn):
    # 系统侧不可改验收标准；唯一出口是 human（防放宽标准制造假进步，PRD §9.5）
    assert evo.assert_frozen_writable("system") is False
    assert evo.assert_frozen_writable("human") is True


def test_detect_skips_small_samples(conn):
    # 样本不足（<5 次调用）即使全失败也不出提案——小数据噪声不升级为瓶颈
    with conn:
        conn.execute("DELETE FROM capability_stats WHERE capability_id=( "
                     "SELECT id FROM capabilities WHERE name='text_stats')")
        conn.execute(
            "INSERT INTO capability_stats(capability_id, calls, ok, avg_ms, updated_at) "
            "SELECT id, 3, 0, 10, 1 FROM capabilities WHERE name='text_stats'")
    assert evo.detect(conn) == []
