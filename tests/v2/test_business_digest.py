"""R10 老板经营 · 大单进度（确定性成稿）单元契约。

守四件事：
1. 选单：有风险的大单优先于「更大但正常」的单（老板先看要出事的）；
2. 计算：加权完成率、剩余量、按型号近 7 日产能推算完工日（含提前/延后）；
3. 主动风险：risk_type / delay_days / shortage_note / 交付日对比，缺数据要标注；
4. 降级：无 ≥阈值 在制单、无产能记录、无数据 —— 都如实说明，不编造。
"""
from __future__ import annotations

from mfg_agents.v2.services.business_digest import compose_big_order_digest

ROWS = [
    {"order_no": "SO-2026-14987", "product_model": "B型号", "qty": 14987,
     "stage": "生产", "progress": 49, "due_date": "2026-10-14",
     "risk_type": "正常", "delay_days": 0, "shortage_note": ""},
    {"order_no": "SO-2026-15331", "product_model": "C型号", "qty": 14547,
     "stage": "计划", "progress": 20, "due_date": "2026-10-14",
     "risk_type": "客诉", "delay_days": 3,
     "shortage_note": "主控芯片 IC-100 交期顺延"},
    {"order_no": "SO-2026-00001", "product_model": "C型号", "qty": 1000,
     "stage": "生产", "progress": 50, "due_date": "2026-10-15",
     "risk_type": "正常", "delay_days": 0, "shortage_note": ""},
    {"order_no": "SO-2026-99999", "product_model": "C型号", "qty": 20000,
     "stage": "发货", "progress": 100, "due_date": "2026-10-10",
     "risk_type": "正常", "delay_days": 0, "shortage_note": ""},
]

CAPACITY = [
    {"record_date": f"2026-10-{d:02d}", "actual_qty": 1000}
    for d in range(9, 16)
]


def _compose(**kwargs):
    return compose_big_order_digest(
        ROWS, lambda model: CAPACITY if model == "C型号" else [], **kwargs)


def test_selects_risky_big_order_over_larger_normal_one():
    rep = _compose()
    assert rep.selected["order_no"] == "SO-2026-15331"      # 有风险，优先
    assert len(rep.candidates) == 2                         # 小单/已发货被排除
    assert rep.threshold == 5000


def test_weighted_progress_and_remaining_quantity():
    rep = _compose()
    # (14987*49 + 14547*20) / 29534 ≈ 34.7
    assert rep.aggregate_progress == 34.7
    assert rep.total_qty == 29534
    assert rep.remaining_qty == 11638                       # 14547 * 80%
    assert rep.daily_capacity == 1000.0


def test_finish_date_and_slack_are_derived_from_capacity():
    rep = _compose(today=None)
    assert rep.as_of == "2026-10-15"
    assert rep.finish_date == "2026-10-27"                  # ceil(11638/1000)=12 天
    assert rep.slack_days == -13                            # 交付日晚 13 天


def test_proactive_risks_cover_order_markers_and_shortage():
    rep = _compose()
    text = "\n".join(rep.risks)
    assert "客诉" in text and "延期 3 天" in text
    assert "主控芯片 IC-100" in text
    assert "已过客户要求交付日" in text
    assert "主动风险" in rep.report


def test_model_and_order_no_filters():
    by_model = _compose(model="B型号")
    assert by_model.selected["order_no"] == "SO-2026-14987"
    by_order = _compose(order_no="SO-2026-15331")
    assert by_order.selected["order_no"] == "SO-2026-15331"
    miss = _compose(order_no="SO-NOT-EXIST")
    assert miss.has_data is False
    assert any("未找到在制订单" in g for g in miss.data_gaps)


def test_threshold_fallback_reports_gap_honestly():
    rep = _compose(min_qty=20000)
    assert any("没有 ≥20000 件" in g for g in rep.data_gaps)
    assert rep.selected["order_no"] == "SO-2026-14987"      # 最大在制单
    assert "没有 ≥20000 件" in rep.report


def test_missing_capacity_leaves_finish_empty_with_gap():
    rep = compose_big_order_digest(ROWS, lambda model: [], min_qty=5000)
    assert rep.finish_date == ""
    assert rep.daily_capacity == 0
    assert any("预计完工日无法估算" in g for g in rep.data_gaps)
    assert "无法估算" in rep.report


def test_no_inprogress_rows_is_no_data():
    rep = compose_big_order_digest([], lambda model: [], min_qty=5000)
    assert rep.has_data is False
    assert "没有符合条件的在制订单" in rep.report


def test_normal_order_still_gets_a_risk_baseline():
    rep = _compose(model="B型号")
    assert any("未发现订单级风险标记" in r for r in rep.risks)
