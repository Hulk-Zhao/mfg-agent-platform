"""A2A 任务结果持久化仓储（生产化清单：tasks/get 不再只活 1 小时）。

契约与既有仓储同形：行级 dict 进 / dict 出；SQLite 原生实现，MySQL 走
SQLAlchemy；表结构由 ``common.platform_schema`` 统一建（``a2a_task``）。

TTL 语义不变：仍然只保留最近 1 小时的任务结果，只是从"进程内 dict"变成
"库内行 + 过期清理"——重启后 ``tasks/get`` 仍可查到未过期任务。
"""
from __future__ import annotations

import json
import sqlite3
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, text

from ...common.config import settings
from ...common.platform_schema import (
    ensure_platform_schema,
    ensure_platform_schema_sqlite,
    is_sqlite_target,
    sqlite_path_from_url,
)

__all__ = [
    "A2A_TASK_TABLE",
    "A2ATaskRepository",
    "SQLiteA2ATaskRepository",
    "SQLAlchemyA2ATaskRepository",
    "resolve_a2a_repo",
]

A2A_TASK_TABLE = "a2a_task"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_record(task: dict[str, Any]) -> dict[str, Any]:
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    return {
        "task_id": str(task.get("id") or ""),
        "context_id": str(task.get("contextId") or ""),
        "state": str(status.get("state") or "completed"),
        "artifacts_json": json.dumps(task.get("artifacts") or [], ensure_ascii=False),
        "created_at": _now_iso(),
        "ts": float(task.get("ts") or time.time()),
    }


def _from_record(row: dict[str, Any]) -> dict[str, Any]:
    try:
        artifacts = json.loads(row.get("artifacts_json") or "[]")
    except (TypeError, ValueError):
        artifacts = []
    return {
        "id": str(row.get("task_id") or ""),
        "contextId": str(row.get("context_id") or ""),
        "status": {"state": str(row.get("state") or "completed")},
        "artifacts": artifacts,
        "ts": float(row.get("ts") or 0),
    }


class A2ATaskRepository(ABC):
    """A2A 任务仓储契约。"""

    @abstractmethod
    def ensure_schema(self) -> None: ...

    @abstractmethod
    def save(self, task: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, task_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def purge(self, ttl_s: float, now: Optional[float] = None) -> int: ...


class SQLiteA2ATaskRepository(A2ATaskRepository):
    """SQLite 实现（治理库；``ensure_platform_schema_sqlite`` 建表）。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        ensure_platform_schema_sqlite(self.db_path)

    def save(self, task: dict[str, Any]) -> None:
        record = _to_record(task)
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO {A2A_TASK_TABLE} "
                    "(task_id, context_id, state, artifacts_json, created_at, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(task_id) DO UPDATE SET "
                    "context_id=excluded.context_id, state=excluded.state, "
                    "artifacts_json=excluded.artifacts_json, ts=excluded.ts",
                    (record["task_id"], record["context_id"], record["state"],
                     record["artifacts_json"], record["created_at"], record["ts"]))
        finally:
            conn.close()

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {A2A_TASK_TABLE} WHERE task_id = ?",
                (task_id,)).fetchone()
        finally:
            conn.close()
        return _from_record(dict(row)) if row else None

    def purge(self, ttl_s: float, now: Optional[float] = None) -> int:
        cutoff = float(now if now is not None else time.time()) - float(ttl_s)
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    f"DELETE FROM {A2A_TASK_TABLE} WHERE ts < ?", (cutoff,))
                return int(cur.rowcount or 0)
        finally:
            conn.close()


class SQLAlchemyA2ATaskRepository(A2ATaskRepository):
    """MySQL / 其他 SQLAlchemy 方言实现。"""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        ensure_platform_schema(self.db_url)

    def save(self, task: dict[str, Any]) -> None:
        record = _to_record(task)
        if self.engine.dialect.name == "mysql":
            suffix = ("ON DUPLICATE KEY UPDATE context_id=VALUES(context_id), "
                      "state=VALUES(state), artifacts_json=VALUES(artifacts_json), "
                      "ts=VALUES(ts)")
        else:
            suffix = ("ON CONFLICT(task_id) DO UPDATE SET "
                      "context_id=excluded.context_id, state=excluded.state, "
                      "artifacts_json=excluded.artifacts_json, ts=excluded.ts")
        with self.engine.begin() as conn:
            conn.execute(text(
                f"INSERT INTO {A2A_TASK_TABLE} "
                "(task_id, context_id, state, artifacts_json, created_at, ts) "
                "VALUES (:task_id, :context_id, :state, :artifacts_json, "
                ":created_at, :ts) " + suffix), record)

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {A2A_TASK_TABLE} WHERE task_id = :tid"),
                {"tid": task_id}).mappings().first()
        return _from_record(dict(row)) if row else None

    def purge(self, ttl_s: float, now: Optional[float] = None) -> int:
        cutoff = float(now if now is not None else time.time()) - float(ttl_s)
        with self.engine.begin() as conn:
            result = conn.execute(
                text(f"DELETE FROM {A2A_TASK_TABLE} WHERE ts < :cutoff"),
                {"cutoff": cutoff})
            return int(result.rowcount or 0)


def resolve_a2a_repo() -> A2ATaskRepository:
    """按治理库地址解析 A2A 任务仓储（SQLite / MySQL）。"""
    target = str(getattr(settings, "WORKFLOW_DB", "") or "")
    if is_sqlite_target(target):
        return SQLiteA2ATaskRepository(sqlite_path_from_url(target))
    return SQLAlchemyA2ATaskRepository(target)
