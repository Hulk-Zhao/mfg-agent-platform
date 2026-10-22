"""对客交期答复组装（R5/U8：销售当场答得上，对外留痕）。

纪律（对标开沿参考图场景 2「交期答复：客户一问，当场答得上」）：
- 所有数字来自注册查询（``atp_by_model`` / ``plan_by_model``），**不编造**；
- 组装是确定性的（算术 + 模板），不调 LLM——LLM 只在 Agent 层做意图与措辞；
- 数据缺口如实进草稿的「待确认」段，绝不用假设数补齐；
- 答复是对外承诺：本服务只产草稿，放行必须走 ``hitl.eta`` 人工确认
  （闸门理由见 ``gate_reasons.yaml``：说给客户的正式交期，超期即违约）。

典型用法（Agent 工具 ``draft_delivery_reply``）：销售问「SO-3108 月底能交吗」
→ 本服务组装「现货先发 X 套 / 剩余排产 Y 日完工 / 比约定早（晚）N 天」草稿
→ Agent 带草稿建 ``hitl.eta`` 工单 → 人工确认后放行，记录落工作流审计。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

#: 排产基准窗口（近 N 天实际产出均值）——口径写进草稿，对客可解释
PLAN_WINDOW_DAYS = 7

#: verdict 取值
VERDICT_ON_TIME = "on_time"      # 能按期（现货全覆盖或排产赶得上）
VERDICT_LATE = "late"            # 排产赶不上，差 N 天
VERDICT_NO_DATA = "no_data"      # 关键数据缺失，草稿只列已知数 + 待核实项


@dataclass
class DeliveryReply:
    """交期答复组装结果（草稿 + 全部中间量，供审计与测试断言）。"""

    ok: bool
    model: str
    qty: int
    need_date: str
    on_hand: int = 0             # 现货可用量（ATP：在库 − 预留，跨仓汇总）
    first_ship: int = 0          # 可先发数量（现货 ∩ 需求量）
    to_produce: int = 0          # 需排产数量
    daily_capacity: float = 0.0  # 近 7 日实际产出均值（套/天）
    finish_date: str = ""        # 排产完工日（ISO）
    slack_days: int = 0          # 约定日 − 完工日（正=提前，负=延误）
    verdict: str = VERDICT_NO_DATA
    draft: str = ""
    data_gaps: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "model": self.model, "qty": self.qty,
            "need_date": self.need_date, "on_hand": self.on_hand,
            "first_ship": self.first_ship, "to_produce": self.to_produce,
            "daily_capacity": round(self.daily_capacity, 1),
            "finish_date": self.finish_date, "slack_days": self.slack_days,
            "verdict": self.verdict, "draft": self.draft,
            "data_gaps": list(self.data_gaps),
            "provenance": [dict(p) for p in self.provenance],
        }


def parse_need_date(text: str, today: Optional[date] = None) -> tuple[str, list[str]]:
    """解析约定交期：ISO 日期 / 「月底」「月末」「下月底」；解析不了返回空 + 缺口。

    不做自由自然语言猜测——「下周」「过几天」这类模糊说法一律进 data_gaps，
    让 Agent 反问她确认，而不是替客户定一个日子。
    """
    base = today or date.today()
    raw = (text or "").strip()
    gaps: list[str] = []
    if not raw:
        return "", ["未提供约定交期"]
    try:
        return date.fromisoformat(raw).isoformat(), gaps
    except ValueError:
        pass
    if raw in ("月底", "月末"):
        nxt = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        return (nxt - timedelta(days=1)).isoformat(), gaps
    if raw in ("下月底", "下月末"):
        month_end = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        nxt = (month_end.replace(day=28) + timedelta(days=4)).replace(day=1)
        return (nxt - timedelta(days=1)).isoformat(), gaps
    gaps.append(f"约定交期无法解析（{raw!r}）——需确认为具体日期")
    return "", gaps


def compose_delivery_reply(
    model: str,
    qty: int,
    need_date: str,
    run_query: Callable[[str, dict], list[dict]],
    today: Optional[date] = None,
) -> DeliveryReply:
    """组装对客交期答复草稿（确定性，数字全部来自 ``run_query``）。

    Args:
        model: 产品型号（ATP / 排产查询的 ``:model`` 参数）。
        qty: 客户需求量（套）。
        need_date: 约定交期（ISO / 月底 / 下月底；无法解析进 data_gaps）。
        run_query: 查询执行器 ``(name, params) -> rows``（Agent 层传入，
            带 user_ctx 与权限/脱敏同源）。
        today: 基准日（测试可注入；缺省 date.today()）。
    """
    base = today or date.today()
    model = str(model or "").strip()
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        qty = 0
    rep = DeliveryReply(ok=False, model=model, qty=qty, need_date="")
    gaps: list[str] = []
    if not model:
        gaps.append("未提供产品型号")
    if qty <= 0:
        gaps.append(f"需求量无效（{qty!r}）——需确认为正整数")

    # ---- 约定交期 ----
    iso_need, need_gaps = parse_need_date(need_date, today=base)
    rep.need_date = iso_need
    gaps.extend(need_gaps)

    # ---- 现货（ATP：在库 − 预留，跨仓汇总） ----
    on_hand = 0
    if model:
        try:
            atp_rows = run_query("atp_by_model", {"model": model}) or []
            rep.provenance.append({"query": "atp_by_model",
                                   "rows": len(atp_rows)})
            # 无库存记录 = 零现货（事实）；查询异常 = 未知（缺口，见 except）
            on_hand = sum(int(r.get("available_qty") or 0) for r in atp_rows)
        except Exception as exc:  # noqa: BLE001 - 查询失败按缺口处理，不编造
            gaps.append(f"ATP 查询失败（{exc}）")
            rep.provenance.append({"query": "atp_by_model", "error": str(exc)})

    # ---- 排产（近 7 日实际产出均值 → 日均产能） ----
    daily_capacity = 0.0
    if model:
        try:
            plan_rows = run_query("plan_by_model", {"model": model}) or []
            rep.provenance.append({"query": "plan_by_model",
                                   "rows": len(plan_rows)})
            recent = [r for r in plan_rows
                      if _within(r.get("record_date"), base, PLAN_WINDOW_DAYS)]
            days = {str(r.get("record_date")) for r in recent}
            produced = sum(int(r.get("actual_qty") or 0) for r in recent)
            if days:
                daily_capacity = produced / len(days)
            if daily_capacity <= 0:
                gaps.append(f"近 {PLAN_WINDOW_DAYS} 天无 {model} 的实际产出记录，"
                            "排产完工日无法估算")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"排产查询失败（{exc}）")
            rep.provenance.append({"query": "plan_by_model", "error": str(exc)})

    # ---- 算术（确定性） ----
    rep.on_hand = on_hand
    rep.daily_capacity = daily_capacity
    rep.first_ship = min(on_hand, qty) if qty > 0 else 0
    rep.to_produce = max(0, qty - on_hand) if qty > 0 else 0

    if qty > 0 and model and not gaps:
        rep.ok = True
    if rep.to_produce > 0 and daily_capacity > 0 and iso_need:
        days_needed = math.ceil(rep.to_produce / daily_capacity)
        finish = base + timedelta(days=days_needed)
        rep.finish_date = finish.isoformat()
        rep.slack_days = (date.fromisoformat(iso_need) - finish).days
        rep.verdict = VERDICT_ON_TIME if rep.slack_days >= 0 else VERDICT_LATE
    elif rep.to_produce == 0 and qty > 0 and on_hand > 0:
        rep.verdict = VERDICT_ON_TIME   # 现货全覆盖
        rep.finish_date = base.isoformat()
        rep.slack_days = ((date.fromisoformat(iso_need) - base).days
                          if iso_need else 0)
    else:
        rep.verdict = VERDICT_NO_DATA

    # 诚实闸门：任何数据缺口 → 一律 no_data。算术值仍算（信息量），但
    # 「能不能按期」的结论不基于带缺口的输入——ATP 查询失败时 on_hand=0
    # 是「未知」不是「零库存」，据此算出的完工日是编造。
    if gaps:
        rep.verdict = VERDICT_NO_DATA

    rep.data_gaps = gaps
    rep.draft = _render_draft(rep)
    return rep


def _within(value: Any, base: date, window_days: int) -> bool:
    """record_date 是否落在基准日前 window_days 窗口内（含未来 1 天容差）。"""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return False
    delta = (base - d).days
    return -1 <= delta <= window_days


def _render_draft(rep: DeliveryReply) -> str:
    """草稿正文：结论先行 + 数字 + 口径 + 待确认项。不编造、不美化。"""
    head = (f"关于 {rep.model} {rep.qty} 套的交期答复（草稿，经人工确认后方可发送）")
    lines = [head]
    if rep.verdict == VERDICT_ON_TIME and rep.to_produce == 0:
        lines.append(
            f"结论：可以按期。现货可用 {rep.on_hand} 套（在库减预留），"
            f"覆盖全部 {rep.qty} 套需求，可立即安排发运。")
    elif rep.verdict == VERDICT_ON_TIME:
        lines.append(
            f"结论：可以按期。现货 {rep.on_hand} 套可先发；"
            f"剩余 {rep.to_produce} 套按近 {PLAN_WINDOW_DAYS} 日均产 "
            f"{rep.daily_capacity:.0f} 套/天排产，预计 {rep.finish_date} 完工，"
            f"比约定交期（{rep.need_date}）提前 {rep.slack_days} 天。")
    elif rep.verdict == VERDICT_LATE:
        lines.append(
            f"结论：按当前产能赶不上约定交期。现货 {rep.on_hand} 套可先发；"
            f"剩余 {rep.to_produce} 套预计 {rep.finish_date} 完工，"
            f"比约定交期（{rep.need_date}）晚 {abs(rep.slack_days)} 天。"
            f"可选：分批先发现货 / 协商延期 / 转加急（成本上升）。")
    else:
        if any("ATP" in g for g in rep.data_gaps):
            stock_line = "现货数据待核实（查询失败或无记录）。"
        elif rep.on_hand:
            stock_line = f"目前已知：现货可用 {rep.on_hand} 套。"
        else:
            stock_line = "目前无可用现货。"
        lines.append(f"结论：数据不足，无法给出可靠交期，需人工核实后再答复客户。{stock_line}")
    if rep.to_produce > 0 and rep.daily_capacity > 0:
        lines.append(
            f"口径：ATP = 在库 − 预留（跨仓汇总）；排产以近 {PLAN_WINDOW_DAYS} 天"
            f"实际产出均值 {rep.daily_capacity:.0f} 套/天为基准，未含加急/扩产。")
    if rep.data_gaps:
        lines.append("待确认（数据缺口，不编造）："
                     + "；".join(rep.data_gaps))
    lines.append("本稿由平台按注册查询数据组装；对外发送须经 hitl.eta 人工确认。")
    return "\n".join(lines)


def draft_as_hitl_prompt(rep: DeliveryReply) -> str:
    """草稿 → HITL 工单提示词（人工确认卡上要能看到完整口径）。"""
    return rep.draft


__all__ = ["DeliveryReply", "compose_delivery_reply", "parse_need_date",
           "draft_as_hitl_prompt", "VERDICT_ON_TIME", "VERDICT_LATE",
           "VERDICT_NO_DATA", "PLAN_WINDOW_DAYS"]
