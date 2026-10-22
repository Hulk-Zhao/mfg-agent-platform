"""签名账本只读接口（GET /api/v2/workflow/{task_id}/signoffs）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

import mfg_agents.v2 as v2_pkg
from mfg_agents.v2.contracts.permissions import reset_permission_registry
from mfg_agents.v2.services.auth import issue_token
from mfg_agents.v2.services.workflow import WorkflowEngine

SCENARIO_DIR = Path(v2_pkg.__file__).resolve().parent / "scenarios"

SQE_CTX: dict[str, Any] = {"user_id": "u-sqe", "dept": "quality", "roles": ["quality"]}
PQE_CTX: dict[str, Any] = {"user_id": "u-pqe", "dept": "quality", "roles": ["pqe"]}


def _actions() -> list[dict[str, Any]]:
    data = yaml.safe_load(
        (SCENARIO_DIR / "incoming_quality_v2.yaml").read_text(encoding="utf-8"))
    step = next(s for s in data["steps"] if s.get("id") == "hitl_disposition")
    return step["actions"]


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
    return TestClient(api_module.app)


def _signed_task(engine: WorkflowEngine) -> str:
    task = engine.create_task(
        wf_id="hitl_disposition", title="签名账本接口测试",
        context={"run_id": "run-sign-api", "slot": "disposition",
                 "kind": "disposition", "actions": _actions()},
        assignees=["SQE", "PQE"], hitl=True)
    uid = task.task_uid
    engine.select_action(uid, "concession", "王倩", user_ctx=SQE_CTX,
                         comment="加严检验 + 客户告知")
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    return uid


def test_signoffs_require_token(client):
    resp = client.get("/api/v2/workflow/any-task/signoffs")
    assert resp.status_code == 401


def test_signoffs_unknown_task_is_404(client):
    resp = client.get("/api/v2/workflow/wf-nope/signoffs", headers=_auth("quality"))
    assert resp.status_code == 404


def test_signoffs_returns_ledger_rows(client, engine):
    uid = _signed_task(engine)
    resp = client.get(f"/api/v2/workflow/{uid}/signoffs",
                      headers=_auth("quality"))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["task_uid"] == uid and data["source"] == "wf_signoff"
    assert data["count"] == 1
    assert data["rows"][0]["verb"] == "cosign"
    assert data["rows"][0]["role_key"] == "pqe"
    assert data["rows"][0]["actor_id"] == "u-pqe"
    assert "governance_json" in data["note"]


def test_signoffs_invisible_task_is_404(client, engine):
    uid = _signed_task(engine)
    # 仓储岗位不在来料处置的 propose/cosign/approve 范围 → 不可见
    resp = client.get(f"/api/v2/workflow/{uid}/signoffs",
                      headers=_auth("warehouse"))
    assert resp.status_code == 404
