"""决策支持型问数的态势汇总（digest 通道）。

"本月度最需关注什么 / 本月重点是什么 / 整体情况怎么样 / 最近有什么风险" ——
这类问题**既无场景触发词、也无具体实体**，却是管理者最高频的一类提问。
旧行为把它落到 chitchat（只回一句问候 + 示例问题），或在降级路径被隐式指派
到某个场景跑重型 DAG（跑出"客诉 8D + 双人工确认"这种答非所问的链路）。

本模块给出**有数据支撑**的答案，而不是靠模型编：
- 遍历各看板聚合（订单履约 / 品质 / 制造 / SPC），逐屏走 ``data_scope`` 裁决
  可见性（与看板端点**同一套 fail-closed 权限**，无权屏如实标注"不可见"）；
- 提取**未达标**的 KPI（warn/danger），叠加 SPC 不可判定特性与待我确认的工单数；
- 按严重度 + 缺口幅度排序，输出"最需关注"清单（业务语言，不暴露内部 key）。

纪律：只读、不建工单、不发起 HITL；数据缺口与无权屏一律如实标注，绝不编造。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["build_digest", "pick_window", "SCREEN_LABELS"]

# 屏 → 业务名（报告里用，不暴露内部 key）
SCREEN_LABELS: dict[str, str] = {
    "orders": "订单履约",
    "quality": "品质",
    "manufacturing": "制造",
    "spc": "SPC 过程能力",
}


def pick_window(query: str) -> str:
    """按提问里的时间词选口径档位（与看板三档位同键），保证答案口径与提问一致。"""
    if any(w in query for w in ("本月", "这个月", "当月")):
        return "month"
    if any(w in query for w in ("今天", "今日")):
        return "day"
    if any(w in query for w in ("本周", "这周", "最近")):
        return "window"
    return "window"


def _visible(screen: str, roles: list[str]) -> bool:
    """看板可见性裁决（与看板端点同一套 data_scope；未声明/无权一律不可见）。"""
    try:
        from .data_scope import get_data_scope
        return get_data_scope().is_allowed(screen, roles)
    except Exception as exc:  # noqa: BLE001 —— 裁决异常按不可见处理（fail-closed）
        logger.warning("[digest] data_scope 裁决异常（按不可见处理）: %s", exc)
        return False


def _kpi_gap(entry: dict) -> float:
    """未达标缺口（正值 = 离目标还差多少）。方向随指标极性翻转。"""
    try:
        value = float(entry.get("value"))
        target = float(entry.get("target"))
    except (TypeError, ValueError):
        return 0.0
    return (target - value) if entry.get("higher_is_better") else (value - target)


def build_digest(registry: Any, workflow: Any, roles: list[str],
                 window: str = "window",
                 query: str = "") -> Optional[dict[str, Any]]:
    """构建跨域态势汇总；无任何可见看板时返回 None（调用方回落原链路，不臆造）。"""
    from . import dashboard as dash

    items: list[dict[str, Any]] = []
    boards: list[str] = []
    skipped: list[str] = []
    gaps: list[str] = []
    spc_notes: list[str] = []
    window_meta: Optional[dict] = None

    def _collect(screen: str, payload: dict) -> None:
        nonlocal window_meta
        # 口径以**首个**（订单/品质/制造，跟随提问所选档位）为准：SPC 看板用的是
        # 固定演示窗口，不能让它把汇总口径覆盖成"最近7天"而与提问不符。
        if window_meta is None and payload.get("window"):
            window_meta = payload["window"]
        boards.append(SCREEN_LABELS.get(screen, screen))
        if payload.get("degraded"):
            gaps.append(f"{SCREEN_LABELS.get(screen, screen)}看板数据不全"
                        f"（{payload.get('degraded_reason') or '数据源降级'}）")
        for kpi in (payload.get("kpis") or []):
            if kpi.get("status") in ("warn", "danger"):
                items.append({
                    "board": SCREEN_LABELS.get(screen, screen),
                    "screen": screen,
                    "label": kpi.get("label"),
                    "value": f"{kpi.get('value')}{kpi.get('unit', '')}",
                    "target": f"{kpi.get('target')}{kpi.get('unit', '')}",
                    "gap": round(_kpi_gap(kpi), 1),
                    "status": kpi.get("status"),
                    "note": kpi.get("scope_note") or "",
                })
        # SPC 逐特性：不可判定 / 能力不足（口径与 SPC 看板同源，不另算一套）
        if screen == "spc":
            caps = payload.get("capabilities") or []
            bad = [c for c in caps if c.get("verdict") in ("不合格", "偏低")]
            undecidable = [c for c in caps if c.get("data_gap")]
            if bad:
                names = "、".join(str(c.get("characteristic")) for c in bad[:3])
                spc_notes.append(f"SPC 有 {len(bad)} 个特性过程能力不足（{names}）")
            if undecidable:
                spc_notes.append(
                    f"SPC 有 {len(undecidable)} 个特性不可判定（缺规格限），未编造结论")

    # ---- 逐屏聚合（权限同源：无权的屏不参与汇总，并如实标注"不可见"）----
    try:
        if _visible("orders", roles):
            _collect("orders", dash.build_order_dashboard(registry, window))
        else:
            skipped.append(SCREEN_LABELS["orders"])
        if _visible("quality", roles):
            _collect("quality", dash.build_quality_dashboard(registry, window))
        else:
            skipped.append(SCREEN_LABELS["quality"])
        if _visible("manufacturing", roles):
            _collect("manufacturing",
                     dash.build_manufacturing_dashboard(registry, window))
        else:
            skipped.append(SCREEN_LABELS["manufacturing"])
        if _visible("spc", roles):
            _collect("spc", dash.build_spc_dashboard(registry))
        else:
            skipped.append(SCREEN_LABELS["spc"])
    except Exception as exc:  # noqa: BLE001 —— 汇总失败回落原链路（降级不崩）
        logger.warning("[digest] 看板聚合失败，回落原链路: %s", exc)
        return None

    # ---- 治理待办（workflow_scope 同源裁决：只看"我能动的"）----
    todo = {"mine": 0, "visible": 0}
    try:
        from . import workflow_scope
        waiting = workflow.list_tasks(status="waiting_hitl")
        todo = workflow_scope.summarize(workflow, waiting, roles)
    except Exception as exc:  # noqa: BLE001 —— 待办失败不拖垮整体
        logger.warning("[digest] 待办汇总失败（跳过）: %s", exc)

    if not boards:
        return None

    # 排序：先按严重度（danger 优先），再按缺口幅度降序
    items.sort(key=lambda x: (0 if x["status"] == "danger" else 1, -abs(x["gap"])))
    return {
        "items": items[:8],
        "total_not_ok": len(items),
        "boards": boards,
        "skipped": skipped,
        "spc_notes": spc_notes,
        "todo": todo,
        "gaps": gaps,
        "window": window_meta or {},
        "window_key": window,
    }
