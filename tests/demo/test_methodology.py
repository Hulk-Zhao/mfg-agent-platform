"""R12 落地方法论产品化（帮助中心）静态契约。"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


def test_methodology_sections_and_counts():
    js = (UI / "help-screen.js").read_text(encoding="utf-8")
    assert "落地方法论" in js and "上线六项体检" in js
    assert "METH_SEVEN" in js and "METH_FIVE" in js and "METH_EVOLUTION" in js
    assert "METH_CHECKS" in js
    # 七步 / 五步 / 六项 —— 数量写死，改内容必须同步改测试（防悄悄少一项）
    seven = js[js.index("const METH_SEVEN"):js.index("const METH_FIVE")]
    five = js[js.index("const METH_FIVE"):js.index("const METH_EVOLUTION")]
    checks = js[js.index("const METH_CHECKS"):js.index("function loadMethState")]
    assert len(re.findall(r"\{ t:", seven)) == 7
    assert len(re.findall(r"\{ t:", five)) == 5
    assert len(re.findall(r'\{ id: "', checks)) == 6


def test_checklist_persists_and_exports_markdown():
    js = (UI / "help-screen.js").read_text(encoding="utf-8")
    assert 'METH_KEY = "mfg_launch_check_v1"' in js
    assert 'data-mth-check="' in js
    assert "localStorage.getItem(METH_KEY)" in js
    assert "localStorage.setItem(METH_KEY" in js
    assert "function exportChecklist()" in js
    assert "text/markdown" in js and ".md`" in js
    assert "上线体检清单-" in js
    assert "window.HelpScreen = { render, exportChecklist };" in js
    # 六项体检：通过标准 + 完成度 + 未通过项
    assert "完成度：" in js and "未通过项" in js
    assert "上线六项体检清单" in js


def test_methodology_styles_use_theme_vars():
    css = (UI / "app.css").read_text(encoding="utf-8")
    start = css.index("/* R12 帮助中心")
    end = css.index("/* ============================================================", start + 10)
    block = css[start:end]
    assert ".mth-card" in block and ".mth-check-row" in block
    assert "var(--primary)" in block and "var(--ok)" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), \
        "落地方法论样式必须用主题变量，不许写死 hex"
