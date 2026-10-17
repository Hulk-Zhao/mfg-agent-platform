"""P0 工具层落地测试：多 server 分治 + 6 个新工具 + 治理分级（tier）。

覆盖点：
- 域拆分（键恰为 data/asset/quality/workflow/knowledge/office、归属正确、展平全集一致、单域 ≤ 15）；
- 新工具注册与 tier/readonly 标记；
- 安全红队（匿名 workflow.create 必 403，且不产生工单）；
- 数据类工具结构化返回与"诚实降级"数据缺口；
- 工单只发起不执行（waiting_hitl 且可被 workflow.pending 查到）；
- 报告草稿禁词过滤；
- 知识库不可用降级；
- MCP server 按域过滤。
"""
from __future__ import annotations

import io
import json

import pytest

from mfg_agents.v2.mcp_tools import McpStdioServer, ToolRegistry
from mfg_agents.v2.mcp_tools.tools import (
    DOMAIN_TOOLSETS,
    build_domain_toolsets,
    build_platform_tools,
    register_domain_toolsets,
    register_platform_tools,
)
from mfg_agents.v2.services.workflow import PermissionDenied

_DOMAINS = {"data", "asset", "quality", "spc", "workflow", "knowledge", "office"}


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


@pytest.fixture(scope="module")
def registry(orch) -> ToolRegistry:
    return register_platform_tools(ToolRegistry(), orch)


# ============================================================
# 1. 五 server 拆分
# ============================================================


def test_domain_toolsets_split(orch):
    """键恰为各域；归属正确；展平全集 == build_platform_tools；每域 ≤ 15。"""
    toolsets = build_domain_toolsets(orch)
    assert set(toolsets) == _DOMAINS

    expect = {
        "data": ["platform.describe", "data.list_queries", "data.run_query",
                 "data.metric", "data.sql_readonly", "metric.dict",
                 "schedule.what_if"],
        "asset": ["asset.status", "inventory.atp", "equipment.predict",
                  "supply.risk"],
        "quality": ["quality.trace", "quality.8d"],
        "spc": ["spc.capability", "spc.pareto", "spc.ingest"],
        "workflow": ["scenario.list", "scenario.run", "scenario.explain",
                     "workflow.pending", "workflow.act", "workflow.governance",
                     "workflow.create"],
        "knowledge": ["knowledge.search", "report.draft", "doc.extract", "spc.report"],
        "office": ["office.list_rooms", "office.find_available_rooms",
                   "office.book_room", "office.cancel_booking",
                   "office.my_bookings", "office.list_restaurants",
                   "office.menu", "office.place_order", "office.cancel_order",
                   "office.my_orders"],
    }
    for domain, names in expect.items():
        assert [t.name for t in toolsets[domain]] == names, domain
        assert len(toolsets[domain]) <= 15, f"{domain} 超过 15 个工具"

    flat = {t.name for domain in toolsets.values() for t in domain}
    assert flat == {t.name for t in build_platform_tools(orch)}
    # 既有工具（10）+ P0 增量（6）= 16，+ P1 增量（7）= 23，+ 办公助手域（10）= 33，
    # + SPC 域（capability/pareto/ingest = 3）+ spc.report（knowledge 域）= 37
    assert len(flat) == 37 == len(build_platform_tools(orch))


def test_register_domain_toolsets(orch):
    """register_domain_toolsets 返回各域 registry，各含对应域工具。"""
    regs = register_domain_toolsets(orch)
    assert set(regs) == _DOMAINS
    assert {t.name for t in regs["asset"].list()} == {
        "asset.status", "inventory.atp", "equipment.predict", "supply.risk"}
    assert {t.name for t in regs["knowledge"].list()} == {
        "knowledge.search", "report.draft", "doc.extract", "spc.report"}


# ============================================================
# 2. 新工具注册与 tier/readonly 标记
# ============================================================


def test_new_tools_registered_with_tier_and_readonly(registry):
    by_name = {t.name: t for t in registry.list()}
    for name in ("asset.status", "quality.trace", "inventory.atp",
                 "knowledge.search", "workflow.create", "report.draft"):
        assert name in by_name, f"新工具未注册: {name}"

    # 只读自动放行（tier=0, readonly=True）
    for name in ("asset.status", "quality.trace", "inventory.atp",
                 "knowledge.search", "report.draft"):
        assert by_name[name].tier == 0, name
        assert by_name[name].readonly is True, name

    # 写类只发起（tier=1, readonly=False）
    assert by_name["workflow.create"].tier == 1
    assert by_name["workflow.create"].readonly is False


def test_tier_semantics_exposed_in_mcp_dict(registry):
    """tier 语义随 MCP tools/list 透出（治理分级展示）。"""
    entry = next(t for t in registry.list_mcp() if t["name"] == "workflow.create")
    assert entry["tier"] == 1
    assert "inputSchema" in entry


# ============================================================
# 3. 安全红队：匿名发起工单必须拒绝
# ============================================================


def test_anonymous_workflow_create_denied(registry, orch):
    wf = orch.runtime.workflow
    before = len(wf.list_tasks())
    with pytest.raises(PermissionDenied):
        registry.call("workflow.create", {"title": "匿名越权尝试"},
                      user_ctx={})
    # 仅缺 user_id（有 roles 但无身份）同样拒绝
    with pytest.raises(PermissionDenied):
        registry.call("workflow.create", {"title": "无 id"},
                      user_ctx={"roles": ["gm"]})
    assert len(wf.list_tasks()) == before, "拒绝路径不得产生任何工单"


# ============================================================
# 4. 数据类工具：结构化返回 + 诚实降级
# ============================================================


def test_data_tools_return_structured_rows(registry):
    """带合法 user_ctx：返回 channel/rows 等结构化字段。"""
    asset = registry.call("asset.status", {"equipment_id": "PRT-01"},
                          user_ctx={"user_id": "u-eq", "roles": ["equipment_manager"]})
    assert asset["channel"] == "registered"
    assert "rows" in asset and isinstance(asset["rows"], list)
    assert asset["row_count"] >= 1

    trace = registry.call("quality.trace", {"batch_no": "BATCH-A-1008"},
                          user_ctx={"user_id": "u-qa", "roles": ["quality"]})
    assert trace["channel"] == "registered"
    assert trace["row_count"] >= 1

    atp = registry.call("inventory.atp", {"model": "A型号"},
                        user_ctx={"user_id": "u-wh", "roles": ["warehouse"]})
    assert atp["channel"] == "registered"
    assert atp["row_count"] >= 1
    assert "available_qty" in atp["rows"][0]


def test_data_tools_degrade_honestly_without_acl(registry):
    """无有效权限（缺 roles）→ 审核拦截即降级为数据缺口，绝不编造。"""
    res = registry.call("asset.status", {"equipment_id": "PRT-01"},
                        user_ctx={"user_id": "u-x", "roles": []})
    assert res["channel"] == "fallback"
    assert res["rows"] == [] and res["row_count"] == 0
    assert res["data_gap"] is True
    assert any("数据缺口" in w for w in res["warnings"])


# ============================================================
# 5. 工单只发起不执行：waiting_hitl 且可被 workflow.pending 查到
# ============================================================


def test_workflow_create_then_pending_visible(registry):
    created = registry.call(
        "workflow.create",
        {"title": "设备异常人工确认", "context": {"slot": "disposition"},
         "priority": "P1", "hitl_prompt": "请确认处置动作"},
        user_ctx={"user_id": "u-mfg", "roles": ["manufacturing"]})
    assert created["status"] == "waiting_hitl"
    assert created["task_uid"].startswith("wf-")
    assert created["wf_id"] == "hitl_disposition"

    pending = registry.call("workflow.pending", {},
                            user_ctx={"user_id": "u-mfg", "roles": []})
    uids = {t["task_uid"] for t in pending["tasks"]}
    assert created["task_uid"] in uids


# ============================================================
# 6. 报告草稿：is_draft + 禁词过滤
# ============================================================


def test_report_draft_masks_forbidden_words(registry):
    res = registry.call(
        "report.draft",
        {"scenario_key": "delivery_status_v2", "customer_facing": True,
         "fields": {"问题状态": "停线", "根因": "设备故障", "处理": "维修"}},
        user_ctx={"user_id": "u-sales", "roles": ["sales"]})
    assert res["is_draft"] is True
    assert isinstance(res["text"], str) and res["text"]
    from mfg_agents.v2.prompts.report import FORBIDDEN_WORDS
    for word in FORBIDDEN_WORDS:
        assert word not in res["text"], f"禁词未过滤: {word}"


# ============================================================
# 7. 知识检索：不可用即降级
# ============================================================


def test_knowledge_search_degrades_when_unavailable(registry, monkeypatch):
    from mfg_agents.tools import rag_tools

    def _boom(query, n_results=3):
        raise RuntimeError("chroma 不可用")

    monkeypatch.setattr(rag_tools, "search_cases", _boom)
    res = registry.call("knowledge.search", {"query": "停线复线案例"},
                        user_ctx={"user_id": "u-k", "roles": []})
    assert res["hits"] == [] and res["hit_count"] == 0
    assert res["degraded"] is True
    assert "知识库不可用" in res["note"]


# ============================================================
# 8. MCP server 按域过滤
# ============================================================


def _mcp_session(server: McpStdioServer, messages: list[dict]) -> list[dict]:
    msgs = list(messages) + [{"jsonrpc": "2.0", "method": "shutdown"}]
    reqs = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs)
    server._stdin = io.StringIO(reqs + "\n")
    server._stdout = io.StringIO()
    server.serve()
    out = server._stdout.getvalue()
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


def test_server_domain_filter(orch):
    """指定 domain → tools/list 只返回该域工具；serverInfo 名称体现域。"""
    server = McpStdioServer(orchestrator=orch, domain="asset")
    resps = _mcp_session(server, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    assert resps[0]["result"]["serverInfo"]["name"] == \
        "mfg-agent-platform-mcp:asset"
    names = {t["name"] for t in resps[1]["result"]["tools"]}
    assert names == {"asset.status", "inventory.atp", "equipment.predict",
                     "supply.risk"}


def test_server_default_exposes_all_and_legacy_name(orch):
    """不指定域：行为与既有单 server 完全一致（全量工具 + 原 serverInfo 名）。"""
    server = McpStdioServer(orchestrator=orch)
    resps = _mcp_session(server, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    assert resps[0]["result"]["serverInfo"]["name"] == "mfg-agent-platform"
    names = {t["name"] for t in resps[1]["result"]["tools"]}
    assert names == {t.name for t in build_platform_tools(orch)}
    assert len(names) == 37
