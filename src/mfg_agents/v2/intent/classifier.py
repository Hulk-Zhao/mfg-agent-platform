"""意图分类器骨架（§6.2）。

``IntentResult`` 与现有 ``RouteResult`` 字段一一对应，是其后继上位契约。
``RuleLLMClassifier`` 本次**内部直接调用现 TwoLevelRouter**（行为等价），
为后续"Router 改为 classifier.classify → selector.select"迁移铺路。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["IntentResult", "IntentClassifier", "RuleLLMClassifier"]


@dataclass
class IntentResult:
    """意图识别结果（字段与 ``core.router.RouteResult`` 一一对应）。"""

    domain: Optional[str] = None
    scenario_key: Optional[str] = None
    confidence: float = 0.0
    level: str = "L0"                    # L0 规则 | L1 模型 | degrade 降级
    entities: dict[str, Any] = field(default_factory=dict)
    low_confidence: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "scenario_key": self.scenario_key,
            "confidence": round(self.confidence, 2),
            "level": self.level,
            "entities": self.entities,
            "low_confidence": self.low_confidence,
            "reason": self.reason,
        }


class IntentClassifier(ABC):
    """意图分类器抽象（可替换为独立分类模型/服务）。"""

    @abstractmethod
    def classify(self, query: str,
                 user_ctx: Optional[dict] = None) -> IntentResult:
        """把用户问题分类为意图（域组 + 场景 key）。"""


class RuleLLMClassifier(IntentClassifier):
    """规则 + LLM 分类器：**包装现 TwoLevelRouter**（行为等价）。

    可注入自定义 router（测试友好）；未注入时按默认配置惰性构建。
    """

    def __init__(self, router: Any = None) -> None:
        self._router = router

    def _get_router(self) -> Any:
        if self._router is None:
            from ..core.router import TwoLevelRouter
            from ..contracts.scenario import ScenarioRegistry
            from ...config import settings
            registry = ScenarioRegistry(settings.V2_SCENARIOS_DIR)
            registry.load()
            self._router = TwoLevelRouter(registry)
        return self._router

    def classify(self, query: str,
                 user_ctx: Optional[dict] = None) -> IntentResult:
        route = self._get_router().route(query, user_ctx)
        return IntentResult(
            domain=route.domain,
            scenario_key=route.scenario_key,
            confidence=route.confidence,
            level=route.level,
            entities=dict(route.entities or {}),
            low_confidence=route.low_confidence,
            reason=route.reason,
        )
