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

# 项目根（data/*.sql 所在；布局：src/mfg_agents/v2 → 根需上溯 3 级）
_DATA_DIR = _V2_DIR.parents[2] / "data"
# 按序执行的种子（幂等 INSERT OR IGNORE）：主扩充 + 工段/工序/工位明细 + 库存域
_SEED_SQLS = [
    _DATA_DIR / "seed_v3.sql",
    _DATA_DIR / "seed_station_detail.sql",
    _DATA_DIR / "seed_inventory.sql",
]


def _apply_seed(db_path: str) -> None:
    """执行演示数据扩充（幂等 INSERT OR IGNORE；只补历史数据不碰红线行）。

    V3.1：员工自由提问需要"有东西可查"——扩充历史批次/客诉/产出/设备/
    停线数据（2026-10-08 ~ 10-14），"今天"（10-15）的行与场景锚点一律不动，
    由 tests/v2/test_scenarios_v2.py 的数据红线测试守护。
    V3.5：追加 ``seed_station_detail.sql``，把六指标按 工段/工序/工位×班次
    拆分入库（拆分式，求和回原值），供三级看板下钻。
    """
    import sqlite3

    seeds = [p for p in _SEED_SQLS if p.exists()]
    if not seeds:
        return
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=10.0) as conn:
            for p in seeds:
                conn.executescript(p.read_text(encoding="utf-8"))
                logger.info("[seed] 演示数据扩充完成: %s", p.name)
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
    # 办公后端（会议室/订餐）；demo 用内存实现，接真实飞书/钉钉/美餐时替换实现
    office: Any = None

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
        from ..services.office import DemoOfficeBackend
        office_backend = DemoOfficeBackend()
        if settings.OFFICE_BACKEND.strip().lower() in ("feishu", "lark"):
            # 真实飞书接入：会议室走飞书日历（订餐暂仍 demo，P1-B 再接美餐/美团）。
            # 缺凭据即启动报错——业务办理不允许静默落到 demo 内存数据。
            from ..services.feishu_calendar import build_feishu_backend
            office_backend = build_feishu_backend()
        runtime = cls(
            router=TwoLevelRouter(scenario_registry),
            scenario_registry=scenario_registry,
            data_registry=data_registry,
            text2sql=Text2SQLService(data_registry, metrics, queries),
            notifier=NotificationService(LogNotificationChannel()),
            workflow=workflow,
            office=office_backend,
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


# qa 直答通道 → 中文（业务语言）
_QA_CHANNEL_CN: dict[str, str] = {
    "metric": "指标库（口径统一）",
    "registered": "预注册查询",
    "detail": "明细探查",
    "fallback": "人工兜底",
}

# qa 直答通道 → 中文（业务语言）
_QA_CHANNEL_CN: dict[str, str] = {
    "metric": "指标库（口径统一）",
    "registered": "预注册查询",
    "detail": "明细探查",
    "fallback": "人工兜底",
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
    "spc_capability": "过程能力", "spc_pareto": "不良柏拉图",
    "report_spc": "过程能力简报", "hitl_spc_action": "过程处置",
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

        if route.strategy == "chitchat":
            return self._handle_chitchat(result, query, t0)

        if route.strategy == "qa":
            return self._handle_qa(result, query, user_ctx, t0)

        if route.strategy == "office":
            return self._handle_office(result, query, user_ctx, t0)

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

    def _handle_chitchat(self, result: OrchestratorResult, query: str,
                         t0: float) -> OrchestratorResult:
        """非领域意图（闲聊/无关）：轻量回应 + 引导回领域问题。

        与人工兜底的边界：闲聊**不建工单**（"你好"转人工是误导且浪费），
        回复限长（两句内），并给可点击的领域问题示例把用户引回来。
        """
        result.report = (
            "你好！我是制造业协同平台的问数助手，擅长回答跨部门的生产、"
            "质量、交付类问题。下面是一些例子，点击即可直接提问～")
        result.suggestions = self._route_suggestions("")
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("[orchestrator] %s 闲聊轻回应（不建工单）", result.run_id)
        return result

    def _handle_qa(self, result: OrchestratorResult, query: str,
                   user_ctx: Optional[dict], t0: float) -> OrchestratorResult:
        """数据直答（qa 策略）：轻量路径——不跑场景 DAG、不建工单、无 HITL。

        头脑风暴结论（2026-10-17）："今天A型号的质量怎么样"这类查询型问题
        被硬套客诉 8D 链路（10 节点+双 HITL+工单）是答非所问。简单数据
        查询走 Text2SQL 统一通道（指标库→注册查询→明细探查）直接回答。
        """
        t2s = self.runtime.text2sql
        qr = t2s.query(query, user_ctx=user_ctx)
        # 三层递进：直答能答就直接答（指标/注册/明细）→ 全部失败才由
        # Agentic 域智能体接管（自主决定查什么/组合什么/何时 HITL）→ 人工
        light_hit = (qr.channel in ("metric", "registered", "detail")
                     and qr.rows and qr.fallback is None)
        if not light_hit and qr.fallback == "manual_ticket":
            agentic = self._run_agentic(result.route.get("domain"), query, user_ctx, result.run_id)
            if agentic is not None:
                result.route = dict(result.route, strategy="agentic")
                result.internal_brief = {
                    "执行方式": "域智能体自主决策（Agentic）",
                    "工具调用步数": len(agentic["steps"]),
                    "数据缺口": "；".join(agentic["data_gaps"]) or "无",
                }
                result.provenance = agentic["steps"]
                result.report = agentic["report"]
                result.degraded_reasons = (["存在数据缺口：" + "；".join(agentic["data_gaps"])]
                                           if agentic["data_gaps"] else [])
                result.degraded = bool(result.degraded_reasons)
                if agentic.get("hitl_task"):
                    result.hitl = [self._agentic_hitl_card(
                        agentic["hitl_task"], result.run_id)]
                result.elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.info("[orchestrator] %s agentic 接管: domain=%s steps=%d",
                            result.run_id, result.route.get("domain"),
                            len(agentic["steps"]))
                return result
        result.internal_brief = {"回答通道": _QA_CHANNEL_CN.get(qr.channel, qr.channel),
                                 "数据行数": len(qr.rows or []),
                                 "脱敏": qr.masked}
        result.provenance = [qr.to_dict()]
        result.report = self._qa_report(query, qr)
        result.degraded = qr.channel in ("detail", "fallback") and bool(qr.fallback)
        if qr.fallback == "manual_ticket":
            result.fallback = "manual_ticket"
            result.suggestions = self._route_suggestions(query)
        result.degraded_reasons = list(qr.warnings or [])
        result.degraded = bool(result.degraded_reasons)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("[orchestrator] %s qa 直答: channel=%s rows=%d",
                    result.run_id, qr.channel, len(qr.rows or []))
        return result

    def _handle_office(self, result: OrchestratorResult, query: str,
                       user_ctx: Optional[dict], t0: float) -> OrchestratorResult:
        """办公助手通道：独立 Agentic Agent 接管会议室预订/订餐等办公事务。

        Agent 自主决定调哪些办公工具、何时对高影响事项发起 HITL；执行或失败
        均如实回写（含数据缺口）——失败回落人工兜底，降级不崩。
        """
        from ...tools.llm_factory import llm_available
        from ..agents.office import OfficeAssistantAgent

        if not llm_available():
            # LLM 不可用：办公助手无法自主规划工具调用 → 诚实降级转人工，绝不空转
            result.fallback = "manual_ticket"
            result.internal_brief = {
                "执行方式": "办公助手智能体（Agentic）", "工具调用步数": 0,
                "数据缺口": "LLM 服务不可用，无法自主办理"}
            result.report = ("办公助手需要 LLM 服务（当前不可用），已转人工兜底："
                             "请稍后重试，或直接联系行政办理会议室预订/订餐。")
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.info("[orchestrator] %s 办公助手降级（LLM 不可用）", result.run_id)
            return result

        try:
            agent = OfficeAssistantAgent(orchestrator=self)
            out = agent.run(query, user_ctx=user_ctx)
        except Exception as exc:  # noqa: BLE001 —— 办公助手异常回落人工兜底
            logger.warning("[orchestrator] %s 办公助手执行失败: %s", result.run_id, exc)
            result.fallback = "manual_ticket"
            result.report = "办公助手当前不可用，已转人工兜底处理（请稍后重试或联系行政）。"
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            return result

        result.route = dict(result.route, strategy="office")
        result.internal_brief = {
            "执行方式": "办公助手智能体（Agentic）",
            "工具调用步数": len(out["steps"]),
            "数据缺口": "；".join(out["data_gaps"]) or "无",
        }
        result.provenance = out["steps"]
        result.report = out["report"]
        result.degraded_reasons = (["存在缺口：" + "；".join(out["data_gaps"])]
                                   if out["data_gaps"] else [])
        result.degraded = bool(result.degraded_reasons)
        if out.get("hitl_task"):
            result.hitl = [self._agentic_hitl_card(out["hitl_task"], result.run_id)]
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("[orchestrator] %s 办公助手完成: steps=%d hitl=%d",
                    result.run_id, len(out["steps"]), len(result.hitl))
        return result

    # 专业角色路由表（V3.3）：问题关键词 → 专属智能体（二级路由，覆盖域默认值）。
    # 顺序敏感：更具体的词放前面。
    ROLE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
        ("npi_engineer", ("NPI", "EVT", "DVT", "PVT", "试产", "DFM", "导入进度", "转段")),
        ("ie_engineer", ("工时", "产能测算", "线平衡", "标准工时", "效率改善", "layout", "人力配置")),
        ("me_engineer", ("结构", "模具", "公差", "装配问题", "变形", "断裂", "间隙", "卡扣")),
        ("rd_test", ("软件测试", "固件", "Bug", "bug", "回归测试", "版本验证")),
        ("test_engineer", ("测试报告", "可靠性", "跌落", "高低温", "测试执行", "失效分析", "测试计划")),
        ("project_manager", ("项目里程碑", "项目风险", "资源协调", "跨部门协调", "项目健康度")),
        ("procurement", ("采购", "供应商交期", "供应商绩效", "SCAR", "询价", "比价", "索赔")),
        ("warehouse", ("库存", "仓库", "仓储", "出入库", "盘点", "库龄", "拣货", "发货单")),
        ("ehs", ("EHS", "安全环保", "安全隐患", "危废", "消防", "职业健康", "安全事故")),
        ("customer_service", ("客诉受理", "客服", "客户服务", "售后", "维修服务", "退换货")),
    ]

    def _run_agentic(self, domain, query, user_ctx, run_id):
        """运行域组 Agentic Agent；无对应域（function/None）返回 None 走原兜底。

        二级路由（V3.3）：先按角色关键词匹配专属智能体（NPI/IE/ME/研发测试/TE/PM，
        以及 P1 拆分的采购/仓储/安全环保/客户服务），无命中再落回域默认智能体
        ——17 个智能体共享同一工具层与治理约束。
        """
        from ..agents.agentic import DOMAIN_AGENT_CONFIGS, AgenticDomainAgent

        agentic_domain = None
        for role_key, words in self.ROLE_KEYWORDS:
            if any(w in query for w in words):
                agentic_domain = role_key
                break
        if agentic_domain is None:
            agentic_domain = domain if domain in DOMAIN_AGENT_CONFIGS else None
        if agentic_domain is None:
            return None
        try:
            agent = AgenticDomainAgent(domain=agentic_domain,
                                       orchestrator=self)
            return agent.run(query, user_ctx=user_ctx)
        except Exception as exc:  # noqa: BLE001 —— Agent 异常回落人工兜底（降级不崩）
            logger.warning("[orchestrator] %s agentic 执行失败: %s", run_id, exc)
            return None

    def _agentic_hitl_card(self, hitl_task, run_id):
        """把 Agent 发起的 HITL 工单包装为前端待确认卡（与场景 HITL 卡同构）。"""
        try:
            task = self.runtime.workflow.get(hitl_task["task_uid"])
            d = task.to_dict()
            return {"kind": (d.get("governance") or {}).get("slot") or d.get("wf_id", ""),
                    "prompt": d.get("hitl_prompt") or d.get("title", ""),
                    "task_uid": d["task_uid"], "approver_roles": d.get("assignees") or [],
                    "actions": [], "slot": (d.get("governance") or {}).get("slot", ""),
                    "governance": d.get("governance") or {},
                    "signoff_status": task.signoff_status(),
                    "run_id": run_id, "demo": False}
        except Exception:  # noqa: BLE001
            return {"kind": "", "prompt": hitl_task.get("note", ""),
                    "task_uid": hitl_task.get("task_uid", ""), "approver_roles": [],
                    "actions": [], "slot": "", "governance": {},
                    "signoff_status": None, "run_id": run_id, "demo": False}

    @staticmethod
    def _qa_report(query: str, qr: Any) -> str:
        """把 QueryResult 组装为可读直答（数字来自查询，带口径与脱敏说明）。"""
        if qr.fallback == "manual_ticket":
            return ("该问题需要人工跟进（明细探查通道当前不可用），"
                    "已生成人工兜底工单。")
        lines: list[str] = []
        channel_cn = _QA_CHANNEL_CN.get(qr.channel, qr.channel)
        lines.append(f"已通过{channel_cn}查询（{len(qr.rows or [])} 行）：")
        for i, row in enumerate((qr.rows or [])[:5], 1):
            kv = "；".join(f"{k}={v}" for k, v in list(row.items())[:6])
            lines.append(f"{i}. {kv}")
        if len(qr.rows or []) > 5:
            lines.append(f"…共 {len(qr.rows)} 行，可在数据溯源中查看完整结果")
        if qr.masked:
            lines.append("（结果含敏感字段，已按你的岗位权限脱敏）")
        for w in (qr.warnings or [])[:2]:
            lines.append(f"注意：{w}")
        return "\n".join(lines)

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
        "spc_anomaly_v2": "回流焊峰值温度过程能力Cpk是否越限",
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
