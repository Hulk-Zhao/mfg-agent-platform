"""超时代签：fail-closed 授权、SLA 门控、签名账本字段（第三十四棒）。"""
from __future__ import annotations

import pytest

from mfg_agents.common.config import settings
from mfg_agents.v2.contracts.permissions import reset_permission_registry
from mfg_agents.v2.services.workflow import PermissionDenied, WorkflowEngine

SQE_CTX = {"user_id": "u-sqe", "dept": "quality", "roles": ["sqe"]}
QD_CTX = {"user_id": "u-qd", "dept": "quality", "roles": ["quality_director"]}
QA_CTX = {"user_id": "u-qm", "dept": "quality", "roles": ["qa_manager"]}
MFG_CTX = {"user_id": "u-mfg", "dept": "manufacturing",
           "roles": ["manufacturing"]}

ACTIONS = [{"key": "probe", "label": "探测动作",
            "propose": ["SQE"], "cosign": []}]


@pytest.fixture()
def engine(tmp_path) -> WorkflowEngine:
    reset_permission_registry()
    return WorkflowEngine(f"sqlite:///{tmp_path / 'wf.db'}")


def _task(engine: WorkflowEngine, *, sla_hours: float = -1.0) -> str:
    task = engine.create_task(
        "hitl_disposition", "超时代签测试",
        context={"slot": "disposition", "actions": ACTIONS},
        assignees=["SQE"], hitl=True, sla_hours=sla_hours)
    engine.select_action(task.task_uid, "probe", "王倩", user_ctx=SQE_CTX)
    return task.task_uid


def test_delegation_disabled_by_default(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "")
    state = engine.delegation_state(engine.get(_task(engine)), user_ctx=QD_CTX)
    assert state["enabled"] is False and state["allowed"] is False
    assert "HITL_DELEGATE_ROLES" in state["reason"]


def test_delegation_allowed_only_when_overdue(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    overdue = engine.delegation_state(engine.get(_task(engine)),
                                      user_ctx=QD_CTX)
    assert overdue["allowed"] is True and overdue["overdue"] is True

    fresh = engine.delegation_state(engine.get(_task(engine, sla_hours=1)),
                                    user_ctx=QD_CTX)
    assert fresh["allowed"] is False and "未超 SLA" in fresh["reason"]


def test_delegation_requires_configured_role_and_approve_permission(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    uid = _task(engine)
    # qa_manager 有 approve 权限但不在授权名单
    not_listed = engine.delegation_state(engine.get(uid), user_ctx=QA_CTX)
    assert not_listed["allowed"] is False and "授权名单" in not_listed["reason"]

    # manufacturing 在名单里但没有 approve 权限（仅会签）
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "manufacturing")
    no_perm = engine.delegation_state(engine.get(uid), user_ctx=MFG_CTX)
    assert no_perm["allowed"] is False and "终批权限" in no_perm["reason"]


def test_approve_with_delegation_records_ledger_and_governance(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    uid = _task(engine)
    task = engine.approve(uid, "赵总", comment="SQE 超时未处理，代签放行",
                          user_ctx=QD_CTX, on_behalf_of="SQE 王倩")
    assert task.status == "running"

    rows = engine.repository.list_signoffs(uid)
    assert len(rows) == 1
    assert rows[0]["on_behalf_of"] == "SQE 王倩"
    assert rows[0]["overdue_signoff"] in (1, True)

    approval = engine.get(uid).governance["approvals"][-1]
    assert approval["on_behalf_of"] == "SQE 王倩"
    assert approval["overdue_signoff"] is True


def test_delegation_requires_completed_cosign(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    task = engine.create_task(
        "hitl_disposition", "会签未完成",
        context={"slot": "disposition",
                 "actions": [{"key": "p", "label": "探测", "propose": ["SQE"],
                              "cosign": ["PQE"]}]},
        assignees=["SQE"], hitl=True, sla_hours=-1)
    engine.select_action(task.task_uid, "p", "王倩", user_ctx=SQE_CTX)
    state = engine.delegation_state(engine.get(task.task_uid), user_ctx=QD_CTX)
    assert state["allowed"] is False and "会签未完成" in state["reason"]


def test_delegation_tolerates_malformed_sla(engine, monkeypatch):
    """存量脏 SLA 不能让待办接口 500；按「未超时」诚实降级。"""
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    task = engine.get(_task(engine))
    task.sla_due_at = "not-a-date"
    state = engine.delegation_state(task, user_ctx=QD_CTX)
    assert state["allowed"] is False and state["overdue"] is False


def test_delegation_rejected_when_not_overdue(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    uid = _task(engine, sla_hours=1)
    with pytest.raises(PermissionDenied, match="未超 SLA"):
        engine.approve(uid, "赵总", comment="理由",
                       user_ctx=QD_CTX, on_behalf_of="SQE 王倩")


def test_delegation_rejected_without_authority(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "")
    uid = _task(engine)
    with pytest.raises(PermissionDenied, match="不可超时代签"):
        engine.approve(uid, "赵总", comment="理由",
                       user_ctx=QD_CTX, on_behalf_of="SQE 王倩")


def test_delegation_requires_reason(engine, monkeypatch):
    """``on_behalf_of`` 为空 = 普通终批；非空代签时理由必填。"""
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    uid = _task(engine)
    with pytest.raises(ValueError, match="必须填写理由"):
        engine.approve(uid, "赵总", comment="",
                       user_ctx=QD_CTX, on_behalf_of="SQE 王倩")


def test_delegation_rejects_self(engine, monkeypatch):
    monkeypatch.setattr(settings, "HITL_DELEGATE_ROLES", "quality_director")
    uid = _task(engine)
    with pytest.raises(ValueError, match="不能是本人"):
        engine.approve(uid, "赵总", comment="理由",
                       user_ctx=QD_CTX, on_behalf_of="赵总")


def test_normal_approve_keeps_empty_delegation_fields(engine):
    uid = _task(engine)
    engine.approve(uid, "赵总", comment="正常终批", user_ctx=QD_CTX)
    rows = engine.repository.list_signoffs(uid)
    assert rows[0]["on_behalf_of"] is None
    assert rows[0]["overdue_signoff"] in (0, False)
