"""数据查询权限申请 + 演示自动审批（与案例检索同构）。

为什么这样切分：与案例域的 ``case_access.py``（申请/裁决/授权）+ ``case_auto_approve.py``
（演示代批）保持同一套职责纪律——**申请** 与 **代批** 分离，代批复用同一条
「审批通过 → 写入授权」的路径，只在事件名上打 ``.demo`` 后缀显式标记「这不是真人点的」。

数据域与案例域的**结构差异**（不是标准不同）：案例有逐条 ACL，申请通过写 ``case_acl``
的 user 维度；数据域是**表级 × 角色**白名单（``TABLE_ACL``），没有逐行/逐用户过滤，
因此授权按**角色**落——写入 ``services/text2sql`` 的运行时叠加层（``grant_table_access``），
三个权限生效点（auditor / agents.office / demo.api）统一通过 ``table_acl()`` 读到它。

生产形态（预留）：把 :meth:`DataAccessService.demo_approve` 换成真实审批流
（OA / IM 审批）回调同一个"审批通过 → 授权"路径，服务端代码零改动；演示开关关闭时
只提交申请、等待人工审批，不自动放行。

演示纪律（与案例一致）：
1. 审批人身份来自**表的归属职责岗**（``OWNER_ROLES_BY_TABLE``，取自静态白名单），
   不是申请人自己——解析不出就 fail-closed（``ApproverUnavailable``）；
2. 触发代批者必须是申请人本人（不许拿别人的申请号刷授权）；
3. 存储为**进程内**（重启复位）：数据域没有像案例那样的关系库真相源，
   demo 用内存即可；测试用 ``reset_data_access()`` 隔离。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from ...common.config import settings
from .text2sql import TABLE_ACL, grant_table_access, revoke_table_access, table_acl

logger = logging.getLogger(__name__)

__all__ = [
    "DataAccessError",
    "RequestNotFound",
    "DuplicateRequest",
    "AlreadyAuthorized",
    "NotRequester",
    "ApproverUnavailable",
    "DemoDisabled",
    "OWNER_ROLES_BY_TABLE",
    "OWNER_LABEL",
    "DataAccessService",
    "get_data_access",
    "reset_data_access",
]

#: 申请理由最少字数（低于此值信息量不足，审批人无从判断）。
MIN_REASON_LEN = 6

#: 审批通过写入的决策理由（对外展示的申请记录里可见）。
DECISION_REASON = "职责岗审批通过"

#: 演示审批人的 user_id 前缀（与 /session 的 ``u-<role>`` 口径一致）。
_DEMO_APPROVER_PREFIX = "u-"

#: 受限表 → 归属职责岗的**优先序列**（取自该表静态白名单，用于解析演示审批人）。
#: 序列里第一个既在该表白名单、又不在申请人角色里的岗，即为代批身份。
OWNER_ROLES_BY_TABLE: dict[str, tuple[str, ...]] = {
    "material_inventory": ("material_control", "warehouse_manager", "supply_chain_manager"),
    "finished_inventory": ("warehouse_manager", "supply_chain_manager", "material_control"),
    "incoming_batch": ("supply_chain_manager", "material_control", "warehouse_manager"),
}

#: 表 → 职责岗的人话标签（弹窗「推断职责岗」一行用）。
OWNER_LABEL: dict[str, str] = {
    "material_inventory": "物控 / 仓储",
    "finished_inventory": "仓储管理",
    "incoming_batch": "供应链管理 / 物控",
}

#: 状态机（pending 是唯一可裁决的中间态）。
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("approved", "rejected", "expired"),
    "approved": ("expired",),
    "rejected": (),
    "expired": (),
}


class DataAccessError(Exception):
    """数据查询权限申请异常基类。"""


class RequestNotFound(DataAccessError):
    """申请不存在。"""


class DuplicateRequest(DataAccessError):
    """同一 (table, requester) 已有 pending 申请。"""


class AlreadyAuthorized(DataAccessError):
    """当前角色已可查询该数据，无需申请。"""


class NotRequester(DataAccessError):
    """触发者不是申请人本人（不允许代别人的申请触发审批）。"""


class ApproverUnavailable(DataAccessError):
    """解析不出有资格的演示审批人（fail-closed，不降级找"差不多的人"批）。"""


class DemoDisabled(DataAccessError):
    """演示自动审批开关未打开。"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _ttl_days() -> int:
    try:
        return max(1, int(getattr(settings, "DATA_ACCESS_GRANT_TTL_DAYS", 30)))
    except Exception:  # noqa: BLE001 - 配置异常不应让链路崩在有效期上
        return 30


class DataAccessService:
    """数据查询权限申请：提交 / 预检 / 演示代批 / 回收（进程内存储）。"""

    def __init__(self) -> None:
        self._requests: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    # ---------- 开关 ----------

    @property
    def enabled(self) -> bool:
        """演示自动审批是否启用：``CASE_DEMO_MODE`` 且 ``DATA_ACCESS_AUTO_APPROVE_DEMO``。"""
        return bool(getattr(settings, "CASE_DEMO_MODE", True)) and bool(
            getattr(settings, "DATA_ACCESS_AUTO_APPROVE_DEMO", True))

    # ---------- 提交 ----------

    def request_access(self, *, table: str, roles: Iterable[str], reason: str,
                       actor: str = "", dept: str = "") -> dict[str, Any]:
        """提交查询权限申请（**不直接授权**；授权只在审批通过那一刻发生）。"""
        table = str(table or "")
        if table not in TABLE_ACL:
            raise DataAccessError(f"未登记的数据表: {table!r}")
        role_set = self._normalize(roles)
        if not role_set:
            raise DataAccessError("缺少岗位角色，无法判定申请主体")
        text = str(reason or "").strip()
        if len(text) < MIN_REASON_LEN:
            raise DataAccessError(
                f"申请理由至少 {MIN_REASON_LEN} 字：审批人需要知道你要拿这条数据做什么")
        # 已经能查 → 不建申请（避免用申请通道刷事件）
        if role_set & table_acl(table):
            raise AlreadyAuthorized("当前岗位已可查询该数据，无需申请")
        if self._pending_for(table, actor):
            raise DuplicateRequest("该数据已有待审批申请，请等待职责岗处理")

        request_id = "DAR-" + uuid.uuid4().hex[:12].upper()
        row = {
            "request_id": request_id,
            "table": table,
            "dataset_label": OWNER_LABEL.get(table, table),
            "requester": str(actor or ""),
            "requester_dept": str(dept or ""),
            "roles": sorted(role_set),
            "reason": text,
            "status": "pending",
            "approver": "",
            "decision_reason": "",
            "created_at": _iso(_now()),
            "decided_at": "",
            "expires_at": "",
        }
        self._requests[request_id] = row
        self._append_event("data.access.requested", actor=str(actor or ""),
                           detail={"request_id": request_id, "table": table,
                                   "roles": sorted(role_set), "dept": str(dept or "")})
        logger.info("[data_access] 收到数据查询权限申请 %s：%s → %s",
                    request_id, actor, table)
        return dict(row)

    def preview(self, *, table: str, roles: Iterable[str], actor: str = "") -> dict[str, Any]:
        """不落任何库的预检（供响应告诉前端能否直接审批通过）。"""
        if not self.enabled:
            return {"available": False,
                    "reason": "该数据暂不支持自动审批，提交后将由职责岗人工审批"}
        if table not in TABLE_ACL:
            return {"available": False, "reason": f"未登记的数据表: {table!r}"}
        try:
            role_key, label = self._resolve_approver(table, roles)
        except ApproverUnavailable:
            return {"available": False,
                    "reason": "未找到该数据的归属职责岗，提交后将由人工审批"}
        return {"available": True, "approver_role": role_key, "owner_label": label,
                "note": "提交后将推送至职责岗审批，通过后授权即时生效"}

    # ---------- 裁决 ----------

    def demo_approve(self, request_id: str, *, actor: str = "") -> dict[str, Any]:
        """演示专用：模拟「职责岗审批通过」的回调，写入运行时授权叠加层。

        生产环境由真实审批流回调同一条"审批通过 → 授权"路径（本方法即该路径的演示实现）。
        """
        if not self.enabled:
            raise DemoDisabled("该数据暂不支持自动审批，请等待职责岗人工审批")
        # 取**存储对象本体**（``get()`` 返回副本，只用于外部只读消费）——代批要落库到申请行。
        row = self._requests.get(str(request_id))
        if row is None:
            raise RequestNotFound(f"申请不存在: {request_id}")
        status = str(row.get("status") or "")
        if status != "pending":
            raise DataAccessError(f"申请 {request_id} 已是 {status}，不能再审批")
        if str(row.get("requester") or "") != str(actor or ""):
            raise NotRequester("只能为本人提交的申请触发审批（申请与触发者不是同一个人）")

        table = str(row.get("table") or "")
        roles = list(row.get("roles") or [])
        approver_role, label = self._resolve_approver(table, roles)

        # 授权副作用只有这一条路：写入叠加层（三个生效点统一读 table_acl()）。
        grant_table_access(table, roles)
        now = _now()
        row["status"] = "approved"
        row["approver"] = _DEMO_APPROVER_PREFIX + approver_role
        row["decision_reason"] = DECISION_REASON
        row["decided_at"] = _iso(now)
        row["expires_at"] = _iso(now + timedelta(days=_ttl_days()))
        self._append_event("data.access.approved.demo", actor=row["approver"],
                           detail={"request_id": request_id, "table": table,
                                   "roles": roles, "expires_at": row["expires_at"],
                                   "demo": True, "simulated_approver": row["approver"]})
        logger.info("[data_access] 申请 %s 由演示审批岗 %s 代批：%s ← %s（仅演示）",
                    request_id, label, table, roles)
        return dict(row)

    def list_mine(self, actor: str) -> list[dict[str, Any]]:
        """申请人自己的申请历史（含已裁决，用于卡片状态）。"""
        rows = [dict(r) for r in self._requests.values()
                if str(r.get("requester") or "") == str(actor or "")]
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows

    def get(self, request_id: str) -> Optional[dict[str, Any]]:
        row = self._requests.get(str(request_id))
        return dict(row) if row else None

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        """演示事件留痕（最近 N 条；动作名带 ``.demo`` 后缀的可一眼分辨）。"""
        return list(self._events[-max(1, int(limit)):])

    def reap_expired(self) -> int:
        """回收到期授权（移除叠加层 + 申请置 expired）。"""
        now = _now()
        count = 0
        for row in self._requests.values():
            if row.get("status") != "approved":
                continue
            expires = str(row.get("expires_at") or "")
            if expires and expires <= _iso(now):
                revoke_table_access(str(row.get("table") or ""), row.get("roles") or [])
                row["status"] = "expired"
                count += 1
                self._append_event("data.access.expired", actor="system",
                                   detail={"request_id": row.get("request_id"),
                                           "table": row.get("table")})
        return count

    # ---------- 内部 ----------

    @staticmethod
    def _normalize(roles: Optional[Iterable[str]]) -> set[str]:
        return {str(r) for r in (roles or []) if r}

    def _pending_for(self, table: str, actor: str) -> Optional[str]:
        for row in self._requests.values():
            if (str(row.get("table") or "") == str(table)
                    and str(row.get("requester") or "") == str(actor or "")
                    and row.get("status") == "pending"):
                return str(row.get("request_id"))
        return None

    def _resolve_approver(self, table: str, roles: Iterable[str]) -> tuple[str, str]:
        """解析职责岗演示审批人：第一个"在表白名单内、且不是申请人角色"的岗。

        解析不出 → fail-closed（不找"差不多的人"批）。
        """
        static = TABLE_ACL.get(table, set())
        own = self._normalize(roles)
        candidates = OWNER_ROLES_BY_TABLE.get(table)
        if not candidates:
            raise ApproverUnavailable(
                f"{table} 未登记归属职责岗，无法自动审批（请人工审批）")
        for role_key in candidates:
            if role_key in static and role_key not in own:
                return role_key, OWNER_LABEL.get(table, role_key)
        raise ApproverUnavailable(
            f"{table} 未找到归属职责岗（候选 {list(candidates)}），无法自动审批")

    def _append_event(self, action: str, *, actor: str, detail: dict[str, Any]) -> None:
        self._events.append({
            "ts": _iso(_now()), "actor": str(actor or ""),
            "action": action, "detail": detail,
        })


_INSTANCE: Optional[DataAccessService] = None


def get_data_access() -> DataAccessService:
    """数据查询权限申请服务单例（装配点；测试可 ``reset_data_access()``）。"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DataAccessService()
    return _INSTANCE


def reset_data_access() -> None:
    """清空单例 + 叠加层（测试隔离用）。"""
    global _INSTANCE
    _INSTANCE = None
    from .text2sql import reset_table_grants

    reset_table_grants()
