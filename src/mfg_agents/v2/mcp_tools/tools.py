"""平台能力 → 统一 function call 工具注册（单一事实源）。

把 V2 已有的确定性能力包装成 ``Tool``：数据查询 / 指标 / 明细探查 /
场景清单 / 场景执行 / 待办 / 治理动作 / 权限目录。
**不新增任何业务逻辑**——全部转发给既有服务层（text2sql / orchestrator /
workflow / permission registry），治理权限（表级 ACL / 脱敏 / 会签）在
工具调用链上原样生效。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..contracts.data_source import DataSourceRegistry
from ..core.orchestrator import V2Orchestrator
from ..services.text2sql import Text2SQLService
from ..services.workflow import WorkflowEngine
from .registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)

__all__ = ["build_platform_tools", "register_platform_tools"]


def _runtime(orchestrator: V2Orchestrator) -> Any:
    return orchestrator.runtime


def _require_user(user_ctx: dict[str, Any], action: str) -> dict[str, Any]:
    """写类工具的统一身份门槛（与 HTTP 端点同一口径：403 语义）。"""
    from ..services.workflow import PermissionDenied

    if not isinstance(user_ctx, dict) or not str(user_ctx.get("user_id") or "").strip():
        raise PermissionDenied(
            f"工具调用（{action}）必须携带 user_ctx.user_id："
            "缺少可核验身份时无法判定权限，禁止匿名操作")
    return user_ctx


def _to_plain(obj: Any) -> Any:
    """把服务层对象转成 JSON 安全结构（dataclass / 自定义对象 → dict）。"""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return obj


# ---------- 各工具 handler（全部转发既有服务层，不写新业务逻辑） ----------


def _h_platform_describe(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        rt = _runtime(orch)
        registry = rt.scenario_registry
        return {
            "scenarios": [{"key": s.key, "name": s.name, "domains": list(s.domains)}
                          for s in registry.list()],
            "datasources": rt.data_registry.describe_all(),
            "workflow_pending_total": len(rt.workflow.pending_hitl()),
        }
    return handler


def _h_data_run_query(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        rt = _runtime(orch)
        reg: DataSourceRegistry = rt.data_registry
        name = str(args.get("query_name") or "")
        if not name:
            raise ValueError("缺少 query_name（可先调用 data.list_queries）")
        rows = reg.run_query(name, args.get("params") or {},
                             source=args.get("source"))
        return {"query_name": name, "row_count": len(rows), "rows": rows}
    return handler


def _h_data_list_queries(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        rt = _runtime(orch)
        src = rt.data_registry.get(args.get("source"))
        return {"source": src.name, "queries": sorted(getattr(src, "queries", {}))}
    return handler


def _h_data_metric(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        t2s: Text2SQLService = _runtime(orch).text2sql
        metric_id = str(args.get("metric_id") or "")
        if not metric_id:
            raise ValueError("缺少 metric_id（当前可用：otd_rate）")
        result = t2s.query_metric(metric_id, args.get("params") or {},
                                  user_ctx=user_ctx)
        return {"channel": result.channel, "sql": result.sql,
                "row_count": len(result.rows or []), "rows": result.rows,
                "masked": result.masked, "confidence": result.confidence,
                "fallback": result.fallback,
                "warnings": list(result.warnings or [])}
    return handler


def _h_data_sql_readonly(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        t2s: Text2SQLService = _runtime(orch).text2sql
        question = str(args.get("question") or "")
        if not question:
            raise ValueError("缺少 question（自然语言明细探查）")
        result = t2s.query_detail(question, user_ctx=user_ctx)
        return {"channel": result.channel, "sql": result.sql,
                "row_count": len(result.rows or []), "rows": result.rows,
                "masked": result.masked, "confidence": result.confidence,
                "fallback": result.fallback,
                "warnings": list(result.warnings or [])}
    return handler


def _h_scenario_list(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        items = []
        for s in _runtime(orch).scenario_registry.list():
            items.append({"key": s.key, "name": s.name,
                          "domains": list(s.domains),
                          "version": getattr(s, "version", "")})
        return {"scenarios": items}
    return handler


def _h_scenario_run(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        _require_user(user_ctx, "场景执行")
        query = str(args.get("query") or "")
        if not query:
            raise ValueError("缺少 query")
        result = orch.handle(query, user_ctx=user_ctx)
        return result.to_dict()
    return handler


def _h_workflow_pending(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        wf: WorkflowEngine = _runtime(orch).workflow
        roles = [str(r) for r in (args.get("roles")
                                  or user_ctx.get("roles") or [])]
        tasks = wf.pending_hitl(roles or None)
        items = []
        for t in tasks:
            d = t.to_dict()
            items.append({"task_uid": d["task_uid"], "title": d["title"],
                          "status": d["status"], "priority": d["priority"],
                          "slot": (d.get("governance") or {}).get("slot", ""),
                          "my_action": wf.action_for(t, roles or None),
                          "signoff_status": t.signoff_status()})
        return {"tasks": items, "total": len(items)}
    return handler


def _h_workflow_act(orch: V2Orchestrator) -> Callable[..., dict]:
    """治理链动作（select_action / cosign / approve / reject）——写类工具。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        _require_user(user_ctx, "治理动作")
        wf: WorkflowEngine = _runtime(orch).workflow
        task_id = str(args.get("task_uid") or "")
        action = str(args.get("action") or "")
        if not task_id or not action:
            raise ValueError("缺少 task_uid 或 action")
        kwargs: dict[str, Any] = {
            "user_ctx": user_ctx, "role": args.get("role"),
            "comment": str(args.get("comment") or ""),
        }
        if action == "select_action":
            task = wf.select_action(task_id, str(args.get("action_key") or ""),
                                    actor=str(user_ctx.get("name") or "工具调用"),
                                    **kwargs)
        elif action == "cosign":
            task = wf.cosign(task_id, str(user_ctx.get("name") or "工具调用"),
                             decision=str(args.get("decision") or ""), **kwargs)
        elif action == "approve":
            task = wf.approve(task_id, str(user_ctx.get("name") or "工具调用"),
                              decision=str(args.get("decision") or ""),
                              final=bool(args.get("final")), **kwargs)
        elif action == "reject":
            task = wf.reject(task_id, str(user_ctx.get("name") or "工具调用"),
                             decision=str(args.get("decision") or ""), **kwargs)
        else:
            raise ValueError(f"非法 action: {action!r}"
                             "（可选 select_action / cosign / approve / reject）")
        d = task.to_dict()
        return {"task_uid": task_id, "status": d["status"],
                "signoff_status": task.signoff_status(),
                "history_tail": d["history"][-1] if d["history"] else None}
    return handler


def _h_workflow_governance(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..contracts.permissions import get_permission_registry
        return get_permission_registry().describe()
    return handler


# ---------- 注册 ----------

_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "user_id": {"type": "string", "description": "稳定用户标识（一人一签判定依据）"},
        "name": {"type": "string", "description": "姓名（展示用，不作为身份判定）"},
        "dept": {"type": "string"}, "roles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["user_id", "roles"],
}


def build_platform_tools(orchestrator: V2Orchestrator) -> list[Tool]:
    """构造平台工具清单（ToolRegistry 的数据来源，MCP 与 A2A 共用）。"""
    return [
        Tool(name="platform.describe",
             description="平台能力总览：已注册场景、数据源（含健康状态与表清单）、待办总量",
             input_schema={"type": "object", "properties": {}},
             handler=_h_platform_describe(orchestrator), domain="platform"),
        Tool(name="data.list_queries",
             description="列出某数据源可用的预注册查询名（参数化查询，优先于裸 SQL）",
             input_schema={"type": "object", "properties": {
                 "source": {"type": "string", "description": "数据源名，缺省默认源"}}},
             handler=_h_data_list_queries(orchestrator), domain="data"),
        Tool(name="data.run_query",
             description="执行预注册查询（参数化、走表级 ACL 与脱敏）",
             input_schema={"type": "object", "required": ["query_name"], "properties": {
                 "query_name": {"type": "string"},
                 "params": {"type": "object"},
                 "source": {"type": "string"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_data_run_query(orchestrator), domain="data"),
        Tool(name="data.metric",
             description="查询平台指标（口径统一，当前可用：otd_rate 准交率）；带表级权限与脱敏",
             input_schema={"type": "object", "required": ["metric_id"], "properties": {
                 "metric_id": {"type": "string"},
                 "params": {"type": "object"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_data_metric(orchestrator), domain="data"),
        Tool(name="data.sql_readonly",
             description="自然语言明细探查（LLM 生成 SQL → 八道防线审核 → 只读执行）；超范围返回人工兜底",
             input_schema={"type": "object", "required": ["question"], "properties": {
                 "question": {"type": "string"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_data_sql_readonly(orchestrator), domain="data",
             readonly=False),
        Tool(name="scenario.list",
             description="已注册协同场景清单（停线/来料/客诉 8D/出货/设备）",
             input_schema={"type": "object", "properties": {}},
             handler=_h_scenario_list(orchestrator), domain="scenario"),
        Tool(name="scenario.run",
             description="触发一次协同场景（完整问数链路：路由→DAG→报告，含 HITL 待确认卡）",
             input_schema={"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "业务问题，如：A型号在SMT-1停线，何时复线？"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_scenario_run(orchestrator), domain="scenario",
             readonly=False),
        Tool(name="workflow.pending",
             description="HITL 待办（按角色过滤，含 my_action：待我发起/会签/终批）",
             input_schema={"type": "object", "properties": {
                 "roles": {"type": "array", "items": {"type": "string"}}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_workflow_pending(orchestrator), domain="workflow"),
        Tool(name="workflow.act",
             description="治理链动作：select_action（选处置动作）/ cosign（会签）/ approve（终批）/ reject（驳回）；权限与会签独立性后端强制",
             input_schema={"type": "object", "required": ["task_uid", "action"],
                           "properties": {
                 "task_uid": {"type": "string"},
                 "action": {"type": "string",
                            "enum": ["select_action", "cosign", "approve", "reject"]},
                 "action_key": {"type": "string"},
                 "decision": {"type": "string"},
                 "comment": {"type": "string"},
                 "final": {"type": "boolean"},
                 "role": {"type": "string"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_workflow_act(orchestrator), domain="workflow",
             readonly=False),
        Tool(name="workflow.governance",
             description="治理权限目录（21 权限 × 15 角色 × 7 槽位；工具调用前确认权限用）",
             input_schema={"type": "object", "properties": {}},
             handler=_h_workflow_governance(orchestrator), domain="workflow"),
    ]


def register_platform_tools(registry: ToolRegistry,
                            orchestrator: V2Orchestrator) -> ToolRegistry:
    """把平台工具注册进指定 registry（幂等入口）。"""
    for tool in build_platform_tools(orchestrator):
        registry.register(tool)
    return registry
