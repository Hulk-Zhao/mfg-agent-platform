"""R13 / D14：Tool.tier 预执行同意门控（tier=1 + requires_consent）。"""
from __future__ import annotations

import pytest

from mfg_agents.v2.mcp_tools.registry import (
    ConsentRequired,
    Tool,
    ToolError,
    ToolRegistry,
    consent_is_approved,
)


def _registry(calls: list[str]) -> ToolRegistry:
    def handler(args: dict, user_ctx: dict) -> dict:
        calls.append(str(args.get("ref") or ""))
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(Tool(
        name="erp.delivery.write",
        description="对客交期承诺回写（落地前必须预执行同意）",
        input_schema={"type": "object"},
        handler=handler,
        domain="workflow",
        readonly=False,
        tier=1,
        requires_consent=True,
    ))
    registry.register(Tool(
        name="workflow.create",
        description="只发起：产 HITL 工单（不落地）",
        input_schema={"type": "object"},
        handler=handler,
        domain="workflow",
        readonly=False,
        tier=1,
    ))
    registry.register(Tool(
        name="data.read",
        description="只读",
        input_schema={"type": "object"},
        handler=handler,
        domain="data",
        tier=0,
    ))
    return registry


def test_tier1_gate_blocks_without_consent_and_does_not_execute():
    calls: list[str] = []
    registry = _registry(calls)

    with pytest.raises(ConsentRequired) as err:
        registry.call("erp.delivery.write", {"ref": "SO-1"}, user_ctx={})

    assert err.value.tool_name == "erp.delivery.write"
    assert "workflow.create" in str(err.value)
    assert calls == [], "未同意前 handler 不得执行（未落地）"


def test_tier1_gate_rejects_unapproved_consent():
    calls: list[str] = []
    registry = _registry(calls)

    for bad in ({}, {"ticket_id": "wf-1"}, {"ticket_id": "wf-1", "approved": False}):
        with pytest.raises(ConsentRequired):
            registry.call("erp.delivery.write", {"ref": "SO-1"},
                          user_ctx={}, consent=bad)
    assert calls == []


def test_tier1_gate_allows_approved_consent():
    calls: list[str] = []
    registry = _registry(calls)

    result = registry.call("erp.delivery.write", {"ref": "SO-2"}, user_ctx={},
                           consent={"ticket_id": "wf-9", "approved": True})

    assert result == {"ok": True}
    assert calls == ["SO-2"]


def test_only_tools_declaring_consent_are_gated():
    """既有 tier=1「只发起」工具与只读工具不受门控影响（向后兼容）。"""
    calls: list[str] = []
    registry = _registry(calls)

    assert registry.call("workflow.create", {"ref": "A"}, user_ctx={}) == {"ok": True}
    assert registry.call("data.read", {"ref": "B"}, user_ctx={}) == {"ok": True}
    assert calls == ["A", "B"]


def test_gated_tool_exposes_requires_consent_in_mcp():
    registry = _registry([])
    entries = {e["name"]: e for e in registry.list_mcp()}
    assert entries["erp.delivery.write"]["requiresConsent"] is True
    assert entries["erp.delivery.write"]["tier"] == 1
    assert "requiresConsent" not in entries["workflow.create"]
    assert "requiresConsent" not in entries["data.read"]


def test_invalid_handler_result_still_errors():
    registry = ToolRegistry()
    registry.register(Tool(name="bad.tool", description="x", input_schema={},
                           handler=lambda args, user_ctx: "not-a-dict"))
    with pytest.raises(ToolError):
        registry.call("bad.tool", {}, user_ctx={})


def test_consent_shape_helper_matches_gate():
    assert consent_is_approved({"ticket_id": "wf-1", "approved": "yes"})
    assert not consent_is_approved({"ticket_id": " ", "approved": True})
