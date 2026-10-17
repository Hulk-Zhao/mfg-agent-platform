"""WorkflowEngine：轻量工作流引擎骨架（状态机 + SLA + HITL 待办队列 + 治理校验）。

架构铁律（ARCHITECTURE-V2.md §3.5）：确定性流程（审批/通知/升级/工单状态）
只允许工作流引擎执行；LLM 节点产出只能是"建议"进入 task 节点。
本引擎 SQLite 持久化（platform_workflow_task），可重放、可审计。

状态机：pending → running → waiting_hitl → done / closed（+ escalated）
HITL 白名单（B.3 第 9 条）：复线决策、对客 ETA/ATP 承诺、特采放行、
加急采购>5万、ECN 会签、8D 关闭——必须人工确认，卡片按钮回写 advance()。

治理层（本轮新增，从"演示可信"推进到"制度可信"）：
    create_task（待确认）
      → select_action（按选中动作 propose/cosign 生成会签要求）
      → N × cosign（多角色会签，缺一不可）
      → approve（终批，校验 approve 权限 + 会签齐备）

关键建模判断：**会签方不能在建单时固定**——同一 HITL 节点下不同处置动作的
会签方不同（来料"退货"只需采购会签，"特采"需 PQE+业务+QA 三方会签），
因此必须先"选定动作"才能确定 required_signoffs；禁止取全部动作 cosign 并集。

会签语义（本轮）：**每个会签角色至少 1 人签署**，全部角色签齐才可终批。
每个 required_signoff 项固定输出 ``min_count: 1``——这是未来"任签 N"
（任一人签即可 / 需 N 人签）的演进位：语义扩展时只改 ``signoff_status()`` 的
满足判定与 ``min_count`` 取值，``required_signoffs`` 的数据形状不变。
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
from ..contracts.permissions import (
    PermissionRegistry,
    get_permission_registry,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PermissionDenied",
    "SignoffIncomplete",
    "WorkflowEngine",
    "WorkflowTask",
    "WorkflowTransitionError",
    "TRANSITIONS",
    "TERMINAL_STATUSES",
    "GOVERNANCE_SLOTS",
]


class WorkflowTransitionError(Exception):
    """非法状态迁移（安全边界：防止跳过审批直接放行）。"""


class PermissionDenied(Exception):
    """当前岗位无该动作权限（fail-closed：未识别角色同样拒绝）。

    Attributes:
        required: 有权执行该动作的角色中文名列表（拼 403 提示用）。
        actual: 操作者实际归一化后的角色中文名列表（审计用）。
    """

    def __init__(self, message: str = "", required: Optional[list[str]] = None,
                 actual: Optional[list[str]] = None) -> None:
        self.required = list(required or [])
        self.actual = list(actual or [])
        super().__init__(message or "当前岗位无该动作的审批权限")


class SignoffIncomplete(Exception):
    """多人会签未齐备（缺一不可，禁止提前放行）。

    Attributes:
        missing: 缺失会签人 ``[{"role": "pqe", "role_name": "PQE"}, ...]``。
        progress: 会签进度，形如 ``"1/3"``。
    """

    def __init__(self, message: str = "", missing: Optional[list[dict[str, str]]] = None,
                 progress: str = "") -> None:
        self.missing = list(missing or [])
        self.progress = progress
        super().__init__(message or "会签未完成")


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

# 治理层支持的 7 个 HITL 槽位（与 v2/data/permissions.yaml 的 slots 对齐）
GOVERNANCE_SLOTS: frozenset[str] = frozenset({
    "restart", "eta", "disposition", "d8_close", "complaint_reply",
    "delivery_commit", "maintenance_window",
})


@dataclass
class WorkflowTask:
    """工作流工单（DB 行的结构化视图）。

    ``governance`` 是治理链快照（独立列 ``governance_json`` 存储）：
        {
          "selected_action": "特采（让步接收）", "action_key": "concession",
          "slot": "disposition",
          "required_signoffs": [{"role": "pqe", "role_name": "PQE",
                                 "kind": "cosign", "min_count": 1,
                                 "permission": "hitl.disposition.cosign"}],
          "signoffs": [{"role": "pqe", "actor": "王倩", "action": "cosign",
                        "decision": "", "comment": "", "ts": "..."}],
          "approvals": [{"role": "qa_manager", "actor": "...",
                         "decision": "...", "ts": "..."}],
        }
    ``min_count`` 是本轮固定值 1（每角色至少 1 人签），为未来"任签 N"预留的
    演进位；满足判定统一收敛在 ``signoff_status()``，扩展时无需改数据结构。
    老工单（本轮之前的存量）该字段为空 dict，行为保持宽松（向后兼容）。
    """

    task_uid: str
    wf_id: str
    title: str
    status: str = "pending"
    priority: str = "P1"
    assignees: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    hitl_prompt: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    governance: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    sla_due_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["governance"] = dict(self.governance or {})
        data["signoff_status"] = self.signoff_status()
        return data

    # ---------- 治理视图 ----------

    @property
    def slot(self) -> str:
        """HITL 槽位（restart/eta/disposition/...；老工单可能为空串）。"""
        return str((self.governance or {}).get("slot") or "")

    def is_governed(self) -> bool:
        """是否启用治理校验（新工单 = 已知槽位 + 建单时携带处置动作）。

        槽位不在 7 个合法槽位内时按"未纳管"处理（与老工单同路径），
        避免脏数据在权限查询时抛错；纳管工单的槽位只可能来自本模块的归一化。
        """
        return self.slot in GOVERNANCE_SLOTS

    def signoff_status(self) -> dict[str, Any]:
        """会签进度视图（前端进度条 / 409 缺口提示数据源）。

        满足判定收敛在本方法（当前：每个会签角色至少 1 人签）。
        未来支持"任签 N"时只改这里的判定与 ``min_count`` 取值。
        """
        gov = self.governance or {}
        required = [dict(r) for r in (gov.get("required_signoffs") or [])]
        signoffs = [dict(s) for s in (gov.get("signoffs") or [])]
        signed_roles = {str(s.get("role") or "") for s in signoffs}
        signed = [r for r in required if str(r.get("role") or "") in signed_roles]
        missing = [
            {"role": str(r.get("role") or ""),
             "role_name": str(r.get("role_name") or r.get("role") or "")}
            for r in required if str(r.get("role") or "") not in signed_roles
        ]
        total = len(required)
        done = len(signed)
        return {
            "required": required,
            "signed": signed,
            "missing": missing,
            "satisfied": done >= total,
            "progress": f"{done}/{total}",
            "total": total,
            "done": done,
            "selected_action": str(gov.get("selected_action") or ""),
            "action_key": str(gov.get("action_key") or ""),
            "slot": self.slot,
            "approvals": [dict(a) for a in (gov.get("approvals") or [])],
        }

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
    """工作流引擎：状态机 + SLA 计时 + HITL 待办队列 + 角色权限/会签治理。

    SQLite 持久化（platform_workflow_task），可重放、可审计。
    :memory: 场景使用进程内持久单连接（修复 BUG-4：DDL 与 DML 同一库）。
    """

    def __init__(self, db_url: str, default_sla_hours: float = 2.0,
                 permission_registry: Optional[PermissionRegistry] = None) -> None:
        self.db_url = db_url
        self.db_path = sqlite_path_from_url(db_url)
        self.default_sla_hours = default_sla_hours
        self.permission_registry = permission_registry or get_permission_registry()
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
        """创建工单；hitl=True 时直接进入 waiting_hitl（待人工确认，绝不自动放行）。

        治理链初始化条件：``hitl=True`` + 能解析出已知 slot + ``context`` 携带
        ``actions``（真实处置节点）。其余工单一律 ``governance={}``，保持既有宽松行为
        （向后兼容存量工单与不涉及处置动作的内部工单）。
        """
        now = datetime.now(timezone.utc)
        sla = now + timedelta(hours=sla_hours if sla_hours is not None
                              else self.default_sla_hours)
        task_context = dict(context or {})
        task = WorkflowTask(
            task_uid=f"wf-{uuid.uuid4().hex[:12]}",
            wf_id=wf_id, title=title,
            status="waiting_hitl" if hitl else "pending",
            priority=priority,
            assignees=list(assignees or []),
            context=task_context,
            hitl_prompt=hitl_prompt,
            governance=self._init_governance(task_context, wf_id) if hitl else {},
            created_at=now.isoformat(), updated_at=now.isoformat(),
            sla_due_at=sla.isoformat(),
        )
        task.history.append(self._history_entry("create", "system", "", "pending",
                                                task.status, "创建工单"))
        self._insert(task)
        logger.info("[workflow] 创建工单 %s（%s）status=%s slot=%s", task.task_uid,
                    wf_id, task.status, task.slot or "-")
        return task

    @staticmethod
    def _init_governance(context: dict[str, Any], wf_id: str) -> dict[str, Any]:
        """初始化治理快照（无 slot 或无处置动作 → 空 dict，走宽松老路径）。"""
        slot = WorkflowEngine._derive_slot(context, wf_id)
        actions = context.get("actions") or []
        if not slot or not actions:
            return {}
        return {
            "selected_action": "",
            "action_key": "",
            "slot": slot,
            "required_signoffs": [],
            "signoffs": [],
            "approvals": [],
        }

    @staticmethod
    def _derive_slot(context: dict[str, Any], wf_id: str) -> str:
        """由 context.slot / context.kind / wf_id(hitl_xxx) 归一出 HITL 槽位。"""
        ctx = context or {}
        for candidate in (ctx.get("slot"), ctx.get("kind")):
            text = str(candidate or "").strip()
            if text in GOVERNANCE_SLOTS:
                return text
        prefix = "hitl_"
        wf = str(wf_id or "")
        if wf.startswith(prefix):
            tail = wf[len(prefix):]
            if tail in GOVERNANCE_SLOTS:
                return tail
        return ""

    def advance(self, task_uid: str, action: str, actor: str = "system",
                comment: str = "") -> WorkflowTask:
        """通用状态推进（系统内部入口：启动/关闭/升级）。

        action: start | approve | reject | escalate | done | close

        受治理工单（新 HITL 工单）的 approve/reject **必须**走 ``approve()`` /
        ``reject()``，禁止经本方法绕过角色校验与会签门控。
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
        if action in ("approve", "reject") and self.get(task_uid).is_governed():
            raise WorkflowTransitionError(
                f"受治理工单不允许经 advance({action!r}) 绕过审批：请走 "
                f"select_action/cosign/approve 治理链")
        return self._transition(task_uid, action_map[action], actor, action, comment)

    # ---------- 治理：选定动作 → 会签 → 终批 ----------

    def select_action(self, task_uid: str, action_key: str, actor: str = "system",
                      role: Optional[str] = None,
                      user_ctx: Optional[dict] = None,
                      comment: str = "") -> WorkflowTask:
        """选定处置动作：按该动作的 cosign 列表生成会签要求（status 不变）。

        Raises:
            WorkflowTransitionError: 状态非法 / 动作不存在 / 未声明治理链。
            PermissionDenied: 操作者岗位不在该动作的 propose 角色内（fail-closed）。
            ValueError: 该动作 ``require_comment`` 但未填写决策依据。
        """
        task = self._require_governed_hitl(task_uid, "select_action")
        actions = self._actions_of(task)
        if not actions:
            raise WorkflowTransitionError("工单未携带可选处置动作（context.actions 为空）")
        action = next((a for a in actions
                       if str(a.get("key") or "") == str(action_key or "")), None)
        if action is None:
            raise WorkflowTransitionError(f"未知处置动作: {action_key!r}")

        registry = self.permission_registry
        slot = task.slot
        role_keys = registry.resolve_actor_roles(role, user_ctx)
        propose_names = [str(p) for p in (action.get("propose") or [])]
        propose_keys = list(registry.normalize_roles(propose_names))
        propose_perm = registry.slot_permission(slot, "propose")

        if not role_keys:
            raise PermissionDenied(
                f"未能识别操作者岗位（role/user_ctx.roles 均为空或未知），"
                f"该动作需由 {' / '.join(propose_names) or '有权岗位'} 发起",
                required=propose_names, actual=[])
        if propose_keys and not (set(role_keys) & set(propose_keys)):
            raise PermissionDenied(
                f"当前岗位无该动作的发起权限（需：{' / '.join(propose_names)}）",
                required=[registry.role_name(k) for k in propose_keys],
                actual=registry.role_names(role_keys))
        if not registry.has_any(role_keys, [propose_perm]):
            raise PermissionDenied(
                f"当前岗位无该动作的发起权限（权限：{propose_perm}）",
                required=registry.roles_with_permission_names(propose_perm),
                actual=registry.role_names(role_keys))

        comment_text = (comment or "").strip()
        if bool(action.get("require_comment")) and not comment_text:
            raise ValueError(
                f"处置动作「{action.get('label') or action_key}」需填写决策依据（意见不能为空）")

        required_signoffs = self._build_required_signoffs(task, action)
        previous_key = str((task.governance or {}).get("action_key") or "")
        governance = dict(task.governance or {})
        governance["slot"] = slot
        governance["action_key"] = str(action_key)
        governance["selected_action"] = str(action.get("label") or action_key)
        governance["required_signoffs"] = required_signoffs
        governance.setdefault("signoffs", [])
        governance.setdefault("approvals", [])
        if previous_key != str(action_key):
            # 改选动作 → 旧的会签事实失效（会签要求已变化，必须重新会签）
            governance["signoffs"] = []
        comment_out = f"选定动作：{governance['selected_action']}"
        if comment_text:
            comment_out += f"；依据：{comment_text}"
        return self._transition(task_uid, task.status, actor, "select_action",
                                comment_out, governance=governance, in_place=True)

    def cosign(self, task_uid: str, actor: str = "system",
               role: Optional[str] = None,
               user_ctx: Optional[dict] = None,
               decision: str = "",
               comment: str = "") -> WorkflowTask:
        """会签（多角色背书，缺一不可）。

        Raises:
            WorkflowTransitionError: 未选动作 / 该角色已会签 / 状态非法。
            PermissionDenied: 操作者岗位不在该动作的会签人范围（fail-closed）。
        """
        task = self._require_governed_hitl(task_uid, "cosign")
        governance = dict(task.governance or {})
        if not governance.get("action_key"):
            raise WorkflowTransitionError("请先选定处置动作（select_action），再进行会签")

        registry = self.permission_registry
        slot = task.slot
        required = [dict(r) for r in (governance.get("required_signoffs") or [])]
        required_keys = [str(r.get("role") or "") for r in required]
        signoffs = [dict(s) for s in (governance.get("signoffs") or [])]
        signed_keys = {str(s.get("role") or "") for s in signoffs}

        role_keys = registry.resolve_actor_roles(role, user_ctx)
        if not role_keys:
            raise PermissionDenied(
                f"未能识别会签人岗位（role/user_ctx.roles 均为空或未知），"
                f"会签人：{' / '.join(r.get('role_name') or '' for r in required) or '无'}",
                required=[str(r.get("role_name") or "") for r in required],
                actual=[])
        candidates = [k for k in role_keys if k in required_keys]
        if not candidates:
            raise PermissionDenied(
                f"当前岗位不在该动作的会签人范围（需会签："
                f"{'、'.join(str(r.get('role_name') or '') for r in required) or '无'}）",
                required=[str(r.get("role_name") or "") for r in required],
                actual=registry.role_names(role_keys))
        pending = [k for k in candidates if k not in signed_keys]
        if not pending:
            raise WorkflowTransitionError("该角色已会签，请勿重复提交")
        role_key = pending[0]
        cosign_perm = registry.slot_permission(slot, "cosign")
        if not registry.has_any([role_key], [cosign_perm]):
            raise PermissionDenied(
                f"岗位 {registry.role_name(role_key)} 不持有会签权限（{cosign_perm}）",
                required=registry.roles_with_permission_names(cosign_perm),
                actual=[registry.role_name(role_key)])

        signoffs.append({
            "role": role_key,
            "role_name": registry.role_name(role_key),
            "actor": actor,
            "action": "cosign",
            "decision": (decision or "同意").strip(),
            "comment": (comment or "").strip(),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        governance["signoffs"] = signoffs
        progress = f"{len({s.get('role') for s in signoffs})}/{len(required)}"
        comment_out = f"{registry.role_name(role_key)} 会签（{progress}）"
        if (comment or "").strip():
            comment_out += f"；{comment.strip()}"
        return self._transition(task_uid, task.status, actor, "cosign",
                                comment_out, governance=governance, in_place=True)

    def approve(self, task_uid: str, approver: str,
                comment: str = "", final: bool = False,
                decision: str = "", role: Optional[str] = None,
                user_ctx: Optional[dict] = None) -> WorkflowTask:
        """HITL 审批通过：waiting_hitl → running（final=True 时 → done）。

        受治理工单（新工单）额外强制：
        1. 已选定处置动作（否则 409）；
        2. 操作者岗位持有该 slot 的 ``approve`` 权限（否则 403，禁止匿名终批）；
        3. 会签齐备（否则 409 + 缺口清单）。

        decision：所选处置动作（如"特采（让步接收）"），写入 history 供审计。
        老工单（``governance`` 为空）保持原有宽松行为，向后兼容。
        """
        task = self.get(task_uid)
        governance: Optional[dict[str, Any]] = None
        if task.is_governed():
            role_key = self._assert_approve_permission(task, role, user_ctx)
            self._assert_signoffs_complete(task)
            governance = self._append_approval(
                task, role_key, approver, decision or "approve", comment)
        comment_out = self._with_decision(decision, comment)
        return self._transition(task_uid, "done" if final else "running",
                                approver, "approve", comment_out,
                                governance=governance)

    def reject(self, task_uid: str, approver: str, comment: str = "",
               decision: str = "", role: Optional[str] = None,
               user_ctx: Optional[dict] = None) -> WorkflowTask:
        """HITL 审批驳回：→ closed。

        驳回不要求会签齐备（会签未齐也可驳回止损），但仍校验岗位权限
        （持有该 slot 的 ``propose`` 或 ``approve`` 权限之一）。
        """
        task = self.get(task_uid)
        governance: Optional[dict[str, Any]] = None
        if task.is_governed():
            role_key = self._assert_reject_permission(task, role, user_ctx)
            governance = self._append_approval(
                task, role_key, approver, "reject", comment)
        comment_out = self._with_decision(decision, comment)
        return self._transition(task_uid, "closed", approver, "reject",
                                comment_out, governance=governance)

    @staticmethod
    def _with_decision(decision: str, comment: str) -> str:
        """把处置动作并入审批意见（history 可读、可审计）。"""
        decision = (decision or "").strip()
        if not decision:
            return comment
        return f"决策：{decision}；{comment}" if comment else f"决策：{decision}"

    # ---------- 治理内部校验 ----------

    def _require_governed_hitl(self, task_uid: str, action: str) -> WorkflowTask:
        """治理动作前置：工单存在、状态 waiting_hitl、已声明治理链。"""
        task = self.get(task_uid)
        if not task.is_governed():
            raise WorkflowTransitionError(
                f"工单 {task_uid} 未声明治理链（无 HITL 槽位/处置动作），不支持 {action}")
        if task.status != "waiting_hitl":
            raise WorkflowTransitionError(
                f"工单状态 {task.status} 不允许 {action}（需 waiting_hitl）")
        return task

    @staticmethod
    def _actions_of(task: WorkflowTask) -> list[dict[str, Any]]:
        """工单可选处置动作（context.actions，HITL 建单时写入）。"""
        raw = (task.context or {}).get("actions") or []
        return [dict(a) for a in raw if isinstance(a, dict)]

    def _build_required_signoffs(self, task: WorkflowTask,
                                 action: dict[str, Any]) -> list[dict[str, str]]:
        """按动作 cosign 列表生成会签要求（角色归一化；未知角色 fail-closed）。

        Raises:
            ValueError: cosign 中出现未识别角色，或某会签角色不持有 cosign 权限
                （配置错误必须显式失败，不能静默减少会签人 → 否则即 fail-open）。
        """
        registry = self.permission_registry
        slot = task.slot
        cosign_perm = registry.slot_permission(slot, "cosign")
        names = [str(c) for c in (action.get("cosign") or [])]
        unknown = [n for n in names if registry.normalize_role(n) is None]
        if unknown:
            raise ValueError(f"处置动作会签角色未识别，拒绝生成会签要求: {unknown}")
        required: list[dict[str, str]] = []
        seen: set[str] = set()
        for name in names:
            key = registry.normalize_role(name)
            if not key or key in seen:
                continue
            seen.add(key)
            if not registry.has_any([key], [cosign_perm]):
                raise ValueError(
                    f"会签角色 {registry.role_name(key)} 不持有权限 {cosign_perm}"
                    f"（权限目录与场景 YAML 不一致）")
            required.append({
                "role": key,
                "role_name": registry.role_name(key),
                "kind": "cosign",
                # min_count=1：本轮语义"每角色至少 1 人签"；未来"任签 N"的演进位
                "min_count": 1,
                "permission": cosign_perm,
            })
        return required

    def _assert_approve_permission(self, task: WorkflowTask, role: Optional[str],
                                   user_ctx: Optional[dict]) -> str:
        """终批权限校验：返回主责 role key。"""
        registry = self.permission_registry
        approve_perm = registry.slot_permission(task.slot, "approve")
        allowed_names = registry.roles_with_permission_names(approve_perm)
        role_keys = registry.resolve_actor_roles(role, user_ctx)
        if not role_keys:
            raise PermissionDenied(
                f"缺少可核验的岗位身份（user_ctx.roles 为空或岗位未知），"
                f"禁止匿名终批；该动作需：{' / '.join(allowed_names)}",
                required=allowed_names, actual=[])
        if not registry.has_any(role_keys, [approve_perm]):
            raise PermissionDenied(
                f"当前岗位无该动作的审批权限（需：{' / '.join(allowed_names)}）",
                required=allowed_names, actual=registry.role_names(role_keys))
        for key in role_keys:
            if registry.has_any([key], [approve_perm]):
                return key
        return role_keys[0]

    def _assert_signoffs_complete(self, task: WorkflowTask) -> None:
        """会签门控：未选动作 → 409；会签未齐 → SignoffIncomplete（缺口清单）。"""
        governance = task.governance or {}
        if not governance.get("action_key"):
            raise WorkflowTransitionError(
                "请先选定处置动作（select_action）并完成会签，再进行终批")
        status = task.signoff_status()
        required_total = int(status.get("total") or 0)
        if required_total and not status.get("satisfied"):
            missing = status.get("missing") or []
            names = "、".join(str(m.get("role_name") or "") for m in missing)
            raise SignoffIncomplete(
                f"会签未完成（{status.get('progress')}）：待 {names} 会签",
                missing=missing, progress=str(status.get("progress") or ""))

    def _assert_reject_permission(self, task: WorkflowTask, role: Optional[str],
                                  user_ctx: Optional[dict]) -> str:
        """驳回权限校验：持有 propose 或 approve 权限之一即可（止损通道）。"""
        registry = self.permission_registry
        propose_perm = registry.slot_permission(task.slot, "propose")
        approve_perm = registry.slot_permission(task.slot, "approve")
        allowed = sorted(set(registry.roles_with_permission(propose_perm))
                         | set(registry.roles_with_permission(approve_perm)))
        allowed_names = [registry.role_name(k) for k in allowed]
        role_keys = registry.resolve_actor_roles(role, user_ctx)
        if not role_keys:
            raise PermissionDenied(
                f"缺少可核验的岗位身份（user_ctx.roles 为空或岗位未知），禁止匿名驳回；"
                f"驳回需：{' / '.join(allowed_names)}",
                required=allowed_names, actual=[])
        if not registry.has_any(role_keys, [propose_perm, approve_perm]):
            raise PermissionDenied(
                f"当前岗位无该动作的驳回权限（需：{' / '.join(allowed_names)}）",
                required=allowed_names, actual=registry.role_names(role_keys))
        for key in role_keys:
            if registry.has_any([key], [propose_perm, approve_perm]):
                return key
        return role_keys[0]

    def _append_approval(self, task: WorkflowTask, role_key: str, actor: str,
                         decision: str, comment: str) -> dict[str, Any]:
        """追加终批/驳回留痕（返回新的 governance 快照）。"""
        governance = dict(task.governance or {})
        approvals = [dict(a) for a in (governance.get("approvals") or [])]
        approvals.append({
            "role": role_key,
            "role_name": self.permission_registry.role_name(role_key),
            "actor": actor,
            "decision": str(decision or "").strip(),
            "comment": (comment or "").strip(),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        governance["approvals"] = approvals
        return governance

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

    def pending_hitl(self, role_names: Optional[list[str]] = None) -> list[WorkflowTask]:
        """HITL 待办队列（IM 卡片催办 / 看板数据源）。

        Args:
            role_names: 操作者的角色写法列表（中文/别名/英文 key 均可）。
                传了就只返回**该角色在当前阶段可动作**的工单（审批即待办）；
                不传则返回全量 waiting_hitl（向后兼容既有调用方）。

        Returns:
            工单列表（``my_action`` 由 ``action_for()`` 单独计算，见 API 层）。
        """
        tasks = self.list_tasks(status="waiting_hitl")
        if not role_names:
            return tasks
        return [t for t in tasks if self.action_for(t, role_names) is not None]

    def action_for(self, task: WorkflowTask,
                   role_names: Optional[list[str]]) -> Optional[str]:
        """当前角色在该工单上**可执行**的动作（"审批即待办"的判定核心）。

        Returns:
            ``"select_action"``：角色出现在该节点某动作的 propose 列表内；
            ``"cosign"``：角色在已选动作的 required_signoffs 内且尚未签署；
            ``"approve"``：角色持有该 slot 的 approve 权限且会签已齐；
            ``None``：只看不办（无任何可执行动作）。
        """
        registry = self.permission_registry
        role_keys = set(registry.normalize_roles(role_names or []))
        if not role_keys or not task.is_governed():
            return None
        slot = task.slot
        governance = task.governance or {}

        if governance.get("action_key"):
            required = [str(r.get("role") or "")
                        for r in (governance.get("required_signoffs") or [])]
            signed = {str(s.get("role") or "")
                      for s in (governance.get("signoffs") or [])}
            if any(k in required and k not in signed for k in role_keys):
                return "cosign"
            approve_perm = registry.slot_permission(slot, "approve")
            if (registry.has_any(role_keys, [approve_perm])
                    and task.signoff_status().get("satisfied")):
                return "approve"
            return None

        # 未选动作：只要角色落在该节点任一动作的 propose 列表内即可发起
        propose_perm = registry.slot_permission(slot, "propose")
        if not registry.has_any(role_keys, [propose_perm]):
            return None
        for action in self._actions_of(task):
            action_keys = set(registry.normalize_roles(action.get("propose") or []))
            if action_keys & role_keys:
                return "select_action"
        return None

    def check_sla(self, task: WorkflowTask) -> bool:
        """SLA 超时检测（True=已超时且仍在途）。"""
        return bool(task.sla_status().get("overdue"))

    # ---------- 内部 ----------

    def _transition(self, task_uid: str, target: str, actor: str,
                    action: str, comment: str,
                    governance: Optional[dict[str, Any]] = None,
                    in_place: bool = False) -> WorkflowTask:
        """状态迁移并落库。

        ``in_place=True`` 表示"原地留痕"（select_action/cosign 不改变状态，
        但仍写 history 与会签事实，形成可审计链路）；此时要求 target 与当前一致。
        """
        task = self.get(task_uid)
        if in_place:
            if target != task.status:
                raise ValueError(
                    f"原地留痕要求目标状态与当前一致: {task.status} != {target}")
        elif target not in TRANSITIONS.get(task.status, set()):
            raise WorkflowTransitionError(
                f"非法状态迁移: {task.status} -> {target}（工单 {task_uid}）")
        old = task.status
        task.status = target
        task.updated_at = datetime.now(timezone.utc).isoformat()
        if governance is not None:
            task.governance = governance
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
                " hitl_prompt, history_json, governance_json, created_at, updated_at, "
                " sla_due_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.task_uid, task.wf_id, task.title, task.status, task.priority,
                 json.dumps(task.assignees, ensure_ascii=False),
                 json.dumps(task.context, ensure_ascii=False),
                 task.hitl_prompt,
                 json.dumps(task.history, ensure_ascii=False),
                 json.dumps(task.governance or {}, ensure_ascii=False),
                 task.created_at, task.updated_at, task.sla_due_at))

    def _update(self, task: WorkflowTask) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE platform_workflow_task SET status = ?, priority = ?, "
                "assignees = ?, context_json = ?, hitl_prompt = ?, "
                "history_json = ?, governance_json = ?, updated_at = ?, "
                "sla_due_at = ? "
                "WHERE task_uid = ?",
                (task.status, task.priority,
                 json.dumps(task.assignees, ensure_ascii=False),
                 json.dumps(task.context, ensure_ascii=False),
                 task.hitl_prompt,
                 json.dumps(task.history, ensure_ascii=False),
                 json.dumps(task.governance or {}, ensure_ascii=False),
                 task.updated_at, task.sla_due_at, task.task_uid))

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> WorkflowTask:
        # 老库（未迁移）可能没有 governance_json 列：容错回落空 dict
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        raw_governance = row["governance_json"] if "governance_json" in keys else "{}"
        try:
            governance = json.loads(raw_governance or "{}")
        except (TypeError, ValueError):
            logger.warning("[workflow] governance_json 解析失败，按空治理链处理")
            governance = {}
        if not isinstance(governance, dict):
            governance = {}
        return WorkflowTask(
            task_uid=row["task_uid"], wf_id=row["wf_id"], title=row["title"],
            status=row["status"], priority=row["priority"],
            assignees=json.loads(row["assignees"] or "[]"),
            context=json.loads(row["context_json"] or "{}"),
            hitl_prompt=row["hitl_prompt"] or "",
            history=json.loads(row["history_json"] or "[]"),
            governance=governance,
            created_at=row["created_at"], updated_at=row["updated_at"],
            sla_due_at=row["sla_due_at"],
        )

    @staticmethod
    def _history_entry(action: str, actor: str, comment: str, old: str,
                       new: str, reason: str) -> dict[str, Any]:
        return {"ts": datetime.now(timezone.utc).isoformat(), "action": action,
                "actor": actor, "comment": comment, "from": old, "to": new,
                "reason": reason}
