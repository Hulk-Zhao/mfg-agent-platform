"""看板数据范围裁决（声明式目录 → 服务端强制执行）。

设计意图
--------
四个 ``/api/v2/dashboard/*`` 端点曾经是「全员可见」：任何岗位持有有效令牌
都能拉 SPC 过程能力、订单履约聚合。菜单层收敛只挡 UI，挡不住直接调接口。

本模块把「谁可以看看板的哪一部分」收敛到 ``v2/data/data_scope.yaml``，
与 ``permissions.yaml`` 同一套纪律：

- **fail-closed**：未声明的看板一律拒绝，新增看板必须显式登记；
- **构造期自检**：角色 key 必须落在 32 角色权限目录内，脱敏路径必须可解析，
  写错一个字母就在启动时炸，而不是等到演示现场返回空数据；
- **脱敏在服务端完成**：前端拿到的已是脱敏后的载荷，不存在「前端不显示但接口给了」；
- **显示名与下钻键成对处理**：只藏名字、留下钻用的客户键，点一下就还原真名。

角色口径
--------
一律用 ``permissions.yaml`` 的 32 个角色 key（``manufacturing`` / ``admin_hr`` …），
不是前端 31 个岗位的短 id（``mfg`` / ``hr`` …）。映射关系由
``tests/v2/test_数据范围.py`` 交叉校验，避免两套命名漂移。
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

from ..contracts.permissions import get_permission_registry

DEFAULT_DATA_SCOPE_PATH = Path(__file__).resolve().parent.parent / "data" / "data_scope.yaml"

#: 已声明看板 → 对应前端屏 id（``drill`` 端点按 screen 复用它做同源裁决）
_SEGMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\[(?P<sel>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\]]*)\])?"
    r"(?P<list>\[\])?$"
)


class DataScopeError(RuntimeError):
    """数据范围目录非法（加载期即抛出，绝不静默降级）。"""


# ---------------------------------------------------------------- 路径解析

@dataclass(frozen=True)
class _Segment:
    name: str
    sel_key: str = ""
    sel_val: str = ""
    is_list: bool = False


def _parse_path(path: str) -> tuple[_Segment, ...]:
    """``rankings[kpi=otd].items[].name`` → 段序列。

    支持的写法只有三种，够用且不会被写出花：
      - ``name``              普通字段
      - ``name[]``            列表，逐元素
      - ``name[k=v][]``       按 ``k == v`` 过滤后的列表，逐元素
    """
    segs: list[_Segment] = []
    for raw in str(path or "").split("."):
        m = _SEGMENT_RE.match(raw.strip())
        if not m:
            raise DataScopeError(f"脱敏路径无法解析: {path!r}（非法段 {raw!r}）")
        segs.append(_Segment(m.group("name"), m.group("sel") or "",
                             m.group("val") or "", bool(m.group("list"))))
    if not segs:
        raise DataScopeError(f"脱敏路径为空: {path!r}")
    return tuple(segs)


def _visit(node: Any, segs: tuple[_Segment, ...],
           on_leaf: Callable[[Any, Any], None]) -> None:
    """按路径遍历载荷，命中叶子时回调 ``on_leaf(container, key)``。

    只读不改结构：``container[key]`` 既可读也可写，列表用下标。
    """
    if not segs or not isinstance(node, dict):
        return
    head, rest = segs[0], segs[1:]
    if head.name not in node:
        return
    child = node[head.name]
    if head.sel_key:
        # `rankings[kpi=otd].items[].name`：选择器命中若干字典元素后，其余段
        # **逐个元素**继续走（选择器段本身不是列表叶子，不能带 []）
        if not isinstance(child, list) or head.is_list:
            return
        for item in child:
            if not isinstance(item, dict):
                continue
            if str(item.get(head.sel_key) or "") == head.sel_val and rest:
                _visit(item, rest, on_leaf)
        return
    if head.is_list:
        if not isinstance(child, list):
            return
        for idx, item in enumerate(child):
            if rest:
                _visit(item, rest, on_leaf)
            else:
                on_leaf(child, idx)
        return
    if rest:
        _visit(child, rest, on_leaf)
    else:
        on_leaf(node, head.name)


# ---------------------------------------------------------------- 目录模型

@dataclass(frozen=True)
class MaskRule:
    """一条字段脱敏规则。"""

    path: str
    label: str
    keep_roles: frozenset[str]
    pseudo: str = ""          # 稳定编号前缀（配合 pseudo 表）
    literal: str = ""         # 固定替换文本
    compose_sep: str = ""     # "客户 · 等级" → 以分隔符前的部分作为编号键
    also_drop: tuple[str, ...] = ()   # 同层需要一并删除的键（如下钻实体）

    def replacement_key(self, value: Any) -> str:
        text = str(value or "")
        if self.compose_sep:
            return text.split(self.compose_sep)[0].strip()
        return text


@dataclass(frozen=True)
class DashboardScope:
    key: str
    name: str
    screen: str
    allow_roles: frozenset[str]
    masks: tuple[MaskRule, ...] = field(default=())

    def allows(self, roles: Iterable[str]) -> bool:
        if not self.allow_roles:      # 空 = 全员（显式声明，非缺省）
            return True
        return any(r in self.allow_roles for r in roles)


class DataScopeRegistry:
    """数据范围目录（构造期自检，配置错误即启动失败）。"""

    def __init__(self, dashboards: dict[str, DashboardScope],
                 pseudo_prefix: dict[str, str]) -> None:
        self._dashboards = dict(dashboards)
        self._pseudo = dict(pseudo_prefix)

    # ---------- 加载与自检 ----------

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_DATA_SCOPE_PATH) -> "DataScopeRegistry":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise DataScopeError("data_scope.yaml 顶层必须是映射")
        permissions = get_permission_registry()
        known_roles = {str(r["key"]) for r in permissions.describe()["roles"]}
        pseudo = {str(k): str(v) for k, v in (raw.get("pseudo") or {}).items()}

        dashboards: dict[str, DashboardScope] = {}
        for key, node in (raw.get("dashboards") or {}).items():
            key = str(key)
            if not isinstance(node, dict):
                raise DataScopeError(f"看板 {key!r} 的配置必须是映射")
            allow = [str(x) for x in (node.get("allow_roles") or [])]
            unknown = [r for r in allow if r not in known_roles]
            if unknown:
                raise DataScopeError(
                    f"看板 {key!r} 引用了未识别角色: {unknown}"
                    f"（角色必须取自 permissions.yaml 的 32 个 key）")
            masks: list[MaskRule] = []
            for item in (node.get("mask") or []):
                if not isinstance(item, dict) or not item.get("path"):
                    raise DataScopeError(f"看板 {key!r} 的 mask 条目缺少 path")
                _parse_path(str(item["path"]))          # 路径必须可解析
                keep = [str(x) for x in (item.get("keep_roles") or [])]
                bad = [r for r in keep if r not in known_roles]
                if bad:
                    raise DataScopeError(
                        f"看板 {key!r} 脱敏规则 {item['path']!r} 引用了未识别角色: {bad}")
                prefix = str(item.get("pseudo") or "")
                if prefix and prefix not in pseudo:
                    raise DataScopeError(
                        f"看板 {key!r} 脱敏规则 {item['path']!r} 的 pseudo="
                        f"{prefix!r} 未在 pseudo 表中定义")
                if not prefix and not item.get("literal"):
                    raise DataScopeError(
                        f"看板 {key!r} 脱敏规则 {item['path']!r} 必须声明 "
                        f"pseudo 或 literal 之一")
                masks.append(MaskRule(
                    path=str(item["path"]),
                    label=str(item.get("label") or item["path"]),
                    keep_roles=frozenset(keep),
                    pseudo=prefix,
                    literal=str(item.get("literal") or ""),
                    compose_sep=str(item.get("compose_sep") or ""),
                    also_drop=tuple(str(x) for x in (item.get("also_drop") or [])),
                ))
            dashboards[key] = DashboardScope(
                key=key,
                name=str(node.get("name") or key),
                screen=str(node.get("screen") or key),
                allow_roles=frozenset(allow),
                masks=tuple(masks),
            )
        if not dashboards:
            raise DataScopeError("data_scope.yaml 未声明任何看板")
        return cls(dashboards, pseudo)

    # ---------- 查询 ----------

    def get(self, key: str) -> Optional[DashboardScope]:
        return self._dashboards.get(str(key or ""))

    def keys(self) -> list[str]:
        return list(self._dashboards)

    def by_screen(self, screen: str) -> list[DashboardScope]:
        return [d for d in self._dashboards.values() if d.screen == str(screen or "")]

    def is_allowed(self, dashboard: str, roles: Iterable[str]) -> bool:
        """未声明 = 拒绝（fail-closed）。"""
        scope = self.get(dashboard)
        if scope is None:
            return False
        return scope.allows(roles)

    # ---------- 脱敏 ----------

    def drill_rules(self, dashboard: str, kpi: str) -> tuple[MaskRule, ...]:
        """把「排行行脱敏」推导成「下钻行脱敏」。

        下钻载荷的 ``rows[]`` 与排行的 ``items[]`` 是同一个维度分解，规则理应同源：
        声明一次 ``rankings[key=otd].items[].name``，下钻 kpi=otd 时自动作用于
        ``rows[].name``。否则就有了「排行里看不到客户名、下钻表里全列出来」的后门。
        """
        scope = self.get(dashboard)
        if scope is None or not kpi:
            return ()
        out: list[MaskRule] = []
        pattern = re.compile(r"^rankings\[key=(?P<kpi>[^\]]+)\]\.items\[\](?:\.(?P<tail>.+))?$")
        for rule in scope.masks:
            m = pattern.match(rule.path)
            if not m or m.group("kpi") != str(kpi):
                continue
            tail = (m.group("tail") or "").strip()
            out.append(dataclasses.replace(
                rule, path=f"rows[].{tail}" if tail else "rows[]"))
        return tuple(out)

    def apply(self, dashboard: str, payload: Any, roles: Iterable[str],
              kpi: str = "") -> tuple[Any, list[str]]:
        """就地脱敏并返回 ``(payload, 已脱敏字段标签)``。

        调用方须先确认 ``is_allowed``；本方法只处理字段级规则。
        ``kpi`` 非空表示本次是下钻载荷，会一并套用 ``drill_rules`` 推导出的规则。
        """
        scope = self.get(dashboard)
        role_set = {str(r) for r in roles}
        if scope is None or not payload:
            return payload, []

        rules = list(scope.masks) + list(self.drill_rules(dashboard, kpi))
        active = [m for m in rules if not (m.keep_roles & role_set)]
        if not active:
            return payload, []

        # 先收集需要编号的原始值 → 全局排序分配字母，保证同一批数据多次请求一致，
        # 且同一客户在 risk_orders 与 rankings 里编号相同（否则一比对就还原）。
        buckets: dict[str, set[str]] = {}
        for rule in active:
            if not rule.pseudo:
                continue
            keys = buckets.setdefault(rule.pseudo, set())

            def _collect(container: Any, key: Any, _r: MaskRule = rule,
                         _b: set[str] = keys) -> None:
                _b.add(_r.replacement_key(container[key]))

            _visit(payload, _parse_path(rule.path), _collect)

        maps: dict[str, dict[str, str]] = {}
        for prefix, values in buckets.items():
            ordered = sorted(v for v in values if v)
            maps[prefix] = {
                value: f"{self._pseudo.get(prefix, prefix)} {_alpha(i)}"
                for i, value in enumerate(ordered)
            }

        labels: list[str] = []
        for rule in active:
            segs = _parse_path(rule.path)
            hit = False

            def _rewrite(container: Any, key: Any, _r: MaskRule = rule) -> None:
                nonlocal hit
                value = container[key]
                if _r.literal:
                    container[key] = _r.literal
                else:
                    mapping = maps.get(_r.pseudo) or {}
                    replacement = mapping.get(_r.replacement_key(value))
                    if replacement is None:
                        return          # 未命中不猜，保持原值（宁可漏改也不改错）
                    container[key] = replacement
                hit = True
                item = container[key] if isinstance(container, list) else container
                if _r.also_drop and isinstance(item, dict):
                    for drop_key in _r.also_drop:
                        item.pop(drop_key, None)
                    # 名字已脱敏且下钻键被移除 → 该行不可下钻，前端据此去掉点击区
                    item["masked"] = True

            _visit(payload, segs, _rewrite)
            if hit:                 # 只报真正命中过的字段，避免"声称脱敏了但其实没这根路径"
                labels.append(rule.label)

        return payload, sorted(set(labels))


def _alpha(index: int) -> str:
    """0→A, 25→Z, 26→AA …（客户数量远小于 26，但别留边界坑）"""
    out = ""
    n = index
    while True:
        out = chr(ord("A") + n % 26) + out
        n = n // 26 - 1
        if n < 0:
            return out


# ---------------------------------------------------------------- 单例

_REGISTRY: Optional[DataScopeRegistry] = None
_REGISTRY_PATH: Optional[str] = None


def get_data_scope(path: Optional[str | Path] = None,
                   force_reload: bool = False) -> DataScopeRegistry:
    """数据范围目录单例（惰性加载；测试可注入路径）。"""
    global _REGISTRY, _REGISTRY_PATH
    target = str(Path(path).resolve()) if path else str(DEFAULT_DATA_SCOPE_PATH)
    if force_reload or _REGISTRY is None or _REGISTRY_PATH != target:
        _REGISTRY = DataScopeRegistry.from_yaml(target)
        _REGISTRY_PATH = target
    return _REGISTRY


def reset_data_scope() -> None:
    """清空单例缓存（测试隔离用）。"""
    global _REGISTRY, _REGISTRY_PATH
    _REGISTRY = None
    _REGISTRY_PATH = None
