"""报告中心持久层（``report_store``）契约测试。

验证四件事：建表幂等 / 幂等 upsert / 状态机与非法流转 / 筛选与统计口径。
全部用临时库，**不碰演示库**（``resolve_report_db`` 被显式传参覆盖）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services import report_store as store


@pytest.fixture
def db(tmp_path):
    return tmp_path / "reports.db"


def _row(**kw):
    row = {
        "report_id": "rpt-1",
        "title": "停线应急协同 · 内部简报",
        "scenario_key": "line_stop_v2",
        "source_module": "chat",
        "risk_level": "B",
        "internal_brief": {"停线确认": "是", "风险评级": "评级B"},
        "provenance": [{"domain": "fa", "note": "根因分析结论"}],
        "versions": [],
    }
    row.update(kw)
    return row


def test_ensure_table_is_idempotent(db):
    first = store.ensure_reports_table(db)
    second = store.ensure_reports_table(db)
    assert first == second == str(db)
    assert store.count_by_status(db)["total"] == 0


def test_upsert_is_idempotent(db):
    store.upsert_report(_row(), db)
    store.upsert_report(_row(title="改名后的报告"), db)
    rows, total = store.list_reports(db_path=db)
    assert total == 1
    assert rows[0]["title"] == "改名后的报告"
    # 首次归档时间不被后续修订篡改
    store.upsert_report(_row(), db)
    again = store.get_report("rpt-1", db)
    assert again["created_at"] == rows[0]["created_at"]


def test_upsert_rejects_illegal_status(db):
    with pytest.raises(store.InvalidReportTransition):
        store.upsert_report(_row(status="不存在"), db)


def test_missing_report_id_rejected(db):
    with pytest.raises(store.ReportStoreError):
        store.upsert_report({"title": "无 id"}, db)


def test_get_report_not_found(db):
    with pytest.raises(store.ReportNotFound):
        store.get_report("rpt-none", db)


def test_list_filters_and_pagination(db):
    store.upsert_report(_row(report_id="rpt-a", scenario_key="line_stop_v2",
                             source_module="chat"), db)
    store.upsert_report(_row(report_id="rpt-b", scenario_key="customer_complaint_v2",
                             source_module="task", visibility="customer"), db)
    store.upsert_report(_row(report_id="rpt-c", scenario_key="line_stop_v2",
                             source_module="alert"), db)

    rows, total = store.list_reports(scenario_key="line_stop_v2", db_path=db)
    assert total == 2 and {r["report_id"] for r in rows} == {"rpt-a", "rpt-c"}

    rows, total = store.list_reports(source_module="task", db_path=db)
    assert total == 1 and rows[0]["report_id"] == "rpt-b"

    rows, total = store.list_reports(visibility="customer", db_path=db)
    assert total == 1

    rows, total = store.list_reports(q="停线应急", db_path=db)
    assert total == 3  # 关键字命中内部简报 JSON

    rows, total = store.list_reports(limit=1, offset=1, db_path=db)
    assert total == 3 and len(rows) == 1


def test_transition_status_happy_and_illegal(db):
    store.upsert_report(_row(), db)
    moved = store.transition_status("rpt-1", "pending_review", actor="业务演示",
                                    note="发起审定", db_path=db)
    assert moved["status"] == "pending_review"
    assert moved["versions"][-1]["actor"] == "业务演示"

    # pending_review → dispatched 非法（必须先 approved）
    with pytest.raises(store.InvalidReportTransition):
        store.transition_status("rpt-1", "dispatched", db_path=db)


def test_append_version_keeps_history(db):
    store.upsert_report(_row(), db)
    store.append_version("rpt-1", {"ver": "v1.1", "actor": "PQE", "note": "修订"}, db)
    report = store.get_report("rpt-1", db)
    assert [v["ver"] for v in report["versions"]] == ["v1.1"]


def test_count_by_status_includes_gaps(db):
    store.upsert_report(_row(report_id="rpt-gap",
                             data_gaps=["制造环节数据不完整"]), db)
    store.upsert_report(_row(report_id="rpt-ok"), db)
    counts = store.count_by_status(db)
    assert counts["total"] == 2
    assert counts["draft"] == 2
    assert counts["with_gaps"] == 1
