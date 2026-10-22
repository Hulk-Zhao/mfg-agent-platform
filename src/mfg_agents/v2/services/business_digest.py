"""老板经营对话 · 大单进度（R10 · 确定性成稿，不调 LLM）。

产品目标（PRD P1 R10 / U7）：「5000 件大单做到哪了」→ 整体完成率、预计完工日、
**主动指出风险**（不等老板问），进度条一眼可见。

架构判定：digest 已具备跨屏态势汇总，但「单张大单进度」是对象级问题，不是开放式
汇总——两条通道分开，避免汇总吞掉对象级提问（与 router `_has_entity` 同一纪律）。

数据口径（全部来自注册查询，不编造）：
- ``big_orders_in_progress``：未发货且进度 < 100 的订单（TMS order_fulfillment）；
- ``plan_by_model``：该型号近 7 日实际产出 → 日均产能 → 剩余量推算完工日。

纪律：
- 产能是**全产线合计**，未按订单分配——结论里明确写这句，不假装精确到单；
- 无产能记录 → 完工日留空 + 数据缺口，不拿「大概齐」补；
- 风险来自订单行（risk_type / delay_days / shortage_note）与交付日对比，
  不虚构保养计划（平台目前没有未来保养台账）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

__all__ = ["BigOrderDigest", "compose_big_order_digest", "DEFAULT_MIN_QTY"]

DEFAULT_MIN_QTY = 5000
PLAN_WINDOW_DAYS = 7


@dataclass
class BigOrderDigest:
    threshold: int = DEFAULT_MIN_QTY
    as_of: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    selected: dict[str, Any] = field(default_factory=dict)
    aggregate_progress: float = 0.0
    total_qty: int = 0
    remaining_qty: int = 0
    daily_capacity: float = 0.0
    finish_date: str = ""
    slack_days: Optional[int] = None
    risks: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""

    @property
    def has_data(self) -> bool:
        return bool(self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "as_of": self.as_of,
            "candidates": list(self.candidates),
            "selected": dict(self.selected),
            "aggregate_progress": self.aggregate_progress,
            "total_qty": self.total_qty,
            "remaining_qty": self.remaining_qty,
            "daily_capacity": round(self.daily_capacity, 1),
            "finish_date": self.finish_date,
            "slack_days": self.slack_days,
            "risks": list(self.risks),
            "data_gaps": list(self.data_gaps),
            "provenance": [dict(p) for p in self.provenance],
            "report": self.report,
            "note": "数字来自注册查询；产能为全产线合计、未按订单分配；缺口如实标注",
        }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _risk_key(row: dict[str, Any]) -> tuple:
    """选单顺序：有风险标记优先 → 延期更久 → 进度更低 → 数量更大。"""
    risk_type = str(row.get("risk_type") or "").strip()
    risky = 0 if risk_type not in ("", "正常") else 1
    return (risky, -int(_num(row.get("delay_days"))),
            _num(row.get("progress")), -_num(row.get("qty")))


def _capacity(capacity_rows: list[dict[str, Any]],
              as_of: Optional[date]) -> tuple[float, str]:
    """近 7 日日均产能（按有记录的自然日平均）+ 数据截止日。"""
    rows = [r for r in capacity_rows if _date(r.get("record_date"))]
    if not rows:
        return 0.0, ""
    latest = max(_date(r.get("record_date")) for r in rows if _date(r.get("record_date")))
    base = as_of or latest
    recent = [r for r in rows
              if 0 <= (base - _date(r.get("record_date"))).days <= PLAN_WINDOW_DAYS]
    days = {str(r.get("record_date")) for r in recent}
    produced = sum(_num(r.get("actual_qty")) for r in recent)
    return ((produced / len(days)) if days else 0.0), base.isoformat()


def compose_big_order_digest(
    rows: list[dict[str, Any]],
    capacity_lookup: Callable[[str], list[dict[str, Any]]],
    *,
    min_qty: int = DEFAULT_MIN_QTY,
    order_no: str = "",
    model: str = "",
    today: Optional[date] = None,
) -> BigOrderDigest:
    """组装大单进度成稿（纯确定性；``capacity_lookup(model)`` 返回产出记录）。"""
    rep = BigOrderDigest(threshold=max(1, int(min_qty or DEFAULT_MIN_QTY)))
    rep.provenance.append({"query": "big_orders_in_progress",
                           "rows": len(rows or [])})
    pool = [dict(r) for r in (rows or [])
            if str(r.get("stage") or "") != "发货"
            and _num(r.get("progress"), 100.0) < 100]
    if order_no:
        pool = [r for r in pool
                if str(r.get("order_no") or "").upper() == order_no.upper()]
        if not pool:
            rep.data_gaps.append(f"未找到在制订单 {order_no}（可能已发货或单号不存在）")
    if model:
        pool = [r for r in pool if str(r.get("product_model") or "") == model]
        if not pool:
            rep.data_gaps.append(f"未找到 {model} 的在制订单")
    if not pool:
        rep.report = ("老板经营 · 大单进度\n"
                      "结论：当前没有符合条件的在制订单，无法给出进度。"
                      + ("（" + "；".join(rep.data_gaps) + "）" if rep.data_gaps else ""))
        return rep

    big = [r for r in pool if _num(r.get("qty")) >= rep.threshold]
    if not big:
        rep.data_gaps.append(
            f"没有 ≥{rep.threshold} 件的在制订单；以下按当前最大在制单展示")
        big = [max(pool, key=lambda r: _num(r.get("qty")))]

    big.sort(key=_risk_key)
    rep.candidates = big
    rep.selected = dict(big[0])
    total_qty = sum(_num(r.get("qty")) for r in big)
    rep.total_qty = int(round(total_qty))
    rep.aggregate_progress = round(
        (sum(_num(r.get("qty")) * _num(r.get("progress")) for r in big) / total_qty),
        1) if total_qty else 0.0

    sel = rep.selected
    sel_model = str(sel.get("product_model") or "")
    try:
        cap_rows = capacity_lookup(sel_model) if sel_model else []
        rep.provenance.append({"query": "plan_by_model", "model": sel_model,
                               "rows": len(cap_rows)})
    except Exception as exc:  # noqa: BLE001 - 查询失败记缺口，不编造
        cap_rows = []
        rep.data_gaps.append(f"排产/产出查询失败（{exc}）")
        rep.provenance.append({"query": "plan_by_model", "model": sel_model,
                               "error": str(exc)})

    capacity, as_of = _capacity(cap_rows, today)
    rep.as_of = as_of
    rep.daily_capacity = capacity
    remaining = max(0.0, _num(sel.get("qty")) * (100 - _num(sel.get("progress"))) / 100)
    rep.remaining_qty = int(round(remaining))
    due = _date(sel.get("due_date"))
    if capacity > 0:
        finish = (date.fromisoformat(as_of) + timedelta(
            days=math.ceil(remaining / capacity)))
        rep.finish_date = finish.isoformat()
        if due:
            rep.slack_days = (due - finish).days
    else:
        rep.data_gaps.append(
            f"近 {PLAN_WINDOW_DAYS} 天无 {sel_model} 产出记录，预计完工日无法估算")

    # ---- 主动风险（订单行事实 + 交付日对比；不虚构保养计划）----
    risk_type = str(sel.get("risk_type") or "").strip()
    delay = int(_num(sel.get("delay_days")))
    if risk_type and risk_type != "正常":
        rep.risks.append(f"订单标记「{risk_type}」"
                         + (f"，已延期 {delay} 天" if delay > 0 else ""))
    elif delay > 0:
        rep.risks.append(f"已延期 {delay} 天")
    note = str(sel.get("shortage_note") or "").strip()
    if note:
        rep.risks.append(f"齐套风险：{note}")
    if due and as_of:
        overdue = (date.fromisoformat(as_of) - due).days
        if overdue > 0:
            rep.risks.append(
                f"截至数据日 {as_of}，已过客户要求交付日（{due.isoformat()}）"
                f"{overdue} 天仍未发货")
    if not rep.risks:
        rep.risks.append("未发现订单级风险标记（risk_type=正常、无缺料备注）；"
                        "仍建议按交付日跟踪")

    rep.report = _render(rep)
    return rep


def _render(rep: BigOrderDigest) -> str:
    sel = rep.selected
    progress = _num(sel.get("progress"))
    lines = ["老板经营 · 大单进度"
             + (f"（数据截至 {rep.as_of}）" if rep.as_of else "")]
    lines.append(
        f"口径：≥{rep.threshold} 件在制订单（未发货且进度<100）；"
        f"当前 {len(rep.candidates)} 张，加权完成 {rep.aggregate_progress}%。")
    lines.append(
        f"最需关注：{sel.get('order_no')}（{sel.get('product_model')} · "
        f"{int(_num(sel.get('qty')))} 件），当前在「{sel.get('stage') or '—'}」环节，"
        f"进度 {progress:g}%，客户要求 {sel.get('due_date') or '—'} 交付。")
    if rep.finish_date and rep.daily_capacity:
        tail = ""
        if rep.slack_days is not None:
            tail = (f"，比要求日提前 {rep.slack_days} 天" if rep.slack_days >= 0
                    else f"，比要求日晚 {abs(rep.slack_days)} 天")
        lines.append(
            f"预计完工：按 {sel.get('product_model')} 近 {PLAN_WINDOW_DAYS} 日实际产出均值 "
            f"{rep.daily_capacity:.0f} 套/天推算，剩余 {rep.remaining_qty} 套预计 "
            f"{rep.finish_date} 完工{tail}。"
            "（产能为全产线合计，未按订单分配，属估算口径）")
    else:
        lines.append(f"预计完工：无法估算（剩余 {rep.remaining_qty} 套，缺产能数据）。")
    lines.append("主动风险：")
    for i, risk in enumerate(rep.risks, 1):
        lines.append(f"{i}. {risk}")
    for gap in rep.data_gaps:
        lines.append(f"· 数据缺口：{gap}")
    return "\n".join(lines)
