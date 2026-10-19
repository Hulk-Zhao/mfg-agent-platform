"""Text2SQLService 包（由 ``services/text2sql.py`` 包化，import 路径不变）。

对外契约（**既有符号全部重导出，零破坏**）：
    from mfg_agents.v2.services.text2sql import Text2SQLService, QueryResult, ...

分层：``service``（通道编排）/ ``auditor``（审核链）/ ``masker``（脱敏）/
``libraries``（指标库·注册库·表权限）。
"""
from __future__ import annotations

from .auditor import SQLAuditError, SQLAuditor, extract_tables
from .libraries import (TABLE_ACL, MetricLibrary, QueryLibrary, grant_table_access,
                        reset_table_grants, revoke_table_access, table_acl)
from .masker import SENSITIVE_COLUMNS, FieldMasker
from .service import QueryResult, ResultValidator, Text2SQLService

__all__ = [
    "Text2SQLService", "QueryResult", "SQLAuditor", "SQLAuditError",
    "ResultValidator", "FieldMasker", "MetricLibrary", "QueryLibrary",
    "TABLE_ACL", "SENSITIVE_COLUMNS", "extract_tables",
    "table_acl", "grant_table_access", "revoke_table_access", "reset_table_grants",
]
