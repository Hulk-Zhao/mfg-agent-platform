"""同事化交互静态契约（D1/D2）：防回退丢失关键代码点。

只做源码静态断言（无浏览器依赖，CI 可跑）——跨函数可达性是静态断言的
天然盲区，但「代码点存在」这一层必须钉死：
1. app.js：同事目录拉取 / 状态 / 切换 / ctx 透出 / localStorage 键；
2. screens.js：同事切换器 / 问候行 / 快捷问题按同事切换 / conclusion
   消息 kind / agentic+collab 不再掉人工兜底卡 / hitl 卡发起人 / 工作台同事卡；
3. app.css：同事样式块存在且用主题变量（不写死色值）。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
APP_JS = (UI_DIR / "app.js").read_text(encoding="utf-8")
SCREENS_JS = (UI_DIR / "screens.js").read_text(encoding="utf-8")
CSS = (UI_DIR / "app.css").read_text(encoding="utf-8")


def test_app_js_colleague_state_and_api():
    assert 'colleagues() { return this._get("/api/v2/colleagues"); }' in APP_JS
    assert 'COLLEAGUE_KEY = "mfg_colleague_v1"' in APP_JS
    assert "async function loadColleagueCatalog()" in APP_JS
    assert "function currentColleague()" in APP_JS
    assert "function setColleague(roleKey)" in APP_JS
    # ctx 透出：屏幕函数拿不到就等于没接
    assert "currentColleague, setColleague," in APP_JS


def test_app_js_role_switch_resets_colleague():
    """换岗即换同事：手动选择不跨岗位继承（否则能力面与岗位错配）。"""
    assert 'state.colleague = "";' in APP_JS
    assert 'localStorage.removeItem(COLLEAGUE_KEY)' in APP_JS


def test_screens_js_colleague_switch_in_chat():
    assert 'id="colleague-select"' in SCREENS_JS
    assert 'id="colleague-name"' in SCREENS_JS
    assert "function colleagueOptionsHtml(ctx)" in SCREENS_JS
    assert "function colleagueIntroHtml(ctx)" in SCREENS_JS
    assert "function chatQuickQuestions(ctx)" in SCREENS_JS
    assert 'colSel.addEventListener("change"' in SCREENS_JS


def test_screens_js_conclusion_card_routing():
    """agentic/collab → conclusion（修「AI 答了话却显示人工兜底」错标）。"""
    assert 'else if (m.kind === "conclusion") body = conclusionCardHtml(ctx, m);' in SCREENS_JS
    assert "function conclusionCardHtml(ctx, m)" in SCREENS_JS
    # R7：批次追溯（trace 策略）也必须走结论卡，不许掉人工兜底
    assert 'result.route.strategy === "trace"' in SCREENS_JS
    assert 'result.route.strategy === "agentic"' in SCREENS_JS
    assert 'result.route.strategy === "collab"' in SCREENS_JS
    # 结论卡必须带来源徽标（实时/演示二选一，不可关）
    assert 'UI.badge("实时链路", "badge-green")' in SCREENS_JS


def test_screens_js_hitl_card_colleague_attribution():
    """待确认卡显示发起同事 + actions 从消息带出（R1 前置闭环）。"""
    assert "发起：<b>${UI.esc(m.colleague)}</b>" in SCREENS_JS
    assert 'colleague: (ctx.currentColleague() || {}).name || "",' in SCREENS_JS


def test_screens_js_dashboard_colleague_card():
    assert "function colleagueCardHtml(ctx)" in SCREENS_JS
    assert "${colleagueCardHtml(ctx)}" in SCREENS_JS
    assert "bindColleagueCard(ctx, root);" in SCREENS_JS
    assert 'data-colleague-q=' in SCREENS_JS


def test_css_colleague_styles_use_theme_vars():
    for cls in (".colleague-switch", ".colleague-intro", ".colleague-card",
                ".conclusion-card", ".concl-step"):
        assert cls in CSS, f"app.css 缺少 {cls} 样式块"
    # 颜色纪律：同事样式不写死色值（一律主题变量；守卫测试禁 #fff）
    block = CSS[CSS.find("岗位 AI 同事（D1/R2"):]
    assert "#fff" not in block.replace("#ffffff", ""), "新样式不得写死白色"
    assert "var(--primary-deep)" in block and "var(--bg-soft)" in block
