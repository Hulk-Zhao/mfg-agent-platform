"""企业制度问答（kbqa）离线校验：语料播种 + 缓存 + 限流。

不连 Milvus / 不连 Redis，纯离线；只守护"内容可播种、分类不漂移、
缓存键稳定、公网限流生效"这几件事。

| 组 | 钉什么 |
| --- | --- |
| C | data/kbqa_corpus 语料可被 load_chunks 读出（md + jsonl 双分支）|
| D | 分类映射稳定：块 dept ∈ DEPT_ORDER，KBQA_DEPTS 与 DEPT_ORDER 同源 |
| K | 缓存键确定性 + answer 走 core.cache.get_or_compute |
| R | /api/v2/kbqa 限流返回 429（KBQA_RATE_LIMITED），空问题 400 |
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mfg_agents.common import kbqa_seed
from mfg_agents.v2.core import cache
from mfg_agents.v2.services import kbqa as kbqa_service

CORPUS_DIR = Path("data/kbqa_corpus")


# ---------------------------------------------------------------- C：语料可读

@pytest.fixture(scope="module")
def rows():
    return kbqa_seed.load_chunks(CORPUS_DIR)


def test_corpus_dir_exists():
    assert CORPUS_DIR.is_dir(), "data/kbqa_corpus 语料目录缺失"


def test_corpus_is_large_enough(rows):
    """演示体量：16 类制造制度，至少 400 个知识块。"""
    assert len(rows) >= 400, f"制度语料过少：{len(rows)}"


def test_rows_have_required_fields(rows):
    for r in rows[:60]:
        assert r["id"], "缺 id"
        assert r["doc"], "缺 doc"
        assert r["meta"]["dept"]
        assert r["meta"]["dept_cn"]


def test_every_category_present(rows):
    present = {r["meta"]["dept"] for r in rows}
    missing = [d for d in kbqa_seed.DEPT_ORDER if d not in present]
    assert not missing, f"以下类别无语料: {missing}"


def test_chunk_index_is_dense_per_source(rows):
    """同一来源文件的 chunk_index 应从 0 连续递增（幂等 id 前提）。"""
    by_source: dict[str, list[int]] = {}
    for r in rows:
        key = f"{r['meta']['dept']}/{r['meta']['source']}"
        by_source.setdefault(key, []).append(int(r["meta"]["chunk_index"]))
    for key, idx in by_source.items():
        assert idx == list(range(len(idx))), f"{key} 的 chunk_index 不连续"


def test_jsonl_branch(tmp_path):
    """load_chunks 的 jsonl 分支：每行 {doc, meta} → 补全 dept/source/chunk_index。"""
    folder = tmp_path / "quality"
    folder.mkdir()
    (folder / "faq.jsonl").write_text(
        '{"id": "q1", "doc": "首件检验第一段\\n\\n首件检验第二段"}\n'
        '{"doc": "缺 id 的行按文件名_行号生成"}\n',
        encoding="utf-8")
    rows = kbqa_seed.load_chunks(tmp_path)
    assert len(rows) == 2
    assert rows[0]["id"] == "q1"
    assert rows[0]["meta"]["dept"] == "quality_qms"
    assert rows[0]["meta"]["source"] == "faq.jsonl"
    assert rows[0]["meta"]["chunk_index"] == 0
    assert rows[1]["id"].startswith("quality_faq_")


# ---------------------------------------------------------------- D：分类映射

def test_dept_mapping_is_stable(rows):
    assert {r["meta"]["dept"] for r in rows} <= set(kbqa_seed.DEPT_ORDER)


def test_kbqa_depts_match_order_exactly():
    """前端"支持类别"面板（KBQA_DEPTS）必须与 DEPT_ORDER 同源、16 类。"""
    keys = [d["key"] for d in kbqa_service.KBQA_DEPTS]
    assert keys == kbqa_seed.DEPT_ORDER
    assert len(keys) == 16


def test_chunk_text_splits_paragraphs():
    assert kbqa_seed.chunk_text("第一段。\n\n第二段。", max_len=400) == ["第一段。", "第二段。"]


def test_chunk_text_respects_max_len():
    long_text = "。".join(["较长的制度条款用于测试分块" ] * 40)
    chunks = kbqa_seed.chunk_text(long_text, max_len=120)
    assert chunks and all(len(c) <= 120 for c in chunks)


def test_run_seed_batches_upsert(monkeypatch):
    """run_seed 按 64 分批 upsert（monkeypatch add_kb_chunks，不真正写库）。"""
    calls: list[int] = []

    def _fake(ids, documents, metadatas):
        calls.append(len(ids))

    monkeypatch.setattr(kbqa_seed, "add_kb_chunks", _fake)
    n = kbqa_seed.run_seed()
    assert n >= 400
    assert sum(calls) == n
    assert all(c <= 64 for c in calls)


# ---------------------------------------------------------------- K：缓存

def test_cache_key_is_deterministic():
    a = kbqa_service._cache_key("首件检验要求", 3)
    b = kbqa_service._cache_key("  首件检验要求  ", 3)
    assert a == b
    assert a.startswith("mfg:kbqa:")
    assert kbqa_service._cache_key("首件检验要求", 5) != a


def test_answer_delegates_to_cache(monkeypatch):
    seen: dict[str, object] = {}

    def _fake_get_or_compute(key, ttl, producer):
        seen["key"] = key
        seen["ttl"] = ttl
        return producer()

    monkeypatch.setattr(cache, "get_or_compute", _fake_get_or_compute)
    monkeypatch.setattr(kbqa_service, "search_kb", lambda q, n_results=3: [])
    out = kbqa_service.answer("首件检验要求", top_k=2)
    assert str(seen["key"]).startswith("mfg:kbqa:")
    assert seen["ttl"] == kbqa_service.KBQA_CACHE_TTL_S
    assert "answer" in out


# ---------------------------------------------------------------- R：HTTP 限流

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


def test_kbqa_blank_query_returns_400(client):
    resp = client.post("/api/v2/kbqa", json={"query": "   "})
    assert resp.status_code == 400


def test_kbqa_endpoint_rate_limited(client, monkeypatch):
    """超限返回 429 + KBQA_RATE_LIMITED（不影响前几次正常请求）。"""
    monkeypatch.setattr(kbqa_service, "answer",
                        lambda q, top_k=3: {"answer": "ok", "tags": [], "sources": [],
                                            "category": {"name": "品质制度"},
                                            "degraded": False})
    state = {"n": 0}

    def _fake_limit(bucket, limit, window_s=60):
        state["n"] += 1
        return state["n"] > 2

    monkeypatch.setattr(cache, "rate_limit_exceeded", _fake_limit)
    codes = [client.post("/api/v2/kbqa", json={"query": "首件检验要求"}).status_code
             for _ in range(5)]
    assert 200 in codes and 429 in codes
    limited = client.post("/api/v2/kbqa", json={"query": "首件检验要求"})
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "KBQA_RATE_LIMITED"
