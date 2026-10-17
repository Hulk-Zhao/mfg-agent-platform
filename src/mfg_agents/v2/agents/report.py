"""通用报告 handler 工厂（按场景聚合内部简报；S3/S4 另出对客脱敏报告）。

- 内部简报：按场景从 ``ctx.snapshot()`` 聚合关键结论（带 provenance）；
- 对客报告（仅 S3/S4）：LLM 可用时生成 + 禁词表兜底；不可用时模板化（degraded）；
- ``data_gap``：汇总各域写入的数据缺口，进 payload/provenance（前端可见）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..prompts.report import FORBIDDEN_WORDS, _apply_forbidden_words

logger = logging.getLogger(__name__)

__all__ = ["make_report_handler"]

# 场景 → 内部简报字段（标签, SharedContext 键）
_BRIEF_SPEC: dict[str, list[tuple[str, str]]] = {
    "incoming_quality_v2": [
        ("来料不合格判定", "iqc_judgement"),
        ("缺陷等级", "defect_grade"),
        ("处置建议", "disposition_suggestion"),
        ("采购跟催", "procurement_summary"),
        ("排产影响", "planning_summary"),
        ("物料评估", "material_summary"),
    ],
    "customer_complaint_v2": [
        ("客诉摘要", "complaint_summary"),
        ("8D报告", "d8_report"),
        ("过程分析", "quality_summary"),
        ("供应商侧", "supplier_summary"),
        ("设计根因", "design_root_cause"),
        ("对客口径", "business_summary"),
    ],
    "delivery_status_v2": [
        ("ATP承诺分析", "atp_summary"),
        ("风险订单", "risk_list"),
        ("订单", "business_summary"),
        ("排产", "planning_summary"),
        ("库存评估", "material_summary"),
        ("产出", "production_summary"),
        ("物流在途", "logistics_summary"),
    ],
    "equipment_warning_v2": [
        ("设备告警", "equipment_summary"),
        ("维保建议", "maintenance_summary"),
        ("换产窗口", "planning_summary"),
        ("参数建议", "engineering_summary"),
        ("备件需求", "spare_summary"),
    ],
    "order_change_impact_v2": [
        ("排产影响", "planning_summary"),
        ("物料齐套", "material_summary"),
        ("制造产能", "production_summary"),
    ],
    "spc_anomaly_v2": [
        ("过程能力", "spc_capability_summary"),
        ("不良柏拉图", "spc_pareto_summary"),
    ],
}

# 场景 → 对客报告模板（字段级脱敏之外的禁词兜底）
_CUST_TEMPLATE: dict[str, Callable[[Any], str]] = {
    "customer_complaint_v2": lambda ctx: (
        "尊敬的客户：关于贵司反馈的批次质量问题，我们已立案并启动 8D 专项整改，"
        "对在库/在途批次实施围堵与复检。当前主因分析进行中，整改措施与关闭时间"
        "将以正式 8D 报告同步。感谢理解与支持。"),
    "delivery_status_v2": lambda ctx: (
        "尊敬的客户：经核实，贵司订单当前受产能与物料因素影响，存在交付风险。"
        "我方已启动跨部门协同（计划/物控/物流），并在确认后第一时间同步可承诺交期。"
        "敬请谅解，感谢支持。"),
}


def fmt_text(value: Any) -> str:
    """结构化 dict → 可读文本（用于模板/LLM 上下文；前端结构化渲染用 dict 本体）。"""
    if isinstance(value, dict):
        return "；".join(f"{k}={fmt_text(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "；".join(fmt_text(v) for v in value) if value else "—"
    return _fmt(value) if not isinstance(value, (dict, list, tuple)) else str(value)


def _fmt(value: Any) -> str:
    """把任意值转成简报可读文本（不编造，仅格式化真实结论）。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "；".join(_fmt(v) for v in value) if value else "—"
    if isinstance(value, dict):
        return "；".join(f"{k}={_fmt(v)}" for k, v in value.items())
    return str(value)


def _collect_gaps(ctx: Any) -> list[str]:
    """汇总所有域写入的 ``data_gap``（只增历史；**按 note 去重保序**）。

    注意（H-1）：来源域归因由**各域 handler 自身**写入其 payload provenance
    （domain=真实来源域）；报告节点只做聚合展示，**不再二次写入 provenance**，
    避免同一条缺口被重复计入且 domain 被覆盖成 ``report``。
    """
    seen: set[str] = set()
    gaps: list[str] = []
    for record in ctx.history("data_gap"):
        value = record.value
        items = value if isinstance(value, (list, tuple)) else ([value] if value else [])
        for item in items:
            note = str(item)
            if note and note not in seen:
                seen.add(note)
                gaps.append(note)
    return gaps


def _try_llm_report(brief: dict[str, Any]) -> str:
    """在线路径：LLM 生成对客报告；失败返回空串（调用方转模板）。"""
    try:
        from ...common.llm_factory import get_llm
        from ..prompts.report import build_report_prompt
        llm = get_llm(temperature=0.3, tier="L2", max_tokens=600, timeout=45,
                      thinking="disabled")
        resp = llm.invoke(build_report_prompt(brief))
        text = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        return _apply_forbidden_words(text) if text else ""
    except Exception:  # noqa: BLE001 报告失败不阻断整链
        return ""


def make_report_handler(scenario_key: str,
                        llm_ok: Callable[[], bool]) -> Callable:
    """构造某场景的报告 handler。

    :param scenario_key: 场景 key（决定简报字段与对客模板）
    :param llm_ok: 可用性探针（由 node_registry 传入，闭包捕获 monkeypatch 后的值）
    """
    spec = _BRIEF_SPEC.get(scenario_key, [])

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        brief: dict[str, Any] = {label: _fmt(ctx.get(key)) for label, key in spec}
        # 结构化镜像键（前端 8D 网格等专用排版用；镜像键不参与文本断言）
        for label, key in spec:
            raw = ctx.get(key)
            if isinstance(raw, dict) and raw:
                brief[f"{label}#structured"] = raw
        gaps = _collect_gaps(ctx)
        if gaps:
            brief["数据缺口"] = "；".join(gaps)

        # 报告必要性智能判断（V3.4）：不是所有问题都值得一份完整报告——
        # 判定为"轻量问题"（单域取证且无 HITL 且无数据缺口）时仅输出简报，
        # 不生成对客报告（省时省成本，也避免用户被冗余报告打扰）。
        # 判据均为确定量（域数/HITL/缺口），可审计；LLM 判断版留作演进位。
        # 多域取证判定：统计 SharedContext 中来自不同域的 provenance 记录
        domains_seen = len({rec.get("source") for key in ctx.keys()
                            for rec in ctx.provenance(key)
                            if rec.get("source") and rec.get("source") != "router"})
        hitl_count = len(getattr(task, "hitl", None) or []) or len(
            ctx.get("hitl_pending") or [])
        needs_full = bool(domains_seen >= 2 or hitl_count or gaps)
        if not needs_full:
            logger.info("[report] %s 轻量问题（单域 %d 取证 / 无 HITL / 无缺口）："
                        "仅输出简报，不生成完整报告", scenario_key, domains_seen)

        degraded = not llm_ok()
        # 无对客模板的场景（如来料异常/设备预警）不产出对客报告，
        # 但必须先初始化，避免下方 summary 行未绑定即引用（UnboundLocalError）。
        customer_report = ""
        try:
            if scenario_key in _CUST_TEMPLATE and needs_full:
                customer_report = ""
                if not degraded:
                    sink.emit({"type": "llm_start", "detail": "生成对客脱敏报告..."})
                    customer_report = _try_llm_report(brief)
                if not customer_report:
                    degraded = True
                    customer_report = _apply_forbidden_words(
                        _CUST_TEMPLATE[scenario_key](ctx))
            elif scenario_key in _CUST_TEMPLATE:
                # 轻量问题：不生成完整对客报告，仅给简报式结论（report 非空但轻量）
                kv = "；".join(f"{k}：{v}" for k, v in list(brief.items())[:3] if v)
                customer_report = f"轻量结论（未达完整报告标准）：{kv or '已查询'}"
            else:
                customer_report = ""
        except Exception as exc:  # noqa: BLE001 —— 报告生成异常不得清空简报（加固）
            logger.warning("[report] %s 报告生成异常，简报保留: %s", scenario_key, exc)
            customer_report = "报告生成异常，简报与数据溯源不受影响，可人工复核。"
            degraded = True

        summary = f"内部简报已生成（{len(spec)} 项）"
        if gaps:
            summary += "；含数据缺口"
        if customer_report and degraded:
            summary += "；对客报告（degraded 模板化）"

        payload: dict[str, Any] = {
            "internal_brief": brief,
            "report_degraded": degraded,
            "data_gaps": gaps,
        }
        if customer_report:
            payload["customer_report"] = customer_report
        provenance: list[dict] = [{
            "channel": "report", "scenario": scenario_key, "degraded": degraded,
            "masked_words": list(FORBIDDEN_WORDS)}]
        # H-1：data_gap 的 provenance 归因由各域 handler 自带（domain=来源域），
        # 报告节点不再重复写入，避免"同 note 重复 + domain 被覆盖为 report"。
        payload["provenance"] = provenance
        return {"domain": "report", "status": "ok",
                "payload": payload, "summary": summary}
    return handler
