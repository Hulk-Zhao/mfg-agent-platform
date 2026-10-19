"""SQL 审核链：正则 → 结构 → 表级权限（从 ``services/text2sql.py`` 拆分）。

对外符号（``SQLAuditor`` / ``extract_tables`` / ``SQLAuditError``）经
``services.text2sql`` 包重导出，import 路径不变。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .libraries import TABLE_ACL, table_acl

logger = logging.getLogger(__name__)

# 正则拦截：任何位置的 DDL/DML/危险命令（延续 V1 sql_tools 思路并扩充）
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|"
    r"detach|pragma|vacuum|reindex|replace|load_extension|sqlite_compileoption|"
    r"readfile|writefile)\b", re.IGNORECASE)

__all__ = ["SQLAuditError", "SQLAuditor", "extract_tables", "_parse_sql",
           "_FORBIDDEN_SQL"]


def _parse_sql(sql: str):
    """sqlglot AST 解析（SQLite 方言）；失败返回 None（调用方决定 fail 策略）。"""
    try:
        import sqlglot
        return sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:  # noqa: BLE001 解析失败由调用方按 fail-closed 处理
        return None


def extract_tables(sql: str) -> set[str]:
    """提取 SQL 引用的全部表名（sqlglot AST 级，主理人 Round 2 规格）。

    停止正则打补丁：真解析器天然覆盖 FROM/JOIN/CTE/子查询/UNION 分支、
    括号紧贴、反引号/双引号标识符、逗号隐式 join。
    **解析失败 → fail-closed 抛 SQLAuditError**（解析失败本身即可疑，
    绝不回退到宽松路径——QA-BUG-1 的教训）。
    """
    expr = _parse_sql(sql)
    if expr is None:
        raise SQLAuditError(f"SQL 解析失败，拒绝查询: {sql[:120]}")
    from sqlglot import exp
    return {t.name.lower() for t in expr.find_all(exp.Table) if t.name}


class SQLAuditError(Exception):
    """SQL 审核链拦截（安全边界）。"""


class SQLAuditor:
    """SQL 审核链：正则 → 结构 → 表级权限。"""

    @staticmethod
    def regex_guard(sql: str) -> None:
        """第一道闸：DDL/DML/危险命令关键词拦截 + 多语句拦截。"""
        if ";" in sql.strip().rstrip(";"):
            raise SQLAuditError("禁止多语句执行")
        if _FORBIDDEN_SQL.search(sql):
            raise SQLAuditError(f"SQL 含禁用关键词: {sql[:120]}")
        if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
            raise SQLAuditError("仅允许 SELECT/WITH 查询")

    @staticmethod
    def structure_check(sql: str) -> None:
        """第二道闸：结构校验（无 sqlglot 依赖下的轻量 AST 替代方案）。

        校验：单语句；WITH 从句必须包含 SELECT；禁止写操作伪装
        （如 INSERT ... SELECT 已被正则拦截，这里防 "SELECT INTO" 类）。
        """
        stripped = sql.strip().rstrip(";")
        if re.search(r"\binto\b", stripped, re.IGNORECASE) and not re.search(
                r"\bexists\b|\bin\s*\(", stripped, re.IGNORECASE):
            # SELECT INTO 属写操作方言；INTO 仅允许出现在子查询上下文（简化判定）
            if re.search(r"select\s+.*\binto\b", stripped, re.IGNORECASE | re.S):
                raise SQLAuditError("禁止 SELECT INTO 写操作")
        if re.match(r"^\s*with\b", stripped, re.IGNORECASE) and not re.search(
                r"\bselect\b", stripped, re.IGNORECASE):
            raise SQLAuditError("WITH 查询缺少 SELECT 主体")

    @staticmethod
    def check_table_permission(sql: str, user_ctx: dict) -> None:
        """第三道闸：表级权限（用户部门/角色必须在表白名单内，fail-closed）。

        白名单经 ``table_acl()`` 读取（静态白名单 ∪ 数据查询权限申请的运行时授权叠加层）。
        """
        allowed = SQLAuditor._user_scopes(user_ctx)
        for table in sorted(extract_tables(sql)):
            if table not in TABLE_ACL:
                raise SQLAuditError(f"表未注册权限，拒绝访问: {table}")
            acl = table_acl(table)
            if not (allowed & acl):
                raise SQLAuditError(
                    f"表级权限不足: {table}（用户范围 {sorted(allowed)}）")

    @staticmethod
    def _user_scopes(user_ctx: Optional[dict]) -> set[str]:
        """用户权限范围（部门 + 角色）；roles 为 None/缺失时按空处理（BUG-3）。"""
        ctx = user_ctx or {}
        scopes = {str(ctx.get("dept") or "").lower()}
        scopes.update(str(r).lower() for r in (ctx.get("roles") or []))
        scopes.discard("")
        return scopes

    @classmethod
    def audit(cls, sql: str, user_ctx: Optional[dict]) -> list[str]:
        """执行前三道闸，返回告警列表；拦截即抛 SQLAuditError。

        BUG-3 修复（主理人规格）：入口先校验 user_ctx——
        roles 为 None/不可迭代、dept 非字符串 → 直接抛 SQLAuditError，
        fail-closed，绝不让 TypeError 逃逸到调用方。
        """
        cls._validate_user_ctx(user_ctx)
        cls.regex_guard(sql)
        cls.structure_check(sql)
        cls.check_table_permission(sql, user_ctx)
        return []

    @staticmethod
    def _validate_user_ctx(user_ctx: Optional[dict]) -> None:
        """user_ctx 入口校验（fail-closed）：无效上下文一律拒绝查询。"""
        if not isinstance(user_ctx, dict):
            raise SQLAuditError("user_ctx 无效（非字典），拒绝查询")
        dept = user_ctx.get("dept")
        if dept is not None and not isinstance(dept, str):
            raise SQLAuditError("user_ctx.dept 必须为字符串，拒绝查询")
        roles = user_ctx.get("roles")
        if roles is None or isinstance(roles, (str, bytes)) or \
                not isinstance(roles, (list, tuple, set, frozenset)):
            raise SQLAuditError("user_ctx.roles 必须为角色列表，拒绝查询")
