"""
logger.py — 统一日志模块
每个项目在 projects/<project_dir>/logs/ 下生成日志文件：
  - research.log   : 研究流程日志（INFO/WARNING/ERROR）
  - error.log      : 仅错误日志（ERROR）

全局日志（框架级）写入 projects/logs/framework.log
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECTS_DIR = os.path.join(os.path.dirname(__file__), "projects")

# ==================== 格式 ====================
_FMT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _make_handler(log_path: str, level: int, max_bytes: int = 10 * 1024 * 1024) -> RotatingFileHandler:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    return handler


def get_project_logger(project_dir: str, name: str) -> logging.Logger:
    """
    获取项目级 logger。
    日志写入 projects/<project_dir>/logs/research.log 和 error.log，
    同时输出到控制台。
    """
    logger_name = f"dr.{project_dir}.{name}"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    log_dir = os.path.join(PROJECTS_DIR, project_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # research.log — INFO 及以上
    logger.addHandler(_make_handler(os.path.join(log_dir, "research.log"), logging.INFO))
    # error.log — ERROR 及以上
    logger.addHandler(_make_handler(os.path.join(log_dir, "error.log"), logging.ERROR))

    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    logger.addHandler(console)

    logger.propagate = False
    return logger


def get_framework_logger(name: str = "framework") -> logging.Logger:
    """
    获取框架级 logger（不依赖项目目录）。
    写入 projects/logs/framework.log
    """
    logger_name = f"dr.{name}"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    log_dir = os.path.join(PROJECTS_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)

    logger.addHandler(_make_handler(os.path.join(log_dir, "framework.log"), logging.INFO))
    logger.addHandler(_make_handler(os.path.join(log_dir, "error.log"), logging.ERROR))

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    logger.addHandler(console)

    logger.propagate = False
    return logger
