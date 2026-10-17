"""意图分类器（§6.2）。

- ``IntentResult`` 与 ``RouteResult`` 字段一一对应；
- ``LLMClosedSetClassifier``：**已接线**——Router 的 L1 分类委托给它（闭集 5 域组）；
- ``RuleLLMClassifier``：历史包装器（包装整个 Router），保留兼容，运行路径不再使用。
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["IntentResult", "IntentClassifier", "LLMClosedSetClassifier",
           "RuleLLMClassifier"]

logger = logging.getLogger(__name__)


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


class LLMClosedSetClassifier(IntentClassifier):
    """闭集域组分类器（Router L1 的正式实现，**已接线**）。

    只判断"域组"（5 域组 + out_of_scope）；场景 key 由 Router 依据场景注册表决定。
    服务不可用 / 调用失败 / 输出不可解析 → 返回 None，由 Router 走关键词兜底。
    """

    def classify(self, query: str,
                 user_ctx: Optional[dict] = None) -> Optional[IntentResult]:
        from ...common.llm_factory import get_llm, llm_available
        from ..prompts.routing import DOMAIN_GROUPS, build_l1_prompt

        if not llm_available():
            return None
        try:
            llm = get_llm(temperature=0.0, tier="L1",
                          max_tokens=80, timeout=20, thinking="disabled")
            resp = llm.invoke(build_l1_prompt(query))
            content = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("语义分类调用失败，转关键词兜底: %s", exc)
            return None
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        domain = str(data.get("domain", "")).strip()
        if domain not in DOMAIN_GROUPS:
            return None
        confidence = float(data.get("confidence", 0.5) or 0.5)
        if domain == "out_of_scope":
            return IntentResult(domain=None, confidence=confidence, level="L1",
                                low_confidence=confidence < 0.6,
                                reason="语义匹配：超出平台覆盖范围")
        return IntentResult(domain=domain, confidence=confidence, level="L1",
                            low_confidence=confidence < 0.6,
                            reason=f"语义匹配: {domain}")


class RuleLLMClassifier(IntentClassifier):
    """历史包装器（包装整个 TwoLevelRouter）——保留兼容，运行路径不再使用。

    新代码请直接用 ``LLMClosedSetClassifier``（Router 已接线）。
    """

    def __init__(self, router: Any = None) -> None:
        self._router = router

    def _get_router(self) -> Any:
        if self._router is None:
            from ..core.router import TwoLevelRouter
            from ..contracts.scenario import ScenarioRegistry
            from ...common.config import settings
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
