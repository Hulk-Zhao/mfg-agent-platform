"""生产化清单第 2 条：签名事实落独立表 + DB 级幂等（wf_signoff）。

守四件事：
1. 会签 / 终批 / 驳回都写 ``wf_signoff``（不再只存在于 governance_json）；
2. 重复签名由唯一索引拦截（同一角色+同一人 / 同一自然人跨角色）；
3. 改选处置动作 → 旧会签账本行同步失效；
4. 老工单（未走治理链）不受影响。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import mfg_agents.v2 as v2_pkg
from mfg_agents.v2.contracts.permissions import reset_permission_registry
from mfg_agents.v2.services import workflow_repository as repo_mod
from mfg_agents.v2.services.workflow import SignoffConflict, WorkflowEngine

SCENARIO_DIR = Path(v2_pkg.__file__).resolve().parent / "scenarios"

SQE_CTX: dict[str, Any] = {"user_id": "u-sqe", "dept": "quality", "roles": ["quality"]}
PQE_CTX: dict[str, Any] = {"user_id": "u-pqe", "dept": "quality", "roles": ["pqe"]}
BIZ_CTX: dict[str, Any] = {"user_id": "u-biz", "dept": "sales", "roles": ["business"]}
QA_CTX: dict[str, Any] = {"user_id": "u-qa", "dept": "quality", "roles": ["qa"]}
QD_CTX: dict[str, Any] = {"user_id": "u-qd", "dept": "quality", "roles": ["质量总监"]}


def _actions() -> list[dict[str, Any]]:
    data = yaml.safe_load(
        (SCENARIO_DIR / "incoming_quality_v2.yaml").read_text(encoding="utf-8"))
    step = next(s for s in data["steps"] if s.get("id") == "hitl_disposition")
    return step["actions"]


@pytest.fixture()
def engine(tmp_path) -> WorkflowEngine:
    reset_permission_registry()
    return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")


def _make_task(engine: WorkflowEngine) -> str:
    task = engine.create_task(
        wf_id="hitl_disposition", title="退换货/特采决策确认",
        context={"run_id": "run-sign", "slot": "disposition",
                 "kind": "disposition", "actions": _actions()},
        assignees=["SQE", "PQE", "生产"], hitl=True)
    return task.task_uid


def _select_concession(engine: WorkflowEngine, uid: str) -> None:
    engine.select_action(uid, "concession", "王倩", user_ctx=SQE_CTX,
                         comment="加严检验 + 客户告知，风险可控")


def _sign_all(engine: WorkflowEngine, uid: str) -> None:
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)


def test_cosign_and_approve_write_signoff_ledger(engine):
    uid = _make_task(engine)
    _select_concession(engine, uid)
    _sign_all(engine, uid)
    engine.approve(uid, "李静", user_ctx=QD_CTX, final=True,
                   decision="特采（让步接收）")

    rows = engine.repository.list_signoffs(uid)
    assert [r["verb"] for r in rows] == ["cosign", "cosign", "cosign", "final_approve"]
    assert {r["role_key"] for r in rows[:3]} == {"pqe", "sales", "qa"}
    assert {r["actor_id"] for r in rows[:3]} == {"u-pqe", "u-biz", "u-qa"}
    assert rows[-1]["role_key"] == "quality_director"
    assert rows[-1]["decision"] == "特采（让步接收）"
    # 审计字段形状：一人一签/代签字段位仍保留
    assert all("on_behalf_of" in r and "overdue_signoff" in r for r in rows)


def test_duplicate_signoff_is_blocked_by_unique_index(engine):
    uid = _make_task(engine)
    _select_concession(engine, uid)
    base = {
        "task_uid": uid, "role_key": "pqe", "role_name": "PQE",
        "actor": "王倩", "actor_id": "u-pqe", "actor_kind": "human",
        "verb": "cosign", "decision": "同意", "comment": "",
        "on_behalf_of": None, "overdue_signoff": 0, "ts": "2026-10-23T00:00:00+00:00",
    }
    repo = engine.repository
    assert repo.insert_signoff(base) is True
    assert repo.insert_signoff(dict(base)) is False  # 同角色+同一人
    # 一人一签：同自然人换角色再来一次也必须被拦
    assert repo.insert_signoff({**base, "role_key": "qa",
                                "role_name": "QA"}) is False


def test_duplicate_cosign_raises_signoff_conflict(engine):
    uid = _make_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    # 同一自然人换角色再来一次：一人一签（actor_id 判定，不看姓名）
    with pytest.raises(SignoffConflict):
        engine.cosign(uid, "王倩", user_ctx={"user_id": "u-pqe", "roles": ["qa"]})


def test_reselect_action_clears_cosign_ledger(engine):
    uid = _make_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    assert len(engine.repository.list_signoffs(uid)) == 1

    engine.select_action(uid, "return_goods", "王倩", user_ctx=SQE_CTX,
                         comment="客户要求退货")
    assert engine.repository.list_signoffs(uid) == []


def test_reject_writes_reject_row(engine):
    uid = _make_task(engine)
    _select_concession(engine, uid)
    engine.reject(uid, "李静", user_ctx=QD_CTX, comment="客户不接受特采")

    rows = engine.repository.list_signoffs(uid)
    assert [r["verb"] for r in rows] == ["reject"]
    assert rows[0]["role_key"] == "quality_director"
    assert rows[0]["comment"] == "客户不接受特采"


def test_legacy_task_without_governance_writes_no_signoff(engine):
    task = engine.create_task("legacy_wf", "老工单", hitl=True)
    engine.approve(task.task_uid, "王制造")
    assert engine.repository.list_signoffs(task.task_uid) == []


def test_signoff_columns_are_stable():
    """契约列不得随实现漂移（下游审计/导出按列读取）。"""
    assert repo_mod.SIGNOFF_COLUMNS == (
        "task_uid", "role_key", "role_name", "actor", "actor_id", "actor_kind",
        "verb", "decision", "comment", "on_behalf_of", "overdue_signoff", "ts",
    )
    assert repo_mod.SIGNOFF_TABLE == "wf_signoff"
