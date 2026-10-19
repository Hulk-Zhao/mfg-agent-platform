"""T12 演示标识与隔离验证守卫（纯 Python，禁网络 / 禁 node）。

收口任务守护三件事，全部是「别的窗口改不到、但一破就全站失守」的接缝：

1. **徽标框架级强制**：`scenes/scene-kit.js` 在加载时包装 `SceneRegistry.register`，
   给每个场景 render 加「同步首帧必须产出 `.demo-flag`」的守护；漏挂的屏首帧被换成
   框架拒绝面板（`.sk-degrade[data-sk-badge-guard]`）并 console.error。
   本机没有 node，无法起 JS 运行时执行它，所以这里用**结构断言**钉住：包装存在、
   检测类名存在、拒绝分支存在、且在 `originalRender` 之后执行；真正的「摘掉徽标
   即渲染失败」由 T12 真机故意测试复核（见计划文档 T12 特殊备注）。
2. **全局演示标识**：`demo/api.py` 的 `DemoMarkerMiddleware` 给所有响应追加
   `X-Demo-Mode` / `X-Demo-Label` 头（纯 ASGI，不改 SSE 长连接），并用 TestClient
   实测一次，证明头真的发出去了。
3. **隔离红线可复跑**：`tools/demo_gen/check_isolation.py` 扫演示实例断言
   A 无外部连接串 / B 无凭据泄漏 / C 无真实企业名词；这里以子进程实跑，要求退出码 0，
   并要求 `--self-test` 能证明三类检测器都真的会报出（防「永远全绿的空壳」）。
   旧演示 UI 的真实品牌命中按 2026-10-19 人工裁决列为「遗留·超范围」：照实打印、
   不阻塞验收，由本文件另用一条断言钉住「必须点名三个旧 UI 文件且给出条数」。
4. **L-02 两条终态断言**（原遗留缺口）：① `registry.json` 每个 `scenes[]` 条目的
   脚本文件必须真实存在；② `index.html` 引入的每个 `scenes/sNN-*.js` 必须在
   `registry.scenes` 有条目。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
SCENES_DIR = UI_DIR / "scenes"
SCENE_KIT = SCENES_DIR / "scene-kit.js"
REGISTRY_JS = SCENES_DIR / "registry.js"
INDEX_HTML = UI_DIR / "index.html"
DEMO_API = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py"
FRONTEND_DIR = REPO_ROOT / "data" / "demo_storyline" / "frontend"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"
CHECK_ISOLATION = REPO_ROOT / "tools" / "demo_gen" / "check_isolation.py"


def _src(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def _registry() -> dict:
    return json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))


# ============================================================
# 1. 徽标框架级强制（scene-kit.js）
# ============================================================

def test_scene_kit_badge_guard_installed_and_exported():
    """守护必须存在、导出、且在加载期装上（覆盖 T06–T11 全部场景屏）。"""
    src = _src(SCENE_KIT)
    assert "function installBadgeGuard(" in src, "scene-kit.js 缺少 installBadgeGuard()"
    assert "function hasDemoBadge(" in src, "scene-kit.js 缺少 hasDemoBadge()"
    assert "hasDemoBadge," in src and "installBadgeGuard," in src, (
        "hasDemoBadge / installBadgeGuard 必须导出（供外围核对与后续屏复用）")
    assert "installBadgeGuard();" in src, "加载期必须调用 installBadgeGuard() 装守护"
    assert "document.addEventListener(\"DOMContentLoaded\"" in src, (
        "registry 尚未就绪时需在 DOMContentLoaded 再补装一次")


def test_scene_kit_badge_guard_wraps_registry_register():
    """守护挂在 SceneRegistry.register 上：新屏绕不开，不必要求各屏自觉。"""
    src = _src(SCENE_KIT)
    assert "const reg = window.SceneRegistry;" in src, "守护必须取 window.SceneRegistry"
    assert "reg.register = function (def)" in src, (
        "守护必须包装 register：只包 register 才能覆盖后续新增的全部场景屏")
    assert "reg.__demoBadgeGuard" in src, "守护必须带幂等标记，避免重复包装"


def test_scene_kit_badge_guard_checks_demo_flag_class():
    """检测口径：只认 `.demo-flag` 类名（唯一出口 demoBadge()）。"""
    src = _src(SCENE_KIT)
    assert "DEMO_BADGE_CLASS_RE" in src, "缺少 `.demo-flag` 类名匹配常量"
    assert "demo-flag" in src, "检测目标必须是 .demo-flag 徽标类名"
    block = re.search(r"const DEMO_BADGE_CLASS_RE = (.+?);", src, re.S)
    assert block, "未找到 DEMO_BADGE_CLASS_RE 定义"
    assert "class=" in block.group(1), (
        "必须只匹配 class 属性里的 demo-flag，避免误命中正文同名字样")


def test_scene_kit_badge_guard_blocks_after_render_when_badge_missing():
    """漏挂徽标时：originalRender 先跑，随后换成拒绝面板 + console.error。"""
    src = _src(SCENE_KIT)
    idx_call = src.index("originalRender.call(this, root, ctx)")
    idx_check = src.index("if (hasDemoBadge(root)) return;")
    idx_block = src.index("root.innerHTML = badgeGuardPanel(sceneId);")
    assert idx_call < idx_check < idx_block, (
        "顺序必须是「先渲染 → 再检测 → 缺徽标才拦截」")
    assert "console.error(" in src, "拦截时必须 console.error，不能静默"
    assert "data-sk-badge-guard" in src, "拒绝面板必须带 data-sk-badge-guard 标记（可截图取证）"
    assert "拒绝渲染" in src, "拒绝面板必须说明「已按框架纪律拒绝渲染」"


@pytest.mark.parametrize("script", sorted(
    path.name for path in (UI_DIR / "scenes").glob("s0*.js")))
def test_existing_screens_all_use_pagehead(script: str):
    """所有已交付场景屏首帧都经 pageHead()（内联 demoBadge）——守护不会误伤它们。"""
    src = _src(SCENES_DIR / script)
    assert "pageHead(" in src, f"{script} 首帧必须调用 SceneKit.pageHead()（内联演示徽标）"


# ============================================================
# 2. 全局演示标识（api.py）
# ============================================================

def test_api_has_global_demo_marker_middleware():
    """api.py 必须有纯 ASGI 的演示标识中间件（不加 BaseHTTPMiddleware 以保 SSE）。"""
    src = _src(DEMO_API)
    assert "class DemoMarkerMiddleware" in src, "缺少 DemoMarkerMiddleware"
    assert "app.add_middleware(DemoMarkerMiddleware)" in src, "中间件未挂到 app"
    assert "http.response.start" in src, "必须在响应起始消息追加头"
    assert "b\"x-demo-mode\"" in src and "b\"x-demo-label\"" in src, (
        "必须输出 X-Demo-Mode / X-Demo-Label 两个头")
    assert "DEMO_LABEL" in src, "头值须取 config_demo.DEMO_LABEL 口径"


def test_api_demo_header_reaches_response():
    """实跑一次：任何响应都带演示标识头（用最轻的 /api/v2/demo/banner 端点）。"""
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    # 不用 `with TestClient(...)`：避开 lifespan（不建库、不预热 KB），只测响应头。
    client = TestClient(app)
    resp = client.get("/api/v2/demo/banner")
    assert resp.status_code == 200, f"banner 端点异常：{resp.status_code}"
    assert resp.headers.get("x-demo-mode") == "1", (
        f"X-Demo-Mode 应为 1，实际 {resp.headers.get('x-demo-mode')!r}")
    assert resp.headers.get("x-demo-label") == "demo-data", (
        f"X-Demo-Label 应为 demo-data，实际 {resp.headers.get('x-demo-label')!r}")


# ============================================================
# 3. 隔离红线可复跑（check_isolation.py）
# ============================================================

def _run_isolation(*extra: str) -> subprocess.CompletedProcess:
    assert CHECK_ISOLATION.exists(), f"缺少隔离脚本：{CHECK_ISOLATION}"
    return subprocess.run(
        [sys.executable, str(CHECK_ISOLATION), *extra],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )


def test_isolation_script_passes_on_current_tree():
    """隔离脚本在当前工作树上必须范围内全绿（A 连接串 / B 凭据 / C 企业名词零命中）。"""
    done = _run_isolation()
    assert done.returncode == 0, (
        "隔离自检未通过：\n" + done.stdout + done.stderr)
    assert "[PASS] A.连接串 / B.凭据 / C.企业名词" in done.stdout, (
        "范围内未全绿：\n" + done.stdout)
    assert "[FAIL]" not in done.stdout, (
        "范围内仍有违规：\n" + done.stdout)


def test_isolation_scope_split_reports_legacy_ui():
    """旧演示 UI 的真实品牌命中必须被「照实打印」，不许静默；且不阻塞范围内验收。

    2026-10-19 人工裁决：旧演示 UI（demo-data.js / screens.js / app.js）用真实行业
    品牌作语义素材、且 9 个既有 tests/v2 用例依赖这些串，整改越出 T12「可碰文件」
    清单，故列为「遗留·超范围」照实打印但不计入本节点验收。
    本断言钉住三件事：① 三个旧 UI 文件都在遗留清单里被点名；② 遗留命中非空
    （不静默、不隐藏）；③ 遗留项不影响退出码（脚本仍绿）。
    """
    done = _run_isolation()
    legacy_line = next(
        (ln for ln in done.stdout.splitlines() if "[遗留·超范围]" in ln), "")
    assert legacy_line, (
        "隔离脚本未按裁决打印「遗留·超范围」段（不许静默丢弃命中）：\n" + done.stdout)
    for name in ("demo-data.js", "screens.js", "app.js"):
        assert name in legacy_line, (
            f"遗留段未点名 {name}：{legacy_line}")
    assert re.search(r"命中 \d+ 条", legacy_line), (
        f"遗留段必须给出命中条数：{legacy_line}")
    assert done.returncode == 0, (
        "遗留段属超范围，不得影响本节点验收退出码：\n" + done.stdout)


def test_isolation_detectors_actually_fire():
    """自测：A/B/C 三类检测器都能报出合成坏内容——防「永远全绿的空壳脚本」。"""
    done = _run_isolation("--self-test")
    assert done.returncode == 0, (
        "隔离检测器自测未通过：\n" + done.stdout + done.stderr)
    for name in ("A.连接串", "B.凭据", "C.企业名词"):
        assert f"[PASS] 自测 {name}" in done.stdout, (
            f"{name} 检测器自测未通过：\n" + done.stdout)


# ============================================================
# 4. L-02 两条终态断言（登记表双向闭环）
# ============================================================

def test_l02_every_registered_scene_script_exists():
    """① registry.scenes 的每个条目，脚本文件必须在盘（含 owner / data 声明齐全）。"""
    scenes = _registry()["scenes"]
    assert scenes, "registry.scenes 为空"
    for scene in scenes:
        script_rel = scene.get("file") or scene.get("module")
        assert script_rel, f"{scene.get('id')} 缺少屏文件键（file / module）"
        assert (UI_DIR / script_rel).exists(), (
            f"{scene['id']} 的脚本不存在：{script_rel}（登记了却指到空文件）")


def test_l02_index_script_has_registry_entry():
    """② index.html 引入的每个 scenes/sNN-*.js，必须在 registry.scenes 有条目。"""
    html = _src(INDEX_HTML)
    srcs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    imported = sorted({
        Path(src.split("?")[0]).name
        for src in srcs
        if Path(src.split("?")[0]).parent.as_posix() == "scenes"
        and re.match(r"^s\d{2}-.*\.js$", Path(src.split("?")[0]).name)
    })
    assert imported, "index.html 未引入任何场景屏脚本"

    registered = set()
    for scene in _registry()["scenes"]:
        script_rel = scene.get("file") or scene.get("module") or ""
        registered.add(Path(script_rel).name)

    missing = [name for name in imported if name not in registered]
    assert not missing, (
        f"index.html 引入了但 registry.scenes 未登记的屏脚本：{missing}"
        "（场景能跑，数据契约却查不到归属——L-02 反向缺口）")
