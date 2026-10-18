"""CaseSearchService：两段式安全检索编排（架构 §7.5 的 16 步，顺序不可调换）。

```
 1. 校验主体（user_id 非空）               → 失败 401（上游 PrincipalResolver 已完成验签）
 2. pv 版本比对                            → 不匹配 401（A5）
 3. 动作门禁（export/share）                → 短路 FALSE_FILTER + 审计
 4. 显式 DENY 规则匹配                      → 短路 FALSE_FILTER + 审计
 5. RBAC case.<action>                     → 无权限 FALSE_FILTER + 审计
 6. 编译 FilterAST（硬维度 + 可见性分流）
 7. 翻译为后端原生 filter（含防注入校验）
 8. 执行混合检索（filter 下推）              ← ★ 过滤在召回前生效（BR-01 前半）
 9. Rerank（状态权重 × 新鲜度 × 有效性）
10. 同 case 去重（≤2 chunk）
11. 二次授权（关系库权威 ACL，独立代码路径）  ← ★ BR-01 后半
12. 排序
13. 组装上下文（仅 KnowledgeChunk） + 引用校验（BR-05）
14. hidden_count（仅硬维度计数查询）
15. 落审计（只记哈希）
16. 返回
```

**两条红线**（架构 §16.3，违反即返工）：

- 候选不足时**绝不**去掉 filter 重试。``hits == 0`` → 返回空 + ``hidden_count``；
  ``0 < hits < k`` → 如实返回 + ``notice``。代码里不存在任何"放宽重试"分支。
- 返回的每条命中都必须再过一次**二次授权**；二次授权全丢弃 → 返回空 +
  ``second_stage_dropped``，**不回退**到未经二次校验的结果。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from ...common.config import settings
from ..contracts.case_acl import FilterNode, Principal
from ..contracts.case_index import IndexHealth, KnowledgeChunk, SearchHit
from ..security.acl_compiler import (
    AclCompiler,
    CasePolicySet,
    EnvContext,
    get_acl_compiler,
)
from ..security.acl_evaluator import AclEvaluator, get_acl_evaluator
from ..security.filter_translator import explain_filter, filter_hash
from .case_audit import CaseAuditLog, hash_query
from .case_index import CaseIndexService
from .case_store import VISIBLE_STATUS, CaseStore

logger = logging.getLogger(__name__)

__all__ = [
    "CaseSearchError",
    "SearchRequest",
    "SearchResponse",
    "CaseSearchService",
    "DEFAULT_K",
    "source_ticket_ref",
]

#: 默认返回条数。
DEFAULT_K = 5

#: 受限清单最多回多少条（标题级信息也不能变成批量导出）。
RESTRICTED_MAX = 20
#: 计算受限清单时最多扫描多少候选 chunk（上限保护：全量扫描在百例库上是 O(库大小)）。
MAX_RESTRICTED_SCAN = 10000
#: 受限清单的扫描上限（**按案例计**）。命中上限即停止——宁可少列几条，
#: 也不让每次检索都扫全库；清单本身只是"申请入口提示"，不是完备列表。
RESTRICTED_SCAN_CAP = 200
#: 顶部统计卡片（``/cases/stats``）的扫描上限。比受限清单宽得多：卡片上的
#: 数字要能和检索结果对上，不能被扫描上限截断成"少了几个"；万一大库触顶，
#: 响应里带 ``truncated=true``，前端标注"统计不完整"而不是假装精确。
STATS_SCAN_CAP = 5000

#: 可走访问申请的状态（与 services/case_access.REQUESTABLE_STATUS 对齐）。
_REQUESTABLE_STATUS: frozenset[str] = frozenset({"active", "deprecated"})


def _is_requestable(case: Mapping[str, Any]) -> bool:
    """这条案例能不能走"申请访问"（涉密与未结案不走）。"""
    return _is_disclosable(case)


def _is_disclosable(case: Mapping[str, Any]) -> bool:
    """这条案例**是否允许被披露"存在"**（B10）。

    为什么必须单独一个函数、而不是复用 ``_is_requestable`` 的语义：
    **"可申请"与"可披露存在"是两件事**。涉密（L3）案例由
    ``acl_evaluator`` 的显式 DENY 规则排除出共享池（E1 / PRD §7.4），
    检索链路也确实会丢弃它；但它**同样不该出现在"有但你看不透"的清单里**——
    标题本身就是信息。``requestable=false`` 只是个标记，
    **标记不构成过滤**：条目照样会带着标题进响应，等于绕过 DENY 规则泄漏。

    规则：涉密 / 未结案 / 已撤权 / 未知状态 → **一律不可披露**，连 case_id 都不给。
    """
    try:
        rank = int(case.get("cls_rank") or 0)
    except (TypeError, ValueError):
        return False
    if rank >= 3:
        return False
    return str(case.get("status") or "") in _REQUESTABLE_STATUS


def source_ticket_ref(source_ticket_id: str) -> str:
    """来源单据号的**不可反推引用句柄**（``st_`` + sha256 前 10 位）。

    为什么检索响应不能直接给单号：8D / 客诉单号在真实工厂里可反查到客户、
    批次与产线，属于"可关联到具体主体的标识"，放在面向前端的命中载荷里
    等于给了一条侧信道。但前端又需要一个**稳定、可对账**的句柄来说明
    "这条案例来自哪张单"，因此给哈希句柄：同一单号永远同一 ref，
    但拿 ref 反推不出单号。
    """
    raw = str(source_ticket_id or "").strip()
    if not raw:
        return ""
    return "st_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


class CaseSearchError(Exception):
    """检索编排异常（配置/内部缺陷；权限拒绝不走异常，走空结果 + reason）。"""


@dataclass(frozen=True)
class SearchRequest:
    """一次检索请求（**刻意不包含 filter 字段**：调用方无法传自定义过滤条件）。"""

    query: str = ""
    action: str = "query"
    k: int = DEFAULT_K
    channel: str = "http"


@dataclass
class SearchResponse:
    """检索结果（API 直接序列化）。"""

    hits: list[dict[str, Any]] = field(default_factory=list)
    hidden_count: int = 0
    #: ``hidden_count`` 的单位（口径固化，避免"16 条"被当成 16 个案例）。
    hidden_count_unit: str = "case"
    degraded: bool = False
    notice: str = ""
    low_confidence: bool = False
    second_stage_dropped: int = 0
    denied_reason: str = ""
    action: str = "query"
    query_hash: str = ""
    filter_hash: str = ""
    candidates: int = 0
    backend: str = ""
    acl_denied: bool = False
    denied_code: str = ""
        #: 人类可读的过滤条件解释（供前端「为什么看不到」面板）。**不含原始 expr / AST**。
    filter_explain: list[str] = field(default_factory=list)
    #: 本次结果的分类分布（按 subcategory 计数；前端画条形图用，**真实统计**）。
    category_dist: dict[str, int] = field(default_factory=dict)
    #: 受限案例（"有，但你看不透"）：只回 case_id/title/category/reason/requestable。
    #: **不回正文、不回密级细节、不回 ACL 明细**——否则就成了新的泄漏面。
    restricted: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "hidden_count": self.hidden_count,
            "hidden_count_unit": self.hidden_count_unit,
            "restricted": self.restricted,
            "category_dist": self.category_dist,
            "degraded": self.degraded,
            "notice": self.notice,
            "low_confidence": self.low_confidence,
            "second_stage_dropped": self.second_stage_dropped,
            "denied_reason": self.denied_reason,
            "denied_code": self.denied_code,
            "action": self.action,
            "query_hash": self.query_hash,
            "filter_hash": self.filter_hash,
            "candidates": self.candidates,
            "backend": self.backend,
            "acl_denied": self.acl_denied,
            "filter_explain": self.filter_explain,
        }


def _parse_date(text: str) -> Optional[date]:
    """尽量解析日期（解析不出就返回 None，不猜）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


class CaseSearchService:
    """两段式安全检索（唯一检索入口；MCP/A2A/HTTP/IM 都调它）。"""

    def __init__(self, store: CaseStore, index_service: CaseIndexService,
                 compiler: Optional[AclCompiler] = None,
                 evaluator: Optional[AclEvaluator] = None,
                 audit: Optional[CaseAuditLog] = None,
                 policy: Optional[CasePolicySet] = None,
                 reranker: Optional[Callable[[list[SearchHit], Mapping[str, Any]],
                                             list[SearchHit]]] = None) -> None:
        self._store = store
        self._index_service = index_service
        self._compiler = compiler or get_acl_compiler()
        self._evaluator = evaluator or get_acl_evaluator()
        self._audit = audit or CaseAuditLog(store.db_path)
        self._policy = policy or self._compiler.policy
        # 重排是可替换适配器：生产注入 BGE-Reranker，demo 用确定性加权
        self._reranker = reranker or self._default_rerank

    @property
    def policy(self) -> CasePolicySet:
        """当前生效的策略集（只读；供 ``/meta`` 与前端展示）。"""
        return self._policy

    # ---------- 主入口 ----------

    def search(self, principal: Optional[Principal], query: str,
               action: str = "query", k: int = DEFAULT_K,
               env: Optional[EnvContext] = None,
               channel: str = "http") -> SearchResponse:
        """按 16 步顺序执行检索（每一步都有明确失败语义）。"""
        request = SearchRequest(query=str(query or ""), action=str(action or "query"),
                                k=max(0, int(k)), channel=str(channel or "http"))
        active_env = env or EnvContext(channel=request.channel)

        # ── 1-2. 主体可用性（验签在上游；此处校验非空 + pv）───────────
        if principal is None or not principal.user_id:
            return self._deny(principal, request, "无身份主体：请先登录（401）",
                              audit_action="case.query.denied",
                              code="CASE_TOKEN_MISSING")
        if (active_env.expected_principal_version is not None
                and int(principal.principal_version) != int(active_env.expected_principal_version)):
            return self._deny(principal, request, "主体授权版本失效（pv 不匹配，401）",
                              audit_action="case.query.denied",
                              code="CASE_PRINCIPAL_REVOKED")

        # ── 3-5. 动作门禁 / 显式 DENY / RBAC（全部短路为 FALSE_FILTER）──
        filter_node, reason = self._compiler.compile_explained(
            principal, request.action, policy=self._policy, env=active_env)
        if not filter_node.children:
            # FALSE_FILTER：动作被拒或主体不可用——**不进检索**
            return self._deny(principal, request, reason,
                              audit_action="case.query.denied",
                              code="CASE_ACTION_DENIED")

        # ── 7. filter 已由编译器产出；翻译在后端适配器内完成（防注入校验在其入口）
        fh = filter_hash(filter_node)

        # ── 8. 执行检索：filter 随召回一起下推（BR-01 前半）──────────
        health: IndexHealth = self._index_service.health()
        if not health.ready:
            return self._degrade(request, f"向量索引不可用：{health.detail}")

        candidates = self._index_service.index.search(
            request.query, filter_node, max(request.k * 3, 12))
        total_candidates = len(candidates)

        # ── 9. Rerank（可替换适配器）──────────────────────────────
        reranked = self._reranker(candidates, {"principal": principal,
                                               "policy": self._policy})

        # ── 10. 同 case 去重（≤ max_chunks_per_case，按类型优先级）──
        deduped = self._dedupe_by_case(reranked)

        # ── 11. 二次授权（关系库权威 ACL，独立代码路径）─────────────
        passed, dropped = self._second_stage(principal, deduped,
                                             request.action, active_env)

        # ── 12-13. 排序 + 组装上下文（仅 KnowledgeChunk）────────────
        ordered = self._sort(passed)
        hits = [self._to_payload(hit, principal, request.action)
                for hit in ordered[:request.k]]

        # ── 14. hidden_count（仅硬维度安全计数，且只在候选不足时算）───
        hidden = self._hidden_count(principal, request.action, filter_node,
                                    len(hits), request.k)

        # ── 14.5 受限案例（"有，但你看不透"）────────────────────────
        # 需求侧诉求是"别的部门的案例对我有用"，那就得让人**看得见它存在**，
        # 否则只剩一句"N 条不可见"，无处申请。这里只回 5 个字段：
        # case_id / title / category / reason / requestable——
        # **不回正文、不回密级细节、不回 ACL 明细**，否则申请通道会变成新的泄漏面。
        # 受限清单的数据源是"仅硬维度"的候选集（软维度过滤掉的那部分）。
        # 注意：不能从 ``deduped`` 取——前置过滤已经把越权的挡在召回之前，
        # 二次授权拦不到东西（这正是 BR-01 的设计），所以要从硬维度候选里找。
        restricted = self._restricted(principal, request.action, filter_node,
                                      {h["case_id"] for h in hits})

        # ── 15. 审计（只记哈希与计数）────────────────────────────
        qh = hash_query(request.query)
        self._audit.append(
            actor=principal.user_id, action=f"case.{request.action}",
            query_hash=qh, filter_hash=fh, hit_count=len(hits),
            hidden_count=hidden,
            detail={
                "channel": request.channel,
                "roles": list(principal.roles),
                "candidates": total_candidates,
                "dropped": len(dropped),
                "backend": self._index_service.index.backend,
            })

        notice, low_confidence = self._compose_notice(
            hits=len(hits), k=request.k, hidden=hidden, dropped=len(dropped),
            query=request.query)
        dist: dict[str, int] = {}
        for hit in hits:
            key = str(hit.get("subcategory") or "") or "other"
            dist[key] = dist.get(key, 0) + 1
        return SearchResponse(
            hits=hits, restricted=restricted, category_dist=dist,
            hidden_count=hidden, degraded=False, notice=notice,
            low_confidence=low_confidence, second_stage_dropped=len(dropped),
            action=request.action, query_hash=qh, filter_hash=fh,
            candidates=total_candidates,
            backend=self._index_service.index.backend,
            filter_explain=explain_filter(filter_node),
        )

    # ---------- 单案例读取 / 引用 ----------

    def read_case(self, principal: Principal, case_id: str, *,
                  action: str = "read") -> Optional[dict[str, Any]]:
        """按 id 读取案例（**无权时返回 None → API 层 404**，防存在性探测）。

        ⚠️ **两条路径必须做等价的工作量**（B1）。
        "不存在"如果只查一次 `get_case` 就返回，而"存在但无权"要多走
        `eval_rows` + `authorize` + 审计三次 DB 往返，那么**耗时差本身就是
        存在性预言机**（实测 30.9ms vs 3.3ms）。因此不存在路径也要：
        `eval_rows`（真实查询，返回空集）→ `authorize`（空行集）→ 同构审计写入。
        代价是每次"查无此案"多花几次 IO，收益是两条路径不可区分。
        """
        case = self._store.get_case(case_id)
        if case is None:
            # ↓ 与"存在但无权"对齐：同样的查询次数、同样的判定调用、同样的审计写入
            rows = self._store.eval_rows(case_id)      # 空集，但仍是一次真实 DB 查询
            self._evaluator.authorize(principal, rows, action)
            # 审计里**不写 case_id**（不把可能不存在的 id 填进共享审计流水）。
            # 审计写入对两条路径都无条件执行——B1 的等价性靠"都写"，
            # 不能靠"只写一部分"（采样会让部分 404 更快，时间差又回来了）。
            # 审计灌水的治理手段是入口限流（security/case_rate_limit.py），不是采样。
            self._audit.append(actor=principal.user_id,
                               action=f"case.{action}.denied", case_id="",
                               detail={"rule": "not_found_or_not_visible"})
            return None
        decision = self._evaluator.authorize(
            principal, self._store.eval_rows(case_id), action)
        if not decision.allow:
            self._audit.append(actor=principal.user_id,
                               action=f"case.{action}.denied", case_id=case_id,
                               detail={"rule": decision.rule_id})
            return None
        self._audit.append(actor=principal.user_id, action=f"case.{action}",
                           case_id=case_id, hit_count=1)
        return self._case_payload(case, principal)

    def trace_allowed(self, principal: Principal, case_id: str) -> bool:
        """是否允许把**原始来源单号**给这个主体（R1 的"完整溯源"闸门）。

        与 ``read_case`` 用同一套判定（独立代码路径的二次授权），但**不写审计**：
        它只是"能不能把详情里那个字段给出去"的子判定，本身不是一次读取行为。
        """
        case = self._store.get_case(case_id)
        if case is None:
            self._evaluator.authorize(principal, [], "read")
            return False
        decision = self._evaluator.authorize(
            principal, self._store.eval_rows(case_id), "read")
        return bool(decision.allow)

    # ---------- 二次授权 ----------

    def _second_stage(self, principal: Principal, hits: Sequence[SearchHit],
                      action: str, env: EnvContext
                      ) -> tuple[list[SearchHit], list[str]]:
        """逐条回关系库读权威 ACL 再判一次（双保险）。

        注意：这里刻意**不复用**编译器的 AST 判定，而是让 ``AclEvaluator`` 读
        ``case_acl`` 行做集合运算——两套独立实现，才能兜住 filter 翻译 bug。
        """
        passed: list[SearchHit] = []
        dropped: list[str] = []
        cache: dict[str, bool] = {}
        # ★ 批量取 ACL 行：逐条 eval_rows 是「每案例 2 次 SQLite 建连」，
        #   候选几十条时一次检索要多开近百个连接（实测占单次检索耗时的大头）。
        #   行内容与逐条调用完全一致，只是把 N×2 次建连压成 1 次。
        case_ids = sorted({str(hit.case_id) for hit in hits})
        rows_by_case = self._store.eval_rows_many(case_ids) if case_ids else {}
        for hit in hits:
            allowed = cache.get(hit.case_id)
            if allowed is None:
                decision = self._evaluator.authorize(
                    principal, rows_by_case.get(str(hit.case_id), []), action, env)
                allowed = decision.allow
                cache[hit.case_id] = allowed
                if not allowed:
                    logger.warning(
                        "[case_search] 二次授权丢弃 %s（%s）——索引元数据可能过期，"
                        "已计入 second_stage_dropped", hit.case_id, decision.reason)
            if allowed:
                passed.append(hit)
            elif hit.case_id not in dropped:
                dropped.append(hit.case_id)
        return passed, dropped

    # ---------- 重排 / 去重 / 排序 ----------

    def _default_rerank(self, hits: Sequence[SearchHit],
                        context: Mapping[str, Any]) -> list[SearchHit]:
        """确定性加权重排：相似度 × 状态权重 × 新鲜度 × 有效性验证。

        生产替换为 BGE-Reranker（构造参数 ``reranker=``），接口不变。
        """
        policy: CasePolicySet = context["policy"]  # type: ignore[assignment]
        weights = policy.setting("status_weight", {}) or {}
        stale_days = int(policy.setting("freshness_days", 730) or 730)
        stale_factor = float(policy.setting("stale_factor", 0.85) or 0.85)
        unverified_factor = float(policy.setting("unverified_factor", 0.9) or 0.9)
        today = datetime.now(timezone.utc).date()
        out: list[SearchHit] = []
        for hit in hits:
            meta = hit.metadata or {}
            status = str(meta.get("status") or "")
            weight = float(weights.get(status, 1.0))
            factor = weight
            verified_at = _parse_date(str(meta.get("last_verified_at") or ""))
            if verified_at is not None and (today - verified_at).days > stale_days:
                factor *= stale_factor
            if not bool(meta.get("verified")):
                factor *= unverified_factor
            out.append(SearchHit(
                case_id=hit.case_id, chunk_id=hit.chunk_id,
                chunk_type=hit.chunk_type, content_version=hit.content_version,
                text=hit.text, score=round(hit.score * factor, 6),
                metadata=meta,
            ))
        return out

    def _dedupe_by_case(self, hits: Sequence[SearchHit]) -> list[SearchHit]:
        """同一案例最多保留 N 个 chunk（默认 2），按类型优先级 + 分数。"""
        from .case_index import CHUNK_TYPE_PRIORITY

        limit = int(self._policy.setting("max_chunks_per_case", 2) or 2)
        grouped: dict[str, list[SearchHit]] = {}
        for hit in hits:
            grouped.setdefault(hit.case_id, []).append(hit)
        out: list[SearchHit] = []
        for case_id, group in grouped.items():
            group.sort(key=lambda h: (CHUNK_TYPE_PRIORITY.get(h.chunk_type, 9),
                                      -h.score, h.chunk_id))
            out.extend(group[:max(1, limit)])
        return out

    @staticmethod
    def _sort(hits: Sequence[SearchHit]) -> list[SearchHit]:
        return sorted(hits, key=lambda h: (-h.score, h.case_id, h.chunk_id))

    # ---------- 结果组装 ----------

    def _to_payload(self, hit: SearchHit, principal: Principal,
                    action: str) -> dict[str, Any]:
        """命中 → API 载荷（**只含知识层脱敏内容** + 状态徽标 + 关键差异字段）。"""
        meta = dict(hit.metadata or {})
        chunk = KnowledgeChunk(
            case_id=hit.case_id, chunk_type=hit.chunk_type or "summary",
            content_version=hit.content_version, text=hit.text,
            metadata=meta, is_knowledge_layer=True, score=hit.score)
        case = self._store.get_case(hit.case_id) or {}
        return {
            "case_id": hit.case_id,
            "case_code": str(case.get("case_code") or hit.case_id),
            "chunk_id": chunk.chunk_id,
            "chunk_type": hit.chunk_type,
            "content_version": hit.content_version,
            "title": str(case.get("title") or meta.get("title") or ""),
            "text": hit.text,
            "score": round(float(hit.score), 4),
            "status": str(meta.get("status") or ""),
            "status_badge": self._status_badge(str(meta.get("status") or "")),
            "category": str(meta.get("category") or ""),
            "subcategory": str(meta.get("subcategory") or ""),
            "defect_mode_code": str(meta.get("defect_mode_code") or ""),
            "root_cause_code": str(meta.get("root_cause_code") or ""),
            "plant": str(meta.get("plant") or ""),
            "line_code": str(meta.get("line_code") or ""),
            # 密级：让前端能显示"这份案例属于几级"，与能否看到是两件事
            "cls_rank": int(case.get("cls_rank") or 0),
            "verified": bool(meta.get("verified")),
            "last_verified_at": str(meta.get("last_verified_at") or ""),
            "citation_count": int(meta.get("citation_count") or 0),
            "source_ticket_type": str(meta.get("source_ticket_type") or ""),
            # R1 裁定：检索响应**不返回原始来源单号**（8D/客诉单号可反查到客户与批次），
            # 只给不可反推、可对账的引用句柄；需要真号请走详情接口（带 X-Case-Trace: full）。
            "source_ticket_ref": source_ticket_ref(
                str(meta.get("source_ticket_id") or "")),
            "action": action,
            # 引用三元组（BR-05）：引用标识只出现 case_code，不出现 v1/v2
            "citation": {
                "case_id": hit.case_id,
                "chunk_id": chunk.chunk_id,
                "content_version": hit.content_version,
            },
        }

    def _case_payload(self, case: Mapping[str, Any],
                      principal: Principal) -> dict[str, Any]:
        """单案例详情（``read`` 动作；只返回脱敏知识层）。"""
        return {
            "case_id": str(case.get("case_id") or ""),
            "case_code": str(case.get("case_code") or case.get("case_id") or ""),
            "title": str(case.get("title") or ""),
            "summary": str(case.get("summary") or ""),
            "phenomenon": str(case.get("phenomenon") or ""),
            "root_cause_text": str(case.get("root_cause_text") or ""),
            "countermeasure_text": str(case.get("countermeasure_text") or ""),
            "category": str(case.get("category") or ""),
            "subcategory": str(case.get("subcategory") or ""),
            "defect_mode_code": str(case.get("defect_mode_code") or ""),
            "root_cause_code": str(case.get("root_cause_code") or ""),
            "status": str(case.get("status") or ""),
            "status_badge": self._status_badge(str(case.get("status") or "")),
            "plant": str(case.get("plant") or ""),
            "line_code": str(case.get("line_code") or ""),
            "cls_rank": int(case.get("cls_rank") or 0),
            "verified": bool(case.get("verified")),
            "last_verified_at": str(case.get("last_verified_at") or ""),
            "citation_count": int(case.get("citation_count") or 0),
            "content_version": int(case.get("content_version") or 1),
            "source_ticket_type": str(case.get("source_ticket_type") or ""),
            # 详情接口是**受控的窄面**：调用方已通过 case.read 且 cls_rank 达标，
            # 原始单号是溯源必需字段，因此这里给真号（API 层加 X-Case-Trace: full）。
            "source_ticket_id": str(case.get("source_ticket_id") or ""),
            "trace": "full",
            "layer": "knowledge",
            # 证据层**只给元数据提示，不给内容/链接**（证据层内容永不出检索侧）
            "evidence_available": bool(self._store.evidence_rows(
                str(case.get("case_id") or ""))),
            "visible_to": list(principal.roles),
        }

    @staticmethod
    def _status_badge(status: str) -> str:
        mapping = {
            "active": "生效",
            "deprecated": "待复核（降权）",
            "superseded": "已被新案例替代（降权）",
            "archived": "已归档（降权）",
        }
        return mapping.get(status, status or "未知")

    # ---------- 受限案例（可见存在、可申请） ----------

    def _scan_visible_restricted(
            self, principal: Optional[Principal], action: str,
            cap: int = STATS_SCAN_CAP,
            visible_ids: Optional[set[str]] = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """可见案例与受限案例的**权威清单**（受限清单 / 顶部统计卡共用一次扫描）。

        为什么抽出来：受限清单和统计卡片必须报同一套数字——现场最怕的就是
        "卡片上说 12 个受限、清单里只有 8 条"，一被追问就穿。两边各自过滤
        必然漂移，所以扫描只有这一份实现。

        判定口径与 search 第 8-9 步 + ``_second_stage`` 完全一致：
        候选 = ``compile_hard_only``（仅硬维度）命中的案例；其中
        ``compile`` 完整过滤器命中**且** ``AclEvaluator.authorize`` 放行 →
        可见；其余中 ``_is_disclosable`` 为真 → 受限（可申请）。
        编译器与评估器是两套独立实现（刻意不互相复用，见 ``_second_stage``），
        **同时满足才计入可见**；任何一边存疑都不计入——宁可少算，不可把
        不可见的算成可见。

        Args:
            visible_ids: 调用方（search）已确认的可见集。传入时受限判定加一层
                ``not in visible_ids``；统计卡不传（``None``），只用过滤器口径。
        """
        visible: list[dict[str, Any]] = []
        restricted: list[dict[str, Any]] = []
        try:
            filter_node, _reason = self._compiler.compile_explained(
                principal, action, policy=self._policy)
            if not filter_node.children:
                # FALSE_FILTER：动作被拒或主体不可用——什么都不可见（fail-closed）
                return visible, restricted
            hard_only = self._compiler.compile_hard_only(
                principal, action, policy=self._policy)
            # 只过滤不打分（见 MemoryCaseIndex.filtered_case_ids 的说明）
            filter_ids = set(self._index_service.hard_visible_case_ids(
                filter_node, cap))
            candidate_ids = self._index_service.hard_visible_case_ids(
                hard_only, cap)
        except Exception as exc:  # noqa: BLE001 - 受限清单/统计是增强项，绝不影响主流程
            logger.warning("[case_search] 可见/受限扫描失败: %s", exc)
            return visible, restricted
        if not candidate_ids:
            return visible, restricted
        # ★ 批量取行：逐条 get_case + eval_rows 会为每个候选各开一次 SQLite
        #   连接（演示库 15 个案例就把 search_e2e 从 55s 拖到 213s）。
        #   行内容与逐条调用完全一致，只是把 N×2 次建连压成 1 次。
        cases = self._store.cases_by_ids(candidate_ids)
        rows_by_case = self._store.eval_rows_many(candidate_ids)
        seen: set[str] = set()
        for case_id in candidate_ids:
            if case_id in seen:
                continue
            seen.add(case_id)
            case = cases.get(case_id)
            if not case:
                continue
            allowed = self._evaluator.authorize(
                principal, rows_by_case.get(case_id, []), action).allow
            if allowed and case_id in filter_ids:
                if visible_ids is None or case_id in visible_ids:
                    visible.append(case)
            elif (not allowed and case_id not in filter_ids
                  and (visible_ids is None or case_id not in visible_ids)
                  and _is_disclosable(case)):
                # ★ B10：先判"能不能披露存在"，再谈"能不能申请"。
                # 涉密 / 未结案 / 已撤权：连 case_id 都不进清单。
                restricted.append(case)
        return visible, restricted

    def _restricted(self, principal: Principal, action: str,
                    filter_node: FilterNode,
                    visible_ids: set[str]) -> list[dict[str, Any]]:
        """软维度挡住的案例 → "有，但你看不透"的清单（可发起访问申请）。

        这是**有意披露**：用户看不到、但确实存在的案例，如果只留一句"N 条不可见"，
        就没有任何合法的争取路径（要么忍，要么找人私下要截图——更糟）。
        所以给出标题级信息 + 申请入口。

        关键取舍：**只回 5 个字段**（case_id / title / category / reason /
        requestable）。给了标题 + 可申请性，用户就能走审批；给了正文、密级细节
        或 ACL 明细，这条通道立刻变成绕过过滤的读接口——那才是真泄漏。
        """
        _visible, restricted = self._scan_visible_restricted(
            principal, action, cap=RESTRICTED_SCAN_CAP, visible_ids=visible_ids)
        return [{
            "case_id": str(case.get("case_id") or ""),
            "title": str(case.get("title") or case.get("case_id") or ""),
            "category": str(case.get("category") or ""),
            "reason": "该案例属于其他职能域，可申请访问",
            "requestable": True,
        } for case in restricted[:RESTRICTED_MAX]]

    def stats(self, principal: Optional[Principal], action: str = "query",
              scan_cap: int = STATS_SCAN_CAP) -> dict[str, Any]:
        """案例域统计（顶部数字卡片）：口径与检索**完全同源**。

        复用 ``_scan_visible_restricted``（= search 的 filter + 二次授权 +
        披露判定），不引入任何新过滤逻辑；分类/密级/状态分布都从**可见案例**
        的行里聚合，因此卡片数字、受限清单、检索结果三者永远对得上。

        受限数可能被 ``scan_cap`` 截断（大库），此时 ``restricted_truncated``
        为真——前端标注"统计不完整"，不假装精确。
        """
        visible, restricted = self._scan_visible_restricted(
            principal, action, cap=scan_cap)
        by_category: dict[str, int] = {}
        by_subcategory: dict[str, int] = {}
        by_rank: dict[str, int] = {}
        by_status: dict[str, int] = {}
        def rank_key(case: Mapping[str, Any]) -> str:
            # ★ 不能写 ``case.get("cls_rank") or "?"``：``cls_rank == 0``（公开级，
            # 演示库里占六成）是 falsy，会被当成"未知"落进 "L?"，前端固定序里
            # 没有这一档 → 密级分布只剩 L1 几条，加起来对不上可见总数。
            raw = case.get("cls_rank")
            return "L" + str(raw) if raw is not None and raw != "" else "L?"

        for case in visible:
            for bucket, key in ((by_category, str(case.get("category") or "uncategorized")),
                                (by_subcategory, str(case.get("subcategory") or "")),
                                (by_rank, rank_key(case)),
                                (by_status, str(case.get("status") or "unknown"))):
                if key:
                    bucket[key] = bucket.get(key, 0) + 1
        return {
            "visible_total": len(visible),
            "restricted_total": len(restricted),
            "restricted_truncated": len(restricted) >= int(scan_cap),
            "by_category": by_category,
            "by_subcategory": by_subcategory,
            "by_rank": by_rank,
            "by_status": by_status,
        }

    # ---------- hidden_count / notice ----------

    def _hidden_count(self, principal: Principal, action: str,
                      filter_node: FilterNode, returned: int, k: int) -> int:
        """``hidden_count``（架构 §7.6 安全算法）。

        只在候选不足时才计算（性能与信息泄漏的双重考量），且只做**计数**：
        用"仅硬维度"过滤器与完整过滤器各取一次计数，差值即"因软维度看不到"的条数。
        硬维度已排除涉密/未结案/证据层/跨租户，因此不泄漏任何受保护内容。

        **单位是案例数**（R2 裁定）：一个案例有 4 个 chunk，若按 chunk 计数，
        页面会显示"另有 16 条不可见"而实际只藏了 6 个案例——现场一被追问就穿。
        因此两条路径都按 ``case_id`` 去重后再相减，响应里同时声明 ``hidden_count_unit``。
        """
        expose = bool(getattr(settings, "CASE_EXPOSE_HIDDEN_COUNT", True))
        if not expose:
            return 0
        only_when_short = bool(self._policy.setting("hidden_count_only_when_short", True))
        if only_when_short and returned >= max(1, k):
            return 0
        try:
            hard_only = self._compiler.compile_hard_only(principal, action,
                                                         policy=self._policy)
            n_hard = self._index_service.count_cases(hard_only)
            n_visible = self._index_service.count_cases(filter_node)
        except Exception as exc:  # noqa: BLE001 - 计数失败绝不能影响主流程
            logger.warning("[case_search] hidden_count 计算失败: %s", exc)
            return 0
        return max(0, int(n_hard) - int(n_visible))

    def _compose_notice(self, *, hits: int, k: int, hidden: int, dropped: int,
                        query: str) -> tuple[str, bool]:
        """提示语 + 低置信标记（诚实返回空，不编造）。"""
        low_confidence = False
        parts: list[str] = []
        if hits == 0:
            parts.append("未找到你有权限查看的相关案例")
            if hidden > 0:
                parts.append(f"另有 {hidden} 条同类案例因权限范围不可见，可申请授权")
            elif dropped:
                parts.append("部分命中因二次授权校验被丢弃（索引可能过期，已触发对账）")
            elif not str(query or "").strip():
                parts.append("查询为空：请描述现象/缺陷模式")
        else:
            if hits < max(1, k):
                parts.append(f"结果少于 {k} 条（已如实返回，不放宽过滤补齐）")
            if hidden > 0:
                parts.append(f"另有 {hidden} 条因权限范围不可见")
            if dropped:
                parts.append(f"{dropped} 条命中未通过二次授权校验，已丢弃")
        return "；".join(parts), low_confidence

    # ---------- 拒绝 / 降级 ----------

    def _deny(self, principal: Optional[Principal], request: SearchRequest,
              reason: str, *, audit_action: str,
              code: str = "CASE_ACTION_DENIED") -> SearchResponse:
        """拒绝：返回空结果 + 原因 + 错误码（**不抛异常**，避免异常做权限侧信道）。"""
        self._audit.append(
            actor=(principal.user_id if principal else ""),
            action=audit_action,
            detail={"reason": reason[:120], "channel": request.channel})
        return SearchResponse(hits=[], hidden_count=0, degraded=False,
                              notice=reason, action=request.action,
                              denied_reason=reason, acl_denied=True,
                              denied_code=code,
                              backend=self._index_service.index.backend)

    def _degrade(self, request: SearchRequest, reason: str) -> SearchResponse:
        """索引不可用 → ``degraded=true`` + 空结果（不编造，沿用现有降级语义）。"""
        logger.warning("[case_search] 降级: %s", reason)
        return SearchResponse(hits=[], hidden_count=0, degraded=True,
                              notice=reason, action=request.action,
                              denied_reason="", backend="unavailable")

    # ---------- 供 QA / API 复用 ----------

    def to_chunks(self, hits: Sequence[Mapping[str, Any]]) -> list[KnowledgeChunk]:
        """把命中载荷转回 ``KnowledgeChunk``（**只允许知识层进 prompt**，闸门 4）。"""
        chunks: list[KnowledgeChunk] = []
        for hit in hits or ():
            chunks.append(KnowledgeChunk(
                case_id=str(hit.get("case_id") or ""),
                chunk_type=str(hit.get("chunk_type") or "summary"),
                content_version=int(hit.get("content_version") or 1),
                text=str(hit.get("text") or ""),
                is_knowledge_layer=True,
            ))
        return chunks
