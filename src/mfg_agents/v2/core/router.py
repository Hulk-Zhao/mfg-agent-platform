"""两级路由器 TwoLevelRouter（D1：16 部门意图无限膨胀 → 闭集域组分类）。

路由目标不是 16 个部门，而是 **5 个域组 + N 个能力服务**（闭集，可扩展）：
- L0 规则快路径：复用 V1 SCENE_RULES 关键词，毫秒级命中直接返回；
- L1 LLM 闭集分类：小模型（tier=L1）把问题分到 5 域组 + out_of_scope；
- 降级路径：LLM 不可用（Ollama 未启动）时退化为规则模糊匹配，
  并标记 low_confidence=True——demo 在离线环境下必须可跑（硬性验收项）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ...tools.llm_factory import get_llm, llm_available
from ..contracts.scenario import ScenarioRegistry
# 域组定义与 L1 prompt 已迁移至 prompts/routing（§6.3，逐字节一致）；
# 此处再导出 DOMAIN_GROUPS，保持 router 对外符号不变。
from ..prompts.routing import DOMAIN_GROUPS, build_l1_prompt

logger = logging.getLogger(__name__)

# 降级模糊匹配用的域组关键词（L1 不可用时的兜底）
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "supply_chain": ["订单", "发货", "交期", "库存", "采购", "物料", "在途", "能发"],
    "rd_engineering": ["BOM", "ECN", "NPI", "图纸", "工艺文件", "试产"],
    "quality": ["不良", "缺陷", "客诉", "投诉", "8D", "IQC", "来料", "CPK", "SPC"],
    "manufacturing": ["停线", "复线", "OEE", "产出", "设备", "告警", "参数", "产线", "线体"],
    "function": ["报销", "请假", "开票", "发票", "资产", "会议"],
}

# V1 场景 → V2 场景 key（S1-S5 全部迁移完成；未映射仍返回 None 走人工兜底）
V1_SCENE_TO_V2: dict[str, Optional[str]] = {
    "line_stop": "line_stop_v2",
    "incoming_quality": "incoming_quality_v2",
    "customer_complaint": "customer_complaint_v2",
    "delivery_risk": "delivery_status_v2",
    "equipment_warning": "equipment_warning_v2",
}

# V1 场景 → 域组（供 L0 命中但场景未迁移时给出域归属）
V1_SCENE_TO_DOMAIN: dict[str, str] = {
    "line_stop": "manufacturing",
    "incoming_quality": "quality",
    "customer_complaint": "quality",
    "delivery_risk": "supply_chain",
    "equipment_warning": "manufacturing",
}


@dataclass
class RouteResult:
    """路由结果（可审计：level/confidence/low_confidence 全程可见）。"""

    domain: Optional[str] = None
    scenario_key: Optional[str] = None
    confidence: float = 0.0
    level: str = "L0"                    # L0 规则 | L1 模型 | degrade 降级
    entities: dict[str, Any] = field(default_factory=dict)
    low_confidence: bool = False         # True 时调用方应提示人工复核
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


class TwoLevelRouter:
    """两级路由器：L0 关键词快路径 → L1 LLM 闭集分类 →（降级）规则模糊匹配。"""

    def __init__(self, scenario_registry: Optional[ScenarioRegistry] = None) -> None:
        self.scenario_registry = scenario_registry

    def route(self, query: str, user_ctx: Optional[dict] = None) -> RouteResult:
        """路由入口：先 L0，未命中再 L1，LLM 不可用则降级。"""
        entities = self._extract_entities(query)
        l0 = self._route_l0(query)
        if l0 is not None:
            l0.entities = entities
            return l0
        if llm_available():
            l1 = self._route_l1(query)
            if l1 is not None:
                l1.entities = entities
                return l1
            return RouteResult(level="L1", entities=entities, low_confidence=True,
                               reason="L1 分类失败，未匹配任何域组")
        degraded = self._route_degraded(query)
        degraded.entities = entities
        return degraded

    # ---------- L0：规则快路径（复用 V1 SCENE_RULES） ----------

    def _route_l0(self, query: str) -> Optional[RouteResult]:
        """V1 关键词命中即返回（毫秒级）；同时优先匹配已注册场景的触发词。"""
        if self.scenario_registry is not None:
            scenario_key = self.scenario_registry.match_trigger_words(query)
            if scenario_key is not None:
                meta = self.scenario_registry.get(scenario_key)
                return RouteResult(
                    domain=meta.domains[0] if meta.domains else None,
                    scenario_key=scenario_key,
                    confidence=1.0,
                    level="L0",
                    reason=f"场景触发词命中: {scenario_key}",
                )
        from ...agents.business import SCENE_RULES  # V1 资产复用，零破坏
        for scene, keywords in SCENE_RULES:
            if any(k in query for k in keywords):
                return RouteResult(
                    domain=V1_SCENE_TO_DOMAIN.get(scene),
                    scenario_key=V1_SCENE_TO_V2.get(scene),
                    confidence=1.0,
                    level="L0",
                    reason=f"V1 关键词命中: {scene}",
                )
        return None

    # ---------- L1：LLM 闭集分类 ----------

    def _route_l1(self, query: str) -> Optional[RouteResult]:
        prompt = build_l1_prompt(query)
        try:
            llm = get_llm(temperature=0.0, tier="L1")
            resp = llm.invoke(prompt)
            content = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("L1 分类调用失败，转降级: %s", exc)
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
            return RouteResult(domain=None, scenario_key=None, confidence=confidence,
                               level="L1", low_confidence=confidence < 0.6,
                               reason="L1 判定超出平台范围")
        return RouteResult(
            domain=domain,
            scenario_key=self._scenario_for_domain(domain),
            confidence=confidence,
            level="L1",
            low_confidence=confidence < 0.6,
            reason=f"L1 分类: {domain}",
        )

    # ---------- 降级：规则模糊匹配（offline 兜底） ----------

    def _route_degraded(self, query: str) -> RouteResult:
        """LLM 不可用：按触发词/域组关键词打分，命中即走，标记 low_confidence。"""
        if self.scenario_registry is not None:
            scored = sorted(
                ((sum(1 for w in m.trigger_words() if w in query), m.key)
                 for m in self.scenario_registry.list()),
                reverse=True)
            if scored and scored[0][0] > 0:
                score, key = scored[0]
                meta = self.scenario_registry.get(key)
                return RouteResult(
                    domain=meta.domains[0] if meta.domains else None,
                    scenario_key=key,
                    confidence=min(0.5 + 0.1 * score, 0.8),
                    level="degrade",
                    low_confidence=True,
                    reason=f"LLM 不可用，场景触发词模糊匹配 {score} 个",
                )
        best_domain, best_score = None, 0
        for domain, words in DOMAIN_KEYWORDS.items():
            score = sum(1 for w in words if w in query)
            if score > best_score:
                best_domain, best_score = domain, score
        if best_domain is not None:
            return RouteResult(
                domain=best_domain,
                scenario_key=self._scenario_for_domain(best_domain),
                confidence=min(0.4 + 0.1 * best_score, 0.7),
                level="degrade",
                low_confidence=True,
                reason=f"LLM 不可用，域组关键词模糊匹配 {best_score} 个",
            )
        return RouteResult(
            domain=None, scenario_key=None, confidence=0.0, level="degrade",
            low_confidence=True, reason="LLM 不可用且无关键词命中，超出平台范围",
        )

    # ---------- 工具 ----------

    def _scenario_for_domain(self, domain: str) -> Optional[str]:
        """按域组找已注册场景（当前骨架仅 line_stop_v2，其余域返回 None）。"""
        if self.scenario_registry is None:
            return None
        for meta in self.scenario_registry.list():
            if domain in meta.domains:
                return meta.key
        return None

    @staticmethod
    def _extract_entities(query: str) -> dict[str, Any]:
        """实体抽取：复用 V1 business_agent 的型号/线体/紧急度规则（L0 级，无 LLM）。"""
        from ...agents.business import business_agent  # V1 资产复用
        state = business_agent({"user_query": query})
        return {
            "product_model": state.get("product_model"),
            "line_code": state.get("line_code"),
            "urgency": state.get("urgency"),
            "v1_scenario": state.get("scenario"),
        }
