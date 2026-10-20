"""标准三率两级看板 + 班组明细弹窗测试。

口径铁律（改动前必读）：
- 整体 / 工段 / 班组共用同一套 ``_three_rates``（完整率 = 有文件行/总行，
  完善率 = 完善行/有文件行，遵守率 = Σaudit_conform/Σaudit_total，
  符合率 = 三者乘积）；
- 四个工段按审核项加权后的符合率必须 == 整体符合率（允许 1 位小数舍入差）；
- 班组弹窗的原因归因（``std_team_cause``）按天落库，任何口径下
  同维度原因 ``occurrences`` 之和 == 该维度不符合数（从审核明细现算），
  「弹窗数字与明细可对账」是这条链路的硬约束。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services.dashboard import (
    build_quality_dashboard,
    build_std_team_detail,
)

_STAGES = ("SMT", "PCM", "PACK", "整机")
_TARGET_CONFORMITY = 88.2  # 100% × 98% × 90%


@pytest.fixture(scope="module")
def registry():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator().runtime.data_registry


# ============================================================
# 1. 两级看板结构
# ============================================================

def test_two_level_structure(registry):
    s = build_quality_dashboard(registry, "window")["standard_three_rates"]
    assert s["stage_count"] == 4
    assert s["team_count"] == 60          # 全厂 4 × 15
    assert [x["stage"] for x in s["by_stage"]] == list(_STAGES)
    for st in s["by_stage"]:
        assert len(st["teams"]) == 15
        assert st["team_count"] == 15
        # 班组按符合率升序（最差在前，前端 TOP1–3 标记依赖此顺序）
        rates = [t["conformity_rate"] for t in st["teams"]]
        assert rates == sorted(rates)


def test_stage_weighting_matches_overall(registry):
    for win in ("window", "day", "month"):
        s = build_quality_dashboard(registry, win)["standard_three_rates"]
        o = s["overall"]
        items = sum(st["audit_items"] for st in s["by_stage"])
        assert items == o["audit_items"], f"{win}: 工段审核项之和 != 整体"
        weighted = sum(st["conformity_rate"] * st["audit_items"]
                       for st in s["by_stage"]) / items
        assert abs(weighted - o["conformity_rate"]) < 0.5, (
            f"{win}: 四段加权符合率 {weighted:.2f} 与整体 "
            f"{o['conformity_rate']} 偏差超舍入容差")


# ============================================================
# 2. 班组明细弹窗
# ============================================================

def test_team_detail_rates_match_l2_row(registry):
    """弹窗三率必须与 2 级看板里该班组行完全一致（同口径同数据）。"""
    s = build_quality_dashboard(registry, "window")["standard_three_rates"]
    stage = next(x for x in s["by_stage"] if x["stage"] == "PCM")
    worst = stage["teams"][0]
    d = build_std_team_detail(registry, "window", "PCM", worst["team"])
    assert d["found"] is True
    for key in ("completeness_rate", "perfection_rate",
                "compliance_rate", "conformity_rate", "audit_rows", "audit_items"):
        assert d["rates"][key] == worst[key], f"{key} 与 2 级看板不一致"
    assert d["window"]["start"] and d["window"]["end"]
    # 差值小签：与工段差由两份现算数据相减可得
    assert d["deltas"]["vs_stage"] == pytest.approx(
        round((worst["conformity_rate"] - stage["conformity_rate"]) * 10) / 10)


def test_team_detail_causes_reconcile(registry):
    """同维度原因 occurrences 之和 == 该维度不符合数（弹窗可对账的硬约束）。"""
    d = build_std_team_detail(registry, "window", "PCM", "PCM十五班")
    assert d["dim_totals"], "维度合计不应为空"
    for dim, total in d["dim_totals"].items():
        occ = sum(c["occurrences"] for c in d["causes"] if c["rate_dim"] == dim)
        assert occ == total, f"{dim}: 原因合计 {occ} != 维度不符合数 {total}"
    for c in d["causes"]:
        assert c["cause"] and c["unit"]
        assert 0 <= c["share"] <= 100
        if c["rate_dim"] == "compliance":
            assert c["defect_type"], "遵守率原因应关联不良类型"


def test_team_detail_step_and_day_granularity(registry):
    d = build_std_team_detail(registry, "window", "PCM", "PCM十五班")
    steps = {s["step"] for s in d["by_step"]}
    assert steps == {"分容配组", "极耳焊接", "保护板贴装", "电性能测试", "内阻检测"}
    for s in d["by_step"]:
        # 工序级四率都在 (0, 100]，不允许出现 0%（生成器已按天打散缺失）
        assert 0 < s["completeness_rate"] <= 100
        assert 0 < s["conformity_rate"] <= 100
    assert len(d["by_day"]) == 7
    for x in d["by_day"]:
        assert x["d"] and 0 < x["conformity_rate"] <= 100


def test_team_detail_unknown_team(registry):
    """不存在的班组不抛错：found=False + 全零骨架（「本窗口没被审核」是有效结论）。"""
    d = build_std_team_detail(registry, "window", "PCM", "不存在的班组")
    assert d["found"] is False
    assert d["rates"]["conformity_rate"] == 0.0
    assert d["by_step"] == [] and d["by_day"] == [] and d["causes"] == []
