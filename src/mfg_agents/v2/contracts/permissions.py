"""治理层权限契约：声明式权限目录 + 角色×权限矩阵（PermissionRegistry）。

设计范式对标 Huly Platform `controlled-documents`：
- ``Permission`` 是**独立实体**（带 scope/label/description），不是散落在
  业务代码里的字符串判断；
- ``RoleDef`` 是 **Permission 的命名集合**；
- 场景 YAML 的 HITL 动作声明 ``propose`` / ``cosign``（要谁发起、要谁会签），
  引擎经本注册表把中文写法归一化为稳定 role key 后做校验。

铁律（fail-closed）：
1. 未知角色 / 空角色 / ``None`` → **空权限集**，绝不默认放行；
2. 业务逻辑不得硬编码角色名或权限判断，一律 ``PermissionRegistry`` 查询；
3. ``user_ctx.roles`` 是权威来源，``role``（前端展示用）不可信。

权限命名规范：``hitl.<slot>.<verb>``，verb ∈ propose | cosign | approve。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "Permission",
    "RoleDef",
    "SlotDef",
    "PermissionRegistry",
    "PermissionRegistryError",
    "get_permission_registry",
    "reset_permission_registry",
    "DEFAULT_PERMISSIONS_PATH",
]

# v2/data/permissions.yaml（与本模块同包层级：contracts/ → ../data/）
DEFAULT_PERMISSIONS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "permissions.yaml"
)

VALID_VERBS: tuple[str, ...] = ("propose", "cosign", "approve")


class PermissionRegistryError(Exception):
    """权限目录加载/校验失败（配置错误必须是显式错误，不能静默降级放行）。"""


@dataclass(frozen=True)
class Permission:
    """权限实体（一个可被授予的最小操作单元）。

    Attributes:
        key: 稳定标识，形如 ``hitl.disposition.approve``。
        name: 中文可读名（用于 403 提示"需：质量总监/QA经理"）。
        scope: 所属域，HITL 场景固定为 ``hitl``。
        description: 语义说明（审计/文档用）。
    """

    key: str
    name: str = ""
    scope: str = "hitl"
    description: str = ""

    @property
    def slot(self) -> str:
        """``hitl.<slot>.<verb>`` 中的 slot 段（非法 key 返回空串）。"""
        parts = self.key.split(".")
        return parts[1] if len(parts) == 3 else ""

    @property
    def verb(self) -> str:
        """``hitl.<slot>.<verb>`` 中的 verb 段（非法 key 返回空串）。"""
        parts = self.key.split(".")
        return parts[2] if len(parts) == 3 else ""


@dataclass(frozen=True)
class RoleDef:
    """角色定义（Role = 权限 key 的命名集合）。

    Attributes:
        key: 英文稳定标识（如 ``sqe``），API/DB 只存它。
        name: 中文名（如 ``SQE``），UI 展示用。
        dept: 归属部门（组织维度，便于按部门聚合待办）。
        permissions: 持有的权限 key 元组。
        aliases: YAML / 前端 / 用户上下文里可能出现的写法（归一化用）。
    """

    key: str
    name: str
    dept: str = ""
    permissions: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotDef:
    """HITL 槽位定义（一个 HITL 节点支持哪三个权限）。"""

    key: str
    name: str
    scene: str = ""
    available_permissions: tuple[str, ...] = field(default_factory=tuple)


class PermissionRegistry:
    """权限目录 + 角色矩阵（只读，线程内共享；加载后不可变）。"""

    def __init__(self, permissions: Iterable[Permission],
                 roles: Iterable[RoleDef],
                 slots: Iterable[SlotDef] = ()) -> None:
        self._permissions: dict[str, Permission] = {}
        for perm in permissions:
            if not perm.key:
                raise PermissionRegistryError("权限缺少 key")
            if perm.key in self._permissions:
                raise PermissionRegistryError(f"权限 key 重复: {perm.key}")
            self._permissions[perm.key] = perm

        self._roles: dict[str, RoleDef] = {}
        self._alias_index: dict[str, str] = {}
        for role in roles:
            if not role.key:
                raise PermissionRegistryError("角色缺少 key")
            if role.key in self._roles:
                raise PermissionRegistryError(f"角色 key 重复: {role.key}")
            unknown = [p for p in role.permissions if p not in self._permissions]
            if unknown:
                raise PermissionRegistryError(
                    f"角色 {role.key} 引用了未定义的权限: {unknown}")
            self._roles[role.key] = role

        for role in self._roles.values():
            for alias in self._aliases_of(role):
                index_key = self._normalize_text(alias)
                if not index_key:
                    continue
                existed = self._alias_index.get(index_key)
                if existed and existed != role.key:
                    # 别名冲突是配置错误：静默取其一会造成权限漂移
                    raise PermissionRegistryError(
                        f"角色别名冲突: {alias!r} 同时属于 {existed} 与 {role.key}")
                self._alias_index[index_key] = role.key

        self._slots: dict[str, SlotDef] = {s.key: s for s in slots}

    # ---------- 加载 ----------

    @classmethod
    def from_yaml(cls, path: Optional[str | Path] = None) -> "PermissionRegistry":
        """从 YAML 权限目录加载（默认 ``v2/data/permissions.yaml``）。"""
        import yaml  # pyyaml 已是既有依赖，零新增依赖

        target = Path(path) if path else DEFAULT_PERMISSIONS_PATH
        if not target.is_file():
            raise PermissionRegistryError(f"权限目录不存在: {target}")
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise PermissionRegistryError(
                f"权限目录解析失败: {target}（{exc}）") from exc
        if not isinstance(raw, dict):
            raise PermissionRegistryError(f"权限目录格式非法（应为 mapping）: {target}")

        permissions = [
            Permission(
                key=str(item.get("key", "")).strip(),
                name=str(item.get("name", "")),
                scope=str(item.get("scope", "hitl")),
                description=str(item.get("description", "")),
            )
            for item in (raw.get("permissions") or [])
            if isinstance(item, dict)
        ]
        roles = [
            RoleDef(
                key=str(item.get("key", "")).strip(),
                name=str(item.get("name", "")),
                dept=str(item.get("dept", "")),
                permissions=tuple(str(p) for p in (item.get("permissions") or [])),
                aliases=tuple(str(a) for a in (item.get("aliases") or [])),
            )
            for item in (raw.get("roles") or [])
            if isinstance(item, dict)
        ]
        slots = [
            SlotDef(
                key=str(item.get("key", "")).strip(),
                name=str(item.get("name", "")),
                scene=str(item.get("scene", "")),
                available_permissions=tuple(
                    str(p) for p in (item.get("available_permissions") or [])
                ),
            )
            for item in (raw.get("slots") or [])
            if isinstance(item, dict)
        ]
        registry = cls(permissions=permissions, roles=roles, slots=slots)
        logger.info("[permissions] 权限目录加载完成: %s（%d 权限 / %d 角色 / %d 槽位）",
                    target, len(registry._permissions), len(registry._roles),
                    len(registry._slots))
        return registry

    # ---------- 归一化 ----------

    @staticmethod
    def _normalize_text(name: Any) -> str:
        """文本归一化：去空白、小写、全角空格等（仅用于比对）。"""
        if name is None:
            return ""
        text = str(name).strip().replace("\u3000", " ").strip()
        return text.lower()

    @classmethod
    def _aliases_of(cls, role: RoleDef) -> list[str]:
        return [role.key, role.name, *role.aliases]

    def normalize_role(self, name: Any) -> Optional[str]:
        """中文/别名/大小写 → role key；未识别返回 ``None``（fail-closed）。"""
        key = self._normalize_text(name)
        if not key:
            return None
        return self._alias_index.get(key)

    def normalize_roles(self, names: Optional[Iterable[Any]]) -> tuple[str, ...]:
        """批量归一化（保序去重，丢弃未识别项）。"""
        out: list[str] = []
        for name in (names or []):
            key = self.normalize_role(name)
            if key and key not in out:
                out.append(key)
        return tuple(out)

    def resolve_actor_roles(self, role: Any = None,
                            user_ctx: Any = None) -> tuple[str, ...]:
        """解析操作者角色集合（``user_ctx.roles`` 为权威来源）。

        规则：
        1. ``user_ctx`` 是 dict 且含 ``roles`` 键 → **只**以 ``user_ctx.roles`` 为准
           （``role`` 仅供展示，防止前端越权伪造）；
        2. 否则退回 ``role`` 单值（IM 回调等无上下文的入口）；
        3. 都没有 / 全部未识别 → 空元组（fail-closed）。
        """
        if isinstance(user_ctx, dict) and "roles" in user_ctx:
            return self.normalize_roles(user_ctx.get("roles") or [])
        return self.normalize_roles([role] if role else [])

    # ---------- 查询 ----------

    def role(self, role_key: Any) -> Optional[RoleDef]:
        key = self.normalize_role(role_key)
        return self._roles.get(key) if key else None

    def role_name(self, role_key: Any) -> str:
        """role key → 中文名（未识别时回显原值，便于错误提示可读）。"""
        role = self.role(role_key)
        return role.name if role else str(role_key or "")

    def role_names(self, role_keys: Iterable[Any]) -> list[str]:
        return [self.role_name(k) for k in (role_keys or [])]

    def permission(self, perm_key: str) -> Optional[Permission]:
        return self._permissions.get(str(perm_key))

    def permissions_of(self, role_names: Optional[Iterable[Any]]) -> set[str]:
        """角色集合 → 权限 key 集合（未识别角色贡献空集）。"""
        out: set[str] = set()
        for key in self.normalize_roles(role_names):
            role = self._roles.get(key)
            if role:
                out.update(role.permissions)
        return out

    def has_any(self, role_names: Optional[Iterable[Any]],
                required: Iterable[str]) -> bool:
        """角色集合是否持有 ``required`` 中**任一**权限。"""
        need = {str(r) for r in (required or [])}
        if not need:
            return False
        return bool(self.permissions_of(role_names) & need)

    def has_all(self, role_names: Optional[Iterable[Any]],
                required: Iterable[str]) -> bool:
        """角色集合是否持有 ``required`` 中**全部**权限。"""
        need = {str(r) for r in (required or [])}
        if not need:
            return False
        return need.issubset(self.permissions_of(role_names))

    def roles_with_permission(self, perm_key: str) -> list[str]:
        """持有该权限的角色 key 列表（用于提示"该动作需谁发起/会签"）。"""
        key = str(perm_key or "")
        return [r.key for r in self._roles.values() if key in r.permissions]

    def roles_with_permission_names(self, perm_key: str) -> list[str]:
        """持有该权限的角色中文名列表（拼提示语用）。"""
        return [self.role_name(k) for k in self.roles_with_permission(perm_key)]

    # ---------- 槽位 ----------

    def slot(self, slot_key: Any) -> Optional[SlotDef]:
        return self._slots.get(str(slot_key or ""))

    def slot_name(self, slot_key: Any) -> str:
        slot = self.slot(slot_key)
        return slot.name if slot else str(slot_key or "")

    def is_known_slot(self, slot_key: Any) -> bool:
        return str(slot_key or "") in self._slots

    def slot_permission(self, slot_key: str, verb: str) -> str:
        """拼 ``hitl.<slot>.<verb>``（并校验该权限在目录中存在）。"""
        slot = str(slot_key or "")
        verb = str(verb or "")
        if not self.is_known_slot(slot):
            raise PermissionRegistryError(f"未知 HITL 槽位: {slot!r}")
        if verb not in VALID_VERBS:
            raise PermissionRegistryError(f"未知权限动词: {verb!r}")
        perm_key = f"hitl.{slot}.{verb}"
        if perm_key not in self._permissions:
            raise PermissionRegistryError(f"权限目录缺少: {perm_key}")
        return perm_key

    # ---------- 诊断 ----------

    def describe(self) -> dict[str, Any]:
        """目录快照（/api/v2/governance/permissions 与前端展示用）。"""
        return {
            "permissions": [
                {"key": p.key, "name": p.name, "scope": p.scope,
                 "description": p.description}
                for p in self._permissions.values()
            ],
            "roles": [
                {"key": r.key, "name": r.name, "dept": r.dept,
                 "permissions": list(r.permissions), "aliases": list(r.aliases)}
                for r in self._roles.values()
            ],
            "slots": [
                {"key": s.key, "name": s.name, "scene": s.scene,
                 "available_permissions": list(s.available_permissions)}
                for s in self._slots.values()
            ],
        }

    def __len__(self) -> int:
        return len(self._permissions)


# ---------- 单例（惰性加载，可注入路径便于测试） ----------

_REGISTRY: Optional[PermissionRegistry] = None
_REGISTRY_PATH: Optional[str] = None


def get_permission_registry(path: Optional[str | Path] = None,
                            force_reload: bool = False) -> PermissionRegistry:
    """获取权限注册表单例（惰性加载；测试可注入自定义路径）。"""
    global _REGISTRY, _REGISTRY_PATH
    target = str(Path(path).resolve()) if path else str(DEFAULT_PERMISSIONS_PATH)
    if force_reload or _REGISTRY is None or _REGISTRY_PATH != target:
        _REGISTRY = PermissionRegistry.from_yaml(target)
        _REGISTRY_PATH = target
    return _REGISTRY


def reset_permission_registry() -> None:
    """清空单例缓存（测试隔离用）。"""
    global _REGISTRY, _REGISTRY_PATH
    _REGISTRY = None
    _REGISTRY_PATH = None
