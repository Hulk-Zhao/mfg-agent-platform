"""D15 迁移第 1 项：ReportRepository 契约与 SQLite 实现。"""
from __future__ import annotations

import json
from typing import Any

from mfg_agents.v2.services import report_store
from mfg_agents.v2.services.report_repository import (
    REPORT_COLUMNS,
    ReportRepository,
    SQLiteReportRepository,
)


def _record(report_id: str, *, title: str = "测试报告", status: str = "draft",
            created_at: str = "2026-10-22T00:00:00+00:00",
            data_gaps: list[str] | None = None) -> dict[str, Any]:
    row = {name: "" for name in REPORT_COLUMNS}
    row.update({
        "report_id": report_id,
        "title": title,
        "status": status,
        "degraded": 0,
        "internal_brief_json": json.dumps({"summary": title}, ensure_ascii=False),
        "mask_map_json": "[]",
        "provenance_json": "[]",
        "data_gaps_json": json.dumps(data_gaps or [], ensure_ascii=False),
        "linked_json": "{}",
        "versions_json": "[]",
        "created_at": created_at,
        "updated_at": created_at,
    })
    return row


def test_repository_roundtrip_and_idempotent_upsert(tmp_path):
    repo = SQLiteReportRepository(str(tmp_path / "gov.db"))
    assert isinstance(repo, ReportRepository)
    repo.ensure_schema()

    repo.upsert(_record("r-1", title="初版"))
    repo.upsert(_record("r-1", title="修订版"))  # 同主键：更新不新增

    row = repo.fetch("r-1")
    assert row and row["title"] == "修订版"
    assert repo.fetch("r-missing") is None

    _, total = repo.list_rows()
    assert total == 1


def test_repository_list_filters_keyword_and_paging(tmp_path):
    repo = SQLiteReportRepository(str(tmp_path / "gov.db"))
    repo.ensure_schema()
    repo.upsert(_record("r-1", title="客诉周报", created_at="2026-10-20T00:00:00+00:00"))
    repo.upsert(_record("r-2", title="SPC 月报", status="approved",
                        created_at="2026-10-21T00:00:00+00:00"))
    repo.upsert(_record("r-3", title="客诉专项", status="approved",
                        created_at="2026-10-22T00:00:00+00:00"))

    rows, total = repo.list_rows(status="approved")
    assert total == 2 and [r["report_id"] for r in rows] == ["r-3", "r-2"]

    rows, total = repo.list_rows(q="客诉")
    assert total == 2 and {r["report_id"] for r in rows} == {"r-1", "r-3"}

    rows, total = repo.list_rows(limit=1, offset=1)
    assert total == 3 and len(rows) == 1 and rows[0]["report_id"] == "r-2"


def test_repository_summary(tmp_path):
    repo = SQLiteReportRepository(str(tmp_path / "gov.db"))
    repo.ensure_schema()
    repo.upsert(_record("r-1", status="draft", data_gaps=["缺 BOM"]))
    repo.upsert(_record("r-2", status="approved"))
    repo.upsert(_record("r-3", status="approved"))

    summary = repo.summary()
    assert summary["by_status"] == {"draft": 1, "approved": 2}
    assert summary["with_gaps"] == 1


def test_report_store_uses_repository_contract(tmp_path):
    """模块函数走契约：建表/写入/读取/统计全部经 ReportRepository。"""
    db = str(tmp_path / "reports.db")
    repo = report_store._repo(db)
    assert isinstance(repo, SQLiteReportRepository)

    report_store.upsert_report({
        "report_id": "r-9",
        "title": "仓储契约报告",
        "scenario_key": "line_stop",
        "status": "draft",
        "internal_brief": {"summary": "x"},
    }, db_path=db)

    assert report_store.get_report("r-9", db_path=db)["title"] == "仓储契约报告"
    rows, total = report_store.list_reports(q="仓储契约", db_path=db)
    assert total == 1 and rows[0]["report_id"] == "r-9"
    counts = report_store.count_by_status(db_path=db)
    assert counts["draft"] == 1 and counts["total"] == 1
