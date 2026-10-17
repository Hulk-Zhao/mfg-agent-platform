"""制造综合看板聚合服务（V3.4）。

纯聚合函数：把本地 SQLite 只读数据源的原始明细聚合成
「制造看板」/「品质看板」两套结果，供 ``demo/api.py`` 的两个只读 GET 端点返回。

纪律：
- 真实优先：全量走 ``DataSourceRegistry.execute_readonly_sql``（只读 + 行数上限）；
- 降级不崩：任一聚合 SQL 失败只记 ``degraded_reason`` 并返回空值，**绝不抛出**；
- 口径即代码：SQL 均为常量串、**绝不拼接用户输入**（该通道不经 SQLAuditor）。

指标口径见 ``docs/架构与前端设计-制造综合看板.md`` §8.3。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 演示窗口：最近 7 天（10-08 ~ 10-14），天然避开 10-15 红线日
DEMO_WINDOW = {
    "start": "2026-10-08",
    "end": "2026-10-14",
    "days": 7,
    "label": "最近 7 天（演示窗口）",
}

_WINDOW_DATES = ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11",
                 "2026-10-12", "2026-10-13", "2026-10-14"]

# 标准三率目标线（完整率 100% / 完善率 98% / 遵守率 90%）→ 符合率目标 = 三者乘积
_STD_TARGETS = {"completeness_rate": 100.0, "perfection_rate": 98.0,
                "compliance_rate": 90.0}
_STD_CONFORMITY_TARGET = round(
    _STD_TARGETS["completeness_rate"] * _STD_TARGETS["perfection_rate"]
    * _STD_TARGETS["compliance_rate"] / 10000.0, 1)


# ---------------------------------------------------------------- 基础工具

def _rows(registry, sql: str, params: dict, state: dict) -> list[dict]:
    """执行只读聚合 SQL；异常兜底返回空列表并记录降级原因。"""
    try:
        return registry.execute_readonly_sql(sql, params)
    except Exception as exc:  # noqa: BLE001 —— 聚合失败不抛出，记原因后降级
        msg = f"{type(exc).__name__}: {exc}"
        if msg not in state["reasons"]:
            state["reasons"].append(msg)
        logger.warning("[dashboard] 聚合查询失败: %s", msg)
        return []


def _pct(num: float, den: float) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def _status(value: float, target: float, higher_is_better: bool) -> str:
    """达标判定：达标线 tanh；越高越好允许下沿 10%，越低越好允许上沿 50%。"""
    if higher_is_better:
        if value >= target:
            return "ok"
        return "warn" if value >= target * 0.9 else "danger"
    if value <= target:
        return "ok"
    return "warn" if value <= target * 1.5 else "danger"


def _trend(rows: list[dict]) -> list:
    """按日序列化为 7 点数值数组（缺日回填上一个可用值；全员缺则回填首值）。"""
    by_date: dict[str, float] = {}
    for r in rows:
        by_date[r.get("d")] = _pct(r.get("num") or 0, r.get("den") or 0)
    out: list[Optional[float]] = []
    last: Optional[float] = None
    for d in _WINDOW_DATES:
        if d in by_date:
            last = by_date[d]
        out.append(last)
    first = next((v for v in out if v is not None), 0.0)
    return [v if v is not None else first for v in out]


def _sum(rows: list[dict], field: str) -> float:
    return sum((r.get(field) or 0) for r in rows)


def _rank_items(rows: list[dict], name_of: Callable[[dict], str], target: float,
                higher_is_better: bool, limit: int = 5) -> list[dict]:
    """TOP5 = 最需关注的 5 名：越高越好取最低 5、越低越好取最高 5。"""
    items: list[dict] = []
    for r in rows:
        num, den = r.get("num") or 0, r.get("den") or 0
        if den <= 0:
            continue
        val = _pct(num, den)
        items.append({
            "name": name_of(r),
            "line": r.get("line_code") or "",
            "model": r.get("product_model") or "",
            "value": val,
            "status": _status(val, target, higher_is_better),
            "numerator": num,
            "denominator": den,
        })
    items.sort(key=lambda x: x["value"], reverse=not higher_is_better)
    for i, it in enumerate(items[:limit], 1):
        it["rank"] = i
    return items[:limit]


def _kpi(key: str, label: str, num: float, den: float, target: float,
         higher_is_better: bool, definition: str, ask: str,
         trend: list) -> dict:
    value = _pct(num, den)
    return {
        "key": key, "label": label, "value": value, "unit": "%",
        "numerator": num, "denominator": den,
        "target": target, "higher_is_better": higher_is_better,
        "status": _status(value, target, higher_is_better),
        "delta": round(value - target, 1),
        "definition": definition, "formula": f"{num} / {den}",
        "ask": ask, "trend": trend,
    }


def _envelope(screen: str, state: dict) -> dict:
    return {
        "screen": screen,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "sqlite:dev",
        "degraded": bool(state["reasons"]),
        "degraded_reason": "；".join(state["reasons"][:3]),
        "window": dict(DEMO_WINDOW),
    }


def _line_model(r: dict) -> str:
    return f"{r.get('line_code') or '—'} · {r.get('product_model') or '—'}"


# ---------------------------------------------------------------- 制造看板

_MFG_NOTES = [
    "稼动率 = 实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
    "报废率 = 报废数 ÷ 总产出数；返工品既不计入良品也不计入报废。",
    "报工完成率 = 已报工工序数 ÷ 应报工工序数。",
    "测试直通率 = 首次测试通过数 ÷ 测试总数（重测才过不计入）。",
    "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转："
    "越高越好取最低 5、越低越好取最高 5。",
]


def build_manufacturing_dashboard(registry) -> dict:
    state: dict[str, Any] = {"reasons": []}
    params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"]}
    win = "record_date BETWEEN :start AND :end"

    # 稼动率
    av = _rows(registry,
               "SELECT line_code, product_model, SUM(actual_run_min) AS num, "
               "SUM(planned_run_min) AS den FROM equipment_run_time "
               f"WHERE {win} GROUP BY line_code, product_model", params, state)
    av_t = _rows(registry,
                 "SELECT record_date AS d, SUM(actual_run_min) AS num, "
                 "SUM(planned_run_min) AS den FROM equipment_run_time "
                 f"WHERE {win} GROUP BY record_date", params, state)

    # 报废率
    sp = _rows(registry,
               "SELECT line_code, product_model, SUM(scrap_qty) AS num, "
               "SUM(output_qty) AS den FROM scrap_record "
               f"WHERE {win} GROUP BY line_code, product_model", params, state)
    sp_t = _rows(registry,
                 "SELECT record_date AS d, SUM(scrap_qty) AS num, "
                 "SUM(output_qty) AS den FROM scrap_record "
                 f"WHERE {win} GROUP BY record_date", params, state)

    # 报工完成率
    wr = _rows(registry,
               "SELECT wo_no, line_code, product_model, SUM(reported) AS num, "
               "SUM(should_report) AS den FROM work_report "
               "WHERE report_date BETWEEN :start AND :end "
               "GROUP BY wo_no, line_code, product_model", params, state)
    wr_t = _rows(registry,
                 "SELECT report_date AS d, SUM(reported) AS num, "
                 "SUM(should_report) AS den FROM work_report "
                 "WHERE report_date BETWEEN :start AND :end "
                 "GROUP BY report_date", params, state)

    # 测试直通率
    tr = _rows(registry,
               "SELECT product_model, "
               "SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END) AS num, "
               "COUNT(*) AS den FROM test_records "
               "WHERE test_date BETWEEN :start AND :end "
               "GROUP BY product_model", params, state)
    tr_t = _rows(registry,
                 "SELECT test_date AS d, "
                 "SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END) AS num, "
                 "COUNT(*) AS den FROM test_records "
                 "WHERE test_date BETWEEN :start AND :end "
                 "GROUP BY test_date", params, state)

    kpis = [
        _kpi("availability", "稼动率", _sum(av, "num"), _sum(av, "den"), 90.0,
             True, "实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
             "最近7天各产线稼动率如何？最低的是哪条线？", _trend(av_t)),
        _kpi("scrap_rate", "报废率", _sum(sp, "num"), _sum(sp, "den"), 2.0,
             False, "报废数 ÷ 总产出数（不可返修的判废比例）。",
             "最近7天各产线报废率如何？最高的是哪条线？", _trend(sp_t)),
        _kpi("report_rate", "报工完成率", _sum(wr, "num"), _sum(wr, "den"), 95.0,
             True, "已报工工序数 ÷ 应报工工序数。",
             "最近7天哪些工单报工完成率偏低？", _trend(wr_t)),
        _kpi("test_fpy", "测试直通率", _sum(tr, "num"), _sum(tr, "den"), 98.0,
             True, "首次测试通过数 ÷ 测试总数（重测才过不计入）。",
             "最近7天各型号测试直通率如何？", _trend(tr_t)),
    ]

    rankings = [
        {"key": "availability", "label": "稼动率", "unit": "%", "target": 90.0,
         "higher_is_better": True,
         "items": _rank_items(av, _line_model, 90.0, True)},
        {"key": "scrap_rate", "label": "报废率", "unit": "%", "target": 2.0,
         "higher_is_better": False,
         "items": _rank_items(sp, _line_model, 2.0, False)},
        {"key": "report_rate", "label": "报工完成率", "unit": "%", "target": 95.0,
         "higher_is_better": True,
         "items": _rank_items(wr, lambda r: r.get("wo_no") or "—", 95.0, True)},
        {"key": "test_fpy", "label": "测试直通率", "unit": "%", "target": 98.0,
         "higher_is_better": True,
         "items": _rank_items(tr, lambda r: r.get("product_model") or "—", 98.0, True)},
    ]

    data = _envelope("manufacturing", state)
    data.update({"kpis": kpis, "rankings": rankings, "notes": _MFG_NOTES})
    return data


# ---------------------------------------------------------------- 品质看板

_QTY_NOTES = [
    "不良率 = 不良数 ÷ 受检总数。",
    "直通率 FPY = 一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
    "标准三率 = 标准完整率 × 标准完善率 × 标准遵守率 = 符合率"
    "（完整率：该有的标准是否齐全；完善率：标准与现场是否一致；"
    "遵守率：现场是否按标准作业）。",
    "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转："
    "越高越好取最低 5、越低越好取最高 5。",
]


def _three_rates(rows: list[dict]) -> tuple[float, float, float, float]:
    total = len(rows)
    present = sum(1 for r in rows if r.get("doc_present"))
    complete = sum(1 for r in rows if r.get("doc_complete"))
    comp = _pct(present, total)
    perf = _pct(complete, present)
    obey = _pct(_sum(rows, "audit_conform"), _sum(rows, "audit_total"))
    conformity = round(comp * perf * obey / 10000.0, 1)
    return comp, perf, obey, conformity


def build_quality_dashboard(registry) -> dict:
    state: dict[str, Any] = {"reasons": []}
    params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"]}
    win = "date(inspect_time) BETWEEN :start AND :end"

    # 不良率 / 直通率（同一份 quality_defect 明细，互为补数）
    qd = _rows(registry,
               "SELECT line_code, product_model, SUM(defect_qty) AS num, "
               "SUM(total_qty) AS den FROM quality_defect "
               f"WHERE {win} GROUP BY line_code, product_model", params, state)
    qd_t = _rows(registry,
                 "SELECT date(inspect_time) AS d, SUM(defect_qty) AS num, "
                 "SUM(total_qty) AS den FROM quality_defect "
                 f"WHERE {win} GROUP BY date(inspect_time)", params, state)

    defect_num, defect_den = _sum(qd, "num"), _sum(qd, "den")
    fpy_num = defect_den - defect_num

    # 直通率趋势 = 100 − 不良率趋势
    fpy_trend = [round(100.0 - v, 1) for v in _trend(qd_t)]

    # 标准三率
    qs = _rows(registry,
               "SELECT product_model, doc_present, doc_complete, "
               "audit_total, audit_conform FROM std_standard_audit "
               "WHERE audit_date BETWEEN :start AND :end", params, state)
    comp, perf, obey, conformity = _three_rates(qs)
    by_model = []
    for model in ("A型号", "B型号", "C型号"):
        sub = [r for r in qs if r.get("product_model") == model]
        if not sub:
            continue
        c, f, o, k = _three_rates(sub)
        by_model.append({"model": model, "completeness_rate": c,
                         "perfection_rate": f, "compliance_rate": o,
                         "conformity_rate": k,
                         "status": _status(k, _STD_CONFORMITY_TARGET, True)})

    kpis = [
        _kpi("defect_rate", "不良率", defect_num, defect_den, 2.0, False,
             "不良数 ÷ 受检总数。",
             "最近7天各产线不良率如何？最高的是哪条线？", _trend(qd_t)),
        _kpi("fpy", "直通率 FPY", fpy_num, defect_den, 98.0, True,
             "一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
             "最近7天各产线直通率如何？最低的是哪条线？", fpy_trend),
    ]

    rankings = [
        {"key": "defect_rate", "label": "不良率", "unit": "%", "target": 2.0,
         "higher_is_better": False,
         "items": _rank_items(qd, _line_model, 2.0, False)},
        {"key": "fpy", "label": "直通率", "unit": "%", "target": 98.0,
         "higher_is_better": True,
         "items": _fpy_rank_items(qd, 98.0)},
    ]

    data = _envelope("quality", state)
    data.update({
        "kpis": kpis, "rankings": rankings, "notes": _QTY_NOTES,
        "standard_three_rates": {
            "definition": "标准三率 = 标准完整率 × 标准完善率 × 标准遵守率 = 符合率",
            "targets": dict(_STD_TARGETS),
            "overall": {
                "completeness_rate": comp, "perfection_rate": perf,
                "compliance_rate": obey, "conformity_rate": conformity,
                "status": _status(conformity, _STD_CONFORMITY_TARGET, True),
                "formula": f"{comp}% × {perf}% × {obey}% = {conformity}%",
            },
            "by_model": by_model,
        },
    })
    return data


def _fpy_rank_items(qd: list[dict], target: float, limit: int = 5) -> list[dict]:
    """直通率 TOP5：把 quality_defect 分组的「良品/投入」换算后取最低 5。"""
    rows = [{"line_code": r.get("line_code"), "product_model": r.get("product_model"),
             "num": (r.get("den") or 0) - (r.get("num") or 0), "den": r.get("den") or 0}
            for r in qd]
    return _rank_items(rows, _line_model, target, True, limit)
