"""T10 S2 立项屏验收（纯 Python，禁网络 / 禁 node）。

守护四类接缝：

1. **登记契约**：S2 屏必须在 ``registry.json`` 的 ``scenes`` 里登记（module / data 两个文件
   真实在盘、objects 均已登记），且在 ``index.html`` 里插在 scene-kit 之后、app.js 之前——
   defer 按文档顺序执行，插错位置就拿不到 ``window.SceneKit``，漏插则菜单有、点进去空白。
2. **配置镜像不漂移**：屏文件里的 ``INLINE_SCENE_DATA`` 与
   ``data/demo_storyline/frontend/s02-npi-kickoff.js`` 的 ``window.SCENE_DATA["s02"]``
   必须逐值相等（浏览器取不到 data/ 目录：``/demo-ui`` 只挂载 ui，兜底只能内联）。
3. **口径可复算**：五张卡的口径都算得出来——用 data/demo_storyline/*.jsonl 实数据
   按契约里的阈值重算，验证 ``refs`` 指向的主线对象、会签事件、阶段门事件、在途 PO、
   EBOM 子件都真实存在（否则卡片会显示 0 或破折号，"数字说不清出处"）。
4. **张张有口径**：数据文件里登记的五张卡，屏文件必须逐一登记下钻口径（``drills["<key>"]``），
   不允许"有卡片没口径"。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
SCENES_DIR = UI_DIR / "scenes"
DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
FRONTEND_DIR = DATA_DIR / "frontend"

SCENE_ID = "s02"
SCENE_FILE = SCENES_DIR / "s02-npi-kickoff.js"
DATA_FILE = FRONTEND_DIR / "s02-npi-kickoff.js"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"

INLINE_PATTERN = r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});"
FRONTEND_PATTERN = r'window\.SCENE_DATA\["s02"\]\s*=\s*(\{.*?\n  \});'

# 五张立项材料卡（键即下钻口径键）
CARD_KEYS = ["order-input", "ebom", "dfm", "long-lead", "stage-gate-plan"]


# ============================================================
# 通用读取
# ============================================================

def _registry() -> dict:
    assert REGISTRY_JSON.exists(), f"缺少数据契约文件：{REGISTRY_JSON}"
    return json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))


def _scene_entry() -> dict:
    scenes = _registry().get("scenes") or []
    entry = next((item for item in scenes if item.get("id") == SCENE_ID), None)
    assert entry, f"registry.json 的 scenes 未登记 {SCENE_ID}"
    return entry


def _jsonl(name: str) -> list[dict]:
    path = DATA_DIR / f"{name}.jsonl"
    assert path.exists(), f"缺少剧情数据 {path}"
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _extract(path: Path, pattern: str) -> dict:
    assert path.exists(), f"缺少文件：{path}"
    text = path.read_text(encoding="utf-8")
    matched = re.search(pattern, text, re.S)
    assert matched, f"{path.name} 未匹配到场景配置字面量：{pattern}"
    return json.loads(matched.group(1))


def _scene_src() -> str:
    assert SCENE_FILE.exists(), f"S2 场景屏不存在：{SCENE_FILE}"
    return SCENE_FILE.read_text(encoding="utf-8")


def _loader_objects() -> tuple[str, ...]:
    """从 T04 loader.py 实读 OBJECTS（对象名的唯一权威清单）。"""
    loader = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "loader.py"
    assert loader.exists(), f"缺少装载层：{loader}"
    text = loader.read_text(encoding="utf-8")
    matched = re.search(r"OBJECTS\s*=\s*\(([^)]*)\)", text, re.S)
    assert matched, "loader.py 未匹配到 OBJECTS 元组"
    return tuple(re.findall(r'"([^"]+)"', matched.group(1)))


# ============================================================
# 1. 登记契约
# ============================================================

def test_registry_entry_declares_scene_and_data_files():
    """registry.json 条目：module / data 两文件真实在盘，objects 必须已登记。"""
    entry = _scene_entry()
    assert entry.get("owner") == "T10", f"s02 的 owner 应为 T10，实际 {entry.get('owner')}"

    module = entry.get("module")
    assert module, "s02 缺少 module（屏文件相对路径）"
    assert (UI_DIR / module).exists(), f"s02.module 指向的文件不存在：{module}"

    data = entry.get("data")
    assert data, "s02 缺少 data（场景数据文件相对路径）"
    assert (FRONTEND_DIR / Path(data).name).exists(), f"s02.data 指向的文件不存在：{data}"

    objects = entry.get("objects") or []
    assert objects, "s02 必须声明 objects（本屏取数的剧情对象）"
    registered = set(_registry().get("objects") or {})
    unknown = sorted(set(objects) - registered)
    assert not unknown, f"s02.objects 引用了未登记对象：{unknown}"
    not_in_loader = sorted(set(objects) - set(_loader_objects()))
    assert not not_in_loader, f"s02.objects 不在 T04 装载层 OBJECTS 里：{not_in_loader}"


def test_index_html_loads_scene_between_kit_and_app():
    """defer 顺序：scene-kit → s02 场景脚本 → app.js（顺序错则 window.SceneKit 缺失）。"""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    srcs = [src.split("?")[0] for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)]

    idx_kit = next(i for i, s in enumerate(srcs) if s.endswith("scenes/scene-kit.js"))
    idx_app = next(i for i, s in enumerate(srcs) if s.endswith("app.js"))
    found = [i for i, s in enumerate(srcs) if s.endswith("scenes/" + SCENE_FILE.name)]
    assert found, f"index.html 未引入 {SCENE_FILE.name}"
    assert idx_kit < found[0] < idx_app, (
        f"{SCENE_FILE.name} 必须插在 scene-kit（{idx_kit}）之后、app.js（{idx_app}）之前，"
        f"当前 {found[0]}")


def test_scene_registers_through_registry_api():
    """屏必须经 window.SceneRegistry.register() 自注册，且 roles 走统一业务口径。"""
    src = _scene_src()
    assert "window.SceneRegistry.register(" in src, "屏未调用 SceneRegistry.register"
    assert 'id: SCENE_ID' in src, "register 的 id 未用 SCENE_ID 常量（易与其它屏串号）"
    assert "roles: window.SceneRegistry.BUSINESS_ROLES" in src, (
        "roles 未使用 SceneRegistry.BUSINESS_ROLES：五访客的可见性靠它命中治理角色 key")
    assert "group: window.SceneRegistry.GROUP_NAME" in src, (
        "group 未使用 SceneRegistry.GROUP_NAME（契约组名：生态链场景）")


# ============================================================
# 2. 配置镜像不漂移
# ============================================================

def test_inline_scene_data_mirrors_frontend_file():
    """屏内 INLINE_SCENE_DATA 必须与契约文件逐值相等（浏览器读不到 data/ 目录）。"""
    inline = _extract(SCENE_FILE, INLINE_PATTERN)
    frontend = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert inline == frontend, (
        "S2 屏内联配置与 data/demo_storyline/frontend/s02-npi-kickoff.js 不一致，"
        f"差异键：{sorted(set(inline) ^ set(frontend)) or '（同键不同值）'}")


def test_scene_data_declares_seven_sections():
    """契约文件必备七段：sceneId / refs / rules / cards / caseView / signoff / sourceNote。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert cfg["sceneId"] == SCENE_ID
    for key in ("refs", "rules", "cards", "caseView", "signoff", "sourceNote"):
        assert cfg.get(key), f"场景配置缺少 {key}"

    assert 0 < float(cfg["rules"]["supplierOtdWarnLow"]) <= 1, "供应商 OTD 阈值口径非法"
    assert int(cfg["rules"]["caseListLimit"]) > 0, "案例展示上限必须为正"
    assert cfg["rules"]["substituteNameToken"], "替代料识别标记不能为空"

    card_keys = [card["key"] for card in cfg["cards"]]
    assert card_keys == CARD_KEYS, f"五张卡的键与顺序应为 {CARD_KEYS}，实际 {card_keys}"
    for card in cfg["cards"]:
        for field in ("label", "tone", "unit", "hint"):
            assert card.get(field), f"卡片 {card['key']} 缺少 {field}（展示元信息不完整）"


def test_story_data_goes_through_scene_kit_story():
    """公共故事数据必须经 SceneKit.story() 取，不许屏内自写端点直连（T05 契约细则第 2 条）。"""
    src = _scene_src()
    assert ("K.story(" in src) or ("SceneKit.story(" in src), (
        "屏未使用 SceneKit.story() 取故事数据")
    assert "fetch(" not in src, "屏内自写 fetch 直连装载层端点（应走 SceneKit.story()）"
    assert "encodeURIComponent" not in src, "屏内自拼端点 URL（应走 SceneKit.story()）"


def test_scene_registers_into_scene_data_global():
    """屏必须把内联配置登记进 window.SCENE_DATA['s02']（契约口径唯一读取入口）。"""
    src = _scene_src()
    assert "window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA" in src, (
        "屏未把内联配置登记进 window.SCENE_DATA['s02']")
    assert "function sceneData()" in src and "INLINE_SCENE_DATA" in src, (
        "屏未按契约先登记兜底再读取（缺位时抛错会整屏空白）")


# ============================================================
# 3. 口径可复算（数字都算得出来，卡片不会空）
# ============================================================

def test_refs_point_to_real_rows():
    """refs 指向的主线订单与会签事件必须真实存在，否则卡片显示破折号。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    orders = _jsonl("sales_orders")
    main = next((o for o in orders if o.get("order_id") == cfg["refs"]["orderId"]), None)
    assert main, f"refs.orderId 指向的订单不存在：{cfg['refs']['orderId']}"
    lines = main.get("lines") or []
    assert lines and float(lines[0].get("qty") or 0) > 0, "主线订单首个订单行数量非正，订单输入卡无值"

    scene = cfg["refs"]["eventScene"]
    hitl = [e for e in _jsonl("events")
            if e.get("scene") == scene and e.get("type") == "hitl"
            and (e.get("payload") or {}).get("slots")]
    assert hitl, f"scene={scene} 下没有带 slots 的 hitl 事件，立项会签卡没有留痕可回放"


def test_card_caliber_recomputable_from_storyline():
    """五张卡的被除数都可从 jsonl 现算：EBOM 子件 / 在途 PO / 阶段门 / 风险项 / 订单行。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    threshold = float(cfg["rules"]["supplierOtdWarnLow"])
    token = cfg["rules"]["substituteNameToken"]

    boms = _jsonl("boms")
    ebom = next((b for b in boms if b.get("kind") == "E"), None)
    assert ebom, "没有 kind=E 的 EBOM，EBOM/MBOM 卡无值"
    ebom_lines = ebom.get("lines") or []
    assert ebom_lines, "EBOM 子件行数为 0，EBOM/MBOM 卡无值"

    materials = {m["material_id"]: m for m in _jsonl("materials")}
    suppliers = {s["supplier_id"]: s for s in _jsonl("suppliers")}
    # 风险规则①：默认供应商历史 OTD 低于阈值；规则②：已存在同品类「（替代）」料件
    substitute_categories = {
        str(m.get("name", "")).split(token)[0].strip().split(" ")[0]
        for m in materials.values() if token in str(m.get("name", ""))
    }
    assert substitute_categories, f"没有料名带「{token}」的替代料，DFM 规则②恒不命中"

    risky = 0
    for line in ebom_lines:
        mat = materials.get(line.get("material_id")) or {}
        otd = suppliers.get(mat.get("supplier_id"), {}).get("otd_rate")
        category = str(mat.get("name", "")).split(" ")[0]
        if otd is not None and float(otd) < threshold:
            risky += 1
        elif category in substitute_categories:
            risky += 1
    assert risky > 0, "按契约阈值重算 DFM 风险项为 0——卡片会显示 0 且无明细可看"

    open_pos = [p for p in _jsonl("purchase_orders") if str(p.get("status")) != "closed"]
    assert open_pos, "没有在途采购单，长周期料卡无值"

    stages = [e for e in _jsonl("events")
              if e.get("scene") == "S3" and (e.get("payload") or {}).get("stage")]
    assert stages, "没有 scene=S3 的阶段门事件，阶段门计划卡无值"

    assert _jsonl("cases"), "案例库为空，相似历史案例检索无内容"


# ============================================================
# 4. 张张有口径
# ============================================================

def test_every_card_has_a_drill_caliber():
    """数据文件登记的五张卡，屏文件必须逐一登记下钻口径 drills["<key>"]。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    src = _scene_src()
    missing = [card["key"] for card in cfg["cards"]
               if f'drills["{card["key"]}"]' not in src]
    assert not missing, f"以下卡片没有登记口径下钻：{missing}"


def test_drills_declare_source_and_caliber():
    """每个口径都必须同时写明口径与来源（计划要求：数字说得清出处）。"""
    src = _scene_src()
    for key in CARD_KEYS:
        block = re.search(r'drills\["' + re.escape(key) + r'"\]\s*=\s*function[\s\S]*?\n    \};', src)
        assert block, f"口径 {key} 未匹配到实现块"
        body = block.group(0)
        assert '"口径"' in body, f"口径 {key} 未声明「口径」字段"
        assert '"来源"' in body, f"口径 {key} 未声明「来源」字段"
        assert "api/v2/demo/objects" in body, f"口径 {key} 未标注装载层端点"
