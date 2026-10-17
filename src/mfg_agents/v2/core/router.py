"""两级路由器 TwoLevelRouter（D1：16 部门意图无限膨胀 → 闭集域组分类）。

路由目标不是 16 个部门，而是 **5 个域组 + N 个能力服务**（闭集，可扩展）：
- L0 规则快路径：复用 V1 SCENE_RULES 关键词，毫秒级命中直接返回；
- L1 LLM 闭集分类：小模型（tier=L1）把问题分到 5 域组 + out_of_scope；
- 降级路径：LLM 不可用（Ollama 未启动）时退化为规则模糊匹配，
  并标记 low_confidence=True——demo 在离线环境下必须可跑（硬性验收项）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ...common.llm_factory import llm_available
from ..contracts.scenario import ScenarioRegistry
# 域组定义已迁移至 prompts/routing；此处再导出 DOMAIN_GROUPS，保持 router 对外符号不变。
from ..prompts.routing import DOMAIN_GROUPS

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

# 办公意图关键词（办公助手 Agentic Agent 的确定性入口；先于 L1 语义分类，
# 保证"帮我订会议室/中午订个餐"这类口语问法稳定落到办公助手）
OFFICE_KEYWORDS: tuple[str, ...] = (
    "会议室", "会议室预订", "预订会议室", "预定会议室", "预约会议室", "订会议室",
    "订餐", "点餐", "叫餐", "订饭", "订午饭", "订晚饭", "订晚餐", "订外卖",
    "订个餐", "订个饭", "食堂", "餐厅", "工作餐", "加餐",
    "商务餐厅", "客餐", "宴请",
    "我的预订", "我的订单", "我的会议室",
)

# V1 场景 → 域组（供 L0 命中但场景未迁移时给出域归属）
V1_SCENE_TO_DOMAIN: dict[str, str] = {
    "line_stop": "manufacturing",
    "incoming_quality": "quality",
    "customer_complaint": "quality",
    "delivery_risk": "supply_chain",
    "equipment_warning": "manufacturing",
}

# 制造数据速查意图（V3.7 企业综合助手）：材料库存/成品库存/来料批次/库龄呆滞/
# 缺料/批次追溯等**口径查询**。这些问题的共性词（库存/缺料/能发/齐套/来料）
# 会被场景触发词误判成 delivery_status_v2 等协同场景，故单列快路径直答。
INVENTORY_QUERY_KEYWORDS: tuple[str, ...] = (
    "材料库存", "原材料库存", "物料库存", "原料库存", "材料仓",
    "成品库存", "成品仓", "成品有多少", "库存还有多少", "还有多少库存",
    "库存量", "库存速查", "查库存",
    "库龄", "呆滞", "积压", "长库龄",
    "安全库存", "低于安全库存", "缺料", "备料缺口",
    "来料批次", "到货批次", "批次追溯", "检验结论", "让步接收", "特采",
)

# 场景强信号（排除表）：一旦出现这些词，说明是停线/客诉/交期承诺等**协同场景**，
# 让位给 L0 场景触发词，不抢路由。
INVENTORY_SCENARIO_SIGNALS: tuple[str, ...] = (
    "停线", "复线", "客诉", "投诉", "8D", "发货", "交期", "承诺",
    "齐套", "延期", "升级", "报告", "设备", "参数", "不良批次", "质量异常",
)


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
    # 多意图路由策略（V3.1）：不是所有问题都要跑重型场景链路——
    # "scenario"=协同场景 DAG；"qa"=数据直答（无工单无 HITL）；"fallback"=转人工
    strategy: str = "scenario"

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "scenario_key": self.scenario_key,
            "confidence": round(self.confidence, 2),
            "level": self.level,
            "entities": self.entities,
            "low_confidence": self.low_confidence,
            "reason": self.reason,
            "strategy": self.strategy,
        }


class TwoLevelRouter:
    """两级路由器：L0 关键词快路径 → L1 语义闭集分类 →（兜底）关键词模糊匹配。"""

    def __init__(self, scenario_registry: Optional[ScenarioRegistry] = None,
                 intent_classifier: Optional[Any] = None) -> None:
        self.scenario_registry = scenario_registry
        # L1 分类已接线到 intent 模块（可注入替换，便于后续换独立分类模型）
        from ..intent.classifier import LLMClosedSetClassifier
        self.intent_classifier = intent_classifier or LLMClosedSetClassifier()

    def route(self, query: str, user_ctx: Optional[dict] = None) -> RouteResult:
        """路由入口：库存数据速查 → L0 场景/办公 → L1 语义 → 降级。"""
        entities = self._extract_entities(query)
        inventory = self._route_inventory(query)
        if inventory is not None:
            inventory.entities = entities
            return inventory
        l0 = self._route_l0(query)
        if l0 is not None:
            l0.entities = entities
            return l0
        office = self._route_office(query)
        if office is not None:
            office.entities = entities
            return office
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
        from ...common.business import SCENE_RULES  # V1 资产复用，零破坏
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

    # ---------- 制造数据速查快路径（企业综合助手的确定性入口） ----------

    def _route_inventory(self, query: str) -> Optional[RouteResult]:
        """库存/批次等制造数据口径查询：直答（strategy="qa"，无工单无 HITL）。

        先于 L0 场景触发词：这批问题的共性词会把它们误判为协同场景
        （delivery_status_v2 / incoming_quality_v2）。命中**具体库存问法**后直答；
        一旦出现停线/客诉/交期承诺等场景强信号，则让位给协同场景。
        """
        hit = next((w for w in INVENTORY_QUERY_KEYWORDS if w in query), None)
        if hit is None:
            return None
        if any(w in query for w in INVENTORY_SCENARIO_SIGNALS):
            return None
        return RouteResult(
            domain="supply_chain", scenario_key=None, confidence=1.0, level="L0",
            reason=f"制造数据速查意图命中: {hit}", strategy="qa")

    # ---------- 办公意图快路径（办公助手 Agentic Agent 的确定性入口） ----------

    def _route_office(self, query: str) -> Optional[RouteResult]:
        """办公关键词命中即路由到办公助手（独立 Agentic Agent）。

        先于 L1 语义分类：保证"帮我订个会议室""中午订个餐"等口语问法稳定落地，
        且在 LLM 不可用时同样可用（离线可演示）。
        """
        hit = next((w for w in OFFICE_KEYWORDS if w in query), None)
        if hit is None:
            return None
        return RouteResult(
            domain="office", scenario_key=None, confidence=1.0, level="L0",
            reason=f"办公意图关键词命中: {hit}", strategy="office")

    # ---------- L1：语义闭集分类（委托 intent.classifier） ----------

    def _route_l1(self, query: str) -> Optional[RouteResult]:
        intent = self.intent_classifier.classify(query)
        if intent is None:
            return None
        if intent.domain is None:  # 非领域意图（闲聊/无关）：轻量回应+引导回领域
            return RouteResult(domain=None, scenario_key=None,
                               confidence=intent.confidence, level="L1",
                               low_confidence=intent.low_confidence,
                               reason=intent.reason,
                               strategy="chitchat")
        # 多意图路由（V3.1）：域命中但无场景触发词命中 → 数据直答（qa），
        # 不硬套场景 DAG——"今天A型号的质量怎么样"是查询，不是客诉闭环
        scenario_key, trig_score = self._scenario_for_domain(intent.domain, query)
        if trig_score > 0:
            return RouteResult(
                domain=intent.domain, scenario_key=scenario_key,
                confidence=intent.confidence, level="L1",
                low_confidence=intent.low_confidence,
                reason=intent.reason + "（触发词命中，走协同场景）",
                strategy="scenario")
        return RouteResult(
            domain=intent.domain, scenario_key=None,
            confidence=intent.confidence, level="L1",
            low_confidence=intent.low_confidence,
            reason="数据查询型问题，走数据直答（无场景触发词命中）",
            strategy="qa",
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
                scenario_key=self._scenario_for_domain(best_domain, query),
                confidence=min(0.4 + 0.1 * best_score, 0.7),
                level="degrade",
                low_confidence=True,
                reason=f"LLM 不可用，域组关键词模糊匹配 {best_score} 个",
                strategy="scenario",
            )
        return RouteResult(
            domain=None, scenario_key=None, confidence=0.0, level="degrade",
            low_confidence=True, reason="LLM 不可用且无关键词命中，超出平台范围",
        )

    # ---------- 工具 ----------

    def _scenario_for_domain(self, domain: str, query: str = "") -> Optional[str]:
        """按域组找已注册场景；同域多场景时按**触发词命中**消歧。

        V3.1 修复：此前按注册字典序取第一个（customer < delivery），
        导致 supply_chain 域的"齐套了没"被错误路由到客诉场景——
        客诉场景的参与域含 supply_chain，字典序又恰好靠前。
        现在同域多候选时，用场景触发词与问题的重叠数消歧（无信号回退首序）。
        """
        if self.scenario_registry is None:
            return None, 0
        candidates = [m for m in self.scenario_registry.list()
                      if domain in m.domains]
        if not candidates:
            return None, 0
        if len(candidates) == 1:
            return candidates[0].key, 1
        q = (query or "").strip()

        def trigger_score(meta: Any) -> int:
            return sum(1 for trig in meta.triggers
                       for w in (trig.get("words") or [])
                       if str(w) and str(w) in q)

        scored = sorted(((trigger_score(m), m.key) for m in candidates),
                        reverse=True)
        return (scored[0][1], scored[0][0])

    @staticmethod
    def _extract_entities(query: str) -> dict[str, Any]:
        """实体抽取：复用 V1 business_agent 的型号/线体/紧急度规则（L0 级，无 LLM）。"""
        from ...common.business import business_agent  # V1 资产复用
        state = business_agent({"user_query": query})
        return {
            "product_model": state.get("product_model"),
            "line_code": state.get("line_code"),
            "urgency": state.get("urgency"),
            "v1_scenario": state.get("scenario"),
        }
