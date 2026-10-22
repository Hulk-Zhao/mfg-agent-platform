"""两级路由器 TwoLevelRouter（D1：16 部门意图无限膨胀 → 闭集域组分类）。

路由目标不是 16 个部门，而是 **5 个域组 + N 个能力服务**（闭集，可扩展）：
- L0 规则快路径：复用 V1 SCENE_RULES 关键词，毫秒级命中直接返回；
- L1 LLM 闭集分类：小模型（tier=L1）把问题分到 5 域组 + out_of_scope；
- 降级路径：LLM 不可用（Ollama 未启动）时退化为规则模糊匹配，
  并标记 low_confidence=True——demo 在离线环境下必须可跑（硬性验收项）。
"""
from __future__ import annotations

import logging
import re
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

# R7 质量追溯强信号：批次号 + 追溯意图词 → 直接交质量域智能体三路并查。
# 为什么必须前置：Text2SQL 的注册查询匹配会忽略批次号、命中无参的「近期来料」，
# 把「BATCH-A-1008 的来龙去脉」答成另一个批次（真机实测 MAT-SP-1015）。
QUALITY_TRACE_WORDS: tuple[str, ...] = (
    "追溯", "来龙去脉", "来龙", "串一下", "串起来", "一起追",
    "全链路", "完整链路",
)
_BATCH_NO_RE = re.compile(
    r"\b(BATCH-[A-Za-z0-9][A-Za-z0-9-]*|MAT-[A-Za-z0-9][A-Za-z0-9-]*)\b",
    re.IGNORECASE)

# 场景强信号（排除表）：一旦出现这些词，说明是停线/客诉/交期承诺等**协同场景**，
# 让位给 L0 场景触发词，不抢路由。
INVENTORY_SCENARIO_SIGNALS: tuple[str, ...] = (
    "停线", "复线", "客诉", "投诉", "8D", "发货", "交期", "承诺",
    "齐套", "延期", "升级", "报告", "设备", "参数", "不良批次", "质量异常",
)

# 决策支持型（开放式态势汇总）意图（V4.1）：
# "本月度最需关注什么 / 本月重点是什么 / 整体情况怎么样 / 最近有什么风险"。
# 这类问题**既无场景触发词也无具体实体**——旧行为是落 chitchat（只回一句问候 +
# 示例问题），或在降级路径里被**隐式指派**到某个场景跑重型 DAG（截图反馈：问
# "本月最需关注什么"却跑了客诉 8D 链路 + 双人工确认）。管理者这类问题恰恰最需要
# 答案，故单列 digest 快路径：跨域态势汇总（只读、无工单）。
# 强信号词（命中其一即可，但仍需"无具体实体"这一硬门槛）：
DIGEST_STRONG: tuple[str, ...] = (
    "最需关注", "重点关注", "需要关注", "最该关注", "该关注", "关注什么",
    "重点是什么", "重点是", "重点在哪", "有哪些风险", "有什么风险", "风险有哪些",
    "整体情况", "整体态势", "整体怎么样", "全局情况", "经营情况", "整体表现",
    "汇总", "概览", "小结", "总结一下", "概括一下",
    # 2026-10-22：管理者的「待我决策」类提问。此类问题同样是无实体的决策支持，
    # 旧逻辑落 qa 明细探查 → 模型去猜 risk_level 等不存在的列 → 人工兜底。
    "要我拍板", "待我拍板", "拍板", "升级事项", "待我确认", "待我终批", "待终批",
)
# 弱信号：单说"关注"时必须同时带范围/时间词，否则不判 digest（防误吞对象级提问）：
DIGEST_SCOPE: tuple[str, ...] = (
    "本月", "这个月", "当月", "本周", "这周", "今天", "今日",
    "最近", "当前", "目前", "整体", "全厂", "公司",
)

# R10 老板经营 · 大单进度（对象级，早于 L1，避免被 qa 明细探查答成别的东西）。
BUSINESS_ORDER_WORDS: tuple[str, ...] = (
    "大单", "大订单", "大额订单", "大单进度",
)
_BIG_QTY_RE = re.compile(r"(\d{3,6})\s*(?:件|台|套|pcs)", re.IGNORECASE)
_CN_BIG_QTY: dict[str, int] = {"三千": 3000, "五千": 5000, "八千": 8000, "一万": 10000}
_SO_NO_RE = re.compile(r"\b(SO-\d{4}-\d+)\b", re.IGNORECASE)
_MODEL_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*型号")

# R11 呆滞料：库龄/积压问法（先于库存速查）；天数缺省 180（呆滞口径）
AGING_WORDS: tuple[str, ...] = (
    "呆滞", "超龄", "积压", "库龄", "没动的料", "没有动", "长期不动", "半年没动",
)
_AGING_DAYS_RE = re.compile(r"(\d{2,4})\s*天")


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
        """路由入口：库存数据速查 → L0 场景/办公 → 态势汇总 → L1 语义 → 降级。"""
        entities = self._extract_entities(query)
        # R7 质量追溯强信号（批次号 + 追溯词）：早于库存/场景快路径。
        # 根因：批次号被忽略时会命中无参的「近期来料」查询，答成别的批次
        # （R7 真机实测：问 BATCH-A-1008 返回 MAT-SP-1015）。
        trace = self._route_quality_trace(query, entities)
        if trace is not None:
            return trace
        # R11 呆滞料随时问：必须先于库存速查（后者也含「库龄/呆滞」关键词，
        # 会把「超180天没动的料」答成 TOP10 明细，缺资金占用与处置建议）。
        aging = self._route_aging(query, entities)
        if aging is not None:
            return aging
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
        # 决策支持型（开放式态势汇总）：无场景/办公信号时兜住"最需关注什么"这类
        # 管理者提问——否则会落到 L1 → chitchat（只回问候），或被降级路径隐式
        # 指派到某个场景跑重型 DAG。必须在 L1 之前。
        digest = self._route_digest(query)
        if digest is not None:
            digest.entities = entities
            return digest
        # R10 老板经营 · 大单进度（对象级问题，不能走开放式 digest 汇总）：
        # 「5000 件大单做到哪了」必须回到那张单的进度 + 预计完工 + 主动风险。
        business = self._route_business(query, entities)
        if business is not None:
            return business
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

    # ---------- 质量追溯快路径（R7：批次号 + 追溯意图） ----------

    def _route_quality_trace(self, query: str,
                             entities: dict[str, Any]) -> Optional[RouteResult]:
        """批次号 + 追溯词 → 质量域 Agentic（三路并查），不交给 Text2SQL 猜。"""
        if not any(word in query for word in QUALITY_TRACE_WORDS):
            return None
        match = _BATCH_NO_RE.search(query or "")
        if not match:
            return None
        batch = match.group(1).upper()
        return RouteResult(
            domain="quality", confidence=0.95, level="L1",
            entities={**entities, "batch_no": batch}, strategy="trace",
            reason=f"批次追溯意图命中（批次号 {batch}）",
        )

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

    # ---------- 决策支持型：开放式态势汇总快路径（digest） ----------

    def _route_digest(self, query: str) -> Optional[RouteResult]:
        """开放式态势汇总问句 → digest（跨域态势汇总，只读无工单）。

        两道判定，缺一不可：
        ① **意图信号**：命中 DIGEST_STRONG 之一；或单说"关注"且带范围/时间词；
        ② **无具体实体**：不含型号/线体/料号·批号·单号类编码——一旦有实体，说明是
           对象级问题（"A型号本月情况如何"），必须走原链路，不能被汇总吞掉。
        """
        hit = next((w for w in DIGEST_STRONG if w in query), None)
        if hit is None and "关注" in query:
            hit = "关注" if any(w in query for w in DIGEST_SCOPE) else None
        if hit is None:
            return None
        if self._has_entity(query):
            logger.info("[router] 汇总意图命中 %r 但含具体对象 → 按对象级链路: %s",
                        hit, query[:40])
            return None
        logger.info("[router] 汇总意图命中 %r → digest: %s", hit, query[:40])
        return RouteResult(
            domain=None, scenario_key=None, confidence=0.9, level="L0",
            reason=f"开放式态势汇总意图命中: {hit}", strategy="digest")

    # ---------- R11：呆滞料随时问 ----------

    def _route_aging(self, query: str,
                     entities: dict[str, Any]) -> Optional[RouteResult]:
        """库龄/呆滞问句 → aging 确定性盘点（清单 + 资金占用 + 退供评审）。"""
        hit = next((w for w in AGING_WORDS if w in query), None)
        if hit is None:
            return None
        m = _AGING_DAYS_RE.search(query or "")
        if m:
            days = int(m.group(1))
        elif "半年" in query:
            days = 180
        elif hit in ("呆滞", "超龄", "积压", "没动的料", "没有动", "长期不动"):
            days = 180
        else:
            days = 90
        return RouteResult(
            domain="supply_chain", confidence=0.95, level="L0",
            entities={**entities, "days": days},
            strategy="aging",
            reason=f"呆滞料盘点意图命中（{hit}；库龄≥{days} 天）")

    # ---------- R10：老板经营 · 大单进度 ----------

    def _route_business(self, query: str,
                        entities: dict[str, Any]) -> Optional[RouteResult]:
        """「大单做到哪了 / 大单进度」→ business 确定性成稿。

        与 digest 的分界：digest 是**开放式**汇总（无具体对象）；这里明确指向
        「在制大单」，要返回单张单的进度、预计完工与主动风险，不能只给 KPI 清单。
        数量阈值默认 5000（"5000 件大单"），单号/型号可选收窄。
        """
        hit = next((w for w in BUSINESS_ORDER_WORDS if w in query), None)
        if hit is None:
            # "做到哪了 / 完成到哪" 必须带"单"字才判（防吞一般进度问句）
            if not (("做到哪" in query or "完成到哪" in query
                     or "做到什么程度" in query) and "单" in query):
                return None
            hit = "做到哪"
        min_qty = 0
        m = _BIG_QTY_RE.search(query or "")
        if m:
            min_qty = int(m.group(1))
        else:
            min_qty = next((v for k, v in _CN_BIG_QTY.items() if k in (query or "")), 0)
        so = _SO_NO_RE.search(query or "")
        model_match = _MODEL_CODE_RE.search(query or "")
        model = f"{model_match.group(1).upper()}型号" if model_match else ""
        scoped = {**entities,
                  "min_qty": min_qty or 5000,
                  "order_no": so.group(1).upper() if so else "",
                  "model": model}
        bits = [f"阈值≥{scoped['min_qty']} 件"]
        if scoped["order_no"]:
            bits.append(scoped["order_no"])
        if model:
            bits.append(model)
        return RouteResult(
            domain="orders", confidence=0.93, level="L0", entities=scoped,
            strategy="business",
            reason=f"老板经营 · 大单进度意图命中（{hit}；{'，'.join(bits)}）")

    @staticmethod
    def _has_entity(query: str) -> bool:
        """是否含具体对象（型号 / 线体 / 料号·批号·单号类编码）——只看**问题原文**。

        为什么不用 ``_extract_entities``（V1 business_agent）的结果：它在**抽不到**
        时会给默认值（``A型号`` / ``SMT-1`` / ``line_stop``）。据此判"有实体"会把
        所有开放式提问都误判成对象级问题——本通道曾因此完全失效（"本月最需关注
        什么"被判为有 A型号，digest 永不命中）。判据必须是问题原文本身。
        """
        if re.search(r"[A-Ea-e]\s*型号", query):
            return True
        if re.search(r"(SMT|DIP)-\d", query, re.IGNORECASE):
            return True
        code = re.search(r"\b([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)\b", query)
        if code and not code.group(1).upper().startswith(("SMT-", "DIP-")) \
                and any(ch.isdigit() for ch in code.group(1)):
            return True
        return bool(re.search(r"客户\s*[A-Za-z一二三四五六七八九十]", query))

    # ---------- L1：语义闭集分类（委托 intent.classifier） ----------

    def _route_l1(self, query: str) -> Optional[RouteResult]:
        intent = self.intent_classifier.classify(query)
        if intent is None:
            return None
        if intent.domain is None:  # 非领域意图（闲聊/无关）：轻量回应+引导回领域
            # V4.1 复核：**带具体对象**的问句（型号 / 线体 / 料号·批号类编码）本质是
            # 数据问题，不该被回一句问候——"A型号本月情况如何"曾因此答非所问。
            # 有对象 → 走数据直查（查不到还有 Agentic 域智能体 / 人工兜底）；无对象
            # （真闲聊"你好""你会做饭吗"）才轻回应。这是对 L1 判定的**保守复核**，
            # 只放宽"有明确指向"的那一类，不放宽纯闲聊。
            if self._has_entity(query):
                return RouteResult(
                    domain=None, scenario_key=None,
                    confidence=intent.confidence, level="L1",
                    low_confidence=True,
                    reason=f"{intent.reason}；但问题含具体对象（型号/线体/编码），"
                           "按数据直查处理",
                    strategy="qa")
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
            scenario_key, trig_score = self._scenario_for_domain(best_domain, query)
            # V4.1：只有**场景触发词真命中**才走 scenario。此前这里无条件取
            # `_scenario_for_domain` 的返回值（无触发词时它退化为"该域第一个场景"），
            # 于是"域关键词命中但没有任何场景信号"的问题会被**隐式指派**一个场景
            # 跑重型 DAG（截图反馈：泛化提问却跑了客诉 8D + 双 HITL）。无明显信号
            # 的问题应走数据直答（qa），把"要不要建工单"的决定交给真实信号。
            if trig_score > 0:
                return RouteResult(
                    domain=best_domain,
                    scenario_key=scenario_key,
                    confidence=min(0.4 + 0.1 * best_score, 0.7),
                    level="degrade",
                    low_confidence=True,
                    reason=f"LLM 不可用，域组关键词模糊匹配 {best_score} 个"
                           f"（场景触发词命中 {trig_score} 个）",
                    strategy="scenario",
                )
            return RouteResult(
                domain=best_domain, scenario_key=None,
                confidence=min(0.4 + 0.1 * best_score, 0.7),
                level="degrade", low_confidence=True,
                reason=f"LLM 不可用，域组关键词模糊匹配 {best_score} 个"
                       f"（无场景触发词，走数据直答）",
                strategy="qa",
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
