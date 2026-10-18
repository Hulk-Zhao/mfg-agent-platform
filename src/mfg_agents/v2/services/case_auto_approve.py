"""跨部门访问申请的「自动审批」：**预留接口** + 演示实现。

铁律仍然成立：**Agent 不得持 ``case.access.approve``**
（``security.case_rbac.AGENT_FORBIDDEN_ACTIONS``）。本模块不是给 Agent 开审批
口子，而是把"谁来批准"这件事抽成一个可替换接口：

* **生产实现（未做，接口在此预留）**：对接真实审批流（OA / 企业微信审批 /
  会签系统）。职能部门负责人**本人**在审批流里点同意，系统回调
  :meth:`CaseAccessService.decide`——服务端代码零改动；
* **演示实现（本文件 :class:`DemoAgentApprover`）**：用归属职能部门管理岗的
  **演示身份**代替真人点击，走的仍是同一条 ``decide()``：``case_acl`` 写入、
  有效期、索引重建、审计一条不少，只在审计 action 上打 ``.demo`` 后缀
  （``case.access.approved.demo``），哈希链上可见。

为什么单独一个模块而不是塞进 ``case_access.py``：审批人的**身份来源**是独立
替换点（SSO / 审批流 / 演示假身份），而"批准之后发生什么"（``_grant``）必须
只有一份。把两者混在一起，将来接真实审批流时就会有人图省事"再造一条授权路径"
——那正是本项目一路在堵的事（不另起第二套判定）。

演示实现的三条自我约束（写下来防回归）：

1. **审批人必须是真的有资格**：只从 ``case_roles.yaml`` 里持有
   ``case.access.approve`` 的角色中，选**部门命中该案例职能部门**的那一个；
   解析不出就 fail-closed（``DemoApproverUnavailable``），不找个"差不多的人"批。
2. **职责分离照查**：解析出的演示审批人 user_id 恰好是申请人自己时，跳过；
   无可选人 → fail-closed（B9 不因演示而豁免）。
3. **不碰任何其它路径**：本模块只调 ``decide()``，不自己写 ACL / 不重建索引 /
   不改可见性——授权副作用必须只有 ``_grant`` 一个出处。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional

from ...common.config import settings
from ..contracts.case_acl import Principal
from ..contracts.permissions import get_permission_registry
from ..security.case_rbac import get_case_rbac
from .case_access import (
    AccessNotFound,
    AccessRequestError,
    AccessTransitionError,
    CaseAccessService,
    CaseStore,
    case_departments,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DemoApprovalError",
    "DemoApprovalDisabled",
    "DemoApproverUnavailable",
    "DemoApprovalNotRequester",
    "AccessApprover",
    "DemoAgentApprover",
    "get_access_approver",
    "reset_access_approver",
]

#: 演示自动审批写入的决策理由（会进 ``decision_reason`` 列，前端"审批意见"可见）。
DEMO_DECISION_REASON = "演示代批：模拟职能部门审批通过（仅演示，不代替人审批）"

#: 演示审批人的 user_id 前缀（与 /session 的 ``u-<role>`` 口径一致，
#: 这样审计表里能一眼看出"这是哪个职能岗的代批身份"）。
_DEMO_APPROVER_PREFIX = "u-"


class DemoApprovalError(AccessRequestError):
    """演示自动审批异常基类（复用申请域异常族，便于 API 层统一映射）。"""


class DemoApprovalDisabled(DemoApprovalError):
    """自动审批开关未打开（``CASE_DEMO_MODE=false`` 或开关为 false）。"""


class DemoApproverUnavailable(DemoApprovalError):
    """解析不出有资格的演示审批人（无归属部门 / 该部门无审批岗 / 只有申请人自己）。"""


class DemoApprovalNotRequester(DemoApprovalError):
    """触发者不是申请人本人（不允许代别人的申请触发审批）。"""


class AccessApprover(ABC):
    """审批人身份提供者（**预留接口**）。

    唯一职责：给定一条 pending 申请，给出一个**有资格的审批人身份**，
    然后复用 :meth:`CaseAccessService.decide` 完成批准。实现方**不得**自己写
    ACL / 改索引 / 改可见性——授权副作用必须只有 ``decide()`` 一条路。

    生产替换方式：实现本接口（从审批流系统取"当前点击同意的负责人"身份），
    在 :class:`CaseRuntime` 装配处换掉 ``DemoAgentApprover`` 即可。
    """

    #: 实现名（审计/日志/前端展示用）。
    name: str = "access_approver"

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """当前是否启用（演示实现跟随 ``CASE_DEMO_MODE`` + 专用开关）。"""

    @abstractmethod
    def resolve(self, case: Mapping[str, Any],
                requester_id: str) -> tuple[Principal, str]:
        """解析审批人身份，返回 ``(principal, role_key)``。

        Raises:
            DemoApproverUnavailable: 解析不出有资格的人（fail-closed）。
        """

    @abstractmethod
    def preview(self, case: Mapping[str, Any],
                requester_id: str) -> dict[str, Any]:
        """不落任何库的预检（供 ``request-access`` 响应告诉前端能否自动批）。"""

    @abstractmethod
    def approve(self, access: CaseAccessService, principal: Principal,
                request_id: str) -> dict[str, Any]:
        """执行批准（走 ``decide(demo=True)``），返回申请行。"""


class DemoAgentApprover(AccessApprover):
    """演示用审批人：归属职能部门管理岗的**演示身份**。

    资格判据与人工审批**完全同源**（``case_departments`` + ``case_roles.yaml``
    的 ``case.access.approve`` 授权矩阵），不是另立一套标准。
    """

    name = "demo_agent_approver"

    def __init__(self, store: Optional[CaseStore] = None) -> None:
        self._store = store

    # ---------- 开关 ----------

    @property
    def enabled(self) -> bool:
        # 双重门：演示模式关着 → 一律不自动批（生产形态没有"演示身份"可言）；
        # 专用开关关着 → 人工在「访问申请待办」里审批（产品默认路径）。
        return bool(getattr(settings, "CASE_DEMO_MODE", True)) and bool(
            getattr(settings, "CASE_ACCESS_AUTO_APPROVE_DEMO", True))

    # ---------- 解析 ----------

    def resolve(self, case: Mapping[str, Any],
                requester_id: str) -> tuple[Principal, str]:
        departments = self._departments(case)
        if not departments:
            raise DemoApproverUnavailable(
                f"案例 {case.get('case_id')} 没有归属部门（owner_dept 与 dept ACL "
                "均为空），无法判定职能部门——fail-closed：请人工在「访问申请待办」审批")

        requester = str(requester_id or "")
        for role_key in get_case_rbac().roles_with("case.access.approve"):
            dept = self._dept_of(role_key)
            if not dept or dept not in departments:
                continue
            synthetic_id = _DEMO_APPROVER_PREFIX + role_key
            if synthetic_id == requester:
                # B9 职责分离不因演示豁免：唯一有资格的审批人恰好是申请人自己时，
                # 不许"换顶帽子接着批"。
                continue
            return self._principal(role_key, dept), role_key

        holders = sorted(get_case_rbac().roles_with("case.access.approve"))
        raise DemoApproverUnavailable(
            f"未找到 {sorted(departments)} 职能部门持有 case.access.approve 的管理岗"
            f"（候选审批岗：{holders}；解析不到≠降低标准，请人工审批）")

    def preview(self, case: Mapping[str, Any],
                requester_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {
                "available": False,
                "reason": "演示自动审批未开启（CASE_DEMO_MODE=false 或 "
                          "CASE_ACCESS_AUTO_APPROVE_DEMO=false）：请人工审批",
            }
        try:
            _, role_key = self.resolve(case, requester_id)
        except DemoApproverUnavailable as exc:
            return {"available": False, "reason": str(exc)}
        return {
            "available": True,
            "approver_role": role_key,
            "note": "演示模式：提交后将弹窗模拟职能部门审批通过（仅演示）",
        }

    # ---------- 批准 ----------

    def approve(self, access: CaseAccessService, principal: Principal,
                request_id: str) -> dict[str, Any]:
        if not self.enabled:
            raise DemoApprovalDisabled(
                "演示自动审批未开启（CASE_DEMO_MODE=false 或 "
                "CASE_ACCESS_AUTO_APPROVE_DEMO=false）")

        row = access.get(request_id)
        if row is None:
            raise AccessNotFound(f"申请不存在: {request_id}")
        status = str(row.get("status") or "")
        if status != "pending":
            raise AccessTransitionError(
                f"申请 {request_id} 已是 {status}，不能再审批")
        if str(row.get("requester_id") or "") != str(principal.user_id or ""):
            raise DemoApprovalNotRequester(
                "只能为本人提交的申请触发演示审批（申请与触发者不是同一个人）")

        case = access.store.get_case(str(row.get("case_id") or ""))
        if case is None:
            raise AccessNotFound("案例不存在，无法审批")

        approver, role_key = self.resolve(case, str(row.get("requester_id") or ""))
        decided = access.decide(
            approver, request_id, approve=True,
            reason=DEMO_DECISION_REASON, demo=True)
        logger.info("[case_auto_approve] 申请 %s 由演示审批岗 %s 代批（仅演示）",
                    request_id, role_key)
        return decided

    # ---------- 内部 ----------

    def _departments(self, case: Mapping[str, Any]) -> set[str]:
        if self._store is None:  # pragma: no cover - 装配时必传 store
            from .case_runtime import get_case_runtime

            self._store = get_case_runtime().store
        return case_departments(self._store, case)

    @staticmethod
    def _dept_of(role_key: str) -> str:
        """角色 → 部门（平台权限注册表口径；案例域不另建一份部门表）。"""
        item = get_permission_registry().role(role_key)
        return str(getattr(item, "dept", "") or "") if item is not None else ""

    @staticmethod
    def _principal(role_key: str, dept: str) -> Principal:
        """构造演示审批人身份。

        只填 ``decide()`` 真正用到的维度（user_id / roles / dept）；
        工厂、产线等维度留空——审批判定不依赖它们，填了反而像"伪造了一个全权身份"。
        """
        try:
            tenant = str(getattr(settings, "CASE_DEFAULT_TENANT", "T001"))
        except Exception:  # noqa: BLE001 - 配置异常不应让审批链路崩在租户上
            tenant = "T001"
        return Principal(
            user_id=_DEMO_APPROVER_PREFIX + role_key,
            tenant=tenant,
            dept=str(dept or ""),
            roles=(str(role_key),),
            plant="",
            employment_type="employee",
            max_classification_rank=3,
        )


_APPROVER: Optional[AccessApprover] = None


def get_access_approver(store: Optional[CaseStore] = None) -> AccessApprover:
    """审批人提供者单例（装配点；测试可传临时库的 store）。"""
    global _APPROVER
    if _APPROVER is None:
        _APPROVER = DemoAgentApprover(store)
    elif store is not None and getattr(_APPROVER, "_store", None) is None:
        _APPROVER._store = store  # noqa: SLF001 - 延迟补挂 store（测试装配顺序）
    return _APPROVER


def reset_access_approver() -> None:
    """清空单例（测试隔离用）。"""
    global _APPROVER
    _APPROVER = None
