"""SPC 看板端到端验证（真机浏览器 + 真后端）。

覆盖 QA 无法静态断言的四态：
  1. 板级筛选三档口径（当日/当月/最近7天）× 产品分类 × 工段
  2. 2 级看板行单击 → 弹窗（含「进入 3 级看板」入口）
  3. 无权岗位 403 → 前端如实拒绝（不降级演示明细）
  4. 离线 → 显式降级标注（不假装是筛选后的汇总）

用法：python tools/e2e_spc_verify.py
要求：后端已在 127.0.0.1:8011 运行（由调用方启动）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8011"
SHOT_DIR = Path("logs/e2e_spc")
SHOT_DIR.mkdir(parents=True, exist_ok=True)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


def login(page, role: str) -> None:
    """按 UI 点击登录：选岗位卡 → 点「进入系统」（不 eval setRole）。"""
    page.goto(f"{BASE}/demo-ui/index.html", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    card = page.query_selector(f'button[data-role="{role}"]')
    if card is None:
        raise AssertionError(f"登录页找不到角色 {role} 的岗位卡")
    card.click(force=True)
    page.wait_for_timeout(1200)
    enter = page.query_selector("#login-enter")
    if enter is None:
        raise AssertionError("登录页缺少「进入系统」按钮")
    enter.click(force=True)
    page.wait_for_timeout(3000)


def goto_spc(page) -> None:
    """切到 SPC 屏（侧边栏）。"""
    page.wait_for_timeout(800)
    for sel in ('.nav-item:has-text("SPC")', '[data-screen="spc"]',
                '.nav-item:has-text("过程能力")', 'a:has-text("SPC")'):
        el = page.query_selector(sel)
        if el is not None:
            el.click()
            page.wait_for_timeout(2200)
            return
    raise AssertionError("侧边栏找不到 SPC 入口")


def spc_payload(page) -> dict:
    return page.evaluate(
        "async () => { const r = await fetch('/api/v2/dashboard/spc?window=window');"
        " return {status: r.status, body: await r.text()}; }"
    )


#: 页面内带令牌取数（裸 fetch 无 Authorization 头 → 401 fail-closed，不能用来断言数据）
API_JS = """
async (path) => {
  const t = (window.App && window.App.state && window.App.state.token) || "";
  const r = await fetch(path, {headers: t ? {Authorization: 'Bearer ' + t} : {}});
  let j = null; try { j = await r.json(); } catch (e) { j = null; }
  const d = (j && j.data) || {};
  return {status: r.status, caps: (d.capabilities || []),
          kpis: (d.kpis || []), filter: d.filter || null,
          window: d.window || null, degraded: !!d.degraded,
          reasons: d.degraded_reasons || d.reasons || []};
}
"""


def api_get(page, path: str) -> dict:
    """页面内带令牌取数。"""
    return page.evaluate(f"(path) => {{ {API_JS} }}", path) if False else page.evaluate(
        """async (path) => {
          const t = (window.App && window.App.state && window.App.state.token) || '';
          const r = await fetch(path, {headers: t ? {Authorization: 'Bearer ' + t} : {}});
          let j = null; try { j = await r.json(); } catch (e) { j = null; }
          const d = (j && j.data) || {};
          return {status: r.status, caps: (d.capabilities || []), kpis: (d.kpis || []),
                  filter: d.filter || null, window: d.window || null,
                  degraded: !!d.degraded, raw: JSON.stringify(j).slice(0, 300)};
        }""", path)


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as pw:
        # 本机用系统 Chrome（Playwright 自带 chromium 未下载）
        chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        browser = pw.chromium.launch(
            headless=True,
            executable_path=chrome if Path(chrome).exists() else None,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1680, "height": 1000})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---------- 1. 有权岗位：工具栏 + 筛选 + 弹窗 ----------
        login(page, "gm")
        goto_spc(page)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOT_DIR / "01_spc_board.png"), full_page=True)

        # 工具栏存在性（图1 形态：口径 chips + 产品分类下拉 + 工段 chips）
        has_windows = page.query_selector("#spc-windows [data-window]") is not None
        check("板级工具栏：统计口径 chips 存在", has_windows)
        sel = page.query_selector("#spc-windows select[data-pcat], #spc-windows [data-pcat]")
        check("板级工具栏：产品分类下拉存在", sel is not None,
              f"tag={sel.evaluate('e=>e.tagName') if sel else '-'}")
        stage_chips = page.query_selector_all("#spc-windows [data-stage]")
        check("板级工具栏：工段 chips 存在", len(stage_chips) >= 4,
              f"{len(stage_chips)} 个")
        win_chips = page.query_selector_all("#spc-windows [data-window]")
        check("板级工具栏：三档口径齐全（当日/当月/最近7天）", len(win_chips) == 3,
              f"{len(win_chips)} 个")

        # 切口径 → 数据应变化且不空（页面内带令牌取数）
        counts = {}
        for w in ("day", "month", "window"):
            r = api_get(page, f"/api/v2/dashboard/spc?window={w}")
            counts[w] = (r["status"], len(r["caps"]))
        check("三档口径均有数据（无空板）",
              all(s == 200 and n > 0 for s, n in counts.values()),
              json.dumps(counts, ensure_ascii=False))

        # 切工段 → 仍应三档齐备（演示库 12 特性方案）
        stage_ok = True
        stage_detail = []
        for st in ("SMT", "PCM", "PACK", "整机"):
            v = api_get(page, f"/api/v2/dashboard/spc?window=window&stage={st}")
            verdicts = sorted({c.get("verdict") for c in v["caps"]})
            need = {"合格", "偏低", "不合格"}
            stage_detail.append(f"{st}:{len(v['caps'])}/{','.join(verdicts)}")
            if v["status"] != 200 or not need <= set(verdicts):
                stage_ok = False
        check("筛工段后三档判定仍齐备（不塌成单档）", stage_ok, " | ".join(stage_detail))

        # 产品分类筛选（板级下拉）也应三档齐备
        pcat_ok = True
        pcat_detail = []
        for pc in ("消费电子", "智能电源", "储能电源", "车载"):
            v = api_get(page, f"/api/v2/dashboard/spc?window=window&product_category={pc}")
            verdicts = sorted({c.get("verdict") for c in v["caps"]})
            pcat_detail.append(f"{pc}:{len(v['caps'])}/{','.join(verdicts)}")
            if v["status"] != 200 or not {"合格", "偏低", "不合格"} <= set(verdicts):
                pcat_ok = False
        check("筛产品分类后三档判定仍齐备", pcat_ok, " | ".join(pcat_detail))

        # 载荷回显筛选（前端口径标签的数据来源）
        rv = api_get(page, "/api/v2/dashboard/spc?window=day&stage=SMT&product_category=消费电子")
        check("载荷回显筛选与口径（filter/window）",
              (rv.get("filter") or {}).get("stage") == "SMT"
              and (rv.get("filter") or {}).get("product_category") == "消费电子"
              and (rv.get("window") or {}).get("key") == "day",
              json.dumps({"filter": rv.get("filter"), "window": rv.get("window")},
                         ensure_ascii=False))

        # 2 级看板行 → 弹窗：
        #   L1 能力表（spcCapCard）的行带 data-drill-entity（直下 L3）；
        #   L2 下钻页（mfgDrillL2Body）的行才带 data-spc-modal="capability"（弹窗）。
        #   L2 入口 = KPI 卡（[data-kpi] 且不带 data-drill-entity）→ 点它进 2 级看板。
        page.wait_for_timeout(1200)
        kpi_card = page.query_selector("[data-kpi='spc_capability']:not([data-drill-entity])")
        check("L1 KPI 卡存在（进 2 级看板的入口）", kpi_card is not None)
        # L1「全厂越限明细」入口（扩充：不必进 2 级看板即可查看全厂超差记录）
        l1_over = page.query_selector("#spc-body [data-spc-records='overlimit']")
        check("L1 存在「全厂越限明细」入口", l1_over is not None)
        if l1_over is not None:
            l1_over.click(force=True)
            page.wait_for_timeout(2500)
            m0 = page.query_selector(".std-modal")
            check("L1 单击弹出全厂越限明细弹窗", m0 is not None)
            if m0 is not None:
                t0 = m0.inner_text()
                check("全厂越限弹窗含记录或如实提示",
                      ("越限记录" in t0 or "版本待更新" in t0 or "离线" in t0),
                      t0[:140].replace("\n", " "))
                page.screenshot(path=str(SHOT_DIR / "07_l1_overlimit.png"), full_page=True)
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
        if kpi_card is not None:
            kpi_card.click(force=True)
            page.wait_for_timeout(3200)
        rows = page.query_selector_all("[data-spc-modal='capability']")
        check("2 级看板行带弹窗钩子（data-spc-modal）", len(rows) > 0, f"{len(rows)} 行")
        if rows:
            rows[0].click(force=True)
            page.wait_for_timeout(3000)
            modal = page.query_selector(".std-modal")
            check("单击 2 级行弹出能力明细弹窗", modal is not None)
            if modal is not None:
                txt = modal.inner_text()
                check("弹窗含 Cpk 判定与原始记录", ("Cpk" in txt and "判定" in txt),
                      txt[:200].replace("\n", " "))
                check("弹窗含「进入 3 级看板」入口", "进入 3 级看板" in txt)
                page.screenshot(path=str(SHOT_DIR / "02_spc_modal.png"), full_page=True)
                d3 = page.query_selector(".std-modal [data-spc-drill3]")
                if d3 is not None:
                    d3.click(force=True)
                    page.wait_for_timeout(3000)
                    drill = page.evaluate(
                        "() => JSON.stringify((window.App && window.App.state && "
                        "window.App.state.screenState || {}).boardDrill || {})")
                    check("弹窗入口可进入 3 级看板",
                          '"level":3' in drill.replace(" ", "") and "spc" in drill,
                          drill)
                    page.screenshot(path=str(SHOT_DIR / "03_spc_level3.png"), full_page=True)
                    # 3 级 hero 的「越限明细」按钮 → 越限明细弹窗（数据集增量新增）
                    over_btn = page.query_selector("[data-spc-records='overlimit']")
                    check("3 级 hero 存在「越限明细」入口", over_btn is not None)
                    if over_btn is not None:
                        over_btn.click(force=True)
                        page.wait_for_timeout(2500)
                        modal2 = page.query_selector(".std-modal")
                        check("单击「越限明细」弹出记录弹窗", modal2 is not None)
                        if modal2 is not None:
                            txt2 = modal2.inner_text()
                            check("越限明细弹窗含记录与口径说明",
                                  ("越限" in txt2 and ("超出" in txt2 or "测量" in txt2)),
                                  txt2[:160].replace("\n", " "))
                            page.screenshot(
                                path=str(SHOT_DIR / "05_spc_overlimit_modal.png"),
                                full_page=True)
                            close2 = modal2.query_selector(".std-modal-close, [data-modal-close], button[aria-label='关闭']")
                            if close2 is not None:
                                close2.click(force=True)
                            else:
                                page.keyboard.press("Escape")
                            page.wait_for_timeout(1200)
                    # 3 级逐日表行单击 → 单日测量明细弹窗
                    day_row = page.query_selector("[data-spc-records='daydetail']")
                    check("3 级逐日行带单击钩子", day_row is not None)
                    if day_row is not None:
                        day_row.click(force=True)
                        page.wait_for_timeout(2500)
                        modal3 = page.query_selector(".std-modal")
                        check("单击逐日行弹出单日明细弹窗", modal3 is not None)
                        if modal3 is not None:
                            txt3 = modal3.inner_text()
                            check("单日明细弹窗含测量记录表",
                                  ("测量" in txt3 or "记录" in txt3),
                                  txt3[:160].replace("\n", " "))
                            page.screenshot(
                                path=str(SHOT_DIR / "06_spc_daydetail_modal.png"),
                                full_page=True)
        else:
            errors.append("未找到 2 级看板行（data-spc-modal）")
            page.screenshot(path=str(SHOT_DIR / "02_spc_modal_missing.png"), full_page=True)

        # ---------- 2. 无权岗位 403 ----------
        page2 = ctx.new_page()
        try:
            login(page2, "wh")
            st = page2.evaluate(
                "async () => { const t = (window.App.state && window.App.state.token) || '';"
                " const r = await fetch('/api/v2/dashboard/spc?window=window',"
                " {headers: t ? {Authorization: 'Bearer ' + t} : {}});"
                " return {status: r.status, body: (await r.text()).slice(0, 200)}; }")
            check("无权岗位 → 403 fail-closed", st["status"] == 403,
                  f"HTTP {st['status']} {st['body'][:100]}")
            check("403 响应体不含降级 code 0",
                  '"code":0' not in st["body"].replace(" ", ""))
        except Exception as exc:
            check("无权岗位 → 403 fail-closed", False, f"{type(exc).__name__}: {exc}")
        finally:
            page2.close()

        # ---------- 3. 离线降级 ----------
        page3 = ctx.new_page()
        try:
            login(page3, "gm")
            goto_spc(page3)
            page3.wait_for_timeout(2000)
            # 断掉后端
            ctx.route("**/api/v2/dashboard/spc*",
                      lambda route: route.abort())
            page3.evaluate("() => window.App && window.App.UI && 0")
            page3.wait_for_timeout(200)
            # 触发重新加载
            page3.evaluate(
                "() => { const s = window.App && window.App.state;"
                " if (s && s.screenState) { s.screenState.spcWindow = s.screenState.spcWindow; } }")
            page3.reload(wait_until="domcontentloaded")
            page3.wait_for_timeout(3000)
            goto_spc(page3)
            page3.wait_for_timeout(3000)
            body = page3.inner_text("body")
            has_note = ("离线" in body) or ("演示数据" in body)
            check("离线 → 页面有显式降级标注", has_note, body[:200].replace("\n", " "))
            page3.screenshot(path=str(SHOT_DIR / "04_spc_offline.png"), full_page=True)
        except Exception as exc:
            check("离线 → 页面有显式降级标注", False, f"{type(exc).__name__}: {exc}")
        finally:
            page3.close()

        if errors:
            check("无 JS 运行时错误", False, "; ".join(errors[:3]))
        else:
            check("无 JS 运行时错误", True)

        browser.close()

    print("\n" + "=" * 60)
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"端到端结果：{passed}/{len(RESULTS)} 通过")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} :: {detail}")
    print("=" * 60)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
