# 路由与载荷抽取测试：deep_research 规则命中/让路/主题清洗
import subprocess
import sys
from pathlib import Path

import aster.orchestrator.core as core
from aster.orchestrator.core import _build_payload, classify

WORKER = Path(__file__).resolve().parents[2] / "capabilities" / "deep_research" / "run.py"


def setup_module():
    # 单测不发真实模型请求：规则未命中时的 LLM 兜底分类直接置为不可用
    core.provider.available = lambda: False


def test_deep_research_rule_hits():
    assert classify("深度调研：Rust 生态 2026 趋势") == "deep_research"
    assert classify("帮我深入调研 MCP 协议的现状") == "deep_research"
    assert classify("全面调研开源 agent 框架格局") == "deep_research"


def test_deep_research_yields_to_composite_and_github():
    # 复合连接词让路（交给规划器/其他规则）；仓库链接归 repo_analysis
    assert classify("深度调研 https://github.com/a/b 顺便总结") != "deep_research"
    assert classify("综合深度调研 A 和 B 的差异") != "deep_research"
    assert classify("先分析仓库再深度调研") != "deep_research"


def test_deep_research_payload_topic_extracted():
    p = _build_payload("deep_research", "帮我深度调研：Rust 生态 2026 趋势")
    assert p == {"topic": "Rust 生态 2026 趋势"}


def test_worker_rejects_bad_input():
    # 空主题/短主题在工人入口被拒（exit 4），不会触发任何 API 调用
    r = subprocess.run([sys.executable, str(WORKER)], input='{"topic": "ab"}',
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 4
