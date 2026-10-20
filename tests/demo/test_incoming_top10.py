"""来料质量「TOP10」—— 前端渲染契约测试。

2026-10-21 产品调整：品质看板的来料质量区从「全量供应商排序表」改为
**L1 只出 TOP10**——左「来料批次合格率 · TOP10」、右「来料上线不良率 · TOP10」，
两者均为**固定全量口径**，不受统计口径（日/周/月）与工段 / 类别 / 柏拉图筛选影响；
全量供应商改到 2 级看板看。

为什么单独守：这是**前端分流渲染**，后端断言只能证明数据给全了，
顶不住「前端又按全量表画回去 / 又混进 TOP5 网格」这类回归。
"""
from pathlib import Path

UI_DIR = (Path(__file__).resolve().parents[2]
          / "src" / "mfg_agents" / "v2" / "demo" / "ui")


def _read(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")


def _fn_block(src: str, header: str) -> str:
    body = src[src.index(header):]
    return body[:body.index("\n  }") + 4]


def test_quality_body_renders_incoming_top10_only():
    """L1 来料质量只出 TOP10：渲染 ``incoming_top10``，且不再渲染全量供应商排序表。"""
    body = _fn_block(_read("screens.js"), "function mfgQualityBody")
    assert "d.incoming_top10" in body, "未读取 incoming_top10"
    assert "mfgIncomingTopCard" in body, "未渲染来料 TOP10 卡"
    assert "mfgSupplierRankCard" not in body, "L1 不应再渲染全量供应商排序表"
    assert 'view === "supplier_table"' not in body, "supplier_table 分流已废弃"
    # 已并入 TOP10 的配套榜（如来料批次合格率）必须跳过，避免同一指标出两张卡
    assert 'r.view !== "merged_into_top10"' in body, "未过滤已并入 TOP10 的配套榜"
    assert "mfgRankCard" in body, "其余指标仍应走 TOP5 卡"


def test_incoming_top_card_is_top10_and_drillable():
    """TOP10 卡：标题带 · TOP10；卡主体可下钻 2 级（全量供应商），行可下钻 3 级明细。"""
    card = _fn_block(_read("screens.js"), "function mfgIncomingTopCard")
    assert "· TOP10" in card
    # 极性随指标方向翻转：合格率越高越好 → 取最低 10；不良率越低越好 → 取最高 10
    assert "越高越好 · 取最低 10" in card and "越低越好 · 取最高 10" in card
    assert "data-kpi" in card and "data-drill-entity" in card
    assert "全量供应商" in card, "应说明 2 级看板是全量供应商"
    assert "不受统计口径" in card, "应标注固定全量口径、不受筛选影响"


def test_supplier_rank_table_shape_is_gone():
    """全量排序表相关实现（函数 / 限高滚动容器类）必须一并移除，避免死代码回流。"""
    src = _read("screens.js")
    assert "function mfgSupplierRankCard" not in src
    assert 'class="drill-scroll rank-scroll"' not in src
    assert ".drill-scroll.rank-scroll" not in _read("app.css")
