"""超时代签 HTTP 契约：delegation 裁决透出 + 代签终批 + 越权拒绝。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mfg_agents.common.config import settings
from mfg_agents.v2.contracts.permissions import reset_permission_registry
from mfg_agents.v2.services.auth import issue_token
from mfg_agents.v2.services.workflow import WorkflowEngine

SQE_CTX = {"user_id": "u-sqe", "dept": "quality", "roles": ["sqe"]}
ACTIONS = [{"key": "probe", "label": "探测动作",
            "propose": ["SQE"], "cosign": []}]


def _auth(*roles: str) -> dict[str, str]:
    token, _ = issue_token(user_id="u-test", name="测试用户", dept="", roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def engine(tmp_path) -> WorkflowEngine:
    reset_permission_registry()
    return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")


@pytest.fixture()
def client(engine, monkeypatch) -> TestClient:
    from mfg_agents.v2.demo import api as api_module

    monkeypatch.setattr(api_module, "_workflow_engine", lambda: engine)
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    return TestClient(api_module.app)


def _overdue_task(engine: WorkflowEngine, *, sla_hours: float = -1.0) -> str:
    task = engine.create_task(
        "hitl_disposition", "超时代签接口测试",
        context={"slot": "disposition", "actions": ACTIONS},
        assignees=["SQE"], hitl=True, sla_hours=sla_hours)
    engine.select_action(task.task_uid, "probe", "王倩", user_ctx=SQE_CTX)
    return task.task_uid


def test_task_detail_exposes_delegation_state(client, engine):
    uid = _overdue_task(engine)
    resp = client.get(f"/api/v2/workflow/{uid}", headers=_auth("quality_director"))
    assert resp.status_code == 200, resp.text
    delegation = resp.json()["data"]["delegation"]
    assert delegation["enabled"] is True
    assert delegation["overdue"] is True
    assert delegation["allowed"] is True
    assert delegation["assignees"] == ["SQE"]


def test_task_detail_delegation_denied_without_authority(client, engine):
    uid = _overdue_task(engine)
    resp = client.get(f"/api/v2/workflow/{uid}", headers=_auth("qa_manager"))
    assert resp.status_code == 200, resp.text
    delegation = resp.json()["data"]["delegation"]
    assert delegation["allowed"] is False
    assert "授权名单" in delegation["reason"]


def test_approve_on_behalf_records_signoff(client, engine):
    uid = _overdue_task(engine)
    resp = client.post(
        f"/api/v2/workflow/{uid}/approve",
        json={"approver": "赵总", "comment": "SQE 超时未处理，代签放行",
              "on_behalf_of": "SQE 王倩"},
        headers=_auth("quality_director"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "running"

    signoffs = client.get(f"/api/v2/workflow/{uid}/signoffs",
                          headers=_auth("quality_director")).json()["data"]
    assert signoffs["rows"][0]["on_behalf_of"] == "SQE 王倩"
    assert signoffs["rows"][0]["overdue_signoff"] in (1, True)


def test_approve_on_behalf_denied_for_unlisted_role(client, engine):
    uid = _overdue_task(engine)
    resp = client.post(
        f"/api/v2/workflow/{uid}/approve",
        json={"approver": "钱经理", "comment": "代签", "on_behalf_of": "SQE 王倩"},
        headers=_auth("qa_manager"))
    assert resp.status_code == 403, resp.text


def test_approve_on_behalf_requires_reason(client, engine):
    uid = _overdue_task(engine)
    resp = client.post(
        f"/api/v2/workflow/{uid}/approve",
        json={"approver": "赵总", "comment": "", "on_behalf_of": "SQE 王倩"},
        headers=_auth("quality_director"))
    assert resp.status_code == 400, resp.text


def test_delegation_disabled_reports_reason(client, engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "")
    uid = _overdue_task(engine)
    delegation = client.get(
        f"/api/v2/workflow/{uid}",
        headers=_auth("quality_director")).json()["data"]["delegation"]
    assert delegation["enabled"] is False and delegation["allowed"] is False
    assert "HITL_DELEGATE_ROLES" in delegation["reason"]
