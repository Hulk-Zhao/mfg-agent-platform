"""研发工程域模块（S3 设计根因 / S8 参数·备件 —— 均走 degrade 模板 + data_gap）。

工程域当前**无数据源**（无 BOM/ECN/图纸/参数基线/备件库存）——按架构纪律
"数据质量先于智能能力"：**降级为模板 + ``data_gap`` 标记，绝不编造**。
``make_template_agent`` 为一个通用"模板型 Agent"工厂。
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "design_root_cause_template",
    "param_advice_template",
    "spare_parts_template",
    "make_template_agent",
]

TemplateFn = Callable[[Any, Any], dict]


def design_root_cause_template(ctx: Any, task: Any) -> dict[str, Any]:
    """设计根因模板（无 BOM/ECN 数据源）。"""
    return {
        "design_root_cause": "无 BOM/ECN/图纸数据源，未做设计根因推断",
        "engineering_summary": "设计根因：数据缺口（无 BOM/ECN 数据源）",
        "data_gap": ["设计根因：无 BOM/ECN/图纸数据源，未编造结论"],
    }


def param_advice_template(ctx: Any, task: Any) -> dict[str, Any]:
    """工艺参数建议模板（无参数基线数据源）。"""
    return {
        "param_advice": "无工艺参数基线数据源，建议按设备告警项复测并回归基准",
        "engineering_summary": "工艺参数建议：数据缺口（无参数基线数据源）",
        "data_gap": ["工艺参数基线：无数据源，未编造结论"],
    }


def spare_parts_template(ctx: Any, task: Any) -> dict[str, Any]:
    """备件需求模板（无备件库存/领用数据源）。"""
    return {
        "spare_parts": "无备件库存/领用数据源，未生成备件清单",
        "spare_summary": "备件需求：数据缺口（无备件数据源）",
        "data_gap": ["备件库存：无数据源，未编造结论"],
    }


def make_template_agent(domain: str, template_fn: TemplateFn, *,
                        summary_key: str | None = None) -> Callable:
    """通用「模板型 Agent」：产出规则模板结论 + ``data_gap``，标 degraded。

    :param domain: 域组名（rd_engineering / supply_chain ...）
    :param template_fn: ``(ctx, task) -> dict``；返回值可含 ``data_gap`` 列表
    :param summary_key: 摘要写入的 payload 键（默认 ``f"{domain}_summary"``）
    """

    def handler(task: Any, ctx: Any, sink: Any) -> dict:
        out = dict(template_fn(ctx, task))
        gaps = _as_list(out.get("data_gap"))
        sink.emit({"type": "tool_start", "tool": "规则模板",
                   "detail": "无数据源 → 模板化结论（不臆造）"})
        sink.emit({"type": "tool_end", "tool": "规则模板",
                   "detail": "；".join(gaps) or "模板化结论"})
        provenance: list[dict] = [
            {"channel": "template", "domain": domain, "degraded": True,
             "note": "规则模板生成（无数据源）"}]
        for gap in gaps:
            provenance.append({"channel": "data_gap", "domain": domain,
                               "note": gap})
        out["provenance"] = provenance
        out[f"{domain}_degraded"] = True
        summary = str(out.get(summary_key or f"{domain}_summary")
                      or "模板化结论（degraded）")
        return {"domain": domain, "status": "degraded",
                "payload": out, "summary": summary}
    return handler


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return [str(value)]
