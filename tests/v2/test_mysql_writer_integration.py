"""真实 MySQL **写账号**回归：DML 可用、自愈建表可用、无 DROP/GRANT 权限。

默认跳过；本地验证：

    MFG_MYSQL_RW_URL=mysql+pymysql://mfg_rw:...@127.0.0.1:3307/mfg_agent \
        uv run python -m pytest tests/v2/test_mysql_writer_integration.py -q

账号由 ``tools/setup_mysql_writer.py`` 创建。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from mfg_agents.v2.services.spc_store import (
    append_measurements,
    count_measurements,
    ensure_measurements_table,
)

RW_URL = os.getenv("MFG_MYSQL_RW_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not RW_URL,
    reason="需要 MFG_MYSQL_RW_URL 指向真实业务库的**写账号**")


@pytest.fixture()
def engine():
    eng = create_engine(RW_URL, pool_pre_ping=False)
    yield eng
    eng.dispose()


def test_write_account_has_dml_but_no_drop_or_grant(engine):
    """权限面用 SHOW GRANTS 断言，避免真的执行 DROP 造成破坏。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SHOW GRANTS FOR CURRENT_USER()")).fetchall()
    grants = "\n".join(str(r[0]) for r in rows).upper()
    for needed in ("INSERT", "UPDATE", "DELETE"):
        assert needed in grants, grants
    assert "DROP" not in grants, grants
    assert "GRANT OPTION" not in grants, grants


def test_write_account_dml_roundtrip(engine):
    # 写账号本身具备 CREATE，先自愈建表，再验证 DML（不依赖演示库是否预置该表）
    ensure_measurements_table(RW_URL)
    marker = "RW-PROBE-" + uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO spc_measurement (characteristic, subgroup_no, seq, "
                "value, ingested_by, ingested_at) VALUES (:c, 1, 1, 1.0, :u, :t)"),
                {"c": marker, "u": "rw-probe", "t": now})
            conn.execute(text(
                "UPDATE spc_measurement SET value = 2.0 WHERE characteristic = :c"),
                {"c": marker})
            value = conn.execute(text(
                "SELECT value FROM spc_measurement WHERE characteristic = :c"),
                {"c": marker}).scalar()
            assert value == pytest.approx(2.0)
    finally:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM spc_measurement WHERE characteristic = :c"),
                {"c": marker})


def test_write_account_can_self_heal_schema():
    """ensure_schema 的 CREATE/ALTER/INDEX 要够用（应用启动会自愈老库）。"""
    ensure_measurements_table(RW_URL)


def test_spc_store_write_path_works_with_write_account(engine):
    """真实写通道（spc_store 仓储）落库 + 计数 + 清理，全部用写账号完成。"""
    ensure_measurements_table(RW_URL)
    marker = "RW-SPC-" + uuid4().hex[:8]
    written = append_measurements(
        RW_URL,
        [{"characteristic": marker, "subgroup_no": 1, "seq": 1, "value": 1.5,
          "source": "rw-probe"}],
        ingested_by="rw-probe")
    try:
        assert written == 1
        assert count_measurements(RW_URL, marker) == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM spc_measurement WHERE characteristic = :c"),
                {"c": marker})
