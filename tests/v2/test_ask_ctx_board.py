# -*- coding: utf-8 -*-
"""V4 多 Agent 协同贯通 · 回归测试。

覆盖四条主线：
1. **ask_ctx 结构化问数贯通**：前端携带 → api.py 接收 → orchestrator 消费 →
   board_qa 看板口径直查（权限 data_scope 同源裁决 + 脱敏同源）；
2. **SQL 语义 lint**：聚合进 WHERE / 混聚缺 GROUP BY 硬拦截，合法 SQL 放行；
3. **协同规划器**：跨域判定（≥2 域信号）、子任务域去重、LLM 不可用规则拆解；
4. **Agent 工具层并轨**：域智能体从 ToolRegistry 装配附加工具（单一事实源）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.core.cache import query_key
from mfg_agents.v2.core.orchestrator import create_default_orchestrator
from mfg_agents.v2.core.planner import CollaborativePlanner
from mfg_agents.v2.services.board_qa import answer_board_ask
from mfg_agents.v2.services.text2sql.linter import SqlLintError, lint_semantic


@pytest.fixture(scope="module")
def orch():
    return create_default_orchestrator()


UC_QD = {"user_id": "u-qd", "dept": "quality", "roles": ["quality_director"]}
UC_SALES = {"user_id": "u-sales", "dept": "business", "roles": ["sales"]}
UC_MC = {"user_id": "u-mc", "dept": "manufacturing", "roles": ["material_control"]}


# ---------------------------------------------------------------- ask_ctx 直查

def test_board_kpi_direct_hit(orch):
    """品质屏 KPI 直查：channel=board、口径含档位与筛选标签、毫秒级。"""
    qr = answer_board_ask(
        orch.runtime.data_registry,
        {"screen": "quality", "kpi": "defect_rate", "view": "kpi",
         "window": "month", "filters": {"category": "外观不良"}},
        UC_QD)
    assert qr is not None
    assert qr.channel == "board"
    assert qr.confidence == 0.95
    assert qr.fallback is None
    assert qr.rows and qr.rows[0]["指标"] == "不良率"
    assert "当月" in qr.rows[0]["口径"]
    assert "外观不良" in qr.rows[0]["口径"]
    assert qr.elapsed_ms < 3000
    assert qr.provenance["source"].startswith("dashboard.build_")


def test_board_ranking_direct_hit(orch):
    """订单屏 TOP5 排行直查：ranking 视图取排行行。"""
    qr = answer_board_ask(
        orch.runtime.data_registry,
        {"screen": "orders", "kpi": "otd", "view": "ranking", "window": "day"},
        UC_SALES)
    assert qr is not None and qr.channel == "board"
    assert len(qr.rows) == 5
    assert {"排名", "对象", "值"} <= set(qr.rows[0])


def test_board_fixed_view_fallback(orch):
    """固定口径榜（来料 TOP10，payload.views 下）按 key 兜底命中。"""
    qr = answer_board_ask(
        orch.runtime.data_registry,
        {"screen": "quality", "kpi": "incoming_batch_quality",
         "view": "ranking", "window": "window"},
        UC_QD)
    assert qr is not None and qr.channel == "board"


def test_board_permission_fail_closed(orch):
    """data_scope fail-closed：无品质看板权限的角色（material_control）→ None。"""
    assert answer_board_ask(
        orch.runtime.data_registry,
        {"screen": "quality", "kpi": "defect_rate", "view": "kpi", "window": "month"},
        UC_MC) is None


def test_board_masking_same_as_dashboard(orch):
    """订单屏客户名脱敏同源：sales 见真名（masked=False），物控见伪名（masked=True）。"""
    qr_sales = answer_board_ask(
        orch.runtime.data_registry,
        {"screen": "orders", "kpi": "otd", "view": "ranking", "window": "month"},
        UC_SALES)
    qr_mc = answer_board_ask(
        orch.runtime.data_registry,
        {"screen": "orders", "kpi": "otd", "view": "ranking", "window": "month"},
        UC_MC)
    assert qr_sales is not None and not qr_sales.masked
    assert qr_mc is not None and qr_mc.masked
    assert all(str(r["对象"]).startswith("客户") for r in qr_mc.rows)


def test_board_miss_returns_none(orch):
    """未知屏 / 未知 KPI / 空 ask_ctx → None（静默回落自然语言链路）。"""
    reg = orch.runtime.data_registry
    assert answer_board_ask(reg, None, UC_QD) is None
    assert answer_board_ask(reg, {"screen": "nope", "kpi": "x"}, UC_QD) is None
    assert answer_board_ask(reg, {"screen": "quality", "kpi": "nope"}, UC_QD) is None


def test_handle_with_ask_ctx_full_chain(orch):
    """全链路：/api/v2/query 形状的 ask_ctx → handle → board 通道。

    关键契约：看板口径直查**早于策略分派**——提问文案含场景触发词（如"交期"）
    时路由会判 scenario，但结构化 ask_ctx 是更强的意图信号，必须以 board 口径
    回答（用户点的就是这个 KPI），不得被文本抢走跑重型链路。
    """
    result = orch.handle(
        "当月OTD准时交付率如何？", user_ctx=UC_SALES,
        ask_ctx={"module": "mfgboard", "screen": "orders", "kpi": "otd",
                 "view": "kpi", "window": "month"})
    d = result.to_dict()
    assert d["route"]["strategy"] == "board"
    assert d["route"]["ask_ctx"]["kpi"] == "otd"
    assert "看板口径直查" in d["internal_brief"]["回答通道"]
    assert "OTD" in d["report"]
    assert d["provenance"][0]["channel"] == "board"


def test_board_ask_beats_text_route(orch):
    """ask_ctx 优先于文本路由：命中即不建工单（scenario 被短路）。"""
    result = orch.handle(
        "A型号能发多少货，当月发货情况如何？", user_ctx=UC_SALES,
        ask_ctx={"module": "mfgboard", "screen": "orders", "kpi": "otd",
                 "view": "kpi", "window": "month"})
    d = result.to_dict()
    assert d["route"]["strategy"] == "board"
    assert d["hitl"] == [] and d["workflow_tasks"] == []


def test_cache_key_includes_ask_ctx():
    """缓存键并入 ask_ctx：同文案不同档位不得共用缓存（口径错配防护）。"""
    k1 = query_key("当月OTD如何？", UC_SALES,
                   {"kpi": "otd", "window": "month"})
    k2 = query_key("当月OTD如何？", UC_SALES,
                   {"kpi": "otd", "window": "day"})
    k3 = query_key("当月OTD如何？", UC_SALES)
    assert len({k1, k2, k3}) == 3


# ---------------------------------------------------------------- SQL 语义 lint

def test_lint_rejects_aggregate_in_where():
    """聚合函数进 WHERE（截图同款 SQL）→ 硬拦截。"""
    bad = ("SELECT ROUND(sum*1.0/SUM(sum) over(),2) AS a, COUNT(*) AS t "
           "FROM shipment WHERE eta >= date('now','-7 days') "
           "AND COUNT(*) > 0 ORDER BY t")
    with pytest.raises(SqlLintError):
        lint_semantic(bad)


def test_lint_rejects_mixed_agg_without_group_by():
    """混聚 SELECT（聚合列 + 裸列）无 GROUP BY → 硬拦截（SQLite 静默取任意行）。"""
    with pytest.raises(SqlLintError):
        lint_semantic("SELECT product_model, COUNT(*) AS c "
                      "FROM shipment WHERE eta > '2026-10-01'")


def test_lint_allows_legal_sql():
    """合法 SQL 放行：分组聚合 TOP N / 纯聚合均不误伤。"""
    assert lint_semantic(
        "SELECT product_model, COUNT(*) AS c FROM shipment "
        "GROUP BY product_model ORDER BY c LIMIT 5") == []
    assert lint_semantic(
        "SELECT COUNT(*) AS c FROM shipment "
        "WHERE eta BETWEEN '2026-10-08' AND '2026-10-14'") == []


def test_lint_warns_missing_limit():
    """明细查询无 LIMIT → 软警告（不拦执行，提示复核）。"""
    warns = lint_semantic("SELECT * FROM shipment")
    assert warns and "LIMIT" in warns[0]


# ---------------------------------------------------------------- 协同规划器

def test_planner_cross_domain_detection(orch):
    """≥2 个域信号命中 → 判定协同；单域/无域 → 不协同。"""
    p = CollaborativePlanner(orch)
    assert p.should_collaborate("A型号不良率升高的同时OTD也降了，缺料对排产有什么影响")
    assert not p.should_collaborate("今天A型号的质量怎么样")
    assert not p.should_collaborate("你好")


def test_planner_plan_domain_dedup(orch):
    """子任务域唯一：规则拆解天然去重；LLM 拆解亦强制去重。"""
    p = CollaborativePlanner(orch)
    subtasks = p.plan("不良率、设备稼动与缺料对交期的综合影响")
    domains = [t["domain"] for t in subtasks]
    assert len(domains) == len(set(domains))
    assert len(subtasks) <= 4
    assert len(domains) >= 2


# ---------------------------------------------------------------- 工具层并轨

def test_agent_tools_registry_assembly(orch):
    """域智能体从 ToolRegistry 装配附加工具（单一事实源），safe name 双下划线。"""
    from mfg_agents.v2.agents.agentic import AgenticDomainAgent

    qa = AgenticDomainAgent(domain="quality", orchestrator=orch)
    tools = qa._tools()
    assert "quality__trace" in tools and "spc__capability" in tools
    assert "metric__dict" in tools
    # 闭包基础工具全保留（兼容零破坏）
    for legacy in ("query_metric", "run_registered_query", "explore_data",
                   "create_hitl_task", "run_scenario"):
        assert legacy in tools
    # 治理工具 workflow.act（含终批动作）不进智能体
    assert "workflow__act" not in tools


def test_agent_tool_schema_drops_user_ctx(orch):
    """registry 工具 schema 剔除 user_ctx 参数：身份由运行时注入，不让模型填写。"""
    from mfg_agents.v2.agents.agentic import AgenticDomainAgent

    qa = AgenticDomainAgent(domain="supply_chain", orchestrator=orch)
    _, schema = qa._tools()["inventory__atp"]
    assert "user_ctx" not in (schema.get("properties") or {})
    assert "user_ctx" not in (schema.get("required") or [])


# ---------------------------------------------------------------- 前端契约

def test_frontend_ask_ctx_contract():
    """前端贯通契约：关键代码点存在（静态检查，防回退丢失 ask_ctx 链路）。"""
    from pathlib import Path
    ui = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"
    app = (ui / "app.js").read_text(encoding="utf-8")
    screens = (ui / "screens.js").read_text(encoding="utf-8")
    reports = (ui / "reports-screen.js").read_text(encoding="utf-8")
    # app.js：query 封装携带 ask_ctx
    assert "ask_ctx" in app and "askCtx" in app
    # screens.js：helper + 发送链路 + 各入口构造
    assert "writeAskPrefill" in screens and "readAskCtx" in screens
    assert "data-ask-view" in screens and "collab_quality" in screens
    assert "prefillAskCtx" in screens
    # reports-screen.js：报告回链带 module 标注（源码用 JSON.stringify，键值均为双引号）
    assert "chat_ask_ctx" in reports and "reports" in reports
    assert "module" in reports
