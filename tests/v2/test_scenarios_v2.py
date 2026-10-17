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

from mfg_agents.config import settings
from mfg_agents.tools.llm_factory import reset_probe_cache
from mfg_agents.v2.core.orchestrator import V2Orchestrator, V2Runtime

DB_PATH = "data/mfg_agent.db"

USER_CTX = {"user_id": "u-gm", "dept": "management", "roles": ["coordinator"]}

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
    for mod in ("mfg_agents.tools.llm_factory",
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
    # data_gap 可见（工程根因无 BOM 数据源）
    gaps = [p for p in result.provenance if p.get("channel") == "data_gap"]
    assert any("设计根因" in (g.get("note") or "") for g in gaps)
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
    # 工程参数/备件 → data_gap 可见（不编造）
    gaps = [p for p in result.provenance if p.get("channel") == "data_gap"]
    notes = " ".join((g.get("note") or "") for g in gaps)
    assert "参数基线" in notes and "备件" in notes
    assert brief.get("数据缺口")
    assert result.degraded is True


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
