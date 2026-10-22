"""R5 对客交期答复组装（delivery_reply.py）契约测试。

钉四件事：
1. 算术正确：现货先发 / 剩余排产 / 完工日 / 提前（晚）天数；
2. 诚实：数据缺失进 data_gaps，verdict=no_data，草稿不编造；
3. 口径可解释：草稿含 ATP 与排产基准说明；
4. 边界：约定交期解析（ISO/月底/无法解析）、需求量非法、查询异常。
"""
from __future__ import annotations

from datetime import date

import pytest

from mfg_agents.v2.services.delivery_reply import (
    VERDICT_LATE,
    VERDICT_NO_DATA,
    VERDICT_ON_TIME,
    compose_delivery_reply,
    parse_need_date,
)

TODAY = date(2026, 10, 20)


def _runner(atp_rows, plan_rows, atp_error=None, plan_error=None):
    """假查询执行器：按名返回预制行（模拟 atp_by_model / plan_by_model）。"""

    def _run(name, params):
        if name == "atp_by_model":
            if atp_error:
                raise RuntimeError(atp_error)
            return atp_rows
        if name == "plan_by_model":
            if plan_error:
                raise RuntimeError(plan_error)
            return plan_rows
        raise AssertionError(f"未预期的查询: {name}")

    return _run


def _plan(days=7, per_day=200, start=TODAY):
    """近 7 天排产行（actual=200/天 → 日均 200）。"""
    from datetime import timedelta

    rows = []
    for i in range(days):
        d = start - timedelta(days=i)
        rows.append({"record_date": d.isoformat(), "line_code": "SMT-1",
                     "product_model": "A型号", "planned_qty": per_day,
                     "actual_qty": per_day})
    return rows


# ---------------------------------------------------------------- 算术


def test_on_time_with_partial_stock():
    """现货 600 + 排产 1400/200每天 → 7 天完工，比月底（10-31）提前 4 天。"""
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], _plan(per_day=200)), today=TODAY)
    assert rep.verdict == VERDICT_ON_TIME
    assert rep.on_hand == 600 and rep.first_ship == 600
    assert rep.to_produce == 1400
    assert rep.finish_date == "2026-10-27"   # 10-20 + ceil(1400/200)=7 天
    assert rep.slack_days == 4               # 10-31 − 10-27
    assert "提前 4 天" in rep.draft
    assert "600 套可先发" in rep.draft


def test_late_when_capacity_insufficient():
    """产能 100/天 → 1400 套需 14 天，赶不上 10-31。"""
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], _plan(per_day=100)), today=TODAY)
    assert rep.verdict == VERDICT_LATE
    assert rep.slack_days < 0
    assert "晚" in rep.draft and "分批先发现货" in rep.draft


def test_stock_covers_all_no_production():
    """现货全覆盖 → 无需排产，直接可发。"""
    rep = compose_delivery_reply(
        "A型号", 500, "2026-10-31",
        _runner([{"available_qty": 900}], _plan()), today=TODAY)
    assert rep.verdict == VERDICT_ON_TIME and rep.to_produce == 0
    assert "覆盖全部 500 套需求" in rep.draft


def test_atp_sums_across_warehouses():
    rep = compose_delivery_reply(
        "A型号", 100, "2026-10-31",
        _runner([{"available_qty": 40}, {"available_qty": 60}], []), today=TODAY)
    assert rep.on_hand == 100 and rep.first_ship == 100


# ---------------------------------------------------------------- 诚实降级


def test_no_plan_records_gives_no_data():
    """无排产记录 → 不猜完工日，缺口如实。"""
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], []), today=TODAY)
    assert rep.verdict == VERDICT_NO_DATA
    assert rep.finish_date == ""
    assert any("实际产出" in g for g in rep.data_gaps)
    assert "需人工核实" in rep.draft
    assert "600 套" in rep.draft   # 已知数仍然给


def test_empty_atp_rows_is_zero_stock_not_gap():
    """无库存记录 = 零现货（事实，不是缺口）——与查询失败区分。"""
    rep = compose_delivery_reply(
        "A型号", 500, "2026-10-31",
        _runner([], _plan()), today=TODAY)
    assert rep.on_hand == 0
    assert not any("ATP" in g for g in rep.data_gaps)
    # 零现货 + 有排产 → 全部排产，结论仍可给出（赶不赶得上由算术定）
    assert rep.verdict in {VERDICT_ON_TIME, VERDICT_LATE}


def test_atp_query_error_is_gap_not_fabrication():
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([], _plan(), atp_error="connection refused"), today=TODAY)
    assert rep.verdict == VERDICT_NO_DATA
    assert any("ATP 查询失败" in g for g in rep.data_gaps)
    # 失败 ≠ 零库存结论：草稿只能说待核实，绝不把「未知」说成「没有」
    assert "需人工核实" in rep.draft
    assert "现货数据待核实" in rep.draft
    assert "无可用现货" not in rep.draft


def test_unparseable_need_date_is_gap():
    rep = compose_delivery_reply(
        "A型号", 100, "下周吧",
        _runner([{"available_qty": 900}], _plan()), today=TODAY)
    assert rep.verdict == VERDICT_NO_DATA
    assert any("无法解析" in g for g in rep.data_gaps)


def test_invalid_qty_is_gap():
    rep = compose_delivery_reply(
        "A型号", 0, "2026-10-31",
        _runner([{"available_qty": 900}], _plan()), today=TODAY)
    assert rep.verdict == VERDICT_NO_DATA
    assert any("需求量无效" in g for g in rep.data_gaps)


def test_missing_model_is_gap():
    rep = compose_delivery_reply(
        "", 100, "2026-10-31", _runner([], []), today=TODAY)
    assert rep.verdict == VERDICT_NO_DATA
    assert any("未提供产品型号" in g for g in rep.data_gaps)


# ---------------------------------------------------------------- 口径与日期解析


def test_draft_states_caliber():
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], _plan(per_day=200)), today=TODAY)
    assert "ATP = 在库 − 预留" in rep.draft
    assert "近 7 天" in rep.draft
    assert "hitl.eta 人工确认" in rep.draft


def test_draft_never_claims_sent():
    """草稿必须自证是草稿（对外承诺的前置态）。"""
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], _plan(per_day=200)), today=TODAY)
    assert "草稿" in rep.draft and "人工确认后方可发送" in rep.draft


@pytest.mark.parametrize("text,expected", [
    ("2026-10-31", "2026-10-31"),
    ("月底", "2026-10-31"),      # TODAY=2026-10-20 的当月月末
    ("下月底", "2026-11-30"),
])
def test_parse_need_date(text, expected):
    got, gaps = parse_need_date(text, today=TODAY)
    assert got == expected and gaps == []


def test_parse_need_date_garbage():
    got, gaps = parse_need_date("下周吧", today=TODAY)
    assert got == "" and gaps


def test_provenance_records_queries():
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], _plan(per_day=200)), today=TODAY)
    names = [p["query"] for p in rep.provenance]
    assert names == ["atp_by_model", "plan_by_model"]


def test_old_plan_rows_outside_window_ignored():
    """窗口外（30 天前）的产出不计入日均（口径=近 7 天）。"""
    from datetime import timedelta

    stale = [{"record_date": (TODAY - timedelta(days=30)).isoformat(),
              "actual_qty": 9999}]
    rep = compose_delivery_reply(
        "A型号", 2000, "2026-10-31",
        _runner([{"available_qty": 600}], stale), today=TODAY)
    assert rep.daily_capacity == 0.0
    assert rep.verdict == VERDICT_NO_DATA
