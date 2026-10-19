"""T11 S3 阶段门会签屏验收（纯 Python，禁网络 / 禁 node）。

守护三类接缝：

1. **登记契约**：S3 屏必须在 ``registry.json`` 的 ``scenes`` 里登记（module / data 两个
   文件都真实存在），且在 ``index.html`` 里插在 scene-kit 之后、app.js 之前——
   defer 按文档顺序执行，插错位置就拿不到 ``window.SceneKit``。
2. **配置镜像不漂移**：屏文件里的 ``INLINE_SCENE_DATA`` 与
   ``data/demo_storyline/frontend/s03-stage-gate.js`` 的 ``window.SCENE_DATA["s03"]``
   必须逐值相等。浏览器取不到 ``data/`` 目录（``/demo-ui`` 只挂载 ui），屏内只能内联
   一份兜底；这条断言就是那份兜底的锚，任一侧改动都会变红。
3. **门禁机械判定可复算**：用登记在 frontend 数据文件里的阈值，从
   ``data/demo_storyline/*.jsonl`` 重算 EVT/DVT/PVT 四维记分卡，机械判定结论必须与
   T02 载荷里的 ``verdict`` 一致（T11 验收项：四个数与 T03 重算值一致）。
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

SCENE_ID = "s03"
SCENE_FILE = SCENES_DIR / "s03-stage-gate.js"
DATA_FILE = FRONTEND_DIR / "s03-stage-gate.js"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"

SCENE_OBJ = 'window.SCENE_DATA["s03"]'
INLINE_PATTERN = r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});"
FRONTEND_PATTERN = r'window\.SCENE_DATA\["s03"\]\s*=\s*(\{.*?\n  \});'


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
    assert SCENE_FILE.exists(), f"S3 场景屏不存在：{SCENE_FILE}"
    return SCENE_FILE.read_text(encoding="utf-8")


# ============================================================
# 1. 登记契约
# ============================================================

def test_registry_entry_declares_scene_and_data_files():
    """registry.json 条目：module / data 两个文件必须真实存在，objects 必须已登记。"""
    entry = _scene_entry()
    assert entry.get("owner") == "T11", f"s03 的 owner 应为 T11，实际 {entry.get('owner')}"

    module = entry.get("module")
    assert module, "s03 缺少 module（屏文件相对路径）"
    assert (UI_DIR / module).exists(), f"s03.module 指向的文件不存在：{module}"

    data = entry.get("data")
    assert data, "s03 缺少 data（场景数据文件相对路径）"
    assert (FRONTEND_DIR / Path(data).name).exists(), f"s03.data 指向的文件不存在：{data}"

    objects = entry.get("objects") or []
    assert objects, "s03 必须声明 objects（本屏取数的剧情对象）"
    registered = set(_registry().get("objects") or {})
    unknown = sorted(set(objects) - registered)
    assert not unknown, f"s03.objects 引用了未登记对象：{unknown}"


def test_index_html_loads_scene_between_kit_and_app():
    """defer 顺序：scene-kit → s03 场景脚本 → app.js（顺序错则 window.SceneKit 缺失）。"""
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
    """契约文件必须挂在 window.SCENE_DATA['s03'] 下，且含阶段顺序 / 阈值 / 规则三段。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert cfg["scene"] == SCENE_ID
    assert cfg["stage_order"] == ["EVT", "DVT", "PVT"], "阶段顺序必须是 EVT / DVT / PVT"
    for key in ("yield_min", "cpk_min", "fpy_min"):
        assert key in cfg["gate"], f"gate 缺少 {key}"
    assert cfg["verdict_rule"], "缺少门禁判定规则（机械判定不能只写在屏里）"
    assert cfg["threshold_sources"], "阈值必须标注出处（操作细则 / spec-v1.md）"
    for stage in cfg["stage_order"]:
        assert stage in cfg["gate"]["yield_min"], f"yield_min 缺少阶段 {stage}"
        assert stage in cfg["gate"]["fpy_min"], f"fpy_min 缺少阶段 {stage}"


def test_inline_scene_data_mirrors_frontend_file():
    """屏内 INLINE_SCENE_DATA 必须与契约文件逐值相等（浏览器读不到 data/ 目录）。"""
    inline = _extract(SCENE_FILE, INLINE_PATTERN)
    frontend = _extract(DATA_FILE, FRONTEND_PATTERN)
    assert inline == frontend, (
        "S3 屏内联配置与 data/demo_storyline/frontend/s03-stage-gate.js 不一致，"
        f"差异键：{sorted(set(inline) ^ set(frontend)) or '（同键不同值）'}")


def test_scene_registers_into_scene_data():
    """屏必须把内联配置登记进 window.SCENE_DATA（契约口径：场景数据挂在 SCENE_DATA 下）。"""
    src = _scene_src()
    assert "window.SCENE_DATA" in src, "屏未使用 window.SCENE_DATA"
    assert "window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA" in src or \
        "window.SCENE_DATA['s03']" in src, "屏未把内联配置登记进 window.SCENE_DATA['s03']"


# ============================================================
# 3. 门禁机械判定可复算（与 T02 载荷一致）
# ============================================================

def _stage_metrics() -> dict:
    """从 events 取 S3 三个阶段的检验载荷。"""
    out: dict[str, dict] = {}
    for event in _jsonl("events"):
        if event.get("scene") != "S3" or event.get("type") != "inspection":
            continue
        payload = event.get("payload") or {}
        out[payload["stage"]] = {
            "cpk": float(payload["cpk"]),
            "fpy": float(payload["fpy"]),
            "verdict": payload.get("verdict"),
            "ts": event["ts"],
        }
    return out


def _main_order_fpy() -> float:
    """工单级直通率（与 T03 validate.check_metrics 同口径）：主线订单、status ≠ released。"""
    orders = _jsonl("sales_orders")
    main = max(orders, key=lambda o: sum(
        float(line.get("qty") or 0) for line in (o.get("lines") or [])))
    wos = [w for w in _jsonl("work_orders")
           if w.get("order_id") == main.get("order_id") and w.get("status") != "released"]
    assert wos, "主线订单下没有已开工工单，工单级直通率无从计算"
    good = sum(float(w.get("qty_good") or 0) for w in wos)
    plan = sum(float(w.get("qty_planned") or 0) for w in wos)
    assert plan > 0, "主线工单计划量为 0，直通率分母非法"
    return good / plan


def _open_issues(ts: str) -> int:
    """截至 ts 的未决问题数：问题 = alert（非 closed）+ complaint；闭环 = alert(closed) + hitl(closed/8D)。"""
    opened = closed = 0
    for event in _jsonl("events"):
        if str(event.get("ts")) > str(ts):
            continue
        payload = event.get("payload") or {}
        if event.get("type") == "alert" and payload.get("kind") != "closed":
            opened += 1
        if event.get("type") == "complaint":
            opened += 1
        if event.get("type") == "alert" and payload.get("kind") == "closed":
            closed += 1
        if event.get("type") == "hitl" and payload.get("result") in {"closed", "8D 闭环"}:
            closed += 1
    return max(0, opened - closed)


def _mechanical_verdict(stage: str, gate: dict) -> tuple[str, list[str]]:
    """按契约阈值机械判定，返回 (结论, 不达标维度名)。"""
    metrics = _stage_metrics()[stage]
    fpy = _main_order_fpy()
    open_issues = _open_issues(metrics["ts"])
    fails = []
    if metrics["fpy"] < float(gate["yield_min"][stage]):
        fails.append("良率")
    if metrics["cpk"] < float(gate["cpk_min"]):
        fails.append("CPK")
    if fpy < float(gate["fpy_min"][stage]):
        fails.append("直通率")
    if open_issues != 0:
        fails.append("问题闭环数")
    if not fails:
        return "Go", fails
    if len(fails) == 1:
        return "有条件 Go", fails  # 演示数据无「未决高风险」标注位
    return "No-Go", fails


def test_mechanical_gate_matches_t02_payload():
    """三道门的机械判定结论必须与 T02 载荷 verdict 一致（四维全达标 → Go）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    metrics = _stage_metrics()
    assert set(metrics) == set(cfg["stage_order"]), (
        f"events 里的阶段与契约 stage_order 不一致：{sorted(metrics)}")

    for stage in cfg["stage_order"]:
        verdict, fails = _mechanical_verdict(stage, cfg["gate"])
        payload_verdict = metrics[stage]["verdict"]
        assert verdict == payload_verdict, (
            f"{stage} 门机械判定为 {verdict}（不达标：{fails}），但 T02 载荷写的是 "
            f"{payload_verdict}——记分卡阈值与载荷判定漂移，必须调平")


def test_work_order_fpy_recompute_is_in_range():
    """工单级直通率重算值必须落在 (0,1] 且满足 PVT 直通率下限（T03 同口径）。"""
    cfg = _extract(DATA_FILE, FRONTEND_PATTERN)
    fpy = _main_order_fpy()
    assert 0.0 < fpy <= 1.0, f"工单级直通率越界：{fpy}"
    assert fpy >= float(cfg["gate"]["fpy_min"]["PVT"]), (
        f"工单级直通率 {fpy:.4f} 低于 PVT 直通率下限 {cfg['gate']['fpy_min']['PVT']}")


# ============================================================
# 4. 场景屏纪律：组件复用 / 降级 / 只读
# ============================================================

def test_scene_uses_scene_kit_contract():
    """必须通过 SceneRegistry 登记，并复用 SceneKit 组件（不另造一套渲染）。"""
    src = _scene_src()
    assert "SceneRegistry.register(" in src, "S3 屏未通过 SceneRegistry.register() 登记"
    assert "GROUP_NAME" in src, "分组必须取 SceneRegistry.GROUP_NAME"
    for api in ("pageHead", "numberRow", "eventFlow", "signoffCard", "openDrill", "bindDrills", "story("):
        assert api in src, f"S3 屏未使用组件契约 {api}"
    assert "演示数据" not in src.split("INLINE_SCENE_DATA")[0], (
        "「演示数据」徽标只能由 pageHead 统一产出，屏内不得自行拼接")


def test_scene_has_degrade_path_and_object_loader():
    """装载层未就绪必须给降级条（不编数字）；取数需兼容契约端点与当前实现端点。"""
    src = _scene_src()
    assert "演示装载层未就绪" in src, "缺少装载层降级文案"
    assert "sk-degrade" in src, "降级必须复用 sk-degrade 结构"
    assert "K.story(name)" in src, "未走 T05 契约入口 SceneKit.story()"
    assert "/api/v2/demo/objects/" in src, "缺少兜底直连端点（/api/v2/demo/objects/{name}）"
    for obj in ("events", "sales_orders", "work_orders", "inspections"):
        assert f'"{obj}"' in src, f"未取用对象 {obj}"


def test_scene_exposes_four_dimension_drills():
    """四维 + 门禁结论 + 检验批都必须登记口径下钻（验收：每格可下钻到口径/检验批）。"""
    src = _scene_src()
    for stage in ["EVT", "DVT", "PVT"]:
        for prefix in ["yield", "cpk", "fpy", "closed", "verdict"]:
            assert f'drills["{prefix}-" + n]' in src or f'drills["{prefix}-{stage}"]' in src, (
                f"缺少 {stage} 的 {prefix} 口径下钻登记")
    assert 'drills["batches"]' in src, "缺少检验批口径下钻"
    assert 'data-sk-drill="batches"' in src, (
        "检验批口径已登记但无点击入口（须渲染 data-sk-drill=\"batches\"，否则该下钻不可达）")
    assert "batchPairs" in src, "缺少检验批列表构造（验收：可下钻到检验批）"


def test_scene_has_no_write_path():
    """本屏不得出现写操作：会签一律置只读，避免绕过治理的旁路写入。"""
    src = _scene_src()
    assert "readonly: true" in src, "会签卡必须显式 readonly: true"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{method}"' not in src, f"S3 屏不应出现 {method} 写请求"
    assert "会签写操作" in src, "只读会签必须说明原因（真实写链路在治理窗口）"
