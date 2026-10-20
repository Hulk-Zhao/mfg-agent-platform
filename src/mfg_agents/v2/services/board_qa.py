"""看板口径直查（ask_ctx 通道⓪）：看板问数与看板同源同口径。

设计（多 Agent 协同迭代 · P0-1「结构化问数上下文贯通」）：
- 前端各模块问数入口把结构化上下文（module / kpi / window / filters / view）随
  自然语言并行传给后端（POST /api/v2/query 的 ``ask_ctx`` 字段）；
- 后端命中看板 KPI 时**直接复用看板聚合函数**（dashboard.build_*_dashboard）
  ——同一函数同一数据，口径天然一致；不经 LLM、不重写 SQL，毫秒级返回；
- 权限与脱敏走 **data_scope（与看板 API 同一套裁决）**：``is_allowed`` 判
  看板可见性（fail-closed），``apply`` 套看板同款字段脱敏（如订单屏客户名
  伪名化）——"看板能看、问数也能答；看板里脱敏的，问数里同样脱敏"；
  未声明 / 无权 / 未命中 KPI 一律返回 None 静默回落自然语言链路。

返回 ``QueryResult``（channel="board"），与 Text2SQL 三通道同构——
orchestrator._qa_report 无需分支即可组装回答。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["answer_board_ask", "SCREEN_TABLES"]


# 看板屏 → 核心事实表（审计留痕用；权限裁决走 data_scope，与看板 API 同源）
SCREEN_TABLES: dict[str, tuple[str, ...]] = {
    "manufacturing": ("equipment_run_time", "scrap_record", "work_report", "test_records"),
    "quality": ("quality_defect", "material_batch", "customer_complaint",
                "std_standard_audit"),
    "orders": ("order_fulfillment",),
    "spc": ("spc_measurement",),
}

# ask_ctx.view → 载荷字段（KPI 卡问数取 kpis；TOP5 排行问数取 rankings）
_VIEW_FIELD = {"kpi": "kpis", "ranking": "rankings"}


def _roles_allowed(screen: str, user_ctx: Optional[dict]) -> bool:
    """看板可见性裁决：与看板 API 同一套 data_scope（fail-closed，未声明即拒）。"""
    from .data_scope import get_data_scope

    roles = [str(r) for r in ((user_ctx or {}).get("roles") or []) if str(r)]
    if not roles:
        return False
    try:
        return get_data_scope().is_allowed(screen, roles)
    except Exception as exc:  # noqa: BLE001 —— scope 服务异常按无权处理（fail-closed）
        logger.warning("[board_qa] data_scope 裁决异常（按无权回落）: %s", exc)
        return False


def _build_screen(registry: Any, screen: str, window: str,
                  filters: dict[str, Any]) -> Optional[dict]:
    """按屏调用看板聚合函数（口径与看板 API 完全同源）；异常返回 None 走原链路。"""
    from . import dashboard as dash

    try:
        if screen == "manufacturing":
            return dash.build_manufacturing_dashboard(
                registry, window, stage=filters.get("stage") or None,
                product_category=filters.get("prod_cat") or None)
        if screen == "quality":
            return dash.build_quality_dashboard(
                registry, window, stage=filters.get("stage") or None,
                category=filters.get("category") or None)
        if screen == "orders":
            return dash.build_order_dashboard(registry, window)
        if screen == "spc":
            # 跟随看板口径：否则出现「看板显示 SMT 的数、问数却给全厂的数」= 口径不一致缺陷
            return dash.build_spc_dashboard(
                registry, window, stage=filters.get("stage") or None,
                product_category=filters.get("prod_cat") or None)
    except Exception as exc:  # noqa: BLE001 —— 看板聚合失败回落原链路（降级不崩）
        logger.warning("[board_qa] 看板聚合失败，回落原链路: %s", exc)
    return None


def _find_entry(payload: dict, field: str, kpi_key: str) -> Optional[dict]:
    """按 kpi key 找指标/排行条目。

    先查主字段（kpi→kpis / ranking→rankings），再兜底扫描 payload 内的**其他列表**
    ——固定口径榜（如来料 TOP10）挂在顶层自定义键（``incoming_top10``）或
    嵌套 dict（``views``）下，结构与 rankings 同构（key/label/items）。
    """
    def _pick(items: Any) -> Optional[dict]:
        if isinstance(items, list):
            for e in items:
                if isinstance(e, dict) and e.get("key") == kpi_key:
                    return e
        return None

    hit = _pick(payload.get(field))
    if hit is not None:
        return hit
    for value in payload.values():
        if isinstance(value, list):
            hit = _pick(value)
            if hit is not None:
                return hit
        elif isinstance(value, dict):
            for nested in value.values():
                hit = _pick(nested)
                if hit is not None:
                    return hit
    return None


def answer_board_ask(registry: Any, ask_ctx: Optional[dict],
                     user_ctx: Optional[dict]) -> Optional[Any]:
    """看板口径直查：命中返回 QueryResult（channel="board"），未命中返回 None。

    ask_ctx 形状（前端各问数入口构造，缺项即降级）：
    ``{"module": "mfgboard|dashboard|alerts|reports|search",
        "screen": "manufacturing|quality|orders|spc",
        "kpi": "otd|defect_rate|...", "view": "kpi|ranking",
        "window": "day|month|window|all", "filters": {stage/category/prod_cat}}``
    """
    from .text2sql import QueryResult

    if not isinstance(ask_ctx, dict):
        return None
    screen = str(ask_ctx.get("screen") or "")
    kpi_key = str(ask_ctx.get("kpi") or "")
    if screen not in SCREEN_TABLES or not kpi_key:
        return None
    if not _roles_allowed(screen, user_ctx):
        logger.info("[board_qa] 看板可见性预检未过（screen=%s），回落原链路", screen)
        return None

    t0 = time.perf_counter()
    view = str(ask_ctx.get("view") or "kpi")
    window = str(ask_ctx.get("window") or "window")
    filters = dict(ask_ctx.get("filters") or {})
    payload = _build_screen(registry, screen, window, filters)
    if payload is None:
        return None

    # 看板同款脱敏（data_scope.apply）：订单屏客户名伪名化等规则在问数里
    # 同样生效——"看板里脱敏的，问数里同样脱敏"。
    masked = False
    try:
        from .data_scope import get_data_scope
        roles = [str(r) for r in ((user_ctx or {}).get("roles") or []) if str(r)]
        payload, mask_labels = get_data_scope().apply(screen, payload, roles,
                                                      kpi=kpi_key)
        masked = bool(mask_labels)
    except Exception as exc:  # noqa: BLE001 —— 脱敏异常按无权处理（fail-closed）
        logger.warning("[board_qa] 脱敏异常（按无权回落）: %s", exc)
        return None

    entry = _find_entry(payload, _VIEW_FIELD.get(view, "kpis"), kpi_key)
    if entry is None:
        return None

    # 组装直答行（业务语言，不暴露内部 key）
    win_meta = payload.get("window") or {}
    scope_label = f"{win_meta.get('label') or ''}".strip()
    filter_bits = [str(filters.get(k)) for k in ("stage", "category", "prod_cat")
                   if filters.get(k)]
    if filter_bits:
        scope_label = f"{scope_label} · {' · '.join(filter_bits)}" if scope_label \
            else " · ".join(filter_bits)

    rows: list[dict[str, Any]] = []
    # 排行型（带 items 的条目，含固定口径 TOP 榜）：按行列出
    if "items" in entry or view == "ranking":
        for it in (entry.get("items") or [])[:5]:
            rows.append({"排名": it.get("rank"), "对象": it.get("name"),
                         "值": f"{it.get('value')}{entry.get('unit', '')}",
                         "状态": it.get("status", "")})
        if not rows:
            rows.append({"说明": "该口径下暂无排行数据"})
    else:
        rows.append({
            "指标": entry.get("label"), "值": f"{entry.get('value')}{entry.get('unit', '')}",
            "目标": f"{entry.get('target')}{entry.get('unit', '')}",
            "状态": "达标" if entry.get("status") == "ok" else "未达标",
            "口径": scope_label or win_meta.get("label") or "",
        })
        scope_note = entry.get("scope_note")
        if scope_note:
            rows.append({"说明": scope_note})

    warnings = [w for w in [payload.get("degraded_reason")] if w]
    return QueryResult(
        channel="board", sql="", rows=rows, masked=masked, confidence=0.95,
        warnings=warnings, elapsed_ms=(time.perf_counter() - t0) * 1000,
        provenance={
            "channel": "board", "screen": screen, "kpi": kpi_key,
            "kpi_label": entry.get("label"), "view": view,
            "window": win_meta, "filters": filters,
            "source": "dashboard.build_*_dashboard（与看板同源同口径）",
            "row_count": len(rows), "elapsed_ms": 0.0,
        },
    )
