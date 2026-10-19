"""T13 演示冒烟（真机）：自起临时实例 → 五访客登录 → 按各自可见屏逐屏走 →
断言关键数字出现 +「演示数据」徽标在位 + 不可见屏在侧栏确实不存在。

口径与理由
----------
- **自起临时实例**：本机常驻 8004 是公网隧道入口（用户红线「绝不动」）；冒烟必须
  能在「干净环境复现」，故本文件用 ``uvicorn`` 子进程在**随机空闲端口**起一份独立
  实例，测毕回收。前端 ``api.probe()`` 同源优先，实例端口即数据来源，不依赖 8004。
- **按各自可见屏走**：可见性**不硬编码**，而是从 ``registry.json`` 的 ``roles`` 现算，
  再与真机侧栏对账——契约说某访客不该看见 s04，真机侧栏就必须没有它。这是
  「契约 ↔ 运行时」的交叉验证（s04 缺 biz / s05 缺 rd，正是 spec-v1.md §4 的设计）。
- **关键数字**：每屏断言至少两个**由装载层现算**的锚点文本出现在 ``#screen-root``；
  装载层降级时这些文本不出现，故锚点同时兼作「真数据已到位」的证据。
- **徽标**：``.demo-flag`` 必须在位，且不得出现 ``[data-sk-badge-guard]`` 拒绝面板
  （T12 框架级守护：漏挂徽标即拒渲染）。
- 登录一律走 UI 点击（``[data-role]`` → ``#login-enter`` → ``.nav-item``），
  不 eval ``setRole()``（计划 §0 规则 7）。
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
REGISTRY_JSON = REPO_ROOT / "data" / "demo_storyline" / "frontend" / "registry.json"

#: 五访客 id → 其命中治理目录的角色 key（与 tests/demo/test_t05_contract.py 同源口径）。
VISITOR_ROLES = {
    "gm-demo": ["gm"],
    "biz-demo": ["sales"],
    "rd-demo": ["rd_engineering"],
    "qa-demo": ["qa"],
    "mfg-demo": ["manufacturing"],
}

#: 屏 id → 侧栏可见性判据（即 registry.json 该条目的 roles；空 = 全员可见）。
#: 运行期真实值从 registry.json 现读，见 ``_expected_visible()``。

#: 屏 id → 关键数字锚点（全部由 T04 装载层现算，装载层降级时不会出现）。
#: T20 起每屏追加一条「智能体层」锚点（T16–T19 回放面板标题）：异步渲染，
#: 锚点出现即证明回放端点与组件都真的到位（等待逻辑天然覆盖异步加载）。
SCENE_ANCHORS = {
    "s00": ["订购总量", "7,400"],
    "s01": ["ATP 可用（现算）", "169,309.60", "接单制度问答"],
    "s02": ["20 条合成案例", "5,000", "立项案例检索智能体"],
    "s03": ["主线直通率", "98.77%", "门禁准则问答"],
    "s04": ["判异点数", "ECN 影响面合计", "SPC 判异智能体"],
    "s05": ["同库位库存数量", "3,952", "客诉 8D 多智能体协同"],
    "s06": ["间隔 0 分钟", "2,000 片", "采购智能体"],
}


# ============================================================
# 期望可见性（契约 → 运行时交叉验证的「契约」一侧）
# ============================================================

def _registry() -> dict:
    return json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))


def _scene_ids() -> list[str]:
    return sorted(scene["id"] for scene in _registry()["scenes"])


def _expected_visible(visitor: str) -> set[str]:
    """按 registry.json 的 roles 现算该访客应可见的场景 id。

    判定同 ``app.js::navVisible``：``[role.id].concat(role.roles)`` 是否命中
    ``item.roles``；``roles`` 缺省/空 = 全员可见。
    """
    own = [visitor] + VISITOR_ROLES[visitor]
    visible = set()
    for scene in _registry()["scenes"]:
        roles = scene.get("roles") or []
        if not roles or set(own) & set(roles):
            visible.add(scene["id"])
    return visible


# ============================================================
# 临时实例（随机空闲端口自起自收）
# ============================================================

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(base: str, proc: subprocess.Popen, timeout_s: float = 90.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base + "/api/v2/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 未就绪即继续重试
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def demo_base():
    """起一份独立临时实例（不复用 8004，不碰公网隧道），测毕回收。"""
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mfg_agents.v2.demo.api:app",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_ready(base, proc):
            out = b""
            if proc.stdout is not None:
                try:
                    out = proc.stdout.read()
                except Exception:  # noqa: BLE001
                    pass
            pytest.fail(f"临时演示实例未就绪（端口 {port}）：\n{out.decode('utf-8', 'ignore')[-2000:]}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        try:
            handle = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:  # noqa: BLE001 - 无 chrome 通道时回落打包 chromium
            handle = playwright.chromium.launch(headless=True)
        yield handle
        handle.close()


# ============================================================
# 冒烟主体：五访客各一条用例
# ============================================================

def _login(page, base: str, visitor: str) -> None:
    page.goto(base + "/demo-ui/", wait_until="load")
    page.wait_for_selector("#login-root:not([hidden]) [data-role]", timeout=20000)
    page.click(f'[data-role="{visitor}"]')
    page.click("#login-enter")
    page.wait_for_selector(".nav-item", timeout=20000)


def _walk_screen(page, sid: str) -> None:
    """点开该屏，等待装载层数据到达（全部锚点出现），并核验徽标。"""
    page.click(f'.nav-item[data-screen="{sid}"]')
    anchors = SCENE_ANCHORS[sid]
    page.wait_for_function(
        "() => { const t = document.querySelector('#screen-root')?.innerText || '';"
        " return " + " && ".join(f"t.includes({json.dumps(a)})" for a in anchors) + "; }",
        timeout=25000)
    assert page.query_selector("#screen-root .demo-flag") is not None, (
        f"{sid} 首帧缺少「演示数据」徽标（.demo-flag）")
    assert page.query_selector("#screen-root [data-sk-badge-guard]") is None, (
        f"{sid} 触发 T12 徽标守护拒绝面板（漏挂徽标）")
    body = page.inner_text("#screen-root")
    for anchor in anchors:
        assert anchor in body, f"{sid} 关键数字锚点缺失：{anchor}（装载层未到位？）"
    assert "装载层未就绪" not in body, f"{sid} 走了降级分支（未读到 T04 装载层真数据）"


@pytest.mark.parametrize("visitor", sorted(VISITOR_ROLES))
def test_visitor_walks_visible_screens(demo_base, browser, visitor: str):
    """单访客：登录 → 侧栏只见契约允许的屏 → 逐屏走通 → 不可见屏侧栏确实不存在。"""
    visible = _expected_visible(visitor)
    invisible = set(_scene_ids()) - visible
    assert visible, f"{visitor} 契约下有零个可见屏，登记表异常"

    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: errors.append(f"console.{msg.type}: {msg.text}")
        if msg.type == "error" and "favicon" not in msg.text.lower() else None,
    )
    try:
        _login(page, demo_base, visitor)

        groups = [g.inner_text().strip() for g in page.query_selector_all(".nav-group-label")]
        assert any("生态链场景" in g for g in groups), (
            f"{visitor} 侧栏缺少「生态链场景」分组：{groups}")

        # 不可见屏在侧栏确实不存在（契约 roles 与运行时登记一致）
        for sid in sorted(invisible):
            assert page.query_selector(f'.nav-item[data-screen="{sid}"]') is None, (
                f"{visitor} 侧栏出现了契约不可见的屏 {sid}")

        # 侧栏场景项数 = 契约可见数（无多余、无遗漏）
        scene_nav = [
            item.get_attribute("data-screen")
            for item in page.query_selector_all(".nav-item")
            if item.get_attribute("data-screen") in set(_scene_ids())
        ]
        assert set(scene_nav) == visible, (
            f"{visitor} 侧栏场景项与契约不符：侧栏 {sorted(scene_nav)} vs 契约 {sorted(visible)}")

        # 逐屏走通
        for sid in sorted(visible):
            _walk_screen(page, sid)

        assert not errors, f"{visitor} 真机出现 JS 报错：{errors[:5]}"
    finally:
        ctx.close()


def test_db_visibility_spec_notes_match_contract():
    """把 spec-v1.md §4 的两条特殊可见性固化成断言（s04 缺 biz / s05 缺 rd）。

    这条与真机无关，单独钉住「可见性矩阵」这一契约事实，防后续窗口误改 roles
    把业务/研发访客的负向用例悄悄抹平。
    """
    s04 = next(s for s in _registry()["scenes"] if s["id"] == "s04")
    s05 = next(s for s in _registry()["scenes"] if s["id"] == "s05")
    assert "biz" not in set(s04.get("roles") or []) and "sales" not in set(s04.get("roles") or []), (
        "s04 不应含业务（biz / sales）：spec-v1.md §4 划定 S4 面向 总经理/研发/质量/制造")
    assert "rd" not in set(s05.get("roles") or []) and "rd_engineering" not in set(s05.get("roles") or []), (
        "s05 不应含研发（rd / rd_engineering）：spec-v1.md §4 划定 S5 面向 总经理/业务/质量/制造")
