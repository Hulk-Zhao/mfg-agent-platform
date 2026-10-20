"""CaseOntologyGapService：**本体缺口登记申请**（PRD FR-09）。

## 为什么需要它

"术语归一落空"是检索链路上最贵的一种失败：现场说"雪花点"，词表里只有"锡珠"——
归一失败 → 同义扩展扩不出词 → 关系扩展也扩不出词 → 用户看到的是"没案例"，
而真相是"这个词还没登记"。用户此刻最该做的不是换个词再试，而是**把这个缺口登记下来**。

## 与既有红线的对齐

1. **不自动入词表**（承接 N7）：审批通过只是把申请置为 ``approved``，
   真正的词条登记仍由质量体系在词表 YAML 里做（需要人工归并同义词，避免"同义爆炸"）。
   按钮/接口都不存在"审批即写词表"的路径——那正是 N7 禁止的自动本体抽取变体。
2. **走既有审批语义**：门禁复用 ``case.ingest``（本体词条与案例同属知识资产写入）；
   职责分离照旧——**不能批自己提的申请**；驳回必须写理由（与访问申请同口径）。
3. **两条治理链分开**：审计链只记哈希与计数；本表才记"申请的是什么词"
   （审计的 ``FORBIDDEN_DETAIL_KEYS`` 明确禁止明文，二者不能混用）。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ...common.case_schema import connect, ensure_case_schema, resolve_case_db
from ..contracts.case_acl import Principal
from ..contracts.ontology import OntologyRegistry, get_ontology_registry
from .case_audit import CaseAuditLog
from .case_store import CaseStore

logger = logging.getLogger(__name__)

__all__ = [
    "GapError",
    "GapInvalid",
    "GapNotFound",
    "GapTransitionError",
    "GapAlreadyRegistered",
    "CaseOntologyGapService",
    "VALID_VOCABS",
    "GAP_STATUS",
]

#: 可登记的词表（对策词表也开放：现场"怎么修"的说法最容易没登记）。
VALID_VOCABS: tuple[str, ...] = ("defect_mode", "root_cause", "countermeasure")
GAP_STATUS: tuple[str, ...] = ("pending", "approved", "rejected")
#: 审批门禁动作：本体词条与案例同属"知识资产写入"，复用同一权限点
#: （生产若要拆分，在 case_roles.yaml 里加 ``case.ontology.approve`` 即可，服务端零改动）。
DECIDE_ACTION = "case.ingest"
#: 单个术语/现象描述的长度上限（防止把整段工单描述塞进来当词条）。
TERM_MAX_LEN = 60
HINT_MAX_LEN = 120


class GapError(Exception):
    """本体缺口登记的领域异常（API 层映射为 4xx）。"""


class GapInvalid(GapError):
    """入参非法（词表未知、术语为空、超长等）。"""


class GapNotFound(GapError):
    """申请不存在。"""


class GapTransitionError(GapError):
    """状态机不允许（已裁决的申请不能再裁决）。"""


class GapAlreadyRegistered(GapError):
    """该词已在受控词表中——不需要登记（防"重复造词"）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gap_id(tenant: str, vocab: str, term: str) -> str:
    """稳定但不泄露内容的 id（同词同租户 = 同 id，便于对账与幂等）。"""
    digest = hashlib.sha256(
        f"{tenant}|{vocab}|{term}".encode("utf-8")).hexdigest()[:12]
    return f"OG-{digest.upper()}"


@dataclass(frozen=True)
class _Request:
    vocab: str
    term: str
    code: str = ""
    hint: str = ""
    source: str = "manual"


class CaseOntologyGapService:
    """本体缺口登记（申请 / 列表 / 裁决）。"""

    def __init__(self, store: CaseStore,
                 audit: Optional[CaseAuditLog] = None,
                 ontology: Optional[OntologyRegistry] = None,
                 rbac: Optional[Any] = None) -> None:
        ensure_case_schema(store.db_path)
        self.db_path = resolve_case_db(store.db_path)
        self._audit = audit or CaseAuditLog(store.db_path)
        self._ontology = ontology or get_ontology_registry()
        if rbac is None:
            from ..security.case_rbac import get_case_rbac

            rbac = get_case_rbac()
        self._rbac = rbac

    # ---------- 申请 ----------

    def request(self, principal: Principal, *, vocab: str, term: str,
                code: str = "", hint: str = "", source: str = "manual",
                channel: str = "http") -> dict[str, Any]:
        """登记一条本体缺口申请（**只入申请队列，不入词表**）。

        Raises:
            GapInvalid: 词表未知 / 术语为空 / 超长。
            GapAlreadyRegistered: 该词已在受控词表里（不需要登记）。
        """
        if principal is None or not principal.user_id:
            raise GapInvalid("无身份主体：请先建立案例会话")
        vocabulary = str(vocab or "").strip()
        if vocabulary not in VALID_VOCABS:
            raise GapInvalid(f"词表必须是 {list(VALID_VOCABS)}，收到 {vocab!r}")
        text = " ".join(str(term or "").split())
        if not text:
            raise GapInvalid("请填写要登记的术语或现场说法（如「雪花点」）")
        if len(text) > TERM_MAX_LEN:
            raise GapInvalid(f"术语长度不能超过 {TERM_MAX_LEN} 字")
        hint_text = " ".join(str(hint or "").split())[:HINT_MAX_LEN]

        # 已在词表里（含同义词命中）→ 明确拒绝，并告知规范名（防重复造词）
        existing = self._ontology.normalize(vocabulary, text)
        if existing:
            found = self._ontology.term(vocabulary, existing)
            raise GapAlreadyRegistered(
                f"「{text}」已在受控词表中（规范名：{found.name if found else existing}），"
                "无需登记")

        payload = _Request(vocab=vocabulary, term=text,
                           code=str(code or "").strip()[:32],
                           hint=hint_text, source=str(source or "manual")[:16])
        gap_id = _gap_id(str(principal.tenant or ""), payload.vocab, payload.term)
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT * FROM case_ontology_gap WHERE gap_id=?",
                               (gap_id,)).fetchone()
            if row is not None and str(row["status"]) == "pending":
                # 幂等：同一词重复提交 → 返回既有申请（不产生第二张待办）
                self._audit.append(
                    actor=principal.user_id, action="case.ontology.gap.duplicated",
                    detail={"vocab": payload.vocab, "gap_id": gap_id})
                return {**dict(row), "duplicated": True,
                        "next_step": "该术语已有待审申请，无需重复提交（由质量体系统一裁决）"}
            created = _now_iso()
            conn.execute(
                "INSERT INTO case_ontology_gap (gap_id, vocab, term, code_proposed, "
                "phenomenon_hint, source, requester_id, requester_dept, requester_role, "
                "status, approver_id, decision_reason, created_at, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', ?, '') "
                "ON CONFLICT(gap_id) DO UPDATE SET vocab=excluded.vocab, "
                "term=excluded.term, code_proposed=excluded.code_proposed, "
                "phenomenon_hint=excluded.phenomenon_hint, source=excluded.source, "
                "requester_id=excluded.requester_id, requester_dept=excluded.requester_dept, "
                "requester_role=excluded.requester_role, status='pending', "
                "approver_id='', decision_reason='', created_at=excluded.created_at, "
                "decided_at=''",
                (gap_id, payload.vocab, payload.term, payload.code, payload.hint,
                 payload.source, principal.user_id, str(principal.dept or ""),
                 ",".join(str(r) for r in principal.roles or ()), created))
            conn.commit()
        finally:
            conn.close()
        self._audit.append(
            actor=principal.user_id, action="case.ontology.gap.requested",
            detail={"vocab": payload.vocab, "gap_id": gap_id, "channel": channel})
        logger.info("[case_gap] 登记申请 %s（%s/%s）", gap_id, payload.vocab, payload.term)
        return self.get(gap_id) | {
            "duplicated": False,
            "next_step": "已进入本体词条登记审批队列；审批通过后由质量体系在词表中登记"
                         "（系统不自动写入词表，需人工归并同义词）",
        }

    # ---------- 查询 ----------

    def get(self, gap_id: str) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT * FROM case_ontology_gap WHERE gap_id=?",
                               (str(gap_id or ""),)).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else {}

    def list(self, *, status: Optional[str] = None, requester_id: Optional[str] = None,
             limit: int = 50) -> list[dict[str, Any]]:
        """申请列表（可按状态 / 申请人过滤）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            target = str(status).strip()
            if target not in GAP_STATUS:
                raise GapInvalid(f"状态必须是 {list(GAP_STATUS)}，收到 {status!r}")
            clauses.append("status = ?")
            params.append(target)
        if requester_id:
            clauses.append("requester_id = ?")
            params.append(str(requester_id))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM case_ontology_gap" + where
                + " ORDER BY created_at DESC, gap_id DESC LIMIT ?",
                (*params, max(1, min(int(limit or 50), 200)))).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def pending_count(self) -> int:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM case_ontology_gap WHERE status='pending'"
            ).fetchone()
            return int(row["n"] if row else 0)
        finally:
            conn.close()

    # ---------- 裁决 ----------

    def decide(self, principal: Principal, gap_id: str, *, approve: bool,
               reason: str = "", channel: str = "http") -> dict[str, Any]:
        """裁决一条登记申请（**批准 ≠ 写入词表**）。

        三条门禁（与跨部门访问申请同口径）：
        1. 权限：需持有 ``case.ingest``（质量/工艺岗）；
        2. 职责分离：不能裁决自己提的申请；
        3. 状态机：只有 ``pending`` 可裁决；驳回必须写理由。
        """
        if principal is None or not principal.user_id:
            raise GapInvalid("无身份主体：请先建立案例会话")
        row = self.get(gap_id)
        if not row:
            raise GapNotFound(f"登记申请不存在: {gap_id}")
        if str(row.get("status")) != "pending":
            raise GapTransitionError(
                f"申请 {gap_id} 已是 {row.get('status')}，不能再裁决")
        if not self._rbac.has_any(principal.roles, DECIDE_ACTION):
            raise GapError(
                f"角色 {list(principal.roles)} 不持有 {DECIDE_ACTION}（本体词条登记需质量/工艺岗）")
        if str(row.get("requester_id") or "") == str(principal.user_id or ""):
            raise GapError("不能裁决自己提交的登记申请（职责分离）")
        text = str(reason or "").strip()
        if not approve and not text:
            raise GapInvalid("驳回登记申请必须写明理由（词条不登记会影响所有同类问句）")

        new_status = "approved" if approve else "rejected"
        conn = connect(self.db_path)
        try:
            conn.execute(
                "UPDATE case_ontology_gap SET status=?, approver_id=?, "
                "decision_reason=?, decided_at=? WHERE gap_id=? AND status='pending'",
                (new_status, principal.user_id, text[:120], _now_iso(),
                 str(gap_id or "")))
            conn.commit()
        finally:
            conn.close()
        action = "case.ontology.gap.approved" if approve else "case.ontology.gap.denied"
        self._audit.append(actor=principal.user_id, action=action,
                           detail={"gap_id": str(gap_id or ""), "channel": channel})
        return self.get(gap_id) | {
            "vocabulary_written": False,
            "next_step": ("已批准：请质量体系在词表 YAML 中登记该词条（含同义词归并），"
                          "系统不自动写入词表" if approve
                          else "已驳回：申请人与后续同类问句仍按现有词表口径处理"),
        }
