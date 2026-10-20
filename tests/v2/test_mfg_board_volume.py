"""制造看板体量 / 维度扩容验收（seed_board_mfg_volume2.sql + dashboard 制造侧事实表）。

覆盖 7 组断言：
1) 制造四表行数 / 日期跨度达标、2026-09 数据存在、2026-10-15 锚点行未被改动；
2) station_detail 覆盖全部 (line,model) 组（每指标组 > 0）且 wo_no 非空率 100%；
3) **核心**：availability / scrap_rate / test_fpy 的 L2 首行（最差那行）进入 L3
   必须非空（stages 非空，且 stations 或 records 至少一项非空）；
4) equipment_run_time.stop_reason 回填完成（老行非空、stop_min=0 行为空、8 类齐全）；
5) 制造侧两套 fact（stop / scrap）与品质 pareto_fact **同构**（键集合 / rows 字段
   一致、qty 合计 > 0、categories 为空）；
6) _MFG_CAT_MODELS 覆盖 12 型号、分类数 = 4；
7) 报工完成率 L3 出现 SMT段（work_report 工序口径别名修复生效）。
"""
from __future__ import annotations

import sqlite3

import pytest

from mfg_agents.v2.services.dashboard import (
    PRODUCT_CATEGORIES,
    _MFG_CAT_MODELS,
    build_drill,
    build_manufacturing_dashboard,
    build_quality_dashboard,
)

DB_PATH = "data/mfg_agent.db"

LINES = ["SMT-1", "SMT-2", "SMT-3", "DIP-1", "DIP-2", "PACK-1", "PACK-2", "ASSY-1"]
MODELS = ["A型号", "B型号", "C型号", "D型号", "E型号", "F型号",
          "G型号", "H型号", "I型号", "J型号", "K型号", "L型号"]
FACT_KEYS = {"range", "categories", "stages", "prod_cats", "days", "months", "rows"}
ROW_KEYS = {"d", "cat", "pc", "stage", "type", "qty"}


@pytest.fixture(scope="module")
def registry():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator().runtime.data_registry


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


# ============================================================
# 1. 四表行数 / 日期跨度 / 9 月数据 / 锚点行未改
# ============================================================


def test_four_tables_volume_and_span():
    c = _conn()
    spec = {
        "equipment_run_time": ("record_date", 7000),
        "scrap_record": ("record_date", 7000),
        "work_report": ("report_date", 10000),
        "test_records": ("test_date", 5500),
    }
    for table, (date_col, floor) in spec.items():
        n, mn, mx = c.execute(
            f"SELECT COUNT(*), MIN({date_col}), MAX({date_col}) FROM {table}"
        ).fetchone()
        assert n >= floor, f"{table} 行数未达标: {n} < {floor}"
        assert mn == "2026-09-01", f"{table} 未补 9 月历史: {mn}"
        assert mx == "2026-10-15", f"{table} 右端不是锚点日: {mx}"
        sept = c.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {date_col} < '2026-10-01'"
        ).fetchone()[0]
        assert sept > 0, f"{table} 缺 9 月数据"
    c.close()


def test_existing_anchor_rows_untouched():
    """10-15 锚点行与既有 id 段的行数不得被改动（只新增，不删改）。"""
    c = _conn()
    # 既有 id 段行数守恒（原值：equipment 2870 / scrap 4427 / work 4897 / test 6970 / output 107 之前）
    preserved = {
        "equipment_run_time": (2870, 1877), "scrap_record": (4427, 2433),
        "work_report": (4897, 1919), "test_records": (6970, 2992),
        "production_output": (107, 10),
    }
    for table, (max_old, expected) in preserved.items():
        got = c.execute(
            f"SELECT COUNT(*) FROM {table} WHERE id <= {max_old}").fetchone()[0]
        assert got == expected, f"{table} 既有 id 段行数被改动: {got} != {expected}"
    # 场景锚点：production_output 10-15 SMT-1/A型号 的 OEE 暴跌行原样保留
    row = c.execute("SELECT * FROM production_output WHERE id = 2").fetchone()
    assert row[1:8] == ("2026-10-15", "SMT-1", "A型号", 5000, 1800, 32.1, 150), row
    c.close()


# ============================================================
# 2. station_detail 覆盖率 / wo_no 非空
# ============================================================


@pytest.mark.parametrize("metric", ["availability", "scrap_rate", "test_fpy"])
def test_station_detail_covers_all_line_model(metric):
    """每个 (line,model) 组在该指标下都必须有 > 0 行（L3 不再空页）。"""
    expected = {(l, m) for l in LINES for m in MODELS}
    c = _conn()
    rows = c.execute(
        "SELECT line_code, product_model, COUNT(*) FROM station_detail "
        "WHERE metric_key = ? AND line_code IS NOT NULL "
        "GROUP BY line_code, product_model", (metric,)).fetchall()
    c.close()
    covered = {(r[0], r[1]) for r in rows}
    assert expected - covered == set(), f"{metric} 未覆盖组: {expected - covered}"
    assert all(r[2] > 0 for r in rows)
    assert len(covered) == len(LINES) * len(MODELS)


def test_station_detail_wo_no_fully_filled():
    c = _conn()
    n_null = c.execute(
        "SELECT COUNT(*) FROM station_detail WHERE wo_no IS NULL").fetchone()[0]
    c.close()
    assert n_null == 0, f"station_detail.wo_no 非空率非 100%（{n_null} 行为 NULL）"


# ============================================================
# 3. 核心：L2 首行 → L3 非空
# ============================================================


@pytest.mark.parametrize("kpi", ["availability", "scrap_rate", "test_fpy"])
def test_l2_first_row_drills_into_nonempty_l3(registry, kpi):
    l2 = build_drill(registry, "manufacturing", kpi)
    assert l2["level"] == 2 and l2["rows"], f"{kpi} 无 L2 明细"
    entity = l2["rows"][0]["entity"]
    l3 = build_drill(registry, "manufacturing", kpi, entity=entity)
    assert l3["level"] == 3
    assert l3["stages"], f"{kpi} L3 stages 为空（首行 {entity}）"
    assert l3["stations"] or l3["records"], \
        f"{kpi} L3 stations/records 双空（首行 {entity}）"


# ============================================================
# 4. stop_reason 回填完成
# ============================================================


def test_stop_reason_backfill_complete():
    c = _conn()
    null_positive = c.execute(
        "SELECT COUNT(*) FROM equipment_run_time "
        "WHERE stop_reason IS NULL AND stop_min > 0").fetchone()[0]
    set_zero = c.execute(
        "SELECT COUNT(*) FROM equipment_run_time "
        "WHERE stop_reason IS NOT NULL AND stop_min = 0").fetchone()[0]
    classes = {r[0] for r in c.execute(
        "SELECT DISTINCT stop_reason FROM equipment_run_time "
        "WHERE stop_reason IS NOT NULL")}
    c.close()
    assert null_positive == 0, f"{null_positive} 条有停机但原因为空"
    assert set_zero == 0, f"{set_zero} 条无停机却写了原因"
    assert classes == {"设备故障", "换型调机", "缺料待料", "计划保养",
                       "品质异常停线", "程序参数异常", "人员待岗", "其他"}, classes


# ============================================================
# 5. 两套制造 fact 与品质 fact 同构
# ============================================================


def test_mfg_pareto_facts_isomorphic_with_quality(registry):
    m = build_manufacturing_dashboard(registry)
    qf = build_quality_dashboard(registry)["pareto_fact"]
    assert m["pareto_threshold"] == 80.0
    for key in ("stop_pareto_fact", "scrap_pareto_fact"):
        f = m[key]
        assert set(f) == FACT_KEYS == set(qf), f"{key} 顶层键与品质不一致"
        assert f["categories"] == [], f"{key} 制造侧 categories 应为空"
        assert f["rows"], f"{key} rows 为空"
        assert set(f["rows"][0]) == ROW_KEYS == set(qf["rows"][0]), key
        assert sum(r["qty"] for r in f["rows"]) > 0, f"{key} qty 合计为 0"
        assert f["stages"] == ["SMT", "PCM", "PACK", "整机"], key
        assert f["prod_cats"] == list(PRODUCT_CATEGORIES), key
        assert "2026-10" in f["months"] and "2026-09" in f["months"], key
    # 停机原因头部：前 3 类约占 80%（关键少数）
    stop = m["stop_pareto_fact"]
    by_type: dict[str, int] = {}
    for r in stop["rows"]:
        by_type[r["type"]] = by_type.get(r["type"], 0) + r["qty"]
    ordered = sorted(by_type.values(), reverse=True)
    total = sum(ordered)
    assert sum(ordered[:3]) / total >= 0.75, "停机原因头部不够明显"


# ============================================================
# 6. _MFG_CAT_MODELS 覆盖 12 型号 / 4 分类
# ============================================================


def test_mfg_cat_models_cover_12_models():
    assert len(_MFG_CAT_MODELS) == 4
    assert list(_MFG_CAT_MODELS) == list(PRODUCT_CATEGORIES)
    models = [m for v in _MFG_CAT_MODELS.values() for m in v]
    assert sorted(models) == sorted(MODELS)
    assert len(models) == len(set(models)) == 12


# ============================================================
# 7. 报工完成率 L3 出现 SMT段
# ============================================================


def test_report_rate_l3_has_smt_stage(registry):
    l2 = build_drill(registry, "manufacturing", "report_rate")
    assert l2["rows"]
    l3 = build_drill(registry, "manufacturing", "report_rate",
                     entity=l2["rows"][0]["entity"])
    stages = [s["stage"] for s in l3["stages"]]
    assert "SMT段" in stages, f"报工完成率 L3 未出现 SMT段: {stages}"
