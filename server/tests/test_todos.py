# 待办功能测试：语法解析 / 排序 / 对话路由
from datetime import date, timedelta

import aster.orchestrator.core as core
from aster.orchestrator.core import classify
from aster.todos import parse_content, sort_key


def setup_module():
    core.provider.available = lambda: False  # 单测不发真实模型请求


def test_parse_priority_and_due():
    p = parse_content("(A) 给妈妈打电话 @2026-11-08")
    assert p == {"content": "给妈妈打电话", "priority": "A", "due": "2026-11-08"}
    p = parse_content("!B 交报告 截止2026-12-01")
    assert p["priority"] == "B" and p["due"] == "2026-12-01" and p["content"] == "交报告"


def test_parse_relative_words():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    p = parse_content("买牛奶 明天")
    assert p["due"] == tomorrow and p["content"] == "买牛奶"


def test_parse_plain_text_untouched():
    p = parse_content("看板里 2026 年的规划文档")
    assert p["priority"] is None and p["due"] is None
    assert p["content"] == "看板里 2026 年的规划文档"


def test_sort_overdue_first_then_priority():
    today = "2026-11-05"
    overdue = {"due": "2026-11-01", "priority": None, "created_at": 3}
    today_a = {"due": "2026-11-05", "priority": "A", "created_at": 2}
    today_none = {"due": "2026-11-05", "priority": None, "created_at": 1}
    future = {"due": "2026-11-09", "priority": "A", "created_at": 0}
    none = {"due": None, "priority": "A", "created_at": 0}
    order = sorted([none, future, today_none, today_a, overdue], key=lambda t: sort_key(t, today))
    assert [id(x) for x in order] == [id(overdue), id(today_a), id(today_none), id(future), id(none)]


def test_classify_todo_rules():
    assert classify("待办：给妈妈打电话") == "todo_add"
    assert classify("todo: buy milk") == "todo_add"
    assert classify("添加待办 交报告") == "todo_add"
    assert classify("看看待办") == "todo_list"
    assert classify("我的待办") == "todo_list"
    # 待办里的调研内容不该被调研路由劫持
    assert classify("待办：深度调研 MCP 生态") == "todo_add"
    # 普通调研不受影响
    assert classify("深度调研 MCP 生态") == "deep_research"
