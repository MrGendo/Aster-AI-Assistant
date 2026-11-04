# LLM Provider 层：默认 GLM（Anthropic 兼容端点），Anthropic 官方为备选
# 关键约束：不假定任何官方后端——base_url/model 全部来自配置（Phase 0 PoC-5 实测事实）
import json
import os
from pathlib import Path

from ..db import connect
from ..gateway.core import _get_setting, set_setting
from ..security.keyring_store import get_secret, set_secret

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"


def bootstrap_from_claude_cli(conn) -> None:
    """首次启动时复用 claude CLI 的 GLM 配置（用户已批准复用现有 Key，见 spec Q3）。
    只在 keychain 尚无密钥时执行一次，且绝不打印密钥内容。"""
    if get_secret("provider.api_key"):
        return
    if not CLAUDE_SETTINGS.exists():
        return
    try:
        env = json.loads(CLAUDE_SETTINGS.read_text()).get("env", {})
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        token = env.get("ANTHROPIC_AUTH_TOKEN", "")
        if base_url and token:
            set_secret("provider.api_key", token)
            set_setting(conn, "provider.base_url", base_url)
            set_setting(conn, "provider.default", "glm")
            set_setting(conn, "provider.model", os.environ.get("ASTER_MODEL", "glm-4.7"))
    except (json.JSONDecodeError, OSError):
        pass  # 配置不可读就静默跳过，走设置页手工配置


def provider_config() -> dict:
    """汇总 provider 运行配置：keychain 密钥 + settings 非敏感项 + 环境变量兜底"""
    conn = connect()
    try:
        return {
            "api_key": get_secret("provider.api_key") or os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
            "base_url": _get_setting(conn, "provider.base_url")
            or os.environ.get("ANTHROPIC_BASE_URL", ""),
            "model": _get_setting(conn, "provider.model", "glm-4.7"),
        }
    finally:
        conn.close()


def available() -> bool:
    cfg = provider_config()
    return bool(cfg["api_key"])


def chat(messages: list[dict], system: str | None = None, max_tokens: int = 2048) -> str:
    """单轮补全（Phase 1 够用：意图理解与交付生成都是一问一答）"""
    import anthropic

    cfg = provider_config()
    client = anthropic.Anthropic(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
    resp = client.messages.create(
        model=cfg["model"],
        max_tokens=max_tokens,
        system=system or "",
        messages=messages,
    )
    return "".join(b.text for b in resp.content if b.type == "text")
