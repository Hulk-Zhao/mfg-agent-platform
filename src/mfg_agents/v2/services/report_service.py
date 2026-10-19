"""报告中心服务层：归档 / 生成 / 列表 / 详情 / 审定 / 派发 / 导出。

架构位置：夹在 ``report_store``（SQLite 真相源）与 ``demo/api.py``（HTTP 契约）之间，
是**唯一**允许拼装报告对象的层——API 层只做鉴权、参数校验与异常→状态码映射。

四条纪律（与平台既有口径同源）：

1. **不编造**：报告字段一律取自编排结果（``OrchestratorResult.to_dict()``）或
   来源模块的既有服务/端点；手工生成的报告显式标记 ``channel=manual`` 并写来源，
   绝不假装成链路产出。
2. **无溯源不输出**：``dispatch``（对外派发）要求 ``provenance`` 非空。
3. **fail-closed 治理**：审定/派发走 ``PermissionRegistry`` 的
   ``hitl.report_publish.{propose,cosign,approve}``；未识别角色 → 拒绝。
4. **两层可见性**：``internal_brief`` 只对内部岗位开放；非内部岗位读
   ``customer`` 报告时自动剥离内部简报（对客产物本身已脱敏）。

渲染契约：返回的字典键名与前端 ``reports-screen.js``、``report_store`` 的行结构一致
（``internal_brief / provenance / data_gaps / linked / versions``）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Mapping, Optional

from ..contracts.permissions import PermissionRegistry, get_permission_registry
from ..services import report_store as store
from ..services.workflow import (
    PermissionDenied,
    SignoffIncomplete,
    WorkflowEngine,
)

logger = logging.getLogger(__name__)

__all__ = [
    "REPORT_SLOT",
    "INTERNAL_VIEW_ROLES",
    "archive_run_result",
    "generate_from_context",
    "list_payload",
    "get_payload",
    "stats_payload",
    "review_report",
    "dispatch_report",
    "export_report",
    "ensure_report_schema",
]

#: 报告审定/派发的 HITL 槽位（与 ``permissions.yaml`` 的 slots 同名）
REPORT_SLOT = "report_publish"

_SLOT_PERM: dict[str, str] = {
    "propose": f"hitl.{REPORT_SLOT}.propose",
    "cosign": f"hitl.{REPORT_SLOT}.cosign",
    "approve": f"hitl.{REPORT_SLOT}.approve",
}

#: 可读「内部简报」的岗位（**与前端 NAV_ITEMS 的 reports.roles 一一对应**——
#: 两处口径必须一致，否则会出现"菜单能进、内容被剥空"的割裂体验）。
#: 前端 id（gm/mfg/qd/sqe/pqe/qa/cs/biz/pm）→ 治理角色 key 的映射在此固定。
INTERNAL_VIEW_ROLES = frozenset({
    "gm", "manufacturing", "quality_director", "sqe", "pqe", "qa",
    "customer_service", "sales", "project_manager",
})

#: 报告编制/派发工单的动作表（与场景 YAML 的 hitl.actions 同构，保持单一事实源）
_REPORT_ACTIONS: list[dict[str, Any]] = [
    {
        "key": "publish",
        "label": "审定通过并发布",
        "propose": ["业务"],
        "cosign": ["质量总监", "业务"],
        "impact": "报告结论与对客口径经复核后发布",
        "followup": "记录版本、允许派发",
    },
    {
        "key": "revise",
        "label": "退回补充数据",
        "propose": ["业务"],
        "impact": "数据缺口或结论存疑 · 退回编制人补充",
        "require_comment": True,
        "followup": "退回编制人补充溯源",
    },
]

_RISK_RE = re.compile(r"评级\s*([A-Da-d])")


def ensure_report_schema() -> str:
    """启动/首次调用时幂等建表；返回实际库路径。"""
    return store.ensure_reports_table()


# ---------- 内部工具 ----------


def _registry() -> PermissionRegistry:
    return get_permission_registry()


def _roles_of(user_ctx: Optional[Mapping[str, Any]]) -> list[str]:
    """权威角色来源：``user_ctx.roles``（令牌载荷），不做任何宽松回退。"""
    return [str(r) for r in ((user_ctx or {}).get("roles") or [])]


def _require_perm(user_ctx: Optional[Mapping[str, Any]], perm: str,
                  action_cn: str) -> None:
    """岗位权限门控（fail-closed 403，提示语拼出"该动作需谁"）。"""
    registry = _registry()
    roles = _roles_of(user_ctx)
    if registry.has_any(roles, [perm]):
        return
    required = registry.roles_with_permission_names(perm) or ["系统管理员"]
    actual = registry.role_names(roles) or ["未识别岗位"]
    raise PermissionDenied(
        f"当前岗位（{'、'.join(actual)}）无权{action_cn}；需：{'、'.join(required)}",
        required=required, actual=actual)


def _extract_risk(brief: Mapping[str, Any]) -> str:
    """从内部简报里提取风险评级字母（``评级B，根因：…`` → ``B``）；提取不到返回空串。"""
    for key, value in (brief or {}).items():
        if "评级" not in str(key):
            continue
        match = _RISK_RE.search(str(value))
        if match:
            return match.group(1).upper()
    return ""


def _linked_of(result: Mapping[str, Any]) -> dict[str, Any]:
    """从编排结果抽取"关联工单"（HITL 待办 + 工作流任务），供深链回审批中心。"""
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(result.get("hitl") or []) + list(result.get("workflow_tasks") or []):
        if not isinstance(item, Mapping):
            continue
        uid = str(item.get("task_uid") or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        tasks.append({
            "task_uid": uid,
            "wf_id": str(item.get("wf_id") or ""),
            "title": str(item.get("title") or ""),
            "slot": str(item.get("slot") or (item.get("governance") or {}).get("slot") or ""),
            "status": str(item.get("status") or ""),
        })
    return {"tasks": tasks, "alerts": [], "cases": [], "policies": []}


def _visible_for(user_ctx: Optional[Mapping[str, Any]]) -> bool:
    """当前岗位是否可读**内部简报**（非该集合的岗位只能读对客产物）。"""
    registry = _registry()
    keys = set(registry.normalize_roles(_roles_of(user_ctx)))
    return bool(keys & INTERNAL_VIEW_ROLES)


def _mask_internal(report: dict[str, Any]) -> dict[str, Any]:
    """剥掉内部简报，只留可对外部分（非内部岗位的读视图）。"""
    stripped = dict(report)
    stripped["internal_brief"] = {}
    stripped["provenance"] = []
    stripped["data_gaps"] = []
    stripped["linked"] = {"tasks": [], "alerts": [], "cases": [], "policies": []}
    stripped["internal_hidden"] = True
    return stripped


# ---------- 归档（问数链路产出 → 报告中心） ----------


def archive_run_result(result: Mapping[str, Any], actor: str = "",
                       source_module: str = "chat") -> Optional[str]:
    """把一次编排结果归档成报告；非报告型结果（无简报无正文）**不入库**，返回 ``None``。

    幂等：``report_id = "rpt-" + run_id``，重复归档只更新不新增。
    """
    if not isinstance(result, Mapping):
        return None
    brief = result.get("internal_brief") or {}
    body = str(result.get("report") or "")
    if not brief and not body:
        return None

    run_id = str(result.get("run_id") or "").strip()
    route = result.get("route") or {}
    scenario = str(result.get("scenario_key") or route.get("scenario_key")
                   or route.get("scenario") or "")
    query = str(result.get("query") or "")
    report_id = f"rpt-{run_id}" if run_id else ""

    customer = body if scenario in ("customer_complaint_v2", "delivery_status_v2") else ""
    # 报告多样性：所有场景都把正文以"报告正文"并入内部简报，供详情页全文展示
    merged_brief = dict(brief)
    if body and "报告正文" not in merged_brief:
        merged_brief["报告正文"] = body

    report = {
        "report_id": report_id,
        "scenario_key": scenario,
        "run_id": run_id,
        "source_module": source_module,
        "title": (query or "未命名报告")[:60],
        "visibility": "customer" if customer else "internal",
        "status": "draft",
        "risk_level": _extract_risk(merged_brief),
        "degraded": bool(result.get("degraded")),
        "actor": actor or "系统生成",
        "internal_brief": merged_brief,
        "customer_report": customer,
        "mask_map": list(result.get("masked_words") or result.get("mask_map") or []),
        "provenance": [p for p in (result.get("provenance") or []) if p],
        "data_gaps": [str(x) for x in (result.get("data_gaps") or [])],
        "linked": _linked_of(result),
        "governing_slot": next(iter(
            [t["slot"] for t in _linked_of(result)["tasks"] if t.get("slot")]), ""),
        "versions": [{
            "ver": "v1.0",
            "time": store._now(),
            "actor": actor or "系统生成",
            "badge": "初版",
            "note": f"链路归档（{scenario or 'qa'} · 溯源 "
                    f"{len(result.get('provenance') or [])} 条）",
        }],
    }
    if not report_id:
        # 无 run_id（极少：手工构造的结果）→ 用标题+场景派生稳定 id，仍保证幂等
        import hashlib
        digest = hashlib.sha1(f"{scenario}|{query}".encode("utf-8")).hexdigest()[:12]
        report["report_id"] = f"rpt-manual-{digest}"

    try:
        return store.upsert_report(report)
    except store.ReportStoreError as exc:  # 归档失败绝不能反噬问数主链路
        logger.warning("[report] 归档失败（不影响问数返回）: %s", exc)
        return None


# ---------- 生成（跨模块就地按钮 → 报告） ----------


def generate_from_context(source_module: str, context: Mapping[str, Any],
                          actor: str = "", engine: Optional[WorkflowEngine] = None
                          ) -> dict[str, Any]:
    """按来源模块的上下文生成一份报告并入库（人工触发，显式标注来源）。

    ``source_module`` ∈ ``task``（工单，后端补真实事实）/ ``alert`` / ``case`` /
    ``kpi`` / ``manual``。除 ``task`` 外，事实由调用方（前端已渲染的真实字段）
    提供——因此 provenance 一律标 ``channel=manual``，**不冒充实链路产出**。

    Raises:
        ValueError: 缺少标题（400）。
    """
    module = str(source_module or "").strip() or "manual"
    ctx = dict(context or {})
    title = str(ctx.get("title") or "").strip()
    if not title:
        raise ValueError("生成报告必须提供标题（title）")

    brief: dict[str, Any] = {}
    provenance: list[dict[str, Any]] = []
    linked: dict[str, Any] = {"tasks": [], "alerts": [], "cases": [], "policies": []}
    scenario = str(ctx.get("scenario_key") or "")
    governing_slot = ""
    risk = str(ctx.get("risk_level") or "")

    if module == "task":
        task_uid = str(ctx.get("task_uid") or ctx.get("ref") or "").strip()
        if not task_uid or engine is None:
            raise ValueError("从工单生成报告需要有效的 task_uid")
        task = engine.get(task_uid)  # KeyError → 404
        brief.update({
            "工单": f"{task.title}（{task.task_uid}）",
            "状态": task.status,
            "优先级": task.priority,
            "治理槽位": task.slot or "未纳管",
            "候批岗位": "、".join(task.assignees) or "—",
            "处理留痕": "；".join(
                f"{h.get('action')}·{h.get('actor')}·{h.get('comment')}"
                for h in (task.history or [])[-4:]) or "—",
        })
        linked["tasks"] = [{
            "task_uid": task.task_uid, "wf_id": task.wf_id,
            "title": task.title, "slot": task.slot, "status": task.status,
        }]
        governing_slot = task.slot
        provenance.append({
            "channel": "manual", "source_module": "task",
            "domain": "workflow", "ref": task_uid,
            "note": f"由 {actor or '用户'} 从审批确认中心工单生成",
        })
        scenario = scenario or str(task.context.get("scene") or "")
        risk = risk or str((task.governance or {}).get("risk") or "")
    else:
        summary = str(ctx.get("summary") or "")
        if summary:
            brief["摘要"] = summary
        for key, label in (("line_code", "产线"), ("product_model", "型号"),
                           ("customer", "客户"), ("owner", "责任人")):
            if ctx.get(key):
                brief[label] = str(ctx[key])
        refs: Iterable[Any] = ctx.get("refs") or []
        for ref in refs:
            provenance.append({
                "channel": "manual", "source_module": module,
                "domain": module, "ref": str(ref),
                "note": f"由 {actor or '用户'} 从{'预警中心' if module == 'alert' else '模块'}生成",
            })
        if not provenance:
            provenance.append({
                "channel": "manual", "source_module": module, "domain": module,
                "ref": str(ctx.get("ref") or title),
                "note": f"手工生成（来源模块 {module}），事实由发起界面提供",
            })
        linked = {**linked, **dict(ctx.get("linked") or {})}

    brief.setdefault("来源", f"手工生成 · {module}")
    if ctx.get("summary") and "摘要" not in brief:
        brief["摘要"] = str(ctx["summary"])

    report = {
        "report_id": "",
        "scenario_key": scenario,
        "run_id": str(ctx.get("run_id") or ""),
        "source_module": module,
        "title": title[:60],
        "visibility": "customer" if str(ctx.get("visibility") or "") == "customer"
                      else "internal",
        "status": "draft",
        "risk_level": risk,
        "degraded": False,
        "actor": actor or "用户生成",
        "internal_brief": brief,
        "customer_report": str(ctx.get("customer_report") or ""),
        "mask_map": [],
        "provenance": provenance,
        "data_gaps": [],
        "linked": linked,
        "governing_slot": governing_slot,
        "versions": [{
            "ver": "v1.0", "time": store._now(), "actor": actor or "用户生成",
            "badge": "初版", "note": f"就地生成（来源：{module}）",
        }],
    }
    import uuid
    report["report_id"] = f"rpt-gen-{uuid.uuid4().hex[:10]}"
    store.upsert_report(report)
    return store.get_report(report["report_id"])


# ---------- 读取 ----------


def list_payload(user_ctx: Optional[Mapping[str, Any]], *,
                 scenario_key: str = "", source_module: str = "", status: str = "",
                 visibility: str = "", risk_level: str = "", q: str = "",
                 limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """列表 + 筛选 + 分页；内部岗位读全文，其余岗位视图自动剥离内部简报。"""
    rows, total = store.list_reports(
        scenario_key=scenario_key, source_module=source_module, status=status,
        visibility=visibility, risk_level=risk_level, q=q,
        limit=limit, offset=offset)
    can_internal = _visible_for(user_ctx)
    if not can_internal:
        rows = [
            _mask_internal(row) if row.get("visibility") != "customer" else row
            for row in rows
        ]
        # 内部专报对非内部岗位不可见（不是"看不到列表"，而是"这条没有你的份"）
        rows = [row for row in rows if row.get("visibility") == "customer"]
        total = len(rows)
    return {"items": rows, "total": total,
            "limit": max(1, min(int(limit or 50), 200)),
            "offset": max(0, int(offset or 0)),
            "scope": {"roles": _registry().role_names(_roles_of(user_ctx)),
                      "internal_visible": can_internal}}


def get_payload(report_id: str,
                user_ctx: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """详情。不可见 → ``ReportNotFound``（404：不泄漏 ID 空间，与工单同口径）。"""
    report = store.get_report(report_id)
    if _visible_for(user_ctx):
        return report
    if report.get("visibility") == "customer":
        return _mask_internal(report)
    raise store.ReportNotFound(f"报告不存在: {report_id}")


def stats_payload(user_ctx: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """顶部 KPI 概览条：各状态计数 + 含数据缺口数。"""
    counts = store.count_by_status()
    can_internal = _visible_for(user_ctx)
    gaps = counts.get("with_gaps", 0) if can_internal else 0
    return {
        "total": counts.get("total", 0),
        "draft": counts.get("draft", 0),
        "pending_review": counts.get("pending_review", 0),
        "approved": counts.get("approved", 0),
        "dispatched": counts.get("dispatched", 0),
        "rejected": counts.get("rejected", 0),
        "archived": counts.get("archived", 0),
        "with_gaps": gaps,
        "internal_visible": can_internal,
    }


# ---------- 治理：审定 / 派发 ----------


def review_report(report_id: str, action: str, comment: str,
                  user_ctx: Optional[Mapping[str, Any]],
                  engine: WorkflowEngine) -> dict[str, Any]:
    """报告审定（submit / approve / reject），与审批确认中心共用一套治理链。

    - ``submit``：需 ``propose`` 权限 → 建真实 HITL 工单（进入审批确认中心待办），
      报告 ``draft/rejected → pending_review``；
    - ``approve``：需 ``approve`` 权限 → 走 ``WorkflowEngine.approve``（会签未齐 → 409），
      报告 ``pending_review → approved``；
    - ``reject``：需 ``propose``/``approve`` 权限 + **必填理由** → 报告 ``→ rejected``。
    """
    verb = str(action or "").strip().lower()
    report = store.get_report(report_id)
    actor_name = str((user_ctx or {}).get("name") or "演示用户")

    if verb == "submit":
        _require_perm(user_ctx, _SLOT_PERM["propose"], "发起报告审定")
        if report["status"] not in ("draft", "rejected"):
            raise store.InvalidReportTransition(
                f"报告当前状态 {report['status']} 不能发起审定")
        task = engine.create_task(
            wf_id="hitl_report_publish",
            title=f"报告审定发布：{report['title']}",
            context={
                "slot": REPORT_SLOT,
                "report_id": report_id,
                "scenario_key": report.get("scenario_key", ""),
                "risk_level": report.get("risk_level", ""),
                "actions": _REPORT_ACTIONS,
            },
            assignees=["业务经理", "质量总监"],
            priority="P2", sla_hours=8, hitl=True,
            hitl_prompt="报告结论与对客口径复核：请按岗位职责完成会签与终批",
        )
        # 发起即选定「审定通过并发布」动作 → 确定会签方（口径与 order_review 一致）
        engine.select_action(task.task_uid, "publish", actor=actor_name,
                             user_ctx=dict(user_ctx or {}),
                             comment=comment or "报告结论已复核，发起发布审定")
        linked = dict(report.get("linked") or {})
        tasks = list(linked.get("tasks") or [])
        tasks.append({"task_uid": task.task_uid, "wf_id": task.wf_id,
                      "title": task.title, "slot": REPORT_SLOT,
                      "status": task.status})
        linked["tasks"] = tasks
        updated = store.transition_status(
            report_id, "pending_review", actor=actor_name,
            note=comment or "发起审定（已生成会签工单）",
            extra={"linked": linked, "governing_slot": REPORT_SLOT})
        return {"report": updated, "task": task.to_dict()}

    task_uid = ""
    for item in (report.get("linked") or {}).get("tasks") or []:
        if str(item.get("slot") or "") == REPORT_SLOT:
            task_uid = str(item.get("task_uid") or "")
    if not task_uid:
        raise store.InvalidReportTransition("该报告尚无审定工单，请先发起审定（submit）")

    if verb == "approve":
        _require_perm(user_ctx, _SLOT_PERM["approve"], "审定通过该报告")
        task = engine.approve(task_uid, actor_name, comment, final=True,
                              decision="审定通过并发布",
                              user_ctx=dict(user_ctx or {}))
        updated = store.transition_status(
            report_id, "approved", actor=actor_name,
            note=comment or "审定通过（会签齐备）")
        return {"report": updated, "task": task.to_dict()}

    if verb == "reject":
        _require_perm(user_ctx, _SLOT_PERM["propose"], "驳回该报告")
        if not str(comment or "").strip():
            raise ValueError("驳回必须填写理由（决策意见不能为空）")
        task = engine.reject(task_uid, actor_name, comment,
                             user_ctx=dict(user_ctx or {}))
        updated = store.transition_status(
            report_id, "rejected", actor=actor_name, note=comment)
        return {"report": updated, "task": task.to_dict()}

    raise ValueError(f"未知审定动作: {action!r}（可选 submit/approve/reject）")


def dispatch_report(report_id: str, comment: str,
                    user_ctx: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """派发/对外发布：需 ``approve`` 权限 + **必填意见** + **有溯源**（无溯源不输出）。"""
    _require_perm(user_ctx, _SLOT_PERM["approve"], "派发该报告")
    if not str(comment or "").strip():
        raise ValueError("派发/对外发布必须填写意见（说明派发对象与口径）")
    report = store.get_report(report_id)
    if not report.get("provenance"):
        raise ValueError("该报告缺少溯源信息（无溯源不输出），请先补充数据来源再派发")
    if report["status"] != "approved":
        raise store.InvalidReportTransition(
            f"报告当前状态 {report['status']} 不能派发（须先审定通过）")
    actor_name = str((user_ctx or {}).get("name") or "演示用户")
    return store.transition_status(report_id, "dispatched", actor=actor_name,
                                   note=comment)


# ---------- 导出 ----------


def export_report(report_id: str, fmt: str = "md",
                  user_ctx: Optional[Mapping[str, Any]] = None
                  ) -> tuple[str, str, str]:
    """导出为 ``(正文, media_type, 建议文件名)``；支持 ``md`` / ``html``。

    **PDF 不在后端生成**——对客报告本就要求"所见即所得"的可审计版式，
    由浏览器打印对话框输出 PDF 更诚实（不引入 PDF 依赖，也不会排版走样）。
    """
    report = get_payload(report_id, user_ctx)
    kind = str(fmt or "md").strip().lower()
    safe_title = re.sub(r"[/\\:*?\"<>|]", "_", str(report.get("title") or "report"))[:60]

    if kind in ("html", "htm"):
        return _render_html(report), "text/html; charset=utf-8", f"{safe_title}.html"

    lines: list[str] = [f"# {report.get('title') or '未命名报告'}", ""]
    lines.append(f"- 报告号：{report.get('report_id')}")
    lines.append(f"- 场景：{report.get('scenario_key') or '—'}")
    lines.append(f"- 来源：{report.get('source_module') or '—'}")
    lines.append(f"- 状态：{report.get('status')}　风险评级：{report.get('risk_level') or '—'}")
    lines.append(f"- 可见范围：{'对客（含脱敏产物）' if report.get('visibility') == 'customer' else '内部'}")
    lines.append("")
    lines.append("## 内部简报")
    lines.extend(f"- **{k}**：{v}" for k, v in (report.get("internal_brief") or {}).items())
    if report.get("customer_report"):
        lines.extend(["", "## 对客报告（脱敏）", str(report["customer_report"])])
    gaps = report.get("data_gaps") or []
    if gaps:
        lines.extend(["", "## 数据缺口", *[f"- {g}" for g in gaps]])
    lines.extend(["", "## 数据溯源"])
    for item in report.get("provenance") or []:
        if isinstance(item, Mapping):
            lines.append(f"- [{item.get('domain', '—')}] {item.get('note') or item.get('ref') or item}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 版本留痕"])
    for v in report.get("versions") or []:
        lines.append(f"- {v.get('ver')} · {v.get('time')} · {v.get('actor')}：{v.get('note')}")
    return "\n".join(lines), "text/markdown; charset=utf-8", f"{safe_title}.md"


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _render_html(report: Mapping[str, Any]) -> str:
    """自包含可打印 HTML（浏览器打印即可另存 PDF，无需后端 PDF 依赖）。"""
    brief = "".join(
        f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
        for k, v in (report.get("internal_brief") or {}).items())
    prov = "".join(
        f"<li>[{_esc(p.get('domain', '—'))}] {_esc(p.get('note') or p.get('ref') or p)}</li>"
        if isinstance(p, Mapping) else f"<li>{_esc(p)}</li>"
        for p in (report.get("provenance") or [])) or "<li>—</li>"
    versions = "".join(
        f"<li>{_esc(v.get('ver'))} · {_esc(v.get('time'))} · {_esc(v.get('actor'))}：{_esc(v.get('note'))}</li>"
        for v in (report.get("versions") or []))
    gaps = "".join(f"<li>{_esc(g)}</li>" for g in (report.get("data_gaps") or []))
    customer = (f"<h2>对客报告（脱敏）</h2><p>{_esc(report.get('customer_report'))}</p>"
                if report.get("customer_report") else "")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{_esc(report.get('title'))}</title>
<style>
  body {{ font: 14px/1.7 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         max-width: 820px; margin: 32px auto; padding: 0 20px; color: #17202E; }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #1B66CC; padding-bottom: 10px; }}
  h2 {{ font-size: 16px; margin-top: 26px; color: #1B66CC; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #D8DEE9; padding: 7px 10px; text-align: left;
           vertical-align: top; font-size: 13px; }}
  th {{ background: #F2F6FC; width: 168px; font-weight: 600; }}
  .meta {{ color: #64748B; font-size: 12.5px; }}
  ul {{ padding-left: 20px; }}
  @media print {{ body {{ margin: 0; }} }}
</style></head><body>
<h1>{_esc(report.get('title'))}</h1>
<p class="meta">报告号 {_esc(report.get('report_id'))} · 场景 {_esc(report.get('scenario_key') or '—')}
 · 来源 {_esc(report.get('source_module') or '—')} · 状态 {_esc(report.get('status'))}
 · 风险评级 {_esc(report.get('risk_level') or '—')}</p>
<h2>内部简报</h2><table>{brief or '<tr><td>—</td></tr>'}</table>
{customer}
{('<h2>数据缺口</h2><ul>' + gaps + '</ul>') if gaps else ''}
<h2>数据溯源</h2><ul>{prov}</ul>
<h2>版本留痕</h2><ul>{versions or '<li>—</li>'}</ul>
</body></html>"""
