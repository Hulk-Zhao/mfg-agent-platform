"""R13 / D15：WorkflowRepository 契约 + SQLite 实现 + 引擎注入（不再裸 _connect）。"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from mfg_agents.v2.services.workflow import WorkflowEngine
from mfg_agents.v2.services.workflow_repository import (
    TASK_COLUMNS,
    SQLiteWorkflowRepository,
    WorkflowBackendError,
    WorkflowRepository,
    assert_sqlite_backed,
)


def _row(task_uid: str, *, status: str = "pending", run_id: str = "") -> dict[str, Any]:
    return {
        "task_uid": task_uid,
        "wf_id": "wf-test",
        "title": "测试工单",
        "status": status,
        "priority": "P1",
        "assignees": "[]",
        "context_json": json.dumps({"run_id": run_id} if run_id else {}),
        "hitl_prompt": "",
        "history_json": "[]",
        "governance_json": "{}",
        "owner_scope": "{}",
        "created_at": "2026-10-22T00:00:00+00:00",
        "updated_at": "2026-10-22T00:00:00+00:00",
        "sla_due_at": None,
    }


# ---------- 契约：SQLite 实现 ----------


def test_sqlite_repository_roundtrip(tmp_path):
    repo = SQLiteWorkflowRepository(f"sqlite:///{tmp_path / 'gov.db'}")
    assert set(TASK_COLUMNS) >= {"task_uid", "governance_json", "owner_scope"}

    repo.insert_task(_row("wf-1", run_id="run-a"))
    repo.insert_task(_row("wf-2", status="running"))

    fetched = repo.fetch_task("wf-1")
    assert fetched and fetched["title"] == "测试工单"
    assert repo.fetch_task("wf-missing") is None

    assert [r["task_uid"] for r in repo.list_tasks()] == ["wf-2", "wf-1"]
    assert [r["task_uid"] for r in repo.list_tasks(status="pending")] == ["wf-1"]
    assert [r["task_uid"] for r in repo.list_tasks(run_id="run-a")] == ["wf-1"]

    changed = repo.update_task("wf-1", {"status": "running", "governance_json": "{}"})
    assert changed == 1
    assert repo.fetch_task("wf-1")["status"] == "running"
    assert repo.update_task("wf-missing", {"status": "closed"}) == 0


def test_sqlite_repository_rejects_column_drift(tmp_path):
    repo = SQLiteWorkflowRepository(f"sqlite:///{tmp_path / 'gov.db'}")
    with pytest.raises(ValueError):
        repo.insert_task({"task_uid": "wf-1"})  # 缺列
    repo.insert_task(_row("wf-1"))
    with pytest.raises(ValueError):
        repo.update_task("wf-1", {"not_a_column": 1})  # 非法列：只允许契约列
    with pytest.raises(ValueError):
        repo.update_task("wf-1", {"task_uid": "wf-2"})


def test_memory_repository_shares_single_connection():
    repo = SQLiteWorkflowRepository("sqlite:///:memory:")
    repo.insert_task(_row("wf-mem"))
    assert repo.fetch_task("wf-mem")["task_uid"] == "wf-mem"
    # :memory: 必须同一连接（否则新连接是空库）
    assert repo.connect() is repo.connect()


def test_non_sqlite_backend_fails_fast():
    with pytest.raises(WorkflowBackendError) as err:
        SQLiteWorkflowRepository("mysql+pymysql://ro:pw@db:3306/mfg")
    message = str(err.value)
    assert "WorkflowRepository" in message and "SQLite" in message
    with pytest.raises(WorkflowBackendError):
        assert_sqlite_backed("postgresql://db/mfg")


# ---------- 引擎接入契约 ----------


class DictRepository(WorkflowRepository):
    """纯内存仓储：证明引擎可以完全脱离 SQLite 运行。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.signoffs: list[dict[str, Any]] = []
        self.schema_calls = 0

    def ensure_schema(self) -> None:
        self.schema_calls += 1

    def fetch_task(self, task_uid: str) -> Optional[dict[str, Any]]:
        row = self.rows.get(task_uid)
        return dict(row) if row else None

    def list_tasks(self, *, status: Optional[str] = None,
                   run_id: Optional[str] = None) -> list[dict[str, Any]]:
        rows = list(self.rows.values())
        if status:
            rows = [r for r in rows if r["status"] == status]
        if run_id:
            rows = [r for r in rows if run_id in str(r.get("context_json") or "")]
        return list(reversed(rows))

    def insert_task(self, row: dict[str, Any]) -> None:
        self.rows[row["task_uid"]] = dict(row)

    def update_task(self, task_uid: str, fields: dict[str, Any]) -> int:
        if task_uid not in self.rows:
            return 0
        self.rows[task_uid].update(fields)
        return 1

    def insert_signoff(self, row: dict[str, Any]) -> bool:
        for existing in self.signoffs:
            if (existing["task_uid"] == row["task_uid"]
                    and existing["verb"] == row["verb"]
                    and (existing["actor_id"] == row["actor_id"]
                         or (existing["role_key"] == row["role_key"]
                             and existing["actor"] == row["actor"]))):
                return False
        self.signoffs.append(dict(row))
        return True

    def list_signoffs(self, task_uid: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.signoffs if r["task_uid"] == task_uid]

    def delete_signoffs(self, task_uid: str, verb: Optional[str] = None) -> int:
        before = len(self.signoffs)
        self.signoffs = [
            r for r in self.signoffs
            if not (r["task_uid"] == task_uid and (not verb or r["verb"] == verb))]
        return before - len(self.signoffs)


def test_engine_runs_on_injected_repository():
    repo = DictRepository()
    # 注入仓储时不得被 SQLite 方言校验挡住——这是 MySQL 后端接入的前置条件
    engine = WorkflowEngine("mysql+pymysql://ro:pw@db:3306/mfg", repository=repo)
    engine.repository.ensure_schema()
    assert repo.schema_calls == 1
    assert repo.rows == {}, "注入仓储时引擎不得偷偷写 SQLite"

    task = engine.create_task("wf-x", "注入仓储工单", context={"run_id": "run-x"})
    assert task.task_uid in repo.rows
    assert engine.get(task.task_uid).title == "注入仓储工单"
    assert [t.task_uid for t in engine.list_tasks(run_id="run-x")] == [task.task_uid]


def test_engine_compat_connect_targets_same_db(tmp_path):
    engine = WorkflowEngine(f"sqlite:///{tmp_path / 'gov.db'}")
    task = engine.create_task("wf-c", "兼容连接")
    with engine._connect() as conn:
        row = conn.execute(
            "SELECT title FROM platform_workflow_task WHERE task_uid = ?",
            (task.task_uid,)).fetchone()
    assert row["title"] == "兼容连接"
