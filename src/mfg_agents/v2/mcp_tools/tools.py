"""平台能力 → 统一 function call 工具注册（单一事实源）。

把 V2 已有的确定性能力包装成 ``Tool``：数据查询 / 指标 / 明细探查 /
场景清单 / 场景执行 / 待办 / 治理动作 / 权限目录；P0 增量补齐现场高频的
设备状态 / 质量追溯 / 库存 ATP / 知识检索 / 工单发起 / 报告草稿六类工具。
**不新增任何业务逻辑**——全部转发给既有服务层（text2sql / orchestrator /
workflow / permission registry / RAG），治理权限（表级 ACL / 脱敏 / 会签）
在工具调用链上原样生效。

工具按**五个 server 分治**（``data`` / ``asset`` / ``quality`` / ``workflow`` /
``knowledge``，单 server ≤ 15 个工具），由 ``build_domain_toolsets`` 装配；
``build_platform_tools`` 仍返回全部工具，供 MCP/A2A 向后兼容。
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

from ..contracts.data_source import DataSourceRegistry
from ..core.orchestrator import V2Orchestrator
from ..core.spc import build_pareto, compute_capability, normalize_measurements
from ..services.text2sql import Text2SQLService
from ..services.workflow import WorkflowEngine
from .registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "build_platform_tools", "register_platform_tools",
    "build_domain_toolsets", "register_domain_toolsets",
    "DOMAIN_TOOLSETS",
]


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
        from ..services import workflow_scope

        rt = _runtime(orch)
        registry = rt.scenario_registry
        # 待办计数按调用者角色裁决；无身份 → 0（原实现给全厂总数 621，
        # 等于一个能力发现接口顺带泄漏了全公司治理负载）。
        roles = [str(r) for r in ((user_ctx or {}).get("roles") or [])]
        pending = (workflow_scope.summarize(
            rt.workflow, rt.workflow.list_tasks(status="waiting_hitl"), roles,
            user_ctx={**(user_ctx or {}), "roles": roles})
            if roles else {"mine": 0, "domain": 0, "visible": 0})
        return {
            "scenarios": [{"key": s.key, "name": s.name, "domains": list(s.domains)}
                          for s in registry.list()],
            "datasources": rt.data_registry.describe_all(),
            "workflow_pending_visible": pending["visible"],
            "workflow_pending_mine": pending["mine"],
        }
    return handler


_WEEKDAY_CN = "一二三四五六日"


def _h_platform_now() -> Callable[..., dict]:
    """当前本地日期时间：把"今天/明天/后天/下周"等相对时间换算成具体日期的基准。

    纯时钟查询——不依赖运行时、不做任何业务推理（换算是调用方/LLM 依返回的
    ``date`` 做确定性加减，工具只给事实，绝不臆造日期）。因此不接 orchestrator。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        now = datetime.now().astimezone()
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": f"星期{_WEEKDAY_CN[now.weekday()]}",
            "timezone": str(now.tzinfo or "本地时区"),
            "note": "相对时间换算基准：今天=date；明天=date+1；以此类推",
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
    """待办查询——与 HTTP 通道同一裁决（只能看「我的活」+「我治理域内的活」）。

    ``args.roles`` **只能收窄** ``user_ctx.roles``，不能扩宽。原实现是
    ``args.get("roles") or user_ctx.get("roles")``：只要模型在参数里写一个角色，
    就把真实身份覆盖掉了，等于「自证式权限」——与 README 承诺的
    「A2A/MCP 不是绕过治理的后门」直接矛盾。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services import workflow_scope
        from ..services.workflow import PermissionDenied

        _require_user(user_ctx, "待办查询")
        wf: WorkflowEngine = _runtime(orch).workflow
        registry = wf.permission_registry
        granted = [str(r) for r in (user_ctx.get("roles") or [])]
        requested = [str(r) for r in (args.get("roles") or [])]
        if requested:
            if not (set(registry.normalize_roles(requested))
                    <= set(registry.normalize_roles(granted))):
                raise PermissionDenied("不能查询本人岗位之外的待办")
            roles = requested
        else:
            roles = granted

        pairs = workflow_scope.visible_tasks(
            wf, wf.list_tasks(status="waiting_hitl"), roles,
            user_ctx={**(user_ctx or {}), "roles": roles})
        items = []
        mine = domain = 0
        for t, vis in pairs:
            d = t.to_dict()
            action = wf.action_for(t, roles) if vis == workflow_scope.VIS_MINE else None
            if vis == workflow_scope.VIS_MINE:
                mine += 1
            else:
                domain += 1
            items.append({"task_uid": d["task_uid"], "title": d["title"],
                          "status": d["status"], "priority": d["priority"],
                          "slot": (d.get("governance") or {}).get("slot", ""),
                          "visibility": vis,
                          "my_action": action,
                          "signoff_status": t.signoff_status()})
        return {"tasks": items, "total": len(items),
                "counts": {"mine": mine, "domain": domain}}
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


# ---------- P0 增量工具 handler（全部转发既有服务层，不写新业务逻辑） ----------


def _registered_query_result(orch: V2Orchestrator, name: str,
                             params: dict[str, Any], user_ctx: dict) -> dict:
    """注册查询的统一返回形状（与 ``_h_data_metric`` 同源：channel/sql/rows...）。

    数据源未接入或查询失败时**诚实降级**：返回明确的数据缺口说明，绝不编造数字。
    """
    t2s: Text2SQLService = _runtime(orch).text2sql
    try:
        result = t2s.query_registered(name, params, user_ctx=user_ctx)
    except Exception as exc:  # noqa: BLE001 —— 查询不可用时诚实降级为数据缺口
        logger.warning("[mcp_tools] 注册查询 %s 失败: %s", name, exc)
        return {
            "channel": "degraded", "sql": "", "row_count": 0, "rows": [],
            "masked": False, "confidence": 0.0, "fallback": "data_gap",
            "data_gap": True,
            "warnings": [f"数据缺口：{name} 查询不可用（{exc}），未编造数据"],
        }
    payload: dict[str, Any] = {
        "channel": result.channel, "sql": result.sql,
        "row_count": len(result.rows or []), "rows": result.rows,
        "masked": result.masked, "confidence": result.confidence,
        "fallback": result.fallback, "warnings": list(result.warnings or []),
    }
    # 空结果 / 人工兜底 → 显式数据缺口（有据可查，绝不补齐"看起来对"的数字）
    if result.channel == "fallback" or not result.rows:
        payload["data_gap"] = True
        payload["warnings"].append(
            f"数据缺口：{name} 无有效结果（审核拦截或条件无匹配），未编造数据")
    else:
        payload["data_gap"] = False
    return payload


def _h_asset_status(orch: V2Orchestrator) -> Callable[..., dict]:
    """设备状态：按设备号查最近状态与关键参数趋势（表级 ACL+脱敏原样生效）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        equipment_id = str(args.get("equipment_id") or "").strip()
        if not equipment_id:
            raise ValueError("缺少 equipment_id（设备号，如 PRT-01）")
        return _registered_query_result(
            orch, "asset_status_by_equipment",
            {"equipment_id": equipment_id}, user_ctx)
    return handler


def _h_quality_trace(orch: V2Orchestrator) -> Callable[..., dict]:
    """质量追溯：按批次号做正/反向追溯（来料批次 LEFT JOIN 不良记录）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        batch_no = str(args.get("batch_no") or "").strip()
        if not batch_no:
            raise ValueError("缺少 batch_no（批次号）")
        return _registered_query_result(
            orch, "quality_trace_by_batch", {"batch_no": batch_no}, user_ctx)
    return handler


def _h_inventory_atp(orch: V2Orchestrator) -> Callable[..., dict]:
    """库存 ATP：按产品型号查可用量（available = on_hand - reserved）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        model = str(args.get("model") or "").strip()
        if not model:
            raise ValueError("缺少 model（产品型号）")
        return _registered_query_result(
            orch, "atp_by_model", {"model": model}, user_ctx)
    return handler


def _h_knowledge_search(orch: V2Orchestrator) -> Callable[..., dict]:
    """知识检索：自然语言检索历史案例 / 手册 / SOP（复用 ChromaDB + rag_tools）。

    知识库或依赖不可用时**诚实降级**：返回空命中 + 明确说明，绝不编造检索结果。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("缺少 query（自然语言检索词）")
        top_k = int(args.get("top_k") or 3)
        try:
            from ...common.rag_tools import search_cases
            hits = search_cases(query, n_results=top_k)
        except Exception as exc:  # noqa: BLE001 —— 知识库不可用降级
            logger.warning("[mcp_tools] 知识库不可用: %s", exc)
            return {"hits": [], "hit_count": 0, "degraded": True,
                    "note": f"知识库不可用（{exc}），返回空命中，未编造检索结果"}
        items = [{"document": h.get("document"),
                  "metadata": dict(h.get("metadata") or {})} for h in hits]
        if not items:
            return {"hits": [], "hit_count": 0, "degraded": True,
                    "note": "知识库暂无命中，未编造检索结果"}
        return {"hits": items, "hit_count": len(items), "degraded": False,
                "note": ""}
    return handler


def _h_workflow_create(orch: V2Orchestrator) -> Callable[..., dict]:
    """发起人工确认工单（对应门户 create_hit：**只发起不执行**）。

    Agent 身份永不放行——只产出 ``waiting_hitl`` 工单，落库/审批走既有
    WorkflowEngine（select_action → cosign → approve）。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        _require_user(user_ctx, "发起确认工单")
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("缺少 title（工单标题）")
        from ..services.workflow import GOVERNANCE_SLOTS
        wf: WorkflowEngine = _runtime(orch).workflow
        context = dict(args.get("context") or {})
        # 槽位解析（与 WorkflowEngine._derive_slot 同口径）：context.slot/kind
        # 命中 7 个合法槽位则用 hitl_<slot>，否则用通用 id（保持既有宽松行为）。
        slot = next((str(context.get(k)).strip() for k in ("slot", "kind")
                     if str(context.get(k) or "").strip() in GOVERNANCE_SLOTS), "")
        wf_id = f"hitl_{slot}" if slot else "hitl_manual"
        context.setdefault("origin", "mcp_tool")
        context.setdefault("user_id", str(user_ctx.get("user_id") or ""))
        # 单工具工单无 DAG 节点轨迹：补造流程留痕，供审批摘要卡渲染进展与各环节耗时
        prompt = str(args.get("hitl_prompt") or "")
        if not context.get("flow"):
            from ..agents.hitl import single_tool_flow
            context.setdefault("reason", f"工具发起人工确认；现需人工确认：{prompt or title}")
            context["flow"] = single_tool_flow(
                [("mcp_intent", "MCP 工具 · 请求受理", 6_000),
                 ("mcp_tool_run", "MCP 工具 · 取证与判定", 29_000)],
                wf_id, prompt or title, seed=prompt or title)
        task = wf.create_task(
            wf_id=wf_id, title=title, context=context,
            priority=str(args.get("priority") or "P1"),
            sla_hours=args.get("sla_hours"),
            hitl=True, hitl_prompt=str(args.get("hitl_prompt") or ""))
        return {"task_uid": task.task_uid, "status": task.status,
                "slot": task.slot, "wf_id": task.wf_id}
    return handler


def _fmt_draft_value(value: Any) -> str:
    """草稿字段值 → 可读文本（不编造，仅格式化真实输入）。"""
    if isinstance(value, (list, tuple)):
        return "；".join(_fmt_draft_value(v) for v in value) if value else "—"
    if isinstance(value, dict):
        return "；".join(f"{k}={_fmt_draft_value(v)}" for k, v in value.items())
    return "—" if value is None else str(value)


def _h_report_draft(orch: V2Orchestrator) -> Callable[..., dict]:
    """生成对客脱敏报告 / 内部简报**草稿**（复用 prompts/report 的禁词过滤）。

    LLM 可用则生成；不可用则模板化并置 ``degraded=True``。草稿必须经过禁词
    过滤，且返回 ``is_draft=True`` —— 草稿须人工复核后方可对外。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..prompts.report import (FORBIDDEN_WORDS, _apply_forbidden_words,
                                      build_report_prompt)
        scenario_key = str(args.get("scenario_key") or "general")
        raw_fields = args.get("fields") or {}
        if not isinstance(raw_fields, dict):
            raise ValueError("fields 必须是键值对对象（object）")
        customer_facing = bool(args.get("customer_facing"))
        brief = {str(k): _fmt_draft_value(v) for k, v in raw_fields.items()}

        from ...common.llm_factory import get_llm, llm_available
        degraded = not llm_available()
        draft = ""
        if not degraded:
            try:
                llm = get_llm(temperature=0.3, tier="L2", max_tokens=600,
                              timeout=45, thinking="disabled")
                resp = llm.invoke(build_report_prompt(brief))
                draft = (resp.content if hasattr(resp, "content")
                         else str(resp)).strip()
            except Exception as exc:  # noqa: BLE001 —— 生成失败转模板降级
                logger.warning("[mcp_tools] 报告生成失败，转模板: %s", exc)
                draft = ""
        if not draft:
            degraded = True
            label = "对客报告" if customer_facing else "内部简报"
            body = "；".join(f"{k}：{v}" for k, v in brief.items()) or "（无字段）"
            draft = f"【{label}草稿】{body}"
        draft = _apply_forbidden_words(draft)  # 禁词过滤（双保险，绝不缺席）
        return {
            "is_draft": True, "degraded": degraded,
            "scenario_key": scenario_key, "customer_facing": customer_facing,
            "text": draft, "masked_words": list(FORBIDDEN_WORDS),
            "note": "草稿须人工复核后方可对外（禁词已过滤）",
        }
    return handler


# ---------- P1 增量工具 handler（规则型/确定性，复用既有服务层） ----------


# equipment.predict 打分权重（确定性，无 ML；阈值与权重写死以便单元测试复现）
_RISK_W_ABNORMAL = 60.0    # 异常记录占比权重
_RISK_W_RECENT_ALARM = 25.0  # 最近 5 条告警密度权重
_RISK_W_TREND = 15.0       # 参数上升趋势信号权重
_RISK_LEVEL_HIGH = 60.0    # risk_score ≥ 60 → 高风险
_RISK_LEVEL_MID = 30.0     # 30 ≤ risk_score < 60 → 中风险；否则低风险
_RECENT_WINDOW = 5         # "近期"窗口：最近 N 条记录

_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _first_number(value: Any) -> float | None:
    """从参数值中提取首个数值（如 '38kPa（基准≥55）' → 38.0）；无法解析返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(str(value or ""))
    return float(match.group()) if match else None


def _is_abnormal(value: Any) -> bool:
    """is_abnormal 真值判定（兼容 1/'1'/True/'是'）。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "t", "是")


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _equipment_risk_score(rows: list[dict]) -> dict[str, Any]:
    """确定性设备风险打分（规则型，无 ML 依赖；给定固定 rows → 固定分数）。

    组成（0–100，三段加权，权重见模块常量）：
      - 异常占比：``is_abnormal`` 为真的条数占比 × 60；
      - 近期告警密度：最近 ``_RECENT_WINDOW`` 条内 ``alarm_code`` 非空占比 × 25；
      - 参数趋势信号：可比较参数中"近段均值 > 远段均值"（上升）的占比 × 15。
    等级：``risk_score ≥ 60`` → 高；``≥ 30`` → 中；否则低。
    输入 rows 按 log_time 倒序（最新在前）。无行时不做打分（由上层判数据缺口）。
    """
    rows = [r for r in rows if isinstance(r, dict)]
    n = len(rows)
    if n == 0:
        return {"risk_score": None, "risk_level": None, "evidence": {}}

    abnormal_cnt = sum(1 for r in rows if _is_abnormal(r.get("is_abnormal")))
    abnormal_ratio = abnormal_cnt / n

    window = rows[:_RECENT_WINDOW]
    recent_alarm_cnt = sum(
        1 for r in window if str(r.get("alarm_code") or "").strip())
    recent_alarm_ratio = recent_alarm_cnt / len(window) if window else 0.0

    # 参数趋势：按 param_name 分组，比较远段（较早半段）与近段（较新半段）均值
    def _param_means(records: list[dict]) -> dict[str, float]:
        groups: dict[str, list[float]] = {}
        for r in records:
            name = str(r.get("param_name") or "").strip()
            num = _first_number(r.get("param_value"))
            if name and num is not None:
                groups.setdefault(name, []).append(num)
        return {k: _mean(v) for k, v in groups.items() if _mean(v) is not None}

    mid = n // 2
    newer_means = _param_means(rows[:mid])
    older_means = _param_means(rows[mid:])
    comparable = [p for p in newer_means if p in older_means]
    rising = [p for p in comparable if newer_means[p] > older_means[p]]
    trend_signal = len(rising) / len(comparable) if comparable else 0.0

    score = (_RISK_W_ABNORMAL * abnormal_ratio
             + _RISK_W_RECENT_ALARM * recent_alarm_ratio
             + _RISK_W_TREND * trend_signal)
    score = max(0.0, min(100.0, score))
    risk_score = int(round(score))
    if risk_score >= _RISK_LEVEL_HIGH:
        risk_level = "高"
    elif risk_score >= _RISK_LEVEL_MID:
        risk_level = "中"
    else:
        risk_level = "低"
    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "evidence": {
            "record_count": n,
            "abnormal_count": abnormal_cnt,
            "abnormal_ratio": round(abnormal_ratio, 3),
            "recent_alarm_count": recent_alarm_cnt,
            "recent_window": len(window),
            "trend_comparable_params": comparable,
            "trend_rising_params": rising,
        },
    }


def _maintenance_draft(equipment_id: str, rows: list[dict],
                       scoring: dict[str, Any]) -> dict[str, Any]:
    """由打分证据合成维修工单**草稿**（只发起不执行；字段全部有据可依，不编造）。"""
    ev = scoring.get("evidence") or {}
    actions: list[str] = []
    basis: list[str] = []
    if ev.get("abnormal_count"):
        actions.append("对异常参数项安排现场点检与基准校准")
        basis.append(
            f"异常记录 {ev['abnormal_count']}/{ev['record_count']} 条"
            f"（占比 {ev['abnormal_ratio']}）")
    if ev.get("recent_alarm_count"):
        codes = sorted({str(r.get("alarm_code") or "") for r in rows[: _RECENT_WINDOW]
                        if str(r.get("alarm_code") or "").strip()})
        actions.append(f"核查近期告警（{ '、'.join(codes) }）并复位/更换相关部件")
        basis.append(f"最近 {ev['recent_window']} 条中告警 {ev['recent_alarm_count']} 条")
    if ev.get("trend_rising_params"):
        actions.append("加密监测呈上升趋势的参数：" + "、".join(ev["trend_rising_params"]))
        basis.append("参数近段均值高于远段（上升趋势）：" + "、".join(ev["trend_rising_params"]))
    actions.append("提交设备工程师人工确认维修方案")
    return {
        "is_draft": True,
        "confirmed": False,
        "requires_manual_confirm": True,
        "equipment_id": equipment_id,
        "risk_level": scoring.get("risk_level"),
        "suggested_actions": actions,
        "basis": basis or ["证据不足：无异常/告警记录，建议常规巡检"],
        "note": "维修工单草稿（草稿/未确认）：须人工确认后由 WorkflowEngine 落库与审批，Agent 不执行维修",
    }


def _h_equipment_predict(orch: V2Orchestrator) -> Callable[..., dict]:
    """设备风险预测 + 维修工单**草稿**（规则型、无 ML；只产出草稿不执行）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        equipment_id = str(args.get("equipment_id") or "").strip()
        if not equipment_id:
            raise ValueError("缺少 equipment_id（设备号，如 PRT-01）")
        base = _registered_query_result(
            orch, "asset_status_by_equipment",
            {"equipment_id": equipment_id}, user_ctx)
        if base.get("data_gap"):
            # 诚实降级：无有效记录即不给假分数
            return {**base, "equipment_id": equipment_id, "risk_score": None,
                    "risk_level": None, "maintenance_draft": None,
                    "is_draft": True, "confirmed": False,
                    "note": "数据缺口：无有效设备记录，无法输出风险评分（未编造分数）"}
        scoring = _equipment_risk_score(base["rows"])
        draft = _maintenance_draft(equipment_id, base["rows"], scoring)
        return {**base, "equipment_id": equipment_id,
                "risk_score": scoring["risk_score"],
                "risk_level": scoring["risk_level"],
                "scoring_evidence": scoring["evidence"],
                "maintenance_draft": draft,
                "is_draft": True, "confirmed": False,
                "note": "风险评分为规则型确定性打分；维修工单为草稿（草稿/未确认），"
                        "须人工确认后方可落地"}
    return handler


# 8D 关闭会签动作（照抄 scenarios/customer_complaint_v2.yaml 的 hitl_d8_close，
# 供 quality.8d 发起工单后由 select_action 生成真实会签要求：close → PQE/SQE/工程）
_D8_CLOSE_ACTIONS: list[dict[str, Any]] = [
    {"key": "close", "label": "关闭 8D", "propose": ["质量总监", "QA经理"],
     "cosign": ["PQE", "SQE", "工程"], "require_comment": True,
     "impact": "结案 · 措施需已验证", "followup": "归档 / 更新经验库"},
    {"key": "return_supplement", "label": "退回补充",
     "propose": ["质量总监", "QA经理"], "require_comment": True,
     "impact": "闭环延期 · 需补验证", "followup": "退回责任部门 / 重新提交"},
    {"key": "extend", "label": "延期关闭", "propose": ["质量总监"],
     "require_comment": True, "impact": "客户等待 · 需沟通",
     "followup": "对客说明 / 设定新期限"},
]

# 8D 阶段定义（D1–D8），字段缺失一律"待补"，绝不编造
_D8_STAGES: list[tuple[str, str]] = [
    ("D1", "团队"), ("D2", "问题描述"), ("D3", "围堵措施"), ("D4", "根因"),
    ("D5", "纠正措施"), ("D6", "验证"), ("D7", "预防"), ("D8", "结案"),
]


def _fmt_d8_text(value: Any) -> str:
    """8D 字段值 → 文本（复用草稿格式化，不编造）。"""
    return _fmt_draft_value(value)


def _h_quality_8d(orch: V2Orchestrator) -> Callable[..., dict]:
    """生成结构化 8D 报告**草稿**（只发起关闭工单，不执行关闭）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        _require_user(user_ctx, "生成 8D 草稿")  # 匿名 → PermissionDenied，零工单
        raw_fields = args.get("fields") or {}
        if not isinstance(raw_fields, dict):
            raise ValueError("fields 必须是键值对对象（object）")
        batch_no = str(args.get("batch_no") or "").strip()
        request_signoff = bool(args.get("request_signoff", True))
        title = str(args.get("title") or "").strip()
        if not title:
            title = (f"8D 关闭确认（批次 {batch_no}）" if batch_no
                     else "8D 关闭确认（客诉/批次待补）")

        # 取证：给了批次号就用 quality_trace_by_batch 填 D2/D4（其他阶段仍待补）
        trace: dict[str, Any] = {"data_gap": True, "rows": []}
        if batch_no:
            trace = _registered_query_result(
                orch, "quality_trace_by_batch", {"batch_no": batch_no}, user_ctx)

        d2_evidence: list[str] = []
        d4_evidence: list[str] = []
        if batch_no and not trace.get("data_gap"):
            defect_total = 0
            defect_types: list[str] = []
            incoming: set[str] = set()
            for row in trace.get("rows") or []:
                if row.get("defect_type"):
                    defect_types.append(str(row["defect_type"]))
                    defect_total += int(row.get("defect_qty") or 0)
                if row.get("incoming_result"):
                    incoming.add(str(row["incoming_result"]))
            d2_evidence.append(f"批次 {batch_no} 追溯：来料结论 {sorted(incoming) or '待补'}"
                               f"；不良分布 {defect_types or '待补'}")
            d4_evidence.append(f"来料侧证据：incoming_result={sorted(incoming) or '待补'}"
                               f"；不良合计 {defect_total}（根因待 PQE 判定，未编造）")

        stages: dict[str, dict[str, Any]] = {}
        provenance: dict[str, str] = {}
        for code, name in _D8_STAGES:
            # 用户提供优先；其次批次取证（仅 D2/D4）；否则"待补"
            if raw_fields.get(code) not in (None, "", [], {}):
                content = _fmt_d8_text(raw_fields[code])
                source = "user_fields"
            elif code == "D2" and d2_evidence:
                content = "；".join(d2_evidence)
                source = "batch_trace"
            elif code == "D4" and d4_evidence:
                content = "；".join(d4_evidence)
                source = "batch_trace(证据)"
            else:
                content = "待补"
                source = "待补"
            stages[code] = {"name": name, "content": content, "status": (
                "待补" if source == "待补" else "已填"), "provenance": source}
            provenance[code] = source

        # 关闭需会签：只发起一张 8D 关闭 HITL 工单（Agent 不执行关闭）
        signoff_task: dict[str, Any] | None = None
        if request_signoff:
            wf: WorkflowEngine = _runtime(orch).workflow
            from ..agents.hitl import single_tool_flow
            # 单工具工单无 DAG 节点轨迹：补造流程留痕（批次取证 → 8D 骨架合成），
            # 使审批摘要卡可渲染流程进展与各环节耗时；取证有缺口时该环节标降级。
            brief = ([{"label": "批次", "summary": batch_no}] if batch_no else [])
            if d2_evidence:
                brief.append({"label": "D2 问题描述", "summary": d2_evidence[0]})
            if d4_evidence:
                brief.append({"label": "D4 根因", "summary": d4_evidence[0]})
            context = {
                "slot": "d8_close", "kind": "d8_close",
                "actions": _D8_CLOSE_ACTIONS,
                "origin": "mcp_tool:quality.8d",
                "user_id": str(user_ctx.get("user_id") or ""),
                "batch_no": batch_no,
                "reason": (f"批次 {batch_no} 的 8D 报告待关闭" if batch_no else "8D 报告待关闭")
                          + "；现需人工确认：8D 报告关闭确认",
                "flow": single_tool_flow(
                    [("quality_8d_trace", "质量 · 批次追溯取证", 34_000),
                     ("quality_8d_draft", "质量 · 8D 骨架合成", 47_000)],
                    "hitl_d8_close", "8D 报告关闭确认",
                    degraded=("quality_8d_trace",) if trace.get("data_gap") else (),
                    seed=batch_no or title),
                "brief": brief,
            }
            task = wf.create_task(
                wf_id="hitl_d8_close", title=title, context=context,
                priority="P1", hitl=True, hitl_prompt="8D 报告关闭确认")
            status = task.signoff_status()
            signoff_task = {
                "task_uid": task.task_uid, "status": task.status, "wf_id": task.wf_id,
                "slot": task.slot, "is_governed": task.is_governed(),
                "required_signoffs": status["required"],
                "signoff_status": status,
                "note": "工单已纳入治理链（slot=d8_close + actions）；会签要求由 "
                        "select_action 选定动作后生成，Agent 不执行关闭/终批",
            }

        return {
            "is_draft": True,
            "requires_signoff": request_signoff,
            "title": title,
            "batch_no": batch_no or None,
            "stages": stages,
            "provenance": provenance,
            "trace_data_gap": bool(trace.get("data_gap")),
            "signoff_task": signoff_task,
            "note": "8D 草稿（草稿/未确认）：缺失阶段标注'待补'，绝不编造根因；"
                    "关闭须人工会签，Agent 不执行关闭",
        }
    return handler


_SUPPLY_STALE_DAYS = 14  # 最近到料距今超过该天数 → 供应时效偏紧（中风险）


# ---------- SPC 域（V3.6 新增）：过程能力 / 柏拉图 ----------
# 纯统计内核 core/spc.py 已实现；此处仅做参数校验 + 注册查询取数 + 转发，不写业务投机逻辑。


def _h_spc_capability(orch: V2Orchestrator) -> Callable[..., dict]:
    """过程能力分析：Cp/Cpk（组内 σ）与 Pp/Ppk（整体 σ）双口径 + 正态性提示。

    移植自 Hogo-QA-tool 统计内核（双西格玛口径）。规格限 usl/lsl 必填；
    测量值给 values（扁平）或 subgroups（已分组）；数据不足一律诚实降级，不编造指数。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        characteristic = str(args.get("characteristic") or "").strip()
        usl = _first_number(args.get("usl"))
        lsl = _first_number(args.get("lsl"))
        if usl is None or lsl is None:
            return {"data_gap": True, "characteristic": characteristic, "verdict": "不可判定",
                    "cp": None, "cpk": None, "pp": None, "ppk": None,
                    "warnings": ["缺少规格限 usl/lsl，无法计算能力指数"]}
        values = args.get("values") if isinstance(args.get("values"), list) else None
        subgroups = args.get("subgroups") if isinstance(args.get("subgroups"), list) else None
        try:
            size = int(args.get("subgroup_size") or 5)
        except (TypeError, ValueError):
            size = 5
        try:
            return compute_capability(usl=usl, lsl=lsl, values=values,
                                      subgroups=subgroups, subgroup_size=size,
                                      characteristic=characteristic)
        except ValueError as exc:  # noqa: BLE001 —— 输入非法 → 诚实降级
            return {"data_gap": True, "characteristic": characteristic, "verdict": "不可判定",
                    "cp": None, "cpk": None, "pp": None, "ppk": None,
                    "warnings": [str(exc)]}
    return handler


def _h_spc_pareto(orch: V2Orchestrator) -> Callable[..., dict]:
    """柏拉图：不良项降序 + 累计占比 + 关键少数。

    默认吃现有不良分布注册查询（quality_defect_pareto_all）；也可直接给 items
    （不落库的即席分析）。数据缺口时诚实降级，不编造。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        threshold = _first_number(args.get("threshold"))
        threshold = threshold if threshold and 0 < threshold <= 1 else 0.8
        items = args.get("items") if isinstance(args.get("items"), list) else None
        if items:
            result = build_pareto(items, threshold=threshold)
            result["channel"] = "inline"
            return result
        source = str(args.get("source_query") or "quality_defect_pareto_all").strip()
        fetched = _registered_query_result(orch, source, {}, user_ctx)
        rows = fetched.get("rows") or []
        if fetched.get("data_gap") or not rows:
            return {"data_gap": True, "channel": fetched.get("channel"), "query": source,
                    "total": 0, "count": 0, "items": [], "vital_few": [],
                    "vital_few_count": 0,
                    "warnings": [f"数据缺口：{source} 无不良分布数据，未编造柏拉图"]
                                + list(fetched.get("warnings") or [])}
        mapped = [{"name": r.get("defect_type"),
                   "qty": _first_number(r.get("total_qty"))} for r in rows]
        result = build_pareto(mapped, threshold=threshold)
        result.update({"channel": fetched.get("channel"), "sql": fetched.get("sql"),
                       "query": source, "masked": fetched.get("masked", False)})
        return result
    return handler


_SPC_MEASUREMENTS_TABLE = "spc_measurement"


def _spc_db_path(orch: V2Orchestrator) -> str:
    """取默认数据源的同库路径——SPC 写入与经注册查询的读取落在**同一库**上。

    优先用运行时默认数据源的 db_path（与只读查询同源）；非 SQLite 源回退 settings。
    """
    from ...common.config import settings
    source = _runtime(orch).data_registry.get()
    return getattr(source, "db_path", None) or settings.DB_URL.replace("sqlite:///", "")


def _h_spc_ingest(orch: V2Orchestrator) -> Callable[..., dict]:
    """测量值导入（tier 2 · 写）：先校验后落 spc_measurement，非法值显式拒绝。

    边界：业务**查询**通道始终只读（mode=ro）；本工具是唯一受治理的 SPC 写入通道——
    必须携带可核验身份，先经 ``core/spc.normalize_measurements`` 校验（拒绝非有限数值，
    不静默丢弃/补齐），再落库。全部非法或无输入时诚实降级：不写库、不编造。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        _require_user(user_ctx, "SPC 测量值导入")
        from ..services.spc_store import append_measurements, count_measurements
        characteristic = str(args.get("characteristic") or "").strip()
        db_path = _spc_db_path(orch)
        if not characteristic:
            return {"data_gap": True, "table": _SPC_MEASUREMENTS_TABLE,
                    "characteristic": "", "accepted_count": 0, "written": 0,
                    "rejected": [], "count_after": count_measurements(db_path),
                    "warnings": ["缺少 characteristic（特性名），未导入任何数据"]}
        values = args.get("values") if isinstance(args.get("values"), list) else None
        subgroups = args.get("subgroups") if isinstance(args.get("subgroups"), list) else None
        try:
            size = int(args.get("subgroup_size") or 5)
        except (TypeError, ValueError):
            size = 5
        norm = normalize_measurements(
            characteristic=characteristic, values=values, subgroups=subgroups,
            subgroup_size=size, usl=_first_number(args.get("usl")),
            lsl=_first_number(args.get("lsl")),
            measured_at=(str(args.get("measured_at") or "").strip() or None),
            source=(str(args.get("source") or "").strip() or None),
            process_stage=(str(args.get("process_stage") or "").strip() or None),
            product_category=(str(args.get("product_category") or "").strip() or None))
        accepted, rejected = norm["accepted"], norm["rejected"]
        if not accepted:
            return {"data_gap": True, "table": _SPC_MEASUREMENTS_TABLE,
                    "characteristic": characteristic, "accepted_count": 0,
                    "written": 0, "rejected": rejected,
                    "count_after": count_measurements(db_path, characteristic),
                    "warnings": norm["warnings"] or ["无有效测量值可导入"]}
        written = append_measurements(
            db_path, accepted, ingested_by=str(user_ctx.get("user_id") or ""))
        return {"data_gap": False, "table": _SPC_MEASUREMENTS_TABLE,
                "characteristic": characteristic, "accepted_count": len(accepted),
                "written": written, "rejected": rejected,
                "count_after": count_measurements(db_path, characteristic),
                "warnings": norm["warnings"],
                "note": "已落 spc_measurement；可用 spc.capability/spc.report 按特性取数分析"}
    return handler


def _spc_rows_to_subgroups(rows: Any) -> list[list[float]]:
    """把测量值行按 subgroup_no 归组（组内按 seq 排序），供能力分析复用。"""
    ordered = sorted(rows or [],
                     key=lambda r: (_first_number(r.get("subgroup_no")) or 0,
                                    _first_number(r.get("seq")) or 0))
    buckets: dict[int, list[float]] = {}
    for r in ordered:
        sg = int(_first_number(r.get("subgroup_no")) or 0)
        val = _first_number(r.get("value"))
        if val is None:
            continue
        buckets.setdefault(sg, []).append(val)
    return [buckets[k] for k in sorted(buckets)]


def _h_spc_report(orch: V2Orchestrator) -> Callable[..., dict]:
    """SPC 报表**草稿**（tier 1 · 草稿）：能力分析结论 → 报告草稿（禁词过滤）。

    复用 ``prompts/report`` 的提示词与禁词过滤（与 report.draft 同源）；测量值可取显式
    values/subgroups，或按 characteristic 从 spc_measurement 取（spc.ingest 后的闭环）。
    LLM 不可用即模板化并置 ``degraded=True``；缺口/未提供即如实标注，绝不编造结论。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..prompts.report import (FORBIDDEN_WORDS, _apply_forbidden_words,
                                      build_report_prompt)
        characteristic = str(args.get("characteristic") or "").strip()
        usl = _first_number(args.get("usl"))
        lsl = _first_number(args.get("lsl"))
        values = args.get("values") if isinstance(args.get("values"), list) else None
        subgroups = args.get("subgroups") if isinstance(args.get("subgroups"), list) else None
        try:
            size = int(args.get("subgroup_size") or 5)
        except (TypeError, ValueError):
            size = 5
        warnings: list[str] = []
        if not values and not subgroups and characteristic:
            fetched = _registered_query_result(
                orch, "spc_measurements_by_characteristic",
                {"characteristic": characteristic}, user_ctx)
            rows = fetched.get("rows") or []
            if rows:
                subgroups = _spc_rows_to_subgroups(rows)
                for r in rows:
                    usl = usl if usl is not None else _first_number(r.get("usl"))
                    lsl = lsl if lsl is not None else _first_number(r.get("lsl"))
            else:
                warnings.append("库中无该特性测量值（请先 spc.ingest 或直接给 values/subgroups）")
        capability = None
        if usl is not None and lsl is not None and (values or subgroups):
            capability = compute_capability(
                usl=usl, lsl=lsl, values=values, subgroups=subgroups,
                subgroup_size=size, characteristic=characteristic)
        brief: dict[str, Any] = {"报告类型": "SPC 过程能力分析",
                                 "特性": characteristic or "未注明"}
        if capability and not capability.get("data_gap"):
            brief.update({
                "规格上限": capability.get("spec", {}).get("usl"),
                "规格下限": capability.get("spec", {}).get("lsl"),
                "样本量": capability.get("n_total"),
                "Cp（组内）": capability.get("cp"), "Cpk（组内）": capability.get("cpk"),
                "Pp（整体）": capability.get("pp"), "Ppk（整体）": capability.get("ppk"),
                "判定": capability.get("verdict"),
            })
        else:
            warnings.append("缺少规格限/测量值，未计算能力指数（不编造）")
            brief["判定"] = "不可判定"

        brief_text = "；".join(f"{k}：{_fmt_draft_value(v)}" for k, v in brief.items())
        customer_facing = bool(args.get("customer_facing"))
        from ...common.llm_factory import get_llm, llm_available
        degraded = not llm_available()
        draft = ""
        if not degraded:
            try:
                llm = get_llm(temperature=0.3, tier="L2", max_tokens=600,
                              timeout=45, thinking="disabled")
                resp = llm.invoke(build_report_prompt(brief))
                draft = (resp.content if hasattr(resp, "content")
                         else str(resp)).strip()
            except Exception as exc:  # noqa: BLE001 —— 生成失败转模板降级
                logger.warning("[mcp_tools] SPC 报表生成失败，转模板: %s", exc)
                draft = ""
        if not draft:
            degraded = True
            label = "客户" if customer_facing else "内部"
            draft = f"【SPC 能力分析（{label}）草稿】{brief_text}"
        draft = _apply_forbidden_words(draft)
        return {
            "is_draft": True, "degraded": degraded,
            "characteristic": characteristic, "customer_facing": customer_facing,
            "capability": capability, "text": draft,
            "masked_words": list(FORBIDDEN_WORDS),
            "warnings": warnings,
            "note": "SPC 报表草稿（草稿/未确认）：禁词已过滤，须人工复核后方可对外",
        }
    return handler


def _h_supply_risk(orch: V2Orchestrator) -> Callable[..., dict]:
    """供应风险判定（按料号来料批次；确定性规则，不编造）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        part_no = str(args.get("part_no") or "").strip()
        if not part_no:
            raise ValueError("缺少 part_no（料号，如 CAP-0805-22uF）")
        base = _registered_query_result(
            orch, "supply_risk_by_part", {"part_no": part_no}, user_ctx)
        if base.get("data_gap"):
            return {**base, "part_no": part_no, "risk_level": None,
                    "basis_rows": [],
                    "note": "数据缺口：无该料号到货记录，无法判定供应风险（未编造）"}

        rows = base["rows"]
        fail_rows = [r for r in rows
                     if str(r.get("incoming_result") or "").strip().lower() == "fail"]
        reasons: list[str] = []
        if fail_rows:
            risk_level = "高"
            reasons.append(f"存在不合格来料批次 {len(fail_rows)} 个（incoming_result=fail）")
        else:
            latest = None
            for r in rows:
                dt = _parse_time(r.get("arrival_time"))
                if dt and (latest is None or dt > latest):
                    latest = dt
            age_days = ((datetime.now(timezone.utc) - latest).total_seconds() / 86400
                        if latest else None)
            if age_days is not None and age_days > _SUPPLY_STALE_DAYS:
                risk_level = "中"
                reasons.append(f"最近到料距今约 {age_days:.0f} 天（> {_SUPPLY_STALE_DAYS} 天），时效偏紧")
            else:
                risk_level = "低"
                reasons.append("未发现不合格来料，且最近到料在时效窗口内")
        return {
            **base, "part_no": part_no, "risk_level": risk_level,
            "fail_count": len(fail_rows), "reasons": reasons,
            "basis_rows": rows[:5],
            "note": "风险判定为规则型（不合格→高 / 到料过久→中 / 否则低），"
                    "依据为下列真实到货记录",
        }
    return handler


def _parse_time(value: Any) -> datetime | None:
    """解析到达时间字符串（支持 'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD'）。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _sum_available(rows: list[dict]) -> float | None:
    """可用量合计（ATP 多仓行求和；非数值行忽略；空 → None）。"""
    values = [float(r["available_qty"]) for r in rows
              if isinstance(r.get("available_qty"), (int, float))]
    return float(sum(values)) if values else None


def _h_schedule_what_if(orch: V2Orchestrator) -> Callable[..., dict]:
    """排产/交付影响**确定性推演**（非优化求解器）：缺口量 + 预计补齐天数。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        model = str(args.get("model") or "").strip()
        if not model:
            raise ValueError("缺少 model（产品型号）")
        if args.get("demand_qty") is None:
            raise ValueError("缺少 demand_qty（新增需求/插单量）")
        demand_qty = float(args["demand_qty"])

        atp = _registered_query_result(orch, "atp_by_model", {"model": model}, user_ctx)
        provided = args.get("available_qty")
        if provided is not None:
            available_qty: float | None = float(provided)
            available_source = "param"
        elif not atp.get("data_gap"):
            available_qty = _sum_available(atp["rows"])
            available_source = "atp"
        else:
            available_qty, available_source = None, "unavailable"

        output = _registered_query_result(
            orch, "daily_output_by_model", {"model": model}, user_ctx)
        daily_values = [float(r["output_qty"]) for r in (output.get("rows") or [])
                        if isinstance(r.get("output_qty"), (int, float))]
        daily_avg = _mean(daily_values)

        if available_qty is None:
            return {
                "model": model, "demand_qty": demand_qty,
                "available_qty": None, "available_source": available_source,
                "gap_qty": None, "daily_output_avg": daily_avg,
                "estimated_days": None,
                "estimated_days_reason": "可用量缺失，无法计算缺口与补齐天数",
                "feasible": None, "basis": {"atp": atp.get("rows") or [],
                                            "daily_output": output.get("rows") or []},
                "data_gap": True,
                "note": "数据缺口：ATP 不可用且未提供 available_qty，未编造缺口/天数",
            }

        gap_qty = max(0.0, demand_qty - available_qty)
        if gap_qty <= 0:
            estimated_days: int | None = 0
            est_reason = "无缺口，现有可用量即可满足"
            feasible = True
        elif daily_avg is None or daily_avg <= 0:
            estimated_days = None
            est_reason = "日均产出缺失或为 0，无法估算补齐天数"
            feasible = False
        else:
            estimated_days = int(math.ceil(gap_qty / daily_avg))
            est_reason = f"缺口 {gap_qty:g} ÷ 日均产出 {daily_avg:g} 向上取整"
            feasible = True
        # 数据缺口：可用量来源不可用（且未显式提供），或日均产出取不到
        data_gap = ((bool(atp.get("data_gap")) and provided is None)
                    or bool(output.get("data_gap")))
        return {
            "model": model, "demand_qty": demand_qty,
            "available_qty": available_qty, "available_source": available_source,
            "gap_qty": gap_qty, "daily_output_avg": daily_avg,
            "estimated_days": estimated_days,
            "estimated_days_reason": est_reason,
            "feasible": feasible,
            "basis": {"atp": atp.get("rows") or [],
                      "daily_output": output.get("rows") or []},
            "data_gap": data_gap,
            "note": "确定性推演（非优化求解器）：缺口=max(0,需求-可用量)，"
                    "补齐天数=ceil(缺口/日均产出)",
        }
    return handler


# doc.extract 默认抽取字段
_DOC_DEFAULT_FIELDS = ["批次号", "供应商", "日期", "数量", "检验结论"]

# 已知字段的锚定正则（关键词 + 值捕获组；值捕获组须为 group(1)）
_DOC_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "批次号": re.compile(r"(?:批次号|批次|批\s*号|批号|Lot|LOT|BATCH)\s*[:：]?\s*"
                      r"([A-Za-z0-9][A-Za-z0-9\-_/]{2,})"),
    "供应商": re.compile(r"(?:供应商|供货商|厂商|Supplier)\s*[:：]?\s*([^\s，,。；;、]+)"),
    "日期": re.compile(r"((?:20\d{2})[-/年]\d{1,2}[-/月]\d{1,2}日?)"),
    "数量": re.compile(r"(?:数量|数\s*量|共计|Qty|QTY)\s*[:：]?\s*(\d+(?:\.\d+)?)"),
    "检验结论": re.compile(r"(?:检验结论|检验结果|结论|判定|IQC)\s*[:：]?\s*"
                       r"(合格|不合格|通过|不通过|pass|fail|PASS|FAIL)"),
}


def _extract_field(text: str, field: str) -> dict[str, Any]:
    """规则抽取单字段（正则+关键词锚定），返回值 + 字符区间来源；未命中留空。"""
    pattern = _DOC_FIELD_PATTERNS.get(field)
    match = pattern.search(text) if pattern else None
    if match is None:
        # 自定义字段：按"字段名：值"通用锚定
        generic = re.compile(
            rf"{re.escape(field)}\s*[:：]\s*([^\s，,。；;、]+)")
        match = generic.search(text)
    if match is None:
        return {"value": None, "source_span": None, "status": "未提取"}
    start, end = match.start(1), match.end(1)
    return {"value": match.group(1), "source_span": [start, end], "status": "已提取"}


def _h_doc_extract(orch: V2Orchestrator) -> Callable[..., dict]:
    """单据/报告**文本**字段规则抽取（无多模态即显式降级，绝不编造）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        text = str(args.get("text") or "")
        fields = args.get("fields")
        if fields is not None and not isinstance(fields, (list, tuple)):
            raise ValueError("fields 必须是字段名数组（array）")
        targets = [str(f) for f in fields] if fields else list(_DOC_DEFAULT_FIELDS)
        image_hint = str(args.get("image_hint") or "").strip()

        if not text.strip():
            note = ("需多模态模型解析图片，当前不可用，未编造字段"
                    if image_hint else "无文本可抽取且未提供图片信息，未编造字段")
            return {"fields": {f: {"value": None, "source_span": None,
                                   "status": "未提取"} for f in targets},
                    "field_names": targets, "extracted_count": 0,
                    "degraded": True, "note": note}

        extracted: dict[str, Any] = {}
        count = 0
        for field in targets:
            item = _extract_field(text, field)
            extracted[field] = item
            if item["value"] is not None:
                count += 1
        return {"fields": extracted, "field_names": targets,
                "extracted_count": count, "degraded": False,
                "note": "规则抽取（正则+关键词锚定，source_span 为字符区间来源）；"
                        "未命中字段留空，未编造"}
    return handler


def _metric_public(metric: dict[str, Any]) -> dict[str, Any]:
    """词条 → 公开字典（仅取实际字段，不臆造；``id`` 保留旧口径便于调用方兼容）。"""
    entry_id = str(metric.get("id") or "")
    source = str(metric.get("source") or "")
    raw_id = (entry_id.split(".", 1)[1]
              if source in ("registered_query", "metric_library") else entry_id)
    return {
        "id": raw_id,
        "entry_id": entry_id,
        "name": metric.get("name"),
        "source": source,
        "source_label": metric.get("source_label"),
        "owner": metric.get("owner"),
        "description": metric.get("definition"),
        "definition": metric.get("definition"),
        "tables": list(metric.get("tables") or []),
        "unit": metric.get("unit"),
        "keywords": list(metric.get("keywords") or []),
        "formula": metric.get("formula"),
        "target": metric.get("target"),
        "higher_is_better": metric.get("higher_is_better"),
        "refs": list(metric.get("refs") or []),
    }


def _h_metric_dict(orch: V2Orchestrator) -> Callable[..., dict]:
    """指标字典与口径（R8：三源汇总同一出口；未找到即明示，不编造）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services import metric_dict as metric_dict_svc

        metric_id = str(args.get("metric_id") or "").strip()
        if metric_id:
            entry = metric_dict_svc.resolve_entry(metric_id)
            if entry is None:
                return {"found": False, "metric_id": metric_id, "metric": None,
                        "note": f"未找到口径 {metric_id!r}（不编造；"
                                "可省略 metric_id 查全部目录）"}
            return {"found": True, "metric": _metric_public(entry), "note": ""}
        bundle = metric_dict_svc.build_dictionary()
        metrics = [_metric_public(e) for e in bundle["entries"]]
        return {"found": True, "metrics": metrics, "count": len(metrics),
                "sources": bundle["sources"], "coverage": bundle["coverage"],
                "note": ""}
    return handler


def _explain_scenario(reg: Any, meta: Any) -> dict[str, Any]:
    """场景说明（字段全部来自 registry/YAML 真实字段，拿不到即留空 + note）。"""
    step_labels = [str(s.get("label")) for s in (meta.steps or [])
                   if s.get("label")]
    # 入口指引：entry 节点 id + 其对应步骤 label（若有）
    entry_step = next((s for s in (meta.steps or [])
                       if str(s.get("id")) == meta.entry), None)
    entry_hint = f"入口节点：{meta.entry}"
    if entry_step and entry_step.get("label"):
        entry_hint += f"（{entry_step['label']}）"
    return {
        "key": meta.key,
        "name": meta.name,
        "version": meta.version,
        "domains": list(meta.domains),
        "entry": meta.entry,
        "entry_hint": entry_hint,
        "capabilities": step_labels,
        "outputs": list(meta.outputs),
        "trigger_examples": meta.trigger_words(),
        "triggers": [dict(t) for t in (meta.triggers or [])],
        "hitl_slots": [n["slot"] for n in reg.hitl_nodes(meta)],
        # registry/YAML 未声明"场景→工具映射"，留空不臆造
        "related_tools": [],
        "note": "related_tools 留空：registry 未声明场景→工具映射（未臆造）",
    }


def _h_scenario_explain(orch: V2Orchestrator) -> Callable[..., dict]:
    """场景能力与入口指引（基于 scenario_registry 真实字段；未命中明示）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..contracts.scenario import ScenarioNotFound

        reg = _runtime(orch).scenario_registry
        key = str(args.get("scenario_key") or "").strip()
        if key:
            try:
                meta = reg.get(key)
            except ScenarioNotFound:
                return {"found": False, "scenario_key": key, "scenario": None,
                        "note": f"未找到场景 {key!r}（可省略 scenario_key 查全部）"}
            return {"found": True, "scenario": _explain_scenario(reg, meta), "note": ""}
        scenarios = [_explain_scenario(reg, m) for m in reg.list()]
        return {"found": True, "scenarios": scenarios, "count": len(scenarios),
                "note": ""}
    return handler


# ---------- 办公助手工具 handler（转发 OfficeBackend 契约，不写新业务逻辑） ----------


def _office_actor(user_ctx: dict[str, Any]) -> str:
    """办公操作归属人（本人预订/订单）：姓名优先，缺省 user_id。"""
    return (str(user_ctx.get("name") or "").strip()
            or str(user_ctx.get("user_id") or "").strip() or "演示用户")


def _office_error(exc: Exception) -> dict[str, Any]:
    """办公业务错误 → 结构化返回（保留可读原因，便于调用方换方案）。"""
    return {"ok": False, "error": str(exc)}


def _h_office_list_rooms(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        be = _runtime(orch).office
        rooms = be.list_rooms(args.get("capacity_min"), args.get("equipment"))
        return {"rooms": rooms, "count": len(rooms)}
    return handler


def _h_office_find_available(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        for key in ("date", "start", "end"):
            if not str(args.get(key) or "").strip():
                raise ValueError(f"缺少 {key}（date=YYYY-MM-DD, start/end=HH:MM）")
        try:
            rooms = be.find_available_rooms(
                str(args["date"]), str(args["start"]), str(args["end"]),
                args.get("capacity_min"), args.get("equipment"))
        except OfficeError as exc:
            return _office_error(exc)
        return {"date": args["date"], "start": args["start"], "end": args["end"],
                "available_rooms": rooms, "count": len(rooms)}
    return handler


def _h_office_book_room(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        for key in ("room_id", "date", "start", "end"):
            if not str(args.get(key) or "").strip():
                raise ValueError(f"缺少 {key}")
        try:
            booking = be.book_room(
                str(args["room_id"]), str(args["date"]), str(args["start"]),
                str(args["end"]), str(args.get("title") or ""),
                int(args.get("attendees") or 1), _office_actor(user_ctx))
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "booking": booking,
                "need_approval": booking.get("need_approval", False)}
    return handler


def _h_office_cancel_booking(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        booking_id = str(args.get("booking_id") or "").strip()
        if not booking_id:
            raise ValueError("缺少 booking_id")
        try:
            booking = be.cancel_booking(booking_id, _office_actor(user_ctx))
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "booking": booking}
    return handler


def _h_office_my_bookings(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        items = _runtime(orch).office.list_bookings(_office_actor(user_ctx))
        return {"bookings": items, "count": len(items)}
    return handler


def _h_office_list_restaurants(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        items = _runtime(orch).office.list_restaurants()
        return {"restaurants": items, "count": len(items)}
    return handler


def _h_office_menu(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        restaurant_id = str(args.get("restaurant_id") or "").strip()
        if not restaurant_id:
            raise ValueError("缺少 restaurant_id")
        try:
            return {"ok": True, "menu": be.get_menu(restaurant_id)}
        except OfficeError as exc:
            return _office_error(exc)
    return handler


def _h_office_place_order(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        restaurant_id = str(args.get("restaurant_id") or "").strip()
        items = args.get("items")
        if not restaurant_id:
            raise ValueError("缺少 restaurant_id")
        if not isinstance(items, list) or not items:
            raise ValueError("缺少 items（非空数组，如 [{\"item_id\":\"M101\",\"qty\":2}]）")
        try:
            order = be.place_order(
                restaurant_id, items, str(args.get("meal_time") or "午餐"),
                _office_actor(user_ctx), str(args.get("note") or ""))
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "order": order,
                "need_approval": order.get("need_approval", False)}
    return handler


def _h_office_cancel_order(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        order_id = str(args.get("order_id") or "").strip()
        if not order_id:
            raise ValueError("缺少 order_id")
        try:
            order = be.cancel_order(order_id, _office_actor(user_ctx))
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "order": order}
    return handler


def _h_office_my_orders(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        items = _runtime(orch).office.list_orders(_office_actor(user_ctx))
        return {"orders": items, "count": len(items)}
    return handler


# ---- OA 自助办理（报销/请假/补考勤；转发同一 OfficeBackend 契约） ----


def _h_office_submit_expense(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        for key in ("category", "amount", "expense_date"):
            if args.get(key) in (None, ""):
                raise ValueError(f"缺少 {key}（category/amount/expense_date 必填）")
        try:
            expense = be.submit_expense(
                str(args["category"]), float(args["amount"]),
                str(args["expense_date"]), str(args.get("note") or ""),
                _office_actor(user_ctx), str(args.get("invoice_no") or ""))
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "expense": expense,
                "need_approval": expense.get("need_approval", False),
                "high_value": expense.get("high_value", False)}
    return handler


def _h_office_apply_leave(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        for key in ("leave_type", "start_date", "end_date"):
            if not str(args.get(key) or "").strip():
                raise ValueError(f"缺少 {key}（leave_type/start_date/end_date 必填）")
        try:
            leave = be.apply_leave(
                str(args["leave_type"]), str(args["start_date"]),
                str(args["end_date"]), _office_actor(user_ctx),
                str(args.get("reason") or ""),
                args.get("days") if args.get("days") is not None else None)
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "leave": leave,
                "need_approval": leave.get("need_approval", False),
                "long_leave": leave.get("long_leave", False)}
    return handler


def _h_office_submit_attendance(orch: V2Orchestrator) -> Callable[..., dict]:
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        for key in ("fix_type", "work_date", "time"):
            if not str(args.get(key) or "").strip():
                raise ValueError(f"缺少 {key}（fix_type/work_date/time 必填）")
        try:
            fix = be.submit_attendance_fix(
                str(args["fix_type"]), str(args["work_date"]),
                str(args["time"]), _office_actor(user_ctx),
                str(args.get("reason") or ""))
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "fix": fix,
                "need_approval": fix.get("need_approval", False)}
    return handler


def _h_office_submit_service_request(orch: V2Orchestrator) -> Callable[..., dict]:
    """OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）。

    kind 决定归口与单号前缀；必填校验在 OfficeBackend 侧 fail-closed（缺项返回可读原因）。
    """
    def handler(args: dict, user_ctx: dict) -> dict:
        from ..services.office import OfficeError
        be = _runtime(orch).office
        kind = str(args.get("kind") or "").strip()
        if not kind:
            raise ValueError("缺少 kind（it_repair/it_access/it_device/parking/"
                             "visitor_vehicle/business_car）")
        try:
            req = be.submit_service_request(
                kind, str(args.get("title") or ""), str(args.get("detail") or ""),
                _office_actor(user_ctx), str(args.get("urgency") or "一般"),
                str(args.get("location") or ""), args.get("extra") or {})
        except OfficeError as exc:
            return _office_error(exc)
        return {"ok": True, "service_request": req,
                "need_approval": req.get("need_approval", False),
                "assignee": req.get("assignee", "")}
    return handler


def _h_office_my_requests(orch: V2Orchestrator) -> Callable[..., dict]:
    """本人 OA 申请汇总（报销/请假/补卡/服务工单 + 假期余额；只读）。"""
    def handler(args: dict, user_ctx: dict) -> dict:
        be = _runtime(orch).office
        actor = _office_actor(user_ctx)
        expenses = be.list_expenses(actor)
        leaves = be.list_leaves(actor)
        fixes = be.list_attendance_fixes(actor)
        services = be.list_service_requests(actor)
        return {"expenses": expenses, "leaves": leaves, "attendance_fixes": fixes,
                "service_requests": services,
                "leave_balance": be.leave_balance(actor),
                "count": len(expenses) + len(leaves) + len(fixes) + len(services)}
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
        Tool(name="platform.now",
             description="当前日期时间（本地时区，含星期）：把'今天/明天/后天/大后天/昨天/"
                         "下周'等相对时间表达换算为具体日期时先调用；只给事实、不臆造",
             input_schema={"type": "object", "properties": {}},
             handler=_h_platform_now(), domain="platform",
             readonly=True, tier=0),
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
        # ---- P0 增量（tier 治理分级：只读自动放行 / 预执行同意）----
        Tool(name="asset.status",
             description="设备状态查询：按设备号返回最近状态与关键参数趋势（走表级 ACL 与脱敏）；"
                         "查设备健康初判时用，不要用于设备控制/写操作",
             input_schema={"type": "object", "required": ["equipment_id"], "properties": {
                 "equipment_id": {"type": "string", "description": "设备号，如 PRT-01"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_asset_status(orchestrator), domain="asset",
             readonly=True, tier=0),
        Tool(name="quality.trace",
             description="质量追溯：按批次号做正/反向追溯（来料批次 LEFT JOIN 不良记录，含来料结论与不良分布）；"
                         "不要用于批次放行/判决等写操作",
             input_schema={"type": "object", "required": ["batch_no"], "properties": {
                 "batch_no": {"type": "string", "description": "批次号，如 BATCH-A-1015"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_quality_trace(orchestrator), domain="quality",
             readonly=True, tier=0),
        Tool(name="inventory.atp",
             description="库存 ATP 查询：按产品型号返回各仓可用量（available = on_hand - reserved）；"
                         "不要用于库存调拨/锁定等写操作",
             input_schema={"type": "object", "required": ["model"], "properties": {
                 "model": {"type": "string", "description": "产品型号，如 A型号"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_inventory_atp(orchestrator), domain="asset",
             readonly=True, tier=0),
        Tool(name="knowledge.search",
             description="知识检索：自然语言检索历史案例/手册/SOP，返回命中案例与出处元数据；"
                         "检索不可用时返回空命中并说明，不要臆造结论",
             input_schema={"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "自然语言检索词"},
                 "top_k": {"type": "integer", "description": "返回条数，默认 3"}}},
             handler=_h_knowledge_search(orchestrator), domain="knowledge",
             readonly=True, tier=0),
        Tool(name="workflow.create",
             description="发起人工确认工单（只发起不执行）：产出待确认 HITL 工单，"
                         "落库/审批走既有治理链；Agent 身份不放行、不持 approve 权限",
             input_schema={"type": "object", "required": ["title"], "properties": {
                 "title": {"type": "string", "description": "工单标题"},
                 "context": {"type": "object", "description": "上下文（可含 slot/kind 以归一会签槽位）"},
                 "priority": {"type": "string", "description": "优先级，默认 P1"},
                 "sla_hours": {"type": "number", "description": "SLA 小时数，缺省用引擎默认"},
                 "hitl_prompt": {"type": "string", "description": "人工确认提示语"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_workflow_create(orchestrator), domain="workflow",
             readonly=False, tier=1),
        Tool(name="report.draft",
             description="生成对客脱敏报告/内部简报草稿（经禁词过滤，标注 is_draft/degraded）；"
                         "草稿须人工复核后方可对外，不要直接对外发送",
             input_schema={"type": "object", "required": ["fields"], "properties": {
                 "scenario_key": {"type": "string", "description": "场景键，缺省 general"},
                 "fields": {"type": "object", "description": "报告字段键值对"},
                 "customer_facing": {"type": "boolean", "description": "是否对客（脱敏口径）"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_report_draft(orchestrator), domain="knowledge",
             readonly=True, tier=0),
        # ---- P1 增量（分层推进：规则型只读 / 写类只发起）----
        Tool(name="equipment.predict",
             description="设备风险预测（规则型、无 ML）：按设备号做确定性风险打分并产出"
                         "维修工单**草稿**（草稿/未确认，须人工确认）；数据不足时如实降级，"
                         "不要用于设备控制/写操作，不要据草稿直接派工",
             input_schema={"type": "object", "required": ["equipment_id"], "properties": {
                 "equipment_id": {"type": "string", "description": "设备号，如 PRT-01"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_equipment_predict(orchestrator), domain="asset",
             readonly=True, tier=0),
        Tool(name="quality.8d",
             description="生成结构化 8D 报告**草稿**（D1–D8，缺失阶段标'待补'，绝不编造根因）；"
                         "关闭需会签——只发起一张 8D 关闭 HITL 工单，Agent 不执行关闭/终批",
             input_schema={"type": "object", "properties": {
                 "title": {"type": "string", "description": "标题，缺省按批次/客诉生成"},
                 "batch_no": {"type": "string", "description": "批次号（给了即取证填 D2/D4）"},
                 "fields": {"type": "object", "description": "D1–D8 已有内容（键为 D1..D8）"},
                 "request_signoff": {"type": "boolean",
                                     "description": "是否发起关闭会签工单，默认 true"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_quality_8d(orchestrator), domain="quality",
             readonly=False, tier=1),
        Tool(name="spc.capability",
             description="过程能力分析（SPC）：Cp/Cpk（组内 σ）与 Pp/Ppk（整体 σ）**双口径**，"
                         "含正态性提示与合格判定（Cpk≥1.33 合格）；给 values（扁平）或 "
                         "subgroups（已分组）+ usl/lsl；数据不足诚实降级，不编造指数",
             input_schema={"type": "object", "properties": {
                 "characteristic": {"type": "string",
                                    "description": "特性名（仅用于标注，如 外壳长度）"},
                 "values": {"type": "array", "items": {"type": "number"},
                            "description": "扁平测量值（与 subgroups 二选一）"},
                 "subgroups": {"type": "array", "items": {"type": "array",
                                "items": {"type": "number"}},
                               "description": "分组测量值（与 values 二选一）"},
                 "subgroup_size": {"type": "integer", "description": "子组容量，缺省 5"},
                 "usl": {"type": "number", "description": "规格上限"},
                 "lsl": {"type": "number", "description": "规格下限"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_spc_capability(orchestrator), domain="spc",
             readonly=True, tier=0),
        Tool(name="spc.pareto",
             description="柏拉图（SPC）：不良项降序 + 累计占比 + 80% 关键少数；"
                         "默认取现有不良分布（quality_defect_pareto_all），也可直接给 "
                         "items=[{name,qty}] 做即席分析；数据缺口诚实降级，不编造",
             input_schema={"type": "object", "properties": {
                 "items": {"type": "array", "items": {
                     "type": "object",
                     "properties": {"name": {"type": "string"},
                                    "qty": {"type": "number"}},
                     "required": ["name", "qty"]},
                     "description": "不良项与数量（给了即做即席分析）"},
                 "source_query": {"type": "string",
                                  "description": "取数注册查询名，缺省 quality_defect_pareto_all"},
                 "threshold": {"type": "number",
                               "description": "关键少数累计分界，缺省 0.8"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_spc_pareto(orchestrator), domain="spc",
             readonly=True, tier=0),
        Tool(name="spc.ingest",
             description="测量值导入（写）：先经确定性校验再落 spc_measurement 表——"
                         "非法值显式拒绝、绝不静默丢弃或编造；须携带可核验身份；"
                         "返回导入回执（accepted/rejected/count_after）",
             input_schema={"type": "object", "required": ["characteristic"], "properties": {
                 "characteristic": {"type": "string", "description": "特性名，如 外壳长度"},
                 "values": {"type": "array", "items": {"type": "number"},
                            "description": "扁平测量值（与 subgroups 二选一）"},
                 "subgroups": {"type": "array", "items": {"type": "array",
                                "items": {"type": "number"}},
                               "description": "分组测量值（与 values 二选一）"},
                 "subgroup_size": {"type": "integer",
                                   "description": "子组容量（切分 values），缺省 5"},
                 "usl": {"type": "number", "description": "规格上限（随行落库，供后续分析）"},
                 "lsl": {"type": "number", "description": "规格下限"},
                 "measured_at": {"type": "string", "description": "测量时间（可选）"},
                 "source": {"type": "string", "description": "数据来源（可选，如 设备/手工）"},
                 "process_stage": {"type": "string",
                                   "description": "工段（可选）：SMT/PCM/PACK/整机；"
                                                  "缺省不写该列，非法值整批拒绝"},
                 "product_category": {"type": "string",
                                      "description": "产品分类（可选）：消费电子/智能电源/"
                                                     "储能电源/车载；缺省不写该列，"
                                                     "非法值整批拒绝"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_spc_ingest(orchestrator), domain="spc",
             readonly=False, tier=2),
        Tool(name="spc.report",
             description="SPC 报表**草稿**（经禁词过滤，标注 is_draft/degraded）：按特性给 "
                         "usl/lsl + values/subgroups，或直接按特性取已导入测量值分析；"
                         "草稿须人工复核后方可对外",
             input_schema={"type": "object", "properties": {
                 "characteristic": {"type": "string", "description": "特性名（用于取数与标注）"},
                 "usl": {"type": "number", "description": "规格上限"},
                 "lsl": {"type": "number", "description": "规格下限"},
                 "values": {"type": "array", "items": {"type": "number"}},
                 "subgroups": {"type": "array", "items": {"type": "array",
                                "items": {"type": "number"}}},
                 "subgroup_size": {"type": "integer",
                                   "description": "子组容量（切分 values），缺省 5"},
                 "customer_facing": {"type": "boolean", "description": "是否对客（脱敏口径）"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_spc_report(orchestrator), domain="knowledge",
             readonly=False, tier=1),
        Tool(name="supply.risk",
             description="供应风险查询：按料号返回来料批次结论与风险等级（不合格→高 / "
                         "到料过久→中 / 否则低）；查不到即明确缺口，不要编造风险",
             input_schema={"type": "object", "required": ["part_no"], "properties": {
                 "part_no": {"type": "string", "description": "料号，如 CAP-0805-22uF"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_supply_risk(orchestrator), domain="supply",
             readonly=True, tier=0),
        Tool(name="schedule.what_if",
             description="排产/交付影响**确定性推演**（非优化求解器）：缺口量 = max(0,需求-可用量)，"
                         "补齐天数 = ceil(缺口/日均产出)；产出缺失即如实标注无法估算，不要编造天数",
             input_schema={"type": "object", "required": ["model", "demand_qty"],
                           "properties": {
                 "model": {"type": "string", "description": "产品型号，如 A型号"},
                 "demand_qty": {"type": "number", "description": "新增需求/插单量"},
                 "available_qty": {"type": "number",
                                   "description": "可用量，缺省用 ATP 取"}},
                 "user_ctx": _USER_SCHEMA},
             handler=_h_schedule_what_if(orchestrator), domain="schedule",
             readonly=True, tier=0),
        Tool(name="doc.extract",
             description="单据/检验报告**文本**字段规则抽取（正则+关键词锚定，带字符区间来源）；"
                         "仅图片时显式降级（需多模态，当前不可用），未命中留空不编造",
             input_schema={"type": "object", "properties": {
                 "text": {"type": "string", "description": "原始单据/报告文本"},
                 "fields": {"type": "array", "items": {"type": "string"},
                            "description": "目标字段名，缺省用常见字段组"},
                 "image_hint": {"type": "string",
                                "description": "有图片但当前不可解析时的说明"}}},
             handler=_h_doc_extract(orchestrator), domain="document",
             readonly=True, tier=0),
        Tool(name="metric.dict",
             description="数据口径词典（三源汇总：注册查询 / 指标库 / 看板 KPI）："
                         "返回词条 id / 来源 / 口径说明 / 公式 / 引用方；"
                         "未命中即明示'未找到'，不要编造口径",
             input_schema={"type": "object", "properties": {
                 "metric_id": {"type": "string",
                               "description": "词条 id 或旧指标 id（缺省返回全部目录）"}}},
             handler=_h_metric_dict(orchestrator), domain="data",
             readonly=True, tier=0),
        Tool(name="scenario.explain",
             description="场景能力与入口指引：返回场景 key/名称/域/版本 + 能力（步骤）"
                         "+ 触发示例 + 入口指引 + HITL 槽位；未命中即明示'未找到'",
             input_schema={"type": "object", "properties": {
                 "scenario_key": {"type": "string",
                                  "description": "场景键，缺省返回全部"}}},
             handler=_h_scenario_explain(orchestrator), domain="scenario",
             readonly=True, tier=0),
        # ---- 办公助手（会议室/订餐；转发 OfficeBackend 契约，可换真实系统） ----
        Tool(name="office.list_rooms",
             description="按容量/设备筛选会议室（含位置与是否需授权）；"
                         "订会议室前先查可用时用 office.find_available_rooms",
             input_schema={"type": "object", "properties": {
                 "capacity_min": {"type": "integer", "description": "最小容纳人数"},
                 "equipment": {"type": "array", "items": {"type": "string"}},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_list_rooms(orchestrator), domain="office",
             readonly=True, tier=0),
        Tool(name="office.find_available_rooms",
             description="查某时段可用会议室（自动规避已预订冲突）",
             input_schema={"type": "object", "required": ["date", "start", "end"],
                           "properties": {
                 "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                 "start": {"type": "string", "description": "开始 HH:MM"},
                 "end": {"type": "string", "description": "结束 HH:MM"},
                 "capacity_min": {"type": "integer"},
                 "equipment": {"type": "array", "items": {"type": "string"}},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_find_available(orchestrator), domain="office",
             readonly=True, tier=0),
        Tool(name="office.book_room",
             description="预订会议室（本人名下，自动查时段冲突，返回预订号）；"
                         "需授权资源（报告厅/培训室）须经人工确认",
             input_schema={"type": "object",
                           "required": ["room_id", "date", "start", "end"],
                           "properties": {
                 "room_id": {"type": "string"},
                 "date": {"type": "string"}, "start": {"type": "string"},
                 "end": {"type": "string"},
                 "title": {"type": "string", "description": "会议主题"},
                 "attendees": {"type": "integer"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_book_room(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.cancel_booking",
             description="取消本人会议室预订（只能取消本人名下预订）",
             input_schema={"type": "object", "required": ["booking_id"],
                           "properties": {"booking_id": {"type": "string"},
                                          "user_ctx": _USER_SCHEMA}},
             handler=_h_office_cancel_booking(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.my_bookings",
             description="查我的会议室预订（本人名下有效预订）",
             input_schema={"type": "object", "properties": {"user_ctx": _USER_SCHEMA}},
             handler=_h_office_my_bookings(orchestrator), domain="office",
             readonly=True, tier=0),
        Tool(name="office.list_restaurants",
             description="列出园区餐厅（员工食堂/商务餐厅，含是否需授权）",
             input_schema={"type": "object", "properties": {"user_ctx": _USER_SCHEMA}},
             handler=_h_office_list_restaurants(orchestrator), domain="office",
             readonly=True, tier=0),
        Tool(name="office.menu",
             description="查某餐厅菜单与价格（下单前先看菜）",
             input_schema={"type": "object", "required": ["restaurant_id"],
                           "properties": {"restaurant_id": {"type": "string"},
                                          "user_ctx": _USER_SCHEMA}},
             handler=_h_office_menu(orchestrator), domain="office",
             readonly=True, tier=0),
        Tool(name="office.place_order",
             description="下单订餐（本人名下，按菜单计价，返回订单号与金额）；"
                         "商务客餐/超 500 元订单须经人工确认",
             input_schema={"type": "object", "required": ["restaurant_id", "items"],
                           "properties": {
                 "restaurant_id": {"type": "string"},
                 "items": {"type": "array", "items": {
                     "type": "object", "properties": {
                         "item_id": {"type": "string"},
                         "qty": {"type": "integer"}},
                     "required": ["item_id", "qty"]}},
                 "meal_time": {"type": "string", "description": "档期，如 午餐/晚餐"},
                 "note": {"type": "string"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_place_order(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.cancel_order",
             description="取消本人订餐订单（只能取消本人名下订单；已下单未取餐可撤）",
             input_schema={"type": "object", "required": ["order_id"],
                           "properties": {"order_id": {"type": "string"},
                                          "user_ctx": _USER_SCHEMA}},
             handler=_h_office_cancel_order(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.my_orders",
             description="查我的订餐订单（本人名下有效订单）",
             input_schema={"type": "object", "properties": {"user_ctx": _USER_SCHEMA}},
             handler=_h_office_my_orders(orchestrator), domain="office",
             readonly=True, tier=0),
        # ---- OA 自助办理（报销/请假/补考勤；写操作需人工确认） ----
        Tool(name="office.submit_expense",
             description="提交费用报销单（本人名下，返回报销号）；"
                         "报销一律需人工确认（财务专员）",
             input_schema={"type": "object",
                           "required": ["category", "amount", "expense_date"],
                           "properties": {
                 "category": {"type": "string",
                              "enum": ["差旅", "招待", "办公用品", "交通", "其他"]},
                 "amount": {"type": "number", "description": "金额（元）"},
                 "expense_date": {"type": "string", "description": "发生日期 YYYY-MM-DD"},
                 "note": {"type": "string", "description": "事由"},
                 "invoice_no": {"type": "string", "description": "发票号（可选）"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_submit_expense(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.apply_leave",
             description="提交请假申请（本人名下，返回请假号）；"
                         "请假一律需人工确认（部门主管）",
             input_schema={"type": "object",
                           "required": ["leave_type", "start_date", "end_date"],
                           "properties": {
                 "leave_type": {"type": "string",
                                "enum": ["年假", "事假", "病假", "调休", "婚假", "产假"]},
                 "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                 "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                 "reason": {"type": "string", "description": "请假事由"},
                 "days": {"type": "number", "description": "天数（缺省按自然日含端点算）"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_apply_leave(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.submit_attendance_fix",
             description="提交补考勤打卡申请（上班/下班补卡，返回补卡号）；"
                         "补卡一律需人工确认（HR 专员）",
             input_schema={"type": "object",
                           "required": ["fix_type", "work_date", "time"],
                           "properties": {
                 "fix_type": {"type": "string",
                              "enum": ["上班补卡", "下班补卡"]},
                 "work_date": {"type": "string", "description": "补卡日期 YYYY-MM-DD"},
                 "time": {"type": "string", "description": "补卡时间 HH:MM"},
                 "reason": {"type": "string", "description": "补卡事由"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_submit_attendance(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.submit_service_request",
             description="提交 OA 服务工单（IT 故障报修 / 账号与权限申请 / 设备领用申请 / "
                         "车位申请 / 访客车辆报备 / 公务用车申请），返回服务工单号；"
                         "按 kind 归口 IT 运维专员或行政后勤专员，一律需人工确认",
             input_schema={"type": "object",
                           "required": ["kind", "title"],
                           "properties": {
                 "kind": {"type": "string",
                          "enum": ["it_repair", "it_access", "it_device",
                                   "parking", "visitor_vehicle", "business_car"],
                          "description": "服务类型（it_repair=IT 报修 / it_access=账号权限 / "
                                         "it_device=设备领用 / parking=车位 / "
                                         "visitor_vehicle=访客车辆 / business_car=公务用车）"},
                 "title": {"type": "string", "description": "一句话事由或故障现象"},
                 "detail": {"type": "string", "description": "补充说明"},
                 "urgency": {"type": "string", "enum": ["一般", "紧急", "非常紧急"]},
                 "location": {"type": "string", "description": "设备与位置（IT 报修必填）"},
                 "extra": {"type": "object",
                           "description": "结构化补充：system/permission/device_type/plate/"
                                          "visit_date/use_from/dest 等"},
                 "user_ctx": _USER_SCHEMA}},
             handler=_h_office_submit_service_request(orchestrator), domain="office",
             readonly=False, tier=2),
        Tool(name="office.my_requests",
             description="查本人的 OA 申请汇总（报销/请假/补卡/服务工单单据 + 年假余额）",
             input_schema={"type": "object", "properties": {"user_ctx": _USER_SCHEMA}},
             handler=_h_office_my_requests(orchestrator), domain="office",
             readonly=True, tier=0),
    ]


# 各 server 的工具归属（每域 ≤ 15；不是按 Tool.domain 直接切，而是按暴露面归组）
DOMAIN_TOOLSETS: dict[str, list[str]] = {
    "data": ["platform.describe", "platform.now", "data.list_queries",
             "data.run_query", "data.metric", "data.sql_readonly", "metric.dict",
             "schedule.what_if"],
    "asset": ["asset.status", "inventory.atp", "equipment.predict", "supply.risk"],
    "quality": ["quality.trace", "quality.8d"],
    # SPC 域（V3.6 新增）：过程能力 / 柏拉图（只读）+ 测量值导入（写），纯统计内核 core/spc.py
    "spc": ["spc.capability", "spc.pareto", "spc.ingest"],
    "workflow": ["scenario.list", "scenario.run", "scenario.explain",
                 "workflow.pending", "workflow.act", "workflow.governance",
                 "workflow.create"],
    "knowledge": ["knowledge.search", "report.draft", "doc.extract", "spc.report"],
    # 办公助手域（V3.4 新增）：会议室预订 / 订餐，转发 OfficeBackend 契约
    "office": ["office.list_rooms", "office.find_available_rooms",
               "office.book_room", "office.cancel_booking", "office.my_bookings",
               "office.list_restaurants", "office.menu", "office.place_order",
               "office.cancel_order", "office.my_orders",
               # OA 自助办理（报销/请假/补考勤）与服务工单（IT 报修/账号权限/设备领用/车位/访客车辆/公务用车）
               "office.submit_expense", "office.apply_leave",
               "office.submit_attendance_fix", "office.submit_service_request",
               "office.my_requests"],
}


def build_domain_toolsets(orchestrator: V2Orchestrator) -> dict[str, list[Tool]]:
    """把**全部**工具按各 server 归组（键为 data/asset/quality/spc/workflow/knowledge/office）。

    每个 server ≤ 15 个工具；各域展平后应等于 ``build_platform_tools`` 的全集。
    """
    by_name = {t.name: t for t in build_platform_tools(orchestrator)}
    toolsets: dict[str, list[Tool]] = {}
    for domain, names in DOMAIN_TOOLSETS.items():
        missing = [n for n in names if n not in by_name]
        if missing:
            raise ValueError(f"域 {domain!r} 引用了未注册工具: {missing}")
        toolsets[domain] = [by_name[n] for n in names]
    return toolsets


def register_domain_toolsets(
        orchestrator: V2Orchestrator) -> dict[str, ToolRegistry]:
    """构造各按域拆分的 registry（多 server 分治的装配入口）。"""
    return {domain: ToolRegistry.toolset(tools)
            for domain, tools in build_domain_toolsets(orchestrator).items()}


def register_platform_tools(registry: ToolRegistry,
                            orchestrator: V2Orchestrator) -> ToolRegistry:
    """把平台工具注册进指定 registry（幂等入口；仍注册**全部**工具）。"""
    for tool in build_platform_tools(orchestrator):
        registry.register(tool)
    return registry
