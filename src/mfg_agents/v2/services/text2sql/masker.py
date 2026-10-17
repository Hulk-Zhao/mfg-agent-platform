"""字段脱敏（从 ``services/text2sql.py`` 拆分）。

``FieldMasker`` 按角色掩码敏感列，覆盖列别名（BUG-2 修复）；``SENSITIVE_COLUMNS``
定义脱敏规则。对外符号经 ``services.text2sql`` 包重导出。
"""
from __future__ import annotations

from typing import Optional

from .auditor import _parse_sql

__all__ = ["SENSITIVE_COLUMNS", "FieldMasker"]

# ---------- 敏感字段脱敏（列级；角色不在白名单即脱敏） ----------
SENSITIVE_COLUMNS: dict[str, dict] = {
    "supplier": {"mask": "供应商***", "allow_roles": {"procurement", "sqe"}},
    "unit_price": {"mask": "***", "allow_roles": {"procurement", "planning", "finance"}},
}


class FieldMasker:
    """字段脱敏（第六道闸）：敏感列按角色掩码，覆盖列别名（BUG-2 修复）。

    ``SELECT supplier AS s`` 的结果列名是 ``s``，仅按列名匹配会漏掩。
    传入 SQL 时用 sqlglot 解析 SELECT 投影的 输出列→源列 映射（主理人
    Round 2 规格：真解析器）；解析失败退化为纯列名匹配（审计已先行放行，
    此处不应失败，且绝不因解析失败而放宽脱敏）。
    """

    @staticmethod
    def alias_map(sql: str) -> dict[str, str]:
        """解析 SELECT 投影：输出列名（别名）→ 源列名（小写）。

        规则（sqlglot AST）：
        - ``exp.Alias``（``col AS a``）：alias → 内层源列名；
        - 裸 ``exp.Column``（``col`` / ``t.col``）：列名 → 列名；
        - 函数/表达式包裹的列（``SUM(defect_qty) AS total``）：
          取其中最后一个 Column 作为近似源列（宁可多映射不漏映射）。
        """
        expr = _parse_sql(sql)
        if expr is None:
            return {}
        from sqlglot import exp
        mapping: dict[str, str] = {}

        def _column_name(node) -> str:
            if isinstance(node, exp.Column):
                return node.name.lower()
            columns = list(node.find_all(exp.Column))
            return columns[-1].name.lower() if columns else ""

        for select in expr.find_all(exp.Select):
            for projection in select.expressions:
                if isinstance(projection, exp.Alias):
                    source = _column_name(projection.this)
                    if source:
                        mapping[projection.alias.lower()] = source
                elif isinstance(projection, exp.Column):
                    name = projection.name.lower()
                    if name:
                        mapping[name] = name
        return mapping

    @classmethod
    def mask(cls, rows: list[dict], user_ctx: Optional[dict],
             sql: Optional[str] = None) -> tuple[list[dict], bool]:
        """按角色掩码敏感列；sql 提供时联动别名解析（端到端防绕过）。"""
        roles = {str(r).lower() for r in (user_ctx or {}).get("roles") or []}
        aliases = cls.alias_map(sql) if sql else {}
        masked = False
        out: list[dict] = []
        for row in rows:
            new_row = dict(row)
            for column in new_row:
                lowered = str(column).lower()
                # 输出列名本身是敏感列，或其源列（经别名映射）是敏感列
                source = aliases.get(lowered, lowered)
                rule = SENSITIVE_COLUMNS.get(lowered) or \
                    SENSITIVE_COLUMNS.get(source)
                if rule is not None and not (roles & rule["allow_roles"]):
                    new_row[column] = rule["mask"]
                    masked = True
            out.append(new_row)
        return out, masked
