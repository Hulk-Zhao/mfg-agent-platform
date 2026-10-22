"""R11 退供评审端点契约（/api/v2/aging/return-request）+ 前端接线守卫。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mfg_agents.v2.demo.api as api
from mfg_agents.v2.demo.api import app
from mfg_agents.v2.services.workflow import WorkflowEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def temp_engine(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "wf.db").replace("\\", "/")
    engine = WorkflowEngine(url)
    monkeypatch.setattr(api, "_workflow_engine", lambda: engine)
    return engine


def _hdr(client: TestClient, *roles: str) -> dict:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def test_requires_login(client):
    resp = client.post("/api/v2/aging/return-request",
                       json={"part_nos": ["PACK-BOX-A"]})
    assert resp.status_code == 401


def test_rejects_role_without_inventory_scope(client):
    resp = client.post("/api/v2/aging/return-request",
                       headers=_hdr(client, "admin_hr"),
                       json={"part_nos": ["PACK-BOX-A"]})
    assert resp.status_code == 403
    assert "退供评审" in resp.json()["detail"]["message"]


def test_rejects_empty_part_list(client):
    resp = client.post("/api/v2/aging/return-request",
                       headers=_hdr(client, "warehouse"),
                       json={"part_nos": []})
    assert resp.status_code == 400


def test_rejects_non_aging_parts(client, temp_engine):
    resp = client.post("/api/v2/aging/return-request",
                       headers=_hdr(client, "warehouse"),
                       json={"part_nos": ["NO-SUCH-PART"], "days": 180})
    assert resp.status_code == 404
    assert "未达到库龄" in resp.json()["detail"]


def test_creates_waiting_hitl_disposition_task(client, temp_engine):
    resp = client.post("/api/v2/aging/return-request",
                       headers=_hdr(client, "warehouse"),
                       json={"part_nos": ["PACK-BOX-A"], "days": 180,
                             "note": "R11 测试"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["task_uid"].startswith("wf-")
    assert data["status"] == "waiting_hitl"
    assert data["slot"] == "disposition"
    assert data["interim"] is True
    assert data["count"] == 1
    assert data["total_value"] > 0
    assert "人工确认" in data["note"] or "不会自动放行" in data["note"]

    task = temp_engine.get(data["task_uid"])
    assert task.is_governed()
    assert task.slot == "disposition"
    assert task.context.get("origin") == "aging_return"
    assert task.context.get("actions")
    # 未自动选定动作 / 未自动放行
    assert task.status == "waiting_hitl"
    assert not (task.governance or {}).get("action_key")


def test_front_end_wiring():
    js = (UI / "screens.js").read_text(encoding="utf-8")
    assert "function agingCardHtml(ctx, result, opts)" in js
    assert 'result.route.strategy === "aging"' in js
    assert 'kind: "aging"' in js
    assert "data-ag-return" in js
    assert "agingReturnRequest" in js

    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert 'agingReturnRequest(partNos, days, note)' in app_js
    assert '"/api/v2/aging/return-request"' in app_js

    css = (UI / "app.css").read_text(encoding="utf-8")
    start = css.index("/* R11 呆滞料盘点卡")
    block = css[start:start + 1800]
    assert ".ag-summary" in block and ".ag-gate-note" in block
