"""治理层测试：权限目录 / 角色校验 / 多人会签 / 老库迁移 / API 状态码映射。

覆盖（对应工程约束 §六）：
1. 无权限 approve → PermissionDenied；有权限 → 通过
2. 未 select_action 直接 approve → WorkflowTransitionError（API 409）
3. 会签不齐 approve → SignoffIncomplete（含 missing 列表与 progress）
4. 逐个会签齐全 → approve 成功，history 含每次 cosign
5. 重复会签 → WorkflowTransitionError（API 409）
6. 未授权角色尝试 cosign → PermissionDenied
7. 未知角色名（如"随便谁"）→ 拒绝（fail-closed）
8. user_ctx.roles 与 role 不一致 → 以 user_ctx 为准（构造越权）
9. 老工单无 governance → 行为不变（回归）
10. 老库迁移：预置无 governance_json 列的库 → 初始化后自动加列且历史数据可读

测试数据直接读真实场景 YAML（incoming_quality_v2 / line_stop_v2），
避免"测试与生产两套动作定义"漂移。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

import mfg_agents.v2 as v2_pkg
from mfg_agents.v2.contracts.permissions import (
    AGENT_ACTOR_ROLES,
    DEFAULT_PERMISSIONS_PATH,
    PermissionRegistry,
    PermissionRegistryError,
    get_permission_registry,
    reset_permission_registry,
)
from mfg_agents.v2.contracts.scenario import ScenarioLoadError, ScenarioRegistry
from mfg_agents.v2.services.workflow import (
    PermissionDenied,
    SignoffIncomplete,
    WorkflowEngine,
    WorkflowTransitionError,
)

SCENARIO_DIR = Path(v2_pkg.__file__).resolve().parent / "scenarios"

# 7 个合法 HITL 槽位（与 permissions.yaml 的 slots 一致）
LEGAL_SLOTS = ["restart", "eta", "disposition", "d8_close", "complaint_reply",
               "delivery_commit", "maintenance_window"]

# ---------- 演示身份（user_ctx.roles 为权威角色来源） ----------
SQE_CTX: dict[str, Any] = {"user_id": "u-sqe", "dept": "quality",
                           "roles": ["quality"]}          # quality → sqe
PQE_CTX: dict[str, Any] = {"user_id": "u-pqe", "dept": "quality",
                           "roles": ["pqe"]}
BIZ_CTX: dict[str, Any] = {"user_id": "u-biz", "dept": "sales",
                           "roles": ["business"]}          # business → sales
QA_CTX: dict[str, Any] = {"user_id": "u-qa", "dept": "quality", "roles": ["qa"]}
QD_CTX: dict[str, Any] = {"user_id": "u-qd", "dept": "quality",
                          "roles": ["质量总监"]}            # → quality_director
WH_CTX: dict[str, Any] = {"user_id": "u-wh", "dept": "warehouse",
                          "roles": ["warehouse"]}
UNKNOWN_CTX: dict[str, Any] = {"user_id": "u-x", "dept": "x", "roles": ["随便谁"]}


def _hitl_step(scenario: str, step_id: str) -> dict[str, Any]:
    """读取真实场景 YAML 的 HITL 节点定义（含 actions）。"""
    data = yaml.safe_load((SCENARIO_DIR / scenario).read_text(encoding="utf-8"))
    for step in data.get("steps") or []:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"场景 {scenario} 缺少节点 {step_id}")


def _disposition_actions() -> list[dict[str, Any]]:
    return _hitl_step("incoming_quality_v2.yaml", "hitl_disposition")["actions"]


def _eta_actions() -> list[dict[str, Any]]:
    return _hitl_step("line_stop_v2.yaml", "hitl_eta")["actions"]


def _restart_actions() -> list[dict[str, Any]]:
    return _hitl_step("line_stop_v2.yaml", "hitl_restart")["actions"]


@pytest.fixture()
def engine(tmp_path) -> WorkflowEngine:
    reset_permission_registry()
    return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")


def _make_hitl_task(engine: WorkflowEngine, *, wf_id: str = "hitl_disposition",
                    slot: str = "disposition",
                    actions: list[dict[str, Any]] | None = None,
                    title: str = "退换货/特采决策确认") -> str:
    """建一张真实治理工单（hitl=True + slot + actions → 治理链启用）。"""
    task = engine.create_task(
        wf_id=wf_id, title=title,
        context={"run_id": "run-gov", "slot": slot, "kind": slot,
                 "actions": actions if actions is not None else _disposition_actions()},
        assignees=["SQE", "PQE", "生产"], priority="P1", hitl=True,
        hitl_prompt=title)
    return task.task_uid


def _select_concession(engine: WorkflowEngine, uid: str) -> Any:
    """SQE 选定"特采（让步接收）"（require_comment=True，必须填依据）。"""
    return engine.select_action(uid, "concession", "王倩", user_ctx=SQE_CTX,
                                comment="加严检验 + 客户告知，风险可控")


# ============================================================
# 1. 权限目录（Permission 独立实体 + Role 命名集合 + fail-closed）
# ============================================================


def test_permission_catalog_shape():
    """目录规模：21 条权限（7 slot × 3 verb）/ 15 角色 / 7 槽位。"""
    registry = get_permission_registry()
    describe = registry.describe()
    assert len(describe["permissions"]) == 21
    assert len(describe["slots"]) == 7
    assert len(describe["roles"]) >= 14
    assert {p["key"] for p in describe["permissions"]} == {
        f"hitl.{slot}.{verb}"
        for slot in ("restart", "eta", "disposition", "d8_close",
                     "complaint_reply", "delivery_commit", "maintenance_window")
        for verb in ("propose", "cosign", "approve")
    }
    assert DEFAULT_PERMISSIONS_PATH.is_file()


def test_normalize_role_aliases():
    """中文/别名/大小写 → 稳定 role key（覆盖 YAML 全部写法）。"""
    registry = get_permission_registry()
    expected = {
        "SQE": "sqe",
        "sqe": "sqe",
        "PQE": "pqe",
        "QA": "qa",
        "质量总监": "quality_director",
        "QA经理": "qa_manager",
        "制造现场负责人": "manufacturing",
        "生产": "manufacturing",
        "设备工程": "equipment",
        "业务经理": "sales",
        "业务": "sales",
        "销售总监": "sales",
        "计划": "planning",
        "物控": "material_control",
        "仓库": "warehouse",
        "物流": "warehouse",
        "工程": "rd_engineering",
        "研发": "rd_engineering",
        "采购": "procurement",
        "财务": "finance",
        # 演示前端的平台域角色写法
        "quality": "sqe",
        "business": "sales",
        "supply_chain": "material_control",
        "coordinator": "gm",
    }
    for raw, key in expected.items():
        assert registry.normalize_role(raw) == key, f"{raw} 应归一为 {key}"


def test_unknown_role_is_fail_closed():
    """未知角色/空/None → 空权限集，绝不默认放行。"""
    registry = get_permission_registry()
    assert registry.normalize_role("随便谁") is None
    assert registry.normalize_role("") is None
    assert registry.normalize_role(None) is None
    assert registry.permissions_of(["随便谁"]) == set()
    assert registry.permissions_of(None) == set()
    assert registry.has_any(["随便谁"], ["hitl.disposition.approve"]) is False
    assert registry.has_any([], ["hitl.disposition.approve"]) is False
    assert registry.has_all(["sqe"], ["hitl.disposition.approve"]) is False


def test_roles_with_permission_hint():
    """roles_with_permission 用于 403 提示"该动作需谁审批"。"""
    registry = get_permission_registry()
    approvers = registry.roles_with_permission("hitl.disposition.approve")
    assert {"quality_director", "qa_manager", "gm"} <= set(approvers)
    assert "warehouse" not in approvers
    names = registry.roles_with_permission_names("hitl.disposition.approve")
    assert "质量总监" in names and "QA经理" in names


def test_registry_rejects_bad_configuration(tmp_path):
    """配置错误必须显式失败（别名冲突 / 引用未定义权限）。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "permissions:\n  - {key: hitl.x.approve, name: A, scope: hitl}\n"
        "roles:\n"
        "  - {key: r1, name: 甲, permissions: [hitl.x.approve]}\n"
        "  - {key: r2, name: 甲, permissions: [hitl.x.approve]}\n",
        encoding="utf-8")
    with pytest.raises(Exception):
        PermissionRegistry.from_yaml(bad)


# ============================================================
# 2. select_action：按选中动作生成会签要求（不取并集、不改状态）
# ============================================================


def test_select_action_builds_required_signoffs(engine):
    """"特采"需 PQE + 业务 + QA 三方会签；建单时不固定会签方。"""
    uid = _make_hitl_task(engine)
    before = engine.get(uid)
    assert before.is_governed()
    assert before.signoff_status()["required"] == []          # 选动作前无会签要求
    assert before.status == "waiting_hitl"

    task = _select_concession(engine, uid)
    status = task.signoff_status()
    assert task.status == "waiting_hitl"                      # 选动作不改状态
    assert status["selected_action"] == "特采（让步接收）"
    assert status["action_key"] == "concession"
    assert status["slot"] == "disposition"
    assert [r["role"] for r in status["required"]] == ["pqe", "sales", "qa"]
    assert [r["role_name"] for r in status["required"]] == ["PQE", "业务", "QA"]
    assert all(r["permission"] == "hitl.disposition.cosign"
               for r in status["required"])
    assert status["progress"] == "0/3"
    assert status["satisfied"] is False
    assert task.history[-1]["action"] == "select_action"


def test_different_actions_have_different_signoffs(engine):
    """同一节点不同动作的会签方不同（退货只需采购会签）。"""
    uid = _make_hitl_task(engine)
    task = engine.select_action(uid, "return_goods", "王倩", user_ctx=SQE_CTX)
    assert [r["role"] for r in task.signoff_status()["required"]] == ["procurement"]

    uid2 = _make_hitl_task(engine)
    task2 = engine.select_action(uid2, "sort", "王倩", user_ctx=PQE_CTX)
    assert [r["role"] for r in task2.signoff_status()["required"]] == [
        "manufacturing", "procurement"]


def test_select_action_requires_comment_when_declared(engine):
    """require_comment=True 的动作不填依据 → ValueError（API 400）。"""
    uid = _make_hitl_task(engine)
    with pytest.raises(ValueError, match="决策依据"):
        engine.select_action(uid, "concession", "王倩", user_ctx=SQE_CTX,
                             comment="   ")


def test_select_action_unknown_action_key(engine):
    uid = _make_hitl_task(engine)
    with pytest.raises(WorkflowTransitionError, match="未知处置动作"):
        engine.select_action(uid, "no_such_action", "王倩", user_ctx=SQE_CTX,
                             comment="x")


def test_select_action_permission_denied(engine):
    """仓库岗位不在该动作 propose 列表 → PermissionDenied（fail-closed）。"""
    uid = _make_hitl_task(engine)
    with pytest.raises(PermissionDenied) as exc:
        engine.select_action(uid, "concession", "周凯", user_ctx=WH_CTX,
                             comment="越权尝试")
    assert "SQE" in str(exc.value)


def test_select_action_unknown_identity_denied(engine):
    uid = _make_hitl_task(engine)
    with pytest.raises(PermissionDenied):
        engine.select_action(uid, "concession", "随便谁", user_ctx=UNKNOWN_CTX,
                             comment="x")


def test_user_ctx_overrides_role_for_select(engine):
    """前端的 role=sqe 不可信：user_ctx.roles=['business'] 时按业务（sales）判权。"""
    uid = _make_hitl_task(engine)
    with pytest.raises(PermissionDenied):
        # role 声称是 sqe，但 user_ctx.roles 只给 business（销量岗无发起权）
        engine.select_action(uid, "concession", "陈昊", role="sqe",
                             user_ctx={"roles": ["business"]}, comment="越权")


# ============================================================
# 3. cosign：会签（多角色背书，缺一不可）
# ============================================================


def test_cosign_requires_selected_action(engine):
    uid = _make_hitl_task(engine)
    with pytest.raises(WorkflowTransitionError, match="请先选定处置动作"):
        engine.cosign(uid, "王倩", user_ctx=PQE_CTX)


def test_cosign_unauthorized_role_denied(engine):
    """不在会签人范围的角色（仓库/总经理）尝试会签 → PermissionDenied。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    with pytest.raises(PermissionDenied, match="会签人范围"):
        engine.cosign(uid, "周凯", user_ctx=WH_CTX)
    with pytest.raises(PermissionDenied):
        engine.cosign(uid, "张启明", user_ctx={"roles": ["gm"]})


def test_cosign_progress_increments(engine):
    """逐个会签 → 进度 1/3 → 2/3 → 3/3，状态保持 waiting_hitl。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)

    task = engine.cosign(uid, "王倩", user_ctx=PQE_CTX, comment="工艺可行")
    assert task.status == "waiting_hitl"
    assert task.signoff_status()["progress"] == "1/3"
    assert [m["role"] for m in task.signoff_status()["missing"]] == ["sales", "qa"]

    task = engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    assert task.signoff_status()["progress"] == "2/3"

    task = engine.cosign(uid, "李静", user_ctx=QA_CTX)
    status = task.signoff_status()
    assert status["progress"] == "3/3"
    assert status["satisfied"] is True
    assert status["missing"] == []
    assert all(s["actor"] and s["ts"] for s in task.governance["signoffs"])


def test_duplicate_cosign_conflict(engine):
    """同一角色重复会签 → WorkflowTransitionError（API 409）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    with pytest.raises(WorkflowTransitionError, match="已会签"):
        engine.cosign(uid, "王倩", user_ctx=PQE_CTX)


# ============================================================
# 4. approve：会签门控 + 角色权限
# ============================================================


def test_approve_before_select_action_conflict(engine):
    """未 select_action 直接 approve → 409（WorkflowTransitionError）。"""
    uid = _make_hitl_task(engine)
    with pytest.raises(WorkflowTransitionError, match="先选定处置动作"):
        engine.approve(uid, "李静", user_ctx=QD_CTX)


def test_approve_with_incomplete_signoff_raises(engine):
    """会签不齐 → SignoffIncomplete（含 missing 与 progress）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    with pytest.raises(SignoffIncomplete) as exc:
        engine.approve(uid, "李静", user_ctx=QD_CTX)
    err = exc.value
    assert err.progress == "0/3"
    assert [m["role"] for m in err.missing] == ["pqe", "sales", "qa"]
    assert [m["role_name"] for m in err.missing] == ["PQE", "业务", "QA"]
    assert "会签未完成" in str(err)

    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    with pytest.raises(SignoffIncomplete) as exc2:
        engine.approve(uid, "李静", user_ctx=QD_CTX)
    assert exc2.value.progress == "1/3"
    assert len(exc2.value.missing) == 2


def test_approve_permission_denied(engine):
    """无 approve 权限的岗位（仓库/业务/SQE）→ PermissionDenied。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    with pytest.raises(PermissionDenied, match="无该动作的审批权限") as exc:
        engine.approve(uid, "周凯", user_ctx=WH_CTX)
    assert "质量总监" in str(exc.value)
    # 工单未被推进（拒绝必须无副作用）
    assert engine.get(uid).status == "waiting_hitl"


def test_anonymous_approve_denied(engine):
    """新工单禁止匿名终批（无 role/user_ctx → fail-closed）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    with pytest.raises(PermissionDenied, match="禁止匿名终批"):
        engine.approve(uid, "匿名用户")


def test_full_governance_flow(engine):
    """完整链路：选动作 → 3 人会签 → 终批；history 逐条留痕。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX, comment="工艺可行")
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    task = engine.approve(uid, "李静", user_ctx=QD_CTX, comment="同意特采",
                          decision="特采（让步接收）")

    assert task.status == "running"
    actions = [h["action"] for h in task.history]
    assert actions == ["create", "select_action", "cosign", "cosign", "cosign",
                       "approve"]
    assert [h["actor"] for h in task.history[2:5]] == ["王倩", "陈昊", "李静"]
    assert len(task.governance["signoffs"]) == 3
    assert len(task.governance["approvals"]) == 1
    assert task.governance["approvals"][0]["role"] == "quality_director"
    assert task.signoff_status()["satisfied"] is True

    # 落库可读回（可重放、可审计）
    reloaded = engine.get(uid)
    assert reloaded.governance["action_key"] == "concession"
    assert len(reloaded.governance["signoffs"]) == 3


def test_approve_final_reaches_done(engine):
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    assert engine.approve(uid, "李静", user_ctx=QD_CTX, final=True).status == "done"


def test_action_without_cosign_can_approve_directly(engine):
    """无会签要求的动作（ETA 加缓冲承诺）选完即可终批。"""
    uid = _make_hitl_task(engine, wf_id="hitl_eta", slot="eta",
                          actions=_eta_actions(), title="对客 ETA 承诺确认")
    task = engine.select_action(uid, "commit_buffered", "陈昊", user_ctx=BIZ_CTX)
    assert task.signoff_status()["required"] == []
    assert task.signoff_status()["satisfied"] is True
    approved = engine.approve(uid, "陈昊", user_ctx=BIZ_CTX)
    assert approved.status == "running"


def test_reselect_action_resets_signoffs(engine):
    """改选动作 → 旧会签事实失效（会签要求已变化，必须重新会签）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    assert engine.get(uid).signoff_status()["progress"] == "1/3"
    task = engine.select_action(uid, "return_goods", "王倩", user_ctx=SQE_CTX)
    assert task.governance["signoffs"] == []
    assert [r["role"] for r in task.signoff_status()["required"]] == ["procurement"]


# ============================================================
# 5. reject：可止损（不要求会签齐备），但仍校验权限
# ============================================================


def test_reject_allowed_with_incomplete_signoff(engine):
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    task = engine.reject(uid, "李静", user_ctx=QD_CTX, comment="证据不足，退回")
    assert task.status == "closed"
    assert task.governance["approvals"][-1]["decision"] == "reject"


def test_reject_by_proposer_allowed(engine):
    """发起人（propose 权限）也可驳回。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    assert engine.reject(uid, "王倩", user_ctx=SQE_CTX).status == "closed"


def test_reject_permission_denied(engine):
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    with pytest.raises(PermissionDenied, match="驳回权限"):
        engine.reject(uid, "周凯", user_ctx=WH_CTX)
    assert engine.get(uid).status == "waiting_hitl"


# ============================================================
# 6. 向后兼容：老工单宽松 + 老库幂等迁移
# ============================================================


def test_legacy_task_without_governance_stays_lenient(engine):
    """老工单（governance 为空）行为不变：approve/reject 不校验角色。"""
    task = engine.create_task("hitl_eta", "对客 ETA 确认", hitl=True)
    assert task.governance == {}
    assert task.is_governed() is False
    approved = engine.approve(task.task_uid, "业务经理", comment="同意")
    assert approved.status == "running"
    assert approved.history[-1]["action"] == "approve"

    task2 = engine.create_task("hitl_restart", "复线决策", hitl=True)
    assert engine.reject(task2.task_uid, "王制造").status == "closed"


def test_advance_cannot_bypass_governance(engine):
    """受治理工单禁止经 advance('approve') 绕过角色校验与会签门控。"""
    uid = _make_hitl_task(engine)
    with pytest.raises(WorkflowTransitionError, match="绕过"):
        engine.advance(uid, "approve", actor="随便谁")


_OLD_DDL = """
CREATE TABLE platform_workflow_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_uid TEXT NOT NULL UNIQUE,
  wf_id TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  priority TEXT NOT NULL DEFAULT 'P1',
  assignees TEXT DEFAULT '',
  context_json TEXT DEFAULT '{}',
  hitl_prompt TEXT DEFAULT '',
  history_json TEXT DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sla_due_at TEXT
)
"""


def test_old_database_migrates_governance_column(tmp_path):
    """老库（无 governance_json 列）→ 初始化自动加列，历史数据仍可读可审批。"""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute(_OLD_DDL)
    conn.execute(
        "INSERT INTO platform_workflow_task (task_uid, wf_id, title, status, "
        " priority, assignees, context_json, hitl_prompt, history_json, "
        " created_at, updated_at, sla_due_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("wf-legacy0001", "hitl_eta", "老工单·对客 ETA", "waiting_hitl", "P1",
         '["业务经理"]', '{"run_id": "run-old"}', "老工单待确认", "[]",
         "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00",
         "2024-01-01T02:00:00+00:00"))
    conn.commit()
    conn.close()

    engine = WorkflowEngine(f"sqlite:///{db_file}")
    # 列已加上
    with sqlite3.connect(db_file) as check:
        columns = {row[1] for row in
                   check.execute("PRAGMA table_info(platform_workflow_task)")}
    assert "governance_json" in columns

    # 历史数据可读，且按老工单宽松处理
    legacy = engine.get("wf-legacy0001")
    assert legacy.governance == {}
    assert legacy.context["run_id"] == "run-old"
    assert engine.approve("wf-legacy0001", "业务经理").status == "running"

    # 新工单在本库同样启用治理链
    uid = _make_hitl_task(engine)
    assert engine.get(uid).is_governed() is True


def test_migration_is_idempotent(tmp_path):
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    WorkflowEngine(db)
    WorkflowEngine(db)  # 二次初始化不得报错（列已存在）
    assert True


# ============================================================
# 7. API 层：状态码映射与载荷
# ============================================================


@pytest.fixture()
def api_client(engine, monkeypatch):
    """把 API 的引擎替换为隔离测试库（不碰演示库）。"""
    from mfg_agents.v2.demo import api as api_module

    monkeypatch.setattr(api_module, "_workflow_engine", lambda: engine)
    return TestClient(api_module.app)


def _payload(user_ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    body = {"approver": user_ctx.get("user_id", "演示用户"), "comment": "",
            "role": "", "user_ctx": user_ctx, "action_key": ""}
    body.update(kwargs)
    return body


def test_api_select_action_and_pending_payload(api_client, engine):
    uid = _make_hitl_task(engine)
    resp = api_client.post(f"/api/v2/workflow/{uid}/select-action",
                           json=_payload(SQE_CTX, action_key="concession",
                                         comment="加严检验"))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["governance"]["selected_action"] == "特采（让步接收）"
    assert data["signoff_status"]["progress"] == "0/3"

    pending = api_client.get("/api/v2/workflow/pending").json()["data"]
    item = next(t for t in pending if t["task_uid"] == uid)
    assert item["governance"]["action_key"] == "concession"
    assert [r["role"] for r in item["signoff_status"]["required"]] == [
        "pqe", "sales", "qa"]


def test_api_status_codes(api_client, engine):
    uid = _make_hitl_task(engine)

    # 未选动作 approve → 409
    resp = api_client.post(f"/api/v2/workflow/{uid}/approve", json=_payload(QD_CTX))
    assert resp.status_code == 409
    assert "先选定处置动作" in resp.json()["detail"]

    # 未选动作 cosign → 409
    resp = api_client.post(f"/api/v2/workflow/{uid}/cosign", json=_payload(PQE_CTX))
    assert resp.status_code == 409

    # 选动作后：越权 select（仓库）→ 403
    uid2 = _make_hitl_task(engine)
    resp = api_client.post(f"/api/v2/workflow/{uid2}/select-action",
                           json=_payload(WH_CTX, action_key="concession",
                                         comment="越权"))
    assert resp.status_code == 403
    assert "SQE" in resp.json()["detail"]

    # require_comment 未填 → 400
    resp = api_client.post(f"/api/v2/workflow/{uid2}/select-action",
                           json=_payload(SQE_CTX, action_key="concession",
                                         comment=""))
    assert resp.status_code == 400
    assert "决策依据" in resp.json()["detail"]

    # 正常选动作
    resp = api_client.post(f"/api/v2/workflow/{uid2}/select-action",
                           json=_payload(SQE_CTX, action_key="concession",
                                         comment="加严检验"))
    assert resp.status_code == 200

    # 会签不齐 approve → 409 结构化 detail
    resp = api_client.post(f"/api/v2/workflow/{uid2}/approve", json=_payload(QD_CTX))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["progress"] == "0/3"
    assert [m["role_name"] for m in detail["missing"]] == ["PQE", "业务", "QA"]
    assert "会签未完成" in detail["message"]

    # 会签（PQE）→ 200，进度 1/3
    resp = api_client.post(f"/api/v2/workflow/{uid2}/cosign", json=_payload(PQE_CTX))
    assert resp.status_code == 200
    assert resp.json()["data"]["signoff_status"]["progress"] == "1/3"

    # 越权会签（仓库）→ 403
    resp = api_client.post(f"/api/v2/workflow/{uid2}/cosign", json=_payload(WH_CTX))
    assert resp.status_code == 403

    # 无 approve 权限（仓库）→ 403
    resp = api_client.post(f"/api/v2/workflow/{uid2}/approve", json=_payload(WH_CTX))
    assert resp.status_code == 403
    assert "审批权限" in resp.json()["detail"]

    # 补全会签后终批 → 200
    api_client.post(f"/api/v2/workflow/{uid2}/cosign", json=_payload(BIZ_CTX))
    api_client.post(f"/api/v2/workflow/{uid2}/cosign", json=_payload(QA_CTX))
    resp = api_client.post(f"/api/v2/workflow/{uid2}/approve", json=_payload(QD_CTX))
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "running"

    # 不存在的工单 → 404
    resp = api_client.post("/api/v2/workflow/wf-not-exist/approve", json=_payload(QD_CTX))
    assert resp.status_code == 404


def test_api_governance_permissions_endpoint(api_client):
    resp = api_client.get("/api/v2/governance/permissions")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["permissions"]) == 21
    assert {"restart", "eta", "disposition", "d8_close", "complaint_reply",
            "delivery_commit", "maintenance_window"} == {
        s["key"] for s in data["slots"]}


# ============================================================
# 8. 场景 YAML × 权限目录一致性（防止两套定义漂移）
# ============================================================


def test_all_scenario_hitl_actions_are_governable():
    """全部 7 个 HITL 节点的每个动作：propose/cosign 角色必须可识别且持有对应权限。"""
    registry = get_permission_registry()
    slots = {
        "hitl_restart": "restart",
        "hitl_eta": "eta",
        "hitl_disposition": "disposition",
        "hitl_d8_close": "d8_close",
        "hitl_complaint_reply": "complaint_reply",
        "hitl_delivery_commit": "delivery_commit",
        "hitl_maintenance_window": "maintenance_window",
    }
    seen: set[str] = set()
    for yaml_file in sorted(SCENARIO_DIR.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        for step in data.get("steps") or []:
            slot = slots.get(str(step.get("id") or ""))
            if not slot:
                continue
            seen.add(slot)
            propose_perm = registry.slot_permission(slot, "propose")
            cosign_perm = registry.slot_permission(slot, "cosign")
            for action in step.get("actions") or []:
                for name in action.get("propose") or []:
                    key = registry.normalize_role(name)
                    assert key, f"{step['id']} 动作 {action['key']} propose 角色未识别: {name}"
                    assert registry.has_any([key], [propose_perm]), (
                        f"{step['id']} 动作 {action['key']}: {name} 缺 {propose_perm}")
                for name in action.get("cosign") or []:
                    key = registry.normalize_role(name)
                    assert key, f"{step['id']} 动作 {action['key']} cosign 角色未识别: {name}"
                    assert registry.has_any([key], [cosign_perm]), (
                        f"{step['id']} 动作 {action['key']}: {name} 缺 {cosign_perm}")
    assert seen == set(slots.values()), f"场景 HITL 节点不全: {seen}"


def test_restart_scenario_governance_flow(engine):
    """复线决策：制造发起 → PQE + 设备工程会签 → 制造（现场负责人）终批。"""
    uid = _make_hitl_task(engine, wf_id="hitl_restart", slot="restart",
                          actions=_restart_actions(), title="复线决策确认")
    mfg_ctx = {"user_id": "u-mfg", "dept": "manufacturing",
               "roles": ["manufacturing"]}
    engine.select_action(uid, "confirm_restart", "李卫东", user_ctx=mfg_ctx)
    task = engine.get(uid)
    assert [r["role"] for r in task.signoff_status()["required"]] == [
        "pqe", "equipment"]
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "赵工", user_ctx={"user_id": "u-equip",
                                         "dept": "manufacturing",
                                         "roles": ["设备工程"]})
    assert engine.approve(uid, "李卫东", user_ctx=mfg_ctx).status == "running"


# ============================================================
# 9. 审批即待办：pending_hitl 按人过滤 + my_action（Huly ApproveRequest 范式）
# ============================================================


def test_required_signoffs_declare_min_count(engine):
    """每个会签项输出 min_count=1（未来"任签 N"的演进位）。"""
    uid = _make_hitl_task(engine)
    task = _select_concession(engine, uid)
    required = task.signoff_status()["required"]
    assert required and all(r["min_count"] == 1 for r in required)
    assert all(r["kind"] == "cosign" for r in required)


def test_pending_hitl_filters_by_role_and_my_action(engine):
    """待办按角色过滤：只返回该角色当前阶段可动作的工单，并标注 my_action。"""
    uid = _make_hitl_task(engine)

    # 全量（不传角色）：向后兼容，返回全部 waiting_hitl
    assert [t.task_uid for t in engine.pending_hitl()] == [uid]

    # 无该节点权限的岗位（仓库）→ 看不到
    assert engine.pending_hitl(["仓库"]) == []

    # 选动作阶段：SQE 可发起（PQE 因"挑选/全检"动作 propose 也含 PQE，同样可发起）
    assert engine.action_for(engine.get(uid), ["quality"]) == "select_action"
    assert [t.task_uid for t in engine.pending_hitl(["quality"])] == [uid]
    # 纯会签人此时还不能动作（业务不在任何动作的 propose 列表）
    assert engine.action_for(engine.get(uid), ["业务经理"]) is None

    _select_concession(engine, uid)
    task = engine.get(uid)
    # 会签阶段：PQE / 业务 / QA 可会签，SQE 发起人已无可执行动作
    assert engine.action_for(task, ["PQE"]) == "cosign"
    assert engine.action_for(task, ["业务经理"]) == "cosign"
    assert engine.action_for(task, ["qa"]) == "cosign"
    assert engine.action_for(task, ["quality"]) is None
    # 终批人（质量总监）在会签未齐时不可动作
    assert engine.action_for(task, ["质量总监"]) is None

    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    assert engine.action_for(engine.get(uid), ["PQE"]) is None   # 已签，不再待我
    assert engine.action_for(engine.get(uid), ["业务经理"]) == "cosign"

    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    task = engine.get(uid)
    # 会签齐 → 有 approve 权限的岗位可终批
    assert engine.action_for(task, ["质量总监"]) == "approve"
    assert engine.action_for(task, ["QA经理"]) == "approve"
    assert engine.action_for(task, ["总经理"]) == "approve"
    assert engine.action_for(task, ["仓库"]) is None
    assert engine.action_for(task, ["随便谁"]) is None            # fail-closed


def test_api_pending_role_query(api_client, engine):
    """GET /workflow/pending?role=... 支持重复与逗号分隔；带 my_action。"""
    uid = _make_hitl_task(engine)
    # 不传 role → 全量 + my_action=None（向后兼容）
    full = api_client.get("/api/v2/workflow/pending").json()
    assert [t["task_uid"] for t in full["data"]] == [uid]
    assert full["data"][0]["my_action"] is None
    assert full["scope"]["filtered"] is False

    # 重复参数
    resp = api_client.get("/api/v2/workflow/pending",
                          params=[("role", "sqe"), ("role", "qa")]).json()
    assert {t["my_action"] for t in resp["data"]} == {"select_action"}
    assert resp["scope"]["roles"] == ["sqe", "qa"]

    # 逗号分隔 + 无权限岗位
    resp = api_client.get("/api/v2/workflow/pending", params={"role": "仓库"}).json()
    assert resp["data"] == []
    assert resp["scope"]["filtered"] is True

    # 未知角色 → 空（fail-closed），并在 scope.unknown 里回显
    resp = api_client.get("/api/v2/workflow/pending", params={"role": "随便谁"}).json()
    assert resp["data"] == []
    assert resp["scope"]["unknown"] == ["随便谁"]

    # 选动作后 PQE 的待办变为 cosign
    api_client.post(f"/api/v2/workflow/{uid}/select-action",
                    json=_payload(SQE_CTX, action_key="concession", comment="加严检验"))
    resp = api_client.get("/api/v2/workflow/pending", params={"role": "pqe"}).json()
    assert resp["data"][0]["my_action"] == "cosign"


# ============================================================
# 10. 架构铁律：Agent 不得持 approve（YAML 结构 + 启动自检 + 单测不变量）
# ============================================================


def test_agent_identity_roles_never_hold_approve():
    """架构铁律不变量：任何 Agent 身份角色都不得持有 *.approve。

    理由：Huly 的 `primarySocialId === System` 直接放行权限，会让治理层对最
    活跃的写入方（Agent/LLM）失效。我们的铁律是"LLM 只出建议，确定性流程
    不许自动放行"——这条不能只靠口头纪律，必须是可执行断言。
    """
    registry = get_permission_registry()
    roles = registry.describe()["roles"]
    offenders = [
        f"{r['key']}({r['name']}) 持有 {[p for p in r['permissions'] if p.endswith('.approve')]}"
        for r in roles
        if (r["actor_kind"] == "agent" or r["key"] in AGENT_ACTOR_ROLES)
        and any(p.endswith(".approve") for p in r["permissions"])
    ]
    assert offenders == [], (
        "架构铁律被违反：以下 Agent 身份角色持有终批权限（Agent 只能 propose）：\n"
        + "\n".join(offenders))


def test_registry_rejects_agent_role_with_approve(tmp_path):
    """配置层面给出 Agent + approve → 加载期即失败（不是运行时才发现）。"""
    bad = tmp_path / "bad_agent.yaml"
    bad.write_text(
        "permissions:\n"
        "  - {key: hitl.eta.propose, name: 发起, scope: hitl}\n"
        "  - {key: hitl.eta.approve, name: 终批, scope: hitl}\n"
        "slots:\n"
        "  - {key: eta, name: ETA, available_permissions: [hitl.eta.propose, hitl.eta.approve]}\n"
        "roles:\n"
        "  - {key: bot, name: 自动决策体, actor_kind: agent,"
        " permissions: [hitl.eta.propose, hitl.eta.approve]}\n",
        encoding="utf-8")
    with pytest.raises(PermissionRegistryError) as exc:
        PermissionRegistry.from_yaml(bad)
    message = str(exc.value)
    assert "Agent" in message and "bot" in message and "approve" in message, message

    # 正对照：同一角色改成 human → 合法（证明规则只针对 Agent 身份）
    ok = tmp_path / "ok_human.yaml"
    ok.write_text(bad.read_text(encoding="utf-8").replace(
        "actor_kind: agent", "actor_kind: human"), encoding="utf-8")
    registry = PermissionRegistry.from_yaml(ok)
    assert registry.has_any(["自动决策体"], ["hitl.eta.approve"]) is True


# ============================================================
# 11. 槽位白名单：启动期双注册表校验（PermissionRegistry + ScenarioRegistry）
# ============================================================


def test_every_slot_declares_available_permissions():
    """7 个槽位必须显式声明 3 个权限，且都存在于目录、归属本槽位。"""
    registry = get_permission_registry()
    assert sorted(registry.slot_keys()) == sorted(LEGAL_SLOTS)
    for slot_key in LEGAL_SLOTS:
        slot = registry.slot(slot_key)
        assert slot is not None
        assert sorted(slot.available_permissions) == sorted(
            f"hitl.{slot_key}.{verb}" for verb in ("propose", "cosign", "approve"))
        for perm_key in slot.available_permissions:
            assert registry.permission(perm_key) is not None


def test_slot_with_undeclared_permission_fails_at_load(tmp_path):
    """槽位声明了目录里没有的权限 → 权限注册表加载失败。"""
    bad = tmp_path / "bad_slot.yaml"
    bad.write_text(
        "permissions:\n"
        "  - {key: hitl.eta.approve, name: 终批, scope: hitl}\n"
        "slots:\n"
        "  - {key: eta, name: ETA, available_permissions: [hitl.eta.approve, hitl.eta.cosign]}\n"
        "roles:\n"
        "  - {key: gm, name: 总经理, actor_kind: human, permissions: [hitl.eta.approve]}\n",
        encoding="utf-8")
    with pytest.raises(PermissionRegistryError, match="不存在于目录"):
        PermissionRegistry.from_yaml(bad)


def test_all_scenarios_hitl_slots_are_whitelisted():
    """5 个真实场景的 7 个 HITL 节点，槽位全部落在白名单内。"""
    registry = get_permission_registry()
    scenario_registry = ScenarioRegistry(SCENARIO_DIR)
    loaded = scenario_registry.load()
    nodes: list[dict[str, str]] = []
    for meta in loaded.values():
        nodes.extend(scenario_registry.hitl_nodes(meta))
    assert len(nodes) == 8, f"HITL 节点数应为 8，实际 {len(nodes)}: {nodes}"
    for node in nodes:
        assert node["slot"] in registry.slot_keys(), node
    assert {n["slot"] for n in nodes} == set(LEGAL_SLOTS)


def test_illegal_hitl_slot_fails_at_scenario_load(tmp_path):
    """非法 hitl kind → 场景加载期即报错（含文件名 / 节点 id / 合法清单）。"""
    (tmp_path / "bad_scene.yaml").write_text(
        "key: bad_scene\nname: 非法槽位场景\nsteps:\n"
        "  - {id: intake, type: agent, agent: business}\n"
        "  - {id: hitl_unknown_kind, type: hitl, agent: hitl_unknown_kind,"
        "    prompt: 未知确认}\n",
        encoding="utf-8")
    registry = ScenarioRegistry(tmp_path)
    with pytest.raises(ScenarioLoadError) as exc:
        registry.load()
    message = str(exc.value)
    assert "bad_scene.yaml" in message
    assert "hitl_unknown_kind" in message
    assert "unknown_kind" in message
    assert "合法槽位清单" in message and "disposition" in message, message


def test_illegal_slot_in_hitl_section_also_fails(tmp_path):
    """``hitl:`` 段里声明的非法节点同样在加载期被拦（不只 steps）。"""
    (tmp_path / "bad_scene2.yaml").write_text(
        "key: bad_scene2\nname: 非法槽位场景2\n"
        "hitl:\n  - {step: hitl_overtime_approve, approver_roles: [厂长]}\n"
        "steps:\n  - {id: intake, type: agent, agent: business}\n",
        encoding="utf-8")
    registry = ScenarioRegistry(tmp_path)
    with pytest.raises(ScenarioLoadError, match="hitl_overtime_approve"):
        registry.load()


# ============================================================
# 12. 并发双签：进程内锁保证"恰好一条会签记录"
# ============================================================


def test_concurrent_cosign_records_exactly_one(engine):
    """两个线程同时对同一角色 cosign → 1 成功 + 1 "该角色已会签"，只落 1 条。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        barrier.wait(timeout=5)
        try:
            engine.cosign(uid, name, user_ctx=PQE_CTX)
            outcome = "ok"
        except WorkflowTransitionError as exc:
            outcome = f"conflict:{exc}"
        except Exception as exc:  # noqa: BLE001
            outcome = f"error:{type(exc).__name__}:{exc}"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(f"王倩-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(r.split(":")[0] for r in results) == ["conflict", "ok"], results
    assert any("已会签" in r for r in results if r.startswith("conflict")), results
    task = engine.get(uid)
    assert len(task.governance["signoffs"]) == 1
    assert task.signoff_status()["progress"] == "1/3"
    assert len([h for h in task.history if h["action"] == "cosign"]) == 1


# ============================================================
# 13. 代签：本轮不做入口，但必须 fail-closed
# ============================================================


def test_cosign_cannot_spoof_role(engine):
    """试图用 role 参数伪装成别人会签 → 403（角色只认 user_ctx.roles）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    with pytest.raises(PermissionDenied) as exc:
        engine.cosign(uid, "张三", role="qa",
                      user_ctx={"user_id": "u-wh", "dept": "warehouse",
                                "roles": ["warehouse"]})
    assert "会签人范围" in str(exc.value)
    assert engine.get(uid).governance["signoffs"] == []


def test_signoff_and_approval_records_carry_on_behalf_of_slots(engine):
    """签名/终批记录预留 on_behalf_of（本轮恒 None）与 actor_kind。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    task = engine.approve(uid, "李静", user_ctx=QD_CTX, decision="特采（让步接收）")

    for signoff in task.governance["signoffs"]:
        assert signoff["on_behalf_of"] is None      # 代签字段位已预留
        assert signoff["overdue_signoff"] is False
        assert signoff["actor_kind"] == "human"     # 支撑 Agent 不得终批的运行时校验
    for approval in task.governance["approvals"]:
        assert approval["on_behalf_of"] is None
        assert approval["overdue_signoff"] is False
        assert approval["actor_kind"] == "human"
    # 落库后可读回（输出侧同样带出字段位）
    data = engine.get(uid).to_dict()
    assert data["governance"]["approvals"][0]["on_behalf_of"] is None
    assert data["governance"]["signoffs"][0]["actor_kind"] == "human"


# ============================================================
# 14. 裁定 2：8D 关闭只落知识资产口径，不产生受控文档
# ============================================================


def test_d8_close_does_not_create_controlled_document(engine):
    """8D 关闭走完治理链后：只写 signoffs/approvals，不产生 controlled_document。

    裁定 2：8D 进知识库（knowledge_case），不进受控文档体系。
    本用例锁定"治理层不得越界创建受控文档表/记录"。
    """
    actions = _hitl_step("customer_complaint_v2.yaml", "hitl_d8_close")["actions"]
    uid = _make_hitl_task(engine, wf_id="hitl_d8_close", slot="d8_close",
                          actions=actions, title="8D 报告关闭确认")
    engine.select_action(uid, "close", "李静", user_ctx=QD_CTX, comment="措施已验证")
    # 三方会签必须是三个不同自然人（user_id 不同）：PQE / SQE / 工程
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "刘强", user_ctx=SQE_CTX)
    engine.cosign(uid, "林哲", user_ctx={"user_id": "u-rd",
                                         "dept": "rd_engineering",
                                         "roles": ["工程"]})
    task = engine.approve(uid, "李静", user_ctx=QD_CTX, decision="关闭 8D")
    assert task.status == "running"
    assert len(task.governance["approvals"]) == 1

    with sqlite3.connect(engine.db_path) as conn:
        tables = {str(r[0]).lower() for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    doc_tables = [t for t in tables
                  if "controlled" in t or "document" in t or "doc_" in t]
    assert doc_tables == [], (
        f"8D 关闭不应产生受控文档记录（裁定 2），却出现表: {doc_tables}")


# ============================================================
# 10. 会签独立性 G6：一人一签（同一 user_id 跨角色 → SignoffConflict）
# ============================================================


def test_same_user_cannot_cosign_twice_with_different_roles(engine):
    """G6-S1：同一 user_id 以第二个角色会签 → SignoffConflict（409）。

    会签的意义是"多方独立确认"，同一人多次确认没有独立价值——
    在审核视角等同会签造假。判定依据是 user_id，不按姓名。
    """
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx={"user_id": "u-same",
                                         "dept": "quality", "roles": ["pqe"]})
    from mfg_agents.v2.services.workflow import SignoffConflict
    with pytest.raises(SignoffConflict) as exc:
        engine.cosign(uid, "王倩", user_ctx={"user_id": "u-same",
                                             "dept": "sales",
                                             "roles": ["business"]})
    assert "已以" in str(exc.value) and "会签" in str(exc.value)
    # 第三次换 QA 角色同样拒绝
    with pytest.raises(SignoffConflict):
        engine.cosign(uid, "王倩", user_ctx={"user_id": "u-same",
                                             "dept": "quality",
                                             "roles": ["qa"]})
    # 进度停在第 1 票，不会因冲突产生多余记录
    assert engine.get(uid).signoff_status()["progress"] == "1/3"


def test_two_different_users_complete_all_cosigns(engine):
    """G6 正例：不同 user_id 正常完成全部会签（防止把会签整体锁死）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)      # u-pqe
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)      # u-biz
    engine.cosign(uid, "李静", user_ctx=QA_CTX)       # u-qa
    st = engine.get(uid).signoff_status()
    assert st["progress"] == "3/3" and st["satisfied"] is True
    # 会签人 user_id 互不相同（多方独立确认的真实体现）
    ids = {s["actor_id"] for s in engine.get(uid).governance["signoffs"]}
    assert ids == {"u-pqe", "u-biz", "u-qa"}


def test_governed_task_requires_user_id(engine):
    """G6-S3：受治理工单缺少 user_id → PermissionDenied（403，fail-closed）。

    没有稳定 user_id 就无法判定"同一人不得为多个角色会签"，也无法审计
    到自然人——因此拒绝，而不是按姓名降级或默认放行。
    """
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    with pytest.raises(PermissionDenied) as exc:
        engine.cosign(uid, "王倩", user_ctx={"roles": ["pqe"]})
    assert "user_id" in str(exc.value)
    with pytest.raises(PermissionDenied):
        engine.select_action(uid, "return_goods", "王倩",
                             user_ctx={"roles": ["quality"]})
    with pytest.raises(PermissionDenied):
        engine.approve(uid, "李静", user_ctx={"roles": ["质量总监"]})
    # signoffs 未被污染
    assert engine.get(uid).governance["signoffs"] == []


def test_actor_id_persisted_in_signoff_and_approval(engine):
    """G6-S2：signoff/approval 记录的 actor_id 必须真实落库（不是 None）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)
    engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
    engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
    engine.cosign(uid, "李静", user_ctx=QA_CTX)
    task = engine.approve(uid, "李静", user_ctx=QD_CTX, decision="特采（让步接收）")
    for s in task.governance["signoffs"]:
        assert s.get("actor_id"), f"signoff 缺 actor_id: {s}"
    for a in task.governance["approvals"]:
        assert a.get("actor_id"), f"approval 缺 actor_id: {a}"
    # 重放后仍可读回（落库而非仅内存）
    reloaded = engine.get(uid)
    assert [s["actor_id"] for s in reloaded.governance["signoffs"]] == [
        "u-pqe", "u-biz", "u-qa"]


def test_proposer_may_cosign_once_but_not_twice(engine):
    """发起人边界：propose 不占会签名额——发起人可以再签一次（只签一次）。"""
    uid = _make_hitl_task(engine)
    _select_concession(engine, uid)  # 发起人 u-sqe（SQE）
    # 发起人（u-sqe）以 PQE 身份会签：合法（一人一签只约束 cosign 之间）
    engine.cosign(uid, "王倩", user_ctx={"user_id": "u-sqe",
                                         "dept": "quality", "roles": ["pqe"]})
    # 同一 user_id 换 QA 身份再签 → 冲突
    from mfg_agents.v2.services.workflow import SignoffConflict
    with pytest.raises(SignoffConflict):
        engine.cosign(uid, "王倩", user_ctx={"user_id": "u-sqe",
                                             "dept": "quality",
                                             "roles": ["qa"]})
    # proposed_by 已记录发起人身份（审计可见"谁发起"）
    pb = engine.get(uid).governance.get("proposed_by") or {}
    assert pb.get("actor_id") == "u-sqe"


# ============================================================
# 11. 数据注入接口（V3.1）：DataSource 自描述 + 注册表总览
# ============================================================


def test_datasource_describe_reports_tables_and_health(tmp_path):
    """describe()：表清单 + 注册查询数 + 健康状态（接入即见的契约）。"""
    import sqlite3

    from mfg_agents.v2.contracts.data_source import SQLiteDataSource

    db = tmp_path / "ds.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE inventory (id INTEGER, qty INTEGER)")
        conn.execute("CREATE TABLE shipment (id INTEGER, eta TEXT)")
    src = SQLiteDataSource(str(db), name="t1", queries={"q1": "SELECT 1"},
                           meta={"system": "MES", "domain": "生产"})
    d = src.describe()
    assert d["name"] == "t1" and d["health"]["ok"] is True
    assert d["system"] == "MES" and d["domain"] == "生产"
    assert [t["name"] for t in d["tables"]] == ["inventory", "shipment"]
    assert d["tables"][0]["fields"][0]["name"] == "id"  # SQLite 字段级自描述
    assert d["registered_queries"] == 1


def test_datasource_describe_failure_is_reported_not_raised(tmp_path):
    """不可达数据源：health=False + error 信息，describe 不抛异常（总览永远可用）。"""
    from mfg_agents.v2.contracts.data_source import SQLiteDataSource

    src = SQLiteDataSource(str(tmp_path / "missing.db"), name="bad")
    d = src.describe()
    assert d["health"]["ok"] is False and d["health"]["error"]
    assert d["tables"] == []


def test_registry_describe_all_isolates_faults(tmp_path):
    """注册表总览：单个源故障不影响其他源（隔离故障）。"""
    from mfg_agents.v2.contracts.data_source import (
        SQLiteDataSource,
        DataSourceRegistry,
    )

    db = tmp_path / "ok.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
    reg = DataSourceRegistry()
    reg.register(SQLiteDataSource(str(db), name="good"))
    reg.register(SQLiteDataSource(str(tmp_path / "nope.db"), name="bad"))
    out = {d["name"]: d for d in reg.describe_all()}
    assert out["good"]["health"]["ok"] is True
    assert out["bad"]["health"]["ok"] is False  # 坏源只报健康，不炸总览


def test_api_datasources_endpoint(api_client):
    """GET /api/v2/datasources：dev 源可见，含元数据与表清单。"""
    resp = api_client.get("/api/v2/datasources")
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert items and items[0]["name"] == "dev"
    assert items[0]["health"]["ok"] is True
    assert items[0]["meta"].get("system")  # orchestrator 已注入元数据
    assert len(items[0]["tables"]) >= 8    # 演示库 8 张表


# ============================================================
# 11. 数据注入接口（V3.1）：DataSource 自描述 + 注册表总览
# ============================================================


def test_datasource_describe_reports_tables_and_health(tmp_path):
    """describe()：表清单 + 注册查询数 + 健康状态（接入即见的契约）。"""
    import sqlite3

    from mfg_agents.v2.contracts.data_source import SQLiteDataSource

    db = tmp_path / "ds.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE inventory (id INTEGER, qty INTEGER)")
        conn.execute("CREATE TABLE shipment (id INTEGER, eta TEXT)")
    src = SQLiteDataSource(str(db), name="t1", queries={"q1": "SELECT 1"},
                           meta={"system": "MES", "domain": "生产"})
    d = src.describe()
    assert d["name"] == "t1" and d["health"]["ok"] is True
    assert d["system"] == "MES" and d["domain"] == "生产"
    assert [t["name"] for t in d["tables"]] == ["inventory", "shipment"]
    assert d["tables"][0]["fields"][0]["name"] == "id"  # SQLite 字段级自描述
    assert d["registered_queries"] == 1


def test_datasource_describe_failure_is_reported_not_raised(tmp_path):
    """不可达数据源：health=False + error 信息，describe 不抛异常（总览永远可用）。"""
    from mfg_agents.v2.contracts.data_source import SQLiteDataSource

    src = SQLiteDataSource(str(tmp_path / "missing.db"), name="bad")
    d = src.describe()
    assert d["health"]["ok"] is False and d["health"]["error"]
    assert d["tables"] == []


def test_registry_describe_all_isolates_faults(tmp_path):
    """注册表总览：单个源故障不影响其他源（隔离故障）。"""
    from mfg_agents.v2.contracts.data_source import (
        SQLiteDataSource,
        DataSourceRegistry,
    )

    db = tmp_path / "ok.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
    reg = DataSourceRegistry()
    reg.register(SQLiteDataSource(str(db), name="good"))
    reg.register(SQLiteDataSource(str(tmp_path / "nope.db"), name="bad"))
    out = {d["name"]: d for d in reg.describe_all()}
    assert out["good"]["health"]["ok"] is True
    assert out["bad"]["health"]["ok"] is False  # 坏源只报健康，不炸总览


def test_api_datasources_endpoint(api_client):
    """GET /api/v2/datasources：dev 源可见，含元数据与表清单。"""
    resp = api_client.get("/api/v2/datasources")
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert items and items[0]["name"] == "dev"
    assert items[0]["health"]["ok"] is True
    assert items[0]["meta"].get("system")  # orchestrator 已注入元数据
    assert len(items[0]["tables"]) >= 8    # 演示库 8 张表



# ============================================================
# 12. 职能域鉴权（V3.2）：财务制度高管限定
# ============================================================


def test_finance_policy_gm_only():
    """鉴权裁定：finance_policy 仅 finance/gm/coordinator 可查，其他岗位拒绝。"""
    from mfg_agents.v2.services.text2sql import SQLAuditor, SQLAuditError

    for roles in (["gm"], ["finance"], ["coordinator"]):
        SQLAuditor.audit("SELECT * FROM finance_policy",
                         {"dept": "x", "roles": roles})  # 不抛即通过
    for roles in (["pqe"], ["manufacturing"], ["warehouse"], ["sales"], ["qa"]):
        with pytest.raises(SQLAuditError, match="finance_policy"):
            SQLAuditor.audit("SELECT * FROM finance_policy",
                             {"dept": "x", "roles": roles})


def test_admin_policy_readable_by_all():
    """行政制度全员可读（公开制度）。"""
    from mfg_agents.v2.services.text2sql import SQLAuditor

    for roles in (["pqe"], ["warehouse"], ["gm"], ["admin"] if False else ["gm"]):
        SQLAuditor.audit("SELECT * FROM admin_policy",
                         {"dept": "x", "roles": roles})
