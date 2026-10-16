"""数据源接入契约（docs/ARCHITECTURE-V2.md §5.1 / T02）。

新数据源（API / MCP / ETL / RPA）只需实现 ``DataSource`` 三个方法即可接入：
- list_tables()            ：枚举可用表（供 schema 注册与权限裁剪）
- run_query(name, params)  ：执行预注册查询（参数化，高频固定查询走此通道）
- execute_readonly_sql(sql)：执行明细探查 SQL（调用前必须已过 Text2SQL 审核链）

``SQLiteDataSource`` 为本地开发实现：只读连接（``mode=ro``），
任何写操作在 SQLite 驱动层即被拒绝，这是"数据质量优先于智能能力"的落地边界。
"""
from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DataSource(ABC):
    """数据源抽象：所有查询能力统一经此契约暴露。"""

    name: str = "abstract"

    @abstractmethod
    def list_tables(self) -> list[str]:
        """枚举数据源中的表名。"""

    @abstractmethod
    def run_query(self, name: str, params: Optional[dict] = None) -> list[dict]:
        """执行注册查询（按名称索引预定义参数化 SQL）。"""

    @abstractmethod
    def execute_readonly_sql(self, sql: str, params: Optional[dict] = None,
                             row_cap: int = 200) -> list[dict]:
        """执行明细探查 SQL（只读 + 行数上限）。"""


class SQLiteDataSource(DataSource):
    """SQLite 只读数据源（本地 dev fixture / 演示）。

    连接串使用 ``file:...?mode=ro``，从驱动层杜绝写操作；
    注册查询通过构造参数 ``queries`` 注入（见 data/queries.yaml）。
    """

    def __init__(self, db_path: str, name: str = "dev",
                 queries: Optional[dict[str, str]] = None,
                 row_cap: int = 200) -> None:
        self.name = name
        self.db_path = str(db_path)
        self.queries: dict[str, str] = dict(queries or {})
        self.row_cap = row_cap

    def _connect_ro(self) -> sqlite3.Connection:
        """建立只读连接（URI mode=ro；文件不存在会直接报错，避免误建空库）。"""
        uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def list_tables(self) -> list[str]:
        with self._connect_ro() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def run_query(self, name: str, params: Optional[dict] = None) -> list[dict]:
        sql = self.queries.get(name)
        if sql is None:
            raise KeyError(f"数据源 {self.name!r} 未注册查询: {name!r}")
        return self.execute_readonly_sql(sql, params, self.row_cap)

    def execute_readonly_sql(self, sql: str, params: Optional[dict] = None,
                             row_cap: int = 200) -> list[dict]:
        sql = sql.strip().rstrip(";")
        capped = _apply_row_cap(sql, row_cap)
        with self._connect_ro() as conn:
            cur = conn.execute(capped, params or {})
            rows = [dict(r) for r in cur.fetchmany(row_cap)]
        if len(rows) >= row_cap:
            logger.warning("查询结果达到行数上限 %d，可能被截断: %s", row_cap, sql)
        return rows


def _apply_row_cap(sql: str, row_cap: int) -> str:
    """无 LIMIT 的查询外包一层 LIMIT，防止全表拉取（结果爆炸防线）。"""
    if row_cap <= 0 or "limit" in sql.lower():
        return sql
    return f"SELECT * FROM ({sql}) LIMIT {int(row_cap)}"


class DataSourceRegistry:
    """数据源注册表：按名获取，支持默认源（新数据源零改框架代码接入）。"""

    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}
        self._default: Optional[str] = None

    def register(self, source: DataSource, default: bool = False) -> None:
        if source.name in self._sources:
            raise ValueError(f"数据源名称冲突: {source.name!r}")
        self._sources[source.name] = source
        if default or self._default is None:
            self._default = source.name

    def get(self, name: Optional[str] = None) -> DataSource:
        key = name or self._default
        if key is None or key not in self._sources:
            raise KeyError(f"数据源不存在: {key!r}（已注册: {list(self._sources)}）")
        return self._sources[key]

    def list(self) -> list[str]:
        return sorted(self._sources)

    # ---- 便捷转发（Text2SQLService 直接面向注册表编程）----

    def list_tables(self, source: Optional[str] = None) -> list[str]:
        return self.get(source).list_tables()

    def run_query(self, name: str, params: Optional[dict] = None,
                  source: Optional[str] = None) -> list[dict]:
        return self.get(source).run_query(name, params)

    def execute_readonly_sql(self, sql: str, params: Optional[dict] = None,
                             source: Optional[str] = None,
                             row_cap: int = 200) -> list[dict]:
        return self.get(source).execute_readonly_sql(sql, params, row_cap)
