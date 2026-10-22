"""A2A 任务结果持久化仓储契约。"""
from __future__ import annotations

import time

from mfg_agents.v2.services.a2a_task_store import (
    A2ATaskRepository,
    SQLiteA2ATaskRepository,
    resolve_a2a_repo,
)


def _task(task_id: str, *, ts: float | None = None) -> dict:
    return {
        "id": task_id,
        "contextId": "run-1",
        "status": {"state": "completed"},
        "artifacts": [{"artifactId": "art-1", "name": "scenario-result",
                       "parts": [{"kind": "text", "text": "ok"}]}],
        "ts": float(ts if ts is not None else time.time()),
    }


def test_sqlite_repo_roundtrip_and_upsert(tmp_path):
    repo = SQLiteA2ATaskRepository(str(tmp_path / "gov.db"))
    assert isinstance(repo, A2ATaskRepository)

    repo.save(_task("a2a-1"))
    got = repo.get("a2a-1")
    assert got["id"] == "a2a-1" and got["contextId"] == "run-1"
    assert got["status"]["state"] == "completed"
    assert got["artifacts"][0]["artifactId"] == "art-1"
    assert repo.get("a2a-missing") is None

    # 同 id 覆盖（重放/重试不产生第二行）
    repo.save({**_task("a2a-1"), "contextId": "run-2"})
    assert repo.get("a2a-1")["contextId"] == "run-2"


def test_sqlite_repo_purge_expired(tmp_path):
    repo = SQLiteA2ATaskRepository(str(tmp_path / "gov.db"))
    now = time.time()
    repo.save(_task("a2a-old", ts=now - 7200))
    repo.save(_task("a2a-new", ts=now))

    assert repo.purge(3600, now=now) == 1
    assert repo.get("a2a-old") is None
    assert repo.get("a2a-new") is not None


def test_resolve_repo_follows_sqlite_workflow_db(tmp_path, monkeypatch):
    from mfg_agents.common.config import settings

    monkeypatch.setattr(settings, "WORKFLOW_DB",
                        f"sqlite:///{tmp_path / 'gov.db'}")
    assert isinstance(resolve_a2a_repo(), SQLiteA2ATaskRepository)
