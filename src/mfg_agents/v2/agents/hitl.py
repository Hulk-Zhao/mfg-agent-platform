"""HITL 节点工厂（人工在环，绝不自动放行）。

把现有 ``node_registry._make_hitl_handler`` 抽为带 ``kind`` 参数的通用工厂：
- 现有 kinds（restart/eta）行为**逐字节等价**（保证 line_stop 回归不破）；
- 新 kinds（disposition/d8_close/complaint_reply/delivery_commit/
  maintenance_window）复用同一实现：一律创建 ``waiting_hitl`` 工单与 IM 卡片，
  **绝不调用 approve/reject**（关键决策必须人工确认）。

治理层（多人会签）：建单时把 step 的 ``actions`` 全量写入
``context["actions"]``，并写入 ``context["slot"]``（= kind），
供 WorkflowEngine 在 ``select_action`` 时按**选中动作**的 propose/cosign
生成会签要求（不能在建单时把全部动作的 cosign 取并集固定下来）。
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = ["make_hitl_handler"]


def make_hitl_handler(engine: Any, notifier: Any, kind: str) -> Callable:
    """构造 HITL handler（kind 决定工单 wf_id = ``hitl_{kind}`` 与治理槽位）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        step = task.params
        prompt = str(step.get("prompt", "待确认"))
        approvers = [str(r) for r in step.get("approver_roles", [])]
        actions = [dict(a) for a in step.get("actions", [])]
        eta = ctx.get("eta_hours", "?")
        body_lines = [
            f"{prompt}",
            f"线体: {task.entities.get('line_code')} | 型号: {task.entities.get('product_model')}",
            f"FA评级: {ctx.get('fa_rating', '?')} | 预估复线: {eta}h",
            f"审批人: {'、'.join(approvers)}",
        ]
        card = {"title": f"[待确认] {prompt}", "body": "\n".join(body_lines),
                "actions": actions, "level": "P1"}
        wf_task = engine.create_task(
            wf_id=f"hitl_{kind}", title=prompt,
            context={"run_id": ctx.get("run_id"), "step": task.step_id,
                     "kind": kind, "slot": kind, "actions": actions, "card": card,
                     "eta_hours": ctx.get("eta_hours")},
            assignees=approvers, priority="P1", hitl=True, hitl_prompt=prompt)
        notifier.broadcast_hitl({**card, "hitl_prompt": prompt,
                                 "approver_roles": approvers}, approvers)
        summary = f"{prompt} → waiting_hitl（审批人：{'、'.join(approvers)}）"
        return {"domain": "hitl", "status": "ok", "payload": {
            "step": task.step_id, "kind": kind, "prompt": prompt,
            "status": "waiting_hitl", "approver_roles": approvers,
            "actions": actions, "task_uid": wf_task.task_uid,
            "card": card, "slot": kind,
            "governance": dict(wf_task.governance or {}),
            "signoff_status": wf_task.signoff_status(),
            "provenance": [{"channel": "hitl", "task_uid": wf_task.task_uid,
                            "approver_roles": approvers}],
        }, "summary": summary}
    return handler
