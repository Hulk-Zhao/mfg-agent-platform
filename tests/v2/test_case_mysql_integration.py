"""案例库（CASE_DB）MySQL 真库回归：连接 / schema / 主数据 / 审计 / 申请。

默认跳过（CI 无 MySQL）；本地验证方式：

    MFG_MYSQL_CASE_URL=mysql+pymysql://root:demo123@127.0.0.1:3307/mfg_case_test \
        uv run python -m pytest tests/v2/test_case_mysql_integration.py -q

URL 必须指向**一次性空库**（本文件会建表并按用例 ID 清理自己的行；
``case_audit`` 是只追加的哈希链，测试行不删除——下一次运行会自然地接在链尾）。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from mfg_agents.common.case_db import ensure_case_schema
from mfg_agents.v2.contracts.case_acl import Principal
from mfg_agents.v2.services.case_audit import CaseAuditLog
from mfg_agents.v2.services.case_ontology_gap import CaseOntologyGapService
from mfg_agents.v2.services.case_store import CaseStore

CASE_URL = os.getenv("MFG_MYSQL_CASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not CASE_URL,
    reason="需要 MFG_MYSQL_CASE_URL 指向一次性 MySQL 库（例如 mfg_case_test）")


@pytest.fixture()
def case_id() -> str:
    return "CASE-MY-" + uuid.uuid4().hex[:10].upper()


@pytest.fixture()
def store() -> CaseStore:
    ensure_case_schema(CASE_URL)
    ensure_case_schema(CASE_URL)   # 幂等：CREATE TABLE IF NOT EXISTS
    return CaseStore(CASE_URL)


@pytest.fixture(autouse=True)
def _cleanup(request):
    """用例结束后删掉自己写入的业务行（审计链除外）。"""
    yield
    cid = request.node.funcargs.get("case_id")
    if not cid:
        return
    st = CaseStore(CASE_URL)
    with st._conn() as conn:  # noqa: SLF001 - 测试清理
        for table in ("case_acl", "case_chunk", "case_evidence",
                      "case_access_request", "case"):
            # ``case`` 是 MySQL 保留字，需要反引号
            name = "`case`" if table == "case" else table
            conn.execute(f"DELETE FROM {name} WHERE case_id=?", (cid,))


def _create(store: CaseStore, case_id: str, *, status: str = "active"):
    return store.create_case({
        "case_id": case_id,
        "title": "MySQL 案例库回归",
        "summary": "摘要",
        "phenomenon": "现象",
        "root_cause_text": "根因",
        "countermeasure_text": "对策",
        "status": status,
        "source_ticket_type": "complaint",
        "source_ticket_id": "T-MYSQL-1",
        "tenant": "demo",
        "acl": [{"dim": "dept", "value": "quality"}],
    }, allow_widen_acl=True)


def test_store_roundtrip_and_rowcount_semantics(store, case_id):
    assert _create(store, case_id) == case_id
    row = store.get_case(case_id)
    assert row["title"] == "MySQL 案例库回归" and row["status"] == "active"

    store.update_fields(case_id, title="改后标题")
    assert store.get_case(case_id)["title"] == "改后标题"
    # 相同值再写一次：MySQL 默认 rowcount=0 会被误判为"案例不存在"，
    # 适配层用 CLIENT_FOUND_ROWS 对齐 SQLite 的匹配行数语义。
    store.update_fields(case_id, title="改后标题")

    assert store.transition(case_id, "deprecated") == "deprecated"
    assert store.get_case(case_id)["status"] == "deprecated"


def test_acl_widening_guard_and_rows(store, case_id):
    _create(store, case_id)
    rows = store.acl_entries(case_id)
    assert any(r["dim"] == "dept" and r["value"] == "quality" for r in rows)
    store.set_acl(case_id, [{"dim": "dept", "value": "quality"}])  # 收紧/等价可写
    with pytest.raises(Exception):   # AclWideningNotAllowed
        store.set_acl(case_id, [{"dim": "dept", "value": "rd"}])
    with pytest.raises(Exception):   # 新增 plant 同样是放宽
        store.set_acl(case_id, [{"dim": "dept", "value": "quality"},
                                {"dim": "plant", "value": "P1"}])
    vis = store.set_acl(case_id, [{"dim": "dept", "value": "quality"},
                                  {"dim": "plant", "value": "P1"}],
                        allow_widen=True)
    assert "plant:P1" in vis


def test_chunks_and_evidence(store, case_id):
    _create(store, case_id)
    store.replace_chunks(case_id, [
        {"chunk_id": f"{case_id}-c1", "chunk_type": "summary",
         "content_version": 1, "text_hash": "h1"},
    ])
    chunks = store.chunk_rows(case_id)
    assert chunks[0]["chunk_id"] == f"{case_id}-c1"
    store.set_chunk_status(case_id, "indexed", "2026-10-23T00:00:00+00:00")
    assert store.chunk_rows(case_id)[0]["vector_status"] == "indexed"
    store.add_evidence(case_id, evidence_id=f"{case_id}-e1",
                       kind="image", uri="s3://bucket/x.png")
    assert store.evidence_rows(case_id)[0]["evidence_id"] == f"{case_id}-e1"


def test_health_probe_reads_case_db_mysql(monkeypatch):
    """健康探针在 MySQL 案例库上要验到表级（连接可达 ≠ schema 就绪）。"""
    from mfg_agents.common.config import settings
    from mfg_agents.v2.services import health as health_mod

    monkeypatch.setattr(settings, "CASE_DB", CASE_URL, raising=False)
    state, detail = health_mod._probe_case_db()
    assert state == "ok" and "case 表可读" in detail


def test_named_lock_available_for_audit_chain():
    from mfg_agents.common.case_db import connect

    conn = connect(CASE_URL)
    try:
        assert conn.lock_chain("mfg_case_audit_test", timeout_s=5) is True
        conn.unlock_chain("mfg_case_audit_test")
    finally:
        conn.close()


def test_audit_chain_on_mysql(store, case_id):
    _create(store, case_id)
    audit = CaseAuditLog(CASE_URL)
    seq = audit.append(actor="u-audit", action="case.query", case_id=case_id,
                       query_hash="q", filter_hash="f", hit_count=2,
                       detail={"stage": "mysql"})
    assert seq > 0
    chain = audit.verify_chain()
    assert chain["valid"] is True
    assert audit.count() >= 1
    assert audit.recent(5)[0]["case_id"] == case_id


def test_audit_append_is_serialized_across_connections():
    """多连接并发追加：命名锁保证链不断（无锁时 prev_hash 会分叉）。"""
    from concurrent.futures import ThreadPoolExecutor

    before = CaseAuditLog(CASE_URL).count()

    def _append(index: int) -> int:
        return CaseAuditLog(CASE_URL).append(
            actor=f"u-concurrent-{index}", action="case.query",
            query_hash=f"q{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_append, range(8)))

    log = CaseAuditLog(CASE_URL)
    chain = log.verify_chain()
    assert chain["valid"] is True, chain
    assert chain["rows"] == before + 8


def test_ontology_gap_request_and_decide(store, case_id):
    _create(store, case_id)
    audit = CaseAuditLog(CASE_URL)
    svc = CaseOntologyGapService(store, audit=audit)
    requester = Principal(user_id="u-gap-" + uuid.uuid4().hex[:6],
                          tenant="demo", dept="quality", roles=["quality"])
    term = "雪花点-" + uuid.uuid4().hex[:8]
    first = svc.request(requester, vocab="defect_mode", term=term, hint="现场说法")
    gap_id = first["gap_id"]
    try:
        assert first["status"] == "pending"
        # 幂等：同一词重复提交返回既有申请，不产生第二张待办
        again = svc.request(requester, vocab="defect_mode", term=term, hint="现场说法")
        assert again["duplicated"] is True and again["gap_id"] == gap_id

        approver = Principal(user_id="u-approver-" + uuid.uuid4().hex[:6],
                             tenant="demo", dept="quality",
                             roles=["quality_director"])
        decided = svc.decide(approver, gap_id, approve=True, reason="已确认词条归属")
        assert decided["status"] == "approved"
        assert decided["vocabulary_written"] is False   # 批准 ≠ 自动写词表
    finally:
        with store._conn() as conn:  # noqa: SLF001 - 测试清理（表无 case_id 列）
            conn.execute("DELETE FROM case_ontology_gap WHERE gap_id=?", (gap_id,))


def test_partial_unique_index_equivalence(store, case_id):
    """MySQL 无部分索引：函数唯一索引只约束 pending 状态。"""
    _create(store, case_id)
    base = ("INSERT INTO case_access_request (request_id, case_id, requester_id, "
            "requester_dept, requester_role, reason, scope, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
    with store._conn() as conn:   # noqa: SLF001
        conn.execute(base, (f"AR-{case_id}-1", case_id, "u-dup", "rd", "rd",
                            "排查", "grant", "pending", "2026-10-23T00:00:00+00:00"))
    with pytest.raises(Exception):   # IntegrityError（唯一键冲突）
        with store._conn() as conn:  # noqa: SLF001
            conn.execute(base, (f"AR-{case_id}-2", case_id, "u-dup", "rd", "rd",
                                "重复", "grant", "pending",
                                "2026-10-23T00:00:01+00:00"))
    with store._conn() as conn:      # noqa: SLF001
        # 已裁决的同主体申请不受唯一约束
        conn.execute(base, (f"AR-{case_id}-3", case_id, "u-dup", "rd", "rd",
                            "二次", "grant", "approved",
                            "2026-10-23T00:00:02+00:00"))
        n = conn.execute("SELECT COUNT(1) AS n FROM case_access_request "
                         "WHERE case_id=?", (case_id,)).fetchone()
    assert n["n"] == 2
