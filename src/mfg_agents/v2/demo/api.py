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

from ...common.config import settings
from ...common.rag_tools import kb_count
from ..contracts.im_gateway import ReplyCard
from ..contracts.adapters.wecom import WeComAdapter
from ..core import cache
from ..core.events import EventHub, QueueSink, bind_existing, hub, unbind
from ..core.orchestrator import V2Orchestrator, create_default_orchestrator
from ..services import kbqa as kbqa_service
from ..services.auth import AuthError, get_codec, resolve_login_roles
from ..services.workflow import (
    PermissionDenied,
    SignoffIncomplete,
    WorkflowEngine,
    WorkflowTransitionError,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="mfg-agent-platform v2", version="2.0.0")

# ---------- 动态 index：静态资源版本号 = 文件 mtime（文件一改缓存自动失效） ----------
import os as _os
import re as _re
from fastapi.responses import HTMLResponse as _HTMLResponse
from pathlib import Path as _Path

_UI_DIR = _Path(__file__).resolve().parent / "ui"


@app.get("/demo-ui/", include_in_schema=False)
@app.get("/demo-ui/index.html", include_in_schema=False)
def demo_ui_index() -> _HTMLResponse:
    """index.html 动态渲染：JS/CSS 引用注入 ?v=<mtime>，根治浏览器缓存。

    版本号字符类放宽为 ``[0-9A-Za-z_.\\-]+``（兼容数字 / split1 / hitlfix1 /
    带点划线等任意常见格式），且资源引用即使原本没有 ``?v=`` 也会被追加，
    保证清单内资源必定带上 ``?v=<mtime>``——文件一改，缓存自动失效。
    """
    html = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    for f in ("demo-data.js", "terms.js", "app.js", "screens.js", "help-screen.js", "kb-screen.js", "office-screen.js", "app.css"):
        fp = _UI_DIR / f
        v = int(fp.stat().st_mtime) if fp.exists() else 0
        # 仅替换引号内的资源引用（允许 ./、子目录等路径前缀），不动 HTML 其余部分；
        # 无 ?v= 时追加，有则无论原格式（split1/数字/带点划线）一律按 mtime 覆写。
        html = _re.sub(
            rf"([\"'])([.\w/-]*{_re.escape(f)})(?:\?v=[0-9A-Za-z_.\-]+)?\1",
            rf"\g<1>\g<2>?v={v}\g<1>",
            html,
        )
    return _HTMLResponse(html)



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

# 公网访问（Cloudflare / cpolar 隧道）下首个请求要跨公网往返多次，
# 未压缩的 demo-ui 静态资源合计约 284 KB，实测串行加载 ~12s。
# GZip 中间件把文本资源压到 ~1/5，并加 ETag 让重复访问走 304。
# min_size=800：小于此值压缩收益不抵 CPU 开销。
try:
    from fastapi.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=800)
except Exception:  # pragma: no cover - 旧版 Starlette 无 GZip 时降级
    logger.warning("[perf] GZipMiddleware 不可用，静态资源未压缩")

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


class OfficeCancelRequest(BaseModel):
    """取消本人会议室预订 / 订餐订单（演示端点；归属校验仍在办公后端）。"""

    kind: str                      # booking | order
    id: str                        # booking_id / order_id
    actor: str = ""                # 操作人（缺省用演示身份）


@app.get("/api/v2/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "2.0.0"}


# ---------- 企业咨询助手（独立轻量 RAG：Milvus enterprise_faq + 统一 LLM） ----------


class KbQueryRequest(BaseModel):
    """企业制度问答请求。

    - ``query``：员工自然语言问题；
    - ``top_k``：检索制度片段数（默认 3）。
    """

    query: str
    top_k: int = 3


@app.get("/api/v2/kbqa/meta")
def kbqa_meta() -> dict[str, Any]:
    """企业咨询助手元数据：支持类别面板 + 知识库块数（前端侧栏展示）。"""
    return {"code": 0,
            "data": {"depts": kbqa_service.KBQA_DEPTS, "count": kb_count()},
            "message": "ok"}


@app.post("/api/v2/kbqa")
def kbqa_query(req: KbQueryRequest) -> dict[str, Any]:
    """企业制度问答：检索制度片段 → LLM 生成带出处回答。

    独立通道，不经 TwoLevelRouter / 制造场景 DAG；LLM 不可用时降级回放片段。
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")
    data = kbqa_service.answer(req.query, top_k=max(1, min(int(req.top_k), 8)))
    return {"code": 0, "data": data, "message": "ok"}


# 演示默认用户上下文：**仅用于只读/问数路径**（POST /api/v2/query 未显式传
# user_ctx 时兜底，只会"发起"待确认工单，不会放行任何审批）。
# HITL 写接口（select-action/cosign/approve/reject）不接受此兜底，必须带访问令牌。
DEFAULT_USER_CTX = {
    "user_id": "api-demo",
    "dept": "manufacturing",
    "roles": ["manufacturing", "coordinator"],
}


@app.post("/api/v2/query")
def api_query(req: QueryRequest, request: Request) -> dict[str, Any]:
    """统一查询入口：路由 → 场景 DAG → 聚合结果（事件同步进请求队列）。

    可选 Redis 缓存层（``v2/core/cache.py``，未配置/异常时全部降级）：

    - **限流**：单 IP 每分钟超 ``RATE_LIMIT_PER_MIN`` 次返回 429；
    - **结果缓存**：同一问题 + 同一角色的结果直接回放（TTL ``CACHE_TTL_S``），
      省去 DAG 重跑；命中时不再开事件队列（无进度事件，前端立即拿到结果）；
    - **会话上下文**：记住该用户最近一次查询，供后续追问指代。
    """
    user_ctx = req.user_ctx or DEFAULT_USER_CTX
    client_ip = request.client.host if request.client else "unknown"
    if cache.rate_limit_exceeded(f"ip:{client_ip}", settings.RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    request_id = req.request_id or f"req-{uuid.uuid4().hex[:12]}"
    ckey = cache.query_key(req.query, user_ctx)

    cached = cache.cache_get(ckey)
    if cached is not None:
        data = dict(cached)
        data["request_id"] = request_id
        data["cached"] = True
        return {"code": 0, "data": data, "message": "ok"}

    event_queue = hub.open(request_id)
    token = bind_existing(event_queue)
    try:
        result = get_orchestrator().handle(req.query, user_ctx,
                                           sink=QueueSink(event_queue))
    finally:
        unbind(token)
    data = result.to_dict()
    # 先缓存不含 request_id 的快照（json 序列化即快照），再回填本次 request_id
    cache.cache_set(ckey, data, ttl=settings.CACHE_TTL_S)
    cache.remember_session(str(user_ctx.get("user_id") or ""), {
        "request_id": request_id,
        "query": req.query,
        "scenario_key": data.get("scenario_key"),
        "task_uids": [h.get("task_uid") for h in (data.get("hitl") or [])
                      if h.get("task_uid")],
    })
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


# ---------- 认证与岗位令牌门禁（F2） ----------


class LoginRequest(BaseModel):
    """岗位登录请求（演示环境免密码：选择岗位身份即登录）。

    - ``roles``：岗位对应的平台域角色写法（前端 ROLES.roles，如 ``["quality"]``），
      **优先**作为权威来源；``role`` 仅在缺 ``roles`` 时使用（前端 ROLES.id）；
    - ``user_id`` / ``name`` / ``dept``：登录者标识与展示信息（写入令牌载荷）。
      两者缺省时由归一化后的角色派生 ``u-<role_key>``，保证 actor 有稳定标识。
    """

    roles: Optional[list[str]] = None
    role: str = ""
    user_id: str = ""
    name: str = ""
    dept: str = ""


def _ctx_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """令牌载荷 → 统一用户上下文（写入引擎的 ``user_ctx`` 形状）。"""
    return {
        "user_id": str(payload.get("sub") or ""),
        "name": str(payload.get("name") or ""),
        "dept": str(payload.get("dept") or ""),
        "roles": [str(r) for r in (payload.get("roles") or [])],
    }


def _bearer_token(request: Request) -> str:
    """从 ``Authorization: Bearer <token>`` 取出令牌（缺失返回空串）。"""
    raw = request.headers.get("authorization") or ""
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _require_identity(request: Request) -> dict[str, Any]:
    """解出**权威**用户上下文；令牌缺失/非法/过期 → 401（fail-closed）。

    HITL 写接口一律以此为准：**不再接受请求体里的 ``user_ctx``**——角色与 actor
    标识都由服务端签发的令牌裁决，消除「链接即权限」与「每次写请求自由声明身份」。
    """
    try:
        payload = get_codec().verify(_bearer_token(request))
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    ctx = _ctx_from_payload(payload)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="访问令牌缺少用户标识")
    return ctx


@app.post("/api/v2/auth/login")
def api_auth_login(req: LoginRequest) -> dict[str, Any]:
    """岗位登录：校验岗位 → 签发访问令牌（后续写接口凭令牌鉴权）。

    服务端**只校验角色合法性**（必须落在 32 角色权限目录内），不校验口令——这是
    「免密演示」的诚实边界；生产须替换为 SSO。全部未识别 → 400，绝不下发匿名令牌。
    """
    candidates: list[str] = list(req.roles or [])
    if not candidates and req.role:
        candidates = [req.role]
    role_keys = resolve_login_roles(candidates)
    if not role_keys:
        raise HTTPException(
            status_code=400,
            detail="岗位未识别：可选岗位见 /api/v2/governance/permissions 的角色目录")
    user_id = req.user_id.strip() or f"u-{role_keys[0]}"
    token, payload = get_codec().issue(
        user_id=user_id, name=req.name, dept=req.dept, roles=role_keys)
    return {"code": 0,
            "data": {"token": token, "expires_in": get_codec().ttl_s,
                     "user_ctx": _ctx_from_payload(payload)},
            "message": "ok"}


@app.get("/api/v2/auth/me")
def api_auth_me(request: Request) -> dict[str, Any]:
    """当前登录态（令牌校验入口；前端启动时用它确认会话是否仍有效）。"""
    ctx = _require_identity(request)
    return {"code": 0, "data": {"user_ctx": ctx}, "message": "ok"}


# ---------- HITL 审批中心（WorkflowEngine 待办 / 审批回写 / 角色校验 / 多人会签） ----------


class WorkflowActionRequest(BaseModel):
    """HITL 审批动作请求。

    - ``approver``：审批人姓名（写入工单留痕）；
    - ``action_key``：选定/处置动作 key（select-action 必填）；
    - ``decision``：所选处置动作中文名（写入审批留痕）。

    **角色不再来自请求体**（F2）：``role`` / ``user_ctx`` 仅为兼容旧前端留的字段，
    服务端一律忽略，权威身份改由 ``Authorization: Bearer`` 令牌裁决
    （见 ``_require_identity``）——请求体里声明的角色不再具备任何效力。
    """

    approver: str = "演示用户"
    comment: str = ""
    final: bool = False
    decision: str = ""       # 所选处置动作（如"特采（让步接收）"），写入审批留痕
    role: str = ""           # 兼容字段：已失效（仅留旧前端不改也能收到请求）
    user_ctx: Optional[dict[str, Any]] = None  # 兼容字段：已失效，服务端忽略
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
def api_workflow_select_action(task_id: str, req: WorkflowActionRequest,
                               request: Request) -> dict[str, Any]:
    """选定处置动作：按该动作 propose/cosign 生成会签要求（不改变工单状态）。

    写接口需访问令牌（F2）：操作者角色与 actor 标识由令牌裁决，无令牌 → 401。
    """
    user_ctx = _require_identity(request)
    try:
        task = _workflow_engine().select_action(
            task_id, req.action_key, req.approver,
            user_ctx=user_ctx, comment=req.comment)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/cosign")
def api_workflow_cosign(task_id: str, req: WorkflowActionRequest,
                        request: Request) -> dict[str, Any]:
    """多人会签（多角色背书，缺一不可；不改变工单状态）。

    写接口需访问令牌（F2）：会签角色与一人一签的 actor 标识均由令牌裁决。
    """
    user_ctx = _require_identity(request)
    try:
        task = _workflow_engine().cosign(
            task_id, req.approver, user_ctx=user_ctx,
            decision=req.decision, comment=req.comment)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/approve")
def api_workflow_approve(task_id: str, req: WorkflowActionRequest,
                         request: Request) -> dict[str, Any]:
    """HITL 审批通过：waiting_hitl → running（final=True 时 → done）。

    受治理工单强制校验岗位权限与会签齐备（403 / 409）；写接口需访问令牌（401）。
    """
    user_ctx = _require_identity(request)
    try:
        task = _workflow_engine().approve(
            task_id, req.approver, req.comment, final=req.final,
            decision=req.decision, user_ctx=user_ctx)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.post("/api/v2/workflow/{task_id}/reject")
def api_workflow_reject(task_id: str, req: WorkflowActionRequest,
                        request: Request) -> dict[str, Any]:
    """HITL 审批驳回：waiting_hitl → closed（回写审批人与意见）。

    驳回不要求会签齐备（会签未齐也可驳回止损），但仍校验岗位权限；需访问令牌。
    """
    user_ctx = _require_identity(request)
    try:
        task = _workflow_engine().reject(
            task_id, req.approver, req.comment, decision=req.decision,
            user_ctx=user_ctx)
    except (KeyError, PermissionDenied, SignoffIncomplete,
            WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": task.to_dict(), "message": "ok"}


@app.get("/api/v2/agents/configs")
def api_agents_configs() -> dict[str, Any]:
    """域组智能体配置总览（帮助界面的数据来源：意图/边界/工具/HITL 准则）。

    含独立的办公助手智能体（``key=office``）——它是单独成型的 Agentic Agent，
    与五个域组智能体并列展示。
    """
    from ..agents.agentic import DOMAIN_AGENT_CONFIGS, AGENT_TOOLS_META
    from ..agents.office import OFFICE_AGENT_CONFIG, OFFICE_TOOLS_META

    agents = [{"key": k, **cfg} for k, cfg in DOMAIN_AGENT_CONFIGS.items()]
    agents.append({"key": "office", **OFFICE_AGENT_CONFIG})
    return {"code": 0,
            "data": {"agents": agents,
                     "tools": [*AGENT_TOOLS_META, *OFFICE_TOOLS_META]},
            "message": "ok"}


# ============================================================
# 办公助手（独立 Agentic Agent：会议室预订 / 订餐）demo 端点
# ============================================================


@app.post("/api/v2/office/chat")
def api_office_chat(req: QueryRequest) -> dict[str, Any]:
    """办公助手对话入口：直接交给独立的办公助手 Agentic Agent 办理。

    与 ``/api/v2/query`` 的区别：此处**强制定向**办公助手（不依赖关键词路由），
    供前台聚焦演示"帮我订个会议室/中午订个餐"；返回值含工具调用留痕（steps）。
    真实接入时把 ``runtime.office`` 换成飞书/钉钉/美餐适配器即可，本端点零改动。
    """
    from ...common.llm_factory import llm_available
    from ..agents.office import OfficeAssistantAgent

    if not llm_available():
        return {"code": 1, "data": None,
                "message": "办公助手需要 LLM 服务（当前不可用），请稍后重试。"}

    orch = get_orchestrator()
    try:
        out = OfficeAssistantAgent(orchestrator=orch).run(
            req.query, user_ctx=req.user_ctx or DEFAULT_USER_CTX)
    except Exception as exc:  # noqa: BLE001 —— 演示端点：失败如实降级不崩
        return {"code": 1, "data": None,
                "message": f"办公助手暂不可用：{type(exc).__name__}: {exc}"}
    return {"code": 0, "data": out, "message": "ok"}


@app.get("/api/v2/office/rooms")
def api_office_rooms(capacity_min: Optional[int] = Query(None)) -> dict[str, Any]:
    """会议室目录（供前台展示与筛选）。"""
    rooms = get_orchestrator().runtime.office.list_rooms(capacity_min)
    return {"code": 0, "data": {"rooms": rooms, "count": len(rooms)},
            "message": "ok"}


@app.get("/api/v2/office/restaurants")
def api_office_restaurants() -> dict[str, Any]:
    """餐厅目录（员工食堂 / 商务餐厅）。"""
    items = get_orchestrator().runtime.office.list_restaurants()
    return {"code": 0, "data": {"restaurants": items, "count": len(items)},
            "message": "ok"}


@app.get("/api/v2/office/my/{actor}")
def api_office_mine(actor: str) -> dict[str, Any]:
    """按操作人查其会议室预订与订餐订单（demo 演示用；真实环境按登录身份）。"""
    be = get_orchestrator().runtime.office
    bookings = be.list_bookings(actor)
    orders = be.list_orders(actor)
    return {"code": 0, "data": {"bookings": bookings, "orders": orders},
            "message": "ok"}


@app.get("/api/v2/office/available")
def api_office_available(date: str = Query(...), start: str = Query(...),
                         end: str = Query(...),
                         capacity_min: Optional[int] = Query(None)) -> dict[str, Any]:
    """某时段可用会议室（无需 LLM 的确定性查询：demo 走内存预订，真实接入后走飞书忙闲）。"""
    from ..services.office import OfficeError

    try:
        rooms = get_orchestrator().runtime.office.find_available_rooms(
            date, start, end, capacity_min)
    except OfficeError as exc:
        return {"code": 1, "data": None, "message": str(exc)}
    return {"code": 0, "data": {"date": date, "start": start, "end": end,
                                "available_rooms": rooms, "count": len(rooms)},
            "message": "ok"}


@app.get("/api/v2/office/meta")
def api_office_meta() -> dict[str, Any]:
    """办公助手能力元数据（前端横幅用：后端实现 / LLM 可用性 / 高影响阈值）。

    演示态 ``backend`` 为 demo（内存种子，进程重启复位）；真实接入适配层已就绪
    （会议室→飞书日历，见 ``services/feishu_calendar.py``），切换只改环境变量。
    """
    from ...common.llm_factory import llm_available
    from ..services.office import ORDER_APPROVAL_AMOUNT

    office = get_orchestrator().runtime.office
    impl = type(office).__name__
    return {"code": 0, "data": {
        "backend": "demo" if impl == "DemoOfficeBackend" else "real",
        "backend_impl": impl,
        "llm_available": bool(llm_available()),
        "order_approval_amount": ORDER_APPROVAL_AMOUNT,
        "real_adapter": "飞书日历（会议室）· 适配层已就绪",
        "switch_hint": "OFFICE_BACKEND=feishu 即切到真实飞书日历；订餐 P1-B 再接美餐/美团",
    }, "message": "ok"}


@app.get("/api/v2/office/menu/{restaurant_id}")
def api_office_menu(restaurant_id: str) -> dict[str, Any]:
    """某餐厅菜单（含价格与是否需人工确认），供前台展示与快捷下单。"""
    from ..services.office import OfficeError

    try:
        menu = get_orchestrator().runtime.office.get_menu(restaurant_id)
    except OfficeError as exc:
        return {"code": 1, "data": None, "message": str(exc)}
    return {"code": 0, "data": menu, "message": "ok"}


@app.post("/api/v2/office/cancel")
def api_office_cancel(req: OfficeCancelRequest) -> dict[str, Any]:
    """取消本人会议室预订 / 订餐订单。

    治理不放松：归属校验在办公后端（只能取消本人单据），演示端点同样不放行
    他人订单；真实接入后由飞书/美餐侧一并执行，本端点零改动。
    """
    from ..services.office import OfficeError

    be = get_orchestrator().runtime.office
    actor = req.actor or DEFAULT_USER_CTX.get("name") or DEFAULT_USER_CTX["user_id"]
    try:
        if req.kind == "booking":
            out = be.cancel_booking(req.id, actor)
        elif req.kind == "order":
            out = be.cancel_order(req.id, actor)
        else:
            return {"code": 1, "data": None,
                    "message": "kind 必须为 booking（会议室预订）或 order（订餐订单）"}
    except OfficeError as exc:
        return {"code": 1, "data": None, "message": str(exc)}
    return {"code": 0, "data": out, "message": "ok"}


@app.get("/api/v2/office/snapshot")
def api_office_snapshot(roles: Optional[str] = Query(None)) -> dict[str, Any]:
    """企业综合助手·制造数据速查快照（首屏用，无需 LLM）。

    一次返回四块确定性只读数据：材料库存速查、缺料清单、成品库存、近期来料批次。
    全部转发统一注册查询（口径即代码），并按岗位表级权限（TABLE_ACL）过滤：
    无权限的块标记 ``authorized=false`` 并给出提示，越权不返回数据（fail-closed）。
    任一查询失败只降级该块（``ok=false``），端点绝不 500。
    """
    from ..services.text2sql import TABLE_ACL

    role_set = {s.strip() for s in str(roles or "").split(",") if s.strip()}
    if not role_set:
        role_set = {str(r) for r in (DEFAULT_USER_CTX.get("roles") or [])}

    blocks_spec = [
        ("material_stock", "material_stock_all", "material_inventory",
         "材料库存（按料号×仓库；可用量=在库-预留，冻结量不计可用）"),
        ("shortage", "material_shortage", "material_inventory",
         "缺料清单（可用量低于安全库存，含缺口）"),
        ("finished_stock", "finished_stock_all", "finished_inventory",
         "成品库存（型号×仓库；可发量 ATP=在库-预留）"),
        # 用「全量扫描（上限 60）」而非「近期 10 条」：速查卡的检索是在**返回行**上做的，
        # 若只给最近 10 条，按批号/料号搜更早的批次会搜不到，演示不了检索完整性。
        ("incoming_batch", "incoming_batch_scan", "incoming_batch",
         "来料批次（全量，上限 60；IQC 结论：合格/不合格/待检/让步接收）"),
    ]
    reg = get_orchestrator().runtime.data_registry
    blocks: dict[str, Any] = {}
    for key, qname, table, note in blocks_spec:
        if not (role_set & TABLE_ACL.get(table, set())):
            blocks[key] = {"authorized": False, "ok": False, "rows": [], "count": 0,
                           "note": note,
                           "message": f"当前岗位无权限查询（{table}），请走对应职责岗"}
            continue
        try:
            rows = reg.run_query(qname)
            blocks[key] = {"authorized": True, "ok": True, "rows": rows,
                           "count": len(rows), "note": note,
                           "query": qname, "message": "ok"}
        except Exception as exc:  # noqa: BLE001 —— 单块降级不拖垮整页
            blocks[key] = {"authorized": True, "ok": False, "rows": [], "count": 0,
                           "note": note, "query": qname,
                           "message": f"暂不可用：{type(exc).__name__}: {str(exc)[:120]}"}
    degraded = any(not b["ok"] for b in blocks.values())
    return {"code": 0, "data": {
        "blocks": blocks, "roles": sorted(role_set), "degraded": degraded,
        "source": "registered_query（统一口径，只读）",
    }, "message": "ok"}


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


@app.get("/api/v2/dashboard/manufacturing")
def api_dashboard_manufacturing() -> dict[str, Any]:
    """制造看板：稼动率 / 报废率 / 报工完成率 / 测试直通率（全员可见）。

    真实链路聚合本地只读数据源；聚合内部全兜底，数据源故障时返回
    ``degraded:true`` 的骨架，**绝不 500**（前端据此降级演示数据）。
    """
    from ..services.dashboard import build_manufacturing_dashboard

    registry = get_orchestrator().runtime.data_registry
    return {"code": 0, "data": build_manufacturing_dashboard(registry),
            "message": "ok"}


@app.get("/api/v2/dashboard/quality")
def api_dashboard_quality() -> dict[str, Any]:
    """品质看板：不良率 / 直通率 FPY / 标准三率（全员可见）。"""
    from ..services.dashboard import build_quality_dashboard

    registry = get_orchestrator().runtime.data_registry
    return {"code": 0, "data": build_quality_dashboard(registry),
            "message": "ok"}


@app.get("/api/v2/dashboard/orders")
def api_dashboard_orders() -> dict[str, Any]:
    """订单履约看板：OTD / OTIF / 延期订单率 / 齐套率（全员可见）。

    真实链路聚合本地只读数据源（order_fulfillment）；聚合内部全兜底，
    数据源故障时返回 ``degraded:true`` 的骨架，**绝不 500**（前端据此降级演示数据）。
    """
    from ..services.dashboard import build_order_dashboard

    registry = get_orchestrator().runtime.data_registry
    return {"code": 0, "data": build_order_dashboard(registry),
            "message": "ok"}


@app.get("/api/v2/dashboard/spc")
def api_dashboard_spc() -> dict[str, Any]:
    """SPC 过程能力看板：逐特性 Cp/Cpk/Pp/Ppk 判定 + 超差率 + 不良柏拉图（全员可见）。

    能力指数由确定性内核 ``core/spc``（双西格玛口径）计算；缺测量值时逐特性标
    ``data_gap``，聚合内部全兜底，**绝不 500**（前端据此降级演示数据）。
    """
    from ..services.dashboard import build_spc_dashboard

    registry = get_orchestrator().runtime.data_registry
    return {"code": 0, "data": build_spc_dashboard(registry),
            "message": "ok"}


@app.get("/api/v2/dashboard/drill")
def api_dashboard_drill(request: Request, screen: str = Query(...),
                        kpi: str = Query(...), step: str = Query(""),
                        station: str = Query(""),
                        defect_type: str = Query("")) -> dict[str, Any]:
    """看板下钻（只读）：无实体 = L2；带实体 = L3；再带 ``step`` = L4；再带 ``station`` = L5。

    - ``screen``：``manufacturing`` / ``quality`` / ``orders`` / ``spc``；``kpi``：DRILL_DEFS 白名单键；
    - 实体参数按指标维度通用取用（``entity_cols`` / ``entity`` kind 白名单），
      例如 ``line``+``model`` / ``wo`` / ``model`` / ``customer``+``level`` /
      ``supplier`` / ``test_type`` / ``warehouse`` / ``wf`` 等；
    - 层级判定：无 entity→L2、有 entity→L3、``+step``→L4、``+step&station``→L5；
      跳级（有 step 无 entity、有 station 无 step）或指标不支持该层级 → 400；
    - ``defect_type``：仅白名单 ``DEFECT_TYPE_DRILLS``（services/dashboard.py）内的指标
      支持——质量看板不良率走「L1 累计不良柏拉图 → L2 按产线 · 型号分解」，
      且不与实体/工序/工位组合（组合 → 400）；spc_pareto 作为已声明实体维度下钻；
      白名单外一律 400；
    - 聚合内部全兜底，数据源故障时返回 ``degraded:true``，**绝不 500**。
    """
    from ..services.dashboard import (
        DEFECT_TYPE_DRILLS, DRILL_DEFS, build_drill, build_pareto_drill,
        entity_param_names,
    )

    defn = DRILL_DEFS.get(kpi)
    if defn is None or defn["screen"] != screen:
        raise HTTPException(status_code=400, detail="未知看板指标")

    # 实体参数：按指标声明的维度名从 query 通用取用；全部为空表示不锁定实体（L2）
    qp = request.query_params
    vals = {p: (qp.get(p) or "").strip() for p in entity_param_names(kpi)}
    entity: Optional[dict[str, str]] = vals if any(vals.values()) else None

    defect_type = (defect_type or "").strip()
    # defect_type 只对白名单 DEFECT_TYPE_DRILLS 内的 (screen, kpi) 生效（唯一事实源）：
    #   "pareto" → 走 L1 累计不良柏拉图下钻；"entity" → 作为已声明实体维度走下方通用下钻；
    # 白名单外一律 400（脏参不被静默吞掉），不再用散落的 str 判定。
    if defect_type:
        dt_kind = DEFECT_TYPE_DRILLS.get((screen, kpi))
        if dt_kind == "pareto":
            if entity or step or station:
                raise HTTPException(
                    status_code=400,
                    detail="不良类型下钻不支持与实体/工序/工位组合")
            registry = get_orchestrator().runtime.data_registry
            return {"code": 0, "data": build_pareto_drill(registry, screen, kpi,
                                                          defect_type),
                    "message": "ok"}
        if dt_kind != "entity":
            raise HTTPException(status_code=400,
                                detail="该指标不支持不良类型下钻")

    if station and not step:
        raise HTTPException(status_code=400, detail="下钻工位需先指定工序")
    if step and not entity:
        raise HTTPException(status_code=400, detail="下钻工序需先指定实体")
    if step and not defn.get("stage_source"):
        raise HTTPException(status_code=400, detail="该指标不支持工序/工位下钻")
    if station and defn.get("stage_source") != "station_detail":
        raise HTTPException(status_code=400, detail="该指标不支持工位下钻")

    registry = get_orchestrator().runtime.data_registry
    data = build_drill(registry, screen, kpi, entity, step or None,
                       station or None)
    return {"code": 0, "data": data, "message": "ok"}


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
