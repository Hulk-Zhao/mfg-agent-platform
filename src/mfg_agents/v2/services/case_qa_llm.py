"""大模型作答器与计划器（PRD FR-01 / FR-02 的 Agentic 环节）。

## 一句话设计

把 LLM 放进既有的 ``Answerer`` 协议里——**形态不变、纪律不变**，只把"模板拼装"
换成"大模型作答 + 跨案例归纳"。检索、权限、二次授权、引用校验一行不动。

## 三条硬约束（写在这里，是因为它们最容易被"为了效果"改坏）

1. **引用不可编造**：LLM 只被允许引用"喂给它的片段**编号**"，编号 → 真实
   ``(case_id, chunk_id, content_version)`` 的映射由本模块完成。模型想编一个
   不存在的案例 id 也没有入口——引用在结构上就是可信的。
2. **不能越界用知识**：提示词明确禁止使用片段之外的事实/数字；即便模型犯规，
   下游引用校验（回查知识层文本哈希 + 内容版本 + 二次授权）会照常丢弃。
3. **不能碰权限**：LLM 的所有输出只允许是"结论文本"与"查询文本片段"。
   计划器对返回值做**白名单校验**——``filter`` / ``expr`` / ``ast`` / ``acl``
   之类字段一律丢弃并记进 trace（裁定 A10：过滤器只由 ``AclCompiler`` 生成）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

from ..contracts.case_index import KnowledgeChunk
from ..contracts.ontology import OntologyRegistry
from .case_agentic import DeterministicPlanner, RetrievalPlan

if TYPE_CHECKING:  # 仅为类型标注：运行期延迟导入，避免 case_qa ↔ case_qa_llm 循环
    from .case_qa import Citation, Claim

logger = logging.getLogger(__name__)

__all__ = ["AnswererError", "LLMAnswerer", "LLMPlanner", "AutoPlanner",
           "build_json"]


class AnswererError(RuntimeError):
    """作答器不可用/输出不可解析（**由调用方降级**，不向上冒泡成 5xx）。"""


#: 计划器返回值里**允许**出现的键（白名单之外一律丢弃并留痕）。
_PLAN_KEYS = ("intent", "subqueries", "queries", "want_relation", "allow_relation",
              "note")
#: 越界字段名（出现即视为"试图生成过滤条件"，丢弃 + 记 trace）。
_FORBIDDEN_KEYS = ("filter", "filters", "expr", "expression", "ast", "acl",
                   "acl_tokens", "visibility", "dept", "tenant", "cls_rank",
                   "sql", "cypher")

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def build_json(text: Any) -> Optional[dict[str, Any]]:
    """从模型输出里取 JSON 对象（容忍 ```json 围栏与前后废话）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    candidate = raw
    match = _JSON_BLOCK.search(raw)
    if match:
        candidate = match.group(1)
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start:end + 1]
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _content_of(message: Any) -> str:
    """取 AIMessage 的文本内容（兼容 list[dict] 形态的 content）。"""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _invoke(llm: Any, prompt: str) -> str:
    """统一调用入口（异常一律转 ``AnswererError``，由调用方决定降级）。"""
    try:
        return _content_of(llm.invoke(prompt))
    except Exception as exc:  # noqa: BLE001 - 网络/鉴权/超时都走降级
        raise AnswererError(f"LLM 调用失败：{type(exc).__name__}: {exc}") from exc


class LLMAnswerer:
    """引用约束作答器（实现 ``Answerer`` 协议：``(question, chunks) -> Claims``）。"""

    def __init__(self, *, llm_factory: Optional[Callable[..., Any]] = None,
                 timeout: float = 20.0, max_chars: int = 200,
                 temperature: float = 0.0) -> None:
        self._factory = llm_factory or _default_factory
        self._timeout = float(timeout)
        self._max_chars = int(max_chars)
        self._temperature = float(temperature)
        #: 最近一次调用的人工可读状态（进 trace，便于现场讲解）
        self.last_note = ""

    # ---------- 主入口 ----------

    def __call__(self, question: str,
                 chunks: Sequence[KnowledgeChunk]) -> Sequence[Claim]:
        usable = [c for c in (chunks or ()) if str(c.text or "").strip()]
        if not usable:
            raise AnswererError("无可用知识层片段")
        prompt = self._prompt(question, usable)
        llm = self._factory(temperature=self._temperature,
                            max_tokens=max(256, self._max_chars * 3),
                            timeout=self._timeout)
        raw = _invoke(llm, prompt)
        payload = build_json(raw)
        if payload is None:
            raise AnswererError("LLM 输出不是合法 JSON（无法解析断言与引用）")
        claims = self._claims(payload, usable)
        if not claims:
            raise AnswererError("LLM 未产出任何带引用的结论")
        return claims

    # ---------- 提示词 ----------

    def _prompt(self, question: str, chunks: Sequence[KnowledgeChunk]) -> str:
        lines: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            label = {"summary": "摘要", "phenomenon": "现象",
                     "root_cause": "根因", "countermeasure": "对策"}.get(
                str(chunk.chunk_type or ""), str(chunk.chunk_type or "片段"))
            lines.append(f"[{index}]（{label}）{chunk.text}")
        context = "\n".join(lines)
        return (
            "你是制造业质量案例助手的作答环节。请**只依据**下面给出的案例片段回答现场问题。\n\n"
            f"【问题】{question}\n\n"
            f"【案例片段】\n{context}\n\n"
            "【输出要求】只输出一个 JSON 对象，形如 "
            '{"claims":[{"text":"结论一句话","refs":[1,2]}]}，不要输出任何其他文字。\n'
            "1. 每条结论必须给 refs（上面片段的编号，至少一个）；"
            "没有片段支撑的话不要写。\n"
            "2. 允许把多条片段归纳成一条结论——归纳句同样要挂 refs。\n"
            "3. 不得使用片段以外的任何事实、参数、比例、客户名或文件名；"
            "片段里出现过的数字可以引用。\n"
            "4. 不要出现案例编号；结论用现场工程师听得懂的话表述。\n"
            f"5. 全部结论合计不超过 {self._max_chars} 个汉字。\n"
            "6. 若片段不足以回答这个问题，返回 {\"claims\":[]}。\n"
        )

    # ---------- 解析 ----------

    def _claims(self, payload: dict[str, Any],
                chunks: Sequence[KnowledgeChunk]) -> "list[Claim]":
        from .case_qa import Citation, Claim  # 延迟导入（避免循环依赖）

        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list):
            raise AnswererError("LLM 输出缺少 claims 列表")
        out: list[Claim] = []
        total = 0
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            refs: list[int] = []
            for ref in (item.get("refs") or item.get("citations") or []):
                try:
                    index = int(ref)
                except (TypeError, ValueError):
                    continue
                if 1 <= index <= len(chunks) and index not in refs:
                    refs.append(index)
            if not refs:
                continue  # 无引用断言：按 BR-05 直接丢弃（不补假引用）
            budget = self._max_chars - total
            if budget <= 0:
                break
            if len(text) > budget:
                text = text[:budget]
            total += len(text)
            out.append(Claim(
                text=text,
                citations=tuple(
                    Citation(case_id=chunks[i - 1].case_id,
                             chunk_id=chunks[i - 1].chunk_id,
                             content_version=int(chunks[i - 1].content_version or 1))
                    for i in refs),
            ))
            if total >= self._max_chars:
                break
        return out


class LLMPlanner:
    """LLM 计划器（意图识别 + 复合问句拆解 + 是否走关系）。

    它与 ``DeterministicPlanner`` **同签名、同输出类型**，因此可互换；
    解析/校验失败一律退回确定性计划（``source="llm-fallback"``）。
    """

    def __init__(self, *, llm_factory: Optional[Callable[..., Any]] = None,
                 timeout: float = 12.0, max_subqueries: int = 3,
                 fallback: Optional[Any] = None) -> None:
        self._factory = llm_factory or _default_factory
        self._timeout = float(timeout)
        self._max_sub = int(max_subqueries)
        self._fallback = fallback or DeterministicPlanner()
        #: 最近一次"越界字段"记录（进 trace，证明 LLM 碰不到过滤器）
        self.last_dropped: tuple[str, ...] = ()

    def __call__(self, question: str, normalized: str,
                 ontology: OntologyRegistry) -> RetrievalPlan:
        base = self._fallback(question, normalized, ontology)
        self.last_dropped = ()
        try:
            payload = self._plan_payload(question, ontology)
        except AnswererError as exc:
            logger.info("[case_planner] LLM 计划失败，退回确定性计划：%s", exc)
            return RetrievalPlan(intent=base.intent, queries=base.queries,
                                 allow_relation=base.allow_relation,
                                 note=base.note + "（LLM 计划不可用，已用确定性规则）",
                                 source="llm-fallback")
        intents = {"single", "composite", "cross_segment", "countermeasure"}
        intent = str(payload.get("intent") or base.intent).strip()
        if intent not in intents:
            intent = base.intent
        subs: list[str] = []
        for raw in (payload.get("subqueries") or payload.get("queries") or []):
            text = str(raw or "").strip()
            if 2 <= len(text) <= 60 and text not in subs:
                subs.append(text)
        queries = tuple([normalized] + [s for s in subs
                                       if s != question and s != normalized])[:self._max_sub + 1]
        note = str(payload.get("note") or "").strip()[:80]
        return RetrievalPlan(
            intent=intent, queries=queries or base.queries,
            allow_relation=bool(payload.get("want_relation",
                                           payload.get("allow_relation", True))),
            note=f"LLM 计划：{note}" if note else "LLM 计划完成",
            source="llm", dropped_fields=self.last_dropped)

    def _plan_payload(self, question: str,
                      ontology: OntologyRegistry) -> dict[str, Any]:
        vocab_terms: list[str] = []
        for vocab in ("defect_mode", "root_cause", "countermeasure"):
            for term in ontology.find_terms(vocab, question, limit=3):
                found = ontology.term(vocab, term)
                if found is not None:
                    vocab_terms.append(found.name)
        prompt = (
            "你是制造案例检索的规划环节。只做两件事：判断意图、把复合问句拆成子问句。\n\n"
            f"【问题】{question}\n"
            f"【问句中已识别的制造术语】{'、'.join(vocab_terms) or '（无）'}\n\n"
            "【输出要求】只输出一个 JSON 对象："
            '{"intent":"single|composite|cross_segment|countermeasure",'
            '"subqueries":["子问句1","子问句2"],"want_relation":true}\n'
            "1. intent 只能从上列四个值里选；cross_segment 表示问句在追问"
            "「这个问题在别的工艺段/别的产线出现过吗」。\n"
            f"2. subqueries 最多 {self._max_sub} 条，每条不超过 60 字；"
            "简单问题给空数组。\n"
            "3. 你**只能**产出查询文本，不得产出任何过滤条件、字段名、权限信息。\n"
            "4. 不确定就给单点查询（intent=single，subqueries=[]）。\n"
        )
        llm = self._factory(temperature=0.0, max_tokens=256,
                            timeout=self._timeout)
        payload = build_json(_invoke(llm, prompt))
        if payload is None:
            raise AnswererError("计划输出不是合法 JSON")
        dropped = tuple(sorted(
            key for key in payload
            if key not in _PLAN_KEYS and str(key).lower() in _FORBIDDEN_KEYS))
        if dropped:
            logger.warning("[case_planner] LLM 输出越界字段已丢弃：%s", dropped)
            self.last_dropped = dropped
        return {k: v for k, v in payload.items() if k in _PLAN_KEYS}


class AutoPlanner:
    """自动计划器：LLM 可用 → 用 LLM；不可用 → 确定性（**结果可复现**）。"""

    def __init__(self, *, llm_factory: Optional[Callable[..., Any]] = None,
                 timeout: float = 12.0,
                 available: Optional[Callable[[], bool]] = None) -> None:
        self._deterministic = DeterministicPlanner()
        self._llm = LLMPlanner(llm_factory=llm_factory, timeout=timeout)
        self._available = available or _default_available
        #: 最近一次真正生效的计划器（进 trace）
        self.last_source = "deterministic"

    def __call__(self, question: str, normalized: str,
                 ontology: OntologyRegistry) -> RetrievalPlan:
        if not self._available():
            self.last_source = "deterministic"
            return self._deterministic(question, normalized, ontology)
        plan = self._llm(question, normalized, ontology)
        self.last_source = plan.source
        return plan


def _default_factory(**kwargs: Any) -> Any:
    from ...common.llm_factory import get_llm

    # L1（低延迟档）用于计划/结构化输出：计划层不该拖慢演示节奏。
    tier = kwargs.pop("tier", "L1")
    return get_llm(tier=tier, thinking="disabled", **kwargs)


def _default_available() -> bool:
    from ...common.llm_factory import llm_available

    return bool(llm_available(timeout=1.0))
