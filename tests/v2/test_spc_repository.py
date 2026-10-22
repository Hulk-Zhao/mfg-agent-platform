"""D15 迁移第 3 项：SpcRepository 契约与受治理写入通道。"""
from __future__ import annotations

import sqlite3

from mfg_agents.v2.services import spc_store
from mfg_agents.v2.services.spc_repository import (
    MEASUREMENTS_TABLE,
    SpcRepository,
    SQLAlchemySpcRepository,
    SQLiteSpcRepository,
)


def _rows(n: int = 2) -> list[dict]:
    return [
        {"characteristic": "外壳长度", "subgroup_no": 1, "seq": i + 1,
         "value": 10.0 + i, "usl": 12.0, "lsl": 8.0,
         "measured_at": "2026-10-22T00:00:00+00:00", "source": "test"}
        for i in range(n)
    ]


def test_repository_append_and_count(tmp_path):
    repo = SQLiteSpcRepository(str(tmp_path / "demo.db"))
    assert isinstance(repo, SpcRepository)
    repo.ensure_schema()

    assert repo.append(_rows(3), ingested_by="tester") == 3
    assert repo.count() == 3
    assert repo.count("外壳长度") == 3
    assert repo.count("不存在") == 0
    assert repo.append([], ingested_by="tester") == 0


def test_repository_adds_columns_on_legacy_table(tmp_path):
    """老库（缺 process_stage / product_category）必须能自愈。"""
    db = str(tmp_path / "legacy.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            f"CREATE TABLE {MEASUREMENTS_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, characteristic TEXT NOT NULL, "
            "subgroup_no INTEGER NOT NULL, seq INTEGER NOT NULL, value REAL NOT NULL, "
            "usl REAL, lsl REAL, measured_at TEXT, source TEXT, "
            "ingested_by TEXT, ingested_at TEXT NOT NULL)")

    repo = SQLiteSpcRepository(db)
    repo.ensure_schema()

    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({MEASUREMENTS_TABLE})")}
    assert {"process_stage", "product_category"} <= cols


def test_spc_store_delegates_to_repository(tmp_path):
    db = str(tmp_path / "store.db")
    assert isinstance(spc_store._repo(db), SpcRepository)

    written = spc_store.append_measurements(
        db, _rows(2), ingested_by="qa", ingested_at="2026-10-22T01:00:00+00:00")
    assert written == 2
    assert spc_store.count_measurements(db) == 2
    assert spc_store.count_measurements(db, "外壳长度") == 2


def test_sqlalchemy_spc_repository_contract(tmp_path):
    repo = SQLAlchemySpcRepository(f"sqlite:///{tmp_path / 'demo.db'}")
    assert isinstance(repo, SpcRepository)

    assert repo.append(_rows(3), ingested_by="qa") == 3
    assert repo.count() == 3
    assert repo.count("外壳长度") == 3
    assert repo.append([], ingested_by="qa") == 0
