"""T06 S5 出货与质量追溯屏验收（纯 Python，禁网络 / 禁 node）。

守护五类接缝：

1. **登记契约**：S5 屏必须在 ``registry.json`` 的 ``scenes`` 里登记（module / data
   两个文件都真实存在、objects 都已登记），且在 ``index.html`` 里插在 scene-kit 之后、
   app.js 之前——defer 按文档顺序执行，插错位置就拿不到 ``window.SceneKit``。
2. **配置镜像不漂移**：屏内 ``INLINE_SCENE_DATA`` 与
   ``data/demo_storyline/frontend/s05-trace.js`` 的 ``window.SCENE_DATA["s05"]``
   必须逐值相等（两份字面量都是纯 JSON，直接 json.loads 深比对）。
3. **图谱几何可验**：节点矩形必须全部落在画布内、两两不重叠；连线端点必须引用
   真实存在的节点 key——「连线指向空气 / 节点压成一团」是 SVG 屏最常见的静默缺陷。
4. **取数纪律**：公共数字只许经 ``SceneKit.story(objectName)`` 现取，屏内（镜像块以外）
   不得出现剧情数据里的业务值（如批次数量 1976 / 同批合计 3952 / 客户名 华骏电子）；
   降级路径必须诚实（不编数字）。
5. **链路自洽（与 T01/T02 载荷对账）**：定位结论的每一段必须与剧情数据互相印证——
   批次.工单 == trace.wo、工序号 == trace.op、工序.设备 == trace.equipment、
   客诉载荷批次 == trace.lot、同批库存批次数 == lots 同库位批数；定位时限话术必须能在
   定位事件载荷里找到出处（否则就是屏内臆造的 SLA 口径）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))  # 与 tests/ 其它用例同口径

from mfg_agents.v2.demo import loader  # noqa: E402

UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
SCENES_DIR = UI_DIR / "scenes"
DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
FRONTEND_DIR = DATA_DIR / "frontend"

SCENE_ID = "s05"
SCENE_FILE = SCENES_DIR / "s05-trace.js"
DATA_FILE = FRONTEND_DIR / "s05-trace.js"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"

INLINE_PATTERN = r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});"
FRONTEND_PATTERN = r'window\.SCENE_DATA\["s05"\]\s*=\s*(\{.*?\n  \});'

#: 本屏取数的故事对象（与 registry.json 条目 objects 一致）
STORY_OBJECTS = ["events", "lots", "work_orders", "equipment", "inspections",
                 "sales_orders", "customers"]

#: 剧情数据里的业务值——屏内（镜像块以外）出现即视为抄数
BUSINESS_VALUES = ["1976", "3952", "华骏电子"]


# ============================================================
# 通用读取
# ============================================================

def _registry() -> dict:
    assert REGISTRY_JSON.exists(), f"缺少数据契约文件：{REGISTRY_JSON}"
    return json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))


def _scene_entry() -> dict:
    entry = next((item for item in _registry().get("scenes") or []
                  if item.get("id") == SCENE_ID), None)
    assert entry, f"registry.json 的 scenes 未登记 {SCENE_ID}"
    return entry


def _extract(path: Path, pattern: str) -> dict:
    assert path.exists(), f"缺少文件：{path}"
    text = path.read_text(encoding="utf-8")
    matched = re.search(pattern, text, re.S)
    assert matched, f"{path.name} 未匹配到字面量：{pattern}"
    return json.loads(matched.group(1))


def _scene_src() -> str:
    assert SCENE_FILE.exists(), f"S5 场景屏不存在：{SCENE_FILE}"
    return SCENE_FILE.read_text(encoding="utf-8")


def _scene_src_without_mirror() -> str:
    """屏脚本去掉镜像块后的部分（用于验证没在别处抄数）。"""
    src = _scene_src()
    matched = re.search(INLINE_PATTERN, src, re.S)
    assert matched, "屏内未找到 INLINE_SCENE_DATA 镜像块"
    return src.replace(matched.group(0), "")


def _rows(name: str) -> list[dict]:
    return list(loader.get_object(name))


def _s5_events() -> list[dict]:
    return [event for event in _rows("events") if event.get("scene") == "S5"]


def _by(list_: list[dict], key: str, value) -> dict:
    hit = next((row for row in list_ if str(row.get(key)) == str(value)), None)
    assert hit, f"{key}={value} 在剧情数据中不存在（剧情已改名？本屏引用即失效）"
    return hit


def _complaint_payload() -> dict:
    return _by(_s5_events(), "event_id", "DEMO-EV-0024").get("payload") or {}


def _located_payload() -> dict:
    return _by(_s5_events(), "event_id", "DEMO-EV-0025").get("payload") or {}


def _signoff_event() -> dict:
    return _by(_s5_events(), "event_id", "DEMO-EV-0026")


# ============================================================
# 1. 登记契约
# ============================================================

def test_registry_entry_declares_scene_and_data_files():
    """registry.json 条目：module / data 真实存在，objects 覆盖本屏七件且已登记。"""
    entry = _scene_entry()
    assert entry.get("owner") == "T06", f"s05 的 owner 应为 T06，实际 {entry.get('owner')}"

    module = entry.get("module") or entry.get("file")
    assert (UI_DIR / module).exists(), f"s05.module 指向的文件不存在：{module}"
    data = entry.get("data") or entry.get("data_file")
    assert (FRONTEND_DIR / Path(data).name).exists(), f"s05.data 指向的文件不存在：{data}"

    objects = entry.get("objects") or []
    assert sorted(objects) == sorted(STORY_OBJECTS), (
        f"s05.objects 应为本屏七件取数对象，实际 {objects}")
    registered = set(_registry().get("objects") or {})
    unknown = sorted(set(objects) - registered)
    assert not unknown, f"s05.objects 引用了未登记对象：{unknown}"

    roles = entry.get("roles") or []
    assert {"gm", "sales", "qa", "manufacturing"} <= set(roles), (
        f"s05.roles 必须覆盖四类访客可见性，实际 {roles}")


def test_index_html_loads_scene_between_kit_and_app():
    """defer 顺序：scene-kit → s05 场景脚本 → app.js。"""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    srcs = [src.split("?")[0] for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)]
    idx_kit = next(i for i, s in enumerate(srcs) if s.endswith("scenes/scene-kit.js"))
    idx_app = next(i for i, s in enumerate(srcs) if s.endswith("app.js"))
    found = [i for i, s in enumerate(srcs) if s.endswith("scenes/" + SCENE_FILE.name)]
    assert found, f"index.html 未引入 {SCENE_FILE.name}"
    assert idx_kit < found[0] < idx_app, (
        f"{SCENE_FILE.name} 必须插在 scene-kit（{idx_kit}）之后、app.js（{idx_app}）之前，"
        f"当前 {found[0]}")


# ============================================================
# 2. 数据文件形态 + 镜像不漂移
# ============================================================

def test_frontend_data_file_shape():
    """契约文件必须含画布 / 节点 / 连线 / 追溯步骤 / 定位时限 / 点击路径 / 8D 引用。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert cfg["scene_id"] == SCENE_ID
    assert cfg["spec_ref"].startswith("data/demo_storyline/spec-v1.md"), "缺少规范出处"
    assert "public" not in cfg.get("value_owner", "") and cfg["value_owner"], "value_owner 必须说明数字归属"
    assert cfg["canvas"]["w"] > 0 and cfg["canvas"]["h"] > 0, "画布尺寸非法"

    keys = [node["key"] for node in cfg["nodes"]]
    assert len(keys) == 7, f"追溯图谱应为七节点（客诉/批次/工单/工序/设备/当班/同批库存），实际 {keys}"
    assert len(set(keys)) == len(keys), f"节点 key 有重复：{keys}"
    for key in ("complaint", "lot", "wo", "op", "equipment", "shift", "stock"):
        assert key in keys, f"图谱缺少节点 {key}"

    for node in cfg["nodes"]:
        assert node["tone"] in {"red", "blue", "green", "purple", "gray", "amber"}, (
            f"节点 {node['key']} 的 tone={node['tone']} 不在色调词表内")
        assert node["value_from"], f"节点 {node['key']} 未标注取数来源"
        assert node["record"], f"节点 {node['key']} 未标注关联原始记录"

    steps = cfg["trace_steps"]
    assert [step["no"] for step in steps] == list(range(1, len(steps) + 1)), (
        "追溯步骤编号必须连续")
    assert {step["key"] for step in steps} <= set(keys), "追溯步骤引用了不存在的节点"

    assert cfg["locate_sla"]["minutes"] == 15, "定位时限口径为演示口径 15 分钟"
    assert "15" in cfg["locate_sla"]["source"] or "支线 B" in cfg["locate_sla"]["source"], (
        "定位时限必须标注出处")

    refs = cfg["eight_d"]["refs"]
    for field in ("complaint_event", "located_event", "signoff_event", "complaint_id", "report_id"):
        assert field in refs, f"8D 引用缺少 {field}"
    assert len(cfg["slot_labels"]) >= 2, "会签槽位词典不能为空（槽位 key 取自事件载荷）"
    assert cfg["value_lines"], "缺少本屏价值话术"


def test_inline_scene_data_mirrors_frontend_file():
    """屏内 INLINE_SCENE_DATA 必须与契约文件逐值相等。"""
    inline = _extract(SCENE_FILE, INLINE_PATTERN)
    frontend = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert inline == frontend, (
        "S5 屏内联镜像与 data/demo_storyline/frontend/s05-trace.js 不一致，"
        f"差异键：{sorted(set(inline) ^ set(frontend)) or '（同键不同值）'}")


def test_scene_registers_into_scene_data():
    """屏必须把镜像登记进 window.SCENE_DATA['s05']（未挂载时浏览器才拿得到）。"""
    src = _scene_src()
    assert "window.SCENE_DATA" in src, "屏未使用 window.SCENE_DATA"
    assert "window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA" in src, (
        "屏未把内联镜像登记进 window.SCENE_DATA[SCENE_ID]")


# ============================================================
# 3. 图谱几何 / 连线 / 点击路径
# ============================================================

def test_graph_geometry_in_canvas_and_non_overlapping():
    """节点矩形必须全部落在画布内且两两不重叠（SVG 屏最常见的静默缺陷）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    canvas = cfg["canvas"]
    nodes = cfg["nodes"]

    for node in nodes:
        assert 0 <= node["x"] and node["x"] + node["w"] <= canvas["w"], (
            f"节点 {node['key']} 横向越界：x={node['x']} w={node['w']} canvas.w={canvas['w']}")
        assert 0 <= node["y"] and node["y"] + node["h"] <= canvas["h"], (
            f"节点 {node['key']} 纵向越界：y={node['y']} h={node['h']} canvas.h={canvas['h']}")

    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            overlap = not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                           or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
            assert not overlap, f"节点 {a['key']} 与 {b['key']} 矩形重叠：{a} / {b}"


def test_graph_edges_reference_existing_nodes():
    """每条连线的 from/to 都必须是已登记节点，且两端不同；纵向连线两端必须同列。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    keys = {node["key"] for node in cfg["nodes"]}
    by_key = {node["key"]: node for node in cfg["nodes"]}
    assert cfg["edges"], "缺少图谱连线"

    for edge in cfg["edges"]:
        assert edge["from"] in keys, f"连线 from={edge['from']} 不是已登记节点"
        assert edge["to"] in keys, f"连线 to={edge['to']} 不是已登记节点"
        assert edge["from"] != edge["to"], "连线两端不能是同一节点"
        a, b = by_key[edge["from"]], by_key[edge["to"]]
        same_column = (a["x"] + a["w"] / 2) == (b["x"] + b["w"] / 2)
        same_row = (a["y"] + a["h"] / 2) == (b["y"] + b["h"] / 2)
        assert same_column or same_row, (
            f"连线 {edge['from']}→{edge['to']} 既不同列也不同行，"
            "渲染端只画横/竖两种正交线，斜线会被画成折线断层")


def test_click_path_within_five_clicks():
    """验收口径：从客诉单到定位批次 ≤ 5 次点击，且路径步骤数不超过上限。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    path = cfg["click_path"]
    assert path["limit"] == 5, f"点击上限应为 5，实际 {path['limit']}"
    assert 0 < len(path["steps"]) <= path["limit"], (
        f"点击路径步数 {len(path['steps'])} 必须是 1..{path['limit']}")
    for i, step in enumerate(path["steps"]):
        assert step["no"] == i + 1, "点击路径编号必须连续"
        assert step["action"] and step["lands"], "每步都要写清动作与落点"


# ============================================================
# 4. 取数纪律 / 组件复用 / 降级 / 只读
# ============================================================

def test_scene_reads_all_objects_via_story_contract():
    """七个对象都必须经 SceneKit.story() 现取；对象名要与 registry 条目一致。"""
    src = _scene_src()
    assert "K.story(name)" in src, "未走 T05 契约入口 SceneKit.story()"
    for obj in STORY_OBJECTS:
        assert f'"{obj}"' in src, f"未取用对象 {obj}"
    entry = _scene_entry()
    assert sorted(entry.get("objects") or []) == sorted(STORY_OBJECTS), (
        "registry 条目的 objects 与屏内取数清单不一致")


def test_scene_does_not_inline_business_values():
    """公共数字必须现取：镜像块以外不得出现剧情数据里的业务值。"""
    rest = _scene_src_without_mirror()
    hits = [value for value in BUSINESS_VALUES if value in rest]
    assert not hits, (
        f"屏脚本（镜像块以外）出现剧情数据业务值 {hits}——公共数字必须经 SceneKit.story 现取，"
        "不得抄数")
    assert not re.search(r'"DEMO-[A-Z0-9-]+"', rest), (
        "屏脚本（镜像块以外）写死了 DEMO-* 对象 id——应改为运行时从事件载荷读取")

    # 契约文件本身也只放派生数据：业务值与对象 id 同样不得出现
    data_text = DATA_FILE.read_text(encoding="utf-8")
    body = re.search(FRONTEND_PATTERN, data_text, re.S).group(1)
    data_hits = [value for value in BUSINESS_VALUES if value in body]
    assert not data_hits, f"场景数据文件出现业务值 {data_hits}（应只放布局与口径）"


def test_scene_uses_scene_kit_contract():
    """必须通过 SceneRegistry 登记并复用 SceneKit 组件（不另造一套渲染）。"""
    src = _scene_src()
    assert "SceneRegistry.register(" in src, "S5 屏未通过 SceneRegistry.register() 登记"
    assert "GROUP_NAME" in src, "分组必须取 SceneRegistry.GROUP_NAME"
    for api in ("pageHead", "numberRow", "eventFlow", "signoffCard", "openDrill",
                "bindDrills", "story(", "isReadonly", "icon", "badge", "esc",
                "fmtDate", "fmtNum", "spinner"):
        assert api in src, f"S5 屏未使用组件契约 {api}"
    assert "演示数据" not in src.split("INLINE_SCENE_DATA")[0], (
        "「演示数据」徽标只能由 pageHead 统一产出，屏内不得自行拼接")


def test_scene_has_degrade_path_and_no_write_path():
    """降级必须诚实（不编数字）；本屏不得出现写请求。"""
    src = _scene_src()
    assert "演示装载层未就绪" in src, "缺少装载层降级文案"
    assert "sk-degrade" in src, "降级必须复用 sk-degrade 结构"
    assert "不显示编造数字" in src, "降级文案必须明确「不显示编造数字」"
    assert "静默降级" in src, "次要对象缺失必须走静默降级（不影响整屏）"

    assert "K.isReadonly()" in src, "只读访客判定必须走 SceneKit.isReadonly()"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{method}"' not in src, f"S5 屏不应出现 {method} 写请求"
    assert "定位为纯视图动作" in src or "定位为视图动作" in src, (
        "定位动作必须在文案上说明为纯视图动作（不写库）")


def test_every_graph_node_has_drill_entry():
    """图谱每个节点都要登记口径下钻（验收：所有数字可点开处理链路）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    src = _scene_src()
    for node in cfg["nodes"]:
        assert f'registerDrill("{node["key"]}"' in src, (
            f"节点 {node['key']} 未登记口径下钻")
    for key in ("qty", "sla", "oqc", "ship", "invoice", "order", "customer", "signoff",
                "trace-step"):
        assert f'registerDrill("{key}"' in src, f"指标 {key} 未登记口径下钻"
    assert 'data-sk-drill' in src, "登记了下钻但未渲染点击入口"


# ============================================================
# 5. 链路自洽（与剧情数据对账）
# ============================================================

def test_referenced_object_ids_exist_in_storyline():
    """镜像块引用的对象 id 必须在剧情数据里真实存在（禁止臆造 id）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    refs = cfg["eight_d"]["refs"]

    raw = "\n".join((DATA_DIR / f"{name}.jsonl").read_text(encoding="utf-8")
                    for name in STORY_OBJECTS)
    for field, object_id in refs.items():
        assert object_id in raw, f"引用 {field}={object_id} 在剧情数据中不存在"

    event_ids = {event["event_id"] for event in _rows("events")}
    for field in ("complaint_event", "located_event", "signoff_event"):
        assert refs[field] in event_ids, f"引用的事件 {refs[field]} 不在 events 对象里"


def test_trace_chain_is_consistent_with_storyline():
    """定位结论五段必须与剧情数据互相印证（任一段改口径即红）。"""
    trace = _located_payload()["trace"]
    complaint = _complaint_payload()
    lot = _by(_rows("lots"), "lot_id", trace["lot"])
    wo = _by(_rows("work_orders"), "wo_id", trace["wo"])
    op = _by(wo["ops"], "op_no", trace["op"])
    equipment = _by(_rows("equipment"), "equipment_id", trace["equipment"])

    assert lot["wo_id"] == trace["wo"], "批次.工单与 trace.wo 不一致"
    assert str(lot["op_no"]) == str(trace["op"]), "批次.工序号与 trace.op 不一致"
    assert str(op["equipment_id"]) == str(trace["equipment"]), "工序.设备与 trace.equipment 不一致"
    assert complaint["lot_id"] == trace["lot"], "客诉载荷批次与 trace.lot 不一致"
    assert equipment["equipment_id"] == trace["equipment"], "设备对象缺该设备"

    # 同批库存：定位载荷自陈的批次数必须等于 lots 里同库位批数（两口径互相印证）
    warehouse = str(lot["location"]).split("（")[0]
    same_warehouse = [row for row in _rows("lots")
                      if str(row["location"]).split("（")[0] == warehouse]
    assert len(same_warehouse) == int(trace["stock_lots"]), (
        f"同批库存批次数：载荷说 {trace['stock_lots']}，lots 同库位实际 {len(same_warehouse)} 批")
    assert sum(int(row["qty"]) for row in same_warehouse) > 0, "同库位批次数为零，影响面无从展示"


def test_sla_and_signoff_wording_sourced_from_payload():
    """「15 分钟」定位口径与会签结论必须能在事件载荷里找到出处（不得屏内臆造）。"""
    located = _located_payload()
    assert "15 分钟" in str(located.get("note") or ""), (
        "定位时限话术在事件载荷里找不到出处——SLA 口径必须先落到剧情数据")

    signoff = _signoff_event()
    payload = signoff.get("payload") or {}
    assert payload.get("slots"), "会签事件缺少 slots（槽位词典无从渲染）"
    assert payload.get("result"), "会签事件缺少 result（闭环结论无从渲染）"
    assert "闭环" in str(payload["result"]), (
        f"会签结论应为闭环类文案（本屏据此判定槽位状态），实际 {payload['result']}")
