"""SPC 测量值受控读写（平台唯一受治理的 SPC 写入通道）。

红线说明（与 ``contracts/data_source.py`` 呼应）：业务**查询**通道始终只读
（``SQLiteDataSource`` 用 ``mode=ro`` 从驱动层杜绝写操作）。本模块是该红线之外
唯一受治理的 SPC 写入通道，对应工具 ``spc.ingest``（tier 2 · 写）：必须携带
可核验身份，先经 ``core/spc.normalize_measurements`` 校验，再落 ``spc_measurement``。

D15：连接 / 建表 / 补列 / INSERT 已下沉到 ``spc_repository``；
本模块只保留受治理写入口的业务语义（校验由调用方做、空行直接返回 0）。

与 ``orchestrator`` 的启动播种同源——同一 ``db_path``（演示库），只写测量值表，
绝不触碰只读查询数据；读取仍走注册查询（表级 ACL + 脱敏原样生效）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .spc_repository import (
    MEASUREMENTS_TABLE,
    SpcRepository,
    SQLiteSpcRepository,
)

__all__ = ["MEASUREMENTS_TABLE", "SpcRepository", "ensure_measurements_table",
           "append_measurements", "count_measurements"]


def _repo(db_path: str) -> SpcRepository:
    """构造 SQLite 仓储（D15：模块函数不再持有裸 ``_connect``）。"""
    return SQLiteSpcRepository(str(db_path))


def ensure_measurements_table(db_path: str) -> None:
    """幂等建表（种子未执行或全新库时的兜底；不写入任何数据行）。

    老库（表已存在但缺新列）会就地补列 —— 由 ``SQLiteSpcRepository.ensure_schema``
    负责；补列失败不阻断（表可能只读），由调用方的写入异常兜底。
    """
    _repo(db_path).ensure_schema()


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
    repo = _repo(db_path)
    repo.ensure_schema()
    return repo.append(rows, ingested_by=ingested_by, ingested_at=ts)


def count_measurements(db_path: str, characteristic: Optional[str] = None) -> int:
    """测量值行数（可选按特性）；用于导入回执的落库核对。"""
    repo = _repo(db_path)
    repo.ensure_schema()
    return repo.count(characteristic)
