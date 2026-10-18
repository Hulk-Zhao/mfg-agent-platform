"""kbqa 意图路由（DESIGN-2）离线校验：路由 / 直答契约 / 缓存 / 端点。

不连 Milvus / 不连 Redis / 不调真 LLM（``get_llm`` 与 ``llm_available`` 均
monkeypatch）。DB 用 tmp SQLite 文件库——DDL 从 ``data/seed_v3.sql`` 现抽，
引擎经 ``settings.DB_URL`` + ``kbqa_store.reset_engine()`` 指向它。

| 组 | 钉什么 |
| --- | --- |
| M | 路由纯函数：域内→RAG / 域外→direct / 退化输入保守 / 拉丁词边界 / 长词优先 / 词表快照 / 零外部调用 |
| D | direct 契约：形状 / 不调 search_kb / 总开关回落 RAG / LLM 挂诚实降级 / 走缓存 / 降级不缓存 |
| A | API：域外→direct / 域内→RAG（端点层路由生效） |

**缓存纪律**：本文件所有用例一律经 ``no_redis_cache`` 隔离——2026-10-18
实测：不隔离时用例会读到 6380 Redis 上旧架构遗留的同键条目（channel=miss），
造成假绿。路由/契约类用例的结果不允许取决于外部缓存状态。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from mfg_agents.common.config import settings
from mfg_agents.v2.core import cache
from mfg_agents.v2.services import kbqa as kbqa_service
from mfg_agents.v2.services import kbqa_intent
from mfg_agents.v2.services import kbqa_store

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _boom(*args, **kwargs):
    raise AssertionError("direct 路径不应调用 search_kb")


# ----------------------------------------------------------------  fixtures


def _kbqa_ddl() -> list[str]:
    """从 seed_v3.sql 抽 kbqa_policy + 三张新表 DDL（守护种子文件含表）。"""
    sql = (_REPO_ROOT / "data" / "seed_v3.sql").read_text(encoding="utf-8")
    stmts = [s for s in sql.split(";")
             if "CREATE TABLE" in s.upper()
             and ("kbqa_policy" in s or "kbqa_faq" in s or "kbqa_query_log" in s)]
    assert len(stmts) >= 4, \
        "seed_v3.sql 缺少 kbqa_policy/kbqa_faq/kbqa_faq_draft/kbqa_query_log DDL"
    return stmts


@pytest.fixture()
def kbqa_db(tmp_path, monkeypatch):
    """tmp SQLite 库：kbqa_policy + 三张新表 + 一条年假政策行；引擎指向它。"""
    db = tmp_path / "kbqa_test.db"
    conn = sqlite3.connect(db)
    for stmt in _kbqa_ddl():
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO kbqa_policy (id, dept, dept_cn, policy_no, category, "
        "title, content, keywords) VALUES (1,'policy_attendance','考勤制度',"
        "'QP-HR-003','年假','年假与调休','入职满 1 年享 5 天年假，满 10 年享 10 天。',"
        "'年假,休假,调休')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "DB_URL", f"sqlite:///{db}")
    kbqa_store.reset_engine()
    yield db
    kbqa_store.reset_engine()


@pytest.fixture()
def no_redis_cache(monkeypatch):
    """缓存直通（测试不依赖 Redis 是否配置 / 有无遗留条目）。"""
    monkeypatch.setattr(cache, "get_or_compute",
                        lambda key, ttl, producer: producer())
    monkeypatch.setattr(cache, "cache_delete", lambda key: None)


class _FakeResp:
    def __init__(self, text: str):
        self.content = text


class _FakeLLM:
    """记录调用次数的假 LLM（thinking-disabled 直答路径用）。"""

    def __init__(self, text: str = "这是助手直答内容。"):
        self.text = text
        self.calls = 0

    def invoke(self, msgs):
        self.calls += 1
        return _FakeResp(self.text)


@pytest.fixture()
def fake_llm(monkeypatch):
    """patch get_llm/llm_available → 假 LLM；返回实例供断言调用次数。"""
    llm = _FakeLLM()
    monkeypatch.setattr(kbqa_service, "llm_available", lambda: True)
    monkeypatch.setattr(kbqa_service, "get_llm",
                        lambda **kw: llm)
    return llm


@pytest.fixture()
def client(monkeypatch, kbqa_db, no_redis_cache, fake_llm):
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


# ---------------------------------------------------------------- M：路由纯函数

#: 域内典型问法（覆盖各制度域 + 拉丁缩写 + 口语说法）
_DOMAIN_QUERIES = [
    "年假怎么休", "报销需要什么发票", "VPN 连不上", "MES系统怎么用",
    "首件检验要求", "8D报告怎么写", "危化品存放规定", "食堂饭卡丢了",
    "打卡迟到了怎么办", "出差住宿标准", "转正流程", "门禁卡失灵",
    "薪资几号发", "保密协议签不签", "车间温湿度记录保存多久",
]

#: 域外典型问法（天气/写作/闲聊/百科/技术百科）
_OUT_QUERIES = [
    "今天天气怎么样", "帮我写一首诗", "你好", "在吗", "现在几点了",
    "美国大选谁赢了", "推荐个减肥计划", "python怎么安装", "翻译成英文",
]


def test_m_domain_queries_route_to_rag():
    for q in _DOMAIN_QUERIES:
        d = kbqa_intent.route(q)
        assert d.domain, f"域内问法误判直答: {q!r}（matched={d.matched!r}）"


def test_m_out_of_domain_queries_route_to_direct():
    for q in _OUT_QUERIES:
        d = kbqa_intent.route(q)
        assert not d.domain, f"域外问法误判 RAG: {q!r}（matched={d.matched!r}）"


def test_m_degenerate_input_conservative_to_rag():
    """空/纯标点（含全角 CJK 标点，normalize 不转换它们）→ 保守走 RAG。"""
    for q in ["", "   ", "。。。", "？？？", "《》", "——", "!?!?"]:
        assert kbqa_intent.route(q).domain, f"退化输入应保守: {q!r}"


def test_m_latin_abbrev_word_boundary():
    """拉丁缩写词边界匹配：防 "MES" 命中 "TIMES" 类；独立出现则命中。"""
    assert kbqa_intent.route("TIMES 是什么意思").domain is False
    assert kbqa_intent.route("mes 系统怎么用").domain is True
    assert kbqa_intent.route("CPK 怎么算").domain is True
    assert kbqa_intent.route("SPC 分析").domain is True


def test_m_longest_word_reported_first():
    """长词优先上报（运维日志/词表校准要看具体命中词）。"""
    d = kbqa_intent.route("账号安全策略是什么")
    assert d.matched == "账号安全"


def test_m_lexicon_snapshot():
    """词表快照：防手滑删词（漏判 = 制度问题拿到无出处回答 = 质量事故）。"""
    sentinels = ["年假", "报销", "考勤", "危化品", "首件", "涉密",
                 "打卡", "出差", "薪资", "车间", "制度"]
    words = kbqa_intent._DOMAIN_WORDS
    for w in sentinels:
        assert w in words, f"词表缺少哨兵词: {w}"
    assert len(words) >= 115, "词表规模异常缩小，确认是否有意裁剪"


def test_m_route_is_pure_and_fast(monkeypatch):
    """路由零外部调用（search_kb/get_llm 装了炸弹也不炸）+ sub-ms 级。"""
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    monkeypatch.setattr(kbqa_service, "get_llm", _boom)
    monkeypatch.setattr(kbqa_service, "llm_available", _boom)
    kbqa_intent.route("年假怎么休")
    kbqa_intent.route("今天天气怎么样")
    t0 = time.perf_counter()
    for _ in range(1000):
        kbqa_intent.route("报销需要什么发票")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 100, f"路由退化到 {elapsed_ms:.1f}ms/1000 次"


# ---------------------------------------------------------------- D：直答契约


def test_d_direct_contract_shape(monkeypatch, kbqa_db, no_redis_cache,
                                 fake_llm):
    """域外问题 → direct 契约：无 sources、无 confidence、channel=direct。"""
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)  # direct 不许检索
    out = kbqa_service.answer("今天天气怎么样", session_id="t-direct")
    assert out["channel"] == kbqa_service.CHANNEL_DIRECT
    assert out["sources"] == [] and out["tags"] == []
    assert out["confidence"] is None
    assert out["degraded"] is False
    assert out["answer"] == "这是助手直答内容。"
    assert out["latency_ms"] >= 0
    assert fake_llm.calls == 1


def test_d_direct_skips_search_kb(monkeypatch, kbqa_db, no_redis_cache,
                                  fake_llm):
    """直答不碰 Milvus——这是省 1-1.5s 的核心，用炸弹守住。"""
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    kbqa_service.answer("帮我写一首诗")
    assert fake_llm.calls == 1


def test_d_disabled_falls_back_to_rag(monkeypatch, kbqa_db, no_redis_cache,
                                      fake_llm):
    """KBQA_DIRECT_ENABLED=false → 逃生阀：域外问题也回落 RAG（迭代一行为）。"""
    monkeypatch.setattr(settings, "KBQA_DIRECT_ENABLED", False)
    seen: list[str] = []
    monkeypatch.setattr(kbqa_service, "search_kb",
                        lambda q, n_results=3: seen.append(q) or [])
    out = kbqa_service.answer("今天天气怎么样")
    assert out["channel"] == "miss"  # 检索无命中 → 与迭代一一致
    assert seen == ["今天天气怎么样"]
    assert fake_llm.calls == 0


def test_d_llm_unavailable_degraded(monkeypatch, kbqa_db, no_redis_cache):
    """LLM 挂 → 诚实降级话术（不编造），channel 仍为 direct。"""
    monkeypatch.setattr(kbqa_service, "llm_available", lambda: False)
    monkeypatch.setattr(kbqa_service, "get_llm", _boom)  # 不许走到构造
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    out = kbqa_service.answer("今天天气怎么样")
    assert out["channel"] == kbqa_service.CHANNEL_DIRECT
    assert out["degraded"] is True
    assert "暂不可用" in out["answer"]


def test_d_degraded_direct_not_cached(monkeypatch, kbqa_db):
    """降级直答绝不缓存 30 分钟——LLM 恢复后要被立刻放行。"""
    deleted: list[str] = []
    monkeypatch.setattr(cache, "get_or_compute",
                        lambda key, ttl, producer: producer())
    monkeypatch.setattr(cache, "cache_delete", lambda key: deleted.append(key))
    monkeypatch.setattr(kbqa_service, "llm_available", lambda: False)
    monkeypatch.setattr(kbqa_service, "get_llm", _boom)
    kbqa_service.answer("今天天气怎么样")
    assert len(deleted) == 1 and deleted[0].startswith("mfg:kbqa:v2:")


def test_d_direct_goes_through_cache(monkeypatch, kbqa_db, fake_llm):
    """同问第二次走缓存回放，不重复调 LLM（direct 也在 L0 覆盖范围）。"""
    store: dict[str, dict] = {}

    def _fake_get_or_compute(key, ttl, producer):
        if key not in store:  # 不能写 setdefault(key, producer())——饿值求值
            store[key] = producer()
        return store[key]

    monkeypatch.setattr(cache, "get_or_compute", _fake_get_or_compute)
    monkeypatch.setattr(cache, "cache_delete", lambda key: store.pop(key, None))
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    first = kbqa_service.answer("今天天气怎么样")
    second = kbqa_service.answer("今天天气怎么样")
    assert first["answer"] == second["answer"]
    assert fake_llm.calls == 1  # 第二次缓存回放，没有新调用


def test_d_domain_query_still_uses_rag(monkeypatch, kbqa_db, no_redis_cache,
                                       fake_llm):
    """回归：域内问法不受路由影响，仍走 RAG 全链路（要出处）。"""
    seen: list[str] = []
    monkeypatch.setattr(kbqa_service, "search_kb",
                        lambda q, n_results=3: seen.append(q) or [])
    out = kbqa_service.answer("报销多久到账")
    assert out["channel"] == "miss"  # 检索空 → miss（不是 direct）
    assert seen == ["报销多久到账"]


# ---------------------------------------------------------------- A：端点层


def test_a_api_out_of_domain_returns_direct(client):
    """端点层：域外问题返回 channel=direct（假 LLM，不联网）。"""
    resp = client.post("/api/v2/kbqa", json={"query": "今天天气怎么样",
                                             "session_id": "a-1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["channel"] == "direct"
    assert data["sources"] == []


def test_a_api_domain_query_goes_rag(client, monkeypatch):
    """端点层：域内问题走 RAG（search_kb 被调用，返回 miss）。"""
    seen: list[str] = []
    monkeypatch.setattr(kbqa_service, "search_kb",
                        lambda q, n_results=3: seen.append(q) or [])
    resp = client.post("/api/v2/kbqa", json={"query": "报销多久到账",
                                             "session_id": "a-2"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["channel"] == "miss"
    assert seen == ["报销多久到账"]


def test_a_stats_includes_direct_channel(client, monkeypatch):
    """度量端点：direct 计数随域外提问出现（stats 无需改代码自动多一类）。"""
    monkeypatch.setattr(kbqa_service, "search_kb", lambda q, n_results=3: [])
    client.post("/api/v2/kbqa", json={"query": "帮我写一首诗",
                                      "session_id": "a-3"})
    stats = kbqa_store.channel_stats(days=1)
    assert stats["distribution"].get("direct") == 1, \
        f"stats 未见 direct 计数: {stats}"


# ------------------------------------------------- H：how_to 让位（DESIGN-4）
#
# 背景（Hulk 截图实证）：L1 语料是 48 条 <60 字单事实速查卡，"怎么报销"
# 与"我要报销，怎么操作"都只拿到「30 天时限」。Hulk 拍板"所有怎么X走
# RAG"——L1 命中也让位，由 Milvus 完整文档切片综合回答。

_HOW_TO_QUERIES = ["怎么报销", "我要报销，怎么操作", "年假怎么休",
                   "如何申请 VPN", "首件检验怎样做", "密码怎么修改"]

#: 不含 how_to 的事实类问法（必须保持 L1 0ms 快车道，不许被误伤）
_FACT_QUERIES = ["年假规定", "标准工时", "差旅住宿标准", "报销时效多久",
                 "备用金借支上限"]


def test_h_how_to_flag_detection():
    """how_to 标记：怎么/如何/怎样/咋办 → True；事实类问法 → False。"""
    for q in _HOW_TO_QUERIES:
        assert kbqa_intent.route(q).how_to is True, f"漏判 how_to: {q}"
    for q in _FACT_QUERIES:
        assert kbqa_intent.route(q).how_to is False, f"误判 how_to: {q}"


def test_h_how_to_domain_query_calls_search_kb(kbqa_db, no_redis_cache,
                                               monkeypatch):
    """域内 + how_to → 走 RAG（search_kb 被调用，不再 L1 短路）。"""
    seen: list[str] = []
    monkeypatch.setattr(kbqa_service, "search_kb",
                        lambda q, n_results=3: seen.append(q) or [])
    out = kbqa_service.answer("年假怎么休", session_id="h-1")
    # L1 原本能命中 QP-HR-003，但 how_to 让位 → 必须真的去检索
    assert seen == ["年假怎么休"], f"how_to 未让位 RAG: {seen}"
    assert out["channel"] in ("miss", "rag")


def test_h_how_to_rag_miss_falls_back_to_l1(kbqa_db, no_redis_cache,
                                            monkeypatch):
    """RAG 无命中 + L1 有命中 → 回落 L1 速查卡，但必须说清"未找到完整流程"。

    半份答案也是答案，可不能让用户误以为这就是制度的全部（契约诚实性）。
    """
    monkeypatch.setattr(kbqa_service, "search_kb", lambda q, n_results=3: [])
    out = kbqa_service.answer("年假怎么休", session_id="h-2")
    assert out["channel"] == "miss"
    assert "完整流程" in out["answer"] and "供参考" in out["answer"], \
        f"回落未诚实说明: {out['answer']}"
    assert "入职满 1 年" in out["answer"], "回落未带上 L1 内容"
    assert out["sources"], "回落应保留 L1 出处"


def test_h_out_of_domain_how_to_still_direct(monkeypatch, kbqa_db,
                                             no_redis_cache, fake_llm):
    """域外 + how_to → 仍走 direct，不白跑 Milvus（"怎么写诗"）。"""
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    out = kbqa_service.answer("帮我写一首诗", session_id="h-3")
    assert out["channel"] == "direct", f"域外 how_to 不应走 RAG: {out}"
    assert fake_llm.calls == 1


def test_h_fact_query_still_l1_zero_hop(kbqa_db, no_redis_cache, monkeypatch):
    """事实类问法（不含怎么）保持 L1 快车道——how_to 不许误伤。"""
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    out = kbqa_service.answer("年假规定", session_id="h-4")
    assert out["channel"] == "faq", f"事实类问法被误伤成非 L1: {out}"
