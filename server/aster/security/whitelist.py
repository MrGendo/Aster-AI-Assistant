# 授权目录白名单：文件系统访问默认拒绝（PRD §12.2 最小权限）
# 白名单内路径放行，符号链接逃逸用 realpath 归一化后判定
from pathlib import Path

from ..config import DATA_DIR, ROOT

# Phase 1 默认边界：系统自身数据目录 + capabilities 目录（worker 需要被 exec）
DEFAULT_ALLOWED = [DATA_DIR.resolve(), (ROOT / "capabilities").resolve()]
_extra: list[Path] = []


def allow(path: str | Path) -> None:
    """新增授权目录（运行期由设置页触发；属于权限扩张，调用方须过 L2 审批）"""
    _extra.append(Path(path).resolve())


def is_allowed(path: str | Path) -> bool:
    p = Path(path).resolve()
    return any(str(p).startswith(str(base) + "/") or p == base for base in DEFAULT_ALLOWED + _extra)
