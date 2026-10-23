"""真实 MySQL **只读账号**回归：业务库读路径 / 注册查询 / DB 侧写拒绝。

默认跳过；本地验证方式：

    MFG_MYSQL_RO_URL=mysql+pymysql://mfg_ro:ro_demo_123@127.0.0.1:3307/mfg_agent \
        uv run python -m pytest tests/v2/test_mysql_readonly_integration.py -q

账号由 ``tools/setup_mysql_readonly.py`` 创建（仅授予目标库 SELECT）。
诚实边界：本文件验证的是**数据库权限兜底**；代码层只读守卫另有单测。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from mfg_agents.v2.contracts.data_source import MySQLDataSource
from mfg_agents.v2.services.text2sql import QueryLibrary

RO_URL = os.getenv("MFG_MYSQL_RO_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not RO_URL,
    reason="需要 MFG_MYSQL_RO_URL 指向真实业务库的**只读账号**")

DATA_DIR = (Path(__file__).resolve().parents[2] / "src" / "mfg_agents"
            / "v2" / "data")
QUERIES = QueryLibrary.load(DATA_DIR / "queries.yaml")


@pytest.fixture(scope="module")
def source() -> MySQLDataSource:
    spec = QUERIES.get("supply_batches_today")
    src = MySQLDataSource(RO_URL, queries={"supply_batches_today": spec["sql"]})
    yield src
    src.close()


def test_readonly_account_health_and_tables(source):
    assert source.health()["ok"] is True
    tables = set(source.list_tables())
    assert {"material_batch", "production_output"} <= tables


def test_registered_query_runs_with_readonly_account(source):
    rows = source.run_query("supply_batches_today", {})
    assert isinstance(rows, list)
    assert rows and "batch_no" in rows[0]


def test_database_privileges_reject_writes():
    """代码层守卫之外的第二道防线：只读账号在 DB 侧必须写不进去。"""
    engine = create_engine(RO_URL, pool_pre_ping=False)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                with pytest.raises(Exception):
                    conn.execute(text(
                        "INSERT INTO material_batch (batch_no, part_no) "
                        "VALUES ('RO-WRITE-TEST', 'RO-X')"))
            finally:
                trans.rollback()   # 若权限意外放行，也不留下测试数据
    finally:
        engine.dispose()


def test_readonly_account_cannot_create_tables():
    engine = create_engine(RO_URL, pool_pre_ping=False)
    try:
        with engine.connect() as conn:
            with pytest.raises(Exception):
                conn.execute(text("CREATE TABLE _ro_probe (id INT)"))
    finally:
        engine.dispose()
