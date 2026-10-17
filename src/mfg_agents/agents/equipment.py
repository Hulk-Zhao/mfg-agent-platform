"""设备Agent：EAM设备日志。"""
from typing import Any
from ..common.mysql_client import run_sql


def equipment_agent(state: dict[str, Any]) -> dict[str, Any]:
    line = state.get("line_code", "SMT-1")
    events = [{"type": "tool_start", "tool": "SQL查询", "detail": f"查询{line}线设备异常"}]

    rows = run_sql(
        "SELECT equipment_id, alarm_code, param_name, param_value FROM equipment_log "
        "WHERE line_code=:line AND is_abnormal=1",
        {"line": line},
    ).to_dict(orient="records")

    events.append({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(rows)}条异常"})

    abnormal_params = [
        f"{r.get('equipment_id','设备')} {r.get('param_name','')}={r.get('param_value','')}"
        for r in rows
    ]
    return {
        "equipment_data": {"rows": rows},
        "abnormal_alarms": rows,
        "equipment_summary": (
            f"近2小时{len(rows)}条设备异常：" + "；".join(abnormal_params)
            if rows else "无设备异常"
        ),
        "_events": events,
    }
