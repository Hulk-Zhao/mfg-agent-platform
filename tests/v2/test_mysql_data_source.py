"""MySQLDataSource 契约测试（无真库）：方言桥接 + 只读守卫。"""
from __future__ import annotations

import pytest

from mfg_agents.v2.contracts.data_source import (
    MySQLDataSource,
    WritebackNotSupported,
)

URL = "mysql+pymysql://ro:pw@127.0.0.1:3306/mfg_unused"


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
