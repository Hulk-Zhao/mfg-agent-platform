"""T09 S4 批量监控与 ECN 屏验收（纯 Python，禁网络 / 禁 node）。

守护四类接缝：

1. **登记契约**：S4 屏必须在 ``registry.json`` 的 ``scenes`` 里登记（``module`` /
   ``data`` 两个文件都真实存在，``objects`` 全部已登记），且在 ``index.html`` 里插在
   scene-kit 之后、app.js 之前——defer 按文档顺序执行，插错位置就拿不到 ``window.SceneKit``。
2. **派生数据不漂移**：屏文件里的 ``INLINE_SCENE_DATA`` 与
   ``data/demo_storyline/frontend/s04-batch-ecn.js`` 的 ``window.SCENE_DATA["s04"]``
   必须逐值相等。浏览器取不到 ``data/`` 目录（``/demo-ui`` 只挂载 ui），屏内只能内联一份
   兜底（与 S3/T11 同法）；这条断言就是那份兜底的锚，任一侧改动都会变红。
3. **判异口径可复算**：SPC 判异点数必须能从 ``spc_series.jsonl`` 重算出来，且与 T02
   载荷（events 里两次 ``kind=spc`` 判异）双向一致；控制限口径（target ± tolerance）
   必须回指事件载荷。
4. **影响面四象限可回溯**：ECN 四象限的每个对象 id 必须在 ``work_orders`` / ``lots`` /
   ``sales_orders`` 里真实存在；象限数量必须与 ``DEMO-EV-0016`` payload 的
   ``impact_wos`` 清单 + 三个数量字段逐项对账（T09 验收项：影响面不造假）。
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

SCENE_ID = "s04"
SCENE_FILE = SCENES_DIR / "s04-batch-ecn.js"
DATA_FILE = FRONTEND_DIR / "s04-batch-ecn.js"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"

SCENE_OBJ = 'window.SCENE_DATA["s04"]'
INLINE_PATTERN = r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});"
FRONTEND_PATTERN = r'window\.SCENE_DATA\["s04"\]\s*=\s*(\{.*?\n  \});'

# S4 关键对象的登记字段（口径取自 registry.json → objects，禁止臆造）
OBJECT_KEYS = {
    "work_orders": "wo_id",
    "lots": "lot_id",
    "sales_orders": "order_id",
    "materials": "material_id",
    "equipment": "equipment_id",
    "events": "event_id",
    "spc_series": "equipment_id",
}

# 背景事件判据：背景工单 / 背景检验批 / 设备日点检（与屏内 isBackgroundEvent 同口径）
BACKGROUND_REF = re.compile(r"^DEMO-(BG|IB-BG)|^DEMO-WO-10\d")


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


def _frontend_cfg() -> dict:
    return _extract(DATA_FILE, FRONTEND_PATTERN)


def _scene_src() -> str:
    assert SCENE_FILE.exists(), f"S4 场景屏不存在：{SCENE_FILE}"
    return SCENE_FILE.read_text(encoding="utf-8")


def _events() -> list[dict]:
    return _jsonl("events")


def _s4_main_events() -> list[dict]:
    """S4 主线/支线事件（不含背景工单、背景检验批、设备点检）。"""
    return [
        e for e in _events()
        if e.get("scene") == "S4"
        and not BACKGROUND_REF.match(str(e.get("object_ref") or ""))
        and (e.get("payload") or {}).get("kind") != "daily_check"
    ]


def _event(event_id: str) -> dict:
    found = next((e for e in _events() if e.get("event_id") == event_id), None)
    assert found, f"events.jsonl 缺少事件 {event_id}"
    return found


# ============================================================
# 1. 登记契约
# ============================================================

def test_registry_entry_declares_scene_and_data_files():
    """registry.json 条目：module / data 两个文件必须真实存在，objects 必须已登记。"""
    entry = _scene_entry()
    assert entry.get("owner") == "T09", f"s04 的 owner 应为 T09，实际 {entry.get('owner')}"

    module = entry.get("module")
    assert module, "s04 缺少 module（屏文件相对路径）"
    assert (UI_DIR / module).exists(), f"s04.module 指向的文件不存在：{module}"

    data = entry.get("data")
    assert data, "s04 缺少 data（场景数据文件相对路径）"
    assert (FRONTEND_DIR / Path(data).name).exists(), f"s04.data 指向的文件不存在：{data}"

    objects = entry.get("objects") or []
    assert objects, "s04 必须声明 objects（本屏取数的剧情对象）"
    registered = set(_registry().get("objects") or {})
    unknown = sorted(set(objects) - registered)
    assert not unknown, f"s04.objects 引用了未登记对象：{unknown}"


def test_registry_roles_cover_gm_rd_qa_mfg():
    """roles 必须同时覆盖业务岗位 id 与治理角色 key（两套口径缺一访客就看不到本屏）。"""
    roles = set(_scene_entry().get("roles") or [])
    assert {"gm", "rd", "qa", "mfg"} <= roles, f"s04 业务岗位缺失：{sorted({'gm', 'rd', 'qa', 'mfg'} - roles)}"
    assert {"rd_engineering", "manufacturing"} <= roles, (
        f"s04 治理角色 key 缺失：{sorted({'rd_engineering', 'manufacturing'} - roles)}")


def test_index_html_loads_scene_between_kit_and_app():
    """defer 顺序：scene-kit → s04 场景脚本 → app.js（顺序错则 window.SceneKit 缺失）。"""
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
# 2. 派生数据不漂移
# ============================================================

def test_frontend_data_file_shape():
    """契约文件必须挂在 window.SCENE_DATA['s04'] 下，且含 SPC / 处置链 / ECN 三段。"""
    cfg = _frontend_cfg()
    assert cfg["scene"] == SCENE_ID
    assert cfg["disposition_chain"] == ["拉群", "取证", "方案", "会签", "闭环"], (
        "判异处置链必须是 spec-v1.md §3 的 拉群→取证→方案→会签→闭环")
    for key in ("target", "tolerance", "equipment_id", "param", "source"):
        assert key in cfg["spc_limits"], f"spc_limits 缺少 {key}"
    assert float(cfg["spc_limits"]["target"]) == 245.0
    assert float(cfg["spc_limits"]["tolerance"]) == 6.0
    assert cfg["alerts"], "缺少判异处置登记（alerts）"
    for alert in cfg["alerts"]:
        for key in ("alert_id", "equipment_id", "event_refs", "steps", "signoff_slots"):
            assert key in alert, f"alert {alert.get('alert_id')} 缺少 {key}"
        assert len(alert["steps"]) == len(cfg["disposition_chain"]), (
            f"alert {alert['alert_id']} 的 steps 必须与处置链逐格对齐")
    ecn = cfg["ecn"]
    for key in ("ecn_id", "impact", "impact_counts", "impact_sources", "quadrant_titles",
                "event_refs", "signoff_slots", "signoff_result", "progress"):
        assert key in ecn, f"ecn 缺少 {key}"
    assert set(ecn["impact"]) == set(ecn["quadrant_titles"]), "四象限键必须与标题键一一对应"
    assert set(ecn["impact"]) == set(ecn["impact_sources"]), "四象限必须逐格标注推算出处"
    assert float(ecn["progress"]["spec_point"]) == 0.6, "ECN 触发时点必须锚定 spec 的 60% 进度点"


def test_inline_scene_data_mirrors_frontend_file():
    """屏内 INLINE_SCENE_DATA 必须与契约文件逐值相等（浏览器读不到 data/ 目录）。"""
    inline = _extract(SCENE_FILE, INLINE_PATTERN)
    frontend = _frontend_cfg()
    assert inline == frontend, (
        "S4 屏内联配置与 data/demo_storyline/frontend/s04-batch-ecn.js 不一致，"
        f"差异键：{sorted(set(inline) ^ set(frontend)) or '（同键不同值）'}")


def test_scene_registers_into_scene_data():
    """屏必须把内联配置登记进 window.SCENE_DATA（契约口径：场景数据挂在 SCENE_DATA 下）。"""
    src = _scene_src()
    assert "window.SCENE_DATA" in src, "屏未使用 window.SCENE_DATA"
    assert "window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA" in src or \
        'window.SCENE_DATA["s04"]' in src, "屏未把内联配置登记进 window.SCENE_DATA['s04']"


# ============================================================
# 3. 判异口径可复算（与 T02 载荷双向一致）
# ============================================================

def test_spc_out_points_match_series_and_alerts():
    """判异点数 = spc_series 里 flag=='out' 的行数，且与 alerts / 事件载荷一致。"""
    cfg = _frontend_cfg()
    series = _jsonl("spc_series")
    out_rows = [r for r in series if str(r.get("flag")) == "out"]
    assert len(out_rows) == 2, f"spc_series 判异点数应为 2，实际 {len(out_rows)}"
    assert len(cfg["alerts"]) == 2, (
        f"派生数据登记的判异处置数（{len(cfg['alerts'])}）与序列判异点数（{len(out_rows)}）不一致")

    spc_events = [e for e in _s4_main_events()
                  if e.get("type") == "alert" and (e.get("payload") or {}).get("kind") == "spc"]
    assert len(spc_events) == 2, f"events 里 kind=spc 的判异事件应为 2 条，实际 {len(spc_events)}"

    # 控制限口径必须能回指事件载荷
    ev13 = _event("DEMO-EV-0013")["payload"]
    assert float(cfg["spc_limits"]["target"]) == float(ev13["target"]), "spc_limits.target 与 EV-0013 载荷不一致"
    assert float(cfg["spc_limits"]["tolerance"]) == float(ev13["tolerance"]), (
        "spc_limits.tolerance 与 EV-0013 载荷不一致")

    # 序列判异点的实测值必须与该序列自己的数值一致（不许把事件载荷数值写成序列判异值）
    out_values = sorted({float(r["value"]) for r in out_rows})
    alert_values = sorted({float(a["value"]) for a in cfg["alerts"] if a["equipment_id"] == "DEMO-EQ-002"})
    assert alert_values == [float(ev13["value"])], (
        f"DEMO-EQ-002 判异处置的实测值应取自 EV-0013 载荷 {ev13['value']}，实际 {alert_values}")
    assert all(v == out_values[0] for v in out_values), (
        f"spc_series 两个判异点数值不一致：{out_values}")


def test_alerts_reference_real_s4_events():
    """每次判异处置引用的 event_refs 必须真实存在，且属 S4 主线事件。"""
    cfg = _frontend_cfg()
    main_ids = {str(e["event_id"]) for e in _s4_main_events()}
    for alert in cfg["alerts"]:
        for ref in alert["event_refs"]:
            assert ref in main_ids, f"alert {alert['alert_id']} 引用了不存在的 S4 事件 {ref}"
        got = [e for e in _s4_main_events() if e["event_id"] in alert["event_refs"]]
        kinds = [str(e["type"]) for e in got]
        assert "alert" in kinds, f"alert {alert['alert_id']} 未引用告警事件"
        if alert["signoff_slots"]:
            assert "hitl" in kinds, f"alert {alert['alert_id']} 声明了会签槽位，却无关联的 hitl 事件"
            slots = set(_event([
                e["event_id"] for e in got if e["type"] == "hitl"
            ][0])["payload"]["slots"])
            assert slots == set(alert["signoff_slots"]), (
                f"alert {alert['alert_id']} 的会签槽位 {alert['signoff_slots']} 与 hitl 事件 {sorted(slots)} 不一致")


# ============================================================
# 4. 影响面四象限可回溯
# ============================================================

def test_ecn_impact_ids_exist_in_story_data():
    """四象限每个对象 id 必须真实存在，且物料/在途口径与派生数据的说明一致。"""
    cfg = _frontend_cfg()
    ecn = cfg["ecn"]
    impact = ecn["impact"]

    wos = {str(r["wo_id"]): r for r in _jsonl("work_orders")}
    for wo_id in impact["wos"]:
        assert wo_id in wos, f"影响面在制工单 {wo_id} 不在 work_orders.jsonl"
        assert wos[wo_id]["order_id"] == ecn["order_id"], (
            f"{wo_id} 不属主线订单 {ecn['order_id']}")
        assert wos[wo_id]["status"] in {"in_progress", "released"}, (
            f"{wo_id} 状态 {wos[wo_id]['status']} 不属「在制/已下达」口径")

    lots = {str(r["lot_id"]): r for r in _jsonl("lots")}
    for lot_id in impact["stock_lots"]:
        assert lot_id in lots, f"影响面库存批 {lot_id} 不在 lots.jsonl"
        assert lots[lot_id]["material_id"] == ecn["old_material"], (
            f"库存批 {lot_id} 未持旧料 {ecn['old_material']}")
    for lot_id in impact["transit_lots"]:
        assert lot_id in lots, f"影响面在途批 {lot_id} 不在 lots.jsonl"
        assert str(lots[lot_id]["location"]).startswith("在途"), f"在途批 {lot_id} 的 location 口径不符"

    orders = {str(r["order_id"]): r for r in _jsonl("sales_orders")}
    for line in impact["open_lines"]:
        order_id = str(line["order_id"])
        assert order_id in orders, f"影响面未发订单行指向的订单 {order_id} 不在 sales_orders.jsonl"
        lines = orders[order_id]["lines"]
        assert any(str(l["material_id"]) == str(line["material_id"]) for l in lines), (
            f"{order_id} 的订单行里没有物料 {line['material_id']}")


def test_ecn_impact_counts_match_payload():
    """象限数量必须与 DEMO-EV-0016 payload 逐项对账（payload 只给 wos 清单 + 三个数量）。"""
    cfg = _frontend_cfg()
    ecn = cfg["ecn"]
    payload = _event("DEMO-EV-0016")["payload"]

    assert sorted(ecn["impact"]["wos"]) == sorted(payload["impact_wos"]), (
        "在制工单清单与 EV-0016 payload.impact_wos 不一致")
    for key, payload_key in (("stock_lots", "impact_stock_lots"),
                             ("transit_lots", "impact_transit_lots"),
                             ("open_lines", "impact_open_lines")):
        assert len(ecn["impact"][key]) == int(payload[payload_key]), (
            f"{key} 清单行数（{len(ecn['impact'][key])}）与 EV-0016 payload.{payload_key}"
            f"（{payload[payload_key]}）不一致")
    for key in ("wos", "stock_lots", "transit_lots", "open_lines"):
        assert int(ecn["impact_counts"][key]) == len(ecn["impact"][key]), (
            f"impact_counts.{key} 与 impact.{key} 行数不一致")

    # ECN 会签 / 生效必须回指 hitl + ecn 事件
    signoff = _event("DEMO-EV-0017")
    assert set(signoff["payload"]["slots"]) == set(ecn["signoff_slots"]), "ECN 会签槽位与 EV-0017 不一致"
    assert str(signoff["payload"]["result"]) == str(ecn["signoff_result"]), "ECN 会签结论与 EV-0017 不一致"
    assert str(_event("DEMO-EV-0018")["ts"]) == str(ecn["effective_ts"]), "ECN 生效时间与 EV-0018 不一致"


def test_ecn_quadrant_events_are_real():
    """ECN 引用的 event_refs 必须存在且 object_ref 指向本 ECN。"""
    cfg = _frontend_cfg()
    ecn = cfg["ecn"]
    for ref in ecn["event_refs"]:
        event = _event(ref)
        assert str(event["object_ref"]) == str(ecn["ecn_id"]), (
            f"{ref} 的 object_ref ({event['object_ref']}) 不是 {ecn['ecn_id']}")


# ============================================================
# 5. 场景屏纪律：组件复用 / 降级 / 只读 / 下钻
# ============================================================

def test_scene_uses_scene_kit_contract():
    """必须通过 SceneRegistry 登记，并复用 SceneKit 组件（不另造一套渲染）。"""
    src = _scene_src()
    assert "SceneRegistry.register(" in src, "S4 屏未通过 SceneRegistry.register() 登记"
    assert "GROUP_NAME" in src, "分组必须取 SceneRegistry.GROUP_NAME"
    for api in ("pageHead", "numberRow", "eventFlow", "signoffCard", "openDrill", "bindDrills", "story("):
        assert api in src, f"S4 屏未使用组件契约 {api}"
    assert "演示数据" not in src.split("INLINE_SCENE_DATA")[0], (
        "「演示数据」徽标只能由 pageHead 统一产出，屏内不得自行拼接")


def test_scene_has_degrade_path_and_object_loader():
    """装载层未就绪必须给降级条（不编数字）；取数需兼容契约端点与当前实现端点。"""
    src = _scene_src()
    assert "演示装载层未就绪" in src, "缺少装载层降级文案"
    assert "sk-degrade" in src, "降级必须复用 sk-degrade 结构"
    assert "K.story(name)" in src, "未走 T05 契约入口 SceneKit.story()"
    assert "/api/v2/demo/objects/" in src, "缺少端点口径标注（/api/v2/demo/objects/{name}）"
    for obj in ("spc_series", "events", "work_orders", "lots", "boms", "sales_orders",
                "materials", "equipment"):
        assert f'"{obj}"' in src, f"未取用对象 {obj}"


def test_scene_exposes_drills_for_every_number_and_quadrant():
    """每个 KPI、SPC 图、判异会签、四象限行、ECN 会签都必须登记口径下钻且有点击入口。"""
    src = _scene_src()
    registered = [
        'drills["kpi-spc"]', 'drills["kpi-out"]', 'drills["kpi-wip"]', 'drills["kpi-ecn"]',
        'drills["spc-chart"]', 'drills[drillKey]', 'drills[r.drill]',
        'drills["ecn-signoff"]', 'drills["ecn-progress"]',
    ]
    for item in registered:
        assert item in src, f"缺少口径下钻登记：{item}"
    assert '"alert-signoff-" + a.alert_id' in src, "判异会签下钻键未按工单号生成"
    assert 'drill: "ecn-wos-" + id' in src and 'drill: "ecn-lot-" + id' in src and \
        'drill: "ecn-line-" + ' in src, "四象限行未登记下钻键"
    assert 'data-sk-drill="' in src, "登记了口径但无点击入口（data-sk-drill 缺失）"
    assert src.count('data-sk-drill=') >= 5, "点击入口过少：数字卡/口径按钮/四象限行都要可点"


def test_scene_has_no_write_path():
    """本屏不得出现写操作：会签一律置只读，避免绕过治理的旁路写入。"""
    src = _scene_src()
    assert "readonly: true" in src, "会签卡必须显式 readonly: true"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{method}"' not in src, f"S4 屏不应出现 {method} 写请求"
    assert "会签写操作" in src, "只读会签必须说明原因（真实写链路在治理窗口）"
