"""企业问答助手 FAQ 前置层（ADR-001）离线校验：匹配 / 契约 / 仓库 / 端点 / 度量。

不连 Milvus / 不连 Redis / 不调 LLM。DB 用 tmp SQLite 文件库——DDL 从
``data/seed_v3.sql`` 现抽（顺带守护"种子文件里必须有三张新表"），引擎经
``settings.DB_URL`` + ``kbqa_store.reset_engine()`` 指向它。

| 组 | 钉什么 |
| --- | --- |
| M | FAQMatcher：命中 / 阈值 / margin / 归一化 / 空输入 / 同题直中 |
| C | 统一契约：字段完备 + L1 命中形状 |
| S | kbqa_store：语料与版本戳 / 草稿流转 / 发布幂等 / 度量统计 |
| A | API：L1 直答不调 LLM / 反馈入草稿 / 管理端点 403 / publish 后同问走 L1 |
| R | 回归：缓存键含版本 / L1 库故障可绕过 / 存量缓存委派语义 |
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mfg_agents.common.config import settings
from mfg_agents.v2.core import cache
from mfg_agents.v2.services import kbqa as kbqa_service
from mfg_agents.v2.services import kbqa_store
from mfg_agents.v2.services.kbqa_faq_match import FAQMatcher, normalize

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _boom(*args, **kwargs):  # L1 命中时若被调用即测试失败
    raise AssertionError("L1 命中不应调用 L2（search_kb / LLM）")


# ----------------------------------------------------------------  fixtures


def _kbqa_ddl() -> list[str]:
    """从 seed_v3.sql 抽 kbqa_policy + 三张新表 DDL（守护种子文件含表）。

    只取 CREATE TABLE——kbqa_policy 在 seed_v3.sql 里还带 48 行 INSERT
    种子数据，抽进来会污染 fixture 里"一条年假政策行"的隔离断言。
    """
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
    """缓存直通（测试不依赖 Redis 是否配置）。"""
    monkeypatch.setattr(cache, "get_or_compute",
                        lambda key, ttl, producer: producer())
    monkeypatch.setattr(cache, "cache_delete", lambda key: None)


@pytest.fixture()
def client(monkeypatch, kbqa_db, no_redis_cache):
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


# ---------------------------------------------------------------- M：匹配器

FAKE_CORPUS = [
    {"ref": "QP-T-1", "question": "年假与调休", "answer": "入职满1年享5天年假。",
     "keywords": "年假,休假,调休", "dept": "policy_attendance",
     "dept_cn": "考勤制度", "src": "policy"},
    {"ref": "QP-T-2", "question": "费用报销时效", "answer": "30天内提交报销单。",
     "keywords": "报销,时效,审批", "dept": "finance",
     "dept_cn": "财务报销", "src": "policy"},
    {"ref": "QP-T-3", "question": "差旅费用标准", "answer": "一线城市500元每晚。",
     "keywords": "差旅,住宿,发票", "dept": "finance",
     "dept_cn": "财务报销", "src": "policy"},
    {"ref": "12", "question": "VPN 连不上怎么自救", "answer": "先重启客户端再联系IT。",
     "keywords": "VPN,网络", "dept": "it_vpn",
     "dept_cn": "IT 支持", "src": "faq"},
]


def test_m_hit_keyword_and_title():
    m = FAQMatcher(corpus=FAKE_CORPUS)
    r = m.match("年假怎么休")
    assert r.hit and r.row["ref"] == "QP-T-1"
    assert r.score >= 0.9


def test_m_below_threshold_falls_through():
    m = FAQMatcher(corpus=FAKE_CORPUS)
    r = m.match("公司附近有租房补贴吗")
    assert not r.hit
    assert r.reason == "below_threshold"


def test_m_single_two_char_keyword_cannot_pass_alone():
    """单个 2 字符关键词命中 = 0.67，必须独自过不了 0.70 的闸。

    "酒店发票抬头怎么写"只该命中含"发票"关键词的《差旅费用标准》是不够的
    （答案不覆盖"抬头"）——没有标题/内容佐证就回落 L2。
    """
    m = FAQMatcher(corpus=FAKE_CORPUS)
    r = m.match("酒店发票抬头怎么写")
    assert not r.hit
    assert r.reason == "below_threshold"


def test_m_margin_ambiguity_falls_through():
    """两条都能答（同分）时不许乱指 → 回落 L2。"""
    corpus = [
        {"ref": "A", "question": "报销流程管理", "answer": "见OA。",
         "keywords": "报销,流程", "src": "policy"},
        {"ref": "B", "question": "报销时效规定", "answer": "30天。",
         "keywords": "报销,时效", "src": "policy"},
    ]
    m = FAQMatcher(corpus=corpus)
    r = m.match("报销")
    assert not r.hit
    assert r.reason == "ambiguous_margin"


def test_m_normalize_fullwidth_and_whitespace():
    """全角字符 + 多余空白归一后同问同命中。"""
    m = FAQMatcher(corpus=FAKE_CORPUS)
    assert m.match("年假怎么休").hit
    assert m.match("　年假　怎么休？").hit  # 全角空格 + 全角问号
    assert normalize("ＶＰＮ") == "vpn"


def test_m_empty_query_and_empty_corpus():
    assert not FAQMatcher(corpus=FAKE_CORPUS).match("").hit
    assert not FAQMatcher(corpus=FAKE_CORPUS).match("   ").hit
    assert not FAQMatcher(corpus=[]).match("年假怎么休").hit


def test_m_faq_row_and_exact_question():
    """FAQ 行（src=faq）可命中；同题直中满分。"""
    m = FAQMatcher(corpus=FAKE_CORPUS)
    assert m.match("vpn连不上怎么办").row["ref"] == "12"
    assert m.match("年假与调休").score == 1.0


# ---------------------------------------------------------------- C：契约

def test_c_contract_fields_complete():
    c = kbqa_service._contract(
        "x", channel="faq", tags=[], sources=[],
        category={"name": "n"}, degraded=False)
    assert set(c) >= {"answer", "tags", "sources", "category", "degraded",
                      "channel", "confidence", "latency_ms", "faq_version"}


def test_c_l1_contract_shape(kbqa_db, no_redis_cache, monkeypatch):
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    out = kbqa_service.answer("年假怎么休", session_id="t")
    assert out["channel"] == "faq"
    assert out["answer"].startswith("入职满 1 年")
    assert out["sources"][0]["label"] == "QP-HR-003"
    assert out["degraded"] is False
    assert out["confidence"] >= 0.7
    assert out["faq_version"].startswith("policy:1:1|faq:0:0")


# ---------------------------------------------------------------- S：仓库

def test_s_corpus_and_version(kbqa_db):
    corpus = kbqa_store.faq_corpus()
    assert len(corpus) == 1 and corpus[0]["ref"] == "QP-HR-003"
    assert kbqa_store.faq_version() == "policy:1:1|faq:0:0"


def test_s_insert_faq_changes_version(kbqa_db):
    kbqa_store.insert_faq("VPN 连不上怎么办", "先重启客户端。",
                          keywords="VPN,网络")
    assert kbqa_store.faq_version() == "policy:1:1|faq:1:1"
    corpus = kbqa_store.faq_corpus()
    assert [r for r in corpus if r["src"] == "faq"]


def test_s_publish_draft_flow(kbqa_db):
    did = kbqa_store.insert_draft("工资什么时候发", "每月15日发工资。")
    faq_id = kbqa_store.publish_draft(did, reviewer="hulk")
    assert faq_id >= 1
    drafts = kbqa_store.list_drafts("published")
    assert drafts and drafts[0]["id"] == did
    faq_rows = [r for r in kbqa_store.faq_corpus() if r["src"] == "faq"]
    assert len(faq_rows) == 1
    # 晋升行的关键词 = 问题本身（否则标题单独 0.5 分过不了闸）
    assert faq_rows[0]["keywords"] == "工资什么时候发"


def test_s_republish_rejected_and_missing(kbqa_db):
    did = kbqa_store.insert_draft("q1", "a1")
    kbqa_store.publish_draft(did)
    with pytest.raises(ValueError):
        kbqa_store.publish_draft(did)
    with pytest.raises(KeyError):
        kbqa_store.publish_draft(9999)


def test_s_log_and_stats(kbqa_db):
    kbqa_store.log_query("s1", "年假怎么休", "faq", confidence=0.95,
                         top_score=0.95, latency_ms=3)
    kbqa_store.log_query("s1", "工资什么时候发", "rag")
    stats = kbqa_store.channel_stats()
    assert stats["distribution"] == {"faq": 1, "rag": 1}
    assert stats["total"] == 2
    assert stats["rag_total"] == 1
    assert stats["rag_low_confidence"] == 1  # confidence 为 None 计入低置信


def test_s_log_query_never_raises(kbqa_db):
    """遥测失败只吞不抛（度量写失败不能弄丢回答）。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(kbqa_store, "get_engine", _boom)
        kbqa_store.log_query("s", "q", "faq")  # 不抛即过


# ---------------------------------------------------------------- A：API

def test_a_l1_hit_no_llm(client, monkeypatch):
    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    resp = client.post("/api/v2/kbqa", json={"query": "年假怎么休"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["channel"] == "faq"
    assert "年假" in data["answer"]
    assert data["sources"][0]["label"] == "QP-HR-003"


def test_a_feedback_creates_draft(client):
    resp = client.post("/api/v2/kbqa/feedback", json={
        "query": "工资什么时候发", "answer": "每月15日发。",
        "rating": 5, "channel": "rag"})
    assert resp.status_code == 200
    assert resp.json()["data"]["drafted"] is True
    assert any(d["question"] == "工资什么时候发"
               for d in kbqa_store.list_drafts("pending"))


def test_a_feedback_low_rating_no_draft(client):
    resp = client.post("/api/v2/kbqa/feedback", json={
        "query": "工资什么时候发", "answer": "每月15日发。",
        "rating": 2, "channel": "rag"})
    assert resp.json()["data"]["drafted"] is False
    assert kbqa_store.list_drafts("pending") == []


def test_a_feedback_faq_channel_no_draft(client):
    """L1 直答本就是权威制度，不需要晋升。"""
    resp = client.post("/api/v2/kbqa/feedback", json={
        "query": "年假怎么休", "answer": "5天。", "rating": 5, "channel": "faq"})
    assert resp.json()["data"]["drafted"] is False


def test_a_feedback_blank_query_400(client):
    resp = client.post("/api/v2/kbqa/feedback", json={"query": "  "})
    assert resp.status_code == 400


def test_a_admin_denied_without_token(client):
    assert client.get("/api/v2/kbqa/drafts").status_code == 403
    assert client.get("/api/v2/kbqa/stats").status_code == 403
    assert client.post("/api/v2/kbqa/drafts/1/publish").status_code == 403


def test_a_publish_then_same_query_hits_l1(client, monkeypatch):
    """端到端：好评反馈 → 草稿 → 带令牌发布 → 同问立刻走 L1 直答。"""
    monkeypatch.setattr(settings, "KBQA_ADMIN_TOKEN", "t0ken")
    client.post("/api/v2/kbqa/feedback", json={
        "query": "工资什么时候发", "answer": "每月15日发工资。",
        "rating": 5, "channel": "rag"})
    did = kbqa_store.list_drafts("pending")[0]["id"]

    denied = client.post(f"/api/v2/kbqa/drafts/{did}/publish")
    assert denied.status_code == 403  # 配置了令牌但没带头

    ok = client.post(f"/api/v2/kbqa/drafts/{did}/publish",
                     headers={"X-FAQ-Admin-Token": "t0ken"})
    assert ok.status_code == 200
    assert ok.json()["data"]["faq_id"] >= 1

    again = client.post(f"/api/v2/kbqa/drafts/{did}/publish",
                        headers={"X-FAQ-Admin-Token": "t0ken"})
    assert again.status_code == 409  # 重复发布

    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    resp = client.post("/api/v2/kbqa", json={"query": "工资什么时候发"})
    assert resp.json()["data"]["channel"] == "faq"


def test_a_stats_endpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "KBQA_ADMIN_TOKEN", "t0ken")
    head = {"X-FAQ-Admin-Token": "t0ken"}
    client.post("/api/v2/kbqa", json={"query": "年假怎么休"})
    resp = client.get("/api/v2/kbqa/stats", headers=head)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["distribution"].get("faq") == 1
    assert data["total"] >= 1


def test_a_missing_draft_404(client, monkeypatch):
    monkeypatch.setattr(settings, "KBQA_ADMIN_TOKEN", "t0ken")
    resp = client.post("/api/v2/kbqa/drafts/9999/publish",
                       headers={"X-FAQ-Admin-Token": "t0ken"})
    assert resp.status_code == 404


# ---------------------------------------------------------------- R：回归

def test_r_cache_key_includes_version():
    a = kbqa_service._cache_key("年假怎么休", 3, "policy:48:48|faq:0:0")
    b = kbqa_service._cache_key("年假怎么休", 3, "policy:48:48|faq:1:1")
    assert a != b  # FAQ 写操作 → 版本变 → 旧键不可达（ADR-001 D2）
    assert kbqa_service._cache_key("年假怎么休", 3, "v") != \
        kbqa_service._cache_key("年假怎么休", 5, "v")


def test_r_l1_store_failure_does_not_break_answer(monkeypatch):
    """L1 数据层故障 → 整层跳过，问答照常走 L2（加速层不是依赖）。"""
    monkeypatch.setattr(kbqa_store, "faq_corpus", _boom)
    monkeypatch.setattr(kbqa_service, "search_kb", lambda q, n_results=3: [])
    out = kbqa_service.answer("任意问题")
    assert out["channel"] == "miss"
    assert "answer" in out
