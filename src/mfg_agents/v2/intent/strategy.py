"""策略选择器骨架（§6.2）。

``Strategy`` 描述"用哪个场景 key 编排"；``DefaultStrategySelector`` 复用
现注册表匹配逻辑（意图里已带 scenario_key 时直接透传）。本次仅骨架，
``Router`` 暂不委托（运行路径不变）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["Strategy", "StrategySelector", "DefaultStrategySelector"]


@dataclass
class Strategy:
    """编排策略（场景 key + 选择理由，可审计）。"""

    scenario_key: Optional[str]
    reason: str = ""


class StrategySelector(ABC):
    """策略选择器抽象。"""

    @abstractmethod
    def select(self, intent: Any,
               user_ctx: Optional[dict] = None) -> Strategy:
        """按意图选择编排策略。"""


class DefaultStrategySelector(StrategySelector):
    """默认策略选择器：直接采用意图中的场景 key（当前注册表匹配结果）。"""

    def select(self, intent: Any,
               user_ctx: Optional[dict] = None) -> Strategy:
        scenario_key = getattr(intent, "scenario_key", None)
        reason = getattr(intent, "reason", "") or "默认策略：按意图场景 key 编排"
        return Strategy(scenario_key=scenario_key, reason=reason)
