"""WorkflowEngine：轻量工作流引擎骨架（状态机 + SLA + HITL 待办队列）。

架构铁律（ARCHITECTURE-V2.md §3.5）：确定性流程（审批/通知/升级/工单状态）
只允许工作流引擎执行；LLM 节点产出只能是"建议"进入 task 节点。
本引擎 SQLite 持久化（platform_workflow_task），可重放、可审计。

状态机：pending → running → waiting_hitl → done / closed（+ escalated）
HITL 白名单（B.3 第 9 条）：复线决策、对客 ETA/ATP 承诺、特采放行、
加急采购>5万、ECN 会签、8D 关闭——必须人工确认，卡片按钮回写 advance()。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ...db.platform_schema import (
    ensure_platform_schema,
    ensure_platform_schema_sqlite,
    sqlite_path_from_url,
)

logger = logging.getLogger(__name__)


class WorkflowTransitionError(Exception):
    """非法状态迁移（安全边界：防止跳过审批直接放行）。"""


# 状态机定义：当前状态 → 允许的目标状态
TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "waiting_hitl", "closed"},
    "running": {"waiting_hitl", "done", "closed", "escalated"},
    "waiting_hitl": {"running", "done", "closed", "escalated"},
    "escalated": {"running", "closed"},
    "done": {"closed"},
    "closed": set(),
}

TERMINAL_STATUSES = {"done", "closed"}


@dataclass
class WorkflowTask:
    """工作流工单（DB 行的结构化视图）。"""

    task_uid: str
    wf_id: str
    title: str
    status: str = "pending"
    priority: str = "P1"
    assignees: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    hitl_prompt: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    sla_due_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def sla_status(self) -> dict[str, Any]:
        """SLA 计时状态（未超时/已超时/已终止）。"""
        if self.status in TERMINAL_STATUSES or not self.sla_due_at:
            return {"sla_active": False, "overdue": False, "sla_due_at": self.sla_due_at}
        due = datetime.fromisoformat(self.sla_due_at)
        now = datetime.now(timezone.utc)
        return {"sla_active": True, "overdue": now > due,
                "sla_due_at": self.sla_due_at,
                "remaining_s": round((due - now).total_seconds(), 1)}


class WorkflowEngine:
    """工作流引擎：状态机 + SLA 计时 + HITL 待办队列（DB 持久化）。

    SQLite 持久化（platform_workflow_task），可重放、可审计。
    :memory: 场景使用进程内持久单连接（修复 BUG-4：DDL 与 DML 同一库）。
    """

    def __init__(self, db_url: str, default_sla_hours: float = 2.0) -> None:
        self.db_url = db_url
        self.db_path = sqlite_path_from_url(db_url)
        self.default_sla_hours = default_sla_hours
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            # 内存库：建一个进程内持久连接，DDL/DML 共用同一库
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
            ensure_platform_schema_sqlite(self._memory_conn)
        else:
            ensure_platform_schema(db_url)

    # ---------- 连接 ----------

    def _connect(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- 生命周期 ----------

    def create_task(self, wf_id: str, title: str,
                    context: Optional[dict] = None,
                    assignees: Optional[list[str]] = None,
                    priority: str = "P1",
                    sla_hours: Optional[float] = None,
                    hitl: bool = False,
                    hitl_prompt: str = "") -> WorkflowTask:
        """创建工单；hitl=True 时直接进入 waiting_hitl（待人工确认，绝不自动放行）。"""
        now = datetime.now(timezone.utc)
        sla = now + timedelta(hours=sla_hours if sla_hours is not None
                              else self.default_sla_hours)
        task = WorkflowTask(
            task_uid=f"wf-{uuid.uuid4().hex[:12]}",
            wf_id=wf_id, title=title,
            status="waiting_hitl" if hitl else "pending",
            priority=priority,
            assignees=list(assignees or []),
            context=dict(context or {}),
            hitl_prompt=hitl_prompt,
            created_at=now.isoformat(), updated_at=now.isoformat(),
            sla_due_at=sla.isoformat(),
        )
        task.history.append(self._history_entry("create", "system", "", "pending",
                                                task.status, "创建工单"))
        self._insert(task)
        logger.info("[workflow] 创建工单 %s（%s）status=%s", task.task_uid,
                    wf_id, task.status)
        return task

    def advance(self, task_uid: str, action: str, actor: str = "system",
                comment: str = "") -> WorkflowTask:
        """通用状态推进（IM 卡片按钮回写入口）。

        action: start | approve | reject | escalate | done | close
        """
        action_map = {
            "start": "running",
            "approve": "running",     # waiting_hitl → running（final=True 走 approve_final）
            "request_hitl": "waiting_hitl",
            "reject": "closed",
            "escalate": "escalated",
            "done": "done",
            "close": "closed",
        }
        if action not in action_map:
            raise WorkflowTransitionError(f"未知动作: {action!r}")
        return self._transition(task_uid, action_map[action], actor, action, comment)

    def approve(self, task_uid: str, approver: str,
                comment: str = "", final: bool = False,
                decision: str = "") -> WorkflowTask:
        """HITL 审批通过：waiting_hitl → running（final=True 时 → done）。

        decision：所选处置动作（如"特采（让步接收）"），写入 history 供审计。
        """
        return self._transition(task_uid, "done" if final else "running",
                                approver, "approve",
                                self._with_decision(decision, comment))

    def reject(self, task_uid: str, approver: str, comment: str = "",
               decision: str = "") -> WorkflowTask:
        """HITL 审批驳回：→ closed。"""
        return self._transition(task_uid, "closed", approver, "reject",
                                self._with_decision(decision, comment))

    @staticmethod
    def _with_decision(decision: str, comment: str) -> str:
        """把处置动作并入审批意见（history 可读、可审计）。"""
        decision = (decision or "").strip()
        if not decision:
            return comment
        return f"决策：{decision}；{comment}" if comment else f"决策：{decision}"

    def escalate(self, task_uid: str, reason: str,
                 actor: str = "system") -> WorkflowTask:
        """升级（SLA 超时 / 低置信度）：→ escalated。"""
        return self._transition(task_uid, "escalated", actor, "escalate", reason)

    def close(self, task_uid: str, actor: str = "system") -> WorkflowTask:
        return self._transition(task_uid, "closed", actor, "close", "关闭工单")

    # ---------- 查询 ----------

    def get(self, task_uid: str) -> WorkflowTask:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM platform_workflow_task WHERE task_uid = ?",
                (task_uid,)).fetchone()
        if row is None:
            raise KeyError(f"工单不存在: {task_uid!r}")
        return self._row_to_task(row)

    def list_tasks(self, status: Optional[str] = None,
                   run_id: Optional[str] = None) -> list[WorkflowTask]:
        """按状态 / run_id（context.run_id）过滤工单。"""
        sql = "SELECT * FROM platform_workflow_task"
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if run_id:
            conditions.append("context_json LIKE ?")
            params.append(f'%"{run_id}"%')
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    def pending_hitl(self) -> list[WorkflowTask]:
        """HITL 待办队列（IM 卡片催办 / 看板数据源）。"""
        return self.list_tasks(status="waiting_hitl")

    def check_sla(self, task: WorkflowTask) -> bool:
        """SLA 超时检测（True=已超时且仍在途）。"""
        return bool(task.sla_status().get("overdue"))

    # ---------- 内部 ----------

    def _transition(self, task_uid: str, target: str, actor: str,
                    action: str, comment: str) -> WorkflowTask:
        task = self.get(task_uid)
        if target not in TRANSITIONS.get(task.status, set()):
            raise WorkflowTransitionError(
                f"非法状态迁移: {task.status} -> {target}（工单 {task_uid}）")
        old = task.status
        task.status = target
        task.updated_at = datetime.now(timezone.utc).isoformat()
        task.history.append(self._history_entry(action, actor, comment, old,
                                                target, comment))
        self._update(task)
        logger.info("[workflow] %s %s: %s -> %s（%s）", task_uid, action, old,
                    target, actor)
        return task

    def _insert(self, task: WorkflowTask) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO platform_workflow_task "
                "(task_uid, wf_id, title, status, priority, assignees, context_json, "
                " hitl_prompt, history_json, created_at, updated_at, sla_due_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.task_uid, task.wf_id, task.title, task.status, task.priority,
                 json.dumps(task.assignees, ensure_ascii=False),
                 json.dumps(task.context, ensure_ascii=False),
                 task.hitl_prompt,
                 json.dumps(task.history, ensure_ascii=False),
                 task.created_at, task.updated_at, task.sla_due_at))

    def _update(self, task: WorkflowTask) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE platform_workflow_task SET status = ?, priority = ?, "
                "assignees = ?, context_json = ?, hitl_prompt = ?, "
                "history_json = ?, updated_at = ?, sla_due_at = ? "
                "WHERE task_uid = ?",
                (task.status, task.priority,
                 json.dumps(task.assignees, ensure_ascii=False),
                 json.dumps(task.context, ensure_ascii=False),
                 task.hitl_prompt,
                 json.dumps(task.history, ensure_ascii=False),
                 task.updated_at, task.sla_due_at, task.task_uid))

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> WorkflowTask:
        return WorkflowTask(
            task_uid=row["task_uid"], wf_id=row["wf_id"], title=row["title"],
            status=row["status"], priority=row["priority"],
            assignees=json.loads(row["assignees"] or "[]"),
            context=json.loads(row["context_json"] or "{}"),
            hitl_prompt=row["hitl_prompt"] or "",
            history=json.loads(row["history_json"] or "[]"),
            created_at=row["created_at"], updated_at=row["updated_at"],
            sla_due_at=row["sla_due_at"],
        )

    @staticmethod
    def _history_entry(action: str, actor: str, comment: str, old: str,
                       new: str, reason: str) -> dict[str, Any]:
        return {"ts": datetime.now(timezone.utc).isoformat(), "action": action,
                "actor": actor, "comment": comment, "from": old, "to": new,
                "reason": reason}
