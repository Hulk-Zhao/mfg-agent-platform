"""演示库启动播种（D15 迁移第 5 项）：把 ``sqlite3`` 直连从编排器移出。

``orchestrator._apply_seed`` 原先在编排内核里直接 ``sqlite3.connect`` 并执行
``data/seed_*.sql``；本模块提供 ``SeedRunner`` 契约 + SQLite 实现：

- 播种是**可重放的幂等写**（``INSERT OR IGNORE`` + PRAGMA 守卫补列）；
- 失败不阻断启动（演示库可能只读）——与既有行为逐字一致；
- 引擎/编排器只依赖契约，不再持有裸连接；将来换库只需实现同一 ``apply``。
"""
from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence, Union

logger = logging.getLogger(__name__)

__all__ = ["SeedRunner", "SQLiteSeedRunner"]


class SeedRunner(ABC):
    """播种契约：把一组幂等 SQL 应用到目标库，返回成功执行的文件数。"""

    @abstractmethod
    def apply(self, db_path: str) -> int:
        """执行播种；失败降级（不抛错），返回实际执行的文件数。"""


class SQLiteSeedRunner(SeedRunner):
    """SQLite 实现：按序执行种子文件 + 每个文件前做 PRAGMA 守卫补列。"""

    def __init__(self, seeds: Sequence[Union[str, Path]]) -> None:
        self.seeds: tuple[Path, ...] = tuple(Path(p) for p in seeds)

    def apply(self, db_path: str) -> int:
        seeds = [p for p in self.seeds if p.exists()]
        if not seeds:
            return 0
        applied = 0
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                # 幂等补列：老库（已存在表但缺新列）就地升级，跨 SQLite 版本安全
                # （不使用 ADD COLUMN IF NOT EXISTS）；表不存在时空集 → 跳过。
                # 每个种子文件执行前补一次：覆盖「老库升级」与「全新库先建表后补列」。
                for path in seeds:
                    _ensure_columns(conn)
                    conn.executescript(path.read_text(encoding="utf-8"))
                    logger.info("[seed] 演示数据扩充完成: %s", path.name)
                    applied += 1
        except Exception as exc:  # noqa: BLE001 —— 种子失败不阻断启动（演示库可能只读）
            logger.warning("[seed] 执行失败（不阻断）: %s", exc)
        return applied


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """PRAGMA 守卫的幂等补列（老演示库不重建，就地升级）。

    在**每个种子文件执行前**调用一次：表尚未创建时 ``PRAGMA table_info`` 为空集则跳过，
    待前置种子建表后再回到这里补列——因此对「老库升级」与「全新库首建」都成立
    （全新库：seed_v3 建表 → 下一次调用补列 → 后续种子即可写入新列）。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(quality_defect)")}
    if cols and "process_stage" not in cols:
        conn.execute("ALTER TABLE quality_defect ADD COLUMN process_stage TEXT")
    if cols and "defect_category" not in cols:
        conn.execute("ALTER TABLE quality_defect ADD COLUMN defect_category TEXT")
    if cols and "product_category" not in cols:
        conn.execute("ALTER TABLE quality_defect ADD COLUMN product_category TEXT")
    # 标准三率两级看板：老库补齐工段 / 班组两列（同法 PRAGMA 守卫，表不存在则跳过）
    std_cols = {r[1] for r in conn.execute("PRAGMA table_info(std_standard_audit)")}
    if std_cols and "process_stage" not in std_cols:
        conn.execute("ALTER TABLE std_standard_audit ADD COLUMN process_stage TEXT")
    if std_cols and "team" not in std_cols:
        conn.execute("ALTER TABLE std_standard_audit ADD COLUMN team TEXT")
    # V3.9：设备运行补 ``stop_reason``（停机原因，8 类）供制造侧原因柏拉图事实表
    eq_cols = {r[1] for r in conn.execute("PRAGMA table_info(equipment_run_time)")}
    if eq_cols and "stop_reason" not in eq_cols:
        conn.execute("ALTER TABLE equipment_run_time ADD COLUMN stop_reason TEXT")
    # SPC 过程能力看板：老库补齐 工段 / 产品分类 两列（同法 PRAGMA 守卫，表不存在则跳过）
    # 说明：``CREATE TABLE IF NOT EXISTS`` 对老库不生效，不在此补列则老库永远缺这两列，
    # 板级「工段 / 产品分类」筛选将无数据可筛。
    spc_cols = {r[1] for r in conn.execute("PRAGMA table_info(spc_measurement)")}
    if spc_cols and "process_stage" not in spc_cols:
        conn.execute("ALTER TABLE spc_measurement ADD COLUMN process_stage TEXT")
    if spc_cols and "product_category" not in spc_cols:
        conn.execute("ALTER TABLE spc_measurement ADD COLUMN product_category TEXT")
