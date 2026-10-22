"""R10 老板经营 · 大单进度卡前端接线守卫（静态代码点）。"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


def test_business_card_rendering_and_wiring():
    js = (UI / "screens.js").read_text(encoding="utf-8")
    assert "function businessCardHtml(ctx, result, opts)" in js
    assert 'result.route.strategy === "business"' in js
    assert 'kind: "business"' in js
    assert 'm.kind === "business"' in js
    # 一眼可见：整体 + 单张进度条、主动风险、预计完工
    assert "biz-bar" in js and "biz-risks-title" in js
    assert "主动风险（不等你问）" in js
    assert "预计完工" in js


def test_business_card_styles_use_theme_vars():
    css = (UI / "app.css").read_text(encoding="utf-8")
    start = css.index("/* R10 老板经营")
    block = css[start:start + 2200]
    assert ".biz-card" in block and ".biz-bar" in block
    assert "var(--warn)" in block
    assert not __import__("re").search(r"#[0-9a-fA-F]{3,8}\b", block), \
        "大单进度卡样式必须用主题变量，不许写死 hex"
