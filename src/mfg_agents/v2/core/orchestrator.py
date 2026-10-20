"""V2Orchestrator：统一编排入口（IM / Web / 定时任务三种触发都走这里）。

收敛 V1 技术债：api.py 原先手工顺序编排、未使用 build_graph() 编译图；
V2 统一为 两级路由 → 场景注册表 → YamlDagExecutor（LangGraph 子图）。

总控边界纪律（ARCHITECTURE-V2.md §3.2）：
不直接查数据库（必须经 Text2SQLService）、不直接发通知（必须经
NotificationService）、不做审批判断（必须经 WorkflowEngine）。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...common.config import settings
from ..contracts.data_source import DataSourceRegistry, SQLiteDataSource
from ..contracts.scenario import ScenarioRegistry
from .node_registry import build_handlers
from .router import RouteResult, TwoLevelRouter
from .shared_context import SharedContext
from .yaml_dag import DagRunResult, YamlDagExecutor
from ..services.notification import (
    BoardNotificationChannel,
    IMGroupNotificationChannel,
    LogNotificationChannel,
    MultiChannel,
    NotificationService,
)
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
# + 制造/品质看板万行体量扩充 + 不良柏拉图（日/月别 · 类别/工段）扩充
# + 不良柏拉图产品分类（消费电子/智能电源/储能电源/车载）扩充 + 订单履约体量扩充
_SEED_SQLS = [
    _DATA_DIR / "seed_v3.sql",
    _DATA_DIR / "seed_station_detail.sql",
    _DATA_DIR / "seed_inventory.sql",
    _DATA_DIR / "seed_board_mfg_volume.sql",
    _DATA_DIR / "seed_quality_pareto.sql",
    _DATA_DIR / "seed_quality_pareto_pcat.sql",
    _DATA_DIR / "seed_board_orders_volume.sql",
    _DATA_DIR / "seed_std_three_rates.sql",
    _DATA_DIR / "seed_incoming_defect.sql",
    _DATA_DIR / "seed_std_team_cause.sql",
    # V3.9：制造看板体量/维度二次扩容（8 线 × 12 型号、9 月历史、station_detail
    # 全覆盖、整机 ASSY-1、equipment_run_time 停机原因）；见 seed_board_mfg_volume2.sql
    _DATA_DIR / "seed_board_mfg_volume2.sql",
    # V3.10：SPC 测量值维度扩量（工段 / 产品分类 + 全月覆盖 10-01~10-15）；
    # 由 tools/gen_spc_dimensions_seed.py 生成。须排在 seed_v3.sql 之后
    # （表已建 + _ensure_columns 已补列），否则新列无处可写。
    _DATA_DIR / "seed_spc_dimensions.sql",
]


def _ensure_columns(conn) -> None:
    """PRAGMA 守卫的幂等补列（老演示库不重建，就地升级）。

    在**每个种子文件执行前**调用一次：表尚未创建时 ``PRAGMA table_info`` 为空集则跳过，
    待前置种子建表后再回到这里补列——因此对「老库升级」与「全新库首建」都成立
    （全新库：seed_v3 建表 → 下一次调用补列 → 后续种子即可写入新列）。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(quality_defect)")}
    if cols and "process_stage" not in cols:
        conn.execute("ALTER TABLE quality_defect ADD COLUMN process_stage TEXT")
    if cols and "defect_category" not in cols:
        conn.execute("ALTER TABLE quality_defect ADD COLUMN defect_category TEXT")
    if cols and "product_category" not in cols:
        conn.execute("ALTER TABLE quality_defect ADD COLUMN product_category TEXT")
    # 标准三率两级看板：老库补齐工段 / 班组两列（同法 PRAGMA 守卫，表不存在则跳过）
    std_cols = {r[1] for r in conn.execute("PRAGMA table_info(std_standard_audit)")}
    if std_cols and "process_stage" not in std_cols:
        conn.execute("ALTER TABLE std_standard_audit ADD COLUMN process_stage TEXT")
    if std_cols and "team" not in std_cols:
        conn.execute("ALTER TABLE std_standard_audit ADD COLUMN team TEXT")
    # V3.9：设备运行补 ``stop_reason``（停机原因，8 类）供制造侧原因柏拉图事实表
    eq_cols = {r[1] for r in conn.execute("PRAGMA table_info(equipment_run_time)")}
    if eq_cols and "stop_reason" not in eq_cols:
        conn.execute("ALTER TABLE equipment_run_time ADD COLUMN stop_reason TEXT")
    # SPC 过程能力看板：老库补齐 工段 / 产品分类 两列（同法 PRAGMA 守卫，表不存在则跳过）
    # 说明：``CREATE TABLE IF NOT EXISTS`` 对老库不生效，不在此补列则老库永远缺这两列，
    # 板级「工段 / 产品分类」筛选将无数据可筛。
    spc_cols = {r[1] for r in conn.execute("PRAGMA table_info(spc_measurement)")}
    if spc_cols and "process_stage" not in spc_cols:
        conn.execute("ALTER TABLE spc_measurement ADD COLUMN process_stage TEXT")
    if spc_cols and "product_category" not in spc_cols:
        conn.execute("ALTER TABLE spc_measurement ADD COLUMN product_category TEXT")


def _apply_seed(db_path: str) -> None:
    """执行演示数据扩充（幂等 INSERT OR IGNORE；只补历史数据不碰红线行）。

    V3.1：员工自由提问需要"有东西可查"——扩充历史批次/客诉/产出/设备/
    停线数据（2026-10-08 ~ 10-14），"今天"（10-15）的行与场景锚点一律不动，
    由 tests/v2/test_scenarios_v2.py 的数据红线测试守护。
    V3.5：追加 ``seed_station_detail.sql``，把六指标按 工段/工序/工位×班次
    拆分入库（拆分式，求和回原值），供三级看板下钻。
    V3.6：追加 ``seed_board_mfg_volume.sql``（制造四表万行）+ ``seed_quality_pareto.sql``
    （不良柏拉图日/月别·类别/工段数据集）；老演示库不重建，故先做 **PRAGMA 守卫的
    幂等 ALTER** 为 ``quality_defect`` 补 ``process_stage`` / ``defect_category`` 两列。
    V3.7：追加 ``seed_quality_pareto_pcat.sql``（柏拉图产品分类：消费电子/智能电源/
    储能电源/车载，含老行幂等回填 UPDATE）；同法补 ``product_category`` 一列。
    V3.8：追加 ``seed_std_three_rates.sql``——标准三率改为「1 级 = 4 工段 / 2 级 = 各班组」
    两级看板，同法为 ``std_standard_audit`` 补 ``process_stage`` / ``team`` 两列，
    并按 4 工段 × 3 班组 × 17 工序 × 15 天补齐班组级审核明细（765 行）。
    """
    import sqlite3

    seeds = [p for p in _SEED_SQLS if p.exists()]
    if not seeds:
        return
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=10.0) as conn:
            # 幂等补列：老库（已存在表但缺新列）就地升级，跨 SQLite 版本安全
            # （不使用 ADD COLUMN IF NOT EXISTS）；表不存在时空集 → 跳过。
            # 每个种子文件执行前补一次：覆盖「老库升级」与「全新库先建表后补列」两种路径。
            for p in seeds:
                _ensure_columns(conn)
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
                # 行数上限：数据速查四块各 200+ 行（材料 248 / 成品 241 / 来料 250），
                # 默认 200 会按 ORDER BY 截断尾部（如料号 U–Z 丢失）。仍保留上限防全表拉取。
                row_cap=500,
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
            # 通知通道并联：日志留痕（demo outbox，既有行为不变）+ 现场看板
            # （仅当 target 命中工段白名单才落推送行见 BoardNotificationChannel）
            # + 现场群（按 im_groups.yaml 把工段解析到企微群，逐条记账：
            # 未配 IM_GROUP_KEY_<SLUG> 时如实 skipped，只落站内，不静默丢弃；
            # 配了企业应用凭据时，群内「责任人」投递走真实应用消息 API）。
            notifier=NotificationService(MultiChannel([
                LogNotificationChannel(),
                BoardNotificationChannel(),
                IMGroupNotificationChannel(gateway=_build_im_push_gateway(settings)),
            ])),
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
    "board": "看板口径直查（与看板同源）",
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
    # ---- 以下为补齐节点：此前缺失，降级原因里会漏出英文 key（如 business_order / warehouse_atp）----
    # 场景结构化节点
    "intake": "受理", "fanout": "并行分发", "fanin": "结果汇总",
    "sqe_assess": "SQE 评估", "equipment_check": "设备检查",
    "manufacturing": "制造",
    # 出货风险场景数据节点
    "business_order": "订单/交期", "warehouse_atp": "仓库 ATP",
    "manufacturing_output": "制造产出", "logistics": "物流",
    "atp_analysis": "ATP 分析",
    # 工单 / 流程节点
    "impact_wf": "停线影响工单", "scar_wf": "SCAR 工单",
    "capa_flow": "8D 整改工单", "maintenance_flow": "维保工单",
    "change_flow": "订单变更工单", "delivery_risk_wf": "出货风险工单",
    # HITL 节点（与 7 个 HITL 槽位一一对应）
    "hitl_restart": "复线决策确认", "hitl_eta": "对客 ETA 确认",
    "hitl_disposition": "来料处置确认", "hitl_d8_close": "8D 关闭确认",
    "hitl_complaint_reply": "对客回复确认", "hitl_delivery_commit": "交期承诺确认",
    "hitl_maintenance_window": "停机窗口确认",
    # 报告节点
    "report_incoming": "来料异常简报", "report_equipment": "设备预警报告",
    "report_delivery": "出货风险简报", "report_complaint": "客诉 8D 报告",
}


class V2Orchestrator:
    """智能总控（V2 骨架）：唯一任务入口与出口。"""

    def __init__(self, runtime: V2Runtime) -> None:
        self.runtime = runtime

    def handle(self, query: str, user_ctx: Optional[dict] = None,
               sink: Optional[Any] = None,
               ask_ctx: Optional[dict] = None) -> OrchestratorResult:
        """处理一次请求：路由 → 场景 DAG → 聚合（含 provenance/HITL/降级标记）。

        ``ask_ctx``（V4 结构化问数上下文）：前端各模块问数入口并行携带的
        module / kpi / window / filters / view——看板问数命中 KPI 时直接复用
        看板聚合函数（口径同源、毫秒级），未命中则静默回落自然语言链路。
        """
        t0 = time.perf_counter()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        route = self.runtime.router.route(query, user_ctx)
        result = OrchestratorResult(
            query=query, run_id=run_id, route=route.to_dict(),
            scenario_key=route.scenario_key)
        if ask_ctx:
            # 审计留痕：结构化上下文并入 route（前端结果卡「执行方式」可见来源）
            result.route = dict(result.route, ask_ctx=ask_ctx)
        result.degraded_reasons = self._degraded_reasons(route)

        # 通道⓪：看板口径直查——**早于策略分派**。结构化上下文（screen+kpi）
        # 是比文本关键词更强的意图信号：用户从看板点某个 KPI 进来，哪怕文案里
        # 含"交期/发货"等场景触发词（会被 L0 判成 scenario），也应回该看板的口径
        # 而不是被文本抢走跑去跑重型链路。命中即以 strategy="board" 返回。
        if ask_ctx:
            board = self._try_board_ask(result, query, user_ctx, ask_ctx, t0)
            if board is not None:
                return board

        if route.strategy == "chitchat":
            return self._handle_chitchat(result, query, t0)

        if route.strategy == "qa":
            return self._handle_qa(result, query, user_ctx, t0, sink=sink)

        if route.strategy == "office":
            return self._handle_office(result, query, user_ctx, t0)

        if route.strategy == "digest":
            return self._handle_digest(result, query, user_ctx, t0)

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
        # 降级原因用业务语言表达，并把多个环节合并成一句——逐节点重复
        # 「…环节数据不完整」会把一段人话读成复读机（也会漏出 node 英文 key）。
        degraded_labels = [_NODE_CN.get(node, node) for node in dag.degraded_nodes]
        if degraded_labels:
            if len(degraded_labels) == 1:
                head = f"{degraded_labels[0]}环节"
            else:
                head = f"{'、'.join(degraded_labels)}等 {len(degraded_labels)} 个环节"
            result.degraded_reasons.append(
                f"{head}数据不完整（缺少数据源），已标注缺口并输出可复核结论")
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

    def _try_board_ask(self, result: OrchestratorResult, query: str,
                       user_ctx: Optional[dict], ask_ctx: dict,
                       t0: float) -> Optional[OrchestratorResult]:
        """看板口径直查（通道⓪）：命中返回结果，未命中返回 None。

        复用看板聚合函数（dashboard.build_*_dashboard）——口径与看板完全同源，
        不经 LLM、不重写 SQL；权限与脱敏走看板同一套 data_scope 裁决。
        """
        from ..services.board_qa import answer_board_ask

        board = answer_board_ask(self.runtime.data_registry, ask_ctx, user_ctx)
        if board is None:
            return None
        result.route = dict(result.route, strategy="board")
        result.internal_brief = {
            "回答通道": _QA_CHANNEL_CN.get(board.channel, board.channel),
            "数据行数": len(board.rows or []),
            "口径来源": "与看板同源（dashboard 聚合函数）",
            "脱敏": board.masked,
        }
        result.provenance = [board.to_dict()]
        result.report = self._qa_report(query, board)
        result.degraded_reasons = list(board.warnings or [])
        result.degraded = bool(result.degraded_reasons)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("[orchestrator] %s 看板口径直查: kpi=%s view=%s %.1fms",
                    result.run_id, ask_ctx.get("kpi"), ask_ctx.get("view"),
                    result.elapsed_ms)
        return result

    def _handle_qa(self, result: OrchestratorResult, query: str,
                   user_ctx: Optional[dict], t0: float,
                   sink: Optional[Any] = None) -> OrchestratorResult:
        """数据直答（qa 策略）：轻量路径——不跑场景 DAG、不建工单、无 HITL。

        头脑风暴结论（2026-10-17）："今天A型号的质量怎么样"这类查询型问题
        被硬套客诉 8D 链路（10 节点+双 HITL+工单）是答非所问。简单数据
        查询走 Text2SQL 统一通道（指标库→注册查询→明细探查）直接回答。

        V4 递进：① 直答能答就答；② 直答全失败且命中 ≥2 个域信号 → 协同
        规划器并行取证（跨域结论分节汇总）；③ 单域 → Agentic 域智能体接管。
        （看板口径直查已在 handle() 早于策略分派处执行，不在此重复。）
        """
        t2s = self.runtime.text2sql
        qr = t2s.query(query, user_ctx=user_ctx)
        # 三层递进：直答能答就直接答（指标/注册/明细）→ 跨域协同（≥2 域信号，
        # 多域并行取证）→ 单域 Agentic 接管 → 人工
        light_hit = (qr.channel in ("metric", "registered", "detail")
                     and qr.rows and qr.fallback is None)
        if not light_hit and qr.fallback == "manual_ticket":
            collab = self._run_collaborative(query, user_ctx, sink)
            if collab is not None:
                result.route = dict(result.route, strategy="collab")
                result.internal_brief = {
                    "执行方式": "多域协同取证（Planner 并行编排）",
                    "参与域": "、".join(collab["domains"]),
                    "工具调用步数": len(collab["steps"]),
                    "数据缺口": "；".join(collab["data_gaps"]) or "无",
                }
                result.provenance = collab["steps"]
                result.report = collab["report"]
                result.degraded_reasons = (["存在数据缺口：" + "；".join(collab["data_gaps"])]
                                           if collab["data_gaps"] else [])
                result.degraded = bool(result.degraded_reasons)
                result.hitl = [self._agentic_hitl_card(t, result.run_id)
                               for t in collab["hitl_tasks"]]
                result.elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.info("[orchestrator] %s 协同取证完成: domains=%s steps=%d",
                            result.run_id, collab["domains"], len(collab["steps"]))
                return result
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

    def _handle_digest(self, result: OrchestratorResult, query: str,
                       user_ctx: Optional[dict], t0: float) -> OrchestratorResult:
        """跨域态势汇总（digest）：回答"本月度最需关注什么"这类决策支持型提问。

        这类问题既无场景触发词也无具体实体——旧行为落到 chitchat（只回一句问候）
        或被隐式指派到某个场景跑重型 DAG。本通道给出**有数据支撑**的"最需关注"清单：
        各看板未达标 KPI（口径与看板同源）+ SPC 能力不足/不可判定 + 待我确认的工单。

        只读：不建工单、不发起 HITL；无权看板如实标注"不可见"，缺口如实标注。
        """
        from ..services.digest import build_digest, pick_window

        roles = [str(r) for r in ((user_ctx or {}).get("roles") or []) if str(r)]
        window = pick_window(query)
        data = build_digest(self.runtime.data_registry, self.runtime.workflow,
                           roles, window=window, query=query)
        if data is None:
            # 无任何可见看板：不臆造态势，如实说明并回落（转人工/引导）
            result.fallback = "manual_ticket"
            result.report = ("当前岗位没有可见的看板数据范围，无法给出态势汇总。"
                            "如需查看，请申请相应数据权限或联系对应岗位。")
            result.suggestions = self._route_suggestions(query)
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            return result

        items = data["items"]
        win_meta = data.get("window") or {}
        win_label = f"{win_meta.get('label') or ''}"
        if win_meta.get("start") and win_meta.get("end"):
            win_label += f"（{win_meta.get('start')} ~ {win_meta.get('end')}）"
        lines: list[str] = []
        if items:
            lines.append(f"按当前口径（{win_label or '最近7天'}）共 "
                         f"{data['total_not_ok']} 项未达标，最需关注：")
            for i, it in enumerate(items, 1):
                flag = "严重" if it["status"] == "danger" else "关注"
                gap_txt = f"，缺口 {abs(it['gap'])}pt" if it.get("gap") else ""
                lines.append(f"{i}. 【{flag}】{it['board']} · {it['label']} "
                             f"{it['value']}（目标 {it['target']}{gap_txt}）"
                             + (f" —— {it['note']}" if it.get("note") else ""))
        else:
            lines.append(f"按当前口径（{win_label or '最近7天'}）各看板指标均已达标，"
                         "暂无明显需要升级的项。")
        for note in data.get("spc_notes") or []:
            lines.append(f"· {note}")
        todo = data.get("todo") or {}
        if todo.get("mine"):
            lines.append(f"· 有 {todo['mine']} 项待你确认的工单（见审批确认中心）")
        elif todo.get("visible"):
            lines.append(f"· 治理域内有 {todo['visible']} 项待办（多不在你的处置范围）")
        if data.get("skipped"):
            lines.append(f"· 以下看板当前岗位不可见，未纳入汇总："
                         f"{'、'.join(data['skipped'])}")
        for gap in data.get("gaps") or []:
            lines.append(f"· 数据缺口：{gap}")
        lines.append("（需要逐项下钻时，点对应看板的卡片；或把问题带上型号 / 线体 / "
                     "批号等具体对象，走对象级链路。）")

        result.internal_brief = {
            "回答通道": "跨域态势汇总（汇总问数）",
            "覆盖看板": "、".join(data["boards"]),
            "未达标项": data["total_not_ok"],
            "待我确认": todo.get("mine", 0),
            "口径": win_label or "最近7天",
        }
        result.provenance = [{
            "channel": "digest", "boards": data["boards"],
            "window": win_meta, "not_ok": data["total_not_ok"],
            "items": items,
            "source": "各看板聚合函数（与看板同源口径）+ SPC 能力 + 治理待办",
        }]
        result.report = "\n".join(lines)
        result.degraded_reasons = list(data.get("gaps") or [])
        result.degraded = bool(result.degraded_reasons)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("[orchestrator] %s 态势汇总: boards=%s 未达标=%d %.1fms",
                    result.run_id, data["boards"], data["total_not_ok"],
                    result.elapsed_ms)
        return result

    def _handle_office(self, result: OrchestratorResult, query: str,
                       user_ctx: Optional[dict], t0: float) -> OrchestratorResult:
        """办公助手通道：独立 Agentic Agent 接管会议室预订/订餐等办公事务。

        Agent 自主决定调哪些办公工具、何时对高影响事项发起 HITL；执行或失败
        均如实回写（含数据缺口）——失败回落人工兜底，降级不崩。
        """
        from ...common.llm_factory import llm_available
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

    def _run_collaborative(self, query, user_ctx, sink):
        """跨域协同取证（V4）：≥2 个域信号命中时由协同规划器并行编排。

        Planner 拆解 ≤4 个单域子任务 → 多域智能体并行 ReAct 取证 → 汇总器
        分节聚合（冲突不仲裁、缺口如实标注）。子智能体之间不互相调用、
        不递归触发 Planner——协同结构恒为「规划器 → 执行者」浅层星型。
        未达协同条件或全部失败时返回 None，回落单域 Agentic / 人工兜底。
        """
        from .planner import CollaborativePlanner

        planner = CollaborativePlanner(self)
        if not planner.should_collaborate(query):
            return None
        try:
            return planner.run(query, user_ctx=user_ctx, sink=sink)
        except Exception as exc:  # noqa: BLE001 —— 协同失败回落原链路（降级不崩）
            logger.warning("[orchestrator] 协同取证执行失败: %s", exc)
            return None

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
        """把 QueryResult 组装为可读直答（数字来自查询，带口径与脱敏说明）。

        **首行必须是业务结论**：前端取首句当卡片标题（``terms.headline``）。
        此前首行恒为「已通过{通道}查询（N 行）：」这类实现细节——标题里没有答案
        （截图反馈：OTD 直答标题写成"已通过指标库（口径统一）查询（1 行）"）。
        单行结果（指标库命中 / 看板口径直查）改由 :meth:`_single_row_report`
        出结论句；多行明细仍走「查询通道 + 逐行列出」。
        """
        if qr.fallback == "manual_ticket":
            return ("该问题需要人工跟进（明细探查通道当前不可用），"
                    "已生成人工兜底工单。")
        rows = qr.rows or []
        if len(rows) == 1 and isinstance(rows[0], dict):
            verdict = V2Orchestrator._single_row_report(qr, rows[0])
            if verdict:
                return verdict
        lines: list[str] = []
        channel_cn = _QA_CHANNEL_CN.get(qr.channel, qr.channel)
        lines.append(f"已通过{channel_cn}查询（{len(rows)} 行）：")
        for i, row in enumerate(rows[:5], 1):
            kv = "；".join(f"{k}={v}" for k, v in list(row.items())[:6])
            lines.append(f"{i}. {kv}")
        if len(rows) > 5:
            lines.append(f"…共 {len(rows)} 行，可在数据溯源中查看完整结果")
        if qr.masked:
            lines.append("（结果含敏感字段，已按你的岗位权限脱敏）")
        for w in (qr.warnings or [])[:2]:
            lines.append(f"注意：{w}")
        return "\n".join(lines)

    @staticmethod
    def _single_row_report(qr: Any, row: dict) -> str:
        """单行直答 → 结论句 + 口径/明细行；无法判定口径时返回 ``""``。

        只对**口径已知**的单行结果拼结论：看板口径直查（行内含「指标/值/目标/状态」）
        与指标库命中（provenance 带 metric_name/unit/target/window）。其余单行
        （注册查询 / 明细探查）保持通用列表——不把列名当结论臆造。
        """
        prov = qr.provenance or {}
        if qr.channel == "board" and row.get("指标") and row.get("值"):
            head = V2Orchestrator._verdict_headline(
                str(row.get("口径") or ""), str(row.get("指标")),
                str(row.get("值")), row.get("状态"), row.get("目标"))
            lines = [head]
            for k, v in row.items():
                if k in ("指标", "值", "目标", "状态", "口径") or not v:
                    continue
                lines.append(f"{k}：{v}")
            return "\n".join(lines)
        if qr.channel == "metric" and prov.get("metric_name"):
            unit = str(prov.get("metric_unit") or "")
            first = next(iter(row.values()), "")
            value = (f"{first}{unit}"
                     if unit and not str(first).endswith(unit) else str(first))
            # 窗口标签只在指标 SQL 真的按窗口取数时才有意义（如 OTD 的 :start/:end）；
            # 否则「最近7天 来料检验合格率」会暗示一个并不存在的口径。
            scoped = ":start" in str(qr.sql or "")
            win = prov.get("window") or {}
            head = V2Orchestrator._verdict_headline(
                str(win.get("label") or "") if scoped else "",
                str(prov.get("metric_name") or ""), value, None,
                prov.get("metric_target"), unit,
                bool(prov.get("metric_higher_is_better", True)))
            lines = [head]
            if prov.get("metric_desc"):
                lines.append(f"口径：{prov['metric_desc']}")
            if scoped and win.get("start") and win.get("end"):
                lines.append(f"统计窗口：{win['start']} ~ {win['end']}")
            rest = list(row.items())[1:]
            if rest:
                lines.append("数据明细：" + " · ".join(f"{k} {v}" for k, v in rest))
            if prov.get("owner"):
                lines.append(f"指标归属：{prov['owner']}")
            return "\n".join(lines)
        return ""

    @staticmethod
    def _verdict_headline(scope: str, name: str, value: str,
                          status: Any = None, target: Any = None,
                          unit: str = "", higher: bool = True) -> str:
        """结论句：「{窗口} {指标} {数值}（达标状态，目标 ≥…）」。"""
        head = " ".join(b for b in (scope, name, value) if b)
        target_txt = ""
        if target not in (None, ""):
            t_val = (f"{target}{unit}"
                     if unit and not str(target).endswith(unit) else str(target))
            target_txt = f"目标 {'≥' if higher else '≤'}{t_val}"
        state = str(status or "") or (
            V2Orchestrator._verdict_state(value, target, higher) if target_txt else "")
        suffix = "，".join(b for b in (state, target_txt) if b)
        return f"{head}（{suffix}）" if suffix else head

    @staticmethod
    def _verdict_state(value: str, target: Any, higher: bool = True) -> str:
        """数值 vs 目标 → 「达标 / 未达标」（解析不出数字则不给判定，不臆造）。"""
        try:
            v = float(re.findall(r"-?\d+(?:\.\d+)?", str(value))[0])
            t = float(target)
        except (ValueError, IndexError, TypeError):
            return ""
        return "达标" if (v >= t if higher else v <= t) else "未达标"

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
        "order_change_impact_v2": "订单交期提前两周是否可行",
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


def _build_im_push_gateway(settings_obj: Any) -> Any:
    """按**企业应用凭据**构造真实推送网关（应用消息 / @ 责任人）；缺凭据返回 ``None``。

    需要 corp_id（缺省回落 ``IM_APPID``：回调侧该字段即企业 CorpID）
    + ``IM_AGENT_ID`` + ``IM_CORP_SECRET``。三者不齐返回 ``None``——现场群通道据此
    如实记账为 failed 并写明原因，**不静默、也不假装 @ 到了人**。

    返回的是 ``WeComPushClient``（与 ``IMGateway.push`` 同签名），因此"只有推送凭据、
    没有回调凭据"的部署不必为了推送去凑 AES 密钥。
    """
    corp_id = str(getattr(settings_obj, "IM_CORP_ID", "")
                  or getattr(settings_obj, "IM_APPID", "") or "")
    agent_id = str(getattr(settings_obj, "IM_AGENT_ID", "") or "")
    secret = str(getattr(settings_obj, "IM_CORP_SECRET", "") or "")
    if not (corp_id and agent_id and secret):
        return None
    from ..contracts.adapters.wecom import WeComPushClient

    client = WeComPushClient(corp_id, agent_id, secret)
    logger.info("[runtime] 企业应用凭据已就绪：现场群「责任人」投递走真实应用消息")
    return client


def create_default_orchestrator() -> V2Orchestrator:
    """便捷工厂：装配默认运行时（demo / API 入口用）。"""
    return V2Orchestrator(V2Runtime.create_default())


__all__ = ["V2Orchestrator", "V2Runtime", "OrchestratorResult",
           "create_default_orchestrator"]
