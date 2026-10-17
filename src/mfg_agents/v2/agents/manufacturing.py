"""制造域模块（S8 维保建议）。

设备取证复用现有冻结 handler（``equipment``）；本模块只补"维保建议"节点
（基于真实 ``equip_alarms`` 行，规则化建议，不编造）。
"""
from __future__ import annotations

from typing import Any

__all__ = ["make_maintenance"]


def make_maintenance(t2s: Any):
    """维保工单建议节点：按设备告警行生成维保动作（数据来自 SQL）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        line = task.entities.get("line_code") or "SMT-1"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询 {line} 设备异常以生成维保建议"})
        qr = t2s.query_registered("equip_alarms", {"line": line}, task.user_ctx)
        rows = qr.rows
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(rows)}条异常"})
        advice = [
            f"{r.get('equipment_id')} {r.get('param_name')}={r.get('param_value')}"
            f"（{r.get('alarm_code')}）→ 停机校准/复测至基准"
            for r in rows]
        summary = (f"维保建议 {len(rows)} 项" if rows
                   else "无设备异常，无需维保（data_gap）")
        payload: dict[str, Any] = {
            "maintenance_advice": advice,
            "maintenance_summary": summary,
            "provenance": [qr.to_dict()],
        }
        if not rows:
            payload["data_gap"] = ["维保建议：无设备异常记录"]
        return {"domain": "manufacturing",
                "status": "ok" if rows else "degraded",
                "payload": payload, "summary": summary}
    return handler
