"""CaseIngestService：入库管道（**顺序不可颠倒**）。

架构位置：§8.2（管道八步）、PRD BR-03 / BR-06 / BR-11。

本服务实现的步骤（与架构 §8.2 一一对应）：

1. 来源校验（BR-11）：``source_ticket_type`` + ``source_ticket_id`` 非空，否则拒绝；
2. 结构化抽取 + 受控词表归一化（未命中 → 兜底码；无兜底码 → 明确报错）；
3. 敏感识别 + **脱敏固化**（入库前，不是查询时）；
4. **漏脱复检**（对脱敏后文本重跑识别器，命中即拒绝入库，不降级放行）；
5. 落库 ``status=review``（**不自动 approve**——Agent 只 propose，BR-07）；
6. 建 ACL（继承来源工单可见范围，只可收紧）+ 生成 chunk 映射行。

第 7-8 步（人工三签 → 发布 → 落索引）由 HITL ``case_publish`` 槽位驱动
（本 demo 未接会签，见 docs 偏差说明）：:meth:`CaseIngestService.publish` 提供一个
"审核通过"的最小实现，供 demo 与后续接 HITL 用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .case_index import CaseIndexService
from .case_store import (
    BUSINESS_ACL_DIMS,
    CaseStore,
    CaseStoreError,
    SourceTicketRequired,
)
from .redaction import RedactionError, RedactionService, get_redaction_service

logger = logging.getLogger(__name__)

__all__ = [
    "IngestError",
    "SourceTicketRequired",
    "RedactionFailed",
    "OntologyCodeUnknown",
    "CaseAlreadyActive",
    "IngestResult",
    "CaseIngestService",
    "TEXT_FIELDS",
]

#: 需要脱敏 + 复检的文本字段（知识层全部文本）。
TEXT_FIELDS: tuple[str, ...] = (
    "title", "summary", "phenomenon", "root_cause_text", "countermeasure_text",
)

#: 允许被重复 ingest 覆盖的状态（已 active 的案例不在其中）。
OVERWRITABLE_STATUSES: frozenset[str] = frozenset({"draft", "review", "rejected"})


class IngestError(Exception):
    """入库失败基类。"""


class CaseAlreadyActive(IngestError):
    """目标案例已是 active：拒绝覆盖（B2）。

    为什么必须是硬拒绝而不是"覆盖回 review"：``create_case`` 是 upsert，重复 ingest
    会把 ``status`` 写回 ``review``、把 ``content_version`` 固定为 1，并让
    ``sync_case`` 把这个案例**移出索引**——别人正在引用的案例会凭空消失。
    这是数据事故，不是演示瑕疵。要改内容请走"新增 content_version + 复审"通道。
    """


class RedactionFailed(IngestError):
    """漏脱复检失败（拒绝入库，管道纪律 3）。"""

    def __init__(self, message: str, problems: Optional[Mapping[str, list]] = None):
        super().__init__(message)
        self.problems = dict(problems or {})


class OntologyCodeUnknown(IngestError):
    """受控词表未命中且无兜底码。"""


@dataclass(frozen=True)
class IngestResult:
    """入库结果。"""

    case_id: str
    status: str
    chunks: int
    redaction_kinds: tuple[str, ...] = ()
    acl_tokens: tuple[str, ...] = ()
    visibility_class: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


class CaseIngestService:
    """入库管道（来源校验 → 抽取 → 脱敏 → 复检 → 落库 review）。"""

    def __init__(self, store: CaseStore, index_service: CaseIndexService,
                 redaction: Optional[RedactionService] = None,
                 ontology: Optional[Any] = None) -> None:
        self._store = store
        self._index_service = index_service
        self._redaction = redaction or get_redaction_service()
        if ontology is None:
            from ..contracts.ontology import get_ontology_registry

            ontology = get_ontology_registry()
        self._ontology = ontology

    # ---------- 主入口 ----------

    def ingest(self, draft: Mapping[str, Any]) -> IngestResult:
        """把一条草稿走完整条管道，落库为 ``status=review``（**不自动 approve**）。

        Args:
            draft: 至少包含 ``source_ticket_type`` / ``source_ticket_id`` /
                ``title`` / ``phenomenon`` / ``root_cause_text`` /
                ``countermeasure_text``；可选 ``defect_mode``（文本或 code）、
                ``root_cause``（文本或 code）、``category`` / ``subcategory`` /
                ``plant`` / ``line_code`` / ``owner_dept`` / ``cls_rank`` /
                ``author_role`` / ``acl`` / ``case_id``。

        Raises:
            SourceTicketRequired: 缺来源单据（BR-11）。
            RedactionFailed: 漏脱复检失败（409）。
            OntologyCodeUnknown: 词表未命中且无兜底码。
        """
        # ── 步 1：来源校验（BR-11）────────────────────────────────
        source_type = str(draft.get("source_ticket_type") or "").strip()
        source_id = str(draft.get("source_ticket_id") or "").strip()
        if not source_type or not source_id:
            raise SourceTicketRequired(
                "无来源单据的案例不得进入共享池（BR-11）：请提供 source_ticket_type "
                "与 source_ticket_id；个人草稿请勿走共享入库通道")

        notes: list[str] = []

        # ── 步 2：结构化抽取 + 词表归一化 ─────────────────────────
        defect_code = self._resolve_code(
            draft, code_key="defect_mode_code", text_key="defect_mode",
            vocab="defect_mode", fallback="DM-SMT-099", notes=notes)
        root_code = self._resolve_code(
            draft, code_key="root_cause_code", text_key="root_cause",
            vocab="root_cause", fallback=None, notes=notes)

        # ── 步 3：脱敏固化（入库前）────────────────────────────────
        raw_texts = {name: str(draft.get(name) or "") for name in TEXT_FIELDS}
        redacted, hits = self._redaction.redact_fields(raw_texts)

        # ── 步 4：漏脱复检（重跑识别器，命中即拒绝）──────────────────
        problems = self._redaction.verify_fields(redacted)
        if problems:
            kinds = sorted({name for name in problems})
            raise RedactionFailed(
                f"脱敏复检未通过（字段 {kinds} 仍有残留敏感信息），拒绝入库",
                problems=problems)

        # ── 步 5：落库 review（不自动 approve）────────────────────
        case_id = str(draft.get("case_id") or "").strip() or self._make_case_id(
            source_type, source_id)

        # 步 5.0（B2）：重复 ingest 的三条分支——绝不把 active 案例打回 review
        existing = self._store.get_case(case_id)
        existing_status = str((existing or {}).get("status") or "")
        if existing is not None and existing_status == "active":
            raise CaseAlreadyActive(
                f"案例 {case_id} 已发布（status=active），不得被重复入库覆盖："
                "请走「新增 content_version + 复审」通道"
                "（POST /cases/{id}/revise 或 HITL case_revise）")
        bump_version = 1
        if existing is not None:
            if existing_status not in OVERWRITABLE_STATUSES:
                raise IngestError(
                    f"案例 {case_id} 当前状态 {existing_status!r} 不接受覆盖"
                    f"（允许：{sorted(OVERWRITABLE_STATUSES)}）")
            # 覆盖同一草稿也要进版本：否则引用三元组会在内容变化后"看起来没变"
            bump_version = int(existing.get("content_version") or 1) + 1

        acl = self._normalize_acl(draft.get("acl") or [], notes=notes)
        record = {
            "case_id": case_id,
            "case_code": str(draft.get("case_code") or case_id),
            "tenant": str(draft.get("tenant") or self._default_tenant()),
            "category": str(draft.get("category") or "quality_case"),
            "subcategory": str(draft.get("subcategory") or "process_defect"),
            "status": "review",           # ← 关键：入库不发布
            "cls_rank": int(draft.get("cls_rank") or 1),
            "plant": str(draft.get("plant") or ""),
            "line_code": str(draft.get("line_code") or ""),
            "owner_dept": str(draft.get("owner_dept") or ""),
            "defect_mode_code": defect_code,
            "root_cause_code": root_code,
            "source_ticket_type": source_type,
            "source_ticket_id": source_id,
            "is_export_controlled": int(draft.get("is_export_controlled") or 0),
            "published_at": "",
            "last_verified_at": "",
            "verified": int(draft.get("verified") or 0),
            "citation_count": 0,
            "author_role": str(draft.get("author_role") or ""),
            "content_version": bump_version,
            "acl": acl,
            **redacted,
        }
        self._store.create_case(record, allow_widen_acl=True)
        tokens = self._apply_visibility(case_id, acl)

        # ── 步 6：chunk 映射行（review 状态不进索引，BR-06）─────────
        written = self._index_service.sync_case(case_id)

        kinds = tuple(sorted({hit.kind for hit in hits}))
        logger.info("[case_ingest] 入库完成: %s status=review redacted=%s",
                    case_id, list(kinds))
        return IngestResult(
            case_id=case_id,
            status="review",
            chunks=len(self._index_service.build_chunks(
                self._store.get_case(case_id) or {})),
            redaction_kinds=kinds,
            acl_tokens=tokens,
            visibility_class=0 if not tokens else 1,
            notes=tuple(notes) + (
                f"索引写入 {written} 条（review 状态不进索引，BR-06）",
            ),
        )

    # ---------- 发布（HITL 审核通过后的最小实现） ----------

    def publish(self, case_id: str, *, approver: str = "") -> int:
        """审核通过 → ``review → active`` → 落索引（返回写入 chunk 数）。

        生产路径必须由 HITL ``case_publish`` 三签驱动（propose/cosign/approve，
        一人一签 SoD）；本 demo 只留最小可运行入口，并强制记录审批人。
        """
        if not str(approver or "").strip():
            raise IngestError("发布必须记录审批人（approver），Agent 不得自行 approve")
        self._store.transition(case_id, "active", extra={
            "published_at": self._now_iso(),
            "last_verified_at": self._now_iso(),
        })
        written = self._index_service.sync_case(case_id)
        logger.info("[case_ingest] 发布 %s by %s（索引 %d 条）",
                    case_id, approver, written)
        return written

    # ---------- 内部 ----------

    def _resolve_code(self, draft: Mapping[str, Any], *, code_key: str,
                      text_key: str, vocab: str, fallback: Optional[str],
                      notes: list[str]) -> str:
        """受控词表归一化：显式 code 优先，其次文本归一化，最后兜底码。"""
        explicit = str(draft.get(code_key) or "").strip()
        if explicit:
            if not self._ontology.validate_code(vocab, explicit):
                raise OntologyCodeUnknown(
                    f"未受控的 {vocab} code: {explicit!r}"
                    f"（新增词条需走质量体系审批，见词表治理纪律）")
            return explicit
        text = str(draft.get(text_key) or "").strip()
        if text:
            code = self._ontology.normalize(vocab, text)
            if code:
                return code
            notes.append(f"{vocab} 文本 {text!r} 未命中词表")
        if fallback and self._ontology.validate_code(vocab, fallback):
            notes.append(f"{vocab} 使用兜底码 {fallback}")
            return fallback
        if not text:
            return ""
        raise OntologyCodeUnknown(
            f"{vocab} 文本 {text!r} 未命中受控词表且无兜底码："
            "请人工归并后重试（系统不自动新增词条）")

    def _normalize_acl(self, raw: Sequence[Mapping[str, Any]],
                       *, notes: list[str]) -> list[dict[str, Any]]:
        """ACL 条目整形：维度白名单 + 只允许 allow（deny 由策略统一表达）。"""
        out: list[dict[str, Any]] = []
        for entry in raw or ():
            if not isinstance(entry, Mapping):
                raise IngestError(f"ACL 条目格式非法: {entry!r}")
            dim = str(entry.get("dim") or "").strip()
            value = str(entry.get("value") or "").strip()
            effect = str(entry.get("effect") or "allow").strip().lower()
            if dim not in BUSINESS_ACL_DIMS:
                raise IngestError(
                    f"未知 ACL 维度 {dim!r}（合法：{list(BUSINESS_ACL_DIMS)}）")
            if effect != "allow":
                notes.append(f"ACL 条目 {dim}:{value} 的 effect={effect} 已忽略"
                             "（显式拒绝由 case_policy.yaml 统一表达）")
                continue
            if not value:
                raise IngestError(f"ACL 条目 {dim} 缺少 value")
            out.append({"dim": dim, "value": value, "effect": "allow"})
        return out

    def _apply_visibility(self, case_id: str,
                          acl: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        tokens = tuple(f"{e['dim']}:{e['value']}" for e in acl)
        self._store.set_visibility(case_id, 0 if not tokens else 1, tokens)
        return tokens

    def _default_tenant(self) -> str:
        from ...common.config import settings

        return str(getattr(settings, "CASE_DEFAULT_TENANT", "T001"))

    def _make_case_id(self, source_type: str, source_id: str) -> str:
        """无显式 case_id 时按来源单据生成（确定性、幂等）。"""
        raw = f"{source_type}::{source_id}"
        digest = __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest()[:10]
        prefix = {"8d": "QC-CC", "iqc": "QC-MD", "process": "QC-PD",
                  "equipment": "QC-EF", "manual": "RC-EX"}.get(
                      source_type.lower(), "QC-EX")
        return f"{prefix}-{digest}"

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
