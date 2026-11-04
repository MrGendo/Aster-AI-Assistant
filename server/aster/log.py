# 结构化日志：Phase 1 直接用标准库，格式统一带模块名，敏感值由调用方负责不打进来
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            stream=sys.stdout,
        )
    return logging.getLogger(name)
