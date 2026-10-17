"""意图识别演进位（§6.2，对齐 Itcast ``query_classifier`` + ``strategy_selector`` 分离）。

现状：``TwoLevelRouter.route()`` 一体化（L0 规则 + L1 LLM 分类 + 降级）→ RouteResult。
目标：分类与策略选择**解耦**——
    ``IntentClassifier.classify()`` → ``IntentResult``（= RouteResult 上位契约）
    ``StrategySelector.select(intent)`` → ``Strategy(scenario_key)``

**本次仅骨架**：``RuleLLMClassifier`` 内部直接调用现 ``TwoLevelRouter``（行为等价），
``Router`` 运行路径不变（保证既有测试全绿）。切换为独立分类服务列为后续独立需求。
"""
from __future__ import annotations

from .classifier import IntentClassifier, IntentResult, RuleLLMClassifier
from .strategy import (
    DefaultStrategySelector,
    Strategy,
    StrategySelector,
)

__all__ = [
    "IntentResult",
    "IntentClassifier",
    "RuleLLMClassifier",
    "Strategy",
    "StrategySelector",
    "DefaultStrategySelector",
]
