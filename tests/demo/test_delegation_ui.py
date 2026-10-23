"""超时代签前端契约（静态断言）：入口、必填交互、API 透传。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


def test_api_passes_on_behalf_of():
    src = (UI / "app.js").read_text(encoding="utf-8")
    assert "workflowApprove(taskId, approver, comment, final, decision, onBehalfOf)" in src
    assert "body.on_behalf_of = onBehalfOf" in src


def test_detail_card_renders_delegate_entry():
    src = (UI / "screens.js").read_text(encoding="utf-8")
    assert 'id="hitl-delegate"' in src
    assert "t.delegation.allowed" in src
    assert 'hitlAction(ctx, t.task_uid, "delegate")' in src


def test_delegate_flow_requires_target_and_reason():
    src = (UI / "screens.js").read_text(encoding="utf-8")
    assert "代签对象（原会签人/岗位，必填）" in src
    assert "超时代签理由（必填，写入签名账本）" in src
    assert "超时代签不可用" in src


def test_delegate_uses_approve_path_with_on_behalf():
    src = (UI / "screens.js").read_text(encoding="utf-8")
    assert "const isApprove = action === \"approve\" || action === \"delegate\"" in src
    assert "decision, onBehalfOf)" in src
