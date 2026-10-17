"""仓储物流Agent：WMS库存 + TMS发货/在途状态。"""
from typing import Any
from ..common.sql_tools import text_to_sql


def warehouse_agent(state: dict[str, Any]) -> dict[str, Any]:
    model = state.get("product_model", "A型号")

    inv_q = f"查询{model}的库存（on_hand_qty, reserved_qty）"
    inv = text_to_sql(inv_q)

    ship_q = f"查询{model}所有发货单的状态和eta"
    ship = text_to_sql(ship_q)

    rows = inv.get("rows", [])
    available = 0
    if rows:
        available = int(rows[0].get("on_hand_qty", 0)) - int(rows[0].get("reserved_qty", 0))

    return {
        "inventory_data": inv,
        "shipment_data": ship,
        "available_qty": available,
        "warehouse_summary": f"{model}可用库存={available}，在途发货{len(ship.get('rows',[]))}单",
    }
