"""SQL 语义 lint（第五道半闸）：拦"语法合法但口径必错"的 LLM 生成 SQL。

安全边界（DDL/DML/表权限）由 ``SQLAuditor`` 把守；本模块把守**正确性边界**：
LLM 生成的 SQL 常见病是「聚合函数进 WHERE」「无 GROUP BY 的混聚 SELECT」
「COUNT(*)>0 当过滤条件」——SQLite 宽松语义下静默返回错误数字，比查不到
更危险（用户拿错误数字去对客承诺）。

分级：
- **硬伤**（``SqlLintError``）：口径必错，拒绝执行 → 降级人工兜底；
- **软警告**（warnings）：疑似口径问题，放行但随结果提示复核。

解析失败不 lint（auditor 已 fail-closed，不会到这里）。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SqlLintError", "lint_semantic"]


class SqlLintError(Exception):
    """SQL 语义硬伤（口径必错，拒绝执行）。"""


def lint_semantic(sql: str) -> list[str]:
    """语义 lint：硬伤抛 ``SqlLintError``；返回软警告列表。

    规则（sqlglot AST 级，与 auditor 同解析器同方言）：
    1. WHERE 节点内出现聚合函数（SUM/COUNT/AVG/MIN/MAX）且不在子查询里
       → 硬伤（SQLite 对聚合比较静默返回任意行）；
    2. GROUP BY 缺失但 SELECT 混聚（聚合列与裸列并列）
       → 硬伤（SQLite 取任意行，TOP N 类问题必然答错）；
    3. 无 LIMIT 且无 GROUP BY 的明细 SELECT → 软警告（row_cap 会截断，提示复核）。
    """
    try:
        import sqlglot
        from sqlglot import exp
        expr = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:  # noqa: BLE001 —— auditor 已保证可解析；二次失败不拦
        return []

    warnings: list[str] = []

    # sqlglot 版本兼容：保留字键名在部分版本为 ``from_`` / ``group_``（带下划线）
    def _arg(select_node: Any, *keys: str) -> Any:
        for k in keys:
            v = select_node.args.get(k)
            if v is not None:
                return v
        return None

    # 规则 1：WHERE 内聚合（只查带 FROM 的 SELECT 的 WHERE，子查询内合法）
    for select in expr.find_all(exp.Select):
        if _arg(select, "from_", "from") is None:
            continue
        where = _arg(select, "where")
        if where is None:
            continue
        agg = next(where.find_all(exp.AggFunc), None)
        if agg is not None:
            raise SqlLintError(
                "WHERE 子句中使用了聚合函数（如 SUM/COUNT）：聚合比较应使用 "
                "HAVING 或子查询，当前写法在 SQLite 下会返回不可预期的行")

    # 规则 2：混聚 SELECT（有聚合列 + 有裸列 + 无 GROUP BY）→ 口径必错
    for select in expr.find_all(exp.Select):
        if _arg(select, "group_", "group") is not None:
            continue
        projections = list(select.expressions)
        has_agg = any(p.find(exp.AggFunc) for p in projections)
        if not has_agg:
            continue
        bare_cols = [p for p in projections
                     if not p.find(exp.AggFunc)
                     and not isinstance(p, exp.Star)]
        if has_agg and bare_cols:
            names = ", ".join(getattr(p, "alias", "") or p.sql()[:24]
                              for p in bare_cols[:3])
            raise SqlLintError(
                f"SELECT 混合聚合列与普通列（{names}）但缺少 GROUP BY："
                "SQLite 会从任意行取普通列的值，聚合结果口径必错")

    # 规则 3：明细查询无 LIMIT → 软警告（执行层有 row_cap 兜底，不拦）
    has_limit = next(expr.find_all(exp.Limit), None) is not None
    has_agg_any = next(expr.find_all(exp.AggFunc), None) is not None
    if not has_limit and not has_agg_any:
        warnings.append("SQL 未写 LIMIT：已按行数上限截断，如需完整明细请在问题中"
                        "说明范围或限定条数")

    return warnings
