"""质量域模块（S2 来料异常 / S3 客诉 8D）。

角色分层：SQE（来料判定/供应商）、QA（8D 牵头）、PQE（过程分析）。
降级铁律：无 LLM 时用规则/模板结论并标 ``status="degraded"``；
数字必须来自 SQL（provenance），不得编造；数据缺失标 ``data_gap``。
"""
from __future__ import annotations

import re
from typing import Any

__all__ = [
    "summarize_pqe",
    "summarize_sqe_supplier",
    "sqe_assessment_template",
    "make_qa_lead",
    "make_pqe_process",
    "make_sqe_supplier",
]

_BATCH_RE = re.compile(r"BATCH-[A-Za-z0-9\-]+")


def _extract_batch_no(task: Any, ctx: Any) -> str:
    """从问题/上下文抽取批号（BATCH-xxx）；规则级、无 LLM。"""
    m = _BATCH_RE.search(getattr(task, "query", "") or "")
    if m:
        return m.group(0)
    ctx_batch = ctx.get("batch_no")
    if ctx_batch:
        return str(ctx_batch)
    return str((getattr(task, "entities", {}) or {}).get("batch_no") or "")


# ---------- 摘要函数（供通用「注册查询型」工厂使用） ----------


def summarize_pqe(rows: list[dict], ctx: Any) -> str:
    """过程不良分布摘要（8D D4）。"""
    if not rows:
        return "该批次无不良明细（data_gap：数据库无该批号过程不良记录）"
    total = sum(float(r.get("total_qty", 0) or 0) for r in rows)
    top = max(rows, key=lambda r: float(r.get("total_qty", 0) or 0))
    return (f"过程不良分布：{len(rows)}类，主导 {top.get('defect_type')}"
            f"（{int(float(top.get('total_qty', 0) or 0))}pcs，合计{int(total)}pcs）")


def summarize_sqe_supplier(rows: list[dict], ctx: Any) -> str:
    """供应商侧摘要（8D 供应商分析）。"""
    if not rows:
        return "未查到该批次供应商信息（data_gap：material_batch 无该批号）"
    r = rows[0]
    res = "合格" if r.get("incoming_result") == "pass" else "不合格"
    return f"批次供应商：{r.get('supplier')}（来料检验：{res}）"


# ---------- S2：SQE 来料不合格判定（判定型模板） ----------


def sqe_assessment_template(rows: list[dict], ctx: Any, task: Any) -> dict[str, Any]:
    """SQE 不合格判定（规则模板；LLM 不可用时即结论，绝不编造）。

    :param rows: ``iqc_batches_recent`` 命中行（均为 incoming_result='fail'）
    """
    rejected = bool(rows)
    if rows:
        heavy = any(
            "锡膏" in str(r.get("part_name", ""))
            or str(r.get("part_no", "")).startswith("SP")
            for r in rows)
        grade = "A" if (heavy or len(rows) >= 2) else "B"
    else:
        grade = None
    suggestions = ["退货", "换货", "挑选"] if rejected else []
    if rejected and grade == "B":
        suggestions.insert(2, "特采（需三方会签）")
    if rows:
        first = rows[0]
        judgement = (
            f"{len(rows)} 个不合格批次：{first.get('batch_no')}"
            f"（{first.get('part_name')}，供应商 {first.get('supplier')}，"
            f"COA {first.get('coa_url')}）；处置建议：{'、'.join(suggestions)}")
    else:
        judgement = "未发现不合格来料批次"
    return {
        "iqc_judgement": judgement,
        "iqc_rejected": rejected,
        "defect_grade": grade,
        "disposition_suggestion": suggestions,
        "summary": judgement,
    }


# ---------- S3：QA 8D 牵头（两查询 + 模板合成 D1-D8） ----------


def make_qa_lead(t2s: Any):
    """QA 8D 牵头节点：客诉明细（D2）+ 批次不良（D4），合成 8D 骨架。

    说明（§8.4 U1）：MVP **不建 8D 表**，D1-D8 由规则模板确定性合成，
    由 ``capa_flow`` 工单 context 承载；生产需持久化 D1-D8 步骤（见验证报告）。
    """

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        batch_no = _extract_batch_no(task, ctx)
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": "查询未闭环客诉单（8D D2 问题界定）"})
        qr1 = t2s.query_registered("complaint_detail", {}, task.user_ctx)
        complaints = qr1.rows
        sink.emit({"type": "tool_end", "tool": "SQL查询",
                   "detail": f"返回{len(complaints)}条未闭环客诉"})

        qr2 = None
        defect_rows: list[dict] = []
        if batch_no:
            sink.emit({"type": "tool_start", "tool": "SQL查询",
                       "detail": f"查询批次{batch_no}不良分布（8D D4）"})
            qr2 = t2s.query_registered(
                "quality_defect_by_batch", {"batch_no": batch_no}, task.user_ctx)
            defect_rows = qr2.rows
            sink.emit({"type": "tool_end", "tool": "SQL查询",
                       "detail": f"返回{len(defect_rows)}类不良"})

        complaint_found = bool(complaints)
        c = complaints[0] if complaints else {}
        cust = c.get("customer") or "客户"
        model = c.get("product_model") or task.entities.get("product_model") or "—"
        batch = c.get("batch_no") or batch_no or "—"
        defect_desc = c.get("defect_desc") or "（无客诉明细描述）"
        dominant = (max(defect_rows, key=lambda r: float(r.get("total_qty", 0) or 0))
                    if defect_rows else None)
        d4_hyp = (
            f"过程不良以「{dominant['defect_type']}」为主"
            f"（{int(float(dominant.get('total_qty', 0) or 0))}pcs），疑似贴装/工艺偏移"
            if dominant else
            "过程不良明细缺失（data_gap），根因待 PQE 现场确认")
        d8 = {
            "D1_team": "质量牵头（QA）+ PQE + SQE + 工程 + 业务（客户接口）",
            "D2_problem": f"{cust} 反馈：{defect_desc}（型号 {model}，批次 {batch}）",
            "D3_containment": "在库/在途该批次冻结，已发货批次通知客户加严检验",
            "D4_root_cause": d4_hyp,
            "D5_permanent": "工艺参数复核 + 首件全检 + 供应商来料加严（如需）",
            "D6_validation": "连续 3 批 CPK 达标且无同类不良后解除围堵",
            "D7_prevent": "更新 SOP/FMEA，纳入过程监控与供应商评级",
            "D8_closure": "待质量总监/QA 经理 HITL 确认关闭",
        }
        summary = (f"客诉 {len(complaints)} 单（未闭环）；8D 骨架已合成，D2={cust}"
                   if complaints else "未查到未闭环客诉（data_gap）")
        provenance = [qr1.to_dict()] + ([qr2.to_dict()] if qr2 else [])
        return {"domain": "quality", "status": "degraded", "payload": {
            "complaint_found": complaint_found,
            "d8_report": d8,
            "batch_no": batch_no or None,
            "complaint_summary": summary,
            "qa_lead_degraded": True,
            "provenance": provenance,
        }, "summary": summary}
    return handler


def make_pqe_process(t2s: Any):
    """PQE 过程分析节点（按批号查不良分布；空则标 data_gap）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        batch_no = _extract_batch_no(task, ctx)
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"查询批次{batch_no or '（未知）'}过程不良分布"})
        qr = t2s.query_registered(
            "quality_defect_by_batch", {"batch_no": batch_no}, task.user_ctx) \
            if batch_no else None
        rows = qr.rows if qr else []
        if qr:
            sink.emit({"type": "tool_end", "tool": "SQL查询",
                       "detail": f"返回{len(rows)}类不良"})
        summary = summarize_pqe(rows, ctx)
        payload: dict[str, Any] = {
            "process_analysis": rows,
            "quality_summary": summary,
            "provenance": [qr.to_dict()] if qr else [],
        }
        if not rows:
            payload["data_gap"] = [f"过程分析：批号{batch_no or '未知'}无不良明细"]
        status = "degraded" if (qr is None or qr.fallback or not rows) else "ok"
        return {"domain": "quality", "status": status,
                "payload": payload, "summary": summary}
    return handler


def make_sqe_supplier(t2s: Any):
    """SQE 供应商侧节点（按批号查供应商；空则标 data_gap）。"""

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        batch_no = _extract_batch_no(task, ctx)
        sink.emit({"type": "tool_start", "tool": "SQL查询",
                   "detail": f"按批号 {batch_no or '（未知）'} 查供应商"})
        qr = t2s.query_registered(
            "batch_supplier_by_no", {"batch_no": batch_no}, task.user_ctx) \
            if batch_no else None
        rows = qr.rows if qr else []
        if qr:
            sink.emit({"type": "tool_end", "tool": "SQL查询",
                       "detail": f"返回{len(rows)}行"})
        summary = summarize_sqe_supplier(rows, ctx)
        payload: dict[str, Any] = {
            "supplier_analysis": rows,
            "supplier_summary": summary,
            "provenance": [qr.to_dict()] if qr else [],
        }
        if not rows:
            payload["data_gap"] = [f"供应商侧：批号{batch_no or '未知'}无来料检验记录"]
        status = "degraded" if (qr is None or qr.fallback or not rows) else "ok"
        return {"domain": "quality", "status": status,
                "payload": payload, "summary": summary}
    return handler
