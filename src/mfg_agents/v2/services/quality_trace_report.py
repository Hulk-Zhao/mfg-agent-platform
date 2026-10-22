"""质量追溯三路并查成稿（R7 · 串行三查，不新增并行编排原语）。

产品目标（PRD P1 R7 / 用户故事 U4）：「客诉电机异响，跨部门跑生产/来料/设备
两三天下落不明」→ 一句话触发三源并查，锁定批次 + 同批在制工单清单，追溯报告
自动成稿。

架构判定（D11）：一期在工具循环内**串行**三查——
``quality_defect_scope_by_batch``（生产记录）→ ``incoming_batch_by_no``（来料检验）
→ ``equipment_log_by_batch``（设备状态），再补 ``inprocess_wo_by_batch``（同批影响面），
本服务只做确定性组装（不调 LLM、不编造）；并行 fan-out 不在本期引入。

纪律：
- 每一路查询失败/无权限 → 记 data_gap 并如实写进草稿，**不拿空结果当"没有异常"**；
- 批次数值全部来自注册查询，草稿里的每条数字可回溯到查询名；
- 三路都拿不到批次主记录（生产/来料均空）→ ``verdict=no_data``，只列缺口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["QualityTraceReport", "compose_quality_trace", "VERDICT_TRACED",
           "VERDICT_PARTIAL", "VERDICT_NO_DATA"]

VERDICT_TRACED = "traced"        # 批次锁定 + 至少一路有实质记录
VERDICT_PARTIAL = "partial"      # 批次锁定但多路缺口
VERDICT_NO_DATA = "no_data"      # 批次主记录不存在/不可读


@dataclass
class QualityTraceReport:
    batch_no: str
    verdict: str = VERDICT_NO_DATA
    locked: dict[str, Any] = field(default_factory=dict)
    production: list[dict[str, Any]] = field(default_factory=list)
    incoming: dict[str, Any] = field(default_factory=dict)
    equipment: list[dict[str, Any]] = field(default_factory=list)
    abnormal_count: int = 0
    wip_orders: list[dict[str, Any]] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_no": self.batch_no, "verdict": self.verdict,
            "locked": dict(self.locked),
            "production": list(self.production),
            "incoming": dict(self.incoming),
            "equipment": list(self.equipment),
            "abnormal_count": self.abnormal_count,
            "wip_orders": list(self.wip_orders),
            "wip_count": len(self.wip_orders),
            "data_gaps": list(self.data_gaps),
            "provenance": [dict(p) for p in self.provenance],
            "report": self.report,
            "note": ("三路串行取证（生产记录 → 来料检验 → 设备状态）+ 同批影响面；"
                     "数字全部来自注册查询，缺口如实标注，未编造"),
        }


def _num(value: Any) -> str:
    """数量格式化：整数值去掉小数点，其余保留原样（不四舍五入编造）。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    return str(int(n)) if n.is_integer() else f"{n:g}"


def _is_abnormal(value: Any) -> bool:
    text = str(value).strip().lower()
    return text in ("1", "true", "yes", "y", "异常")


def compose_quality_trace(batch_no: str,
                          run_query: Callable[[str, dict], list[dict]]
                          ) -> QualityTraceReport:
    """串行三查 + 同批影响面，组装可复核的追溯草稿。

    ``run_query(name, params) -> rows``：Agent 层注入（带 user_ctx 与权限/脱敏）；
    本函数不吞异常——每路单独 try，失败记缺口后继续下一路。
    """
    batch = str(batch_no or "").strip()
    rep = QualityTraceReport(batch_no=batch)
    if not batch:
        rep.data_gaps.append("未提供批次号")
        rep.report = _render(rep)
        return rep

    def _query(name: str, params: dict, label: str) -> list[dict]:
        try:
            rows = run_query(name, params) or []
            rep.provenance.append({"query": name, "label": label,
                                   "rows": len(rows)})
            return rows
        except Exception as exc:  # noqa: BLE001 - 单路失败记缺口，不阻断其余
            rep.data_gaps.append(f"{label}查询失败（{exc}）")
            rep.provenance.append({"query": name, "label": label,
                                   "error": str(exc)})
            return []

    # ① 批次主记录（生产/来料之一存在即可锁定批次）
    trace_rows = _query("batch_trace_full", {"batch_no": batch}, "批次综合")
    incoming_rows = _query("incoming_batch_by_no", {"batch_no": batch}, "来料检验")

    # ② 生产记录（含产线/工段；也是设备归属的来源）
    rep.production = _query("quality_defect_scope_by_batch",
                            {"batch_no": batch}, "生产不良记录")
    # ③ 设备状态（按该批次不良记录所属产线）
    rep.equipment = _query("equipment_log_by_batch",
                           {"batch_no": batch}, "设备状态")
    rep.abnormal_count = sum(1 for r in rep.equipment if _is_abnormal(r.get("is_abnormal")))
    # ④ 同批影响面（同型号同产线在制工单）
    rep.wip_orders = _query("inprocess_wo_by_batch",
                            {"batch_no": batch}, "同批在制工单")

    # 批次锁定：优先「来料综合」，其次「来料检验」；只有生产记录时（来料未入）
    # 仍可锁定型号/产线，但物料与供应商字段如实留空（不猜）。
    lock_source = (trace_rows or incoming_rows or
                   ([{"batch_no": batch}] if rep.production else []))
    lock_row = lock_source[0] if lock_source else {}
    prod0 = rep.production[0] if rep.production else {}
    if lock_row:
        rep.locked = {
            "batch_no": str(lock_row.get("batch_no") or batch),
            "part_no": str(lock_row.get("part_no") or ""),
            "part_name": str(lock_row.get("part_name") or ""),
            "supplier": str(lock_row.get("supplier") or ""),
            "inspect_status": str(lock_row.get("inspect_status")
                                  or lock_row.get("legacy_result") or ""),
            "warehouse": str(lock_row.get("warehouse") or ""),
            "bin_location": str(lock_row.get("bin_location") or ""),
            "product_model": str(prod0.get("product_model")
                                 or lock_row.get("product_model") or ""),
            "line_code": str(prod0.get("line_code")
                             or lock_row.get("line_code") or ""),
        }
    rep.incoming = dict(incoming_rows[0]) if incoming_rows else {}
    if incoming_rows:
        rep.incoming["qty"] = lock_row.get("qty", rep.incoming.get("qty"))
        rep.incoming["qualified_qty"] = lock_row.get(
            "qualified_qty", rep.incoming.get("qualified_qty"))

    # 事实缺口（空结果 ≠ 正常，分开表述）
    if not trace_rows and not incoming_rows and not rep.production:
        rep.data_gaps.append("批次主记录不存在（生产/来料均无该批次）")
    if not rep.production:
        rep.data_gaps.append("无该批次的生产不良记录（可能未投产或未接入）")
    if not trace_rows and not incoming_rows:
        rep.data_gaps.append("无该批次的来料检验记录（数据缺口）")

    if not trace_rows and not incoming_rows and not rep.production:
        rep.verdict = VERDICT_NO_DATA
    elif (len(rep.data_gaps) >= 2
          or any("查询失败" in g for g in rep.data_gaps)
          or not rep.production
          or not (trace_rows or incoming_rows)):
        # 多路/单路缺口、生产缺失、或来料缺失 → 只能算「部分追溯」，
        # 不能拿一路成功冒充完整三路（诚实优先于好看）。
        rep.verdict = VERDICT_PARTIAL
    else:
        rep.verdict = VERDICT_TRACED

    rep.report = _render(rep)
    return rep


def _render(rep: QualityTraceReport) -> str:
    """草稿正文：结论先行 + 三路事实 + 影响面 + 缺口（不美化、不补数）。"""
    lock = rep.locked
    head = f"质量追溯报告（草稿）· 批次 {rep.batch_no or '—'}"
    if rep.verdict == VERDICT_NO_DATA:
        lines = [head, "结论：该批次无生产/来料主记录，无法追溯；需人工核实批次号或数据接入。"]
    else:
        model = lock.get("product_model") or "型号未知"
        part = " ".join(x for x in (lock.get("part_no"), lock.get("part_name")) if x)
        supplier = lock.get("supplier") or "供应商未知"
        lines = [head,
                 f"结论：已锁定批次 {rep.batch_no}（{model} · {part or '物料未知'} · {supplier}），"
                 f"三路取证如下。"]
    if lock:
        detail = "｜".join(x for x in (
            f"物料 {lock.get('part_no') or '—'}",
            f"供应商 {lock.get('supplier') or '—'}",
            f"来料结论 {lock.get('inspect_status') or '—'}",
            f"库位 {lock.get('warehouse') or '—'}/{lock.get('bin_location') or '—'}",
        ))
        lines.append(f"批次档案：{detail}")

    # 生产记录
    if rep.production:
        top = rep.production[:3]
        desc = "；".join(
            f"{r.get('defect_type') or '不良'} {_num(r.get('defected_qty'))} 件"
            f"（{r.get('line_code') or '—'} / {r.get('process_stage') or '—'}）"
            for r in top)
        lines.append(f"一、生产记录：{desc}")
    else:
        lines.append("一、生产记录：无该批次不良记录（数据缺口，未推定正常）")

    # 来料检验
    inc = rep.incoming
    if inc:
        lines.append(
            f"二、来料检验：到货 {_num(inc.get('qty'))} 件，合格 "
            f"{_num(inc.get('qualified_qty'))} 件，结论 {inc.get('inspect_status') or '—'}"
            f"（{inc.get('part_no') or '—'} · {inc.get('supplier') or '—'}）")
    else:
        lines.append("二、来料检验：无该批次来料检验记录（数据缺口）")

    # 设备状态
    if rep.equipment:
        abnormal = [r for r in rep.equipment if _is_abnormal(r.get("is_abnormal"))]
        codes = "、".join(sorted({str(r.get("alarm_code") or "")
                                 for r in abnormal if r.get("alarm_code")}))[:160]
        lines.append(
            f"三、设备状态：产线 {rep.equipment[0].get('line_code') or '—'} 近期 "
            f"{len(rep.equipment)} 条日志，异常 {rep.abnormal_count} 条"
            + (f"（报警 {codes}）" if codes else ""))
    elif any("设备状态查询失败" in g for g in rep.data_gaps):
        lines.append("三、设备状态：查询失败（数据缺口，未推定无异常）")
    else:
        lines.append("三、设备状态：该批次产线近期无设备日志（数据缺口）")

    if rep.wip_orders:
        sample = "、".join(str(r.get("wo_no") or "") for r in rep.wip_orders[:5])
        lines.append(f"影响面：同型号同产线在制工单 {len(rep.wip_orders)} 张"
                     f"（如 {sample}；按报工记录去重，最多列 30 张）")
    else:
        lines.append("影响面：未发现同型号同产线在制工单（按报工记录）")

    if rep.data_gaps:
        lines.append("数据缺口（不编造）：" + "；".join(rep.data_gaps))
    lines.append("本稿由平台按注册查询数据组装；对外发布须经人工确认（报告审定）。")
    return "\n".join(lines)
