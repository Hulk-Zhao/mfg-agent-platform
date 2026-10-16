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

from ...tools.llm_factory import get_llm, llm_available
from .shared_context import SharedContext

logger = logging.getLogger(__name__)

# V1 对客脱敏禁词表（report.py 机制保留为兜底，主通道为字段级脱敏）
FORBIDDEN_WORDS: dict[str, str] = {
    "设备故障": "产线例行校准",
    "供应商来料不良": "批次质量复核",
    "品质不良超标": "工艺参数优化",
    "停线": "计划内暂停",
    "根因": "主因分析",
    "维修": "维护校准",
}


def build_handlers(runtime: Any) -> dict[str, Any]:
    """按运行时依赖装配全部节点 handler（场景 YAML 按 key 引用）。"""
    t2s = runtime.text2sql
    engine = runtime.workflow
    notifier = runtime.notifier
    handlers: dict[str, Any] = {}

    # ---------- 业务意图（复用 V1 business_agent） ----------

    def business(task: Any, ctx: SharedContext, sink: Any) -> dict:
        from ...agents.business import business_agent  # V1 资产复用
        state = business_agent({"user_query": task.query})
        summary = (f"识别场景：{state.get('scenario')} | "
                   f"型号：{state.get('product_model')} | "
                   f"线体：{state.get('line_code')}")
        sink.emit({"type": "tool_start", "tool": "意图识别",
                   "detail": "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）"})
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
            from ...tools.rag_tools import search_cases  # V1 RAG（本地 Chroma）
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
            prompt = (
                "你是制造业Failure Analysis专家。基于以下信息判定异常等级并预估复线时间。\n\n"
                f"【当前异常】\n- 线体：{line}\n"
                f"- 主导不良：{defect_type or '未知'}\n"
                f"- 设备异常：{[a.get('param_name', '') + '=' + a.get('param_value', '') for a in alarms]}\n\n"
                f"【历史相似案例】\n{case_text}\n\n"
                '请输出（JSON格式）：{"rating":"A/B/C/D","root_cause":"一句话",'
                '"eta_hours":2,"verification":["..."]}\n只输出JSON。')
            import json
            import re
            try:
                llm = get_llm(temperature=0.1, tier="L2")
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
            fa_summary += "（degraded：LLM 不可用，模板化结论）"
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

    def _make_hitl_handler(kind: str) -> Any:
        def handler(task: Any, ctx: SharedContext, sink: Any) -> dict:
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
                         "kind": kind, "actions": actions, "card": card,
                         "eta_hours": ctx.get("eta_hours")},
                assignees=approvers, priority="P1", hitl=True, hitl_prompt=prompt)
            notifier.broadcast_hitl({**card, "hitl_prompt": prompt,
                                     "approver_roles": approvers}, approvers)
            summary = f"{prompt} → waiting_hitl（审批人：{'、'.join(approvers)}）"
            return {"domain": "hitl", "status": "ok", "payload": {
                "step": task.step_id, "kind": kind, "prompt": prompt,
                "status": "waiting_hitl", "approver_roles": approvers,
                "actions": actions, "task_uid": wf_task.task_uid,
                "card": card,
                "provenance": [{"channel": "hitl", "task_uid": wf_task.task_uid,
                                "approver_roles": approvers}],
            }, "summary": summary}
        return handler

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
            prompt = (
                "你是制造业业务接口人。基于以下内部数据，生成给客户的正式回复（200字以内）。\n\n"
                f"【内部数据】\n{brief}\n\n"
                "【规则】\n1. 禁止出现：设备故障、供应商来料不良、品质不良超标、停线、根因、维修\n"
                "2. 统一替换为：产线例行校准、批次质量复核、工艺参数优化、计划内暂停\n"
                "3. 必须包含：当前状态、预计恢复时间（基于ETA换算）、补救措施、发货影响\n"
                "4. 语气专业诚恳，不推诿\n5. 只输出报告正文，不要标题")
            try:
                llm = get_llm(temperature=0.3, tier="L2")
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
    handlers["hitl_restart"] = _make_hitl_handler("restart")
    handlers["hitl_eta"] = _make_hitl_handler("eta")
    handlers["report"] = report
    return handlers


# ---------- 模板化降级实现 ----------


def _fa_template(defect: dict, alarms: list) -> tuple[str, int, str, list[str]]:
    """LLM 不可用时的 FA 模板化结论（规则推导，非编造；标记 degraded）。"""
    qty = float(defect.get("total_qty", 0) or 0)
    if qty >= 100:
        rating = "A"
    elif qty >= 40:
        rating = "B"
    elif qty >= 10:
        rating = "C"
    else:
        rating = "D"
    defect_type = defect.get("defect_type", "未知不良")
    if alarms:
        first = alarms[0]
        root = (f"设备参数异常（{first.get('param_name', '')}="
                f"{first.get('param_value', '')}）与{defect_type}不良相关，疑似工艺偏差")
    else:
        root = f"主导不良为{defect_type}，疑似来料/工艺因素，待 PQE 现场确认"
    eta = 2
    verif = ["复测设备参数至基准范围", "加严 IPQC 检验频次", "确认来料批次 COA"]
    return rating, eta, root, verif


def _apply_forbidden_words(text: str) -> str:
    """V1 Prompt 禁词表兜底（主通道为字段级脱敏，这里是双保险）。"""
    for word, replacement in FORBIDDEN_WORDS.items():
        text = text.replace(word, replacement)
    return text


def _customer_report_template(brief: dict, ctx: SharedContext) -> str:
    """degraded 模式对客报告模板（结构化、无编造数字）。"""
    if not brief.get("停线确认"):
        return ("尊敬的客户：经核查，当前产线运行正常，无停线情况，"
                "订单交付按原计划推进，感谢关注。")
    eta = ctx.get("eta_hours", 2)
    return (f"尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核"
            f"与工艺参数优化。根据当前评估，预计约 {eta} 小时恢复，"
            f"届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。")


__all__ = ["build_handlers", "FORBIDDEN_WORDS"]
