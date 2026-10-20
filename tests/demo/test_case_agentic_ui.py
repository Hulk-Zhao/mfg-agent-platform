"""案例屏「证据链 2.0 / 三方对照」前端契约守护（PRD FR-04 / FR-05 / FR-06 / FR-07）。

为什么用静态契约而不是端到端点击：这几条契约最容易在"改样式/改文案"时被顺手删掉
（删掉一个徽标没人会发现，但演示现场会立刻少一屏说服力）。静态断言能便宜地钉住：

1. 证据链步骤必须渲染 **后端给定的 kind / numbers / why**（前端不许自己猜标题字符串）；
2. 作答器徽标必须区分 llm / template / template(fallback)（降级必须诚实可见）；
3. 三方对照页签必须**由服务端开关驱动**（`S.meta.demo_compare`），不得写死 true；
4. 对照请求必须一次走 `/api/v2/cases/compare`（三组的"同一身份"前提只有服务端能保证）。
"""
from __future__ import annotations

from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"


def _src() -> str:
    return (UI_DIR / "case-screen.js").read_text(encoding="utf-8")


def _office_src() -> str:
    return (UI_DIR / "office-screen.js").read_text(encoding="utf-8")


def _css() -> str:
    return (UI_DIR / "app.css").read_text(encoding="utf-8")


def test_quick_chip_case_ask_deeplink_is_wired():
    """数据速查示例胶囊的「问案例」深链：出口 / 入口 / 呈现三件套缺一不可。

    为什么用静态契约：这条链路横跨 office-screen.js（写预填键 + 切屏）与
    case-screen.js（消费预填键 + 到屏自动提问）两个文件——删掉任何一半都不会
    抛错，现场表现只是"点了没反应"，正是最难被发现的半截删除。
    """
    office = _office_src()
    assert 'data-case-ask' in office, "速查示例胶囊缺少「问案例」按钮"
    assert 'cases_ask_prefill' in office, "缺少深链预填键写入"
    assert 'ctx.navigate("cases")' in office, "「问案例」必须切到案例检索室"

    src = _src()
    assert "cases_ask_prefill" in src, "案例屏未消费 cases_ask_prefill"
    assert "S.pendingAsk" in src, "深链问题未进入待提问状态"
    assert "doAsk(pendingQ)" in src, "缺少到屏后的自动提问"

    css = _css()
    assert ".of-quick .chip-ask" in css, "缺少双动作胶囊容器样式"
    assert ".of-quick .chip-ask .chip-case" in css, "缺少「问案例」子按钮样式"


def test_evidence_chain_renders_backend_decision_fields():
    """步骤徽标/数字/原因三件套都来自后端字段，前端不做任何推断。"""
    src = _src()
    assert "case-kind-badge" in src, "缺少决策类型徽标"
    assert "step.kind" in src, "徽标必须取后端 step.kind（不能按标题猜）"
    assert "step.numbers" in src and "case-num-chip" in src, "缺少关键数字渲染"
    assert "case-step-why" in src and "step.why" in src, "缺少触发/终止原因渲染"
    assert "STEP_KIND" in src, "缺少决策类型 → 中文标签映射"


def test_answerer_badge_distinguishes_llm_and_fallback():
    """作答器必须诚实标注（大模型 / 模板 / 模板降级）。"""
    src = _src()
    assert "case-answerer" in src
    assert 'is-llm' in src and "大模型作答" in src
    assert "template(fallback)" in src, "降级态必须与普通模板态区分"
    assert "case-answerer is-fb" in src, "缺少降级徽标"


def test_agentic_strip_shows_rounds_hops_and_termination():
    """循环摘要条：意图 / 轮数 / 跳数 / 终止原因——"Agentic 做了什么"一眼可见。"""
    src = _src()
    assert "case-agentic-strip" in src and "case-kpi-chip" in src
    assert "召回轮数" in src and "关系跳数" in src and "终止" in src


def test_suggestions_are_clickable_and_backend_driven():
    """改写引导：点击即重问；建议来自后端 `a.suggestions`。"""
    src = _src()
    assert "case-sugg" in src and "a.suggestions" in src
    assert 'data-act="ask-suggest"' in src
    assert 'act === "ask-suggest"' in src, "缺少点击处理（建议点了没反应）"


def test_compare3_tab_is_gated_by_server_switch():
    """三方对照只在服务端开关打开时出现（生产环境前端不得自行放开）。"""
    src = _src()
    assert 'data-act="view-compare3"' in src
    assert "compare3Enabled" in src
    assert "S.meta.demo_compare" in src, "页签必须由 meta.demo_compare 驱动"
    assert "demo_compare: true" not in src and "demoCompare = true" not in src, \
        "前端不得硬编码开关为真"


def test_compare3_runs_single_backend_call():
    """对照必须打一次 `/cases/compare`：三组同身份的保证在服务端。"""
    src = _src()
    assert "/api/v2/cases/compare" in src
    assert "function doCompare3" in src
    assert 'on("#case-cmp3-run"' in src, "缺少对照运行按钮的绑定"
    assert "cmp3Loading" in src and "case-cmp3" in src


def test_case_css_defines_evidence_and_compare_classes():
    """新增类名必须有样式（只有类名没有样式 = 演示现场一片裸文字）。"""
    css = _css()
    for name in ("case-answerer", "case-agentic-strip", "case-kpi-chip",
                 "case-kind-badge", "case-num-chip", "case-step-why",
                 "case-sugg", "case-cmp3", "case-cmp3-col", "case-cmp3-gain",
                 "case-cmp3-new", "case-cmp3-strip"):
        assert "." + name in css, f"app.css 缺少样式类 .{name}"


def test_coverage_pane_is_backend_driven_and_fetch_once():
    """FR-08 覆盖率看板：治理入口 + 一次 GET；数字全部来自服务端。"""
    src = _src()
    assert 'govLink("gov-onto"' in src, "缺少本体覆盖率治理入口"
    assert 'act === "gov-onto"' in src, "入口无点击处理"
    assert "/api/v2/cases/ontology/coverage" in src, "缺少覆盖率接口调用"
    assert "coveragePane" in src and "S.coverage" in src
    assert "case-cov-bar" in src, "缺少覆盖率可视化"
    assert "unregistered_sample" in src, "未登记编码样例必须展示（治理要能派工）"


def test_case_css_defines_coverage_classes():
    css = _css()
    for name in ("case-cov", "case-cov-card", "case-cov-bar", "case-cov-miss",
                 "case-cov-notes"):
        assert "." + name in css, f"app.css 缺少样式类 .{name}"


def test_heat_panel_is_backend_driven_and_marks_blank_cells():
    """FR-10 分布热力：数据来自 /cases/distribution；空白格必须有明确视觉与文案。"""
    src = _src()
    assert "heatPanel" in src and "case-heat-table" in src
    assert "/api/v2/cases/distribution" in src, "缺少分布接口调用"
    assert "heat-open" in src and "heat-load" in src, "缺少展开/加载动作"
    assert "case-heat-blank" in src, "空白格必须有独立样式（不能和 0 值格混为一谈）"
    assert "未出现过" in src, "空白格必须有可解释的 title"
    css = _css()
    for name in ("case-heat", "case-heat-table", "case-heat-cell",
                 "case-heat-blank", "case-heat-legend"):
        assert "." + name in css, f"app.css 缺少样式类 .{name}"


def test_gap_registration_entry_and_panel():
    """FR-09 登记入口：问答侧引导 + 治理视图表单/裁决；批准不写词表的纪律要写上界面。"""
    src = _src()
    assert "gapEntry" in src and 'data-act="gap-open"' in src
    assert "gapPanel" in src and "case-gap-form" in src
    assert "/api/v2/cases/ontology/gap" in src
    assert 'on("#gap-submit"' in src, "表单提交未绑定"
    assert "gap-approve" in src and "gap-reject" in src, "缺少裁决动作"
    assert "驳回必须写明理由" in src, "驳回理由的前端硬约束缺失"
    assert "系统不自动写入词表" in src, "『批准≠写词表』的纪律必须在界面上写清楚"
    css = _css()
    for name in ("case-gap", "case-gap-entry", "case-gap-row", "case-gap-form",
                 "case-gap-cols", "case-gap-vocab"):
        assert "." + name in css, f"app.css 缺少样式类 .{name}"


def test_case_css_uses_tokens_not_hardcoded_light_colors():
    """新样式只用设计令牌（深浅主题 + 主题色都要随动，不写死浅色 hex）。"""
    css = _css()
    tail = css[css.index("证据链 2.0"):]
    for token in ("var(--card)", "var(--border)", "var(--text-2)", "var(--primary-rgb)"):
        assert token in tail, f"新样式未使用令牌 {token}"
    assert "#fff" not in tail.replace("#ffffff", ""), "新样式不得写死白色（亮/暗主题会违和）"
