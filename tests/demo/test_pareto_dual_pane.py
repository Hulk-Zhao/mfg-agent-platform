"""品质看板柏拉图：月别 / 日别 双面板 + 产品分类下拉 —— 静态契约测试。

为什么要单独守：这两块面板是**纯前端本地聚合**（零网络往返），后端断言顶不住
「有人把粒度切换加回来」「期间下拉复活」「产品分类下拉被删」这类回归。
"""
from pathlib import Path

import re

UI_DIR = (Path(__file__).resolve().parents[2]
          / "src" / "mfg_agents" / "v2" / "demo" / "ui")


def _read(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")


def test_pareto_card_splits_into_month_and_day_panes():
    """左块月别（当月累计）/ 右块日别（当日）：两块写死，不再有粒度切换。"""
    src = _read("screens.js")
    assert 'paneHtml("month", "月别 · 当月"' in src, "左块应为月别面板"
    assert 'paneHtml("day", "日别 · 当日"' in src, "右块应为日别面板"
    assert 'class="pareto-dual"' in src, "两块应并排于 .pareto-dual 容器"


def test_pareto_granularity_and_period_controls_removed():
    """「粒度」切换与「期间」下拉必须已移除（双面板 + 固定期间）。"""
    src = _read("screens.js")
    assert "data-p-gran" not in src, "粒度切换控件残留"
    assert "data-p-period" not in src, "期间下拉控件残留"


def test_pareto_product_category_select_and_drill_attrs():
    """产品分类下拉 + 行下钻带本块期间与三项筛选。"""
    src = _read("screens.js")
    assert "data-p-pcat" in src, "缺少产品分类下拉"
    assert "prod_cats" in src, "下拉选项应取后端事实表的 prod_cats"
    assert "data-drill-prod-cat" in src, "行未携带产品分类筛选"
    assert "data-drill-window=" in src, "行未携带本块期间（月别/日别）"
    js = _read("app.js")
    assert "prodCat)" in js and "prod_category=" in js, "app.js 未贯通产品分类参数"


def test_pareto_pcat_select_writes_back_selection():
    """重绘后必须**显式回写** <select> 的选中值。

    真实报障：选了产品分类后，数据变了但下拉框显示回「全部」——
    只靠 <option selected> 属性不可靠，必须在重绘后写 sel.value，
    并把状态里已不在选项集的值收敛回「全部」。
    """
    src = _read("screens.js")
    assert 'sel.value = st.pcat || ""' in src, "缺少下拉框选中值显式回写"
    assert 'pcats.indexOf(st.pcat) < 0) st.pcat = ""' in src, "缺少失效值收敛"
    assert 'pcats.indexOf(sel.value) >= 0 ? sel.value : ""' in src, "change 未校验取值"


def test_pareto_defect_category_filter_moved_to_bottom():
    """性能不良 / 外观不良（缺陷类别）移到柏拉图**底部**，只作本柏拉图的筛选。

    品质卡片的 ``const bottomFilter`` 现在按 ``cats`` 是否为空决定渲不渲染（制造停机 / 报废
    原因柏拉图没有缺陷类别维度）——但**缺陷类别 chips 仍只出现在底部筛选条内**，不上顶部工具条。
    """
    src = _read("screens.js")
    at = src.index("const bottomFilter =")
    seg = src[at:at + 700]
    assert "pareto-bottom-filter" in seg, "底部筛选条容器丢失"
    assert "data-p-cat" in seg, "缺陷类别 chips 未落在底部筛选条内"
    assert "data-p-cat" not in src[:at], "缺陷类别 chips 仍留在顶部工具条"
    # 顶部工具条只保留 产品分类 + 工段
    top = src[src.index('const toolbar = `<div class="pareto-toolbar">'):src.index("const bottomFilter")]
    assert "data-p-pcat" in top and "data-p-stage" in top
    css = _read("app.css")
    assert ".pareto-bottom-filter" in css


def test_quality_board_three_bodies_order():
    """品质看板三个主体顺序：①核心指标+TOP5 ②标准三率 ③不良累计柏拉图。"""
    src = _read("screens.js")
    body = src[src.index("function mfgQualityBody"):]
    body = body[:body.index("\n  }") + 4]
    i_metrics = body.index('data-mfg-group="metrics"')
    i_std = body.index('data-mfg-group="standard"')
    i_pareto = body.index('data-mfg-group="pareto"')
    assert i_metrics < i_std < i_pareto, "三大主体顺序不是 指标/TOP5 → 三率 → 柏拉图"
    assert "mfgStdThreeRates" in body and "rankings" in body
    assert _read("app.css").count(".mfg-group") >= 1


def test_pareto_threshold_label_drawn_above_bars():
    """「关键少数」标签必须绘制在柱体之后（此前先画被高柱遮死），且带药丸底。"""
    src = _read("screens.js")
    chart = src[src.index("function mfgParetoChart"):]
    chart = chart[:chart.index("\n  }")]
    i_bars = chart.index("${bars}")
    i_thr = chart.index("${thr}")
    assert i_bars < i_thr, "阈值标签仍绘制在柱体之前，会被高柱遮挡"
    assert "pthr-pill" in chart, "标签缺少药丸底（无法在高柱上读清）"
    assert "pthr-pill" in _read("app.css")


def test_pareto_dual_layout_css_present():
    """双面板布局样式（仅新增类，且禁用 color-mix —— 旧内核整条声明会失效）。"""
    css = _read("app.css")
    assert ".pareto-dual" in css and ".pareto-pane-head" in css
    assert "color-mix(" not in css


def test_offline_sample_fact_matches_new_contract():
    """离线样例必须与线上同构：带 pc / prod_cats，且含当日 2026-10-15。"""
    src = _read("demo-data.js")
    assert '"prod_cats": ["消费电子", "智能电源", "储能电源", "车载"]' in src
    assert '"pc": "' in src
    assert '"d": "2026-10-15"' in src, "样例缺当日数据 → 日别面板会空"


# ============================================================
# 板级「工段 / 缺陷类别」筛选条（4 张 KPI 卡 = 所选范围汇总）
# ============================================================


def test_quality_scope_chips_and_options():
    """两个 Tab 各自都有板级筛选条：品质 = 工段 + 类别；制造 = 产品分类下拉 + 工段。"""
    src = _read("screens.js")
    assert 'const QUALITY_STAGE_CHIPS = ["SMT", "PCM", "PACK", "整机"]' in src
    assert 'const QUALITY_CAT_CHIPS = [["性能不良", "性能"], ["外观不良", "外观"]]' in src
    assert 'const MFG_STAGE_CHIPS = ["SMT", "PCM", "PACK", "整机"]' in src
    assert 'const MFG_PCAT_OPTIONS = ["消费电子", "智能电源", "储能电源", "车载"]' in src
    assert "isQ ? mfgScopeChips(UI, st, fs) : mfgMfgScopeChips(UI, st, fs)" in src, \
        "品质筛选条与制造筛选条必须按 Tab 分流渲染，且按当前下钻目标的生效范围裁剪"


def test_quality_scope_chips_wired_and_keep_drill_level():
    """筛选条点击 → 写 screenState；**下钻态下保持当前层级**（把筛选带进下钻对象就地重取）。"""
    src = _read("screens.js")
    assert '#mfg-windows [data-stage]' in src and '#mfg-windows [data-cat]' in src
    assert "st.mfgStage = btn.dataset.stage" in src
    assert "st.mfgCat = btn.dataset.cat" in src
    assert src.count("st.boardDrill = null;   // 切换 Tab 复位下钻层级") >= 1


def test_quality_scope_passed_to_api_and_summary_shown():
    """加载链路带上 scope；汇总口径文案与 TOP5 问数文案同步带上范围。"""
    src = _read("screens.js")
    assert "ctx.api.qualityDashboard(win, stage, category)" in src
    assert "汇总口径：" in src and "const scopeCn = mfgScopeCn(d.filter)" in src
    assert '"quality", d.window && d.window.label, scopeCn' in src, "TOP5 问数未带筛选范围"
    assert '<div class="stat-scope">' in src, "卡片未渲染范围标注"
    js = _read("app.js")
    assert "qualityDashboard(window, stage, category)" in js
    assert "&stage=${encodeURIComponent(stage)}" in js
    assert "&category=${encodeURIComponent(category)}" in js


def test_quality_scope_note_css_present():
    """范围标注样式：仅新增类，仍禁用 color-mix（旧内核整条声明会失效）。"""
    css = _read("app.css")
    assert ".stat-scope" in css
    assert "color-mix(" not in css


# ============================================================
# 制造看板板级「产品分类 / 工段」筛选 + 2 级看板详细数据
# ============================================================


def test_mfg_scope_chips_wired_and_keep_drill_level():
    """制造筛选条点击/选择 → 写 screenState；下钻态下把筛选带进下钻对象，不退回 1 级。"""
    src = _read("screens.js")
    assert '#mfg-windows [data-pcat]' in src and '#mfg-windows [data-mstage]' in src
    assert "st.mfgProdCat = pcatSel.value" in src
    assert "st.mfgMStage = btn.dataset.mstage" in src
    # 下拉用 change 事件（select 语义），胶囊用 click
    assert 'pcatSel.addEventListener("change"' in src


def test_mfg_scope_passed_to_api_and_drill():
    """加载链路带上制造筛选；下钻时与板级筛选同口径（否则卡片说 SMT、下钻是全厂）。"""
    src = _read("screens.js")
    assert "ctx.api.manufacturingDashboard(win, stage, pcat)" in src
    assert 'drill.screen === "manufacturing" ? (ss.mfgMStage || "")' in src
    assert 'drill.screen === "manufacturing" ? (ss.mfgProdCat || "")' in src
    assert "const scopeCn = mfgMfgScopeCn(d.filter)" in src
    js = _read("app.js")
    assert "manufacturingDashboard(window, stage, prodCat)" in js
    assert "&product_category=${encodeURIComponent(prodCat)}" in js
    # 离线演示数据无法按筛选重算 → 必须如实标注，不许把全厂值当筛选值
    assert "离线演示数据不支持板级筛选" in src


def test_l2_detail_blocks_rendered_only_when_backend_supplies():
    """2 级明细页：整体汇总 hero + 覆盖/达标条 + 整体逐日 + 关联原始记录，
    且三块都以后端下发字段为准（离线演示数据缺省即不渲染，绝不编造）。"""
    src = _read("screens.js")
    body = src[src.index("function mfgDrillL2Body"):]
    body = body[:body.index("\n  }") + 4]
    assert "const s = d.summary || null;" in body
    assert '${d.label} · 整体汇总' in body
    assert "覆盖${UI.esc(d.dimension)}" in body
    assert "整体逐日明细" in body and "mfgDailyCard(UI, d," in body
    assert 'mfgRecordCard(UI, d, "筛选范围内' in body
    assert "return hero + summaryStrip +" in body, "三块补充数据须渲染在明细表之前/之后同序"


def test_manufacturing_scope_chips_use_select_not_pcat_chips():
    """产品分类必须是下拉（图1 形态），工段才是胶囊组；「全部」默认选中 = 不改既有口径。"""
    src = _read("screens.js")
    fn = src[src.index("function mfgMfgScopeChips"):]
    fn = fn[:fn.index("\n  }") + 4]
    assert 'class="chip-select" data-pcat' in fn, "产品分类未用下拉"
    assert '<option value="">全部</option>' in fn, "缺「全部」项（默认不应改口径）"
    assert 'data-mstage=' in fn, "工段未用胶囊按钮"
    assert "MFG_PCAT_OPTIONS.map" in fn and "MFG_STAGE_CHIPS.map" in fn


def test_chip_select_css_present():
    """下拉样式与 .chip 同族（胶囊 + 描边），且仍禁用 color-mix。"""
    css = _read("app.css")
    assert ".chip-select" in css and ".chip-select:hover" in css
    assert "border-radius: 999px" in css
    assert "color-mix(" not in css


# ============================================================
# 2 级看板筛选条：只保留该模块真正生效的项 + 点筛选不再退回 1 级
# ============================================================


def _drill_filter_scope_table() -> dict:
    """从 screens.js 解析 DRILL_FILTER_SCOPE 常量（"screen:kpi" → 生效字段集）。"""
    src = _read("screens.js")
    block = src[src.index("const DRILL_FILTER_SCOPE = {"):]
    block = block[:block.index("\n  };")]
    out = {}
    for m in re.finditer(r'"([a-z_]+:[a-z_0-9]+)":\s*\{([^}]*)\}', block):
        out[m.group(1)] = set(re.findall(r"(\w+):\s*true", m.group(2)))
    return out


def test_drill_filter_scope_table_matches_backend_declarations():
    """前端筛选条生效表必须与后端 DRILL_DEFS 声明逐项一致（防两边漂移）。

    比对范围 = 制造综合看板两个 Tab（quality / manufacturing）**与 SPC 过程能力
    看板（spc）**的全部下钻指标：
    - ``window``：有 date_expr 且非 ``fixed_range`` → 跟随顶部「统计口径」；
    - ``stage`` / ``product_category``：声明了 ``scope``（制造四指标）；
    - ``stage`` / ``category``：声明了 ``q_scope``（品质不良率 / 直通率）；
    - ``stage`` / ``product_category``：声明了 ``spc_scope``（SPC 能力 / 柏拉图，
      与 L1 卡片同源；其 ``pcat_col`` 同样映射到 ``product_category``）。
    订单履约等屏的筛选条是各自独立的行，不在这张表的消费方范围内。
    """
    from mfg_agents.v2.services.dashboard import DRILL_DEFS
    known = {f'{d["screen"]}:{k}' for k, d in DRILL_DEFS.items()}
    table = _drill_filter_scope_table()
    expected = {}
    for kpi, defn in DRILL_DEFS.items():
        if defn["screen"] not in ("quality", "manufacturing", "spc"):
            continue
        sc, qs, sp = (defn.get("scope") or {}, defn.get("q_scope") or {},
                      defn.get("spc_scope") or {})
        keys = set()
        if defn.get("date_expr") and not defn.get("fixed_range"):
            keys.add("window")
        if sc.get("stage_col") or qs.get("stage_col") or sp.get("stage_col"):
            keys.add("stage")
        if sc.get("pcat_col") or sp.get("pcat_col"):
            keys.add("product_category")
        if qs.get("category_col"):
            keys.add("category")
        if keys:
            expected[f'{defn["screen"]}:{kpi}'] = keys
    assert expected, "后端应有吃板级筛选的下钻指标"
    assert set(table) <= known, f"登记了未知指标：{set(table) - known}"
    assert {k: v for k, v in table.items() if v} == expected, \
        "前端筛选条生效表与后端声明不一致（新增指标须同步登记）"
    # 固定全量口径 / 快照口径：登记为空集（2 级看板整行不渲染）
    for kpi in ("quality:incoming_batch_quality", "quality:incoming_line_defect",
                "quality:complaint_close_rate"):
        assert table.get(kpi) == set(), f"{kpi} 应登记为不吃任何板级筛选"


def test_drill_filter_row_renders_only_effective_parts():
    """筛选条按当前下钻目标的生效范围裁剪；全不生效时整条不渲染（不给假控件）。"""
    src = _read("screens.js")
    assert "function drillFilterScope(drill)" in src
    assert "if (drill.stdStage) return { window: true };" in src, "标准三率 2 级只跟统计口径"
    assert "if (drill.defectType) return {};" in src, "不良类型下钻自带筛选，不算板级筛选"
    assert "const winGroup = fs.window" in src
    assert "const windowRow = (winGroup || scopeGroup)" in src, "筛选条未按生效范围整体收起"
    # 生效范围同时决定「下钻是否跟随板级状态」：只有声明生效的项才读板级筛选
    assert "onBoard && fs.stage ? (ss.mfgStage || \"\")" in src
    assert "onBoard && fs.category ? (ss.mfgCat || \"\")" in src


def test_drill_filter_click_keeps_drill_level():
    """下钻态点筛选不再 st.boardDrill = null（退回 1 级）；改为把筛选带进下钻对象就地重取。"""
    src = _read("screens.js")
    # 品质：工段 / 类别
    for sel, write in (('#mfg-windows [data-stage]', "if (drill) drill.stage = st.mfgStage;"),
                       ('#mfg-windows [data-cat]', "if (drill) drill.category = st.mfgCat;")):
        seg = src[src.index(sel):]
        seg = seg[:seg.index("});")]
        assert write in seg, f"{sel} 未把新筛选带进下钻对象"
        assert "st.boardDrill = null" not in seg, f"{sel} 仍会复位下钻层级（退回看板首页）"
    # 制造：产品分类 / 工段
    seg = src[src.index("const pcatSel = root.querySelector"):]
    seg = seg[:seg.index("ctx.refreshScreen();") + len("ctx.refreshScreen();")]
    assert "if (drill) drill.prodCat = st.mfgProdCat;" in seg
    assert "st.boardDrill = null" not in seg
    seg = src[src.index('#mfg-windows [data-mstage]'):]
    seg = seg[:seg.index("});")]
    assert "if (drill) drill.stage = st.mfgMStage;" in seg
    assert "st.boardDrill = null" not in seg


# ============================================================
# 制造看板 2/3/4 级行 → 明细弹窗（对齐订单 / 班组弹窗模式）
# ============================================================


def _fn_block(src: str, name: str, nxt: str) -> str:
    block = src[src.index("function %s" % name):]
    return block[:block.index("function %s" % nxt)]


def test_mfg_l2_rows_open_detail_modal():
    """制造 2 级明细行改为弹窗（data-mfg-modal=3），且不再被通用下钻绑定抢走。"""
    src = _read("screens.js")
    block = src[src.index("function mfgDrillL2Body"):]
    block = block[:block.index("function mfgDrillL3Body")]
    assert 'd.screen === "manufacturing"' in block, "制造 2 级行未分流到弹窗"
    assert 'data-mfg-modal="3"' in block and "data-mfg-name" in block
    assert "明细弹窗" in block, "行提示未说明弹窗形态"
    bind = src[src.index("function mfgBind"):]
    bind = bind[:bind.index("async function loadMfgDrill")]
    assert "openMfgDrillModal" in bind
    assert ':not([data-mfg-modal])' in bind, "通用下钻绑定未排除弹窗行（会同时触发两次）"


def test_mfg_l3_l4_rows_open_detail_modal():
    """3 级工序行 / 4 级工位行同样带弹窗标记（2→3→4→5 逐级都能弹窗看详情）。"""
    src = _read("screens.js")
    l3 = _fn_block(src, "mfgStageCard", "mfgStationCard")
    assert 'data-mfg-modal="4"' in l3 and 'd.screen === "manufacturing"' in l3
    l4 = _fn_block(src, "mfgStationCard", "mfgShiftCard")
    assert 'data-mfg-modal="5"' in l4 and 'd.screen === "manufacturing"' in l4


def test_mfg_detail_modal_body_and_jump_entry():
    """弹窗正文 = 下一级载荷（工段/工序/工位 + 逐日 + 原始记录），并保留「进入 N 级看板」入口；
    离线降级与 403 如实处理，不编造明细。"""
    src = _read("screens.js")
    fn = _fn_block(src, "openMfgDrillModal", "orderDomainModalBody")
    assert "ctx.api.dashboardDrill" in fn, "弹窗未走同口径下钻接口"
    assert "mfgDrillFallback" in fn, "离线未降级演示数据"
    assert "permission_denied" in fn, "403 未如实拒绝"
    assert "data-mfg-modal-jump" in fn and "进入 ${lvl} 级看板" in fn, "弹窗内缺整页入口"
    assert "{ 3: mfgDrillL3Body, 4: mfgDrillL4Body, 5: mfgDrillL5Body }" in src
    # 弹窗机件复用订单 / 班组弹窗骨架（.std-modal），不另起一套样式
    assert "openOrderModalShell" in fn and "fillOrderModal" in fn
    # 弹窗正文里的工序 / 工位行必须补绑一次（弹窗在 body 下，常规绑定到不了，否则是死按钮）
    assert 'mfgBind(ctx, modalHost, data, "")' in fn
    assert 'document.querySelector(".std-modal")' in fn


# ============================================================
# 制造看板两张「原因累计柏拉图」（停机 / 报废）—— 复用品质双面板机件
# ============================================================


def test_mfg_reason_pareto_cards_configured():
    """制造 L1 出两张原因柏拉图（停机 / 报废），配置与品质同构但无缺陷类别、原因维度不下钻。"""
    src = _read("screens.js")
    body = src[src.index("function mfgManufacturingBody"):src.index("const MFG_STOP_PARETO")]
    assert 'mfgParetoCard(UI, d, "manufacturing", MFG_STOP_PARETO)' in body
    assert "MFG_SCRAP_PARETO" in body
    stop = src[src.index("const MFG_STOP_PARETO"):src.index("const MFG_SCRAP_PARETO")]
    for key in ('factKey: "stop_pareto_fact"', 'hostId: "mfg-stop-pareto-fact"',
                'stateKey: "mfgStopPareto"', "drillable: false", "cats: false"):
        assert key in stop, f"停机原因柏拉图缺配置 {key}"
    scrap = src[src.index("const MFG_SCRAP_PARETO"):]
    for key in ('factKey: "scrap_pareto_fact"', 'hostId: "mfg-scrap-pareto-fact"',
                "drillable: false", "cats: false"):
        assert key in scrap[:900], f"报废原因柏拉图缺配置 {key}"
    # 绑定：制造 Tab 各绑一次（品质只在 quality 绑默认的 pareto_fact）
    load = src[src.index("async function loadMfgDashboard"):]
    load = load[:load.index("async function render_") if "async function render_" in load else len(load)]
    assert "mfgParetoBind(ctx, box, payload, MFG_STOP_PARETO)" in load
    assert "mfgParetoBind(ctx, box, payload, MFG_SCRAP_PARETO)" in load


def test_dead_rows_open_detail_modal():
    """「点了没反应」的数据行补明细弹窗：制造原因柏拉图行 + 品质不良类型（terminal）行。

    守住两件事：①行改开弹窗（不再只有 tooltip）；②弹窗必须被 ``mfgBind`` 绑定，
    否则又是一颗死按钮（弹窗挂在 body 下，常规绑定到不了）。
    """
    src = _read("screens.js")
    # 制造原因柏拉图：不可下钻分支改带 data-p-reason + 切片参数
    fn = _fn_block(src, "mfgParetoBind", "mfgParetoStaticCard")
    assert 'data-p-reason="1"' in fn, "制造原因柏拉图行未开放明细弹窗"
    for key in ("data-p-fact=", "data-p-state=", "data-p-win=", "data-p-ref="):
        assert key in fn, f"原因行缺切片参数 {key}"
    assert "function openMfgReasonModal" in src, "缺少原因明细弹窗构造器"
    # 品质不良类型下钻（terminal）行：带 data-p-term（行数据即载荷本身）
    assert "function openParetoTermModal" in src, "缺少 terminal 行明细弹窗构造器"
    l2 = _fn_block(src, "mfgDrillL2Body", "mfgDrillL3Body")
    assert "data-p-term=" in l2, "terminal 明细行未开放明细弹窗"
    assert "termRow" in l2, "terminal / 脱敏行未分流（脱敏行仍不可点）"
    # 绑定必须落在 mfgBind（否则弹窗是死按钮）
    bind = _fn_block(src, "mfgBind", "loadMfgDrill")
    assert 'querySelectorAll("[data-p-reason]")' in bind, "原因行弹窗未绑定"
    assert 'querySelectorAll("[data-p-term]")' in bind, "terminal 行弹窗未绑定"


def test_pareto_bind_is_parameterized_for_other_facts():
    """交互式柏拉图机件按 ``opt`` 参数化：事实表字段 / 容器 / 状态键 / 损失量列名 / 是否可下钻。"""
    src = _read("screens.js")
    fn = _fn_block(src, "mfgParetoBind", "mfgParetoStaticCard")
    for key in ('o.factKey || "pareto_fact"', 'o.hostId || "mfg-pareto-fact"',
                'o.stateKey || "mfgPareto"', 'o.qtyLabel || "损失量"',
                "o.drillable !== false", 'o.cats === false'):
        assert key in fn, f"机件未参数化 {key}"
    # 原因维度不可下钻：行不带 data-drill-pareto（否则点了没反应 / 报错）
    assert "const rowAttrs = drillable" in fn, "行属性未按可下钻开关分流"
    # 空筛选条不渲染：cats 为空（制造）时不出「缺陷类别」行
    assert 'o.cats === false || !cats.length' in fn
    assert "该筛选条件下暂无${UI.esc(o.emptyWord" in fn


# ============================================================
# 柏拉图图内底部：每根柱标占比
# ============================================================


def test_pareto_chart_labels_bar_shares_below_axis():
    """柱 svg 底部必须逐柱标出占比（取值来自同一份 share，不许另算一套口径）。"""
    src = _read("screens.js")
    assert "const shares = list.map((x, i) => {" in src, "缺少柱下占比标注"
    assert 'class="pshare" text-anchor="middle">${UI.esc(x.share)}%</text>' in src
    # 绘制顺序：标注须在折线/圆点之后，否则会被晚绘制的元素压住
    svg = src[src.index('aria-label="累计不良柏拉图'):]
    assert svg.index("${shares}") > svg.index("${dots}"), "占比标注应在折线与圆点之后绘制"
    # 缺 share 的旧数据不标，避免凭空造数
    assert "if (x.share === undefined || x.share === null) return" in src


def test_pareto_share_label_css_present():
    css = _read("app.css")
    assert ".pshare" in css
    assert "color-mix(" not in css
