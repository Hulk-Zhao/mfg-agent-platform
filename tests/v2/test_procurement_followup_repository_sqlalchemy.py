"""SQLAlchemyProcurementFollowupRepository：催交仓储的方言无关契约（SQLite 侧）。"""
from __future__ import annotations

from typing import Any

import pytest

from mfg_agents.v2.services import procurement_followup as svc
from mfg_agents.v2.services.procurement_followup import (
    FollowupNotFound,
    ProcurementFollowupRepository,
    SQLAlchemyProcurementFollowupRepository,
)


def _row(followup_id: str = "fo-sa") -> dict[str, Any]:
    return {
        "followup_id": followup_id,
        "work_order_set": "SET-SA",
        "part_no": "MAT-1",
        "part_name": "外壳",
        "supplier": "SUP-1",
        "shortage_qty": 20,
        "available_qty": 5,
        "safety_stock": 25,
        "status": "draft",
        "history": [],
    }


def test_sqlalchemy_followup_roundtrip(tmp_path):
    repo = SQLAlchemyProcurementFollowupRepository(
        f"sqlite:///{tmp_path / 'demo_governance.db'}", scope="demo")
    assert isinstance(repo, ProcurementFollowupRepository)

    inserted = repo.insert(_row())
    assert inserted["followup_id"] == "fo-sa"
    assert inserted["status"] == "draft"
    assert inserted["demo_writeback"] is True
    assert repo.get("fo-missing") is None

    updated = repo.update("fo-sa", {"status": "dispatched"},
                          event={"action": "dispatch", "ts": "2026-10-23T00:00:00Z"})
    assert updated["status"] == "dispatched"
    assert updated["history"][-1]["action"] == "dispatch"
    assert [r["followup_id"] for r in repo.list_rows(status="dispatched")] == ["fo-sa"]
    assert [r["followup_id"] for r in repo.list_rows(work_order_set="SET-SA")] == ["fo-sa"]

    with pytest.raises(FollowupNotFound):
        repo.update("fo-missing", {"status": "closed"})


def test_resolve_followup_target_keeps_mysql_url():
    url = "mysql+pymysql://ro:pw@db:3306/mfg"
    target, scope = svc.resolve_followup_target(url)
    assert target == url and scope == "governance"


def test_resolve_followup_db_still_sqlite_only():
    """兼容入口保留 SQLite 语义：MySQL 目标应 fail-fast。"""
    from mfg_agents.v2.services.workflow import WorkflowBackendError

    with pytest.raises(WorkflowBackendError):
        svc.resolve_followup_db("mysql+pymysql://ro:pw@db:3306/mfg")
