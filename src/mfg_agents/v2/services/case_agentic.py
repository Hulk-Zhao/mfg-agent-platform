"""有界 Agentic 检索：计划层 + 充分性评估 + 结果归并（PRD FR-02 / FR-03）。

## 这一层解决什么

升级前 ``CaseQAService.answer`` 是**线性管道**：归一 → 检索一次 → 闸门 → 组文 →
校验 → 输出。一次召回就是终局，复杂问句（跨段追溯、复合描述）只能靠"整句碰运气"。

本模块给出三件可组合的机件，让同一条链路变成**有界闭环**：

1. ``DeterministicPlanner`` / ``LLMPlanner``：判断问句意图、拆分子问句、产出检索计划；
2. ``assess_sufficiency``：**确定性**自检——命中够不够、关系诉求有没有被覆盖；
3. ``merge_search_responses``：多轮结果归并（同 chunk 取最高分，多轮计数汇总）。

## 红线（每次改动都要自检）

- 计划对象里**没有、也永远不会有 filter 字段**：过滤条件只由 ``AclCompiler``
  按身份编译（裁定 A10）。LLM 计划器只被允许产出"查询文本 + 动作"，任何
  多余字段（尤其 filter/expr/AST 之类）一律丢弃并在 trace 里记"已丢弃越界字段"。
- **每一轮召回都必须完整走安全内核**（过滤器下推 + 二次授权）。本模块不做
  任何"合并成一次查询"的优化——那等于给闭环开了一条绕过二次授权的快路径。
- 循环**必须有刹车**：跳数 ≤ ``max_hops``、轮数 ≤ ``max_rounds``、
  相对收益 < ``gain_threshold`` 即停；触顶要如实写明终止原因。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from ..contracts.ontology import OntologyRegistry
from .case_relation import SEGMENT_QUESTION_TERMS, segments_in_text

logger = logging.getLogger(__name__)

__all__ = [
    "RetrievalPlan",
    "Planner",
    "DeterministicPlanner",
    "Sufficiency",
    "assess_sufficiency",
    "merge_search_responses",
    "INTENT_LABELS",
    "SPLIT_TOKENS",
]

#: 意图 → 中文标签（trace 展示口径，唯一来源）。
INTENT_LABELS: dict[str, str] = {
    "single": "单点查询",
    "composite": "复合查询",
    "cross_segment": "跨段追溯",
    "countermeasure": "对策复用",
}

#: 复合问句的切分标记（确定性；只在"同一句里问了两件事"时才拆）。
#: 注意**刻意不含裸逗号**：中文问句里"整机段发现的异响，能追到插件段的波峰焊吗"
#: 是一个整体问题，按逗号切开会把"追到波峰焊吗"这种元问句也当成检索式，
#: 白白吃掉检索预算（实测把本可以走 2 跳关系的问题挤成了单轮）。
SPLIT_TOKENS: tuple[str, ...] = ("；", ";", "另外", "以及", "还有",
                                 "并且", "同时", "而且")

#: 跨段诉求的触发词（问句侧识别，确定性）。
CROSS_WORDS: tuple[str, ...] = ("跨段", "别的段", "其他段", "其它段", "追到",
                                "追溯到", "追溯", "关联到", "同根因", "跨工序")

#: 对策复用诉求的触发词。
COUNTER_WORDS: tuple[str, ...] = ("怎么修", "怎么处理", "怎么处置", "对策",
                                  "怎么办", "如何解决", "如何处置")


@dataclass(frozen=True)
class RetrievalPlan:
    """检索计划（**刻意不含 filter / AST / expr 字段**）。"""

    intent: str = "single"
    #: 实际用于检索的查询文本（第一句恒为归一化后的原问句）
    queries: tuple[str, ...] = ()
    #: 是否允许沿本体关系再检索（由评估层最终决定是否真的用）
    allow_relation: bool = True
    #: 计划说明（中文，直接进 trace）
    note: str = ""
    #: 计划器来源：deterministic / llm / llm-fallback
    source: str = "deterministic"
    #: 被丢弃的越界字段（LLM 计划器若吐出 filter 之类脏字段，记在这里）
    dropped_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"intent": self.intent,
                "intent_label": INTENT_LABELS.get(self.intent, self.intent),
                "queries": list(self.queries), "allow_relation": bool(self.allow_relation),
                "note": self.note, "source": self.source,
                "dropped_fields": list(self.dropped_fields)}


class Planner(Protocol):
    """计划器协议（生产可注入 LLM 实现）。"""

    def __call__(self, question: str, normalized: str,
                 ontology: OntologyRegistry) -> RetrievalPlan:  # pragma: no cover
        ...


def _split_question(text: str, limit: int = 3) -> list[str]:
    """复合问句切分（确定性；切不出就返回原句）。"""
    parts = [text]
    for token in SPLIT_TOKENS:
        expanded: list[str] = []
        for part in parts:
            pieces = [p.strip() for p in part.split(token)]
            expanded.extend([p for p in pieces if len(p) >= 2])
        parts = expanded or parts
    out: list[str] = []
    for part in parts:
        if part and part not in out:
            out.append(part)
    return out[:max(1, int(limit))]


class DeterministicPlanner:
    """确定性计划器（**默认实现，离线可跑、结果可复现**）。

    它不"猜"语义：意图由触发词与段识别决定，切分由标点/连接词决定，
    因此同一问句永远得到同一计划——这是可回归、可讲解的前提。
    """

    def __call__(self, question: str, normalized: str,
                 ontology: OntologyRegistry) -> RetrievalPlan:
        q = str(question or "").strip()
        segments = segments_in_text(q)
        counter_hits = ontology.find_terms("countermeasure", q, limit=2)
        cross = (len(segments) >= 2 or any(w in q for w in CROSS_WORDS))
        if cross:
            intent = "cross_segment"
        elif counter_hits and any(w in q for w in COUNTER_WORDS):
            intent = "countermeasure"
        else:
            intent = "single"
        parts = _split_question(q)
        if intent == "single" and len(parts) > 1:
            intent = "composite"
        queries = [normalized] if intent != "composite" else [
            (normalized if p == q else p) for p in parts]
        notes: list[str] = [f"意图：{INTENT_LABELS.get(intent, intent)}"]
        if segments:
            notes.append("识别到工艺段：" + "、".join(segments))
        if len(queries) > 1:
            notes.append(f"复合问句拆为 {len(queries)} 个子问句分别召回")
        if counter_hits:
            notes.append(f"命中对策词条 {len(counter_hits)} 个")
        return RetrievalPlan(intent=intent, queries=tuple(queries),
                             allow_relation=True, note="；".join(notes),
                             source="deterministic")


@dataclass
class Sufficiency:
    """充分性评估结论（确定性，进入 trace 的"自检"步骤）。"""

    sufficient: bool = True
    #: 缺口描述（中文条目，直接进 trace）
    reasons: list[str] = field(default_factory=list)
    #: 是否有"关系诉求"（问句在问跨段/关系类问题）
    relation_requested: bool = False
    #: 是否判定为"无关问题"（零术语且分数低于术语闸门）——**不许再检索**
    off_topic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"sufficient": bool(self.sufficient), "reasons": list(self.reasons),
                "relation_requested": bool(self.relation_requested),
                "off_topic": bool(self.off_topic)}


def assess_sufficiency(*, hits: Sequence[Mapping[str, Any]], k: int,
                       term_hits: int, segments: Sequence[str],
                       intent: str, thresholds: Mapping[str, float],
                       relation_used: bool = False) -> Sufficiency:
    """自检：这一轮召回够不够用？缺什么？（确定性规则，可断言、可复盘）

    关键设计一：**"无关问题"必须被识别出来并禁止扩展**。否则关系扩展会把
    "食堂几点开饭"也扩到某个制造段上，把拒答变成一本正经的胡说——
    引用合法 ≠ 答的是这个问题（既有闸门的原话）。

    关键设计二：**"关系诉求"独立成一条判据**。问句在问跨段/追溯时，
    命中数够多也不代表答对了问题——"整机段的异响能追到插件段吗"若只返回
    整机段的案例，用户会立刻发现没答到点上。因此只要问句含关系诉求、
    且尚未走过关系扩展，一律判"不充分"，由循环去走关系（PRD FR-03）。
    """
    out = Sufficiency(sufficient=True)
    best = max((float(h.get("score") or 0.0) for h in hits), default=0.0)
    min_score = float(thresholds.get("answer_min_score", 0.0) or 0.0)
    no_term_score = float(thresholds.get("answer_score_without_terms", 0.0) or 0.0)

    # 关系诉求：① 问句里出现 ≥2 个工艺段 ② 意图被判为跨段追溯。
    # 刻意不含"复合查询"——复合问句只是"问了两件事"，不等于"在问关系"，
    # 否则每条并列问句都要白走一轮关系扩展。
    out.relation_requested = bool(len(segments) >= 2 or intent == "cross_segment")
    if not hits:
        out.sufficient = False
        out.reasons.append("本轮零命中")
    if term_hits == 0 and best < no_term_score:
        # 术语闸门的镜像：没有制造术语、分数也不够 → 这条问句不属于案例域
        out.sufficient = False
        out.off_topic = True
        out.reasons.append("未命中任何制造术语且相关度不足：判定为非案例域问题，"
                           "不再扩展检索")
        return out
    if min_score > 0.0 and best < min_score:
        out.sufficient = False
        out.reasons.append(f"最高相关度 {best:.3f} 低于绝对下限 {min_score:.2f}")
    if hits and len({str(h.get("case_id") or "") for h in hits}) < max(1, int(k)):
        if out.relation_requested:
            out.sufficient = False
            out.reasons.append(f"命中案例少于 k={k} 且问句含关系诉求")
        elif intent == "composite":
            out.sufficient = False
            out.reasons.append(f"命中案例少于 k={k}（复合问句需更宽的召回）")
    if out.relation_requested and not relation_used and not out.off_topic:
        out.sufficient = False
        out.reasons.append("问句含关系诉求（跨段/追溯），尚未沿本体关系扩展")
    if out.sufficient:
        out.reasons.append(f"命中 {len(hits)} 条、最高相关度 {best:.3f}：判定为充分")
    return out


def merge_search_responses(rounds: Sequence[Any],
                           *, same_case_limit: int = 2) -> Optional[Any]:
    """把多轮 ``SearchResponse`` 归并成一份（同 chunk 取最高分）。

    为什么必须归并而不是"用最后一轮"：再检索的目的是**补充**，不是替换。
    若用最后一轮覆盖，第一轮的强命中会被丢掉，用户会觉得"越搜越差"。

    权限侧说明：每一轮都是独立通过安全内核（过滤器下推 + 二次授权）的结果，
    归并只是在**都合法**的集合上取并集，因此不构成任何越权面。
    """
    from .case_search import SearchResponse  # 循环导入规避（同层服务互相引用）

    usable = [r for r in rounds or () if r is not None and not r.degraded]
    if not usable:
        return rounds[0] if rounds else None

    best: dict[str, dict[str, Any]] = {}
    for response in usable:
        for hit in response.hits or ():
            chunk_id = str(hit.get("chunk_id") or "")
            if not chunk_id:
                continue
            current = best.get(chunk_id)
            if current is None or float(hit.get("score") or 0.0) > float(
                    current.get("score") or 0.0):
                best[chunk_id] = dict(hit)
    hits = sorted(best.values(), key=lambda h: (-float(h.get("score") or 0.0),
                                               str(h.get("case_id") or ""),
                                               str(h.get("chunk_id") or "")))
    # 同案例去重口径与单轮一致（按 chunk_type 优先级取前 N 条）——归并后可能
    # 出现"同一案例来自不同轮"的情况，需要重新收敛一次。
    grouped: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        grouped.setdefault(str(hit.get("case_id") or ""), []).append(hit)
    from .case_index import CHUNK_TYPE_PRIORITY

    limited: list[dict[str, Any]] = []
    for group in grouped.values():
        group.sort(key=lambda h: (CHUNK_TYPE_PRIORITY.get(str(h.get("chunk_type")), 9),
                                 -float(h.get("score") or 0.0),
                                 str(h.get("chunk_id") or "")))
        limited.extend(group[:max(1, int(same_case_limit))])
    limited.sort(key=lambda h: (-float(h.get("score") or 0.0),
                                str(h.get("case_id") or ""),
                                str(h.get("chunk_id") or "")))

    first = usable[0]
    last = usable[-1]
    restricted: dict[str, dict[str, Any]] = {}
    for response in usable:
        for row in response.restricted or ():
            restricted.setdefault(str(row.get("case_id") or ""), row)
    dist: dict[str, int] = {}
    for hit in limited:
        key = str(hit.get("subcategory") or "") or "other"
        dist[key] = dist.get(key, 0) + 1
    return SearchResponse(
        hits=limited,
        restricted=list(restricted.values()),
        category_dist=dist,
        hidden_count=max([int(r.hidden_count or 0) for r in usable] or [0]),
        degraded=False,
        notice=last.notice,
        low_confidence=all(bool(r.low_confidence) for r in usable),
        second_stage_dropped=sum(int(r.second_stage_dropped or 0) for r in usable),
        action=first.action,
        query_hash=first.query_hash,
        filter_hash=first.filter_hash,
        candidates=sum(int(r.candidates or 0) for r in usable),
        backend=first.backend,
        acl_denied=any(bool(r.acl_denied) for r in usable),
        denied_code=first.denied_code,
        filter_explain=list(first.filter_explain or []),
    )


def build_relation_terms(expansion: Any) -> tuple[list[str], list[str]]:
    """关系扩展 → (召回词, 已使用的关系说明)。空扩展 → 两个空列表。"""
    if expansion is None:
        return [], []
    terms = [str(t) for t in (expansion.terms or ()) if str(t).strip()]
    notes = [f"{e.via}→{e.label}" + (f"（{e.support} 例）" if e.support else "")
             for e in (expansion.entries or ())[:6]]
    return terms, notes


def relation_wanted(question: str, *, intent: str,
                    segment_terms: Mapping[str, str] | None = None) -> bool:
    """问句是否在问"关系"（跨段/追溯）——决定 trace 里是否要求关系覆盖。"""
    q = str(question or "")
    mapping = segment_terms or SEGMENT_QUESTION_TERMS
    segs = set()
    for term, group in mapping.items():
        if term and term in q.lower():
            segs.add(group)
    return bool(len(segs) >= 2 or any(w in q for w in CROSS_WORDS)
                or intent in ("cross_segment", "composite"))
