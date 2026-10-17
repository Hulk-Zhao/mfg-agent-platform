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
from typing import Any, Optional

from ...contracts.data_source import DataSourceRegistry
from .auditor import SQLAuditError, SQLAuditor, extract_tables
from .libraries import MetricLibrary, QueryLibrary
from .masker import FieldMasker
from ...prompts.text2sql import build_detail_prompt

logger = logging.getLogger(__name__)

__all__ = ["QueryResult", "ResultValidator", "Text2SQLService"]


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
            SQLAuditor.audit(sql, user_ctx)  # 入口校验 + 正则 + 结构 + 表权限
        except SQLAuditError as exc:
            return self._rejected("metric", sql, str(exc), t0,
                                  tables=metric.get("tables", []),
                                  metric_id=metric_id)
        rows = self.data_registry.execute_readonly_sql(
            sql, params, row_cap=self.row_cap)
        rows, masked = FieldMasker.mask(rows, user_ctx, sql)
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
        rows, masked = FieldMasker.mask(rows, user_ctx, sql)
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
        from ....tools.llm_factory import get_llm, llm_available
        if not llm_available():
            return QueryResult(
                channel="fallback", sql="", confidence=0.0,
                fallback="manual_ticket",
                warnings=["LLM 不可用，明细探查降级为人工兜底工单"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail", "degraded": True},
            )
        from ....tools.sql_tools import _extract_sql  # V1 资产复用
        try:
            llm = get_llm(temperature=0.0, tier="L1",
                          max_tokens=300, timeout=30, thinking="disabled")
            resp = llm.invoke(build_detail_prompt(question))
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
        rows, masked = FieldMasker.mask(rows, user_ctx, sql)
        warnings = ResultValidator.validate(rows, self.row_cap)
        return QueryResult(
            channel="detail", sql=sql, rows=rows, masked=masked, confidence=0.6,
            warnings=warnings, elapsed_ms=(time.perf_counter() - t0) * 1000,
            provenance={"channel": "detail", "tables": sorted(extract_tables(sql)),
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
        # V3.2：职能域知识检索参数（kw = 问题中命中的制度关键词；
        # 词表与 queries.yaml 的 finance/admin_policy_search keywords 同源）
        for kw in ("借支", "报销", "发票", "差旅", "开票", "备用金", "住宿标准",
                   "伙食补助", "报销流程", "抬头", "会议室", "食堂", "班车",
                   "团建", "社团", "餐饮", "会议预订", "行政"):
            if kw in question:
                params["kw"] = kw
                break
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
