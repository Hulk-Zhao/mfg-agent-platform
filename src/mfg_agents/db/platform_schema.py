"""平台库 DDL 与指标库种子（V2 新增，V1 八张模拟表零改动）。

表：
- platform_workflow_task：工作流工单（状态机 + SLA + HITL 待办队列）
- platform_metric_def    ：指标库（指标定义即代码，Text2SQL 第①道通道的数据面）

用法：WorkflowEngine 初始化时调用 ensure_platform_schema(db_url)（幂等）。
指标种子来自 v2/data/metrics/*.yaml（与代码共用同一份定义源，避免双源漂移）。
"""
from __future__ import annotations

import logging
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


def sqlite_path_from_url(db_url: str) -> str:
    """DB_URL → sqlite 文件路径（sqlite:///path 或裸路径均可）。"""
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    if db_url.startswith("sqlite://"):
        return db_url[len("sqlite://"):]
    return db_url


def ensure_platform_schema(db_url: str) -> None:
    """建平台表（幂等）+ 播种指标库。"""
    engine = create_engine(db_url)
    with engine.begin() as conn:
        for ddl in PLATFORM_DDL:
            conn.execute(text(ddl))
    seed_metric_defs(engine)
    logger.info("[platform] 平台库就绪: %s", db_url)


def seed_metric_defs(engine) -> int:
    """把 v2/data/metrics/*.yaml 的指标定义写入 platform_metric_def。"""
    import json

    from ..v2.services.text2sql import MetricLibrary  # 延迟导入避免环

    metrics_dir = Path(__file__).resolve().parent.parent / "v2" / "data" / "metrics"
    try:
        library = MetricLibrary.load(metrics_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[platform] 指标库加载失败，跳过种子: %s", exc)
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with engine.begin() as conn:
        for metric in library._metrics.values():
            conn.execute(text(
                "INSERT OR REPLACE INTO platform_metric_def "
                "(id, name, owner, description, tables_json, keywords_json, sql, unit, version, updated_at) "
                "VALUES (:id, :name, :owner, :desc, :tables, :keywords, :sql, :unit, 1, :now)"),
                {"id": str(metric["id"]), "name": str(metric.get("name", "")),
                 "owner": str(metric.get("owner", "")),
                 "desc": str(metric.get("desc", "")),
                 "tables": json.dumps(metric.get("tables", []), ensure_ascii=False),
                 "keywords": json.dumps(metric.get("keywords", []), ensure_ascii=False),
                 "sql": str(metric["sql"]), "unit": str(metric.get("unit", "")),
                 "now": now})
            count += 1
    logger.info("[platform] 指标库种子写入 %d 条", count)
    return count
