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
    """数据源抽象：所有查询能力统一经此契约暴露。

    数据注入三步（V3.1 起）：
    1. 实现 :meth:`list_tables` / :meth:`run_query` / :meth:`execute_readonly_sql`；
    2. 覆写 :meth:`describe` 声明元数据（业务系统名 / 表说明 / 归属域 / 脱敏要求）；
    3. 注册进 ``DataSourceRegistry`` —— 立即出现在 ``GET /api/v2/datasources``
       的就绪度总览里（健康 / 表清单 / 注册查询数），无需改任何框架代码。
    """

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

    # ---------- 数据注入自描述（V3.1：接入即见，便于后续真实数据源接入） ----------

    # 元数据由接入方声明（子类覆写或构造时注入）；缺省为空 → 前端展示"未声明"
    meta: dict[str, Any] = {}

    def describe(self) -> dict[str, Any]:
        """数据源自描述：接入就绪度总览的数据来源。

        返回结构（键缺省安全，前端按缺失降级显示"未声明"）：
        ``{name, kind, system, domain, tables: [{name, fields: [...]}],
        registered_queries: int, health: {ok, error}, meta}``
        """
        tables = []
        try:
            tables = [{"name": t} for t in self.list_tables()]
        except Exception as exc:  # noqa: BLE001 —— 健康探针不抛出，失败进 health
            return {"name": self.name, "kind": type(self).__name__,
                    "system": self.meta.get("system", ""),
                    "domain": self.meta.get("domain", ""),
                    "tables": [], "registered_queries": 0,
                    "health": {"ok": False, "error": str(exc)[:200]},
                    "meta": dict(self.meta)}
        return {
            "name": self.name,
            "kind": type(self).__name__,
            "system": self.meta.get("system", ""),
            "domain": self.meta.get("domain", ""),
            "tables": tables,
            "registered_queries": len(getattr(self, "queries", {}) or {}),
            "health": {"ok": True, "error": ""},
            "meta": dict(self.meta),
        }

    def health(self) -> dict[str, Any]:
        """轻量健康探针（不拉全表，只验证可达与可枚举）。"""
        try:
            self.list_tables()
            return {"ok": True, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}


class SQLiteDataSource(DataSource):
    """SQLite 只读数据源（本地 dev fixture / 演示）。

    连接串使用 ``file:...?mode=ro``，从驱动层杜绝写操作，这是"数据质量
    优先于智能能力"的落地边界；注册查询通过构造参数 ``queries`` 注入
    （见 data/queries.yaml）。

    :memory: 特殊支持（BUG-4 修复，主理人规格）：内存库每个新连接都是
    独立空库，故按源名在进程内共享同一连接（StaticPool 语义），使
    ``sqlite:///:memory:`` 与文件库行为一致；该场景无落盘数据，
    只读约束不适用（进程内临时库）。
    """

    # 进程内共享的内存库连接（源名 → 连接），模拟 StaticPool 连接复用
    _memory_conns: dict[str, sqlite3.Connection] = {}

    def __init__(self, db_path: str, name: str = "dev",
                 queries: Optional[dict[str, str]] = None,
                 row_cap: int = 200,
                 meta: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.db_path = str(db_path)
        self.queries: dict[str, str] = dict(queries or {})
        self.row_cap = row_cap
        # 接入方声明的元数据（system/domain/owner/敏感字段说明等）
        self.meta: dict[str, Any] = dict(meta or {})

    def _connect_ro(self) -> sqlite3.Connection:
        """建立只读连接（URI mode=ro；文件不存在会直接报错，避免误建空库）。

        :memory: 走进程内共享连接（见类文档）。
        """
        if self.db_path == ":memory:":
            conn = self._memory_conns.get(self.name)
            if conn is None:
                conn = sqlite3.connect(":memory:")
                conn.row_factory = sqlite3.Row
                self._memory_conns[self.name] = conn
            return conn
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

    def describe(self) -> dict[str, Any]:
        """SQLite 实现的自描述：表清单含字段与类型（PRAGMA table_info）。"""
        base = super().describe()
        if not base["health"]["ok"]:
            return base
        tables: list[dict[str, Any]] = []
        try:
            with self._connect_ro() as conn:
                for t in base["tables"]:
                    cols = conn.execute(
                        f"PRAGMA table_info({t['name']})").fetchall()
                    tables.append({
                        "name": t["name"],
                        "fields": [{"name": c["name"], "type": c["type"]}
                                   for c in cols],
                    })
        except Exception as exc:  # noqa: BLE001 —— 字段信息失败不影响整体就绪度
            logger.warning("describe 字段信息获取失败（%s）: %s", self.name, exc)
            tables = base["tables"]
        base["tables"] = tables
        return base

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

    def describe_all(self) -> list[dict[str, Any]]:
        """全部数据源的自描述（GET /api/v2/datasources 的数据来源）。

        单个源 describe 抛错不影响其他源（隔离故障，总览永远可用）。
        """
        out: list[dict[str, Any]] = []
        for name in self.list():
            try:
                out.append(self._sources[name].describe())
            except Exception as exc:  # noqa: BLE001
                out.append({"name": name, "kind": "unknown",
                            "health": {"ok": False, "error": str(exc)[:200]},
                            "tables": [], "registered_queries": 0,
                            "system": "", "domain": "", "meta": {}})
        return out

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
