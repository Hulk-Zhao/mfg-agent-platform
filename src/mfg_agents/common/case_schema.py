"""案例域关系库 schema（SQLite 默认 / MySQL 可选）：案例主数据 / ACL / chunk 映射 / 证据元数据 / 审计。

架构位置：§6.2（ER 图）与 §6.1（三库分工）。

**关系库是真相源，向量库是可重建的派生索引。** 因此这里存的是"权威 ACL"与
"脱敏后的知识层全文"（用于索引重建），**不存**任何未脱敏文本，也**不存**证据层内容
（只存 ``uri`` 与 ``cls_rank`` 元数据）。

D15：连接创建与建表已下沉到 ``common/case_db.py`` 的
``CaseDatabase`` / ``SQLiteCaseDatabase``；本模块保留原导入路径与 docstring，
是案例域的稳定门面（``case_store`` / ``case_audit`` / ``case_ontology_gap``
继续从这里取 ``connect`` / ``ensure_case_schema``）。

与既有 ``common/platform_schema.ensure_platform_schema`` 同机制（``CREATE TABLE IF NOT
EXISTS``，可重复调用），不新建迁移框架（架构 G13）。

两处对架构文档的**增补**：
- ``case.visibility_class`` / ``case.acl_version`` 落表（架构 §6.2 ER 有，任务清单未列）；
- ``case.pipeline_stage`` 不落表（MVP 用 ``status`` 单一口径，避免"授权只认 status"
  这条纪律被第二套状态字段稀释）。
"""
from __future__ import annotations

from .case_db import (
    CASE_DDL,
    CASE_DDL_MYSQL,
    CASE_TABLES,
    DEFAULT_CASE_DB,
    CaseDatabase,
    MySQLCaseDatabase,
    SQLiteCaseDatabase,
    bulk_load,
    case_database_for,
    connect,
    default_case_database,
    ensure_case_schema,
    is_mysql_target,
    resolve_case_db,
)

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
