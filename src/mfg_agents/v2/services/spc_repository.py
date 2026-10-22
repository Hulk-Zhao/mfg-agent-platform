"""SPC 测量值仓储契约（D15 迁移第 3 项）：受治理写入通道的持久化边界。

``spc_store`` 原先直接持有 ``_connect`` + INSERT SQL；本契约把连接、建表、
补列与写入收敛到 ``SpcRepository``：

- 业务校验（``core/spc.normalize_measurements``）仍在调用方，仓储只写
  **已校验**的行，不做二次判断；
- 建表与老库补列（``process_stage`` / ``product_category``）由
  ``ensure_schema`` 负责，受治理通道自己必须能自愈老库；
- 读取查询仍走注册查询与 ``SQLiteDataSource``（只读红线不变）。
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, text

from ...common.platform_schema import sqlite_path_from_url

__all__ = [
    "MEASUREMENTS_TABLE",
    "MYSQL_SPC_DDL",
    "SpcRepository",
    "SQLiteSpcRepository",
    "SQLAlchemySpcRepository",
]

MEASUREMENTS_TABLE = "spc_measurement"

_DDL = """
CREATE TABLE IF NOT EXISTS spc_measurement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    characteristic VARCHAR(80) NOT NULL,
    subgroup_no INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    value REAL NOT NULL,
    usl REAL,
    lsl REAL,
    measured_at VARCHAR(30),
    source VARCHAR(40),
    ingested_by VARCHAR(40),
    ingested_at VARCHAR(30) NOT NULL,
    process_stage VARCHAR(20),
    product_category VARCHAR(20)
);
CREATE INDEX IF NOT EXISTS idx_spc_measurement_char
    ON spc_measurement (characteristic);
"""

#: MySQL 版建表（索引内联；TEXT 默认值不适用）。
MYSQL_SPC_DDL = """
CREATE TABLE IF NOT EXISTS spc_measurement (
    id BIGINT NOT NULL AUTO_INCREMENT,
    characteristic VARCHAR(80) NOT NULL,
    subgroup_no INT NOT NULL,
    seq INT NOT NULL,
    value DOUBLE NOT NULL,
    usl DOUBLE NULL,
    lsl DOUBLE NULL,
    measured_at VARCHAR(30) NULL,
    source VARCHAR(40) NULL,
    ingested_by VARCHAR(40) NULL,
    ingested_at VARCHAR(40) NOT NULL,
    process_stage VARCHAR(20) NULL,
    product_category VARCHAR(20) NULL,
    PRIMARY KEY (id),
    KEY idx_spc_measurement_char (characteristic)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

#: 老库升级：``CREATE TABLE IF NOT EXISTS`` 对已存在的表不生效，老库永远缺这两列。
_ALTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("process_stage", "ALTER TABLE spc_measurement ADD COLUMN process_stage TEXT"),
    ("product_category",
     "ALTER TABLE spc_measurement ADD COLUMN product_category TEXT"),
)

_INSERT_SQL = (
    f"INSERT INTO {MEASUREMENTS_TABLE} "
    "(characteristic, subgroup_no, seq, value, usl, lsl, measured_at, source, "
    "ingested_by, ingested_at, process_stage, product_category) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class SpcRepository(ABC):
    """SPC 测量值仓储契约（SQLite 实现先行）。"""

    @abstractmethod
    def ensure_schema(self) -> None:
        """建表 + 索引 + 老库补列（幂等）。"""

    @abstractmethod
    def append(self, rows: list[dict[str, Any]], *, ingested_by: str,
               ingested_at: Optional[str] = None) -> int:
        """写入已校验的测量值行，返回写入行数。"""

    @abstractmethod
    def count(self, characteristic: Optional[str] = None) -> int:
        """测量值行数（可选按特性）。"""


class SQLiteSpcRepository(SpcRepository):
    """SQLite 实现（演示库；与只读查询通道相互独立）。"""

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
                cols = {r[1] for r in conn.execute(
                    f"PRAGMA table_info({MEASUREMENTS_TABLE})")}
                for name, alter in _ALTER_COLUMNS:
                    if name not in cols:
                        try:
                            conn.execute(alter)
                        except sqlite3.OperationalError:
                            pass  # 老库只读 / 已被并发补列：交由调用方兜底
        finally:
            conn.close()

    def append(self, rows: list[dict[str, Any]], *, ingested_by: str,
               ingested_at: Optional[str] = None) -> int:
        if not rows:
            return 0
        ts = ingested_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = [
            (str(r["characteristic"]), int(r["subgroup_no"]), int(r["seq"]),
             float(r["value"]), r.get("usl"), r.get("lsl"),
             r.get("measured_at"), r.get("source"), str(ingested_by), ts,
             r.get("process_stage"), r.get("product_category"))
            for r in rows
        ]
        conn = self._connect()
        try:
            with conn:
                conn.executemany(_INSERT_SQL, payload)
        finally:
            conn.close()
        return len(payload)

    def count(self, characteristic: Optional[str] = None) -> int:
        conn = self._connect()
        try:
            if characteristic:
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {MEASUREMENTS_TABLE} "
                    "WHERE characteristic = ?", (characteristic,)).fetchone()
            else:
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {MEASUREMENTS_TABLE}").fetchone()
            return int(row["c"]) if row else 0
        finally:
            conn.close()


class SQLAlchemySpcRepository(SpcRepository):
    """跨方言实现：SQLite 复用原生实现，MySQL 用命名参数与 InnoDB DDL。"""

    _INSERT = (
        f"INSERT INTO {MEASUREMENTS_TABLE} "
        "(characteristic, subgroup_no, seq, value, usl, lsl, measured_at, source, "
        "ingested_by, ingested_at, process_stage, product_category) "
        "VALUES (:characteristic, :subgroup_no, :seq, :value, :usl, :lsl, "
        ":measured_at, :source, :ingested_by, :ingested_at, "
        ":process_stage, :product_category)"
    )

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            SQLiteSpcRepository(sqlite_path_from_url(self.db_url)).ensure_schema()
            return
        if dialect != "mysql":
            raise NotImplementedError(
                f"SPC 仓储暂只支持 SQLite / MySQL，收到方言 {dialect!r}")
        with self.engine.begin() as conn:
            conn.execute(text(MYSQL_SPC_DDL))

    def append(self, rows: list[dict[str, Any]], *, ingested_by: str,
               ingested_at: Optional[str] = None) -> int:
        if not rows:
            return 0
        ts = ingested_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = [
            {"characteristic": str(r["characteristic"]),
             "subgroup_no": int(r["subgroup_no"]), "seq": int(r["seq"]),
             "value": float(r["value"]), "usl": r.get("usl"),
             "lsl": r.get("lsl"), "measured_at": r.get("measured_at"),
             "source": r.get("source"), "ingested_by": str(ingested_by),
             "ingested_at": ts, "process_stage": r.get("process_stage"),
             "product_category": r.get("product_category")}
            for r in rows
        ]
        with self.engine.begin() as conn:
            conn.execute(text(self._INSERT), payload)
        return len(payload)

    def count(self, characteristic: Optional[str] = None) -> int:
        if characteristic:
            sql = (f"SELECT COUNT(*) AS c FROM {MEASUREMENTS_TABLE} "
                   "WHERE characteristic = :characteristic")
            params: dict[str, Any] = {"characteristic": characteristic}
        else:
            sql = f"SELECT COUNT(*) AS c FROM {MEASUREMENTS_TABLE}"
            params = {}
        with self.engine.connect() as conn:
            return int(conn.execute(text(sql), params).scalar() or 0)
