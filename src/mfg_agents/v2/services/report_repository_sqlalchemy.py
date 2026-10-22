"""`ReportRepository` 的 SQLAlchemy 实现（治理库切 MySQL 的第二块）。

- 建表按方言分派：SQLite 复用 ``SQLiteReportRepository``，MySQL 执行
  ``MYSQL_REPORTS_DDL``；其他方言显式拒绝。
- 行级 SQL 只用命名参数；upsert 按方言生成（SQLite ``ON CONFLICT`` /
  MySQL ``ON DUPLICATE KEY UPDATE``），不把 ``?`` 或 SQLite 专属语法带进来。
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import create_engine, text

from ...common.platform_schema import sqlite_path_from_url
from .report_repository import (
    MYSQL_REPORTS_DDL,
    REPORT_COLUMNS,
    REPORTS_TABLE,
    ReportRepository,
    SQLiteReportRepository,
)

__all__ = ["SQLAlchemyReportRepository"]


class SQLAlchemyReportRepository(ReportRepository):
    """跨方言报告仓储（SQLite / MySQL）。"""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            SQLiteReportRepository(sqlite_path_from_url(self.db_url)).ensure_schema()
            return
        if dialect != "mysql":
            raise NotImplementedError(
                f"报告仓储暂只支持 SQLite / MySQL，收到方言 {dialect!r}")
        with self.engine.begin() as conn:
            conn.execute(text(MYSQL_REPORTS_DDL))

    def fetch(self, report_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {REPORTS_TABLE} WHERE report_id = :rid"),
                {"rid": report_id}).mappings().first()
        return dict(row) if row is not None else None

    def upsert(self, record: Mapping[str, Any]) -> None:
        columns = ", ".join(REPORT_COLUMNS)
        values = ", ".join(f":{c}" for c in REPORT_COLUMNS)
        params = {c: record.get(c) for c in REPORT_COLUMNS}
        if self.engine.dialect.name == "mysql":
            updates = ", ".join(f"{c}=VALUES({c})" for c in REPORT_COLUMNS
                                if c != "report_id")
            suffix = f"ON DUPLICATE KEY UPDATE {updates}"
        else:
            updates = ", ".join(f"{c}=excluded.{c}" for c in REPORT_COLUMNS
                                if c != "report_id")
            suffix = f"ON CONFLICT(report_id) DO UPDATE SET {updates}"
        with self.engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO {REPORTS_TABLE} ({columns}) "
                     f"VALUES ({values}) {suffix}"), params)

    def list_rows(self, *, scenario_key: str = "", source_module: str = "",
                  status: str = "", visibility: str = "", risk_level: str = "",
                  q: str = "", limit: int = 50, offset: int = 0
                  ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: dict[str, Any] = {}
        for column, value in (("scenario_key", scenario_key),
                              ("source_module", source_module),
                              ("status", status), ("visibility", visibility),
                              ("risk_level", risk_level)):
            text_value = str(value or "").strip()
            if text_value:
                where.append(f"{column} = :{column}")
                params[column] = text_value
        keyword = str(q or "").strip()
        if keyword:
            where.append("(title LIKE :q OR scenario_key LIKE :q "
                         "OR source_module LIKE :q OR internal_brief_json LIKE :q)")
            params["q"] = f"%{keyword}%"
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        safe_limit = max(1, min(int(limit or 50), 200))
        safe_offset = max(0, int(offset or 0))

        with self.engine.connect() as conn:
            total = int(conn.execute(
                text(f"SELECT COUNT(*) AS c FROM {REPORTS_TABLE}{clause}"),
                params).scalar() or 0)
            rows = conn.execute(
                text(f"SELECT * FROM {REPORTS_TABLE}{clause} "
                     f"ORDER BY created_at DESC, report_id DESC "
                     f"LIMIT :lim OFFSET :off"),
                {**params, "lim": safe_limit, "off": safe_offset}).mappings().all()
        return [dict(r) for r in rows], total

    def summary(self) -> dict[str, Any]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT status, COUNT(*) AS c FROM {REPORTS_TABLE} "
                     f"GROUP BY status")).mappings().all()
            gaps = conn.execute(
                text(f"SELECT COUNT(*) AS c FROM {REPORTS_TABLE} "
                     f"WHERE data_gaps_json NOT IN ('', '[]')")).scalar()
        return {
            "by_status": {str(r["status"]): int(r["c"]) for r in rows},
            "with_gaps": int(gaps or 0),
        }
