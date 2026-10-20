"""订单履约看板「模块点击详情」测试（状态管道点环节 / 风险订单点行）。

口径铁律（改动前必读）：
- 环节弹窗（build_order_stage_detail）与看板 pipeline **同源同口径**：
  同一张 order_fulfillment、同一个 due_date 窗口——弹窗汇总的订单数 /
  延期数必须与管道环节块逐一相等，否则弹窗与大盘对不上账；
- 订单详情弹窗（build_order_detail）客户身份字段走 data_scope.yaml 的
  ``orders[].customer*`` / ``order.customer*`` 规则，与 risk_orders[] 同一套
  keep_roles——看板里被脱敏的客户，弹窗里也看不到；
- 查无此单返回 found:false 骨架（200 不是 404），与班组明细弹窗同一裁决。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services.dashboard import (
    ORDER_STAGES,
    build_order_dashboard,
    build_order_detail,
    build_order_stage_detail,
)


@pytest.fixture(scope="module")
def registry():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator().runtime.data_registry


# ============================================================
# 1. 环节弹窗与管道对账
# ============================================================

def test_stage_detail_matches_pipeline(registry):
    """每个环节：弹窗汇总的订单数 / 延期数 == 看板管道环节块（同口径可对账）。"""
    d = build_order_dashboard(registry, "window")
    for p in d["pipeline"]:
        s = build_order_stage_detail(registry, p["stage"], "window")
        assert s["summary"]["count"] == p["count"], (
            f"{p['stage']}: 弹窗汇总 {s['summary']['count']} != 管道 {p['count']}")
        assert s["summary"]["delayed"] == (p.get("delayed") or 0)
        for r in s["orders"]:
            assert r["order_no"]
            # 清单行必须都属于该环节（窗口一致，不允许串环节）
            assert r.get("stage") == p["stage"] or "stage" not in r


def test_stage_detail_all_windows(registry):
    for win in ("window", "day", "month"):
        s = build_order_stage_detail(registry, "生产", win)
        assert s["window"]["label"]
        assert s["summary"]["count"] >= 0
        # 平均进度在 [0, 100]（无订单时为 0）
        assert 0 <= s["summary"]["avg_progress"] <= 100


def test_stage_detail_row_fields_and_cap(registry):
    d = build_order_stage_detail(registry, "生产", "window")
    assert len(d["orders"]) <= 50
    assert d["truncated"] == max(0, d["summary"]["count"] - len(d["orders"]))
    if d["orders"]:
        need = {"order_no", "customer", "product_model", "qty",
                "progress", "delay_days", "risk_type", "owner"}
        assert need <= set(d["orders"][0].keys())


# ============================================================
# 2. 订单详情弹窗
# ============================================================

def test_order_detail_found(registry):
    """用环节清单里的真实订单号反查：found=True 且全字段齐。"""
    s = build_order_stage_detail(registry, "生产", "window")
    assert s["orders"], "种子数据在最近7天窗口内应有生产环节订单"
    no = s["orders"][0]["order_no"]
    d = build_order_detail(registry, no)
    assert d["found"] is True
    assert d["order"]["order_no"] == no
    for key in ("customer", "customer_level", "product_model", "qty",
                "due_date", "stage", "progress", "owner"):
        assert key in d["order"]
    # 齐套率与明细可对账：kit_rate == done/total（有分母时）
    kt = d["order"].get("kit_total_qty") or 0
    kd = d["order"].get("kit_done_qty") or 0
    if kt > 0:
        assert d["kit_rate"] == pytest.approx(round(kd / kt * 100, 1))
    else:
        assert d["kit_rate"] is None


def test_order_detail_unknown_order(registry):
    """查无此单不抛错：found=False + 空骨架（「查无此单」是有效结论）。"""
    d = build_order_detail(registry, "SO-0000-00000")
    assert d["found"] is False
    assert d["order"] == {}
    assert d["kit_rate"] is None


# ============================================================
# 3. 客户身份脱敏（弹窗与看板同一套规则）
# ============================================================

def test_stage_and_detail_customer_masks():
    """仓库岗位看板里客户是代号 → 两个弹窗里也必须是代号（不许后门）。"""
    from mfg_agents.v2.services.data_scope import get_data_scope

    ds = get_data_scope()
    keep = {"customer": "华为终端", "customer_level": "S"}

    stage_payload = {"orders": [dict(keep)]}
    masked, labels = ds.apply("orders", stage_payload, roles=["warehouse"])
    assert masked["orders"][0]["customer"] != "华为终端"
    assert masked["orders"][0]["customer"].startswith("客户")
    assert masked["orders"][0]["customer_level"] == "—"
    assert "客户名称" in labels

    detail_payload = {"order": dict(keep)}
    masked, labels = ds.apply("orders", detail_payload, roles=["warehouse"])
    assert masked["order"]["customer"].startswith("客户")
    assert masked["order"]["customer_level"] == "—"

    # 对客责任岗位（sales）看真名
    detail_payload = {"order": dict(keep)}
    masked, _ = ds.apply("orders", detail_payload, roles=["sales"])
    assert masked["order"]["customer"] == "华为终端"
    assert masked["order"]["customer_level"] == "S"


def test_order_stages_alias_matches_pipeline_literal():
    """API 白名单别名与聚合器环节表同源（防止两处字面量漂移）。"""
    from mfg_agents.v2.services.dashboard import _ORDER_STAGES
    assert ORDER_STAGES == _ORDER_STAGES
