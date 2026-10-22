"""现场看板仓储契约（D15 迁移第 2 项）：推送行级读写，不暴露 SQL 方言。

``site_board`` 原先直接持有 ``_connect`` + SQL；本契约把持久化收敛到
``SiteBoardRepository``，与 ``WorkflowRepository`` / ``ReportRepository`` 同形状：

- 归因 / 状态机 / 脱敏仍留在 ``site_board``（业务语义不下沉）；
- 建表 + 老库补列（``customer``）由 ``ensure_schema`` 负责；
- MySQL 实现只换连接与占位符，行级 dict 契约不变。
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping, Optional

__all__ = [
    "SITE_BOARD_TABLE",
    "SITE_BOARD_COLUMNS",
    "SITE_BOARD_CONTENT_COLUMNS",
    "MYSQL_SITE_BOARD_DDL",
    "SiteBoardRepository",
    "SQLiteSiteBoardRepository",
]

SITE_BOARD_TABLE = "site_board_push"

#: 物理列顺序（INSERT 参数顺序；与 DDL 一致）
SITE_BOARD_COLUMNS: tuple[str, ...] = (
    "push_id", "stage", "level", "title", "body", "status", "source",
    "ref_id", "complaint_no", "batch_no", "customer", "ack_by", "closed_by",
    "feedback", "created_at", "updated_at", "ack_at", "closed_at",
)

#: 重推时只刷新的内容列（不改现场已推进的 status / ack / closed / feedback）
SITE_BOARD_CONTENT_COLUMNS: tuple[str, ...] = (
    "title", "body", "level", "source", "ref_id",
    "complaint_no", "batch_no", "customer", "updated_at",
)

_DDL = """
CREATE TABLE IF NOT EXISTS site_board_push (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_id TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'P1',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pushed',
    source TEXT NOT NULL DEFAULT '',
    ref_id TEXT NOT NULL DEFAULT '',
    complaint_no TEXT NOT NULL DEFAULT '',
    batch_no TEXT NOT NULL DEFAULT '',
    customer TEXT NOT NULL DEFAULT '',
    ack_by TEXT NOT NULL DEFAULT '',
    ack_at TEXT,
    closed_by TEXT NOT NULL DEFAULT '',
    closed_at TEXT,
    feedback TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_site_board_stage_status
    ON site_board_push (stage, status);
CREATE INDEX IF NOT EXISTS idx_site_board_complaint
    ON site_board_push (complaint_no);
"""

#: MySQL 版建表（索引内联，幂等；``customer`` 直接包含在新表里，无需老库补列流程）。
MYSQL_SITE_BOARD_DDL = f"""
CREATE TABLE IF NOT EXISTS {SITE_BOARD_TABLE} (
    id BIGINT NOT NULL AUTO_INCREMENT,
    push_id VARCHAR(80) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    level VARCHAR(16),
    title VARCHAR(512),
    body LONGTEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'pushed',
    source VARCHAR(64),
    ref_id VARCHAR(128),
    complaint_no VARCHAR(128),
    batch_no VARCHAR(128),
    customer VARCHAR(255),
    ack_by VARCHAR(128),
    ack_at VARCHAR(40) NULL,
    closed_by VARCHAR(128),
    closed_at VARCHAR(40) NULL,
    feedback LONGTEXT,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY ux_site_board_push_id (push_id),
    KEY idx_site_board_stage_status (stage, status),
    KEY idx_site_board_complaint (complaint_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

#: 老库升级（``CREATE TABLE IF NOT EXISTS`` 对已存在的表不生效）：PRAGMA 守卫 + 幂等补列。
_ALTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("customer", "ALTER TABLE site_board_push ADD COLUMN customer TEXT DEFAULT ''"),
)


class SiteBoardRepository(ABC):
    """现场看板仓储契约（SQLite 实现先行）。"""

    @abstractmethod
    def ensure_schema(self) -> None:
        """建表 + 索引 + 老库补列（幂等）。"""

    @abstractmethod
    def fetch(self, push_id: str) -> Optional[dict[str, Any]]:
        """按 ``push_id`` 取一行（不存在返回 None）。"""

    @abstractmethod
    def upsert_push(self, record: Mapping[str, Any]) -> None:
        """按 ``push_id`` 幂等落推送行；冲突时只刷新内容列。"""

    @abstractmethod
    def update_push(self, push_id: str, record: Mapping[str, Any]) -> int:
        """整行更新（现场回填字段），返回受影响行数。"""

    @abstractmethod
    def list_rows(self, *, stage: Optional[str] = None,
                  status: Optional[str] = None,
                  limit: int = 50) -> list[dict[str, Any]]:
        """按工段 / 状态过滤，``ORDER BY created_at DESC, id DESC``。"""


class SQLiteSiteBoardRepository(SiteBoardRepository):
    """SQLite 实现：``db_path`` 由调用方解析并过 ``assert_sqlite_backed``。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.executescript(_DDL)
                existing = {r[1] for r in conn.execute(
                    f"PRAGMA table_info({SITE_BOARD_TABLE})")}
                for column, ddl in _ALTER_COLUMNS:
                    if existing and column not in existing:
                        conn.execute(ddl)
        finally:
            conn.close()

    def fetch(self, push_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {SITE_BOARD_TABLE} WHERE push_id = ?",
                (push_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def upsert_push(self, record: Mapping[str, Any]) -> None:
        columns = ", ".join(SITE_BOARD_COLUMNS)
        placeholders = ", ".join("?" for _ in SITE_BOARD_COLUMNS)
        updates = ", ".join(f"{name}=excluded.{name}"
                            for name in SITE_BOARD_CONTENT_COLUMNS)
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO {SITE_BOARD_TABLE} ({columns}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT(push_id) DO UPDATE SET {updates}",
                    [record.get(name) for name in SITE_BOARD_COLUMNS])
        finally:
            conn.close()

    def update_push(self, push_id: str, record: Mapping[str, Any]) -> int:
        assignments = ", ".join(f"{name}=?" for name in SITE_BOARD_COLUMNS
                                if name != "push_id")
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    f"UPDATE {SITE_BOARD_TABLE} SET {assignments} WHERE push_id = ?",
                    [record.get(name) for name in SITE_BOARD_COLUMNS
                     if name != "push_id"] + [push_id])
                return int(cur.rowcount or 0)
        finally:
            conn.close()

    def list_rows(self, *, stage: Optional[str] = None,
                  status: Optional[str] = None,
                  limit: int = 50) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {SITE_BOARD_TABLE} WHERE 1 = 1"
        params: list[Any] = []
        if stage:
            sql += " AND stage = ?"
            params.append(stage)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
