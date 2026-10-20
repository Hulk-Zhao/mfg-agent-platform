"""域 Agent 节点注册表：场景 YAML 的 agent key → handler 实现。

handler 契约（见 yaml_dag.NodeTask）：
    入参：NodeTask（step/实体/问题/用户上下文）、SharedContext、EventSink
    返回：{"domain", "status", "payload", "summary"}

设计原则：
- 复用 V1 资产（business_agent 意图抽取、rag_tools RAG、sql_tools 的
  SCHEMA_PROMPT/_extract_sql、fa/report 的 Prompt 禁词表），V1 零破坏；
- 所有数据查询经 Text2SQLService（注册查询通道），真实 provenance 入 SharedContext；
- 确定性流程（工单/通知）走 WorkflowEngine + NotificationService；
- HITL 节点只创建 waiting_hitl 工单与卡片，绝不自动放行。
"""
from __future__ import annotations

import logging
from typing import Any

from ...common.llm_factory import get_llm, llm_available
from .shared_context import SharedContext
# 提示词集中化（§6.3）：字符串逐字节复制，node_registry 对外符号保持不变
from ..prompts.fa import build_fa_prompt
from ..prompts.report import (
    FORBIDDEN_WORDS,           # 再导出：对外可见符号不变（demo 资产）
    _apply_forbidden_words,
    _customer_report_template,
    _fa_template,
    build_report_prompt,
)
# 新场景业务域模块（§5.1 脚手架对齐：各域独立文件，node_registry 变薄装配层）
from ..agents import engineering as eng_agents
from ..agents import manufacturing as mf_agents
from ..agents import quality as q_agents
from ..agents import report as rep_agents
from ..agents import supply_chain as sc_agents
from ..agents.hitl import _flow_snapshot, make_hitl_handler
from ..agents.workflow_nodes import make_workflow_handler

logger = logging.getLogger(__name__)


def _build_params(entities: Any, param_map: Any) -> dict:
    """按 ``param_map``（query 参数名 → 实体键）从 entities 构造注册查询参数。"""
    params: dict = {}
    for query_key, entity_key in (param_map or {}).items():
        value = (entities or {}).get(entity_key)
        if value is not None:
            params[query_key] = value
    return params


def _make_registered_query_agent(t2s: Any, domain: str, query_id: str, *,
                                 param_map: Any = None, summary_fn: Any = None,
                                 payload_keys: Any = None,
                                 summary_key: Any = None) -> Any:
    """通用「注册查询型域 Agent」工厂（§3.2）。

    按 ``param_map`` 调 ``t2s.query_registered``；``summary_fn(rows, ctx)``
    生成摘要；把命中行写入 ``payload_keys``，摘要写入 ``summary_key``，并
    附带 ``provenance``（QueryResult.to_dict()）。
    """

    def handler(task: Any, ctx: SharedContext, sink: Any) -> dict:
        params = _build_params(task.entities, param_map)
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询 {query_id}（注册查询）"})
        qr = t2s.query_registered(query_id, params, task.user_ctx)
        rows = qr.rows
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(rows)}行"
                             f"（{'脱敏' if qr.masked else '原文'}）"})
        summary = (summary_fn(rows, ctx) if summary_fn
                   else f"{query_id}：命中 {len(rows)} 行")
        payload: dict[str, Any] = {}
        for key in (payload_keys or []):
            payload[key] = rows
        payload[summary_key or f"{domain}_summary"] = summary
        payload["provenance"] = [qr.to_dict()]
        return {"domain": domain, "status": "degraded" if qr.fallback else "ok",
                "payload": payload, "summary": summary}
    return handler


def _make_assessment_agent(t2s: Any, domain: str, query_id: str, *,
                           template_fn: Any = None, param_map: Any = None,
                           summary_key: Any = None) -> Any:
    """通用「判定型域 Agent」工厂（§3.2）。

    查询 + ``template_fn(rows, ctx, task)``（degrade 规则结论）产出结构化判定；
    LLM 不可用或查询被拦截时标 ``status="degraded"`` 且 payload 带 ``*_degraded``。
    """

    def handler(task: Any, ctx: SharedContext, sink: Any) -> dict:
        params = _build_params(task.entities, param_map)
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询 {query_id}（判定）"})
        qr = t2s.query_registered(query_id, params, task.user_ctx)
        out = dict(template_fn(qr.rows, ctx, task) if template_fn else {})
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(qr.rows)}行"})
        summary = str(out.get("summary") or f"{query_id}：判定完成")
        payload = dict(out)
        payload["provenance"] = [qr.to_dict()]
        payload[summary_key or f"{domain}_summary"] = summary
        degraded = (not llm_available()) or bool(qr.fallback)
        payload[f"{domain}_degraded"] = degraded
        return {"domain": domain, "status": "degraded" if degraded else "ok",
                "payload": payload, "summary": summary}
    return handler


def build_handlers(runtime: Any) -> dict[str, Any]:
    """按运行时依赖装配全部节点 handler（场景 YAML 按 key 引用）。"""
    t2s = runtime.text2sql
    engine = runtime.workflow
    notifier = runtime.notifier
    handlers: dict[str, Any] = {}

    # ---------- 业务意图（复用 V1 business_agent） ----------

    def business(task: Any, ctx: SharedContext, sink: Any) -> dict:
        from ...common.business import business_agent  # V1 资产复用
        state = business_agent({"user_query": task.query})
        summary = (f"识别场景：{state.get('scenario')} | "
                   f"型号：{state.get('product_model')} | "
                   f"线体：{state.get('line_code')}")
        sink.emit({"type": "tool_start", "tool": "意图识别",
                   "detail": "问题匹配（规则优先 / 语义补充 / 关键词兜底）"})
        sink.emit({"type": "tool_end", "tool": "意图识别", "detail": summary})
        return {"domain": "orchestration", "status": "ok",
                "payload": dict(state), "summary": summary}

    # ---------- 制造域：确认停线（等价 V1 ManufacturingAgent） ----------

    def manufacturing(task: Any, ctx: SharedContext, sink: Any) -> dict:
        line = task.entities.get("line_code") or "SMT-1"
        model = task.entities.get("product_model") or "A型号"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询{line}线{model}今日OEE与产出 + 进行中停线事件"})
        oee = t2s.query_registered("mfg_oee", {"line": line, "model": model},
                                   task.user_ctx)
        stop = t2s.query_registered("mfg_stop_ongoing",
                                    {"line": line, "model": model}, task.user_ctx)
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(oee.rows)}条产出，{len(stop.rows)}条停线事件"
                             f"（{'脱敏' if oee.masked or stop.masked else '原文'}）"})
        rows, stop_rows = oee.rows, stop.rows
        try:
            oee_low = bool(rows and float(rows[-1].get("oee") or 100) < 50)
        except (TypeError, ValueError):
            oee_low = False
        confirmed = oee_low or bool(stop_rows)
        summary = (f"{line}线{model}今日OEE={rows[-1].get('oee')}%"
                   if rows else "无产出数据")
        status = "degraded" if (oee.fallback or stop.fallback) else "ok"
        return {"domain": "manufacturing", "status": status, "payload": {
            "oee_data": {"rows": rows},
            "stop_info": stop_rows[0] if stop_rows else None,
            "confirmed_stop": confirmed,
            "production_summary": summary,
            "provenance": [oee.to_dict(), stop.to_dict()],
        }, "summary": summary}

    # ---------- 品质域：不良分布（等价 V1 QualityAgent） ----------

    def quality(task: Any, ctx: SharedContext, sink: Any) -> dict:
        line = task.entities.get("line_code") or "SMT-1"
        model = task.entities.get("product_model") or "A型号"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询{line}线{model}不良分布"})
        qr = t2s.query_registered("quality_defect_dist",
                                  {"line": line, "model": model}, task.user_ctx)
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(qr.rows)}种不良"})
        rows = qr.rows
        dominant = max(rows, key=lambda r: float(r.get("total_qty", 0))) if rows else None
        qty = float(dominant["total_qty"]) if dominant else 0
        summary = (f"主导不良：{dominant['defect_type']}，共{int(qty)}pcs"
                   if dominant else "无集中不良")
        return {"domain": "quality", "status": "ok", "payload": {
            "quality_data": {"rows": rows}, "dominant_defect": dominant,
            "quality_summary": summary, "provenance": [qr.to_dict()],
        }, "summary": summary}

    # ---------- 供应链域：来料批次（等价 V1 SupplyChainAgent） ----------

    def supply_chain(task: Any, ctx: SharedContext, sink: Any) -> dict:
        sink.emit({"type": "tool_start", "tool": "SQL查询", "detail": "查询今日到货批次"})
        qr = t2s.query_registered("supply_batches_today", {}, task.user_ctx)
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(qr.rows)}条批次"
                             f"（敏感字段已按角色脱敏: {qr.masked}）"})
        rows = qr.rows
        failed = [r for r in rows if r.get("incoming_result") == "fail"]
        summary = f"今日{len(rows)}个批次到货，{'有' if failed else '无'}来料异常"
        return {"domain": "supply_chain", "status": "ok", "payload": {
            "supply_data": {"rows": rows}, "abnormal_material": failed or None,
            "supply_summary": summary, "provenance": [qr.to_dict()],
        }, "summary": summary}

    # ---------- 设备域：异常告警（等价 V1 EquipmentAgent） ----------

    def equipment(task: Any, ctx: SharedContext, sink: Any) -> dict:
        line = task.entities.get("line_code") or "SMT-1"
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询{line}线设备异常"})
        qr = t2s.query_registered("equip_alarms", {"line": line}, task.user_ctx)
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(qr.rows)}条异常"})
        rows = qr.rows
        abnormal_params = [f"{r.get('equipment_id', '设备')} "
                           f"{r.get('param_name', '')}={r.get('param_value', '')}"
                           for r in rows]
        summary = (f"近2小时{len(rows)}条设备异常：" + "；".join(abnormal_params)
                   if rows else "无设备异常")
        return {"domain": "equipment", "status": "ok", "payload": {
            "equipment_data": {"rows": rows}, "abnormal_alarms": rows,
            "equipment_summary": summary, "provenance": [qr.to_dict()],
        }, "summary": summary}

    # ---------- FA 分析：RAG + LLM（不可用时模板化降级并标记） ----------

    def fa(task: Any, ctx: SharedContext, sink: Any) -> dict:
        defect = ctx.get("dominant_defect") or {}
        alarms = ctx.get("abnormal_alarms") or []
        line = task.entities.get("line_code", "SMT-1")
        defect_type = defect.get("defect_type", "")
        query = (f"{line}线 {defect_type} "
                 f"设备告警={','.join(a.get('alarm_code', '') for a in alarms[:3])}")
        sink.emit({"type": "tool_start", "tool": "RAG检索", "detail": f"查询: {query}"})
        try:
            from ...common.rag_tools import search_cases  # V1 RAG（本地 Chroma）
            cases = search_cases(query, n_results=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG 检索失败，按无案例处理: %s", exc)
            cases = []
        sink.emit({"type": "tool_end", "tool": "RAG检索",
                   "detail": f"匹配{len(cases)}条历史案例"})

        degraded = not llm_available()
        if not degraded:
            sink.emit({"type": "llm_start",
                       "detail": "FA模型推理中（评级/根因/复线时间）..."})
            case_text = "\n".join(
                f"- {c['document']} 评级={c['metadata'].get('rating', '?')} "
                f"复线={c['metadata'].get('recovery_hours', '?')}h"
                for c in cases)
            prompt = build_fa_prompt(line, defect_type, alarms, case_text)
            import json
            import re
            try:
                # 思考档位动态分级：有相似案例 → 证据充分，低思考足够；
                # 无案例 → 开放推理（评级/根因全靠模型权衡），升中思考
                llm = get_llm(temperature=0.1, tier="L2", max_tokens=900, timeout=45,
                              thinking="low" if cases else "medium")
                resp = llm.invoke(prompt)
                content = resp.content if hasattr(resp, "content") else str(resp)
                match = re.search(r"\{.*\}", content, re.S)
                data = json.loads(match.group(0)) if match else {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("FA LLM 推理失败，转模板降级: %s", exc)
                degraded = True
                data = {}
            rating = data.get("rating", "C")
            eta = data.get("eta_hours", 2)
            root = data.get("root_cause", "待分析")
            verif = data.get("verification", [])
        if degraded:
            rating, eta, root, verif = _fa_template(defect, alarms)
        fa_summary = f"评级{rating}，根因：{root}，预计{eta}h复线"
        if degraded:
            fa_summary += "（模板化结论：分析服务未就绪）"
        return {"domain": "fa", "status": "ok", "payload": {
            "fa_cases": cases, "fa_rating": rating, "root_cause": root,
            "eta_hours": eta, "verification_items": verif,
            "fa_summary": fa_summary, "fa_degraded": degraded,
            "provenance": [{"channel": "rag", "query": query,
                            "case_count": len(cases)}],
        }, "summary": fa_summary}

    # ---------- 工作流节点：影响评估工单（确定性流程） ----------

    def impact_assessment(task: Any, ctx: SharedContext, sink: Any) -> dict:
        step = task.params
        assignees = [str(a) for a in step.get("assignees", ["计划", "物控", "业务"])]
        level = str(step.get("notify_level", "P1"))
        sla_hours = float(step.get("sla_hours", 2))
        title = str(step.get("title", "停线影响评估工单"))
        context = {
            "run_id": ctx.get("run_id"),
            "line_code": task.entities.get("line_code"),
            "product_model": task.entities.get("product_model"),
            "confirmed_stop": ctx.get("confirmed_stop"),
            "fa_rating": ctx.get("fa_rating"),
            "eta_hours": ctx.get("eta_hours"),
            "root_cause": ctx.get("root_cause"),
            # 流程留痕：上游已执行节点轨迹 + 本工作流卡点（审批摘要卡渲染进展/耗时）
            "flow": _flow_snapshot(ctx, task.step_id, title, kind="workflow"),
        }
        wf_task = engine.create_task(
            wf_id="impact_assessment", title=title, context=context,
            assignees=assignees, priority=level, sla_hours=sla_hours)
        wf_task = engine.advance(wf_task.task_uid, "start",
                                 actor="orchestrator", comment="影响评估启动")
        body = (f"{title}\n线体: {context['line_code']} | 型号: {context['product_model']}\n"
                f"FA评级: {context['fa_rating']} | 预估复线: {context['eta_hours']}h\n"
                f"请评估排产/物料/对客影响（SLA {sla_hours:.0f}h，超时自动升级）")
        notice = notifier.send(level, assignees, f"停线影响评估（{context['line_code']}）", body)
        summary = (f"工单 {wf_task.task_uid} 已创建（{level} 通知 "
                   f"{','.join(assignees)}，SLA {sla_hours:.0f}h）")
        return {"domain": "workflow", "status": "ok", "payload": {
            "ticket": wf_task.to_dict(), "notified": assignees,
            "notice": notice,
            "provenance": [{"channel": "workflow", "task_uid": wf_task.task_uid,
                            "wf_id": "impact_assessment", "assignees": assignees}],
        }, "summary": summary}

    # ---------- HITL 节点：复线决策 / 对客 ETA（waiting_hitl，不自动放行） ----------
    # 说明（§5.1）：HITL 工厂已迁移至 agents/hitl.py（make_hitl_handler），
    # restart/eta 行为逐字节等价；新 kinds 复用同一工厂（装配见下方注册段）。

    # ---------- 报告节点：内部简报 + 对客脱敏报告 ----------

    def report(task: Any, ctx: SharedContext, sink: Any) -> dict:
        brief = {
            "停线确认": ctx.get("confirmed_stop"),
            "生产": ctx.get("production_summary"),
            "品质": ctx.get("quality_summary"),
            "供应链": ctx.get("supply_summary"),
            "设备": ctx.get("equipment_summary"),
            "FA评级": ctx.get("fa_summary"),
            "仓储": ctx.get("warehouse_summary", ""),
        }
        degraded = not llm_available()
        if not degraded:
            sink.emit({"type": "llm_start", "detail": "生成对客脱敏报告..."})
            prompt = build_report_prompt(brief)
            try:
                llm = get_llm(temperature=0.3, tier="L2", max_tokens=700, timeout=45,
                              thinking="disabled")
                resp = llm.invoke(prompt)
                customer_report = (resp.content if hasattr(resp, "content")
                                   else str(resp)).strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning("报告 LLM 生成失败，转模板降级: %s", exc)
                degraded = True
                customer_report = ""
            if not degraded:
                customer_report = _apply_forbidden_words(customer_report)
        if degraded:
            customer_report = _customer_report_template(brief, ctx)
        summary = ("对客报告已生成（degraded 模板化 + 禁词表兜底）"
                   if degraded else "对客报告已生成")
        return {"domain": "report", "status": "ok", "payload": {
            "internal_brief": brief, "customer_report": customer_report,
            "report_degraded": degraded,
            "provenance": [{"channel": "report", "degraded": degraded,
                            "masked_words": list(FORBIDDEN_WORDS)}],
        }, "summary": summary}

    handlers["business"] = business
    handlers["manufacturing"] = manufacturing
    handlers["quality"] = quality
    handlers["supply_chain"] = supply_chain
    handlers["equipment"] = equipment
    handlers["fa"] = fa
    handlers["impact_assessment"] = impact_assessment
    handlers["report"] = report

    # ============================================================
    # V2 增量（4 场景）：新 handler（§3.3）——现有 10 个 handler 不动
    # ============================================================

    def llm_ok() -> bool:
        """可用性探针（闭包捕获模块级 llm_available，测试 monkeypatch 生效）。"""
        return llm_available()

    # 设备取证复用现有 handler；wrapper 补条件键 has_alarm（不改冻结 handler）
    def equipment_warn(task: Any, ctx: SharedContext, sink: Any) -> dict:
        out = equipment(task, ctx, sink)
        payload = dict(out.get("payload") or {})
        payload["has_alarm"] = bool(payload.get("abnormal_alarms"))
        return {**out, "payload": payload}

    handlers["equipment_warn"] = equipment_warn

    # HITL 工厂：restart/eta 等价迁移（line_stop 回归基线）+ 新 kinds
    handlers["hitl_restart"] = make_hitl_handler(engine, notifier, "restart")
    handlers["hitl_eta"] = make_hitl_handler(engine, notifier, "eta")
    handlers["hitl_disposition"] = make_hitl_handler(engine, notifier, "disposition")
    handlers["hitl_d8_close"] = make_hitl_handler(engine, notifier, "d8_close")
    handlers["hitl_complaint_reply"] = make_hitl_handler(
        engine, notifier, "complaint_reply")
    handlers["hitl_delivery_commit"] = make_hitl_handler(
        engine, notifier, "delivery_commit")
    handlers["hitl_maintenance_window"] = make_hitl_handler(
        engine, notifier, "maintenance_window")
    # S6 SPC 过程能力异常预警：过程异常处置决策（加严检验/工程介入/计划内暂停/维持监控）
    handlers["hitl_spc_action"] = make_hitl_handler(engine, notifier, "spc_action")

    # 工作流工厂（同一实现按 step.params 装配；key = YAML step 的 workflow id）
    wf_handler = make_workflow_handler(engine, notifier)
    for key in ("scar_flow", "complaint_8d_flow", "delivery_risk_flow",
                "maintenance_flow", "order_change_flow"):
        handlers[key] = wf_handler

    # 现场分派：客诉归因到责任工段 → 推送该工段现场看板（确定性，无 LLM）
    from ..services import site_board  # 局部导入：避免 services ↔ core 的模块级环
    handlers["site_dispatch"] = site_board.make_site_dispatch(notifier, t2s)

    # 注册查询型域 Agent
    handlers["procurement"] = _make_registered_query_agent(
        t2s, "supply_chain", "supplier_followup",
        summary_fn=sc_agents.summarize_procurement,
        payload_keys=["supplier_actions"], summary_key="procurement_summary")
    handlers["planning"] = _make_registered_query_agent(
        t2s, "supply_chain", "plan_impact_by_material",
        param_map={"model": "product_model"},
        summary_fn=sc_agents.summarize_planning,
        payload_keys=["affected_plans"], summary_key="planning_summary")
    handlers["business_order"] = _make_registered_query_agent(
        t2s, "supply_chain", "orders_by_model",
        param_map={"model": "product_model"},
        summary_fn=sc_agents.summarize_business_order,
        payload_keys=["orders"], summary_key="business_summary")
    handlers["manufacturing_output"] = _make_registered_query_agent(
        t2s, "manufacturing", "output_by_model",
        param_map={"model": "product_model"},
        summary_fn=sc_agents.summarize_output,
        payload_keys=["output_data"], summary_key="production_summary")
    handlers["logistics"] = _make_registered_query_agent(
        t2s, "supply_chain", "shipment_status",
        param_map={"model": "product_model"},
        summary_fn=sc_agents.summarize_logistics,
        payload_keys=["in_transit"], summary_key="logistics_summary")
    handlers["business_sales"] = _make_registered_query_agent(
        t2s, "supply_chain", "shipment_status",
        param_map={"model": "product_model"},
        summary_fn=sc_agents.summarize_business_sales,
        payload_keys=["customer_comm"], summary_key="business_summary")

    # 判定型域 Agent
    handlers["sqe_assess"] = _make_assessment_agent(
        t2s, "quality", "iqc_batches_recent",
        template_fn=q_agents.sqe_assessment_template,
        summary_key="quality_summary")

    # 专用域节点
    handlers["qa_lead"] = q_agents.make_qa_lead(t2s)
    handlers["pqe_process"] = q_agents.make_pqe_process(t2s)
    handlers["sqe_supplier"] = q_agents.make_sqe_supplier(t2s)
    handlers["material_control"] = sc_agents.make_material_control(t2s)
    handlers["warehouse_atp"] = sc_agents.make_warehouse_atp(t2s)
    handlers["atp_analysis"] = sc_agents.make_atp_analysis()
    handlers["planning_window"] = sc_agents.make_planning_window(t2s)
    handlers["maintenance"] = mf_agents.make_maintenance(t2s)
    handlers["engineering"] = eng_agents.make_template_agent(
        "rd_engineering", eng_agents.design_root_cause_template,
        summary_key="engineering_summary")
    handlers["engineering_param"] = eng_agents.make_template_agent(
        "rd_engineering", eng_agents.param_advice_template,
        summary_key="engineering_summary")
    handlers["spare_parts"] = eng_agents.make_template_agent(
        "supply_chain", eng_agents.spare_parts_template,
        summary_key="spare_summary")

    # S6 SPC 过程能力异常预警：能力判定 + 不良柏拉图（口径来自 core.spc）
    handlers["spc_capability"] = q_agents.make_spc_capability(t2s)
    handlers["spc_pareto"] = q_agents.make_spc_pareto(t2s)

    # 报告工厂（按场景聚合简报；S3/S4 另出对客脱敏报告）
    handlers["report_incoming"] = rep_agents.make_report_handler(
        "incoming_quality_v2", llm_ok)
    handlers["report_complaint"] = rep_agents.make_report_handler(
        "customer_complaint_v2", llm_ok)
    handlers["report_delivery"] = rep_agents.make_report_handler(
        "delivery_status_v2", llm_ok)
    handlers["report_equipment"] = rep_agents.make_report_handler(
        "equipment_warning_v2", llm_ok)
    handlers["report_order_change"] = rep_agents.make_report_handler(
        "order_change_impact_v2", llm_ok)
    handlers["report_spc"] = rep_agents.make_report_handler(
        "spc_anomaly_v2", llm_ok)

    return handlers


# ---------- 模板化降级实现 ----------
# 说明（§6.3）：_fa_template / _apply_forbidden_words / _customer_report_template
# 已迁移至 prompts/report.py（字符串逐字节复制），此处由顶部 import 引入，
# 保持 node_registry 同名符号可导入（对外可见符号不变）。


__all__ = ["build_handlers", "FORBIDDEN_WORDS"]
