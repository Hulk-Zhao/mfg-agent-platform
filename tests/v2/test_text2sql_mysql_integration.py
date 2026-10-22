"""真实 MySQL 业务库上的注册查询 / Text2SQL 只读回归。

默认跳过（CI 无 MySQL）；本地验证方式：

    MFG_MYSQL_BIZ_URL=mysql+pymysql://root:demo123@127.0.0.1:3307/mfg_agent \
        uv run python -m pytest tests/v2/test_text2sql_mysql_integration.py -q

诚实边界：
- 容器自带的 ``mfg_agent`` 演示业务库只有 8 张表，本文件只回归**表已存在**的
  注册查询；未建表的查询（order_fulfillment / *_policy / 物料库存扩展表等）
  自动跳过，不伪装成"全量通过"。
- 连接用容器 root 是为了本地可复现；生产必须用只读账号——代码层只读守卫
  （仅 SELECT / WITH）不是数据库权限的替代品。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from mfg_agents.v2.contracts.data_source import DataSourceRegistry, MySQLDataSource
from mfg_agents.v2.services.text2sql import (
    MetricLibrary,
    QueryLibrary,
    Text2SQLService,
)

BIZ_URL = os.getenv("MFG_MYSQL_BIZ_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not BIZ_URL,
    reason="需要 MFG_MYSQL_BIZ_URL 指向真实 MySQL 业务库（只读回归）")

DATA_DIR = (Path(__file__).resolve().parents[2] / "src" / "mfg_agents"
            / "v2" / "data")
QUERIES = QueryLibrary.load(DATA_DIR / "queries.yaml")

#: 覆盖全部已登记事实表的最小角色集（只用于审核链，不落到运行时权限）
AUDIT_CTX = {"user_id": "u-mysql", "dept": "quality",
             "roles": ["quality", "manufacturing", "warehouse", "business",
                       "procurement", "gm"]}


@pytest.fixture(scope="module")
def source():
    src = MySQLDataSource(
        BIZ_URL, queries={n: s["sql"] for n, s in QUERIES.all().items()})
    yield src
    src.close()


@pytest.fixture(scope="module")
def samples():
    """从真实库采样参数值（参数名与 queries.yaml 的命名绑定对齐）。"""
    engine = create_engine(BIZ_URL)
    try:
        def first(sql: str):
            with engine.connect() as conn:
                return conn.execute(text(sql)).scalar()

        values = {
            "line": first("SELECT line_code FROM production_output LIMIT 1"),
            "model": first("SELECT product_model FROM production_output LIMIT 1"),
            # 质量追溯三查共用 quality_defect 的批次号，采样必须来自同一张表
            "batch_no": first("SELECT batch_no FROM quality_defect LIMIT 1"),
            "part_no": first("SELECT part_no FROM material_batch LIMIT 1"),
            "equipment_id": first("SELECT equipment_id FROM equipment_log LIMIT 1"),
            "days": 30,
            "kw": "电源",
        }
    finally:
        engine.dispose()
    return values


def test_all_registered_queries_on_present_tables_run(source, samples):
    """表已存在的注册查询全部实跑一遍；失败项聚合报告（不静默跳过）。"""
    present = set(source.list_tables())
    checked, skipped, failed = [], [], []
    for name, spec in QUERIES.all().items():
        missing = set(spec.get("tables") or []) - present
        if missing:
            skipped.append(f"{name}(缺表 {'/'.join(sorted(missing))})")
            continue
        checked.append(name)
        try:
            rows = source.run_query(name, samples)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        assert isinstance(rows, list), name
    assert checked, "没有任何注册查询可在当前 MySQL 业务库上执行"
    assert not failed, ("注册查询在 MySQL 执行失败（SQLite 放行、MySQL 拒绝的方言差异）:\n"
                        + "\n".join(failed)
                        + "\n未覆盖: " + ", ".join(skipped))


def test_only_full_group_by_regression(source):
    """supplier_followup：规范要求 SELECT 的非聚合列全部进入 GROUP BY。

    SQLite 默认放行，MySQL 8 默认 ONLY_FULL_GROUP_BY 会报 1055——同一份 SQL
    必须按更严格的一侧写。
    """
    rows = source.run_query("supplier_followup", {})
    assert rows, "material_batch 有数据，聚合结果不应为空"
    assert {"supplier", "part_no", "part_name", "batch_cnt", "fail_cnt"} <= set(rows[0])


def test_derived_table_alias_regression(source, samples):
    """quality_defect_scope_by_batch：派生表必须带别名（MySQL 1248）。"""
    rows = source.run_query(
        "quality_defect_scope_by_batch", {"batch_no": samples["batch_no"]})
    assert rows, f"批次 {samples['batch_no']} 应有不良记录"
    assert "defected_qty" in rows[0]


def test_concat_bridge_on_real_table(source):
    """SQLite ``'%' || :kw || '%'`` 在真实 MySQL 上应被桥接为 CONCAT 并可执行。"""
    rows = source.execute_readonly_sql(
        "SELECT batch_no FROM material_batch "
        "WHERE part_no LIKE '%' || :kw || '%' LIMIT 5", {"kw": "SP"})
    assert isinstance(rows, list)


def test_write_guard_on_real_connection(source):
    """只读守卫在真实连接上仍先于 DB 生效（不能靠数据库账号兜底）。"""
    with pytest.raises(ValueError):
        source.execute_readonly_sql("DELETE FROM material_batch")


def test_service_registered_channel_on_mysql(source):
    """注册查询经审核链（表权限 + 只读执行）在 MySQL 上可用。"""
    registry = DataSourceRegistry()
    registry.register(source, default=True)
    service = Text2SQLService(
        registry, MetricLibrary.load(DATA_DIR / "metrics"), QUERIES)

    result = service.query_registered("supplier_followup", {}, AUDIT_CTX)
    assert result.channel == "registered" and result.rows
    assert result.provenance["query_name"] == "supplier_followup"

    batch = service.query_registered("batch_supplier_by_no", {}, AUDIT_CTX)
    # 缺参守卫优先于执行：空参数应降级而不是抛驱动异常
    assert batch.channel == "fallback" and batch.fallback == "manual_ticket"
