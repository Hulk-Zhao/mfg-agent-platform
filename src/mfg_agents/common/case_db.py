"""案例库连接与 schema 契约（D15 迁移第 4 项）。

``case_schema`` 原先直接持有 ``sqlite3.connect``；本契约把**连接创建**与
**建表**收敛到 ``CaseDatabase`` / ``SQLiteCaseDatabase``：

- ``case_schema.connect`` / ``ensure_case_schema`` 变为门面，保持旧导入路径；
- ``bulk_load``（批量导入时关闭 fsync）随连接实现一起下沉，旧路径继续 re-export；
- 案例业务 SQL 仍在 ``case_store`` / ``case_audit`` / ``case_access`` /
  ``case_ontology_gap``；它们通过 ``connect()`` 拿连接，不直接持有 sqlite3。
- ``CASE_DB`` 显式配成 ``mysql+pymysql://...`` 时，``connect()`` 返回
  ``MySQLCaseConnection``（DB-API 兼容 + 少量 SQLite 方言桥接），
  schema 用 ``CASE_DDL_MYSQL``；SQLite 仍是默认零依赖实现。

为什么关系库是真相源、向量库是可重建派生索引：见 ``case_schema`` 模块 docstring
与架构 §6.2；本文件只搬持久化边界，不改语义。
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CASE_DB",
    "CASE_TABLES",
    "CASE_DDL",
    "CASE_DDL_MYSQL",
    "CaseDatabase",
    "SQLiteCaseDatabase",
    "MySQLCaseDatabase",
    "default_case_database",
    "case_database_for",
    "resolve_case_db",
    "is_mysql_target",
    "bulk_load",
    "connect",
    "ensure_case_schema",
]

#: 默认案例库文件（相对运行目录；由 ``settings.CASE_DB`` 覆盖）。
DEFAULT_CASE_DB = Path("data") / "case.db"

CASE_DDL: tuple[str, ...] = (
    # ---------- 案例主数据 ----------
    """
    CREATE TABLE IF NOT EXISTS "case" (
        case_id               TEXT PRIMARY KEY,
        case_code             TEXT NOT NULL DEFAULT '',
        tenant                TEXT NOT NULL DEFAULT '',
        category              TEXT NOT NULL DEFAULT '',
        subcategory           TEXT NOT NULL DEFAULT '',
        title                 TEXT NOT NULL DEFAULT '',
        summary               TEXT NOT NULL DEFAULT '',
        phenomenon            TEXT NOT NULL DEFAULT '',
        root_cause_text       TEXT NOT NULL DEFAULT '',
        countermeasure_text   TEXT NOT NULL DEFAULT '',
        defect_mode_code      TEXT NOT NULL DEFAULT '',
        root_cause_code       TEXT NOT NULL DEFAULT '',
        plant                 TEXT NOT NULL DEFAULT '',
        line_code             TEXT NOT NULL DEFAULT '',
        owner_dept            TEXT NOT NULL DEFAULT '',
        cls_rank              INTEGER NOT NULL DEFAULT 0,
        status                TEXT NOT NULL DEFAULT 'draft',
        content_version       INTEGER NOT NULL DEFAULT 1,
        visibility_class      INTEGER NOT NULL DEFAULT 0,
        acl_version           INTEGER NOT NULL DEFAULT 1,
        source_ticket_type    TEXT NOT NULL DEFAULT '',
        source_ticket_id      TEXT NOT NULL DEFAULT '',
        is_export_controlled  INTEGER NOT NULL DEFAULT 0,
        published_at          TEXT NOT NULL DEFAULT '',
        last_verified_at      TEXT NOT NULL DEFAULT '',
        verified              INTEGER NOT NULL DEFAULT 0,
        citation_count        INTEGER NOT NULL DEFAULT 0,
        superseded_by         TEXT NOT NULL DEFAULT '',
        author_role           TEXT NOT NULL DEFAULT '',
        created_at            TEXT NOT NULL DEFAULT ''
    )
    """,
    # ---------- 权威 ACL（governedBy 1:N） ----------
    """
    CREATE TABLE IF NOT EXISTS case_acl (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id     TEXT NOT NULL,
        dim         TEXT NOT NULL,
        value       TEXT NOT NULL DEFAULT '',
        effect      TEXT NOT NULL DEFAULT 'allow',
        origin      TEXT NOT NULL DEFAULT 'inherited',
        expires_at  TEXT NOT NULL DEFAULT '',
        UNIQUE (case_id, dim, value, effect)
    )
    """,
    # ---------- 知识层 chunk（向量库的冗余映射，用于对账） ----------
    """
    CREATE TABLE IF NOT EXISTS case_chunk (
        chunk_id        TEXT PRIMARY KEY,
        case_id         TEXT NOT NULL,
        chunk_type      TEXT NOT NULL,
        content_version INTEGER NOT NULL DEFAULT 1,
        vector_status   TEXT NOT NULL DEFAULT 'pending',
        text_hash       TEXT NOT NULL DEFAULT '',
        indexed_at      TEXT NOT NULL DEFAULT ''
    )
    """,
    # ---------- 证据层（只存元数据，内容在对象存储；检索侧永不读取） ----------
    """
    CREATE TABLE IF NOT EXISTS case_evidence (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        evidence_id TEXT NOT NULL UNIQUE,
        case_id     TEXT NOT NULL,
        kind        TEXT NOT NULL DEFAULT '',
        uri         TEXT NOT NULL DEFAULT '',
        cls_rank    INTEGER NOT NULL DEFAULT 3,
        retain_until TEXT NOT NULL DEFAULT ''
    )
    """,
    # ---------- 跨部门访问申请（授权 + 审批；不给它单独造一套判定）----------
    """
    CREATE TABLE IF NOT EXISTS case_access_request (
        request_id     TEXT PRIMARY KEY,
        case_id        TEXT NOT NULL,
        requester_id   TEXT NOT NULL,
        requester_dept TEXT NOT NULL DEFAULT '',
        requester_role TEXT NOT NULL DEFAULT '',
        reason         TEXT NOT NULL DEFAULT '',
        scope          TEXT NOT NULL DEFAULT 'grant',
        status         TEXT NOT NULL DEFAULT 'pending',
        approver_id    TEXT NOT NULL DEFAULT '',
        decision_reason TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL DEFAULT '',
        decided_at     TEXT NOT NULL DEFAULT '',
        expires_at     TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_case_access_pending "
    "ON case_access_request (case_id, requester_id) WHERE status='pending'",
    "CREATE INDEX IF NOT EXISTS idx_case_access_status "
    "ON case_access_request (status, created_at)",

    # ---------- 本体缺口登记申请（FR-09：术语未登记时的治理入口）----------
    # 为什么单独一张表而不是复用审计：审计纪律是"只记哈希、不记明文"
    # （见 case_audit.FORBIDDEN_DETAIL_KEYS），而登记申请必须能看到"申请的是什么词"，
    # 否则审批人无从裁决。二者是两条不同的治理链，各自保持单一职责。
    """
    CREATE TABLE IF NOT EXISTS case_ontology_gap (
        gap_id          TEXT PRIMARY KEY,
        vocab           TEXT NOT NULL DEFAULT 'defect_mode',
        term            TEXT NOT NULL DEFAULT '',
        code_proposed   TEXT NOT NULL DEFAULT '',
        phenomenon_hint TEXT NOT NULL DEFAULT '',
        source          TEXT NOT NULL DEFAULT 'manual',
        requester_id    TEXT NOT NULL DEFAULT '',
        requester_dept  TEXT NOT NULL DEFAULT '',
        requester_role  TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'pending',
        approver_id     TEXT NOT NULL DEFAULT '',
        decision_reason TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT '',
        decided_at      TEXT NOT NULL DEFAULT ''
    )
    """,
    # 同一词表下同一个词只允许一条待审申请（防刷单；已裁决的可再申请）
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ontology_gap_pending "
    "ON case_ontology_gap (vocab, term) WHERE status='pending'",
    "CREATE INDEX IF NOT EXISTS idx_ontology_gap_status "
    "ON case_ontology_gap (status, created_at)",

    # ---------- 审计（只追加 + hash chain） ----------
    """
    CREATE TABLE IF NOT EXISTS case_audit (
        seq         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL DEFAULT '',
        actor       TEXT NOT NULL DEFAULT '',
        actor_kind  TEXT NOT NULL DEFAULT 'human',
        action      TEXT NOT NULL DEFAULT '',
        case_id     TEXT NOT NULL DEFAULT '',
        chunk_id    TEXT NOT NULL DEFAULT '',
        query_hash  TEXT NOT NULL DEFAULT '',
        filter_hash TEXT NOT NULL DEFAULT '',
        hit_count   INTEGER NOT NULL DEFAULT 0,
        hidden_count INTEGER NOT NULL DEFAULT 0,
        prev_hash   TEXT NOT NULL DEFAULT '',
        row_hash    TEXT NOT NULL DEFAULT '',
        detail      TEXT NOT NULL DEFAULT ''
    )
    """,
    'CREATE INDEX IF NOT EXISTS idx_case_status ON "case" (status)',
    'CREATE INDEX IF NOT EXISTS idx_case_tenant_status ON "case" (tenant, status)',
    'CREATE INDEX IF NOT EXISTS idx_case_defect ON "case" (defect_mode_code)',
    "CREATE INDEX IF NOT EXISTS idx_case_acl_case ON case_acl (case_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_chunk_case ON case_chunk (case_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_evidence_case ON case_evidence (case_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_audit_ts ON case_audit (ts)",
)

CASE_TABLES: tuple[str, ...] = (
    "case", "case_acl", "case_chunk", "case_evidence", "case_access_request",
    "case_ontology_gap", "case_audit",
)

#: MySQL 8 版 schema。与 ``CASE_DDL`` **语义等价**，差异只来自方言：
#:
#: - ``TEXT`` 在 MySQL 不允许字面量默认值 → 内容列用 ``LONGTEXT NOT NULL DEFAULT ('')``
#:   （8.0.13+ 表达式默认值，空串语义与 SQLite 一致）；
#: - ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``BIGINT AUTO_INCREMENT PRIMARY KEY``；
#: - 部分索引（``WHERE status='pending'``）MySQL 不支持 → 用**函数唯一索引**等价：
#:   表达式在非 pending 时返回 NULL（唯一索引允许多个 NULL），语义与部分索引一致；
#: - 索引全部内联在 ``CREATE TABLE IF NOT EXISTS`` 里，保证重复调用幂等。
CASE_DDL_MYSQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS `case` (
        case_id               VARCHAR(64) NOT NULL,
        case_code             VARCHAR(64) NOT NULL DEFAULT '',
        tenant                VARCHAR(64) NOT NULL DEFAULT '',
        category              VARCHAR(64) NOT NULL DEFAULT '',
        subcategory           VARCHAR(64) NOT NULL DEFAULT '',
        title                 VARCHAR(255) NOT NULL DEFAULT '',
        summary               LONGTEXT NOT NULL DEFAULT (''),
        phenomenon            LONGTEXT NOT NULL DEFAULT (''),
        root_cause_text       LONGTEXT NOT NULL DEFAULT (''),
        countermeasure_text   LONGTEXT NOT NULL DEFAULT (''),
        defect_mode_code      VARCHAR(64) NOT NULL DEFAULT '',
        root_cause_code       VARCHAR(64) NOT NULL DEFAULT '',
        plant                 VARCHAR(64) NOT NULL DEFAULT '',
        line_code             VARCHAR(64) NOT NULL DEFAULT '',
        owner_dept            VARCHAR(64) NOT NULL DEFAULT '',
        cls_rank              INT NOT NULL DEFAULT 0,
        status                VARCHAR(32) NOT NULL DEFAULT 'draft',
        content_version       INT NOT NULL DEFAULT 1,
        visibility_class      INT NOT NULL DEFAULT 0,
        acl_version           INT NOT NULL DEFAULT 1,
        source_ticket_type    VARCHAR(64) NOT NULL DEFAULT '',
        source_ticket_id      VARCHAR(64) NOT NULL DEFAULT '',
        is_export_controlled  TINYINT NOT NULL DEFAULT 0,
        published_at          VARCHAR(40) NOT NULL DEFAULT '',
        last_verified_at      VARCHAR(40) NOT NULL DEFAULT '',
        verified              TINYINT NOT NULL DEFAULT 0,
        citation_count        INT NOT NULL DEFAULT 0,
        superseded_by         VARCHAR(64) NOT NULL DEFAULT '',
        author_role           VARCHAR(64) NOT NULL DEFAULT '',
        created_at            VARCHAR(40) NOT NULL DEFAULT '',
        PRIMARY KEY (case_id),
        KEY idx_case_status (status),
        KEY idx_case_tenant_status (tenant, status),
        KEY idx_case_defect (defect_mode_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS case_acl (
        id          BIGINT NOT NULL AUTO_INCREMENT,
        case_id     VARCHAR(64) NOT NULL,
        dim         VARCHAR(32) NOT NULL,
        value       VARCHAR(128) NOT NULL DEFAULT '',
        effect      VARCHAR(16) NOT NULL DEFAULT 'allow',
        origin      VARCHAR(32) NOT NULL DEFAULT 'inherited',
        expires_at  VARCHAR(40) NOT NULL DEFAULT '',
        PRIMARY KEY (id),
        UNIQUE KEY uq_case_acl (case_id, dim, value, effect),
        KEY idx_case_acl_case (case_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS case_chunk (
        chunk_id        VARCHAR(128) NOT NULL,
        case_id         VARCHAR(64) NOT NULL,
        chunk_type      VARCHAR(64) NOT NULL,
        content_version INT NOT NULL DEFAULT 1,
        vector_status   VARCHAR(32) NOT NULL DEFAULT 'pending',
        text_hash       VARCHAR(128) NOT NULL DEFAULT '',
        indexed_at      VARCHAR(40) NOT NULL DEFAULT '',
        PRIMARY KEY (chunk_id),
        KEY idx_case_chunk_case (case_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS case_evidence (
        id           BIGINT NOT NULL AUTO_INCREMENT,
        evidence_id  VARCHAR(128) NOT NULL,
        case_id      VARCHAR(64) NOT NULL,
        kind         VARCHAR(64) NOT NULL DEFAULT '',
        uri          VARCHAR(1024) NOT NULL DEFAULT '',
        cls_rank     INT NOT NULL DEFAULT 3,
        retain_until VARCHAR(40) NOT NULL DEFAULT '',
        PRIMARY KEY (id),
        UNIQUE KEY uq_case_evidence (evidence_id),
        KEY idx_case_evidence_case (case_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS case_access_request (
        request_id      VARCHAR(64) NOT NULL,
        case_id         VARCHAR(64) NOT NULL,
        requester_id    VARCHAR(64) NOT NULL,
        requester_dept  VARCHAR(64) NOT NULL DEFAULT '',
        requester_role  VARCHAR(128) NOT NULL DEFAULT '',
        reason          LONGTEXT NOT NULL DEFAULT (''),
        scope           VARCHAR(32) NOT NULL DEFAULT 'grant',
        status          VARCHAR(32) NOT NULL DEFAULT 'pending',
        approver_id     VARCHAR(64) NOT NULL DEFAULT '',
        decision_reason LONGTEXT NOT NULL DEFAULT (''),
        created_at      VARCHAR(40) NOT NULL DEFAULT '',
        decided_at      VARCHAR(40) NOT NULL DEFAULT '',
        expires_at      VARCHAR(40) NOT NULL DEFAULT '',
        PRIMARY KEY (request_id),
        UNIQUE KEY uq_case_access_pending
            ((CASE WHEN status='pending'
                   THEN CONCAT(case_id, '|', requester_id) ELSE NULL END)),
        KEY idx_case_access_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS case_ontology_gap (
        gap_id          VARCHAR(64) NOT NULL,
        vocab           VARCHAR(32) NOT NULL DEFAULT 'defect_mode',
        term            VARCHAR(255) NOT NULL DEFAULT '',
        code_proposed   VARCHAR(64) NOT NULL DEFAULT '',
        phenomenon_hint VARCHAR(1000) NOT NULL DEFAULT '',
        source          VARCHAR(32) NOT NULL DEFAULT 'manual',
        requester_id    VARCHAR(64) NOT NULL DEFAULT '',
        requester_dept  VARCHAR(64) NOT NULL DEFAULT '',
        requester_role  VARCHAR(128) NOT NULL DEFAULT '',
        status          VARCHAR(32) NOT NULL DEFAULT 'pending',
        approver_id     VARCHAR(64) NOT NULL DEFAULT '',
        decision_reason LONGTEXT NOT NULL DEFAULT (''),
        created_at      VARCHAR(40) NOT NULL DEFAULT '',
        decided_at      VARCHAR(40) NOT NULL DEFAULT '',
        PRIMARY KEY (gap_id),
        UNIQUE KEY uq_ontology_gap_pending
            ((CASE WHEN status='pending' THEN CONCAT(vocab, '|', term) ELSE NULL END)),
        KEY idx_ontology_gap_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS case_audit (
        seq          BIGINT NOT NULL AUTO_INCREMENT,
        ts           VARCHAR(40) NOT NULL DEFAULT '',
        actor        VARCHAR(128) NOT NULL DEFAULT '',
        actor_kind   VARCHAR(32) NOT NULL DEFAULT 'human',
        action       VARCHAR(128) NOT NULL DEFAULT '',
        case_id      VARCHAR(64) NOT NULL DEFAULT '',
        chunk_id     VARCHAR(128) NOT NULL DEFAULT '',
        query_hash   VARCHAR(64) NOT NULL DEFAULT '',
        filter_hash  VARCHAR(64) NOT NULL DEFAULT '',
        hit_count    INT NOT NULL DEFAULT 0,
        hidden_count INT NOT NULL DEFAULT 0,
        prev_hash    VARCHAR(64) NOT NULL DEFAULT '',
        row_hash     VARCHAR(64) NOT NULL DEFAULT '',
        detail       LONGTEXT NOT NULL DEFAULT (''),
        PRIMARY KEY (seq),
        KEY idx_case_audit_ts (ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


def is_mysql_target(target: Union[str, Path]) -> bool:
    """目标是否是 MySQL URL（``mysql+pymysql://`` / ``mysql://``）。"""
    raw = str(target).strip().lower()
    return raw.startswith(("mysql://", "mysql+pymysql://", "mysql+mysqldb://"))


def resolve_case_db(path: Optional[Union[str, Path]] = None) -> Union[Path, str]:
    """解析案例库目标（SQLite 文件路径 或 MySQL URL）。

    - ``None`` → ``settings.CASE_DB``；
    - ``sqlite:///xxx`` → 去掉 scheme 后的文件路径（与既有 ``WORKFLOW_DB`` 口径一致）；
    - ``mysql+pymysql://...`` → 原样返回 URL（由 :class:`MySQLCaseDatabase` 消费）；
    - 裸路径 → 原样（相对路径按当前工作目录解析）。
    """
    if path is None:
        from .config import settings

        path = settings.CASE_DB
    raw = str(path).strip()
    if is_mysql_target(raw):
        return raw
    if raw in (":memory:", "file::memory:"):
        return Path(":memory:")
    if raw.startswith("sqlite:///"):
        raw = raw[len("sqlite:///"):]
    return Path(raw)


#: 进程级"批量导入"开关。为真时 :func:`connect` 关闭 fsync 与回滚日志。
#: **只允许在种子导入这种"崩了重跑即可"的路径里打开**——审计链写入依赖
#: 持久性，绝不能在那种路径上关。
_BULK_LOAD = threading.local()


def bulk_load(enabled: bool = True):
    """上下文管理器：临时关闭 fsync（仅用于可重放的批量导入）。"""
    @contextmanager
    def _cm():
        previous = getattr(_BULK_LOAD, "on", False)
        _BULK_LOAD.on = enabled
        try:
            yield
        finally:
            _BULK_LOAD.on = previous

    return _cm()


def _bulk_load_active() -> bool:
    return bool(getattr(_BULK_LOAD, "on", False))


class CaseDatabase(ABC):
    """案例库连接 / schema 契约（SQLite 默认；MySQL 按 CASE_DB 方言分派）。"""

    @abstractmethod
    def connect(self, db_path: Optional[Union[str, Path]] = None) -> Any:
        """打开连接（调用方负责关闭）。SQLite 返回 sqlite3.Connection，
        MySQL 返回同形态的 DB-API 兼容连接。"""

    @abstractmethod
    def ensure_schema(self, db_path: Optional[Union[str, Path]] = None
                      ) -> Union[Path, str]:
        """建表（幂等），返回实际库路径 / URL。"""


class SQLiteCaseDatabase(CaseDatabase):
    """SQLite 实现（案例域关系库）。"""

    def connect(self, db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
        target = resolve_case_db(db_path)
        if is_mysql_target(target):
            raise ValueError("CASE_DB 是 MySQL URL：请走 MySQLCaseDatabase / connect() 分派")
        text = str(target)
        if text != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(text, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if _bulk_load_active():
            # 仅批量导入期间：关 fsync + 用内存日志。崩溃可重放，所以可接受。
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA journal_mode = MEMORY")
        return conn

    def ensure_schema(self, db_path: Optional[Union[str, Path]] = None) -> Path:
        target = resolve_case_db(db_path)
        if is_mysql_target(target):
            raise ValueError("CASE_DB 是 MySQL URL：请走 MySQLCaseDatabase / ensure_case_schema() 分派")
        if str(target) != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), check_same_thread=False)
        try:
            for ddl in CASE_DDL:
                conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()
        logger.debug("[case_db] 案例库 schema 就绪: %s", target)
        return target


# ---------- MySQL 实现 ----------

_SQL_INSERT_OR_REPLACE = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.I)
_SQL_ON_CONFLICT = re.compile(
    r"ON\s+CONFLICT\s*\([^)]*\)\s+DO\s+UPDATE\s+SET\s+", re.I)
_SQL_EXCLUDED = re.compile(
    r"([A-Za-z_]\w*)\s*=\s*excluded\.([A-Za-z_]\w*)", re.I)


def to_mysql_sql(sql: str) -> str:
    """SQLite SQL → MySQL 8 可执行 SQL（只覆盖案例域实际用到的差异）。

    三项差异：
    1. ``INSERT OR REPLACE INTO`` → ``REPLACE INTO``；
    2. ``ON CONFLICT(...) DO UPDATE SET x=excluded.x`` →
       ``ON DUPLICATE KEY UPDATE x=VALUES(x)``；
    3. 双引号标识符 ``"case"`` → 反引号（``case`` 在 MySQL 是保留字），
       占位符 ``?`` → ``%s``（pymysql paramstyle）。
    """
    out = _SQL_INSERT_OR_REPLACE.sub("REPLACE INTO", str(sql))
    if _SQL_ON_CONFLICT.search(out):
        out = _SQL_ON_CONFLICT.sub("ON DUPLICATE KEY UPDATE ", out)
        out = _SQL_EXCLUDED.sub(r"\1=VALUES(\2)", out)
    out = out.replace('"case"', "`case`")
    return out.replace("?", "%s")


class CaseRow(dict):
    """同时支持按列名与按序号取值（对齐 sqlite3.Row 的两种读法）。"""

    def __init__(self, names: list[str], values: list[Any]) -> None:
        super().__init__(zip(names, values))
        self._values = tuple(values)

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class _MySQLCursor:
    """pymysql cursor 的最小包装：SQLite 风格的 fetchone/fetchall/rowcount。"""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount or 0)

    @property
    def lastrowid(self) -> Any:
        return self._cursor.lastrowid

    def _wrap(self, row: Any) -> Optional[CaseRow]:
        if row is None:
            return None
        names = [d[0] for d in (self._cursor.description or ())]
        return CaseRow(names, list(row))

    def fetchone(self) -> Optional[CaseRow]:
        return self._wrap(self._cursor.fetchone())

    def fetchall(self) -> list[CaseRow]:
        return [self._wrap(r) for r in self._cursor.fetchall() if r is not None]


class MySQLCaseConnection:
    """pymysql 连接的最小 DB-API 兼容层。

    案例域既有 SQL 全部保持原样（只有少量方言改写集中在 :func:`to_mysql_sql`），
    避免为迁移再复制一套业务逻辑。
    """

    def __init__(self, url: str) -> None:
        import pymysql
        from pymysql.constants import CLIENT
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        self._conn = pymysql.connect(
            host=parsed.host or "127.0.0.1",
            port=int(parsed.port or 3306),
            user=parsed.username or "",
            password=parsed.password or "",
            database=parsed.database or "",
            charset=str(parsed.query.get("charset") or "utf8mb4"),
            autocommit=False,
            # 与 SQLite 的 rowcount 语义对齐：返回**匹配**行数而非变更行数
            # （否则 update_fields 在"值未变化"时会误判为案例不存在）
            client_flag=CLIENT.FOUND_ROWS,
        )

    def execute(self, sql: str, params: Any = None) -> _MySQLCursor:
        cur = self._conn.cursor()
        cur.execute(to_mysql_sql(sql), params)
        return _MySQLCursor(cur)

    def executemany(self, sql: str, seq_of_params: Any) -> _MySQLCursor:
        cur = self._conn.cursor()
        cur.executemany(to_mysql_sql(sql), seq_of_params)
        return _MySQLCursor(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    # ---------- 跨实例串行化（审计链） ----------

    def lock_chain(self, name: str = "mfg_case_audit", timeout_s: int = 10) -> bool:
        """MySQL 命名锁：多实例并发追加审计时保证链首尾相接。"""
        cur = self._conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (str(name), int(timeout_s)))
        row = cur.fetchone()
        return bool(row and row[0] == 1)

    def unlock_chain(self, name: str = "mfg_case_audit") -> None:
        cur = self._conn.cursor()
        cur.execute("SELECT RELEASE_LOCK(%s)", (str(name),))

    def __enter__(self) -> "MySQLCaseConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


class MySQLCaseDatabase(CaseDatabase):
    """MySQL 8 实现（``CASE_DB`` 显式配置为 MySQL URL 时启用）。"""

    def connect(self, db_path: Optional[Union[str, Path]] = None
                ) -> MySQLCaseConnection:
        target = resolve_case_db(db_path)
        if not is_mysql_target(target):
            raise ValueError(f"CASE_DB 不是 MySQL URL: {target!r}")
        return MySQLCaseConnection(str(target))

    def ensure_schema(self, db_path: Optional[Union[str, Path]] = None
                      ) -> str:
        target = resolve_case_db(db_path)
        if not is_mysql_target(target):
            raise ValueError(f"CASE_DB 不是 MySQL URL: {target!r}")
        conn = MySQLCaseConnection(str(target))
        try:
            for ddl in CASE_DDL_MYSQL:
                conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()
        logger.debug("[case_db] 案例库 MySQL schema 就绪: %s", target)
        return str(target)


_DEFAULT_DATABASE = SQLiteCaseDatabase()


def default_case_database() -> CaseDatabase:
    """默认案例库（进程内单例；测试可替换返回值之外不持有状态）。"""
    return _DEFAULT_DATABASE


def case_database_for(target: Union[str, Path]) -> CaseDatabase:
    """按目标方言选实现：MySQL URL → MySQLCaseDatabase，其余 → SQLite。"""
    return MySQLCaseDatabase() if is_mysql_target(target) else _DEFAULT_DATABASE


def connect(db_path: Optional[Union[str, Path]] = None) -> Any:
    """按目标方言打开连接（SQLite 默认；MySQL URL 自动分派）。"""
    target = resolve_case_db(db_path)
    return case_database_for(target).connect(target)


def ensure_case_schema(db_path: Optional[Union[str, Path]] = None
                       ) -> Union[Path, str]:
    """按目标方言建表（幂等）。"""
    target = resolve_case_db(db_path)
    return case_database_for(target).ensure_schema(target)
