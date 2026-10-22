"""MySQLDataSource 契约测试（无真库）：方言桥接 + 只读守卫。

另有两条**注册查询可移植性**静态守卫：SQLite 放行、MySQL 8 默认拒绝的写法
必须在提交前拦下（真库回归见 ``test_text2sql_mysql_integration.py``，默认跳过）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mfg_agents.v2.contracts.data_source import (
    MySQLDataSource,
    WritebackNotSupported,
)
from mfg_agents.v2.services.text2sql import QueryLibrary

URL = "mysql+pymysql://ro:pw@127.0.0.1:3306/mfg_unused"
QUERIES_FILE = (Path(__file__).resolve().parents[2] / "src" / "mfg_agents"
                / "v2" / "data" / "queries.yaml")


def _source(**kwargs) -> MySQLDataSource:
    return MySQLDataSource(URL, **kwargs)


def test_concat_translation_for_registered_queries():
    sql = "SELECT title FROM policy WHERE title LIKE '%' || :kw || '%'"
    assert MySQLDataSource._translate_concat(sql) == (
        "SELECT title FROM policy WHERE title LIKE CONCAT('%', :kw, '%')")


def test_concat_translation_leaves_column_or_alone():
    sql = "SELECT a FROM t WHERE a || b"
    assert MySQLDataSource._translate_concat(sql) == sql


def test_readonly_guard_rejects_non_select_without_connecting():
    source = _source()
    try:
        for bad in ("INSERT INTO t VALUES (1)", "UPDATE t SET a=1",
                    "DELETE FROM t", "DROP TABLE t", "SELECT 1; DROP TABLE t"):
            with pytest.raises(ValueError):
                source.execute_readonly_sql(bad)
    finally:
        source.close()


def test_readonly_guard_ignores_write_words_inside_literals():
    """字面量里的 'delete' 不应误伤；该 SQL 只会在连接阶段失败，不是守卫失败。"""
    source = _source()
    try:
        assert source._WRITE_WORDS.search(
            __import__("re").sub(r"'(?:[^']|'')*'", "''",
                                 "SELECT a FROM t WHERE title = 'delete'")) is None
    finally:
        source.close()


def test_unknown_registered_query_and_writeback():
    source = _source(queries={"ok": "SELECT 1"})
    try:
        with pytest.raises(KeyError):
            source.run_query("nope")
        with pytest.raises(WritebackNotSupported):
            source.write_back("erp.purchase_receipt", {})
    finally:
        source.close()


def test_comment_stripping_before_prefix_check():
    source = _source()
    try:
        with pytest.raises(ValueError):
            source.execute_readonly_sql("-- select\nDELETE FROM t")
    finally:
        source.close()


# ---------- 注册查询可移植性（无真库也能拦住的 MySQL 8 差异） ----------

def _registered() -> dict[str, dict]:
    return QueryLibrary.load(QUERIES_FILE).all()


def test_registered_derived_tables_have_alias():
    """派生表必须带别名：MySQL 8 报 1248，SQLite 放行。"""
    for name, spec in _registered().items():
        sql = str(spec["sql"])
        if re.search(r"FROM\s*\(\s*SELECT", sql, re.I):
            assert re.search(r"\)\s*AS\s+[A-Za-z_]\w*", sql, re.I), (
                f"{name} 的派生表缺少 AS 别名（MySQL 8 会拒绝）")


def test_supplier_followup_group_by_covers_selected_columns():
    """ONLY_FULL_GROUP_BY：SELECT 的非聚合列必须全部进 GROUP BY。

    MySQL 8 默认开启，SQLite 默认关闭——曾因漏了 part_name 在真库报 1055。
    """
    sql = str(_registered()["supplier_followup"]["sql"])
    assert re.search(r"GROUP\s+BY\s+supplier\s*,\s*part_no\s*,\s*part_name",
                     sql, re.I), "supplier_followup 的 GROUP BY 必须覆盖 part_name"
