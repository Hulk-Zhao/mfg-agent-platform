"""统一日志入口（对齐 Itcast ``base/logger`` 思路）。

设计原则（零破坏）：
- 薄封装 ``logging.getLogger``：**不替换**现有各模块的 logging 行为，
  仅提供统一入口与一次性全局配置，避免各模块各自 ``basicConfig``；
- ``get_logger(name)`` 语义与 ``logging.getLogger(name)`` 完全一致，
  现有调用点可零成本迁移（本次不强制迁移，迁移是纯重构）。
"""
from __future__ import annotations

import logging
import os

__all__ = ["get_logger", "configure", "DEFAULT_FORMAT"]

# 与平台日志风格一致的默认格式（时间 + 级别 + 模块 + 消息）
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    """返回指定名称的 logger（等价 ``logging.getLogger(name)``）。

    :param name: logger 名称（惯例使用 ``__name__``）。
    """
    return logging.getLogger(name)


def configure(level: str | int | None = None) -> None:
    """一次性配置根日志（幂等：多次调用以最后一次为准）。

    级别优先级：显式入参 > 环境变量 ``MFG_LOG_LEVEL`` > ``INFO``。
    """
    resolved = level if level is not None else os.getenv("MFG_LOG_LEVEL", "INFO")
    logging.basicConfig(level=resolved, format=DEFAULT_FORMAT)
