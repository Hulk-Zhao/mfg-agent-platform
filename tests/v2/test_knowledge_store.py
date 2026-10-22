"""业务知识中枢（R4/D4）：BOM 版本 + ECN 契约与 SQLite 实现。

tmp 文件库 + 显式注入 db_path（不碰真实治理库）。钉三件事：
1. apply_ecn 语义：新版本 active、旧活跃版本 superseded（「答到现行版本」）；
2. 检索：关键词命中 BOM/ECN 参数文本，active 加权；
3. 版本戳：任何写操作改戳（D6 缓存命名空间的基石）+ 演示种子幂等。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services import knowledge_store as ks


@pytest.fixture()
def repo(tmp_path):
    r = ks.SQLiteKnowledgeRepository(db_path=str(tmp_path / "knowledge_test.db"))
    yield r
    r.close()


# ---------------------------------------------------------------- ECN 语义


def test_apply_ecn_activates_new_and_supersedes_old(repo):
    """ECN 生效 = 新版本 active + 旧活跃版本 superseded。"""
    repo.apply_ecn("ECN-T01", "P-001", "V2", {"电流": "180A"},
                   title="测试变更一", effective_date="2026-10-01")
    repo.upsert_bom("P-001", "V1", params={"电流": "160A"}, status="active")
    repo.apply_ecn("ECN-T02", "P-001", "V3", {"电流": "200A"},
                   effective_date="2026-10-10")
    cur = repo.current_bom("P-001")
    assert cur["version"] == "V3" and cur["status"] == "active"
    versions = {v["version"]: v["status"] for v in repo.bom_versions("P-001")}
    assert versions == {"V3": "active", "V2": "superseded", "V1": "superseded"}


def test_ecn_lookup_and_list(repo):
    repo.apply_ecn("ECN-T01", "P-001", "V2", {"a": 1}, title="变更一")
    repo.apply_ecn("ECN-T02", "P-002", "V1", {"b": 2}, title="变更二")
    assert repo.ecn_by_id("ECN-T01")["title"] == "变更一"
    assert repo.ecn_by_id("nope") is None
    assert {e["ecn_id"] for e in repo.ecn_list()} == {"ECN-T01", "ECN-T02"}
    assert [e["ecn_id"] for e in repo.ecn_list("P-002")] == ["ECN-T02"]


def test_ecn_insert_idempotent(repo):
    repo.apply_ecn("ECN-T01", "P-001", "V2", {"a": 1}, title="变更一")
    repo.insert_ecn("ECN-T01", title="重复登记")
    assert repo.ecn_by_id("ECN-T01")["title"] == "变更一"  # 首次登记为准


# ---------------------------------------------------------------- 检索


def test_search_business_hits_params_and_part(repo):
    repo.apply_ecn("ECN-0921", "BKT-B01", "V3",
                   {"焊接电流": "180A", "焊接速度": "35cm/min"},
                   effective_date="2026-09-21")
    hits = repo.search_business("B型支架 焊接参数 用哪一版")
    assert hits and hits[0]["kind"] == "bom"
    assert hits[0]["part_no"] == "BKT-B01" and hits[0]["version"] == "V3"
    assert hits[0]["score"] >= 1.0
    assert repo.search_business("今天天气怎么样") == []
    assert repo.search_business("") == []


def test_search_active_ranks_above_superseded(repo):
    repo.apply_ecn("ECN-0921", "BKT-B01", "V3", {"焊接电流": "180A"})
    repo.upsert_bom("BKT-B01", "V2", params={"焊接电流": "160A"},
                    status="superseded")
    hits = repo.search_business("BKT-B01 焊接")
    assert hits[0]["version"] == "V3"  # active 加权，现行版本优先


def test_pending_withdraw_cards(repo):
    repo.apply_ecn("ECN-0921", "BKT-B01", "V3", {"焊接电流": "180A"})
    repo.upsert_bom("BKT-B01", "V2", params={"焊接电流": "160A"},
                    status="superseded")
    cards = repo.pending_withdraw_cards()
    assert any(c["part_no"] == "BKT-B01" and c["version"] == "V2"
               and c["superseded_by"] == "V3" for c in cards)


# ---------------------------------------------------------------- 版本戳与种子


def test_version_stamp_changes_on_write_and_idempotent_replay(repo):
    s0 = repo.version_stamp()
    repo.apply_ecn("ECN-0921", "BKT-B01", "V3", {"焊接电流": "180A"})
    s1 = repo.version_stamp()
    assert s0 != s1
    repo.apply_ecn("ECN-0921", "BKT-B01", "V3", {"焊接电流": "180A"})  # 幂等重放
    assert repo.version_stamp() == s1


def test_demo_seed_idempotent(repo):
    ks.ensure_demo_seed(repo)
    s1 = repo.version_stamp()
    n_bom = len(repo.bom_versions("BKT-B01"))
    ks.ensure_demo_seed(repo)
    assert repo.version_stamp() == s1
    assert len(repo.bom_versions("BKT-B01")) == n_bom
    cur = repo.current_bom("BKT-B01")
    assert cur["version"] == "V3" and cur["ecn_id"] == "ECN-0921"
    assert cur["params"]["焊接电流"] == "180A"


def test_demo_seed_stale_cards(repo):
    ks.ensure_demo_seed(repo)
    cards = {(c["part_no"], c["version"]) for c in repo.pending_withdraw_cards()}
    assert ("BKT-B01", "V2") in cards and ("MTR-M02", "V1") in cards


def test_contract_is_abstract():
    """契约不可裸实例化（ABC 防线）。"""
    with pytest.raises(TypeError):
        ks.KnowledgeRepository()  # type: ignore[abstract]
