"""案例种子批量导入：连接复用 + 事务回滚（SQLite 可测，MySQL 同理）。"""
from __future__ import annotations

import json

import pytest

from mfg_agents.v2.services import case_store as case_store_mod
from mfg_agents.v2.services.case_store import CaseStore


def _seed_payload() -> dict:
    return {
        "tenant": "demo",
        "cases": [
            {
                "case_id": f"CASE-SEED-{i}",
                "title": f"批量种子 {i}",
                "summary": "摘要",
                "phenomenon": "现象",
                "root_cause_text": "根因",
                "countermeasure_text": "对策",
                "status": "active",
                "source_ticket_type": "manual",
                "source_ticket_id": f"T-{i}",
                "acl": [{"dim": "dept", "value": "quality"}],
            } for i in range(3)
        ],
    }


def _write_seed(tmp_path) -> str:
    path = tmp_path / "case_seed.json"
    path.write_text(json.dumps(_seed_payload(), ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_seed_reuses_single_connection(tmp_path, monkeypatch):
    """种子导入必须整段复用一个连接（否则 MySQL 下每例十几次建连）。"""
    calls: list[int] = []
    original = case_store_mod.connect

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(case_store_mod, "connect", counting)
    store = CaseStore(tmp_path / "case.db")
    imported = store.seed_from_json(_write_seed(tmp_path))
    assert imported == 3
    assert len(calls) == 1, f"种子导入建连 {len(calls)} 次（应为 1）"


def test_batch_conn_commits_on_success(tmp_path):
    store = CaseStore(tmp_path / "case.db")
    with store._batch_conn() as conn:   # noqa: SLF001 - 契约级测试
        conn.execute(
            'INSERT INTO "case" (case_id, title, tenant, status, '
            "source_ticket_type, source_ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("CASE-BATCH-OK", "批量提交", "demo", "active", "manual", "T-OK"))
    assert store.get_case("CASE-BATCH-OK") is not None


def test_batch_conn_rolls_back_on_error(tmp_path):
    store = CaseStore(tmp_path / "case.db")
    with pytest.raises(RuntimeError):
        with store._batch_conn() as conn:   # noqa: SLF001
            conn.execute(
                'INSERT INTO "case" (case_id, title, tenant, status, '
                "source_ticket_type, source_ticket_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("CASE-BATCH-FAIL", "批量回滚", "demo", "active", "manual", "T-FAIL"))
            raise RuntimeError("模拟导入中断")
    assert store.get_case("CASE-BATCH-FAIL") is None
