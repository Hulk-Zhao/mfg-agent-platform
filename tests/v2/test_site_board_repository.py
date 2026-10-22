"""D15 迁移第 2 项：SiteBoardRepository 契约与 SQLite 实现。"""
from __future__ import annotations

from typing import Any

from mfg_agents.v2.services import site_board
from mfg_agents.v2.services.site_board_repository import (
    SITE_BOARD_COLUMNS,
    SiteBoardRepository,
    SQLiteSiteBoardRepository,
)


def _record(push_id: str, *, stage: str = "SMT", status: str = "pushed",
            title: str = "现场处置",
            created_at: str = "2026-10-22T00:00:00+00:00") -> dict[str, Any]:
    row: dict[str, Any] = {name: "" for name in SITE_BOARD_COLUMNS}
    row.update({
        "push_id": push_id,
        "stage": stage,
        "level": "P1",
        "title": title,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "ack_at": None,
        "closed_at": None,
    })
    return row


def test_repository_roundtrip_and_missing(tmp_path):
    repo = SQLiteSiteBoardRepository(str(tmp_path / "gov.db"))
    assert isinstance(repo, SiteBoardRepository)
    repo.ensure_schema()

    repo.upsert_push(_record("p-1"))
    assert repo.fetch("p-1")["stage"] == "SMT"
    assert repo.fetch("p-missing") is None


def test_upsert_does_not_regress_field_progress(tmp_path):
    """重推只刷内容列：现场已 ack / closed 的进度不得被打回。"""
    repo = SQLiteSiteBoardRepository(str(tmp_path / "gov.db"))
    repo.ensure_schema()
    repo.upsert_push(_record("p-1", title="初版"))

    progressed = _record("p-1", status="closed", title="初版")
    progressed.update({"ack_by": "张三", "ack_at": "2026-10-22T01:00:00+00:00",
                       "closed_by": "李四", "closed_at": "2026-10-22T02:00:00+00:00",
                       "feedback": "已围堵"})
    repo.update_push("p-1", progressed)
    repo.upsert_push(_record("p-1", title="重推正文"))

    row = repo.fetch("p-1")
    assert row["title"] == "重推正文"
    assert row["status"] == "closed"
    assert row["ack_by"] == "张三" and row["feedback"] == "已围堵"


def test_list_filters_order_and_limit(tmp_path):
    repo = SQLiteSiteBoardRepository(str(tmp_path / "gov.db"))
    repo.ensure_schema()
    repo.upsert_push(_record("p-1", stage="SMT", created_at="2026-10-20T00:00:00+00:00"))
    repo.upsert_push(_record("p-2", stage="PACK", created_at="2026-10-21T00:00:00+00:00"))
    repo.upsert_push(_record("p-3", stage="SMT", status="acked",
                             created_at="2026-10-22T00:00:00+00:00"))

    rows = repo.list_rows(stage="SMT")
    assert [r["push_id"] for r in rows] == ["p-3", "p-1"]
    rows = repo.list_rows(stage="SMT", status="acked")
    assert [r["push_id"] for r in rows] == ["p-3"]
    rows = repo.list_rows(limit=2)
    assert [r["push_id"] for r in rows] == ["p-3", "p-2"]


def test_update_missing_returns_zero(tmp_path):
    repo = SQLiteSiteBoardRepository(str(tmp_path / "gov.db"))
    repo.ensure_schema()
    assert repo.update_push("p-missing", _record("p-missing")) == 0


def test_site_board_uses_repository_contract(tmp_path):
    db = str(tmp_path / "site.db")
    assert isinstance(site_board._repo(db), SiteBoardRepository)

    pid = site_board.push_message(stage="SMT", title="客诉现场处置",
                                  complaint_no="C-1", db_path=db)
    assert site_board.get_push(pid, db_path=db)["status"] == "pushed"
    site_board.ack(pid, actor="张三", note="已接收", db_path=db)
    rows = site_board.list_pushes(stage="SMT", db_path=db)
    assert len(rows) == 1 and rows[0]["status"] == "acked"
