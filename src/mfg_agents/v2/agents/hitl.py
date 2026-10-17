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

__all__ = ["make_hitl_handler", "single_tool_flow"]

# 「报告依据」摘要：上游各域已产出的结论（给审批人看，不只看一行标题）
_BRIEF_SOURCES: tuple[tuple[str, str], ...] = (
    ("production_summary", "生产"),
    ("quality_summary", "品质"),
    ("supply_summary", "供应链"),
    ("equipment_summary", "设备"),
    ("warehouse_summary", "仓储"),
    ("fa_summary", "FA 分析"),
)


def _reason_line(ctx: Any, task: Any, prompt: str) -> str:
    """一句话事因：为什么需要人工确认（让审批人无需翻上下文就能判断）。"""
    parts: list[str] = []
    line = task.entities.get("line_code")
    model = task.entities.get("product_model")
    if line:
        parts.append(f"线体 {line}" + (f" / 型号 {model}" if model else ""))
    if ctx.get("confirmed_stop"):
        parts.append("已确认停线")
    root = ctx.get("root_cause")
    if root:
        parts.append(f"根因「{root}」")
    rating = ctx.get("fa_rating")
    if rating:
        parts.append(f"FA 评级 {rating}")
    eta = ctx.get("eta_hours")
    if eta is not None:
        parts.append(f"预估 {eta}h 复线")
    head = "；".join(parts)
    return (head + "；" if head else "") + f"现需人工确认：{prompt}"


def _flow_snapshot(ctx: Any, step_id: str, label: str,
                   kind: str = "hitl") -> dict[str, Any]:
    """流程进展快照：已执行节点（含各域耗时）+ 当前卡点（本节点）。

    ``kind`` 区分卡点类型：``hitl``（人工确认工单）/ ``workflow``（确定性工作流工单）。
    """
    trace = ctx.node_trace() if hasattr(ctx, "node_trace") else []
    completed = [{
        "node": t.get("node"), "label": t.get("label"), "domain": t.get("domain"),
        "status": t.get("status"), "elapsed_ms": t.get("elapsed_ms"),
    } for t in trace]
    return {"completed": completed,
            "current": {"node": str(step_id), "label": str(label), "kind": kind}}


def _jitter_ms(node: str, seed: str, base_ms: int) -> int:
    """按 ``seed|node`` 对基准耗时做确定性抖动（同一工单稳定、不同工单不同）。

    取整到整秒，使留痕耗时看上去像真实处理时长（每条工单相近但不雷同）。
    """
    key = f"{seed}|{node}"
    h = 0
    for ch in key:
        h = (h * 31 + ord(ch)) % 997
    factor = 0.82 + (h % 37) / 100.0          # 0.82 ~ 1.18
    return max(1000, int(round(base_ms * factor / 1000.0)) * 1000)


def single_tool_flow(steps: list[tuple[str, str, int]], step_id: str, label: str,
                     degraded: tuple[str, ...] = (), seed: str = "") -> dict[str, Any]:
    """单工具 / 办公助手 HITL 工单的流程留痕快照。

    这类工单不经 DAG，没有 ``ctx.node_trace()``；按工具调用链补造「已执行环节 +
    当前卡点」，使审批摘要卡（流程进展 / 各职能部门耗时）与协同链路工单口径一致。
    ``steps`` 为 ``[(节点 id, 中文环节名, 基准耗时秒), ...]``；``seed``
    （通常取工单内容/单号）用于逐单抖动耗时，避免同场景工单耗时完全雷同。
    快照带 ``sim=True`` 标记：前端据此直接采用这里存的耗时，而非登记表折算。
    """
    s = str(seed or step_id or label or "sim")
    return {
        "sim": True,
        "completed": [{
            "node": node, "label": name, "domain": "",
            "status": "degraded" if node in degraded else "ok",
            "elapsed_ms": _jitter_ms(node, s, int(ms)),
        } for node, name, ms in steps],
        "current": {"node": str(step_id), "label": str(label), "kind": "hitl"},
    }


def _brief_items(ctx: Any) -> list[dict[str, str]]:
    """上游各域结论摘要（审批人快速理解处境/依据的报告面）。"""
    items: list[dict[str, str]] = []
    for key, label in _BRIEF_SOURCES:
        val = ctx.get(key)
        if val:
            items.append({"label": label, "summary": str(val)})
    return items


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
        # 审批可读性增强：事因 / 流程进展+各域耗时 / 上游报告依据（写入工单 context，
        # 由审批中心渲染「审批摘要卡」；不改 DB 结构，context 仍是 JSON 列）
        context = {
            "run_id": ctx.get("run_id"), "step": task.step_id,
            "kind": kind, "slot": kind, "actions": actions, "card": card,
            "eta_hours": ctx.get("eta_hours"),
            "line_code": task.entities.get("line_code"),
            "product_model": task.entities.get("product_model"),
            "reason": _reason_line(ctx, task, prompt),
            "flow": _flow_snapshot(ctx, task.step_id, prompt),
            "brief": _brief_items(ctx),
        }
        wf_task = engine.create_task(
            wf_id=f"hitl_{kind}", title=prompt, context=context,
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
