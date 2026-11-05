# 开发入口：.venv/bin/python server/run.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 能力专属解释器（技术债根治）：deep_research 的 gpt-researcher 依赖阵营
# （mcp<2）与主 venv 的 browser-use 阵营（mcp==2.1.1）互斥，拆独立 venv 运行
_ROOT = Path(__file__).resolve().parent.parent
_dr_py = _ROOT / ".venvs" / "deep_research" / "bin" / "python"
if _dr_py.exists():
    os.environ.setdefault("ASTER_WORKER_PYTHON_DEEP_RESEARCH", str(_dr_py))

import uvicorn

from aster.api.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8317, log_level="info")
