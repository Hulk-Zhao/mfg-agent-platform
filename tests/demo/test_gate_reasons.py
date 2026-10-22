"""R3 闸门公示契约：gate_reasons.yaml 结构 + /api/v2/governance/gates 端点
+ 映射表文档 + 前端静态代码点。

钉四件事：
1. 闸门目录 key 集合 == GOVERNANCE_SLOTS（双向，多一个少一个都是配置错误）；
2. 每条有 gate_class 与 why（审批卡透出不空块）；
3. 端点信封与 count；
4. 前端透出代码点存在（hitl 卡/待办卡常驻、帮助中心矩阵、旧「8 槽位」文案已清）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from mfg_agents.v2.demo.api import app
from mfg_agents.v2.services.workflow import GOVERNANCE_SLOTS

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
GATES_YAML = REPO_ROOT / "src" / "mfg_agents" / "v2" / "data" / "gate_reasons.yaml"
DOC = REPO_ROOT / "docs" / "HITL闸门映射表.md"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def gates_doc() -> dict:
    return yaml.safe_load(GATES_YAML.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------- YAML 结构


def test_gate_keys_match_governance_slots(gates_doc):
    """key 集合必须与 GOVERNANCE_SLOTS 一致（治理/产品两侧不许漂移）。"""
    keys = {str(g.get("key") or "") for g in gates_doc.get("gates") or []}
    assert keys == set(GOVERNANCE_SLOTS), \
        f"gate_reasons.yaml 与 GOVERNANCE_SLOTS 不一致: {keys ^ set(GOVERNANCE_SLOTS)}"


def test_gate_entries_have_class_and_why(gates_doc):
    for g in gates_doc.get("gates") or []:
        assert g.get("name"), g.get("key")
        assert str(g.get("gate_class") or "").strip(), f"{g.get('key')} 缺 gate_class"
        why = str(g.get("why") or "").strip()
        assert len(why) >= 15, f"{g.get('key')} 的 why 太短（{len(why)} 字），透出等于没解释"


def test_spend_gap_declared_in_yaml(gates_doc):
    """「动钱无专属槽位」缺口必须在 YAML 里显式登记（不许静默缺失）。"""
    text = GATES_YAML.read_text(encoding="utf-8")
    assert "spend_commit" in text and "无专属槽位" in text


# ---------------------------------------------------------------- 端点


def test_gates_endpoint_envelope(client):
    resp = client.get("/api/v2/governance/gates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    assert data["count"] == len(data["gates"]) == 10
    assert data["source"] == "gate_reasons.yaml"
    assert "改排产" in data["gate_classes"] and "对外消息" in data["gate_classes"]


def test_gates_endpoint_keys_match_slots(client):
    keys = {g["key"] for g in client.get("/api/v2/governance/gates").json()["data"]["gates"]}
    assert keys == set(GOVERNANCE_SLOTS)


# ---------------------------------------------------------------- 映射表文档


def test_mapping_doc_covers_slots_and_gap():
    text = DOC.read_text(encoding="utf-8")
    for slot in GOVERNANCE_SLOTS:
        assert f"`{slot}`" in text, f"映射表缺少槽位 {slot}"
    # 三类硬闸门 + 缺口 + interim + 正式方案都要在
    for token in ("对外消息", "动钱", "改排产", "无专属槽位", "spend_commit",
                  "interim", "propose", "cosign", "approve"):
        assert token in text, f"映射表缺少关键内容: {token}"


# ---------------------------------------------------------------- 前端静态契约


def test_screens_gate_display_code_points():
    screens = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    assert "function gateReasonHtml(ctx, slot)" in screens
    assert "function signoffGapHtml(ctx, status, slot, actions)" in screens
    assert "function gateChipHtml(ctx, slot)" in screens
    # hitl 卡（chat 内）与工作台待办卡都常驻展示，不只 gate 不 ok 时才出现；
    # 会签候选还要传动作目录（离线无权限目录时从这里推导 cosign）
    assert "${gateReasonHtml(ctx, m.slot || _hitlSlot[m.taskUid] || \"\")}" in screens
    assert ("${signoffGapHtml(ctx, signoff, m.slot || _hitlSlot[m.taskUid] || \"\", m.actions)}"
            in screens)
    assert "rows.push(gateReasonHtml(ctx, slot));" in screens
    # 审批中心：列表行闸门片 + 详情卡常驻理由与缺口（真机教训：详情卡是独立渲染器）
    assert "${gateChipHtml(ctx, slotOf(t))}" in screens
    assert "${gateReasonHtml(ctx, slot)}\n        ${signoffGapHtml(ctx, taskSignoff(t), slot, (t.context || {}).actions)}" in screens
    # 未选定动作时的诚实降级：候选会签方（permissions 实时），不假装已有会签链
    assert "将会签" in screens and "选定动作后生效" in screens


def test_help_screen_gate_matrix():
    help_js = (UI_DIR / "help-screen.js").read_text(encoding="utf-8")
    assert "function gateMatrixHtml(ctx)" in help_js
    assert 'id="help-gate-matrix"' in help_js
    assert "gateMatrixHtml(ctx);" in help_js
    # 旧「8 个 HITL 槽位 / 24 条权限」失真文案必须已清除（R3 顺带修）
    assert "8 个 HITL 槽位" not in help_js
    assert "10 个 HITL 槽位" in help_js


def test_app_js_gate_catalog_loading():
    app_js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    assert 'governanceGates() { return this._get("/api/v2/governance/gates"); }' in app_js
    assert "async function loadGateCatalog()" in app_js
    assert "await loadGateCatalog();" in app_js
    # ctx 透出 gates（帮助中心矩阵消费）
    assert "perms: state.perms, gates: state.gates," in app_js


def test_css_gate_styles_use_theme_vars():
    css = (UI_DIR / "app.css").read_text(encoding="utf-8")
    for cls in (".hitl-gate", ".signoff-gap", ".help-gate-matrix", ".help-gate-gap"):
        assert cls in css, f"app.css 缺少 {cls}"
    block = css[css.find("HITL 闸门公示（R3"):]
    assert "#fff" not in block.replace("#ffffff", ""), "新样式不得写死白色"
    assert "var(--primary-rgb)" in block and "var(--warn)" in block


# ------------------------------------------------- 离线演示兜底（R3 补漏 2026-10-22）


def test_offline_fallback_is_generated_not_handcopied():
    """离线闸门理由块必须与生成器输出字节一致。

    离线演示（后端未起）拿不到 /governance/gates，前端需要内置一份；手抄必然
    与 gate_reasons.yaml 漂移。此处重跑生成器渲染并与 demo-data.js 实文件比对：
    改 yaml 忘记重跑生成器、或有人手改 JS 块，都会变红。
    """
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        from gen_gate_fallback import render_block
    finally:
        _sys.path.pop(0)

    block = render_block().strip()
    src = (UI_DIR / "demo-data.js").read_text(encoding="utf-8")
    assert block in src, "demo-data.js 的闸门兜底块与 gate_reasons.yaml 不一致（重跑 tools/gen_gate_fallback.py）"
    assert "GATE_REASONS," in src, "GATE_REASONS 未导出到 window.DEMO"


def test_offline_gate_reason_used_as_fallback():
    """gateReasonHtml/gateChipHtml 必须在 state.gates 为空时回落兜底目录。"""
    screens = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    assert screens.count("(ctx.DEMO && ctx.DEMO.GATE_REASONS)") == 2, \
        "闸门理由与闸门片两处都要回落兜底目录"


def test_offline_hitl_message_carries_slot_and_signoff():
    """离线回放构造 hitl 消息时必须带 slot/signoffStatus/governance。

    2026-10-22 真机发现：离线分支漏这三个字段 → R3 闸门理由与会签缺口在
    演示路径（最常用的路径）整块不渲染，而在线分支有。离线/在线只该差数据来源。
    """
    screens = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    i = screens.find("async function sendDemoQuery")
    j = screens.find("function sendChatQueryRouter", i)
    assert i > 0 and j > i
    demo_branch = screens[i:j]
    for field in ('slot: h.slot || h.kind || ""',
                  "signoffStatus: h.signoff_status || null",
                  "governance: h.governance || null"):
        assert field in demo_branch, f"离线回放 hitl 消息缺字段: {field}"

