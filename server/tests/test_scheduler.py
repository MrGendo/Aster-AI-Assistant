# 定时调度测试：时间语义解析 / 触发描述 / 路由命中
from datetime import datetime, timedelta

import aster.orchestrator.core as core
from aster.orchestrator.core import classify
from aster.scheduler import describe_trigger, looks_periodic, parse_when


def setup_module():
    core.provider.available = lambda: False


def test_parse_daily_cron():
    assert parse_when("每天早上9点给我日报") == {"type": "cron", "hour": 9, "minute": 0}
    assert parse_when("每天 9:30 推送") == {"type": "cron", "hour": 9, "minute": 30}
    assert parse_when("每天晚上8点半提醒") == {"type": "cron", "hour": 20, "minute": 30}
    assert parse_when("每天下午3点") == {"type": "cron", "hour": 15, "minute": 0}


def test_parse_interval():
    assert parse_when("每30分钟跑一次") == {"type": "interval", "minutes": 30}
    assert parse_when("每 2 小时分析") == {"type": "interval", "hours": 2}


def test_parse_relative_and_abs():
    now = datetime.now()
    tj = parse_when("15分钟后提醒我休息")
    assert tj["type"] == "date" and abs(tj["run_at"] - (now + timedelta(minutes=15)).timestamp()) < 5
    tj = parse_when("明天 9 点开会")
    assert tj["type"] == "date"
    tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    assert abs(tj["run_at"] - tomorrow.timestamp()) < 5
    # 绝对时间已过 → 拒绝（不静默改期）
    assert parse_when("2020-01-01 09:00") is None


def test_parse_past_clock_rolls_tomorrow():
    """今天的时刻已过 → 顺延明天（打过的闹钟不该哑火也不该立刻响）"""
    past_hour = (datetime.now() - timedelta(hours=1)).hour
    tj = parse_when(f"{past_hour} 点提醒")
    assert tj["type"] == "date" and tj["run_at"] > datetime.now().timestamp()


def test_describe_and_hint():
    assert describe_trigger({"type": "cron", "hour": 9, "minute": 30}) == "每天 09:30"
    assert describe_trigger({"type": "interval", "minutes": 30}) == "每隔 30 分钟"
    assert looks_periodic("每天记得跑") and not looks_periodic("明天再说")


def test_classify_schedule_and_alarm():
    assert classify("提醒我明天 9 点开会") == "alarm"
    assert classify("提醒我 15 分钟后休息") == "alarm"
    assert classify("每天早上9点给我热点日报") == "schedule"
    assert classify("每30分钟跑一次热点分析") == "schedule"
    # 无时间的提醒仍是待办
    assert classify("提醒我明天吃早餐") == "todo_add"
    assert classify("深度调研 MCP") == "deep_research"
