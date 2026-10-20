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
    build_manufacturing_dashboard,
    build_pareto_drill,
    build_quality_dashboard,
    build_spc_dashboard,
    entity_param_names,
)
from mfg_agents.v2.services.dashboard import (
    _entity_cols,
    _MFG_CAT_MODELS,
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
#
# 既有 3 特性（种子基线，行数与取值一字不改）= 焊点高度(mm) / 锡膏厚度(μm) /
# 回流焊峰值温度(℃)。V3.10 起种子新增 4 个特性（每工段 1 个，覆盖既有特性
# 没覆盖的 PACK / 整机），故本节断言一律**限定既有 3 特性子集**，
# 避免「新增特性」把「既有特性的精确数值」断言冲掉。
_SPC_BASE_CHARS = ("焊点高度(mm)", "锡膏厚度(μm)", "回流焊峰值温度(℃)")


def _spc_base_kpis(d: dict) -> list[dict]:
    """从 L1 载荷里挑出既有 3 特性的能力卡（按特性名精确匹配）。"""
    return [k for k in d["kpis"] if k["characteristic"] in _SPC_BASE_CHARS]


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

    # 逐特性能力卡：既有 3 特性 × 双西格玛口径（candidate 合格/偏低/不合格 覆盖三态）
    # 注：V3.10 起种子新增 4 个特性 → 断言限定既有 3 特性子集
    base = _spc_base_kpis(d)
    assert len(base) == 3
    assert {k["status"] for k in base} == {"ok", "warn", "danger"}
    assert {k["verdict"] for k in base} == {"合格", "偏低", "不合格"}
    cpks = sorted(k["cpk"] for k in base)
    # 全月种子（V3.11）：window 口径混入反标定回填行 → Cpk 相对 seed_v3 原值有
    # 批间聚合漂移（实测 ≤0.08），但**判定档位恒定**（这是 A8 的核心），逐档断言：
    assert cpks[0] == pytest.approx(0.615, abs=0.08) and cpks[0] < 1.0
    assert cpks[1] == pytest.approx(1.15, abs=0.08) and 1.0 <= cpks[1] < 1.33
    assert cpks[2] == pytest.approx(1.46, abs=0.08) and cpks[2] >= 1.33
    for k in base:
        assert k["key"] == "spc_capability"
        assert k["value"] == k["cpk"]
        assert k["target"] == 1.33 and k["higher_is_better"] is True
        assert k["data_gap"] is False
        assert isinstance(k["spec"], dict)
        # 既有 3 特性测量数（**window 口径**）= 10-08~10-13 每天 10 行 + 10-14 共 25 行 = 85，
        # 超差率 = 越限数/测量数
        assert k["over_den"] == 85
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
        assert k["sigma_within"] and k["sigma_overall"]
        assert k["entity"] == {"characteristic": k["characteristic"]}
    # 既有 3 特性（window 口径 = 85 行 / 17 子组：10-08~10-13 每天 2 子组 + 10-14 的 5 子组；
    # 子组号三段 1-5 / 6-10 / 11+ 跨日期不混样）
    for k in _spc_base_kpis(d):
        assert k["n_total"] == 85 and k["n_subgroups"] == 17, k["characteristic"]
    # 越低越好：Cpk 最差（最小）的特性必然超差率最高且判定不合格
    worst = min(d["kpis"], key=lambda k: k["cpk"])
    assert worst["verdict"] == "不合格" and worst["status"] == "danger"
    assert worst["over_rate"] == max(k["over_rate"] for k in d["kpis"])


def test_spc_double_sigma_cp_differs_from_pp(registry):
    """双西格玛口径：Cp/Cpk 用组内 σ、Pp/Ppk 用整体 σ，两组指数必须严格不等。"""
    d = build_spc_dashboard(registry)
    for k in d["kpis"]:
        name = k["characteristic"]
        if k["cpk"] is None:      # 数据缺口特性（缺规格限）不参与比较
            continue
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
    assert rows
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    vals = [r["value"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    for r in rows:
        assert r["value"] == pytest.approx(
            100.0 * r["numerator"] / r["denominator"], abs=0.15)
        assert r["entity"] == {"characteristic": r["name"]}
        assert len(r["trend"]) == 7
    # 既有 3 特性：L2 分母 = **window 口径** 85（10-08~10-13 每天 10 行 + 10-14 共 25 行）
    base_rows = [r for r in rows if r["name"] in _SPC_BASE_CHARS]
    assert len(base_rows) == 3
    for r in base_rows:
        assert r["denominator"] == 85, r["name"]
    # 最差特性排第一（回流焊峰值温度 Cpk 最低 → 超差率最高）；
    # 越限数不锁具体值（新种子含反标定随机行），但必须与 L1 聚合**同源可对账**
    assert rows[0]["name"] == "回流焊峰值温度(℃)"
    l1 = build_spc_dashboard(registry, "window")
    worst = next(k for k in l1["kpis"]
                 if k["characteristic"] == "回流焊峰值温度(℃)")
    assert rows[0]["numerator"] == worst["over_num"]
    assert rows[0]["denominator"] == worst["over_den"]


def test_spc_capability_l3_locks_characteristic(registry):
    """spc_capability L3：锁定单特性，返回关联原始记录与逐日序列。

    越限数 / 分母**不锁具体值**（全月种子含反标定随机行），但必须与 L1 聚合
    **同源可对账**（同 `registry` 下 L1 卡片的 over_num/over_den 逐字段相等）。"""
    ent = "回流焊峰值温度(℃)"
    out = build_drill(registry, "spc", "spc_capability",
                      entity={"characteristic": ent})
    assert out["level"] == 3 and out["entity"]["characteristic"] == ent
    l1 = build_spc_dashboard(registry, "window")
    k = next(k for k in l1["kpis"] if k["characteristic"] == ent)
    assert out["numerator"] == k["over_num"] and out["denominator"] == k["over_den"]
    assert out["value"] == pytest.approx(k["over_rate"], abs=0.1)
    # 状态不锁具体值：超差率状态按 target=5.0 口径算（_status）——
    # 已知口径问题（登记后续项）：Cpk 判定线（1.33/1.00）与超差率目标 5.0%
    # 在数学上不对应（Cpk 0.62 ≈ 双尾 5%），会出现「Cpk 不合格但超差率达标」。
    # 修它要动 DRILL_DEFS.target 与 A8/A9 验收矩阵，不在数据集增量内。
    assert out["status"] in ("ok", "warn", "danger")
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
        # 签名对齐 ``DataSource.execute_readonly_sql`` 协议（``row_cap`` 为行数上限
        # 形参）：SPC 取数显式传 ``cap=_SPC_MEAS_CAP``，mock 少该形参会抛 TypeError
        # → 被误判成「数据源故障」而置 degraded=True（空结果本不算降级）。
        def execute_readonly_sql(self, sql, params=None, row_cap=200):
            return []

    d = build_spc_dashboard(_EmptyRegistry())
    assert d["screen"] == "spc"
    assert d["kpis"] == [] and d["capabilities"] == [] and d["pareto"] == []
    assert d["data_gap"] is True
    assert d["degraded"] is False


def test_spc_dashboard_degrades_without_raising():
    """数据源故障：兜底为空 + 标记降级原因，绝不 500（绝不抛出）。"""
    class _BrokenRegistry:
        def execute_readonly_sql(self, sql, params=None, row_cap=200):
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
    assert d["screen"] == "spc" and len(d["kpis"]) >= 3 and d["pareto"]
    # 既有 3 特性仍在（新增特性不冲掉基线）
    assert len(_spc_base_kpis(d)) == 3


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
    """带**总经理**令牌的客户端。

    看板接口自 P0-2 起按岗位裁决（fail-closed）：裸调用一律 401。本文件的用例
    验的是下钻**结构**，统一用「全权限岗位」跑；越权与脱敏路径见
    ``tests/v2/test_数据范围.py``。
    """
    from fastapi.testclient import TestClient
    from mfg_agents.v2.demo.api import app
    client = TestClient(app)
    login = client.post("/api/v2/auth/login", json={"roles": ["gm"]})
    client.headers.update(
        {"Authorization": f"Bearer {login.json()['data']['token']}"})
    return client


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


# ============================================================
# 5b. 不良柏拉图「产品分类」维度（事实表 + 类型下钻筛选）
# ============================================================


def test_pareto_fact_exposes_product_categories(registry):
    """事实表按产品分类切片：4 类齐全，每行带 ``pc``，每个分类覆盖 4 个工段。"""
    from mfg_agents.v2.services.dashboard import PRODUCT_CATEGORIES

    fact = build_quality_dashboard(registry)["pareto_fact"]
    assert fact["prod_cats"] == PRODUCT_CATEGORIES, "分类集合或顺序被改动"
    rows = fact["rows"]
    assert rows, "事实表不应为空"
    assert all(r["pc"] for r in rows), "每行都必须带产品分类（否则下拉筛选会漏数据）"
    assert "2026-10-15" in fact["days"] and fact["months"][0] == "2026-10"
    for pc in fact["prod_cats"]:
        stages = {r["stage"] for r in rows if r["pc"] == pc}
        assert stages == {"SMT", "PCM", "PACK", "整机"}, f"{pc} 工段覆盖不全: {stages}"


def test_pareto_fact_group_rows_not_truncated(registry):
    """分组维度含产品分类后行数翻倍 → row_cap 必须抬高，且分组键保持唯一。"""
    rows = build_quality_dashboard(registry)["pareto_fact"]["rows"]
    assert len(rows) > 2500, f"分组被 row_cap 截断（仅 {len(rows)} 行）"
    keys = {(r["d"], r["cat"], r["stage"], r["pc"], r["type"]) for r in rows}
    assert len(keys) == len(rows), "同一分组键出现重复行"


def test_pareto_drill_filters_by_product_category(registry):
    """按产品分类 + 本块期间下钻：行分子之和 = 事实表里该分类该期间的损失量。"""
    fact = build_quality_dashboard(registry)["pareto_fact"]
    day = fact["days"][0]
    agg: dict[str, int] = {}
    for r in fact["rows"]:
        if r["d"] == day and r["pc"] == "车载":
            agg[r["type"]] = agg.get(r["type"], 0) + r["qty"]
    assert agg, "当日 · 车载 应有数据"
    top = max(agg, key=lambda k: agg[k])
    data = build_pareto_drill(registry, "quality", "defect_rate", top,
                              prod_category="车载", window="day")
    assert data["level"] == 2 and data["terminal"] is True
    assert data["defect_type"] == top and data["prod_category"] == "车载"
    assert "产品分类 车载" in data["definition"], "口径补注应注明本次筛选"
    assert data["rows"], f"「{top}」在 当日 · 车载 下应有产线 · 型号分解"
    assert sum(r["numerator"] for r in data["rows"]) == agg[top], "分摊不守恒"


def test_pareto_drill_prod_category_api(api_client, registry):
    """API 链路：prod_category 与 window=month/day 组合放行；非法 window 仍 400。"""
    top = build_quality_dashboard(registry)["pareto_fact"]["rows"][0]["type"]
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "quality", "kpi": "defect_rate",
                               "defect_type": top, "prod_category": "车载",
                               "window": "month"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["prod_category"] == "车载"
    bad = api_client.get("/api/v2/dashboard/drill",
                         params={"screen": "quality", "kpi": "defect_rate",
                                 "window": "nope"})
    assert bad.status_code == 400


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


# ============================================================
# 5d. 制造看板板级「产品分类 / 工段」筛选（4 张 KPI 卡 = 所选范围汇总）
# ============================================================


def test_mfg_scope_defaults_to_all(registry):
    """缺省不筛选：filter 回显空串，无范围标注，且与显式空参逐字段同值。"""
    plain = build_manufacturing_dashboard(registry, "month")
    explicit = build_manufacturing_dashboard(registry, "month", stage=None,
                                             product_category=None)
    assert plain["filter"] == {"stage": "", "product_category": ""}
    assert explicit["kpis"] == plain["kpis"], "空筛选不得改变既有口径"
    assert explicit["rankings"] == plain["rankings"]
    for k in plain["kpis"]:
        assert "scope_note" not in k, f"{k['key']} 未筛选时不应有范围标注"


def test_mfg_scope_stage_narrows_by_line_prefix(registry):
    """工段筛选按产线前缀收窄：分母变小、值 = 分子 ÷ 分母、TOP5 只剩本工段线体。"""
    base = build_manufacturing_dashboard(registry, "month")
    for stage, prefix in (("SMT", "SMT-"), ("PCM", "DIP-"), ("PACK", "PACK-")):
        d = build_manufacturing_dashboard(registry, "month", stage=stage)
        assert d["filter"] == {"stage": stage, "product_category": ""}
        for key in ("availability", "scrap_rate", "report_rate"):
            b, s = _kpi_of(base, key), _kpi_of(d, key)
            assert s["denominator"] < b["denominator"], f"{stage} 应收窄 {key} 分母"
            assert s["value"] == pytest.approx(
                100.0 * s["numerator"] / s["denominator"], abs=0.05), key
            assert len(s["trend"]) == len(b["trend"]) > 0, "趋势线仍按窗口逐日"
        for key in ("availability", "scrap_rate"):
            items = next(r for r in d["rankings"] if r["key"] == key)["items"]
            assert items, f"{stage} 应有 {key} TOP5"
            assert all(it["line"].startswith(prefix) for it in items), \
                f"{key} TOP5 混入了其它工段的行"


def test_mfg_scope_product_category_narrows_by_model(registry):
    """产品分类筛选按型号归属收窄；工段 + 产品分类是 AND 关系（叠加后再收窄）。"""
    base = build_manufacturing_dashboard(registry, "month")
    cons = build_manufacturing_dashboard(registry, "month", product_category="消费电子")
    assert cons["filter"]["product_category"] == "消费电子"
    for key in ("availability", "test_fpy"):
        assert _kpi_of(cons, key)["denominator"] < _kpi_of(base, key)["denominator"]
    items = next(r for r in cons["rankings"] if r["key"] == "test_fpy")["items"]
    assert items, "消费电子应有型号级 TOP5"
    assert {it["model"] for it in items} <= set(_MFG_CAT_MODELS["消费电子"]), \
        "混入了其它分类的型号"

    smt = build_manufacturing_dashboard(registry, "month", stage="SMT")
    both = build_manufacturing_dashboard(registry, "month", stage="SMT",
                                         product_category="消费电子")
    assert both["filter"] == {"stage": "SMT", "product_category": "消费电子"}
    assert (_kpi_of(both, "availability")["denominator"]
            < _kpi_of(smt, "availability")["denominator"])


def test_mfg_scope_stage_free_kpi_keeps_board_scope(registry):
    """测试直通率无产线维度：工段筛选不改它的值，并标注「工段筛选不适用」。"""
    base = build_manufacturing_dashboard(registry, "month")
    scoped = build_manufacturing_dashboard(registry, "month", stage="PACK")
    b, s = _kpi_of(base, "test_fpy"), _kpi_of(scoped, "test_fpy")
    assert (s["numerator"], s["denominator"]) == (b["numerator"], b["denominator"])
    assert "筛选不适用" in s["scope_note"]
    # 产品分类仍对它生效（型号维度存在）
    assert "scope_note" not in _kpi_of(
        build_manufacturing_dashboard(registry, "month", product_category="车载"),
        "test_fpy")


def test_mfg_scope_empty_range_is_marked(registry):
    """「整机」工段（ASSY-*）扩容后有真实数据：分母 > 0、无「无记录」标注。

    2026-10-21 数据扩容（seed_board_mfg_volume2.sql）为制造四表补入 ASSY-1，
    该筛选不再为空——断言随数据现状更新为「有数据且 TOP5 均为 ASSY 线体」
    （筛选口径本身未改）。
    """
    d = build_manufacturing_dashboard(registry, "month", stage="整机")
    for key in ("availability", "scrap_rate", "report_rate"):
        k = _kpi_of(d, key)
        assert k["denominator"] > 0, f"整机工段应有 {key} 数据"
        assert "无记录" not in (k.get("scope_note") or "")
        items = next(r for r in d["rankings"] if r["key"] == key)["items"]
        assert items, f"整机工段应有 {key} TOP5"
        assert all(it["line"].startswith("ASSY-") for it in items), \
            f"{key} TOP5 混入了其它工段的行"


def test_mfg_scope_ask_text_follows_scope(registry):
    """问数文案必须带上筛选范围（口径与卡片一致），未筛选时保持原文案。"""
    scoped = build_manufacturing_dashboard(registry, "month", stage="SMT",
                                           product_category="消费电子")
    ask = _kpi_of(scoped, "availability")["ask"]
    assert "工段 SMT" in ask and "产品分类 消费电子" in ask
    assert "最近7天" not in ask, "口径替换后不应残留「最近7天」"
    assert (build_manufacturing_dashboard(registry)["kpis"][0]["ask"]
            == "最近7天各产线稼动率如何？最低的是哪条线？")


def test_mfg_scope_api_params(api_client, registry):
    """API 链路：stage / product_category 放行并回显；非法取值 → 400（不静默当「全部」）。"""
    r = api_client.get("/api/v2/dashboard/manufacturing",
                       params={"window": "month", "stage": "SMT",
                               "product_category": "消费电子"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["filter"] == {"stage": "SMT",
                                          "product_category": "消费电子"}

    for bad in ({"stage": "SMT-1"}, {"product_category": "消费"}):
        resp = api_client.get("/api/v2/dashboard/manufacturing", params=bad)
        assert resp.status_code == 400, f"{bad} 应被拒绝"


# ============================================================
# 5e. 2 级看板补充详细数据（整体汇总 / 整体逐日 / 关联原始记录）
# ============================================================


@pytest.mark.parametrize("kpi,screen", [
    ("availability", "manufacturing"), ("scrap_rate", "manufacturing"),
    ("report_rate", "manufacturing"), ("test_fpy", "manufacturing"),
])
def test_l2_detail_supplement_shape(registry, kpi, screen):
    """制造看板四指标的 L2 附带整体汇总 / 整体逐日 / 原始记录三块补充数据。"""
    out = build_drill(registry, screen, kpi, window="month")
    assert {"summary", "daily", "records", "columns"} <= set(out)
    s = out["summary"]
    assert s["items"] == len(out["rows"])
    assert s["ok"] + s["warn"] + s["danger"] == s["items"]
    # 汇总 = 明细行加权之和（分子分母分别相加），不允许与明细对不上
    assert s["numerator"] == sum(r["numerator"] for r in out["rows"])
    assert s["denominator"] == sum(r["denominator"] for r in out["rows"])
    assert s["value"] == pytest.approx(
        100.0 * s["numerator"] / s["denominator"], abs=0.05)
    assert s["status"] == _status(s["value"], s["target"], s["higher_is_better"])
    if out["rows"]:
        assert s["worst"]["name"] == out["rows"][0]["name"], "最差项应与明细首行同源"
    assert len(out["daily"]) == 15, "当月口径应有 15 个逐日点"
    assert out["records"] and len(out["records"]) <= 20
    assert {c["key"] for c in out["columns"]} == set(
        DRILL_DEFS[kpi]["record_cols"])


def test_l2_detail_only_for_scope_declared_kpis(registry):
    """未声明板级筛选的指标：L2 不额外下发原始记录（不给其它屏开明细通道）。"""
    out = build_drill(registry, "quality", "fpy")
    assert "summary" not in out and "records" not in out
    assert out["rows"], "分组明细行仍照常返回"


def test_drill_follows_board_scope(registry):
    """下钻与板级筛选同口径：L2 行只剩所选工段 · 型号，导出实体后 L3 亦受约束。"""
    scoped = build_drill(registry, "manufacturing", "availability", window="month",
                         stage="SMT", product_category="消费电子")
    assert scoped["filter"] == {"stage": "SMT", "product_category": "消费电子"}
    assert "本次筛选" in scoped["definition"]
    rows = scoped["rows"]
    assert rows, "SMT · 消费电子 应有分解行"
    assert {r["entity"]["line"] for r in rows} <= {"SMT-1", "SMT-2", "SMT-3"}
    assert {r["entity"]["model"] for r in rows} <= {"A型号", "E型号"}
    assert scoped["summary"]["denominator"] == sum(r["denominator"] for r in rows)

    l3 = build_drill(registry, "manufacturing", "availability",
                     entity=rows[0]["entity"], window="month",
                     stage="SMT", product_category="消费电子")
    assert l3["level"] == 3 and l3["denominator"] == rows[0]["denominator"]


def test_drill_ignores_scope_for_other_screens(registry):
    """未声明 scope / q_scope 的指标：多传 stage / category / product_category 不得改变既有行为。"""
    plain = build_drill(registry, "quality", "incoming_pass_rate")
    extra = build_drill(registry, "quality", "incoming_pass_rate", stage="SMT",
                        category="外观不良", product_category="车载")
    assert extra["rows"] == plain["rows"]
    assert "filter" not in extra and "本次筛选" not in extra["definition"], \
        "未声明筛选的指标不得回显 / 标注筛选"


def test_drill_follows_quality_scope(registry):
    """品质板级「工段 / 类别」贯通到 2 级下钻：不良率 / 直通率 L2 = 所选范围的分解。"""
    scoped = build_drill(registry, "quality", "defect_rate", window="month",
                         stage="SMT", category="外观不良")
    assert scoped["filter"] == {"stage": "SMT", "category": "外观不良"}
    assert "本次筛选：工段 SMT · 类别 外观不良" in scoped["definition"]
    rows = scoped["rows"]
    assert rows, "SMT · 外观不良 应有分解行"
    # 与 L1 卡片同源：L2 各行分子分母之和必须等于筛选后的卡片值（口径不允许有裂缝）
    card = _kpi_of(build_quality_dashboard(registry, "month", stage="SMT",
                                           category="外观不良"), "defect_rate")
    assert sum(r["denominator"] for r in rows) == card["denominator"]
    assert sum(r["numerator"] for r in rows) == card["numerator"]
    # 直通率与不良率同一套口径（补数），筛选后仍守恒
    fpy = build_drill(registry, "quality", "fpy", window="month",
                      stage="SMT", category="外观不良")
    assert fpy["filter"] == {"stage": "SMT", "category": "外观不良"}
    assert fpy["rows"], "直通率的 L2 同样按筛选分解"
    # 未筛选时与改动前逐字段一致：无筛选回显与标注
    plain = build_drill(registry, "quality", "defect_rate", window="month")
    assert "filter" not in plain and "本次筛选" not in plain["definition"]
    assert len(plain["rows"]) > len(rows), "未筛选应包含更多产线 · 型号组合"
    # L3 同样受板级筛选约束（实体之外的筛选条件一并生效）
    l3 = build_drill(registry, "quality", "defect_rate", entity=rows[0]["entity"],
                     window="month", stage="SMT", category="外观不良")
    assert l3["level"] == 3 and l3["filter"] == {"stage": "SMT", "category": "外观不良"}


def test_drill_api_quality_scope_params_and_validation(api_client, registry):
    """API 链路：品质「不良率 / 直通率」接受 stage / category 并回显；非法值 → 400。"""
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "quality", "kpi": "defect_rate",
                               "window": "month", "stage": "SMT",
                               "category": "外观不良"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["filter"] == {"stage": "SMT", "category": "外观不良"}
    for bad in ({"stage": "DIP-1"}, {"category": "划伤"}):
        params = dict({"screen": "quality", "kpi": "defect_rate"}, **bad)
        resp = api_client.get("/api/v2/dashboard/drill", params=params)
        assert resp.status_code == 400, f"{bad} 应被拒绝"


def test_drill_api_scope_params_and_validation(api_client, registry):
    """API 链路：制造四指标接受 stage / prod_category 并回显；非法值 → 400。"""
    r = api_client.get("/api/v2/dashboard/drill",
                       params={"screen": "manufacturing", "kpi": "availability",
                               "window": "month", "stage": "PCM",
                               "prod_category": "车载"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["filter"] == {"stage": "PCM", "product_category": "车载"}
    assert d["summary"] and d["records"]
    for bad in ({"stage": "DIP-1"}, {"prod_category": "消费"}):
        resp = api_client.get("/api/v2/dashboard/drill",
                              params=dict({"screen": "manufacturing",
                                           "kpi": "availability"}, **bad))
        assert resp.status_code == 400, f"{bad} 应被拒绝"


# ============================================================
# 5c. 品质看板板级「工段 / 缺陷类别」筛选（4 张 KPI 卡 = 所选范围汇总）
# ============================================================


def _kpi_of(data, key):
    return next(k for k in data["kpis"] if k["key"] == key)


def test_quality_scope_defaults_to_all(registry):
    """缺省不筛选：filter 回显空串，无「筛选不适用」标注，且与显式空参逐字段同值。"""
    plain = build_quality_dashboard(registry, "month")
    explicit = build_quality_dashboard(registry, "month", stage=None, category=None)
    assert plain["filter"] == {"stage": "", "category": ""}
    assert explicit["kpis"] == plain["kpis"], "空筛选不得改变既有口径"
    for k in plain["kpis"]:
        assert "scope_note" not in k, f"{k['key']} 未筛选时不应有范围标注"


def test_quality_scope_recomputes_defect_source_kpis(registry):
    """同源指标随筛选重算：den 收窄、值 = 分子 ÷ 分母，且与全厂口径不同源可比。"""
    base = build_quality_dashboard(registry, "month")
    smt = build_quality_dashboard(registry, "month", stage="SMT")
    b_def, s_def = _kpi_of(base, "defect_rate"), _kpi_of(smt, "defect_rate")
    b_fpy, s_fpy = _kpi_of(base, "fpy"), _kpi_of(smt, "fpy")
    assert s_def["denominator"] < b_def["denominator"], "工段筛选应收窄受检总数"
    assert s_def["numerator"] < b_def["numerator"]
    for k in (s_def, s_fpy):
        assert k["value"] == pytest.approx(
            100.0 * k["numerator"] / k["denominator"], abs=0.05), k["key"]
    # 直通率 = 1 − 不良率（同一份明细的补数，筛选后仍守恒）
    assert s_fpy["denominator"] == s_def["denominator"]
    assert s_fpy["numerator"] + s_def["numerator"] == s_def["denominator"]
    assert len(s_def["trend"]) == len(b_def["trend"]) > 0, "趋势线仍按窗口逐日"


def test_quality_scope_category_narrows_further(registry):
    """工段 + 类别叠加：分母在工段基础上继续收窄（两个条件是 AND 关系）。"""
    smt = build_quality_dashboard(registry, "month", stage="SMT")
    both = build_quality_dashboard(registry, "month", stage="SMT", category="外观不良")
    assert both["filter"] == {"stage": "SMT", "category": "外观不良"}
    assert (_kpi_of(both, "defect_rate")["denominator"]
            < _kpi_of(smt, "defect_rate")["denominator"])


def test_quality_scope_marks_scope_free_kpis(registry):
    """无该维度的两项保持板级口径并标注「筛选不适用」，值不得被筛选改动。"""
    base = build_quality_dashboard(registry, "month")
    scoped = build_quality_dashboard(registry, "month", stage="PACK", category="性能不良")
    for key in ("incoming_pass_rate", "complaint_close_rate"):
        b, s = _kpi_of(base, key), _kpi_of(scoped, key)
        assert (s["numerator"], s["denominator"]) == (b["numerator"], b["denominator"])
        assert "筛选不适用" in s["scope_note"]
    for key in ("defect_rate", "fpy"):
        assert "scope_note" not in _kpi_of(scoped, key)


def test_quality_scope_ask_text_follows_scope(registry):
    """问数文案必须带上筛选范围，不能只说「各产线」而漏掉工段 / 类别。"""
    scoped = build_quality_dashboard(registry, "month", stage="PACK", category="性能不良")
    ask = _kpi_of(scoped, "defect_rate")["ask"]
    assert "工段 PACK" in ask and "类别 性能不良" in ask
    assert "最近7天" not in ask, "口径替换后不应残留「最近7天」"
    # 未筛选时保持原文案（回归护栏）
    assert build_quality_dashboard(registry)["kpis"][0]["ask"] == "最近7天各产线不良率如何？最高的是哪条线？"


def test_quality_scope_rankings_follow_scope(registry):
    """TOP5 与卡片同源：工段筛选后只出现该工段的线体（SMT → SMT-3）。"""
    smt = build_quality_dashboard(registry, "month", stage="SMT")
    items = next(r for r in smt["rankings"] if r["key"] == "defect_rate")["items"]
    assert items, "SMT 工段应有不良率 TOP5"
    assert {it["line"] for it in items} == {"SMT-3"}, "TOP5 混入了其它工段的行"


def test_quality_scope_api_params(api_client, registry):
    """API 链路：stage / category 放行并回显；非法取值 → 400（不静默当「全部」）。"""
    r = api_client.get("/api/v2/dashboard/quality",
                       params={"window": "month", "stage": "SMT",
                               "category": "外观不良"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["filter"] == {"stage": "SMT", "category": "外观不良"}

    for bad in ({"stage": "SMT-1"}, {"category": "全部不良"}):
        resp = api_client.get("/api/v2/dashboard/quality", params=bad)
        assert resp.status_code == 400, f"{bad} 应被拒绝"


# ============================================================
# 8. 来料质量 TOP10（固定全量口径 · L1 只出 TOP10）
# ============================================================


def test_incoming_top10_lists_are_fixed_scope(registry):
    """来料质量 L1 只出 TOP10（``incoming_top10``）：两张榜、各 10 条、固定全量口径。

    2026-10-21 产品调整：L1 不再出「全量供应商排序表」，改由 ``incoming_top10``
    承载「来料批次合格率 / 来料上线不良率」两张 TOP10。``rankings`` 里的
    ``incoming_pass_rate`` 只保留为「每 KPI 一条配套榜」的结构占位，
    不再带 ``view="supplier_table"``（全量排序表这一形态整体下线）。
    """
    d = build_quality_dashboard(registry)
    ranks = {r["key"]: r for r in d["rankings"]}
    assert all(r.get("view") != "supplier_table" for r in d["rankings"]), \
        "L1 不应再有全量供应商排序表"
    assert len(ranks["incoming_pass_rate"]["items"]) <= 5, "配套榜应回到 TOP5"

    inc = {r["key"]: r for r in d["incoming_top10"]}
    assert set(inc) == {"incoming_batch_quality", "incoming_line_defect"}
    for key, r in inc.items():
        assert r["view"] == "incoming_top10", key
        assert r["dimension"] == "供应商", key
        assert len(r["items"]) == 10, f"{key} 应只出 TOP10，实际 {len(r['items'])}"
        assert all((i["entity"] or {}).get("supplier") for i in r["items"]), \
            "下钻实体应为供应商"
        assert all(i["rank"] == n for n, i in enumerate(r["items"], 1)), \
            f"{key} 名次必须与实际顺序一致"

    # 批次合格率：越高越好 → 取最低 10（升序）；上线不良率：越低越好 → 取最高 10（降序）
    low = [i["value"] for i in inc["incoming_batch_quality"]["items"]]
    assert low == sorted(low), "来料批次合格率应升序（低分在上）"
    high = [i["value"] for i in inc["incoming_line_defect"]["items"]]
    assert high == sorted(high, reverse=True), "来料上线不良率应降序"


def test_incoming_top10_api_is_window_free(api_client):
    """API 链路：来料 TOP10 固定全量口径——切换板级窗口不改变它。"""
    snaps = {}
    for win in ("window", "month", "day"):
        r = api_client.get("/api/v2/dashboard/quality", params={"window": win})
        assert r.status_code == 200, r.text
        inc = {x["key"]: x for x in r.json()["data"]["incoming_top10"]}
        assert len(inc["incoming_batch_quality"]["items"]) == 10
        assert len(inc["incoming_line_defect"]["items"]) == 10
        snaps[win] = [(i["name"], i["value"])
                      for i in inc["incoming_batch_quality"]["items"]]
    base = snaps["window"]
    for win, snap in snaps.items():
        assert snap == base, f"来料批次合格率 TOP10 不应随板级窗口变化（{win}）"
