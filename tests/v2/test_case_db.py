"""D15 迁移第 4 项：CaseDatabase 契约（连接 / schema）与 case_schema 门面。"""
from __future__ import annotations

from mfg_agents.common import case_db, case_schema
from mfg_agents.common.case_db import (
    CASE_TABLES,
    CaseDatabase,
    SQLiteCaseDatabase,
    default_case_database,
)


def test_ensure_schema_creates_all_case_tables(tmp_path):
    db_file = tmp_path / "case.db"
    provider = SQLiteCaseDatabase()
    assert isinstance(provider, CaseDatabase)

    assert provider.ensure_schema(db_file) == db_file
    provider.ensure_schema(db_file)  # 幂等

    with provider.connect(db_file) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(CASE_TABLES) <= tables


def test_connect_sets_row_factory_and_foreign_keys(tmp_path):
    db_file = tmp_path / "case.db"
    provider = SQLiteCaseDatabase()
    provider.ensure_schema(db_file)

    conn = provider.connect(db_file)
    try:
        assert conn.row_factory is not None
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


def test_bulk_load_relaxes_durability_only_inside_context(tmp_path):
    db_file = tmp_path / "case.db"
    provider = SQLiteCaseDatabase()
    provider.ensure_schema(db_file)

    with case_db.bulk_load():
        conn = provider.connect(db_file)
        try:
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 0
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "memory"
        finally:
            conn.close()

    conn = provider.connect(db_file)
    try:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] != 0
    finally:
        conn.close()


def test_case_schema_facade_delegates_to_default_database(tmp_path):
    db_file = tmp_path / "facade.db"
    assert isinstance(default_case_database(), CaseDatabase)
    assert default_case_database() is default_case_database()

    assert case_schema.ensure_case_schema(db_file) == db_file
    conn = case_schema.connect(db_file)
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()

    # 旧导入路径仍是同一批常量（case_store / case_audit 依赖）
    assert case_schema.CASE_TABLES is case_db.CASE_TABLES
    assert case_schema.DEFAULT_CASE_DB == case_db.DEFAULT_CASE_DB
    assert callable(case_schema.bulk_load)
