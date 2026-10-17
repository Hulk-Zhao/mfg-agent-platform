"""供应链域模块（S2 采购/计划/物控 / S4 订单·ATP·物流 / S8 计划窗口）。

角色分层：采购（跟催/评级）、计划（排产影响/窗口）、物控（库存/替代/缺口）、
仓库（ATP）、业务（订单/对客）、物流（在途）。
数字一律来自 SQL（provenance）；数据缺口标 ``data_gap``，绝不编造。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "summarize_procurement",
    "summarize_planning",
    "summarize_business_order",
    "summarize_output",
    "summarize_logistics",
    "summarize_business_sales",
    "make_material_control",
    "make_warehouse_atp",
    "make_atp_analysis",
    "make_planning_window",
]


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------- 摘要函数（供通用「注册查询型」工厂使用） ----------


def summarize_procurement(rows: list[dict], ctx: Any) -> str:
    """供应商到货/不合格统计摘要（SRM 跟催与评级）。"""
    if not rows:
        return "无供应商来料统计（data_gap）"
    fails = [r for r in rows if _to_float(r.get("fail_cnt")) > 0]
    if not fails:
        return f"供应商来料统计 {len(rows)} 条，均无不合格"
    parts = "；".join(
        f"{r.get('supplier')}({r.get('part_no')}) {int(_to_float(r.get('batch_cnt')))}批/"
        f"不合格{int(_to_float(r.get('fail_cnt')))}" for r in fails)
    return f"供应商跟催与评级：{parts}"


def summarize_planning(rows: list[dict], ctx: Any) -> str:
    """排产影响摘要（MES 计划）。"""
    if not rows:
        return "无排产数据（data_gap）"
    plan = sum(int(_to_float(r.get("planned_qty"))) for r in rows)
    actual = sum(int(_to_float(r.get("actual_qty"))) for r in rows)
    return f"受影响排产 {len(rows)} 条：计划{plan}/实际{actual}"


def summarize_business_order(rows: list[dict], ctx: Any) -> str:
    """型号订单/发货单摘要（ATP 前置）。"""
    if not rows:
        return "无订单/发货数据（data_gap）"
    total = sum(int(_to_float(r.get("qty"))) for r in rows)
    return f"订单 {len(rows)} 单，合计 {total}pcs"


def summarize_output(rows: list[dict], ctx: Any) -> str:
    """型号产出达成与 OEE 摘要。"""
    if not rows:
        return "无产出数据（data_gap）"
    r = rows[0]
    return (f"产出达成：{r.get('product_model')} 累计 "
            f"{int(_to_float(r.get('actual_qty')))}pcs，"
            f"平均OEE {round(_to_float(r.get('oee')), 1)}")


def summarize_logistics(rows: list[dict], ctx: Any) -> str:
    """发货/在途摘要。"""
    if not rows:
        return "无在途/发货数据（data_gap）"
    transit = [r for r in rows if r.get("ship_status") == "在途"]
    return f"发货 {len(rows)} 单，在途 {len(transit)} 单"


def summarize_business_sales(rows: list[dict], ctx: Any) -> str:
    """对客订单/交期口径摘要（S3 业务侧）。"""
    if not rows:
        return "无对客订单数据（data_gap）"
    return "对客订单/交期 " + str(len(rows)) + " 单：" + "；".join(
        f"{r.get('order_no')} {r.get('ship_status')} ETA {r.get('eta')}"
        for r in rows[:3])


# ---------- S2/S4：物控（库存/替代/冻结 + 缺口） ----------


def make_material_control(t2s: Any):
    """物控节点：库存可用量 + 型号缺口推演（S2 替代料/冻结；S4 缺口）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        model = task.entities.get("product_model") or "A型号"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": "查询库存可用量（on_hand - reserved）"})
        qr1 = t2s.query_registered("inventory_available_all", {}, task.user_ctx)
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": "查询型号库存与订单量（缺口推演）"})
        qr2 = t2s.query_registered("shortage_by_model", {}, task.user_ctx)
        rows = qr1.rows
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(rows)}行库存，{len(qr2.rows)}行缺口推演"})
        alternatives = [r for r in rows if r.get("product_model") == model]
        frozen = [r for r in rows if _to_float(r.get("available_qty")) <= 0]
        summary = (f"可用库存 {len(rows)} 个型号仓；其中 {len(frozen)} 个仓可用量为 0"
                   if rows else "无库存数据（data_gap）")
        payload: dict[str, Any] = {
            "material_rows": rows,
            "shortage_rows": qr2.rows,
            "alternatives": alternatives,
            "frozen_stock": frozen,
            "material_summary": summary,
            "provenance": [qr1.to_dict(), qr2.to_dict()],
        }
        if not rows:
            payload["data_gap"] = ["物料评估：无库存数据"]
        status = "degraded" if (qr1.fallback or not rows) else "ok"
        return {"domain": "supply_chain", "status": status,
                "payload": payload, "summary": summary}
    return handler


# ---------- S4：仓库 ATP（可用库存 + 发货状态） ----------


def make_warehouse_atp(t2s: Any):
    """仓库 ATP 节点：可用库存 + 发货/在途（ATP 计算的原始输入）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        model = task.entities.get("product_model") or "A型号"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询 {model} 可用库存与发货状态"})
        qr1 = t2s.query_registered("warehouse_available", {"model": model},
                                   task.user_ctx)
        qr2 = t2s.query_registered("shipment_status", {"model": model},
                                   task.user_ctx)
        rows = qr1.rows
        availability = sum(_to_float(r.get("available_qty")) for r in rows)
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(rows)}行库存，{len(qr2.rows)}行发货"})
        summary = f"{model} 可用库存合计 {int(availability)}pcs（{len(rows)}个仓）"
        return {"domain": "supply_chain", "status": "ok" if rows else "degraded",
                "payload": {
                    "atp_raw": rows,
                    "shipment_rows": qr2.rows,
                    "warehouse_summary": summary,
                    "provenance": [qr1.to_dict(), qr2.to_dict()],
                }, "summary": summary}
    return handler


# ---------- S4：ATP 分析（代码计算，绝不编造） ----------


def make_atp_analysis():
    """ATP 承诺量分析：``on_hand - reserved`` 与订单缺口（**代码计算**）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        model = task.entities.get("product_model") or "A型号"
        atp_rows = ctx.get("atp_raw") or []
        orders = ctx.get("orders") or []
        availability = sum(_to_float(r.get("available_qty")) for r in atp_rows)
        demand = sum(_to_float(o.get("qty")) for o in orders)
        gap = demand - availability
        risk = gap > 0
        risk_orders = ([{"order_no": o.get("order_no"), "qty": o.get("qty"),
                         "eta": o.get("eta"), "ship_status": o.get("ship_status")}
                        for o in orders] if risk else [])
        has_data = bool(atp_rows or orders)
        summary = (f"{model} ATP={int(availability)}pcs，订单需求={int(demand)}pcs，"
                   f"缺口={int(gap)}pcs，风险订单 {len(risk_orders)} 单"
                   if has_data else "无 ATP/订单数据（data_gap）")
        atp_result = {"model": model, "available_qty": availability,
                      "demand_qty": demand, "gap_qty": gap,
                      "formula": "on_hand - reserved"}
        sink.emit({"type": "tool_end", "tool": "代码计算",
                   "detail": f"ATP={int(availability)}，缺口={int(gap)}"})
        payload: dict[str, Any] = {
            "atp_result": atp_result,
            "risk_list": risk_orders,
            "risk_orders": risk_orders,
            "atp_summary": summary,
            "provenance": [{"channel": "compute",
                            "formula": "on_hand - reserved",
                            "available_qty": availability,
                            "demand_qty": demand, "gap_qty": gap,
                            "source": "warehouse_available + orders_by_model"}],
        }
        if not has_data:
            payload["data_gap"] = ["ATP 分析：无可用库存/订单数据"]
        return {"domain": "supply_chain",
                "status": "ok" if has_data else "degraded",
                "payload": payload, "summary": summary}
    return handler


# ---------- S8：换产/停机窗口建议 ----------


def make_planning_window(t2s: Any):
    """计划窗口节点：按型号排产给出停机/换产窗口建议（数据来自 SQL）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        model = task.entities.get("product_model") or "A型号"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询 {model} 排产以评估停机窗口"})
        qr = t2s.query_registered("plan_by_model", {"model": model},
                                  task.user_ctx)
        rows = qr.rows
        pending = [r for r in rows
                   if _to_float(r.get("planned_qty")) > _to_float(r.get("actual_qty"))]
        window_required = bool(pending) or bool(rows)
        suggestion = ("建议在计划达成后的空档（当日 20:00-22:00）安排停机维护窗口，"
                      "避开交付高峰" if window_required
                      else "当前排产无富余窗口，建议结合下班时段安排")
        summary = f"排产 {len(rows)} 条，{'需停机窗口' if window_required else '暂无窗口需求'}"
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(rows)}条排产"})
        return {"domain": "supply_chain", "status": "ok" if rows else "degraded",
                "payload": {
                    "window_suggestion": suggestion,
                    "window_required": window_required,
                    "planning_summary": summary,
                    "provenance": [qr.to_dict()],
                }, "summary": summary}
    return handler
