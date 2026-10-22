"""签名账本前端入口（审批确认中心「签名流水 + CSV 导出」）静态契约。

钉四件事：
1. API 层有只读查询与带令牌 Blob 下载两个方法，端点与后端路由一致；
2. 审批详情卡内是默认收起的 ``<details>``，展开才懒加载（一屏多卡不预取）；
3. 行字段覆盖 role_name / actor / verb / decision / comment / ts；
4. 新样式只走主题令牌，不写死颜色（亮/暗主题都能跟随）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"


def _read(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")


def test_app_js_exposes_signoff_api():
    app_js = _read("app.js")
    assert "workflowSignoffs(taskId) {" in app_js
    assert "/api/v2/workflow/${encodeURIComponent(taskId)}/signoffs" in app_js
    assert "downloadWorkflowSignoffs(taskId)" in app_js
    assert "/signoffs/export" in app_js
    # 普通 <a href> 无法携带令牌 → 必须带 Authorization 的 fetch + Blob
    block = app_js[app_js.find("downloadWorkflowSignoffs(taskId)"):]
    block = block[:block.find("governancePermissions()")]
    assert "Authorization: `Bearer ${state.token}`" in block
    assert "resp.blob()" in block
    assert "URL.createObjectURL" in block
    assert "signoffs-${taskId}.csv" in block
    assert "URL.revokeObjectURL" in block, "Blob URL 必须释放，避免内存泄漏"


def test_screens_detail_embed_collapsed_flow():
    screens = _read("screens.js")
    assert "function signoffFlowHtml(ctx, taskUid)" in screens
    assert '<details class="sg-flow" data-sg-task=' in screens
    assert "data-sg-count" in screens and "展开加载" in screens
    # 详情卡内挂载（在会签缺口行之后）
    assert ("${signoffGapHtml(ctx, taskSignoff(t), slot, (t.context || {}).actions)}\n"
            "        ${signoffFlowHtml(ctx, t.task_uid)}") in screens
    assert "wireSignoffFlow(document, ctx, t.task_uid);" in screens


def test_screens_lazy_load_and_export():
    screens = _read("screens.js")
    assert 'box.addEventListener("toggle"' in screens, "展开才请求，收起不预取"
    assert "await ctx.api.workflowSignoffs(taskUid)" in screens
    assert "data-sg-export" in screens
    assert "ctx.api.downloadWorkflowSignoffs(taskUid)" in screens
    # 空账本必须诚实说明（存量工单不回溯）
    assert "该工单暂无签名事实" in screens and "不回溯" in screens
    # 后端离线不得伪造签名流水
    assert "后端离线：签名账本需后端在线才能读取" in screens


def test_screens_rows_cover_ledger_columns():
    screens = _read("screens.js")
    i = screens.find("function signoffRowsHtml(ctx, rows)")
    j = screens.find("function signoffFlowHtml(ctx, taskUid)", i)
    assert i > 0 and j > i
    block = screens[i:j]
    for field in ("r.role_name", "r.actor", "r.verb", "r.decision",
                  "r.comment", "r.ts"):
        assert field in block, f"签名流水行缺字段: {field}"
    # 动作动词中文化（不把 cosign / approve 裸 key 丢给用户）
    for label in ("会签", "终批", "驳回", "选定动作"):
        assert label in screens


def test_css_signoff_flow_uses_theme_tokens():
    css = _read("app.css")
    for cls in (".sg-flow", ".sg-flow-sum", ".sg-flow-body",
                ".sg-flow-table", ".sg-flow-row", ".sg-flow-empty"):
        assert cls in css, f"app.css 缺少 {cls}"
    start = css.find("签名流水（wf_signoff 只读审计")
    assert start > 0
    block = css[css.rfind("/*", 0, start):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "新样式不得写死颜色"
    for token in ("var(--border)", "var(--text-2)", "var(--text-3)",
                  "var(--bg-sub)", "var(--bg-2)", "var(--primary)"):
        assert token in block, f"签名流水样式缺主题令牌: {token}"
