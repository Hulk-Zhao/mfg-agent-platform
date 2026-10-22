"""呆滞料随时问 + 退供评审（R11 · U6）。

产品目标：一年一盘 → 随时问「超 180 天没动的料」。返回超龄清单、资金占用、
供应商分布与处置建议；**退供涉及资金回收/折价，必须人工点头**。

诚实边界（重要）：
- 平台没有物料替代 / BOM 关联表，**不能自动识别「可转用」**——只列缺口与建议，
  不编造「4 个可转用」；
- 没有供应商退货窗口 / 折价政策表，退供只能做「评审申请」，不能承诺可退；
- 动钱当前无专属槽位（Q7 未拍板），interim 复用 ``hitl.disposition``
  （来料处置）会签链，工单上明确标注「interim · 动钱需人管」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "AgingDigest",
    "RETURN_ACTIONS",
    "compose_aging_digest",
    "DEFAULT_AGING_DAYS",
]

DEFAULT_AGING_DAYS = 180

#: 退供评审动作（复用 hitl.disposition 槽位；propose 仍只有质量岗位持有）
RETURN_ACTIONS: list[dict[str, Any]] = [
    {"key": "return_supplier", "label": "退供（退回供应商）",
     "propose": ["SQE"], "cosign": ["采购", "财务"], "require_comment": True,
     "impact": "回收资金 / 供应商折价；需确认退货窗口", "followup": "采购谈判 / 财务核销"},
    {"key": "transfer", "label": "转用 / 替代",
     "propose": ["SQE"], "cosign": ["工程"], "require_comment": True,
     "impact": "不减资金占用，但避免报废", "followup": "工程确认 / 批次标记"},
    {"key": "scrap", "label": "报废",
     "propose": ["SQE"], "cosign": ["财务"], "require_comment": True,
     "impact": "确认损失 · 需核销", "followup": "财务核销 / 现场清理"},
]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class AgingDigest:
    days: int = DEFAULT_AGING_DAYS
    rows: list[dict[str, Any]] = field(default_factory=list)
    total_on_hand: int = 0
    total_value: float = 0.0
    oldest_days: int = 0
    suppliers: list[dict[str, Any]] = field(default_factory=list)
    transfer_candidates: list[dict[str, Any]] = field(default_factory=list)
    return_candidates: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    spend_gate: dict[str, Any] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""

    @property
    def has_data(self) -> bool:
        return bool(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "rows": list(self.rows),
            "count": len(self.rows),
            "total_on_hand": self.total_on_hand,
            "total_value": round(self.total_value, 2),
            "oldest_days": self.oldest_days,
            "suppliers": list(self.suppliers),
            "transfer_candidates": list(self.transfer_candidates),
            "return_candidates": list(self.return_candidates),
            "transfer_count": len(self.transfer_candidates),
            "return_count": len(self.return_candidates),
            "risks": list(self.risks),
            "data_gaps": list(self.data_gaps),
            "spend_gate": dict(self.spend_gate),
            "provenance": [dict(p) for p in self.provenance],
            "report": self.report,
            "note": ("库龄口径 = material_inventory.age_days；金额 = 在库 × 单位成本；"
                     "可转用因缺 BOM/替代关系表无法自动识别，不编造"),
        }


def compose_aging_digest(rows: Iterable[dict[str, Any]],
                         days: int = DEFAULT_AGING_DAYS) -> AgingDigest:
    """超龄料汇总（纯确定性，不调 LLM）。"""
    rep = AgingDigest(days=max(1, int(days or DEFAULT_AGING_DAYS)))
    pool = [dict(r) for r in (rows or [])
            if _num(r.get("age_days")) >= rep.days]
    pool.sort(key=lambda r: (-_num(r.get("age_days")), str(r.get("part_no"))))
    rep.rows = pool
    rep.provenance.append({"query": "material_aging_over_days",
                           "params": {"days": rep.days}, "rows": len(pool)})
    if not pool:
        rep.report = (f"呆滞料盘点（库龄 ≥ {rep.days} 天）\n"
                      "结论：当前没有超过该库龄阈值的物料。")
        rep.data_gaps.append(f"没有库龄 ≥ {rep.days} 天的物料")
        return rep

    rep.total_on_hand = int(round(sum(_num(r.get("on_hand_qty")) for r in pool)))
    rep.total_value = sum(_num(r.get("on_hand_qty")) * _num(r.get("unit_cost"))
                          for r in pool)
    rep.oldest_days = int(max(_num(r.get("age_days")) for r in pool))

    # 供应商字段可能因岗位权限被脱敏（如仓储看不到供应商）——
    # 脱敏值不能当真实供应商分组，否则 13 条会坍缩成一个「供应商***」。
    masked_supplier = any("*" in str(r.get("supplier") or "") for r in pool)
    if masked_supplier:
        rep.data_gaps.append(
            "供应商字段按当前岗位脱敏，无法按供应商分组（采购/质量岗位可见）")
        rep.suppliers = []
    else:
        grouped: dict[str, dict[str, Any]] = {}
        for row in pool:
            supplier = str(row.get("supplier") or "").strip() or "未标注供应商"
            g = grouped.setdefault(supplier, {"supplier": supplier, "count": 0,
                                              "part_nos": [], "value": 0.0})
            g["count"] += 1
            g["part_nos"].append(str(row.get("part_no") or ""))
            g["value"] += _num(row.get("on_hand_qty")) * _num(row.get("unit_cost"))
        rep.suppliers = sorted(
            [{**g, "value": round(g["value"], 2)} for g in grouped.values()],
            key=lambda g: (-g["value"], g["supplier"]))

    # 可转用：平台无替代/BOM 关系表，只能如实留空 + 缺口
    rep.data_gaps.append("无物料替代 / BOM 关系表，无法自动识别可转用料号")
    # 退供候选：有供应商 + 有在库；是否可退仍需人工确认退货窗口/折价
    rep.return_candidates = [
        {"part_no": r.get("part_no"), "part_name": r.get("part_name"),
         "supplier": "" if masked_supplier else r.get("supplier"),
         "supplier_masked": masked_supplier,
         "on_hand_qty": int(_num(r.get("on_hand_qty"))),
         "age_days": int(_num(r.get("age_days"))),
         "value": round(_num(r.get("on_hand_qty")) * _num(r.get("unit_cost")), 2)}
        for r in pool if str(r.get("supplier") or "").strip()
        and _num(r.get("on_hand_qty")) > 0
    ]
    for row in pool:
        if _num(row.get("reserved_qty")) > 0:
            rep.risks.append(
                f"{row.get('part_no')} 有预留 {int(_num(row.get('reserved_qty')))}，"
                "退供/转用前需先解绑相关订单")
    rep.risks.insert(0, f"合计资金占用约 ¥{rep.total_value:,.0f}"
                       f"（{len(pool)} 条，最长库龄 {rep.oldest_days} 天）")

    rep.spend_gate = {
        "required": True,
        "slot": "disposition",
        "interim": True,
        "reason": "退供涉及资金回收/折价，按治理铁律必须人工确认；"
                  "当前无专属「动钱」槽位，interim 复用来料处置（disposition）会签链",
        "amount": round(rep.total_value, 2),
    }
    rep.report = _render(rep)
    return rep


def _render(rep: AgingDigest) -> str:
    lines = [f"呆滞料盘点（库龄 ≥ {rep.days} 天，截至数据快照）",
             f"超龄料 {len(rep.rows)} 条，最长库龄 {rep.oldest_days} 天，"
             f"合计在库 {rep.total_on_hand} 件、资金占用约 ¥{rep.total_value:,.0f}。",
             "供应商分布："]
    if rep.suppliers:
        for g in rep.suppliers:
            lines.append(f"- {g['supplier']}：{g['count']} 条，约 ¥{g['value']:,.0f}"
                         f"（{'、'.join(g['part_nos'][:5])}"
                         + (" 等" if len(g["part_nos"]) > 5 else "") + "）")
    else:
        lines.append("- 当前岗位无权查看供应商字段（已脱敏），无法按供应商分组")
    lines.append(f"处置建议：转用优先（{len(rep.transfer_candidates)} 条可自动识别）"
                 f"、退供其次（{len(rep.return_candidates)} 条有供应商可评审）、"
                 "报废兜底；评审通过前冻结补货。")
    lines.append("退供评审：涉及资金回收/折价，**需人工确认**"
                 "（当前无专属动钱槽位，interim 复用来料处置会签链）。")
    for risk in rep.risks:
        lines.append(f"· 风险：{risk}")
    for gap in rep.data_gaps:
        lines.append(f"· 数据缺口：{gap}")
    return "\n".join(lines)
