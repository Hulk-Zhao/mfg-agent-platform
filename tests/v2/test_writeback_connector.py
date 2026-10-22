"""R13 / D14：连接器回写契约（幂等 + 审计回执 + tier=1 预执行同意 + R6 接线）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mfg_agents.v2.contracts.data_source import (
    SQLiteDataSource,
    WritebackNotSupported,
)
from mfg_agents.v2.services import procurement_followup as followup_svc
from mfg_agents.v2.services.writeback import (
    WRITEBACK_INTENTS,
    MockWritebackDataSource,
    WritebackIntentError,
    consent_is_approved,
    connector_for,
)


# ---------- 契约默认行为 ----------


def test_readonly_source_write_back_fails_closed():
    """只读源默认拒绝写回——不静默写入，这是「质量优先」的边界。"""
    source = SQLiteDataSource(":memory:", name="ro-test")
    with pytest.raises(WritebackNotSupported):
        source.write_back("erp.purchase_receipt", {"idempotency_key": "k1"})


def test_mock_connector_read_side_is_not_a_backdoor():
    source = MockWritebackDataSource()
    assert source.list_tables() == ["outbox"]
    with pytest.raises(KeyError):
        source.run_query("whatever", {})
    with pytest.raises(WritebackNotSupported):
        source.execute_readonly_sql("SELECT 1")


def test_unknown_intent_is_rejected():
    source = MockWritebackDataSource()
    with pytest.raises(WritebackIntentError):
        source.write_back("erp.not_registered", {"idempotency_key": "k1"})


# ---------- 回执与审计 ----------


def test_tier2_write_returns_auditable_receipt(tmp_path):
    outbox = tmp_path / "writeback_outbox.jsonl"
    source = MockWritebackDataSource(str(outbox))

    receipt = source.write_back("erp.purchase_receipt", {
        "idempotency_key": "fo-1|arrival",
        "followup_id": "fo-1",
        "arrival_qty": 2000,
    })

    assert receipt.status == "applied"
    assert receipt.tier == 2
    assert receipt.mode == "mock" and receipt.demo is True
    assert "未写回 ERP" in receipt.label
    assert receipt.receipt_id.startswith("rc-")
    assert receipt.idempotency_key == "fo-1|arrival"
    assert receipt.payload["arrival_qty"] == 2000

    lines = [json.loads(x) for x in outbox.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["receipt"]["receipt_id"] == receipt.receipt_id
    assert lines[0]["receipt"]["status"] == "applied"


def test_same_idempotency_key_replays_without_double_apply(tmp_path):
    outbox = tmp_path / "writeback_outbox.jsonl"
    source = MockWritebackDataSource(str(outbox))
    payload = {"idempotency_key": "fo-2|arrival", "arrival_qty": 100}

    first = source.write_back("erp.purchase_receipt", payload)
    second = source.write_back("erp.purchase_receipt", payload)

    assert first.receipt_id == second.receipt_id
    assert first.replayed is False and second.replayed is True
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1


def test_outbox_reload_keeps_idempotency_across_restart(tmp_path):
    outbox = tmp_path / "writeback_outbox.jsonl"
    payload = {"idempotency_key": "fo-3|arrival", "arrival_qty": 50}
    MockWritebackDataSource(str(outbox)).write_back("erp.purchase_receipt", payload)

    reopened = MockWritebackDataSource(str(outbox))
    replay = reopened.write_back("erp.purchase_receipt", payload)
    assert replay.replayed is True
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1


# ---------- tier=1：预执行同意 ----------


def test_consent_shape_validation():
    assert consent_is_approved({"ticket_id": "wf-1", "approved": True})
    assert consent_is_approved({"ticket_id": "wf-1", "approved": "true"})
    assert not consent_is_approved({"ticket_id": "", "approved": True})
    assert not consent_is_approved({"ticket_id": "wf-1", "approved": False})
    assert not consent_is_approved(None)


def test_tier1_without_consent_only_returns_pending(tmp_path):
    source = MockWritebackDataSource(str(tmp_path / "outbox.jsonl"))

    receipt = source.write_back("erp.delivery_commit", {
        "idempotency_key": "SO-1|eta",
        "due_date": "2026-11-01",
    })

    assert receipt.tier == 1
    assert receipt.status == "pending_consent"
    assert "delivery_commit" in receipt.next_step
    # 未落地：回执里没有已审批凭据
    assert receipt.consent == {}


def test_tier1_pending_then_consent_upgrades_same_receipt(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    source = MockWritebackDataSource(str(outbox))
    payload = {"idempotency_key": "SO-2|eta", "due_date": "2026-11-01"}

    pending = source.write_back("erp.delivery_commit", payload)
    applied = source.write_back("erp.delivery_commit", {
        **payload,
        "consent": {"ticket_id": "wf-9", "approved": True, "actor": "业务经理"},
    })

    assert pending.status == "pending_consent"
    assert applied.status == "applied"
    # 同一笔业务：回执 id 稳定（pending → applied 是升级，不是新单据）
    assert applied.receipt_id == pending.receipt_id
    assert applied.consent["ticket_id"] == "wf-9"
    # 台账留两次事实：待会签 + 已同意
    history = [json.loads(x) for x in outbox.read_text(encoding="utf-8").splitlines()]
    assert [h["receipt"]["status"] for h in history] == ["pending_consent", "applied"]


# ---------- R6 催交回写链路 ----------


def _demo_db(tmp_path: Path) -> str:
    return str(tmp_path / "demo_governance.db")


def test_r6_arrival_confirm_emits_writeback_receipt(tmp_path):
    db = _demo_db(tmp_path)
    gap = {"part_no": "MAT-1", "supplier": "SUP-1", "shortage_qty": 20}
    row = followup_svc.dispatch_followups("SET-R13", [gap], actor="采购员", db_path=db)[0]
    followup_svc.transition(row["followup_id"], "reply", actor="采购员",
                            note="周五到 20 件", db_path=db)

    arrived = followup_svc.transition(row["followup_id"], "arrival", actor="采购员",
                                      arrival_qty=20, db_path=db)

    receipt = arrived["writeback_receipt"]
    assert receipt["status"] == "applied"
    assert receipt["intent"] == "erp.purchase_receipt"
    assert receipt["demo_writeback"] is True
    assert "未写回 ERP" in receipt["label"]
    # 回执同时进 history（可审计），不只存在响应里
    assert arrived["history"][-1]["receipt"]["receipt_id"] == receipt["receipt_id"]
    # 台账与治理库同目录
    outbox = tmp_path / "writeback_outbox.jsonl"
    assert outbox.exists()
    assert json.loads(outbox.read_text(encoding="utf-8").splitlines()[0])[
        "receipt"]["payload"]["part_no"] == "MAT-1"


def test_connector_for_is_cached_per_outbox(tmp_path):
    path = str(tmp_path / "writeback_outbox.jsonl")
    assert connector_for(path) is connector_for(path)


def test_registered_intents_tiers_are_governed():
    """意图注册表只允许 tier 1/2（tier=0 只读意图不该走写回）。"""
    assert set(WRITEBACK_INTENTS) == {"erp.purchase_receipt", "erp.delivery_commit"}
    for intent, meta in WRITEBACK_INTENTS.items():
        assert meta["tier"] in (1, 2), intent
        if meta["tier"] == 1:
            assert meta["slot"], f"{intent} 的 tier=1 意图必须绑定 HITL 槽位"
