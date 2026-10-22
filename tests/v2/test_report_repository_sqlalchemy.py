"""SQLAlchemyReportRepository：报告仓储的方言无关契约（SQLite 侧）。"""
from __future__ import annotations

import json
from typing import Any

from mfg_agents.v2.services import report_store
from mfg_agents.v2.services.report_repository import (
    REPORT_COLUMNS,
    ReportRepository,
    SQLiteReportRepository,
)
from mfg_agents.v2.services.report_repository_sqlalchemy import (
    SQLAlchemyReportRepository,
)


def _record(report_id: str, *, status: str = "draft",
            gaps: list[str] | None = None) -> dict[str, Any]:
    row = {name: "" for name in REPORT_COLUMNS}
    row.update({
        "report_id": report_id,
        "title": f"报告-{report_id}",
        "status": status,
        "degraded": 0,
        "internal_brief_json": json.dumps({"summary": "x"}, ensure_ascii=False),
        "data_gaps_json": json.dumps(gaps or [], ensure_ascii=False),
        "created_at": "2026-10-23T00:00:00+00:00",
        "updated_at": "2026-10-23T00:00:00+00:00",
    })
    return row


def test_sqlalchemy_report_contract(tmp_path):
    repo = SQLAlchemyReportRepository(f"sqlite:///{tmp_path / 'gov.db'}")
    assert isinstance(repo, ReportRepository)

    repo.upsert(_record("r-1"))
    repo.upsert(_record("r-1", status="approved"))  # 同主键更新
    assert repo.fetch("r-1")["status"] == "approved"
    assert repo.fetch("r-missing") is None

    repo.upsert(_record("r-2", status="approved", gaps=["缺 BOM"]))
    rows, total = repo.list_rows(status="approved")
    assert total == 2 and {r["report_id"] for r in rows} == {"r-1", "r-2"}
    rows, total = repo.list_rows(q="报告-r-2")
    assert total == 1 and rows[0]["report_id"] == "r-2"

    summary = repo.summary()
    assert summary["by_status"]["approved"] == 2
    assert summary["with_gaps"] == 1


def test_report_store_uses_sqlite_repository_for_paths(tmp_path):
    db = str(tmp_path / "reports.db")
    report_store.upsert_report({
        "report_id": "r-9", "title": "SQLAlchemy 报告",
        "internal_brief": {"summary": "x"},
    }, db_path=db)

    assert isinstance(report_store._repo(db), SQLiteReportRepository)
    assert report_store.get_report("r-9", db_path=db)["title"] == "SQLAlchemy 报告"
    rows, total = report_store.list_reports(q="SQLAlchemy", db_path=db)
    assert total == 1 and rows[0]["report_id"] == "r-9"


def test_resolve_report_target_keeps_mysql_url():
    url = "mysql+pymysql://ro:pw@db:3306/mfg"
    assert report_store.resolve_report_target(url) == url
