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

__all__ = [
    "MEASUREMENTS_TABLE",
    "SpcRepository",
    "SQLiteSpcRepository",
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
