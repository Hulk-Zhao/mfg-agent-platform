"""4 场景离线端到端验收（S2/S3/S4/S8）：路由 + DAG 全链 + degraded + HITL + provenance。

覆盖（§7.4 第 12 条测试纪律）：
1. 5 场景 L0 路由矩阵（含 line_stop 回归）；
2. 每场景：场景 key、关键结论、HITL 状态（waiting_hitl）、工单、provenance（SQL 原文+命中行）；
3. degraded 铁律：LLM 离线时全链可跑，结论标 degraded、数字来自 SQL（不编造）；
4. data_gap 可见（S3 工程根因 / S8 参数·备件无数据源）；
5. **数据红线回归**：material_batch 新增行不得破坏 line_stop 的"无来料异常"与脱敏断言。
"""
import sqlite3

import pytest

from mfg_agents.common.config import settings
from mfg_agents.common.llm_factory import reset_probe_cache
from mfg_agents.v2.core.orchestrator import V2Orchestrator, V2Runtime

DB_PATH = "data/mfg_agent.db"

USER_CTX = {"user_id": "u-gm", "dept": "management", "roles": ["coordinator"]}

# SPC 场景需合法制造/质量身份（HITL 处置决策的发起/会签角色来源）
SPC_CTX = {"user_id": "u1", "dept": "manufacturing",
           "roles": ["manufacturing", "quality"]}

# 5 个快捷问题 → 期望场景/域（L0 触发词路由证据）
QUICK_ROUTES = [
    ("A型号在SMT-1停线，何时复线？", "line_stop_v2", "manufacturing"),
    ("今天锡膏批次有问题", "incoming_quality_v2", "quality"),
    ("华为投诉BATCH-A-1008", "customer_complaint_v2", "quality"),
    ("A型号能发多少货", "delivery_status_v2", "supply_chain"),
    ("SMT-1设备参数异常吗", "equipment_warning_v2", "manufacturing"),
]


@pytest.fixture()
def orchestrator(monkeypatch, tmp_path) -> V2Orchestrator:
    """离线 orchestrator：LLM 探针强制 False，工作流库指向临时 DB。"""
    reset_probe_cache()
    for mod in ("mfg_agents.common.llm_factory",
                "mfg_agents.v2.core.router",
                "mfg_agents.v2.core.node_registry"):
        monkeypatch.setattr(f"{mod}.llm_available", lambda: False)
    monkeypatch.setattr(settings, "WORKFLOW_DB",
                        f"sqlite:///{tmp_path / 'workflow.db'}")
    return V2Orchestrator(V2Runtime.create_default())


# ---------- 1. 路由矩阵（5 快捷问题逐条命中） ----------

@pytest.mark.parametrize("query,scenario,domain", QUICK_ROUTES)
def test_l0_routing_matrix_five_scenarios(orchestrator, query, scenario, domain):
    result = orchestrator.handle(query, USER_CTX)
    assert result.route["level"] == "L0"
    assert result.scenario_key == scenario
    assert result.route["domain"] == domain
    assert result.route["confidence"] == 1.0


# ---------- 2. S2 来料异常联动 ----------

def test_s2_incoming_quality_end_to_end(orchestrator):
    result = orchestrator.handle("今天锡膏批次有问题", USER_CTX)
    assert result.error is None
    assert result.scenario_key == "incoming_quality_v2"
    brief = result.internal_brief
    assert "不合格批次" in brief["来料不合格判定"]
    assert brief["缺陷等级"] == "A"
    assert "退货" in brief["处置建议"]
    # HITL：退换货/特采决策 waiting_hitl（绝不自动放行）
    assert len(result.hitl) == 1
    assert result.hitl[0]["kind"] == "disposition"
    assert result.hitl[0]["status"] == "waiting_hitl"
    # 工单：SCAR
    assert result.workflow_tasks[0]["ticket"]["wf_id"] == "scar_flow"
    # provenance：真实 SQL（不合格批次 + 供应商统计）
    names = {p.get("query_name") for p in result.provenance}
    assert {"iqc_batches_recent", "supplier_followup"} <= names
    iqc = [p for p in result.provenance
           if p.get("query_name") == "iqc_batches_recent"][0]
    assert iqc["row_count"] == 2           # MAT-SP-1014 / MAT-IC-1011
    assert iqc["masked"] is True           # supplier 按角色脱敏
    assert result.degraded is True


# ---------- 3. S3 客户投诉 8D ----------

def test_s3_customer_complaint_8d_end_to_end(orchestrator):
    result = orchestrator.handle("华为投诉BATCH-A-1008", USER_CTX)
    assert result.error is None
    assert result.scenario_key == "customer_complaint_v2"
    brief = result.internal_brief
    assert "华为终端" in brief["8D报告"]
    # 8D 骨架 D1-D8 齐全（模板合成）
    for step in ("D1_team", "D2_problem", "D4_root_cause", "D8_closure"):
        assert step in brief["8D报告"]
    # 双 HITL
    assert {h["kind"] for h in result.hitl} == {"d8_close", "complaint_reply"}
    assert all(h["status"] == "waiting_hitl" for h in result.hitl)
    assert result.workflow_tasks[0]["ticket"]["wf_id"] == "complaint_8d_flow"
    # 过程分析来自真实 SQL（BATCH-A-1008 → 贴片偏移）
    assert "贴片偏移" in brief["过程分析"]
    # data_gap 可见（工程根因无 BOM 数据源）；H-1：去重且保留来源域、无 report 域冗余
    gaps = [p for p in result.provenance if p.get("channel") == "data_gap"]
    assert len(gaps) == 1                       # 唯一真实缺口=工程根因
    assert gaps[0]["domain"] == "rd_engineering"
    assert all(g.get("domain") != "report" for g in gaps)
    assert "设计根因" in (gaps[0].get("note") or "")
    assert result.degraded is True


# ---------- 4. S4 出货风险协同（ATP 代码计算） ----------

def test_s4_delivery_atp_end_to_end(orchestrator):
    result = orchestrator.handle("A型号能发多少货", USER_CTX)
    assert result.error is None
    assert result.scenario_key == "delivery_status_v2"
    # 六域并行取证
    queried = {p.get("query_name") for p in result.provenance}
    assert {"orders_by_model", "warehouse_available", "shipment_status",
            "output_by_model"} <= queried
    # ATP 由代码计算（on_hand - reserved）
    compute = [p for p in result.provenance if p.get("channel") == "compute"]
    assert compute and compute[0]["formula"] == "on_hand - reserved"
    assert compute[0]["available_qty"] == 400      # 1200 - 800
    assert compute[0]["gap_qty"] == 3100           # (2000+1500) - 400
    assert "缺口=3100pcs" in result.internal_brief["ATP承诺分析"]
    # HITL：对客交期承诺
    assert result.hitl[0]["kind"] == "delivery_commit"
    assert result.workflow_tasks[0]["ticket"]["wf_id"] == "delivery_risk_flow"
    assert result.report                              # 对客脱敏报告
    assert "交期" in result.report
    assert result.degraded is True


# ---------- 5. S8 设备预警与维护协同 ----------

def test_s8_equipment_warning_end_to_end(orchestrator):
    result = orchestrator.handle("SMT-1设备参数异常吗", USER_CTX)
    assert result.error is None
    assert result.scenario_key == "equipment_warning_v2"
    brief = result.internal_brief
    assert "刮刀压力" in brief["设备告警"]
    assert "维保建议 3 项" == brief["维保建议"]
    assert result.hitl[0]["kind"] == "maintenance_window"
    assert result.hitl[0]["status"] == "waiting_hitl"
    assert result.workflow_tasks[0]["ticket"]["wf_id"] == "maintenance_flow"
    # 工程参数/备件 → data_gap 可见（不编造）；H-1：去重 + 保留来源域（无 report 域）
    gaps = [p for p in result.provenance if p.get("channel") == "data_gap"]
    notes = " ".join((g.get("note") or "") for g in gaps)
    assert "参数基线" in notes and "备件" in notes
    assert len(gaps) == 2                       # 参数基线 + 备件，各 1 条
    assert {g["domain"] for g in gaps} == {"rd_engineering", "supply_chain"}
    assert all(g.get("domain") != "report" for g in gaps)
    assert len({g["note"] for g in gaps}) == 2  # 无重复 note
    assert brief.get("数据缺口")
    assert result.degraded is True


# ---------- 7. S6 过程能力异常预警（SPC） ----------

SPC_QUERY = "回流焊峰值温度过程能力Cpk是否越限"


def test_s6_spc_trigger_routes_to_scenario(orchestrator):
    """触发词「过程能力/Cpk/越限」命中 L0 路由 → spc_anomaly_v2（quality 域）。"""
    result = orchestrator.handle(SPC_QUERY, SPC_CTX)
    assert result.route["level"] == "L0"
    assert result.scenario_key == "spc_anomaly_v2"
    assert result.route["domain"] == "quality"
    assert result.route["confidence"] == 1.0


def test_s6_spc_scenario_dag_steps(orchestrator):
    """场景 DAG 步骤集合/数量正确；fan_out 条件分支声明 spc_alert + else。"""
    meta = orchestrator.runtime.scenario_registry.get("spc_anomaly_v2")
    assert len(meta.steps) == 6
    assert {s["id"] for s in meta.steps} == {
        "intake", "spc_capability", "fanout", "fanin",
        "hitl_spc_action", "report_spc"}
    fanout = next(s for s in meta.steps if s["id"] == "fanout")
    assert fanout["condition"] == "spc_alert"
    assert fanout["else"] == "report_spc"
    assert [b["id"] for b in fanout["branches"]] == ["spc_pareto"]
    assert set(meta.outputs) == {"spc_capability_result", "spc_pareto_result",
                                 "spc_action_ticket", "internal_brief"}


def test_s6_spc_alert_branch_hits_pareto_and_hitl(orchestrator):
    """越限（演示库 Cpk 含不合格/偏低）→ 走 spc_pareto 分支 + 到达 spc_action 槽位。"""
    result = orchestrator.handle(SPC_QUERY, SPC_CTX)
    assert result.error is None
    assert result.scenario_key == "spc_anomaly_v2"
    nodes = {p["node"] for p in result.provenance if p.get("channel") == "node_timing"}
    assert "spc_pareto" in nodes
    assert {"spc_capability", "hitl_spc_action"} <= nodes
    # HITL 到达 spc_action 槽位（绝不自动放行）
    assert len(result.hitl) == 1
    assert result.hitl[0]["slot"] == "spc_action"
    assert result.hitl[0]["kind"] == "spc_action"
    assert result.hitl[0]["status"] == "waiting_hitl"
    # 内部简报结构
    assert set(result.internal_brief) == {"过程能力", "不良柏拉图"}
    # provenance：真实 SQL（特性清单 + 柏拉图全量聚合）
    names = {p.get("query_name") for p in result.provenance}
    assert {"spc_characteristics_recent",
            "quality_defect_pareto_all"} <= names


def test_s6_spc_no_alert_branch_skips_pareto(orchestrator):
    """无越限（spc_alert=False）→ else 直通 report_spc，跳过 spc_pareto 与 HITL。"""
    def _no_alert(task, ctx, sink):   # 注入无越限判定（演示库恒越限，为覆盖 else 分支）
        return {"domain": "quality", "status": "ok",
                "payload": {"spc_capabilities": [], "spc_alert": False,
                            "spc_capability_summary": "无越限特性"},
                "summary": "无越限特性"}
    orchestrator.runtime.handlers["spc_capability"] = _no_alert

    result = orchestrator.handle(SPC_QUERY, SPC_CTX)
    assert result.scenario_key == "spc_anomaly_v2"
    nodes = {p["node"] for p in result.provenance if p.get("channel") == "node_timing"}
    assert "spc_pareto" not in nodes      # 越限分支未触发
    assert "report_spc" in nodes          # else 直达简报
    assert "hitl_spc_action" not in nodes
    assert result.hitl == []


# ---------- 6. 数据红线回归（守卫未来数据编辑） ----------

def test_data_redline_material_batch_avoids_10_15():
    """material_batch 不得有 arrival_time='2026-10-15' 且 incoming_result='fail' 的行。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT batch_no FROM material_batch "
        "WHERE date(arrival_time) = '2026-10-15' AND incoming_result = 'fail'"
    ).fetchall()
    conn.close()
    assert rows == [], f"破坏 line_stop 断言的数据被引入: {rows}"


def test_data_redline_supply_batches_today_unchanged():
    """supply_batches_today（today=2026-10-15）行数与 rows[0] 供应商必须不变。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT batch_no, supplier, incoming_result FROM material_batch "
        "WHERE date(arrival_time) = '2026-10-15'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["supplier"] == "千住金属"
    assert rows[0]["incoming_result"] == "pass"


def test_data_redline_inventory_count_unchanged():
    """inventory 行数必须保持 2（test_text2sql 明细探查 COUNT 断言依赖）。"""
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
    conn.close()
    assert n == 2
