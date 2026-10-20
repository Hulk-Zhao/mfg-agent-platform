# -*- coding: utf-8 -*-
"""V4.1 决策支持型问数（digest）与路由复核 · 回归测试。

覆盖用户反馈的"牛头不对马嘴"问题：开放式提问「本月度最需关注什么？」此前
落 chitchat（只回一句问候）或被隐式指派到某个场景跑重型 DAG（客诉 8D + 双 HITL）。
本套测试锁定修复后的行为：
1. digest 意图识别（无实体 → 汇总；有实体 → 按对象级链路）；
2. 汇总内容有数据支撑（跨域未达标 KPI、口径随提问、权限同源、只读无工单）；
3. L1 判域外但带具体对象时不再只回问候（复核为数据直查）；
4. 降级路径不再隐式指派场景（无触发词 → qa）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.core.router import TwoLevelRouter
from mfg_agents.v2.services.digest import build_digest, pick_window


@pytest.fixture(scope="module")
def router() -> TwoLevelRouter:
    from mfg_agents.common.config import settings
    from mfg_agents.v2.contracts.scenario import ScenarioRegistry
    reg = ScenarioRegistry(settings.V2_SCENARIOS_DIR)
    reg.load()
    return TwoLevelRouter(reg)


UC_GM = ["gm"]
UC_MC = ["material_control"]


# ---------------------------------------------------------------- 意图识别

@pytest.mark.parametrize("query", [
    "本月度最需关注什么？",
    "整体情况怎么样？",
    "最近有什么风险？",
    "今天有什么需要我关注的？",
    "本月重点是什么？",
    "帮我汇总一下本月的整体情况",
])
def test_digest_intent_hit(router, query):
    """开放式态势汇总问句 → digest（不再落 chitchat）。"""
    res = router.route(query)
    assert res.strategy == "digest", f"{query} → {res.strategy}（{res.reason}）"
    assert res.reason.startswith("开放式态势汇总意图命中")
    assert res.level == "L0"


@pytest.mark.parametrize("query", [
    "A型号本月情况如何？",
    "SMT-1本月产出情况如何？",
    "BATCH-A-1008 本月检验结论汇总",
    "客户A本月的订单交付情况汇总",
])
def test_digest_entity_guard(router, query):
    """带具体对象（型号/线体/批号/客户）的问题**不得**被判为汇总——必须走对象级链路。

    这正是首版实现失效的原因：V1 实体抽取在抽不到时给默认值（A型号/SMT-1），
    据此判"有实体"会把所有开放式提问都误判成对象级问题。
    """
    assert router.route(query).strategy != "digest"


def test_digest_no_entity_on_plain_query(router):
    """纯开放式提问必须"无实体"（判据只看问题原文）。"""
    assert router._has_entity("本月度最需关注什么？") is False
    assert router._has_entity("A型号本月情况如何？") is True
    assert router._has_entity("SMT-1 产出如何") is True


# ---------------------------------------------------------------- 汇总内容

def _digest(roles, query="本月度最需关注什么？"):
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    orch = create_default_orchestrator()
    return orch, build_digest(orch.runtime.data_registry, orch.runtime.workflow,
                              roles, window=pick_window(query), query=query)


def test_digest_window_follows_question():
    """口径随提问：本月→month / 今天→day / 其余→window。"""
    assert pick_window("本月度最需关注什么？") == "month"
    assert pick_window("今天有什么需要我关注的？") == "day"
    assert pick_window("最近有什么风险？") == "window"


def test_digest_is_data_backed():
    """汇总有数据支撑：跨域未达标 KPI（含看板名/值/目标/缺口），不是编的。"""
    _, data = _digest(UC_GM, "本月度最需关注什么？")
    assert data is not None
    assert data["boards"], "应覆盖至少一个看板"
    assert data["window_key"] == "month"
    assert data["window"].get("label") == "当月"
    assert data["total_not_ok"] >= 1
    top = data["items"][0]
    assert {"board", "label", "value", "target", "status"} <= set(top)
    assert top["status"] in ("warn", "danger")


def test_digest_permission_same_as_boards():
    """权限与看板同源（fail-closed）：无权屏不参与汇总，并如实标注"不可见"。"""
    _, gm = _digest(UC_GM)
    assert "订单履约" in gm["boards"] and not gm["skipped"]
    _, mc = _digest(UC_MC)   # 物控无品质/制造/SPC 看板权限
    assert "品质" in mc["skipped"]
    assert "品质" not in mc["boards"]


def test_digest_readonly_no_ticket():
    """digest 只读：不产生工单、不发起 HITL。"""
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    orch = create_default_orchestrator()
    result = orch.handle("本月度最需关注什么？",
                         user_ctx={"user_id": "u-gm", "dept": "management",
                                   "roles": ["gm"]})
    d = result.to_dict()
    assert d["route"]["strategy"] == "digest"
    assert d["hitl"] == [] and d["workflow_tasks"] == []
    assert d["scenario_key"] is None
    # 回答里必须有"未达标"清单与口径说明，而非问候语
    assert "未达标" in d["report"] or "均已达标" in d["report"]
    assert "最近7天" in d["report"] or "当月" in d["report"]


def test_digest_no_visible_board_falls_back():
    """无任何可见看板的岗位：不臆造态势，如实说明并回落（不返回空话术）。"""
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    orch = create_default_orchestrator()
    data = build_digest(orch.runtime.data_registry, orch.runtime.workflow,
                        ["hr_admin"] if False else ["admin_hr"], window="window")
    assert data is None or data["boards"] == []


# ---------------------------------------------------------------- L1 复核与降级

def test_l1_entity_not_chitchat(router, monkeypatch):
    """L1 判域外但带具体对象 → 复核为数据直查（不再只回问候）。

    直接测 ``_route_l1``：本轮改的是"L1 判域外后的复核"，L0 场景触发词命中与否
    是另一层（且依赖注册表配置），不应混进来干扰这条断言的归因。
    """
    from mfg_agents.v2.intent.classifier import IntentResult

    def _fake(_self, _query):
        return IntentResult(domain=None, confidence=0.9,
                            reason="语义匹配：超出平台问答范围",
                            low_confidence=False)
    monkeypatch.setattr(router.intent_classifier.__class__, "classify", _fake)
    res = router._route_l1("A型号本月情况如何？")
    assert res.strategy == "qa"
    assert res.low_confidence is True
    assert "含具体对象" in res.reason


def test_l1_pure_chitchat_unchanged(router, monkeypatch):
    """真闲聊（无对象无信号）仍轻回应——复核不放宽纯闲聊。"""
    from mfg_agents.v2.intent.classifier import IntentResult

    def _fake(_self, _query):
        return IntentResult(domain=None, confidence=0.9,
                            reason="语义匹配：超出平台问答范围",
                            low_confidence=False)
    monkeypatch.setattr(router.intent_classifier.__class__, "classify", _fake)
    assert router._route_l1("你会做饭吗").strategy == "chitchat"


def test_degraded_no_implicit_scenario(router):
    """LLM 不可用 + 域关键词命中但**无场景触发词** → qa，不得隐式指派场景。

    这是"泛化提问跑了客诉 8D + 双 HITL"的根源之一：此前降级路径无条件取
    `_scenario_for_domain` 的返回值（无触发词时它退化为该域第一个场景）。
    """
    res = router._route_degraded("OEE 表现怎么样")
    assert res.strategy == "qa"
    assert res.scenario_key is None
    assert res.domain == "manufacturing"


# ---------------------------------------------------------------- 前端契约

def test_frontend_digest_card_contract():
    """前端 digest 卡片链路存在（防回退丢渲染分支）。"""
    from pathlib import Path
    ui = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"
    screens = (ui / "screens.js").read_text(encoding="utf-8")
    css = (ui / "app.css").read_text(encoding="utf-8")
    assert 'strategy === "digest"' in screens
    assert "digestCardHtml" in screens
    assert "digest-card" in css and "digest-badge" in css


def test_frontend_quick_questions_showcase_verified_paths():
    """引导问题必须指向**已实测能命中亮点链路**的问法（在线/离线两套）。

    纪律：引导问题点了答不上来（或落到无对应链路的问法）就看不出版本能力，
    旧版"排产/排程/设备参数在哪"就是这个问题；这里把两套清单锁住——
    ① 在线用亮点问法；② 离线只能用有预置回放的 5 条（demo-data.CHAT_DEMOS）。
    """
    from pathlib import Path
    ui = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"
    screens = (ui / "screens.js").read_text(encoding="utf-8")
    demo = (ui / "demo-data.js").read_text(encoding="utf-8")

    # 在线亮点清单：跨域态势汇总 + SPC 过程能力 + 协同场景闭环 + 指标口径 + 跨域取证
    for q in ("本月度最需关注什么？", "回流焊峰值温度的过程能力 Cpk 是否越限？",
              "A型号在SMT-1停线，何时复线？", "最近7天 OTD 准时交付率是多少？"):
        assert f'"{q}"' in screens, f"在线引导问题缺少：{q}"
    # 离线清单：每条都必须能在 CHAT_DEMOS 里找到预置回放（否则离线点了报"未收录"）
    offline = screens.split("const DEMO_QUICK_QUESTIONS = [", 1)[1].split("];", 1)[0]
    keys = [ln.strip().strip('",') for ln in offline.splitlines()
            if ln.strip().startswith('"')]
    assert len(keys) == 5, f"离线引导问题应保持 5 条（回放覆盖），实为 {len(keys)}"
    for q in keys:
        assert f'"{q}":' in demo, f"离线引导问题缺少预置回放：{q}"
    # 渲染时按在线/离线切换（不能写死成一套）
    assert "ctx.api.online() ? QUICK_QUESTIONS : DEMO_QUICK_QUESTIONS" in screens
