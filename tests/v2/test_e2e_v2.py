"""V2 端到端验收测试（offline degraded 模式，硬性验收项）。

覆盖：
1. 全链路：TwoLevelRouter → YamlDagExecutor → 简报/报告/HITL/工单/provenance；
2. degraded 标记：LLM 不可用时降级路径全部生效且结果可审计；
3. V1 行为等价：同输入下 V1 域 Agent 与 V2 handler 结论结构一致；
4. 未停线路径：condition 跳转，不产生 HITL（等价 V1 "没停线直接回复"）。
"""
import pytest

from mfg_agents.v2.core.orchestrator import V2Orchestrator, V2Runtime
from mfg_agents.config import settings
from mfg_agents.tools.llm_factory import reset_probe_cache

STOP_QUERY = "A型号在SMT-1停线，何时复线？"
NO_STOP_QUERY = "B型号在DIP-2停线了吗，何时复线？"

USER_CTX = {"user_id": "u-manager", "dept": "manufacturing",
            "roles": ["manufacturing", "coordinator"]}


@pytest.fixture()
def orchestrator(monkeypatch, tmp_path) -> V2Orchestrator:
    """离线 orchestrator：LLM 探针强制 False，工作流库指向临时 DB。"""
    reset_probe_cache()
    monkeypatch.setattr("mfg_agents.tools.llm_factory.llm_available",
                        lambda: False)
    monkeypatch.setattr("mfg_agents.v2.core.router.llm_available",
                        lambda: False)
    monkeypatch.setattr("mfg_agents.v2.core.node_registry.llm_available",
                        lambda: False)
    monkeypatch.setattr(settings, "WORKFLOW_DB",
                        f"sqlite:///{tmp_path / 'workflow.db'}")
    return V2Orchestrator(V2Runtime.create_default())


# ---------- 1. 全链路 ----------

def test_full_pipeline_stop_scenario(orchestrator):
    result = orchestrator.handle(STOP_QUERY, USER_CTX)
    assert result.error is None
    # 路由
    assert result.route["level"] == "L0"
    assert result.scenario_key == "line_stop_v2"
    assert result.route["entities"]["line_code"] == "SMT-1"
    # 结论结构（与 V1 等价）
    brief = result.internal_brief
    assert brief["停线确认"] is True
    assert "32.1" in brief["生产"]
    assert "锡膏偏移" in brief["品质"]
    assert "无来料异常" in brief["供应链"]
    assert "刮刀压力" in brief["设备"]
    assert "评级" in brief["FA评级"]
    # 对客报告（脱敏话术）
    assert result.report
    assert "计划内暂停" in result.report
    assert "停线" not in result.report.replace("计划内暂停", "")
    # HITL：两个关键决策节点都是 waiting_hitl（绝不自动放行）
    assert len(result.hitl) == 2
    assert {h["kind"] for h in result.hitl} == {"restart", "eta"}
    assert all(h["status"] == "waiting_hitl" for h in result.hitl)
    assert {h["prompt"][:2] for h in result.hitl} == {"复线", "对客"}
    # 工作流工单
    assert len(result.workflow_tasks) == 1
    ticket = result.workflow_tasks[0]["ticket"]
    assert ticket["status"] == "running"
    assert ticket["assignees"] == ["计划", "物控", "业务"]
    # provenance：SQL 原文 + 命中行 + 节点耗时
    sql_items = [p for p in result.provenance if p.get("sql")]
    assert len(sql_items) >= 5
    tables = {t for p in sql_items for t in p.get("tables", [])}
    assert {"production_output", "line_stop_event", "quality_defect",
            "material_batch", "equipment_log"} <= tables
    timings = [p for p in result.provenance if p["channel"] == "node_timing"]
    # 9 个 YAML 步骤中 fan_out 展开为 3 分支 → 10 个执行节点
    # （fan_in 为汇聚标记不单独计时），全部必须有耗时记录
    assert len(timings) == 10
    # 脱敏证据
    supply = [p for p in sql_items if p.get("query_name") == "supply_batches_today"]
    assert supply and supply[0]["masked"] is True


def test_degraded_mode_flags(orchestrator):
    """degraded 硬性验收项：LLM 不可用 → 降级原因明确、结论模板化可审计。"""
    result = orchestrator.handle(STOP_QUERY, USER_CTX)
    assert result.degraded is True
    assert any("FA" in r for r in result.degraded_reasons)
    assert any("报告" in r for r in result.degraded_reasons)
    fa_prov = [p for p in result.provenance if p.get("channel") == "rag"]
    assert fa_prov  # RAG 通道仍有检索记录
    assert "degraded" in result.internal_brief["FA评级"]


def test_pipeline_runs_multiple_times_stateless(orchestrator):
    """服务无状态：连续两次请求结果一致（ SharedContext 每次新建）。"""
    r1 = orchestrator.handle(STOP_QUERY, USER_CTX)
    r2 = orchestrator.handle(STOP_QUERY, USER_CTX)
    assert r1.internal_brief["停线确认"] == r2.internal_brief["停线确认"]
    assert r1.report == r2.report
    assert r1.run_id != r2.run_id


# ---------- 2. V1 行为等价 ----------

def test_v1_equivalence_domain_conclusions(orchestrator):
    """同输入同结论结构：V1 域 Agent 直调 vs V2 handler 产出。"""
    from mfg_agents.agents.equipment import equipment_agent
    from mfg_agents.agents.manufacturing import ManufacturingAgent
    from mfg_agents.agents.quality import quality_agent
    from mfg_agents.agents.supply_chain import supply_chain_agent

    state = {"product_model": "A型号", "line_code": "SMT-1"}
    v1 = {
        "confirmed_stop": ManufacturingAgent().run(state)["confirmed_stop"],
        "dominant": quality_agent(state)["dominant_defect"]["defect_type"],
        "supply_summary": supply_chain_agent(state)["supply_summary"],
        "alarms": len(equipment_agent(state)["abnormal_alarms"]),
    }
    result = orchestrator.handle(STOP_QUERY, USER_CTX)
    brief = result.internal_brief
    assert brief["停线确认"] == v1["confirmed_stop"]
    assert v1["dominant"] in brief["品质"]
    assert v1["supply_summary"] == brief["供应链"]
    # 设备告警数量：V1=3 → V2 provenance 中 equip_alarms 命中行数
    equip = [p for p in result.provenance
             if p.get("query_name") == "equip_alarms"][0]
    assert equip["row_count"] == v1["alarms"]


# ---------- 3. 未停线路径 ----------

def test_no_stop_path_skips_fanout(orchestrator):
    """未确认停线：跳转 report，无 HITL/工单（等价 V1 没停线直接回复）。"""
    result = orchestrator.handle(NO_STOP_QUERY, USER_CTX)
    assert result.internal_brief["停线确认"] is False
    assert result.hitl == []
    assert result.workflow_tasks == []
    assert "运行正常" in result.report
    quality_prov = [p for p in result.provenance
                    if p.get("query_name") == "quality_defect_dist"]
    assert quality_prov == []  # 品质域未被执行


# ---------- 4. 超范围兜底 ----------

def test_out_of_scope_fallback(orchestrator):
    result = orchestrator.handle("今天天气怎么样", USER_CTX)
    assert result.scenario_key is None
    assert result.fallback == "manual_ticket"
    assert result.degraded is True
    assert "人工兜底" in result.report


# ---------- 5. 通知服务 ----------

def test_notification_service_levels():
    from mfg_agents.v2.services.notification import (
        LogNotificationChannel,
        NotificationService,
    )
    service = NotificationService(LogNotificationChannel())
    record = service.send("P1", ["计划", "物控"], "停线影响评估", "请评估影响")
    assert record["targets"] == ["计划", "物控"]
    assert len(service.channel.outbox) == 1
    with pytest.raises(ValueError, match="通知级别"):
        service.send("P9", ["x"], "t", "b")
    with pytest.raises(ValueError, match="目标"):
        service.send("P1", [], "t", "b")
