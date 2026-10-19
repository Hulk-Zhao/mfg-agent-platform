"""T17 组件底座 · SceneKit.knowledgeCard 守卫（纯 Python，禁网络 / 禁 node）。

守护**知识图片卡**（架构设计 §3.2 / PRD §5.2 组件 2）的组件契约：

1. **入参 schema**：``{title, svg, nodes:[{id,label,agent,tool,conclusion,refs}],
   caption}`` 每个键在实现里都有读取点；
2. **渲染结构四段**：图题行（标题 + ``SceneKit.demoBadge()``，与 pageHead 同一出口）
   → 内联 SVG 区 → 节点书签列 → 图文互证声明 caption；
3. **SVG 内联纪律**：内联字符串**直接插入**（不过 esc），零外部 URL——
   组件自带外链拦截，命中即整块降级（隔离红线友好，Q6 裁决）；
4. **书签 ↔ openDrill 复用**：点击书签 → SVG 对应节点高亮
   ``sk-kc-node-active`` + 复用既有 ``openDrill()`` 弹层展示
   取证智能体 / 工具 / 结论 / 关联口径；
5. **降级与样式**：svg 缺失 / 含外链 / 无书签都有诚实文案；
   样式类一律 ``sk-kc-`` 前缀经 injectStyle() 自注入，只取 app.css 变量。

徽标口径（共享知识 #3）：图题行的「演示数据」徽标走 ``demoBadge()``
（``.demo-flag``，T12 守护覆盖），与 agentPanel 的来源徽标
``sk-source-badge`` 类名分离、各司其职。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))  # 与 tests/ 其它用例同口径

SCENES_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui" / "scenes"
SCENE_KIT = SCENES_DIR / "scene-kit.js"

#: knowledgeCard 内联夹具（S5 取证图谱七节点形态，架构 §3.2）
CARD_FIXTURE = {
    "title": "批次追溯取证图谱（DEMO-CC-001）",
    "svg": '<svg xmlns="http://www.w3.org/2000/svg" id="kc-graph" viewBox="0 0 320 120"'
           ' role="img"><g id="lot" class="kc-node">'
           '<rect x="10" y="10" width="80" height="40"/></g></svg>',
    "nodes": [
        {"id": "lot", "label": "批次", "agent": "质量域智能体",
         "tool": "run_registered_query", "conclusion": "定位批次 DEMO-LOT-002",
         "refs": ["DEMO-EV-0025（events · stage=located）", "lots（同库位批）"]},
    ],
    "caption": "图中结论与智能体协同面板结论同源，可逐条对回。",
}


def _src() -> str:
    assert SCENE_KIT.exists(), f"缺少 scene-kit.js：{SCENE_KIT}"
    return SCENE_KIT.read_text(encoding="utf-8")


def _export_block(src: str) -> str:
    """抽出 ``const SceneKit = { ... };`` 导出对象体。"""
    matched = re.search(r"const SceneKit = \{(.*?)\n  \};", src, re.S)
    assert matched, "未找到 SceneKit 导出对象（写法被改？请同步更新本断言）"
    return matched.group(1)


def _function_body(src: str, signature: str) -> str:
    """按函数签名截取函数体（到下一个同级右花括号）。"""
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    begun = False
    for pos in range(i, len(src)):
        ch = src[pos]
        if ch == "{":
            depth += 1
            begun = True
        elif ch == "}":
            depth -= 1
            if begun and depth == 0:
                return src[start:pos + 1]
    raise AssertionError(f"函数体未闭合：{signature}")


# ============================================================
# 1. 导出契约
# ============================================================

def test_scene_kit_exports_component_apis():
    """knowledgeCard 与同批 API（agentPanel / replay）都在导出对象上。"""
    block = _export_block(_src())
    for api in ("agentPanel", "knowledgeCard", "replay"):
        assert re.search(rf"(^|\s){api},", block), (
            f"SceneKit 导出对象缺少 {api}")


# ============================================================
# 2. 入参 schema（内联夹具逐字段对读）
# ============================================================

@pytest.mark.parametrize("key", ["title", "svg", "nodes", "caption"])
def test_knowledge_card_schema_key_is_read(key: str):
    """schema 每个键在实现里都有读取点（o.<key>）。"""
    src = _src()
    assert re.search(rf"\bo\.{re.escape(key)}\b", src), (
        f"knowledgeCard 未读取入参键 {key}（架构 §3.2 schema 缺读取点）")


def test_knowledge_card_node_fields_are_read():
    """书签节点六字段（id/label/agent/tool/conclusion/refs）都有渲染读取。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    for field in ("id", "label", "agent", "tool", "conclusion", "refs"):
        assert re.search(rf"nd\.{field}\b", body), (
            f"nodes[].{field} 无渲染读取（书签与 svg 节点对回的字段链断裂）")


def test_knowledge_card_doc_block_declares_schema():
    """JSDoc 必须写明入参 schema 与「内联 SVG 零外部资源」纪律。"""
    src = _src()
    doc = re.search(r"/\*\*\n   \* 知识图片卡（架构 §3\.2）(.*?)\*/", src, re.S)
    assert doc, "knowledgeCard 缺少 JSDoc（schema 文档化是组件契约的一部分）"
    text = doc.group(1)
    for key in ("title", "svg", "nodes", "caption"):
        assert key in text, f"knowledgeCard JSDoc 未声明 schema 键 {key}"
    assert "零外部资源" in text, "JSDoc 必须写明内联 SVG 零外部资源纪律"


# ============================================================
# 3. 渲染结构四段 + demoBadge 复用
# ============================================================

def test_knowledge_card_renders_four_parts():
    """结构四段：图题行 / SVG 区 / 书签列 / 图文互证声明。"""
    src = _src()
    markers = [
        'data-sk-knowledge-card="1"',  # 卡片容器（点击委托的作用域锚）
        "sk-kc-svg",                   # SVG 区
        "sk-kc-bookmarks",             # 节点书签列
        "sk-kc-caption",               # 图文互证声明
    ]
    for marker in markers:
        assert marker in src, f"渲染结构缺少 {marker}"
    body = _function_body(src, "function knowledgeCard(opts)")
    assert '<div class="card-head">' in body, "图题行必须走既有 card-head 结构"


def test_knowledge_card_reuses_demo_badge_export():
    """图题行徽标复用 SceneKit.demoBadge()（.demo-flag 唯一出口），不另造。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    assert "demoBadge()" in body, (
        "knowledgeCard 必须经 demoBadge() 产出「演示数据」徽标（唯一出口）")
    assert 'class="demo-flag"' not in body, (
        "图题行不得自行拼接 .demo-flag 类名（唯一出口纪律，防徽标散失）")


# ============================================================
# 4. SVG 内联纪律（Q6 裁决：现场生成、零外部资源）
# ============================================================

def test_svg_is_inserted_inline_without_escape():
    """内联字符串直接插入（不过 esc——过了就变成转义文本，图全废）。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    assert '\'<div class="sk-kc-svg">\' + svgText' in body, (
        "SVG 必须直接拼接插入（esc(svgText) 会把图形转义成文本）")
    assert "esc(svgText)" not in body and "esc(o.svg)" not in body, (
        "SVG 字符串不得过 esc()")


def test_svg_external_link_is_intercepted():
    """组件自带外链拦截：命中 http(s):// 即整块降级（隔离红线友好）。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    assert "svgHasExternalResource(svgText)" in body, (
        "knowledgeCard 必须经外链检测守卫后才插入 SVG")
    guard = _function_body(src, "function svgHasExternalResource(svgText)")
    assert "https?" in guard, "守卫必须识别 http / https 外链"
    assert "SVG_NS_RE" in guard, "守卫必须放行 W3C 命名空间声明"
    ns = re.search(r"const SVG_NS_RE = (.*?);", src, re.S)
    assert ns and re.search(r"w3\\?\.org", ns.group(1)), (
        "xmlns 命名空间声明（w3.org）是标识符不是网络请求，必须放行")
    assert "图谱包含外部资源引用，已按隔离纪律拦截" in body, (
        "外链命中时必须整块降级并说明（隔离纪律）")
    assert 'data-sk-kc-degrade="1"' in body, "降级块必须带标记（可截图取证）"


def test_component_markup_has_no_external_urls():
    """组件自身产出的 markup 不得引入外部资源（src/href 外链、CDN）。"""
    src = _src()
    # 只看新增组件的实现区（既有 demo-ui 文案不含外链）
    region = src[src.index("智能体协同组件（T17 · 架构设计 §3"):]
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?:', region), (
        "组件 markup 出现外部资源引用（src/href 外链）")
    assert "cdn" not in region.lower(), "组件不得引用 CDN"


def test_svg_missing_degrades_honestly():
    """svg 缺失 → 整块降级，不渲染编造图形。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    assert "知识图片数据缺失，不展示编造图形" in body, (
        "svg 为空时必须整块降级（不展示编造图形）")


# ============================================================
# 5. 书签 ↔ openDrill 复用 + 节点高亮契约
# ============================================================

def test_bookmarks_reuse_open_drill():
    """点击书签复用既有 openDrill() 弹层（不另造弹层）。"""
    src = _src()
    handler = _function_body(src, "function onKnowledgeBookmarkClick(btn)")
    assert "openDrill(" in handler, "书签点击必须复用 openDrill() 弹层"
    assert 'closest("[data-sk-knowledge-card]")' in handler, (
        "处理器必须限定在本卡片作用域内（多卡同屏不串台）")
    assert 'data-sk-kc-bookmark="' in src, "书签按钮必须带 data-sk-kc-bookmark 锚"
    assert "document.addEventListener(\"click\"" in src, (
        "交互必须走文档级委托（屏只插 HTML 不必各自绑事件）")


def test_bookmark_highlight_contract():
    """点击书签 → SVG 对应节点高亮 sk-kc-node-active（id / data-sk-kc-node 双钩）。"""
    src = _src()
    handler = _function_body(src, "function highlightKnowledgeNode(card, nodeId)")
    assert 'classList.toggle("sk-kc-node-active"' in handler, (
        "高亮类名必须是 sk-kc-node-active")
    assert 'getAttribute("id") === nodeId' in handler, (
        "书签必须能按 id 命中 SVG 节点（一一对应）")
    assert 'getAttribute("data-sk-kc-node") === nodeId' in handler, (
        "应同时支持 data-sk-kc-node 钩子（场景 SVG 未必带 id）")
    assert "console.warn" in handler, (
        "书签在 SVG 中找不到对应节点时必须留痕（不许静默不高亮）")
    css = re.search(r"const CSS = `(.*?)`;", src, re.S).group(1)
    assert ".sk-kc .sk-kc-node-active" in css, (
        "样式块必须有 .sk-kc-node-active 高亮规则（描边加粗）")
    assert "stroke-width: 2.8" in css, "高亮必须是描边加粗（2.8）"


def test_drill_content_shows_agent_tool_conclusion_refs():
    """弹层四段：取证智能体 / 工具 / 结论 / 关联口径。"""
    src = _src()
    body = _function_body(src, "function kcDrillHtml(node)")
    for label in ("取证智能体", "取证工具", "节点结论", "关联口径"):
        assert label in body, f"弹层缺少「{label}」段"
    assert "agentKv(" in body, "弹层键值行应复用既有 sk-kv 结构"


def test_bookmark_activation_state_is_tracked():
    """书签激活态可辨（is-active + aria-pressed），再次点击不丢状态。"""
    src = _src()
    handler = _function_body(src, "function onKnowledgeBookmarkClick(btn)")
    assert 'classList.toggle("is-active"' in handler, "书签缺少激活态类"
    assert 'setAttribute("aria-pressed"' in handler, "书签缺少 aria-pressed 无障碍态"


def test_bookmarks_empty_is_explicit():
    """无书签时显式说明（不静默渲染空列）。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    assert "本图未配置节点书签" in body, "无书签时必须显式说明"


# ============================================================
# 6. 图文互证声明
# ============================================================

def test_caption_renders_mutual_evidence_statement():
    """caption 图文互证声明渲染；JSDoc 写明「同源、可逐条对回」口径。"""
    src = _src()
    body = _function_body(src, "function knowledgeCard(opts)")
    assert "sk-kc-caption" in body and "esc(caption)" in body, (
        "caption 必须经 esc 渲染进图文互证声明位")
    doc = re.search(r"/\*\*\n   \* 知识图片卡（架构 §3\.2）(.*?)\*/", src, re.S)
    assert doc and "同源" in doc.group(1), (
        "JSDoc 必须写明 caption 口径（与智能体协同面板结论同源，可逐条对回）")
    fixture_caption = CARD_FIXTURE["caption"]
    assert "同源" in fixture_caption and "对回" in fixture_caption, (
        "内联夹具 caption 必须体现图文互证口径（供屏侧照抄）")


# ============================================================
# 7. 样式块（sk-kc- 前缀 + CSS 变量纪律）
# ============================================================

def test_style_block_has_sk_kc_prefixes():
    """knowledgeCard 样式类一律 sk-kc- 前缀，经 injectStyle 自注入。"""
    src = _src()
    css = re.search(r"const CSS = `(.*?)`;", src, re.S)
    assert css, "未找到 CSS 模板字符串（injectStyle 的数据源）"
    text = css.group(1)
    for cls in ("sk-kc", "sk-kc-svg", "sk-kc-bookmarks", "sk-kc-bookmark",
                "sk-kc-caption", "sk-kc-node-active"):
        assert f".{cls}" in text, f"样式块缺少 .{cls}（sk- 前缀纪律）"
    assert "function injectStyle()" in src, "样式必须经 injectStyle() 自注入"


def test_new_styles_reuse_css_variables_no_dark_hardcode():
    """新样式只取 app.css 变量，不得硬编码深色底（T05 既有断言同口径）。"""
    src = _src()
    css = re.search(r"const CSS = `(.*?)`;", src, re.S).group(1)
    new_block = css[css.index("智能体协同组件（T17"):]
    assert "--border" in new_block and "--primary" in new_block, (
        "新样式必须复用 app.css 的 CSS 变量（--border / --primary）")
    assert not re.search(r"background:\s*#[0-9a-fA-F]{3,8}", new_block), (
        "新样式出现硬编码底色（light/dark 主题切换会静默劣化）")


# ============================================================
# 8. 括号 / 字符串闭合巡检（与 test_agent_panel 同模式，本机无 node 兜底）
# ============================================================

def _strip_js(src: str) -> str:
    """去掉注释 / 字符串 / 正则字面量，留下括号骨架（只用于闭合巡检）。

    增强点（相对 test_s01 同名函数）：识别**正则字面量**——scene-kit.js 的
    DEMO_BADGE_CLASS_RE 内含双引号，朴素算法会把正则里的引号当作字符串起始、
    级联误吞后续代码括号，对含正则的文件必须走本增强版。
    """
    out = []
    i = 0
    n = len(src)
    prev = ""
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and (prev in "=(,:[!&|?{;" or prev.isalpha()):
            j = i + 1
            in_class = False
            while j < n:
                c = src[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                elif c == "\n":
                    break
                j += 1
            if j < n and src[j] == "/":
                i = j + 1
                while i < n and src[i].isalpha():
                    i += 1
                continue
        if not ch.isspace():
            prev = ch
        out.append(ch)
        i += 1
    return "".join(out)


def test_js_delimiters_are_balanced():
    """括号/字符串闭合巡检（不是语法分析器，只挡「多一个括号」类事故）。"""
    code = _strip_js(_src())
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for ch in code:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            assert stack and stack[-1] == pairs[ch], (
                f"scene-kit.js 括号不闭合：遇到 {ch}，栈顶 {stack[-1] if stack else '空'}")
            stack.pop()
    assert not stack, f"scene-kit.js 存在未闭合的 {stack}"
    assert "function" in code, "未扫到函数定义，剥离逻辑可能失效"
    assert "knowledgeCard" in code, "剥离结果中未见到 knowledgeCard（实现被吞？）"
