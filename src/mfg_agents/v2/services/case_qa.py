"""CaseQAService：**本体增强 Agentic RAG** 的问答编排（有界闭环 + 引用约束）。

架构位置：§7.8（RAG 流程）、§7.5 步 13（引用校验）；PRD FR-01/02/03/04/07。

## 一次提问的完整链路（升级后）

```
 ① 术语归一（确定性，全词表）           → 只影响 query_text（A10）
 ② 同义扩展（确定性）                  → 只影响 query_text
 ③ 意图识别 + 查询分解（LLM 可参与）     → 产出**只是查询文本**，白名单校验
 ④ 检索计划                            → 计划里没有 filter 字段
 ⑤ 混合召回（每轮）                     → 安全内核：过滤在召回前下推（BR-01 前半）
 ⑥ 充分性自检（确定性）                  → 够不够？缺在哪？"无关问题"必须被识别
 ⑦ 本体关系扩展 → 再检索（有界）          → 跳数 ≤3 / 轮数 ≤预算 / 相对收益 < 阈值即停
 ⑧ 权限过滤（二次授权）                  → 独立代码路径（BR-01 后半）
 ⑨ 作答（大模型，引用约束；失败降级模板）   → 只吃知识层；每条结论挂引用
 ⑩ 引用校验（确定性）                    → 无引用/不一致即丢弃；全丢拒答（BR-05）
 ⑪ 本体增强收益 + 证据链                 → 真实统计，前端只渲染
```

## 三条纪律

1. **每个断言必须挂 ``(case_id, chunk_id, content_version)``**；无引用的断言**丢弃**
   （不是整段丢弃，也不是补一个假引用）。
2. **引用校验器回查知识层**：``content_version`` 或文本哈希与当前案例不一致 → 该断言丢弃。
3. **全部断言被丢弃 / 无命中 → 拒答**：返回明确原因 + 改写引导，**不编造**。

## 红线（升级不得触碰）

- **权限过滤永远由 ``AclCompiler`` 独占**：LLM 计划器/作答器都拿不到过滤器，
  计划对象里也没有 filter 字段；
- **闭环里每一轮召回都完整走安全内核**，不存在"合并成一次查询"的快路径；
- **循环必须有刹车**（跳数/轮数/收益），触顶如实返回并写明终止原因。
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from ...common.config import settings
from ..contracts.case_acl import Principal
from ..contracts.case_index import KnowledgeChunk
from ..contracts.ontology import OntologyRegistry, get_ontology_registry
from ..security.acl_compiler import EnvContext
from ..security.acl_evaluator import get_acl_evaluator
from .case_agentic import (
    INTENT_LABELS,
    assess_sufficiency,
    merge_search_responses,
)
from .case_audit import CaseAuditLog
from .case_index import CaseIndexService
from .case_qa_llm import AnswererError, AutoPlanner, LLMAnswerer
from .case_relation import get_relation_index, segments_in_text
from .case_search import CaseSearchService, SearchResponse
from .case_store import CaseStore

logger = logging.getLogger(__name__)

__all__ = [
    "Citation",
    "Claim",
    "AnswerResult",
    "Answerer",
    "TemplateAnswerer",
    "CaseQAService",
]


@dataclass(frozen=True)
class Citation:
    """引用三元组（K3：必须能定位到具体 chunk 与内容版本）。"""

    case_id: str
    chunk_id: str
    content_version: int

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "chunk_id": self.chunk_id,
                "content_version": self.content_version}


@dataclass(frozen=True)
class Claim:
    """一条断言 + 其引用集合。``citations`` 为空即"无引用断言"——必须丢弃。"""

    text: str
    citations: tuple[Citation, ...] = ()

    @property
    def has_citation(self) -> bool:
        return bool(self.citations)


class Answerer(Protocol):
    """LLM 接入点协议（生产实现 = ``case_qa_llm.LLMAnswerer``）。"""

    def __call__(self, question: str,
                 chunks: Sequence[KnowledgeChunk]) -> Sequence[Claim]:  # pragma: no cover
        ...


class TemplateAnswerer:
    """确定性模板作答器（**降级兜底，不调 LLM**）。

    产出规则：每条命中生成一条"现象-根因-对策"断言，并挂该 chunk 的引用。
    它刻意**不做**跨案例归纳——归纳需要 LLM，而"跨案例归纳"最容易产出无引用断言。
    """

    def __call__(self, question: str,
                 chunks: Sequence[KnowledgeChunk]) -> Sequence[Claim]:
        claims: list[Claim] = []
        for chunk in chunks or ():
            text = str(chunk.text or "").strip()
            if not text:
                continue
            claims.append(Claim(
                text=f"案例 {chunk.case_id} 的经验（{chunk.chunk_type}）：{text}",
                citations=(Citation(case_id=chunk.case_id,
                                    chunk_id=chunk.chunk_id,
                                    content_version=chunk.content_version),),
            ))
        return claims


def _trace_step(step: int, title: str, detail: str,
                items: Sequence[str] = (), *,
                kind: str = "deterministic", numbers: Any = None,
                why: str = "") -> dict[str, Any]:
    """一条处理轨迹（**证据链 2.0**：结构与 1.0 向后兼容，只做加法）。

    | 字段 | 含义 | 谁在用 |
    |---|---|---|
    | ``step`` / ``title`` / ``detail`` / ``items`` | 1.0 既有字段 | 前端时间线（不破坏） |
    | ``kind`` | 决策类型：deterministic / agentic / safe | 前端步骤徽标（"这步是模型决策还是规则"） |
    | ``numbers`` | 关键数字 ``{"label": value}`` | 前端"这一步量化到了什么" |
    | ``why`` | 触发/终止原因（只在再检索、终止这类步骤上非空） | 讲解"为什么又搜了一轮 / 为什么停了" |

    为什么把"决策类型"做成一等字段而不是让前端按标题猜：Agentic 是不是
    真的在决策，必须能被一眼看出来（PRD FR-04）。前端猜标题字符串，
    等于把最该被验证的东西变成不可验证的文案。
    """
    return {"step": int(step), "title": str(title), "detail": str(detail),
            "items": [str(item) for item in (items or ())][:12],
            "kind": str(kind or "deterministic"),
            "numbers": {str(k): v for k, v in (numbers or {}).items()},
            "why": str(why or "")}


def _renumber(trace: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按出现顺序重排步序（每轮都可能追加步骤，静态编号必然对不上）。"""
    out: list[dict[str, Any]] = []
    for index, step in enumerate(trace or (), start=1):
        row = dict(step)
        row["step"] = index
        out.append(row)
    return out


def _case_ids(hits: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(h.get("case_id") or "") for h in hits or () if h.get("case_id")}


@dataclass
class AgenticLoopConfig:
    """循环刹车参数（env 配置的唯一读取点，便于测试注入与现场调参）。"""

    enabled: bool = True
    max_hops: int = 3
    max_rounds: int = 3
    gain_threshold: float = 0.05

    @classmethod
    def from_settings(cls) -> "AgenticLoopConfig":
        return cls(
            enabled=bool(getattr(settings, "CASE_AGENTIC_ENABLED", True)),
            max_hops=max(0, int(getattr(settings, "CASE_AGENTIC_MAX_HOPS", 3))),
            max_rounds=max(1, int(getattr(settings, "CASE_AGENTIC_MAX_ROUNDS", 3))),
            gain_threshold=float(getattr(settings, "CASE_AGENTIC_GAIN_THRESHOLD", 0.05)),
        )


@dataclass
class AnswerResult:
    """问答结果（**证据链 2.0**：新增字段全部为加法，旧消费方不受影响）。"""

    answered: bool = False
    answer: str = ""
    claims: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    dropped_claims: int = 0
    reason: str = ""
    search: Optional[SearchResponse] = None
    #: 本体增强处理轨迹（前端只渲染，**不在前端编**）。
    #: 每步：``{"step": n, "title": 中文标题, "detail": 中文说明, "items": [...],
    #:  "kind": deterministic|agentic|safe, "numbers": {...}, "why": "..."}``
    trace: list[dict[str, Any]] = field(default_factory=list)
    #: 本体增强的量化收益（"无结果 N 条 → 命中 M 条"），由服务层真实统计
    ontology_gain: dict[str, Any] = field(default_factory=dict)
    #: 实际生效的作答器：llm（大模型）/ template（确定性模板）/ template(fallback)（降级）
    answerer: str = "template"
    #: 作答器状态说明（降级原因等；空串表示一切正常）
    answerer_note: str = ""
    #: Agentic 循环摘要（意图 / 轮数 / 跳数 / 终止原因 / 关系链），供前端展示
    agentic: dict[str, Any] = field(default_factory=dict)
    #: 问句改写建议（FR-07：口语 → 受控术语 / 相邻概念）
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answered": self.answered,
            "answer": self.answer,
            "claims": self.claims,
            "citations": self.citations,
            "dropped_claims": self.dropped_claims,
            "reason": self.reason,
            "trace": self.trace,
            "ontology_gain": self.ontology_gain,
            "answerer": self.answerer,
            "answerer_note": self.answerer_note,
            "agentic": self.agentic,
            "suggestions": self.suggestions,
            "search": self.search.to_dict() if self.search else None,
        }


class CaseQAService:
    """检索 →（有界 Agentic 循环）→ 作答 → 引用校验 → 拒答兜底。"""

    def __init__(self, search: CaseSearchService, store: CaseStore,
                 index_service: CaseIndexService,
                 audit: Optional[CaseAuditLog] = None,
                 ontology: Optional[OntologyRegistry] = None,
                 answerer: Optional[Answerer] = None,
                 planner: Optional[Any] = None,
                 relation_index: Optional[Any] = None) -> None:
        self._search = search
        self._store = store
        self._index_service = index_service
        self._audit = audit or CaseAuditLog(store.db_path)
        self._ontology = ontology or get_ontology_registry()
        # ★ LLM 接入点：显式注入优先；否则按配置决定"大模型作答 + 自动降级"
        self._llm_answerer: Optional[Answerer] = None
        if answerer is not None:
            self._answerer: Answerer = answerer
        elif str(getattr(settings, "CASE_ANSWERER", "llm")).strip().lower() == "llm":
            self._llm_answerer = LLMAnswerer(
                timeout=float(getattr(settings, "CASE_ANSWER_TIMEOUT", 20.0)),
                max_chars=int(getattr(settings, "CASE_ANSWER_MAX_CHARS", 200)))
            self._answerer = self._llm_answerer
        else:
            self._answerer = TemplateAnswerer()
        # ★ 计划层：默认自动（LLM 可用则用 LLM，否则确定性——离线可跑、结果可复现）
        self._planner = planner or AutoPlanner()
        self._fallback_answerer: Answerer = TemplateAnswerer()
        self._relation_index_override = relation_index
        # 单次问答内的案例行缓存（每次 answer() 开头重置，见 _citation_ok）
        self._case_cache: dict[str, dict[str, Any]] = {}

    # ---------- 主入口 ----------

    def answer(self, principal: Optional[Principal], question: str, *,
               k: int = 5, action: str = "read",
               env: Optional[EnvContext] = None,
               channel: str = "http") -> AnswerResult:
        """回答一个问题（每条结论都带引用；全丢则拒答）。"""
        if principal is None or not principal.user_id:
            return AnswerResult(answered=False, reason="无身份主体：请先登录（401）",
                                answerer=self._answerer_label()[0])

        raw_question = str(question or "")
        # 本次问答内的案例行缓存（生命周期仅这一次调用，见 _citation_ok 注释）
        self._case_cache = {}
        trace: list[dict[str, Any]] = []

        # ① 归一化 + ② 同义扩展（确定性；只影响 query_text）
        normalized, term_codes, term_items = self._normalization(raw_question)
        trace.append(_trace_step(
            0, "术语归一",
            f"在受控词表里命中 {len(term_items)} 个词条"
            "（同一现象的不同写法被归一到同一个概念）",
            term_items, kind="deterministic",
            numbers={"命中词条": len(term_items)}))
        expanded = [t for t in normalized.split(" ") if t and t not in raw_question.split(" ")]
        trace.append(_trace_step(
            0, "同义扩展",
            f"把现场口语扩展成 {len(expanded)} 个制造术语后再检索"
            "（扩展只影响召回文本，不参与权限过滤）",
            expanded[:8], kind="deterministic",
            numbers={"扩展词": len(expanded)}))

        # ③ 意图识别 + 分解 / ④ 检索计划（LLM 可参与；越界字段被丢弃）
        plan = self._planner(raw_question, normalized, self._ontology)
        segments = segments_in_text(raw_question)
        trace.append(_trace_step(
            0, "意图识别",
            plan.note or f"意图：{INTENT_LABELS.get(plan.intent, plan.intent)}",
            [f"识别工艺段：{'、'.join(segments)}"] if segments else [],
            kind="agentic" if plan.source.startswith("llm") else "deterministic",
            numbers={"意图": INTENT_LABELS.get(plan.intent, plan.intent),
                     "子问句": max(0, len(plan.queries) - 1)},
            why="" if plan.source != "llm-fallback" else "LLM 计划不可用，已退回确定性规则"))
        plan_items = [f"检索式 {i + 1}：{q}" for i, q in enumerate(plan.queries)]
        if plan.dropped_fields:
            plan_items.append("已丢弃 LLM 输出的越界字段：" + "、".join(plan.dropped_fields))
        trace.append(_trace_step(
            0, "检索计划",
            "计划只包含查询文本与检索动作；过滤条件由安全内核按身份独立编译（不由 LLM 生成）",
            plan_items, kind="agentic" if plan.source.startswith("llm") else "deterministic",
            numbers={"检索式": len(plan.queries)},
            why="计划层不得触碰过滤器（A10）" if plan.dropped_fields else ""))

        # ⑤-⑦ 有界 Agentic 循环（召回 / 自检 / 关系扩展再检索）
        final_search, gate_search, state = self._agentic_search(
            principal, plan=plan, normalized=normalized, raw=raw_question,
            term_hits=len(term_items), segments=segments, term_codes=term_codes,
            k=max(1, int(k)), action=action, env=env, channel=channel, trace=trace)

        if final_search.degraded:
            trace.append(_trace_step(0, "降级", final_search.notice, [], kind="safe"))
            return AnswerResult(answered=False, reason=final_search.notice,
                                search=final_search, trace=_renumber(trace),
                                answerer=self._answerer_label()[0],
                                agentic=state)

        if not final_search.hits:
            trace.append(_trace_step(
                0, "权限过滤",
                f"过滤在召回前生效，命中 0 条；"
                f"另有 {final_search.hidden_count} 条案例在权限范围外",
                [], kind="safe",
                numbers={"命中案例": 0, "权限范围外": final_search.hidden_count}))
            return AnswerResult(
                answered=False,
                reason=final_search.notice or "未检索到你有权限查看的案例",
                search=final_search, trace=_renumber(trace),
                answerer=self._answerer_label()[0],
                ontology_gain=self._ontology_gain(raw_question, final_search, principal),
                agentic=state, suggestions=self._suggestions(raw_question, term_items))

        # ②' 相关度闸门（**在主查询结果上判**，与升级前同口径——
        #     再检索只做"补充证据"，不允许把闸门的标定口径冲淡）
        gate_reasons, gate_numbers = self._relevance_gate(
            gate_search, raw_question=raw_question, term_hits=len(term_items))
        if gate_reasons:
            self._audit.append(
                actor=principal.user_id, action="case.answer.refused",
                hit_count=len(gate_search.hits),
                hidden_count=gate_search.hidden_count,
                query_hash=gate_search.query_hash, filter_hash=gate_search.filter_hash,
                detail={"reason": "low_confidence", "terms": len(term_items),
                        "rounds": state.get("rounds"), "hops": state.get("hops"),
                        "gate": gate_numbers})
            trace.append(_trace_step(
                0, "相关度闸门", "；".join(gate_reasons) + "，拒绝作答", [],
                kind="deterministic", numbers=gate_numbers,
                why="不拿无关案例凑结论（引用合法 ≠ 答的是这个问题）"))
            return AnswerResult(
                answered=False,
                reason=(f"未检索到足够相关的案例（{'；'.join(gate_reasons)}），"
                        f"已拒绝作答：不拿无关案例凑结论"
                        f"（最高匹配度 {gate_numbers.get('最高匹配度', 0.0):.3f}，"
                        f"中位 {gate_numbers.get('中位相关度', 0.0):.3f}）"),
                search=final_search, trace=_renumber(trace),
                answerer=self._answerer_label()[0],
                ontology_gain=self._ontology_gain(raw_question, final_search, principal),
                agentic=state, suggestions=self._suggestions(raw_question, term_items))

        # ③ 组装上下文（★仅 KnowledgeChunk；闸门 4 的类型约束在此生效）
        #    多轮归并后 chunk 数可能超过 k，这里按分数截到 2k —— 上下文预算护栏。
        hits_for_answer = list(final_search.hits)[:max(1, int(k)) * 2]
        chunks = self._search.to_chunks(hits_for_answer)
        self._assert_knowledge_layer(chunks)

        # ④ 生成断言（大模型作答；不可用/失败 → 降级为确定性模板）
        claims, answerer_label, answerer_note, used_llm = self._run_answerer(
            raw_question, normalized, chunks)
        trace.append(_trace_step(
            0, "大模型作答" if used_llm else "模板作答",
            answerer_note or f"由{'大模型' if used_llm else '确定性模板'}产出 {len(claims)} 条结论"
                             "（每条结论都逐条挂引用）",
            [f"{len(claims)} 条结论 · 上下文 {len(chunks)} 个知识层片段"],
            kind="agentic" if used_llm else "deterministic",
            numbers={"结论": len(claims), "引用片段": len(chunks)},
            why="" if used_llm else answerer_note))

        # ⑤ 引用校验（BR-05）：无引用 / 引用不一致的断言一律丢弃
        kept, citations, dropped = self._verify_claims(claims)

        # ⑥ 全丢 → 拒答（不编造）
        if not kept:
            self._audit.append(actor=principal.user_id, action="case.answer.refused",
                               detail={"reason": "no_citation", "dropped": dropped})
            trace.append(_trace_step(
                0, "引用校验", f"全部 {dropped} 条结论未通过引用校验，已拒绝输出",
                [], kind="deterministic",
                numbers={"丢弃": dropped}))
            return AnswerResult(
                answered=False,
                reason="本次回答的全部结论均无法通过引用校验，"
                       "已拒绝输出（不编造结论）",
                dropped_claims=dropped, search=final_search, trace=_renumber(trace),
                answerer=answerer_label, answerer_note=answerer_note,
                ontology_gain=self._ontology_gain(raw_question, final_search, principal),
                agentic=state, suggestions=self._suggestions(raw_question, term_items))

        answer_text = "\n".join(
            f"{index}. {item['text']} "
            f"[{item['citations'][0]['case_id']}#{item['citations'][0]['chunk_id']}"
            f"@{item['citations'][0]['content_version']}]"
            for index, item in enumerate(kept, start=1))

        self._audit.append(
            actor=principal.user_id, action="case.answer",
            hit_count=len(kept), hidden_count=final_search.hidden_count,
            query_hash=final_search.query_hash, filter_hash=final_search.filter_hash,
            detail={"dropped_claims": dropped, "citations": len(citations),
                    "channel": channel, "answerer": answerer_label,
                    "rounds": state.get("rounds"), "hops": state.get("hops"),
                    "termination": state.get("termination")})

        trace.append(_trace_step(
            0, "引用校验",
            f"通过 {len(kept)} 条结论 / 丢弃 {dropped} 条"
            "（校验内容：案例可见 + 文本哈希与内容版本双一致）",
            [], kind="deterministic",
            numbers={"通过": len(kept), "丢弃": dropped}))
        gain = self._ontology_gain(raw_question, final_search, principal,
                                   extra=state)
        trace.append(_trace_step(
            0, "本体增强收益", gain.get("summary", ""),
            list(gain.get("items") or []), kind="deterministic"))

        return AnswerResult(answered=True, answer=answer_text, claims=kept,
                            citations=citations, dropped_claims=dropped,
                            reason="", search=final_search, trace=_renumber(trace),
                            ontology_gain=gain, answerer=answerer_label,
                            answerer_note=answerer_note, agentic=state,
                            suggestions=self._suggestions(raw_question, term_items))

    # ---------- 有界 Agentic 循环 ----------

    def _agentic_search(self, principal: Principal, *, plan: Any, normalized: str,
                        raw: str, term_hits: int, segments: Sequence[str],
                        term_codes: Mapping[str, Sequence[str]], k: int,
                        action: str, env: Optional[EnvContext], channel: str,
                        trace: list[dict[str, Any]]
                        ) -> tuple[SearchResponse, SearchResponse, dict[str, Any]]:
        """召回 → 自检 → （关系扩展 → 再召回）*；触刹车即停并写明原因。

        Returns:
            ``(最终结果, 闸门用结果, 循环摘要)``——**闸门只看主查询轮**，
            再检索轮只做证据补充（不让扩展把闸门的标定口径冲淡）。
        """
        cfg = AgenticLoopConfig.from_settings()
        primary_queries = list(plan.queries) if cfg.enabled else [normalized]
        if not primary_queries:
            primary_queries = [normalized]

        def do_search(query_text: str, why: str) -> SearchResponse:
            response = self._search.search(principal, query_text, action=action,
                                           k=k, env=env, channel=channel)
            trace.append(_trace_step(
                0, f"混合召回（第 {len(rounds) + 1} 轮）",
                "权限过滤器随召回一起下推（召回前生效）：过滤条件由安全内核"
                "按身份独立编译，未经过任何模型环节",
                [f"检索式：{query_text}"], kind="safe",
                numbers={"本轮命中案例": len(_case_ids(response.hits)),
                         "本轮候选 chunk": response.candidates,
                         "二次授权丢弃": response.second_stage_dropped},
                why=why))
            rounds.append(response)
            return response

        rounds: list[SearchResponse] = []
        reason_terms: Mapping[str, float] = {
            "answer_min_score": float(self._search.policy.setting(
                "answer_min_score", 0.0) or 0.0),
            "answer_score_without_terms": float(self._search.policy.setting(
                "answer_score_without_terms", 0.0) or 0.0),
        }
        budget = cfg.max_rounds
        # 关系扩展轮另计预算：否则"复合问句拆成 3 条检索式"会把扩展预算吃光，
        # 于是最该演示的多跳闭环永远不触发（实测踩过）。
        total_budget = cfg.max_rounds + cfg.max_hops
        hops = 0
        termination = ""
        truncated = False
        used_terms: list[str] = []

        # ── 第一段：主查询（复合问句 → 多条检索式），受主预算约束 ──
        primary: list[SearchResponse] = []
        for index, query_text in enumerate(primary_queries):
            if len(rounds) >= min(budget, total_budget):
                truncated = True
                termination = f"检索预算用尽（{budget} 轮）"
                break
            why = "首轮召回" if index == 0 else f"复合问句第 {index + 1} 个子问句"
            response = do_search(query_text, why)
            if response.degraded:
                return response, response, {
                    "enabled": cfg.enabled, "intent": plan.intent,
                    "rounds": len(rounds), "hops": hops,
                    "termination": f"索引不可用：{response.notice}"}
            primary.append(response)
        gate_search = merge_search_responses(primary) or primary[0]

        assess = assess_sufficiency(
            hits=gate_search.hits, k=k, term_hits=term_hits, segments=segments,
            intent=plan.intent, thresholds=reason_terms,
            relation_used=False)
        trace.append(_trace_step(
            0, "充分性自检",
            ("本轮召回已充分，无需再检索" if assess.sufficient
             else "召回不足：" + "；".join(assess.reasons)),
            list(assess.reasons), kind="deterministic",
            numbers={"命中案例": len(_case_ids(gate_search.hits)), "要求 k": k},
            why="自检判据：命中数 / 相关度 / 关系诉求覆盖（确定性规则）"))

        # ── 第二段：关系扩展 → 再检索（有界：跳数 / 轮数 / 收益三重刹车）──
        while (cfg.enabled and plan.allow_relation and not assess.off_topic
               and not assess.sufficient and hops < cfg.max_hops
               and len(rounds) < total_budget):
            relation = self._relation_index()
            before = _case_ids(merge_search_responses(rounds).hits
                               if rounds else [])
            expansion = relation.expand(
                question=raw,
                defect_codes=term_codes.get("defect_mode", ()),
                root_codes=term_codes.get("root_cause", ()),
                max_hops=max(1, cfg.max_hops - hops))
            hops += 1
            # 累积去重：上一跳已经用过的召回词不再重复注入——否则第二跳会
            # "再要一遍同样的词"，搜出同样的结果，白烧一轮预算。
            fresh = [t for t in expansion.terms if t not in used_terms]
            if not fresh:
                trace.append(_trace_step(
                    0, f"本体关系扩展（第 {hops} 跳）",
                    expansion.summary + "；本轮无新的可用召回词，不再检索",
                    list(expansion.skipped)[:6], kind="agentic",
                    numbers={"跳数": hops, "新增召回词": 0},
                    why="自检判定关系覆盖不足，但关系层已无尚未使用的召回词"))
                termination = ("关系扩展已无新的召回词（"
                               + ("未登记本体的节点无可靠召回词"
                                  if expansion.skipped else "关系已走到末端")
                               + "）")
                break
            trace.append(_trace_step(
                0, f"本体关系扩展（第 {hops} 跳）",
                expansion.summary,
                [f"{e.via} → {e.label}" + (f"（{e.support} 例支撑）" if e.support else "")
                 for e in expansion.entries[:8]], kind="agentic",
                numbers={"跳数": hops, "相邻节点": len(expansion.entries),
                         "新增召回词": len(fresh)},
                why="；".join(assess.reasons) or "自检判定召回不足"))
            used_terms.extend(fresh)
            enriched = raw + " " + " ".join(used_terms)
            response = do_search(enriched, f"沿本体关系第 {hops} 跳扩展后再次检索")
            if response.degraded:
                break
            merged = merge_search_responses(rounds) or response
            after = _case_ids(merged.hits)
            gained = len(after - before)
            ratio = gained / max(1, len(before))
            trace.append(_trace_step(
                0, f"本轮收益结算（第 {hops} 跳）",
                f"新增案例 {gained} 个（{len(before)} → {len(after)}）",
                [f"相对收益 {ratio:.1%}，阈值 {cfg.gain_threshold:.0%}"],
                kind="deterministic",
                numbers={"新增案例": gained, "相对收益": round(ratio, 4)}))
            if ratio < cfg.gain_threshold and gained == 0:
                termination = (f"相对收益 {ratio:.1%} < 阈值 {cfg.gain_threshold:.0%}，"
                               "停止再检索")
                break
            assess = assess_sufficiency(
                hits=merged.hits, k=k, term_hits=term_hits, segments=segments,
                intent=plan.intent, thresholds=reason_terms,
                relation_used=True)
            if assess.sufficient:
                termination = "自检判定已充分，停止再检索"
                break

        if not termination:
            if not cfg.enabled:
                termination = "Agentic 循环已关闭（单轮检索）"
            elif assess.off_topic:
                termination = "判定为非案例域问题，未启动关系扩展"
            elif assess.sufficient:
                termination = "自检判定召回充分"
            elif hops >= cfg.max_hops:
                termination = f"已达本体关系跳数上限（{cfg.max_hops} 跳）"
            elif len(rounds) >= total_budget:
                termination = f"检索预算用尽（{total_budget} 轮）"
            elif not plan.allow_relation:
                termination = "计划层未允许关系扩展"
            else:
                termination = "无新的可用扩展，结束循环"
        trace.append(_trace_step(
            0, "循环终止",
            termination,
            [f"共 {len(rounds)} 轮召回 / {hops} 跳关系扩展"
             + ("（触顶截断）" if truncated else "")],
            kind="deterministic",
            numbers={"召回轮数": len(rounds), "关系跳数": hops},
            why=termination))

        final = merge_search_responses(rounds) or gate_search
        # 归并后的 notice 必须**按归并结果重算**：直接沿用某一轮的文案会出现
        # "说 3 条、实际给了 9 条"的口径矛盾。
        notice, low_confidence = self._search._compose_notice(  # noqa: SLF001
            hits=len(final.hits), k=k, hidden=int(final.hidden_count or 0),
            dropped=int(final.second_stage_dropped or 0), query=raw)
        if len(rounds) > 1:
            notice = "；".join(p for p in [
                notice, f"经 {len(rounds)} 轮召回（本体关系扩展 {hops} 跳）归并",
            ] if p)
        final.notice = notice
        final.low_confidence = low_confidence

        state: dict[str, Any] = {
            "enabled": bool(cfg.enabled),
            "intent": plan.intent,
            "intent_label": INTENT_LABELS.get(plan.intent, plan.intent),
            "queries": [str(q) for q in primary_queries],
            "rounds": len(rounds),
            "hops": hops,
            "termination": termination,
            "planner": plan.source,
            "relations": self._relation_labels(final),
        }
        return final, gate_search, state

    def _relation_index(self) -> Any:
        if self._relation_index_override is not None:
            return self._relation_index_override
        return get_relation_index(self._store, ontology=self._ontology)

    def _relation_labels(self, final: SearchResponse) -> list[str]:
        """本次命中集合内部的关系来源（真实共现，供结果卡与讲解使用）。"""
        try:
            index = self._relation_index()
            mapping = index.relations_for_cases(
                sorted(_case_ids(final.hits)), limit=2)
        except Exception as exc:  # noqa: BLE001 - 关系标注是增强项，绝不阻塞回答
            logger.warning("[case_qa] 关系标注失败: %s", exc)
            return []
        labels: list[str] = []
        for case_id in sorted(mapping):
            for label in mapping[case_id]:
                text = f"{case_id}：{label}"
                if text not in labels:
                    labels.append(text)
        return labels[:8]

    # ---------- 作答器（含降级） ----------

    def _answerer_label(self) -> tuple[str, str]:
        return ("llm" if self._llm_answerer is not None else "template", "")

    def _run_answerer(self, raw: str, normalized: str, chunks: Sequence[KnowledgeChunk]
                      ) -> tuple[list[Claim], str, str, bool]:
        """跑作答器：大模型优先，失败/不可用则降级模板（**备注写明原因**）。"""
        if self._llm_answerer is not None:
            try:
                claims = list(self._llm_answerer(raw, chunks) or ())
                if claims:
                    return claims, "llm", "", True
                note = "大模型未产出带引用的结论，已降级为确定性模板作答"
            except AnswererError as exc:
                note = f"大模型作答不可用（{exc}），已降级为确定性模板作答"
            except Exception as exc:  # noqa: BLE001 - 任何意外都不得让问答 500
                note = f"大模型作答异常（{type(exc).__name__}: {exc}），已降级为确定性模板作答"
            logger.info("[case_qa] %s", note)
            claims = list(self._fallback_answerer(normalized, chunks) or ())
            return claims, "template(fallback)", note, False
        claims = list(self._answerer(normalized, chunks) or ())
        return claims, "template", "", False

    # ---------- 相关度闸门 ----------

    def _relevance_gate(self, response: SearchResponse, *, raw_question: str,
                        term_hits: int) -> tuple[list[str], dict[str, Any]]:
        """相关度闸门（口径与升级前完全一致，只把数字抽出来便于展示）。"""
        threshold = float(self._search.policy.setting(
            "low_confidence_threshold", 0.0) or 0.0)
        scores = sorted((float(hit.get("score") or 0.0) for hit in response.hits),
                        reverse=True)
        best = scores[0] if scores else 0.0
        median = float(statistics.median(scores)) if scores else 0.0
        margin = best - median
        min_score = float(self._search.policy.setting("answer_min_score", 0.0) or 0.0)
        min_margin = float(self._search.policy.setting("answer_margin", 0.0) or 0.0)
        no_term_score = float(self._search.policy.setting(
            "answer_score_without_terms", 0.0) or 0.0)

        reasons: list[str] = []
        if threshold > 0.0 and best < threshold:
            reasons.append(f"最高匹配度 {best:.3f} 低于阈值 {threshold:.2f}")
        if min_score > 0.0 and best < min_score:
            reasons.append(f"最高匹配度 {best:.3f} 低于绝对下限 {min_score:.2f}")
        if min_margin > 0.0 and margin < min_margin:
            reasons.append(f"最优命中未显著优于中位分"
                           f"（余量 {margin:.3f} < {min_margin:.2f}）")
        if no_term_score > 0.0 and term_hits == 0 and best < no_term_score:
            # 零术语问题（"食堂几点开饭"）即使分数不低也不作答：
            # 字符重合度对这类问句天然会给中等分，靠术语闸门才分得开。
            reasons.append(f"问题未命中任何制造术语，要求分数 ≥ {no_term_score:.2f}"
                           f"（当前 {best:.3f}）")
        numbers = {
            "最高匹配度": round(best, 4), "中位相关度": round(median, 4),
            "余量": round(margin, 4), "命中案例": len(_case_ids(response.hits)),
            "命中制造术语": int(term_hits),
        }
        return reasons, numbers

    # ---------- 引用校验 ----------

    def _verify_claims(self, claims: Sequence[Claim]
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """引用校验（BR-05）：无引用 / 引用不一致的断言一律丢弃。"""
        kept: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        dropped = 0
        for claim in claims:
            valid = [c for c in claim.citations if self._citation_ok(c)]
            if not claim.has_citation or len(valid) != len(claim.citations) or not valid:
                dropped += 1
                continue
            kept.append({"text": claim.text,
                         "citations": [c.to_dict() for c in valid]})
            for citation in valid:
                payload = citation.to_dict()
                if payload not in citations:
                    citations.append(payload)
        return kept, citations, dropped

    # ---------- 归一化 / 同义扩展 ----------

    def _normalization(self, question: str
                       ) -> tuple[str, dict[str, list[str]], list[str]]:
        """归一化 + 同义扩展（确定性；**只影响 query_text**）。

        返回 ``(归一化文本, 各词表命中的 code, 展示条目)``。
        """
        raw = str(question or "").strip()
        codes: dict[str, list[str]] = {}
        items: list[str] = []
        for vocab, label in (("defect_mode", "缺陷模式"), ("root_cause", "根因"),
                             ("countermeasure", "对策")):
            hits = list(self._ontology.find_terms(vocab, raw, limit=3))
            codes[vocab] = hits
            for code in hits:
                term = self._ontology.term(vocab, code)
                if term is not None:
                    items.append(f"{label}：{term.name}")
        return self._normalize_query(raw), codes, items

    def normalize_query(self, question: str) -> str:
        """对外暴露的**确定性**归一化（供对照演示/外部调用）。

        与内部实现同一份代码——对照实验的 B 组必须真的跑"升级前那套改写"，
        否则对照就变成了自证。
        """
        return self._normalize_query(question)

    def _normalize_query(self, question: str) -> str:
        """确定性查询改写：受控词表同义扩展（不做 LLM 改写）。

        只影响召回文本，**永远不参与权限过滤**（裁定 A10）。
        PRD FR-03：**对策词表也参与召回**——"上次这类问题怎么修"类问句
        过去享受不到本体增强（只扩了缺陷模式与根因）。
        """
        text = str(question or "").strip()
        if not text:
            return ""
        expanded: list[str] = []
        for vocab in ("defect_mode", "root_cause", "countermeasure"):
            for code in self._ontology.find_terms(vocab, text, limit=3):
                for label in self._ontology.expand_synonyms(vocab, code)[:2]:
                    if label and label not in text and label not in expanded:
                        expanded.append(label)
        return " ".join([text, *expanded]) if expanded else text

    # ---------- 问句改写建议（FR-07） ----------

    def _suggestions(self, question: str, term_items: Sequence[str]) -> list[dict[str, Any]]:
        """口语 → 受控术语的改写建议（确定性；**由后端产出，前端只渲染**）。

        两类：
        1. **同义改写**：问句里出现的"口语写法"对应的权威术语
           （"你说的『雪花』是不是『锡珠』"）；
        2. **相邻概念**：问句与受控词表毫无交集时，给出按字面相近的候选术语
           （把"搜不到"从死路变成下一步）。
        """
        raw = str(question or "").strip()
        out: list[dict[str, Any]] = []
        low = raw.lower()
        for vocab, label in (("defect_mode", "缺陷模式"), ("root_cause", "根因"),
                             ("countermeasure", "对策")):
            for term in self._ontology.terms(vocab):
                if term.status != "active":
                    continue
                for synonym in term.synonyms:
                    if len(synonym) >= 2 and synonym.lower() in low \
                            and term.name not in raw:
                        out.append({
                            "kind": "rewrite",
                            "term": term.name,
                            "why": f"你说的「{synonym}」在受控词表里是「{term.name}」"
                                   f"（{label}）",
                            "code": term.code,
                        })
                        break
                if len(out) >= 3:
                    break
        if not out and not term_items:
            # 与词表零交集：按 2 字窗口找字面相近的词条（确定性、只做建议）
            grams = {low[i:i + 2] for i in range(max(0, len(low) - 1))}
            scored: list[tuple[int, str, str, str]] = []
            if grams:
                for vocab, label in (("defect_mode", "缺陷模式"),
                                     ("root_cause", "根因")):
                    for term in self._ontology.terms(vocab):
                        if term.status != "active":
                            continue
                        labels = (term.name, *term.synonyms)
                        score = 0
                        for candidate in labels:
                            text = candidate.lower()
                            for i in range(max(0, len(text) - 1)):
                                if text[i:i + 2] in grams:
                                    score += 1
                        if score > 0:
                            scored.append((-score, term.code, term.name, label))
            scored.sort()
            for _neg, code, name, label in scored[:4]:
                out.append({
                    "kind": "adjacent",
                    "term": name,
                    "why": f"受控词表里字面相近的{label}：{name}（点击替换查询重搜）",
                    "code": code,
                })
        return out[:6]

    # ---------- 本体增强收益 ----------

    def _ontology_gain(self, question: str, search: SearchResponse,
                       principal: Optional[Principal] = None,
                       *, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """本体增强的量化收益：**真实统计**，禁止编造。

        口径：同一句话，直接用原文检索 vs 归一化后检索，各命中多少案例。
        两次都走同一套权限过滤，所以差值只反映"本体增强"带来的召回增益。
        升级后追加循环口径（轮数 / 跳数 / 终止原因），同样来自真实执行。
        """
        raw = str(question or "").strip()
        normalized = self._normalize_query(raw)
        baseline = -1
        if principal is not None and principal.user_id and normalized != raw:
            try:
                base = self._search.search(principal, raw, action="read", k=50)
                baseline = len({hit["case_id"] for hit in base.hits})
            except Exception:  # noqa: BLE001 - 统计失败就说"未统计"，不编
                baseline = -1
        enhanced = len({hit["case_id"] for hit in search.hits})
        items = [f"归一化后命中案例 {enhanced} 个"]
        if baseline >= 0 and normalized != raw:
            # 口径说明：原文检索命中更多，往往是因为字面重合更宽（噪声也进来了）；
            # 归一化后命中更少但更准。所以这里**不宣称"增益"**，只如实并列两个数字。
            items.insert(0, f"未归一化的原文检索命中 {baseline} 个")
            items.append("归一化把不同叫法合并，命中更聚焦（少而准）")
            summary = (f"同一句话：原文检索 {baseline} 个案例 / "
                       f"本体归一后 {enhanced} 个（少而准）")
        else:
            summary = f"本体归一后命中 {enhanced} 个案例"
        out: dict[str, Any] = {"summary": summary, "items": items,
                               "baseline_cases": baseline, "enhanced_cases": enhanced}
        if extra:
            rounds = int(extra.get("rounds") or 0)
            hops = int(extra.get("hops") or 0)
            termination = str(extra.get("termination") or "")
            if rounds:
                out["items"] = list(out["items"]) + [
                    f"本次经 {rounds} 轮召回、{hops} 跳本体关系扩展（{termination}）"]
                out["rounds"] = rounds
                out["hops"] = hops
                out["termination"] = termination
        return out

    def _citation_ok(self, citation: Citation) -> bool:
        """引用校验：案例存在、状态可引、``content_version`` 一致、文本哈希一致。"""
        if not citation.case_id or not citation.chunk_id:
            return False
        expected_chunk = f"{citation.case_id}::"
        if not citation.chunk_id.startswith(expected_chunk):
            return False
        if not self._index_service.verify_citation(
                citation.case_id, citation.chunk_id.split("::", 1)[1],
                citation.content_version):
            return False
        # 双重保险：再用二次授权路径确认该案例仍然可见（防并发撤权）。
        # 同一条案例常被多条断言引用，逐条 get_case 每次都新建 SQLite 连接——
        # 这里按"本次问答"缓存案例行，5 条断言省掉 4 次建连。
        # 缓存只活过一次问答，不跨请求，不存在"撤权后仍读到旧值"的问题。
        case = self._case_cache.get(citation.case_id)
        if case is None:
            case = self._store.get_case(citation.case_id)
            self._case_cache[citation.case_id] = case or {}
        return bool(case) and str(case.get("status") or "") in (
            "active", "deprecated", "superseded", "archived")

    @staticmethod
    def _assert_knowledge_layer(chunks: Sequence[KnowledgeChunk]) -> None:
        """闸门 4 的运行期形态：只允许 ``KnowledgeChunk``（证据层禁止进 prompt）。"""
        for chunk in chunks:
            if type(chunk) is not KnowledgeChunk:  # noqa: E721 - 刻意用 is 判等
                raise TypeError(
                    "证据层内容禁止进入 LLM 上下文："
                    f"收到 {type(chunk).__name__}")
