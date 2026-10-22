"""岗位 AI 同事端点（D1/R2：GET /api/v2/colleagues）契约测试。

钉三件事：
1. 信封与快照形状（colleagues/count/source）；
2. 31 岗位全覆盖且 id ⊆ 前端 ROLES（与 test_colleague_yaml 同口径，
   端点层再兜一次——防线不怕重复）；
3. 每条配置形状完备（前端切换器/推荐问题直接消费，缺字段即烂尾）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mfg_agents.v2.demo.api import app

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _frontend_role_ids() -> set[str]:
    src = (REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
           / "demo-data.js").read_text(encoding="utf-8")
    m = re.search(r"const ROLES = \[(.*?)\n  \];", src, re.S)
    assert m, "demo-data.js ROLES 字面量未找到（正则需同步）"
    return set(re.findall(r'\{ id: "(\w+)"', m.group(1)))


def test_colleagues_envelope_and_count(client):
    resp = client.get("/api/v2/colleagues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    assert data["source"] == "colleague.yaml"
    assert data["count"] == len(data["colleagues"]) == 31


def test_colleague_ids_subset_of_frontend_roles(client):
    ids = {c["role"] for c in client.get("/api/v2/colleagues").json()["data"]["colleagues"]}
    unknown = ids - _frontend_role_ids()
    assert not unknown, f"端点暴露了前端不存在的岗位 id: {unknown}（严禁第三套角色 key）"
    missing = _frontend_role_ids() - ids
    assert not missing, f"端点缺少岗位同事: {missing}"


def test_colleague_entry_shape(client):
    for c in client.get("/api/v2/colleagues").json()["data"]["colleagues"]:
        assert c.get("name") and c.get("greeting"), c.get("role")
        assert len(c.get("scenarios") or []) >= 2, c.get("role")
        assert len(c.get("questions") or []) >= 2, c.get("role")
        assert len(c.get("tools") or []) >= 1, c.get("role")
