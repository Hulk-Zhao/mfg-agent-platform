"""R5 交期答复演示回放契约（demo-data.js::CHAT_DEMOS["SO-3108 月底能交吗"]）。

离线演示闭环（无 LLM 也能真机走通）的守卫：
1. 回放条目存在且事件序列完整（试算 → 草稿 → 建单）；
2. result.route.strategy=agentic → 前端渲染同事结论卡（D2 路由）；
3. hitl 卡带 eta 槽位 + 三类动作 + 演示标记（不假装真实工单）；
4. 离线快捷问题第 6 条与该回放对应（digest_intent 纪律的 R5 增量）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
KEY = "SO-3108 月底能交吗"


def _demo_src() -> str:
    return (UI_DIR / "demo-data.js").read_text(encoding="utf-8")


def test_replay_entry_exists_with_full_event_sequence():
    src = _demo_src()
    assert f'"{KEY}": {{' in src
    seg = src[src.find(f'"{KEY}": {{'):]
    seg = seg[:seg.find('"SMT-1设备参数异常吗"') or len(seg)]
    # 事件序列：agent_start → 工具试算 → 草稿结论 → hitl 建单
    assert '"tool": "draft_delivery_reply"' in seg
    assert "600 套可先发" in seg and "1400 套" in seg
    assert '"node": "hitl_eta"' in seg
    # result 走 agentic 策略 → 同事结论卡（D2）
    assert '"strategy": "agentic"' in seg
    assert '"query_name": "atp_by_model"' in seg
    assert '"query_name": "plan_by_model"' in seg


def test_replay_hitl_card_shape():
    src = _demo_src()
    seg = src[src.find(f'"{KEY}": {{'):]
    seg = seg[:seg.find('"SMT-1设备参数异常吗"') or len(seg)]
    assert '"slot": "eta"' in seg
    assert '"task_uid": "wf-demo-eta-3108"' in seg
    for key in ("commit_planned", "commit_buffered", "escalate_exec"):
        assert key in seg, f"eta 动作目录缺少 {key}（与 line_stop_v2 同源）"
    assert '"demo": true' in seg          # 演示回放不假装真实工单
    assert "草稿，经人工确认后方可发送" in seg


def test_offline_quick_question_registered():
    """第 6 条离线快捷问题 = R5 回放键（digest_intent 纪律增量）。"""
    src = _demo_src()
    screens = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    offline = screens.split("const DEMO_QUICK_QUESTIONS = [", 1)[1].split("];", 1)[0]
    keys = [ln.strip().strip('",') for ln in offline.splitlines()
            if ln.strip().startswith('"')]
    assert KEY in keys
    assert f'"{KEY}":' in src


def test_agentic_template_mentions_tool():
    """Agent 提示词登记新工具（能力对模型可见 + 放行纪律写进 prompt）。"""
    src = (REPO_ROOT / "src" / "mfg_agents" / "v2" / "agents"
           / "agentic.py").read_text(encoding="utf-8")
    assert "draft_delivery_reply(model, qty, need_date)" in src
    assert "hitl.eta 人工确认" in src
