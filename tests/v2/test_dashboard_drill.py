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
from mfg_agents.v2.services.dashboard import (
    _entity_cols,
    _spc_caps_from_rows,
    _status,
    _verdict_status,
)

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


def test_spc_kpi_fields_self_consistent(registry):
    """逐特性能力卡字段自洽：判定映射 / delta / 超差率 / 子组口径 / 问数实体。"""
    d = build_spc_dashboard(registry)
    for k in d["kpis"]:
        # 判定（verdict）与状态色（status）必须同源映射
        assert k["status"] == _verdict_status(k["verdict"]), k["characteristic"]
        assert k["delta"] == pytest.approx(round(k["cpk"] - 1.33, 2))
        assert k["formula"] == f"Cpk={k['cpk']}"
        assert k["over_num"] <= k["over_den"]
        assert k["over_rate"] == pytest.approx(
            100.0 * k["over_num"] / k["over_den"], abs=0.1)
        # 5 子组 × 5 值 = 25 条测量值
        assert k["n_total"] == 25 and k["n_subgroups"] == 5
        assert k["sigma_within"] and k["sigma_overall"]
        assert k["entity"] == {"characteristic": k["characteristic"]}
    # 越低越好：Cpk 最差（最小）的特性必然超差率最高且判定不合格
    worst = min(d["kpis"], key=lambda k: k["cpk"])
    assert worst["verdict"] == "不合格" and worst["status"] == "danger"
    assert worst["over_rate"] == max(k["over_rate"] for k in d["kpis"])


def test_spc_double_sigma_cp_differs_from_pp(registry):
    """双西格玛口径：Cp/Cpk 用组内 σ、Pp/Ppk 用整体 σ，两组指数必须严格不等。"""
    d = build_spc_dashboard(registry)
    for k in d["kpis"]:
        name = k["characteristic"]
        assert k["cp"] > 0 and k["pp"] > 0, name
        assert k["cp"] != k["pp"], f"{name} 组内/整体 σ 未区分"
        assert k["cpk"] != k["ppk"], f"{name} 组内/整体 σ 未区分"
        assert k["sigma_within"] != k["sigma_overall"], name


def test_spc_rankings_follow_engine_direction(registry):
    """TOP5 复用统一排名口径：越低越好 → 取最差（超差率高/不良率高）在前。"""
    by_key = {r["key"]: r for r in build_spc_dashboard(registry)["rankings"]}
    assert set(by_key) == {"spc_capability", "spc_pareto"}

    cap = by_key["spc_capability"]
    assert cap["label"] == "子组超差率" and cap["unit"] == "%"
    assert cap["higher_is_better"] is False and cap["target"] == 5.0
    vals = [i["value"] for i in cap["items"]]
    assert vals == sorted(vals, reverse=True)
    assert [i["rank"] for i in cap["items"]] == list(range(1, len(vals) + 1))
    assert cap["items"][0]["entity"] == {"characteristic": "回流焊峰值温度(℃)"}

    pareto = by_key["spc_pareto"]
    assert pareto["label"] == "缺陷类型不良率" and pareto["unit"] == "%"
    assert pareto["higher_is_better"] is False and pareto["target"] == 10.0
    pvals = [i["value"] for i in pareto["items"]]
    assert pvals == sorted(pvals, reverse=True) and len(pvals) == 5


def test_spc_capability_l2_decomposition(registry):
    """spc_capability L2：按特性全量分解，越低越好 → 最差特性排第一。"""
    out = build_drill(registry, "spc", "spc_capability")
    assert out["level"] == 2 and out["kpi"] == "spc_capability"
    assert out["dimension"] == "特性" and out["degraded"] is False
    rows = out["rows"]
    assert len(rows) == 3
    assert [r["rank"] for r in rows] == [1, 2, 3]
    vals = [r["value"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    for r in rows:
        assert r["denominator"] == 25
        assert r["value"] == pytest.approx(
            100.0 * r["numerator"] / r["denominator"], abs=0.15)
        assert r["entity"] == {"characteristic": r["name"]}
        assert len(r["trend"]) == 7
    assert rows[0]["name"] == "回流焊峰值温度(℃)" and rows[0]["numerator"] == 2


def test_spc_capability_l3_locks_characteristic(registry):
    """spc_capability L3：锁定单特性，返回关联原始记录（8 列）与 7 日渐序列。"""
    ent = "回流焊峰值温度(℃)"
    out = build_drill(registry, "spc", "spc_capability",
                      entity={"characteristic": ent})
    assert out["level"] == 3 and out["entity"]["characteristic"] == ent
    assert out["numerator"] == 2 and out["denominator"] == 25
    assert out["value"] == pytest.approx(8.0) and out["status"] == "danger"
    assert {c["key"] for c in out["columns"]} == {
        "measured_at", "characteristic", "subgroup_no", "seq",
        "value", "usl", "lsl", "source"}
    assert out["records"] and all(r["characteristic"] == ent
                                  for r in out["records"])
    assert len(out["daily"]) == 7


def test_spc_pareto_l2_and_l3(registry):
    """spc_pareto：L2 按不良类型分解、L3 锁定单类型返回原始记录。"""
    l2 = build_drill(registry, "spc", "spc_pareto")
    assert l2["level"] == 2 and l2["dimension"] == "不良类型"
    assert l2["target"] == 10.0 and l2["higher_is_better"] is False
    assert l2["rows"]
    for r in l2["rows"]:
        assert r["entity"] == {"defect_type": r["name"]}
        assert r["value"] == pytest.approx(
            100.0 * r["numerator"] / r["denominator"], abs=0.15)

    name = l2["rows"][0]["name"]
    l3 = build_drill(registry, "spc", "spc_pareto", entity={"defect_type": name})
    assert l3["level"] == 3 and l3["entity"]["defect_type"] == name
    assert {c["key"] for c in l3["columns"]} == {
        "inspect_time", "batch_no", "product_model", "line_code",
        "defect_type", "defect_qty", "total_qty"}
    assert l3["records"] and all(r["defect_type"] == name for r in l3["records"])


def test_spc_entity_params_declared():
    assert entity_param_names("spc_capability") == ["characteristic"]
    assert entity_param_names("spc_pareto") == ["defect_type"]


def test_spc_dashboard_data_gap_when_no_measurements():
    """数据源无测量值：标数据缺口（data_gap）而非编造指数，空结果不算降级。"""
    class _EmptyRegistry:
        def execute_readonly_sql(self, sql, params=None):
            return []

    d = build_spc_dashboard(_EmptyRegistry())
    assert d["screen"] == "spc"
    assert d["kpis"] == [] and d["capabilities"] == [] and d["pareto"] == []
    assert d["data_gap"] is True
    assert d["degraded"] is False


def test_spc_dashboard_degrades_without_raising():
    """数据源故障：兜底为空 + 标记降级原因，绝不 500（绝不抛出）。"""
    class _BrokenRegistry:
        def execute_readonly_sql(self, sql, params=None):
            raise RuntimeError("boom")

    d = build_spc_dashboard(_BrokenRegistry())
    assert d["degraded"] is True and d["data_gap"] is True
    assert d["kpis"] == [] and d["pareto"] == []
    assert "RuntimeError" in d["degraded_reason"]


def test_verdict_status_mapping():
    assert _verdict_status("合格") == "ok"
    assert _verdict_status("偏低") == "warn"
    assert _verdict_status("不合格") == "danger"
    assert _verdict_status("不可判定") == "empty"
    assert _verdict_status(None) == "empty"
    assert _verdict_status("") == "empty"


def test_spc_caps_missing_spec_marks_data_gap():
    """缺 USL/LSL：标 data_gap + 不可判定，指数一律 None（不编造）。"""
    caps = _spc_caps_from_rows([{"characteristic": "X", "subgroup_no": 1,
                                 "value": 1.0, "usl": None, "lsl": None}])
    assert len(caps) == 1
    c = caps[0]
    assert c["characteristic"] == "X" and c["data_gap"] is True
    assert c["verdict"] == "不可判定"
    assert c["cp"] is None and c["cpk"] is None
    assert c["pp"] is None and c["ppk"] is None
    assert c["spec"] == {"usl": None, "lsl": None}
    assert c["warnings"] and "规格限" in c["warnings"][0]


def test_spc_caps_skip_unparsable_measurements():
    """子组号 / 测量值不可解析的行整行跳过，不得混入 n_total。"""
    rows = [{"characteristic": "Y", "subgroup_no": sg, "value": v,
             "usl": 12.0, "lsl": 8.0}
            for sg, v in ((1, 10.0), (1, 10.5), (2, 10.2),
                          (2, 10.8), (3, 11.0), (3, 10.9))]
    rows += [{"characteristic": "Y", "subgroup_no": None, "value": 10.0,
              "usl": 12.0, "lsl": 8.0},
             {"characteristic": "Y", "subgroup_no": 1, "value": "bad",
              "usl": 12.0, "lsl": 8.0},
             {"characteristic": "", "subgroup_no": 1, "value": 10.0,
              "usl": 12.0, "lsl": 8.0}]
    caps = _spc_caps_from_rows(rows)
    assert [c["characteristic"] for c in caps] == ["Y"]
    c = caps[0]
    assert c["data_gap"] is False
    assert c["n_total"] == 6 and c["n_subgroups"] == 3
    assert c["cp"] is not None and c["cpk"] is not None


def test_spc_dashboard_api(api_client):
    r = api_client.get("/api/v2/dashboard/spc")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    d = body["data"]
    assert d["screen"] == "spc" and len(d["kpis"]) == 3 and d["pareto"]


def test_spc_drill_api_l2_and_l3(api_client):
    """HTTP 下钻：无实体 = L2（特性维度），带 characteristic = L3（含原始记录）。"""
    r2 = api_client.get("/api/v2/dashboard/drill",
                        params={"screen": "spc", "kpi": "spc_capability"})
    assert r2.status_code == 200
    d2 = r2.json()["data"]
    assert d2["level"] == 2 and d2["dimension"] == "特性"

    ent = d2["rows"][0]["name"]
    r3 = api_client.get("/api/v2/dashboard/drill",
                        params={"screen": "spc", "kpi": "spc_capability",
                                "characteristic": ent})
    assert r3.status_code == 200
    d3 = r3.json()["data"]
    assert d3["level"] == 3 and d3["entity"]["characteristic"] == ent
    assert d3["columns"] and d3["records"]


def test_spc_pareto_drill_api_accepts_defect_type(api_client):
    """spc_pareto 把 defect_type 声明为实体参数 → 走通用下钻而非保留参数，返回 L3。"""
    l2 = api_client.get("/api/v2/dashboard/drill",
                        params={"screen": "spc", "kpi": "spc_pareto"}).json()["data"]
    name = l2["rows"][0]["name"]
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "spc", "kpi": "spc_pareto",
                               "defect_type": name})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["level"] == 3 and d["entity"]["defect_type"] == name


def test_spc_drill_api_rejects_screen_mismatch(api_client):
    """指标与屏不匹配 → 400（白名单校验，防脏参串联）。"""
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "quality", "kpi": "spc_capability"})
    assert r.status_code == 400
    assert "未知看板指标" in r.json()["detail"]


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


def test_quality_dashboard_card_count_matches_manufacturing(registry):
    """品质看板卡片数与制造看板对齐（4 卡），新增进料端 / 客户端两项行业指标。"""
    from mfg_agents.v2.services.dashboard import build_manufacturing_dashboard

    q = build_quality_dashboard(registry)
    m = build_manufacturing_dashboard(registry)
    assert len(m["kpis"]) == 4 and len(q["kpis"]) == len(m["kpis"]) == 4
    qkeys = [k["key"] for k in q["kpis"]]
    assert qkeys == ["defect_rate", "fpy", "incoming_pass_rate",
                     "complaint_close_rate"]
    # 每个 KPI 均有配套 TOP5 排行（与制造看板一致）
    assert [r["key"] for r in q["rankings"]] == qkeys
    for k in q["kpis"]:
        assert k["definition"] and k["ask"] and k["target"] is not None
    # 进料端样本：来料批次合格率；客户端样本：客诉闭环率，均有真实分母
    incoming = next(k for k in q["kpis"] if k["key"] == "incoming_pass_rate")
    assert incoming["numerator"] > 0 and incoming["denominator"] > 0
    complaint = next(k for k in q["kpis"] if k["key"] == "complaint_close_rate")
    assert complaint["numerator"] >= 0 and complaint["denominator"] > 0


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


@pytest.mark.parametrize("kpi", ["incoming_pass_rate", "complaint_close_rate"])
def test_drill_api_new_quality_kpi_l2(api_client, kpi):
    """新增品质指标（来料批次合格率 / 客诉闭环率）走通用 L2 下钻，真实链路不降级。"""
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "quality", "kpi": kpi})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["level"] == 2 and d["kpi"] == kpi
    assert d["rows"], f"{kpi} 应有 L2 分解行"
    assert d["degraded"] is False, d.get("degraded_reason")
