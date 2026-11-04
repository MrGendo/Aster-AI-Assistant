# 全局配置：路径与环境开关
# Phase 1 以 dev 形态优先：数据默认落在仓库 data/ 下，可用 ASTER_DATA_DIR 覆写（正式部署再切平台目录）
import os
from pathlib import Path

# 仓库根目录 = server/ 的上一级
ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = Path(os.environ.get("ASTER_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "aster.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SANDBOX_DIR = DATA_DIR / "sandboxes"
RUNS_DIR = DATA_DIR / "runs"
LOG_DIR = DATA_DIR / "logs"
CAPABILITIES_DIR = ROOT / "capabilities"

# 能力工作进程统一用同一个解释器（venv 内），保证依赖一致
WORKER_PYTHON = Path(os.environ.get("ASTER_WORKER_PYTHON", ROOT / ".venv/bin/python"))

KEYCHAIN_SERVICE = "com.aster.platform"

for _d in (DATA_DIR, SANDBOX_DIR, RUNS_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)
