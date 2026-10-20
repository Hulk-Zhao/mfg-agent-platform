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
        """通道①：指标库命中（口径统一，最高置信）。

        带命名绑定的指标（如 OTD 的 ``:start/:end`` 统计窗口）由调用方传入
        ``params``；缺省补**默认统计窗口**（最近 7 天，与看板默认档同源），
        保证"未指定口径"时也能给出与看板一致的数值，而不是返回另一套口径。

        结果行按指标定义里的 ``columns`` 映射为**业务列名**（口径即代码的一部分：
        直答表格/结论不暴露 ``otd_rate_pct`` 这类 SQL 别名，与看板直答同构）。
        """
        t0 = time.perf_counter()
        metric = self.metrics.get(metric_id)
        sql = str(metric["sql"])
        merged = {**self.default_window_params(), **(params or {})}
        bound = self._bind_params(sql, merged)
        if bound is None:
            missing = sorted(set(re.findall(r":([A-Za-z_]\w*)", sql))
                             - set(merged))
            logger.warning("[metric] 缺参降级: %s 需要 %s", metric_id, missing)
            return QueryResult(
                channel="fallback", sql=sql, confidence=0.0,
                fallback="manual_ticket",
                warnings=[f"指标 {metric_id} 缺少参数 {missing}，降级人工兜底"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "metric", "metric_id": metric_id,
                            "metric_name": metric.get("name"),
                            "missing_params": missing, "params": params or {}},
            )
        try:
            SQLAuditor.audit(sql, user_ctx)  # 入口校验 + 正则 + 结构 + 表权限
        except SQLAuditError as exc:
            return self._rejected("metric", sql, str(exc), t0,
                                  tables=metric.get("tables", []),
                                  metric_id=metric_id)
        rows = self.data_registry.execute_readonly_sql(
            sql, bound, row_cap=self.row_cap)
        rows, masked = FieldMasker.mask(rows, user_ctx, sql)
        warnings = ResultValidator.validate(rows, self.row_cap)
        rows = [self._label_metric_row(r, metric.get("columns")) for r in rows]
        return QueryResult(
            channel="metric", sql=sql, rows=rows, masked=masked, confidence=0.95,
            warnings=warnings, elapsed_ms=(time.perf_counter() - t0) * 1000,
            provenance={"channel": "metric", "metric_id": metric_id,
                        "metric_name": metric.get("name"),
                        "owner": metric.get("owner"),
                        "metric_desc": metric.get("desc"),
                        "metric_unit": metric.get("unit", ""),
                        "metric_target": metric.get("target"),
                        "metric_higher_is_better": metric.get("higher_is_better"),
                        "tables": metric.get("tables", []),
                        "window": {"start": merged.get("start"),
                                   "end": merged.get("end"),
                                   "label": merged.get("label", "")},
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
        # V3.7 缺参守卫：SQL 命名绑定与传入 params 对齐后再执行——
        # 缺参 → 人工兜底（绝不把 sqlite3.ProgrammingError 抛成 500）；
        # 多余键剔除（避免驱动对未知绑定报错）。与 router 的库存快路径同批修复。
        bound = self._bind_params(sql, params)
        if bound is None:
            missing = sorted(set(re.findall(r":([A-Za-z_]\w*)", sql)))
            logger.warning("[registered] 缺参降级: %s 需要 %s", name, missing)
            return QueryResult(
                channel="fallback", sql=sql, confidence=0.0,
                fallback="manual_ticket",
                warnings=[f"注册查询 {name} 缺少参数 {missing}，降级人工兜底"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "registered", "query_name": name,
                            "missing_params": missing, "params": params or {}},
            )
        rows = self.data_registry.run_query(name, bound)
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
        from ....common.llm_factory import get_llm, llm_available
        if not llm_available():
            return QueryResult(
                channel="fallback", sql="", confidence=0.0,
                fallback="manual_ticket",
                warnings=["LLM 不可用，明细探查降级为人工兜底工单"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail", "degraded": True},
            )
        from ....common.sql_tools import _extract_sql  # V1 资产复用
        try:
            # V4：max_tokens 300→600——SQL 生成被截断会产出残缺语句，
            # 又恰好绕过"能解析才 lint"的路径（残缺语句解析失败全走 fallback）。
            llm = get_llm(temperature=0.0, tier="L1",
                          max_tokens=600, timeout=30, thinking="disabled")
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
        # V4 语义 lint（第五道半闸）：拦"语法合法但口径必错"的生成 SQL——
        # 聚合进 WHERE / 混聚无 GROUP BY 在 SQLite 宽松语义下静默返回错误数字，
        # 比查不到更危险。硬伤 → 人工兜底；软警告（缺 LIMIT 等）随结果提示复核。
        from .linter import SqlLintError, lint_semantic
        try:
            lint_warnings = lint_semantic(sql)
        except SqlLintError as exc:
            logger.warning("[detail] 语义 lint 拦截: %s | SQL: %s", exc, sql[:120])
            return QueryResult(
                channel="fallback", sql=sql, confidence=0.0,
                fallback="manual_ticket",
                warnings=[f"语义校验拦截: {exc}"],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                provenance={"channel": "detail", "blocked": str(exc),
                            "lint": "semantic"},
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
        warnings.extend(lint_warnings)
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
            # 指标口径里的统计窗口随问题口径走（"最近7天 / 当日 / 当月"）——
            # 否则同一句"最近7天 OTD"会拿到与看板不同窗口的数（口径错配）。
            return self.query_metric(str(metric["id"]),
                                     params=self._window_params(question),
                                     user_ctx=user_ctx)
        params = self._extract_params(question)
        name = self.queries.match(question, params)
        if name is not None:
            return self.query_registered(name, params, user_ctx)
        return self.query_detail(question, user_ctx)

    @staticmethod
    def default_window_params() -> dict[str, str]:
        """默认统计窗口（最近 7 天）——与看板默认档 ``resolve_window(None)`` 同源。"""
        from ..dashboard import resolve_window

        win = resolve_window(None)
        return {"start": str(win["start"]), "end": str(win["end"]),
                "label": str(win["label"])}

    @staticmethod
    def _window_params(question: str) -> dict[str, str]:
        """问题文本 → 统计窗口（当日 / 当月 / 最近 7 天），口径与看板同源。

        仅识别看板已定义的三档（``day`` / ``month`` / ``window``）；未提及时间
        或提及其它跨度（如"最近30天"）一律回落默认档，**不臆造窗口**。
        """
        q = question or ""
        key = "window"
        if any(w in q for w in ("今天", "当日", "本日")):
            key = "day"
        elif any(w in q for w in ("本月", "当月", "这个月")):
            key = "month"
        from ..dashboard import resolve_window

        win = resolve_window(key)
        return {"start": str(win["start"]), "end": str(win["end"]),
                "label": str(win["label"])}

    @staticmethod
    def _label_metric_row(row: dict, columns: Optional[list]) -> dict:
        """指标结果行 → 业务列名（口径即代码的一部分）。

        未在 ``columns`` 声明的列原样保留（不丢数据）；``unit`` 存在时值带单位
        （如 ``68.4%`` / ``720单``）。
        """
        if not columns:
            return row
        out: dict[str, Any] = {}
        mapped: set[str] = set()
        for col in columns:
            key = str(col.get("key") or "")
            if key not in row:
                continue
            value = row[key]
            unit = str(col.get("unit") or "")
            out[str(col.get("label") or key)] = f"{value}{unit}" if unit else value
            mapped.add(key)
        for k, v in row.items():
            if k not in mapped:
                out[k] = v
        return out

    @staticmethod
    def _extract_params(question: str) -> dict[str, str]:
        """从问题中轻量抽取注册查询参数（线体/型号/编码/制度词，规则级、无 LLM）。"""
        params: dict[str, str] = {}
        line = re.search(r"(SMT|DIP)-(\d)", question)
        if line:
            params["line"] = f"{line.group(1)}-{line.group(2)}"
        model = re.search(r"([A-E])\s*型号", question)
        if model:
            params["model"] = f"{model.group(1)}型号"
        # V3.7：料号/批号编码 token（大写字母起头、含连字符与数字，排除线体 SMT-/DIP-）。
        # 同一 token 同时登记 part_no / batch_no，最终由命中的注册查询的绑定键裁剪。
        code = re.search(r"\b([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)\b", question)
        if code and not code.group(1).startswith(("SMT-", "DIP-")) \
                and any(ch.isdigit() for ch in code.group(1)):
            params["part_no"] = code.group(1)
            params["batch_no"] = code.group(1)
        # V3.2：职能域知识检索参数（kw = 问题中命中的制度关键词；
        # 词表与 queries.yaml 的 finance/admin_policy_search keywords 同源）
        for kw in ("借支", "报销", "发票", "差旅", "开票", "备用金", "住宿标准",
                   "伙食补助", "报销流程", "抬头", "会议室", "食堂", "班车",
                   "团建", "社团", "餐饮", "会议预订", "行政"):
            if kw in question:
                params["kw"] = kw
                break
        # V3.7：材料口语模糊词的 kw 兜底（"M8 螺栓还有多少库存" → kw="M8"），
        # 供 material_stock_search 的料号/名称 LIKE 使用；制度词优先。
        if "kw" not in params:
            spec = re.search(r"\b([A-Z]{1,3}\d{1,4})\b", question)
            if spec and any(w in question for w in (
                    "库存", "螺栓", "电容", "电阻", "连接器", "外壳", "电池",
                    "液晶屏", "彩盒", "PCB", "物料", "材料")):
                params["kw"] = spec.group(1)
        return params

    @staticmethod
    def _bind_params(sql: str, params: Optional[dict]) -> Optional[dict]:
        """对齐 SQL 命名绑定与传入参数：缺参 → None；无绑定 → {}；多余键剔除。"""
        needed = set(re.findall(r":([A-Za-z_]\w*)", sql))
        if not needed:
            return {}
        provided = params or {}
        if needed - set(provided):
            return None
        return {k: provided[k] for k in needed}

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
