"""MCP 统一工具层 + A2A 最小闭环测试。

设计立场验证点：业务模块以**确定性工具**暴露（function call / MCP），
不做成自主 Agentic Agent；治理权限在工具调用链上与 HTTP 端点同源生效。
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mfg_agents.v2.contracts.data_source import DataSourceRegistry
from mfg_agents.v2.mcp_tools import McpStdioServer, Tool, ToolRegistry
from mfg_agents.v2.mcp_tools.server import PROTOCOL_VERSION


@pytest.fixture(scope="module")
def governed_registry() -> ToolRegistry:
    """真实平台工具注册表（离线 degraded 可跑；conftest 已屏蔽实网 LLM）。"""
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    from mfg_agents.v2.mcp_tools.tools import register_platform_tools

    orch = create_default_orchestrator()
    return register_platform_tools(ToolRegistry(), orch)


@pytest.fixture()
def api_client():
    """A2A 走真实 FastAPI app（演示库；conftest 离线门控生效）。"""
    from mfg_agents.v2.demo.api import app

    return TestClient(app)

# ============================================================
# 1. ToolRegistry（单一事实源）
# ============================================================


def test_tool_registry_conflict_and_lookup():
    """重名注册显式报错（不能静默覆盖）；get 未注册 → KeyError。"""
    reg = ToolRegistry()
    t = Tool(name="x.y", description="d", input_schema={"type": "object"},
             handler=lambda args, user_ctx: {"ok": True})
    reg.register(t)
    with pytest.raises(ValueError, match="冲突"):
        reg.register(t)
    assert reg.get("x.y") is t
    with pytest.raises(KeyError):
        reg.get("nope.nope")


def test_platform_tools_registered_via_registry_fixture(governed_registry):
    """平台工具经统一入口注册：10 个工具、命名规范、schema 合法。"""
    tools = governed_registry.list()
    assert len(tools) >= 10
    for t in tools:
        assert "." in t.name, f"工具名必须 域.动作: {t.name}"
        assert t.input_schema.get("type") == "object"
        assert t.description, t.name
    names = {t.name for t in tools}
    assert {"platform.describe", "data.run_query", "data.metric",
            "scenario.run", "workflow.pending", "workflow.act",
            "workflow.governance"} <= names


# ============================================================
# 2. MCP stdio server（JSON-RPC 2.0 全协议往返）
# ============================================================


def _mcp_session(governed_registry, messages: list[dict]) -> list[dict]:
    """跑一遍 stdio 会话，返回响应列表（StringIO 模拟，不 spawn 子进程）。

    会话以 shutdown notification 结束（合法 JSON-RPC；裸字符串不是协议消息）。
    """
    import io

    msgs = list(messages) + [{"jsonrpc": "2.0", "method": "shutdown"}]
    reqs = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs)
    server = McpStdioServer(
        registry=governed_registry,
        stdin=io.StringIO(reqs + "\n"),
        stdout=io.StringIO())
    server.serve()
    out = server._stdout.getvalue()
    return [json.loads(l) for l in out.strip().splitlines() if l.strip()]


def test_mcp_initialize_tools_list(governed_registry):
    """initialize 握手 + tools/list：协议版本与工具清单。"""
    resps = _mcp_session(governed_registry, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    assert resps[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in resps[0]["result"]["capabilities"]
    assert len(resps) == 2  # notification 不产生响应
    names = {t["name"] for t in resps[1]["result"]["tools"]}
    assert "scenario.run" in names and "workflow.act" in names
    run_tool = next(t for t in resps[1]["result"]["tools"]
                    if t["name"] == "scenario.run")
    assert run_tool["inputSchema"]["required"] == ["query"]


def test_mcp_tools_call_scenario_end_to_end(governed_registry):
    """tools/call 真实跑停线场景：JSON-RPC 请求 → 结构化结果（含 provenance）。"""
    resps = _mcp_session(governed_registry, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "scenario.run",
            "arguments": {"query": "A型号在SMT-1停线，何时复线？",
                          "user_ctx": {"user_id": "u-mfg", "dept": "manufacturing",
                                       "roles": ["manufacturing"]}}}},
    ])
    result = resps[0]["result"]
    assert result.get("isError") is None
    data = result["structuredContent"]
    assert data["route"]["scenario_key"] == "line_stop_v2"
    assert data["route"]["level"] == "L0"
    assert data["provenance"], "provenance 必须随工具结果透出"
    assert data["hitl"], "停线场景应产出 HITL 待确认卡"
    # content 文本与 structuredContent 同源（客户端二选一消费）
    text = result["content"][0]["text"]
    assert json.loads(text)["run_id"] == data["run_id"]


def test_mcp_tool_error_is_result_not_protocol_error(governed_registry):
    """治理拒绝是**工具执行结果**（isError=true），不是 JSON-RPC 协议错误。"""
    resps = _mcp_session(governed_registry, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "workflow.act",
            "arguments": {"task_uid": "wf-nonexistent", "action": "approve",
                          "user_ctx": {"user_id": "u-x", "roles": ["gm"]}}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "workflow.act",
            "arguments": {"task_uid": "wf-1", "action": "approve"}}},  # 缺 user_ctx
    ])
    r1, r2 = resps
    assert r1["result"]["isError"] is True            # 工单不存在 → 工具失败
    assert "error" not in r1                          # 但协议层无 error
    assert r2["result"]["isError"] is True
    assert "user_id" in r2["result"]["content"][0]["text"]


def test_mcp_unknown_tool_is_protocol_error(governed_registry):
    """未知工具 → 协议级错误 -32602（不是 isError——工具根本不存在）。"""
    resps = _mcp_session(governed_registry, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "no.such", "arguments": {}}},
    ])
    assert resps[0]["error"]["code"] == -32602


# ============================================================
# 3. A2A 最小闭环（Agent Card + message/send + tasks/get）
# ============================================================


def test_a2a_agent_card_lists_scenario_skills(api_client):
    """Agent Card：技能清单来自场景注册表（外部智能体发现能力入口）。"""
    resp = api_client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "mfg-agent-platform"
    assert card["capabilities"]["streaming"] is False
    skill_ids = {s["id"] for s in card["skills"]}
    assert "line_stop_v2" in skill_ids
    assert "platform.data-query" in skill_ids
    assert card["description"]  # 明确声明"不部署自主 Agentic Agent"


def test_a2a_message_send_runs_scenario_and_task_get(api_client):
    """message/send 真实跑通客诉场景 → Task+Artifact；tasks/get 可查。"""
    send = api_client.post("/a2a", json={
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": "华为投诉BATCH-A-1008"}],
            "metadata": {"user_ctx": {"user_id": "u-qa", "dept": "quality",
                                      "roles": ["qa"]}}},
        }})
    assert send.status_code == 200
    result = send.json()["result"]
    assert result["status"]["state"] == "completed"
    data = result["artifacts"][0]["parts"][0]["data"]
    assert data["route"]["scenario_key"] == "customer_complaint_v2"
    assert data["hitl"]

    get = api_client.post("/a2a", json={
        "jsonrpc": "2.0", "id": 2, "method": "tasks/get",
        "params": {"id": result["id"]}})
    assert get.status_code == 200
    assert get.json()["result"]["id"] == result["id"]


def test_a2a_requires_verifiable_identity(api_client):
    """A2A 不是治理后门：缺 user_ctx.user_id → 403。"""
    resp = api_client.post("/a2a", json={
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"role": "user",
                               "parts": [{"kind": "text", "text": "A型号在SMT-1停线"}]}}})
    assert resp.status_code == 403
    assert "user_id" in resp.json()["detail"]


def test_a2a_unknown_method(api_client):
    resp = api_client.post("/a2a", json={
        "jsonrpc": "2.0", "id": 1, "method": "no/such", "params": {}})
    assert resp.json()["error"]["code"] == -32601
