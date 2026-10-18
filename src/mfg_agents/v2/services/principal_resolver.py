"""PrincipalResolver：令牌载荷 → ``Principal``（fail-closed）。

架构位置：§4.5 / §4.6 / ADR-007。

三条规则（都是"安全必需"，不是可选的兼容性考虑）：

1. 令牌**无 ``tv`` 或 ``tv < 2``** → ``401 CASE_TOKEN_SCHEMA_LEGACY``，强制重新登录。
   老令牌没有 ``pv``，无法支持"撤权即失效"——留一个"过渡期两种都放行"的中间状态，
   正是合规事故的高发区。
2. ``pv`` 与服务端版本号不匹配 → ``401 CASE_PRINCIPAL_REVOKED``，要求重登。
   撤权从来不是"等令牌 TTL 自然过期"，而是一次整数比对。
3. ``tv == 2`` 但缺某个维度字段 → **不 401**，按"空 = 无授权"处理：
   ``plant=""`` → 令牌里没有 ``plant:*`` → 依赖工厂维的案例不可见（纵深防御）。

服务端 ``pv`` 存放：生产是 Redis ``pv:<user_id>``；demo 用进程内字典
（``CASE_DEMO_MODE=true``），提供 :meth:`PrincipalVersionStore.revoke` 供红队用例触发。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Mapping, Optional

from ..contracts.case_acl import Principal
from ..contracts.permissions import PermissionRegistry, get_permission_registry

logger = logging.getLogger(__name__)

__all__ = [
    "REQUIRED_TOKEN_VERSION",
    "EMPLOYMENT_TYPES",
    "CaseTokenError",
    "CaseTokenLegacy",
    "CasePrincipalRevoked",
    "CaseTokenInvalid",
    "PrincipalVersionStore",
    "get_principal_version_store",
    "reset_principal_version_store",
    "PrincipalResolver",
    "get_principal_resolver",
]

#: 当前令牌 schema 版本。``tv < 2`` 一律 401（ADR-007）。
REQUIRED_TOKEN_VERSION = 2

#: 合法用工类型（PRD §15 P5 / 架构 §4.3 第 0 层）。
EMPLOYMENT_TYPES: tuple[str, ...] = (
    "employee", "outsource", "temp", "supplier", "customer",
)


class CaseTokenError(Exception):
    """令牌不可用于案例域（由 API 层转 401/403）。"""

    code = "CASE_TOKEN_INVALID"
    status_code = 401

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CaseTokenLegacy(CaseTokenError):
    """``tv < 2``：老令牌一律强制重登（ADR-007）。"""

    code = "CASE_TOKEN_SCHEMA_LEGACY"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or (
            "访问令牌版本过旧（缺 tv=2 载荷），请重新选择岗位登录。"
            "老令牌无法支持撤权即时失效，因此不做兼容放行。"))


class CasePrincipalRevoked(CaseTokenError):
    """``pv`` 不匹配：主体已撤权/调岗，要求重登。"""

    code = "CASE_PRINCIPAL_REVOKED"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or "主体授权版本已失效（pv 不匹配），请重新登录")


class CaseTokenInvalid(CaseTokenError):
    """载荷字段非法（如身份为空、用工类型未识别）。"""

    code = "CASE_TOKEN_INVALID"


class PrincipalVersionStore:
    """主体版本号（``pv``）存储。

    生产：Redis ``INCR pv:<user_id>``（A5）。demo：进程内字典 + 线程锁。
    ``revoke()`` 使该用户的下一次请求 401——这就是"撤权即失效"。
    """

    def __init__(self, initial: int = 1) -> None:
        self._versions: dict[str, int] = {}
        self._default = int(initial)
        self._lock = threading.Lock()

    def get(self, user_id: str) -> int:
        with self._lock:
            return self._versions.get(str(user_id or ""), self._default)

    def revoke(self, user_id: str) -> int:
        """撤权：``pv += 1``，返回新版本号。"""
        key = str(user_id or "")
        with self._lock:
            self._versions[key] = self._versions.get(key, self._default) + 1
            return self._versions[key]

    def reset(self) -> None:
        with self._lock:
            self._versions.clear()


_VERSION_STORE: Optional[PrincipalVersionStore] = None


def get_principal_version_store() -> PrincipalVersionStore:
    global _VERSION_STORE
    if _VERSION_STORE is None:
        _VERSION_STORE = PrincipalVersionStore()
    return _VERSION_STORE


def reset_principal_version_store() -> None:
    global _VERSION_STORE
    _VERSION_STORE = None


class PrincipalResolver:
    """载荷/令牌 → ``Principal``（唯一主体构造点）。"""

    def __init__(self, registry: Optional[PermissionRegistry] = None,
                 versions: Optional[PrincipalVersionStore] = None) -> None:
        self._registry = registry or get_permission_registry()
        self._versions = versions or get_principal_version_store()

    # ---------- 主入口 ----------

    def from_payload(self, payload: Optional[Mapping[str, Any]],
                     *, verify_pv: bool = True) -> Principal:
        """令牌载荷 → 主体。

        Raises:
            CaseTokenLegacy: 缺 ``tv`` 或 ``tv < 2``。
            CasePrincipalRevoked: ``pv`` 不匹配。
            CaseTokenInvalid: 身份为空 / 用工类型非法 / 角色全部未识别。
        """
        if not isinstance(payload, Mapping):
            raise CaseTokenInvalid("访问令牌内容不可用")
        user_id = str(payload.get("sub") or "").strip()
        if not user_id:
            raise CaseTokenInvalid("访问令牌缺少用户标识（sub）")

        try:
            tv = int(payload.get("tv") or 0)
        except (TypeError, ValueError):
            tv = 0
        if tv < REQUIRED_TOKEN_VERSION:
            raise CaseTokenLegacy()

        roles = self._registry.normalize_roles(payload.get("roles") or [])
        if not roles:
            raise CaseTokenInvalid("访问令牌不含可识别的治理角色")

        employment_type = str(payload.get("employment_type") or "employee").strip().lower()
        if employment_type not in EMPLOYMENT_TYPES:
            raise CaseTokenInvalid(
                f"未识别的用工类型: {employment_type!r}"
                f"（合法值：{list(EMPLOYMENT_TYPES)}）")

        principal_version = self._as_int(payload.get("pv"), default=0)
        if verify_pv:
            expected = self._versions.get(user_id)
            if principal_version != expected:
                raise CasePrincipalRevoked(
                    f"主体 {user_id} 的 pv={principal_version} 与服务端 {expected} 不一致")

        return Principal(
            user_id=user_id,
            tenant=str(payload.get("tenant") or ""),
            dept=str(payload.get("dept") or ""),
            roles=roles,
            plant=str(payload.get("plant") or ""),
            line_ids=tuple(self._as_list(payload.get("line_ids"))),
            project_ids=tuple(self._as_list(payload.get("project_ids"))),
            customer_ids=tuple(self._as_list(payload.get("customer_ids"))),
            supplier_ids=tuple(self._as_list(payload.get("supplier_ids"))),
            product_families=tuple(self._as_list(payload.get("product_families"))),
            max_classification_rank=self._as_int(
                payload.get("max_classification_rank"), default=0),
            employment_type=employment_type,
            token_version=tv,
            principal_version=principal_version,
        )

    def from_token(self, token: str) -> Principal:
        """令牌字符串 → 主体（验签 + 版本校验）。"""
        from .auth import AuthError, verify_token

        try:
            payload = verify_token(token)
        except AuthError as exc:
            raise CaseTokenInvalid(str(exc)) from exc
        return self.from_payload(payload)

    # ---------- 载荷构造（演示身份选择 / 生产 SSO 适配点） ----------

    def build_payload(self, *, user_id: str, roles: Any, dept: str = "",
                      name: str = "", tenant: str = "", plant: str = "",
                      line_ids: Any = (), project_ids: Any = (),
                      customer_ids: Any = (), supplier_ids: Any = (),
                      product_families: Any = (),
                      max_classification_rank: int = 0,
                      employment_type: str = "employee") -> dict[str, Any]:
        """构造 ``tv=2`` 的令牌载荷（SSO/演示身份适配器统一走这里）。

        角色一律经 ``PermissionRegistry.normalize_roles`` 归一化；全部未识别即拒绝
        ——客户端无法自行发明角色。
        """
        role_keys = self._registry.normalize_roles(roles if isinstance(roles, (list, tuple))
                                                   else [roles])
        if not role_keys:
            raise CaseTokenInvalid("岗位未识别：无可签发的案例角色")
        etype = str(employment_type or "employee").strip().lower()
        if etype not in EMPLOYMENT_TYPES:
            raise CaseTokenInvalid(f"未识别的用工类型: {etype!r}")
        return {
            "sub": str(user_id or "").strip(),
            "name": str(name or ""),
            "dept": str(dept or ""),
            "roles": list(role_keys),
            "tenant": str(tenant or ""),
            "plant": str(plant or ""),
            "line_ids": self._as_list(line_ids),
            "project_ids": self._as_list(project_ids),
            "customer_ids": self._as_list(customer_ids),
            "supplier_ids": self._as_list(supplier_ids),
            "product_families": self._as_list(product_families),
            "max_classification_rank": max(0, min(3, int(max_classification_rank))),
            "employment_type": etype,
            "tv": REQUIRED_TOKEN_VERSION,
            "pv": self._versions.get(user_id),
        }

    # ---------- 内部 ----------

    @staticmethod
    def _as_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            text = str(value).strip()
            return [text] if text else []
        try:
            return [str(v).strip() for v in value if str(v).strip()]
        except TypeError:  # pragma: no cover - 异常载荷兜底
            return []


_RESOLVER: Optional[PrincipalResolver] = None


def get_principal_resolver() -> PrincipalResolver:
    """主体解析器单例。"""
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = PrincipalResolver()
    return _RESOLVER
