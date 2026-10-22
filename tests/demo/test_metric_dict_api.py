"""R8 口径词典端点契约（/api/v2/metric-dict）+ 前端接线守卫。

守三件事：
1. 需要登录（fail-closed），但**不按岗位裁剪**（口径本身不是业务数据）；
2. 三源覆盖 + 来源过滤 + 关键词搜索 + 单条查询/未找到明示；
3. 帮助中心词典屏、KPI 卡「口径」、问数证据「口径」三处入口真实接线。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mfg_agents.v2.demo.api import app

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


@pytest.fixture()
def client():
    return TestClient(app)


def _headers(client: TestClient, role: str = "qa") -> dict:
    resp = client.post("/api/v2/auth/login", json={"roles": [role]})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def test_metric_dict_requires_login(client):
    assert client.get("/api/v2/metric-dict").status_code == 401


def test_metric_dict_three_sources_and_filters(client):
    headers = _headers(client)
    data = client.get("/api/v2/metric-dict", headers=headers).json()["data"]
    assert data["count"] == len(data["entries"]) >= 3
    by = data["coverage"]["by_source"]
    assert set(by) == {"registered_query", "metric_library", "dashboard_kpi"}
    assert data["coverage"]["total"] == data["count"]

    board = client.get("/api/v2/metric-dict?source=dashboard_kpi",
                       headers=headers).json()["data"]
    assert board["count"] == by["dashboard_kpi"]
    assert all(e["source"] == "dashboard_kpi" for e in board["entries"])

    hits = client.get("/api/v2/metric-dict?q=OTD", headers=headers).json()["data"]
    ids = {e["id"] for e in hits["entries"]}
    assert "metric.otd_rate" in ids and "board.orders.otd" in ids
    assert hits["filters"] == {"q": "OTD", "source": ""}


def test_metric_dict_entry_lookup_and_missing(client):
    headers = _headers(client)
    found = client.get("/api/v2/metric-dict?id=query.batch_trace_full",
                       headers=headers).json()["data"]
    assert found["found"] is True
    assert found["entry"]["source"] == "registered_query"
    assert found["entry"]["refs"]

    missing = client.get("/api/v2/metric-dict?id=query.no_such",
                         headers=headers).json()["data"]
    assert missing["found"] is False and missing["entry"] is None
    assert "未找到" in missing["note"]


def test_metric_dict_readable_by_all_roles(client):
    """口径不是业务数据：任意登录岗位都能读全量词典。"""
    for role in ("qa", "manufacturing", "gm", "quality"):
        data = client.get("/api/v2/metric-dict", headers=_headers(client, role)
                          ).json()["data"]
        assert data["count"] >= 3


def test_help_screen_and_kpi_card_wired():
    help_js = (UI / "help-screen.js").read_text(encoding="utf-8")
    assert "/api/v2/metric-dict" in help_js
    assert "function loadMetricDict(ctx)" in help_js
    assert 'id="help-dict-body"' in help_js
    assert 'data-dict-filter="dashboard_kpi"' in help_js
    # api._get 已解信封：必须读 resp.entries，不能再套一层 .data（真机踩过）
    assert "Array.isArray(resp.entries)" in help_js

    screens_js = (UI / "screens.js").read_text(encoding="utf-8")
    assert "function openDictEntry(entryId)" in screens_js
    assert "const entry = resp && resp.entry;" in screens_js
    assert 'data-dict-id="${UI.esc(dictId)}"' in screens_js
    assert "const dictId = `board.${screen}.${kpi.key}`" in screens_js
    assert 'const dictId = p.query_name ? `query.${p.query_name}`' in screens_js
    # KPI 卡主体是下钻按钮：口径入口必须在捕获阶段先 stopPropagation
    assert 'closest("[data-dict-id]")' in screens_js
    assert "}, true);" in screens_js

    css = (UI / "app.css").read_text(encoding="utf-8")
    assert ".help-dict-row" in css and ".stat-def-btn" in css
