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

from ..core.spc import compute_capability

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


def _row_entity(r: dict) -> dict:
    """分组行 → 下钻实体（默认口径：产线·型号 / 工单 / 型号，供 TOP5 行 L3 定位）。"""
    return {"line": r.get("line_code") or "",
            "model": r.get("product_model") or "",
            "wo": r.get("wo_no") or ""}


def _rank_items(rows: list[dict], name_of: Callable[[dict], str], target: float,
                higher_is_better: bool, limit: int = 5,
                entity_of: Optional[Callable[[dict], dict]] = None) -> list[dict]:
    """TOP5 = 最需关注的 5 名：越高越好取最低 5、越低越好取最高 5。

    ``entity_of`` 允许调用方自定义行 → 下钻实体映射（订单看板按 客户·等级 / 型号）。
    """
    items: list[dict] = []
    for r in rows:
        num, den = r.get("num") or 0, r.get("den") or 0
        if den <= 0:
            continue
        val = _pct(num, den)
        items.append({
            "name": name_of(r),
            "entity": (entity_of or _row_entity)(r),
            "line": r.get("line_code") or "",
            "model": r.get("product_model") or "",
            "wo": r.get("wo_no") or "",
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
    "来料批次合格率 = IQC 判定合格（pass）批次数 ÷ 窗口内来料批次总数"
    "（进料端质量，按供应商分解）。",
    "客诉闭环率 = 已关闭（closed）客诉单数 ÷ 客诉单总数"
    "（客户端质量，快照口径不做时间窗口过滤；按 客户 · 型号 分解）。",
    "标准三率 = 标准完整率 × 标准完善率 × 标准遵守率 = 符合率"
    "（完整率：该有的标准是否齐全；完善率：标准与现场是否一致；"
    "遵守率：现场是否按标准作业）。",
    "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转："
    "越高越好取最低 5、越低越好取最高 5。",
    "累计不良柏拉图：窗口内按不良类型降序累积，关键少数 = 累计占比首达 80% 的类型"
    "（与 SPC 柏拉图 spc.pareto 同源口径）；点击类型可下钻其产线 · 型号分解。",
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

    # 来料批次合格率（IQC）：窗口内按供应商分解，合格批次 ÷ 来料批次总数
    ib = _rows(registry,
               "SELECT supplier, "
               "SUM(CASE WHEN inspect_status='pass' THEN 1 ELSE 0 END) AS num, "
               "COUNT(*) AS den FROM incoming_batch "
               "WHERE date(arrival_time) BETWEEN :start AND :end "
               "GROUP BY supplier", params, state)
    ib_t = _rows(registry,
                 "SELECT date(arrival_time) AS d, "
                 "SUM(CASE WHEN inspect_status='pass' THEN 1 ELSE 0 END) AS num, "
                 "COUNT(*) AS den FROM incoming_batch "
                 "WHERE date(arrival_time) BETWEEN :start AND :end "
                 "GROUP BY date(arrival_time)", params, state)

    # 客诉闭环率（快照口径，不按窗口过滤）：已关闭客诉单 ÷ 客诉单总数，按 客户 · 型号 分解
    cc = _rows(registry,
               "SELECT customer, product_model, "
               "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS num, "
               "COUNT(*) AS den FROM customer_complaint "
               "GROUP BY customer, product_model", params, state)

    def _sup_name(r: dict) -> str:
        return r.get("supplier") or "—"

    def _sup_entity(r: dict) -> dict:
        return {"supplier": r.get("supplier") or ""}

    def _comp_name(r: dict) -> str:
        return f"{r.get('customer') or '—'} · {r.get('product_model') or '—'}"

    def _comp_entity(r: dict) -> dict:
        return {"customer": r.get("customer") or "",
                "model": r.get("product_model") or ""}

    kpis = [
        _kpi("defect_rate", "不良率", defect_num, defect_den, 2.0, False,
             "不良数 ÷ 受检总数。",
             "最近7天各产线不良率如何？最高的是哪条线？", _trend(qd_t)),
        _kpi("fpy", "直通率 FPY", fpy_num, defect_den, 98.0, True,
             "一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
             "最近7天各产线直通率如何？最低的是哪条线？", fpy_trend),
        _kpi("incoming_pass_rate", "来料批次合格率", _sum(ib, "num"),
             _sum(ib, "den"), 90.0, True,
             "IQC 判定合格（pass）批次数 ÷ 窗口内来料批次总数。",
             "最近7天各供应商来料批次合格率如何？最低的是哪家？", _trend(ib_t)),
        _kpi("complaint_close_rate", "客诉闭环率", _sum(cc, "num"),
             _sum(cc, "den"), 80.0, True,
             "已关闭（closed）客诉单数 ÷ 客诉单总数（快照口径）。",
             "各客户型号的客诉闭环率如何？哪些还没闭环？", []),
    ]

    rankings = [
        {"key": "defect_rate", "label": "不良率", "unit": "%", "target": 2.0,
         "higher_is_better": False,
         "items": _rank_items(qd, _line_model, 2.0, False)},
        {"key": "fpy", "label": "直通率", "unit": "%", "target": 98.0,
         "higher_is_better": True,
         "items": _fpy_rank_items(qd, 98.0)},
        {"key": "incoming_pass_rate", "label": "来料批次合格率", "unit": "%",
         "target": 90.0, "higher_is_better": True,
         "items": _rank_items(ib, _sup_name, 90.0, True, 5, _sup_entity)},
        {"key": "complaint_close_rate", "label": "客诉闭环率", "unit": "%",
         "target": 80.0, "higher_is_better": True,
         "items": _rank_items(cc, _comp_name, 80.0, True, 5, _comp_entity)},
    ]

    # 累计不良柏拉图：窗口内按 defect_type 聚合，降序累积 + 关键少数（累计首达 80%）
    pareto = _pareto_cum(_rows(registry,
                               "SELECT defect_type AS name, SUM(defect_qty) AS value "
                               "FROM quality_defect "
                               f"WHERE {win} GROUP BY defect_type "
                               "ORDER BY value DESC", params, state))

    data = _envelope("quality", state)
    data.update({
        "kpis": kpis, "rankings": rankings, "notes": _QTY_NOTES,
        "pareto": pareto,
        "pareto_label": "不良类型",
        "pareto_kpi": "defect_rate",   # 供前端点击下钻（与 drill 同源指标）
        "pareto_vital": _vital_few(pareto),
        "pareto_threshold": 80.0,
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


# ---------------------------------------------------------------- 订单履约看板

_ORDER_NOTES = [
    "OTD 准时交付率 = 实际发货日 ≤ 客户要求交付日 的订单数 ÷ 窗口内订单总数"
    "（订单按客户要求交付日 due_date 归入统计窗口）。",
    "OTIF 准时足额率 = 同时满足「准时」与「足额（齐套发齐）」的订单数 ÷ 订单总数，"
    "比 OTD 更严格，是客户面向的核心履约指标。",
    "延期订单率 = 预计/实际延期天数 > 0 的订单数 ÷ 订单总数（越低越好）。",
    "齐套率 = 已齐套料数 ÷ 应齐套料数（物料齐套对按期交付的保障度）。",
    "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转："
    "越高越好取最低 5、越低越好取最高 5。",
]

# 订单状态管道固定环节顺序（与 order_fulfillment.stage 取值一致）
_ORDER_STAGES = ["已下单", "计划", "采购", "生产", "质检", "发货"]


def build_order_dashboard(registry) -> dict:
    """订单履约看板 L1：4 个交付类 KPI + TOP5 排行 + 状态管道 + 风险订单清单。"""
    state: dict[str, Any] = {"reasons": []}
    params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"]}
    win = "due_date BETWEEN :start AND :end"

    def _cust_name(r: dict) -> str:
        return f"{r.get('customer') or '—'} · {r.get('customer_level') or '—'}"

    def _cust_entity(r: dict) -> dict:
        return {"customer": r.get("customer") or "",
                "level": r.get("customer_level") or ""}

    def _model_name(r: dict) -> str:
        return r.get("product_model") or "—"

    def _model_entity(r: dict) -> dict:
        return {"model": r.get("product_model") or ""}

    # OTD 准时交付率
    otd = _rows(registry,
                "SELECT customer, customer_level, SUM(on_time) AS num, COUNT(*) AS den "
                f"FROM order_fulfillment WHERE {win} "
                "GROUP BY customer, customer_level", params, state)
    otd_t = _rows(registry,
                  "SELECT due_date AS d, SUM(on_time) AS num, COUNT(*) AS den "
                  f"FROM order_fulfillment WHERE {win} GROUP BY due_date", params, state)

    # OTIF 准时足额率
    otif = _rows(registry,
                 "SELECT customer, customer_level, "
                 "SUM(CASE WHEN on_time=1 AND in_full=1 THEN 1 ELSE 0 END) AS num, "
                 f"COUNT(*) AS den FROM order_fulfillment WHERE {win} "
                 "GROUP BY customer, customer_level", params, state)
    otif_t = _rows(registry,
                   "SELECT due_date AS d, "
                   "SUM(CASE WHEN on_time=1 AND in_full=1 THEN 1 ELSE 0 END) AS num, "
                   f"COUNT(*) AS den FROM order_fulfillment WHERE {win} "
                   "GROUP BY due_date", params, state)

    # 延期订单率
    dr = _rows(registry,
               "SELECT customer, customer_level, "
               "SUM(CASE WHEN delay_days>0 THEN 1 ELSE 0 END) AS num, "
               f"COUNT(*) AS den FROM order_fulfillment WHERE {win} "
               "GROUP BY customer, customer_level", params, state)
    dr_t = _rows(registry,
                 "SELECT due_date AS d, "
                 "SUM(CASE WHEN delay_days>0 THEN 1 ELSE 0 END) AS num, "
                 f"COUNT(*) AS den FROM order_fulfillment WHERE {win} "
                 "GROUP BY due_date", params, state)

    # 齐套率
    kit = _rows(registry,
                "SELECT product_model, SUM(kit_done_qty) AS num, "
                f"SUM(kit_total_qty) AS den FROM order_fulfillment WHERE {win} "
                "GROUP BY product_model", params, state)
    kit_t = _rows(registry,
                  "SELECT due_date AS d, SUM(kit_done_qty) AS num, "
                  f"SUM(kit_total_qty) AS den FROM order_fulfillment WHERE {win} "
                  "GROUP BY due_date", params, state)

    kpis = [
        _kpi("otd", "OTD 准时交付率", _sum(otd, "num"), _sum(otd, "den"), 98.0,
             True, "准时交付（实际发货日 ≤ 客户要求交付日）订单数 ÷ 订单总数。",
             "最近7天各客户 OTD 准时交付率如何？最低的是哪个客户？", _trend(otd_t)),
        _kpi("otif", "OTIF 准时足额率", _sum(otif, "num"), _sum(otif, "den"), 95.0,
             True, "同时满足准时且足额的订单数 ÷ 订单总数。",
             "最近7天各客户 OTIF 准时足额率如何？最低的是哪个客户？", _trend(otif_t)),
        _kpi("delay_rate", "延期订单率", _sum(dr, "num"), _sum(dr, "den"), 5.0,
             False, "延期天数 > 0 的订单数 ÷ 订单总数（越低越好）。",
             "最近7天各客户延期订单率如何？最高的是哪个客户？延期原因有哪些？",
             _trend(dr_t)),
        _kpi("kit_rate", "齐套率", _sum(kit, "num"), _sum(kit, "den"), 95.0,
             True, "已齐套料数 ÷ 应齐套料数。",
             "最近7天各型号订单齐套率如何？最低的是哪个型号？缺料集中在哪些料号？",
             _trend(kit_t)),
    ]

    rankings = [
        {"key": "otd", "label": "OTD 准时交付率", "unit": "%", "target": 98.0,
         "higher_is_better": True,
         "items": _rank_items(otd, _cust_name, 98.0, True, 5, _cust_entity)},
        {"key": "otif", "label": "OTIF 准时足额率", "unit": "%", "target": 95.0,
         "higher_is_better": True,
         "items": _rank_items(otif, _cust_name, 95.0, True, 5, _cust_entity)},
        {"key": "delay_rate", "label": "延期订单率", "unit": "%", "target": 5.0,
         "higher_is_better": False,
         "items": _rank_items(dr, _cust_name, 5.0, False, 5, _cust_entity)},
        {"key": "kit_rate", "label": "齐套率", "unit": "%", "target": 95.0,
         "higher_is_better": True,
         "items": _rank_items(kit, _model_name, 95.0, True, 5, _model_entity)},
    ]

    # 状态管道（固定环节顺序，附各环节延期数）
    pipe_rows = _rows(registry,
                      "SELECT stage, COUNT(*) AS cnt, "
                      "SUM(CASE WHEN delay_days>0 THEN 1 ELSE 0 END) AS delayed "
                      f"FROM order_fulfillment WHERE {win} GROUP BY stage",
                      params, state)
    pipe_by = {r.get("stage"): r for r in pipe_rows}
    pipeline = []
    for st in _ORDER_STAGES:
        r = pipe_by.get(st) or {}
        pipeline.append({"stage": st, "count": int(r.get("cnt") or 0),
                         "delayed": int(r.get("delayed") or 0)})
    for r in pipe_rows:  # 覆盖非标准环节（防御）
        if r.get("stage") not in _ORDER_STAGES:
            pipeline.append({"stage": r.get("stage") or "—",
                             "count": int(r.get("cnt") or 0),
                             "delayed": int(r.get("delayed") or 0)})

    # 风险订单清单（risk_type ≠ 正常，按延期天数降序）
    risk_orders = _rows(registry,
                        "SELECT order_no, customer, customer_level, product_model, "
                        "qty, stage, risk_type, delay_days, progress, "
                        "shortage_note, owner FROM order_fulfillment "
                        f"WHERE {win} AND risk_type <> '正常' "
                        "ORDER BY delay_days DESC, due_date ASC LIMIT 8",
                        params, state)

    data = _envelope("orders", state)
    data.update({"kpis": kpis, "rankings": rankings, "notes": _ORDER_NOTES,
                 "pipeline": pipeline, "risk_orders": risk_orders})
    return data


# ---------------------------------------------------------------- SPC 过程能力看板

_SPC_NOTES = [
    "过程能力指数双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R，R̄/d2）；"
    "Pp/Ppk 用整体 σ（样本标准差 ddof=1），保证 Pp ≠ Cp。",
    "判定线：Cpk ≥ 1.33 合格；1.00 ≤ Cpk < 1.33 偏低；Cpk < 1.00 不合格。",
    "L1 卡片展示 Cpk（无量纲指数，由确定性内核 core/spc 计算）；"
    "下钻采用「子组超差率」，以复用统一聚合引擎（Cpk 与超差率是同一过程的两面）。",
    "不良柏拉图与 SPC 工具 spc.pareto 同源（quality_defect 全量聚合，关键少数 = 累计首达 80%）。",
    "缺测量值 / 缺规格限时该特性标数据缺口（data_gap），绝不编造指数。",
]


def _verdict_status(verdict: Optional[str]) -> str:
    return {"合格": "ok", "偏低": "warn", "不合格": "danger"}.get(verdict or "", "empty")


def _spc_caps_from_rows(rows: list[dict]) -> list[dict]:
    """测量明细 → 逐特性过程能力（按 subgroup_no 分组后交 core.spc 计算，缺口不编造）。"""
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = str(r.get("characteristic") or "").strip()
        if not name:
            continue
        g = grouped.setdefault(name, {"subgroups": {}, "usl": None, "lsl": None})
        try:
            sg = int(r.get("subgroup_no"))
            value = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        g["subgroups"].setdefault(sg, []).append(value)
        if g["usl"] is None and r.get("usl") is not None:
            g["usl"] = r.get("usl")
        if g["lsl"] is None and r.get("lsl") is not None:
            g["lsl"] = r.get("lsl")

    caps: list[dict] = []
    for name, g in grouped.items():
        subs = [g["subgroups"][k] for k in sorted(g["subgroups"])]
        n_total = sum(len(s) for s in subs)
        if g["usl"] is None or g["lsl"] is None:
            caps.append({
                "characteristic": name, "data_gap": True, "verdict": "不可判定",
                "cp": None, "cpk": None, "pp": None, "ppk": None,
                "n_total": n_total, "n_subgroups": len(subs),
                "spec": {"usl": g["usl"], "lsl": g["lsl"]},
                "warnings": ["缺 USL/LSL 规格限，能力指数不可算"],
            })
            continue
        caps.append(compute_capability(characteristic=name, subgroups=subs,
                                       usl=g["usl"], lsl=g["lsl"]))
    return caps


def build_spc_dashboard(registry) -> dict:
    """SPC 过程能力看板 L1：逐特性 Cp/Cpk/Pp/Ppk 判定 + 超差率 + 不良柏拉图。"""
    state: dict[str, Any] = {"reasons": []}
    params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"]}
    win = "date(measured_at) BETWEEN :start AND :end"

    meas = _rows(registry,
                 "SELECT characteristic, subgroup_no, seq, value, usl, lsl, "
                 f"measured_at, source FROM spc_measurement WHERE {win}", params, state)
    caps = _spc_caps_from_rows(meas)

    over = _rows(registry,
                 "SELECT characteristic, "
                 "SUM(CASE WHEN (usl IS NOT NULL AND value > usl) "
                 "OR (lsl IS NOT NULL AND value < lsl) THEN 1 ELSE 0 END) AS num, "
                 f"COUNT(*) AS den FROM spc_measurement WHERE {win} "
                 "GROUP BY characteristic", params, state)
    over_by = {r.get("characteristic"): r for r in over}

    pareto_rows = _rows(registry,
                        "SELECT defect_type AS name, SUM(defect_qty) AS value "
                        "FROM quality_defect GROUP BY defect_type "
                        "ORDER BY value DESC", {}, state)
    pareto = _pareto_cum(pareto_rows)

    defect_rate_rows = _rows(registry,
                             "SELECT defect_type, SUM(defect_qty) AS num, "
                             "SUM(total_qty) AS den FROM quality_defect "
                             "GROUP BY defect_type", {}, state)

    kpis: list[dict] = []
    for cap in caps:
        name = cap.get("characteristic") or "—"
        r = over_by.get(name) or {}
        num, den = r.get("num") or 0, r.get("den") or 0
        cpk = cap.get("cpk")
        kpis.append({
            "key": "spc_capability",
            "characteristic": name,
            "label": f"{name} 过程能力",
            "value": cpk, "unit": "",
            "cp": cap.get("cp"), "cpk": cpk,
            "pp": cap.get("pp"), "ppk": cap.get("ppk"),
            "sigma_within": cap.get("sigma_within"),
            "sigma_overall": cap.get("sigma_overall"),
            "n_total": cap.get("n_total") or 0,
            "n_subgroups": cap.get("n_subgroups") or 0,
            "spec": cap.get("spec") or {},
            "verdict": cap.get("verdict"),
            "status": _verdict_status(cap.get("verdict")),
            "target": 1.33, "higher_is_better": True,
            "delta": round(cpk - 1.33, 2) if cpk is not None else None,
            "data_gap": bool(cap.get("data_gap")),
            "warnings": cap.get("warnings") or [],
            "over_rate": _pct(num, den), "over_num": num, "over_den": den,
            "definition": "双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R）；Pp/Ppk 用整体 σ。"
                          "判定线：≥1.33 合格 / ≥1.00 偏低 / <1.00 不合格。",
            "formula": f"Cpk={cpk}",
            "ask": f"{name} 的过程能力 Cpk 如何？是否需要加严检验或工程介入？",
            "entity": {"characteristic": name},
            "trend": [],
        })

    rankings = [
        {"key": "spc_capability", "label": "子组超差率", "unit": "%",
         "target": 5.0, "higher_is_better": False,
         "items": _rank_items(over, lambda r: r.get("characteristic") or "—",
                               5.0, False, 5,
                               lambda r: {"characteristic": r.get("characteristic") or ""})},
        {"key": "spc_pareto", "label": "缺陷类型不良率", "unit": "%",
         "target": 10.0, "higher_is_better": False,
         "items": _rank_items(defect_rate_rows, lambda r: r.get("defect_type") or "—",
                              10.0, False, 5,
                              lambda r: {"defect_type": r.get("defect_type") or ""})},
    ]

    data = _envelope("spc", state)
    data.update({"kpis": kpis, "capabilities": caps, "pareto": pareto,
                 "rankings": rankings, "notes": _SPC_NOTES,
                 "data_gap": (not caps) or any(c.get("data_gap") for c in caps)})
    return data


# ---------------------------------------------------------------- 下钻（L2 / L3）

# 明细列 → 中文表头（L3「关联原始记录」用）
_COL_CN: dict[str, str] = {
    "record_date": "日期", "report_date": "日期", "test_date": "日期",
    "inspect_time": "检验时间",
    "line_code": "产线", "product_model": "型号", "wo_no": "工单号",
    "process_step": "工序", "planned_run_min": "计划运行(min)",
    "actual_run_min": "实际运行(min)", "stop_min": "停机(min)",
    "output_qty": "产出数", "scrap_qty": "报废数", "scrap_reason": "报废原因",
    "should_report": "应报工", "reported": "已报工",
    "batch_no": "批次", "test_type": "测试类型", "test_item": "测试项",
    "result": "结果", "tester": "测试员",
    "defect_type": "不良类型", "defect_qty": "不良数", "total_qty": "受检总数",
    # 工段/工序/工位（station_detail）
    "process_stage": "工段", "station_no": "工位号", "station_name": "工位名",
    "equipment_no": "设备编号", "shift": "班次", "operator": "责任人",
    "cycle_time_s": "节拍(s)", "defect_mode": "缺陷模式",
    "num_value": "分子", "den_value": "分母", "defect_value": "缺陷数",
    # 订单履约（orders）
    "order_no": "订单号", "customer": "客户", "customer_level": "客户等级",
    "order_date": "下单日", "due_date": "要求交付日", "ship_date": "实际发货日",
    "delay_days": "延期(天)", "on_time": "准时", "in_full": "足额",
    "kit_done_qty": "已齐套料数", "kit_total_qty": "应齐套料数",
    "stage": "环节", "risk_type": "风险类型", "progress": "履约进度(%)",
    "shortage_note": "缺料/异常说明", "owner": "责任人",
    # 新增指标（全角色看板下钻）
    "oee": "设备综合效率(%)", "planned_qty": "计划产出", "actual_qty": "实际产出",
    "stop_duration_min": "停机(min)",
    "supplier": "供应商", "inspect_status": "检验结论", "qualified_qty": "合格数",
    "qty": "数量", "part_no": "料号", "part_name": "物料名称", "arrival_time": "到货时间",
    "standard_no": "标准编号", "doc_present": "文件齐全", "doc_complete": "文件完整",
    "audit_total": "审核总数", "audit_conform": "符合数", "audit_date": "审核日期",
    "warehouse": "仓库", "category": "类别", "age_days": "库龄(天)",
    "safety_stock": "安全库存", "on_hand_qty": "在库量", "reserved_qty": "预留量",
    "phase": "阶段", "milestone": "里程碑", "status": "状态",
    "planned_end": "计划完成", "actual_end": "实际完成", "risk_note": "风险说明",
    "complaint_no": "客诉单号", "defect_desc": "缺陷描述",
    "task_uid": "工单号", "wf_id": "流程", "title": "标题",
    "priority": "优先级", "assignees": "责任人", "created_at": "创建时间",
    # SPC 过程能力（spc）
    "measured_at": "测量时间", "characteristic": "特性", "subgroup_no": "子组号",
    "seq": "序号", "value": "测量值", "usl": "规格上限", "lsl": "规格下限",
    "source": "数据来源",
}

# 下钻口径白名单（常量串 + 命名绑定参数，**绝不拼接用户输入**）。
# L2 = 按 group_cols 全量分解；L3 = 锁定单实体（entity 决定 WHERE 条件与参数名）。
DRILL_DEFS: dict[str, dict[str, Any]] = {
    "availability": {
        "screen": "manufacturing", "label": "稼动率", "unit": "%",
        "target": 90.0, "higher_is_better": True,
        "definition": "实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
        "table": "equipment_run_time", "date_expr": "record_date",
        "num_expr": "SUM(actual_run_min)", "den_expr": "SUM(planned_run_min)",
        "group_cols": ["line_code", "product_model"],
        "dimension": "产线 · 型号", "entity": "line_model",
        "record_cols": ["record_date", "line_code", "product_model",
                        "planned_run_min", "actual_run_min", "stop_min"],
        "ask": "最近7天各产线稼动率如何？最低的是哪条线？",
        # 工段/工序/工位下钻来源（station_detail 明细表）
        "stage_source": "station_detail", "station_metric": "availability",
        "pareto_label": "停机原因",
    },
    "scrap_rate": {
        "screen": "manufacturing", "label": "报废率", "unit": "%",
        "target": 2.0, "higher_is_better": False,
        "definition": "报废数 ÷ 总产出数（不可返修的判废比例）。",
        "table": "scrap_record", "date_expr": "record_date",
        "num_expr": "SUM(scrap_qty)", "den_expr": "SUM(output_qty)",
        "group_cols": ["line_code", "product_model"],
        "dimension": "产线 · 型号", "entity": "line_model",
        "record_cols": ["record_date", "line_code", "product_model",
                        "output_qty", "scrap_qty", "scrap_reason"],
        "ask": "最近7天各产线报废率如何？最高的是哪条线？",
        "stage_source": "station_detail", "station_metric": "scrap_rate",
        "pareto_label": "报废原因",
    },
    "report_rate": {
        "screen": "manufacturing", "label": "报工完成率", "unit": "%",
        "target": 95.0, "higher_is_better": True,
        "definition": "已报工工序数 ÷ 应报工工序数。",
        "table": "work_report", "date_expr": "report_date",
        "num_expr": "SUM(reported)", "den_expr": "SUM(should_report)",
        "group_cols": ["wo_no", "line_code", "product_model"],
        "dimension": "工单号", "entity": "wo_no",
        "record_cols": ["report_date", "wo_no", "process_step",
                        "should_report", "reported"],
        "ask": "最近7天哪些工单报工完成率偏低？",
        # 工序取自 work_report 原生 process_step（无工位层）
        "stage_source": "work_report", "station_metric": None,
        "pareto_label": "缺陷模式",
    },
    "test_fpy": {
        "screen": "manufacturing", "label": "测试直通率", "unit": "%",
        "target": 98.0, "higher_is_better": True,
        "definition": "首次测试通过数 ÷ 测试总数（重测才过不计入）。",
        "table": "test_records", "date_expr": "test_date",
        "num_expr": "SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["product_model"],
        "dimension": "型号", "entity": "model",
        "record_cols": ["test_date", "product_model", "batch_no",
                        "test_type", "test_item", "result", "tester"],
        "ask": "最近7天各型号测试直通率如何？",
        "stage_source": "station_detail", "station_metric": "test_fpy",
        "pareto_label": "测试失效项",
    },
    "defect_rate": {
        "screen": "quality", "label": "不良率", "unit": "%",
        "target": 2.0, "higher_is_better": False,
        "definition": "不良数 ÷ 受检总数。",
        "table": "quality_defect", "date_expr": "date(inspect_time)",
        "num_expr": "SUM(defect_qty)", "den_expr": "SUM(total_qty)",
        "group_cols": ["line_code", "product_model"],
        "dimension": "产线 · 型号", "entity": "line_model",
        "record_cols": ["inspect_time", "line_code", "product_model",
                        "batch_no", "defect_type", "defect_qty", "total_qty"],
        "ask": "最近7天各产线不良率如何？最高的是哪条线？",
        "stage_source": "station_detail", "station_metric": "defect_rate",
        "pareto_label": "不良模式",
    },
    "fpy": {
        "screen": "quality", "label": "直通率 FPY", "unit": "%",
        "target": 98.0, "higher_is_better": True,
        "definition": "一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
        "table": "quality_defect", "date_expr": "date(inspect_time)",
        "num_expr": "SUM(total_qty) - SUM(defect_qty)",
        "den_expr": "SUM(total_qty)",
        "group_cols": ["line_code", "product_model"],
        "dimension": "产线 · 型号", "entity": "line_model",
        "record_cols": ["inspect_time", "line_code", "product_model",
                        "batch_no", "defect_type", "defect_qty", "total_qty"],
        "ask": "最近7天各产线直通率如何？最低的是哪条线？",
        # 直通率与不良率同源（quality_defect），分子 = 分母 − 不良数
        "stage_source": "station_detail", "station_metric": "defect_rate",
        "num_from_den": True, "pareto_label": "不良模式",
    },
    # ---------------- 订单履约看板（orders）----------------
    # 窗口按客户要求交付日 due_date 归窗口；L2 按 客户·等级 / 型号 分解；L3 单实体逐日。
    "otd": {
        "screen": "orders", "label": "OTD 准时交付率", "unit": "%",
        "target": 98.0, "higher_is_better": True,
        "definition": "准时交付（实际发货日 ≤ 客户要求交付日）订单数 ÷ 窗口内订单总数。",
        "table": "order_fulfillment", "date_expr": "due_date",
        "num_expr": "SUM(on_time)", "den_expr": "COUNT(*)",
        "group_cols": ["customer", "customer_level"],
        "dimension": "客户 · 等级", "entity": "customer",
        "record_cols": ["order_no", "customer", "customer_level", "product_model",
                        "qty", "due_date", "ship_date", "delay_days",
                        "stage", "risk_type"],
        "ask": "最近7天各客户 OTD 准时交付率如何？最低的是哪个客户？",
    },
    "otif": {
        "screen": "orders", "label": "OTIF 准时足额率", "unit": "%",
        "target": 95.0, "higher_is_better": True,
        "definition": "同时满足准时且足额（齐套发齐）的订单数 ÷ 窗口内订单总数。",
        "table": "order_fulfillment", "date_expr": "due_date",
        "num_expr": "SUM(CASE WHEN on_time=1 AND in_full=1 THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["customer", "customer_level"],
        "dimension": "客户 · 等级", "entity": "customer",
        "record_cols": ["order_no", "customer", "customer_level", "product_model",
                        "qty", "due_date", "ship_date", "on_time", "in_full",
                        "stage", "risk_type"],
        "ask": "最近7天各客户 OTIF 准时足额率如何？最低的是哪个客户？",
    },
    "delay_rate": {
        "screen": "orders", "label": "延期订单率", "unit": "%",
        "target": 5.0, "higher_is_better": False,
        "definition": "预计/实际延期天数 > 0 的订单数 ÷ 窗口内订单总数。",
        "table": "order_fulfillment", "date_expr": "due_date",
        "num_expr": "SUM(CASE WHEN delay_days>0 THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["customer", "customer_level"],
        "dimension": "客户 · 等级", "entity": "customer",
        "record_cols": ["order_no", "customer", "customer_level", "product_model",
                        "qty", "due_date", "ship_date", "delay_days",
                        "stage", "risk_type", "shortage_note"],
        "ask": "最近7天各客户延期订单率如何？最高的是哪个客户？延期原因有哪些？",
    },
    "kit_rate": {
        "screen": "orders", "label": "齐套率", "unit": "%",
        "target": 95.0, "higher_is_better": True,
        "definition": "已齐套料数 ÷ 应齐套料数（物料齐套对按期交付的保障度）。",
        "table": "order_fulfillment", "date_expr": "due_date",
        "num_expr": "SUM(kit_done_qty)", "den_expr": "SUM(kit_total_qty)",
        "group_cols": ["product_model"],
        "dimension": "型号", "entity": "model",
        "record_cols": ["order_no", "product_model", "qty", "due_date",
                        "kit_done_qty", "kit_total_qty", "stage",
                        "risk_type", "shortage_note"],
        "ask": "最近7天各型号订单齐套率如何？最低的是哪个型号？缺料集中在哪些料号？",
    },
    # ---------------- 全角色看板下钻扩展（均有真实数据支撑） ----------------
    # 制造域：OEE / 产出达成（production_output，窗口内真实记录）
    "oee": {
        "screen": "manufacturing", "label": "设备综合效率 OEE", "unit": "%",
        "target": 85.0, "higher_is_better": True,
        "definition": "OEE 按记录平均（production_output.oee 各记录均值）。",
        "table": "production_output", "date_expr": "record_date",
        # oee 列本身已是百分数，先 /100 归一为“分数”，再由引擎 ×100 还原为百分比
        "num_expr": "SUM(oee) / 100.0", "den_expr": "COUNT(*)",
        "group_cols": ["line_code", "product_model"],
        "dimension": "产线 · 型号", "entity": "line_model",
        "record_cols": ["record_date", "line_code", "product_model",
                        "planned_qty", "actual_qty", "oee", "stop_duration_min"],
        "ask": "最近7天各产线 OEE 如何？最低的是哪条线？",
    },
    "production_achieve": {
        "screen": "manufacturing", "label": "产出达成率", "unit": "%",
        "target": 95.0, "higher_is_better": True,
        "definition": "实际产出数 ÷ 计划产出数。",
        "table": "production_output", "date_expr": "record_date",
        "num_expr": "SUM(actual_qty)", "den_expr": "SUM(planned_qty)",
        "group_cols": ["line_code", "product_model"],
        "dimension": "产线 · 型号", "entity": "line_model",
        "record_cols": ["record_date", "line_code", "product_model",
                        "planned_qty", "actual_qty", "oee", "stop_duration_min"],
        "ask": "最近7天各产线产出达成率如何？最低的是哪条线？",
    },
    # 品质域：来料批次合格率（按供应商）/ 测试通过率（按测试类型）/ 标准三率符合率
    "incoming_pass_rate": {
        "screen": "quality", "label": "来料批次合格率", "unit": "%",
        "target": 90.0, "higher_is_better": True,
        "definition": "IQC 判定合格（pass）批次数 ÷ 窗口内来料批次总数（来料不良的反向口径）。",
        "table": "incoming_batch", "date_expr": "date(arrival_time)",
        "num_expr": "SUM(CASE WHEN inspect_status='pass' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["supplier"],
        "dimension": "供应商", "entity_cols": [["supplier", "supplier"]],
        "record_cols": ["batch_no", "part_no", "part_name", "supplier",
                        "arrival_time", "qty", "qualified_qty", "inspect_status"],
        "ask": "最近7天各供应商来料批次合格率如何？最低的是哪家？",
    },
    "test_pass_rate": {
        "screen": "quality", "label": "测试通过率", "unit": "%",
        "target": 95.0, "higher_is_better": True,
        "definition": "测试通过数 ÷ 测试总数（按测试类型分解）。",
        "table": "test_records", "date_expr": "test_date",
        "num_expr": "SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["test_type"],
        "dimension": "测试类型", "entity_cols": [["test_type", "test_type"]],
        "record_cols": ["test_date", "product_model", "batch_no",
                        "test_type", "test_item", "result", "tester"],
        "ask": "最近7天各测试类型通过率如何？最低的是哪类？",
    },
    "std_conformity": {
        "screen": "quality", "label": "标准三率符合率", "unit": "%",
        "target": _STD_CONFORMITY_TARGET, "higher_is_better": True,
        "definition": "作业标准符合项数 ÷ 审核项总数（符合率 = 完整率 × 完善率 × 遵守率）。",
        "table": "std_standard_audit", "date_expr": "audit_date",
        "num_expr": "SUM(audit_conform)", "den_expr": "SUM(audit_total)",
        "group_cols": ["product_model", "process_step"],
        "dimension": "型号 · 工序",
        "entity_cols": [["product_model", "model"], ["process_step", "pstep"]],
        "record_cols": ["audit_date", "product_model", "process_step", "standard_no",
                        "doc_present", "doc_complete", "audit_total", "audit_conform"],
        "ask": "最近7天各型号工序标准符合率如何？最低的是哪一项？",
    },
    "complaint_close_rate": {
        "screen": "quality", "label": "客诉闭环率", "unit": "%",
        "target": 80.0, "higher_is_better": True,
        "definition": "已关闭（closed）客诉单数 ÷ 客诉单总数（快照口径，不做时间窗口过滤）。",
        "table": "customer_complaint",
        "num_expr": "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["customer", "product_model"],
        "dimension": "客户 · 型号",
        "entity_cols": [["customer", "customer"], ["product_model", "model"]],
        "record_cols": ["complaint_no", "customer", "product_model", "batch_no",
                        "defect_desc", "status"],
        "ask": "各客户型号的客诉闭环率如何？哪些还没闭环？",
    },
    # 供应链 / 项目 / 协同（快照口径）
    "material_shortage_rate": {
        "screen": "orders", "label": "缺料项占比", "unit": "%",
        "target": 10.0, "higher_is_better": False,
        "definition": "在库量低于安全库存的物料行数 ÷ 物料行总数（快照口径）。",
        "table": "material_inventory",
        "num_expr": "SUM(CASE WHEN on_hand_qty < safety_stock THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["warehouse"],
        "dimension": "仓库", "entity_cols": [["warehouse", "warehouse"]],
        "record_cols": ["part_no", "part_name", "category", "warehouse",
                        "on_hand_qty", "reserved_qty", "safety_stock", "age_days"],
        "ask": "当前各仓库缺料项占比如何？缺料集中在哪些料号？",
    },
    "milestone_done_rate": {
        "screen": "orders", "label": "里程碑达成率", "unit": "%",
        "target": 80.0, "higher_is_better": True,
        "definition": "已完成（done）里程碑数 ÷ 里程碑总数（快照口径）。",
        "table": "project_milestone",
        "num_expr": "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["product_model"],
        "dimension": "型号", "entity": "model",
        "record_cols": ["product_model", "phase", "milestone", "status",
                        "owner", "planned_end", "actual_end", "risk_note"],
        "ask": "各型号项目里程碑达成率如何？哪些里程碑临期或延期？",
    },
    "task_backlog_rate": {
        "screen": "orders", "label": "待人工确认占比", "unit": "%",
        "target": 50.0, "higher_is_better": False,
        "definition": "待人工确认（waiting_hitl）工单数 ÷ 工单总数（快照口径）。",
        "table": "platform_workflow_task",
        "num_expr": "SUM(CASE WHEN status='waiting_hitl' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["wf_id"],
        "dimension": "流程", "entity_cols": [["wf_id", "wf"]],
        "record_cols": ["task_uid", "wf_id", "title", "status",
                        "priority", "assignees", "created_at"],
        "ask": "当前各流程待人工确认工单占比如何？哪些 SLA 临近？",
    },
    # ---- SPC 过程能力（screen=spc）----
    "spc_capability": {
        "screen": "spc", "label": "子组超差率", "unit": "%",
        "target": 5.0, "higher_is_better": False,
        "definition": "越限测量值条数 ÷ 测量条数（子组口径的过程越限比例，"
                      "与 Cpk 同源：Cpk 高则超差率低）。",
        "table": "spc_measurement", "date_expr": "date(measured_at)",
        "num_expr": "SUM(CASE WHEN (usl IS NOT NULL AND value > usl) "
                    "OR (lsl IS NOT NULL AND value < lsl) THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["characteristic"],
        "dimension": "特性", "entity_cols": [["characteristic", "characteristic"]],
        "record_cols": ["measured_at", "characteristic", "subgroup_no",
                        "seq", "value", "usl", "lsl", "source"],
        "ask": "各特性的过程超差率如何？哪个特性最需要加严？",
    },
    "spc_pareto": {
        "screen": "spc", "label": "缺陷类型不良率", "unit": "%",
        "target": 10.0, "higher_is_better": False,
        "definition": "各缺陷类型不良数 ÷ 受检总数（柏拉图关键少数取证）。",
        "table": "quality_defect", "date_expr": "date(inspect_time)",
        "num_expr": "SUM(defect_qty)", "den_expr": "SUM(total_qty)",
        "group_cols": ["defect_type"],
        "dimension": "不良类型", "entity_cols": [["defect_type", "defect_type"]],
        "record_cols": ["inspect_time", "batch_no", "product_model", "line_code",
                        "defect_type", "defect_qty", "total_qty"],
        "ask": "各缺陷类型不良率如何？最集中的不良模式是哪类？",
    },
}


# ---------- defect_type 下钻白名单（唯一事实源，替代散落的 str 判定） ----------
# 语义：只有登记在此的 ``(screen, kpi)`` 才接受 ``defect_type`` 参数，其它指标带该参数一律 400。
#   "pareto"：defect_type 触发「L1 累计不良柏拉图 → L2 按产线 · 型号分解」（build_pareto_drill）；
#   "entity"：defect_type 本身就是该指标声明的实体维度参数，走通用 L3 下钻。
# 新增指标若把 defect_type 写进 entity_cols，**必须**在此登记，否则模块加载期自检即报错。
DEFECT_TYPE_DRILLS: dict[tuple[str, str], str] = {
    ("quality", "defect_rate"): "pareto",
    ("spc", "spc_pareto"): "entity",
}


def _drill_meta(defn: dict, kpi_key: str) -> dict:
    """L2/L3 共用的指标元信息（口径与 L1 KPI 卡片一致）。"""
    return {"kpi": kpi_key, "label": defn["label"], "unit": defn["unit"],
            "target": defn["target"],
            "higher_is_better": defn["higher_is_better"],
            "definition": defn["definition"],
            "dimension": defn["dimension"], "ask": defn["ask"]}


# 实体维度定义：[[数据库列名, 查询参数名], ...]。
# 既有 4 种约定（line_model / wo_no / customer / model）保持不变；
# 新增指标可直接用 ``entity_cols`` 声明任意分组维度（如 supplier / test_type / warehouse）。
_ENTITY_KIND_COLS: dict[str, list[tuple[str, str]]] = {
    "line_model": [("line_code", "line"), ("product_model", "model")],
    "wo_no": [("wo_no", "wo")],
    "customer": [("customer", "customer"), ("customer_level", "level")],
    "model": [("product_model", "model")],
}


def _entity_cols(defn: dict) -> list[tuple[str, str]]:
    """实体维度列 → 参数名；显式 ``entity_cols`` 优先，否则按 ``entity`` 既有约定推导。"""
    explicit = defn.get("entity_cols")
    if explicit:
        return [(c[0], c[1]) for c in explicit]
    kind = defn.get("entity") or "model"
    return list(_ENTITY_KIND_COLS.get(kind, _ENTITY_KIND_COLS["model"]))


def entity_param_names(kpi_key: str) -> list[str]:
    """指标 L3 所需的查询参数名（供 ``api.py`` 通用解析下钻实体）。"""
    return [p for _, p in _entity_cols(DRILL_DEFS[kpi_key])]


def _assert_defect_type_whitelist() -> None:
    """加载期自检：``DEFECT_TYPE_DRILLS`` 与 ``DRILL_DEFS`` 必须双向一致（fail-fast）。

    校验两件事，任何一条不满足即抛错（把配置错误挡在启动前，不留到运行时）：
    1. 白名单里的每一项都指向真实存在的 ``(screen, kpi)``，且 kind 合法，
       ``kind="entity"`` 的还须确实把 defect_type 声明为实体参数；
    2. ``DRILL_DEFS`` 中凡将 defect_type 作为实体参数的指标，都必须登记进白名单
       —— 否则新指标会绕过白名单，脏参被静默吞掉。
    """
    valid_kinds = ("pareto", "entity")
    for (screen, kpi), kind in DEFECT_TYPE_DRILLS.items():
        defn = DRILL_DEFS.get(kpi)
        if defn is None or defn.get("screen") != screen:
            raise ValueError(
                f"defect_type 白名单项 {(screen, kpi)!r} 未在 DRILL_DEFS 注册或 screen 不符")
        if kind not in valid_kinds:
            raise ValueError(f"defect_type 白名单项 {(screen, kpi)!r} 的 kind {kind!r} 非法")
        if kind == "entity" and "defect_type" not in entity_param_names(kpi):
            raise ValueError(
                f"{kpi!r} 登记为 entity 下钻，但 DRILL_DEFS 未把 defect_type 列为实体参数")
    for kpi, defn in DRILL_DEFS.items():
        if "defect_type" in entity_param_names(kpi) and \
                (defn.get("screen"), kpi) not in DEFECT_TYPE_DRILLS:
            raise ValueError(
                f"{kpi!r} 把 defect_type 作为实体参数，却未登记进 DEFECT_TYPE_DRILLS")


_assert_defect_type_whitelist()


def _join_entity(vals: list) -> str:
    return " · ".join("—" if v in (None, "") else str(v) for v in vals)


def _drill_name(defn: dict, row: dict) -> str:
    """实体显示名（与 L1 TOP5 的 name_of 口径保持一致）。"""
    return _join_entity([row.get(c) for c, _ in _entity_cols(defn)])


def _entity_of_row(defn: dict, row: dict) -> dict:
    """分组行 → 下钻实体字典（供前端 L2 行点击直下 L3）。"""
    return {p: (row.get(c) or "") for c, p in _entity_cols(defn)}


def _entity_cond(defn: dict) -> str:
    """L3 实体过滤条件：固定常量串，取值一律命名绑定。"""
    return "AND " + " AND ".join(f"{c} = :{p}" for c, p in _entity_cols(defn))


def _entity_params(defn: dict, entity: dict) -> dict:
    return {p: (entity.get(p) or "") for _, p in _entity_cols(defn)}


def _entity_name(defn: dict, entity: dict) -> str:
    return _join_entity([entity.get(p) for _, p in _entity_cols(defn)])


# ---------------- 工段 / 工序 / 工位（三级看板细化） ----------------

# 工序 → 工段（work_report 无工段列，按工序静态映射；station_detail 直接取列）
_STEP_STAGE: dict[str, str] = {
    "锡膏印刷": "SMT段", "SPI": "SMT段", "贴片": "SMT段", "回流焊": "SMT段",
    "AOI": "SMT段", "插件": "DIP段", "波峰焊": "DIP段", "清洗": "DIP段",
    "组装": "组装段", "包装": "组装段", "ICT": "测试段", "功能测试": "测试段",
    "测试": "测试段", "老化": "测试段", "返修": "返修段",
}
_STAGE_ORDER = ["SMT段", "DIP段", "组装段", "测试段", "返修段", "其他"]
# 班次展示顺序（白班 → 夜班；未列出的排最后）
_SHIFT_ORDER = ["白班", "中班", "夜班"]

# 工位明细 / 过站记录展示列
_STATION_GROUP_COLS = ["process_stage", "process_step", "station_no",
                       "station_name", "equipment_no", "shift"]
_STATION_COLS = ["record_date", "process_stage", "process_step", "station_no",
                 "station_name", "equipment_no", "shift", "operator",
                 "cycle_time_s", "defect_mode", "num_value", "den_value"]


def _stage_rank(stage: str) -> int:
    return _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER)


def _station_num_expr(defn: dict) -> str:
    """station_detail 上的分子表达式（直通率与不良率同源：分子 = 分母 − 不良数）。"""
    return ("SUM(den_value) - SUM(num_value)" if defn.get("num_from_den")
            else "SUM(num_value)")


def _loss_of(defn: dict, num: float, den: float) -> float:
    """损失量（帕累托 / 瀑布用）：越高越好取丢失量，越低越好取不良量。"""
    return max(den - num, 0) if defn["higher_is_better"] else max(num, 0)


def _drill_where(defn: dict, entity: dict, base: dict,
                 step: Optional[str] = None,
                 station: Optional[str] = None) -> tuple[str, dict]:
    """实体 + 工序 + 工位过滤条件（常量串 + 命名绑定参数，绝不拼接用户输入）。

    ``base`` 为窗口参数（``start`` / ``end``），必须并入返回参数，否则 ``:start`` 无法绑定。
    """
    cond = _entity_cond(defn)
    params = dict(base)
    params.update(_entity_params(defn, entity))
    if step:
        cond += " AND process_step = :step"
        params["step"] = step
    if station:
        cond += " AND station_no = :station"
        params["station"] = station
    return cond, params


def _step_rows(registry, defn: dict, params: dict, cond: str,
               state: dict) -> list[dict]:
    """工序聚合行（含工段）：station_detail 明细表 / work_report 原生工序。"""
    if defn.get("stage_source") == "work_report":
        sql = ("SELECT process_step, SUM(reported) AS num, "
               "SUM(should_report) AS den, 0 AS dval FROM work_report "
               f"WHERE report_date BETWEEN :start AND :end {cond} "
               "GROUP BY process_step")
        return _rows(registry, sql, params, state)
    p = dict(params)
    p["m"] = defn["station_metric"]
    sql = (f"SELECT process_stage, process_step, {_station_num_expr(defn)} AS num, "
           "SUM(den_value) AS den, SUM(defect_value) AS dval FROM station_detail "
           f"WHERE metric_key = :m AND record_date BETWEEN :start AND :end {cond} "
           "GROUP BY process_stage, process_step")
    return _rows(registry, sql, p, state)


def _station_rows(registry, defn: dict, params: dict, cond: str,
                  state: dict) -> list[dict]:
    """工位聚合行（station_detail 专有；work_report 口径无工位层 → 空）。"""
    if defn.get("stage_source") != "station_detail":
        return []
    p = dict(params)
    p["m"] = defn["station_metric"]
    cols = ", ".join(_STATION_GROUP_COLS)
    sql = (f"SELECT {cols}, MIN(operator) AS operator, "
           "MIN(cycle_time_s) AS cycle_time_s, MIN(defect_mode) AS defect_mode, "
           f"{_station_num_expr(defn)} AS num, SUM(den_value) AS den, "
           "SUM(defect_value) AS dval FROM station_detail "
           f"WHERE metric_key = :m AND record_date BETWEEN :start AND :end {cond} "
           f"GROUP BY {cols}")
    return _rows(registry, sql, p, state)


def _pareto_rows(registry, defn: dict, params: dict, cond: str,
                 state: dict) -> list[dict]:
    """缺陷/停机模式聚合（帕累托）。"""
    if defn.get("stage_source") != "station_detail":
        return []
    p = dict(params)
    p["m"] = defn["station_metric"]
    sql = ("SELECT defect_mode AS name, SUM(defect_value) AS value "
           "FROM station_detail WHERE metric_key = :m "
           f"AND record_date BETWEEN :start AND :end {cond} "
           "AND defect_value > 0 GROUP BY defect_mode ORDER BY value DESC")
    return _rows(registry, sql, p, state)


def _build_step(defn: dict, r: dict, total_loss: float) -> dict:
    num, den = r.get("num") or 0, r.get("den") or 0
    val = _pct(num, den)
    loss = _loss_of(defn, num, den)
    step = r.get("process_step") or "—"
    return {
        "step": step,
        "stage": r.get("process_stage") or _STEP_STAGE.get(step, "其他"),
        "num": num, "den": den, "defect_value": r.get("dval") or 0,
        "value": val, "loss": loss,
        "loss_pp": (round(100.0 - val, 1) if defn["higher_is_better"]
                    else round(val, 1)),
        "share": round(100.0 * loss / total_loss, 1) if total_loss else 0.0,
        "status": _status(val, defn["target"], defn["higher_is_better"]),
    }


def _build_stages(defn: dict, step_rows: list[dict]) -> list[dict]:
    """工序行 → 工段分组（含各工序损失占比 / 损失 pp，供瀑布与帕累托）。"""
    total_loss = sum(_loss_of(defn, r.get("num") or 0, r.get("den") or 0)
                     for r in step_rows)
    grouped: dict[str, list[dict]] = {}
    for r in step_rows:
        s = _build_step(defn, r, total_loss)
        grouped.setdefault(s["stage"], []).append(s)
    stages: list[dict] = []
    for stage, ss in grouped.items():
        num = sum(x["num"] for x in ss)
        den = sum(x["den"] for x in ss)
        val = _pct(num, den)
        ss.sort(key=lambda x: -x["loss"])
        stages.append({
            "stage": stage, "num": num, "den": den, "value": val,
            "loss": sum(x["loss"] for x in ss),
            "status": _status(val, defn["target"], defn["higher_is_better"]),
            "steps": ss,
        })
    stages.sort(key=lambda x: _stage_rank(x["stage"]))
    return stages


def _build_stations(defn: dict, station_rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in station_rows:
        num, den = r.get("num") or 0, r.get("den") or 0
        if den <= 0:
            continue
        val = _pct(num, den)
        out.append({
            "stage": r.get("process_stage") or "",
            "step": r.get("process_step") or "",
            "station_no": r.get("station_no") or "",
            "station_name": r.get("station_name") or "",
            "equipment_no": r.get("equipment_no") or "",
            "shift": r.get("shift") or "",
            "operator": r.get("operator") or "",
            "cycle_time_s": r.get("cycle_time_s") or 0,
            "defect_mode": r.get("defect_mode") or "",
            "num": num, "den": den, "value": val,
            "status": _status(val, defn["target"], defn["higher_is_better"]),
        })
    out.sort(key=lambda x: (_stage_rank(x["stage"]), x["step"],
                            x["station_no"], x["shift"]))
    return out


def _pareto_cum(pareto_rows: list[dict]) -> list[dict]:
    """帕累托排序累积：``{name, value, share, cum_share}``。

    ``cum_share`` 是「关键少数」的判定依据（累计首达 80% 的前几类）。
    L1 累计不良柏拉图 / SPC 柏拉图 / L3 缺陷帕累托三处同源，口径一致。
    """
    total = sum((r.get("value") or 0) for r in pareto_rows)
    out: list[dict] = []
    cum = 0.0
    for r in pareto_rows:
        val = r.get("value") or 0
        if val <= 0:
            continue
        share = round(100.0 * val / total, 1) if total else 0.0
        cum = round(cum + share, 1)
        out.append({"name": r.get("name") or "—", "value": val,
                    "share": share, "cum_share": cum})
    return out


def _vital_few(pareto: list[dict], threshold: float = 80.0) -> list[str]:
    """关键少数：累计占比首达 ``threshold`` 的项名（含该项）。"""
    names: list[str] = []
    for p in pareto:
        names.append(p["name"])
        if (p.get("cum_share") or 0) >= threshold:
            break
    return names


def _entity_of(defn: dict, entity: dict) -> dict:
    out: dict[str, str] = {"name": _entity_name(defn, entity)}
    for _, p in _entity_cols(defn):
        out[p] = entity.get(p) or ""
    return out


def _days_of(daily: list[dict], defn: dict) -> tuple[list[dict], int, int]:
    """逐日序列（窗口 7 天，缺日 empty）+ 汇总分子/分母。"""
    by_date = {r.get("d"): r for r in daily}
    days: list[dict] = []
    tot_num = tot_den = 0
    for d in _WINDOW_DATES:
        r = by_date.get(d)
        if r is None:
            days.append({"date": d, "numerator": None, "denominator": None,
                         "value": None, "status": "empty"})
            continue
        num, den = r.get("num") or 0, r.get("den") or 0
        tot_num += num
        tot_den += den
        val = _pct(num, den)
        days.append({"date": d, "numerator": num, "denominator": den,
                     "value": val,
                     "status": _status(val, defn["target"],
                                       defn["higher_is_better"])})
    return days, tot_num, tot_den


def _station_cols() -> list[dict]:
    return [{"key": c, "label": _COL_CN.get(c, c)} for c in _STATION_COLS]


def _l2_items(defn: dict, rows: list[dict], daily: list[dict]) -> list[dict]:
    """分组行 → L2 明细项（实体 / 状态 / 分子分母 / 7 日趋势 / 排名）。

    通用 L2（``build_drill``）与「不良类型下钻」（``build_pareto_drill``）
    共用同一构造器，保证两条入口的行结构与排序口径完全一致。
    """
    trends: dict[str, list] = {}
    if daily:
        bucket: dict[str, list[dict]] = {}
        for r in daily:
            bucket.setdefault(_drill_name(defn, r), []).append(r)
        trends = {name: _trend(sub) for name, sub in bucket.items()}

    items: list[dict] = []
    for r in rows:
        num, den = r.get("num") or 0, r.get("den") or 0
        if den <= 0:
            continue
        val = _pct(num, den)
        name = _drill_name(defn, r)
        items.append({
            "name": name,
            "entity": _entity_of_row(defn, r),
            "line": r.get("line_code") or "",
            "model": r.get("product_model") or "",
            "wo": r.get("wo_no") or "",
            "value": val,
            "status": _status(val, defn["target"], defn["higher_is_better"]),
            "numerator": num, "denominator": den,
            "trend": trends.get(name, []),
        })
    items.sort(key=lambda x: x["value"], reverse=not defn["higher_is_better"])
    for i, it in enumerate(items, 1):
        it["rank"] = i
    return items


def build_pareto_drill(registry, screen: str, kpi_key: str,
                       defect_type: str) -> dict:
    """L1 累计不良柏拉图 → 不良类型下钻（L2）：该 defect_type 按产线 · 型号分解。

    行结构与 ``build_drill`` 的 L2 完全同构（``_l2_items`` 同一构造器），
    额外回传 ``defect_type`` 供面包屑/标题标注，并置 ``terminal=True``
    ——这是「不良类型」维度的终点视图，不再继续下钻实体（实体下钻走
    L2 产线·型号入口，避免两种维度在同一层级混用）。
    """
    defn = DRILL_DEFS[kpi_key]
    state: dict[str, Any] = {"reasons": []}
    params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"],
              "defect_type": defect_type}
    group = ", ".join(defn["group_cols"])
    date_expr = defn["date_expr"]
    base_where = (f"{date_expr} BETWEEN :start AND :end "
                  "AND defect_type = :defect_type")
    rows = _rows(registry,
                 f"SELECT {group}, {defn['num_expr']} AS num, "
                 f"{defn['den_expr']} AS den FROM {defn['table']} "
                 f"WHERE {base_where} GROUP BY {group}", params, state)
    daily = _rows(registry,
                  f"SELECT {group}, {date_expr} AS d, {defn['num_expr']} AS num, "
                  f"{defn['den_expr']} AS den FROM {defn['table']} "
                  f"WHERE {base_where} GROUP BY {group}, {date_expr}",
                  params, state)

    data = _envelope(screen, state)
    data.update(_drill_meta(defn, kpi_key))
    data.update({
        "level": 2, "rows": _l2_items(defn, rows, daily),
        "kind": "pareto", "terminal": True, "defect_type": defect_type,
        "pareto_label": defn.get("pareto_label") or "不良类型",
    })
    return data


def build_drill(registry, screen: str, kpi_key: str,
                entity: Optional[dict] = None, step: Optional[str] = None,
                station: Optional[str] = None) -> dict:
    """看板下钻聚合。

    ``entity`` 为空 = L2（按维度全量分解）；非空 = L3（单实体 + 工段/工序/工位分解）；
    再带 ``step`` = L4（该工序的工位明细）；再带 ``station`` = L5（该工位逐日 + 过站记录）。

    纪律同 L1 聚合：只读 + 命名绑定参数 + 异常兜底（记 degraded_reason，绝不抛出）。
    """
    defn = DRILL_DEFS[kpi_key]
    state: dict[str, Any] = {"reasons": []}
    params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"]}
    group = ", ".join(defn["group_cols"])
    date_expr = defn.get("date_expr")
    # 无 date_expr = 快照类指标（库存/里程碑/工单等），不做时间窗口过滤，逐日趋势留空
    base_where = (f"{date_expr} BETWEEN :start AND :end" if date_expr else "1 = 1")

    if not entity:
        rows = _rows(registry,
                     f"SELECT {group}, {defn['num_expr']} AS num, "
                     f"{defn['den_expr']} AS den FROM {defn['table']} "
                     f"WHERE {base_where} GROUP BY {group}", params, state)
        daily: list[dict] = []
        if date_expr:
            daily = _rows(registry,
                          f"SELECT {group}, {date_expr} AS d, "
                          f"{defn['num_expr']} AS num, {defn['den_expr']} AS den "
                          f"FROM {defn['table']} WHERE {base_where} "
                          f"GROUP BY {group}, {date_expr}", params, state)

        data = _envelope(screen, state)
        data.update(_drill_meta(defn, kpi_key))
        data.update({"level": 2, "rows": _l2_items(defn, rows, daily)})
        return data

    cond, p3 = _drill_where(defn, entity, params)
    records = _rows(registry,
                    f"SELECT {', '.join(defn['record_cols'])} "
                    f"FROM {defn['table']} WHERE {base_where} {cond} LIMIT 5",
                    p3, state)
    if date_expr:
        daily = _rows(registry,
                      f"SELECT {date_expr} AS d, {defn['num_expr']} AS num, "
                      f"{defn['den_expr']} AS den FROM {defn['table']} "
                      f"WHERE {base_where} {cond} GROUP BY {date_expr}",
                      p3, state)
        days, tot_num, tot_den = _days_of(daily, defn)
    else:
        # 快照类指标无逐日序列：直接对单实体做一次汇总
        days = []
        agg = _rows(registry,
                    f"SELECT {defn['num_expr']} AS num, {defn['den_expr']} AS den "
                    f"FROM {defn['table']} WHERE {base_where} {cond}", p3, state)
        tot_num = (agg[0].get("num") if agg else 0) or 0
        tot_den = (agg[0].get("den") if agg else 0) or 0
    value = _pct(tot_num, tot_den)

    data = _envelope(screen, state)
    data.update(_drill_meta(defn, kpi_key))
    data.update({
        "level": 3, "entity": _entity_of(defn, entity),
        "value": value, "numerator": tot_num, "denominator": tot_den,
        "status": _status(value, defn["target"], defn["higher_is_better"]),
        "delta": round(value - defn["target"], 1),
        "daily": days, "records": records,
        "columns": [{"key": c, "label": _COL_CN.get(c, c)}
                    for c in defn["record_cols"]],
    })

    src = defn.get("stage_source")
    if not src:
        return data

    # ---- L3 增补：工段/工序分解 + 工位明细 + 缺陷帕累托 ----
    step_rows = _step_rows(registry, defn, p3, cond, state)
    station_rows = _station_rows(registry, defn, p3, cond, state)
    data.update({
        "stages": _build_stages(defn, step_rows),
        "stations": _build_stations(defn, station_rows),
        "pareto": _pareto_cum(_pareto_rows(registry, defn, p3, cond, state)),
        "pareto_label": defn.get("pareto_label") or "缺陷模式",
    })

    if not step:
        return data

    # ---- L4 / L5：下钻到单工序 / 单工位（逐日 + 过站记录改由明细层提供）----
    cond4, p4 = _drill_where(defn, entity, params, step=step, station=station)
    if defn.get("stage_source") == "work_report":
        date_col, tbl, r_cols = "report_date", "work_report", defn["record_cols"]
        mcond, d_num, d_den = "", "SUM(reported) AS num", "SUM(should_report) AS den"
    else:
        date_col, tbl, r_cols = "record_date", "station_detail", _STATION_COLS
        p4["m"] = defn["station_metric"]
        # 明细层必须带 metric_key 过滤，否则会把六个指标的行混在一起求和
        mcond = "metric_key = :m AND "
        d_num, d_den = f"{_station_num_expr(defn)} AS num", "SUM(den_value) AS den"
    d_rows = _rows(registry,
                   f"SELECT {date_col} AS d, {d_num}, {d_den} FROM {tbl} "
                   f"WHERE {mcond}{date_col} BETWEEN :start AND :end {cond4} "
                   f"GROUP BY {date_col}", p4, state)
    r_rows = _rows(registry,
                   f"SELECT {', '.join(r_cols)} FROM {tbl} "
                   f"WHERE {mcond}{date_col} BETWEEN :start AND :end {cond4} "
                   f"ORDER BY {date_col} DESC LIMIT 20", p4, state)
    days, num, den = _days_of(d_rows, defn)
    value = _pct(num, den)
    # L5 附加：该工位的班次分解（L3 工位行按班次展开，此处汇总供对照）
    shifts: list[dict] = []
    if station and tbl == "station_detail":
        sh_rows = _rows(registry,
                        f"SELECT shift, {_station_num_expr(defn)} AS num, "
                        f"SUM(den_value) AS den FROM {tbl} "
                        f"WHERE {mcond}{date_col} BETWEEN :start AND :end {cond4} "
                        "GROUP BY shift", p4, state)
        for r in sh_rows:
            n, d = r.get("num") or 0, r.get("den") or 0
            if d <= 0:
                continue
            v = _pct(n, d)
            shifts.append({"shift": r.get("shift") or "—", "num": n, "den": d,
                           "value": v,
                           "status": _status(v, defn["target"],
                                             defn["higher_is_better"])})
        shifts.sort(key=lambda x: (_SHIFT_ORDER.index(x["shift"])
                                   if x["shift"] in _SHIFT_ORDER else 9))
    stations = [s for s in data["stations"]
                if s["step"] == step and (not station or s["station_no"] == station)]
    step_def = next((s for st in data["stages"] for s in st["steps"]
                     if s["step"] == step), None)
    station_def = (next((s for s in stations if s["station_no"] == station), None)
                   if station else None)
    stage_name = (step_def or {}).get("stage", "")
    parent = {"stage": stage_name, "step": step, "station": station or "",
              "name": " · ".join(x for x in (stage_name, step, station or "") if x)}
    data.update({
        "level": 5 if station else 4,
        "parent": parent, "step": step_def, "station": station_def,
        "stations": stations, "shifts": shifts,
        "daily": days, "records": r_rows,
        "columns": (_station_cols() if tbl == "station_detail"
                    else [{"key": c, "label": _COL_CN.get(c, c)}
                          for c in r_cols]),
        "value": value, "numerator": num, "denominator": den,
        "status": _status(value, defn["target"], defn["higher_is_better"]),
        "delta": round(value - defn["target"], 1),
    })
    return data
