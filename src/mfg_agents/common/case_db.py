"""案例库连接与 schema 契约（D15 迁移第 4 项）。

``case_schema`` 原先直接持有 ``sqlite3.connect``；本契约把**连接创建**与
**建表**收敛到 ``CaseDatabase`` / ``SQLiteCaseDatabase``：

- ``case_schema.connect`` / ``ensure_case_schema`` 变为门面，保持旧导入路径；
- ``bulk_load``（批量导入时关闭 fsync）随连接实现一起下沉，旧路径继续 re-export；
- 案例业务 SQL 仍在 ``case_store`` / ``case_audit`` / ``case_ontology_gap`` ——
  它们各自是行级仓储的后续迁移对象（SQLite 方言：``ON CONFLICT`` / 部分索引），
  本轮只收敛连接与 schema，不假装已完成 MySQL 化。

为什么关系库是真相源、向量库是可重建派生索引：见 ``case_schema`` 模块 docstring
与架构 §6.2；本文件只搬持久化边界，不改语义。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CASE_DB",
    "CASE_TABLES",
    "CASE_DDL",
    "CaseDatabase",
    "SQLiteCaseDatabase",
    "default_case_database",
    "resolve_case_db",
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


def resolve_case_db(path: Optional[Union[str, Path]] = None) -> Path:
    """解析案例库文件路径（``:memory:`` 原样返回为字符串）。

    - ``None`` → ``settings.CASE_DB``；
    - ``sqlite:///xxx`` → 去掉 scheme 后的文件路径（与既有 ``WORKFLOW_DB`` 口径一致）；
    - 裸路径 → 原样（相对路径按当前工作目录解析）。
    """
    if path is None:
        from .config import settings

        path = settings.CASE_DB
    raw = str(path).strip()
    if raw in (":memory:", "file::memory:"):
        return Path(":memory:")  # type: ignore[return-value]
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
    """案例库连接 / schema 契约（SQLite 实现先行）。"""

    @abstractmethod
    def connect(self, db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
        """打开连接（调用方负责关闭）。"""

    @abstractmethod
    def ensure_schema(self, db_path: Optional[Union[str, Path]] = None) -> Path:
        """建表（幂等），返回实际库路径。"""


class SQLiteCaseDatabase(CaseDatabase):
    """SQLite 实现（案例域关系库）。"""

    def connect(self, db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
        target = resolve_case_db(db_path)
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


_DEFAULT_DATABASE = SQLiteCaseDatabase()


def default_case_database() -> CaseDatabase:
    """默认案例库（进程内单例；测试可替换返回值之外不持有状态）。"""
    return _DEFAULT_DATABASE


def connect(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """兼容入口：等价于 ``default_case_database().connect(db_path)``。"""
    return default_case_database().connect(db_path)


def ensure_case_schema(db_path: Optional[Union[str, Path]] = None) -> Path:
    """兼容入口：等价于 ``default_case_database().ensure_schema(db_path)``。"""
    return default_case_database().ensure_schema(db_path)
