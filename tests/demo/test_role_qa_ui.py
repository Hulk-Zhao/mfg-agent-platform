"""岗位案例前端接线（静态契约）。

R2 可答性兜底的最后一米：聊天请求必须携带当前 AI 同事岗位 id，
后端 role_case 结果必须走「同事结论卡」并明确标注「岗位案例」，
不能掉进人工兜底卡或显示「实时链路」。
"""
from __future__ import annotations

from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "src/mfg_agents/v2/demo/ui"


def _screens() -> str:
    return (UI / "screens.js").read_text(encoding="utf-8")


def test_chat_request_carries_colleague_role():
    src = _screens()
    block = src[src.index("function userCtxFor"):src.index("function nearBottom")]
    assert "currentColleague()" in block
    assert "colleague_role" in block
    # 权限仍以 roles 为权威（不能被同事 id 顶替）
    assert "roles: r.roles.slice()" in block


def test_role_case_routes_to_conclusion_card():
    src = _screens()
    block = src[src.index("function resultCardMessage"):src.index("function chitchatCardHtml")]
    assert 'result.route.strategy === "role_case"' in block
    assert 'kind: "conclusion"' in block


def test_role_case_badge_is_not_realtime():
    src = _screens()
    block = src[src.index("function conclusionCardHtml"):src.index("function evidenceHtml")]
    assert "岗位案例 · 已核验口径" in block
    assert 'strategy === "role_case"' in block


def test_role_case_keeps_evidence_fold():
    src = _screens()
    block = src[src.index("function conclusionCardHtml"):src.index("function evidenceHtml")]
    assert "evidenceHtml(ctx, result)" in block
