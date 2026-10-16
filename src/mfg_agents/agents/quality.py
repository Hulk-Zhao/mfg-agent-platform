"""品质Agent：QMS不良分布。"""
from typing import Any
from ..db.mysql_client import run_sql


def quality_agent(state: dict[str, Any]) -> dict[str, Any]:
    model = state.get("product_model", "A型号")
    line = state.get("line_code", "SMT-1")
    events = [{"type": "tool_start", "tool": "SQL查询", "detail": f"查询{line}线{model}不良分布"}]

    rows = run_sql(
        "SELECT defect_type, SUM(defect_qty) AS total_qty FROM quality_defect "
        "WHERE line_code=:line AND product_model=:model GROUP BY defect_type",
        {"line": line, "model": model},
    ).to_dict(orient="records")

    events.append({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(rows)}种不良"})

    dominant = max(rows, key=lambda r: float(r.get("total_qty", 0))) if rows else None
    qty = float(dominant["total_qty"]) if dominant else 0

    return {
        "quality_data": {"rows": rows},
        "dominant_defect": dominant,
        "quality_summary": (
            f"主导不良：{dominant['defect_type']}，共{int(qty)}pcs" if dominant else "无集中不良"
        ),
        "_events": events,
    }
