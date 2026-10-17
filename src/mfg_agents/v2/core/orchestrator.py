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

# 项目根（data/seed_v3.sql 所在；布局：src/mfg_agents/v2 → 根需上溯 3 级）
_SEED_SQL = _V2_DIR.parents[2] / "data" / "seed_v3.sql"


def _apply_seed(db_path: str) -> None:
    """执行演示数据扩充（幂等 INSERT OR IGNORE；只补历史数据不碰红线行）。

    V3.1：员工自由提问需要"有东西可查"——扩充历史批次/客诉/产出/设备/
    停线数据（2026-10-08 ~ 10-14），"今天"（10-15）的行与场景锚点一律不动，
    由 tests/v2/test_scenarios_v2.py 的数据红线测试守护。
    """
    if not _SEED_SQL.exists():
        return
    import sqlite3

    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=10.0) as conn:
            conn.executescript(_SEED_SQL.read_text(encoding="utf-8"))
        logger.info("[seed] 演示数据扩充完成: %s", _SEED_SQL.name)
    except Exception as exc:  # noqa: BLE001 —— 种子失败不阻断启动（演示库可能只读）
        logger.warning("[seed] 执行失败（不阻断）: %s", exc)


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
        _apply_seed(db_path)
        queries = QueryLibrary.load(_V2_DIR / "data" / "queries.yaml")
        metrics = MetricLibrary.load(_V2_DIR / "data" / "metrics")
        data_registry = DataSourceRegistry()
        data_registry.register(
            SQLiteDataSource(db_path, name="dev", queries={
                name: str(spec["sql"])
                for name, spec in queries._queries.items()},
                meta={
                    "system": "演示制造数据集（init.sql）",
                    "domain": "生产/质量/供应链/设备/客诉",
                    "owner_dept": "平台组",
                    "note": "替换为真实 ERP/MES 时：实现 DataSource 契约"
                            "并在 register() 声明 meta，总览页即自动可见",
                }),
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
    suggestions: list[dict[str, Any]] = field(default_factory=list)
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
            "suggestions": self.suggestions,
            "elapsed_ms": round(self.elapsed_ms, 1), "error": self.error,
        }


# 节点 id → 中文环节名（降级原因用业务语言，不暴露内部术语）
_NODE_CN: dict[str, str] = {
    "business": "业务受理", "confirm_stop": "停线确认",
    "quality": "品质", "supply_chain": "供应链", "equipment": "设备",
    "planning": "计划", "material_control": "物控", "procurement": "采购",
    "qa_lead": "QA", "pqe_process": "PQE", "sqe_supplier": "SQE",
    "engineering": "工程", "business_sales": "业务",
    "maintenance": "维保", "planning_window": "计划窗口",
    "engineering_param": "工程参数", "spare_parts": "备件",
    "fa": "根因分析", "report": "报告",
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
            result.suggestions = self._route_suggestions(query)
            if result.suggestions:
                result.report += (
                    "你可以先试试下面的相近问题，或等待人工跟进。")
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
        result.provenance = self._collect_provenance(ctx, dag)
        if ctx.get("fa_degraded"):
            result.degraded_reasons.append("根因分析服务未就绪：已输出模板化结构结论")
        if ctx.get("report_degraded"):
            result.degraded_reasons.append("报告生成服务未就绪：模板化输出 + 禁词过滤")
        for node in dag.degraded_nodes:
            label = _NODE_CN.get(node, node)
            result.degraded_reasons.append(
                f"{label}环节数据不完整（缺少数据源），已标注缺口并输出可复核结论")
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
            reasons.append("语义匹配服务未就绪，已按规则关键词匹配")
        if route.low_confidence:
            reasons.append(f"路由低置信度（{route.confidence:.2f}）：建议人工复核")
        return reasons

    # 兜底引导用的示例问法（场景 key → 员工视角的自然问法；与新员工能直接照抄提问）
    _SCENARIO_EXAMPLES: dict[str, str] = {
        "line_stop_v2": "A型号在SMT-1停线，何时复线？",
        "incoming_quality_v2": "今天锡膏批次有问题",
        "customer_complaint_v2": "华为投诉BATCH-A-1008",
        "delivery_status_v2": "A型号能发多少货",
        "equipment_warning_v2": "SMT-1设备参数异常吗",
    }

    def _route_suggestions(self, query: str) -> list[dict[str, Any]]:
        """兜底引导：按场景触发词与问题的重叠度推荐相近问题（不是编造答案）。

        员工自由提问未命中场景时，与其只给一句"转人工"，不如给 2-3 个
        "你可能想问"——把兜底从死胡同变成入口。
        """
        scored: list[tuple[int, str]] = []
        q = query.strip()
        for meta in self.runtime.scenario_registry.list():
            score = 0
            for trig in meta.triggers:
                for word in (trig.get("words") or []):
                    w = str(word).strip()
                    if w and w in q:
                        score += len(w)  # 长词命中权重更高（信息量更大）
            if score:
                scored.append((score, meta.key))
        scored.sort(reverse=True)
        out: list[dict[str, Any]] = []
        if scored:
            for _, key in scored[:3]:
                meta = self.runtime.scenario_registry.get(key)
                out.append({
                    "scenario_key": key,
                    "name": meta.name,
                    "example": self._SCENARIO_EXAMPLES.get(key, meta.name),
                })
            return out
        # 无触发词命中：给平台能力示例（"不知道你想问什么，这些是能答的"）——
        # 兜底永远给入口，不返回空建议
        for key in list(self._SCENARIO_EXAMPLES)[:3]:
            meta = self.runtime.scenario_registry.get(key)
            out.append({
                "scenario_key": key,
                "name": meta.name,
                "example": self._SCENARIO_EXAMPLES[key],
            })
        return out


    @staticmethod
    def _collect_provenance(ctx: SharedContext,
                            dag: DagRunResult) -> list[dict[str, Any]]:
        """汇总 provenance：各域 SQL/查询记录 + 每节点耗时（可审计）。

        来源改为 **SharedContext 的 ``provenance`` 记录历史**（只增不改）——
        避免"多分支同域"时 ``dag.domains`` 按域收敛（last-write-wins）导致
        部分分支的 SQL provenance 丢失（如 S4 六域并行中 5 个都属 supply_chain）。
        每条 handler payload 的 ``provenance`` 项是 QueryResult.to_dict()，
        其内部还有一层 provenance（通道/表/命中数），这里展平为单层。
        """
        items: list[dict[str, Any]] = []
        for record in ctx.history("provenance"):
            domain = record.domain
            for entry_record in (record.value or []):
                if not isinstance(entry_record, dict):
                    continue
                nested = entry_record.get("provenance") or {}
                entry = {**nested,
                         **{k: v for k, v in entry_record.items()
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
