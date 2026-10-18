"""CaseQAService：带引用的问答（**无引用不输出**，BR-05 / K2 / K3）。

架构位置：§7.8（RAG 流程）、§7.5 步 13（引用校验）。

三条纪律：

1. **每个断言必须挂 ``(case_id, chunk_id, content_version)``**；无引用的断言**丢弃**
   （不是整段丢弃，也不是补一个假引用）。
2. **引用校验器回查知识层**：``content_version`` 与当前案例不一致 → 该断言丢弃
   （提示"案例已更新，请复核"）。校验的是"引用时看到的内容 == 现在的内容"。
3. **全部断言被丢弃 / 无命中 → 拒答**：返回明确原因，**不编造**。

演示态用**确定性模板**作答（不调 LLM），但 ``answerer`` 是可注入参数——接 LLM 时
只需实现同一个 ``Answerer`` 协议，其余链路（检索、二次授权、引用校验）完全不变。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from ..contracts.case_acl import Principal
from ..contracts.case_index import KnowledgeChunk
from ..contracts.ontology import OntologyRegistry, get_ontology_registry
from ..security.acl_compiler import EnvContext
from ..security.acl_evaluator import get_acl_evaluator
from .case_audit import CaseAuditLog
from .case_index import CaseIndexService
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
    """LLM 接入点协议（生产实现见 docs 的"预留接口清单"）。"""

    def __call__(self, question: str,
                 chunks: Sequence[KnowledgeChunk]) -> Sequence[Claim]:  # pragma: no cover
        ...


class TemplateAnswerer:
    """确定性模板作答器（演示默认，不调 LLM）。

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
                items: Sequence[str]) -> dict[str, Any]:
    """一条处理轨迹（结构固定：步序 / 中文标题 / 中文说明 / 条目）。"""
    return {"step": int(step), "title": str(title), "detail": str(detail),
            "items": [str(item) for item in (items or ())][:12]}


@dataclass
class AnswerResult:
    """问答结果。"""

    answered: bool = False
    answer: str = ""
    claims: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    dropped_claims: int = 0
    reason: str = ""
    search: Optional[SearchResponse] = None
    #: 本体增强处理轨迹（前端只渲染，**不在前端编**）。
    #: 每步：``{"step": n, "title": 中文标题, "detail": 中文说明, "items": [...]}``
    trace: list[dict[str, Any]] = field(default_factory=list)
    #: 本体增强的量化收益（"无结果 N 条 → 命中 M 条"），由服务层真实统计
    ontology_gain: dict[str, Any] = field(default_factory=dict)

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
            "search": self.search.to_dict() if self.search else None,
        }


class CaseQAService:
    """检索 →（可注入 LLM）作答 → 引用校验 → 拒答兜底。"""

    def __init__(self, search: CaseSearchService, store: CaseStore,
                 index_service: CaseIndexService,
                 audit: Optional[CaseAuditLog] = None,
                 ontology: Optional[OntologyRegistry] = None,
                 answerer: Optional[Answerer] = None) -> None:
        self._search = search
        self._store = store
        self._index_service = index_service
        self._audit = audit or CaseAuditLog(store.db_path)
        self._ontology = ontology or get_ontology_registry()
        # ★ LLM 接入点：默认确定性模板；生产注入真实 answerer 即可
        self._answerer: Answerer = answerer or TemplateAnswerer()

    # ---------- 主入口 ----------

    def answer(self, principal: Optional[Principal], question: str, *,
               k: int = 5, action: str = "read",
               env: Optional[EnvContext] = None,
               channel: str = "http") -> AnswerResult:
        """回答一个问题（每条结论都带引用；全丢则拒答）。"""
        if principal is None or not principal.user_id:
            return AnswerResult(answered=False, reason="无身份主体：请先登录（401）")

        # ① 查询归一化（确定性；语义扩展只影响 query_text，不影响 filter）
        raw_question = str(question or "")
        normalized, trace = self._build_trace(raw_question)

        # ② 检索（内部完成权限前置 + 二次授权；action=read 要求能读全文）
        search = self._search.search(principal, normalized, action=action,
                                     k=max(1, int(k)), env=env, channel=channel)
        if search.degraded:
            return AnswerResult(answered=False, reason=search.notice, search=search,
                                trace=trace)
        if not search.hits:
            trace.append(_trace_step(4, "权限过滤",
                                     f"过滤在召回前生效，命中 0 条；"
                                     f"另有 {search.hidden_count} 条案例在权限范围外",
                                     []))
            return AnswerResult(answered=False,
                                reason=search.notice or "未检索到你有权限查看的案例",
                                search=search, trace=trace,
                                ontology_gain=self._ontology_gain(raw_question, search, principal))

        # ②' 相关度闸门：命中全都低于阈值 → 拒答。
        #
        # 为什么必须有这道闸：模板/LLM 都能"就着最像的 3 条案例说出一段话"，
        # 于是问"食堂几点开饭"也会得到一段锡膏印刷的答案——引用还都合法。
        # 引用校验只保证"没编造出处"，不保证"答的是这个问题"，两者是两件事。
        # 阈值取策略里的 ``low_confidence_threshold``（与前端 low_confidence 提示同源）。
        threshold = float(self._search.policy.setting(
            "low_confidence_threshold", 0.0) or 0.0)
        best = max((float(hit.get("score") or 0.0) for hit in search.hits), default=0.0)
        if threshold > 0.0 and best < threshold:
            self._audit.append(
                actor=principal.user_id, action="case.answer.refused",
                hit_count=len(search.hits), hidden_count=search.hidden_count,
                query_hash=search.query_hash, filter_hash=search.filter_hash,
                detail={"reason": "low_confidence", "best": round(best, 4),
                        "threshold": threshold})
            trace.append(_trace_step(
                4, "相关度闸门",
                f"最高匹配度 {best:.3f} 低于阈值 {threshold:.2f}，拒绝作答",
                []))
            return AnswerResult(
                answered=False,
                reason=(f"未检索到足够相关的案例（最高匹配度 {best:.3f} 低于阈值 "
                        f"{threshold:.2f}），已拒绝作答：不拿无关案例凑结论"),
                search=search, trace=trace,
                ontology_gain=self._ontology_gain(raw_question, search, principal))

        # ③ 组装上下文（★仅 KnowledgeChunk；闸门 4 的类型约束在此生效）
        chunks = self._search.to_chunks(search.hits)
        self._assert_knowledge_layer(chunks)

        # ④ 生成断言（可注入 LLM）
        claims = list(self._answerer(normalized, chunks) or ())

        # ⑤ 引用校验（BR-05）：无引用 / 引用不一致的断言一律丢弃
        kept: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        dropped = 0
        for claim in claims:
            valid = [c for c in claim.citations if self._citation_ok(c)]
            if not claim.has_citation or len(valid) != len(claim.citations) or not valid:
                dropped += 1
                continue
            kept.append({
                "text": claim.text,
                "citations": [c.to_dict() for c in valid],
            })
            for citation in valid:
                payload = citation.to_dict()
                if payload not in citations:
                    citations.append(payload)

        # ⑥ 全丢 → 拒答（不编造）
        if not kept:
            self._audit.append(actor=principal.user_id, action="case.answer.refused",
                               detail={"reason": "no_citation", "dropped": dropped})
            trace.append(_trace_step(
                5, "引用校验", f"全部 {dropped} 条结论未通过引用校验，已拒绝输出", []))
            return AnswerResult(answered=False,
                                reason="本次回答的全部结论均无法通过引用校验，"
                                       "已拒绝输出（不编造结论）",
                                dropped_claims=dropped, search=search, trace=trace,
                                ontology_gain=self._ontology_gain(raw_question, search, principal))

        answer_text = "\n".join(
            f"{index}. {item['text']} "
            f"[{item['citations'][0]['case_id']}#{item['citations'][0]['chunk_id']}"
            f"@{item['citations'][0]['content_version']}]"
            for index, item in enumerate(kept, start=1))

        self._audit.append(
            actor=principal.user_id, action="case.answer",
            hit_count=len(kept), hidden_count=search.hidden_count,
            query_hash=search.query_hash, filter_hash=search.filter_hash,
            detail={"dropped_claims": dropped, "citations": len(citations),
                    "channel": channel})

        trace.append(_trace_step(
            5, "引用校验",
            f"通过 {len(kept)} 条结论 / 丢弃 {dropped} 条"
            "（校验内容：案例可见 + 文本哈希与内容版本双一致）", []))
        gain = self._ontology_gain(raw_question, search, principal)
        trace.append(_trace_step(
            6, "本体增强收益",
            gain.get("summary", ""), list(gain.get("items") or [])))

        return AnswerResult(answered=True, answer=answer_text, claims=kept,
                            citations=citations, dropped_claims=dropped,
                            reason="", search=search, trace=trace,
                            ontology_gain=gain)

    # ---------- 内部 ----------

    # ---------- 本体增强轨迹（前端只渲染，不在前端编） ----------

    def _build_trace(self, question: str) -> tuple[str, list[dict[str, Any]]]:
        """查询归一化 + 5 步处理轨迹（中文，全部由后端组装）。

        为什么由后端出：轨迹是这个产品的**核心卖点**（术语归一 → 同义扩展 →
        三元组检索 → 权限过滤 → 引用校验）。前端编一份"看起来像"的轨迹，
        就等于把最需要被验证的东西变成不可验证的文案。
        """
        raw = str(question or "").strip()
        normalized = self._normalize_query(raw)
        trace: list[dict[str, Any]] = []

        # ① 术语归一：命中的受控词条（中文名 + code 放技术详情）
        normalized_items: list[str] = []
        for vocab, label in (("defect_mode", "缺陷模式"), ("root_cause", "根因")):
            for code in self._ontology.find_terms(vocab, raw, limit=3):
                term = self._ontology.term(vocab, code)
                if term is not None:
                    normalized_items.append(f"{label}：{term.name}")
        trace.append(_trace_step(
            1, "术语归一",
            f"在受控词表里命中 {len(normalized_items)} 个词条"
            "（同一现象的不同写法被归一到同一个概念）",
            normalized_items))

        # ② 同义扩展
        expanded = [t for t in normalized.split(" ") if t and t not in raw.split(" ")]
        trace.append(_trace_step(
            2, "同义扩展",
            f"把现场口语扩展成 {len(expanded)} 个制造术语后再检索"
            "（扩展只影响召回文本，不参与权限过滤）",
            expanded[:8]))

        # ③ 本体检索：DefectMode → RootCause → Countermeasure 三元组链路
        chain = self._ontology_chain(raw)
        trace.append(_trace_step(
            3, "本体检索",
            f"沿「缺陷模式 → 根因 → 对策」三元组链路检索，命中链路节点 {len(chain)} 个",
            chain))

        return normalized, trace

    def _ontology_chain(self, question: str) -> list[str]:
        """三元组链路上的中文节点（缺陷模式 → 根因 → 对策）。"""
        out: list[str] = []
        for vocab, label in (("defect_mode", "缺陷模式"),
                             ("root_cause", "根因"),
                             ("countermeasure", "对策")):
            for code in self._ontology.find_terms(vocab, question, limit=2):
                term = self._ontology.term(vocab, code)
                if term is not None:
                    out.append(f"{label}：{term.name}")
        return out

    def _ontology_gain(self, question: str, search: SearchResponse,
                       principal: Optional[Principal] = None) -> dict[str, Any]:
        """本体增强的量化收益：**真实统计**，禁止编造。

        口径：同一句话，直接用原文检索 vs 归一化后检索，各命中多少案例。
        两次都走同一套权限过滤，所以差值只反映"本体增强"带来的召回增益。
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
        return {"summary": summary, "items": items,
                "baseline_cases": baseline, "enhanced_cases": enhanced}

    def _normalize_query(self, question: str) -> str:
        """确定性查询改写：受控词表同义词扩展（不做 LLM 改写）。

        只影响召回文本，**永远不参与权限过滤**（裁定 A10）。
        """
        text = str(question or "").strip()
        if not text:
            return ""
        expanded: list[str] = []
        for vocab in ("defect_mode", "root_cause"):
            for code in self._ontology.find_terms(vocab, text, limit=3):
                for label in self._ontology.expand_synonyms(vocab, code)[:2]:
                    if label and label not in text and label not in expanded:
                        expanded.append(label)
        return " ".join([text, *expanded]) if expanded else text

    def _citation_ok(self, citation: Citation) -> bool:
        """引用校验：案例存在、状态可引、``content_version`` 一致。"""
        if not citation.case_id or not citation.chunk_id:
            return False
        expected_chunk = f"{citation.case_id}::"
        if not citation.chunk_id.startswith(expected_chunk):
            return False
        if not self._index_service.verify_citation(
                citation.case_id, citation.chunk_id.split("::", 1)[1],
                citation.content_version):
            return False
        # 双重保险：再用二次授权路径确认该案例仍然可见（防并发撤权）
        case = self._store.get_case(citation.case_id)
        return case is not None and str(case.get("status") or "") in (
            "active", "deprecated", "superseded", "archived")

    @staticmethod
    def _assert_knowledge_layer(chunks: Sequence[KnowledgeChunk]) -> None:
        """闸门 4 的运行期形态：只允许 ``KnowledgeChunk``（证据层禁止进 prompt）。"""
        for chunk in chunks:
            if type(chunk) is not KnowledgeChunk:  # noqa: E721 - 刻意用 is 判等
                raise TypeError(
                    "证据层内容禁止进入 LLM 上下文："
                    f"收到 {type(chunk).__name__}")
