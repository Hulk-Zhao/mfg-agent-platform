"""基础设施层（对齐 Itcast ``base/{config,logger}``）。

本层收敛「无业务语义」的通用能力：统一日志入口。配置仍由
``mfg_agents.common.config.settings`` 承担（本次不迁移，避免 V1 破坏）。
"""
from __future__ import annotations

from .logger import DEFAULT_FORMAT, configure, get_logger

__all__ = ["get_logger", "configure", "DEFAULT_FORMAT"]
