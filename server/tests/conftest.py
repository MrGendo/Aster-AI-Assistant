# 测试环境：数据目录指向临时位置，避免污染真实 data/
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="aster-test-")
os.environ["ASTER_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ.setdefault("ASTER_WORKER_PYTHON", os.path.join(
    os.path.dirname(_TMP), "..", ".venv", "bin", "python"))
