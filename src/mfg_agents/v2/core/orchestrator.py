"""V2Orchestrator：统一编排入口（IM / Web / 定时任务三种触发都走这里）。

收敛 V1 技术债：api.py 原先手工顺序编排、未使用 build_graph() 编译图；
V2 统一为 两级路由 → 场景注册表 → YamlDagExecutor（LangGraph 子图）。

总控边界纪律（ARCHITECTURE-V2.md §3.2）：
不直接查数据库（必须经 Text2SQLService）、不直接发通知（必须经
NotificationService）、不做审批判断（必须经 WorkflowEngine）。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...config import settings
from ..contracts.data_source import DataSourceRegistry, SQLiteDataSource
from ..contracts.scenario import ScenarioRegistry
from .node_registry import build_handlers
from .router import RouteResult, TwoLevelRouter
from .shared_context import SharedContext
from .yaml_dag import DagRunResult, YamlDagExecutor
from ..services.notification import LogNotificationChannel, NotificationService
from ..services.text2sql import (
    MetricLibrary,
    QueryLibrary,
    Text2SQLService,
)
from ..services.workflow import WorkflowEngine

logger = logging.getLogger(__name__)

# V2 资源默认路径（相对本文件：src/mfg_agents/v2/...）
_V2_DIR = Path(__file__).resolve().parent.parent


@dataclass
class V2Runtime:
    """V2 运行时依赖集合（可注入替换，便于测试与扩展）。"""

    router: TwoLevelRouter
    scenario_registry: ScenarioRegistry
    data_registry: DataSourceRegistry
    text2sql: Text2SQLService
    notifier: NotificationService
    workflow: WorkflowEngine
    handlers: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_default(cls) -> "V2Runtime":
        """装配默认依赖（本地 SQLite 数据源 + YAML 场景/指标/查询库）。"""
        db_path = settings.DB_URL.replace("sqlite:///", "")
        queries = QueryLibrary.load(_V2_DIR / "data" / "queries.yaml")
        metrics = MetricLibrary.load(_V2_DIR / "data" / "metrics")
        data_registry = DataSourceRegistry()
        data_registry.register(
            SQLiteDataSource(db_path, name="dev", queries={
                name: str(spec["sql"])
                for name, spec in queries._queries.items()}),
            default=True)
        scenario_registry = ScenarioRegistry(settings.V2_SCENARIOS_DIR)
        scenario_registry.load()
        workflow = WorkflowEngine(settings.WORKFLOW_DB)
        runtime = cls(
            router=TwoLevelRouter(scenario_registry),
            scenario_registry=scenario_registry,
            data_registry=data_registry,
            text2sql=Text2SQLService(data_registry, metrics, queries),
            notifier=NotificationService(LogNotificationChannel()),
            workflow=workflow,
        )
        runtime.handlers = build_handlers(runtime)
        return runtime


@dataclass
class OrchestratorResult:
    """统一编排结果（全字段可审计）。"""

    query: str
    run_id: str
    route: dict[str, Any]
    scenario_key: Optional[str]
    report: str = ""
    internal_brief: dict[str, Any] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    hitl: list[dict[str, Any]] = field(default_factory=list)
    workflow_tasks: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    fallback: Optional[str] = None
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "run_id": self.run_id,
            "route": self.route, "scenario_key": self.scenario_key,
            "report": self.report, "internal_brief": self.internal_brief,
            "provenance": self.provenance, "hitl": self.hitl,
            "workflow_tasks": self.workflow_tasks, "timings": self.timings,
            "degraded": self.degraded,
            "degraded_reasons": self.degraded_reasons,
            "fallback": self.fallback,
            "elapsed_ms": round(self.elapsed_ms, 1), "error": self.error,
        }


class V2Orchestrator:
    """智能总控（V2 骨架）：唯一任务入口与出口。"""

    def __init__(self, runtime: V2Runtime) -> None:
        self.runtime = runtime

    def handle(self, query: str, user_ctx: Optional[dict] = None,
               sink: Optional[Any] = None) -> OrchestratorResult:
        """处理一次请求：路由 → 场景 DAG → 聚合（含 provenance/HITL/降级标记）。"""
        t0 = time.perf_counter()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        route = self.runtime.router.route(query, user_ctx)
        result = OrchestratorResult(
            query=query, run_id=run_id, route=route.to_dict(),
            scenario_key=route.scenario_key)
        result.degraded_reasons = self._degraded_reasons(route)

        if route.scenario_key is None:
            result.fallback = "manual_ticket"
            result.report = (
                "当前问题超出平台已覆盖范围（或路由置信度低），"
                "已转人工兜底工单处理。")
            result.degraded = True
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.info("[orchestrator] %s 无匹配场景，转人工兜底: %s",
                        run_id, route.reason)
            return result

        scenario = self.runtime.scenario_registry.get(route.scenario_key)
        ctx = SharedContext()
        ctx.merge("orchestration", {
            "run_id": run_id, "query": query, "route": route.to_dict(),
            "entities": route.entities,
        }, source="router")
        executor = YamlDagExecutor(scenario, self.runtime.handlers, ctx, sink)
        dag: DagRunResult = executor.run(query, user_ctx,
                                         entities=route.entities)

        result.internal_brief = ctx.get("internal_brief", {})
        result.report = ctx.get("customer_report", "")
        result.hitl = dag.hitl
        result.workflow_tasks = dag.workflow
        result.timings = dag.timings
        result.provenance = self._collect_provenance(dag)
        if ctx.get("fa_degraded"):
            result.degraded_reasons.append("FA 分析 LLM 不可用：模板化结构化结论")
        if ctx.get("report_degraded"):
            result.degraded_reasons.append("报告生成 LLM 不可用：模板化 + 禁词表兜底")
        result.degraded = bool(result.degraded_reasons) or bool(dag.degraded_nodes)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("[orchestrator] %s 场景 %s 完成，耗时 %.1fms，降级=%s",
                    run_id, route.scenario_key, result.elapsed_ms, result.degraded)
        return result

    # ---------- 内部 ----------

    @staticmethod
    def _degraded_reasons(route: RouteResult) -> list[str]:
        reasons: list[str] = []
        if route.level == "degrade":
            reasons.append("LLM 不可用：路由降级为规则模糊匹配")
        if route.low_confidence:
            reasons.append(f"路由低置信度（{route.confidence:.2f}）：建议人工复核")
        return reasons

    @staticmethod
    def _collect_provenance(dag: DagRunResult) -> list[dict[str, Any]]:
        """汇总 provenance：各域 SQL/查询记录 + 每节点耗时（可审计）。

        handler  payload 中的 provenance 项是 QueryResult.to_dict()，
        其内部还有一层 provenance（通道/表/命中数），这里展平为单层。
        """
        items: list[dict[str, Any]] = []
        for domain, state in sorted(dag.domains.items()):
            for record in (state.get("payload") or {}).get("provenance", []):
                nested = record.get("provenance") or {}
                entry = {**nested,
                         **{k: v for k, v in record.items()
                            if k not in ("provenance", "rows")}}
                entry["domain"] = domain
                items.append(entry)
        for node_id, elapsed_ms in sorted(dag.timings.items()):
            items.append({"channel": "node_timing", "node": node_id,
                          "elapsed_ms": elapsed_ms,
                          "status": dag.node_status.get(node_id, "ok")})
        return items


def create_default_orchestrator() -> V2Orchestrator:
    """便捷工厂：装配默认运行时（demo / API 入口用）。"""
    return V2Orchestrator(V2Runtime.create_default())


__all__ = ["V2Orchestrator", "V2Runtime", "OrchestratorResult",
           "create_default_orchestrator"]
