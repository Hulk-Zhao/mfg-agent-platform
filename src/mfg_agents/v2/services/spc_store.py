"""SPC 测量值受控读写（平台唯一受治理的 SPC 写入通道）。

红线说明（与 ``contracts/data_source.py`` 呼应）：业务**查询**通道始终只读
（``SQLiteDataSource`` 用 ``mode=ro`` 从驱动层杜绝写操作）。本模块是该红线之外
唯一受治理的 SPC 写入通道，对应工具 ``spc.ingest``（tier 2 · 写）：必须携带
可核验身份，先经 ``core/spc.normalize_measurements`` 校验，再落 ``spc_measurement``。

与 ``orchestrator._apply_seed`` 同源——同一 ``db_path``（演示库），只写测量值表，
绝不触碰只读查询数据；读取仍走注册查询（表级 ACL + 脱敏原样生效）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

__all__ = ["MEASUREMENTS_TABLE", "ensure_measurements_table",
           "append_measurements", "count_measurements"]

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

# 老库升级：``CREATE TABLE IF NOT EXISTS`` 对已存在的表不生效，老库永远缺这两列。
# 与 core/orchestrator.py::_ensure_columns 同法做 PRAGMA 守卫的幂等补列 ——
# 受治理写入通道是「平台唯一 SPC 写入通道」，它自己必须能自愈老库，
# 不能只依赖 orchestrator 启动时补列。
_ALTER_COLUMNS = (
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


def _connect(db_path: str) -> sqlite3.Connection:
    """可写连接（仅本模块内部使用；与只读查询通道相互独立）。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_measurements_table(db_path: str) -> None:
    """幂等建表（种子未执行或全新库时的兜底；不写入任何数据行）。

    老库（表已存在但缺新列）会就地补列 —— 与 ``core/orchestrator.py::_ensure_columns``
    同法。缺省补列失败不阻断（表可能只读），由调用方的写入异常兜底。
    """
    conn = _connect(db_path)
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


def append_measurements(db_path: str, rows: list[dict[str, Any]], *,
                        ingested_by: str, ingested_at: Optional[str] = None) -> int:
    """把**已校验**的测量值行写入 ``spc_measurement``（返回写入行数）。

    仅接收 ``core/spc.normalize_measurements`` 产出的 accepted 行；不做二次校验、
    不编造缺失字段（缺 usl/lsl/measured_at 时按 NULL 落库）。

    维度列 ``process_stage`` / ``product_category`` 为**可选**：缺键即落 NULL
    （老调用方 ``spc.ingest`` 未传维度时行为逐字段不变，只多了两个 NULL 列）。
    """
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
    ensure_measurements_table(db_path)
    conn = _connect(db_path)
    try:
        with conn:
            conn.executemany(_INSERT_SQL, payload)
    finally:
        conn.close()
    return len(payload)


def count_measurements(db_path: str, characteristic: Optional[str] = None) -> int:
    """测量值行数（可选按特性）；用于导入回执的落库核对。"""
    ensure_measurements_table(db_path)
    conn = _connect(db_path)
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
