"""`WorkflowRepository` 的 SQLAlchemy 参考实现（D15 MySQL 落地的第一块）。

定位：

- 与 ``SQLiteWorkflowRepository`` 同一契约（行级 dict 进 / dict 出），
  引擎通过显式注入使用本实现（``WorkflowEngine(url, repository=...)``）；
- SQL 只用命名参数（``:task_uid``），由 SQLAlchemy 按方言编译；本文件不出现
  ``?`` / ``INSERT OR REPLACE`` / ``AUTOINCREMENT`` 等 SQLite 方言；
- 建表走 ``ensure_platform_schema`` 的方言分支（SQLite 原生 / MySQL InnoDB DDL）。

为什么引擎不自动切换到本实现：``WorkflowEngine(url)`` 对非 SQLite 地址
**必须继续 fail-fast**（防止把 MySQL URL 当文件路径静默建库），所以 MySQL
只允许“显式注入仓储”这一条路径。这是安全边界，不是遗漏。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from ...common.platform_schema import ensure_platform_schema
from .workflow_repository import (
    SIGNOFF_COLUMNS,
    SIGNOFF_TABLE,
    TASK_COLUMNS,
    TASK_TABLE,
    TASK_UPDATABLE,
    WorkflowRepository,
)

__all__ = ["SQLAlchemyWorkflowRepository"]


class SQLAlchemyWorkflowRepository(WorkflowRepository):
    """跨方言实现：同一份行级契约，SQLite / MySQL 均可跑。"""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        self.ensure_schema()

    # ---------- schema ----------

    def ensure_schema(self) -> None:
        ensure_platform_schema(self.db_url)

    # ---------- 工单 ----------

    def fetch_task(self, task_uid: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {TASK_TABLE} WHERE task_uid = :uid"),
                {"uid": task_uid}).mappings().first()
        return dict(row) if row is not None else None

    def list_tasks(self, *, status: Optional[str] = None,
                   run_id: Optional[str] = None) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {TASK_TABLE}"
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if status:
            conditions.append("status = :status")
            params["status"] = status
        if run_id:
            conditions.append("context_json LIKE :run")
            params["run"] = f'%"{run_id}"%'
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC"
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

    def insert_task(self, row: dict[str, Any]) -> None:
        missing = [c for c in TASK_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"工单插入缺列: {missing}")
        sql = (f"INSERT INTO {TASK_TABLE} ({', '.join(TASK_COLUMNS)}) "
               f"VALUES ({', '.join(':' + c for c in TASK_COLUMNS)})")
        with self.engine.begin() as conn:
            conn.execute(text(sql), {c: row[c] for c in TASK_COLUMNS})

    def update_task(self, task_uid: str, fields: dict[str, Any]) -> int:
        payload = dict(fields)
        illegal = sorted(set(payload) - TASK_UPDATABLE)
        if illegal:
            raise ValueError(f"工单更新非法列: {illegal}")
        if not payload:
            return 0
        assignments = ", ".join(f"{k} = :{k}" for k in payload)
        params = {**payload, "uid": task_uid}
        with self.engine.begin() as conn:
            result = conn.execute(
                text(f"UPDATE {TASK_TABLE} SET {assignments} WHERE task_uid = :uid"),
                params)
            return int(result.rowcount or 0)

    # ---------- 签名事实 ----------

    def insert_signoff(self, row: dict[str, Any]) -> bool:
        missing = [c for c in SIGNOFF_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"签名插入缺列: {missing}")
        sql = (f"INSERT INTO {SIGNOFF_TABLE} ({', '.join(SIGNOFF_COLUMNS)}) "
               f"VALUES ({', '.join(':' + c for c in SIGNOFF_COLUMNS)})")
        try:
            with self.engine.begin() as conn:
                conn.execute(text(sql), {c: row[c] for c in SIGNOFF_COLUMNS})
        except IntegrityError:
            return False  # 唯一索引命中：幂等信号，不是故障
        return True

    def list_signoffs(self, task_uid: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT * FROM {SIGNOFF_TABLE} WHERE task_uid = :uid ORDER BY id"),
                {"uid": task_uid}).mappings().all()
        return [dict(r) for r in rows]

    def delete_signoffs(self, task_uid: str, verb: Optional[str] = None) -> int:
        sql = f"DELETE FROM {SIGNOFF_TABLE} WHERE task_uid = :uid"
        params: dict[str, Any] = {"uid": task_uid}
        if verb:
            sql += " AND verb = :verb"
            params["verb"] = verb
        with self.engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return int(result.rowcount or 0)
