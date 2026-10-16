"""QA 验证：HITL 关键决策人工在环路 + 工作流状态机真实性。

覆盖（对抗视角，不复用工程师断言）：
1. hitl_restart / hitl_eta 两节点确实进入 waiting_hitl，且 DB 真实落库；
2. 无自动放行路径：全链路（路由→DAG→工单→HITL）绝不调用 approve/reject，
   advance 仅用于影响工单 start（monkeypatch 哨兵证明）；
3. 状态机对抗：非法迁移（pending→done / done→running / closed→* / escalated→waiting_hitl）必须抛错；
4. SLA：超时判定与 escalate 迁移（含 waiting_hitl 状态超时升级）；
5. 未停线条件分支：B型号/DIP-2 不触发 HITL/工单/三域取证（等价 V1）；
6. 持久化：platform_workflow_task 行可直接从 SQLite 读回（可重放、可审计）。
"""
import sqlite3

import pytest

from mfg_agents.config import settings
from mfg_agents.tools.llm_factory import reset_probe_cache
from mfg_agents.v2.core.orchestrator import V2Orchestrator, V2Runtime
from mfg_agents.v2.services.workflow import (
    WorkflowEngine,
    WorkflowTransitionError,
)

STOP_QUERY = "A型号在SMT-1停线，何时复线？"
NO_STOP_QUERY = "B型号在DIP-2停线了吗，何时复线？"
USER_CTX = {"user_id": "u-manager", "dept": "manufacturing",
            "roles": ["manufacturing", "coordinator"]}


@pytest.fixture()
def orchestrator(monkeypatch, tmp_path) -> V2Orchestrator:
    """离线 orchestrator + 临时工作流库（与真实 DB 隔离）。"""
    reset_probe_cache()
    for mod in ("mfg_agents.tools.llm_factory",
                "mfg_agents.v2.core.router",
                "mfg_agents.v2.core.node_registry"):
        monkeypatch.setattr(f"{mod}.llm_available", lambda: False)
    monkeypatch.setattr(settings, "WORKFLOW_DB",
                        f"sqlite:///{tmp_path / 'workflow.db'}")
    return V2Orchestrator(V2Runtime.create_default())


# ---------- 1. HITL 进入 waiting_hitl 且落库 ----------

class TestHitlWaiting:
    def test_both_hitl_nodes_waiting(self, orchestrator):
        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        kinds = {h["kind"] for h in result.hitl}
        assert kinds == {"restart", "eta"}
        assert all(h["status"] == "waiting_hitl" for h in result.hitl)
        # 审批人角色与 YAML 声明一致（关键决策人，不是泛泛通知）
        approvers = {h["kind"]: h["approver_roles"] for h in result.hitl}
        assert approvers["restart"] == ["制造现场负责人"]
        assert approvers["eta"] == ["业务经理"]

    def test_hitl_tasks_persisted_in_db(self, orchestrator, tmp_path):
        """platform_workflow_task 真实落库：waiting_hitl 行可直读。"""
        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        db = tmp_path / "workflow.db"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT task_uid, wf_id, status, assignees, hitl_prompt, history_json "
            "FROM platform_workflow_task WHERE wf_id LIKE 'hitl%'").fetchall()
        conn.close()
        assert len(rows) == 2
        uids = {h["task_uid"] for h in result.hitl}
        for row in rows:
            assert row["task_uid"] in uids
            assert row["status"] == "waiting_hitl"
            assert row["hitl_prompt"]
            assert row["assignees"]  # 指派到具体审批角色
            assert row["history_json"] and row["history_json"] != "[]"

    def test_hitl_cards_carry_actions(self, orchestrator):
        """HITL 卡片带确认/暂缓按钮（IM 回写 advance 的入口）。"""
        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        actions = {h["kind"]: [a["key"] for a in h["actions"]]
                   for h in result.hitl}
        assert actions["restart"] == ["confirm_restart", "hold"]
        assert actions["eta"] == ["confirm_eta", "revise_eta"]


# ---------- 2. 无自动放行路径 ----------

class TestNoAutoPass:
    def test_pipeline_never_auto_approves(self, orchestrator, monkeypatch):
        """哨兵：全链路运行期间 approve/reject 一次都不允许被调用；
        advance 只允许以 'start' 出现（影响工单启动）。"""
        engine = orchestrator.runtime.workflow
        calls: list[tuple] = []
        orig_approve, orig_reject = engine.approve, engine.reject
        orig_advance = engine.advance

        def spy_approve(*a, **k):
            calls.append(("approve", a, k))
            return orig_approve(*a, **k)

        def spy_reject(*a, **k):
            calls.append(("reject", a, k))
            return orig_reject(*a, **k)

        def spy_advance(task_uid, action, *a, **k):
            calls.append(("advance", task_uid, action))
            return orig_advance(task_uid, action, *a, **k)

        monkeypatch.setattr(engine, "approve", spy_approve)
        monkeypatch.setattr(engine, "reject", spy_reject)
        monkeypatch.setattr(engine, "advance", spy_advance)

        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        assert len(result.hitl) == 2
        auto = [c for c in calls if c[0] in ("approve", "reject")]
        assert auto == [], f"发现自动放行调用: {auto}"
        advance_actions = [c[2] for c in calls if c[0] == "advance"]
        assert set(advance_actions) == {"start"}, \
            f"advance 只能用于影响工单启动，实际: {advance_actions}"

    def test_hitl_tasks_stay_waiting_across_runs(self, orchestrator):
        """多次运行不会推进/消费 HITL 待办（服务无副作用）。"""
        orchestrator.handle(STOP_QUERY, USER_CTX)
        first = {t.task_uid for t in orchestrator.runtime.workflow.pending_hitl()}
        assert len(first) == 2
        orchestrator.handle(STOP_QUERY, USER_CTX)
        second = orchestrator.runtime.workflow.pending_hitl()
        assert {t.task_uid for t in second} >= first  # 旧待办仍在
        assert all(t.status == "waiting_hitl" for t in second)

    def test_approve_requires_explicit_actor(self, tmp_path):
        """放行唯一入口：approve(task_uid, approver) 显式带审批人。"""
        engine = WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")
        task = engine.create_task("hitl_restart", "复线决策", hitl=True)
        running = engine.approve(task.task_uid, "王制造")
        assert running.status == "running"
        assert running.history[-1]["actor"] == "王制造"
        assert running.history[-1]["action"] == "approve"


# ---------- 3. 状态机对抗 ----------

class TestStateMachineAdversarial:
    @pytest.fixture()
    def engine(self, tmp_path):
        # 注：不能用 sqlite:///:memory: —— ensure_platform_schema 走 SQLAlchemy
        # （每连接独立内存库），与 WorkflowEngine 的原生 sqlite3 连接不是同一个库，
        # 导致 "no such table"（QA-BUG-4，见 test_memory_db_url_broken）。
        return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")

    @pytest.mark.parametrize("setup,action,target_desc", [
        ("pending", "done", "pending→done（跳过审批）"),
        ("pending", "escalate", "pending→escalated（未启动就升级）"),
        ("running", "pending", "running→pending（回退；无此 action）"),
        ("done", "start", "done→running（终结后复活）"),
        ("closed", "start", "closed→running（关闭后复活）"),
        ("closed", "approve", "closed→running（关闭后审批）"),
        ("escalated", "request_hitl", "escalated→waiting_hitl（升级后转人工）"),
        ("waiting_hitl", "waiting_hitl", "waiting_hitl→waiting_hitl（重复等待）"),
    ])
    def test_illegal_transitions_rejected(self, engine, setup, action,
                                          target_desc):
        task = engine.create_task("wf", "t")
        if setup == "running":
            engine.advance(task.task_uid, "start")
        elif setup == "waiting_hitl":
            engine.advance(task.task_uid, "start")
            engine.advance(task.task_uid, "request_hitl")
        elif setup == "escalated":
            engine.advance(task.task_uid, "start")
            engine.escalate(task.task_uid, "SLA 超时")
        elif setup == "done":
            engine.create_task("wf2", "t2", hitl=True)
            t2 = engine.list_tasks(status="waiting_hitl")[0]
            engine.approve(t2.task_uid, "approver", final=True)
            task = t2
        elif setup == "closed":
            engine.close(task.task_uid)
        # 未知动作名（如 pending/waiting_hitl）与非法迁移都必须抛
        # WorkflowTransitionError（双重要求：action 白名单 + 状态机白名单）
        with pytest.raises(WorkflowTransitionError,
                           match="未知动作|非法状态迁移"):
            engine.advance(task.task_uid, action)

    def test_full_happy_path_transitions(self, engine):
        """合法主路径：pending→running→waiting_hitl→running→done→closed。"""
        task = engine.create_task("impact_assessment", "停线影响评估")
        assert task.status == "pending"
        task = engine.advance(task.task_uid, "start")
        assert task.status == "running"
        task = engine.advance(task.task_uid, "request_hitl")
        assert task.status == "waiting_hitl"
        task = engine.approve(task.task_uid, "业务经理")
        assert task.status == "running"
        task = engine.advance(task.task_uid, "done")
        assert task.status == "done"
        task = engine.close(task.task_uid)
        assert task.status == "closed"

    def test_reject_closes_task(self, engine):
        task = engine.create_task("hitl_eta", "对客ETA", hitl=True)
        closed = engine.reject(task.task_uid, "业务经理", comment="ETA 需修改")
        assert closed.status == "closed"
        assert closed.history[-1]["comment"] == "ETA 需修改"


# ---------- 4. SLA ----------

class TestSla:
    def test_sla_overdue_detected_and_escalatable(self, tmp_path):
        engine = WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")
        task = engine.create_task("impact_assessment", "t", sla_hours=-1)
        status = task.sla_status()
        assert status["sla_active"] is True
        assert status["overdue"] is True
        assert engine.check_sla(task) is True
        # 超时后必须可升级（running 与 waiting_hitl 都允许 escalate）
        engine.advance(task.task_uid, "start")
        escalated = engine.escalate(task.task_uid, "SLA 超时 30 分钟")
        assert escalated.status == "escalated"
        assert escalated.history[-1]["reason"] == "SLA 超时 30 分钟"

    def test_hitl_task_sla_overdue_escalatable(self, tmp_path):
        """HITL 挂起超时也要能升级（催办不响应 → escalate）。"""
        engine = WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")
        task = engine.create_task("hitl_restart", "复线决策", hitl=True,
                                  sla_hours=-1)
        assert engine.check_sla(task) is True
        escalated = engine.escalate(task.task_uid, "审批超时")
        assert escalated.status == "escalated"

    def test_terminal_task_sla_inactive(self, tmp_path):
        engine = WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")
        task = engine.create_task("impact_assessment", "t")
        closed = engine.close(task.task_uid)
        assert closed.status == "closed"
        assert closed.sla_status()["sla_active"] is False
        assert engine.check_sla(closed) is False

    def test_impact_ticket_sla_window(self, orchestrator):
        """影响工单 SLA：P1 + 2 小时窗口 + 三个受理角色。"""
        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        ticket = result.workflow_tasks[0]["ticket"]
        assert ticket["priority"] == "P1"
        assert ticket["status"] == "running"
        assert ticket["assignees"] == ["计划", "物控", "业务"]
        assert ticket["sla_due_at"]  # SLA 计时已落库


# ---------- 5. 未停线条件分支 ----------

class TestNoStopBranch:
    def test_no_stop_skips_hitl_and_fanout(self, orchestrator):
        """B型号/DIP-2：未确认停线 → 直接跳 report，无 HITL/工单/三域取证。"""
        result = orchestrator.handle(NO_STOP_QUERY, USER_CTX)
        assert result.internal_brief["停线确认"] is False
        assert result.hitl == []
        assert result.workflow_tasks == []
        assert "运行正常" in result.report
        # 三域取证一条都没执行（fan_out condition 走 else）
        queried = {p.get("query_name") for p in result.provenance}
        assert "quality_defect_dist" not in queried
        assert "equip_alarms" not in queried
        assert "supply_batches_today" not in queried

    def test_no_stop_leaves_no_pending_hitl(self, orchestrator):
        orchestrator.handle(NO_STOP_QUERY, USER_CTX)
        assert orchestrator.runtime.workflow.pending_hitl() == []


# ---------- 6. 持久化细节 ----------

class TestPersistence:
    def test_history_audit_trail_complete(self, orchestrator):
        """工单 history 记录每次迁移（actor/from/to/理由），可重放。"""
        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        ticket_uid = result.workflow_tasks[0]["ticket"]["task_uid"]
        task = orchestrator.runtime.workflow.get(ticket_uid)
        actions = [h["action"] for h in task.history]
        assert actions == ["create", "start"]
        assert task.history[0]["from"] == "pending"
        assert task.history[1]["from"] == "pending"
        assert task.history[1]["to"] == "running"
        assert task.history[1]["actor"] == "orchestrator"

    def test_context_run_id_linkage(self, orchestrator):
        """工单 context 带 run_id，可与编排结果关联审计。"""
        result = orchestrator.handle(STOP_QUERY, USER_CTX)
        ticket = result.workflow_tasks[0]["ticket"]
        # to_dict 里 context 是 dict（dataclass asdict 保留嵌套）
        ctx = ticket["context"]
        assert ctx.get("run_id") == result.run_id
        assert ctx.get("confirmed_stop") is True


# ---------- 7. BUG-4 修复验证（Round 2 翻转：缺陷证据 → 修复后行为锁定） ----------

class TestMemoryDbUsable:
    """BUG-4（Round 2 已由工程师修复，commit 54aa607）：

    原缺陷：WorkflowEngine("sqlite:///:memory:") 不可用——ensure_platform_schema
    走 SQLAlchemy（:memory: 每连接一个独立库），与引擎原生 sqlite3 连接不是
    同一个库，create_task 报 "no such table: platform_workflow_task"。
    修复：platform_schema 的 SQLite 路径改原生 sqlite3 建表+播种，内存库用
    进程内持久单连接，DDL/DML 同一库。

    本测试由 QA 从"缺陷证据锚定"翻转为"修复后行为锁定"：
    :memory: 全生命周期必须可用。
    """

    def test_memory_db_full_lifecycle(self):
        engine = WorkflowEngine("sqlite:///:memory:")
        task = engine.create_task("impact_assessment", "停线影响评估",
                                  assignees=["计划"])
        assert task.status == "pending"
        started = engine.advance(task.task_uid, "start")
        assert started.status == "running"
        hitl = engine.create_task("hitl_restart", "复线决策", hitl=True,
                                  hitl_prompt="确认？")
        assert hitl.status == "waiting_hitl"
        assert len(engine.pending_hitl()) == 1
        approved = engine.approve(hitl.task_uid, "王制造", final=True)
        assert approved.status == "done"
        escalated = engine.escalate(started.task_uid, "SLA 超时")
        assert escalated.status == "escalated"
        assert len(engine.list_tasks()) == 2
