"""待办可见性裁决：谁能看到哪条工单。

与 ``data_scope.py`` 的分工（两个**正交**维度，不要合并）：

| 模块 | 维度 | 对象 | 判据来源 |
|---|---|---|---|
| ``data_scope.yaml`` | 业务域 | 聚合看板 / 字段脱敏 | 看板 → allow_roles |
| 本模块 | 治理域 | 逐条待办工单 | 我是否持有该 HITL 槽位的权限 |

一个岗位完全可能「只管停机窗口，但能看全厂订单」，把两者塞进同一份 YAML
会强迫它们同构，因此独立成模块。

判据只有一条：**我是否持有该槽位的任一权限**（propose / cosign / approve）。
这条判据直接来自 ``permissions.yaml`` 的角色矩阵，不引入第二份配置——
新增槽位或调整角色时，可见性自动跟着变，不会漏改。

为什么不用 ``assignees`` 做判据：库里存的是「行政专员」「制造现场负责人」
这类自由文本，多数无法归一化到角色 key。拿它做判据会静默失效（匹配不上 →
全部不可见 → 又变成另一个方向的 bug）。

三种可见性
----------
``mine``
    我能对它做动作（``WorkflowEngine.action_for()`` 非空）。这是「我的活」。
``domain``
    在我治理域内但当前不轮到我（会签未到我、或已被别人签完等着终批），
    以及**未纳管的历史存量**（无主池）。只读可看，不能办。
``hidden``
    与我无关，不出现在任何列表里，详情也取不到。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from ..contracts.permissions import PermissionRegistry

__all__ = [
    "VIS_MINE",
    "VIS_DOMAIN",
    "VIS_HIDDEN",
    "slot_keys_for",
    "classify",
    "visible_tasks",
    "summarize",
]

VIS_MINE = "mine"
VIS_DOMAIN = "domain"
VIS_HIDDEN = "hidden"

_HITL_PREFIX = "hitl."


def slot_keys_for(registry: PermissionRegistry,
                  role_names: Optional[Iterable[Any]]) -> set[str]:
    """角色 → 我持有权限的 HITL 槽位集合。

    空集意味着「没有任何治理职责」（如行政人事），该岗位的待办队列**必然为空**。
    这是刻意设计：不是「查不到」，而是「本来就不该有」。
    """
    out: set[str] = set()
    for key in registry.normalize_roles(role_names or []):
        role = registry.role(key)
        if role is None:
            continue
        for perm in getattr(role, "permissions", ()):
            parts = str(perm).split(".")
            if len(parts) == 3 and parts[0] == "hitl":
                out.add(parts[1])
    return out


def classify(engine, task, role_names: Optional[Iterable[Any]],
             _slots: Optional[set[str]] = None) -> str:
    """单条工单对某组角色的可见性。

    Args:
        engine: ``WorkflowEngine``（提供 ``permission_registry`` 与 ``action_for``）。
        task: ``WorkflowTask``。
        role_names: 角色写法列表（中文名 / 别名 / 英文 key 均可）。
        _slots: 批量调用时传入预计算的槽位集合，避免逐条重算。

    Returns:
        ``mine`` / ``domain`` / ``hidden``。
    """
    slots = _slots if _slots is not None else slot_keys_for(
        engine.permission_registry, role_names)
    if not slots:
        # 无治理职责 → 整个治理队列不可见（含无主池）。
        # 否则 HR 会看到全厂的 8D 与停机工单，这正是要堵的。
        return VIS_HIDDEN
    if not task.is_governed():
        # 未纳管的历史存量：没有槽位就无从判定归属，归入无主池。
        # 只对有治理职责的岗位开放，避免永远沉底无人认领。
        return VIS_DOMAIN
    if task.slot not in slots:
        # 跨域不可见：设备岗不该看到对客 ETA 承诺，业务岗不该看到 8D 关闭。
        return VIS_HIDDEN
    return VIS_MINE if engine.action_for(task, role_names) else VIS_DOMAIN


def visible_tasks(engine, tasks: Iterable[Any],
                  role_names: Optional[Iterable[Any]]
                  ) -> list[tuple[Any, str]]:
    """批量裁决，返回 ``[(task, visibility)]``，已剔除 ``hidden``。

    保持输入顺序（引擎按 created_at 排序），不做二次排序——
    两个列表（我的 / 域内）在前端各自渲染，顺序语义应由调用方决定。
    """
    slots = slot_keys_for(engine.permission_registry, role_names)
    out: list[tuple[Any, str]] = []
    for task in tasks:
        vis = classify(engine, task, role_names, _slots=slots)
        if vis != VIS_HIDDEN:
            out.append((task, vis))
    return out


def summarize(engine, tasks: Iterable[Any],
              role_names: Optional[Iterable[Any]]) -> dict[str, Any]:
    """可见性汇总（给响应信封 ``scope`` 用）。

    **不返回 hidden 的条数**：告诉对方「还有 500 条你看不到」无助于任何操作，
    只会把「有无权限」变成可探测的信息面。
    """
    slots = slot_keys_for(engine.permission_registry, role_names)
    mine = domain = 0
    for task in tasks:
        vis = classify(engine, task, role_names, _slots=slots)
        if vis == VIS_MINE:
            mine += 1
        elif vis == VIS_DOMAIN:
            domain += 1
    return {"mine": mine, "domain": domain, "visible": mine + domain,
            "slots": sorted(slots)}
