"""FastAPI V2 入口（独立 ASGI 应用，可挂载到 V1 app 或独立部署）。

端点：
- POST /api/v2/query            统一入口（TwoLevelRouter → YamlDagExecutor）
- GET  /api/v2/events           SSE 事件流（context-var 请求级队列，修 D6 并发串话）
- POST /api/v2/im/wecom/callback 企业微信回调（GET 校验 + POST 解密 → query → 回复卡片）
- GET  /api/v2/health           健康检查

运行：
    uv run uvicorn mfg_agents.v2.demo.api:app --port 8004

D6 修复说明：V1 events.py 是全局单 queue.Queue，并发请求事件互相串话；
V2 每个请求一个队列（contextvars + EventHub 按 request_id 寻址），
POST 执行线程绑定自己的队列，GET /api/v2/events?request_id=xxx 订阅同一队列。
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ...config import settings
from ..contracts.im_gateway import ReplyCard
from ..contracts.adapters.wecom import WeComAdapter
from ..core.events import EventHub, QueueSink, bind_existing, hub, unbind
from ..core.orchestrator import V2Orchestrator, create_default_orchestrator
from ..services.workflow import (
    PermissionDenied,
    SignoffIncomplete,
    WorkflowEngine,
    WorkflowTransitionError,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="mfg-agent-platform v2", version="2.0.0")

# A2A 协议端点（Agent Card + message/send + tasks/get；工具与 MCP 同源）
from ..a2a import build_a2a_router  # noqa: E402
app.include_router(build_a2a_router())

# 演示前端（demo/ui）可能以 file:// 直开（Origin: null）或跨端口访问，
# 需放开 CORS；仅影响本 demo 应用，V1 与 V2 现有路由行为不变。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: Optional[V2Orchestrator] = None


def get_orchestrator() -> V2Orchestrator:
    """编排器懒加载单例（首次请求时装配默认运行时）。"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = create_default_orchestrator()
    return _orchestrator


class QueryRequest(BaseModel):
    query: str
    user_ctx: Optional[dict[str, Any]] = None
    request_id: Optional[str] = None


@app.get("/api/v2/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "2.0.0"}


# 演示默认用户上下文（未显式传入时使用；生产应由认证体系注入）
DEFAULT_USER_CTX = {
    "user_id": "api-demo",
    "dept": "manufacturing",
    "roles": ["manufacturing", "coordinator"],
}


@app.post("/api/v2/query")
def api_query(req: QueryRequest) -> dict[str, Any]:
    """统一查询入口：路由 → 场景 DAG → 聚合结果（事件同步进请求队列）。"""
    request_id = req.request_id or f"req-{uuid.uuid4().hex[:12]}"
    event_queue = hub.open(request_id)
    token = bind_existing(event_queue)
    try:
        result = get_orchestrator().handle(req.query,
                                           req.user_ctx or DEFAULT_USER_CTX,
                                           sink=QueueSink(event_queue))
    finally:
        unbind(token)
    data = result.to_dict()
    data["request_id"] = request_id
    return {"code": 0, "data": data, "message": "ok"}


@app.get("/api/v2/events")
async def api_events(request_id: str = Query(...),
                     wait_s: float = Query(120.0, le=300.0)) -> StreamingResponse:
    """SSE 事件流：订阅指定请求的事件队列（请求级隔离，无并发串话）。"""
    event_queue = hub.get(request_id)
    if event_queue is None:
        raise HTTPException(status_code=404,
                            detail=f"请求不存在或已结束: {request_id}")

    async def gen():
        try:
            deadline = asyncio.get_event_loop().time() + wait_s
            while asyncio.get_event_loop().time() < deadline:
                try:
                    event = event_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("final", "error"):
                    break
        finally:
            # 客户端断开 / 流结束都必须回收队列，避免 EventHub 泄漏
            hub.close(request_id)

    return StreamingResponse(gen(), media_type="text/event-stream")


class EventsOpenRequest(BaseModel):
    request_id: str


@app.post("/api/v2/events/open")
def api_events_open(req: EventsOpenRequest) -> dict[str, Any]:
    """预开请求事件队列：前端先订阅 SSE 再发查询，事件流实时可达。

    与 POST /api/v2/query 内的 hub.open(request_id) 幂等（同 id 返回同一队列），
    不改变现有查询/事件行为，仅消除"先查询后订阅"的时序竞态。
    """
    hub.open(req.request_id)
    return {"code": 0, "data": {"request_id": req.request_id},
            "message": "ok"}


# ---------- HITL 审批中心（WorkflowEngine 待办 / 审批回写 / 角色校验 / 多人会签） ----------


class WorkflowActionRequest(BaseModel):
    """HITL 审批动作请求。

    - ``approver``：审批人姓名（写入工单留痕）；
    - ``role``：当前岗位 key（**仅用于展示/兜底**，前端可伪造）；
    - ``user_ctx``：用户上下文（``{"dept": ..., "roles": [...]}``），
      其中 ``roles`` 是**权威**角色来源（后端以此为准做权限校验）；
    - ``action_key``：选定/处置动作 key（select-action 必填）；
    - ``decision``：所选处置动作中文名（写入审批留痕）。
    """

    approver: str = "演示用户"
    comment: str = ""
    final: bool = False
    decision: str = ""       # 所选处置动作（如"特采（让步接收）"），写入审批留痕
    role: str = ""           # 当前岗位 key（仅展示；真实校验以 user_ctx.roles 为准）
    user_ctx: Optional[dict[str, Any]] = None
    action_key: str = ""     # 选定处置动作（select-action/cosign 使用）


def _workflow_engine() -> WorkflowEngine:
    """工作流引擎（总控运行时单例；工单与 API 共享同一 SQLite 库）。"""
    return get_orchestrator().runtime.workflow


def _raise_workflow_http(exc: Exception) -> None:
    """治理异常 → HTTP 状态码统一映射（403/404/409/400）。

    - ``PermissionDenied``      → 403（中文明确"需：质量总监/QA经理"）
    - ``SignoffIncomplete``     → 409（结构化 `{message, missing, progress}`）
    - ``WorkflowTransitionError`` → 409
    - ``KeyError``              → 404
    - ``ValueError``            → 400
    """
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionDenied):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, SignoffIncomplete):
        raise HTTPException(status_code=409, detail={
            "message": str(exc),
            "missing": exc.missing,
            "progress": exc.progress,
        }) from exc
    if isinstance(exc, WorkflowTransitionError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@app.get("/api/v2/workflow/pending")
def api_workflow_pending(role: Optional[list[str]] = Query(None)) -> dict[str, Any]:
    """HITL 待办队列（waiting_hitl 工单；审批中心/待办卡片数据源）。

    Query:
        role: 当前岗位（可重复 ``?role=sqe&role=qa`` 或逗号分隔 ``?role=sqe,qa``）。
            传了则按"审批即待办"过滤——只返回该角色**当前阶段可动作**的工单；
            不传返回全量（向后兼容既有调用方）。

    每项含 ``governance``（治理链快照）、``signoff_status``（会签进度）与
    ``my_action``（``select_action`` / ``cosign`` / ``approve`` / ``null``）。
    """
    engine = _workflow_engine()
    raw_roles: list[str] = []
    for item in (role or []):
        raw_roles.extend([seg.strip() for seg in str(item).split(",") if seg.strip()])
    role_keys = list(engine.permission_registry.normalize_roles(raw_roles))
    tasks = engine.pending_hitl(raw_roles or None)
    data: list[dict[str, Any]] = []
    for task in tasks:
        item = task.to_dict()
        item["my_action"] = engine.action_for(task, raw_roles) if raw_roles else None
        data.append(item)
    return {"code": 0, "data": data, "message": "ok",
            "scope": {"roles": role_keys, "filtered": bool(raw_roles),
                      "unknown": [r for r in raw_roles
                                  if engine.permission_registry.normalize_role(r) is None]}}


@app.get("/api/v2/workflow/{task_id}")
def api_workflow_task(task_id: str) -> dict[str, Any]:
    """单工单详情（会签后局部刷新卡片用）。"""
    try:
        task = _workflow_engine().get(task_id)
    except (KeyError, WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover - _raise_workflow_http 必定抛出
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/select-action")
def api_workflow_select_action(task_id: str,
                               req: WorkflowActionRequest) -> dict[str, Any]:
    """选定处置动作：按该动作 propose/cosign 生成会签要求（不改变工单状态）。"""
    try:
        task = _workflow_engine().select_action(
            task_id, req.action_key, req.approver,
            role=req.role, user_ctx=req.user_ctx, comment=req.comment)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/cosign")
def api_workflow_cosign(task_id: str,
                        req: WorkflowActionRequest) -> dict[str, Any]:
    """多人会签（多角色背书，缺一不可；不改变工单状态）。"""
    try:
        task = _workflow_engine().cosign(
            task_id, req.approver, role=req.role, user_ctx=req.user_ctx,
            decision=req.decision, comment=req.comment)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/approve")
def api_workflow_approve(task_id: str,
                         req: WorkflowActionRequest) -> dict[str, Any]:
    """HITL 审批通过：waiting_hitl → running（final=True 时 → done）。

    受治理工单强制校验岗位权限与会签齐备（403 / 409）。
    """
    try:
        task = _workflow_engine().approve(
            task_id, req.approver, req.comment, final=req.final,
            decision=req.decision, role=req.role, user_ctx=req.user_ctx)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/reject")
def api_workflow_reject(task_id: str,
                        req: WorkflowActionRequest) -> dict[str, Any]:
    """HITL 审批驳回：waiting_hitl → closed（回写审批人与意见）。

    驳回不要求会签齐备（会签未齐也可驳回止损），但仍校验岗位权限。
    """
    try:
        task = _workflow_engine().reject(
            task_id, req.approver, req.comment, decision=req.decision,
            role=req.role, user_ctx=req.user_ctx)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.get("/api/v2/agents/configs")
def api_agents_configs() -> dict[str, Any]:
    """域组智能体配置总览（帮助界面的数据来源：意图/边界/工具/HITL 准则）。"""
    from ..agents.agentic import DOMAIN_AGENT_CONFIGS, AGENT_TOOLS_META

    return {"code": 0,
            "data": {"agents": [
                {"key": k, **cfg} for k, cfg in DOMAIN_AGENT_CONFIGS.items()
            ], "tools": AGENT_TOOLS_META},
            "message": "ok"}


@app.get("/api/v2/governance/permissions")
def api_governance_permissions() -> dict[str, Any]:
    """治理权限目录快照（权限实体 / 角色×权限矩阵 / 7 个 HITL 槽位）。"""
    from ..contracts.permissions import get_permission_registry

    return {"code": 0, "data": get_permission_registry().describe(),
            "message": "ok"}


@app.get("/api/v2/datasources")
def api_datasources() -> dict[str, Any]:
    """数据源接入就绪度总览（V3.1 数据注入接口）。

    列出全部已注册数据源的：业务系统名 / 归属域 / 表清单（含字段）/
    注册查询数 / 健康状态。新数据源按 ``DataSource`` 契约实现并
    ``register()`` 后自动出现在这里——接入即见，无需改前端。
    """
    return {"code": 0,
            "data": get_orchestrator().runtime.data_registry.describe_all(),
            "message": "ok"}


# ---------- 企业微信回调 ----------


def _wecom_adapter() -> WeComAdapter:
    if not (settings.IM_TOKEN and settings.IM_AES_KEY and settings.IM_APPID):
        raise HTTPException(
            status_code=500,
            detail="未配置 IM_TOKEN / IM_AES_KEY / IM_APPID 环境变量")
    return WeComAdapter(settings.IM_TOKEN, settings.IM_AES_KEY, settings.IM_APPID)


@app.get("/api/v2/im/wecom/callback")
def wecom_verify(msg_signature: str, timestamp: str, nonce: str,
                 echostr: str) -> PlainTextResponse:
    """GET URL 校验：验签 + 解密 echostr → 回显明文。"""
    adapter = _wecom_adapter()
    try:
        plain = adapter.verify_url({"msg_signature": msg_signature,
                                    "timestamp": timestamp, "nonce": nonce,
                                    "echostr": echostr})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=403, detail=f"URL 校验失败: {exc}") from exc
    return PlainTextResponse(plain)


@app.post("/api/v2/im/wecom/callback")
async def wecom_callback(request: Request) -> PlainTextResponse:
    """POST 消息回调：解密 → 统一编排 → 被动回复卡片（5s 窗口内）。

    生产注意：重链路应回"已受理"再走 push 异步推送；demo 为展示完整链路同步执行。
    """
    body_text = (await request.body()).decode("utf-8")
    params = dict(request.query_params)

    def run_in_thread() -> str:
        adapter = _wecom_adapter()
        payload = {**params, **adapter.parse_callback_xml(body_text)}
        if not adapter.verify_callback(payload):
            raise HTTPException(status_code=403, detail="回调验签失败")
        msg = adapter.receive(payload)
        request_id = f"im-{uuid.uuid4().hex[:12]}"
        event_queue = hub.open(request_id)
        token = bind_existing(event_queue)
        try:
            result = get_orchestrator().handle(
                msg.text, {"user_id": msg.user_id, "dept": "manufacturing",
                           "roles": ["manufacturing", "coordinator"]},
                sink=QueueSink(event_queue))
        finally:
            unbind(token)
        return adapter.reply(msg, _build_card(result, msg))

    return PlainTextResponse(await asyncio.to_thread(run_in_thread),
                             media_type="application/xml")


def _build_card(result: Any, msg: Any) -> ReplyCard:
    """编排结果 → IM 回复卡片（HITL 动作作为卡片按钮）。"""
    actions: list[dict[str, str]] = []
    for item in result.hitl:
        actions.extend(item.get("actions", []))
    lines = [result.report or "（暂无结论）"]
    if result.hitl:
        lines.append("")
        lines.append("待人工确认：")
        for item in result.hitl:
            lines.append(f"- {item.get('prompt')}"
                         f"（{'、'.join(item.get('approver_roles', []))}）")
    if result.degraded:
        lines.append("")
        lines.append("⚠ degraded 模式：LLM 不可用，结论为规则/模板化输出，请人工复核。")
    return ReplyCard(title="停线应急协同 · 分析简报", body="\n".join(lines),
                     actions=actions, level="P1")


# ---------- 静态前端挂载（/demo-ui → demo/ui 目录） ----------

_UI_DIR = Path(__file__).resolve().parent / "ui"

if _UI_DIR.is_dir():
    # html=True：/demo-ui 与 /demo-ui/ 均返回 index.html（单页应用）。
    # 直接双击 index.html（file://）亦可打开：前端自动探测后端，
    # 不可达时进入演示模式（页面完整渲染 + 明确降级提示）。
    app.mount("/demo-ui", StaticFiles(directory=str(_UI_DIR), html=True),
              name="demo-ui")


__all__ = ["app", "get_orchestrator", "EventHub"]
