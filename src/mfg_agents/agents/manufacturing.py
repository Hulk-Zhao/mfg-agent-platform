"""制造Agent：对接MES，硬编码SQL。"""
from typing import Any
from ..db.mysql_client import run_sql


class ManufacturingAgent:
    name = "manufacturing"

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        model = state.get("product_model", "A型号")
        line = state.get("line_code", "SMT-1")
        events = []

        events.append({"type": "tool_start", "tool": "SQL查询",
                       "detail": f"查询{line}线{model}今日OEE与产出"})

        oee_rows = run_sql(
            "SELECT record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min "
            "FROM production_output WHERE line_code=:line AND product_model=:model AND record_date='2026-10-15'",
            {"line": line, "model": model},
        ).to_dict(orient="records")

        stop_rows = run_sql(
            "SELECT * FROM line_stop_event WHERE line_code=:line AND product_model=:model AND status='ongoing'",
            {"line": line, "model": model},
        ).to_dict(orient="records")

        events.append({"type": "tool_end", "tool": "SQL查询",
                       "detail": f"返回{len(oee_rows)}条产出，{len(stop_rows)}条停线事件"})

        confirmed = bool(oee_rows and float(oee_rows[-1].get("oee", 100)) < 50) or bool(stop_rows)

        return {
            "oee_data": {"rows": oee_rows},
            "stop_info": stop_rows[0] if stop_rows else None,
            "confirmed_stop": confirmed,
            "production_summary": (
                f"{line}线{model}今日OEE={oee_rows[-1].get('oee')}%" if oee_rows else "无产出数据"
            ),
            "_events": events,
        }
