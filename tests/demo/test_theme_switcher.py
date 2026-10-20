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
AGING_JS = (UI_DIR / "aging-mode.js").read_text(encoding="utf-8")
DEMO_API = (REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py").read_text(encoding="utf-8")

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
    """存储键与主题语义：mfg-theme 只认 dark（其余一律 light = 默认明亮），mfg-accent 带白名单回退。"""
    assert 'THEME_KEY = "mfg-theme"' in JS and 'ACCENT_KEY = "mfg-accent"' in JS
    assert 'localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"' in JS, \
        "getTheme 语义变更需同步更新本契约（默认必须是明亮 light）"
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
    assert 'if (right && !document.getElementById("btn-theme")) {' in JS, \
        "boot() 必须幂等（重复调用不重复注入）"
    assert "right.insertBefore(themeBtn, refresh)" in JS, \
        "主题按钮应插入在刷新按钮之前"


def test_js_mounts_login_floating_controls():
    """登录页也必须有主题/字号入口：浮动控件挂在 body（#login-root 的后续兄弟），
    显隐走纯 CSS 兄弟选择器（#login-root:not([hidden]) ~ …），登录/登出零 JS 联动。"""
    assert 'id = "login-display-controls"' in JS, "缺少登录页浮动控件容器"
    assert "#login-root:not([hidden]) ~ #login-display-controls" in JS, \
        "显隐必须用 CSS 兄弟选择器（不能靠 JS 监听登录态）"
    assert "document.body.appendChild(box)" in JS, \
        "容器必须挂在 body 下且在 #login-root 之后（~ 选择器前提）"
    assert "mountLoginControls();" in JS and "function mountLoginControls()" in JS
    # 多实例图标同步：切换动作必须刷新所有 data-ts-* 按钮（顶栏 + 登录页）
    assert "refreshButtons()" in JS and "data-ts-theme" in JS and "data-ts-accent" in JS


def test_aging_mode_mounts_login_font_trigger():
    """aging-mode 必须把「Aa」字号入口追加进登录页浮动容器（am-login-btn）。"""
    assert 'function mountLoginTrigger()' in AGING_JS and "mountLoginTrigger();" in AGING_JS
    assert '"am-login-btn"' in AGING_JS, "缺少登录页字号按钮 id"
    assert '"login-display-controls"' in AGING_JS, "字号入口必须挂进登录页浮动控件容器"


# ============================================================
# 5. 挂载契约
# ============================================================

def test_index_html_mounts_theme_switcher():
    assert "theme-switcher.js" in INDEX, "index.html 未挂载 theme-switcher.js"
    assert (UI_DIR / "theme-switcher.js").exists()


# ============================================================
# 6. 首次打开 / 登录的默认外观（2026-10-20 产品裁定）
#    蓝色主题色 + 明亮 + 清晰字号（字号 +12%），且首绘前不得闪出深色
# ============================================================

def test_index_html_declares_light_theme_default():
    """<html> 必须静态声明明亮与清晰字号——不依赖 JS 执行，首帧就是亮色。

    背景：theme-switcher.js / aging-mode.js 都是 defer，排在数百 KB 脚本之后，
    首绘可能早于它们执行；只靠 JS 设属性会先闪一帧深色（历史问题）。
    """
    assert '<html lang="zh-CN" data-theme="light" data-font="medium"' in INDEX, \
        "index.html 的 <html> 必须静态声明 data-theme=light / data-font=medium"


def test_index_html_inline_boot_restores_saved_prefs_before_css():
    """head 内联脚本必须在样式表之前：已存过偏好的用户一次性还原，避免主题/字号闪跳。"""
    boot = INDEX.find('ls.getItem("mfg-theme")')
    assert boot >= 0, "index.html 缺少首绘前读取 mfg-theme 的内联脚本"
    assert boot < INDEX.find('href="app.css'), "内联首绘脚本必须排在 app.css 之前（否则仍会闪一帧）"
    for key in ('"mfg-accent"', '"mfg-font-scale"', '"mfg-density"'):
        assert key in INDEX, f"内联首绘脚本漏还原 {key}（该偏好会先闪默认值再变）"
    assert '=== "dark"' in INDEX, "只有显式存过 dark 才回落深色（默认必须是明亮）"


def test_aging_mode_default_theme_and_font():
    """aging-mode 的 DEFAULTS 必须与默认外观一致：明亮 + 清晰档字号（medium）。"""
    assert 'var DEFAULT_THEME = "light";' in AGING_JS, \
        "aging-mode.js 默认主题必须为 light（与 theme-switcher 同口径）"
    assert 'var DEFAULTS = { font: "medium", density: "standard" };' in AGING_JS, \
        "aging-mode.js 默认字号必须是「清晰」档 medium（字号 +12%）"
    assert 'state.theme = t === "dark" ? "dark" : "light";' in AGING_JS, \
        "readState 的主题兜底必须是明亮（未存偏好即 light）"
    assert 'commit({ font: DEFAULTS.font, density: DEFAULTS.density, theme: DEFAULT_THEME }, "clear")' in AGING_JS, \
        "「恢复默认显示」必须回到出厂默认（清晰档 + 明亮）"


def test_demo_api_versions_theme_switcher():
    """dynamic index 的 mtime 版本号清单必须含 theme-switcher.js——否则改完浏览器仍跑旧脚本。"""
    marker = 'for f in ('
    start = DEMO_API.find(marker)
    assert start >= 0, "api.py 找不到 dynamic index 的资源清单"
    line = DEMO_API[start:DEMO_API.find("):", start)]
    assert "theme-switcher.js" in line, \
        "theme-switcher.js 未进 mtime 版本号清单（index.html 里写死的 ?v=1 会长期命中旧缓存）"


def test_help_arch_theme_adaptive_rules():
    """帮助中心架构图的「强块」必须走类选择器主题自适应（2026-10-20 亮色适配）。"""
    help_js = (UI_DIR / "help-screen.js").read_text(encoding="utf-8")
    assert 'class="help-arch"' in help_js, "架构图 svg 缺少 help-arch 类（CSS 钩子失效）"
    for cls in ("arch-portal", "arch-base", "arch-toolbox"):
        assert cls in help_js and f".help-arch .{cls}" in CSS, f"架构图缺少 {cls} 主题规则"
    assert 'html[data-theme="light"] .help-arch .arch-base {' in CSS, \
        "亮色主题必须翻转治理底盘（否则压一块深藏青与亮色不适配）"


# ============================================================
# 7. 字号缩放覆盖（aging.css 第 3 节）
#    背景：白名单只覆盖「主流容器」，看板 2 级 → 3 级明细弹窗（.std-modal 体系）
#    与下钻内容区字号写死 px、不在名单里 → 切「大 / 超大」档时弹窗内文字不动
#    （2026-10-20 用户反馈，截图红框：标题栏 / 指标大数 / 小节说明）。
# ============================================================

AGING_CSS = (UI_DIR / "aging.css").read_text(encoding="utf-8")

#: 缺口家族的核心选择器（弹窗标题栏 / 指标大数 / 小节说明 / 行内按钮）
_MODAL_DRILL_SELECTORS = (
    ".std-modal-head b", ".std-modal-sub", ".std-modal-title", ".std-modal-note",
    ".std-chip", ".std-conform",
    ".drill-hero-label", ".drill-hero-value .big", ".drill-hero-sub", ".drill-def",
    ".rank-name", ".row-ask", ".section-title .section-extra",
)


def test_aging_css_scales_modal_and_drill_content():
    """弹窗 / 下钻内容家族必须挂 calc(var(--font-scale)) 缩放（否则放大档失效）。"""
    for sel in _MODAL_DRILL_SELECTORS:
        assert re.search(
            re.escape(sel) + r"\s*\{[^{}]*font-size:\s*calc\([^;{}]*var\(--font-scale\)",
            AGING_CSS,
        ), f"aging.css 未对 `{sel}` 做字号缩放（切换大字号档时该处不会变大）"


def test_aging_css_replicates_compound_selectors():
    """复合选择器必须逐字复刻，否则特异性 < app.css 原规则，覆盖会失效。"""
    for sel in (".std-table td.std-conform", ".drill-table td.drill-dim",
                ".rank-row.locked .rank-name::after", ".pareto-toolbar .chip"):
        assert re.search(
            re.escape(sel) + r"\s*\{[^{}]*var\(--font-scale\)",
            AGING_CSS,
        ), f"复合选择器 `{sel}` 未原样复刻（特异性不足，放大档仍不生效）"

