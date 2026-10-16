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
]

_METRIC_UPSERT = (
    "INSERT OR REPLACE INTO platform_metric_def "
    "(id, name, owner, description, tables_json, keywords_json, sql, unit, version, updated_at) "
    "VALUES (:id, :name, :owner, :desc, :tables, :keywords, :sql, :unit, 1, :now)"
)


def sqlite_path_from_url(db_url: str) -> str:
    """DB_URL → sqlite 文件路径（sqlite:///path 或裸路径均可）。"""
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    if db_url.startswith("sqlite://"):
        return db_url[len("sqlite://"):]
    return db_url


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

    SQLite（含 :memory:）：原生 sqlite3，保证与 WorkflowEngine 同一库；
    其他（MySQL 等）：SQLAlchemy。
    """
    if "sqlite" in db_url:
        ensure_platform_schema_sqlite(db_url)
        return
    engine = create_engine(db_url)
    with engine.begin() as conn:
        for ddl in PLATFORM_DDL:
            conn.execute(text(ddl))
        for row in _load_metric_rows():
            conn.execute(text(_METRIC_UPSERT.replace(":", ":")), dict(
                id=row[0], name=row[1], owner=row[2], desc=row[3],
                tables=row[4], keywords=row[5], sql=row[6], unit=row[7],
                now=row[8]))
    logger.info("[platform] 平台库就绪: %s", db_url)


def ensure_platform_schema_sqlite(db_url_or_conn) -> None:
    """在原生 sqlite3 连接（或 sqlite URL）上建表 + 播种指标库。

    接受 sqlite3.Connection（:memory: 持久连接场景）或 sqlite URL（文件库）。
    """
    owns_conn = isinstance(db_url_or_conn, str)
    if owns_conn:
        conn = sqlite3.connect(sqlite_path_from_url(db_url_or_conn))
    else:
        conn = db_url_or_conn
    try:
        for ddl in PLATFORM_DDL:
            conn.execute(ddl)
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
