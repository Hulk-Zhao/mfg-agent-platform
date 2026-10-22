"""SQLAlchemySiteBoardRepository：现场看板仓储的方言无关契约（SQLite 侧）。"""
from __future__ import annotations

from typing import Any

from mfg_agents.v2.services import site_board
from mfg_agents.v2.services.site_board_repository import (
    SITE_BOARD_COLUMNS,
    SiteBoardRepository,
    SQLiteSiteBoardRepository,
)
from mfg_agents.v2.services.site_board_repository_sqlalchemy import (
    SQLAlchemySiteBoardRepository,
)


def _record(push_id: str, *, stage: str = "SMT", status: str = "pushed",
            title: str = "现场处置") -> dict[str, Any]:
    row: dict[str, Any] = {name: "" for name in SITE_BOARD_COLUMNS}
    row.update({
        "push_id": push_id, "stage": stage, "level": "P1", "title": title,
        "status": status, "created_at": "2026-10-23T00:00:00+00:00",
        "updated_at": "2026-10-23T00:00:00+00:00",
        "ack_at": None, "closed_at": None,
    })
    return row


def test_sqlalchemy_site_board_contract(tmp_path):
    repo = SQLAlchemySiteBoardRepository(f"sqlite:///{tmp_path / 'gov.db'}")
    assert isinstance(repo, SiteBoardRepository)

    repo.upsert_push(_record("p-1"))
    assert repo.fetch("p-1")["stage"] == "SMT"
    assert repo.fetch("p-missing") is None

    progressed = _record("p-1", status="closed")
    progressed.update({"ack_by": "张三", "closed_by": "李四", "feedback": "已围堵"})
    assert repo.update_push("p-1", progressed) == 1

    repo.upsert_push(_record("p-1", title="重推正文"))
    row = repo.fetch("p-1")
    assert row["title"] == "重推正文" and row["status"] == "closed"
    assert row["feedback"] == "已围堵"

    repo.upsert_push(_record("p-2", stage="PACK"))
    assert [r["push_id"] for r in repo.list_rows(stage="SMT")] == ["p-1"]
    assert repo.update_push("p-missing", _record("p-missing")) == 0


def test_site_board_store_uses_sqlite_repository_for_paths(tmp_path):
    db = str(tmp_path / "site.db")
    pid = site_board.push_message(stage="SMT", title="SQLAlchemy 推送",
                                  complaint_no="C-SA", db_path=db)

    assert isinstance(site_board._repo(db), SQLiteSiteBoardRepository)
    assert site_board.get_push(pid, db_path=db)["title"] == "SQLAlchemy 推送"
    site_board.ack(pid, actor="张三", db_path=db)
    rows = site_board.list_pushes(stage="SMT", db_path=db)
    assert rows[0]["status"] == "acked"


def test_resolve_site_target_keeps_mysql_url():
    url = "mysql+pymysql://ro:pw@db:3306/mfg"
    assert site_board.resolve_site_target(url) == url
