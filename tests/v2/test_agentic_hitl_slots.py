"""R1 前置：agentic HITL 槽位 enum 对齐 + 未知槽位拒单 + assignees 归一化
+ 槽位动作目录（scenarios/*.yaml 单一事实源）。

背景（2026-10-22）：
1. create_hitl_task 的 schema enum 与 _SYSTEM_TEMPLATE 曾各写死 7 个槽，
   漏 spc_action/order_review/report_publish——U1 插单评估要的
   order_review 工单 Agent 建不出；
2. assignees 曾硬编码「质量总监」（与槽位语义无关）；
3. context.actions 曾传空列表 → 治理快照不初始化 → 人工选不了动作、
   会签链建不起来（U1「Agent 发起 → 人工确认」闭环断点）。

引擎用 :memory:（进程内单连接，不碰真实治理库）；场景注册表指向真实
scenarios/ 目录（只读加载）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mfg_agents.v2.contracts.scenario import ScenarioRegistry
from mfg_agents.v2.services.workflow import GOVERNANCE_SLOTS, WorkflowEngine

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def scenario_registry() -> ScenarioRegistry:
    reg = ScenarioRegistry(_REPO_ROOT / "src" / "mfg_agents" / "v2" / "scenarios")
    reg.load()
    return reg


@pytest.fixture()
def mem_engine():
    return WorkflowEngine(":memory:")


@pytest.fixture()
def agent(mem_engine, scenario_registry):
    from mfg_agents.v2.agents.agentic import AgenticDomainAgent

    orch = SimpleNamespace(runtime=SimpleNamespace(
        workflow=mem_engine, text2sql=None, data_registry=None,
        scenario_registry=scenario_registry))
    qa = AgenticDomainAgent(domain="quality", orchestrator=orch)
    qa.user_ctx = {"user_id": "u-test", "roles": ["sqe"]}
    return qa


def test_slot_enum_aligned_with_governance(agent):
    """工具 schema enum == GOVERNANCE_SLOTS（漏槽即 U1 空跑）。"""
    _, schema = agent._tools()["create_hitl_task"]
    assert set(schema["properties"]["slot"]["enum"]) == set(GOVERNANCE_SLOTS)


def test_system_template_formats_with_all_slots(agent):
    """提示词 {slots} 占位符可格式化且含全部槽位（防模板漂移）。"""
    from mfg_agents.v2.agents.agentic import (
        _SYSTEM_TEMPLATE,
        _governance_slot_list,
    )

    text = _SYSTEM_TEMPLATE.format(
        role=agent.cfg["role"], boundary=agent.cfg["boundary"],
        hitl_rule=agent.cfg["hitl_rule"],
        slots="|".join(_governance_slot_list()))
    for slot in GOVERNANCE_SLOTS:
        assert slot in text


def test_create_hitl_task_rejects_unknown_slot(agent):
    """未知槽位拒单并回合法清单（防静默建成未纳管工单绕过治理链）。"""
    fn = agent._tools()["create_hitl_task"][0]
    out = fn("测试", "内容", "not_a_slot")
    assert out.get("error") and "not_a_slot" in out["error"]
    assert out.get("task_uid") is None


def test_create_hitl_task_order_review_supported(agent, mem_engine):
    """order_review（曾缺失）可建单、携带场景动作目录并落入治理链。"""
    fn = agent._tools()["create_hitl_task"][0]
    out = fn("插单接单评审", "客户插单 800 件，请评审", "order_review")
    assert out.get("task_uid"), out
    task = next(t for t in mem_engine.list_tasks()
                if t.task_uid == out["task_uid"])
    assert task.slot == "order_review" and task.is_governed
    keys = [a.get("key") for a in (task.context.get("actions") or [])]
    assert keys == ["accept", "conditional", "reject"]


def test_agentic_task_actionable_by_human(agent, mem_engine):
    """端到端：Agent 建单 → 人工（propose 角色）选动作 → 会签要求生成。"""
    fn = agent._tools()["create_hitl_task"][0]
    out = fn("插单接单评审", "客户插单 800 件，请评审", "order_review")
    task = mem_engine.select_action(
        out["task_uid"], "accept", actor="业务经理（测试）",
        user_ctx={"user_id": "u-biz", "roles": ["sales"]})
    st = task.signoff_status()
    assert st["selected_action"] and st["required"]
    assert {r["role"] for r in st["required"]} >= {"rd_engineering", "planning"}


def test_assignees_resolved_from_propose_roles(agent, mem_engine):
    """assignees 从槽位 propose 角色经 PermissionRegistry 解析（非硬编码）。"""
    fn = agent._tools()["create_hitl_task"][0]
    out = fn("插单接单评审", "客户插单 800 件，请评审", "order_review")
    task = next(t for t in mem_engine.list_tasks()
                if t.task_uid == out["task_uid"])
    assert task.assignees, "propose 角色应解析出受理人"
    assert "质量总监" not in task.assignees  # order_review 的 propose 不是质量总监


def test_agentic_hitl_card_carries_actions(agent, mem_engine):
    """编排器包装的前端待确认卡必须带动作目录（2026-10-22 前传空数组，
    前端 chips/会签面板全灰——U1「人工确认」闭环在最后一米断掉）。"""
    from mfg_agents.v2.core.orchestrator import V2Orchestrator

    fn = agent._tools()["create_hitl_task"][0]
    out = fn("插单接单评审", "客户插单 800 件，请评审", "order_review")
    card = V2Orchestrator._agentic_hitl_card(agent.orchestrator,
                                             {"task_uid": out["task_uid"]},
                                             "run-test-1")
    keys = [a.get("key") for a in card["actions"]]
    assert keys == ["accept", "conditional", "reject"]
    assert card["slot"] == "order_review"
    assert card["prompt"]


# ---------------------------------------------------------------- R5：交期答复工具


class _FakeT2S:
    """假 Text2SQL：按查询名返回预制行（模拟注册查询通道）。"""

    def __init__(self, atp_rows, plan_rows):
        self._rows = {"atp_by_model": atp_rows, "plan_by_model": plan_rows}

    def query_registered(self, name, params, user_ctx=None):
        rows = self._rows.get(name)
        if rows is None:
            raise AssertionError(f"未预期的查询: {name}")
        return SimpleNamespace(rows=rows, channel="registered", sql="",
                               warnings=[], fallback=None)


def test_draft_delivery_reply_tool_composes_and_requires_hitl():
    """R5：工具返回草稿 + 建单提示（slot=eta），数字来自注册查询、不编造。"""
    from datetime import date, timedelta

    from mfg_agents.v2.agents.agentic import AgenticDomainAgent
    from mfg_agents.v2.services.workflow import WorkflowEngine

    today = date.today()
    plan = [{"record_date": (today - timedelta(days=i)).isoformat(),
             "actual_qty": 200, "planned_qty": 200} for i in range(7)]
    t2s = _FakeT2S([{"available_qty": 600}], plan)
    orch = SimpleNamespace(runtime=SimpleNamespace(
        workflow=WorkflowEngine(":memory:"), text2sql=t2s, data_registry=None,
        scenario_registry=None))
    agent = AgenticDomainAgent(domain="supply_chain", orchestrator=orch)
    agent.user_ctx = {"user_id": "u-biz", "roles": ["sales"]}

    tools = agent._tools()
    assert "draft_delivery_reply" in tools
    _, schema = tools["draft_delivery_reply"]
    assert set(schema["required"]) == {"model", "qty", "need_date"}

    out = tools["draft_delivery_reply"][0]("A型号", 2000, "月底")
    assert out["needs_hitl"] is True and out["hitl_slot"] == "eta"
    assert out["on_hand"] == 600 and out["first_ship"] == 600
    assert out["to_produce"] == 1400
    assert "草稿" in out["draft"] and "人工确认" in out["note"]


def test_draft_delivery_reply_meta_listed():
    """AGENT_TOOLS_META 登记（帮助中心/MCP 展示同源）。"""
    from mfg_agents.v2.agents.agentic import AGENT_TOOLS_META

    names = {t["name"] for t in AGENT_TOOLS_META}
    assert "draft_delivery_reply" in names


class _BlockedT2S:
    """注册查询被表级 ACL 拦截（channel=fallback + 审核拦截 warning）。"""

    def query_registered(self, name, params, user_ctx=None):
        return SimpleNamespace(rows=[], channel="fallback", sql="",
                               warnings=["审核拦截: 表级权限不足（inventory）"],
                               fallback="manual_ticket")


def test_draft_delivery_reply_acl_block_is_no_data():
    """R5 真机回归：查询被 ACL 拦截必须判 NO_DATA，不许把空结果当零库存。"""
    from mfg_agents.v2.agents.agentic import AgenticDomainAgent
    from mfg_agents.v2.services.workflow import WorkflowEngine

    orch = SimpleNamespace(runtime=SimpleNamespace(
        workflow=WorkflowEngine(":memory:"), text2sql=_BlockedT2S(),
        data_registry=None, scenario_registry=None))
    agent = AgenticDomainAgent(domain="supply_chain", orchestrator=orch)
    agent.user_ctx = {"user_id": "u-biz", "roles": ["sales"]}

    out = agent._tools()["draft_delivery_reply"][0]("A型号", 2000, "2026-10-31")
    assert out["verdict"] == "no_data"
    assert any("不可用" in g for g in out["data_gaps"])


# ---------------------------------------------------------------- R6：催交派发工具


class _FollowupT2S:
    """假 Text2SQL：缺口清单两查（material_shortage / supplier_followup）。"""

    def query_registered(self, name, params, user_ctx=None):
        rows = {
            "material_shortage": [
                {"part_no": "IC-100", "part_name": "主控芯片", "shortage_qty": 3200,
                 "available_qty": 800, "safety_stock": 4000},
            ],
            "supplier_followup": [
                {"supplier": "华强电子", "part_no": "IC-100", "part_name": "主控芯片",
                 "batch_cnt": 5, "fail_cnt": 0},
            ],
        }.get(name)
        if rows is None:
            raise AssertionError(f"未预期的查询: {name}")
        return SimpleNamespace(rows=rows, channel="registered", sql="",
                               warnings=[], fallback=None)


def _followup_agent(domain: str = "supply_chain"):
    from mfg_agents.v2.agents.agentic import AgenticDomainAgent
    from mfg_agents.v2.services.workflow import WorkflowEngine

    orch = SimpleNamespace(runtime=SimpleNamespace(
        workflow=WorkflowEngine(":memory:"), text2sql=_FollowupT2S(),
        data_registry=None, scenario_registry=None))
    agent = AgenticDomainAgent(domain=domain, orchestrator=orch)
    agent.user_ctx = {"user_id": "u-buyer", "roles": ["procurement"]}
    return agent


def test_dispatch_followup_tool_creates_governance_track_rows(tmp_path, monkeypatch):
    """R6：缺口清单 → 采购员待办（dispatched），演示库带演示回写事实。"""
    monkeypatch.setenv("MFG_FOLLOWUP_DB", str(tmp_path / "demo_followup.db"))
    agent = _followup_agent()
    tools = agent._tools()
    assert "dispatch_followup" in tools
    _, schema = tools["dispatch_followup"]
    assert schema["required"] == ["work_order_set"]

    out = tools["dispatch_followup"][0]("本周开工批次", 5)
    assert out["dispatched"] == 1 and out["total_gaps"] == 1
    row = out["followups"][0]
    assert row["part_no"] == "IC-100" and row["supplier"] == "华强电子"
    assert row["status"] == "dispatched" and row["owner"] == "采购员"
    assert row["demo_writeback"] is True
    assert out["needs_hitl"] is False


def test_dispatch_followup_rejects_non_supply_domain(tmp_path, monkeypatch):
    """催交派发仅限供应链/采购/仓储域（质量域 Agent 不得派采购待办）。"""
    monkeypatch.setenv("MFG_FOLLOWUP_DB", str(tmp_path / "demo_followup.db"))
    agent = _followup_agent(domain="quality")
    out = agent._tools()["dispatch_followup"][0]("本周开工批次")
    assert out.get("error") and "供应链" in out["error"]


def test_dispatch_followup_requires_work_order_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MFG_FOLLOWUP_DB", str(tmp_path / "demo_followup.db"))
    out = _followup_agent()._tools()["dispatch_followup"][0]("  ")
    assert out.get("error") and "work_order_set" in out["error"]


# ---------------------------------------------------------------- R7：质量追溯工具


class _TraceT2S:
    """假 Text2SQL：五路追溯查询的预制行。"""

    _ROWS = {
        "batch_trace_full": [{"batch_no": "BATCH-A-1008", "part_no": "PCB-022",
                              "part_name": "A型号PCB", "supplier": "深南电路",
                              "inspect_status": "pass", "qty": 1000,
                              "qualified_qty": 980, "warehouse": "电子料仓",
                              "bin_location": "A-03-02"}],
        "incoming_batch_by_no": [{"batch_no": "BATCH-A-1008", "part_no": "PCB-022",
                                  "supplier": "深南电路", "inspect_status": "pass",
                                  "qty": 1000, "qualified_qty": 980}],
        "quality_defect_scope_by_batch": [{"batch_no": "BATCH-A-1008",
                                           "product_model": "A型号",
                                           "line_code": "SMT-2",
                                           "process_stage": "SMT",
                                           "defect_type": "贴片偏移",
                                           "defected_qty": 46}],
        "equipment_log_by_batch": [{"equipment_id": "SMT-2-01",
                                    "line_code": "SMT-2",
                                    "alarm_code": "ALM-TEMP",
                                    "is_abnormal": 1}],
        "inprocess_wo_by_batch": [{"wo_no": "WO-2026-0451",
                                   "product_model": "A型号",
                                   "line_code": "SMT-2"}],
    }

    def query_registered(self, name, params, user_ctx=None):
        rows = self._ROWS.get(name)
        if rows is None:
            raise AssertionError(f"未预期的查询: {name}")
        return SimpleNamespace(rows=rows, channel="registered", sql="",
                               warnings=[], fallback=None)


def _trace_agent(domain: str = "quality"):
    from mfg_agents.v2.agents.agentic import AgenticDomainAgent
    from mfg_agents.v2.services.workflow import WorkflowEngine

    orch = SimpleNamespace(runtime=SimpleNamespace(
        workflow=WorkflowEngine(":memory:"), text2sql=_TraceT2S(),
        data_registry=None, scenario_registry=None))
    agent = AgenticDomainAgent(domain=domain, orchestrator=orch)
    agent.user_ctx = {"user_id": "u-qa", "roles": ["qa"]}
    return agent


def test_trace_quality_chain_tool_composes_three_sources():
    agent = _trace_agent()
    tools = agent._tools()
    assert "trace_quality_chain" in tools
    _, schema = tools["trace_quality_chain"]
    assert schema["required"] == ["batch_no"]

    out = tools["trace_quality_chain"][0]("BATCH-A-1008")
    assert out["verdict"] == "traced"
    assert out["locked"]["supplier"] == "深南电路"
    assert out["abnormal_count"] == 1
    assert out["wip_count"] == 1
    assert out["needs_hitl_hint"]["slot"] == "report_publish"
    assert "贴片偏移 46 件" in out["report"]
    assert "人工确认" in out["note"]


def test_trace_quality_chain_rejects_non_quality_domain():
    out = _trace_agent(domain="manufacturing")._tools()["trace_quality_chain"][0](
        "BATCH-A-1008")
    assert out.get("error") and "质量" in out["error"]


def test_trace_quality_chain_requires_batch():
    out = _trace_agent()._tools()["trace_quality_chain"][0]("  ")
    assert out.get("error") and "batch_no" in out["error"]


def test_trace_quality_chain_meta_listed():
    from mfg_agents.v2.agents.agentic import AGENT_TOOLS_META

    assert "trace_quality_chain" in {t["name"] for t in AGENT_TOOLS_META}
