"""数据源接入契约（docs/ARCHITECTURE-V2.md §5.1 / T02）。

新数据源（API / MCP / ETL / RPA）只需实现 ``DataSource`` 三个读方法即可接入：
- list_tables()            ：枚举可用表（供 schema 注册与权限裁剪）
- run_query(name, params)  ：执行预注册查询（参数化，高频固定查询走此通道）
- execute_readonly_sql(sql)：执行明细探查 SQL（调用前必须已过 Text2SQL 审核链）

R13 / D14 起增加**写回侧契约**（可选实现）：
- write_back(intent, payload) -> Receipt：幂等键 + 审计回执；未实现的源默认
  fail-closed（抛 ``WritebackNotSupported``），只读源绝不静默写入。

``SQLiteDataSource`` 为本地开发实现：只读连接（``mode=ro``），
任何写操作在 SQLite 驱动层即被拒绝，这是"数据质量优先于智能能力"的落地边界。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "WritebackNotSupported",
    "Receipt",
    "DataSource",
    "SQLiteDataSource",
    "MySQLDataSource",
    "DataSourceRegistry",
]


class WritebackNotSupported(NotImplementedError):
    """数据源未实现写回（只读源默认 fail-closed，绝不静默写入）。"""


@dataclass(frozen=True)
class Receipt:
    """写回回执（R13 / D14）：幂等键 + 审计事实，不只返回一个 200。

    Attributes:
        receipt_id: 稳定回执 id（``rc-`` + 幂等键摘要，重放同 id）。
        intent: 写回意图键（如 ``erp.purchase_receipt``）。
        status: ``applied``（已落地）/ ``pending_consent``（待人工会签，未落地）。
        idempotency_key: 调用方幂等键（缺省由意图 + 载荷摘要派生）。
        tier: 治理分级（0 只读 / 1 预执行同意 / 2 事后审计）。
        mode: ``mock``（模拟连接器，未写回真实系统）/ ``real``。
        demo: 是否演示态（前端据此渲染不可关闭的「演示回写」徽标）。
        label: 面向人的回写事实说明（演示态必须显式写「未写回 ERP」）。
        system: 目标系统名（如「ERP(模拟)」）。
        replayed: 是否命中既有幂等回执（重复提交不再落地第二遍）。
        created_at: 回执生成时间（ISO8601）。
        consent: 预执行同意凭据（ticket_id / approved / actor）。
        payload: 本次回写载荷快照（审计用）。
        next_step: 未落地时的下一步指引（如「先经 delivery_commit 会签」）。
    """

    receipt_id: str
    intent: str
    status: str
    idempotency_key: str
    tier: int = 2
    mode: str = "mock"
    demo: bool = True
    label: str = ""
    system: str = ""
    replayed: bool = False
    created_at: str = ""
    consent: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    next_step: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "intent": self.intent,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "tier": self.tier,
            "mode": self.mode,
            "demo": self.demo,
            "demo_writeback": self.demo,
            "label": self.label,
            "system": self.system,
            "replayed": self.replayed,
            "created_at": self.created_at,
            "consent": dict(self.consent),
            "payload": dict(self.payload),
            "next_step": self.next_step,
        }


class DataSource(ABC):
    """数据源抽象：所有查询能力统一经此契约暴露。

    数据注入三步（V3.1 起）：
    1. 实现 :meth:`list_tables` / :meth:`run_query` / :meth:`execute_readonly_sql`；
    2. 覆写 :meth:`describe` 声明元数据（业务系统名 / 表说明 / 归属域 / 脱敏要求）；
    3. 注册进 ``DataSourceRegistry`` —— 立即出现在 ``GET /api/v2/datasources``
       的就绪度总览里（健康 / 表清单 / 注册查询数），无需改任何框架代码。
    """

    name: str = "abstract"
    #: SQL 方言（供 Text2SQL 提示词/查询库选择；未知实现默认按 SQLite 处理）
    dialect: str = "unknown"

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

    # ---------- 写回侧契约（R13 / D14：可选实现，默认 fail-closed） ----------

    def write_back(self, intent: str, payload: Optional[dict[str, Any]] = None) -> Receipt:
        """执行一次写回意图，返回幂等回执。

        设计边界（与「Agent 永不放行」同源）：
        - 写回是有副作用的治理动作，**只读源默认拒绝**（抛
          ``WritebackNotSupported``），不得静默写入；
        - ``payload`` 必须带幂等键（``idempotency_key``；缺省由实现派生），
          重复提交返回同一回执、不重复落地；
        - ``tier=1`` 的意图要求 ``payload["consent"]`` 带已审批工单
          （``ticket_id`` + ``approved``），否则只能返回 ``pending_consent``
          回执（未落地），不得先斩后奏。

        未实现写回的数据源无需覆写本方法。
        """
        raise WritebackNotSupported(
            f"数据源 {self.name!r} 未实现写回（只读契约默认拒绝写入）；"
            f"需要回写时实现 write_back(intent, payload) 并返回 Receipt")

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
    dialect = "sqlite"

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


class MySQLDataSource(DataSource):
    """MySQL 只读数据源（生产业务库副本 / 只读账号）。

    红线与 ``SQLiteDataSource`` 同源：业务查询通道**只读**。

    - 代码层：只允许 ``SELECT`` / ``WITH``，拒绝写动词；行数上限兜底；
    - 连接层：生产必须使用**只读数据库账号**（代码检查不是权限的替代品）；
    - 方言桥接：注册查询里少量 SQLite ``'%' || :kw || '%'`` 写法会映射为
      ``CONCAT(...)``；其余 SQLite 专属函数/语法不做自动翻译（Text2SQL 的方言
      提示词仍是后续工作），遇到不兼容 SQL 应如实报错而不是猜。
    """

    name = "mysql"
    dialect = "mysql"

    #: 写类动词（只读通道拒绝；真实防线仍是只读账号）
    _WRITE_WORDS = re.compile(
        r"\b(insert|update|delete|replace|drop|alter|create|truncate|grant|revoke)\b",
        re.IGNORECASE)
    _CONCAT = re.compile(
        r"'(?P<a>[^']*)'\s*\|\|\s*(?P<b>:\w+)\s*\|\|\s*'(?P<c>[^']*)'")
    _CONCAT2 = re.compile(r"'(?P<a>[^']*)'\s*\|\|\s*(?P<b>:\w+)")

    def __init__(self, url: str, name: str = "mysql",
                 queries: Optional[dict[str, str]] = None,
                 row_cap: int = 200,
                 meta: Optional[dict[str, Any]] = None) -> None:
        from sqlalchemy import create_engine

        self.name = name
        self.url = url
        self.queries: dict[str, str] = dict(queries or {})
        self.row_cap = row_cap
        self.meta: dict[str, Any] = dict(meta or {})
        self._engine = create_engine(url, pool_pre_ping=True, future=True)

    def list_tables(self) -> list[str]:
        from sqlalchemy import text

        with self._engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name")).fetchall()
        return [str(r[0]) for r in rows]

    def health(self) -> dict[str, Any]:
        from sqlalchemy import text

        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1")).fetchone()
            return {"ok": True, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    def describe(self) -> dict[str, Any]:
        """数据源自描述：表清单含字段（information_schema）。"""
        base = super().describe()
        if not base["health"]["ok"]:
            return base
        from sqlalchemy import text

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT table_name, column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "ORDER BY table_name, ordinal_position")).fetchall()
            fields: dict[str, list[dict[str, str]]] = {}
            for table, column, data_type in rows:
                fields.setdefault(str(table), []).append(
                    {"name": str(column), "type": str(data_type)})
            base["tables"] = [
                {"name": str(t["name"]), "fields": fields.get(str(t["name"]), [])}
                for t in base["tables"]]
        except Exception as exc:  # noqa: BLE001
            logger.warning("MySQL describe 字段信息获取失败（%s）: %s", self.name, exc)
        return base

    def run_query(self, name: str, params: Optional[dict] = None) -> list[dict]:
        sql = self.queries.get(name)
        if sql is None:
            raise KeyError(f"数据源 {self.name!r} 未注册查询: {name!r}")
        return self.execute_readonly_sql(sql, params, self.row_cap)

    def execute_readonly_sql(self, sql: str, params: Optional[dict] = None,
                             row_cap: int = 200) -> list[dict]:
        from sqlalchemy import text

        cleaned = self._strip_comments(sql).strip().rstrip(";")
        if not re.match(r"(?is)^(select|with)\b", cleaned):
            raise ValueError("只读数据源拒绝非查询 SQL（仅允许 SELECT / WITH）")
        # 去掉字符串字面量后再查写动词，避免 WHERE title='delete' 这类误伤
        sanitized = re.sub(r"'(?:[^']|'')*'", "''", cleaned)
        if self._WRITE_WORDS.search(sanitized):
            raise ValueError("只读数据源拒绝包含写动词的 SQL")
        translated = self._translate_concat(cleaned)
        capped = _apply_row_cap(translated, row_cap)
        with self._engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                text(capped), params or {}).mappings().fetchmany(row_cap)]
        if len(rows) >= row_cap:
            logger.warning("查询结果达到行数上限 %d，可能被截断: %s", row_cap, sql)
        return rows

    # ---------- 方言桥接 ----------

    @staticmethod
    def _strip_comments(sql: str) -> str:
        """去 ``--`` 行注释与 ``/* */`` 块注释（只读前缀判定前先净化）。"""
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
        return re.sub(r"--[^\n]*", " ", sql)

    @classmethod
    def _translate_concat(cls, sql: str) -> str:
        """SQLite ``'%' || :kw || '%'`` → MySQL ``CONCAT('%', :kw, '%')``。

        只处理注册查询里已知的「字面量 + 命名参数 + 字面量」拼接；列之间的
        ``||`` 不自动改写，避免把逻辑或误改成字符串拼接。无匹配时原样返回。
        """
        translated = cls._CONCAT.sub(
            lambda m: f"CONCAT('{m.group('a')}', {m.group('b')}, '{m.group('c')}')",
            sql)
        return cls._CONCAT2.sub(
            lambda m: f"CONCAT('{m.group('a')}', {m.group('b')})", translated)

    def close(self) -> None:
        self._engine.dispose()


def _apply_row_cap(sql: str, row_cap: int) -> str:
    """无 LIMIT 的查询外包一层 LIMIT，防止全表拉取（结果爆炸防线）。

    派生表必须带别名：SQLite 可省，MySQL 强制（否则 1248 错误）。
    """
    if row_cap <= 0 or "limit" in sql.lower():
        return sql
    return f"SELECT * FROM ({sql}) AS _mfg_q LIMIT {int(row_cap)}"


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
