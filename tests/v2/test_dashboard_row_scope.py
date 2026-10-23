"""行级数据范围：令牌归属声明 → 看板聚合 WHERE 下推。

守的是「同一岗位、不同辖区」这条此前没有服务端口径的缝：

1. **声明式配置**：``data_scope.yaml`` 的 ``row_scope`` 写错（claim 非法 / 看板未登记）
   在加载期就炸，不静默忽略；
2. **缺省不收紧**：令牌不带 claim → 聚合结果与改动前逐字段一致（演示 / 存量令牌）；
3. **能力边界如实**：有产线列的表精确到产线；只有工段列的表按产线前缀收紧到工段
   并标注；两者都没有的指标保持全厂口径并标注——绝不假装已收紧；
4. **下钻不是后门**：同一 claim 下钻同样受限，且辖区外工段 → 403。
"""
from __future__ import annotations

import sqlite3

import pytest

from mfg_agents.v2.contracts.data_source import SQLiteDataSource
from mfg_agents.v2.services.dashboard import (
    build_drill,
    build_manufacturing_dashboard,
    build_pareto_drill,
    build_quality_dashboard,
    build_spc_dashboard,
    stages_from_lines,
)
from mfg_agents.v2.services.data_scope import (
    DataScopeError,
    DataScopeRegistry,
    get_data_scope,
)


# ============================================================
# 1. 目录自检与 claim 解析
# ============================================================

def _write(tmp_path, text: str):
    p = tmp_path / "scope.yaml"
    p.write_text(text, encoding="utf-8")
    return p


_BASE = """
version: 1
pseudo: {customers: 客户}
dashboards:
  manufacturing:
    name: 制造综合看板
    screen: mfgboard
    allow_roles: [gm]
  orders:
    name: 订单履约看板
    screen: orders
    allow_roles: [gm]
"""


def test_registry_parses_row_scope(tmp_path):
    p = _write(tmp_path, _BASE + """
row_scope:
  claim: line_ids
  dashboards: [manufacturing]
""")
    reg = DataScopeRegistry.from_yaml(p)
    assert reg.row_scope("manufacturing") is not None
    assert reg.row_scope("orders") is None          # 未登记 = 不收紧


def test_registry_rejects_bad_claim(tmp_path):
    p = _write(tmp_path, _BASE + """
row_scope:
  claim: "line ids"
  dashboards: [manufacturing]
""")
    with pytest.raises(DataScopeError, match="claim 非法"):
        DataScopeRegistry.from_yaml(p)


def test_registry_rejects_unknown_dashboard(tmp_path):
    p = _write(tmp_path, _BASE + """
row_scope:
  claim: line_ids
  dashboards: [manufacturing, no_such_board]
""")
    with pytest.raises(DataScopeError, match="未声明的看板"):
        DataScopeRegistry.from_yaml(p)


def test_registry_rejects_empty_dashboards(tmp_path):
    p = _write(tmp_path, _BASE + """
row_scope:
  claim: line_ids
  dashboards: []
""")
    with pytest.raises(DataScopeError, match="不能为空"):
        DataScopeRegistry.from_yaml(p)


def test_scope_lines_normalizes_claim(tmp_path):
    p = _write(tmp_path, _BASE + """
row_scope:
  claim: line_ids
  dashboards: [manufacturing]
""")
    reg = DataScopeRegistry.from_yaml(p)
    ctx = {"line_ids": [" SMT-1 ", "SMT-1", "", "PACK-2", 3, None]}
    assert reg.scope_lines("manufacturing", ctx) == ("SMT-1", "PACK-2")
    # 未登记看板 / 无 claim / 脏类型 → 空（不收紧）
    assert reg.scope_lines("orders", ctx) == ()
    assert reg.scope_lines("manufacturing", {}) == ()
    assert reg.scope_lines("manufacturing", {"line_ids": {"a": 1}}) == ()


def test_scope_lines_caps_values(tmp_path):
    p = _write(tmp_path, _BASE + """
row_scope: {claim: line_ids, dashboards: [manufacturing]}
""")
    reg = DataScopeRegistry.from_yaml(p)
    ctx = {"line_ids": [f"L{i}" for i in range(150)]}
    assert len(reg.scope_lines("manufacturing", ctx)) == 100


def test_default_catalog_declares_row_scope_for_three_boards():
    """产品口径快照：行级范围只对这三张有产线/工段维度的看板生效。"""
    reg = get_data_scope(force_reload=True)
    assert {k for k in ("manufacturing", "quality", "spc")
            if reg.row_scope(k)} == {"manufacturing", "quality", "spc"}
    assert reg.row_scope("orders") is None
    assert reg.row_scope("site_board") is None


def test_stages_from_lines_maps_prefix_and_never_guesses():
    assert stages_from_lines(["SMT-1"]) == ["SMT"]
    assert stages_from_lines(["smt-2", "PACK-1"]) == ["SMT", "PACK"]
    assert stages_from_lines(["DIP-3"]) == ["PCM"]
    assert stages_from_lines(["ASSY-1"]) == ["整机"]
    assert stages_from_lines(["未知线体"]) == []       # 认不出 → 空（fail-closed）
    assert stages_from_lines(None) == []


# ============================================================
# 2. 聚合层：两条产线的小库（可控、可对账）
# ============================================================

@pytest.fixture()
def registry(tmp_path):
    path = tmp_path / "board.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE equipment_run_time (
      line_code TEXT, product_model TEXT, record_date TEXT,
      actual_run_min INT, planned_run_min INT, stop_min INT, stop_reason TEXT);
    CREATE TABLE scrap_record (
      line_code TEXT, product_model TEXT, record_date TEXT,
      scrap_qty INT, output_qty INT, scrap_reason TEXT);
    CREATE TABLE work_report (
      wo_no TEXT, line_code TEXT, product_model TEXT, report_date TEXT,
      reported INT, should_report INT);
    CREATE TABLE test_records (
      product_model TEXT, test_date TEXT, result TEXT);
    CREATE TABLE quality_defect (
      line_code TEXT, product_model TEXT, defect_type TEXT, defect_qty INT,
      total_qty INT, inspect_time TEXT, process_stage TEXT,
      defect_category TEXT, product_category TEXT);
    CREATE TABLE std_standard_audit (
      audit_date TEXT, process_stage TEXT, team TEXT, product_model TEXT,
      process_step TEXT, doc_present INT, doc_complete INT,
      audit_total INT, audit_conform INT);
    CREATE TABLE incoming_batch (
      supplier TEXT, arrival_time TEXT, inspect_status TEXT);
    CREATE TABLE customer_complaint (
      customer TEXT, product_model TEXT, status TEXT);
    CREATE TABLE spc_measurement (
      characteristic TEXT, subgroup_no INT, seq INT, value REAL,
      usl REAL, lsl REAL, measured_at TEXT, source TEXT,
      process_stage TEXT, product_category TEXT);
    CREATE TABLE incoming_line_defect (
      supplier TEXT, inspect_date TEXT, defect_qty INT, total_qty INT);
    """)
    d = "2026-10-10"
    # 制造：SMT-1 正常、SMT-2 明显更差（用于验证汇总被收窄）
    conn.executemany("INSERT INTO equipment_run_time VALUES (?,?,?,?,?,?,?)", [
        ("SMT-1", "A型号", d, 90, 100, 10, "换线"),
        ("SMT-2", "A型号", d, 50, 100, 50, "缺料"),
    ])
    conn.executemany("INSERT INTO scrap_record VALUES (?,?,?,?,?,?)", [
        ("SMT-1", "A型号", d, 2, 100, "贴片偏移"),
        ("SMT-2", "A型号", d, 20, 100, "来料不良"),
    ])
    conn.executemany("INSERT INTO work_report VALUES (?,?,?,?,?,?)", [
        ("W1", "SMT-1", "A型号", d, 90, 100),
        ("W2", "SMT-2", "A型号", d, 50, 100),
    ])
    # 测试记录无产线列（行级范围对它不适用）
    conn.execute("INSERT INTO test_records VALUES ('A型号', ?, 'pass')", (d,))
    conn.execute("INSERT INTO test_records VALUES ('A型号', ?, 'fail')", (d,))
    conn.executemany("INSERT INTO quality_defect VALUES (?,?,?,?,?,?,?,?,?)", [
        ("SMT-1", "A型号", "贴片偏移", 2, 100, d + " 10:00:00", "SMT", "性能不良", "消费电子"),
        ("SMT-2", "A型号", "来料不良", 20, 100, d + " 11:00:00", "SMT", "外观不良", "消费电子"),
        ("PACK-1", "A型号", "整机划伤", 5, 100, d + " 12:00:00", "PACK", "外观不良", "消费电子"),
    ])
    # 标准三率：SMT 与 PACK 各一行（无产线列 → 按工段收紧）
    conn.executemany("INSERT INTO std_standard_audit VALUES (?,?,?,?,?,?,?,?,?)", [
        (d, "SMT", "SMT一班", "A型号", "贴片", 1, 1, 10, 9),
        (d, "PACK", "PACK一班", "A型号", "组装", 1, 0, 10, 5),
    ])
    conn.executemany("INSERT INTO incoming_batch VALUES (?,?,?)", [
        ("供应商甲", d + " 08:00:00", "pass"),
        ("供应商乙", d + " 09:00:00", "fail"),
    ])
    conn.executemany("INSERT INTO customer_complaint VALUES (?,?,?)", [
        ("客户甲", "A型号", "closed"),
        ("客户乙", "A型号", "open"),
    ])
    conn.executemany("INSERT INTO incoming_line_defect VALUES (?,?,?,?)", [
        ("供应商甲", d, 2, 100),
        ("供应商乙", d, 10, 100),
    ])
    # SPC：C1 在 SMT-1、C2 在 PACK（验证按产线所属工段收紧）
    rows = []
    for char, stage in (("C1", "SMT"), ("C2", "PACK")):
        for sg in range(1, 4):
            for seq in range(1, 4):
                rows.append((char, sg, seq, 10.0 + sg * 0.1 + seq * 0.01,
                             10.6, 9.4, d + " 08:00:00", "test", stage, "消费电子"))
    conn.executemany("INSERT INTO spc_measurement VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return SQLiteDataSource(str(path), name="row_scope_fixture", row_cap=5000)


def _kpi(data: dict, key: str) -> dict:
    return next(k for k in data["kpis"] if k["key"] == key)


def test_manufacturing_scoped_to_line(registry):
    full = build_manufacturing_dashboard(registry)
    scoped = build_manufacturing_dashboard(registry, lines=["SMT-1"])

    # 缺省 = 全厂（两条产线相加）
    assert _kpi(full, "availability")["numerator"] == 140
    assert _kpi(full, "availability")["denominator"] == 200
    # 收窄到 SMT-1：KPI / TOP5 / 趋势同一口径
    assert _kpi(scoped, "availability")["numerator"] == 90
    assert _kpi(scoped, "availability")["denominator"] == 100
    assert _kpi(scoped, "scrap_rate")["numerator"] == 2
    assert _kpi(scoped, "report_rate")["numerator"] == 90
    avail_rank = next(r for r in scoped["rankings"] if r["key"] == "availability")
    assert [i["line"] for i in avail_rank["items"]] == ["SMT-1"]
    # 测试记录无产线维度 → 保持全厂 + 明确标注
    assert _kpi(scoped, "test_fpy")["denominator"] == 2
    assert "产线" in _kpi(scoped, "test_fpy")["scope_note"]
    # 回显与柏拉图事实表同口径
    assert scoped["filter"]["lines"] == ["SMT-1"]
    assert {r["type"] for r in scoped["stop_pareto_fact"]["rows"]} == {"换线"}
    assert {r["type"] for r in scoped["scrap_pareto_fact"]["rows"]} == {"贴片偏移"}


def test_manufacturing_without_claim_is_byte_identical(registry):
    """未携带归属 = 不做任何收紧（结构化字段逐项一致）。"""
    a = build_manufacturing_dashboard(registry)
    b = build_manufacturing_dashboard(registry, lines=None)
    assert a["kpis"] == b["kpis"]
    assert a["filter"] == b["filter"] == {"stage": "", "product_category": ""}


def test_manufacturing_unknown_line_yields_empty_and_note(registry):
    d = build_manufacturing_dashboard(registry, lines=["不存在的线"])
    assert _kpi(d, "availability")["denominator"] == 0
    assert "无记录" in _kpi(d, "availability")["scope_note"]


def test_quality_line_scope_covers_defect_and_facts(registry):
    scoped = build_quality_dashboard(registry, lines=["SMT-1"])
    assert _kpi(scoped, "defect_rate")["numerator"] == 2
    assert _kpi(scoped, "defect_rate")["denominator"] == 100
    assert [r["name"] for r in scoped["pareto"]] == ["贴片偏移"]
    assert {r["type"] for r in scoped["pareto_fact"]["rows"]} == {"贴片偏移"}
    # 标准三率表无产线列：按产线所属工段收紧（PACK 那行被排除）并标注
    std = scoped["standard_three_rates"]
    assert [s["stage"] for s in std["by_stage"] if s.get("teams")] == ["SMT"]
    assert "工段" in std["scope_note"]
    # 来料 / 客诉无产线也无工段：保持全厂并标注
    assert "产线范围不适用" in _kpi(scoped, "incoming_pass_rate")["scope_note"]
    assert _kpi(scoped, "complaint_close_rate")["denominator"] == 2


def test_spc_line_scope_maps_to_stage(registry):
    scoped = build_spc_dashboard(registry, lines=["SMT-1"])
    assert [c["characteristic"] for c in scoped["capabilities"]] == ["C1"]
    assert scoped["filter"]["lines"] == ["SMT-1"]
    # 能力表无产线列 → 按产线所属工段收紧：PACK 的数据被挡住，
    # SMT 工段内的两条产线在工段口径下同时可见（边界如实、不假装精确到产线）。
    names = [r["name"] for r in scoped["pareto"]]
    assert "整机划伤" not in names
    assert set(names) == {"贴片偏移", "来料不良"}


def test_spc_unknown_line_is_fail_closed(registry):
    """产线前缀认不出 → 1=0 收口，绝不退回全厂。"""
    scoped = build_spc_dashboard(registry, lines=["XYZ-9"])
    assert scoped["capabilities"] == []
    assert scoped["pareto"] == []


def test_drill_inherits_line_scope(registry):
    mfg = build_drill(registry, "manufacturing", "availability", lines=["SMT-1"])
    assert {r["name"] for r in mfg["rows"]} == {"SMT-1 · A型号"}
    assert "产线范围" in mfg["definition"]
    assert mfg["filter"]["lines"] == ["SMT-1"]

    quality = build_drill(registry, "quality", "defect_rate", lines=["SMT-1"])
    assert {r["name"] for r in quality["rows"]} == {"SMT-1 · A型号"}

    # 来料指标无产线维度：不下推，但 definition 明确说明保持全厂
    incoming = build_drill(registry, "quality", "incoming_pass_rate",
                           lines=["SMT-1"])
    assert "保持全厂口径" in incoming["definition"]


def test_pareto_drill_inherits_line_scope(registry):
    out = build_pareto_drill(registry, "quality", "defect_rate", "贴片偏移",
                             window="window", lines=["SMT-1"])
    assert {r["name"] for r in out["rows"]} == {"SMT-1 · A型号"}
    assert "产线范围" in out["definition"]


# ============================================================
# 3. 接口层：claim 只认签名令牌；辖区外工段 403
# ============================================================

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


def _token(*roles: str, lines=None) -> str:
    from mfg_agents.v2.services.auth import issue_token

    token, _ = issue_token(user_id="u-" + roles[0], roles=list(roles),
                           line_ids=lines)
    return token


def _get(client, path: str, token: str):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def test_api_claim_enforces_and_reports_scope(client):
    resp = _get(client, "/api/v2/dashboard/manufacturing",
                _token("manufacturing", lines=["SMT-1"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"]["row_scope"] == {
        "claim": "line_ids", "lines": ["SMT-1"], "enforced": True}
    assert body["data"]["filter"]["lines"] == ["SMT-1"]


def test_api_without_claim_keeps_legacy_shape(client):
    resp = _get(client, "/api/v2/dashboard/manufacturing", _token("manufacturing"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "row_scope" not in body["scope"]      # 响应结构与改动前一致
    assert "lines" not in body["data"]["filter"]


def test_api_stage_outside_line_scope_is_403(client):
    resp = _get(client, "/api/v2/dashboard/manufacturing?stage=PACK",
                _token("manufacturing", lines=["SMT-1"]))
    assert resp.status_code == 403
    assert "不在你的产线范围" in str(resp.json()["detail"])


def test_api_drill_outside_line_scope_is_403(client):
    resp = _get(
        client,
        "/api/v2/dashboard/drill?screen=manufacturing&kpi=availability&stage=PACK",
        _token("manufacturing", lines=["SMT-1"]))
    assert resp.status_code == 403


def test_api_drill_in_scope_reports_row_scope(client):
    resp = _get(
        client,
        "/api/v2/dashboard/drill?screen=manufacturing&kpi=availability",
        _token("manufacturing", lines=["SMT-1"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"]["row_scope"]["enforced"] is True


def test_api_orders_has_no_row_scope(client):
    resp = _get(client, "/api/v2/dashboard/orders",
                _token("manufacturing", lines=["SMT-1"]))
    assert resp.status_code == 200, resp.text
    assert "row_scope" not in resp.json()["scope"]
