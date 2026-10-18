"""企业制度问答服务（ADR-001：L1 FAQ 前置 + L2 RAG 兜底 + 统一契约）。

链路（``answer`` 出口即统一契约）：

1. **L1 FAQ 前置**（``kbqa_faq_match.FAQMatcher``）：``kbqa_policy`` 制度主数据
   ∪ ``kbqa_faq`` 晋升对，词面加权计分 + 阈值/margin 双闸。命中则**不调 LLM、
   不碰 Milvus**，直接回权威制度正文（``channel=faq``）；
2. **意图路由**（``kbqa_intent.route``，sub-ms 词面）：L1 miss 后分流——
   域内词命中走 L2 RAG；其余（域外/兜底）走**约束 LLM 直答**
   （``channel=direct``，不调 ``search_kb``，省 1-1.5s 检索）；
3. **L2 RAG 兜底**（原方案 B 链路不动）：``search_kb``（BGE-M3 混合召回 +
   Reranker）→ ``get_llm``（deepseek-flash）生成带出处回答；
   LLM 不可用时降级回放最相关片段（``degraded=True``）；
4. **L0 缓存**只包 L2/direct（昂贵路径）：键含 ``faq_version`` 命名空间——
   FAQ 任何写操作改变版本戳 → 旧键自然不可达，无需主动失效
   （ADR-001 D2）。L1 每问全量重算 sub-ms，不占缓存正确性面；
5. **度量**：每问写 ``kbqa_query_log``（channel/confidence/latency），
   遥测失败只告警不抛——统计写失败不能弄丢一次回答。

数据面：知识块由 ``mfg_agents.common.kbqa_seed`` 导入 Milvus
``enterprise_faq``；结构化镜像 ``kbqa_policy`` + 本服务三张自有表
（kbqa_faq / kbqa_faq_draft / kbqa_query_log）经 ``kbqa_store`` 访问。

检索复用 ``tools/rag_tools.py``（无需 BERT / bge-small）。
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from ...common.config import settings
from ...common.kbqa_seed import DEPT_CN, DEPT_ORDER
from ...common.llm_factory import get_llm, llm_available
from ...common.rag_tools import search_kb
from ..core import cache
from . import kbqa_intent, kbqa_store
from .kbqa_faq_match import FAQMatcher

logger = logging.getLogger(__name__)

#: 制度问答结果缓存 TTL（制度问答无个人数据，缓存 30 分钟安全）
KBQA_CACHE_TTL_S = 1800

#: 前端"支持类别"面板数据（保持原项目文案与顺序）
KBQA_DEPTS: list[dict[str, str]] = [
    {"key": key, "name": DEPT_CN[key][0], "desc": DEPT_CN[key][1]}
    for key in DEPT_ORDER
]

# ---- 统一答案契约的 channel 取值（ADR-001 §5 + DESIGN-2）----
CHANNEL_FAQ = "faq"                    # L1 制度直答（本次实时匹配）
CHANNEL_RAG = "rag"                    # L2 知识库检索生成（含降级回放）
CHANNEL_RAG_UNAVAILABLE = "rag_unavailable"  # L2 整层不可用（如 Milvus 挂）
CHANNEL_MISS = "miss"                  # L1/L2 都未检索到相关内容
CHANNEL_DIRECT = "direct"              # 意图路由 → 约束 LLM 直答（无出处）

_SYSTEM_PROMPT = """你是企业制度问答助手。根据以下制度片段回答员工问题。
规则：
1. 仅基于片段回答，片段中没有就说"未在制度中找到相关内容"，禁止编造
2. 简洁友好，150字以内
3. 末尾标注出处，格式：【来源：{cn} · 文件名 第N段】，引用多个片段时全部列出
======
{context}"""

# DESIGN-2 直答 prompt：四约束（相关但无答案→指引对口部门；无关→身份声明+
# 可帮方向；禁止编造制度条款；100 字以内）。直答不带 sources，UI 上与
# faq/rag 视觉区分——没有出处的回答不许伪装成制度原文。
_DIRECT_SYSTEM_PROMPT = """你是制造企业的制度问答助手，回答员工的各类提问。
规则：
1. 如果问题与企业运营相关但你无法确定答案，明确说"这超出了我已掌握的企业公开制度范围"，并指引员工咨询对口部门（人事/财务/IT/行政/生产/质量/供应链/安环）
2. 如果问题与企业无关（天气、闲聊、写作、百科、娱乐等），先用一句话说明你是企业制度问答助手，再列举你能帮助的方向（考勤、请假、报销、IT 支持、设备、质量、采购等）
3. 禁止编造任何制度条款、数字、审批流程或文件名，不确定就直说不知道
4. 回答控制在100字以内，简洁友好，不要使用 markdown 格式"""

# LLM 不可用时的直答降级话术（诚实告知，不缓存）
_DIRECT_FALLBACK = ("助手直答暂不可用（语言模型服务连接异常），请稍后重试。"
                    "企业制度问题（考勤、请假、报销、IT 等）也可直接咨询对口部门。")


def _contract(answer: str, *, channel: str, tags: list[dict[str, str]],
              sources: list[dict[str, Any]], category: dict[str, str],
              degraded: bool, confidence: float | None = None,
              latency_ms: int = 0, faq_version: str = "") -> dict[str, Any]:
    """统一答案契约：存量字段（answer/tags/sources/category/degraded）语义不变，
    新增 channel/confidence/latency_ms/faq_version（ADR-001 §5）。"""
    return {
        "answer": answer,
        "tags": tags,
        "sources": sources,
        "category": category,
        "degraded": degraded,
        "channel": channel,
        "confidence": confidence,
        "latency_ms": latency_ms,
        "faq_version": faq_version,
    }


def _tags_and_sources(hits: list[dict[str, Any]]) -> tuple[list[dict[str, str]],
                                                          list[dict[str, Any]]]:
    """由检索命中生成气泡标签（部门 + 出处）与结构化来源列表。"""
    depts: list[str] = []
    for h in hits:
        cn = (h.get("metadata") or {}).get("dept_cn")
        if cn and cn not in depts:
            depts.append(str(cn))

    tags: list[dict[str, str]] = [
        {"text": name, "cls": "first" if i == 0 else ""}
        for i, name in enumerate(depts)
    ]

    sources: list[dict[str, Any]] = []
    for h in hits:
        meta = h.get("metadata") or {}
        seg = int(meta.get("chunk_index") or 0) + 1
        src = str(meta.get("source") or "")
        label = f"{src} 第{seg}段"
        if label not in [s["label"] for s in sources]:
            sources.append({
                "label": label, "source": src, "segment": seg,
                "dept_cn": str(meta.get("dept_cn") or ""),
                "score": h.get("score"),
            })
    tags += [{"text": f"📄 {s['label']}", "cls": "src"} for s in sources]
    return tags, sources


def _l1_tags_and_sources(row: dict[str, Any],
                         score: float) -> tuple[list[dict[str, str]],
                                                list[dict[str, Any]]]:
    """L1 命中行的标签与来源（policy_no / FAQ#id 即出处）。"""
    dept_cn = str(row.get("dept_cn") or "其他")
    ref = str(row.get("ref") or "")
    label = ref if row.get("src") == "policy" else f"FAQ #{ref}"
    tags: list[dict[str, str]] = [
        {"text": dept_cn, "cls": "first"},
        {"text": f"📄 {label}", "cls": "src"},
    ]
    sources = [{
        "label": label, "source": label, "segment": 0,
        "dept_cn": dept_cn, "score": round(float(score), 4),
    }]
    return tags, sources


def _cache_key(query: str, top_k: int, version: str = "") -> str:
    """L2 答案缓存键：``mfg:kbqa:v2:{version}:{sha1(归一化query|top_k)}``。

    version 命名空间保证 FAQ 写操作后旧条目不可达（ADR-001 D2）；
    归一化使"年假 怎么休"与"年假怎么休"同键。
    """
    from .kbqa_faq_match import normalize

    raw = f"{normalize(query)}|{int(top_k)}|{version}"
    return "mfg:kbqa:v2:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _log_query(session_id: str, query: str, channel: str,
               confidence: float | None, top_score: float | None,
               latency_ms: int) -> None:
    """度量落库（开关 + 失败静默，见模块 docstring 第 4 条）。"""
    if not settings.KBQA_LOG_WRITES:
        return
    kbqa_store.log_query(session_id, query, channel,
                         confidence=confidence, top_score=top_score,
                         latency_ms=latency_ms)


def answer(query: str, top_k: int = 3, session_id: str = "") -> dict[str, Any]:
    """企业制度问答入口：L1 前置 → 意图路由 → L2 RAG/约束直答，返回统一契约。

    L1 数据层（库）不可用时整层跳过而非报错——FAQ 是加速层，不是依赖。
    意图路由（DESIGN-2）零延迟词面分流：域内词命中保 RAG 全链路（要出处），
    其余走约束直答（不调 search_kb）；``KBQA_DIRECT_ENABLED=false`` 可整体
    关掉直答回落纯 RAG 行为。
    """
    started = time.time()

    # ---- L1：FAQ 前置（语料/版本戳每次现取，sub-ms；库挂则跳过）----
    version = ""
    corpus: list[dict[str, Any]] = []
    try:
        corpus = kbqa_store.faq_corpus()
        version = kbqa_store.faq_version()
    except Exception as exc:  # noqa: BLE001 - L1 是加速层，故障必须可绕过
        logger.warning("[kbqa] L1 语料装载失败，跳过前置层: %s", exc)

    matcher = FAQMatcher(
        corpus=corpus,
        threshold=settings.KBQA_MATCH_THRESHOLD,
        margin=settings.KBQA_MARGIN_THRESHOLD)
    m = matcher.match(query)
    if m.hit and m.row:
        row = m.row
        tags, sources = _l1_tags_and_sources(row, m.score)
        latency_ms = int((time.time() - started) * 1000)
        _log_query(session_id, query, CHANNEL_FAQ,
                   confidence=m.score, top_score=m.score, latency_ms=latency_ms)
        return _contract(
            answer=str(row.get("answer") or ""),
            channel=CHANNEL_FAQ,
            tags=tags, sources=sources,
            category={"name": str(row.get("dept_cn") or "其他")},
            degraded=False,
            confidence=round(m.score, 4),
            latency_ms=latency_ms,
            faq_version=version,
        )

    # ---- 意图路由（DESIGN-2）：域内词命中 → RAG；其余 → 约束直答 ----
    # 零延迟词面分流（sub-ms）。"都不命中走直答"是 Hulk 确认的策略：
    # thinking-disabled 裸 LLM ~1.0s < search_kb+LLM ~2.6s；直答 prompt 的
    # 四约束保证不编造制度条款，兜底质量由 stats 的 direct 分布回看兜住。
    decision = kbqa_intent.route(query)
    use_direct = not decision.domain and settings.KBQA_DIRECT_ENABLED
    if use_direct:
        logger.info("[kbqa] 意图路由→直答: query=%r", query[:60])

    # ---- L2/direct：昂贵路径进缓存（键含版本命名空间）----
    key = _cache_key(query, top_k, version)
    result = cache.get_or_compute(
        key, KBQA_CACHE_TTL_S,
        lambda: (_answer_direct(query, started=started, version=version)
                 if use_direct
                 else _answer_rag(query, top_k, started=started,
                                  version=version)))
    if result.get("channel") == CHANNEL_RAG_UNAVAILABLE or (
            result.get("channel") == CHANNEL_DIRECT and result.get("degraded")):
        # 不可用状态绝不缓存 30 分钟——中间件恢复后要被立刻放行
        # （rag_unavailable = Milvus 挂；degraded direct = LLM 挂）
        cache.cache_delete(key)
    # 缓存回放时刷新本次实际耗时（契约里的 latency 必须反映这一次调用）
    result["latency_ms"] = int((time.time() - started) * 1000)
    _log_query(session_id, query, str(result.get("channel") or CHANNEL_RAG),
               confidence=result.get("confidence"), top_score=None,
               latency_ms=result["latency_ms"])
    return result


def _answer_rag(query: str, top_k: int, *, started: float,
                version: str) -> dict[str, Any]:
    """（内部）L2 实际检索 + 生成，无缓存。

    返回统一契约；``search_kb`` 抛错（Milvus 挂等）时返回
    ``channel=rag_unavailable`` 的诚实降级契约——由 ``answer`` 负责不缓存它。
    """
    try:
        hits = search_kb(query, n_results=top_k)
    except Exception as exc:  # noqa: BLE001 - 检索层故障 → 诚实降级契约
        logger.warning("[kbqa] L2 检索失败，降级告知: %s", exc)
        return _contract(
            answer="知识库检索暂不可用（向量服务连接失败），请稍后重试；"
                   "常见制度问题（年假/报销/VPN）仍可正常回答。",
            channel=CHANNEL_RAG_UNAVAILABLE,
            tags=[], sources=[], category={"name": "系统"},
            degraded=True,
            latency_ms=int((time.time() - started) * 1000),
            faq_version=version,
        )

    tags, sources = _tags_and_sources(hits)
    top_dept = (hits[0].get("metadata") or {}).get("dept_cn") if hits else None
    category = {"name": str(top_dept or "其他")}
    latency_ms = int((time.time() - started) * 1000)

    if not hits:
        return _contract(
            answer="未在制度中找到相关内容，可以换个问法，"
                   "或咨询考勤、报销、IT、人事等业务问题。",
            channel=CHANNEL_MISS,
            tags=tags, sources=sources, category=category,
            degraded=not llm_available(),
            latency_ms=latency_ms,
            faq_version=version,
        )

    if not llm_available():
        # 降级：无 LLM 时回放最相关片段，仍标注出处
        top = hits[0]
        seg = int((top.get("metadata") or {}).get("chunk_index") or 0) + 1
        src = (top.get("metadata") or {}).get("source") or ""
        return _contract(
            answer=f"{top['document']}\n\n（离线降级：仅回放最相关片段）",
            channel=CHANNEL_RAG,
            tags=tags, sources=sources, category=category,
            degraded=True,
            latency_ms=latency_ms,
            faq_version=version,
        )

    ctx_parts = []
    for h in hits:
        meta = h.get("metadata") or {}
        seg = int(meta.get("chunk_index") or 0) + 1
        ctx_parts.append(f"[{meta.get('source', '')} 第{seg}段]\n{h['document']}")
    context = "\n---\n".join(ctx_parts)

    llm = get_llm(temperature=0.3, thinking="disabled", max_tokens=400, timeout=60)
    msgs = [
        ("system", _SYSTEM_PROMPT.format(cn=category["name"], context=context)),
        ("human", query),
    ]
    resp = llm.invoke(msgs)
    text = getattr(resp, "content", None) or str(resp)
    if isinstance(text, list):  # 部分模型返回分段 content
        text = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part)
                       for part in text)
    return _contract(
        answer=str(text).strip(),
        channel=CHANNEL_RAG,
        tags=tags, sources=sources, category=category,
        degraded=False,
        latency_ms=latency_ms,
        faq_version=version,
    )


def _answer_direct(query: str, *, started: float,
                   version: str) -> dict[str, Any]:
    """（内部）意图路由命中"非域内"时的约束 LLM 直答，不调 ``search_kb``。

    实测 thinking-disabled 裸 LLM ~1.0s（瓶颈在首 token，max_tokens 影响小），
    比 search_kb+LLM 全链路省 ~1.6s。prompt 四约束见 ``_DIRECT_SYSTEM_PROMPT``；
    LLM 不可用 → 诚实降级话术（``degraded=True``，由 ``answer`` 负责不缓存）。
    直答契约**不带 sources**——没有出处的回答不许伪装成制度原文。
    """
    if not llm_available():
        return _contract(
            answer=_DIRECT_FALLBACK,
            channel=CHANNEL_DIRECT,
            tags=[], sources=[], category={"name": "助手"},
            degraded=True,
            latency_ms=int((time.time() - started) * 1000),
            faq_version=version,
        )

    llm = get_llm(temperature=0.3, thinking="disabled",
                  max_tokens=settings.KBQA_DIRECT_MAX_TOKENS, timeout=30)
    msgs = [
        ("system", _DIRECT_SYSTEM_PROMPT),
        ("human", query),
    ]
    resp = llm.invoke(msgs)
    text = getattr(resp, "content", None) or str(resp)
    if isinstance(text, list):  # 部分模型返回分段 content
        text = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part)
                       for part in text)
    return _contract(
        answer=str(text).strip(),
        channel=CHANNEL_DIRECT,
        tags=[], sources=[], category={"name": "助手"},
        degraded=False,
        latency_ms=int((time.time() - started) * 1000),
        faq_version=version,
    )
