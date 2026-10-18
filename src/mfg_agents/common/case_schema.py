"""案例域关系库 schema（SQLite）：案例主数据 / ACL / chunk 映射 / 证据元数据 / 审计。

架构位置：§6.2（ER 图）与 §6.1（三库分工）。

**关系库是真相源，向量库是可重建的派生索引。** 因此这里存的是"权威 ACL"与
"脱敏后的知识层全文"（用于索引重建），**不存**任何未脱敏文本，也**不存**证据层内容
（只存 ``uri`` 与 ``cls_rank`` 元数据）。

与既有 ``common/platform_schema.ensure_platform_schema`` 同机制（``CREATE TABLE IF NOT
EXISTS``，可重复调用），不新建迁移框架（架构 G13）。

两处对架构文档的**增补**（已在 docs/case-assistant-demo-readme.md 记录）：
- ``case.visibility_class`` / ``case.acl_version`` 落表（架构 §6.2 ER 有，任务清单未列）；
- ``case.pipeline_stage`` 不落表（MVP 用 ``status`` 单一口径，避免"授权只认 status"
  这条纪律被第二套状态字段稀释）。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CASE_DB",
    "CASE_TABLES",
    "CASE_DDL",
    "resolve_case_db",
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
    "case_audit",
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


def connect(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """打开案例库连接（``check_same_thread=False``：供 FastAPI 线程池复用）。"""
    target = resolve_case_db(db_path)
    text = str(target)
    if text != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(text, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_case_schema(db_path: Optional[Union[str, Path]] = None) -> Path:
    """建表（**可重复调用**，幂等）。

    Returns:
        实际使用的库文件路径（``:memory:`` 返回 ``Path(":memory:")``）。
    """
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
    logger.debug("[case_schema] 案例库 schema 就绪: %s", target)
    return target
