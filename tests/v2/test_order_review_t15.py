"""T15 · S1 接单评审会签工单化（order_review）。

覆盖（对应计划文档 T15 验收）：
1. ``order_review`` 槽位已在权限目录白名单内（3 条权限，且纳入 GOVERNANCE_SLOTS）
2. 场景 ``order_review_v2`` 登记了 ``hitl_order_review`` 节点，且 ``triggers`` 为空
   （刻意为空：不走路由，故无需在 node_registry 注册执行 handler）
3. 角色 × 槽位矩阵与人裁决一致：
   sales = propose + approve；rd_engineering / planning = cosign；
   gm / manufacturing 无任何 order_review 权限
4. 演示种子幂等：重复调用只留 1 条工单；工单治理链已发起、待会签
5. 按槽位可见：sales / rd_engineering / planning 可见，gm / qa / manufacturing 不可见

为什么单独一个文件：T15 是「S1 屏从事件流回放补齐为真实 HITL」的横切改动，
横跨权限目录 / 场景注册 / 演示种子 / 可见性四处，集中在一处断言避免散落。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import mfg_agents.v2 as v2_pkg
from mfg_agents.v2.contracts.permissions import (
    get_permission_registry,
    reset_permission_registry,
)
from mfg_agents.v2.contracts.scenario import ScenarioRegistry
from mfg_agents.v2.services.workflow import GOVERNANCE_SLOTS, WorkflowEngine
from mfg_agents.v2.services.workflow_scope import (
    VIS_DOMAIN,
    VIS_MINE,
    visible_tasks,
)

SCENARIO_DIR = Path(v2_pkg.__file__).resolve().parent / "scenarios"
SLOT = "order_review"
WF_ID = "hitl_order_review"
VERBS = ("propose", "cosign", "approve")

# 演示身份（user_ctx.roles 为权威角色来源，与 permissions.yaml 的 aliases 对齐）
SALES_CTX: dict[str, Any] = {"user_id": "u-sales", "roles": ["sales"]}
RD_CTX: dict[str, Any] = {"user_id": "u-rd", "roles": ["rd_engineering"]}
PLN_CTX: dict[str, Any] = {"user_id": "u-pln", "roles": ["planning"]}
GM_CTX: dict[str, Any] = {"user_id": "u-gm", "roles": ["gm"]}
QA_CTX: dict[str, Any] = {"user_id": "u-qa", "roles": ["qa"]}
MFG_CTX: dict[str, Any] = {"user_id": "u-mfg", "roles": ["manufacturing"]}


@pytest.fixture()
def engine(tmp_path) -> WorkflowEngine:
    reset_permission_registry()
    return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")


def _seed(monkeypatch, engine: WorkflowEngine) -> None:
    """在隔离库上跑演示种子（把 api 的引擎指向测试库，并复位幂等守卫）。"""
    from mfg_agents.v2.demo import api as api_module

    monkeypatch.setattr(api_module, "_workflow_engine", lambda: engine)
    monkeypatch.setattr(api_module, "_order_review_seeded", False)
    api_module._ensure_order_review_seed()


# ============================================================
# 1. 槽位与权限目录
# ============================================================


def test_order_review_slot_is_whitelisted():
    """order_review 在权限目录白名单内，且持 3 条权限（propose/cosign/approve）。"""
    registry = get_permission_registry()
    assert SLOT in registry.slot_keys()
    assert SLOT in GOVERNANCE_SLOTS
    for verb in VERBS:
        key = f"hitl.{SLOT}.{verb}"
        assert registry.permission(key) is not None, key
        assert key in registry.slot(SLOT).available_permissions
    assert sorted(registry.slot(SLOT).available_permissions) == sorted(
        f"hitl.{SLOT}.{verb}" for verb in VERBS)


def test_order_review_role_slot_matrix_matches_arbitration():
    """角色 × 槽位归属与人裁决一致（sales=propose+approve；工程/计划=cosign）。"""
    registry = get_permission_registry()

    def perms_of(role_key: str) -> set[str]:
        role = registry.role(role_key)
        assert role is not None, role_key
        return {str(p) for p in getattr(role, "permissions", ())}

    sales = perms_of("sales")
    assert f"hitl.{SLOT}.propose" in sales
    assert f"hitl.{SLOT}.approve" in sales
    assert f"hitl.{SLOT}.cosign" not in sales  # 发起人不同时会签（避免自签）

    for key in ("rd_engineering", "planning"):
        assert f"hitl.{SLOT}.cosign" in perms_of(key), key

    # 制造与总经理不持 order_review：演示「按槽位可见的不同待办」的负样本来源
    for key in ("manufacturing", "gm"):
        assert not any(p.startswith(f"hitl.{SLOT}.") for p in perms_of(key)), key


# ============================================================
# 2. 场景登记（声明式，供加载期双注册表校验）
# ============================================================


def test_order_review_scenario_declares_hitl_node_without_triggers():
    """场景登记 hitl_order_review；triggers 为空 → 不被路由、无需注册执行 handler。"""
    loaded = ScenarioRegistry(SCENARIO_DIR).load()
    assert "order_review_v2" in loaded
    meta = loaded["order_review_v2"]
    registry = ScenarioRegistry(SCENARIO_DIR)
    nodes = registry.hitl_nodes(meta)
    assert len(nodes) == 1
    assert nodes[0]["slot"] == SLOT
    assert nodes[0]["agent"] == WF_ID
    assert nodes[0]["declared_in"] == "steps"
    assert not (meta.triggers or []), (
        "triggers 必须为空：本场景是 S1 演示屏治理工单的数据声明，不走对话路由")
    assert meta.trigger_words() == [], "无触发词 → L0/L1 路由不会命中本场景"


# ============================================================
# 3. 演示种子（幂等 + 可读）
# ============================================================


def test_seed_is_idempotent_and_readable(monkeypatch, engine):
    """重复种子只留 1 条；工单已发起治理链、待会签，会签要求为工程 + 计划。"""
    _seed(monkeypatch, engine)
    _seed(monkeypatch, engine)  # 幂等：第二次为 no-op

    tasks = [t for t in engine.list_tasks() if t.wf_id == WF_ID]
    assert len(tasks) == 1, "种子必须幂等：不得重复建单"
    task = tasks[0]
    assert task.is_governed()
    assert task.slot == SLOT
    assert task.status == "waiting_hitl"

    status = task.signoff_status()
    assert [r["role"] for r in status["required"]] == ["rd_engineering", "planning"]
    assert status["progress"] == "0/2" and not status["satisfied"]
    assert status["selected_action"] == "接受订单并按期承诺"

    gov = task.governance
    assert gov["proposed_by"]["role"] == "sales"
    assert gov["proposed_by"]["role_name"] == "业务"


def test_seed_task_context_carries_scene_and_actions(monkeypatch, engine):
    """工单上下文带 S1 场景标记与三处置动作（前端按动作映射槽位）。"""
    _seed(monkeypatch, engine)
    task = [t for t in engine.list_tasks() if t.wf_id == WF_ID][0]
    assert task.context.get("scene") == "S1"
    assert task.context.get("slot") == SLOT
    assert [a["key"] for a in task.context.get("actions") or []] == [
        "accept", "conditional", "reject"]


# ============================================================
# 4. 按槽位可见（演示「不同待办」的核心）
# ============================================================


def test_seeded_task_visibility_follows_slot_matrix(monkeypatch, engine):
    """会签人可见（mine）、发起人域内只读（domain），其余岗位不可见（hidden）。"""
    _seed(monkeypatch, engine)
    task = [t for t in engine.list_tasks() if t.wf_id == WF_ID][0]
    tasks = engine.list_tasks(status="waiting_hitl")

    # 会签人（工程 / 计划）：mine（当前阶段轮到我）
    for ctx in (RD_CTX, PLN_CTX):
        pairs = dict((t.task_uid, vis) for t, vis in
                     visible_tasks(engine, tasks, ctx["roles"]))
        assert pairs.get(task.task_uid) == VIS_MINE, ctx

    # 发起人（业务）：动作已选定、会签未齐 → 域内只读（domain），不重复发起
    sales_pairs = dict((t.task_uid, vis) for t, vis in
                       visible_tasks(engine, tasks, SALES_CTX["roles"]))
    assert sales_pairs.get(task.task_uid) == VIS_DOMAIN

    # 非治理岗位（总经理 / QA / 制造）：槽位不可见 → 不出现在队列（hidden）
    for ctx in (GM_CTX, QA_CTX, MFG_CTX):
        pairs = dict((t.task_uid, vis) for t, vis in
                     visible_tasks(engine, tasks, ctx["roles"]))
        assert task.task_uid not in pairs, ctx


def test_action_for_differs_by_role_on_seeded_task(monkeypatch, engine):
    """同一工单：工程与计划可会签；业务已发起故暂不可动作；制造无从下手。"""
    _seed(monkeypatch, engine)
    task = [t for t in engine.list_tasks() if t.wf_id == WF_ID][0]
    assert engine.action_for(task, RD_CTX["roles"]) == "cosign"
    assert engine.action_for(task, PLN_CTX["roles"]) == "cosign"
    # 会签未齐时业务不满足终批条件（satisfied=False）→ 只看不办
    assert engine.action_for(task, SALES_CTX["roles"]) is None
    assert engine.action_for(task, MFG_CTX["roles"]) is None
    assert engine.action_for(task, GM_CTX["roles"]) is None
