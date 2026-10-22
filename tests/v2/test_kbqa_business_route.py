"""业务知识前置分流（R4/D5/D6）：词面路由 / 服务端分流 / 回落 / 缓存命名空间。

不连 Milvus / 不连 Redis / 不调 LLM（search_kb 与 llm_available 打桩）。
制度链隔离用 tmp kbqa 库（DDL 从 seed_v3.sql 现抽，同 test_kbqa_faq_tier 范式）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mfg_agents.common.config import settings
from mfg_agents.v2.core import cache
from mfg_agents.v2.services import kbqa as kbqa_service
from mfg_agents.v2.services import kbqa_business, kbqa_store, knowledge_store

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _kbqa_ddl() -> list[str]:
    sql = (_REPO_ROOT / "data" / "seed_v3.sql").read_text(encoding="utf-8")
    stmts = [s for s in sql.split(";")
             if "CREATE TABLE" in s.upper()
             and ("kbqa_policy" in s or "kbqa_faq" in s or "kbqa_query_log" in s)]
    assert len(stmts) >= 4
    return stmts


@pytest.fixture()
def kbqa_db(tmp_path, monkeypatch):
    """tmp kbqa 库（仅一条年假政策行）；引擎指向它，遥测关闭。"""
    db = tmp_path / "kbqa_test.db"
    conn = sqlite3.connect(db)
    for stmt in _kbqa_ddl():
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO kbqa_policy (id, dept, dept_cn, policy_no, category, "
        "title, content, keywords) VALUES (1,'policy_attendance','考勤制度',"
        "'QP-HR-003','年假','年假与调休','入职满 1 年享 5 天年假。',"
        "'年假,休假,调休')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "DB_URL", f"sqlite:///{db}")
    kbqa_store.reset_engine()
    yield db
    kbqa_store.reset_engine()


@pytest.fixture()
def knowledge_db(tmp_path, monkeypatch):
    """tmp 知识库 + 演示种子；单例指向它（不碰真实治理库）。"""
    repo = knowledge_store.SQLiteKnowledgeRepository(
        db_path=str(tmp_path / "knowledge_test.db"))
    knowledge_store.ensure_demo_seed(repo)
    monkeypatch.setattr(knowledge_store, "_repo", repo)
    yield repo
    repo.close()
    knowledge_store.reset_knowledge_repository()


@pytest.fixture()
def no_redis_cache(monkeypatch):
    """缓存直通（测试不依赖 Redis 是否配置）。"""
    monkeypatch.setattr(cache, "get_or_compute",
                        lambda key, ttl, producer: producer())
    monkeypatch.setattr(cache, "cache_delete", lambda key: None)


@pytest.fixture()
def no_telemetry(monkeypatch):
    monkeypatch.setattr(settings, "KBQA_LOG_WRITES", False)


def _fake_rag_hit(*_a, **_k):
    return [{"document": "制度片段", "score": 0.9,
             "metadata": {"dept_cn": "考勤制度", "source": "QP-HR-003",
                          "chunk_index": 0}}]


# ---------------------------------------------------------------- 词面路由


@pytest.mark.parametrize("q,expected", [
    ("B型支架换了新材料，焊接参数用哪一版", True),
    ("ECN-0921 之后用哪版", True),
    ("这个料号的现行版本是哪一版", True),
    ("工程图纸图号发我", True),
    ("这台车的扭矩标准是多少", True),
    ("年假怎么休", False),
    ("报销流程是什么", False),
    ("今天天气怎么样", False),
    ("", False),
])
def test_business_word_routing(q, expected):
    assert kbqa_business.is_business_query(q) is expected


# ---------------------------------------------------------------- 服务端分流


def test_business_hit_never_touches_institutional_chain(knowledge_db, kbqa_db,
                                                        no_redis_cache,
                                                        no_telemetry,
                                                        monkeypatch):
    """业务命中 → channel=business，且不碰制度链（search_kb 被调用即失败）。"""
    def _boom(*a, **k):
        raise AssertionError("业务命中不应调用 search_kb（制度 RAG）")

    monkeypatch.setattr(kbqa_service, "search_kb", _boom)
    out = kbqa_service.answer("B型支架换了新材料，焊接参数用哪一版")
    assert out["channel"] == kbqa_service.CHANNEL_BUSINESS
    assert "V3" in out["answer"] and "180A" in out["answer"]
    assert "ECN-0921" in out["answer"]
    assert any("BKT-B01" in s["label"] for s in out["sources"])
    assert out["degraded"] is False


def test_business_hit_mentions_stale_cards(knowledge_db, kbqa_db,
                                           no_redis_cache, no_telemetry):
    """旧版工艺卡未撤换 → 结论里显式提示（R4 验收②）。"""
    out = kbqa_service.answer("B型支架焊接参数用哪一版")
    assert "V2" in out["answer"] and "撤换" in out["answer"]


def test_business_miss_falls_back_to_institutional(knowledge_db, kbqa_db,
                                                   no_redis_cache,
                                                   no_telemetry, monkeypatch):
    """词表命中但知识轨无此料号 → 回落制度链路（宁宽勿窄的兑现）。"""
    called = {"n": 0}

    def _fake_search(query, n_results=3):
        called["n"] += 1
        return _fake_rag_hit()

    monkeypatch.setattr(kbqa_service, "search_kb", _fake_search)
    out = kbqa_service.answer("受控文件发放流程")
    assert out["channel"] == kbqa_service.CHANNEL_RAG
    assert called["n"] == 1


def test_knowledge_store_failure_falls_back(kbqa_db, no_redis_cache,
                                            no_telemetry, monkeypatch):
    """知识轨故障（库挂）→ 回落制度链，问答不崩、不返回业务通道。

    注：回落后的具体通道（rag/direct/miss）取决于 kbqa_intent 词面路由——
    「焊接」不在 _DOMAIN_WORDS（制度语料答不了工程词，收了也是 miss），
    本测试只钉「不崩 + 不进 business 通道」。
    """
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(knowledge_store, "get_knowledge_repository", _boom)
    monkeypatch.setattr(kbqa_service, "search_kb",
                        lambda q, n_results=3: _fake_rag_hit())
    out = kbqa_service.answer("B型支架焊接参数用哪一版")
    assert out["channel"] != kbqa_service.CHANNEL_BUSINESS
    assert out["channel"] in {kbqa_service.CHANNEL_RAG,
                              kbqa_service.CHANNEL_DIRECT,
                              kbqa_service.CHANNEL_MISS}


def test_institutional_query_unaffected(kbqa_db, no_redis_cache,
                                        no_telemetry, monkeypatch):
    """制度问句不被误截（词表不含制度域泛词）。"""
    monkeypatch.setattr(kbqa_service, "search_kb",
                        lambda q, n_results=3: _fake_rag_hit())
    out = kbqa_service.answer("年假怎么休")
    assert out["channel"] in {kbqa_service.CHANNEL_FAQ,
                              kbqa_service.CHANNEL_RAG,
                              kbqa_service.CHANNEL_MISS}


# ---------------------------------------------------------------- 缓存命名空间（D6）


def test_business_cache_key_namespace_by_knowledge_version():
    """业务缓存键独立命名空间 + ECN 版本戳进键（绝不用 faq_version）。"""
    k1 = kbqa_service._business_cache_key("焊接参数用哪一版", "bom:2:2|ecn:2:2")
    k2 = kbqa_service._business_cache_key("焊接参数用哪一版", "bom:3:3|ecn:3:3")
    assert k1 != k2
    assert k1.startswith("mfg:bk:v1:")
    assert not k1.startswith("mfg:kbqa:")


def test_ecn_write_invalidates_business_cache_namespace(knowledge_db,
                                                        no_redis_cache,
                                                        no_telemetry):
    """ECN 写操作 → 版本戳变 → 缓存键变（旧答案自然不可达）。"""
    repo = knowledge_store.get_knowledge_repository()
    s1 = repo.version_stamp()
    k1 = kbqa_service._business_cache_key("焊接参数用哪一版", s1)
    repo.apply_ecn("ECN-0999", "P-999", "V1", {"x": 1})
    s2 = repo.version_stamp()
    k2 = kbqa_service._business_cache_key("焊接参数用哪一版", s2)
    assert s1 != s2 and k1 != k2
