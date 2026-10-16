"""供应链Agent：SCM来料批次。"""
from typing import Any
from ..db.mysql_client import run_sql


def supply_chain_agent(state: dict[str, Any]) -> dict[str, Any]:
    events = [{"type": "tool_start", "tool": "SQL查询", "detail": "查询今日到货批次"}]

    rows = run_sql(
        "SELECT batch_no, part_name, supplier, incoming_result FROM material_batch "
        "WHERE date(arrival_time)='2026-10-15'"
    ).to_dict(orient="records")

    events.append({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(rows)}条批次"})

    failed = [r for r in rows if r.get("incoming_result") == "fail"]
    return {
        "supply_data": {"rows": rows},
        "abnormal_material": failed or None,
        "supply_summary": f"今日{len(rows)}个批次到货，{'有' if failed else '无'}来料异常",
        "_events": events,
    }
