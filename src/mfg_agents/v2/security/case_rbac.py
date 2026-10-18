"""案例域 RBAC 注册表：32 个治理角色 → ``case.*`` 动作矩阵。

**独立注册表**（``v2/data/case_roles.yaml``），不并入 ``permissions.yaml``：

- ``permissions.yaml`` 是 HITL 会签目录（``hitl.<slot>.<verb>``，24 权限 × 32 角色 ×
  8 槽位），它的构造期自检、场景加载校验与既有 625 条测试全部围绕"会签"语义；
- 案例域是 **ABAC + 六动词** 语义，有独立的审批人与生命周期。混进同一容器会让
  两套语义互相污染（例如"角色是否持有 approve"会被案例动作干扰）。

但**角色 key 仍然只认** `PermissionRegistry.normalize_role()`：案例域不得自己发明
角色。未识别的角色在**构造期**即失败（不是运行期静默忽略），未识别的动作同样失败。

铁律：
1. 未知角色 / 未知动作 → 构造期 ``CaseRbacError``（配置错误必须显式暴露）；
2. ``has_any(roles, "case.query")`` 对未识别角色贡献**空集**（fail-closed）；
3. Agent 身份角色不得持有 ingest / export / share（BR-07 的案例域延伸）；
4. ``share`` 默认无任何角色持有（PRD N6）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ..contracts.case_acl import ACTIONS, CASE_PERMISSION_PREFIX
from ..contracts.permissions import (
    PermissionRegistry,
    PermissionRegistryError,
    get_permission_registry,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CaseRbacError",
    "CaseRbacRegistry",
    "DEFAULT_CASE_ROLES_PATH",
    "get_case_rbac",
    "reset_case_rbac",
]

#: ``v2/data/case_roles.yaml``（本模块在 v2/security/ 下 → ../data/）
DEFAULT_CASE_ROLES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "case_roles.yaml"
)

#: Agent 身份角色不得持有的案例动作（Agent 只 propose，永不 approve/导出/入库）。
AGENT_FORBIDDEN_ACTIONS: tuple[str, ...] = ("ingest", "export", "share")


class CaseRbacError(Exception):
    """案例 RBAC 配置错误（加载期 fail-fast，不静默降级）。"""


@dataclass(frozen=True)
class CaseActionDef:
    """案例动作定义（``case.query`` → 中文名）。"""

    key: str
    label: str = ""

    @property
    def action(self) -> str:
        """``case.<action>`` 的动作段。"""
        prefix = CASE_PERMISSION_PREFIX
        return self.key[len(prefix):] if self.key.startswith(prefix) else self.key


class CaseRbacRegistry:
    """角色 → 案例动作矩阵（只读，构造期自检）。"""

    def __init__(self,
                 grants: Mapping[str, Iterable[str]],
                 registry: Optional[PermissionRegistry] = None,
                 action_labels: Optional[Mapping[str, str]] = None) -> None:
        self._registry = registry or get_permission_registry()
        self._grants: dict[str, tuple[str, ...]] = {}
        known_actions = {f"{CASE_PERMISSION_PREFIX}{a}" for a in ACTIONS}
        self._labels: dict[str, str] = {
            "case.query": "检索命中",
            "case.preview": "预览摘要",
            "case.read": "读知识层全文",
            "case.cite": "引用到工单",
            "case.export": "导出（默认拒绝）",
            "case.share": "外发分享（恒定拒绝）",
            "case.ingest": "案例入库",
        }
        for key, label in (action_labels or {}).items():
            self._labels[str(key)] = str(label)

        for raw_role, raw_actions in (grants or {}).items():
            role_key = self._normalize_known_role(raw_role)
            actions: list[str] = []
            for raw in raw_actions or ():
                action = str(raw or "").strip()
                if not action:
                    continue
                if not action.startswith(CASE_PERMISSION_PREFIX):
                    action = f"{CASE_PERMISSION_PREFIX}{action}"
                if action not in known_actions:
                    raise CaseRbacError(
                        f"角色 {role_key} 声明了未知案例动作: {action!r}"
                        f"（合法动作：{sorted(known_actions)}）")
                if action not in actions:
                    actions.append(action)
            if not actions:
                raise CaseRbacError(f"角色 {role_key} 未声明任何案例动作（空授权应立即删除该角色）")
            self._grants[role_key] = tuple(actions)

        self._assert_agent_discipline()

    # ---------- 构造期自检 ----------

    def _normalize_known_role(self, raw_role: Any) -> str:
        """角色写法 → 权威 role key；未识别即失败（案例域不得发明角色）。"""
        role_key = self._registry.normalize_role(raw_role)
        if not role_key:
            raise CaseRbacError(
                f"案例角色未识别: {raw_role!r}——案例域角色必须是 "
                "PermissionRegistry 能识别的 32 个治理角色之一")
        return role_key

    def _assert_agent_discipline(self) -> None:
        """Agent 身份角色不得持有 ingest / export / share。"""
        violations: list[str] = []
        for role_key, actions in self._grants.items():
            role = self._registry.role(role_key)
            if role is None or not role.is_agent:
                continue
            bad = [a for a in actions
                   if a[len(CASE_PERMISSION_PREFIX):] in AGENT_FORBIDDEN_ACTIONS]
            if bad:
                violations.append(f"{role_key} -> {bad}")
        if violations:
            raise CaseRbacError(
                "架构铁律被违反：Agent 身份角色不得持有案例的 "
                f"ingest/export/share。违规：{violations}")

    # ---------- 加载 ----------

    @classmethod
    def from_yaml(cls, path: Optional[str | Path] = None,
                  registry: Optional[PermissionRegistry] = None
                  ) -> "CaseRbacRegistry":
        """从 ``case_roles.yaml`` 加载（缺文件即失败，不静默空授权）。"""
        import yaml  # pyyaml 已在既有依赖中，零新增依赖

        target = Path(path) if path else DEFAULT_CASE_ROLES_PATH
        if not target.is_file():
            raise CaseRbacError(f"案例角色表不存在: {target}")
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise CaseRbacError(f"案例角色表解析失败: {target}（{exc}）") from exc
        if not isinstance(raw, dict):
            raise CaseRbacError(f"案例角色表格式非法（应为 mapping）: {target}")

        labels: dict[str, str] = {}
        for item in (raw.get("actions") or []):
            if isinstance(item, dict) and item.get("key"):
                labels[str(item["key"])] = str(item.get("label", ""))

        grants: dict[str, tuple[str, ...]] = {}
        for item in (raw.get("roles") or []):
            if not isinstance(item, dict):
                raise CaseRbacError(f"案例角色条目格式非法: {item!r}")
            key = str(item.get("key", "")).strip()
            if not key:
                raise CaseRbacError("案例角色条目缺少 key")
            if key in grants:
                raise CaseRbacError(f"案例角色 key 重复: {key}")
            grants[key] = tuple(str(a) for a in (item.get("actions") or []))

        registry_obj = cls(grants=grants, registry=registry, action_labels=labels)
        logger.info("[case_rbac] 案例角色表加载完成: %s（%d 角色）",
                    target, len(registry_obj._grants))
        return registry_obj

    # ---------- 查询 ----------

    def _as_permission(self, action: Any) -> str:
        text = str(action or "").strip()
        if not text:
            raise CaseRbacError("案例动作不能为空")
        if not text.startswith(CASE_PERMISSION_PREFIX):
            text = f"{CASE_PERMISSION_PREFIX}{text}"
        return text

    def actions_of(self, role: Any) -> tuple[str, ...]:
        """某角色持有的案例动作（未识别角色 → 空元组，fail-closed）。"""
        role_key = self._registry.normalize_role(role)
        return self._grants.get(role_key, ()) if role_key else ()

    def has(self, role: Any, action: Any) -> bool:
        """单角色是否持有某动作（``action`` 可写 ``query`` 或 ``case.query``）。"""
        return self._as_permission(action) in self.actions_of(role)

    def has_any(self, roles: Optional[Iterable[Any]], action: Any) -> bool:
        """角色集合是否持有某动作。未识别角色贡献空集（fail-closed）。"""
        permission = self._as_permission(action)
        for role in (roles or ()):
            if permission in self.actions_of(role):
                return True
        return False

    def roles_with(self, action: Any) -> tuple[str, ...]:
        """持有该动作的角色 key（用于 UI 提示与审计）。"""
        permission = self._as_permission(action)
        return tuple(sorted(k for k, v in self._grants.items() if permission in v))

    def matrix(self) -> dict[str, Any]:
        """``/api/v2/cases/meta`` 用的动作矩阵。"""
        return {
            "actions": [
                {"key": key, "label": self._labels.get(key, ""),
                 "roles": list(self.roles_with(key))}
                for key in sorted(self._labels)
            ],
            "roles": [
                {"key": role, "name": self._registry.role_name(role),
                 # 部门取自平台权限注册表（只读；案例域不另建一份部门表，
                 # 避免两边漂移）。前端据此做"部门 → 岗位"级联选择器。
                 "dept": self._dept_of(role),
                 "actions": list(actions)}
                for role, actions in sorted(self._grants.items())
            ],
            # 部门清单（级联选择器的第一级）
            "departments": self.departments(),
        }

    def _dept_of(self, role: str) -> str:
        """角色 → 部门（来自平台注册表；取不到返回空串）。"""
        item = self._registry.role(role)
        return str(getattr(item, "dept", "") or "") if item is not None else ""

    def departments(self) -> list[dict[str, str]]:
        """案例域涉及的部门清单（``{key, label}``），按 key 排序。"""
        seen: dict[str, int] = {}
        for role in self._grants:
            dept = self._dept_of(role)
            if dept:
                seen[dept] = seen.get(dept, 0) + 1
        return [{"key": dept, "label": DEPT_LABEL.get(dept, dept),
                 "roles": str(count)}
                for dept, count in sorted(seen.items())]

    def role_count(self) -> int:
        return len(self._grants)


#: 部门中文名（前端主视图用；英文 key 放技术详情）。
DEPT_LABEL: dict[str, str] = {
    "management": "总经理室",
    "quality": "品质部",
    "equipment": "设备工程部",
    "manufacturing": "制造部",
    "production": "制造部",
    "procurement": "采购部",
    "supply_chain": "供应链部",
    "rd_engineering": "研发部",
    "engineering": "研发部",
    "pm": "项目管理部",
    "sales": "销售部",
    "service": "客户服务部",
    "finance": "财务部",
    "warehouse": "仓储部",
    "admin": "行政人事部",
    "ehs": "环境安全部",
}

_CASE_RBAC: Optional[CaseRbacRegistry] = None


def get_case_rbac(registry: Optional[PermissionRegistry] = None) -> CaseRbacRegistry:
    """案例 RBAC 单例（首次调用时加载；加载失败 → 直接抛错，不降级）。"""
    global _CASE_RBAC
    if _CASE_RBAC is None:
        try:
            perm_registry = registry or get_permission_registry()
        except PermissionRegistryError as exc:  # pragma: no cover - 权限目录坏了本就该失败
            raise CaseRbacError(f"权限目录不可用，案例 RBAC 无法校验角色: {exc}") from exc
        _CASE_RBAC = CaseRbacRegistry.from_yaml(registry=perm_registry)
    return _CASE_RBAC


def reset_case_rbac() -> None:
    """清空单例（测试注入自定义角色表用）。"""
    global _CASE_RBAC
    _CASE_RBAC = None
