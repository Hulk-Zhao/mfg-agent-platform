"""案例库 MySQL 方言层（无真库）：目标解析 / 分派 / SQL 桥接 / 行对象。"""
from __future__ import annotations

from pathlib import Path

import pytest

from mfg_agents.common import case_db, case_schema
from mfg_agents.common.case_db import (
    CASE_DDL_MYSQL,
    CaseRow,
    MySQLCaseDatabase,
    SQLiteCaseDatabase,
    is_mysql_target,
    resolve_case_db,
    to_mysql_sql,
)

MYSQL_URL = "mysql+pymysql://ro:pw@127.0.0.1:3307/mfg_case_test"


def test_is_mysql_target():
    assert is_mysql_target(MYSQL_URL)
    assert is_mysql_target("mysql://u:p@h/db")
    assert not is_mysql_target("sqlite:///data/case.db")
    assert not is_mysql_target("data/case.db")


def test_resolve_case_db_keeps_mysql_url_and_strips_sqlite_scheme():
    assert resolve_case_db(MYSQL_URL) == MYSQL_URL
    assert resolve_case_db("sqlite:///data/case.db") == Path("data/case.db")
    assert resolve_case_db("data/case.db") == Path("data/case.db")
    assert str(resolve_case_db(":memory:")) == ":memory:"


def test_dispatch_by_dialect():
    assert isinstance(case_db.case_database_for(MYSQL_URL), MySQLCaseDatabase)
    assert case_db.case_database_for("data/case.db") is case_db.default_case_database()


def test_sqlite_implementation_rejects_mysql_url():
    with pytest.raises(ValueError, match="MySQL"):
        SQLiteCaseDatabase().connect(MYSQL_URL)
    with pytest.raises(ValueError, match="MySQL"):
        SQLiteCaseDatabase().ensure_schema(MYSQL_URL)


def test_to_mysql_sql_translates_case_domain_differences():
    assert to_mysql_sql(
        'INSERT OR REPLACE INTO case_acl (case_id) VALUES (?)'
    ) == "REPLACE INTO case_acl (case_id) VALUES (%s)"
    assert to_mysql_sql(
        'INSERT INTO "case" (case_id) VALUES (?) '
        'ON CONFLICT(case_id) DO UPDATE SET title=excluded.title, '
        'status=excluded.status'
    ) == (
        "INSERT INTO `case` (case_id) VALUES (%s) "
        "ON DUPLICATE KEY UPDATE title=VALUES(title), status=VALUES(status)"
    )
    # 无差异的 SQL 不应被改写
    assert to_mysql_sql("SELECT * FROM case_audit WHERE seq=?") == (
        "SELECT * FROM case_audit WHERE seq=%s")


def test_case_row_supports_name_and_index_access():
    row = CaseRow(["a", "b"], [1, "x"])
    assert row["a"] == 1 and row[0] == 1
    assert row["b"] == "x" and row[1] == "x"
    assert dict(row) == {"a": 1, "b": "x"}


def test_mysql_ddl_has_all_case_tables_and_no_sqlite_syntax():
    ddl = "\n".join(CASE_DDL_MYSQL)
    for table in case_db.CASE_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS" in ddl and table in ddl
    assert "AUTOINCREMENT" not in ddl
    assert "PRAGMA" not in ddl
    # 部分索引在 MySQL 用函数唯一索引等价（非 pending 返回 NULL）
    assert "uq_case_access_pending" in ddl and "uq_ontology_gap_pending" in ddl
    assert "LONGTEXT NOT NULL DEFAULT ('')" in ddl


def test_facade_reexports_mysql_symbols():
    assert case_schema.CASE_DDL_MYSQL is case_db.CASE_DDL_MYSQL
    assert case_schema.MySQLCaseDatabase is case_db.MySQLCaseDatabase
    assert case_schema.is_mysql_target is case_db.is_mysql_target
