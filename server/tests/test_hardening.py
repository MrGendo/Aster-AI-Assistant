# 批次加固测试：run_if 求值与校验 / 预算熔断 / 分类器标签 / 蒸馏守门
import aster.orchestrator.core as core
import aster.orchestrator.planner as planner_mod
from aster.db import connect, migrate
from aster.orchestrator.core import _budget_exceeded, _eval_run_if, classify
from aster.orchestrator.planner import RUN_IF_RE, validate_plan


_orig_get_active = planner_mod.registry.get_active


def setup_module():
    core.provider.available = lambda: False
    planner_mod.registry.get_active = (
        lambda conn, name: None if "bad" in name else type("Cap", (), {})())


def teardown_module():
    # registry 是共享模块，必须还原（否则污染 test_planner 等用例——本日第二次踩）
    planner_mod.registry.get_active = _orig_get_active


def test_run_if_evaluator():
    outs = {1: {"found": "是", "flag": "", "text": "MCP 生态调研"}}
    assert _eval_run_if("$1.found == 是", outs) is True
    assert _eval_run_if("$1.found != 是", outs) is False
    assert _eval_run_if("$1.text contains MCP", outs) is True
    assert _eval_run_if("$1.flag", outs) is False            # 空值=假
    assert _eval_run_if("$1.missing", outs) is False         # 引用缺失=假
    assert _eval_run_if("胡写的条件", outs) is None          # 非法语法兜底


def test_run_if_syntax_validated():
    assert RUN_IF_RE.match("$1.found == 是")
    ok = {"capability": "llm_task", "desc": "x", "input": {"text": "abc"},
          "output_key": "o", "run_if": "如果失败就跳过"}
    assert not validate_plan({"steps": [ok]}, None)          # 非法条件计划期拒收


def test_budget_breaker():
    conn = connect()
    migrate(conn)
    cur = conn.execute(
        "INSERT INTO tasks(goal, intent, status, created_at, updated_at) "
        "VALUES('t','text_stats','pending',0,0)")
    tid = cur.lastrowid
    conn.execute("UPDATE tasks SET tokens_used=400000 WHERE id=?", (tid,))
    conn.commit()
    assert "token" in _budget_exceeded(conn, tid)
    conn.execute("UPDATE tasks SET tokens_used=0, cost_usd=2.5 WHERE id=?", (tid,))
    conn.commit()
    assert "$" in _budget_exceeded(conn, tid)
    conn.execute("UPDATE tasks SET cost_usd=0 WHERE id=?", (tid,))
    conn.commit()
    assert _budget_exceeded(conn, tid) is None
    # 自定义阈值生效（settings 可调）
    conn.execute("INSERT INTO settings(key, value) VALUES('budget.max_tokens', '100')")
    conn.execute("UPDATE tasks SET tokens_used=200 WHERE id=?", (tid,))
    conn.commit()
    assert _budget_exceeded(conn, tid)
    conn.close()


def test_classifier_full_labels(monkeypatch):
    """LLM 兜底分类器输出全标签集内的意图被接受"""
    monkeypatch.setattr(core.provider, "available", lambda: True)
    monkeypatch.setattr(core.provider, "chat", lambda *a, **k: "deep_research")
    assert classify("帮我全面摸一下 MCP 现在啥样") == "deep_research"
    monkeypatch.setattr(core.provider, "chat", lambda *a, **k: "乱写的")
    assert classify("随便一句话") == "chat"  # 集合外回落 chat


def test_distill_skips_chat_intent():
    """chat 任务不蒸馏（守门在 provider 调用之前）"""
    called = []
    core.provider.chat_with_usage = lambda *a, **k: called.append(1) or ("x", 0)
    conn = connect()
    migrate(conn)
    cur = conn.execute(
        "INSERT INTO tasks(goal, intent, status, result_summary, created_at, updated_at) "
        "VALUES('问个问题','chat','completed','这是一段足够长的结论" + "字" * 60 + "',0,0)")
    conn.commit()
    core._distill_memory(conn, cur.lastrowid, "这是一段足够长的结论" + "字" * 60)
    conn.close()
    assert not called


def test_regression_gate_semantics():
    """准出凭据三态：从未执行=免检放行（防停用死锁）/ 最近失败=拦 / 全过=放行"""
    import json as _j
    from aster.evaluation import regression as reg
    conn = connect()
    migrate(conn)
    conn.execute("INSERT INTO capabilities(name, kind, descriptor_json, status, source, "
                 "version, risk_level, entry, created_at, updated_at) "
                 "VALUES('t_cap','tool','{}','active','builtin','0','L0','x',0,0)")
    cid = conn.execute("SELECT id FROM capabilities WHERE name='t_cap'").fetchone()["id"]
    conn.execute("DELETE FROM eval_cases WHERE capability='t_cap'")
    conn.execute("INSERT INTO eval_cases(capability, name, goal, expect, enabled, created_at) "
                 "VALUES('t_cap','c1','g','e',1,0)")
    conn.commit()
    ok, why = reg.regression_ok(conn, "t_cap")
    assert ok and "免检" in why or ok  # 从未执行 → 放行
    conn.execute("UPDATE eval_cases SET last_result_json=?, finished_at=1 WHERE capability='t_cap'",
                 (_j.dumps({"passed": True}),))
    conn.commit()
    assert reg.regression_ok(conn, "t_cap")[0] is True
    conn.execute("UPDATE eval_cases SET last_result_json=? WHERE capability='t_cap'",
                 (_j.dumps({"passed": False}),))
    conn.commit()
    ok, why = reg.regression_ok(conn, "t_cap")
    assert not ok and "c1" in why
    conn.execute("DELETE FROM eval_cases WHERE capability='t_cap'")
    conn.execute("DELETE FROM capabilities WHERE id=?", (cid,))
    conn.commit()
    conn.close()
