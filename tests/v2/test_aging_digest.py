"""R11 呆滞料盘点 / 退供评审（确定性成稿）单元契约。"""
from __future__ import annotations

from mfg_agents.v2.services.aging_digest import compose_aging_digest

ROWS = [
    {"part_no": "PACK-BOX-A", "part_name": "包装彩盒A", "category": "包材",
     "warehouse": "包材仓", "batch_no": "B1", "supplier": "裕同科技",
     "on_hand_qty": 4500, "reserved_qty": 0, "age_days": 210,
     "last_move_date": "2026-03-20", "unit_cost": 1.1},
    {"part_no": "IC-74X88", "part_name": "主控IC", "category": "电子料",
     "warehouse": "电子料仓", "batch_no": "B2", "supplier": "爱普生",
     "on_hand_qty": 14000, "reserved_qty": 12869, "age_days": 204,
     "last_move_date": "2026-03-25", "unit_cost": 12.0},
    {"part_no": "TRM-35X43", "part_name": "端子", "category": "结构件",
     "warehouse": "结构件仓", "batch_no": "B3", "supplier": "甬声电子",
     "on_hand_qty": 19100, "reserved_qty": 0, "age_days": 197,
     "last_move_date": "2026-04-01", "unit_cost": 75.0},
    {"part_no": "NEW-001", "part_name": "新料", "category": "电子料",
     "warehouse": "电子料仓", "batch_no": "B4", "supplier": "村田",
     "on_hand_qty": 100, "reserved_qty": 0, "age_days": 30,
     "last_move_date": "2026-09-20", "unit_cost": 2.0},
]


def test_counts_value_and_oldest_age():
    rep = compose_aging_digest(ROWS, 180)
    assert len(rep.rows) == 3
    assert rep.total_on_hand == 37600
    assert rep.total_value == round(4500 * 1.1 + 14000 * 12.0 + 19100 * 75.0, 2)
    assert rep.oldest_days == 210


def test_suppliers_grouped_by_value():
    rep = compose_aging_digest(ROWS, 180)
    names = [g["supplier"] for g in rep.suppliers]
    assert names[0] == "甬声电子"          # 19100*75 最大
    assert {g["supplier"] for g in rep.suppliers} == {"裕同科技", "爱普生", "甬声电子"}
    assert sum(g["count"] for g in rep.suppliers) == 3


def test_masked_supplier_does_not_collapse_into_one_group():
    """仓储岗位供应商被脱敏：不能把 13 条坍缩成一个「供应商***」。"""
    rows = [{**r, "supplier": "供应商***"} for r in ROWS[:3]]
    rep = compose_aging_digest(rows, 180)
    assert rep.suppliers == []
    assert any("供应商字段按当前岗位脱敏" in g for g in rep.data_gaps)
    assert all(r["supplier"] == "" and r["supplier_masked"] for r in rep.return_candidates)
    assert "无法按供应商分组" in rep.report


def test_transfer_cannot_be_identified_without_bom():
    rep = compose_aging_digest(ROWS, 180)
    assert rep.transfer_candidates == []
    assert any("BOM" in g and "无法自动识别" in g for g in rep.data_gaps)
    assert "不做自动识别" in rep.report or "无法自动识别" in rep.report


def test_return_candidates_require_supplier():
    rows = [dict(ROWS[0]), {**ROWS[1], "supplier": ""}]
    rep = compose_aging_digest(rows, 180)
    assert [r["part_no"] for r in rep.return_candidates] == ["PACK-BOX-A"]
    assert len(rep.return_candidates) == 1


def test_reserved_quantity_is_flagged_before_return():
    rep = compose_aging_digest(ROWS, 180)
    assert any("IC-74X88" in r and "预留" in r for r in rep.risks)
    assert any("资金占用" in r for r in rep.risks)


def test_spend_gate_is_interim_disposition():
    rep = compose_aging_digest(ROWS, 180)
    assert rep.spend_gate["required"] is True
    assert rep.spend_gate["slot"] == "disposition"
    assert rep.spend_gate["interim"] is True
    assert rep.spend_gate["amount"] == rep.total_value
    assert "人工确认" in rep.report


def test_threshold_filters_rows_and_no_data_is_honest():
    rep90 = compose_aging_digest(ROWS, 90)
    assert len(rep90.rows) == 3
    empty = compose_aging_digest(ROWS, 365)
    assert empty.has_data is False
    assert "没有超过该库龄阈值" in empty.report
    assert empty.spend_gate == {}
