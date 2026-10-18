"""跨部门访问申请服务（授权 + 审批）。

为什么走"申请"而不是"放宽过滤"：现场确实会出现"别的部门的案例对我有用"
（设备工程师想看质量部门的制程案例）。这时正确动作是**留下授权痕迹的特批**，
而不是让检索侧对谁都松一点。设计要点：

1. **审批通过 = 往 ``case_acl`` 写一行 ``dim="user", value="user:<id>"``**，
   然后重算 ``visibility_class`` + 重建该案例的索引 chunk。
   这样向量侧 ``acl_tokens`` 与关系库权威 ACL **同时**变，
   :class:`AclEvaluator` 完全不用改——**不另起第二套判定**是本设计的核心。
2. ``allow_widen=True`` 只在这里（且只在"审批通过"那一刻）传，**永远不接受
   请求体传入**。BR-12「ACL 只可收紧不可放宽」对普通写路径仍然成立。
3. 涉密（``cls_rank >= 3``）与未结案（review/draft/rejected/withdrawn）**不走申请通道**：
   前者是保密红线，后者是内容还不该被引用，都不能靠"谁批一下"解决。
4. 授权带有效期（``CASE_ACCESS_GRANT_TTL_DAYS``，默认 30 天），
   到期由 :meth:`CaseAccessService.reap_expired` 惰性回收（移除 ACL 行 + 重建索引）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from ..contracts.case_acl import Principal
from ..contracts.case_index import IndexHealth  # noqa: F401 - 类型保持一致
from .case_audit import CaseAuditLog
from .case_index import CaseIndexService
from .case_store import CaseStore

logger = logging.getLogger(__name__)

__all__ = [
    "AccessRequestError",
    "AccessNotFound",
    "AccessDuplicate",
    "AccessAlreadyVisible",
    "AccessNotRequestable",
    "AccessNotApprover",
    "AccessTransitionError",
    "AccessRequest",
    "CaseAccessService",
]

#: 允许走申请通道的案例状态（未结案/已撤权不走）。
REQUESTABLE_STATUS: frozenset[str] = frozenset({"active", "deprecated"})
#: 密级红线：>= 该值一律不可申请。
SECRET_CLS_RANK = 3
#: 申请理由最少字数（低于此值信息量不足，审批人无从判断）。
MIN_REASON_LEN = 10

#: 状态机（pending 是唯一可撤回/裁决的中间态）。
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("approved", "rejected", "expired", "cancelled"),
    "approved": ("expired",),
    "rejected": (),
    "expired": (),
    "cancelled": (),
}


class AccessRequestError(Exception):
    """访问申请异常基类。"""


class AccessNotFound(AccessRequestError):
    """申请不存在。"""


class AccessDuplicate(AccessRequestError):
    """同一 (case_id, requester_id) 已有 pending 申请。"""


class AccessAlreadyVisible(AccessRequestError):
    """已经能看到，不该申请。"""


class AccessNotRequestable(AccessRequestError):
    """该案例不可申请（涉密 / 未结案）。"""


class AccessNotApprover(AccessRequestError):
    """无权审批（不持有该案例的职能部门权限）。"""


class AccessTransitionError(AccessRequestError):
    """非法状态流转。"""


@dataclass(frozen=True)
class AccessRequest:
    """一条访问申请（结构化视图；持久化行由 :meth:`CaseAccessService.get` 返回）。"""

    request_id: str
    case_id: str
    requester_id: str
    requester_dept: str
    requester_role: str
    reason: str
    scope: str
    status: str
    approver_id: str = ""
    decision_reason: str = ""
    created_at: str = ""
    decided_at: str = ""
    expires_at: str = ""

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "AccessRequest":
        """DB 行 → 结构化申请。"""
        return cls(**{key: row.get(key, "") for key in
                      ("request_id", "case_id", "requester_id", "requester_dept",
                       "requester_role", "reason", "scope", "status",
                       "approver_id", "decision_reason", "created_at",
                       "decided_at", "expires_at")})

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "case_id": self.case_id,
            "requester_id": self.requester_id,
            "requester_dept": self.requester_dept,
            "requester_role": self.requester_role,
            "reason": self.reason,
            "scope": self.scope,
            "status": self.status,
            "approver_id": self.approver_id,
            "decision_reason": self.decision_reason,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "expires_at": self.expires_at,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


class CaseAccessService:
    """跨部门访问申请：提交 / 待办 / 裁决 / 回收。"""

    def __init__(self, store: CaseStore, index_service: CaseIndexService,
                 audit: Optional[CaseAuditLog] = None) -> None:
        self._store = store
        self._index_service = index_service
        self._audit = audit or CaseAuditLog(store.db_path)

    # ---------- 提交 ----------

    def request_access(self, principal: Principal, case_id: str, *,
                       reason: str, scope: str = "grant") -> dict[str, Any]:
        """提交访问申请（不直接授权；授权只在审批通过那一刻发生）。"""
        text = str(reason or "").strip()
        if len(text) < MIN_REASON_LEN:
            raise AccessRequestError(
                f"申请理由至少 {MIN_REASON_LEN} 字：审批人需要知道你要拿这条案例做什么")
        if scope not in ("grant", "preview"):
            raise AccessRequestError(f"未知的申请范围: {scope!r}")

        case = self._store.get_case(case_id)
        if case is None:
            # 不存在/无权 → 同一个 404 语义（不通过申请通道暴露存在性）
            raise AccessNotFound("案例不存在，或你没有查看权限")

        cls_rank = int(case.get("cls_rank") or 0)
        status = str(case.get("status") or "")
        if cls_rank >= SECRET_CLS_RANK:
            raise AccessNotRequestable(
                f"涉密案例（L{cls_rank}）不走访问申请通道：保密红线不可由审批豁免")
        if status not in REQUESTABLE_STATUS:
            raise AccessNotRequestable(
                f"案例当前状态 {status!r} 不接受访问申请（仅 {sorted(REQUESTABLE_STATUS)}）")

        # 已经能看 → 不建申请（避免用申请通道刷审计）
        from ..security.acl_evaluator import get_acl_evaluator

        decision = get_acl_evaluator().authorize(
            principal, self._store.eval_rows(case_id), "read")
        if decision.allow:
            raise AccessAlreadyVisible("你已经可以查看该案例，无需申请")

        existing = self._pending_for(case_id, principal.user_id)
        if existing is not None:
            raise AccessDuplicate(
                f"该案例已有待审批申请（{existing}），请等待职能部门处理")

        request_id = "AR-" + uuid.uuid4().hex[:12].upper()
        created = _iso(_now())
        with self._store._conn() as conn:   # noqa: SLF001 - 本服务独占该表
            conn.execute(
                "INSERT INTO case_access_request (request_id, case_id, "
                "requester_id, requester_dept, requester_role, reason, scope, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (request_id, str(case_id), principal.user_id,
                 principal.dept, ",".join(principal.roles), text, scope, created))
        self._audit.append(actor=principal.user_id, action="case.access.requested",
                           case_id=case_id,
                           detail={"request_id": request_id, "scope": scope,
                                   "dept": principal.dept})
        logger.info("[case_access] 收到访问申请 %s：%s → %s", request_id,
                    principal.user_id, case_id)
        row = self.get(request_id)
        return row or {"request_id": request_id}

    # ---------- 待办 / 裁决 ----------

    def list_pending(self, *, dept: str = "", status: str = "pending"
                     ) -> list[dict[str, Any]]:
        """职能部门视角的待办（``dept`` 为空则返回全部待审批）。"""
        sql = ("SELECT * FROM case_access_request WHERE status=?")
        params: list[Any] = [str(status or "pending")]
        if dept:
            sql += " AND requester_dept<>?"
            params.append(str(dept))
        sql += " ORDER BY created_at DESC, request_id"
        with self._store._conn() as conn:   # noqa: SLF001
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_for_requester(self, requester_id: str
                           ) -> list[dict[str, Any]]:
        """申请人自己的申请历史（含已裁决，用于卡片右上角状态）。"""
        with self._store._conn() as conn:   # noqa: SLF001
            rows = conn.execute(
                "SELECT * FROM case_access_request WHERE requester_id=? "
                "ORDER BY created_at DESC", (str(requester_id),)).fetchall()
        return [dict(row) for row in rows]

    def decide(self, principal: Principal, request_id: str, *,
               approve: bool, reason: str = "") -> dict[str, Any]:
        """审批：批准 → 写入 ``case_acl`` 的 user 维度授权（带有效期）。"""
        row = self.get(request_id)
        if row is None:
            raise AccessNotFound(f"申请不存在: {request_id}")
        if str(row.get("status")) != "pending":
            raise AccessTransitionError(
                f"申请 {request_id} 已是 {row.get('status')}，不能再裁决")

        case = self._store.get_case(str(row.get("case_id") or ""))
        if case is None:
            raise AccessNotFound("案例不存在，无法批准")
        self._assert_functional_dept(principal, case)

        now = _now()
        target = "approved" if approve else "rejected"
        with self._store._conn() as conn:   # noqa: SLF001
            conn.execute(
                "UPDATE case_access_request SET status=?, approver_id=?, "
                "decision_reason=?, decided_at=? WHERE request_id=?",
                (target, principal.user_id, str(reason or "").strip(),
                 _iso(now), str(request_id)))

        expires_at = ""
        if approve:
            expires_at = self._grant(principal, case, row)
        self._audit.append(
            actor=principal.user_id,
            action=f"case.access.{'approved' if approve else 'rejected'}",
            case_id=str(row.get("case_id") or ""),
            detail={"request_id": request_id,
                    "requester_id": str(row.get("requester_id") or ""),
                    "expires_at": expires_at})
        logger.info("[case_access] 申请 %s → %s by %s", request_id, target,
                    principal.user_id)
        return self.get(request_id) or {}

    def cancel(self, principal: Principal, request_id: str) -> dict[str, Any]:
        """申请人撤回自己的 pending 申请。"""
        row = self.get(request_id)
        if row is None:
            raise AccessNotFound(f"申请不存在: {request_id}")
        if str(row.get("requester_id")) != principal.user_id:
            raise AccessNotRequestable("只能撤回自己提交的申请")
        self._transition(row, "cancelled", actor=principal.user_id)
        self._audit.append(actor=principal.user_id, action="case.access.cancelled",
                           case_id=str(row.get("case_id") or ""), detail={"request_id": request_id})
        return self.get(request_id) or {}

    # ---------- 回收 ----------

    def reap_expired(self) -> int:
        """回收到期授权：移除 user 维度 ACL 行 + 重建索引 + 申请置 expired。"""
        now = _now()
        with self._store._conn() as conn:   # noqa: SLF001
            rows = conn.execute(
                "SELECT request_id, case_id, requester_id FROM case_access_request "
                "WHERE status='approved' AND expires_at<>'' AND expires_at<=?",
                (_iso(now),)).fetchall()
            if not rows:
                return 0
            for row in rows:
                conn.execute(
                    "DELETE FROM case_acl WHERE case_id=? AND dim='user' AND value=?",
                    (str(row["case_id"]), str(row["requester_id"])))
                conn.execute(
                    "UPDATE case_access_request SET status='expired' WHERE request_id=?",
                    (str(row["request_id"]),))
        for row in rows:
            self._index_service.sync_case(str(row["case_id"]))
            self._audit.append(actor="system", action="case.access.expired",
                               case_id=str(row["case_id"]),
                               detail={"request_id": str(row["request_id"])})
        return len(rows)

    # ---------- 内部 ----------

    def _grant(self, approver: Principal, case: dict[str, Any],
               row: dict[str, Any]) -> str:
        """写入 user 维度授权并重算可见性、重建索引。返回到期时间。"""
        days = _grant_ttl_days()
        expires = _iso(_now() + timedelta(days=days))
        entries = [dict(item) for item in self._store.acl_entries(str(case["case_id"]))]
        # 值存**纯 user_id**（与 dept→quality / plant→P1 的口径一致）：
        # 令牌前缀由 `compute_acl_tokens` / `principal_token` 统一拼，
        # 这里再写一遍 "user:" 会变成 "user:user:xxx"，永远匹配不上。
        user_id = str(row["requester_id"])
        keep = [item for item in entries
                if not (str(item["dim"]) == "user" and str(item["value"]) == user_id)]
        keep.append({"dim": "user", "value": user_id, "effect": "allow"})
        # ★ allow_widen=True 的**唯一**合法来源：审批通过的服务端路径。
        # BR-12（只可收紧）对普通写路径仍然成立；这里放宽是"审批"这个动作本身，
        # 且必须同时重算 visibility_class 并重建索引，让向量侧与关系库同时生效。
        self._store.set_acl(str(case["case_id"]), keep, allow_widen=True)
        with self._store._conn() as conn:   # noqa: SLF001
            conn.execute(
                "UPDATE case_access_request SET expires_at=? WHERE request_id=?",
                (expires, str(row["request_id"])))
        self._index_service.sync_case(str(case["case_id"]))
        logger.info("[case_access] 授权 %s ← %s（%s 后到期）", case["case_id"],
                    row["requester_id"], expires)
        return expires

    def _assert_functional_dept(self, approver: Principal,
                                case: dict[str, Any]) -> None:
        """审批人必须持有该案例的**职能部门**权限。

        判据：``case_roles.yaml`` 里该案例 ``owner_dept`` 对应职能域的职能岗，
        或 ``case_acl`` 里已有该职能部门维度的授权。随便哪个角色都能批是不行的。
        """
        from ..security.case_rbac import get_case_rbac

        owner_dept = str(case.get("owner_dept") or "")
        department_ids = {
            str(row["value"]) for row in self._store.acl_entries(str(case["case_id"]))
            if str(row["dim"]) == "dept" and str(row["effect"]) == "allow"
        }
        if owner_dept:
            department_ids.add(owner_dept)
        if not department_ids:
            # 全员开放的案例：任何持有 case.read 的角色都可裁决（无处可推则不拦）
            return
        if approver.dept and approver.dept in department_ids:
            return
        # 职能部门主管（qa_manager / equipment_manager …）按 RBAC 兜底
        if get_case_rbac().has_any(approver.roles, "case.read") \
                and str(approver.dept) in department_ids:
            return
        raise AccessNotApprover(
            f"只有 {sorted(department_ids)} 的职能岗可以审批该案例的访问申请"
            f"（你属于 {approver.dept or '（无部门）'}）")

    def _transition(self, row: dict[str, Any], target: str, *,
                    actor: str) -> None:
        allowed = _TRANSITIONS.get(str(row["status"]), ())
        if target not in allowed:
            raise AccessTransitionError(
                f"非法状态流转: {row['status']} → {target}（允许：{list(allowed)}）")
        with self._store._conn() as conn:   # noqa: SLF001
            conn.execute(
                "UPDATE case_access_request SET status=?, decided_at=? "
                "WHERE request_id=? AND status=?",
                (target, _iso(_now()), str(row["request_id"]), str(row["status"])))

    def get(self, request_id: str) -> Optional[dict[str, Any]]:
        with self._store._conn() as conn:   # noqa: SLF001
            row = conn.execute(
                "SELECT * FROM case_access_request WHERE request_id=?",
                (str(request_id),)).fetchone()
        return dict(row) if row else None

    def _pending_for(self, case_id: str, requester_id: str) -> Optional[str]:
        with self._store._conn() as conn:   # noqa: SLF001
            row = conn.execute(
                "SELECT request_id FROM case_access_request "
                "WHERE case_id=? AND requester_id=? AND status='pending'",
                (str(case_id), str(requester_id))).fetchone()
        return str(row["request_id"]) if row else None

    def requestable_hint(self, principal: Principal, case_id: str
                         ) -> dict[str, Any]:
        """这条案例"能不能申请"（前端据此显示按钮）。不泄漏任何正文。"""
        case = self._store.get_case(case_id)
        if case is None:
            return {"requestable": False, "reason": "案例不存在或不可见"}
        cls_rank = int(case.get("cls_rank") or 0)
        status = str(case.get("status") or "")
        if cls_rank >= SECRET_CLS_RANK:
            return {"requestable": False,
                    "reason": f"涉密案例（L{cls_rank}）不走访问申请通道"}
        if status not in REQUESTABLE_STATUS:
            return {"requestable": False, "reason": "案例尚未结案，暂不受理访问申请"}
        from ..security.acl_evaluator import get_acl_evaluator

        if get_acl_evaluator().authorize(
                principal, self._store.eval_rows(case_id), "read").allow:
            return {"requestable": False, "reason": "你已可查看，无需申请"}
        if self._pending_for(case_id, principal.user_id):
            return {"requestable": False, "reason": "已有待审批申请"}
        return {"requestable": True, "reason": "可申请跨部门访问，等待职能部门审批"}


def _grant_ttl_days() -> int:
    try:
        from ...common.config import settings

        return max(1, int(getattr(settings, "CASE_ACCESS_GRANT_TTL_DAYS", 30)))
    except Exception:  # noqa: BLE001
        return 30