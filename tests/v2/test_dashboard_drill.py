"""看板下钻测试：DRILL_DEFS 白名单结构 / 通用实体机制 / 无窗口（快照）路径。

覆盖点：
- 全角色下钻扩展新增的 9 个指标均注册在 DRILL_DEFS，screen 与声明一致；
- 通用实体机制 ``entity_param_names`` / ``_entity_cols``：显式 ``entity_cols`` 优先，
  否则按 ``entity`` kind（line_model / wo_no / customer / model）推导；
- ``build_drill`` 的 L2（按维度全量分解）与 L3（单实体）均不抛出、degraded 可控；
- 快照类指标（无 ``date_expr``）L3 逐日序列为空（不做时间窗口过滤），
  窗口类指标 L3 逐日序列为 7 天。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services.dashboard import (
    DRILL_DEFS,
    build_drill,
    build_pareto_drill,
    build_quality_dashboard,
    build_spc_dashboard,
    entity_param_names,
)
from mfg_agents.v2.services.dashboard import _entity_cols, _status

# 全角色下钻扩展新增的 9 个真实数据指标（阶段二）
_NEW_KPIS = {
    "oee": "manufacturing",
    "production_achieve": "manufacturing",
    "incoming_pass_rate": "quality",
    "test_pass_rate": "quality",
    "std_conformity": "quality",
    "complaint_close_rate": "quality",
    "material_shortage_rate": "orders",
    "milestone_done_rate": "orders",
    "task_backlog_rate": "orders",
}

# 无 date_expr（快照口径，不做时间窗口过滤）
_SNAPSHOT_KPIS = {"complaint_close_rate", "material_shortage_rate",
                  "milestone_done_rate", "task_backlog_rate"}


@pytest.fixture(scope="module")
def registry():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator().runtime.data_registry


# ============================================================
# 1. DRILL_DEFS 白名单结构
# ============================================================


def test_new_kpis_registered_with_screen():
    for kpi, screen in _NEW_KPIS.items():
        assert kpi in DRILL_DEFS, f"未注册下钻指标: {kpi}"
        assert DRILL_DEFS[kpi]["screen"] == screen, kpi


def test_every_def_has_required_fields():
    required = {"screen", "label", "unit", "target", "higher_is_better",
                "definition", "table", "num_expr", "den_expr", "group_cols",
                "dimension", "record_cols", "ask"}
    for kpi, defn in DRILL_DEFS.items():
        missing = required - set(defn)
        assert not missing, f"{kpi} 缺字段: {missing}"
        # 维度声明二选一：既有 kind（entity）或显式 entity_cols
        assert defn.get("entity") or defn.get("entity_cols"), kpi


def test_snapshot_kpis_have_no_date_expr():
    """快照类指标不得声明 date_expr（否则会被误按窗口过滤）。"""
    for kpi in _SNAPSHOT_KPIS:
        assert "date_expr" not in DRILL_DEFS[kpi], kpi
    # 反向：窗口类指标应声明 date_expr
    for kpi in ("oee", "incoming_pass_rate", "std_conformity"):
        assert DRILL_DEFS[kpi].get("date_expr"), kpi


# ============================================================
# 2. 通用实体机制
# ============================================================


def test_entity_param_names_explicit_cols():
    assert entity_param_names("incoming_pass_rate") == ["supplier"]
    assert entity_param_names("test_pass_rate") == ["test_type"]
    assert entity_param_names("material_shortage_rate") == ["warehouse"]
    assert entity_param_names("task_backlog_rate") == ["wf"]
    assert entity_param_names("std_conformity") == ["model", "pstep"]
    assert entity_param_names("complaint_close_rate") == ["customer", "model"]


def test_entity_param_names_legacy_kinds():
    assert entity_param_names("oee") == ["line", "model"]
    assert entity_param_names("milestone_done_rate") == ["model"]


def test_entity_cols_explicit_takes_priority():
    defn = {"entity": "line_model", "entity_cols": [["warehouse", "warehouse"]]}
    assert _entity_cols(defn) == [("warehouse", "warehouse")]
    # 无显式声明 → 回退到 kind（line_model）
    assert _entity_cols({"entity": "line_model"}) == [
        ("line_code", "line"), ("product_model", "model")]


def test_status_bands():
    # 越高越好：达标 ok，下沿 10% 内 warn，再低 danger
    assert _status(90.0, 90.0, True) == "ok"
    assert _status(85.0, 90.0, True) == "warn"
    assert _status(70.0, 90.0, True) == "danger"
    # 越低越好：达标 ok，上沿 50% 内 warn，再高 danger
    assert _status(5.0, 5.0, False) == "ok"
    assert _status(6.0, 5.0, False) == "warn"
    assert _status(9.0, 5.0, False) == "danger"


# ============================================================
# 3. build_drill：L2 / L3（真实取数，无数据时跳过）
# ============================================================


@pytest.mark.parametrize("kpi", sorted(_NEW_KPIS))
def test_l2_full_decomposition(registry, kpi):
    """每个新指标 L2 均能按维度全量分解，且不抛出、不降级。"""
    out = build_drill(registry, DRILL_DEFS[kpi]["screen"], kpi)
    assert out["level"] == 2
    assert out["kpi"] == kpi
    # 演示库无数据 → 跳过（不编造断言）
    if not out["rows"]:
        pytest.skip(f"演示库无 {kpi} 数据：{out.get('degraded_reason') or '空'}")
    assert out["degraded"] is False, out["degraded_reason"]
    # 每行结构完整，排序按“最需关注”优先
    for it in out["rows"]:
        assert it["name"]
        assert isinstance(it["entity"], dict)
        assert it["value"] == pytest.approx(
            100.0 * it["numerator"] / it["denominator"], abs=0.15)


@pytest.mark.parametrize("kpi,entity", [
    ("oee", {"line": "SMT-2", "model": "B型号"}),
    ("incoming_pass_rate", {"supplier": "国巨"}),
    ("test_pass_rate", {"test_type": "软件"}),
    ("std_conformity", {"model": "B型号", "pstep": "测试"}),
    ("material_shortage_rate", {"warehouse": "电子料仓"}),
    ("milestone_done_rate", {"model": "C型号"}),
    ("task_backlog_rate", {"wf": "hitl_complaint_reply"}),
])
def test_l3_entity_locked(registry, kpi, entity):
    """L3 单实体：锁定实体、不抛出；无数据时跳过。"""
    out = build_drill(registry, DRILL_DEFS[kpi]["screen"], kpi, entity=entity)
    assert out["level"] == 3
    assert out["kpi"] == kpi
    if out["denominator"] <= 0:
        pytest.skip(f"演示库无 {kpi} 实体数据：{entity}")
    assert out["degraded"] is False, out["degraded_reason"]
    # 实体名 = 各维度值以 “ · ” 拼接
    for p, v in entity.items():
        assert out["entity"][p] == v
    assert out["value"] == pytest.approx(
        100.0 * out["numerator"] / out["denominator"], abs=0.15)
    assert out["columns"]  # 关联原始记录列已产出


def test_l3_snapshot_has_empty_daily(registry):
    """快照类指标 L3 无逐日序列（诚实留空，不伪造 7 天）。"""
    out = build_drill(registry, "orders", "material_shortage_rate",
                      entity={"warehouse": "电子料仓"})
    if out["denominator"] <= 0:
        pytest.skip("演示库无该仓库缺料数据")
    assert out["level"] == 3
    assert out["daily"] == []


def test_l3_window_has_seven_days(registry):
    """窗口类指标 L3 逐日序列固定 7 天。"""
    out = build_drill(registry, "manufacturing", "oee",
                      entity={"line": "SMT-2", "model": "B型号"})
    if out["denominator"] <= 0:
        pytest.skip("演示库无该实体 OEE 数据")
    assert out["level"] == 3
    assert len(out["daily"]) == 7
    assert all("date" in d and "status" in d for d in out["daily"])


def test_build_drill_unknown_kpi_raises():
    with pytest.raises(KeyError):
        build_drill(None, "manufacturing", "not_a_kpi")


# ============================================================
# 4. SPC 过程能力看板（build_spc_dashboard）
# ============================================================


def test_spc_capability_and_pareto_registered():
    """spc 屏的两个下钻指标均注册在 DRILL_DEFS，screen=spc。"""
    for kpi in ("spc_capability", "spc_pareto"):
        assert kpi in DRILL_DEFS, f"未注册下钻指标: {kpi}"
        assert DRILL_DEFS[kpi]["screen"] == "spc", kpi


def test_spc_dashboard_shape(registry):
    """L1 看板形状：逐特性能力卡 + 不良柏拉图 + 口径说明 + 无数据缺口。"""
    d = build_spc_dashboard(registry)
    assert d["screen"] == "spc"
    assert {"kpis", "capabilities", "pareto", "notes", "data_gap"} <= set(d)

    # 逐特性能力卡：3 特性 × 双西格玛口径（candidate 合格/偏低/不合格 覆盖三态）
    assert len(d["kpis"]) == 3
    assert len(d["capabilities"]) == 3
    assert {k["status"] for k in d["kpis"]} == {"ok", "warn", "danger"}
    assert {k["verdict"] for k in d["kpis"]} == {"合格", "偏低", "不合格"}
    cpks = sorted(k["cpk"] for k in d["kpis"])
    assert cpks == pytest.approx([0.615, 1.146, 1.461], abs=0.01)
    for k in d["kpis"]:
        assert k["key"] == "spc_capability"
        assert k["value"] == k["cpk"]
        assert k["target"] == 1.33 and k["higher_is_better"] is True
        assert k["data_gap"] is False
        assert isinstance(k["spec"], dict)
        # 每特性 25 条测量值（3 特性 × 25 = 75），超差率 = 越限数/测量数
        assert k["over_den"] == 25
        assert k["over_rate"] == pytest.approx(
            100.0 * k["over_num"] / k["over_den"], abs=0.1)
    # 柏拉图：全量聚合，累计占比单调不减且不超 100
    assert len(d["pareto"]) == 10
    cums = [p["cum_share"] for p in d["pareto"]]
    assert cums == sorted(cums)
    # 累计占比 ≤ 100%（逐项四舍五入后允许极小浮点余量）
    assert 0.0 < cums[-1] <= 100.5
    # 口径说明齐备；演示库无数据缺口
    assert d["notes"] and all(isinstance(n, str) for n in d["notes"])
    assert d["data_gap"] is False


# ============================================================
# 5. 品质看板累计不良柏拉图（L1 字段 + 不良类型下钻）
# ============================================================


def test_quality_dashboard_exposes_cumulative_pareto(registry):
    """L1 品质看板返回累计不良柏拉图：降序、累计单调、关键少数 = 累计首达 80%。"""
    data = build_quality_dashboard(registry)
    assert {"pareto", "pareto_label", "pareto_kpi", "pareto_vital",
            "pareto_threshold"} <= set(data)
    pareto = data["pareto"]
    assert pareto, "品质看板应返回累计不良柏拉图"
    assert data["pareto_label"] == "不良类型"
    assert data["pareto_kpi"] == "defect_rate"   # 与下钻指标同源
    assert data["pareto_threshold"] == 80.0

    values = [p["value"] for p in pareto]
    assert values == sorted(values, reverse=True), "必须按损失量降序"
    cums = [p["cum_share"] for p in pareto]
    assert cums == sorted(cums), "累计占比必须单调不减"
    assert cums[-1] == pytest.approx(100.0, abs=0.5)

    vital = data["pareto_vital"]
    assert vital and vital[0] == pareto[0]["name"]
    assert pareto[len(vital) - 1]["cum_share"] >= 80.0


def test_build_pareto_drill_decomposes_single_defect_type(registry):
    """不良类型下钻：该类型按产线 · 型号分解，分子之和 = 该类型总量（分摊守恒）。"""
    top = build_quality_dashboard(registry)["pareto"][0]
    data = build_pareto_drill(registry, "quality", "defect_rate", top["name"])
    assert data["level"] == 2
    assert data["kind"] == "pareto" and data["terminal"] is True
    assert data["defect_type"] == top["name"]
    assert data["dimension"] == "产线 · 型号"
    rows = data["rows"]
    assert rows, f"「{top['name']}」应有产线 · 型号分解"
    assert sum(r["numerator"] for r in rows) == top["value"]
    vals = [r["value"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    for r in rows:
        assert r["value"] == pytest.approx(
            100.0 * r["numerator"] / r["denominator"], abs=0.15)


# ============================================================
# 6. 下钻 API：defect_type 参数校验
# ============================================================


@pytest.fixture(scope="module")
def api_client():
    from fastapi.testclient import TestClient
    from mfg_agents.v2.demo.api import app
    return TestClient(app)


def test_drill_api_rejects_defect_type_on_other_kpi(api_client):
    """不良类型下钻仅质量看板不良率支持：其它指标 → 400。"""
    for screen, kpi in (("quality", "fpy"), ("manufacturing", "oee")):
        r = api_client.get("/api/v2/dashboard/drill",
                           params={"screen": screen, "kpi": kpi,
                                   "defect_type": "锡膏偏移"})
        assert r.status_code == 400
        assert "不良类型下钻" in r.json()["detail"]


def test_drill_api_rejects_defect_type_combined_with_entity(api_client):
    """不良类型下钻不与实体/工序/工位组合（维度互斥）→ 400。"""
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "quality", "kpi": "defect_rate",
                               "defect_type": "锡膏偏移",
                               "line": "SMT-1", "model": "A型号"})
    assert r.status_code == 400
    assert "实体" in r.json()["detail"]


def test_drill_api_returns_pareto_l2(api_client, registry):
    """真实链路：defect_type 返回类型维度 L2（terminal，行不再下钻实体）。"""
    top = build_quality_dashboard(registry)["pareto"][0]["name"]
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "quality", "kpi": "defect_rate",
                               "defect_type": top})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["level"] == 2 and d["defect_type"] == top
    assert d["kind"] == "pareto" and d["terminal"] is True
    assert d["rows"] and d["dimension"] == "产线 · 型号"
