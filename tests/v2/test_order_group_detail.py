"""订单履约 2 级看板「点行」分组明细弹窗测试。

口径铁律（改动前必读）：
- 弹窗与 ``build_drill``（screen=orders）的 L2 **同一套** ``DRILL_DEFS`` 口径：
  同表 / 同 ``due_date`` 窗口 / 同 ``num_expr``/``den_expr`` / 同实体过滤——
  弹窗头部的指标值、分子分母与 2 级看板该行**逐字段一致**（可对账是硬约束）；
- 分组在窗口内无订单 → ``found=False`` 骨架（200 不是 404，与班组明细弹窗同一裁决）；
- 客户身份字段由 data_scope.yaml 的 ``orders[].customer`` 规则按岗位脱敏：
  仓库等岗位看到的客户必须是「客户 X」代号、等级必须是「—」。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services.dashboard import build_drill, build_order_group_detail


@pytest.fixture(scope="module")
def registry():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator().runtime.data_registry


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


def _token(client, *roles: str) -> str:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _get(client, path: str, roles: tuple[str, ...]):
    headers = {"Authorization": f"Bearer {_token(client, *roles)}"}
    return client.get(path, headers=headers)


# ============================================================
# 1. 口径对账：弹窗 == 2 级看板该行
# ============================================================

@pytest.mark.parametrize("kpi", ["otd", "otif", "delay_rate"])
def test_customer_group_matches_l2_row(registry, kpi):
    """客户维度指标：弹窗头部值 / 分子分母必须与 L2 该行逐字段一致。"""
    l2 = build_drill(registry, "orders", kpi, None, window="window")
    row = next(r for r in l2["rows"] if r.get("entity"))
    d = build_order_group_detail(registry, "window", kpi, row["entity"])
    assert d["found"] is True
    assert d["value"] == row["value"], f"{kpi}: 弹窗值与 2 级看板不一致"
    assert d["numerator"] == row["numerator"]
    assert d["denominator"] == row["denominator"]
    assert d["status"] == row["status"]
    # 清单封顶 50 条（order_limit）：截断必须标注，且截断量 = 分母 − 清单数；
    # 指标值 / 分子分母仍是全量口径（上面三条断言已对账）。
    truncated = int(row["denominator"]) > d["order_limit"]
    assert len(d["orders"]) == min(int(row["denominator"]), d["order_limit"])
    assert truncated == any("截断" in n for n in d["notes"]), d["notes"]
    # 列 = record_cols + progress/owner，且每单至少带订单号
    keys = {c["key"] for c in d["columns"]}
    assert {"order_no", "progress", "owner"} <= keys


def test_model_group_matches_l2_row(registry):
    """齐套率是型号维度：弹窗同样与 L2 该行对账（分母是应齐套料数，非订单数）。"""
    l2 = build_drill(registry, "orders", "kit_rate", None, window="window")
    row = next(r for r in l2["rows"] if r.get("entity"))
    d = build_order_group_detail(registry, "window", "kit_rate", row["entity"])
    assert d["found"] is True
    assert d["value"] == row["value"]
    assert d["numerator"] == row["numerator"]
    assert d["denominator"] == row["denominator"]
    # 齐套率明细不泄露客户字段（型号维度与客户无关，别把客户列顺手带出来）
    assert d["orders"], "应有明细订单"
    assert all("customer" not in o and "customer_level" not in o for o in d["orders"])


def test_unknown_group_returns_found_false(registry):
    """不存在的分组不抛错：found=False + 空清单（「该口径无订单」是有效结论）。"""
    d = build_order_group_detail(registry, "window", "otd",
                                 {"customer": "不存在的客户", "level": "Z"})
    assert d["found"] is False
    assert d["orders"] == []
    assert d["notes"], "found=False 必须说明原因"


# ============================================================
# 2. 接口裁决：401 / 403 / 400
# ============================================================

def test_group_requires_token(client):
    assert client.get("/api/v2/dashboard/orders/group?kpi=otd"
                      "&customer=x&level=S").status_code == 401


def test_group_denied_for_hr(client):
    resp = _get(client, "/api/v2/dashboard/orders/group?kpi=otd"
                "&customer=x&level=S", ("admin_hr",))
    assert resp.status_code == 403


@pytest.mark.parametrize("qs", [
    "kpi=nope&customer=x&level=S",        # 白名单外指标
    "kpi=otd&customer=&level=",           # 缺分组维度
    "kpi=kit_rate&customer=&level=",      # 齐套率缺型号
    "kpi=otd&customer=x&level=S&window=year",  # 非法口径
])
def test_group_bad_request(client, qs):
    resp = _get(client, f"/api/v2/dashboard/orders/group?{qs}", ("gm",))
    assert resp.status_code == 400, qs


# ============================================================
# 3. 脱敏：orders[].customer 规则作用于弹窗清单
# ============================================================

def test_group_customer_masked_for_warehouse(client):
    """仓库岗位直调接口也拿不到客户真名（与风险订单清单同一套 keep_roles）。"""
    # 用 gm 拿一份未脱敏 L2 取真实分组实体，再以 warehouse 身份调弹窗接口
    resp = _get(client, "/api/v2/dashboard/drill?screen=orders&kpi=otd",
                ("gm",))
    assert resp.status_code == 200
    row = next(r for r in resp.json()["data"]["rows"] if r.get("entity"))
    ent = row["entity"]
    qs = (f"kpi=otd&customer={ent.get('customer', '')}"
          f"&level={ent.get('level', '')}")
    body = _get(client, f"/api/v2/dashboard/orders/group?{qs}", ("warehouse",))
    assert body.status_code == 200
    payload = body.json()
    assert payload["scope"]["masked"], "弹窗载荷必须报出已脱敏字段"
    orders = payload["data"]["orders"]
    assert orders, "样本数据该分组应有订单"
    assert all(o["customer"].startswith("客户 ") for o in orders), orders
    assert {o["customer_level"] for o in orders} == {"—"}


def test_group_customer_visible_for_sales(client):
    resp = _get(client, "/api/v2/dashboard/drill?screen=orders&kpi=otd", ("gm",))
    row = next(r for r in resp.json()["data"]["rows"] if r.get("entity"))
    ent = row["entity"]
    qs = (f"kpi=otd&customer={ent.get('customer', '')}"
          f"&level={ent.get('level', '')}")
    body = _get(client, f"/api/v2/dashboard/orders/group?{qs}", ("sales",))
    assert body.status_code == 200
    payload = body.json()
    assert payload["scope"]["masked"] == []
    assert all(o["customer"] and not o["customer"].startswith("客户 ")
               for o in payload["data"]["orders"])


# ============================================================
# 4. 前端契约（源码扫描：行绑定了弹窗、弹窗带 3 级入口）
# ============================================================

def test_l2_orders_rows_open_group_modal():
    """orders 屏 L2 行必须带 data-order-group 且不再直接 data-drill-entity。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "src" / "mfg_agents"
           / "v2" / "demo" / "ui" / "screens.js").read_text(encoding="utf-8")
    block = src[src.index("function mfgDrillL2Body"):]
    block = block[:block.index("function mfgDrillL3Body")]
    assert "data-order-group" in block
    assert "d.screen === \"orders\"" in block
    # L2 通用绑定必须排除弹窗行（否则点行同时触发展开 2 级下钻）
    bind = src[src.index("function mfgBind"):]
    bind = bind[:bind.index("async function loadMfgDrill")]
    assert ':not([data-order-group])' in bind
    assert "openOrderGroupModal" in bind
