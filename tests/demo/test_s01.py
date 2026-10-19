"""S1 接单评审屏守卫（T07 交付物，纯 Python，禁网络 / 禁 node）。

守护的是本屏最容易悄悄失守的三件事：

1. **不许落散数**：ATP 三来源、报价三分量、会签槽位都必须运行时经
   ``SceneKit.story()`` 从 T04 装载层取；屏里出现 ``DEMO-`` 故事 id 或
   金额字面量（161200 / 169309 / 182854）就说明有人把数据抄进了代码——
   下一轮 T02 改数据时屏上数字会静默失真。
2. **取不到数不编数**：装载层未就绪时必须走降级分支（文案「演示装载层未就绪」），
   而不是回落到手写常量。
3. **口径字典不许漂移**：``data/demo_storyline/frontend/s01-intake.js``（权威副本）
   与 ``scenes/s01-intake.js`` 的内联镜像必须逐值一致；两侧任改一处而忘另一处，
   浏览器读到的公式与仓库里的口径就会分叉。

另有一条环境事实：本机没有 node，``node --check`` 跑不了，所以用
``test_js_delimiters_are_balanced`` 做括号/字符串闭合巡检兜底（不是语法分析器，
只挡「多写一个括号」这类把整屏打白的事故）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
SCENES_DIR = UI_DIR / "scenes"
SCENE_JS = SCENES_DIR / "s01-intake.js"
DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
FRONTEND_DIR = DATA_DIR / "frontend"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"
SCENE_DATA_JS = FRONTEND_DIR / "s01-intake.js"
INDEX_HTML = UI_DIR / "index.html"

SCENE_ID = "s01"

#: 32 角色治理目录中承接五访客的角色 key（后端只认这些）
GOVERNANCE_KEYS = {"gm", "sales", "rd_engineering", "qa", "manufacturing"}

#: 屏里一旦出现这些字面量，就说明数据被抄进了代码（而非运行时取数）
FORBIDDEN_LITERALS = (
    "DEMO-SO", "DEMO-QT", "DEMO-CUS", "DEMO-EV", "DEMO-WO", "DEMO-LOT", "DEMO-PO",
    "161200", "169309", "182854",
)


# ============================================================
# 通用工具
# ============================================================

def _scene_src() -> str:
    assert SCENE_JS.exists(), f"缺少 T07 屏文件：{SCENE_JS}"
    return SCENE_JS.read_text(encoding="utf-8")


def _data_src() -> str:
    assert SCENE_DATA_JS.exists(), f"缺少 T07 数据文件：{SCENE_DATA_JS}"
    return SCENE_DATA_JS.read_text(encoding="utf-8")


def _registry() -> dict:
    return json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))


def _scene_entry() -> dict:
    scenes = _registry()["scenes"]
    hit = [scene for scene in scenes if scene.get("id") == SCENE_ID]
    assert hit, f"registry.json 的 scenes 中没有 {SCENE_ID} 条目（T07 未登记）"
    return hit[0]


def _json_block(src: str, anchor: str) -> dict:
    """从 JS 里抽出 ``const <anchor> = { ... };`` 的 JSON 块（两处块都写成严格 JSON）。"""
    match = re.search(rf"const {re.escape(anchor)} = (\{{[\s\S]*?\n {{2}}\}});", src)
    assert match, f"未找到 {anchor} 对象字面量（写法须为严格 JSON，便于逐值比对）"
    return json.loads(match.group(1))


def _strip_js(src: str) -> str:
    """去掉注释与字符串字面量，留下括号骨架（只用于闭合巡检）。"""
    out = []
    i = 0
    n = len(src)
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
        out.append(ch)
        i += 1
    return "".join(out)


# ============================================================
# 1. 屏脚本：登记方式 / 组件契约 / 降级纪律
# ============================================================

def test_scene_registers_through_registry():
    """必须经 SceneRegistry.register() 自注册（不改 app.js），五访客口径齐全。"""
    src = _scene_src()
    assert "window.SceneRegistry.register(" in src, "未通过 SceneRegistry.register() 登记"
    assert 'id: SCENE_ID' in src or f'id: "{SCENE_ID}"' in src or "id: SCENE_ID" in src, (
        f"登记 id 不是 {SCENE_ID}")
    assert "window.SceneRegistry.GROUP_NAME" in src, "分组必须取 SceneRegistry.GROUP_NAME"
    assert "window.SceneRegistry.BUSINESS_ROLES" in src, (
        "分工口径必须取 SceneRegistry.BUSINESS_ROLES（业务岗位 id + 治理角色 key 双口径）")
    for key in ("label", "title", "sub", "icon", "order", "render"):
        assert re.search(rf"\b{key}:", src), f"登记对象缺少 {key}"


def test_scene_uses_contract_components():
    """屏必须复用 T05 组件契约，而不是自己拼页面骨架。"""
    src = _scene_src()
    for api in ("use(", "esc(", "icon(", "badge(", "pageHead(", "numberRow(",
                "signoffCard(", "eventFlow(", "openDrill(", "story("):
        assert api in src, f"s01 未使用组件契约 {api}"
    assert "bindDrills(" in src, "数字卡未绑定下钻（bindDrills）"


def test_scene_keeps_numbers_derived_not_hardcoded():
    """屏里不许出现故事 id 与金额字面量：数字只能来自装载层。"""
    src = _scene_src()
    hits = [text for text in FORBIDDEN_LITERALS if text in src]
    assert not hits, (
        f"s01-intake.js 出现故事数据字面量 {hits}——数字必须经 SceneKit.story() 现算，"
        "抄进代码会在 T02 改数据后静默失真")


def test_scene_degrades_when_loader_missing():
    """装载层未就绪必须走降级分支，不允许回落手写常量。"""
    src = _scene_src()
    assert "演示装载层未就绪" in src, "缺降级文案「演示装载层未就绪」"
    assert "sk-degrade" in src, "降级提示未用组件契约的 sk-degrade 结构"
    assert "missingEssential" in src, "缺少必需对象缺失的判定（必需对象取不到就不给数）"
    for name in ("sales_orders", "quotes", "customers", "events"):
        assert name in src, f"必需对象清单缺少 {name}"


def test_scene_signoff_is_role_aware_and_honest():
    """会签卡：三槽位留痕 + 按岗位区分视角 + 不臆造订单 / 姓名（T15 起接真实工单）。"""
    src = _scene_src()
    assert "payload.slots" in src, "槽位必须取自事件 payload.slots（不许写死三槽位）"
    assert "signoffCard(" in src, "未用会签卡组件"
    for key in ("slotByRole", "viewLabels", "visibilityText", "viewKindOf"):
        assert key in src, f"缺少岗位视角分派 {key}（申请人 / 会签人 / 观察者要看到不同内容）"
    assert "姓名" in src, "必须说明事件未记录会签人姓名（不许虚构姓名）"
    assert "只读" in src, "只读演示纪律未在屏上体现"
    # T15：会签留痕改为「真实治理工单 + 事件流回放」双轨，屏上必须写明槽位与取数接口
    assert "order_review" in src, "必须写明 order_review 治理槽位（T15 起为平台真实工单）"
    assert "wf_id" in src or "wfId" in src, "工单必须按 wf_id 过滤命中（不许把别人的工单当自己的）"


def test_scene_reads_live_workorder_but_never_writes_it():
    """T15 只读铁律：屏须拉真实待办（workflowPending），但**严禁**任何治理写调用。

    人裁决（T15）：S1 屏改为「真实工单驱动 + 按槽位可见的不同待办」，会签卡仍只读、
    无写按钮；真实会签动作留在治理窗口。本用例把这条裁决固化成守卫——屏里一旦出现
    ``workflowSelectAction`` / ``workflowCosign`` / ``workflowApprove`` / ``workflowReject``，
    说明有人把写入口偷偷搬到了只读演示屏。
    """
    src = _scene_src()
    assert "workflowPending(" in src, "T15 起本屏须按岗位令牌拉取真实治理工单"
    write_calls = ("workflowSelectAction", "workflowCosign",
                   "workflowApprove", "workflowReject")
    hits = [call for call in write_calls if f"{call}(" in src]
    assert not hits, f"S1 为只读演示屏，出现治理写调用 {hits}（会签写权限保持只读）"


def test_scene_scopes_transit_and_pos_by_bom():
    """在途批次与料期必须按「BOM 用料」取数。

    只按成品物料号过滤，会漏掉 BOM 组成件的在途批（本屏首版就踩过：在途批次显示 0 片，
    而数据集里 MAT-2202 有一批 600 片在途）。料期初评盯的正是这些组成件的到料风险。
    """
    src = _scene_src()
    assert "scopeMaterialIds" in src, "未按 BOM 用料给定料期 / 在途的取数范围"
    assert "scopedLots" in src, "批次未做 BOM 范围过滤（在途批会漏）"
    assert "scopedPos" in src, "采购单未做 BOM 范围过滤"
    assert "BOM" in src, "屏上未说明料期范围按 BOM 用料"


def test_js_delimiters_are_balanced():
    """括号/字符串闭合巡检（本机无 node，用骨架扫描兜住「多一个括号」类事故）。"""
    for path in (SCENE_JS, SCENE_DATA_JS):
        code = _strip_js(path.read_text(encoding="utf-8"))
        pairs = {")": "(", "]": "[", "}": "{"}
        stack: list[str] = []
        for ch in code:
            if ch in "([{":
                stack.append(ch)
            elif ch in ")]}":
                assert stack and stack[-1] == pairs[ch], (
                    f"{path.name} 括号不闭合：遇到 {ch}，栈顶 {stack[-1] if stack else '空'}")
                stack.pop()
        assert not stack, f"{path.name} 存在未闭合的 {stack}"
        assert "function" in code, f"{path.name} 未扫到函数定义，剥离逻辑可能失效"


# ============================================================
# 2. registry.json 登记条目
# ============================================================

def test_registry_entry_declares_owner_files_and_roles():
    """s01 条目：owner / 屏文件 / 数据文件 / roles / objects 全部合法。"""
    entry = _scene_entry()
    assert entry.get("owner") == "T07", f"s01.owner 应为 T07，实际 {entry.get('owner')}"

    script_rel = entry.get("module") or entry.get("file")
    assert script_rel == "scenes/s01-intake.js", f"s01 屏文件键指向 {script_rel}"
    assert (UI_DIR / script_rel).exists(), f"s01 屏文件不存在：{script_rel}"

    data_rel = entry.get("data") or entry.get("data_file")
    assert data_rel, "s01 未声明数据文件（T07 交付含 frontend/s01-intake.js）"
    assert (FRONTEND_DIR / Path(data_rel).name).exists(), f"s01 数据文件不存在：{data_rel}"

    roles = entry.get("roles") or []
    assert roles, "s01.roles 不能为空（省略即全员可见，与验收口径不符）"
    assert set(roles) <= GOVERNANCE_KEYS | {"biz", "rd", "mfg"}, f"s01.roles 越界：{roles}"
    assert {"sales", "rd_engineering", "manufacturing"} <= set(roles), (
        f"s01.roles 缺治理角色 key，五访客会看不到本屏：{roles}")

    objects = entry.get("objects") or []
    known = _registry()["objects"]
    unknown = sorted(set(objects) - set(known))
    assert not unknown, f"s01.objects 引用未登记对象（会 404）：{unknown}"


def test_registry_objects_match_scene_mirror():
    """registry.json 的 s01.objects 必须与屏内口径字典 objects 一致。"""
    entry = _scene_entry()
    mirror = _json_block(_scene_src(), "INLINE_SCENE_DATA")
    registered = set(entry.get("objects") or [])
    mirrored = set(mirror.get("objects") or [])
    assert mirrored == registered, (
        f"registry 与屏内镜像不一致：registry 多 {sorted(registered - mirrored)}，"
        f"镜像多 {sorted(mirrored - registered)}")


def test_index_html_loads_scene_between_kit_and_app():
    """index.html 必须把本屏插在 scene-kit 之后、app.js 之前（defer 顺序即执行顺序）。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    srcs = [s.split("?")[0] for s in re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)]
    kit = next(i for i, s in enumerate(srcs) if s.endswith("scenes/scene-kit.js"))
    app = next(i for i, s in enumerate(srcs) if s.endswith("app.js"))
    mine = [i for i, s in enumerate(srcs) if s.endswith("scenes/s01-intake.js")]
    assert mine, "index.html 未引入 scenes/s01-intake.js（菜单里会没有本屏）"
    assert kit < mine[0] < app, (
        f"加载顺序错误：scene-kit={kit} s01={mine[0]} app={app}")


# ============================================================
# 3. 口径字典：权威副本 ↔ 屏内镜像
# ============================================================

def test_frontend_data_file_is_a_pure_dictionary():
    """权威副本只装口径，不许出现业务数字（口径与数据分离）。"""
    src = _data_src()
    hits = [text for text in FORBIDDEN_LITERALS if text in src]
    assert not hits, f"{SCENE_DATA_JS.name} 出现业务数字/故事 id {hits}（口径字典应只有公式与映射）"
    assert 'window.SCENE_DATA["s01"]' in src or "window.SCENE_DATA['s01']" in src, (
        "数据文件必须挂到 window.SCENE_DATA['s01']（registry.json 的 scene_globals 约定）")
    assert "SCENE_DATA_OBJECT" in src, "数据文件未导出对象字面量（镜像比对锚点）"


@pytest.mark.parametrize("field", ["sceneId", "label", "title", "sub", "icon", "order",
                                   "objects", "statusLabels", "atp", "quote", "signoff",
                                   "customer", "timelineTitle"])
def test_authoritative_and_inline_copies_agree(field: str):
    """权威副本与屏内镜像逐字段一致（任一侧改了口径而忘另一侧即变红）。"""
    authoritative = _json_block(_data_src(), "SCENE_DATA_OBJECT")
    inline = _json_block(_scene_src(), "INLINE_SCENE_DATA")
    assert field in authoritative, f"权威副本缺字段 {field}"
    assert authoritative[field] == inline[field], (
        f"口径字段 {field} 两侧不一致：\n权威副本 {authoritative[field]!r}\n屏内镜像 {inline[field]!r}")


def test_dictionary_covers_the_three_acceptance_points():
    """口径字典必须覆盖三项验收：ATP 三来源 / 报价三分量 / 三槽位会签映射。"""
    data = _json_block(_data_src(), "SCENE_DATA_OBJECT")

    atp_sources = data["atp"]["sources"]
    assert [src["field"] for src in atp_sources] == ["atp_on_hand", "atp_wip", "atp_safety"], (
        f"ATP 三来源字段口径不符（spec-v1.md §5）：{atp_sources}")
    assert "安全库存" in data["atp"]["formula"] or "−" in data["atp"]["formula"], (
        "ATP 现算公式未声明")

    parts = data["quote"]["parts"]
    assert [part["field"] for part in parts] == ["material_cost", "labor_cost", "tool_amort"], (
        f"报价三分量字段口径不符：{parts}")
    for part in parts:
        assert part.get("formula"), f"{part['label']} 未声明公式（下钻要写清楚怎么算）"

    signoff = data["signoff"]
    assert signoff["slotOrder"] == ["biz", "rd", "pln"], f"三槽位口径不符：{signoff['slotOrder']}"
    assert {"biz", "rd", "pln"} <= set(signoff["slotLabels"]), "槽位缺中文标签"
    assert {"biz-demo": "biz", "rd-demo": "rd", "mfg-demo": "pln"} == {
        key: signoff["slotByRole"][key] for key in ("biz-demo", "rd-demo", "mfg-demo")}, (
        "岗位 → 槽位映射与五访客 id 不符（视角差异会失效）")
