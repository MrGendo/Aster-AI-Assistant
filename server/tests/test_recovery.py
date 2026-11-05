# 验证与恢复机制测试：终检触发表 / on_fail 契约 / 重规划校验
import aster.orchestrator.core as core
from aster.orchestrator.core import DELIVERY_VERIFY, classify
import aster.orchestrator.planner as planner_mod
from aster.orchestrator.planner import validate_plan


_orig_get_active = planner_mod.registry.get_active


def setup_module():
    core.provider.available = lambda: False
    # validate_plan 经 registry.get_active 查真实库——桩掉：bad_* 视为不存在
    planner_mod.registry.get_active = lambda conn, name: (
        None if "bad" in name else type("Cap", (), {})())


def teardown_module():
    # registry 是共享模块，必须还原（否则污染 test_registry 等用例）
    planner_mod.registry.get_active = _orig_get_active


def test_delivery_verify_covers_content_intents_only():
    """只验对外产出内容的三类；确定性 L0 不验（零成本原则）"""
    assert set(DELIVERY_VERIFY) == {"report", "deep_research", "hot_news"}
    for crit in DELIVERY_VERIFY.values():
        assert "编造" in crit or "来源" in crit or "一致" in crit


def test_validate_plan_on_fail_normalize():
    ok_step = {"capability": "web_fetch", "desc": "抓取", "input": {"urls": ["https://a"]},
               "output_key": "pages", "on_fail": "skip"}
    assert validate_plan({"steps": [ok_step]}, None)[0]["on_fail"] == "skip"
    # LLM 自创变体（如 skip_to_next）宽容归一，不为一个字段报废整份计划
    ok_step["on_fail"] = "skip_to_next"
    assert validate_plan({"steps": [ok_step]}, None)[0]["on_fail"] == "skip"
    ok_step["on_fail"] = "explode"
    assert validate_plan({"steps": [ok_step]}, None)[0]["on_fail"] == "abort"


def test_validate_plan_known_keys_for_replan():
    """重规划步骤可引用已完成步骤的输出键（known_keys 注入）"""
    new_step = {"seq": 5, "capability": "llm_task", "desc": "综合", "on_fail": "abort",
                "input": {"text": "$1.report"}, "output_key": "out"}
    known = {1: {"report"}}
    assert validate_plan({"steps": [new_step]}, None, known_keys=known)
    # 引用未声明过的键仍拒绝
    bad_ref = dict(new_step, input={"text": "$1.nonexistent"})
    assert not validate_plan({"steps": [bad_ref]}, None, known_keys=known)


def test_validate_plan_unknown_capability_rejected():
    st = {"capability": "bad_cap", "desc": "x", "input": {}, "output_key": "o"}
    assert not validate_plan({"steps": [st]}, None)


def test_verify_step_requires_criteria():
    """llm_verify 评审步无标准即拒绝（评审无标准等于走过场）；
    有标准且引用合法前驱输出才通过"""
    upstream = {"seq": 1, "capability": "web_fetch", "desc": "抓取",
                "input": {"urls": ["https://a"]}, "output_key": "out"}
    st = {"seq": 2, "capability": "llm_verify", "desc": "评审",
          "input": {"text": "$1.out"}, "output_key": "verdict"}
    assert not validate_plan({"steps": [upstream, st]}, None)
    st["verify"] = "结论有据可查"
    assert validate_plan({"steps": [upstream, st]}, None)


def test_classify_unchanged_by_recovery():
    assert classify("规划执行：A 然后 B") == "planned"
    assert classify("深度调研 MCP") == "deep_research"
