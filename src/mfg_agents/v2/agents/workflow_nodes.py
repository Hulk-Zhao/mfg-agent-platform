"""通用工作流节点工厂（S2/S3/S4/S8 工单）。

架构铁律：确定性流程（建单/通知/状态推进）只允许 WorkflowEngine 执行；
LLM/域节点只产"建议"。本工厂只做 ``create_task`` + ``advance("start")`` +
``notifier.send``——**绝不自动 approve/reject**（人工在环）。
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = ["make_workflow_handler"]

# 从 SharedContext 透传进工单 context 的关键键（审计关联）
_CONTEXT_KEYS = (
    "confirmed_stop", "fa_rating", "eta_hours", "root_cause",
    "iqc_rejected", "defect_grade", "complaint_found", "has_alarm",
    "atp_result", "risk_list", "gap_qty",
)


def make_workflow_handler(engine: Any, notifier: Any) -> Callable:
    """构造通用工作流节点 handler：读 ``task.params`` 建单并启动。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        step = task.params
        wf_id = str(step.get("workflow") or step.get("id") or "workflow")
        title = str(step.get("title") or wf_id)
        assignees = [str(a) for a in step.get("assignees", [])]
        level = str(step.get("notify_level", "P1"))
        sla_hours = float(step.get("sla_hours", 2))
        context: dict[str, Any] = {
            "run_id": ctx.get("run_id"),
            "line_code": task.entities.get("line_code"),
            "product_model": task.entities.get("product_model"),
            "step": task.step_id,
        }
        for key in _CONTEXT_KEYS:
            value = ctx.get(key)
            if value is not None:
                context[key] = value
        sink.emit({"type": "tool_start", "tool": "工作流",
                   "detail": f"创建工单：{title}（{level}，SLA {sla_hours:.0f}h）"})
        wf_task = engine.create_task(
            wf_id=wf_id, title=title, context=context,
            assignees=assignees, priority=level, sla_hours=sla_hours)
        wf_task = engine.advance(wf_task.task_uid, "start",
                                 actor="orchestrator", comment=f"{title}启动")
        body = (f"{title}\n请相关角色处理（SLA {sla_hours:.0f}h，超时自动升级）")
        notice = notifier.send(level, assignees, title, body)
        summary = (f"工单 {wf_task.task_uid} 已创建（{level} 通知 "
                   f"{','.join(assignees)}，SLA {sla_hours:.0f}h）")
        sink.emit({"type": "tool_end", "tool": "工作流", "detail": summary})
        return {"domain": "workflow", "status": "ok", "payload": {
            "ticket": wf_task.to_dict(), "notified": assignees, "notice": notice,
            "provenance": [{"channel": "workflow", "task_uid": wf_task.task_uid,
                            "wf_id": wf_id, "assignees": assignees}],
        }, "summary": summary}
    return handler
