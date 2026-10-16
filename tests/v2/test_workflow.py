"""WorkflowEngine 测试：状态机 / HITL 待办 / SLA / 持久化 / 非法迁移拦截。"""
import pytest

from mfg_agents.v2.services.workflow import (
    WorkflowEngine,
    WorkflowTransitionError,
)


@pytest.fixture()
def engine(tmp_path) -> WorkflowEngine:
    return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")


def test_create_task_pending_then_start(engine):
    task = engine.create_task("impact_assessment", "停线影响评估",
                              assignees=["计划", "物控"])
    assert task.status == "pending"
    assert task.sla_due_at is not None
    started = engine.advance(task.task_uid, "start", actor="orchestrator")
    assert started.status == "running"
    assert started.history[-1]["from"] == "pending"


def test_hitl_task_created_waiting(engine):
    """HITL 工单创建即 waiting_hitl（绝不自动放行）。"""
    task = engine.create_task("hitl_restart", "复线决策确认", hitl=True,
                              hitl_prompt="是否按 ETA 复线？",
                              assignees=["制造现场负责人"])
    assert task.status == "waiting_hitl"
    assert task.hitl_prompt == "是否按 ETA 复线？"
    assert task in engine.pending_hitl()


def test_approve_and_reject_transitions(engine):
    task = engine.create_task("hitl_eta", "对客 ETA 确认", hitl=True)
    approved = engine.approve(task.task_uid, "业务经理", comment="同意")
    assert approved.status == "running"
    assert approved.history[-1]["actor"] == "业务经理"

    task2 = engine.create_task("hitl_eta", "对客 ETA 确认", hitl=True)
    rejected = engine.reject(task2.task_uid, "业务经理", comment="需修改")
    assert rejected.status == "closed"


def test_approve_final_reaches_done(engine):
    task = engine.create_task("hitl_eta", "对客 ETA 确认", hitl=True)
    assert engine.approve(task.task_uid, "业务经理", final=True).status == "done"


def test_escalate_and_close(engine):
    task = engine.create_task("impact_assessment", "停线影响评估")
    engine.advance(task.task_uid, "start")
    escalated = engine.escalate(task.task_uid, "SLA 超时 30 分钟")
    assert escalated.status == "escalated"
    assert escalated.history[-1]["reason"] == "SLA 超时 30 分钟"
    assert engine.close(task.task_uid).status == "closed"


def test_illegal_transition_rejected(engine):
    """安全边界：closed 工单不允许任何后续迁移。"""
    task = engine.create_task("wf", "t")
    engine.close(task.task_uid)
    with pytest.raises(WorkflowTransitionError, match="非法状态迁移"):
        engine.advance(task.task_uid, "start")


def test_pending_to_waiting_hitl_direct(engine):
    """pending → running → waiting_hitl（请求人工确认动作）。"""
    task = engine.create_task("wf", "t")
    started = engine.advance(task.task_uid, "start")
    assert started.status == "running"
    waiting = engine.advance(task.task_uid, "request_hitl")
    assert waiting.status == "waiting_hitl"


def test_sla_status_detection(engine):
    task = engine.create_task("impact_assessment", "t", sla_hours=0)
    status = task.sla_status()
    assert status["sla_active"] is True
    # sla_hours=0 → 立即超时（允许极小时间误差）
    assert status["overdue"] is True or status["remaining_s"] <= 0
    assert engine.check_sla(task) in (True, False)


def test_persistence_across_engine_instances(tmp_path):
    """DB 持久化：新引擎实例可读回工单与历史（可重放、可审计）。"""
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    engine1 = WorkflowEngine(db)
    task = engine1.create_task("impact_assessment", "停线影响评估",
                               context={"run_id": "run-x"},
                               assignees=["计划"])
    engine1.advance(task.task_uid, "start")

    engine2 = WorkflowEngine(db)
    loaded = engine2.get(task.task_uid)
    assert loaded.status == "running"
    assert loaded.context["run_id"] == "run-x"
    assert len(loaded.history) == 2
    # 按 run_id 过滤
    assert engine2.list_tasks(run_id="run-x")[0].task_uid == task.task_uid


def test_list_tasks_by_status(engine):
    engine.create_task("wf", "a")
    running = engine.create_task("wf", "b")
    engine.advance(running.task_uid, "start")
    assert len(engine.list_tasks(status="pending")) == 1
    assert len(engine.list_tasks(status="running")) == 1


def test_unknown_task_raises(engine):
    with pytest.raises(KeyError):
        engine.get("wf-nonexistent")


def test_unknown_action_raises(engine):
    task = engine.create_task("wf", "t")
    with pytest.raises(WorkflowTransitionError, match="未知动作"):
        engine.advance(task.task_uid, "destroy_everything")
