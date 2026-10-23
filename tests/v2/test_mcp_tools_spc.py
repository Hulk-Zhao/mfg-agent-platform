"""SPC 域测试：spc.capability / spc.pareto / spc.ingest / spc.report。

覆盖点：
- 统计内核双西格玛口径：Cp/Cpk 用组内 σ（R̄/d2）、Pp/Ppk 用整体 σ（ddof=1），
  以手算基准钉死（Pp 不得恒等于 Cp）——这是移植 Hogo 的核心价值；
- normalize_measurements：确定性校验（非法值显式拒绝，不静默丢弃/补齐）；
- 缺规格限 / 无测量值 / 小子组 → data_gap 诚实降级，不编造指数；
- 柏拉图：降序 + 累计占比 + 80% 关键少数；
- spc.ingest：须身份、先校验后落 spc_measurement、回执可核对（隔离库）；
- spc.report：能力分析 → 报表草稿（禁词过滤 / is_draft / degraded）+ ingest 闭环取数；
- 工具注册与归组：spc 域 3 个工具、spc.report 归 knowledge 域，展平 == build_platform_tools。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.core.spc import (
    build_pareto,
    compute_capability,
    normalize_measurements,
)
from mfg_agents.v2.mcp_tools import ToolRegistry
from mfg_agents.v2.mcp_tools.tools import (
    DOMAIN_TOOLSETS,
    build_domain_toolsets,
    build_platform_tools,
    register_platform_tools,
)
from mfg_agents.v2.services.spc_store import count_measurements
from mfg_agents.v2.services.workflow import PermissionDenied

_QA_CTX = {"user_id": "u-qa", "roles": ["quality"]}


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


@pytest.fixture(scope="module")
def registry(orch) -> ToolRegistry:
    return register_platform_tools(ToolRegistry(), orch)


@pytest.fixture
def isolated_spc_db(orch, tmp_path, monkeypatch):
    """把读写路径同时指向临时库：SPC 写入（DB_URL）与注册查询读取（DB_READ_URL）同源隔离。"""
    from mfg_agents.common.config import settings

    db = tmp_path / "spc_iso.db"
    source = orch.runtime.data_registry.get()
    monkeypatch.setattr(source, "db_path", str(db))
    monkeypatch.setattr(settings, "DB_URL", str(db))
    monkeypatch.setattr(settings, "DB_READ_URL", str(db))
    return str(db)


# ============================================================
# 1. 统计内核：双西格玛口径（手算基准）
# ============================================================
# 基准数据集：subgroups = [[1,3],[2,4]]，n=2（d2=1.128），usl=6 / lsl=-1
#   mean = 2.5
#   σ_within  = R̄/d2 = 2/1.128            ≈ 1.77305  → Cp  = 7/(6σ) ≈ 0.6580
#   σ_overall = std(ddof=1) = √(5/3)      ≈ 1.29099  → Pp  = 7/(6σ) ≈ 0.9037
#   两侧对称 → Cpk == Cp，Ppk == Pp；Cp ≠ Pp 证明双口径确实拆开


def test_capability_dual_sigma_hand_computed():
    out = compute_capability(usl=6, lsl=-1, subgroups=[[1, 3], [2, 4]],
                             characteristic="基准件")
    assert out["data_gap"] is False
    assert out["mean"] == pytest.approx(2.5)
    assert out["subgroup_size"] == 2
    assert out["n_subgroups"] == 2
    assert out["sigma_within"] == pytest.approx(2 / 1.128, abs=1e-4)
    assert out["sigma_overall"] == pytest.approx((5 / 3) ** 0.5, abs=1e-4)
    assert out["cp"] == pytest.approx(0.6580, abs=1e-4)
    assert out["cpk"] == pytest.approx(0.6580, abs=1e-4)
    assert out["pp"] == pytest.approx(0.9037, abs=1e-4)
    assert out["ppk"] == pytest.approx(0.9037, abs=1e-4)
    # 核心：整体口径与组内口径必须分开（旧工具缺陷是二者相等）
    assert out["pp"] != out["cp"]


def test_capability_flat_values_chunking():
    """扁平 values + subgroup_size 切分，与显式 subgroups 等价。"""
    flat = compute_capability(usl=6, lsl=-1, values=[1, 3, 2, 4], subgroup_size=2)
    explicit = compute_capability(usl=6, lsl=-1, subgroups=[[1, 3], [2, 4]])
    assert flat["cp"] == explicit["cp"]
    assert flat["ppk"] == explicit["ppk"]


def test_capability_verdict_bands():
    good = compute_capability(usl=10.0, lsl=0.0, subgroups=[[5, 5.05], [4.95, 5.0]])
    # σ_within 很小 → Cpk 很高 → 合格
    assert good["verdict"] == "合格"


def test_capability_cpk_uses_worse_side():
    """均值偏向一侧时 Cpk < Cp（单侧能力受限）。"""
    # 组内 R̄=2 → σ_within≈1.773；mean=7.5 偏向 USL=10 一侧 → Cpk 由上侧封顶
    out = compute_capability(usl=10, lsl=0, subgroups=[[6, 8], [7, 9]])
    assert out["cp"] is not None and out["cpk"] is not None
    assert out["cpk"] < out["cp"]


def test_capability_degrades_without_spec_limits():
    out = compute_capability(usl=None, lsl=None, subgroups=[[1, 3], [2, 4]])
    assert out["data_gap"] is True
    assert out["cp"] is None and out["cpk"] is None
    assert any("规格限" in w for w in out["warnings"])


def test_capability_degrades_without_measurements():
    out = compute_capability(usl=10, lsl=0)
    assert out["data_gap"] is True
    assert any("未提供测量值" in w for w in out["warnings"])


def test_capability_degrades_with_single_subgroup():
    out = compute_capability(usl=10, lsl=0, subgroups=[[1, 2, 3, 4, 5]])
    assert out["data_gap"] is True


def test_capability_rejects_illegal_spec():
    out = compute_capability(usl=1, lsl=10, subgroups=[[1, 2], [3, 4]])
    assert out["data_gap"] is True


# ============================================================
# 2. 柏拉图
# ============================================================


def test_pareto_ordering_and_vital_few():
    items = [{"name": "D", "qty": 8}, {"name": "A", "qty": 50},
             {"name": "C", "qty": 12}, {"name": "B", "qty": 30}]
    out = build_pareto(items)
    assert out["data_gap"] is False
    assert out["total"] == 100
    assert [i["name"] for i in out["items"]] == ["A", "B", "C", "D"]
    assert [i["cum_pct"] for i in out["items"]] == pytest.approx([0.5, 0.8, 0.92, 1.0])
    # 累计首达 80% → 前 2 项为关键少数
    assert out["vital_few_count"] == 2
    assert [i["name"] for i in out["vital_few"]] == ["A", "B"]


def test_pareto_zero_and_empty_degrades():
    assert build_pareto([])["data_gap"] is True
    assert build_pareto([{"name": "A", "qty": 0}])["data_gap"] is True


# ============================================================
# 3. 工具注册与归组
# ============================================================


def test_spc_domain_registered(orch):
    toolsets = build_domain_toolsets(orch)
    assert "spc" in toolsets
    assert [t.name for t in toolsets["spc"]] == [
        "spc.capability", "spc.pareto", "spc.ingest"]
    assert [t.name for t in toolsets["spc"]] == DOMAIN_TOOLSETS["spc"]
    # spc.report 归 knowledge 域（报表草稿与 report.draft 同源）
    assert "spc.report" in [t.name for t in toolsets["knowledge"]]


def test_spc_tools_tier_and_readonly(registry):
    by_name = {t.name: t for t in registry.list()}
    # 只读分析（tier=0 自动放行）
    for name in ("spc.capability", "spc.pareto"):
        assert name in by_name, f"未注册: {name}"
        assert by_name[name].tier == 0, name
        assert by_name[name].readonly is True, name
        assert by_name[name].domain == "spc", name
    # 写类：导入（tier=2）/ 报表草稿（tier=1）
    assert by_name["spc.ingest"].domain == "spc"
    assert by_name["spc.ingest"].tier == 2
    assert by_name["spc.ingest"].readonly is False
    assert by_name["spc.report"].domain == "knowledge"
    assert by_name["spc.report"].tier == 1
    assert by_name["spc.report"].readonly is False


# ============================================================
# 4. 工具层调用
# ============================================================


def test_tool_capability_inline_values(registry):
    out = registry.call("spc.capability",
                        {"characteristic": "外壳长度", "values": [1, 3, 2, 4],
                         "subgroup_size": 2, "usl": 6, "lsl": -1},
                        user_ctx=_QA_CTX)
    assert out["data_gap"] is False
    assert out["cp"] == pytest.approx(0.6580, abs=1e-4)
    assert out["characteristic"] == "外壳长度"


def test_tool_capability_missing_spec_degrades(registry):
    out = registry.call("spc.capability",
                        {"values": [1, 3, 2, 4], "subgroup_size": 2},
                        user_ctx=_QA_CTX)
    assert out["data_gap"] is True
    assert out["cp"] is None


def test_tool_pareto_inline_items(registry):
    out = registry.call("spc.pareto",
                        {"items": [{"name": "锡膏印刷", "qty": 50},
                                   {"name": "虚焊", "qty": 30},
                                   {"name": "异物", "qty": 20}]},
                        user_ctx=_QA_CTX)
    assert out["data_gap"] is False
    assert out["channel"] == "inline"
    assert out["items"][0]["name"] == "锡膏印刷"
    assert out["total"] == 100


def test_tool_pareto_default_registered_query(registry):
    """默认路径：吃 quality_defect 全量不良分布（表级 ACL 走 quality 角色）。"""
    out = registry.call("spc.pareto", {}, user_ctx=_QA_CTX)
    if out.get("data_gap"):
        pytest.skip(f"演示库无不良分布数据：{out.get('warnings')}")
    assert out["channel"] == "registered"
    assert out["count"] >= 1
    assert out["items"][0]["name"]  # 有缺陷类型
    # 降序
    qtys = [i["qty"] for i in out["items"]]
    assert qtys == sorted(qtys, reverse=True)


# ============================================================
# 5. 测量值校验（纯函数，spc.ingest 落库前的确定性前置）
# ============================================================


def test_normalize_measurements_valid_chunk_and_attrs():
    out = normalize_measurements(characteristic="外壳长度", values=[1, 3, 2, 4],
                                 subgroup_size=2, usl=6, lsl=-1, source="手工")
    assert out["n_accepted"] == 4 and out["n_rejected"] == 0
    assert [r["subgroup_no"] for r in out["accepted"]] == [1, 1, 2, 2]
    assert [r["seq"] for r in out["accepted"]] == [1, 2, 1, 2]
    assert out["accepted"][0]["usl"] == 6 and out["accepted"][0]["lsl"] == -1
    assert out["accepted"][0]["source"] == "手工"


def test_normalize_measurements_rejects_non_finite():
    out = normalize_measurements(characteristic="X", values=[1, "bad", None, True, float("nan")])
    assert out["n_accepted"] == 1 and out["n_rejected"] == 4
    assert any("已拒绝" in w for w in out["warnings"])
    assert all(r["reason"] == "非有限数值" for r in out["rejected"])


def test_normalize_measurements_subgroups_shape():
    out = normalize_measurements(characteristic="X", subgroups=[[1, 3], [2, 4]])
    assert out["n_accepted"] == 4
    assert {r["subgroup_no"] for r in out["accepted"]} == {1, 2}


def test_normalize_measurements_degrades_without_values():
    out = normalize_measurements(characteristic="X")
    assert out["n_accepted"] == 0 and out["n_rejected"] == 0
    assert any("未提供测量值" in w for w in out["warnings"])


# ============================================================
# 6. spc.ingest（tier 2 · 写；隔离库）
# ============================================================


def test_ingest_requires_identity(registry):
    with pytest.raises(PermissionDenied):
        registry.call("spc.ingest", {"characteristic": "X", "values": [1, 2]})


def test_ingest_persists_receipt_and_isolation(registry, isolated_spc_db):
    out = registry.call("spc.ingest",
                        {"characteristic": "外壳长度", "values": [1, 3, 2, 4],
                         "subgroup_size": 2, "usl": 6, "lsl": -1, "source": "手工"},
                        user_ctx=_QA_CTX)
    assert out["data_gap"] is False
    assert out["accepted_count"] == 4 and out["written"] == 4
    assert out["rejected"] == []
    assert out["count_after"] == 4
    # 落库可核对（写入与经注册查询同源库）
    assert count_measurements(isolated_spc_db, "外壳长度") == 4


def test_ingest_all_invalid_degrades_without_write(registry, isolated_spc_db):
    out = registry.call("spc.ingest",
                        {"characteristic": "异常件", "values": ["bad", None]},
                        user_ctx=_QA_CTX)
    assert out["data_gap"] is True
    assert out["written"] == 0 and out["accepted_count"] == 0
    assert len(out["rejected"]) == 2
    assert count_measurements(isolated_spc_db, "异常件") == 0


# ============================================================
# 7. spc.report（tier 1 · 草稿）
# ============================================================


def test_report_draft_with_explicit_inputs(registry):
    out = registry.call("spc.report",
                        {"characteristic": "停线工位尺寸", "usl": 6, "lsl": -1,
                         "values": [1, 3, 2, 4], "subgroup_size": 2},
                        user_ctx=_QA_CTX)
    assert out["is_draft"] is True
    assert out["degraded"] is True  # 离线无 LLM → 模板降级
    assert out["capability"]["cpk"] == pytest.approx(0.6580, abs=1e-4)
    # 禁词过滤：'停线' → '计划内暂停'
    assert "停线" not in out["text"]
    assert "计划内暂停" in out["text"]


def test_report_degrades_without_inputs(registry):
    out = registry.call("spc.report", {"characteristic": "空特性"}, user_ctx=_QA_CTX)
    assert out["is_draft"] is True and out["degraded"] is True
    assert out["capability"] is None
    assert any("缺少规格限" in w for w in out["warnings"])


def test_report_closed_loop_from_ingested_data(registry, isolated_spc_db):
    """ingest → report 闭环：按特性从 spc_measurement 取数并分析。"""
    registry.call("spc.ingest",
                  {"characteristic": "闭环件", "values": [1, 3, 2, 4],
                   "subgroup_size": 2, "usl": 6, "lsl": -1},
                  user_ctx=_QA_CTX)
    out = registry.call("spc.report", {"characteristic": "闭环件"}, user_ctx=_QA_CTX)
    assert out["capability"] is not None
    assert out["capability"]["cpk"] == pytest.approx(0.6580, abs=1e-4)
