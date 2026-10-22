"""报告仓储契约（D15 迁移第 1 项）：行级读写，不暴露 SQL 方言。

`report_store` 原先直接持有 ``_connect`` + SQL；本契约把这部分收敛到
``ReportRepository``，与 ``WorkflowRepository`` 同一形状：

- 行级 dict 进 / dict 出，MySQL 实现只换连接与占位符；
- ``ensure_schema`` 负责建表（幂等）；
- 业务语义（状态机 / JSON 还原 / 归一化）仍留在 ``report_store``，
  仓储只管持久化，不掺业务判断。
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping, Optional

__all__ = [
    "REPORTS_TABLE",
    "REPORT_COLUMNS",
    "MYSQL_REPORTS_DDL",
    "ReportRepository",
    "SQLiteReportRepository",
]

REPORTS_TABLE = "report_record"

#: 物理列顺序（INSERT 参数顺序；与 SQLite DDL 一致）
REPORT_COLUMNS: tuple[str, ...] = (
    "report_id", "scenario_key", "run_id", "source_module", "title",
    "visibility", "status", "risk_level", "degraded", "actor",
    "internal_brief_json", "customer_report", "mask_map_json",
    "provenance_json", "data_gaps_json", "linked_json", "governing_slot",
    "versions_json", "created_at", "updated_at",
)

_DDL = f"""
CREATE TABLE IF NOT EXISTS {REPORTS_TABLE} (
    report_id TEXT PRIMARY KEY,
    scenario_key TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    source_module TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'internal',
    status TEXT NOT NULL DEFAULT 'draft',
    risk_level TEXT NOT NULL DEFAULT '',
    degraded INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL DEFAULT '',
    internal_brief_json TEXT NOT NULL DEFAULT '{{}}',
    customer_report TEXT NOT NULL DEFAULT '',
    mask_map_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL DEFAULT '[]',
    data_gaps_json TEXT NOT NULL DEFAULT '[]',
    linked_json TEXT NOT NULL DEFAULT '{{}}',
    governing_slot TEXT NOT NULL DEFAULT '',
    versions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_created ON {REPORTS_TABLE} (created_at);
CREATE INDEX IF NOT EXISTS idx_report_status ON {REPORTS_TABLE} (status);
CREATE INDEX IF NOT EXISTS idx_report_scenario ON {REPORTS_TABLE} (scenario_key);
CREATE INDEX IF NOT EXISTS idx_report_run ON {REPORTS_TABLE} (run_id);
"""

#: MySQL 版建表（SQLite 的 TEXT 默认值与 ``CREATE INDEX IF NOT EXISTS`` 不适用；
#: 索引内联进 CREATE TABLE，天然幂等）。
MYSQL_REPORTS_DDL = f"""
CREATE TABLE IF NOT EXISTS {REPORTS_TABLE} (
    report_id VARCHAR(80) NOT NULL,
    scenario_key VARCHAR(80),
    run_id VARCHAR(80),
    source_module VARCHAR(80),
    title VARCHAR(512) NOT NULL,
    visibility VARCHAR(32) NOT NULL DEFAULT 'internal',
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    risk_level VARCHAR(32),
    degraded INT NOT NULL DEFAULT 0,
    actor VARCHAR(128),
    internal_brief_json LONGTEXT,
    customer_report LONGTEXT,
    mask_map_json LONGTEXT,
    provenance_json LONGTEXT,
    data_gaps_json LONGTEXT,
    linked_json LONGTEXT,
    governing_slot VARCHAR(64),
    versions_json LONGTEXT,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (report_id),
    KEY idx_report_created (created_at),
    KEY idx_report_status (status),
    KEY idx_report_scenario (scenario_key),
    KEY idx_report_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class ReportRepository(ABC):
    """报告仓储契约（SQLite 实现先行；MySQL 接同一形状）。"""

    @abstractmethod
    def ensure_schema(self) -> None:
        """建表 + 索引（幂等）。"""

    @abstractmethod
    def fetch(self, report_id: str) -> Optional[dict[str, Any]]:
        """按主键取一行（不存在返回 None）。"""

    @abstractmethod
    def upsert(self, record: Mapping[str, Any]) -> None:
        """按 ``report_id`` 幂等写入/更新（``record`` 已含 JSON 文本列）。"""

    @abstractmethod
    def list_rows(self, *, scenario_key: str = "", source_module: str = "",
                  status: str = "", visibility: str = "", risk_level: str = "",
                  q: str = "", limit: int = 50, offset: int = 0
                  ) -> tuple[list[dict[str, Any]], int]:
        """筛选 + 关键字搜索 + 分页；返回 ``(rows, total)``。"""

    @abstractmethod
    def summary(self) -> dict[str, Any]:
        """状态计数与含数据缺口数：``{by_status: {...}, with_gaps: int}``。"""


class SQLiteReportRepository(ReportRepository):
    """SQLite 实现：``db_path`` 必须是已解析的文件路径（调用方先过 fail-fast）。"""

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
        finally:
            conn.close()

    def fetch(self, report_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {REPORTS_TABLE} WHERE report_id = ?",
                (report_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def upsert(self, record: Mapping[str, Any]) -> None:
        columns = ", ".join(REPORT_COLUMNS)
        placeholders = ", ".join("?" for _ in REPORT_COLUMNS)
        updates = ", ".join(f"{name}=excluded.{name}" for name in REPORT_COLUMNS
                            if name != "report_id")
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO {REPORTS_TABLE} ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT(report_id) DO UPDATE SET {updates}",
                    [record.get(name) for name in REPORT_COLUMNS])
        finally:
            conn.close()

    def list_rows(self, *, scenario_key: str = "", source_module: str = "",
                  status: str = "", visibility: str = "", risk_level: str = "",
                  q: str = "", limit: int = 50, offset: int = 0
                  ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: list[Any] = []
        for column, value in (("scenario_key", scenario_key),
                              ("source_module", source_module),
                              ("status", status), ("visibility", visibility),
                              ("risk_level", risk_level)):
            text = str(value or "").strip()
            if text:
                where.append(f"{column} = ?")
                params.append(text)
        keyword = str(q or "").strip()
        if keyword:
            where.append("(title LIKE ? OR scenario_key LIKE ? OR source_module LIKE ? "
                         "OR internal_brief_json LIKE ?)")
            params.extend([f"%{keyword}%"] * 4)

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        safe_limit = max(1, min(int(limit or 50), 200))
        safe_offset = max(0, int(offset or 0))
        conn = self._connect()
        try:
            total = int(conn.execute(
                f"SELECT COUNT(*) AS c FROM {REPORTS_TABLE}{clause}",
                params).fetchone()["c"])
            rows = conn.execute(
                f"SELECT * FROM {REPORTS_TABLE}{clause} "
                f"ORDER BY created_at DESC, report_id DESC LIMIT ? OFFSET ?",
                [*params, safe_limit, safe_offset]).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows], total

    def summary(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT status, COUNT(*) AS c FROM {REPORTS_TABLE} GROUP BY status"
            ).fetchall()
            gaps_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {REPORTS_TABLE} "
                f"WHERE data_gaps_json NOT IN ('', '[]')").fetchone()
        finally:
            conn.close()
        return {
            "by_status": {str(r["status"]): int(r["c"]) for r in rows},
            "with_gaps": int(gaps_row["c"]) if gaps_row else 0,
        }
