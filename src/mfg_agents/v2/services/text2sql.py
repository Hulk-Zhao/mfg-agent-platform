"""Text2SQLService：数据查询八道防线（ARCHITECTURE-V2.md §5.4）。

查询管道（优先级从高到低）：
① 指标库命中 —— YAML 指标定义（口径统一，最高置信）；
② 注册查询   —— DataSourceRegistry.run_query 参数化执行（高频固定查询）；
③ 明细探查   —— LLM 生成 SQL + 审核链（仅 SELECT / 表级权限 / 行数上限）。

审核链（③ 必经，①② 亦做正则+表权限+脱敏防御纵深）：
  正则拦截（DDL/DML，延续 V1）→ 结构校验（单语句、仅 SELECT/WITH）
  → 表级权限（user_ctx 部门/角色白名单）→ 只读执行 + 行数上限
  → 结果校验（空结果/行数爆炸提示）→ 字段脱敏（敏感列按角色）

超范围问题返回 fallback="manual_ticket"（人工兜底是设计的一部分，不是失败）。
所有返回统一 {sql, rows, provenance, masked, confidence}。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from ..contracts.data_source import DataSourceRegistry

logger = logging.getLogger(__name__)

# ---------- 表级权限（白名单，fail-closed；coordinator=总控跨域取证角色） ----------
TABLE_ACL: dict[str, set[str]] = {
    "production_output": {"manufacturing", "planning", "coordinator"},
    "line_stop_event": {"manufacturing", "coordinator"},
    "quality_defect": {"quality", "manufacturing", "coordinator"},
    "material_batch": {"procurement", "quality", "coordinator"},
    "equipment_log": {"manufacturing", "coordinator"},
    "inventory": {"warehouse", "supply_chain", "coordinator"},
    "shipment": {"business", "warehouse", "coordinator"},
    "customer_complaint": {"quality", "business", "coordinator"},
}

# ---------- 敏感字段脱敏（列级；角色不在白名单即脱敏） ----------
SENSITIVE_COLUMNS: dict[str, dict[str, Any]] = {
    "supplier": {"mask": "供应商***", "allow_roles": {"procurement", "sqe"}},
    "unit_price": {"mask": "***", "allow_roles": {"procurement", "planning", "finance"}},
}

# 正则拦截：任何位置的 DDL/DML/危险命令（延续 V1 sql_tools 思路并扩充）
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|"
    r"detach|pragma|vacuum|reindex|replace)\b", re.IGNORECASE)
_TABLE_RE = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


@dataclass
class QueryResult:
    """统一查询结果（可审计：provenance 记录通道/SQL/命中行/耗时）。"""

    channel: str                      # metric | registered | detail | fallback
    sql: str
    rows: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    masked: bool = False
    confidence: float = 0.0
    fallback: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "sql": self.sql,
            "rows": self.rows,
            "row_count": len(self.rows),
            "provenance": self.provenance,
            "masked": self.masked,
            "confidence": self.confidence,
            "fallback": self.fallback,
            "warnings": self.warnings,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
        }


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
        """第三道闸：表级权限（用户部门/角色必须在表白名单内，fail-closed）。"""
        allowed = SQLAuditor._user_scopes(user_ctx)
        tables = {t.lower() for t in _TABLE_RE.findall(sql)}
        for table in tables:
            acl = TABLE_ACL.get(table)
            if acl is None:
                raise SQLAuditError(f"表未注册权限，拒绝访问: {table}")
            if not (allowed & acl):
                raise SQLAuditError(
                    f"表级权限不足: {table}（用户范围 {sorted(allowed)}）")

    @staticmethod
    def _user_scopes(user_ctx: Optional[dict]) -> set[str]:
        ctx = user_ctx or {}
        scopes = {str(ctx.get("dept", "")).lower()}
        scopes.update(str(r).lower() for r in ctx.get("roles", []))
        scopes.discard("")
        return scopes

    @classmethod
    def audit(cls, sql: str, user_ctx: Optional[dict]) -> list[str]:
        """执行前三道闸，返回告警列表；拦截即抛 SQLAuditError。"""
        cls.regex_guard(sql)
        cls.structure_check(sql)
        cls.check_table_permission(sql, user_ctx)
        return []


class ResultValidator:
    """结果校验（第五道闸）：空结果 / 行数爆炸 / 嵌套异常。"""

    @staticmethod
    def validate(rows: list[dict], row_cap: int) -> list[str]:
        warnings: list[str] = []
        if not rows:
            warnings.append("查询结果为空：可能条件过严、口径不符或数据未就绪")
        if len(rows) >= row_cap:
            warnings.append(f"结果达到行数上限 {row_cap}，可能被截断（谨防全表拉取）")
        for row in rows[:5]:
            for value in row.values():
                if isinstance(value, (dict, list)):
                    warnings.append("结果含嵌套结构，疑似口径异常")
                    break
        return warnings


class FieldMasker:
    """字段脱敏（第六道闸）：敏感列按角色掩码。"""

    @staticmethod
    def mask(rows: list[dict], user_ctx: Optional[dict]) -> tuple[list[dict], bool]:
        roles = {str(r).lower() for r in (user_ctx or {}).get("roles", [])}
        masked = False
        out: list[dict] = []
        for row in rows:
            new_row = dict(row)
            for column, rule in SENSITIVE_COLUMNS.items():
                if column in new_row and not (roles & rule["allow_roles"]):
                    new_row[column] = rule["mask"]
                    masked = True
            out.append(new_row)
        return out, masked


class MetricLibrary:
    """指标库（YAML 指标定义即代码，§5.2）。"""

    def __init__(self, metrics: list[dict]) -> None:
        self._metrics = {str(m["id"]): m for m in metrics}

    @classmethod
    def load(cls, metrics_dir: str | Path) -> "MetricLibrary":
        metrics: list[dict] = []
        for path in sorted(Path(metrics_dir).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for metric in data.get("metrics", []):
                if not metric.get("id") or not metric.get("sql"):
                    raise ValueError(f"指标定义缺少 id/sql: {path.name}")
                metrics.append(metric)
        if not metrics:
            raise ValueError(f"指标库为空: {metrics_dir}")
        return cls(metrics)

    def get(self, metric_id: str) -> dict:
        if metric_id not in self._metrics:
            raise KeyError(f"指标未定义: {metric_id!r}")
        return self._metrics[metric_id]

    def match(self, question: str) -> Optional[dict]:
        """按关键词匹配指标（命中返回定义，否则 None）。"""
        best: Optional[dict] = None
        best_score = 0
        for metric in self._metrics.values():
            score = sum(1 for w in metric.get("keywords", []) if w in question)
            if score > best_score:
                best, best_score = metric, score
        return best


class QueryLibrary:
    """注册查询库（data/queries.yaml）。"""

    def __init__(self, queries: dict[str, dict]) -> None:
        self._queries = queries

    @classmethod
    def load(cls, queries_file: str | Path) -> "QueryLibrary":
        data = yaml.safe_load(Path(queries_file).read_text(encoding="utf-8")) or {}
        queries = data.get("queries", {})
        if not queries:
            raise ValueError(f"注册查询库为空: {queries_file}")
        for name, spec in queries.items():
            if not spec.get("sql"):
                raise ValueError(f"注册查询缺少 sql: {name}")
        return cls(queries)

    def get(self, name: str) -> dict:
        if name not in self._queries:
            raise KeyError(f"注册查询不存在: {name!r}")
        return self._queries[name]

    def match(self, question: str) -> Optional[str]:
        best_name: Optional[str] = None
        best_score = 0
        for name, spec in self._queries.items():
            score = sum(1 for w in spec.get("keywords", []) if w in question)
            if score > best_score:
                best_name, best_score = name, score
        return best_name


class Text2SQLService:
    """Text2SQL 服务：指标库 → 注册查询 → 明细探查的统一入口。"""

    def __init__(self, data_registry: DataSourceRegistry,
                 metrics: MetricLibrary, queries: QueryLibrary,
                 row_cap: int = 200) -> None:
        self.data_registry = data_registry
        self.metrics = metrics
        self.queries = queries
        self.row_cap = row_cap

    # ---------- 三个通道 ----------

    def query_metric(self, metric_id: str, params: Optional[dict] = None,
                     user_ctx: Optional[dict] = None) -> QueryResult:
        """通道①：指标库命中（口径统一，最高置信）。"""
        t0 = time.perf_counter()
        metric = self.metrics.get(metric_id)
        sql = str(metric["sql"])
        try:
            SQLAuditor.regex_guard(sql)
            SQLAuditor.structure_check(sql)
            SQLAuditor.check_table_permission(sql, user_ctx)
        except SQLAuditError as exc:
            return self._rejected("metric", sql, str(exc), t0,
                                  tables=metric.get("tables", []),
                                  metric_id=metric_id)
        rows = self.data_registry.execute_readonly_sql(
            sql, params, row_cap=self.row_cap)
        rows, masked = FieldMasker.mask(rows, user_ctx)
        warnings = ResultValidator.validate(rows, self.row_cap)
        return QueryResult(
            channel="metric", sql=sql, rows=rows, masked=masked, confidence=0.95,
            warnings=warnings, elapsed_ms=(time.perf_counter() - t0) * 1000,
            provenance={"channel": "metric", "metric_id": metric_id,
                        "metric_name": metric.get("name"),
                        "owner": metric.get("owner"),
                        "tables": metric.get("tables", []),
                        "row_count": len(rows), "elapsed_ms": 0.0},
        )

    def query_registered(self, name: str, params: Optional[dict] = None,
                         user_ctx: Optional[dict] = None) -> QueryResult:
        """通道②：注册查询（参数化执行，SQL 预审过仍过防御纵深）。"""
        t0 = time.perf_counter()
        spec = self.queries.get(name)
        sql = str(spec["sql"])
        try:
            SQLAuditor.audit(sql, user_ctx)
        except SQLAuditError as exc:
            return self._rejected("registered", sql, str(exc), t0,
                                  tables=spec.get("tables", []), name=name)
        rows = self.data_registry.run_query(name, params)
        rows, masked = FieldMasker.mask(rows, user_ctx)
        warnings = ResultValidator.validate(rows, self.row_cap)
        return QueryResult(
            channel="registered", sql=sql, rows=rows, masked=masked,
            confidence=0.85, warnings=warnings,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            provenance={"channel": "registered", "query_name": name,
                        "desc": spec.get("desc"), "params": params or {},
                        "tables": spec.get("tables", []),
                        "row_count": len(rows), "elapsed_ms": 0.0},
        )

    def query_detail(self, question: str,
                     user_ctx: Optional[dict] = None) -> QueryResult:
        """通道③：明细探查（LLM 生成 SQL → 审核链 → 只读执行）。

        LLM 不可用时直接返回人工兜底（fallback=manual_ticket）——
        宁可"查不到 + 人工兜底"，不可"编得出 + 没人发现"（架构纪律 2）。
        """
        t0 = time.perf_counter()
        from ...tools.llm_factory import get_llm, llm_available
        if not llm_available():
            return QueryResult(
                channel="fallback", sql="", confidence=0.0,
                fallback="manual_ticket",
                warnings=["LLM 不可用，明细探查降级为人工兜底工单"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail", "degraded": True},
            )
        from ...tools.sql_tools import SCHEMA_PROMPT, _extract_sql  # V1 资产复用
        try:
            llm = get_llm(temperature=0.0, tier="L1")
            resp = llm.invoke(f"{SCHEMA_PROMPT}\n\n问题：{question}")
            raw = resp.content if hasattr(resp, "content") else str(resp)
            sql = _extract_sql(raw)
        except Exception as exc:  # noqa: BLE001
            return QueryResult(
                channel="fallback", sql="", confidence=0.0,
                fallback="manual_ticket",
                warnings=[f"SQL 生成失败: {exc}"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail", "degraded": True},
            )
        try:
            SQLAuditor.audit(sql, user_ctx)
        except SQLAuditError as exc:
            return QueryResult(
                channel="fallback", sql=sql, confidence=0.0,
                fallback="manual_ticket",
                warnings=[f"审核链拦截: {exc}"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail", "blocked": str(exc)},
            )
        try:
            rows = self.data_registry.execute_readonly_sql(
                sql, row_cap=self.row_cap)
        except Exception as exc:  # noqa: BLE001
            return QueryResult(
                channel="fallback", sql=sql, confidence=0.0,
                fallback="manual_ticket", warnings=[f"执行失败: {exc}"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail"},
            )
        rows, masked = FieldMasker.mask(rows, user_ctx)
        warnings = ResultValidator.validate(rows, self.row_cap)
        return QueryResult(
            channel="detail", sql=sql, rows=rows, masked=masked, confidence=0.6,
            warnings=warnings, elapsed_ms=(time.perf_counter() - t0) * 1000,
            provenance={"channel": "detail", "tables": sorted(
                {t.lower() for t in _TABLE_RE.findall(sql)}),
                "row_count": len(rows), "elapsed_ms": 0.0},
        )

    def query(self, question: str,
              user_ctx: Optional[dict] = None) -> QueryResult:
        """统一入口：① 指标库 → ② 注册查询 → ③ 明细探查 → 兜底。"""
        metric = self.metrics.match(question)
        if metric is not None:
            return self.query_metric(str(metric["id"]), user_ctx=user_ctx)
        name = self.queries.match(question)
        if name is not None:
            return self.query_registered(name, self._extract_params(question),
                                         user_ctx)
        return self.query_detail(question, user_ctx)

    @staticmethod
    def _extract_params(question: str) -> dict[str, str]:
        """从问题中轻量抽取注册查询参数（线体/型号，规则级、无 LLM）。"""
        params: dict[str, str] = {}
        line = re.search(r"(SMT|DIP)-(\d)", question)
        if line:
            params["line"] = f"{line.group(1)}-{line.group(2)}"
        model = re.search(r"([AB])\s*型号", question)
        if model:
            params["model"] = f"{model.group(1)}型号"
        return params

    # ---------- 内部 ----------

    def _rejected(self, channel: str, sql: str, reason: str,
                  t0: float, tables: Optional[list] = None,
                  name: Optional[str] = None,
                  metric_id: Optional[str] = None) -> QueryResult:
        logger.warning("[%s] 审核拦截: %s", channel, reason)
        provenance: dict[str, Any] = {"channel": channel, "blocked": reason,
                                      "tables": tables or []}
        if name:
            provenance["query_name"] = name
        if metric_id:
            provenance["metric_id"] = metric_id
        return QueryResult(
            channel="fallback", sql=sql, confidence=0.0,
            fallback="manual_ticket", warnings=[f"审核拦截: {reason}"],
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            provenance=provenance,
        )


__all__ = [
    "Text2SQLService", "QueryResult", "SQLAuditor", "SQLAuditError",
    "ResultValidator", "FieldMasker", "MetricLibrary", "QueryLibrary",
    "TABLE_ACL", "SENSITIVE_COLUMNS",
]
