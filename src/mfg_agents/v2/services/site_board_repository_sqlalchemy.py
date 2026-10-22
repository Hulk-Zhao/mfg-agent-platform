"""`SiteBoardRepository` 的 SQLAlchemy 实现（治理库切 MySQL 的第三块）。

- 建表按方言分派：SQLite 复用 ``SQLiteSiteBoardRepository``（含老库补列），
  MySQL 执行 ``MYSQL_SITE_BOARD_DDL``（索引内联）。
- 推送 upsert 按方言生成：SQLite ``ON CONFLICT(push_id)`` / MySQL
  ``ON DUPLICATE KEY UPDATE``，冲突时都只刷新内容列，不覆盖现场进度。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from sqlalchemy import create_engine, text

from ...common.platform_schema import sqlite_path_from_url
from .site_board_repository import (
    MYSQL_SITE_BOARD_DDL,
    SITE_BOARD_COLUMNS,
    SITE_BOARD_CONTENT_COLUMNS,
    SITE_BOARD_TABLE,
    SiteBoardRepository,
    SQLiteSiteBoardRepository,
)

__all__ = ["SQLAlchemySiteBoardRepository"]


class SQLAlchemySiteBoardRepository(SiteBoardRepository):
    """跨方言现场看板仓储（SQLite / MySQL）。"""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            SQLiteSiteBoardRepository(sqlite_path_from_url(self.db_url)).ensure_schema()
            return
        if dialect != "mysql":
            raise NotImplementedError(
                f"现场看板仓储暂只支持 SQLite / MySQL，收到方言 {dialect!r}")
        with self.engine.begin() as conn:
            conn.execute(text(MYSQL_SITE_BOARD_DDL))

    def fetch(self, push_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {SITE_BOARD_TABLE} WHERE push_id = :pid"),
                {"pid": push_id}).mappings().first()
        return dict(row) if row is not None else None

    def upsert_push(self, record: Mapping[str, Any]) -> None:
        columns = ", ".join(SITE_BOARD_COLUMNS)
        values = ", ".join(f":{c}" for c in SITE_BOARD_COLUMNS)
        params = {c: record.get(c) for c in SITE_BOARD_COLUMNS}
        if self.engine.dialect.name == "mysql":
            updates = ", ".join(f"{c}=VALUES({c})"
                                for c in SITE_BOARD_CONTENT_COLUMNS)
            suffix = f"ON DUPLICATE KEY UPDATE {updates}"
        else:
            updates = ", ".join(f"{c}=excluded.{c}"
                                for c in SITE_BOARD_CONTENT_COLUMNS)
            suffix = f"ON CONFLICT(push_id) DO UPDATE SET {updates}"
        with self.engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO {SITE_BOARD_TABLE} ({columns}) "
                     f"VALUES ({values}) {suffix}"), params)

    def update_push(self, push_id: str, record: Mapping[str, Any]) -> int:
        assignments = ", ".join(f"{c} = :{c}" for c in SITE_BOARD_COLUMNS
                                if c != "push_id")
        params = {c: record.get(c) for c in SITE_BOARD_COLUMNS
                  if c != "push_id"}
        params["pid"] = push_id
        with self.engine.begin() as conn:
            result = conn.execute(
                text(f"UPDATE {SITE_BOARD_TABLE} SET {assignments} "
                     f"WHERE push_id = :pid"), params)
            return int(result.rowcount or 0)

    def list_rows(self, *, stage: Optional[str] = None,
                  status: Optional[str] = None,
                  limit: int = 50) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if stage:
            conditions.append("stage = :stage")
            params["stage"] = stage
        if status:
            conditions.append("status = :status")
            params["status"] = status
        clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params["lim"] = max(1, int(limit))
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT * FROM {SITE_BOARD_TABLE}{clause} "
                     f"ORDER BY created_at DESC, id DESC LIMIT :lim"),
                params).mappings().all()
        return [dict(r) for r in rows]
