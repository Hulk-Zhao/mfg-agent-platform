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
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from ..core.dimensions import (
    MFG_PRODUCT_CATEGORIES,
    MFG_STAGES,
    PRODUCT_CATEGORIES,
)
from ..core.spc import compute_capability

logger = logging.getLogger(__name__)

# 演示锚点「今天」（沿用 10-15；红线日，当日口径的右端点）
DEMO_TODAY = "2026-10-15"

# 演示窗口：最近 7 天（10-08 ~ 10-14），天然避开 10-15 红线日
DEMO_WINDOW = {
    "start": "2026-10-08",
    "end": "2026-10-14",
    "days": 7,
    "label": "最近 7 天（演示窗口）",
}

# 制造/品质看板可切换的三档时间口径（键 → 起止 + 中文标签）。
# 默认键 "window" = 既有「最近 7 天」，保证既有接口行为与全部既有断言零变化。
WINDOWS: dict[str, dict[str, Any]] = {
    "window": {"key": "window", "start": "2026-10-08", "end": "2026-10-14",
               "label": "最近7天"},
    "day": {"key": "day", "start": DEMO_TODAY, "end": DEMO_TODAY,
            "label": "当日"},
    "month": {"key": "month", "start": "2026-10-01", "end": DEMO_TODAY,
              "label": "当月"},
}

# 默认 7 天日期序列（订单/SPC 看板仍按此口径；制造/品质看板按所选窗口现算）
_WINDOW_DATES = ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11",
                 "2026-10-12", "2026-10-13", "2026-10-14"]

_WINDOW_ALL = {"key": "all", "start": None, "end": None, "label": "全部（不限窗口）"}


def resolve_window(key: Optional[str]) -> dict[str, Any]:
    """窗口键 → 起止 + 标签。``"all"`` = 不限窗口（仅柏拉图类型下钻用）；缺省/非法回落 7 天。"""
    if key == "all":
        return dict(_WINDOW_ALL)
    return dict(WINDOWS.get(key or "window") or WINDOWS["window"])


def _window_dates(win: dict[str, Any]) -> list[str]:
    """窗口起止 → 升序日期序列（含端点）；无起止（``all``）返回空列表。"""
    start, end = win.get("start"), win.get("end")
    if not start or not end:
        return []
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return list(_WINDOW_DATES)
    out: list[str] = []
    d = d0
    while d <= d1:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _ask(text: str, label: Optional[str]) -> str:
    """协同问数文案随所选口径自适应：把「最近7天」前缀换成窗口标签。

    仅对两个非默认档位（``当日`` / ``当月``）改写；``all``（不限窗口）与订单/SPC
    看板的固定 7 天口径保持原文案。
    """
    if label in ("当日", "当月"):
        return text.replace("最近7天", label, 1)
    return text


# 标准三率目标线（完整率 100% / 完善率 98% / 遵守率 90%）→ 符合率目标 = 三者乘积
_STD_TARGETS = {"completeness_rate": 100.0, "perfection_rate": 98.0,
                "compliance_rate": 90.0}
_STD_CONFORMITY_TARGET = round(
    _STD_TARGETS["completeness_rate"] * _STD_TARGETS["perfection_rate"]
    * _STD_TARGETS["compliance_rate"] / 10000.0, 1)


# ---------------------------------------------------------------- 基础工具

# 大窗口聚合的显式行数上限：registry 默认 ``row_cap=200`` 会静默截断分组基数较大的
# 聚合（如「当月」540 张工单 × 15 天的 L2 逐日趋势 ≈ 8100 行），按需在各调用点显式抬高。
_AGG_ROW_CAP = 12000


def _rows(registry, sql: str, params: dict, state: dict,
          cap: Optional[int] = None) -> list[dict]:
    """执行只读聚合 SQL；异常兜底返回空列表并记录降级原因。

    ``cap`` 用于显式抬高行数上限（缺省沿用 registry 默认 ``row_cap``）。
    """
    try:
        if cap is None:
            return registry.execute_readonly_sql(sql, params)
        return registry.execute_readonly_sql(sql, params, row_cap=cap)
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


def _trend(rows: list[dict], dates: Optional[list[str]] = None) -> list:
    """按日序列化为数值数组（缺日回填上一个可用值；全员缺则回填首值）。

    ``dates`` 为窗口日期序列（当日=1 点 / 最近7天=7 点 / 当月=15 点），
    缺省沿用 7 天基线，保证既有调用行为零变化。
    """
    seq = dates if dates is not None else _WINDOW_DATES
    by_date: dict[str, float] = {}
    for r in rows:
        by_date[r.get("d")] = _pct(r.get("num") or 0, r.get("den") or 0)
    out: list[Optional[float]] = []
    last: Optional[float] = None
    for d in seq:
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


def _envelope(screen: str, state: dict,
              window: Optional[dict[str, Any]] = None) -> dict:
    return {
        "screen": screen,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "sqlite:dev",
        "degraded": bool(state["reasons"]),
        "degraded_reason": "；".join(state["reasons"][:3]),
        "window": dict(window or WINDOWS["window"]),
    }


def _line_model(r: dict) -> str:
    return f"{r.get('line_code') or '—'} · {r.get('product_model') or '—'}"


# ---------------------------------------------------------------- 制造看板

_MFG_NOTES = [
    "稼动率 = 实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
    "报废率 = 报废数 ÷ 总产出数；返工品既不计入良品也不计入报废。",
    "报工完成率 = 已报工工序数 ÷ 应报工工序数。",
    "测试直通率 = 首次测试通过数 ÷ 测试总数（重测才过不计入）。",
    "板级「产品分类 / 工段」筛选为演示口径静态映射（制造四表无这两列）："
    "工段按产线前缀归属（SMT-* → SMT、DIP-* → PCM、PACK-* → PACK、ASSY-* → 整机），"
    "产品分类按型号归属（消费电子 A/E/G、智能电源 B/F/H、储能电源 C/I/J、"
    "车载 D/K/L，共 12 型号 4 分类）；"
    "卡片值即所选范围的汇总，TOP5 与下钻同口径。",
    "测试直通率取自测试记录（无产线维度），工段筛选对它不适用："
    "筛选生效时该卡保持全厂口径并标注「工段筛选不适用」。",
    "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转："
    "越高越好取最低 5、越低越好取最高 5。",
]


def build_manufacturing_dashboard(registry, window: str = "window", *,
                                  stage: Optional[str] = None,
                                  product_category: Optional[str] = None) -> dict:
    """制造看板聚合。

    ``stage``（工段：SMT/PCM/PACK/整机）/ ``product_category``（产品分类：消费电子 /
    智能电源 / 储能电源 / 车载）为板级筛选，缺省 = 全部（取值受 ``MFG_STAGES`` /
    ``MFG_PRODUCT_CATEGORIES`` 约束，接口层 fail-fast）。四个指标的 KPI 卡、TOP5 排行、
    趋势线均随筛选重算——**卡片值即所选范围的汇总**；测试直通率无产线维度，工段筛选
    对它不适用并回传 ``scope_note``（不伪造不存在的分解）。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    dates = _window_dates(win_def)
    params = {"start": win_def["start"], "end": win_def["end"]}
    win = "record_date BETWEEN :start AND :end"

    # 板级筛选：SQL 仍是常量串 + 命名绑定参数（取值取常量映射，绝不拼接用户输入）。
    scope_cn = _scope_cn(stage, product_category)
    line_frag, line_extra = _mfg_scope(stage, product_category)
    test_frag, test_extra = _mfg_scope(stage, product_category, has_line=False)
    p_line = dict(params, **line_extra)
    p_test = dict(params, **test_extra)

    def _scoped_ask(plain: str, scoped: str) -> str:
        """问数文案：无筛选沿用原窗口口径；有筛选时显式前置「窗口 · 范围」。"""
        if scope_cn:
            return f"{win_def['label']} · {scope_cn} {scoped}"
        return _ask(plain, win_def["label"])

    # 稼动率
    av = _rows(registry,
               "SELECT line_code, product_model, SUM(actual_run_min) AS num, "
               "SUM(planned_run_min) AS den FROM equipment_run_time "
               f"WHERE {win}{line_frag} GROUP BY line_code, product_model",
               p_line, state)
    av_t = _rows(registry,
                 "SELECT record_date AS d, SUM(actual_run_min) AS num, "
                 "SUM(planned_run_min) AS den FROM equipment_run_time "
                 f"WHERE {win}{line_frag} GROUP BY record_date", p_line, state)

    # 报废率
    sp = _rows(registry,
               "SELECT line_code, product_model, SUM(scrap_qty) AS num, "
               "SUM(output_qty) AS den FROM scrap_record "
               f"WHERE {win}{line_frag} GROUP BY line_code, product_model",
               p_line, state)
    sp_t = _rows(registry,
                 "SELECT record_date AS d, SUM(scrap_qty) AS num, "
                 "SUM(output_qty) AS den FROM scrap_record "
                 f"WHERE {win}{line_frag} GROUP BY record_date", p_line, state)

    # 报工完成率（按工单分组，万行数据集下「当月」可达 500+ 张工单 → 需抬高行数上限）
    wr = _rows(registry,
               "SELECT wo_no, line_code, product_model, SUM(reported) AS num, "
               "SUM(should_report) AS den FROM work_report "
               "WHERE report_date BETWEEN :start AND :end"
               f"{line_frag} GROUP BY wo_no, line_code, product_model", p_line, state,
               cap=_AGG_ROW_CAP)
    wr_t = _rows(registry,
                 "SELECT report_date AS d, SUM(reported) AS num, "
                 "SUM(should_report) AS den FROM work_report "
                 "WHERE report_date BETWEEN :start AND :end"
                 f"{line_frag} GROUP BY report_date", p_line, state)

    # 测试直通率（测试记录无产线维度 → 工段筛选不适用）
    tr = _rows(registry,
               "SELECT product_model, "
               "SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END) AS num, "
               "COUNT(*) AS den FROM test_records "
               "WHERE test_date BETWEEN :start AND :end"
               f"{test_frag} GROUP BY product_model", p_test, state)
    tr_t = _rows(registry,
                 "SELECT test_date AS d, "
                 "SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END) AS num, "
                 "COUNT(*) AS den FROM test_records "
                 "WHERE test_date BETWEEN :start AND :end"
                 f"{test_frag} GROUP BY test_date", p_test, state)

    kpis = [
        _kpi("availability", "稼动率", _sum(av, "num"), _sum(av, "den"), 90.0,
             True, "实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
             _scoped_ask("最近7天各产线稼动率如何？最低的是哪条线？",
                         "各产线稼动率如何？最低的是哪条线？"),
             _trend(av_t, dates)),
        _kpi("scrap_rate", "报废率", _sum(sp, "num"), _sum(sp, "den"), 2.0,
             False, "报废数 ÷ 总产出数（不可返修的判废比例）。",
             _scoped_ask("最近7天各产线报废率如何？最高的是哪条线？",
                         "各产线报废率如何？最高的是哪条线？"),
             _trend(sp_t, dates)),
        _kpi("report_rate", "报工完成率", _sum(wr, "num"), _sum(wr, "den"), 95.0,
             True, "已报工工序数 ÷ 应报工工序数。",
             _scoped_ask("最近7天哪些工单报工完成率偏低？",
                         "哪些工单报工完成率偏低？"),
             _trend(wr_t, dates)),
        _kpi("test_fpy", "测试直通率", _sum(tr, "num"), _sum(tr, "den"), 98.0,
             True, "首次测试通过数 ÷ 测试总数（重测才过不计入）。",
             _scoped_ask("最近7天各型号测试直通率如何？",
                         "各型号测试直通率如何？"),
             _trend(tr_t, dates)),
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

    # 筛选生效时的范围标注：① 无产线维度的指标（工段不适用）；
    # ② 所选范围在该指标上没有任何记录（0 值不是「达标」，必须如实说明）
    if scope_cn:
        for k in kpis:
            if k["key"] in MFG_STAGE_FREE_KPIS and stage:
                k["scope_note"] = "工段筛选不适用（测试记录无产线维度）"
            elif not k["denominator"]:
                k["scope_note"] = "所选筛选范围内无记录（0 值不代表达标）"

    data = _envelope("manufacturing", state, win_def)
    data.update({"kpis": kpis, "rankings": rankings, "notes": _MFG_NOTES,
                 # 板级筛选回显（供前端筛选条 / 报告溯源；未筛选 = 空串）
                 "filter": {"stage": stage or "",
                            "product_category": product_category or ""},
                 # 制造侧「原因柏拉图」两套同构事实表（与品质 pareto_fact 结构完全一致）：
                 #   停机原因（equipment_run_time.stop_reason × 停机分钟）/
                 #   报废原因（scrap_record.scrap_reason × 报废数）；前端本地切片零往返。
                 "pareto_threshold": 80.0,
                 "stop_pareto_fact": _mfg_pareto_fact(
                     registry, state, table="equipment_run_time",
                     type_col="stop_reason", qty_expr="SUM(stop_min)",
                     date_col="record_date"),
                 "scrap_pareto_fact": _mfg_pareto_fact(
                     registry, state, table="scrap_record",
                     type_col="scrap_reason", qty_expr="SUM(scrap_qty)",
                     date_col="record_date")})
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
    "标准三率按两级看板呈现：1 级 = 4 个工段（SMT / PCM / PACK / 整机），"
    "2 级 = 各工段下的班组；一行审核记录 = 某班组某天对某道工序标准的审核，"
    "工段与班组均用同一套三率口径，加权求和即回整体（分项与整体不会对不上）。",
    "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转："
    "越高越好取最低 5、越低越好取最高 5。",
    "累计不良柏拉图：窗口内按不良类型降序累积，关键少数 = 累计占比首达 80% 的类型"
    "（与 SPC 柏拉图 spc.pareto 同源口径）；点击类型可下钻其产线 · 型号分解。",
    "板级「工段 / 缺陷类别」筛选只作用于同源的 quality_defect 指标"
    "（不良率 / 直通率及其 TOP5）：卡片值即所选范围的汇总；"
    "来料批次合格率（进料端）/ 客诉闭环率（客户端）在小数据源上无该维度，"
    "仍按板级口径展示并标注「筛选不适用」。",
    "来料批次合格率 TOP10（进料端）：IQC 判定合格（pass）批次数 ÷ 来料批次总数，"
    "按供应商取最需关注的最低 10 名；**固定全量口径，不受统计口径（日/周/月）"
    "与工段 / 类别 / 柏拉图筛选影响**。点行看该供应商 2 级看板的详细 IQC 检验数据"
    "（料号 / 到货时间 / 到货数 / 合格数 / 检验结论）。",
    "来料上线不良率 TOP10（上线端）：来料上线后发现的不良数 ÷ 上线投入数，"
    "衡量来料对产线的实际冲击；同样为固定全量口径、不受任何筛选影响。"
    "2 级看板列出**全部供应商**（不只 TOP10），点行看上线不良明细"
    "（产线 / 型号 / 不良模式 / 处置结论）。",
]


# 不良柏拉图事实表覆盖范围（近 2 个月：8/1 ~ 10/15），独立于板级窗口
PARETO_RANGE = {"start": "2026-08-01", "end": "2026-10-15"}

# 产品分类维度的固定顺序 → 见 ``core/dimensions.py``（唯一事实源，本模块顶部已导入）

# 品质看板「工段 / 缺陷类别」板级筛选白名单（接口按此白名单 fail-fast，非法值 → 400）：
#   工段取值与制造看板 / SPC **同一份事实源**（``core/dimensions.py`` 的 ``MFG_STAGES``）
#   —— 三个看板的工段是同一套产线口径，不各写一份而漂移；
#   缺陷类别是品质看板**独有维度**（非工段/产品分类），故仍在本模块本地定义。
QUALITY_STAGES: tuple[str, ...] = MFG_STAGES
QUALITY_CATEGORIES = ("性能不良", "外观不良")
# 无「工段 / 缺陷类别」维度的品质指标（进料端 IQC / 客户端客诉）：筛选生效时保持板级口径
QUALITY_SCOPE_FREE_KPIS = ("incoming_pass_rate", "complaint_close_rate")


# ---------------- 制造看板板级「工段 / 产品分类」筛选（演示口径 · 静态映射） ----------------
# 制造四表（equipment_run_time / scrap_record / work_report / test_records）与工位明细
# 都没有 process_stage / product_category 两列，这里用**常量映射**把既有列折到筛选维度：
#   工段：按产线前缀归属（SMT-* → SMT、DIP-* → PCM、PACK-* → PACK、ASSY-* → 整机）
#   产品分类：按型号归属（A/E → 消费电子、B/F → 智能电源、C → 储能电源、D → 车载）
# 映射即代码：取值全部来自下面的常量字典（**不是用户输入**），SQL 仍是常量串 + 命名绑定参数。
# 工段 / 产品分类白名单本身 → 见 ``core/dimensions.py``（唯一事实源，本模块顶部已导入并再导出，
# 保持 ``dashboard.MFG_STAGES`` / ``dashboard.MFG_PRODUCT_CATEGORIES`` 既有引用零改动）。
# 工段 → 产线前缀（LIKE 模式；整机映射 ASSY-*，制造四表当前无该线体 → 该筛选如实为空）
_MFG_STAGE_LINE_LIKE: dict[str, str] = {
    "SMT": "SMT-%", "PCM": "DIP-%", "PACK": "PACK-%", "整机": "ASSY-%",
}
# 产品分类 → 型号集合（每类 3 个型号，共 12 个；SQL 占位按实际型号数动态生成）
_MFG_CAT_MODELS: dict[str, tuple[str, ...]] = {
    "消费电子": ("A型号", "E型号", "G型号"),
    "智能电源": ("B型号", "F型号", "H型号"),
    "储能电源": ("C型号", "I型号", "J型号"),
    "车载": ("D型号", "K型号", "L型号"),
}
# 无产线维度的制造指标（筛选生效时工段不适用，卡片保持该口径并标注）
MFG_STAGE_FREE_KPIS = ("test_fpy",)


def _scope_cn(stage: Optional[str], product_category: Optional[str]) -> str:
    """板级筛选的中文范围标签（缺省 = 空串，表示未筛选）。"""
    bits = []
    if product_category:
        bits.append(f"产品分类 {product_category}")
    if stage:
        bits.append(f"工段 {stage}")
    return " · ".join(bits)


def _mfg_scope(stage: Optional[str], product_category: Optional[str], *,
               has_line: bool = True,
               line_col: str = "line_code",
               model_col: str = "product_model") -> tuple[str, dict[str, Any]]:
    """制造板级筛选 → （常量 WHERE 片段, 命名绑定参数）。

    ``stage`` / ``product_category`` 已在接口层按白名单裁定（非法值 400），此处只做
    「白名单键 → 常量映射值」的取值，**绝不出现用户字符串进入 SQL**。
    ``has_line=False``（test_records 无产线列）时跳过工段条件。
    """
    frag, extra = "", {}
    if stage and has_line:
        frag += f" AND {line_col} LIKE :f_stage"
        extra["f_stage"] = _MFG_STAGE_LINE_LIKE[stage]
    if product_category:
        models = _MFG_CAT_MODELS[product_category]
        # 占位数量随型号集合长度动态生成（每类型号数不再是 1~2，见 _MFG_CAT_MODELS）
        placeholders = [f":f_m{i}" for i in range(len(models))]
        frag += f" AND {model_col} IN ({', '.join(placeholders)})"
        for i, m in enumerate(models):
            extra[f"f_m{i}"] = m
    return frag, extra


def _drill_scope(defn: dict, stage: Optional[str],
                 product_category: Optional[str],
                 params: dict) -> tuple[str, dict[str, Any]]:
    """下钻用板级筛选 → （常量片段, 合并后的绑定参数）。

    只对在 ``DRILL_DEFS`` 里声明了 ``scope`` 的指标生效（制造看板四指标）；
    未声明 scope 的指标原样返回空片段 + 原参数 → 行为与改动前逐字段一致。
    """
    sc = defn.get("scope")
    if not sc or not (stage or product_category):
        return "", dict(params)
    frag, extra = _mfg_scope(stage, product_category,
                             has_line=bool(sc.get("stage_col")),
                             line_col=sc.get("stage_col") or "line_code",
                             model_col=sc.get("pcat_col") or "product_model")
    return frag, {**params, **extra}


def _quality_scope_cn(stage: Optional[str], category: Optional[str]) -> str:
    """品质板级「工段 / 类别」筛选的中文范围标签（缺省 = 空串，表示未筛选）。"""
    bits = []
    if stage:
        bits.append(f"工段 {stage}")
    if category:
        bits.append(f"类别 {category}")
    return " · ".join(bits)


def _exact_col_filter(col: str, param: str, value: str) -> tuple[str, dict[str, Any]]:
    """列精确匹配 → （常量 SQL 片段, 命名绑定参数）。

    「列精确匹配」形态的唯一实现内核（``q_scope`` 品质工段/类别 与 ``spc_scope``
    SPC 工段/产品分类 共用）。``col`` 是**代码内声明的常量列名**（绝不来自用户输入），
    ``value`` 已在接口层按白名单裁定 → **用户字符串绝不进入 SQL**。
    """
    if not value:
        return "", {}
    return f" AND {col} = :{param}", {param: value}


def _quality_scope(defn: dict, stage: Optional[str], category: Optional[str],
                   params: dict) -> tuple[str, dict[str, Any]]:
    """下钻用品质板级筛选 → （常量片段, 合并后的绑定参数）。

    只对在 ``DRILL_DEFS`` 里声明了 ``q_scope`` 的指标（不良率 / 直通率，与 L1 卡片
    同源同口径）生效：工段 = ``process_stage`` 精确匹配、类别 = ``defect_category``；
    未声明的品质指标原样返回空片段 → 行为与改动前逐字段一致。
    列名取自声明的常量，取值已在接口层按 ``QUALITY_STAGES`` / ``QUALITY_CATEGORIES``
    白名单裁定，**不出现用户字符串进入 SQL**。
    """
    qs = defn.get("q_scope")
    if not qs or not (stage or category):
        return "", dict(params)
    frag, extra = "", {}
    if stage:
        f, e = _exact_col_filter(qs["stage_col"], "f_stage", stage)
        frag += f
        extra.update(e)
    if category:
        f, e = _exact_col_filter(qs["category_col"], "f_category", category)
        frag += f
        extra.update(e)
    return frag, {**params, **extra}


def _spc_scope(defn: dict, stage: Optional[str], product_category: Optional[str],
               params: dict) -> tuple[str, dict[str, Any]]:
    """下钻用 SPC 板级筛选 → （常量片段, 合并后的绑定参数）。

    只对在 ``DRILL_DEFS`` 里声明了 ``spc_scope`` 的指标（SPC 能力 / 柏拉图，
    与 L1 卡片同源同口径）生效：工段 = ``process_stage`` 精确匹配、
    产品分类 = ``product_category`` 精确匹配；未声明的指标原样返回空片段
    → 行为与改动前逐字段一致。

    为什么不复用 ``_mfg_scope``（产线前缀 LIKE + 型号集合 IN）：``spc_measurement``
    没有 ``line_code`` / ``product_model``，无从折出；为什么不复用 ``q_scope``：
    它第二列语义是「缺陷类别」，与 SPC 的「产品分类」不同（**统一的是「列精确
    匹配」这一形态，不是「同一列」**）。
    """
    ss = defn.get("spc_scope")
    if not ss or not (stage or product_category):
        return "", dict(params)
    frag, extra = "", {}
    if stage:
        f, e = _exact_col_filter(ss["stage_col"], "f_stage", stage)
        frag += f
        extra.update(e)
    if product_category:
        f, e = _exact_col_filter(ss["pcat_col"], "f_pcat", product_category)
        frag += f
        extra.update(e)
    return frag, {**params, **extra}


def _l2_detail(registry, defn: dict, items: list[dict], base_where: str,
               params: dict, dates: list[str], state: dict) -> dict:
    """2 级看板补充数据（与 ``items`` 明细行同口径）：整体汇总 / 整体逐日 / 关联原始记录。

    整体口径直接从明细行求和（分子分母加权），保证「汇总 = 各行之和」不会与明细对不上；
    逐日序列按窗口日期补齐（缺日留空由 ``_days_of`` 处理），原始记录按日期倒序取前 20 条。
    只对声明了板级筛选（``scope``）的指标调用，避免给其它屏的 L2 打开原始记录通道。
    """
    tot_num = sum(it["numerator"] for it in items)
    tot_den = sum(it["denominator"] for it in items)
    value = _pct(tot_num, tot_den)
    date_expr = defn.get("date_expr")
    ov_daily: list[dict] = []
    if date_expr:
        ov_rows = _rows(registry,
                        f"SELECT {date_expr} AS d, {defn['num_expr']} AS num, "
                        f"{defn['den_expr']} AS den FROM {defn['table']} "
                        f"WHERE {base_where} GROUP BY {date_expr}", params, state)
        ov_daily, _, _ = _days_of(ov_rows, defn, dates)
    rec_cols = defn["record_cols"]
    rec_sql = (f"SELECT {', '.join(rec_cols)} FROM {defn['table']} "
               f"WHERE {base_where}")
    if date_expr:
        rec_sql += f" ORDER BY {date_expr} DESC"
    records = _rows(registry, rec_sql + " LIMIT 20", params, state)
    worst = items[0] if items else None
    return {
        "summary": {
            "value": value, "numerator": tot_num, "denominator": tot_den,
            "target": defn["target"], "unit": defn["unit"],
            "higher_is_better": defn["higher_is_better"],
            "status": _status(value, defn["target"], defn["higher_is_better"]),
            "delta": round(value - defn["target"], 1),
            "items": len(items),
            "ok": sum(1 for it in items if it["status"] == "ok"),
            "warn": sum(1 for it in items if it["status"] == "warn"),
            "danger": sum(1 for it in items if it["status"] == "danger"),
            "worst": ({"name": worst["name"], "value": worst["value"],
                       "status": worst["status"]} if worst else None),
        },
        "daily": ov_daily,
        "records": records,
        "columns": [{"key": c, "label": _COL_CN.get(c, c)} for c in rec_cols],
    }

# 来料质量（来料批次合格率 / 来料上线不良率）的**固定全量口径**：
# 这两个指标**不受板级统计口径（日/周/月）与工段 · 类别 · 柏拉图筛选影响**——
# L1 的 TOP10 卡片与 2/3 级下钻都锚定同一固定区间（见 build_drill 的 fixed_range）。
INCOMING_RANGE = {"start": "2026-10-01", "end": "2026-10-15"}
INCOMING_WINDOW = {"key": "all", "start": INCOMING_RANGE["start"],
                   "end": INCOMING_RANGE["end"], "label": "来料全量口径"}
# 来料 TOP10 的关注条数（最需关注的最高 10 名）
_INCOMING_TOP = 10
# 来料明细记录条数上限（L3 展示「详细 IQC 检验数据」，不再只给 5 条）
_INCOMING_REC_LIMIT = 100


def _pareto_fact(registry, state: dict[str, Any]) -> dict:
    """预聚合「不良柏拉图事实表」：按 (日期, 类别, 工段, 产品分类, 类型) 聚合 defect_qty。

    前端据此本地切片/聚合/排序/累计——切换「月别/日别」与「产品分类/类别/工段」筛选
    零网络往返。仅取 ``process_stage IS NOT NULL`` 的电池域新增行（与既有 SMT/DIP 行并存）。
    显式抬高 ``row_cap``：新增 product_category 分组维度后分组基数 ≈ 3800 行
    （2 个月 × 4 工段 × 4 分类 × 2 类别 × 多类型），registry 默认 200 / 旧 2500 都会截断。
    """
    params = {"pstart": PARETO_RANGE["start"], "pend": PARETO_RANGE["end"]}
    rows = _rows(registry,
                 "SELECT process_stage AS stage, defect_category AS category, "
                 "product_category AS pcat, defect_type AS type, "
                 "date(inspect_time) AS d, "
                 "SUM(defect_qty) AS qty FROM quality_defect "
                 "WHERE process_stage IS NOT NULL "
                 "AND date(inspect_time) BETWEEN :pstart AND :pend "
                 "GROUP BY d, category, stage, pcat, type", params, state, cap=8000)
    out: list[dict] = []
    for r in rows:
        d = r.get("d")
        if not d:
            continue
        out.append({"d": d, "cat": r.get("category") or "", "pc": r.get("pcat") or "",
                    "stage": r.get("stage") or "", "type": r.get("type") or "",
                    "qty": r.get("qty") or 0})
    days = sorted({r["d"] for r in out}, reverse=True)
    months = sorted({r["d"][:7] for r in out}, reverse=True)
    cats = [c for c in QUALITY_CATEGORIES if c in {r["cat"] for r in out}]
    stages = [s for s in QUALITY_STAGES
              if s in {r["stage"] for r in out}]
    pcats = [p for p in PRODUCT_CATEGORIES if p in {r["pc"] for r in out}]
    return {"range": dict(PARETO_RANGE), "categories": cats, "stages": stages,
            "prod_cats": pcats, "days": days, "months": months, "rows": out}


# 制造侧原因柏拉图事实表覆盖范围（9 月历史 + 10 月，含 10-15 锚点日）：独立于板级窗口
MFG_PARETO_RANGE = {"start": "2026-09-01", "end": "2026-10-15"}
# 产线前缀 → 制造工段（与 _MFG_STAGE_LINE_LIKE 同源的**反解**；SQL CASE 常量，非用户输入）
_MFG_LINE_PREFIX_STAGE: tuple[tuple[str, str], ...] = (
    ("SMT", "SMT"), ("DIP", "PCM"), ("PACK", "PACK"), ("ASSY", "整机"),
)
# 型号 → 产品分类（SQL CASE 常量；与 _MFG_CAT_MODELS 同源，共 12 型号 4 分类）
_MFG_MODEL_PC: dict[str, str] = {
    "A型号": "消费电子", "E型号": "消费电子", "G型号": "消费电子",
    "B型号": "智能电源", "F型号": "智能电源", "H型号": "智能电源",
    "C型号": "储能电源", "I型号": "储能电源", "J型号": "储能电源",
    "D型号": "车载", "K型号": "车载", "L型号": "车载",
}


def _mfg_pareto_fact(registry, state: dict[str, Any], *, table: str,
                     type_col: str, qty_expr: str, date_col: str) -> dict:
    """预聚合「制造侧原因柏拉图事实表」：结构与品质 ``_pareto_fact()`` **完全一致**。

    按 (日期, 原因类型, 工段, 产品分类) 聚合 ``qty``——工段按产线前缀折、产品分类按型号
    折（常量 CASE 表达式，取值全部来自上面常量映射，**绝不拼接用户输入**）。制造侧无
    「缺陷类别」维度 → ``categories`` 恒为空列表；``rows`` 结构与品质侧逐字段对齐
    （``d / cat / pc / stage / type / qty``），前端可按同一套本地切片逻辑复用。

    纪律同 L1 聚合：只读 + 命名绑定参数 + 异常兜底（记 reasons，绝不抛出）+ 显式抬高 cap。
    """
    stage_case = ("CASE " + " ".join(
        f"WHEN line_code LIKE '{pref}-%' THEN '{stage}'"
        for pref, stage in _MFG_LINE_PREFIX_STAGE) + " ELSE '' END")
    pc_case = ("CASE " + " ".join(
        f"WHEN product_model = '{m}' THEN '{pc}'"
        for m, pc in _MFG_MODEL_PC.items()) + " ELSE '' END")
    params = {"pstart": MFG_PARETO_RANGE["start"], "pend": MFG_PARETO_RANGE["end"]}
    rows = _rows(registry,
                 f"SELECT {stage_case} AS stage, {pc_case} AS pcat, "
                 f"{type_col} AS type, {date_col} AS d, {qty_expr} AS qty "
                 f"FROM {table} "
                 f"WHERE {type_col} IS NOT NULL AND {type_col} <> '' "
                 f"AND {date_col} BETWEEN :pstart AND :pend "
                 "GROUP BY d, stage, pcat, type", params, state, cap=40000)
    out: list[dict] = []
    for r in rows:
        d = r.get("d")
        if not d or (r.get("qty") or 0) <= 0:
            continue
        out.append({"d": d, "cat": "", "pc": r.get("pcat") or "",
                    "stage": r.get("stage") or "", "type": r.get("type") or "",
                    "qty": r.get("qty") or 0})
    days = sorted({r["d"] for r in out}, reverse=True)
    months = sorted({r["d"][:7] for r in out}, reverse=True)
    stages = [s for s in MFG_STAGES if s in {r["stage"] for r in out}]
    pcats = [p for p in PRODUCT_CATEGORIES if p in {r["pc"] for r in out}]
    return {"range": dict(MFG_PARETO_RANGE), "categories": [], "stages": stages,
            "prod_cats": pcats, "days": days, "months": months, "rows": out}


def _three_rates(rows: list[dict]) -> tuple[float, float, float, float]:
    total = len(rows)
    present = sum(1 for r in rows if r.get("doc_present"))
    complete = sum(1 for r in rows if r.get("doc_complete"))
    comp = _pct(present, total)
    perf = _pct(complete, present)
    obey = _pct(_sum(rows, "audit_conform"), _sum(rows, "audit_total"))
    conformity = round(comp * perf * obey / 10000.0, 1)
    return comp, perf, obey, conformity


# 标准三率两级看板：1 级 = 4 工段（电池域产线分工段，与质量不良同源），2 级 = 班组
_STD_STAGE_ORDER = ["SMT", "PCM", "PACK", "整机"]
_STD_STAGE_LINES = {"SMT": "SMT-3", "PCM": "PCM-1", "PACK": "PACK-1",
                    "整机": "ASSY-1", "其他": "—"}


def _std_rate_block(rows: list[dict], key: str, name: str) -> dict:
    """一组审核行 → 三率 + 符合率 + 状态（与整体同一套 ``_three_rates`` 口径）。"""
    comp, perf, obey, conformity = _three_rates(rows)
    return {key: name,
            "completeness_rate": comp, "perfection_rate": perf,
            "compliance_rate": obey, "conformity_rate": conformity,
            "status": _status(conformity, _STD_CONFORMITY_TARGET, True)}


def _std_by_stage(rows: list[dict]) -> list[dict]:
    """审核行 → 1 级工段分组（每段内含 2 级班组分组）。

    口径铁律：工段/班组与整体**同一套** ``_three_rates``，逐行相加即回整体，
    四个工段的分子分母加权后必然等于整体（无"各段之和 ≠ 整体"的口径裂缝）。
    无工段标注的历史行进「其他」桶而不是被悄悄丢掉——否则整体与分项对不上。
    顺序按工艺流向（SMT → PCM → PACK → 整机），班组按符合率升序（最差在前）。
    """
    buckets: dict[str, list[dict]] = {s: [] for s in _STD_STAGE_ORDER}
    other: list[dict] = []
    for r in rows:
        stage = r.get("process_stage") or ""
        (buckets[stage] if stage in buckets else other).append(r)

    out: list[dict] = []
    for stage in _STD_STAGE_ORDER:
        sub = buckets[stage]
        if not sub:
            continue
        block = _std_rate_block(sub, "stage", stage)
        block["line"] = _STD_STAGE_LINES.get(stage, "")
        teams: list[dict] = []
        for name in sorted({(r.get("team") or "—") for r in sub}):
            tsub = [r for r in sub if (r.get("team") or "—") == name]
            tb = _std_rate_block(tsub, "team", name)
            tb["audit_rows"] = len(tsub)
            tb["audit_items"] = _sum(tsub, "audit_total")
            teams.append(tb)
        teams.sort(key=lambda t: t["conformity_rate"])
        block["teams"] = teams
        block["team_count"] = len(teams)
        block["audit_rows"] = len(sub)
        block["audit_items"] = _sum(sub, "audit_total")
        out.append(block)

    if other:
        block = _std_rate_block(other, "stage", "其他")
        block.update({"line": "—", "teams": [], "team_count": 0,
                      "audit_rows": len(other), "audit_items": _sum(other, "audit_total")})
        out.append(block)
    return out


def _std_dim_label(dim: str) -> str:
    return {"completeness": "标准完整率", "perfection": "标准完善率",
            "compliance": "标准遵守率"}.get(dim, dim)


def _std_causes(registry, state: dict, stage: str, team: str,
                dim_totals: dict[str, int], win: dict[str, Any]) -> list[dict]:
    """班组级不良原因（按三率维度归因），按当前口径汇总并回填维度内占比。

    ``dim_totals`` 是从审核明细**现算**的各维度不符合数，用来算占比与校验：
    同一维度的原因 occurrences 之和必须等于它（种子生成器按天保证，这里再兜一次——
    若原因表与明细脱节，占比会算出 >100%，宁可把 share 压成 0 也不编一个好看的数字）。
    """
    rows = _rows(registry,
                 "SELECT rate_dim, cause, defect_type, process_steps, "
                 "occurrences, unit, is_primary, audit_date FROM std_team_cause "
                 "WHERE process_stage = :stage AND team = :team "
                 "AND audit_date BETWEEN :start AND :end",
                 {"stage": stage, "team": team,
                  "start": win["start"], "end": win["end"]}, state, cap=8000)
    # 按 (维度, 原因) 汇总：同一条原因可能跨多天、多工序，弹窗要的是窗口内合计
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        dim = r.get("rate_dim") or ""
        key = (dim, r.get("cause") or "")
        cur = agg.setdefault(key, {
            "rate_dim": dim, "rate_dim_label": _std_dim_label(dim),
            "cause": r.get("cause") or "", "defect_type": "", "steps": [],
            "occurrences": 0, "unit": r.get("unit") or "项",
            "is_primary": bool(r.get("is_primary")), "days": 0,
        })
        cur["occurrences"] += int(r.get("occurrences") or 0)
        cur["days"] += 1
        if r.get("defect_type") and not cur["defect_type"]:
            cur["defect_type"] = r["defect_type"]
        for s in (r.get("process_steps") or "").split(","):
            if s and s not in cur["steps"]:
                cur["steps"].append(s)
    out = []
    for cur in agg.values():
        total = dim_totals.get(cur["rate_dim"], 0)
        # 脱节保护：合计超过实际缺口说明原因表没跟上明细，占比不可信 → 置 0
        cur["share"] = (_pct(cur["occurrences"], total)
                        if total and cur["occurrences"] <= total else 0.0)
        cur["steps"] = cur["steps"][:3]
        out.append(cur)
    order = {"completeness": 0, "perfection": 1, "compliance": 2}
    out.sort(key=lambda c: (order.get(c["rate_dim"], 9), -c["occurrences"]))
    return out


def build_std_team_detail(registry, window: str, stage: str,
                          team: str) -> dict:
    """2 级看板「点班组」弹窗数据：该班组三率拆解 + 不良原因归因。

    与 ``build_quality_dashboard`` 的 ``standard_three_rates`` **同一套** ``_three_rates``：
    班组行的三率、按工序/按日的分解、以及工段/整体的对比基准，全部来自同一批
    审核明细，不存在「弹窗里的数和大盘对不上」。

    ``stage`` / ``team`` 由调用方校验取值；班组在该窗口内无审核记录时返回
    ``found=False`` 的骨架（不抛错——「这个班组本窗口没被审核」本身是有效结论）。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    dates = _window_dates(win_def)
    params = {"start": win_def["start"], "end": win_def["end"]}
    cols = ("audit_date, process_step, product_model, standard_no, "
            "doc_present, doc_complete, audit_total, audit_conform")
    # 三批行都按同一窗口取：班组 / 该工段 / 全厂——用于"与工段差""与全厂差"，
    # 行数上限与品质看板一致地抬到 8000（否则「当月」口径会被默认 cap 截断）。
    team_rows = _rows(registry,
                      f"SELECT {cols} FROM std_standard_audit "
                      "WHERE audit_date BETWEEN :start AND :end "
                      "AND process_stage = :stage AND team = :team",
                      {**params, "stage": stage, "team": team}, state, cap=8000)
    stage_rows = _rows(registry,
                       f"SELECT {cols} FROM std_standard_audit "
                       "WHERE audit_date BETWEEN :start AND :end "
                       "AND process_stage = :stage",
                       {**params, "stage": stage}, state, cap=8000)
    all_rows = _rows(registry,
                     f"SELECT {cols} FROM std_standard_audit "
                     "WHERE audit_date BETWEEN :start AND :end",
                     params, state, cap=8000)

    empty_block = {"completeness_rate": 0.0, "perfection_rate": 0.0,
                   "compliance_rate": 0.0, "conformity_rate": 0.0,
                   "status": "danger", "audit_rows": 0, "audit_items": 0}
    s_block = _std_rate_block(stage_rows, "stage", stage) if stage_rows else dict(empty_block)
    a_block = _std_rate_block(all_rows, "stage", "全部") if all_rows else dict(empty_block)
    base = {
        "stage": stage, "team": team, "line": _STD_STAGE_LINES.get(stage, "—"),
        "window": {"key": window, "start": win_def["start"], "end": win_def["end"],
                   "days": len(dates), "label": win_def["label"]},
        "targets": dict(_STD_TARGETS),
        "target_conformity": _STD_CONFORMITY_TARGET,
        "stage_rates": {k: s_block[k] for k in
                        ("completeness_rate", "perfection_rate", "compliance_rate",
                         "conformity_rate", "status")},
        "overall_rates": {k: a_block[k] for k in
                          ("completeness_rate", "perfection_rate", "compliance_rate",
                           "conformity_rate", "status")},
        "notes": [],
    }
    if not team_rows:
        base.update({"found": False, "rates": dict(empty_block),
                     "by_step": [], "by_day": [], "causes": [],
                     "dim_totals": {}, "reasons": state["reasons"]})
        base["notes"].append("该班组在本口径内没有审核记录（不是数据丢失）。")
        return base

    comp, perf, obey, conformity = _three_rates(team_rows)
    rates = {"completeness_rate": comp, "perfection_rate": perf,
             "compliance_rate": obey, "conformity_rate": conformity,
             "status": _status(conformity, _STD_CONFORMITY_TARGET, True),
             "audit_rows": len(team_rows),
             "audit_items": int(_sum(team_rows, "audit_total"))}

    # 按工序拆解：哪道工序把三率拉下来，一眼看得出（同口径，逐工序复用 _three_rates）
    by_step = []
    for step in sorted({r.get("process_step") or "—" for r in team_rows}):
        sub = [r for r in team_rows if (r.get("process_step") or "—") == step]
        c, f, o, k = _three_rates(sub)
        by_step.append({
            "step": step,
            "completeness_rate": c, "perfection_rate": f,
            "compliance_rate": o, "conformity_rate": k,
            "status": _status(k, _STD_CONFORMITY_TARGET, True),
            "audit_rows": len(sub),
            "audit_items": int(_sum(sub, "audit_total")),
            "nonconform_items": int(sum(r["audit_total"] - r["audit_conform"] for r in sub)),
            "missing_doc_rows": sum(1 for r in sub if not r.get("doc_present")),
            "incomplete_doc_rows": sum(1 for r in sub
                                       if r.get("doc_present") and not r.get("doc_complete")),
        })
    by_step.sort(key=lambda x: x["conformity_rate"])

    # 按日趋势：与板级趋势同一套「先按日期分组再取符合率」，缺日补 null（不断线）
    day_map: dict[str, list[dict]] = {}
    for r in team_rows:
        day_map.setdefault(str(r.get("audit_date"))[:10], []).append(r)
    by_day = []
    for d in dates:
        sub = day_map.get(d)
        by_day.append({"d": d, "conformity_rate": _three_rates(sub)[3] if sub else None,
                       "audit_rows": len(sub or [])})

    dim_totals = {
        "completeness": sum(1 for r in team_rows if not r.get("doc_present")),
        "perfection": sum(1 for r in team_rows
                          if r.get("doc_present") and not r.get("doc_complete")),
        "compliance": int(sum(r["audit_total"] - r["audit_conform"] for r in team_rows)),
    }
    causes = _std_causes(registry, state, stage, team, dim_totals, win_def)

    base.update({
        "found": True, "rates": rates, "by_step": by_step, "by_day": by_day,
        "causes": causes, "dim_totals": dim_totals,
        "deltas": {
            "vs_stage": round(conformity - s_block["conformity_rate"], 1),
            "vs_overall": round(conformity - a_block["conformity_rate"], 1),
            "vs_target": round(conformity - _STD_CONFORMITY_TARGET, 1),
        },
        "reasons": state["reasons"],
    })
    weakest_step = by_step[0] if by_step else None
    if weakest_step:
        base["notes"].append(
            f"最低工序为「{weakest_step['step']}」（符合率 {weakest_step['conformity_rate']}%）——"
            "三率短板要落到工序才有改善抓手。")
    bad_dims = [(k, dim_totals[k]) for k in ("completeness", "perfection", "compliance")
                if dim_totals[k]]
    if bad_dims:
        base["notes"].append(
            "不符合构成：完整率缺口 %d 行 · 完善率缺口 %d 行 · 遵守率不符合 %d 项；"
            "三项相加即该班组审核明细的全部缺口，可与 1 级/2 级看板对账。"
            % (dim_totals["completeness"], dim_totals["perfection"],
               dim_totals["compliance"]))
    if not causes:
        base["notes"].append("本窗口该班组无归因明细（原因表未覆盖），仅有比率。")
    return base


def build_quality_dashboard(registry, window: str = "window", *,
                            stage: Optional[str] = None,
                            category: Optional[str] = None) -> dict:
    """品质看板聚合。

    ``stage`` / ``category``：板级「工段 / 缺陷类别」筛选（缺省 = 全部，取值受
    ``QUALITY_STAGES`` / ``QUALITY_CATEGORIES`` 约束）。仅同源的 ``quality_defect``
    指标（不良率 / 直通率及其 TOP5 排行）随筛选重算——**卡片值即所选范围的汇总**；
    进料端（IQC）/ 客户端（客诉）在小数据源上无该维度，保持板级口径并标注
    ``scope_note``（前端在卡上显示「筛选不适用」），不编造不存在的分解。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    dates = _window_dates(win_def)
    params = {"start": win_def["start"], "end": win_def["end"]}
    win = "date(inspect_time) BETWEEN :start AND :end"
    # 工段 / 类别只加在 quality_defect 同源聚合的 WHERE 上；未筛选时 SQL 与绑定参数
    # 与既有完全一致（保证历史口径零漂移）。范围标签与下钻侧同一套（_quality_scope_cn）。
    scope_cn = _quality_scope_cn(stage, category)
    q_win, q_params = win, params
    if stage:
        q_win += " AND process_stage = :f_stage"
        q_params = dict(q_params, f_stage=stage)
    if category:
        q_win += " AND defect_category = :f_category"
        q_params = dict(q_params, f_category=category)

    def _scoped_ask(plain: str, scoped: str) -> str:
        """问数文案：无筛选沿用原窗口口径；有筛选时显式前置「窗口 · 工段/类别」范围，
        避免问数文案与卡片所看口径不符。"""
        if scope_cn:
            return f"{win_def['label']} · {scope_cn} {scoped}"
        return _ask(plain, win_def["label"])

    # 不良率 / 直通率（同一份 quality_defect 明细，互为补数）
    qd = _rows(registry,
               "SELECT line_code, product_model, SUM(defect_qty) AS num, "
               "SUM(total_qty) AS den FROM quality_defect "
               f"WHERE {q_win} GROUP BY line_code, product_model", q_params, state)
    qd_t = _rows(registry,
                 "SELECT date(inspect_time) AS d, SUM(defect_qty) AS num, "
                 "SUM(total_qty) AS den FROM quality_defect "
                 f"WHERE {q_win} GROUP BY date(inspect_time)", q_params, state)

    defect_num, defect_den = _sum(qd, "num"), _sum(qd, "den")
    fpy_num = defect_den - defect_num

    # 直通率趋势 = 100 − 不良率趋势
    fpy_trend = [round(100.0 - v, 1) for v in _trend(qd_t, dates)]

    # 标准三率（1 级看板 = 4 工段 / 2 级看板 = 各班组；by_model 保留供兼容）
    # 行数上限显式抬高：4 工段 × 3 班组 × 8 工序 × 15 天 ≈ 1440 行，registry 默认
    # 200/500 都会在「当月」口径下截断，截断后各段之和会与整体对不上。
    qs = _rows(registry,
               "SELECT product_model, process_stage, team, doc_present, doc_complete, "
               "audit_total, audit_conform FROM std_standard_audit "
               "WHERE audit_date BETWEEN :start AND :end", params, state, cap=8000)
    comp, perf, obey, conformity = _three_rates(qs)
    by_stage = _std_by_stage(qs)
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

    # 来料质量 · 固定全量口径（不受统计口径 / 工段 / 类别 / 柏拉图筛选影响）：
    #   来料批次合格率 = IQC 判定合格（pass）批次数 ÷ 来料批次总数（按供应商）
    #   来料上线不良率 = 上线不良数 ÷ 上线投入数（按供应商）
    ip = {"istart": INCOMING_RANGE["start"], "iend": INCOMING_RANGE["end"]}
    ibf = _rows(registry,
                "SELECT supplier, "
                "SUM(CASE WHEN inspect_status='pass' THEN 1 ELSE 0 END) AS num, "
                "COUNT(*) AS den FROM incoming_batch "
                "WHERE date(arrival_time) BETWEEN :istart AND :iend "
                "GROUP BY supplier", ip, state)
    ild = _rows(registry,
                "SELECT supplier, SUM(defect_qty) AS num, SUM(total_qty) AS den "
                "FROM incoming_line_defect "
                "WHERE date(inspect_date) BETWEEN :istart AND :iend "
                "GROUP BY supplier", ip, state)

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
             _scoped_ask("最近7天各产线不良率如何？最高的是哪条线？",
                         "各产线不良率如何？最高的是哪条线？"),
             _trend(qd_t, dates)),
        _kpi("fpy", "直通率 FPY", fpy_num, defect_den, 98.0, True,
             "一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
             _scoped_ask("最近7天各产线直通率如何？最低的是哪条线？",
                         "各产线直通率如何？最低的是哪条线？"),
             fpy_trend),
        _kpi("incoming_pass_rate", "来料批次合格率", _sum(ib, "num"),
             _sum(ib, "den"), 90.0, True,
             "IQC 判定合格（pass）批次数 ÷ 窗口内来料批次总数。",
             _ask("最近7天各供应商来料批次合格率如何？最低的是哪家？", win_def["label"]),
             _trend(ib_t, dates)),
        _kpi("complaint_close_rate", "客诉闭环率", _sum(cc, "num"),
             _sum(cc, "den"), 80.0, True,
             "已关闭（closed）客诉单数 ÷ 客诉单总数（快照口径）。",
             "各客户型号的客诉闭环率如何？哪些还没闭环？", []),
    ]
    # 工段 / 类别筛选生效时：无该维度的两项明确标注「不适用」，避免被误读成筛选后的值。
    if scope_cn:
        for k in kpis:
            if k["key"] in QUALITY_SCOPE_FREE_KPIS:
                k["scope_note"] = "工段 / 类别筛选不适用（该指标无此维度）"

    rankings = [
        {"key": "defect_rate", "label": "不良率", "unit": "%", "target": 2.0,
         "higher_is_better": False,
         "items": _rank_items(qd, _line_model, 2.0, False)},
        {"key": "fpy", "label": "直通率", "unit": "%", "target": 98.0,
         "higher_is_better": True,
         "items": _fpy_rank_items(qd, 98.0)},
        {"key": "incoming_pass_rate", "label": "来料批次合格率", "unit": "%",
         "target": 90.0, "higher_is_better": True,
         # 该 KPI 的 L1 榜单展示已并入下方 ``incoming_top10`` 的「来料批次合格率 · TOP10」
         # （固定全量口径）；此处只保留一条配套榜以维持「每个 KPI 一条配套榜」的结构
         # 约束。``view="merged_into_top10"`` = 前端按此标记跳过它，避免同一指标出两张卡。
         "view": "merged_into_top10", "dimension": "供应商",
         "items": _rank_items(ibf, _sup_name, 90.0, True, 5, _sup_entity)},
        {"key": "complaint_close_rate", "label": "客诉闭环率", "unit": "%",
         "target": 80.0, "higher_is_better": True,
         "items": _rank_items(cc, _comp_name, 80.0, True, 5, _comp_entity)},
    ]

    # 来料质量 TOP10（``incoming_top10``）：固定全量口径，**不受任何筛选影响**。
    # 独立于 ``rankings``——那里是「每个 KPI 一条配套榜」的配对结构（有清单守卫，
    # 新增条目会破坏「Card 数与制造看板对齐」契约）；这两张是 L1 的来料关注榜。
    # L1 只给最需关注的 10 家；全量供应商在 2 级看板/下钻里看（build_drill L2 全量分解）。
    incoming_top10 = [
        {"key": "incoming_batch_quality", "label": "来料批次合格率", "unit": "%",
         "target": 90.0, "higher_is_better": True,
         "view": "incoming_top10", "scope_free": True, "dimension": "供应商",
         "items": _rank_items(ibf, _sup_name, 90.0, True, _INCOMING_TOP, _sup_entity)},
        {"key": "incoming_line_defect", "label": "来料上线不良率", "unit": "%",
         "target": 5.0, "higher_is_better": False,
         "view": "incoming_top10", "scope_free": True, "dimension": "供应商",
         "items": _rank_items(ild, _sup_name, 5.0, False, _INCOMING_TOP, _sup_entity)},
    ]

    # 累计不良柏拉图：窗口内按 defect_type 聚合，降序累积 + 关键少数（累计首达 80%）
    pareto = _pareto_cum(_rows(registry,
                               "SELECT defect_type AS name, SUM(defect_qty) AS value "
                               "FROM quality_defect "
                               f"WHERE {win} GROUP BY defect_type "
                               "ORDER BY value DESC", params, state))

    data = _envelope("quality", state, win_def)
    data.update({
        "kpis": kpis, "rankings": rankings, "notes": _QTY_NOTES,
        # 来料质量 TOP10（固定全量口径，不受任何筛选影响）；见上方 incoming_top10
        "incoming_top10": incoming_top10,
        # 板级「工段 / 缺陷类别」筛选（回显给前端筛选条 / 报告溯源；未筛选 = 空串）
        "filter": {"stage": stage or "", "category": category or ""},
        "pareto": pareto,
        "pareto_label": "不良类型",
        "pareto_kpi": "defect_rate",   # 供前端点击下钻（与 drill 同源指标）
        "pareto_vital": _vital_few(pareto),
        "pareto_threshold": 80.0,
        "pareto_fact": _pareto_fact(registry, state),
        "standard_three_rates": {
            "definition": "标准三率 = 标准完整率 × 标准完善率 × 标准遵守率 = 符合率",
            "targets": dict(_STD_TARGETS),
            "levels": {"l1": "工段", "l2": "班组"},
            "overall": {
                "completeness_rate": comp, "perfection_rate": perf,
                "compliance_rate": obey, "conformity_rate": conformity,
                "status": _status(conformity, _STD_CONFORMITY_TARGET, True),
                "formula": f"{comp}% × {perf}% × {obey}% = {conformity}%",
                "audit_rows": len(qs),
                "audit_items": _sum(qs, "audit_total"),
            },
            "by_stage": by_stage,
            "stage_count": sum(1 for s in by_stage if s.get("teams")),
            "team_count": sum(len(s.get("teams") or []) for s in by_stage),
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


def build_order_dashboard(registry, window: str = "window") -> dict:
    """订单履约看板 L1：4 个交付类 KPI + TOP5 排行 + 状态管道 + 风险订单清单。

    ``window``：``window``（最近7天，默认）/ ``day``（当日）/ ``month``（当月）
    ——与制造综合看板同源口径，KPI 卡、TOP5 排行、迷你趋势、状态管道、风险订单
    清单与下钻均随所选口径切换（订单按客户要求交付日 due_date 归入窗口）。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    dates = _window_dates(win_def)
    params = {"start": win_def["start"], "end": win_def["end"]}
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
             "最近7天各客户 OTD 准时交付率如何？最低的是哪个客户？", _trend(otd_t, dates)),
        _kpi("otif", "OTIF 准时足额率", _sum(otif, "num"), _sum(otif, "den"), 95.0,
             True, "同时满足准时且足额的订单数 ÷ 订单总数。",
             "最近7天各客户 OTIF 准时足额率如何？最低的是哪个客户？", _trend(otif_t, dates)),
        _kpi("delay_rate", "延期订单率", _sum(dr, "num"), _sum(dr, "den"), 5.0,
             False, "延期天数 > 0 的订单数 ÷ 订单总数（越低越好）。",
             "最近7天各客户延期订单率如何？最高的是哪个客户？延期原因有哪些？",
             _trend(dr_t, dates)),
        _kpi("kit_rate", "齐套率", _sum(kit, "num"), _sum(kit, "den"), 95.0,
             True, "已齐套料数 ÷ 应齐套料数。",
             "最近7天各型号订单齐套率如何？最低的是哪个型号？缺料集中在哪些料号？",
             _trend(kit_t, dates)),
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
    # due_date 一并取出：预警中心把「客户要求交付日」作为订单临期预警的触发时间，
    # 与看板同一份明细，不另算一套。
    risk_orders = _rows(registry,
                        "SELECT order_no, customer, customer_level, product_model, "
                        "qty, due_date, stage, risk_type, delay_days, progress, "
                        "shortage_note, owner FROM order_fulfillment "
                        f"WHERE {win} AND risk_type <> '正常' "
                        "ORDER BY delay_days DESC, due_date ASC LIMIT 8",
                        params, state)

    data = _envelope("orders", state, win_def)
    data.update({"kpis": kpis, "rankings": rankings, "notes": _ORDER_NOTES,
                 "pipeline": pipeline, "risk_orders": risk_orders})
    return data


# 管道环节白名单公开别名：API 层与聚合器同源，避免两处字面量漂移
ORDER_STAGES = _ORDER_STAGES


def build_order_stage_detail(registry, stage: str, window: str = "window") -> dict:
    """订单履约看板「状态管道 · 点环节」弹窗：该环节订单清单 + 汇总。

    与 ``build_order_dashboard`` 的 ``pipeline`` **同源同口径**（同一张
    ``order_fulfillment``、同一个 ``due_date`` 窗口）：弹窗汇总里的订单数 /
    延期数必须与管道环节块可对账。清单按延期天数降序、进度升序（最紧急在前），
    输出封顶 50 行（``truncated`` 标注截断量，汇总按全量聚合不受影响）。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    params = {"start": win_def["start"], "end": win_def["end"], "stage": stage}
    win = "due_date BETWEEN :start AND :end"

    tot = _rows(registry,
                "SELECT COUNT(*) AS cnt, "
                "SUM(CASE WHEN delay_days>0 THEN 1 ELSE 0 END) AS delayed, "
                "SUM(qty) AS qty, AVG(progress) AS avg_progress "
                f"FROM order_fulfillment WHERE {win} AND stage = :stage",
                params, state)
    t = tot[0] if tot else {}
    rows = _rows(registry,
                 "SELECT order_no, customer, customer_level, product_model, qty, "
                 "progress, delay_days, risk_type, owner, due_date, ship_date, "
                 "shortage_note FROM order_fulfillment "
                 f"WHERE {win} AND stage = :stage "
                 "ORDER BY delay_days DESC, progress ASC, order_no ASC LIMIT 50",
                 params, state, cap=200)

    count = int(t.get("cnt") or 0)
    data = _envelope("orders", state, win_def)
    data.update({
        "stage": stage,
        "summary": {
            "count": count,
            "delayed": int(t.get("delayed") or 0),
            "qty": int(t.get("qty") or 0),
            "avg_progress": round(float(t.get("avg_progress") or 0), 1),
        },
        "orders": rows,
        "truncated": max(0, count - len(rows)),
    })
    return data


def build_order_detail(registry, order_no: str) -> dict:
    """订单履约看板「风险订单 · 点行」弹窗：单张订单全字段明细。

    订单号不存在时返回 ``found:false`` 骨架（200 不是 404——「查无此单」是
    有效结论，与班组明细弹窗同一裁决）。客户身份字段（``order.customer`` /
    ``order.customer_level``）由 data_scope.yaml 的 ``orders`` 看板规则按岗位
    脱敏，与 ``risk_orders[]`` 同一套 keep_roles。
    """
    state: dict[str, Any] = {"reasons": []}
    rows = _rows(registry,
                 "SELECT order_no, customer, customer_level, product_model, qty, "
                 "order_date, due_date, ship_date, stage, risk_type, delay_days, "
                 "on_time, in_full, kit_done_qty, kit_total_qty, progress, "
                 "shortage_note, owner FROM order_fulfillment "
                 "WHERE order_no = :order_no LIMIT 1",
                 {"order_no": order_no}, state, cap=10)
    r = rows[0] if rows else None
    kt = (r or {}).get("kit_total_qty") or 0
    kd = (r or {}).get("kit_done_qty") or 0
    kit_rate = round(kd / kt * 100, 1) if kt > 0 else None

    data = _envelope("orders", state)
    data.update({
        "found": r is not None,
        "order": r or {},
        "kit_rate": kit_rate,
        "notes": [
            "齐套率 = 已齐套料数 ÷ 应齐套料数；准时 = 实际发货日 ≤ 客户要求交付日。",
            "订单按客户要求交付日 due_date 归入统计窗口，与看板口径同源。",
        ],
    })
    return data


# 2 级看板「点行」弹窗的明细行上限：单客户/型号窗口内订单远小于此，
# 抬高只为「当月」口径极端情形不截断（汇总始终按全量现算，不受影响）。
_ORDER_GROUP_LIMIT = 50


def build_order_group_detail(registry, window: str, kpi_key: str,
                             entity: Optional[dict] = None) -> dict:
    """订单履约 2 级看板「点行」弹窗：单维度分组（客户·等级 / 型号）的订单清单。

    与 ``build_drill`` 的 L2 **同一套** ``DRILL_DEFS`` 口径（同表 / 同 ``due_date``
    窗口 / 同 ``num_expr``/``den_expr`` / 同实体过滤 ``_entity_cond``）：弹窗头部的
    指标值、分子分母与 2 级看板里该行**逐字段一致**，可对账。

    ``entity``：``otd`` / ``otif`` / ``delay_rate`` 为 ``{customer, level}``，
    ``kit_rate`` 为 ``{model}``（取值经 ``entity_param_names`` 白名单）；
    分组在窗口内无订单时返回 ``found=False`` 骨架（200，不是 404——
    「该分组本口径无订单」是有效结论，与班组明细弹窗同一裁决）。
    清单按延期天数降序、交期升序（最紧急在前）；客户身份字段由 data_scope.yaml
    的 ``orders[].customer`` 规则按岗位脱敏，与风险订单清单同一套 keep_roles。
    """
    defn = DRILL_DEFS.get(kpi_key)
    if defn is None or defn["screen"] != "orders":
        raise ValueError(f"未知订单指标 kpi={kpi_key!r}")
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    params = {"start": win_def["start"], "end": win_def["end"]}
    base_where = "due_date BETWEEN :start AND :end"
    ent = {p: ((entity or {}).get(p) or "") for _, p in _entity_cols(defn)}
    cond, p3 = _drill_where(defn, ent, params)

    agg = _rows(registry,
                f"SELECT {defn['num_expr']} AS num, {defn['den_expr']} AS den "
                f"FROM {defn['table']} WHERE {base_where} {cond}", p3, state)
    num = (agg[0].get("num") if agg else 0) or 0
    den = (agg[0].get("den") if agg else 0) or 0
    value = _pct(num, den)

    # 明细列 = 指标 record_cols（与 L3 原始记录同源）+ 履约进度 / 责任人补充，
    # 让弹窗一行就是一张订单的完整履约快照。
    cols = list(defn["record_cols"])
    for extra in ("progress", "owner"):
        if extra not in cols:
            cols.append(extra)
    orders = _rows(registry,
                   f"SELECT {', '.join(cols)} FROM {defn['table']} "
                   f"WHERE {base_where} {cond} "
                   f"ORDER BY delay_days DESC, due_date ASC, order_no ASC "
                   f"LIMIT {_ORDER_GROUP_LIMIT}", p3, state)

    data = _envelope("orders", state, win_def)
    data.update(_drill_meta(defn, kpi_key, win_def.get("label")))
    data.update({
        "level": 2, "entity": ent,
        "found": bool(orders),
        "value": value, "numerator": num, "denominator": den,
        "status": _status(value, defn["target"], defn["higher_is_better"]),
        "delta": round(value - defn["target"], 1),
        "orders": orders,
        "columns": [{"key": c, "label": _COL_CN.get(c, c)} for c in cols],
        "order_limit": _ORDER_GROUP_LIMIT,
        "notes": [],
    })
    if not orders:
        data["notes"].append("该分组在本口径内没有订单（不是数据丢失）。")
    elif len(orders) >= _ORDER_GROUP_LIMIT:
        data["notes"].append(f"清单超过 {_ORDER_GROUP_LIMIT} 单已截断，指标值仍为全量口径。")
    return data


# ---------------------------------------------------------------- SPC 过程能力看板

# 测量明细取数的行数上限。registry 默认 row_cap=200，**远远不够**：
#   - 演示种子 1350 行（7 特性 × 4 工段 × 4 产品分类 × 3 天 × 5 子组 × 5 值），
#     默认 200 会把「当月」口径的多数特性整段截断 → 特性数忽多忽少、
#     能力指数与越限率口径漂移（与标准三率看板的 cap=8000 同类坑）；
#   - 截断还会打乱「特性 → 测量行」的对应，使筛选后的特性集合失真。
# 取 8000（覆盖演示库全量 1350 行，且留足扩展余量）。
_SPC_MEAS_CAP = 8000

_SPC_NOTES = [
    "过程能力指数双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R，R̄/d2）；"
    "Pp/Ppk 用整体 σ（样本标准差 ddof=1），保证 Pp ≠ Cp。",
    "判定线：Cpk ≥ 1.33 合格；1.00 ≤ Cpk < 1.33 偏低；Cpk < 1.00 不合格。",
    "L1 卡片展示 Cpk（无量纲指数，由确定性内核 core/spc 计算）；"
    "下钻采用「子组超差率」，以复用统一聚合引擎（Cpk 与超差率是同一过程的两面）。",
    "不良柏拉图与 SPC 工具 spc.pareto 同源（quality_defect 全量聚合，关键少数 = 累计首达 80%）。",
    "缺测量值 / 缺规格限时该特性标数据缺口（data_gap），绝不编造指数。",
    "规格限为演示规格限（工艺侧待给定），不代表真实工艺放行标准；"
    "判定档位随规格限变化，规格限确权后需重算。",
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


def _spc_cap_scope(stage: Optional[str], product_category: Optional[str],
                    params: dict) -> tuple[str, dict[str, Any]]:
    """SPC 板级「工段 / 产品分类」筛选 → （常量片段, 合并后的绑定参数）。

    能力表（``spc_measurement``）与柏拉图表（``quality_defect``）**都有直接列**
    （前者为新增列，后者本来就有）→ 统一走「列精确匹配」形态，与下钻侧
    ``_spc_scope`` 同语义同参数名，保证 L1 / 2 级 / 弹窗三处口径一致。
    取值已在接口层按 ``MFG_STAGES`` / ``MFG_PRODUCT_CATEGORIES`` 白名单裁定，
    **用户字符串绝不进入 SQL**。
    """
    frag, extra = "", {}
    if stage:
        f, e = _exact_col_filter("process_stage", "f_stage", stage)
        frag += f
        extra.update(e)
    if product_category:
        f, e = _exact_col_filter("product_category", "f_pcat", product_category)
        frag += f
        extra.update(e)
    return frag, {**params, **extra}


def build_spc_dashboard(registry, window: str = "window", *,
                        stage: Optional[str] = None,
                        product_category: Optional[str] = None) -> dict:
    """SPC 过程能力看板 L1：逐特性 Cp/Cpk/Pp/Ppk 判定 + 超差率 + 不良柏拉图。

    板级筛选：
    - ``window``：``window``（最近7天，默认）/ ``day``（当日）/ ``month``（当月），
      与制造/品质看板同源（``resolve_window``）。默认键解析结果与既有 ``DEMO_WINDOW``
      起止**完全一致** → 不传参数时能力表取数与改动前逐字段一致；
    - ``stage`` / ``product_category``：工段 / 产品分类（``MFG_STAGES`` /
      ``MFG_PRODUCT_CATEGORIES`` 白名单），按**直接列精确匹配**（能力表与柏拉图表
      都有这两列）→ 三个口径来源同源，柏拉图与能力表不再各算各的。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    params: dict[str, Any] = {"start": win_def["start"], "end": win_def["end"]}
    win = "date(measured_at) BETWEEN :start AND :end"

    # 维度筛选片段（空筛 → 空片段，SQL 与改动前逐字一致）
    dim_frag, params = _spc_cap_scope(stage, product_category, params)

    meas = _rows(registry,
                 "SELECT characteristic, subgroup_no, seq, value, usl, lsl, "
                 f"measured_at, source FROM spc_measurement WHERE {win}"
                 f"{dim_frag}", params, state, cap=_SPC_MEAS_CAP)
    caps = _spc_caps_from_rows(meas)

    over = _rows(registry,
                 "SELECT characteristic, "
                 "SUM(CASE WHEN (usl IS NOT NULL AND value > usl) "
                 "OR (lsl IS NOT NULL AND value < lsl) THEN 1 ELSE 0 END) AS num, "
                 f"COUNT(*) AS den FROM spc_measurement WHERE {win}{dim_frag} "
                 "GROUP BY characteristic", params, state)
    over_by = {r.get("characteristic"): r for r in over}

    # 柏拉图与能力表同源：同一时间窗口 + 同一维度筛选（改动前柏拉图是「全量无
    # WHERE」，与能力表口径不一致 —— 本次按需求 A 统一为跟随所选口径）。
    dwin = "date(inspect_time) BETWEEN :start AND :end"
    pareto_rows = _rows(registry,
                        "SELECT defect_type AS name, SUM(defect_qty) AS value "
                        f"FROM quality_defect WHERE {dwin}{dim_frag} "
                        "GROUP BY defect_type "
                        "ORDER BY value DESC", params, state)
    pareto = _pareto_cum(pareto_rows)

    defect_rate_rows = _rows(registry,
                             "SELECT defect_type, SUM(defect_qty) AS num, "
                             f"SUM(total_qty) AS den FROM quality_defect "
                             f"WHERE {dwin}{dim_frag} "
                             "GROUP BY defect_type", params, state)

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

    data = _envelope("spc", state, win_def)
    data.update({"kpis": kpis, "capabilities": caps, "pareto": pareto,
                 "rankings": rankings, "notes": _SPC_NOTES,
                 # 板级筛选回显（前端口径标签 + 弹窗/下钻参数透传的唯一来源）
                 "filter": {"stage": stage or "", "product_category": product_category or ""},
                 "data_gap": (not caps) or any(c.get("data_gap") for c in caps)})
    return data


def build_spc_capability_detail(registry, window: str = "window", *,
                                 characteristic: str,
                                 stage: Optional[str] = None,
                                 product_category: Optional[str] = None) -> dict:
    """SPC 2 级看板「点特性」弹窗：单特性的能力拆解 + 逐日越限 + 原始记录。

    **同源保证（硬约束）**：能力指数复用 ``_spc_caps_from_rows``（与 L1 同一函数），
    窗口与维度筛选与 L1 同一套（``resolve_window`` + ``_spc_cap_scope``）
    → 弹窗的 cpk / 判定 / 样本数与 L1 卡片、2 级行**逐字段一致、可对账**。
    绝不另写一套能力指数算法。

    无该特性的记录时返回 ``found=False`` 的骨架（**200 而非 404**），
    口径与 ``build_std_team_detail`` 的既有纪律一致。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    params: dict[str, Any] = {"start": win_def["start"], "end": win_def["end"]}
    dim_frag, params = _spc_cap_scope(stage, product_category, params)
    win = "date(measured_at) BETWEEN :start AND :end"
    char = (characteristic or "").strip()
    ent = {"characteristic": char}

    rows = _rows(registry,
                 "SELECT characteristic, subgroup_no, seq, value, usl, lsl, "
                 "measured_at, source, process_stage, product_category "
                 f"FROM spc_measurement WHERE {win} AND characteristic = :ch"
                 f"{dim_frag}", {**params, "ch": char}, state, cap=_SPC_MEAS_CAP)
    rows.sort(key=lambda r: (str(r.get("measured_at") or ""), r.get("subgroup_no") or 0))

    data = _envelope("spc", state, win_def)
    data.update({
        "level": 3,
        "characteristic": char,
        "found": bool(rows),
        "filter": {"stage": stage or "", "product_category": product_category or ""},
        "columns": [
            {"key": "measured_at", "label": "测量时间"},
            {"key": "subgroup_no", "label": "子组号"},
            {"key": "seq", "label": "序号"},
            {"key": "value", "label": "测量值"},
            {"key": "usl", "label": "规格上限"},
            {"key": "lsl", "label": "规格下限"},
            {"key": "process_stage", "label": "工段"},
            {"key": "product_category", "label": "产品分类"},
        ],
        "records": rows[:100],
        "notes": list(_SPC_NOTES),
    })
    if not rows:
        data.update({"cp": None, "cpk": None, "pp": None, "ppk": None,
                     "sigma_within": None, "sigma_overall": None,
                     "n_total": 0, "n_subgroups": 0, "spec": {},
                     "verdict": "不可判定", "status": "empty",
                     "over_rate": 0.0, "over_num": 0, "over_den": 0,
                     "daily": [],
                     "warnings": ["该特性在当前口径与筛选范围内无测量记录"]})
        return data

    caps = _spc_caps_from_rows(rows)
    cap = caps[0] if caps else {}
    num = sum(1 for r in rows
              if (r.get("usl") is not None and (r.get("value") or 0) > r.get("usl"))
              or (r.get("lsl") is not None and (r.get("value") or 0) < r.get("lsl")))
    den = len(rows)

    # 逐日越限率（与 L1 越限率同源：同一批行现算）
    by_day: dict[str, list[int]] = {}
    for r in rows:
        day = str(r.get("measured_at") or "")[:10]
        if not day:
            continue
        slot = by_day.setdefault(day, [0, 0])
        slot[1] += 1
        if ((r.get("usl") is not None and (r.get("value") or 0) > r.get("usl"))
                or (r.get("lsl") is not None and (r.get("value") or 0) < r.get("lsl"))):
            slot[0] += 1
    daily = [{"date": d, "over_num": v[0], "over_den": v[1],
              "over_rate": _pct(v[0], v[1])} for d, v in sorted(by_day.items())]

    data.update({
        "cp": cap.get("cp"), "cpk": cap.get("cpk"),
        "pp": cap.get("pp"), "ppk": cap.get("ppk"),
        "sigma_within": cap.get("sigma_within"),
        "sigma_overall": cap.get("sigma_overall"),
        "n_total": cap.get("n_total") or den,
        "n_subgroups": cap.get("n_subgroups") or 0,
        "spec": cap.get("spec") or {},
        "verdict": cap.get("verdict"),
        "status": _verdict_status(cap.get("verdict")),
        "over_rate": _pct(num, den), "over_num": num, "over_den": den,
        "daily": daily,
        "data_gap": bool(cap.get("data_gap")),
        "warnings": cap.get("warnings") or [],
    })
    return data


def build_spc_measurement_records(registry, window: str = "window", *,
                                  characteristic: str = "",
                                  day: str = "",
                                  over_only: bool = False,
                                  stage: Optional[str] = None,
                                  product_category: Optional[str] = None) -> dict:
    """SPC 测量记录弹窗聚合：越限明细 / 单日明细（与 L1 / 2 级 / 弹窗同口径）。

    **同源保证**：窗口（``resolve_window``）与维度筛选（``_spc_cap_scope``）与
    ``build_spc_dashboard`` / ``build_spc_capability_detail`` 完全同一套，
    弹窗里的记录数与看板「越限条数 / 测量条数」**可对账**。

    - ``over_only=True``：只取越限行（value 超出 usl / lsl），按**超出幅度**降序
      （越得最多的排最前），并附「超出幅度(%)」计算列 —— 供 L2/L3 hero 的
      「越限条数」单击查看「到底哪些值超了、超多少」；
    - ``characteristic``：锁定单特性（L3 逐日行点入）；空 = 全特性（L2 整体逐日行点入）；
    - ``day``（YYYY-MM-DD）：锁定单日（逐日表行点入）。

    无记录时返回 ``found=False`` 骨架（200 而非 404），口径与既有纪律一致。
    """
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    params: dict[str, Any] = {"start": win_def["start"], "end": win_def["end"]}
    dim_frag, params = _spc_cap_scope(stage, product_category, params)
    where = "date(measured_at) BETWEEN :start AND :end" + dim_frag
    char = (characteristic or "").strip()
    if char:
        where += " AND characteristic = :ch"
        params["ch"] = char
    d = (day or "").strip()
    if d:
        where += " AND date(measured_at) = :d"
        params["d"] = d
    over_cond = (" AND (usl IS NOT NULL AND value > usl) "
                 "OR (lsl IS NOT NULL AND value < lsl)")
    if over_only:
        where += over_cond

    cols = ["measured_at", "characteristic", "subgroup_no", "seq", "value",
            "usl", "lsl", "process_stage", "product_category"]
    total = _rows(registry, f"SELECT COUNT(*) AS n FROM spc_measurement WHERE {where}",
                  params, state, cap=1)
    rows = _rows(registry,
                 f"SELECT {', '.join(cols)} FROM spc_measurement WHERE {where} "
                 "ORDER BY measured_at DESC, characteristic LIMIT 200",
                 params, state, cap=200)
    if over_only:
        # 越得最多的排最前：超出幅度 = 超出量 ÷ 规格区间（无单边规格时用规格限绝对值）
        def _over_pct(r: dict) -> float:
            span = None
            if r.get("usl") is not None and r.get("lsl") is not None:
                span = (r["usl"] - r["lsl"]) or None
            v = r.get("value") or 0.0
            if r.get("usl") is not None and v > r["usl"]:
                base = span or abs(r["usl"]) or 1.0
                return round((v - r["usl"]) / base * 100, 2)
            if r.get("lsl") is not None and v < r["lsl"]:
                base = span or abs(r["lsl"]) or 1.0
                return round((r["lsl"] - v) / base * 100, 2)
            return 0.0

        rows.sort(key=_over_pct, reverse=True)
        for r in rows:
            r["over_pct"] = _over_pct(r)
        columns = [{"key": c, "label": _COL_CN.get(c, c)} for c in cols]
        columns.insert(6, {"key": "over_pct", "label": "超出幅度(%)"})
    else:
        columns = [{"key": c, "label": _COL_CN.get(c, c)} for c in cols]

    data = _envelope("spc", state, win_def)
    data.update({
        "found": bool(rows),
        "filter": {"stage": stage or "", "product_category": product_category or "",
                   "characteristic": char, "day": d, "over_only": bool(over_only)},
        "count": (total[0].get("n") if total else 0) or 0,
        "columns": columns,
        "records": rows,
        "notes": list(_SPC_NOTES),
    })
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
    "product_category": "产品分类",
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
    "source": "数据来源",    # 来料质量（incoming）
    "inspect_date": "上线检验日", "disposition": "处置结论",
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
        # 板级「产品分类 / 工段」筛选（静态映射，与 L1 同一套常量）：
        # 工段需要产线列（test_records 没有 → stage_col 置空 = 该筛选不适用）
        "scope": {"stage_col": "line_code", "pcat_col": "product_model"},
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
        "scope": {"stage_col": "line_code", "pcat_col": "product_model"},
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
        "scope": {"stage_col": "line_code", "pcat_col": "product_model"},
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
        # 测试记录无产线列 → 工段筛选对该指标不生效（产品分类仍按型号生效）
        "scope": {"stage_col": None, "pcat_col": "product_model"},
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
        # 品质板级「工段 / 类别」筛选贯通到 2 级下钻（与 L1 卡片同源同口径）：
        # 工段 = process_stage 精确匹配、类别 = defect_category（常量列名 + 命名绑定值）。
        # quality_defect 同源的两个指标（不良率 / 直通率）声明 q_scope；其余品质指标不吃这两个筛选。
        "q_scope": {"stage_col": "process_stage", "category_col": "defect_category"},
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
        # 直通率与不良率同源（quality_defect），分子 = 分母 − 不良数；板级筛选同口径贯通到 2 级
        "q_scope": {"stage_col": "process_stage", "category_col": "defect_category"},
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
    # ---------------- 来料质量（固定全量口径，不受任何筛选影响）----------------
    # ``fixed_range`` = 下钻锚定 INCOMING_WINDOW（不看板级统计口径）；
    # ``record_limit`` 抬高到 100，L3 展示「详细 IQC 检验数据」而不是只给 5 条。
    # L2 = 全量供应商分解（不做 TOP-N 截断）；L3 = 该供应商的明细记录。
    # ``incoming_batch_quality`` 是「来料批次合格率 TOP10 卡」专用的固定口径入口：
    # 与窗口口径的 KPI 下钻（``incoming_pass_rate``）分开，避免卡是固定口径、
    # 下钻却跟着板级窗口跑。
    "incoming_batch_quality": {
        "screen": "quality", "label": "来料批次合格率", "unit": "%",
        "target": 90.0, "higher_is_better": True,
        "definition": "IQC 判定合格（pass）批次数 ÷ 来料批次总数"
                      "（固定全量口径，不受统计口径 / 工段 / 类别筛选影响）。",
        "table": "incoming_batch", "date_expr": "date(arrival_time)",
        "num_expr": "SUM(CASE WHEN inspect_status='pass' THEN 1 ELSE 0 END)",
        "den_expr": "COUNT(*)",
        "group_cols": ["supplier"],
        "dimension": "供应商", "entity_cols": [["supplier", "supplier"]],
        "record_cols": ["batch_no", "part_no", "part_name", "supplier",
                        "arrival_time", "qty", "qualified_qty", "inspect_status"],
        "ask": "各供应商来料批次合格率如何？最低的是哪家？不合格批次的 IQC 结论是什么？",
        "fixed_range": INCOMING_WINDOW, "record_limit": _INCOMING_REC_LIMIT,
    },
    "incoming_line_defect": {
        "screen": "quality", "label": "来料上线不良率", "unit": "%",
        "target": 5.0, "higher_is_better": False,
        "definition": "来料上线后发现的不良数 ÷ 上线投入数"
                      "（固定全量口径，不受统计口径 / 工段 / 类别筛选影响）。",
        "table": "incoming_line_defect", "date_expr": "date(inspect_date)",
        "num_expr": "SUM(defect_qty)", "den_expr": "SUM(total_qty)",
        "group_cols": ["supplier"],
        "dimension": "供应商", "entity_cols": [["supplier", "supplier"]],
        "record_cols": ["inspect_date", "supplier", "part_no", "part_name",
                        "batch_no", "line_code", "product_model", "defect_type",
                        "defect_qty", "total_qty", "disposition"],
        "ask": "各供应商来料上线不良率如何？最高的是哪家？主要不良模式与处置结论是什么？",
        "fixed_range": INCOMING_WINDOW, "record_limit": _INCOMING_REC_LIMIT,
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
        # 分子/分母的**业务语义标签**（前端 hero / 逐日表 / 明细表列头展示用，
        # 缺省回退「分子 / 分母」——只给语义不自明的指标配，避免噪音）
        "num_label": "越限条数", "den_label": "测量条数",
        # 板级「工段 / 产品分类」筛选：两表都有直接列 → 列精确匹配（与 L1 同源）
        "spc_scope": {"stage_col": "process_stage",
                      "pcat_col": "product_category"},
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
        "num_label": "不良数", "den_label": "受检总数",
        # 同上：quality_defect 已有 process_stage / product_category 两列
        "spc_scope": {"stage_col": "process_stage",
                      "pcat_col": "product_category"},
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


def _drill_meta(defn: dict, kpi_key: str, win_label: Optional[str] = None) -> dict:
    """L2/L3 共用的指标元信息（口径与 L1 KPI 卡片一致）。

    ``num_label`` / ``den_label``：分子/分母的**业务语义标签**（如「越限条数 ÷
    测量条数」），供前端 hero 与逐日/明细表列头展示；未配置的指标不下发，
    前端回退「分子 / 分母」。
    """
    meta = {"kpi": kpi_key, "label": defn["label"], "unit": defn["unit"],
            "target": defn["target"],
            "higher_is_better": defn["higher_is_better"],
            "definition": defn["definition"],
            "dimension": defn["dimension"],
            "ask": _ask(defn["ask"], win_label)}
    if defn.get("num_label"):
        meta["num_label"] = defn["num_label"]
    if defn.get("den_label"):
        meta["den_label"] = defn["den_label"]
    return meta


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
#   work_report 原生工序词表含「SMT贴片 / DIP插件 / AOI检测 / ICT测试 / 整机组装 /
#   包装入库」等**带工段前缀的别名**，此前未登记 → 落入「其他」桶（SMT 段整体缺失）。
#   此处补别名，使 work_report 工序落到正确工段（新增行 meanwhile 用 station_detail 同名工序）。
_STEP_STAGE: dict[str, str] = {
    "锡膏印刷": "SMT段", "SPI": "SMT段", "贴片": "SMT段", "回流焊": "SMT段",
    "AOI": "SMT段", "插件": "DIP段", "波峰焊": "DIP段", "清洗": "DIP段",
    "组装": "组装段", "包装": "组装段", "ICT": "测试段", "功能测试": "测试段",
    "测试": "测试段", "老化": "测试段", "返修": "返修段",
    # --- 别名补齐（work_report 原生工序名）---
    "SMT贴片": "SMT段", "AOI检测": "SMT段", "DIP插件": "DIP段",
    "ICT测试": "测试段", "整机组装": "组装段", "包装入库": "组装段",
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


#: 柏拉图展示条数：只列前 N 项，其余（含数据中本就名为「其他」的项）并入末位「其他」。
PARETO_TOP_N = 10
#: 归并后的末位类别名（所有柏拉图统一口径）。
PARETO_OTHER = "其他"


def _pareto_cum(pareto_rows: list[dict]) -> list[dict]:
    """帕累托排序累积：``{name, value, share, cum_share}``。

    ``cum_share`` 是「关键少数」的判定依据（累计首达 80% 的前几类）。
    L1 累计不良柏拉图 / SPC 柏拉图 / L3 缺陷帕累托三处同源，口径一致。

    展示口径：只保留前 ``PARETO_TOP_N`` 项，**其余项目（含数据中本就名为「其他」
    的项）合并为末位「其他」行**（``aggregated=True``，不可作为下钻口径）。
    """
    total = sum((r.get("value") or 0) for r in pareto_rows)
    if total <= 0:
        return []
    head: list[dict] = []
    other_val = 0.0
    other_cnt = 0
    for r in pareto_rows:
        val = r.get("value") or 0
        if val <= 0:
            continue
        name = r.get("name") or "—"
        if name == PARETO_OTHER or len(head) >= PARETO_TOP_N:
            other_val += val
            other_cnt += 1
            continue
        head.append({"name": name, "value": val})
    if other_cnt:
        head.append({"name": PARETO_OTHER, "value": other_val,
                     "aggregated": True, "folded_count": other_cnt})
    out: list[dict] = []
    cum = 0.0
    for r in head:
        val = r["value"]
        share = round(100.0 * val / total, 1)
        cum = round(cum + share, 1)
        row = {"name": r["name"], "value": val,
               "share": share, "cum_share": cum}
        if r.get("aggregated"):
            row["aggregated"] = True
            row["folded_count"] = r["folded_count"]
        out.append(row)
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


def _days_of(daily: list[dict], defn: dict,
             dates: Optional[list[str]] = None) -> tuple[list[dict], int, int]:
    """逐日序列（按窗口日期，缺日 empty）+ 汇总分子/分母。"""
    seq = dates if dates is not None else _WINDOW_DATES
    by_date = {r.get("d"): r for r in daily}
    days: list[dict] = []
    tot_num = tot_den = 0
    for d in seq:
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


def _l2_items(defn: dict, rows: list[dict], daily: list[dict],
              dates: Optional[list[str]] = None) -> list[dict]:
    """分组行 → L2 明细项（实体 / 状态 / 分子分母 / 逐日趋势 / 排名）。

    通用 L2（``build_drill``）与「不良类型下钻」（``build_pareto_drill``）
    共用同一构造器，保证两条入口的行结构与排序口径完全一致。
    """
    trends: dict[str, list] = {}
    if daily:
        bucket: dict[str, list[dict]] = {}
        for r in daily:
            bucket.setdefault(_drill_name(defn, r), []).append(r)
        trends = {name: _trend(sub, dates) for name, sub in bucket.items()}

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


def build_pareto_drill(registry, screen: str, kpi_key: str, defect_type: str,
                       *, stage: Optional[str] = None,
                       category: Optional[str] = None,
                       prod_category: Optional[str] = None,
                       window: str = "window") -> dict:
    """L1 累计不良柏拉图 → 不良类型下钻（L2）：该 defect_type 按产线 · 型号分解。

    ``window`` 缺省 = 板级「最近 7 天」同口径；``all`` = **不限窗口**，``day``/``month``
    = 与柏拉图交互组件左右两块面板各自的期间精确对齐。

    可选筛选（与组件当前「产品分类/类别/工段」选择一致，全部为命名绑定参数）：
    ``stage`` / ``category`` / ``prod_category``。

    行结构与 ``build_drill`` 的 L2 完全同构（``_l2_items`` 同一构造器），
    额外回传 ``defect_type`` 供面包屑/标题标注，并置 ``terminal=True``
    ——这是「不良类型」维度的终点视图，不再继续下钻实体（实体下钻走
    L2 产线·型号入口，避免两种维度在同一层级混用）。
    """
    defn = DRILL_DEFS[kpi_key]
    state: dict[str, Any] = {"reasons": []}
    win_def = resolve_window(window)
    dates = _window_dates(win_def)
    params: dict[str, Any] = {"defect_type": defect_type}
    group = ", ".join(defn["group_cols"])
    date_expr = defn["date_expr"]
    cond = "defect_type = :defect_type"
    if win_def.get("start") and win_def.get("end"):
        params["start"], params["end"] = win_def["start"], win_def["end"]
        cond += f" AND {date_expr} BETWEEN :start AND :end"
    if stage:
        cond += " AND process_stage = :stage"
        params["stage"] = stage
    if category:
        cond += " AND defect_category = :category"
        params["category"] = category
    if prod_category:
        cond += " AND product_category = :pcat"
        params["pcat"] = prod_category
    rows = _rows(registry,
                 f"SELECT {group}, {defn['num_expr']} AS num, "
                 f"{defn['den_expr']} AS den FROM {defn['table']} "
                 f"WHERE {cond} GROUP BY {group}", params, state, cap=_AGG_ROW_CAP)
    daily: list[dict] = []
    if dates:
        daily = _rows(registry,
                      f"SELECT {group}, {date_expr} AS d, {defn['num_expr']} AS num, "
                      f"{defn['den_expr']} AS den FROM {defn['table']} "
                      f"WHERE {cond} GROUP BY {group}, {date_expr}",
                      params, state, cap=_AGG_ROW_CAP)
    else:
        # window="all"：不限窗口 → 逐日趋势序列由数据自带的日期集合决定
        daily = _rows(registry,
                      f"SELECT {group}, {date_expr} AS d, {defn['num_expr']} AS num, "
                      f"{defn['den_expr']} AS den FROM {defn['table']} "
                      f"WHERE {cond} GROUP BY {group}, {date_expr}",
                      params, state, cap=_AGG_ROW_CAP)
        dates = sorted({r["d"] for r in daily if r.get("d")})

    data = _envelope(screen, state, win_def)
    data.update(_drill_meta(defn, kpi_key, win_def.get("label")))
    # 口径补注：本次下钻的筛选范围（与柏拉图交互组件当前「产品分类/类别/工段」选择一致）
    bits = []
    if prod_category:
        bits.append(f"产品分类 {prod_category}")
    if stage:
        bits.append(f"工段 {stage}")
    if category:
        bits.append(f"类别 {category}")
    if bits:
        data["definition"] = f"{data['definition']}；本次筛选：{' · '.join(bits)}"
    data.update({
        "level": 2, "rows": _l2_items(defn, rows, daily, dates),
        "kind": "pareto", "terminal": True, "defect_type": defect_type,
        "stage": stage or "", "category": category or "",
        "prod_category": prod_category or "",
        "pareto_label": defn.get("pareto_label") or "不良类型",
    })
    return data


def build_drill(registry, screen: str, kpi_key: str,
                entity: Optional[dict] = None, step: Optional[str] = None,
                station: Optional[str] = None,
                window: str = "window",
                stage: Optional[str] = None,
                product_category: Optional[str] = None,
                category: Optional[str] = None) -> dict:
    """看板下钻聚合。

    ``entity`` 为空 = L2（按维度全量分解）；非空 = L3（单实体 + 工段/工序/工位分解）；
    再带 ``step`` = L4（该工序的工位明细）；再带 ``station`` = L5（该工位逐日 + 过站记录）。
    ``window`` 与所看板级口径一致（缺省 = 最近 7 天），保证下钻与 L1 同口径。

    ``stage`` / ``product_category``：与制造板级「工段 / 产品分类」筛选同口径——**只对在
    ``DRILL_DEFS`` 里声明了 ``scope`` 的指标（制造看板四个 KPI）生效**；
    ``stage`` / ``category``：与品质板级「工段 / 类别」筛选同口径——**只对声明了
    ``q_scope`` 的指标（不良率 / 直通率，与 L1 卡片同源）生效**。
    两者都未声明的指标忽略这些参数（行为与改动前逐字段一致，不因多传参数而漂移）。

    纪律同 L1 聚合：只读 + 命名绑定参数 + 异常兜底（记 degraded_reason，绝不抛出）。
    """
    defn = DRILL_DEFS[kpi_key]
    state: dict[str, Any] = {"reasons": []}
    # 固定全量口径指标（来料批次不良 / 来料上线不良率）：下钻锚定 defn 自带的固定区间，
    # 完全不看板级统计口径（日/周/月）——这是「不受任何筛选影响」的落点（见 INCOMING_WINDOW）。
    win_def = (dict(defn["fixed_range"]) if defn.get("fixed_range")
               else resolve_window(window))
    dates = _window_dates(win_def)
    params = {"start": win_def["start"], "end": win_def["end"]}
    group = ", ".join(defn["group_cols"])
    date_expr = defn.get("date_expr")
    # 板级筛选折进 WHERE：常量片段 + 命名绑定参数（取值取常量映射，绝不拼接用户输入）。
    # 制造四指标走 ``scope``（工段 / 产品分类）、品质「不良率 / 直通率」走 ``q_scope``
    # （工段 / 类别）、SPC 两指标走 ``spc_scope``（工段 / 产品分类，均为直接列
    # 精确匹配），三者互斥；都未声明的指标片段恒为空串 → 与改动前逐字段一致。
    q_scope = bool(defn.get("q_scope"))
    spc_scope = bool(defn.get("spc_scope"))
    if q_scope:
        scope_frag, params = _quality_scope(defn, stage, category, params)
        scope_cn = _quality_scope_cn(stage, category) if scope_frag else ""
    elif spc_scope:
        scope_frag, params = _spc_scope(defn, stage, product_category, params)
        scope_cn = _scope_cn(stage, product_category) if scope_frag else ""
    else:
        scope_frag, params = _drill_scope(defn, stage, product_category, params)
        scope_cn = _scope_cn(stage, product_category) if scope_frag else ""
    # 「本次筛选」回显与 L1 各自的筛选回显同形
    # （品质 = 工段 + 类别；制造 / SPC = 工段 + 产品分类）
    if q_scope:
        filter_echo = {"stage": stage or "", "category": category or ""}
    else:
        filter_echo = {"stage": stage or "", "product_category": product_category or ""}
    # 无 date_expr = 快照类指标（库存/里程碑/工单等），不做时间窗口过滤，逐日趋势留空
    window_where = f"{date_expr} BETWEEN :start AND :end" if date_expr else "1 = 1"
    base_where = f"{window_where}{scope_frag}"
    # 声明了 scope（制造看板四指标）/ spc_scope（SPC 两指标）的指标＝ 板级筛选
    # 生效的那个口径：其 L2 明细页追加「整体汇总 / 整体逐日 / 关联原始记录」
    # 三块补充数据。
    # 注意：``q_scope``（品质不良率 / 直通率）**不开**这条通道 —— 这是既有行为，
    # 由 ``test_l2_detail_only_for_scope_declared_kpis`` 守护，不得放宽。
    detail_l2 = bool(defn.get("scope") or defn.get("spc_scope"))

    if not entity:
        rows = _rows(registry,
                     f"SELECT {group}, {defn['num_expr']} AS num, "
                     f"{defn['den_expr']} AS den FROM {defn['table']} "
                     f"WHERE {base_where} GROUP BY {group}", params, state,
                     cap=_AGG_ROW_CAP)
        daily: list[dict] = []
        if date_expr:
            daily = _rows(registry,
                          f"SELECT {group}, {date_expr} AS d, "
                          f"{defn['num_expr']} AS num, {defn['den_expr']} AS den "
                          f"FROM {defn['table']} WHERE {base_where} "
                          f"GROUP BY {group}, {date_expr}", params, state,
                          cap=_AGG_ROW_CAP)

        items = _l2_items(defn, rows, daily, dates)
        meta = _drill_meta(defn, kpi_key, win_def.get("label"))
        if scope_cn:
            meta["definition"] = f"{meta['definition']}；本次筛选：{scope_cn}"

        data = _envelope(screen, state, win_def)
        data.update(meta)
        data.update({"level": 2, "rows": items})
        if detail_l2:
            data.update(_l2_detail(registry, defn, items, base_where, params,
                                   dates, state))
        if scope_frag:
            data["filter"] = filter_echo
        return data

    cond, p3 = _drill_where(defn, entity, params)
    if scope_frag:      # L3 也不再穿透板级筛选（实体之外的筛选条件一并在 SQL 里生效）
        cond += scope_frag
    # ``record_limit``：来料质量明细抬高到 100（「详细 IQC 检验数据」），其余沿用 5 条
    rec_limit = int(defn.get("record_limit") or 5)
    records = _rows(registry,
                    f"SELECT {', '.join(defn['record_cols'])} "
                    f"FROM {defn['table']} WHERE {base_where} {cond} LIMIT {rec_limit}",
                    p3, state)
    if date_expr:
        daily = _rows(registry,
                      f"SELECT {date_expr} AS d, {defn['num_expr']} AS num, "
                      f"{defn['den_expr']} AS den FROM {defn['table']} "
                      f"WHERE {base_where} {cond} GROUP BY {date_expr}",
                      p3, state)
        days, tot_num, tot_den = _days_of(daily, defn, dates)
    else:
        # 快照类指标无逐日序列：直接对单实体做一次汇总
        days = []
        agg = _rows(registry,
                    f"SELECT {defn['num_expr']} AS num, {defn['den_expr']} AS den "
                    f"FROM {defn['table']} WHERE {base_where} {cond}", p3, state)
        tot_num = (agg[0].get("num") if agg else 0) or 0
        tot_den = (agg[0].get("den") if agg else 0) or 0
    value = _pct(tot_num, tot_den)

    data = _envelope(screen, state, win_def)
    data.update(_drill_meta(defn, kpi_key, win_def.get("label")))
    data.update({
        "level": 3, "entity": _entity_of(defn, entity),
        "value": value, "numerator": tot_num, "denominator": tot_den,
        "status": _status(value, defn["target"], defn["higher_is_better"]),
        "delta": round(value - defn["target"], 1),
        "daily": days, "records": records,
        "columns": [{"key": c, "label": _COL_CN.get(c, c)}
                    for c in defn["record_cols"]],
    })
    if scope_frag:      # L3 也回显板级筛选（下钻态筛选条据此保持选中，不回退看板首页）
        data["filter"] = filter_echo

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
    if scope_frag:      # 工序 / 工位层同样受板级筛选约束（station_detail / work_report 均有线体·型号列）
        cond4 += scope_frag
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
    days, num, den = _days_of(d_rows, defn, dates)
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
