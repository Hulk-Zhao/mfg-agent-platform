"""T08 S6 采购执行屏验收（纯 Python，禁网络 / 禁 node）。

守护四类接缝：

1. **登记契约**：S6 屏必须在 ``registry.json`` 的 ``scenes`` 里登记（module / data 两个
   文件都真实存在），且在 ``index.html`` 里插在 scene-kit 之后、app.js 之前——
   defer 按文档顺序执行，插错位置就拿不到 ``window.SceneKit`` / ``window.SceneRegistry``。
2. **配置镜像不漂移**：屏文件里的 ``INLINE_SCENE_DATA`` 与
   ``data/demo_storyline/frontend/s06-procurement.js`` 的 ``window.SCENE_DATA["s06"]``
   必须逐值相等。浏览器取不到 ``data/`` 目录（``/demo-ui`` 只挂载 ui），屏内只能内联
   一份兜底；这条断言就是那份兜底的锚，任一侧改动都会变红。
3. **缺口与升级档位可复算**：按登记在 frontend 数据文件里的口径，从
   ``data/demo_storyline/*.jsonl`` 重算缺料缺口与升级档位，必须与 T02 载荷
   （``DEMO-EV-0027`` / ``DEMO-EV-0029``）一致；缺口量 2000 片是「料期 vs 工排需料日」
   比对结果的机械复算值。
4. **预警到待办零人工步骤**：``DEMO-EV-0027``（缺料预警）与 ``DEMO-EV-0028``（催料任务
   自动生成）时间戳必须相等，间隔 0 分钟——这是 T08 验收项「预警到待办全自动无人工步骤」
   的机械证据。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
SCENES_DIR = UI_DIR / "scenes"
DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
FRONTEND_DIR = DATA_DIR / "frontend"

SCENE_ID = "s06"
SCENE_FILE = SCENES_DIR / "s06-procurement.js"
DATA_FILE = FRONTEND_DIR / "s06-procurement.js"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"

INLINE_PATTERN = r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});"
FRONTEND_PATTERN = r'window\.SCENE_DATA\["s06"\]\s*=\s*(\{.*?\n  \});'


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
    assert SCENE_FILE.exists(), f"S6 场景屏不存在：{SCENE_FILE}"
    return SCENE_FILE.read_text(encoding="utf-8")


# ============================================================
# 1. 登记契约
# ============================================================

def test_registry_entry_declares_scene_and_data_files():
    """registry.json 条目：module / data 两个文件必须真实存在，objects 必须已登记。"""
    entry = _scene_entry()
    assert entry.get("owner") == "T08", f"s06 的 owner 应为 T08，实际 {entry.get('owner')}"

    module = entry.get("module")
    assert module, "s06 缺少 module（屏文件相对路径）"
    assert (UI_DIR / module).exists(), f"s06.module 指向的文件不存在：{module}"

    data = entry.get("data")
    assert data, "s06 缺少 data（场景数据文件相对路径）"
    assert (FRONTEND_DIR / Path(data).name).exists(), f"s06.data 指向的文件不存在：{data}"

    objects = entry.get("objects") or []
    assert objects, "s06 必须声明 objects（本屏取数的剧情对象）"
    registered = set(_registry().get("objects") or {})
    unknown = sorted(set(objects) - registered)
    assert not unknown, f"s06.objects 引用了未登记对象：{unknown}"


def test_index_html_loads_scene_between_kit_and_app():
    """defer 顺序：scene-kit → s06 场景脚本 → app.js（顺序错则 window.SceneKit 缺失）。"""
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
# 2. 配置镜像不漂移
# ============================================================

def test_frontend_data_file_shape():
    """契约文件必须挂在 window.SCENE_DATA['s06'] 下，且含口径 / 需料日 / 升级三档三段落。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert cfg["scene"] == SCENE_ID
    assert cfg["shortage_rule"], "缺少缺料预警口径（机械判定不能只写在屏里）"
    assert cfg["plan_need"], "缺少工排需料日（料期 vs 工排计划比对的分母）"
    assert cfg["escalation_rule"], "缺少延期升级规则"
    assert cfg["short_ship_rule"], "缺少短装口径"
    assert cfg["assignees"], "缺少责任人字典（升级链可追到人）"
    assert cfg["threshold_sources"], "阈值必须标注出处（spec-v1.md §3）"

    levels = cfg["escalation_levels"]
    assert [lv["level"] for lv in levels] == [1, 2, 3], (
        "升级链必须是 L1/L2/L3 三档（采购员 / 采购主管 / 供应链经理）")
    assert [lv["threshold_days"] for lv in levels] == [0, 2, 5], (
        "升级阈值必须是 0 / 2 / 5 天（演示载荷 DEMO-EV-0029 由 L1 升到 L2）")
    for lv in levels:
        assert lv["owner_role"] in cfg["assignees"], (
            f"档位 L{lv['level']} 的岗位 {lv['owner_role']} 没有责任人映射（无法追到人）")
    assert "DEMO-MAT-2202" in cfg["plan_need"], "缺料命中料 DEMO-MAT-2202 必须有工排需料日"


def test_inline_scene_data_mirrors_frontend_file():
    """屏内 INLINE_SCENE_DATA 必须与契约文件逐值相等（浏览器读不到 data/ 目录）。"""
    inline = _extract(SCENE_FILE, INLINE_PATTERN)
    frontend = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert inline == frontend, (
        "S6 屏内联配置与 data/demo_storyline/frontend/s06-procurement.js 不一致，"
        f"差异键：{sorted(set(inline) ^ set(frontend)) or '（同键不同值）'}")


def test_scene_declares_same_objects_as_registry():
    """屏内 OBJECTS 常量必须与 registry.json 的 objects 清单一致（多取一个对象就会 404）。"""
    src = _scene_src()
    matched = re.search(r"const OBJECTS\s*=\s*(\[[^\]]*\]);", src)
    assert matched, "屏内缺少 OBJECTS 常量（取数对象清单）"
    declared = set(json.loads(matched.group(1)))
    entry_objects = set(_scene_entry().get("objects") or [])
    assert declared == entry_objects, (
        f"屏内 OBJECTS 与 registry.json 不一致：屏多 {sorted(declared - entry_objects)}，"
        f"屏少 {sorted(entry_objects - declared)}")


# ============================================================
# 3. 缺口与升级档位可复算（与 T02 载荷一致）
# ============================================================

def _po_by_id() -> dict[str, dict]:
    return {row["po_id"]: row for row in _jsonl("purchase_orders")}


def _s6_events(kind: str | None = None, etype: str | None = None) -> list[dict]:
    out = []
    for event in _jsonl("events"):
        if event.get("scene") != "S6":
            continue
        payload = event.get("payload") or {}
        if kind is not None and payload.get("kind") != kind:
            continue
        if etype is not None and event.get("type") != etype:
            continue
        out.append(event)
    return sorted(out, key=lambda e: e["ts"])


def _mechanical_gap(po: dict, plan_need: dict) -> dict:
    """按契约口径机械复算缺口：未交付 且 料期 > 工排需料日 → 缺口成立。"""
    need = plan_need.get(po["material_id"])
    delivered = bool(po.get("actual_date"))
    late_days = None
    if need and po.get("promise_date"):
        late_days = (datetime.fromisoformat(po["promise_date"])
                     - datetime.fromisoformat(need)).days
    gap = (not delivered) and late_days is not None and late_days > 0
    return {"need_date": need, "delivered": delivered, "late_days": late_days,
            "gap": gap, "gap_qty": int(po["qty"]) if gap else 0}


def test_shortage_recompute_matches_t02_payload():
    """缺料预警的缺口量 / 命中料必须与 T02 载荷一致（料期 vs 工排需料日比对结果）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    shortages = _s6_events(kind="shortage", etype="alert")
    assert len(shortages) == 1, f"S6 缺料预警应恰有 1 条，实际 {len(shortages)}"

    payload = shortages[0]["payload"]
    po = _po_by_id()[shortages[0]["object_ref"]]
    gap = _mechanical_gap(po, cfg["plan_need"])

    assert gap["gap"] is True, (
        f"{po['po_id']} 机械复算为无缺口，但载荷记载缺料预警——口径或数据漂移")
    assert gap["gap_qty"] == int(payload["gap_qty"]), (
        f"缺口量机械复算 {gap['gap_qty']} 与 T02 载荷 {payload['gap_qty']} 不一致")
    assert po["material_id"] == payload["material"], (
        f"载荷记载缺料料号 {payload['material']}，但 {po['po_id']} 是 {po['material_id']}")
    assert gap["late_days"] > 0, "缺口成立时料期必须晚于工排需料日"


def test_delivered_po_is_not_flagged_as_shortage():
    """已交付的 PO 一律闭环：即使曾晚于需料日，也不得标缺口（DEMO-PO-001 即此类）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    orders = _po_by_id()
    delivered_late = []
    for po in orders.values():
        gap = _mechanical_gap(po, cfg["plan_need"])
        if gap["delivered"] and gap["late_days"] is not None and gap["late_days"] > 0:
            delivered_late.append(po["po_id"])
            assert gap["gap"] is False, f"{po['po_id']} 已交付却被判缺口，破坏「已交付即闭环」"
    assert delivered_late, (
        "演示数据里应存在「已交付但曾晚于需料日」的 PO（用于证明已交付即闭环），实际没有")


def test_escalation_level_recompute_matches_t02_payload():
    """升级档位按预警持续天数机械判定，结果必须与 T02 载荷（升级至采购主管）一致。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    shortages = _s6_events(kind="shortage", etype="alert")
    escalations = _s6_events(kind="escalated", etype="alert")
    assert len(escalations) == 1, f"S6 延期升级应恰有 1 条，实际 {len(escalations)}"

    esc = escalations[0]
    alert = shortages[0]
    assert esc["object_ref"] == alert["object_ref"], "升级事件与缺料预警必须指向同一张 PO"

    aging = (datetime.fromisoformat(esc["ts"]) - datetime.fromisoformat(alert["ts"])).days
    reached = [lv for lv in cfg["escalation_levels"] if aging >= lv["threshold_days"]]
    assert reached, f"预警持续 {aging} 天，未触达任何升级档位"
    top = reached[-1]

    assert top["level"] == 2 and top["owner_role"] == "采购主管", (
        f"机械判定档位为 L{top['level']} {top['owner_role']}，但 T02 载荷 {esc['event_id']} "
        f"记载「{esc['payload']['note']}」——升级阈值与载荷漂移")
    assert cfg["assignees"][top["owner_role"]], "升级档位必须能追到人（责任人非空）"


# ============================================================
# 4. 预警 → 待办：零人工步骤
# ============================================================

def test_alert_to_todo_has_zero_manual_steps():
    """预警到待办必须同刻生成（间隔 0 分钟）——验收项「全自动无人工步骤」的机械证据。"""
    shortages = _s6_events(kind="shortage", etype="alert")
    todos = _s6_events(etype="order_status")
    assert len(todos) == 1, f"S6 催料待办应恰有 1 条，实际 {len(todos)}"

    todo = todos[0]
    alert = next((a for a in shortages if a["object_ref"] == todo["object_ref"]), None)
    assert alert, f"催料待办 {todo['event_id']} 找不到对应的缺料预警（链路断）"

    delta = (datetime.fromisoformat(todo["ts"]) - datetime.fromisoformat(alert["ts"])).total_seconds()
    assert delta == 0, (
        f"预警 {alert['ts']} → 待办 {todo['ts']} 间隔 {delta / 60:.0f} 分钟（非 0），"
        "链路里混进了人工环节，违反 T08 验收项")


def test_no_short_ship_fabrication():
    """数据集未建模实收量，短装只能按「无留痕」呈现——不得凭空产出短装数量。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    pos = _jsonl("purchase_orders")
    assert not any("received_qty" in po for po in pos), (
        "数据集已新增实收量字段：短装判定应改为按字段复算，改口径需同步本测试")
    assert "未建模" in cfg["short_ship_rule"], (
        "短装口径必须显式声明「未建模实收量」，避免界面编造短装数量")


# ============================================================
# 5. 场景屏纪律：组件复用 / 降级 / 只读
# ============================================================

def test_scene_uses_scene_kit_contract():
    """必须通过 SceneRegistry 登记，并复用 SceneKit 组件（不另造一套渲染）。"""
    src = _scene_src()
    assert "SceneRegistry.register(" in src, "S6 屏未通过 SceneRegistry.register() 登记"
    assert "GROUP_NAME" in src, "分组必须取 SceneRegistry.GROUP_NAME"
    for api in ("pageHead", "numberRow", "eventFlow", "openDrill", "bindDrills", "story("):
        assert api in src, f"S6 屏未使用组件契约 {api}"
    assert "演示数据" not in src.split("INLINE_SCENE_DATA")[0], (
        "「演示数据」徽标只能由 pageHead 统一产出，屏内不得自行拼接")


def test_scene_has_degrade_path_and_object_loader():
    """装载层未就绪必须给降级条（不编数字）；取数需走契约入口并保留端点兜底。"""
    src = _scene_src()
    assert "演示装载层未就绪" in src, "缺少装载层降级文案"
    assert "sk-degrade" in src, "降级必须复用 sk-degrade 结构"
    assert "K.story(name)" in src, "未走 T05 契约入口 SceneKit.story()"
    assert "/api/v2/demo/objects/" in src, "缺少兜底直连端点（/api/v2/demo/objects/{name}）"
    for obj in ("purchase_orders", "suppliers", "materials", "work_orders", "sales_orders", "events"):
        assert f'"{obj}"' in src, f"未取用对象 {obj}"


def test_scene_exposes_procurement_drills():
    """关键格必须登记口径下钻：概览四格 + 逐笔 PO + 自动化链路 + 升级档位 + 留痕表。"""
    src = _scene_src()
    for key in ("ov-po", "ov-confirmed", "ov-shortage", "ov-todo", "chain-auto", "ledger"):
        assert f'drills["{key}"]' in src, f"缺少口径下钻登记 {key}"
    assert 'drills["po-" + o.raw.po_id]' in src, "缺少逐笔 PO 缺口口径下钻"
    assert 'drills["esc-" + lv.level]' in src, "缺少升级档位「追人」下钻"
    assert 'data-sk-drill="po-' in src, "缺口判定格无点击入口（口径已登记但不可达）"


def test_scene_has_no_write_path():
    """本屏不得出现写操作：催料/升级一律只读回放，避免绕过治理的旁路写入。"""
    src = _scene_src()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{method}"' not in src, f"S6 屏不应出现 {method} 写请求"
    assert "治理窗口" in src, "只读回放必须说明写链路归属（治理窗口）"
