"""主题切换器（theme-switcher.js + app.css 主题令牌）单元契约测试。

背景
----
2026-10 主题系统两次真实回归：
1. ``--primary-bright`` 令牌在 :root 中丢失 → 所有引用它的渐变
   （Hero/品牌 Logo/头像/悬浮球/主按钮）在变量缺失时整体失效变透明；
2. ``html[data-accent]`` 块位于 ``html[data-theme="light"]`` 块之前，
   同权重级联下亮色块把主题色压回蓝色。
本文件把这两类结构事实固化成断言，防止再犯。

口径
----
- 只做源码静态契约（无 JS 运行时依赖，CI 无需浏览器）；
- 颜色值本身允许演进，断言的是「结构存在性」与「级联顺序」，
  不钉死具体色值（避免正常调色误伤）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
CSS = (UI_DIR / "app.css").read_text(encoding="utf-8")
JS = (UI_DIR / "theme-switcher.js").read_text(encoding="utf-8")
INDEX = (UI_DIR / "index.html").read_text(encoding="utf-8")

ACCENTS = ["cyan", "violet", "orange"]
#: 主题色块允许声明的变量（派生色必须同时给出，避免回退到默认蓝）
ACCENT_ALLOWED_VARS = {
    "--primary", "--primary-bright", "--primary-dark",
    "--primary-deep", "--primary-hover", "--primary-rgb",
}


def _block_start(css: str, marker: str) -> int:
    idx = css.find(marker)
    assert idx >= 0, f"app.css 缺少关键块：{marker}"
    return idx


# ============================================================
# 1. 令牌存在性（回归 1：--primary-bright 丢失）
# ============================================================

def test_root_defines_full_primary_family():
    """:root 必须定义完整主色家族——缺任何一个，引用它的渐变会整体失效。"""
    root_start = CSS.find(":root {")
    root_end = CSS.find("}", root_start)
    root = CSS[root_start:root_end]
    for token in ("--primary", "--primary-bright", "--primary-dark",
                  "--primary-deep", "--primary-hover", "--primary-rgb",
                  "--primary-light", "--primary-light-2", "--ring-color"):
        assert token + ":" in root, f":root 缺少 {token}（引用它的 background/text-shadow 将整体失效）"


def test_root_primary_rgb_is_static_triplet():
    """--primary-rgb 必须是静态 rgb 三元组（rgba(var(--primary-rgb), α) 的兼容性基础）。"""
    root_start = CSS.find(":root {")
    root_end = CSS.find("}", root_start)
    root = CSS[root_start:root_end]
    m = re.search(r"--primary-rgb:\s*([\d,\s]+);", root)
    assert m, ":root 缺少静态 --primary-rgb 三元组"
    parts = [p.strip() for p in m.group(1).split(",")]
    assert len(parts) == 3 and all(p.isdigit() for p in parts), \
        f"--primary-rgb 应为 'r, g, b' 三元组，实际：{m.group(1)!r}"


def test_light_theme_redefines_primary_family():
    """亮色块必须重定义主色家族（否则亮色下沿用深色派生值，文字不可读）。"""
    light_start = _block_start(CSS, 'html[data-theme="light"] {')
    light_end = CSS.find("}", light_start)
    light = CSS[light_start:light_end]
    for token in ("--primary", "--primary-bright", "--primary-dark",
                  "--primary-deep", "--primary-hover", "--primary-rgb"):
        assert token + ":" in light, f"亮色块缺少 {token}"


# ============================================================
# 2. 级联顺序（回归 2：accent 块被亮色块压住）
# ============================================================

def test_accent_blocks_declared_after_light_block():
    """主题色块必须位于亮色块之后——同权重级联，顺序颠倒即失效。"""
    pos_light = _block_start(CSS, 'html[data-theme="light"] {')
    for accent in ACCENTS:
        pos = CSS.find(f'html[data-accent="{accent}"]')
        assert pos > pos_light, (
            f'html[data-accent="{accent}"] 必须在 html[data-theme="light"] 之后'
            "（同权重级联，先声明者被后者覆盖）")


def test_accent_blocks_declare_full_family():
    """每个主题色块必须声明完整主色家族（core 三值 + dark 主题派生值）。"""
    for accent in ACCENTS:
        start = _block_start(CSS, f'html[data-accent="{accent}"]')
        body = CSS[start:CSS.find("}", start)]
        declared = set(re.findall(r"(--[a-z-]+)\s*:", body))
        missing = ACCENT_ALLOWED_VARS - declared
        assert not missing, f"accent={accent} 缺少变量：{sorted(missing)}"


def test_light_accent_override_blocks_exist():
    """亮色 + 主题色组合块必须存在：deep/hover 在亮色语境需要深色变体。"""
    for accent in ACCENTS:
        marker = f'html[data-theme="light"][data-accent="{accent}"]'
        assert marker in CSS, f"缺少亮色+主题色组合块：{marker}"


# ============================================================
# 3. 兼容性：不允许残留 color-mix（老版 Edge 不支持 → 整条声明失效）
# ============================================================

def test_no_functional_color_mix_left():
    """color-mix 在旧内核上会让整条声明失效（var 引用连带打穿），
    已全量替换为 rgba(var(--primary-rgb), α) / 静态值，不允许回流。"""
    functional = [m for m in re.findall(r"color-mix\([^)]*\)", CSS)]
    assert not functional, f"app.css 残留 color-mix()（旧内核会整条声明失效）：{functional[:3]}"


# ============================================================
# 4. theme-switcher.js 行为契约（静态语义）
# ============================================================

def test_js_storage_keys_and_theme_semantics():
    """存储键与主题语义：mfg-theme 只认 light（其余一律 dark），mfg-accent 带白名单回退。"""
    assert 'THEME_KEY = "mfg-theme"' in JS and 'ACCENT_KEY = "mfg-accent"' in JS
    assert 'localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"' in JS, \
        "getTheme 语义变更需同步更新本契约"
    assert 'ACCENTS.indexOf(a) >= 0 ? a : "blue"' in JS, \
        "getAccent 必须带白名单回退（非法存储值回落 blue）"


def test_js_accent_cycle_order():
    """主题色循环顺序由 ACCENTS 数组 + 取模表达式决定；提取实际值并复算。"""
    m = re.search(r"var ACCENTS = \[([^\]]+)\]", JS)
    assert m, "theme-switcher.js 缺少 ACCENTS 数组"
    accents = [a.strip().strip('"') for a in m.group(1).split(",")]
    assert accents[0] == "blue", "循环起点必须是默认蓝 blue"
    # 循环表达式存在：next = ACCENTS[(idx + 1) % len]
    assert re.search(r"ACCENTS\[\(ACCENTS\.indexOf\(getAccent\(\)\) \+ 1\) % ACCENTS\.length\]", JS), \
        "主题色循环必须走 ACCENTS 取模（保证 wraparound）"
    # 复算循环：blue → cyan → … → blue
    order = [accents[i % len(accents)] for i in range(len(accents) + 1)]
    assert order[0] == order[-1] == "blue" and len(set(order[:-1])) == len(accents)


def test_js_apply_sets_both_attributes_and_runs_immediately():
    """apply() 必须同时写 data-theme / data-accent（blue 时移除后者），且脚本加载即执行（防闪跳）。"""
    assert 'root.setAttribute("data-theme", getTheme());' in JS
    assert 'root.setAttribute("data-accent", getAccent());' in JS
    assert 'root.removeAttribute("data-accent");' in JS
    # IIFE 顶层立即调用（在 boot 定义之前）
    first_apply = JS.find("apply();")
    boot_def = JS.find("function boot()")
    assert 0 < first_apply < boot_def, "apply() 必须在模块加载时立即执行一次（首绘前设属性，避免主题闪跳）"


def test_js_boot_injects_buttons_once_before_refresh():
    """按钮注入：幂等守卫 + 插入到刷新按钮之前。"""
    assert 'if (!right || document.getElementById("btn-theme")) return;' in JS, \
        "boot() 必须幂等（重复调用不重复注入）"
    assert "right.insertBefore(themeBtn, refresh)" in JS, \
        "主题按钮应插入在刷新按钮之前"


# ============================================================
# 5. 挂载契约
# ============================================================

def test_index_html_mounts_theme_switcher():
    assert "theme-switcher.js" in INDEX, "index.html 未挂载 theme-switcher.js"
    assert (UI_DIR / "theme-switcher.js").exists()
