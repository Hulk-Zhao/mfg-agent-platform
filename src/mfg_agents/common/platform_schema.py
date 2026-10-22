"""平台库 DDL 与指标库种子（V2 新增，V1 八张模拟表零改动）。

表：
- platform_workflow_task：工作流工单（状态机 + SLA + HITL 待办队列）
- platform_metric_def    ：指标库（指标定义即代码，Text2SQL 第①道通道的数据面）

用法：WorkflowEngine 初始化时调用 ensure_platform_schema(db_url)（幂等）。
指标种子来自 v2/data/metrics/*.yaml（与代码共用同一份定义源，避免双源漂移）。

BUG-4 修复：SQLite 路径的 DDL/种子改用**原生 sqlite3**（与 WorkflowEngine
的 DML 连接同一文件/同一内存库）；仅 MySQL 等非 SQLite URL 走 SQLAlchemy。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

PLATFORM_DDL = [
    """
    CREATE TABLE IF NOT EXISTS platform_workflow_task (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_uid TEXT NOT NULL UNIQUE,
      wf_id TEXT NOT NULL,
      title TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      priority TEXT NOT NULL DEFAULT 'P1',
      assignees TEXT DEFAULT '',
      context_json TEXT DEFAULT '{}',
      hitl_prompt TEXT DEFAULT '',
      history_json TEXT DEFAULT '[]',
      governance_json TEXT DEFAULT '{}',
      owner_scope TEXT DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      sla_due_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_metric_def (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      owner TEXT DEFAULT '',
      description TEXT DEFAULT '',
      tables_json TEXT DEFAULT '[]',
      keywords_json TEXT DEFAULT '[]',
      sql TEXT NOT NULL,
      unit TEXT DEFAULT '',
      version INTEGER DEFAULT 1,
      updated_at TEXT NOT NULL
    )
    """,
    # 签名事实表（会签/审批的独立落地表）：把"谁在什么角色上签了什么"从
    # governance_json 内嵌数组提升为独立表，重复提交由 DB 唯一索引兜底——
    # 进程内锁只能防单实例并发，唯一索引才能防多实例/多连接并发（见 README 生产化）。
    """
    CREATE TABLE IF NOT EXISTS wf_signoff (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_uid TEXT NOT NULL,
      role_key TEXT NOT NULL,
      role_name TEXT DEFAULT '',
      actor TEXT NOT NULL DEFAULT '',
      actor_id TEXT NOT NULL DEFAULT '',
      actor_kind TEXT DEFAULT 'human',
      verb TEXT NOT NULL DEFAULT 'cosign',
      decision TEXT DEFAULT '',
      comment TEXT DEFAULT '',
      on_behalf_of TEXT,
      overdue_signoff INTEGER DEFAULT 0,
      ts TEXT NOT NULL
    )
    """,
    # 幂等：同工单 + 同角色 + 同一人 + 同动作只能有一行（重复提交 → IntegrityError）。
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_wf_signoff_role
      ON wf_signoff (task_uid, role_key, actor, verb)
    """,
    # 一人一签（SoD）：同一自然人不得在同一工单内会签两次（跨角色也不行）。
    # 判定用 actor_id（稳定用户标识），不按姓名——重名/改名/伪造姓名都不可靠。
    # 当前仅落 cosign 行，故等价于 (task_uid, actor_id) 唯一；未来若把 approve/reject
    # 也写入本表，需注意同一人重复终批会被该索引拦下（业务上是否允许需先定义）。
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_wf_signoff_sod
      ON wf_signoff (task_uid, actor_id, verb)
    """,
]

_METRIC_UPSERT = (
    "INSERT OR REPLACE INTO platform_metric_def "
    "(id, name, owner, description, tables_json, keywords_json, sql, unit, version, updated_at) "
    "VALUES (:id, :name, :owner, :desc, :tables, :keywords, :sql, :unit, 1, :now)"
)

#: MySQL 版指标播种（``INSERT OR REPLACE`` 是 SQLite 方言；MySQL 用 ON DUPLICATE KEY）。
_METRIC_UPSERT_MYSQL = (
    "INSERT INTO platform_metric_def "
    "(id, name, owner, description, tables_json, keywords_json, `sql`, unit, version, updated_at) "
    "VALUES (:id, :name, :owner, :desc, :tables, :keywords, :sql, :unit, 1, :now) "
    "ON DUPLICATE KEY UPDATE name=VALUES(name), owner=VALUES(owner), "
    "description=VALUES(description), tables_json=VALUES(tables_json), "
    "keywords_json=VALUES(keywords_json), `sql`=VALUES(`sql`), unit=VALUES(unit), "
    "version=VALUES(version), updated_at=VALUES(updated_at)"
)

#: MySQL / InnoDB 平台表（D15 MySQL 参考实现的前置）：SQLite 的 AUTOINCREMENT、
#: TEXT 默认值与 ``CREATE INDEX IF NOT EXISTS`` 在 MySQL 均不合法，必须单独一份。
#: 长度说明：``task_uid`` / ``role_key`` / ``actor`` / ``verb`` 参与唯一索引，
#: utf8mb4 下总字节数需 < 3072（InnoDB 大前缀），当前 1152 / 896 均安全。
MYSQL_PLATFORM_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS platform_workflow_task (
      id BIGINT NOT NULL AUTO_INCREMENT,
      task_uid VARCHAR(64) NOT NULL,
      wf_id VARCHAR(128) NOT NULL,
      title VARCHAR(512) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'pending',
      priority VARCHAR(16) NOT NULL DEFAULT 'P1',
      assignees TEXT,
      context_json LONGTEXT,
      hitl_prompt TEXT,
      history_json LONGTEXT,
      governance_json LONGTEXT,
      owner_scope TEXT,
      created_at VARCHAR(40) NOT NULL,
      updated_at VARCHAR(40) NOT NULL,
      sla_due_at VARCHAR(40) NULL,
      PRIMARY KEY (id),
      UNIQUE KEY ux_workflow_task_uid (task_uid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_metric_def (
      id VARCHAR(80) NOT NULL,
      name VARCHAR(255) NOT NULL,
      owner VARCHAR(128),
      description TEXT,
      tables_json TEXT,
      keywords_json TEXT,
      `sql` LONGTEXT NOT NULL,
      unit VARCHAR(32),
      version INT DEFAULT 1,
      updated_at VARCHAR(40) NOT NULL,
      PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS wf_signoff (
      id BIGINT NOT NULL AUTO_INCREMENT,
      task_uid VARCHAR(64) NOT NULL,
      role_key VARCHAR(64) NOT NULL,
      role_name VARCHAR(128),
      actor VARCHAR(128) NOT NULL,
      actor_id VARCHAR(128) NOT NULL,
      actor_kind VARCHAR(32),
      verb VARCHAR(32) NOT NULL,
      decision VARCHAR(255),
      comment TEXT,
      on_behalf_of VARCHAR(128) NULL,
      overdue_signoff INT DEFAULT 0,
      ts VARCHAR(40) NOT NULL,
      PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)

#: MySQL 索引（MySQL 不支持 ``CREATE INDEX IF NOT EXISTS``，先查 information_schema）。
MYSQL_PLATFORM_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("wf_signoff", "ux_wf_signoff_role",
     "CREATE UNIQUE INDEX ux_wf_signoff_role "
     "ON wf_signoff (task_uid, role_key, actor, verb)"),
    ("wf_signoff", "ux_wf_signoff_sod",
     "CREATE UNIQUE INDEX ux_wf_signoff_sod "
     "ON wf_signoff (task_uid, actor_id, verb)"),
)

# 幂等加列清单（表名 → {列名: 列定义}）。
# 治理层（多人会签）新增 governance_json；老演示库已有历史工单，
# **不允许** DROP 重建，只允许 PRAGMA 探测后 ALTER TABLE ADD COLUMN。
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "platform_workflow_task": {
        "governance_json": "TEXT DEFAULT '{}'",
        # R9 行级归属（D13）：岗位/部门/厂区 JSON；空 = 未纳管存量（保持既有可见性）
        "owner_scope": "TEXT DEFAULT '{}'",
    },
}

#: MySQL 版补列定义：TEXT 列不允许 DEFAULT（8.0.13 前）且这里只做老库升级，
#: 新表已含两列；读侧用 ``or "{}"`` 兜底 NULL。
_ADDITIVE_COLUMNS_MYSQL: dict[str, dict[str, str]] = {
    "platform_workflow_task": {
        "governance_json": "LONGTEXT NULL",
        "owner_scope": "TEXT NULL",
    },
}


def _existing_columns_sqlite(conn: sqlite3.Connection, table: str) -> set[str]:
    """SQLite：读取表的现有列名集合（表不存在返回空集）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    """幂等迁移：缺列则 ALTER TABLE ADD COLUMN（保留历史数据）。"""
    for table, columns in _ADDITIVE_COLUMNS.items():
        existing = _existing_columns_sqlite(conn, table)
        if not existing:
            continue  # 表刚由 DDL 创建（已含新列）或不存在
        for column, definition in columns.items():
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            logger.info("[platform] 迁移加列: %s.%s", table, column)


def sqlite_path_from_url(db_url: str) -> str:
    """DB_URL → sqlite 文件路径（sqlite:///path 或裸路径均可）。"""
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    if db_url.startswith("sqlite://"):
        return db_url[len("sqlite://"):]
    return db_url


def is_sqlite_target(db_url: str) -> bool:
    """判断目标是否为 SQLite：``sqlite://`` 协议头，或裸文件路径 / ``:memory:``。

    这是全域**唯一的方言判定入口**，供 ``ensure_platform_schema`` 与
    ``WorkflowEngine.assert_sqlite_backed`` 共用，避免两处规则漂移。

    为什么不能写 ``"sqlite" in db_url``（历史写法，已修复）：
    该子串判定会把裸路径 ``/tmp/platform.db`` 误判为非 SQLite，转而交给
    SQLAlchemy 的 ``create_engine()``，得到
    ``ArgumentError: Could not parse SQLAlchemy URL``——一个纯本地路径却报
    "URL 解析失败"，排查方向完全跑偏。反之，路径里恰好含 "sqlite" 字样
    （如 ``/opt/sqlite-data/x.db``）又会被误判为 SQLite。子串匹配两边都错。
    """
    if "://" not in db_url:
        return True  # 裸文件路径 / :memory:
    scheme = db_url.split("://", 1)[0].split("+", 1)[0].strip().lower()
    return scheme == "sqlite"


def _load_metric_rows() -> list[tuple]:
    """从 v2/data/metrics/*.yaml 加载指标种子行（与 MetricLibrary 同源）。"""
    from ..v2.services.text2sql import MetricLibrary  # 延迟导入避免环

    metrics_dir = Path(__file__).resolve().parent.parent / "v2" / "data" / "metrics"
    library = MetricLibrary.load(metrics_dir)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for metric in library._metrics.values():
        rows.append((
            str(metric["id"]), str(metric.get("name", "")),
            str(metric.get("owner", "")), str(metric.get("desc", "")),
            json.dumps(metric.get("tables", []), ensure_ascii=False),
            json.dumps(metric.get("keywords", []), ensure_ascii=False),
            str(metric["sql"]), str(metric.get("unit", "")), now,
        ))
    return rows


def ensure_platform_schema(db_url: str) -> None:
    """建平台表（幂等）+ 播种指标库。

    SQLite（含 :memory:、裸文件路径）：原生 sqlite3，保证与 WorkflowEngine 同一库；
    MySQL：SQLAlchemy + InnoDB 方言 DDL（``AUTOINCREMENT`` / ``TEXT DEFAULT`` /
    ``CREATE INDEX IF NOT EXISTS`` 在 MySQL 不合法，不能复用 SQLite 那份）。
    """
    if is_sqlite_target(db_url):
        ensure_platform_schema_sqlite(db_url)
        return
    engine = create_engine(db_url)
    dialect = engine.dialect.name
    if dialect != "mysql":
        engine.dispose()
        raise NotImplementedError(
            f"平台建表暂只支持 SQLite / MySQL，收到方言 {dialect!r}：{db_url!r}")
    ddl = MYSQL_PLATFORM_DDL
    additive = _ADDITIVE_COLUMNS_MYSQL
    upsert = _METRIC_UPSERT_MYSQL
    try:
        with engine.begin() as conn:
            for statement in ddl:
                conn.execute(text(statement))
            _ensure_mysql_indexes(conn)
            # 非 SQLite（MySQL 等）：用 inspector 探测后幂等加列
            from sqlalchemy import inspect as sa_inspect

            inspector = sa_inspect(engine)
            for table, columns in additive.items():
                try:
                    existing = {c["name"] for c in inspector.get_columns(table)}
                except Exception:  # noqa: BLE001 - 表不存在时跳过
                    continue
                for column, definition in columns.items():
                    if column in existing:
                        continue
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                    logger.info("[platform] 迁移加列: %s.%s", table, column)
            for row in _load_metric_rows():
                conn.execute(text(upsert), dict(
                    id=row[0], name=row[1], owner=row[2], desc=row[3],
                    tables=row[4], keywords=row[5], sql=row[6], unit=row[7],
                    now=row[8]))
    finally:
        engine.dispose()
    logger.info("[platform] 平台库就绪（MySQL）: %s", db_url)


def _ensure_mysql_indexes(conn) -> None:
    """MySQL 不支持 ``CREATE INDEX IF NOT EXISTS``：查 information_schema 后补建。"""
    for table, name, ddl in MYSQL_PLATFORM_INDEXES:
        exists = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.statistics "
                 "WHERE table_schema = DATABASE() AND table_name = :table "
                 "AND index_name = :name"),
            {"table": table, "name": name}).scalar()
        if not exists:
            conn.execute(text(ddl))
            logger.info("[platform] 迁移加索引: %s.%s", table, name)


def ensure_platform_schema_sqlite(db_url_or_conn) -> None:
    """在原生 sqlite3 连接（或 sqlite URL）上建表 + 播种指标库。

    接受 sqlite3.Connection（:memory: 持久连接场景）或 sqlite URL（文件库）。

    父目录不存在时自动创建：``sqlite3.connect`` 不会建目录，而
    ``WorkflowEngine._connect`` 会。历史实现只在前者缺失这一步，导致
    ``WORKFLOW_DB=sqlite:////var/lib/mfg/platform.db``（目录尚未创建）在
    **建表阶段**就抛 ``unable to open database file``，与连接阶段行为不一致。
    此处补齐，让"路径合法即可用"成为统一语义。
    """
    owns_conn = isinstance(db_url_or_conn, str)
    if owns_conn:
        path = sqlite_path_from_url(db_url_or_conn)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    else:
        conn = db_url_or_conn
    try:
        for ddl in PLATFORM_DDL:
            conn.execute(ddl)
        _migrate_sqlite(conn)
        for row in _load_metric_rows():
            conn.execute(_METRIC_UPSERT, {
                "id": row[0], "name": row[1], "owner": row[2], "desc": row[3],
                "tables": row[4], "keywords": row[5], "sql": row[6],
                "unit": row[7], "now": row[8]})
        conn.commit()
        logger.info("[platform] 平台库就绪（sqlite 原生连接）")
    finally:
        if owns_conn:
            conn.close()
