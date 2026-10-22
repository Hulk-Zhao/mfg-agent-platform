"""SQLAlchemyWorkflowRepository：同一契约在 SQLite 上的方言无关性验证。"""
from __future__ import annotations

import json
from typing import Any

import pytest

from mfg_agents.v2.services.workflow import WorkflowEngine, WorkflowBackendError
from mfg_agents.v2.services.workflow_repository_sqlalchemy import (
    SQLAlchemyWorkflowRepository,
)


def _row(task_uid: str, *, status: str = "pending") -> dict[str, Any]:
    return {
        "task_uid": task_uid,
        "wf_id": "wf-test",
        "title": "SQLAlchemy 工单",
        "status": status,
        "priority": "P1",
        "assignees": "[]",
        "context_json": json.dumps({"run_id": "run-sa"}),
        "hitl_prompt": "",
        "history_json": "[]",
        "governance_json": "{}",
        "owner_scope": "{}",
        "created_at": "2026-10-23T00:00:00+00:00",
        "updated_at": "2026-10-23T00:00:00+00:00",
        "sla_due_at": None,
    }


def _signoff(task_uid: str) -> dict[str, Any]:
    return {
        "task_uid": task_uid, "role_key": "pqe", "role_name": "PQE",
        "actor": "王倩", "actor_id": "u-pqe", "actor_kind": "human",
        "verb": "cosign", "decision": "同意", "comment": "",
        "on_behalf_of": None, "overdue_signoff": 0,
        "ts": "2026-10-23T00:00:00+00:00",
    }


def test_sqlalchemy_repository_roundtrip(tmp_path):
    repo = SQLAlchemyWorkflowRepository(f"sqlite:///{tmp_path / 'gov.db'}")
    repo.insert_task(_row("sa-1"))

    assert repo.fetch_task("sa-1")["title"] == "SQLAlchemy 工单"
    assert repo.fetch_task("sa-missing") is None
    assert [r["task_uid"] for r in repo.list_tasks(run_id="run-sa")] == ["sa-1"]
    assert repo.update_task("sa-1", {"status": "running"}) == 1
    assert repo.update_task("sa-missing", {"status": "closed"}) == 0


def test_sqlalchemy_signoff_idempotency(tmp_path):
    repo = SQLAlchemyWorkflowRepository(f"sqlite:///{tmp_path / 'gov.db'}")
    repo.insert_task(_row("sa-2"))

    assert repo.insert_signoff(_signoff("sa-2")) is True
    assert repo.insert_signoff(_signoff("sa-2")) is False
    assert len(repo.list_signoffs("sa-2")) == 1
    assert repo.delete_signoffs("sa-2", verb="cosign") == 1
    assert repo.list_signoffs("sa-2") == []


def test_engine_accepts_injected_sqlalchemy_repository(tmp_path):
    url = f"sqlite:///{tmp_path / 'gov.db'}"
    repo = SQLAlchemyWorkflowRepository(url)
    engine = WorkflowEngine(url, repository=repo)

    task = engine.create_task("wf-sa", "注入 SQLAlchemy 仓储")
    assert engine.get(task.task_uid).title == "注入 SQLAlchemy 仓储"
    assert [t.task_uid for t in engine.list_tasks()] == [task.task_uid]


def test_non_sqlite_without_repository_still_fails_fast():
    """安全边界不变：MySQL 只允许显式注入仓储，裸 URL 仍拒绝。"""
    with pytest.raises(WorkflowBackendError):
        WorkflowEngine("mysql+pymysql://ro:pw@db:3306/mfg")
