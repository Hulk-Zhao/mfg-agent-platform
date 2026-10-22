"""R6 催交端点契约（/api/v2/followups*）+ 前端接线守卫。

守四件事：
1. **fail-closed**：无令牌 401；岗位不在 orders 范围 403（绝不 200 空数据）。
2. **一键派发幂等**：同工单集重复派发，followup_id 不变、行数不翻倍。
3. **演示回写事实**：演示库路径 → 响应 ``demo_writeback=true`` 且带不可关徽标文案；
   到货确认后仍为 true（状态存演示库，未写回 ERP）。
4. **前端接线**：订单屏催交面板 + 演示回写徽标 + API 方法存在（静态代码点）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mfg_agents.v2.demo.api import app

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # 每个用例独立演示库：不污染仓库 data/demo_storyline/demo_governance.db
    monkeypatch.setenv("MFG_FOLLOWUP_DB", str(tmp_path / "demo_followup.db"))
    return TestClient(app)


def _token(client: TestClient, *roles: str) -> str:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _headers(client: TestClient, *roles: str) -> dict:
    return {"Authorization": f"Bearer {_token(client, *roles)}"}


# ---------------------------------------------------------------- fail-closed


def test_followups_require_token(client):
    assert client.get("/api/v2/followups").status_code == 401
    assert client.post("/api/v2/followups/dispatch",
                       json={"work_order_set": "本周开工批次"}).status_code == 401


def test_followups_reject_out_of_scope_role(client):
    # qa 不在 orders 看板 allow_roles（催交涉及供应商/订单，按订单域裁决）
    resp = client.get("/api/v2/followups", headers=_headers(client, "qa"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["dashboard"] == "orders"


# ---------------------------------------------------------------- 缺口清单


def test_gaps_endpoint_returns_supplier_and_basis(client):
    resp = client.get("/api/v2/followups/gaps?limit=5",
                      headers=_headers(client, "procurement"))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total_gaps"] > 0 and len(data["gaps"]) <= 5
    assert all(g.get("part_no") for g in data["gaps"])
    assert all(g.get("owner") == "采购员" for g in data["gaps"])
    assert "未做 BOM 逐单齐套" in data["basis"]
    assert data["db_scope"] == "demo" and data["demo_writeback"] is True
    assert "未写回 ERP" in data["writeback_label"]


# ---------------------------------------------------------------- 派发 / 状态机


def test_dispatch_is_idempotent_via_api(client):
    headers = _headers(client, "procurement")
    first = client.post("/api/v2/followups/dispatch",
                        json={"work_order_set": "本周开工批次", "limit": 5},
                        headers=headers)
    assert first.status_code == 200, first.text
    rows1 = first.json()["data"]["rows"]
    assert rows1 and all(r["status"] == "dispatched" for r in rows1)

    second = client.post("/api/v2/followups/dispatch",
                         json={"work_order_set": "本周开工批次", "limit": 5},
                         headers=headers)
    rows2 = second.json()["data"]["rows"]
    assert [r["followup_id"] for r in rows2] == [r["followup_id"] for r in rows1]

    listing = client.get("/api/v2/followups", headers=headers).json()["data"]
    assert len(listing["rows"]) == len(rows1)
    assert listing["summary"]["dispatched"] == len(rows1)


def test_transition_chain_and_demo_writeback(client):
    headers = _headers(client, "procurement")
    row = client.post("/api/v2/followups/dispatch",
                      json={"work_order_set": "本周开工批次", "limit": 1},
                      headers=headers).json()["data"]["rows"][0]
    fid = row["followup_id"]
    base = f"/api/v2/followups/{fid}/transition"

    # 跳步：关闭必须 409（状态机不做"尽力而为"）
    bad = client.post(base, json={"action": "close", "note": "跳步"}, headers=headers)
    assert bad.status_code == 409

    # 空回复内容 → 400（不写空留痕；状态未推进）
    blank = client.post(base, json={"action": "reply", "note": " "}, headers=headers)
    assert blank.status_code == 400
    assert client.get("/api/v2/followups", headers=headers
                      ).json()["data"]["rows"][0]["status"] == "dispatched"

    reply = client.post(base, json={"action": "reply", "note": "周五到货 2000"},
                        headers=headers)
    assert reply.status_code == 200, reply.text
    assert reply.json()["data"]["rows"][0]["status"] == "supplier_replied"

    arrival = client.post(base, json={"action": "arrival", "note": "到货 2000",
                                      "arrival_qty": 2000}, headers=headers)
    assert arrival.status_code == 200, arrival.text
    adata = arrival.json()["data"]
    assert adata["rows"][0]["status"] == "arrival_confirmed"
    assert adata["demo_writeback"] is True
    assert "未写回 ERP" in adata["writeback_label"]

    # 关闭需结论（空则 400）
    assert client.post(base, json={"action": "close", "note": ""},
                       headers=headers).status_code == 400
    closed = client.post(base, json={"action": "close", "note": "缺口已补齐"},
                         headers=headers)
    assert closed.status_code == 200
    assert closed.json()["data"]["rows"][0]["status"] == "closed"


def test_unknown_action_rejected(client):
    headers = _headers(client, "procurement")
    row = client.post("/api/v2/followups/dispatch",
                      json={"work_order_set": "本周开工批次", "limit": 1},
                      headers=headers).json()["data"]["rows"][0]
    resp = client.post(f"/api/v2/followups/{row['followup_id']}/transition",
                       json={"action": "teleport"}, headers=headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------- 前端接线


def test_frontend_panel_and_demo_badge_wired():
    screens = (UI / "screens.js").read_text(encoding="utf-8")
    appjs = (UI / "app.js").read_text(encoding="utf-8")
    css = (UI / "app.css").read_text(encoding="utf-8")

    assert "loadFollowupPanel(ctx, box)" in screens
    assert "齐套催交跟踪" in screens and "演示回写" in screens
    assert "data-fu-action" in screens and "fu-dispatch" in screens
    assert "followupDispatch" in appjs and "followupTransition" in appjs
    assert "followups(status, workOrderSet)" in appjs
    assert ".fu-demo" in css and "演示回写" in css

    # CSS 令牌纪律：新增块不得写死 hex 颜色（主题两套必须同时可读）
    block = css[css.index(".fu-card {"):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block[:4000]), \
        "催交面板样式必须用主题变量，不许写死 hex"


# ---------------------------------------------------------------- 遗留：SLA 调度


def test_sla_sweeper_wired_in_lifespan():
    """超 SLA 扫描必须真接进服务生命周期（不是只有函数没人调）。"""
    src = (REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py"
           ).read_text(encoding="utf-8")
    assert "escalate_overdue" in src
    assert "MFG_FOLLOWUP_SLA_SWEEP_S" in src
    assert "followup-sla-sweeper" in src
    assert "sweeper.cancel()" in src


def test_sweep_interval_env_parsing(monkeypatch):
    from mfg_agents.v2.demo import api

    monkeypatch.setenv("MFG_FOLLOWUP_SLA_SWEEP_S", "0")
    assert api._followup_sweep_seconds() == 0
    monkeypatch.setenv("MFG_FOLLOWUP_SLA_SWEEP_S", "30")
    assert api._followup_sweep_seconds() == 30
    monkeypatch.setenv("MFG_FOLLOWUP_SLA_SWEEP_S", "not-a-number")
    assert api._followup_sweep_seconds() == 600   # 配置写错回落默认，不炸服务
