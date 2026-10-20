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
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ...common.config import settings
from ...common.rag_tools import kb_count, search_kb
from ..contracts.im_gateway import ReplyCard
from ..contracts.adapters.wecom import WeComAdapter
from ..contracts.permissions import get_permission_registry
from ..core import cache
from ..core.events import EventHub, QueueSink, bind_existing, hub, unbind
from ..core.orchestrator import V2Orchestrator, create_default_orchestrator
from ..services import kbqa as kbqa_service
from ..services import kbqa_store
from ..services import workflow_scope
from ..services import report_service, report_store
from .config_demo import DEMO_LABEL, DEMO_MODE, assert_isolated
from .loader import (
    OBJECTS,
    DemoDataError,
    demo_banner,
    get_object,
    get_replay,
    load_all,
    story_timeline,
)
from ..services.auth import AuthError, get_codec, resolve_login_roles
from ..services.data_scope import get_data_scope
from ..services.workflow import (
    PermissionDenied,
    SignoffIncomplete,
    WorkflowEngine,
    WorkflowTransitionError,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """启动钩子：后台预热 KB 检索（消首问冷启动），不阻塞服务就绪。"""
    assert_isolated()  # 演示实例隔离自检：配置含外部连接特征即拒绝启动
    threading.Thread(target=_preheat_kb, name="kbqa-preheat", daemon=True).start()
    yield


def _preheat_kb() -> None:
    """预热 BGE-M3 模型 + Milvus 连接（DESIGN-2 同迭代项）。

    实测冷启动首问 ``search_kb`` 10659ms——391MB 嵌入模型现加载全耗在
    第一跳上，演示第一问体验死刑。预热放 daemon 线程：服务秒起，模型在
    后台加载；失败只告警不抛——预热是体验优化，不是依赖（Milvus 挂了
    首问冷启动 + 诚实降级，行为与没有预热时一致）。
    """
    t0 = time.time()
    try:
        search_kb("预热", n_results=1)
        logger.info("[kbqa] 预热完成：search_kb 就绪（%.1fs）", time.time() - t0)
    except Exception as exc:  # noqa: BLE001 - 预热失败必须可绕过
        logger.warning("[kbqa] 预热失败（首问将冷启动）: %s", exc)


app = FastAPI(title="mfg-agent-platform v2", version="2.0.0", lifespan=_lifespan)

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
    for f in ("demo-data.js", "terms.js", "app.js", "screens.js", "reports-screen.js", "help-screen.js", "kb-screen.js", "office-screen.js", "case-screen.js", "float-help.js", "app.css", "aging.css", "aging-mode.js", "theme-switcher.js"):
        fp = _UI_DIR / f
        v = int(fp.stat().st_mtime) if fp.exists() else 0
        # 仅替换引号内的资源引用（允许 ./、子目录等路径前缀），不动 HTML 其余部分；
        # 无 ?v= 时追加，有则无论原格式（split1/数字/带点划线）一律按 mtime 覆写。
        html = _re.sub(
            rf"([\"'])([.\w/-]*{_re.escape(f)})(?:\?v=[0-9A-Za-z_.\-]+)?\1",
            rf"\g<1>\g<2>?v={v}\g<1>",
            html,
        )
    # 单页应用外壳 URL 恒定（hash 路由），必须每次回源校验：
    # 缺 Cache-Control 时浏览器会按启发式规则长期缓存这份 HTML，导致它一直
    # 引用旧的 ?v= 资源 URL，前端改动"看起来没生效"。no-store 让外壳永不驻留。
    return _HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )



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


# ---------- 演示标识：所有响应统一带演示运行头（T12 演示标识与隔离验证） ----------
class DemoMarkerMiddleware:
    """纯 ASGI 中间件：给每个响应的起始消息追加演示运行标识头。

    为什么用纯 ASGI 而不是 ``@app.middleware("http")``：``/api/v2/events`` 是
    SSE ``StreamingResponse``，BaseHTTPMiddleware 会包裹并缓存响应体，历史上
    对长连接有已知副作用；这里只在 ``http.response.start`` 阶段 append 两个头，
    **不碰响应体、不改状态码**，对 SSE / 静态资源 / 普通 JSON 一律零影响。

    头值一律 ASCII（``demo-data``），避免非 ASCII 头值在部分反代/隧道下损坏；
    中文标签仍在响应体 ``is_demo`` / ``label`` 字段里（见 ``demo_banner()``）。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_marker(message: Mapping[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-demo-mode", b"1" if DEMO_MODE else b"0"))
                headers.append((b"x-demo-label", b"demo-data"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_marker)


app.add_middleware(DemoMarkerMiddleware)

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
    # V4 结构化问数上下文（看板问数口径直查用；缺省回落纯自然语言链路）：
    # {"module": "mfgboard", "screen": "orders", "kpi": "otd", "view": "kpi",
    #  "window": "day|month|window|all", "filters": {stage/category/prod_cat}}
    ask_ctx: Optional[dict[str, Any]] = None


class OfficeCancelRequest(BaseModel):
    """取消本人会议室预订 / 订餐订单（演示端点；归属校验仍在办公后端）。"""

    kind: str                      # booking | order
    id: str                        # booking_id / order_id
    actor: str = ""                # 操作人（缺省用演示身份）


class DataAccessRequestModel(BaseModel):
    """数据查询权限申请（演示端点；授权只在审批通过那一刻发生）。

    演示无登录令牌，申请人身份与 ``/office/snapshot`` 同口径：由前端带上当前岗位的
    ``roles`` / ``dept`` / ``actor``（真实环境按登录态由服务端解析）。
    """

    dataset: str = ""              # 速查块 key：material_stock/shortage/finished_stock/incoming_batch
    reason: str = ""
    roles: list[str] = []
    dept: str = ""
    actor: str = ""


class DataAccessApproveRequest(BaseModel):
    """演示代批请求体：只需申请人标识（校验"只能为本人申请触发"）。"""

    actor: str = ""


class OfficeChatRequest(QueryRequest):
    """办公助手对话请求：在通用问数请求上追加**本次携带的上传材料**。

    ``attachments`` 为材料标识列表（attachment_id），服务端据此把材料元数据
    注入 Agent 上下文（提交报销时绑定到单据）；传入的材料只认上传人本人所有。
    """

    attachments: Optional[list[str]] = None


class OfficeOrganizeRequest(BaseModel):
    """整理报销科目请求体（确定性规则，不需要 LLM）。"""

    category: str = ""
    amount: float = 0.0
    note: str = ""
    attachment_kinds: list[str] = []
    actor: str = ""


class OfficeLeaveAttachmentRequest(BaseModel):
    """请假单补件请求体（如病假条）：补件材料须为本人上传。"""

    attachment_id: str = ""
    actor: str = ""


class OfficeUploadRequest(BaseModel):
    """OA 材料登记请求（演示态：**只登记清单，不接收文件字节**）。

    前端选文件只为取文件名与大小，请求体里**没有文件内容**——真实字节从不提交到
    本机（端点不收 ``UploadFile``）。``size`` 缺省时由服务端按文件名生成稳定假大小。
    """

    filename: str = ""
    kind: str = "其他"
    actor: str = ""
    note: str = ""
    size: int = 0
    content_type: str = ""


@app.get("/api/v2/health")
def health() -> dict[str, Any]:
    """健康检查：结构化依赖探针（业务库 / 治理库 / Redis / 向量库 / LLM）。

    ``status``：``ok`` 全部就绪 · ``degraded`` 服务可达但部分能力降级。
    **不返回 error**——HTTP 200 已证明进程可达，把"依赖挂了"表达成不可达
    会让前端误判为离线并退回演示数据，反而掩盖真实故障。

    ``mode`` 供前端展示运行形态：``full`` / ``degraded`` / ``offline``（离线降级）。

    注意：本端点**不触发编排器初始化**（不建库、不加载场景、不连向量库），
    因此可以被 Docker HEALTHCHECK 与前端顶栏高频调用。详见
    ``v2/services/health.py`` 的设计纪律。
    """
    from ..services.health import build_health_report

    return build_health_report()


# ---------- 演示数据装载层（T04：合成故事数据只读 API，全部响应带演示标识） ----------


@app.get("/api/v2/demo/banner")
def demo_banner_api() -> dict[str, Any]:
    """演示标识与装载层状态；前端强制渲染「演示数据」徽标。"""
    return {**demo_banner(), "demo_mode": DEMO_MODE}


@app.get("/api/v2/demo/objects")
def demo_objects() -> dict[str, Any]:
    """演示对象清单（与 data/demo_storyline/spec-v1.md 第 2 节对应）。"""
    return {**demo_banner(), "objects": list(OBJECTS)}


@app.get("/api/v2/demo/objects/{name}")
def demo_object(name: str) -> dict[str, Any]:
    """按对象取演示数据；未知对象返回 404（不泄漏内部结构）。"""
    try:
        rows = get_object(name)
    except DemoDataError:
        raise HTTPException(status_code=404, detail="demo object not found")
    return {**demo_banner(), "object": name, "rows": rows}


@app.get("/api/v2/demo/timeline")
def demo_timeline(scene: str = Query(default="")) -> dict[str, Any]:
    """演示事件流时间轴，可按场景过滤（S1..S6）。"""
    return {**demo_banner(), "events": story_timeline(scene or None)}


@app.get("/api/v2/demo/replays/{replay_id}")
def demo_replay(replay_id: str) -> dict[str, Any]:
    """按 replay_id 取智能体 / RAG 回放包（T16，只读）。

    与 ``/api/v2/demo/objects/{name}`` 同一信封口径（``is_demo/label/data_dir``，
    无 code 信封）；额外带 ``schema_version`` 做前向兼容——前端
    ``SceneKit.replay()`` 校验版本，缺失或不符整块降级，不渲染半包。

    畸形包不喂给前端：形状预检不过时仍 200，但带 ``notice`` 说明降级原因。
    """
    try:
        package = get_replay(replay_id)
    except DemoDataError:
        raise HTTPException(status_code=404, detail="demo replay not found")
    notice = ""
    if package.get("schema_version") != "1.0":
        notice = "回放包 schema 版本不符，前端应按降级处理"
    elif not package.get("steps") or not package.get("conclusion"):
        notice = "回放包形状不完整（steps/conclusion 缺失），前端应按降级处理"
    payload: dict[str, Any] = {
        **demo_banner(), "replay": replay_id,
        "schema_version": str(package.get("schema_version") or ""),
        "package": package,
    }
    if notice:
        payload["notice"] = notice
    return payload


# ---------- 企业咨询助手（独立轻量 RAG：Milvus enterprise_faq + 统一 LLM） ----------


class KbQueryRequest(BaseModel):
    """企业制度问答请求。

    - ``query``：员工自然语言问题；
    - ``top_k``：检索制度片段数（默认 3）；
    - ``session_id``：会话标识（可选，仅用于 query_log 分组度量）。
    """

    query: str
    top_k: int = 3
    session_id: str = ""


@app.get("/api/v2/kbqa/meta")
def kbqa_meta() -> dict[str, Any]:
    """企业咨询助手元数据：支持类别面板 + 知识库块数（前端侧栏展示）。"""
    return {"code": 0,
            "data": {"depts": kbqa_service.KBQA_DEPTS, "count": kb_count()},
            "message": "ok"}


@app.post("/api/v2/kbqa")
def kbqa_query(req: KbQueryRequest, request: Request) -> dict[str, Any]:
    """企业制度问答：L1 FAQ 前置 → L2 RAG 兜底，返回统一答案契约。

    独立通道，不经 TwoLevelRouter / 制造场景 DAG；L1 命中不调 LLM，
    L2 结果经 ``core.cache`` 按 query+top_k+FAQ版本 缓存；公网演示下按 IP 限流。
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")
    client_ip = request.client.host if request.client else "unknown"
    if cache.rate_limit_exceeded(f"kbqa:{client_ip}", settings.RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail={
            "code": "KBQA_RATE_LIMITED",
            "message": "提问过于频繁，请稍后再试"})
    data = kbqa_service.answer(req.query, top_k=max(1, min(int(req.top_k), 8)),
                               session_id=req.session_id)
    return {"code": 0, "data": data, "message": "ok"}


# ---------- FAQ 晋升管线（ADR-001：反馈 → 草稿 → 发布） ----------


class KbFeedbackRequest(BaseModel):
    """问答反馈请求。``rating>=4`` 且 ``channel=rag`` 的回答进待审队列。"""

    query: str
    answer: str = ""
    rating: int = 0
    channel: str = ""
    session_id: str = ""


@app.post("/api/v2/kbqa/feedback")
def kbqa_feedback(req: KbFeedbackRequest, request: Request) -> dict[str, Any]:
    """问答反馈：好评（rating>=4）的 RAG 回答进入 FAQ 晋升待审队列。

    L1 直答（channel=faq）不需要晋升——它本就是权威制度；
    feedback 只回收"RAG 答得好"的长尾，等管理员 publish 后进 L1 语料。
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")
    client_ip = request.client.host if request.client else "unknown"
    if cache.rate_limit_exceeded(f"kbqa:fb:{client_ip}", settings.RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail={
            "code": "KBQA_RATE_LIMITED",
            "message": "操作过于频繁，请稍后再试"})
    drafted = False
    if req.rating >= 4 and req.channel == "rag" and req.answer.strip():
        try:
            kbqa_store.insert_draft(req.query.strip(), req.answer.strip(),
                                    origin="rag_feedback")
            drafted = True
        except Exception as exc:  # noqa: BLE001 - 入库失败必须显式暴露，不静默吞
            logger.warning("[kbqa] 反馈草稿入库失败: %s", exc)
            raise HTTPException(status_code=500, detail={
                "code": "KBQA_FEEDBACK_FAILED",
                "message": "反馈保存失败，请稍后重试"})
    return {"code": 0, "data": {"drafted": drafted}, "message": "ok"}


def _kbqa_admin_guard(request: Request) -> None:
    """FAQ 管理端点保护（ADR-001 §3 D4，Hulk 已确认）：

    - 配置 ``KBQA_ADMIN_TOKEN`` → 任意来源凭 ``X-FAQ-Admin-Token`` 头访问；
    - 未配置 → 仅 127.0.0.1/::1（本地管理态）。
    """
    if settings.KBQA_ADMIN_TOKEN:
        if request.headers.get("X-FAQ-Admin-Token", "") != settings.KBQA_ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail={
                "code": "KBQA_ADMIN_DENIED",
                "message": "管理令牌缺失或不正确"})
        return
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail={
            "code": "KBQA_ADMIN_DENIED",
            "message": "管理端点仅限本机访问（或配置 KBQA_ADMIN_TOKEN）"})


@app.get("/api/v2/kbqa/drafts")
def kbqa_drafts(request: Request) -> dict[str, Any]:
    """FAQ 晋升待审队列（管理态）。"""
    _kbqa_admin_guard(request)
    try:
        drafts = kbqa_store.list_drafts("pending")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kbqa] 草稿列表读取失败: %s", exc)
        raise HTTPException(status_code=500, detail="草稿列表读取失败")
    return {"code": 0, "data": {"drafts": drafts}, "message": "ok"}


@app.post("/api/v2/kbqa/drafts/{draft_id}/publish")
def kbqa_publish_draft(draft_id: int, request: Request) -> dict[str, Any]:
    """草稿过审 → 写入 kbqa_faq（active）→ FAQ 版本戳变 → L0 旧缓存自然失效。"""
    _kbqa_admin_guard(request)
    try:
        faq_id = kbqa_store.publish_draft(draft_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={
            "code": "KBQA_DRAFT_NOT_FOUND",
            "message": f"草稿不存在: {draft_id}"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "KBQA_DRAFT_STATE",
            "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kbqa] 草稿发布失败: %s", exc)
        raise HTTPException(status_code=500, detail="草稿发布失败")
    return {"code": 0, "data": {"faq_id": faq_id}, "message": "ok"}


@app.get("/api/v2/kbqa/stats")
def kbqa_stats(request: Request, days: int = 7) -> dict[str, Any]:
    """问答度量：近 N 天 channel 分布 / 低置信 RAG 占比（管理态）。

    ADR-001 的 KG 启用判据与 L1 阈值定标都从这里取数。
    """
    _kbqa_admin_guard(request)
    try:
        stats = kbqa_store.channel_stats(max(1, min(int(days), 90)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kbqa] 度量统计失败: %s", exc)
        raise HTTPException(status_code=500, detail="度量统计失败")
    return {"code": 0, "data": stats, "message": "ok"}


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
    # 缓存键并入 ask_ctx 关键维度：同一句问数文案在不同档位/筛选下结论不同，
    # 防止「当月」命中「最近7天」的缓存（口径错配比慢更糟）。
    ckey = cache.query_key(req.query, user_ctx, ask_ctx=req.ask_ctx)

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
                                           sink=QueueSink(event_queue),
                                           ask_ctx=req.ask_ctx)
    finally:
        unbind(token)
    data = result.to_dict()
    # 报告中心归档（单一漏斗）：链路产出内部简报/对客报告的查询，自动成为报告一等对象。
    # 幂等（report_id 由 run_id 派生）+ 全兜底——归档失败绝不能反噬问数主链路。
    try:
        report_service.archive_run_result(
            data, actor=str(user_ctx.get("name") or ""), source_module="chat")
    except Exception as exc:  # noqa: BLE001 - 归档是旁路，不得影响查询返回
        logger.warning("[report] 归档异常，问数结果不受影响: %s", exc)
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


# S1 接单评审会签（T15）：工单预置幂等守卫（进程内只查库一次）
_order_review_seeded = False

# 与 scenarios/order_review_v2.yaml 的 hitl_order_review.actions 同构（保持单一事实源）
_ORDER_REVIEW_ACTIONS: list[dict[str, Any]] = [
    {"key": "accept", "label": "接受订单并按期承诺", "propose": ["业务"],
     "cosign": ["工程", "计划"], "impact": "按 ATP 初评口径接单",
     "followup": "锁定产能 / 通知计划排产"},
    {"key": "conditional", "label": "有条件接受", "propose": ["业务"],
     "cosign": ["工程", "计划"], "impact": "工艺/产能经确认后方可接单",
     "require_comment": True, "followup": "补齐评审意见 / 重排产"},
    {"key": "reject", "label": "退回业务重议", "propose": ["业务"],
     "impact": "价格/交期/产能不匹配 · 退回重议",
     "require_comment": True, "followup": "退回业务重新报价"},
]


def _ensure_order_review_seed() -> None:
    """幂等预置 S1「接单评审会签」工单（T15）。

    背景：平台此前没有 order_review 治理工作流，S1 屏只能回放事件流、无法展示
    真实待办。本函数在建单库（``WorkflowEngine`` 实际使用的 SQLite，默认
    ``data/mfg_agent.db``）预置一条 ``waiting_hitl`` 工单，使 S1 屏可改为
    「真实工单驱动」，并天然演示「按槽位可见的不同待办」（业务=发起人视角，
    工程/计划=会签人待办，其余岗位不可见）。

    **幂等**：按 ``wf_id == hitl_order_review`` 判定，存在即跳过；进程内再加
    一次内存守卫，避免每次请求都查库。副作用仅限演示库，不改动既有工单。
    """
    global _order_review_seeded
    if _order_review_seeded:
        return
    engine = _workflow_engine()
    existing = [t for t in engine.list_tasks() if t.wf_id == "hitl_order_review"]
    if not existing:
        task = engine.create_task(
            wf_id="hitl_order_review",
            title="接单评审会签工单（S1 · 主线订单）",
            context={"slot": "order_review", "scene": "S1",
                     "actions": _ORDER_REVIEW_ACTIONS},
            assignees=["业务经理", "工程", "计划"],
            priority="P1", sla_hours=8, hitl=True,
            hitl_prompt="接单评审：订单已过 ATP 与报价初评，请按岗位职责完成会签与终批",
        )
        # 预置「已发起 · 待会签」：业务经理已选定「接受」动作，工程 / 计划待会签。
        engine.select_action(
            task.task_uid, "accept", actor="业务经理（演示）",
            user_ctx={"user_id": "u-sales-demo", "roles": ["sales"]},
            comment="订单已过 ATP 与报价初评，按接受口径发起一次会签")
        logger.info("[demo] 预置 S1 接单评审会签工单 %s", task.task_uid)
    _order_review_seeded = True


# S5 客诉 8D 双 HITL（T16）：工单预置幂等守卫（进程内只查库一次）
_8d_seeded = False
#: 种子标记（架构 A3）：工单库存在 416 条 hitl_d8_close / 211 条 hitl_complaint_reply
#: 的**存量夹具**，按 wf_id 判幂等会被误判「已存在」而跳过；标记判定精确锚定本种子。
_8D_SEED_MARK = "s5_8d_v1"

# 与 scenarios/customer_complaint_v2.yaml 的 hitl_d8_close.actions 同构（单一事实源）
_8D_CLOSE_ACTIONS: list[dict[str, Any]] = [
    {"key": "close", "label": "关闭 8D", "propose": ["质量总监", "QA经理"],
     "cosign": ["PQE", "SQE", "工程"], "require_comment": True,
     "impact": "结案 · 措施需已验证", "followup": "归档 / 更新经验库"},
    {"key": "return_supplement", "label": "退回补充", "propose": ["质量总监", "QA经理"],
     "require_comment": True, "impact": "闭环延期 · 需补验证",
     "followup": "退回责任部门 / 重新提交"},
    {"key": "extend", "label": "延期关闭", "propose": ["质量总监"],
     "require_comment": True, "impact": "客户等待 · 需沟通",
     "followup": "对客说明 / 设定新期限"},
]

# 与 customer_complaint_v2.yaml 的 hitl_complaint_reply.actions 同构
_COMPLAINT_REPLY_ACTIONS: list[dict[str, Any]] = [
    {"key": "reply_8d", "label": "按 8D 结论回复", "propose": ["业务经理"],
     "cosign": ["QA"], "impact": "口径需与 8D 一致", "followup": "发送客户 / 留痕"},
    {"key": "reply_buffered", "label": "加严承诺回复", "propose": ["业务经理"],
     "cosign": ["QA"], "impact": "承诺升级 · 需内部保障",
     "followup": "内部保障措施 / 发送客户"},
    {"key": "escalate", "label": "升级处理", "propose": ["业务经理"],
     "impact": "重大客诉 · 高层介入", "followup": "升级工单"},
]


def _ensure_8d_seed() -> None:
    """幂等预置 S5「客诉 8D」双 HITL 工单（T16，参照 T15 模式）。

    PRD P0-5：S5 屏的 8D 卡要展示**真实治理工单**按槽位可见，而不是纯事件回放。
    本函数在演示建单库（``WorkflowEngine`` 实际使用的 SQLite，默认
    ``data/mfg_agent.db``）预置两条 ``waiting_hitl`` 工单：
    ``d8_close``（质量总监发起关闭、工程侧待会签）与 ``complaint_reply``
    （业务发起对客回复、QA 待会签）。内容全部虚构（DEMO-CC-001 / 演示人名），
    仅动演示库，不改任何治理配置（槽位在 ``GOVERNANCE_SLOTS`` 已存在）。

    **幂等**：按 ``context.demo_seed_id == s5_8d_v1`` 判定，存在即跳过；
    进程内再加一次内存守卫，避免每次请求都查库。
    """
    global _8d_seeded
    if _8d_seeded:
        return
    engine = _workflow_engine()
    seeded = {str((t.context or {}).get("demo_seed_id") or "")
              for t in engine.list_tasks()}
    if _8D_SEED_MARK not in seeded:
        d8_task = engine.create_task(
            wf_id="hitl_d8_close",
            title="客诉 8D 报告关闭确认（DEMO-CC-001 · 演示种子）",
            context={"slot": "d8_close", "scene": "S5", "origin": "demo_seed",
                     "demo_seed_id": _8D_SEED_MARK,
                     "complaint_id": "DEMO-CC-001", "lot_id": "DEMO-LOT-002",
                     "actions": _8D_CLOSE_ACTIONS},
            assignees=["质量总监", "QA经理", "PQE", "SQE", "工程"],
            priority="P1", sla_hours=72, hitl=True,
            hitl_prompt="8D 报告关闭确认：D4 根因（烧录站静电接地失效）与 "
                        "D5–D7 措施已验证，请按岗位会签",
        )
        engine.select_action(
            d8_task.task_uid, "close", actor="质量总监（演示）",
            user_ctx={"user_id": "u-qd-demo", "roles": ["quality_director"]},
            comment="D4–D7 证据链完整，按关闭口径发起会签")
        reply_task = engine.create_task(
            wf_id="hitl_complaint_reply",
            title="对客回复确认（DEMO-CC-001 · 演示种子）",
            context={"slot": "complaint_reply", "scene": "S5", "origin": "demo_seed",
                     "demo_seed_id": _8D_SEED_MARK,
                     "complaint_id": "DEMO-CC-001",
                     "actions": _COMPLAINT_REPLY_ACTIONS},
            assignees=["业务经理", "QA"],
            priority="P1", sla_hours=72, hitl=True,
            hitl_prompt="对客回复确认：按 8D 结论回复口径，请业务发起、QA 会签一致性",
        )
        engine.select_action(
            reply_task.task_uid, "reply_8d", actor="业务经理（演示）",
            user_ctx={"user_id": "u-sales-demo", "roles": ["sales"]},
            comment="按 8D 结论回复，请 QA 会签口径一致性")
        logger.info("[demo] 预置 S5 客诉 8D 双 HITL 工单 %s / %s",
                    d8_task.task_uid, reply_task.task_uid)
    _8d_seeded = True


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
def api_workflow_pending(request: Request,
                         role: Optional[list[str]] = Query(None)) -> dict[str, Any]:
    """HITL 待办队列（只能看到「我的活」与「我治理域内的活」）。

    身份一律由 ``Authorization: Bearer`` 令牌裁决（fail-closed，与写接口同一口径）：

    - 无令牌 / 令牌非法 / 过期 → **401**
    - ``role`` query 只能**收窄**视野（必须 ⊆ 令牌角色），越权扩宽 → **403**
    - 无治理职责的岗位（如行政人事）→ **空队列且 200**：不是「没资格看」，
      而是「本来就没有你的活」，两种情况报不同状态码才是诚实的产品行为

    每项带 ``visibility``：

    - ``mine``：当前阶段我能动作（见 ``WorkflowEngine.action_for``）
    - ``domain``：我治理域内但暂不轮到我，或未纳管的历史存量（只读）

    判据来自 ``permissions.yaml`` 的角色矩阵（我是否持有该 HITL 槽位的权限），
    不引入第二份配置——详见 ``services/workflow_scope.py``。
    """
    identity = _require_identity(request)
    _ensure_order_review_seed()  # T15：S1 接单评审工单幂等预置（存在即跳过）
    _ensure_8d_seed()            # T16：S5 客诉 8D 双 HITL 工单幂等预置（标记判定）
    engine = _workflow_engine()
    registry = engine.permission_registry

    token_roles = [str(r) for r in (identity.get("roles") or [])]
    token_keys = set(registry.normalize_roles(token_roles))

    requested: list[str] = []
    for item in (role or []):
        requested.extend([seg.strip() for seg in str(item).split(",") if seg.strip()])

    if requested:
        unknown = [r for r in requested
                   if registry.normalize_role(r) is None]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail={"message": "岗位未识别", "unknown": unknown,
                        "hint": "可选岗位见 /api/v2/governance/permissions"})
        req_keys = set(registry.normalize_roles(requested))
        if not req_keys.issubset(token_keys):
            # 令牌只证明「你是谁」，不能用来「借别人的眼睛看」。
            # 不做这道校验，fail-closed 就退化成「自证式权限」。
            raise HTTPException(
                status_code=403,
                detail={"message": "不能查看本人岗位之外的待办",
                        "requested": sorted(req_keys),
                        "granted": sorted(token_keys)})
        effective, effective_keys = requested, req_keys
    else:
        effective, effective_keys = token_roles, token_keys

    # 不能用 engine.pending_hitl()：它按 action_for 过滤，只留 mine，会丢掉 domain。
    pairs = workflow_scope.visible_tasks(
        engine, engine.list_tasks(status="waiting_hitl"), effective)

    data: list[dict[str, Any]] = []
    mine = domain = 0
    for task, vis in pairs:
        item = task.to_dict()
        item["visibility"] = vis
        item["my_action"] = (engine.action_for(task, effective)
                             if vis == workflow_scope.VIS_MINE else None)
        if vis == workflow_scope.VIS_MINE:
            mine += 1
        else:
            domain += 1
        data.append(item)

    slots = workflow_scope.slot_keys_for(registry, effective)
    return {
        "code": 0, "data": data, "message": "ok",
        "scope": {
            "roles": sorted(effective_keys),
            "slots": sorted(slots),
            "counts": {"mine": mine, "domain": domain,
                       "visible": mine + domain},
            # 是否发生了过滤：无治理职责的岗位拿到的是真空队列，
            # 前端据此显示「本岗位不参与治理审批」（而非「暂无待办」）。
            "filtered": True,
        },
    }


@app.get("/api/v2/workflow/{task_id}")
def api_workflow_task(task_id: str, request: Request) -> dict[str, Any]:
    """单工单详情（会签后局部刷新卡片用）。

    可见性与队列同源（``workflow_scope.classify``）。**不可见返回 404 而非 403**：
    403 等于确认「这个工单存在，只是你看不到」，把 ID 空间变成可探测信息。
    """
    identity = _require_identity(request)
    engine = _workflow_engine()
    try:
        task = engine.get(task_id)
    except (KeyError, WorkflowTransitionError, ValueError) as exc:
        _raise_workflow_http(exc)
        raise  # pragma: no cover - _raise_workflow_http 必定抛出
    if workflow_scope.classify(
            engine, task, identity.get("roles") or []) == workflow_scope.VIS_HIDDEN:
        raise HTTPException(status_code=404, detail="工单不存在")
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


# ============================================================
# 报告中心（报告一等对象：归档 → 列表/筛选 → 详情 → 审定 → 派发 → 导出）
#
# 设计要点：
# - 真相同源：报告由 ``report_service`` 从编排结果 / 来源模块既有事实拼装，
#   本层只做鉴权、参数校验与异常→状态码映射（不掺业务判断）。
# - 两层可见性：读接口要令牌（fail-closed）；非内部岗位读 ``customer`` 报告时，
#   服务层自动剥离 ``internal_brief``；内部专报对该岗位返回 404（不泄漏 ID 空间）。
# - 审定/派发与审批确认中心共用同一套治理链（``hitl.report_publish`` 槽位 + 会签）。
# ============================================================


class ReportGenerateRequest(BaseModel):
    """就地生成报告请求。

    ``source_module``：``task``（工单，后端补真实事实）/ ``alert`` / ``case`` /
    ``kpi`` / ``manual``；``context``：来源模块已渲染的真实字段
    （``title`` 必填，其余如 ``summary/refs/line_code/risk_level`` 可选）。
    """

    source_module: str = "manual"
    context: dict[str, Any] = {}


class ReportReviewRequest(BaseModel):
    """报告审定请求：``action`` ∈ submit（发起）/ approve（通过）/ reject（驳回）。"""

    action: str = "submit"
    comment: str = ""


class ReportDispatchRequest(BaseModel):
    """报告派发/对外发布请求：``comment`` 必填（说明派发对象与口径）。"""

    comment: str = ""


def _raise_report_http(exc: Exception) -> None:
    """报告域异常 → HTTP（404 不存在 / 409 状态或会签冲突 / 403 无权限 / 400 参数）。"""
    if isinstance(exc, report_store.ReportNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, report_store.InvalidReportTransition):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _raise_workflow_http(exc)


@app.get("/api/v2/reports")
def api_reports(request: Request,
                scenario_key: str = Query(""), source_module: str = Query(""),
                status: str = Query(""), visibility: str = Query(""),
                risk_level: str = Query(""), q: str = Query(""),
                limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """报告列表：筛选（场景 / 来源模块 / 状态 / 可见范围 / 风险评级）+ 关键字 + 分页。"""
    identity = _require_identity(request)
    data = report_service.list_payload(
        identity, scenario_key=scenario_key, source_module=source_module,
        status=status, visibility=visibility, risk_level=risk_level, q=q,
        limit=limit, offset=offset)
    return {"code": 0, "data": data, "message": "ok", "scope": data["scope"]}


@app.get("/api/v2/reports/stats")
def api_reports_stats(request: Request) -> dict[str, Any]:
    """顶部 KPI 概览条（必须在 ``/{report_id}`` **之前**声明，否则 stats 会被吞成 id）。"""
    identity = _require_identity(request)
    return {"code": 0, "data": report_service.stats_payload(identity), "message": "ok"}


@app.get("/api/v2/reports/{report_id}")
def api_report_detail(report_id: str, request: Request) -> dict[str, Any]:
    """报告详情（内部简报 / 对客脱敏 / 数据溯源 / 关联 / 版本）。不可见 → 404。"""
    identity = _require_identity(request)
    try:
        data = report_service.get_payload(report_id, identity)
    except report_store.ReportStoreError as exc:
        _raise_report_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": data, "message": "ok"}


@app.post("/api/v2/reports/generate")
def api_reports_generate(req: ReportGenerateRequest,
                         request: Request) -> dict[str, Any]:
    """按来源模块上下文生成报告（各协同屏「生成报告」按钮的统一入口）。"""
    identity = _require_identity(request)
    try:
        report = report_service.generate_from_context(
            req.source_module, req.context,
            actor=str(identity.get("name") or "用户"),
            engine=_workflow_engine())
    except (report_store.ReportStoreError, KeyError, ValueError) as exc:
        _raise_report_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": report, "message": "ok"}


@app.post("/api/v2/reports/{report_id}/review")
def api_report_review(report_id: str, req: ReportReviewRequest,
                      request: Request) -> dict[str, Any]:
    """报告审定（submit / approve / reject）：走 ``hitl.report_publish`` 治理链与多角色会签。"""
    identity = _require_identity(request)
    try:
        data = report_service.review_report(
            report_id, req.action, req.comment, identity, _workflow_engine())
    except (report_store.ReportStoreError, KeyError, PermissionDenied,
            SignoffIncomplete, WorkflowTransitionError, ValueError) as exc:
        _raise_report_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": data, "message": "ok"}


@app.post("/api/v2/reports/{report_id}/dispatch")
def api_report_dispatch(report_id: str, req: ReportDispatchRequest,
                        request: Request) -> dict[str, Any]:
    """报告派发/对外发布：需 ``approve`` 权限 + 必填意见 + 该报告有溯源。"""
    identity = _require_identity(request)
    try:
        report = report_service.dispatch_report(report_id, req.comment, identity)
    except (report_store.ReportStoreError, KeyError, PermissionDenied,
            WorkflowTransitionError, ValueError) as exc:
        _raise_report_http(exc)
        raise  # pragma: no cover
    return {"code": 0, "data": report, "message": "ok"}


@app.get("/api/v2/reports/{report_id}/export")
def api_report_export(report_id: str, request: Request,
                      fmt: str = Query("md")) -> Response:
    """导出报告（``md`` / ``html``）。PDF 交由浏览器打印对话框输出，后端不造 PDF。"""
    identity = _require_identity(request)
    try:
        body, media_type, filename = report_service.export_report(
            report_id, fmt, identity)
    except report_store.ReportStoreError as exc:
        _raise_report_http(exc)
        raise  # pragma: no cover
    from urllib.parse import quote
    return Response(
        content=body, media_type=media_type,
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


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
def api_office_chat(req: OfficeChatRequest) -> dict[str, Any]:
    """办公助手对话入口：直接交给独立的办公助手 Agentic Agent 办理。

    与 ``/api/v2/query`` 的区别：此处**强制定向**办公助手（不依赖关键词路由），
    供前台聚焦演示"帮我订个会议室/中午订个餐"；返回值含工具调用留痕（steps）。
    真实接入时把 ``runtime.office`` 换成飞书/钉钉/美餐适配器即可，本端点零改动。

    ``req.attachments``（材料标识）由服务端解析为**本人上传**的材料元数据后一并
    交给 Agent：提交报销时绑定到单据（借用他人票据的 id 会被剔除并如实说明）。
    """
    from ...common.llm_factory import llm_available
    from ..agents.office import OfficeAssistantAgent
    from ..services.office import get_attachment_store

    if not llm_available():
        return {"code": 1, "data": None,
                "message": "办公助手需要 LLM 服务（当前不可用），请稍后重试。"}

    user_ctx = req.user_ctx or DEFAULT_USER_CTX
    actor = (str(user_ctx.get("name") or "").strip()
             or str(user_ctx.get("user_id") or "").strip())
    attachments: list[dict[str, Any]] = []
    invalid: list[str] = []
    if req.attachments:
        attachments, invalid = get_attachment_store().resolve(req.attachments, actor)

    orch = get_orchestrator()
    try:
        out = OfficeAssistantAgent(orchestrator=orch).run(
            req.query, user_ctx=user_ctx, attachments=attachments)
    except Exception as exc:  # noqa: BLE001 —— 演示端点：失败如实降级不崩
        return {"code": 1, "data": None,
                "message": f"办公助手暂不可用：{type(exc).__name__}: {exc}"}
    if invalid:
        out["data_gaps"] = list(out.get("data_gaps") or []) + [
            f"以下材料无效或不属于本人，已忽略：{'、'.join(invalid)}"]
    return {"code": 0, "data": out, "message": "ok"}


@app.post("/api/v2/office/upload")
def api_office_upload(req: OfficeUploadRequest) -> dict[str, Any]:
    """登记 OA 办理材料（发票 / 出差报告 / 行程单 / 付款凭证 / 病假条 / 其他）。

    **演示态做"虚假上传"**：只登记材料清单（文件名、类别、大小、上传人），
    **不接收、不保存原文件**——前端选文件只为取文件名与大小，真实字节从不提交到
    本机。返回材料元数据（含 ``attachment_id`` 与 ``virtual=true``），供前端挂到
    报销/请假单据上。治理约束：类别白名单、单份大小上限、上传人必填。
    """
    from ..services.office import OfficeError, get_attachment_store

    uploader = (str(req.actor or "").strip()
                or str(DEFAULT_USER_CTX.get("name") or "").strip()
                or str(DEFAULT_USER_CTX.get("user_id") or "演示用户"))
    try:
        meta = get_attachment_store().save(
            req.filename, req.kind, uploader, req.size, req.content_type, req.note)
    except OfficeError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "OA_UPLOAD_INVALID", "message": str(exc)}) from exc
    return {"code": 0, "data": meta, "message": "ok"}


@app.get("/api/v2/office/attachment/{attachment_id}")
def api_office_attachment(attachment_id: str) -> Response:
    """查看材料登记信息（演示态**无原文件**：返回登记卡片说明，不提供实际下载）。

    真实接入对象存储后，此处改为按单据权限校验后回源（``FileResponse``/预签名 URL）。
    """
    from ..services.office import get_attachment_store

    meta = get_attachment_store().get(attachment_id)
    if not meta:
        raise HTTPException(status_code=404, detail={
            "code": "OA_ATTACHMENT_NOT_FOUND", "message": "材料不存在或已失效"})
    size_kb = f"{meta['size'] / 1024:.1f} KB"
    html = (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<body style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
        "padding:24px;color:#1f2937;background:#f7f8fb\">"
        "<h3 style=\"margin:0 0 8px\">材料登记（演示态 · 未保存原文件）</h3>"
        f"<p style=\"margin:4px 0\">登记号：<b>{meta['attachment_id']}</b></p>"
        f"<p style=\"margin:4px 0\">文件名：{meta['filename']}</p>"
        f"<p style=\"margin:4px 0\">类别：{meta['kind']} · 大小：≈{size_kb}</p>"
        f"<p style=\"margin:4px 0\">上传人：{meta['uploader']} · {meta['uploaded_at']}</p>"
        "<p style=\"margin:12px 0 0;color:#6b7280\">演示模式只登记材料清单，"
        "未在本地接收/保存原文件；接入真实存储后可在此预览或下载原件。</p>"
        "</body></html>")
    return Response(content=html, media_type="text/html; charset=utf-8")


@app.post("/api/v2/office/expense/organize")
def api_office_expense_organize(req: OfficeOrganizeRequest) -> dict[str, Any]:
    """整理报销科目（确定性规则，不需要 LLM）。

    输出：二级科目清单、按「事由」拆分的明细（如「高铁 980 + 住宿 306」）、
    该类别**必传/选传**材料核对与缺失的必传项。供前端"整理报销科目"按钮与
    Agent 的 ``organize_expense`` 工具共用同一实现（口径即代码）。
    """
    from ..services.office import OfficeError, organize_expense

    try:
        out = organize_expense(req.category, req.amount, req.note,
                               req.attachment_kinds)
    except OfficeError as exc:
        return {"code": 1, "data": None, "message": str(exc)}
    return {"code": 0, "data": out, "message": "ok"}


@app.post("/api/v2/office/leave/{leave_id}/attachment")
def api_office_leave_attachment(leave_id: str,
                                req: OfficeLeaveAttachmentRequest) -> dict[str, Any]:
    """给请假单补件（如病假条图片）：仅限本人、未撤销的请假单。

    与"提交后补充病假条"的真实场景对应：先上传取得 ``attachment_id``，再补挂到
    请假单上；补件留痕、病假条到位后清除"待补"标记。他人单据不可代补。
    """
    from ..services.office import OfficeError, get_attachment_store

    actor = (str(req.actor or "").strip()
             or str(DEFAULT_USER_CTX.get("name") or "").strip()
             or str(DEFAULT_USER_CTX.get("user_id") or "演示用户"))
    metas, invalid = get_attachment_store().resolve([req.attachment_id], actor)
    if not metas:
        return {"code": 1, "data": None,
                "message": f"材料无效或不属于本人：{req.attachment_id or '（空）'}"}
    try:
        leave = get_orchestrator().runtime.office.supplement_leave_document(
            leave_id, metas[0], actor)
    except OfficeError as exc:
        return {"code": 1, "data": None, "message": str(exc)}
    return {"code": 0, "data": {"leave": leave, "attachment": metas[0]},
            "message": "ok"}


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
    """按操作人查其会议室预订、订餐订单与 OA 申请（demo 演示用；真实环境按登录身份）。

    OA 申请含：费用报销、请假申请、补考勤补卡、OA 服务工单（IT 报修/账号权限/设备领用/
    车位/访客车辆/公务用车），以及年假/调休余额。
    """
    be = get_orchestrator().runtime.office
    bookings = be.list_bookings(actor)
    orders = be.list_orders(actor)
    expenses = be.list_expenses(actor)
    leaves = be.list_leaves(actor)
    attendance_fixes = be.list_attendance_fixes(actor)
    service_requests = be.list_service_requests(actor)
    balance = be.leave_balance(actor)
    return {"code": 0, "data": {
        "bookings": bookings, "orders": orders,
        "expenses": expenses, "leaves": leaves,
        "attendance_fixes": attendance_fixes,
        "service_requests": service_requests, "leave_balance": balance,
    }, "message": "ok"}


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
    from ..services.office import (ATTACHMENT_KINDS, ATTACHMENT_MAX_MB,
                                   EXPENSE_ATTACHMENT_KINDS,
                                   EXPENSE_APPROVAL_AMOUNT, LEAVE_LONG_DAYS,
                                   LEAVE_ATTACHMENT_KINDS, ORDER_APPROVAL_AMOUNT)

    office = get_orchestrator().runtime.office
    impl = type(office).__name__
    return {"code": 0, "data": {
        "backend": "demo" if impl == "DemoOfficeBackend" else "real",
        "backend_impl": impl,
        "llm_available": bool(llm_available()),
        "order_approval_amount": ORDER_APPROVAL_AMOUNT,
        "expense_approval_amount": EXPENSE_APPROVAL_AMOUNT,
        "leave_long_days": LEAVE_LONG_DAYS,
        "attachment_kinds": ATTACHMENT_KINDS,
        "expense_attachment_kinds": EXPENSE_ATTACHMENT_KINDS,
        "leave_attachment_kinds": LEAVE_ATTACHMENT_KINDS,
        "attachment_max_mb": ATTACHMENT_MAX_MB,
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


#: 数据速查四块（dataset key → 注册查询名 / 物理表 / 说明）。
_SNAPSHOT_BLOCKS: list[tuple[str, str, str, str]] = [
    ("material_stock", "material_stock_all", "material_inventory",
     "材料库存（按料号×仓库；可用量=在库-预留，冻结量不计可用）"),
    ("shortage", "material_shortage", "material_inventory",
     "缺料清单（可用量低于安全库存，含缺口）"),
    ("finished_stock", "finished_stock_all", "finished_inventory",
     "成品库存（型号×仓库；可发量 ATP=在库-预留）"),
    # 用「全量扫描（上限 300）」而非「近期 10 条」：速查卡的检索是在**返回行**上做的，
    # 若只给最近 10 条，按批号/料号搜更早的批次会搜不到，演示不了检索完整性。
    ("incoming_batch", "incoming_batch_scan", "incoming_batch",
     "来料批次（全量，上限 300；IQC 结论：合格/不合格/待检/让步接收）"),
]
#: dataset key → 物理表（权限申请按物理表；前端只认 key，不额外暴露物理表名）。
_SNAPSHOT_TABLE_BY_DATASET: dict[str, str] = {
    key: table for key, _qname, table, _note in _SNAPSHOT_BLOCKS
}


@app.get("/api/v2/office/snapshot")
def api_office_snapshot(roles: Optional[str] = Query(None)) -> dict[str, Any]:
    """企业综合助手·制造数据速查快照（首屏用，无需 LLM）。

    一次返回四块确定性只读数据：材料库存速查、缺料清单、成品库存、近期来料批次。
    全部转发统一注册查询（口径即代码），并按岗位表级权限过滤：
    无权限的块标记 ``authorized=false`` 并给出提示（含 ``requestable`` / ``owner_label``
    供前端展示「申请查询权限」入口），越权不返回数据（fail-closed）。
    任一查询失败只降级该块（``ok=false``），端点绝不 500。
    """
    from ..services.data_access import OWNER_LABEL
    from ..services.text2sql import table_acl

    role_set = {s.strip() for s in str(roles or "").split(",") if s.strip()}
    if not role_set:
        role_set = {str(r) for r in (DEFAULT_USER_CTX.get("roles") or [])}

    reg = get_orchestrator().runtime.data_registry
    blocks: dict[str, Any] = {}
    for key, qname, table, note in _SNAPSHOT_BLOCKS:
        # 白名单经 table_acl() 读取：静态 TABLE_ACL ∪ 数据查询权限申请的运行时授权叠加层。
        if not (role_set & table_acl(table)):
            blocks[key] = {"authorized": False, "ok": False, "rows": [], "count": 0,
                           "note": note, "requestable": True,
                           "owner_label": OWNER_LABEL.get(table, ""),
                           "message": f"当前岗位无权限查询（{table}），"
                                      "请走对应职责岗或申请查询权限"}
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


@app.get("/api/v2/office/data-access/requests")
def api_data_access_requests(actor: str = Query("")) -> dict[str, Any]:
    """我提交的数据查询权限申请（含已裁决状态，供卡片状态展示）。"""
    from ..services.data_access import get_data_access

    svc = get_data_access()
    svc.reap_expired()
    return {"code": 0, "data": {"requests": svc.list_mine(actor)}, "message": "ok"}


@app.post("/api/v2/office/data-access/request")
def api_data_access_request(req: DataAccessRequestModel) -> dict[str, Any]:
    """提交数据查询权限申请（**不直接授权**；授权只在审批通过那一刻发生）。

    返回 ``demo_approval`` 预检：前端据此决定提交后是直接提示"等待审批"，
    还是弹窗走模拟审批链路（与案例检索 ``request-access`` 同构）。
    """
    from ..services.data_access import (
        AlreadyAuthorized,
        DataAccessError,
        DuplicateRequest,
        get_data_access,
    )

    table = _SNAPSHOT_TABLE_BY_DATASET.get(str(req.dataset or ""))
    if not table:
        raise HTTPException(status_code=400, detail={
            "code": "DATA_ACCESS_DATASET_INVALID",
            "message": f"未知的数据集: {req.dataset!r}"})

    svc = get_data_access()
    try:
        created = svc.request_access(table=table, roles=req.roles, reason=req.reason,
                                     actor=req.actor, dept=req.dept)
    except AlreadyAuthorized as exc:
        raise HTTPException(status_code=409, detail={
            "code": "DATA_ACCESS_ALREADY_AUTHORIZED", "message": str(exc)}) from exc
    except DuplicateRequest as exc:
        raise HTTPException(status_code=409, detail={
            "code": "DATA_ACCESS_DUPLICATE", "message": str(exc)}) from exc
    except DataAccessError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "DATA_ACCESS_INVALID", "message": str(exc)}) from exc

    return {"code": 0, "data": {
        "request": created,
        "demo_approval": svc.preview(table=table, roles=req.roles, actor=req.actor),
        "message": "申请已提交，等待职责岗审批",
    }, "message": "ok"}


@app.post("/api/v2/office/data-access/requests/{request_id}/demo-approve")
def api_data_access_demo_approve(request_id: str,
                                  req: DataAccessApproveRequest) -> dict[str, Any]:
    """演示专用：模拟「职责岗审批通过」的回调（**预留接口，仅演示**）。

    生产形态：职责岗负责人在真实审批流（OA / IM 审批）里点同意，系统回调同一条
    "审批通过 → 写入授权叠加层"的路径；本端点只是把"谁点的同意"换成职责岗演示身份。
    硬约束：① 只有申请人本人能触发；② 审批身份由服务端按表归属职责岗解析，解析不出
    → 409，**不降级找"差不多的人"批**；③ 授权副作用只有 ``demo_approve`` 一条路，
    事件打 ``.demo`` 后缀显式标记。开关：``CASE_DEMO_MODE`` 且 ``DATA_ACCESS_AUTO_APPROVE_DEMO``。
    """
    from ..services.data_access import (
        ApproverUnavailable,
        DataAccessError,
        DemoDisabled,
        NotRequester,
        RequestNotFound,
        get_data_access,
    )

    svc = get_data_access()
    try:
        decided = svc.demo_approve(request_id, actor=req.actor)
    except DemoDisabled as exc:
        raise HTTPException(status_code=403, detail={
            "code": "DATA_ACCESS_DEMO_DISABLED", "message": str(exc)}) from exc
    except NotRequester as exc:
        raise HTTPException(status_code=403, detail={
            "code": "DATA_ACCESS_NOT_REQUESTER", "message": str(exc)}) from exc
    except ApproverUnavailable as exc:
        raise HTTPException(status_code=409, detail={
            "code": "DATA_ACCESS_APPROVER_UNAVAILABLE", "message": str(exc)}) from exc
    except RequestNotFound as exc:
        raise HTTPException(status_code=404, detail={
            "code": "DATA_ACCESS_REQUEST_NOT_FOUND", "message": str(exc)}) from exc
    except DataAccessError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "DATA_ACCESS_DECISION_INVALID", "message": str(exc)}) from exc

    return {"code": 0, "data": {
        "request": decided,
        "demo": True,
        "message": "职责岗已审批通过：授权已写入（带有效期，到期自动回收）",
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


# ---------- 看板数据范围：声明式目录 → 服务端裁决 ----------

# 为什么不能只在前端藏菜单：菜单只挡入口，不挡 curl。四个看板端点此前
# docstring 写着「全员可见」，任何岗位持有效令牌即可拉 SPC / 订单履约聚合，
# 「HR 凭什么看 SPC」在服务端没有答案。裁决收敛到 v2/data/data_scope.yaml。


def _scope_guard(request: Request, dashboard: str) -> dict[str, Any]:
    """看板接口的数据范围裁决（fail-closed）。

    - 无令牌 / 令牌非法 → 401（业务数据不接受匿名读取）；
    - 目录未声明该看板 → 403（新增看板必须登记，否则谁也用不了）；
    - 岗位不在 ``allow_roles`` → 403，detail 写明「你是哪个岗位、谁能看」。

    关键纪律：**绝不降级成 200 空数据**。前端拿到空/异常会切演示数据，
    那等于把越权访问伪装成正常展示，比直接报错更糟。
    """
    ctx = _require_identity(request)
    roles = list(ctx["roles"])
    scope = get_data_scope().get(dashboard)
    if scope is None or not scope.allows(roles):
        perms = get_permission_registry()
        raise HTTPException(status_code=403, detail={
            "message": (f"岗位「{'、'.join(perms.role_names(roles)) or '未识别'}」"
                        f"无权查看{scope.name if scope else dashboard}"),
            "dashboard": dashboard,
            "roles": roles,
            "allowed_roles": perms.role_names(sorted(scope.allow_roles)) if scope else [],
        })
    return ctx


def _dashboard_payload(request: Request, dashboard: str, builder,
                       window: Optional[str] = None,
                       **extra: Any) -> dict[str, Any]:
    """统一编排：裁决 → 聚合 → 脱敏 → 回写 scope 凭据。

    ``window`` 为可选板级时间口径（``manufacturing`` / ``quality`` / ``spc`` 支持
    ``window|day|month``）；``None`` 表示该看板不接口径参数（orders）。
    ``extra``：看板自定义筛选（如品质看板的 ``stage`` / ``category``、SPC 看板的
    ``stage`` / ``product_category``），原样透传给聚合器。
    """
    ctx = _scope_guard(request, dashboard)
    registry = get_orchestrator().runtime.data_registry
    if window is not None:
        data = builder(registry, window=window, **extra)
    else:
        data = builder(registry, **extra)
    data, masked = get_data_scope().apply(dashboard, data, ctx["roles"])
    return {"code": 0, "data": data, "message": "ok",
            "scope": {"dashboard": dashboard, "roles": list(ctx["roles"]),
                      "masked": masked}}


# 板级时间口径白名单（制造/品质看板）：默认最近 7 天，非法值 → 400
_WINDOW_KEYS = ("window", "day", "month")
# 下钻口径白名单：额外放行 "all"（仅 defect_type 柏拉图类型下钻可用，见下钻端点）
_DRILL_WINDOW_KEYS = _WINDOW_KEYS + ("all",)


def _checked_window(window: str, allowed: tuple[str, ...] = _WINDOW_KEYS) -> str:
    if window not in allowed:
        raise HTTPException(status_code=400, detail="未知时间口径 window")
    return window


@app.get("/api/v2/dashboard/manufacturing")
def api_dashboard_manufacturing(request: Request,
                                window: str = Query("window"),
                                stage: str = Query(""),
                                product_category: str = Query("")) -> dict[str, Any]:
    """制造看板：稼动率 / 报废率 / 报工完成率 / 测试直通率（按岗位范围裁决）。

    ``window``：``window``（最近7天，默认）/ ``day``（当日）/ ``month``（当月）——
    KPI 卡、TOP5 排行、迷你趋势与下钻均随所选口径切换。非法值 → 400。

    ``stage``（工段：SMT/PCM/PACK/整机）/ ``product_category``（产品分类：消费电子 /
    智能电源 / 储能电源 / 车载）为板级筛选，缺省 = 全部。制造四表没有这两列，用固定
    映射（产线前缀 / 型号）折出筛选维度——卡片值即所选范围的汇总，TOP5 与下钻同口径；
    测试直通率无产线维度，工段筛选对它不适用并回传 ``scope_note``。非法取值 → 400
    （fail-fast，不静默当「全部」）。

    真实链路聚合本地只读数据源；聚合内部全兜底，数据源故障时返回
    ``degraded:true`` 的骨架，**绝不 500**（前端据此降级演示数据）。
    """
    from ..services.dashboard import (
        MFG_PRODUCT_CATEGORIES, MFG_STAGES, build_manufacturing_dashboard,
    )

    stage = (stage or "").strip()
    product_category = (product_category or "").strip()
    if stage and stage not in MFG_STAGES:
        raise HTTPException(status_code=400, detail="未知工段筛选 stage")
    if product_category and product_category not in MFG_PRODUCT_CATEGORIES:
        raise HTTPException(status_code=400, detail="未知产品分类筛选 product_category")

    return _dashboard_payload(request, "manufacturing",
                              build_manufacturing_dashboard,
                              _checked_window(window),
                              stage=stage or None,
                              product_category=product_category or None)


@app.get("/api/v2/dashboard/quality")
def api_dashboard_quality(request: Request,
                          window: str = Query("window"),
                          stage: str = Query(""),
                          category: str = Query("")) -> dict[str, Any]:
    """品质看板：不良率 / 直通率 FPY / 来料批次合格率 / 客诉闭环率（按岗位范围裁决）。

    ``window`` 同制造看板（默认最近 7 天）。``stage``（工段：SMT/PCM/PACK/整机）/
    ``category``（缺陷类别：性能不良/外观不良）为板级筛选，缺省 = 全部；仅同源的
    ``quality_defect`` 指标（不良率 / 直通率及其 TOP5）随筛选重算（卡片值即所选范围
    的汇总），无该维度的进料端 / 客户端指标保持板级口径并回传 ``scope_note``。
    非法取值 → 400（fail-fast，不静默当「全部」）。

    另返回 ``pareto_fact`` 预聚合事实表，供前端柏拉图本地交互（日别/月别 + 类别/工段筛选）。
    """
    from ..services.dashboard import (
        QUALITY_CATEGORIES, QUALITY_STAGES, build_quality_dashboard,
    )

    stage = (stage or "").strip()
    category = (category or "").strip()
    if stage and stage not in QUALITY_STAGES:
        raise HTTPException(status_code=400, detail="未知工段筛选 stage")
    if category and category not in QUALITY_CATEGORIES:
        raise HTTPException(status_code=400, detail="未知缺陷类别筛选 category")

    return _dashboard_payload(request, "quality", build_quality_dashboard,
                              _checked_window(window),
                              stage=stage or None, category=category or None)


@app.get("/api/v2/dashboard/quality/team")
def api_dashboard_quality_team(request: Request,
                               stage: str = Query(""),
                               team: str = Query(""),
                               window: str = Query("window")) -> dict[str, Any]:
    """标准三率 2 级看板「点班组」弹窗：该班组三率拆解 + 不良原因归因。

    ``stage``：工段（SMT/PCM/PACK/整机，受 ``QUALITY_STAGES`` 约束，非法 → 400）；
    ``team``：班组名（如 ``PCM十五班``）；``window`` 同品质看板。

    与 ``/api/v2/dashboard/quality`` 的 ``standard_three_rates`` **同一套** ``_three_rates``，
    按工序/按日分解与原因归因都从同一批审核明细算出，前端弹窗数字与大盘可对账。
    班组在该口径内无审核记录时返回 ``found:false`` 骨架（200，不是 404——
    「本窗口没被审核」是有效结论，不该被当成接口错误）。
    """
    from ..services.dashboard import QUALITY_STAGES, build_std_team_detail

    stage = (stage or "").strip()
    team = (team or "").strip()
    if stage not in QUALITY_STAGES:
        raise HTTPException(status_code=400, detail="未知工段 stage")
    if not team:
        raise HTTPException(status_code=400, detail="缺少班组 team")
    return _dashboard_payload(request, "quality", build_std_team_detail,
                              _checked_window(window), stage=stage, team=team)


@app.get("/api/v2/dashboard/orders")
def api_dashboard_orders(request: Request,
                         window: str = Query("window")) -> dict[str, Any]:
    """订单履约看板：OTD / OTIF / 延期订单率 / 齐套率（按岗位范围裁决）。

    ``window``：``window``（最近7天，默认）/ ``day``（当日）/ ``month``（当月）——
    与制造综合看板同源口径，KPI 卡、TOP5 排行、迷你趋势、状态管道、风险订单与
    下钻均随所选口径切换。非法值 → 400。

    客户身份字段按岗位脱敏（``data_scope.yaml`` 的 mask）：对客与收款责任的
    岗位看到客户真名，物控/仓库/采购看到「客户 A / 客户 B」并**失去客户维度下钻键**。
    """
    from ..services.dashboard import build_order_dashboard

    return _dashboard_payload(request, "orders", build_order_dashboard,
                              _checked_window(window))


@app.get("/api/v2/dashboard/orders/stage")
def api_dashboard_orders_stage(request: Request,
                               stage: str = Query(""),
                               window: str = Query("window")) -> dict[str, Any]:
    """订单履约看板「状态管道 · 点环节」弹窗：该环节订单清单 + 汇总。

    ``stage``：管道环节（已下单/计划/采购/生产/质检/发货，与
    ``ORDER_STAGES`` 同源白名单，非法 → 400）；``window`` 同订单看板。

    与 ``/api/v2/dashboard/orders`` 的 ``pipeline`` 同源同口径（同一张
    ``order_fulfillment``、同一个 ``due_date`` 窗口），弹窗汇总与管道环节块
    可对账；客户身份字段按岗位脱敏（同 ``risk_orders[]`` 规则）。
    """
    from ..services.dashboard import ORDER_STAGES, build_order_stage_detail

    stage = (stage or "").strip()
    if stage not in ORDER_STAGES:
        raise HTTPException(status_code=400, detail="未知订单环节 stage")
    return _dashboard_payload(request, "orders", build_order_stage_detail,
                              _checked_window(window), stage=stage)


@app.get("/api/v2/dashboard/orders/detail")
def api_dashboard_orders_detail(request: Request,
                                order_no: str = Query("")) -> dict[str, Any]:
    """订单履约看板「风险订单 · 点行」弹窗：单张订单全字段明细。

    ``order_no``：订单号（缺省 → 400）。订单号不存在返回 ``found:false``
    骨架（200，不是 404）。客户身份字段按岗位脱敏，与风险订单清单同一套规则。
    """
    from ..services.dashboard import build_order_detail

    order_no = (order_no or "").strip()
    if not order_no:
        raise HTTPException(status_code=400, detail="缺少订单号 order_no")
    return _dashboard_payload(request, "orders", build_order_detail,
                              None, order_no=order_no)


@app.get("/api/v2/dashboard/orders/group")
def api_dashboard_orders_group(request: Request,
                               kpi: str = Query(""),
                               customer: str = Query(""),
                               level: str = Query(""),
                               model: str = Query(""),
                               window: str = Query("window")) -> dict[str, Any]:
    """订单履约 2 级看板「点行」弹窗：单分组（客户·等级 / 型号）的订单清单。

    ``kpi``：``DRILL_DEFS`` 中 ``screen=orders`` 的四个键（otd / otif /
    delay_rate / kit_rate，白名单外 → 400）；分组维度按指标声明通用取用——
    客户维指标传 ``customer`` + ``level``，齐套率传 ``model``（全部为空 → 400）。

    与 ``/api/v2/dashboard/drill``（screen=orders）的 L2 **同一套** ``DRILL_DEFS``
    聚合口径：弹窗头部指标值 / 分子分母与 2 级看板该行逐字段一致，可对账；
    数据范围裁决与客户字段脱敏同订单看板（``orders[].customer`` 规则），
    与班组明细弹窗（``/dashboard/quality/team``）同一模式。
    """
    from ..services.dashboard import (
        DRILL_DEFS, build_order_group_detail, entity_param_names,
    )

    kpi = (kpi or "").strip()
    defn = DRILL_DEFS.get(kpi)
    if defn is None or defn["screen"] != "orders":
        raise HTTPException(status_code=400, detail="未知订单指标 kpi")
    vals = {"customer": (customer or "").strip(),
            "level": (level or "").strip(), "model": (model or "").strip()}
    names = entity_param_names(kpi)
    if not any(vals.get(n) for n in names):
        raise HTTPException(status_code=400,
                            detail=f"缺少分组维度参数: {'/'.join(names)}")
    entity = {n: vals.get(n, "") for n in names}
    return _dashboard_payload(request, "orders", build_order_group_detail,
                              _checked_window(window), kpi_key=kpi, entity=entity)


@app.get("/api/v2/dashboard/spc")
def api_dashboard_spc(request: Request,
                      window: str = Query("window"),
                      stage: str = Query(""),
                      product_category: str = Query("")) -> dict[str, Any]:
    """SPC 过程能力看板：逐特性 Cp/Cpk/Pp/Ppk 判定 + 超差率 + 不良柏拉图（按岗位范围裁决）。

    能力指数由确定性内核 ``core/spc``（双西格玛口径）计算；缺测量值时逐特性标
    ``data_gap``，聚合内部全兜底，**绝不 500**（前端据此降级演示数据）。

    板级筛选（与制造/品质看板同源）：
    - ``window``：``window``（最近7天，默认）/ ``day``（当日）/ ``month``（当月），
      非法值 → 400；
    - ``stage``：工段，按 ``MFG_STAGES`` 白名单裁定，非法值 → 400；
    - ``product_category``：产品分类，按 ``MFG_PRODUCT_CATEGORIES`` 白名单裁定，
      非法值 → 400（**不静默当「全部」**）。
    """
    from ..services.dashboard import (
        MFG_PRODUCT_CATEGORIES, MFG_STAGES, build_spc_dashboard,
    )

    stage = (stage or "").strip()
    product_category = (product_category or "").strip()
    if stage and stage not in MFG_STAGES:
        raise HTTPException(status_code=400, detail="未知工段 stage")
    if product_category and product_category not in MFG_PRODUCT_CATEGORIES:
        raise HTTPException(status_code=400, detail="未知产品分类 product_category")

    return _dashboard_payload(request, "spc", build_spc_dashboard,
                              _checked_window(window), stage=stage or None,
                              product_category=product_category or None)


@app.get("/api/v2/dashboard/spc/capability")
def api_dashboard_spc_capability(request: Request,
                                 characteristic: str = Query(""),
                                 window: str = Query("window"),
                                 stage: str = Query(""),
                                 product_category: str = Query("")) -> dict[str, Any]:
    """SPC 2 级看板「点特性」弹窗：该特性的能力拆解 + 逐日越限 + 原始记录。

    与 L1 看板**同源**（同一 ``_spc_caps_from_rows`` / 同一窗口 / 同一维度筛选）
    → 弹窗的 cpk / 判定 / 样本数与 L1 卡片、2 级行逐字段一致、可对账。

    走 ``_dashboard_payload(request, "spc", ...)`` → 自动继承 ``data_scope.yaml``
    的 ``spc:`` 块（9 角色 allow_roles）→ 无权岗位 403 fail-closed，**绝不降级
    200 空数据**。该特性无记录时返回 200 + ``found:false`` 骨架（**不 404**）。
    """
    from ..services.dashboard import (
        MFG_PRODUCT_CATEGORIES, MFG_STAGES, build_spc_capability_detail,
    )

    characteristic = (characteristic or "").strip()
    if not characteristic:
        raise HTTPException(status_code=400, detail="缺少特性 characteristic")
    stage = (stage or "").strip()
    product_category = (product_category or "").strip()
    if stage and stage not in MFG_STAGES:
        raise HTTPException(status_code=400, detail="未知工段 stage")
    if product_category and product_category not in MFG_PRODUCT_CATEGORIES:
        raise HTTPException(status_code=400, detail="未知产品分类 product_category")

    return _dashboard_payload(request, "spc", build_spc_capability_detail,
                              _checked_window(window), characteristic=characteristic,
                              stage=stage or None, product_category=product_category or None)


@app.get("/api/v2/dashboard/spc/records")
def api_dashboard_spc_records(request: Request,
                              window: str = Query("window"),
                              stage: str = Query(""),
                              product_category: str = Query(""),
                              characteristic: str = Query(""),
                              day: str = Query(""),
                              over_only: bool = Query(False)) -> dict[str, Any]:
    """SPC 测量记录弹窗：越限明细（over_only=true）/ 单日明细（day=YYYY-MM-DD）。

    与 L1 / 2 级 / 能力弹窗**同源**（同一窗口与维度筛选），记录数与看板
    「越限条数 / 测量条数」可对账。走 ``_dashboard_payload(request, "spc", ...)``
    → 继承 ``data_scope.yaml`` 的 ``spc:`` 块 → 无权 403 fail-closed。
    无记录返回 200 + ``found:false`` 骨架（不 404）。
    """
    from ..services.dashboard import (
        MFG_PRODUCT_CATEGORIES, MFG_STAGES, build_spc_measurement_records,
    )

    stage = (stage or "").strip()
    product_category = (product_category or "").strip()
    if stage and stage not in MFG_STAGES:
        raise HTTPException(status_code=400, detail="未知工段 stage")
    if product_category and product_category not in MFG_PRODUCT_CATEGORIES:
        raise HTTPException(status_code=400, detail="未知产品分类 product_category")
    day = (day or "").strip()
    if day and not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")

    return _dashboard_payload(request, "spc", build_spc_measurement_records,
                              _checked_window(window), characteristic=characteristic,
                              day=day, over_only=bool(over_only),
                              stage=stage or None,
                              product_category=product_category or None)


@app.get("/api/v2/alerts")
def api_alerts(request: Request) -> dict[str, Any]:
    """预警中心：五类预警（SPC越限 / 缺料 / 订单临期 / 设备告警 / 现场推送）。

    - 令牌裁决：无令牌 401（业务数据不接受匿名读取）。预警中心**全员可见**
      （异常可能来自任何岗位），故不走 ``data_scope.yaml`` 的看板白名单；
    - 同源可对账：SPC 越限复用 SPC 看板能力判定、订单临期复用订单看板风险订单清单、
      缺料复用统一注册查询 ``material_shortage`` —— 与各看板 / 数据速查逐字段一致；
    - **现场推送**：客诉 8D 场景 ``dispatch_site`` 归因到责任工段的推送队列
      （治理库 ``site_board_push``），状态即现场真实回写（pushed=待处置 / acked=处置中）；
      现场可就地 ``ack`` / ``feedback``（见 ``/api/v2/site-board`` 两个回写端点）。
      该类不读业务只读库，业务库故障时仍如实展示；
    - 客户字段脱敏：订单预警按 ``orders``、现场推送按 ``site_board`` 的同一套规则脱敏
      （非对客岗位看到「客户 A」）——预警全员可见不等于客户真名全员可见；
    - 等级 / 状态为**可复算的机械规则**（随载荷回传 ``rules``）：库里没有预警处置
      生命周期表，就不编造处置步骤与预计复线时间；任一数据源失败返回
      ``degraded:true`` 骨架，**绝不 500**（前端据此回落演示快照）。
    """
    ctx = _require_identity(request)
    from ..services.alerts import build_alerts

    registry = get_orchestrator().runtime.data_registry
    data = build_alerts(registry, list(ctx["roles"]))
    return {"code": 0, "data": data, "message": "ok"}


@app.get("/api/v2/dashboard/drill")
def api_dashboard_drill(request: Request, screen: str = Query(...),
                        kpi: str = Query(...), step: str = Query(""),
                        station: str = Query(""),
                        defect_type: str = Query(""),
                        window: str = Query("window"),
                        stage: str = Query(""),
                        category: str = Query(""),
                        prod_category: str = Query("")) -> dict[str, Any]:
    """看板下钻（只读）：无实体 = L2；带实体 = L3；再带 ``step`` = L4；再带 ``station`` = L5。

    - ``screen``：``manufacturing`` / ``quality`` / ``orders`` / ``spc``；``kpi``：DRILL_DEFS 白名单键；
    - 实体参数按指标维度通用取用（``entity_cols`` / ``entity`` kind 白名单），
      例如 ``line``+``model`` / ``wo`` / ``model`` / ``customer``+``level`` /
      ``supplier`` / ``test_type`` / ``warehouse`` / ``wf`` 等；
    - 层级判定：无 entity→L2、有 entity→L3、``+step``→L4、``+step&station``→L5；
      跳级（有 step 无 entity、有 station 无 step）或指标不支持该层级 → 400；
    - ``window``：``window``（最近7天，默认）/ ``day`` / ``month`` 与板级口径同源；
      ``all`` 仅对不良类型下钻（``defect_type`` + pareto 路径）放行，可叠加
      ``stage`` / ``category`` / ``prod_category``（与质量柏拉图当前
      「产品分类/类别/工段」筛选一致）；
    - 制造看板四个 KPI 声明了板级筛选（``scope``）：``stage`` / ``prod_category``
      与看板同口径作用于下钻；品质「不良率 / 直通率」声明了 ``q_scope``：
      ``stage`` / ``category`` 与品质看板 L1 卡片同口径作用于下钻（进 2 级看板换筛选
      就地重取，不回退看板首页）；两类都未声明的指标忽略这些参数，行为不变；
    - ``defect_type``：仅白名单 ``DEFECT_TYPE_DRILLS``（services/dashboard.py）内的指标
      支持——质量看板不良率走「L1 累计不良柏拉图 → L2 按产线 · 型号分解」，
      且不与实体/工序/工位组合（组合 → 400）；spc_pareto 作为已声明实体维度下钻；
      白名单外一律 400；
    - 聚合内部全兜底，数据源故障时返回 ``degraded:true``，**绝不 500**。
    """
    from ..services.dashboard import (
        DEFECT_TYPE_DRILLS, DRILL_DEFS, MFG_PRODUCT_CATEGORIES, MFG_STAGES,
        QUALITY_CATEGORIES, QUALITY_STAGES,
        build_drill, build_pareto_drill, entity_param_names,
    )

    defn = DRILL_DEFS.get(kpi)
    if defn is None or defn["screen"] != screen:
        raise HTTPException(status_code=400, detail="未知看板指标")

    # 下钻必须与看板同源裁决：否则「看板进不去、下钻却能拿」就是一条后门。
    # ``screen`` 与 data_scope.yaml 的看板 key 同名，直接复用。
    _ctx = _scope_guard(request, screen)

    # 实体参数：按指标声明的维度名从 query 通用取用；全部为空表示不锁定实体（L2）
    qp = request.query_params
    vals = {p: (qp.get(p) or "").strip() for p in entity_param_names(kpi)}
    entity: Optional[dict[str, str]] = vals if any(vals.values()) else None

    defect_type = (defect_type or "").strip()
    # 时间口径：``window|day|month`` 与板级一致；``all`` 仅对不良类型下钻放行
    # （质量柏拉图交互组件的数据集在 7 天窗口外，需要不限窗口的类型下钻）。
    window = (window or "window").strip()
    stage = (stage or "").strip()
    category = (category or "").strip()
    prod_category = (prod_category or "").strip()
    if window not in _DRILL_WINDOW_KEYS:
        raise HTTPException(status_code=400, detail="未知时间口径 window")
    # 板级筛选是否对该下钻指标生效，由 defn 的声明裁定（唯一事实源）：
    #   制造看板四指标声明 ``scope`` → 吃「工段 / 产品分类」；
    #   品质「不良率 / 直通率」声明 ``q_scope`` → 吃「工段 / 类别」（与 L1 卡片同源）。
    # 生效者必须白名单裁定（否则常量映射取值 KeyError / 非白名单值被静默当「全部」），
    # 未声明者不看这两个参数（传了也被忽略，与改动前逐字段一致）。
    if defn.get("scope"):
        if stage and stage not in MFG_STAGES:
            raise HTTPException(status_code=400, detail="未知工段筛选 stage")
        if prod_category and prod_category not in MFG_PRODUCT_CATEGORIES:
            raise HTTPException(status_code=400,
                                detail="未知产品分类筛选 product_category")
    if defn.get("q_scope"):
        if stage and stage not in QUALITY_STAGES:
            raise HTTPException(status_code=400, detail="未知工段筛选 stage")
        if category and category not in QUALITY_CATEGORIES:
            raise HTTPException(status_code=400, detail="未知类别筛选 category")
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
            data = build_pareto_drill(registry, screen, kpi, defect_type,
                                      stage=stage or None,
                                      category=category or None,
                                      prod_category=prod_category or None,
                                      window=window)
            data, masked = get_data_scope().apply(screen, data, _ctx["roles"], kpi=kpi)
            return {"code": 0, "data": data, "message": "ok",
                    "scope": {"dashboard": screen, "roles": list(_ctx["roles"]),
                              "masked": masked}}
        if dt_kind != "entity":
            raise HTTPException(status_code=400,
                                detail="该指标不支持不良类型下钻")

    # window=all 只对不良类型下钻放行，其余 KPI/实体下钻仍只接受三档板级口径
    if window == "all":
        raise HTTPException(status_code=400,
                            detail="window=all 仅支持不良类型下钻")
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
                       station or None, window,
                       stage=stage or None,
                       product_category=prod_category or None,
                       category=category or None)
    data, masked = get_data_scope().apply(screen, data, _ctx["roles"], kpi=kpi)
    return {"code": 0, "data": data, "message": "ok",
            "scope": {"dashboard": screen, "roles": list(_ctx["roles"]),
                      "masked": masked}}


# ---------- 现场看板（客诉 → 责任工段推送队列：读 + 现场回写） ----------


class SiteBoardAckRequest(BaseModel):
    """现场接收请求：备注可选（接收动作本身已按令牌身份留痕）。"""

    note: str = ""


class SiteBoardFeedbackRequest(BaseModel):
    """现场处置闭环请求：处置结论**必填**（空理由不闭环，与驳回同纪律）。"""

    feedback: str = ""


def _site_board_payload(ctx: dict[str, Any], rows: list[dict[str, Any]],
                        stage: str = "") -> dict[str, Any]:
    """现场看板统一信封：按岗位脱敏（客户名）+ 回写 scope 凭据。

    返回结构与读接口同构（``{"stage": …, "rows": […]}}``），所以三个端点的脱敏
    走**同一条**规则（``site_board.mask.rows[].customer``）——写接口返回的单行
    也不许绕开脱敏（客户名藏在响应里等于没脱敏）。
    """
    data, masked = get_data_scope().apply(
        "site_board", {"stage": stage, "rows": list(rows)}, ctx["roles"])
    return {"code": 0, "data": data, "message": "ok",
            "scope": {"dashboard": "site_board", "roles": list(ctx["roles"]),
                      "masked": masked}}


@app.get("/api/v2/site-board")
def api_site_board(request: Request,
                   stage: str = Query(""),
                   status: str = Query("")) -> dict[str, Any]:
    """现场看板推送队列（客诉处置令 / 8D 工单通知）。

    ``stage``（工段：SMT/PCM/PACK/整机）缺省 = 全部工段、``status``
    （pushed / acked / closed）缺省 = 全部状态；非法取值 → 400（fail-fast，
    不静默当「全部」）。按岗位裁决：无令牌 401、岗位不在范围 403。
    """
    from ..services import site_board as site_board_svc

    ctx = _scope_guard(request, "site_board")
    stage_key = (stage or "").strip()
    if stage_key and stage_key not in site_board_svc.STAGES:
        raise HTTPException(status_code=400, detail="未知工段筛选 stage")
    status_key = (status or "").strip()
    if status_key and status_key not in site_board_svc.STATUS_TRANSITIONS:
        raise HTTPException(status_code=400, detail="未知状态筛选 status")

    rows = site_board_svc.list_pushes(stage=stage_key or None,
                                      status=status_key or None)
    return _site_board_payload(ctx, rows, stage=stage_key)


@app.post("/api/v2/site-board/{push_id}/ack")
def api_site_board_ack(request: Request, push_id: str,
                       req: SiteBoardAckRequest) -> dict[str, Any]:
    """现场接收推送（``pushed → acked``）：接收人取自令牌身份，备注可选。"""
    from ..services import site_board as site_board_svc

    ctx = _scope_guard(request, "site_board")
    actor = str(ctx.get("name") or ctx.get("user_id") or "")
    try:
        row = site_board_svc.ack(push_id, actor=actor,
                                 note=(req.note or "").strip())
    except site_board_svc.PushNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except site_board_svc.InvalidSiteBoardTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _site_board_payload(ctx, [row], stage=str(row.get("stage") or ""))


@app.post("/api/v2/site-board/{push_id}/feedback")
def api_site_board_feedback(request: Request, push_id: str,
                            req: SiteBoardFeedbackRequest) -> dict[str, Any]:
    """现场处置闭环（``acked → closed``）：处置结论必填，空则 400。"""
    from ..services import site_board as site_board_svc

    ctx = _scope_guard(request, "site_board")
    text = (req.feedback or "").strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail="现场闭环需填写处置结论（已采取的措施、依据与结果）")
    actor = str(ctx.get("name") or ctx.get("user_id") or "")
    try:
        row = site_board_svc.close(push_id, actor=actor, feedback=text)
    except site_board_svc.PushNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except site_board_svc.InvalidSiteBoardTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _site_board_payload(ctx, [row], stage=str(row.get("stage") or ""))


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


# ---------- 案例助手（Case Assistant）：/api/v2/cases/* ----------
# 说明：本段是**追加**的路由，不改动任何既有路由实现。
# 统一约定：
#   - 身份只来自 Authorization: Bearer（复用既有 IdentityCodec，载荷扩展 tv/pv/维度）；
#   - 错误码：401 CASE_TOKEN_*，403 CASE_ACTION_DENIED，404 CASE_NOT_FOUND_OR_NOT_VISIBLE；
#   - 权限判定全部在服务层（CaseSearchService / AclCompiler / AclEvaluator），路由只做映射。

from ..contracts.case_acl import Principal as _CasePrincipal  # noqa: E402
from ..security.case_rbac import get_case_rbac as _get_case_rbac  # noqa: E402
from ..security import case_rate_limit  # noqa: E402
from ..services.case_ingest import (  # noqa: E402
    CaseAlreadyActive,
    IngestError,
    OntologyCodeUnknown,
    RedactionFailed,
    SourceTicketRequired,
)
from ..services.case_runtime import get_case_runtime as _get_case_runtime  # noqa: E402
from ..services.case_search import source_ticket_ref  # noqa: E402
from ..services.principal_resolver import (  # noqa: E402
    CaseTokenError,
    CaseTokenInvalid,
    get_principal_resolver as _get_principal_resolver,
)


class CaseSessionRequest(BaseModel):
    """演示身份选择请求（仅 ``CASE_DEMO_MODE=true`` 时可用）。"""

    role: str = ""
    roles: Optional[list[str]] = None
    dept: str = ""
    plant: str = "P1"
    employment_type: str = "employee"
    max_classification_rank: int = 1
    tenant: str = ""
    user_id: str = ""
    name: str = ""
    line_ids: Optional[list[str]] = None
    customer_ids: Optional[list[str]] = None


class CaseSearchRequest(BaseModel):
    """检索请求（**刻意没有 filter 字段**：调用方无法传自定义过滤条件）。"""

    query: str = ""
    k: int = 5
    action: str = "query"


class CaseAskRequest(BaseModel):
    question: str = ""
    k: int = 5
    action: str = "read"


class CaseCompareRequest(BaseModel):
    """三方对照请求（演示专用；PRD FR-06）。

    **没有 filter 字段**：三组变体的可见范围完全相同（同一主体、同一安全内核），
    对照的只能是"检索智能"，绝不能是"能看到多少"。
    """

    question: str = ""
    k: int = 5
    action: str = "read"


class CaseCiteRequest(BaseModel):
    target_ticket_type: str = "8d"
    target_ticket_id: str = ""
    note: str = ""
    # ★ 引用必须指向**用户点的那一条 chunk**，否则"可溯源"是假的：
    #   前端从命中卡片带回 chunk_type / content_version，后端据此产出精确三元组。
    chunk_type: str = ""
    content_version: int = 0


class CaseAccessRequestModel(BaseModel):
    reason: str = ""
    scope: str = "grant"


class CaseAccessDecision(BaseModel):
    approve: bool = False
    reason: str = ""


class CaseIngestRequest(BaseModel):
    draft: dict[str, Any] = {}


def _mask_user(user_id: str) -> str:
    """用户 id 脱敏展示（``u-qa`` → ``u-q*``）；审批人不需要看完整 id。"""
    raw = str(user_id or "")
    if len(raw) <= 3:
        return raw
    return raw[:3] + "*" * min(4, len(raw) - 3)


def _demo_warnings() -> list[str]:
    """演示态风险清单（B5）：必须"显式且刺眼"，不能只躺在日志里。

    文案纪律（2026-10-18 用户反馈优化）：主提示说人话，环境变量名退到括号里
    做运维锚点——普通用户不需要懂 CASE_DEMO_MODE，运维需要能查到变量。
    """
    warnings: list[str] = []
    secret = str(getattr(settings, "AUTH_SECRET", "") or "").strip()
    demo_mode = bool(getattr(settings, "CASE_DEMO_MODE", True))
    if not secret:
        if demo_mode:
            warnings.append(
                "服务未设置安全密钥（AUTH_SECRET）：重启后所有登录状态会失效，"
                "需要重新选择岗位（演示环境可忽略）")
        else:
            warnings.append(
                "服务未设置安全密钥（AUTH_SECRET），且未开启演示模式："
                "当前无法签发登录令牌，请联系管理员配置")
    if demo_mode:
        warnings.append(
            "当前为演示模式（CASE_DEMO_MODE=true）：在页面上选择岗位即可登录"
            "（无需密码），仅用于演示环境")
    if demo_mode and bool(getattr(settings, "CASE_ACCESS_AUTO_APPROVE_DEMO", True)):
        warnings.append(
            "访问申请自动审批已开启（仅演示）：提交跨部门访问申请后，"
            "将弹窗模拟职能部门审批并通过；正式环境由职能部门负责人在"
            "「访问申请待办」页审批，系统记录会区分演示审批与正式审批")
    return warnings


def _case_runtime():
    """案例运行时（懒加载单例；总开关关闭 → 503）。"""
    if not bool(getattr(settings, "CASE_ENABLED", True)):
        raise HTTPException(status_code=503,
                            detail={"code": "CASE_DISABLED",
                                    "message": "案例助手已被 CASE_ENABLED=false 关闭"})
    return _get_case_runtime()


def _case_principal(request: Request) -> _CasePrincipal:
    """令牌 → 权威主体（401 fail-closed：老令牌/pv 失效/身份为空）。"""
    token = _bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail={
            "code": "CASE_TOKEN_MISSING", "message": "缺少访问令牌：请先选择岗位登录"})
    try:
        return _case_runtime().resolver.from_token(token)
    except CaseTokenError as exc:
        raise HTTPException(status_code=401,
                            detail={"code": exc.code, "message": str(exc)}) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail={
            "code": "CASE_TOKEN_INVALID", "message": str(exc)}) from exc


def _case_guard_rate_limit(request: Request, principal: _CasePrincipal,
                           action: str) -> None:
    """B8：案例读取类入口的限流守卫（超限 429）。

    背景：B1 让"不存在"与"存在但无权"两条路径做等价工作，代价是**每个 404
    都会写一行审计并 commit**。于是外部扫 ``QA-0001..QA-9999`` 能往审计链里
    灌 1 万行 + 1 万次 fsync。

    治理手段选限流而非审计采样：少写一条审计，B1 消掉的时间差又会回来
    （采样 = 部分 404 更快），等于治一个病添一个病。限流在入口掐断探测流量，
    可追溯性不受影响。
    """
    if not case_rate_limit.check_case_rate_limit(
            case_rate_limit.case_rate_limit_bucket(
                user_id=principal.user_id,
                client_ip=request.client.host if request.client else "")):
        return
    logger.warning("[case-api] 限流拦截：actor=%s action=%s", principal.user_id, action)
    raise HTTPException(status_code=429, detail={
        "code": "CASE_RATE_LIMITED",
        "message": "案例检索/读取过于频繁，请稍后再试"
                   "（限流用于阻断批量探测，不影响正常作业节奏）"})


def _case_error(exc: Exception) -> HTTPException:
    """案例域异常 → HTTP 映射（保持与架构 §9.2 错误码一致）。"""
    if isinstance(exc, SourceTicketRequired):
        return HTTPException(status_code=409, detail={
            "code": "CASE_SOURCE_TICKET_REQUIRED", "message": str(exc)})
    if isinstance(exc, CaseAlreadyActive):
        # B2：已发布案例不得被重复 ingest 覆盖（否则会被静默踢出索引）
        return HTTPException(status_code=409, detail={
            "code": "CASE_ALREADY_ACTIVE", "message": str(exc)})
    if isinstance(exc, RedactionFailed):
        # B3：脱敏复检失败 → 409 CASE_REDACTION_LEAK（不降级放行）
        return HTTPException(status_code=409, detail={
            "code": "CASE_REDACTION_LEAK", "message": str(exc),
            "problems": sorted(exc.problems)})
    if isinstance(exc, OntologyCodeUnknown):
        return HTTPException(status_code=422, detail={
            "code": "CASE_ONTOLOGY_UNKNOWN_CODE", "message": str(exc)})
    if isinstance(exc, CaseTokenInvalid):
        return HTTPException(status_code=401, detail={
            "code": exc.code, "message": str(exc)})
    if isinstance(exc, IngestError):
        return HTTPException(status_code=400, detail={
            "code": "CASE_INGEST_INVALID", "message": str(exc)})
    return HTTPException(status_code=400, detail={
        "code": "CASE_BAD_REQUEST", "message": str(exc)})


def _search_or_raise(response) -> dict[str, Any]:
    """把"权限拒绝"翻译成 401/403；"合法空结果"照常 200 返回。"""
    if response.denied_code:
        status = 401 if response.denied_code.startswith("CASE_TOKEN") or \
            response.denied_code == "CASE_PRINCIPAL_REVOKED" else 403
        raise HTTPException(status_code=status, detail={
            "code": response.denied_code, "message": response.denied_reason})
    return response.to_dict()


@app.post("/api/v2/cases/session")
def api_case_session(req: CaseSessionRequest) -> dict[str, Any]:
    """演示身份选择 → 签发 ``tv=2`` 令牌（仅 ``CASE_DEMO_MODE=true``）。

    生产环境此端点必须关闭（``CASE_DEMO_MODE=false``），身份由 SSO/IdP 提供；
    服务端只做"角色归一化 + 载荷组装"，客户端无法自行发明角色或维度。

    B5：关闭时返回 **503 + ``CASE_DEMO_DISABLED``**（不是 403）。
    理由：403 意味着"这个身份不被允许"，会让人以为是权限配置问题；
    而事实是"这条通道在部署形态上就不该存在"。503 让运维一眼看出是部署开关。
    """
    demo_mode = bool(getattr(settings, "CASE_DEMO_MODE", True))
    secret = str(getattr(settings, "AUTH_SECRET", "") or "").strip()
    if not demo_mode:
        raise HTTPException(status_code=503, detail={
            "code": "CASE_DEMO_DISABLED",
            "message": ("演示身份选择已关闭（CASE_DEMO_MODE=false），请走 SSO 登录"
                        + ("" if secret else "；且 AUTH_SECRET 未配置，本服务不会签发任何令牌"))})
    resolver = _get_principal_resolver()
    candidates: list[str] = list(req.roles or [])
    if not candidates and req.role:
        candidates = [req.role]
    try:
        payload = resolver.build_payload(
            user_id=req.user_id or "",
            roles=candidates,
            dept=req.dept,
            name=req.name,
            tenant=req.tenant or str(getattr(settings, "CASE_DEFAULT_TENANT", "T001")),
            plant=req.plant,
            line_ids=req.line_ids or (),
            customer_ids=req.customer_ids or (),
            max_classification_rank=req.max_classification_rank,
            employment_type=req.employment_type,
        )
    except CaseTokenError as exc:
        raise HTTPException(status_code=400, detail={
            "code": exc.code, "message": str(exc)}) from exc
    if not payload.get("sub"):
        payload["sub"] = f"u-{payload['roles'][0]}"
    try:
        token, issued = get_codec().issue(
            user_id=payload["sub"], name=payload["name"], dept=payload["dept"],
            roles=payload["roles"], tenant=payload["tenant"],
            plant=payload["plant"],
            line_ids=payload["line_ids"], project_ids=payload["project_ids"],
            customer_ids=payload["customer_ids"],
            supplier_ids=payload["supplier_ids"],
            product_families=payload["product_families"],
            max_classification_rank=payload["max_classification_rank"],
            employment_type=payload["employment_type"],
            token_version=payload["tv"], principal_version=payload["pv"],
        )
    except AuthError as exc:  # pragma: no cover - 角色非空时不会触发
        raise HTTPException(status_code=400, detail={
            "code": "CASE_TOKEN_INVALID", "message": str(exc)}) from exc
    principal = _get_case_runtime().resolver.from_payload(issued, verify_pv=False)
    return {
        "code": 0,
        "data": {
            "token": token,
            "expires_in": get_codec().ttl_s,
            "principal": {
                "user_id": principal.user_id,
                "tenant": principal.tenant,
                "dept": principal.dept,
                "roles": list(principal.roles),
                "plant": principal.plant,
                "employment_type": principal.employment_type,
                "max_classification_rank": principal.max_classification_rank,
                "token_version": principal.token_version,
                "principal_version": principal.principal_version,
                "principal_tokens": list(principal.principal_tokens),
            },
            "demo_mode": True,
        },
        "message": "ok",
    }


@app.get("/api/v2/cases/meta")
def api_case_meta(request: Request) -> dict[str, Any]:
    """案例域元信息：分类、受控词表、动作矩阵、当前主体可见维度。"""
    principal = _case_principal(request)
    runtime = _case_runtime()
    from ..contracts.ontology import get_ontology_registry

    registry = get_ontology_registry()
    return {
        "code": 0,
        "data": {
            "categories": [
                {"key": "quality_case", "label": "质量案例",
                 "subcategories": [
                     {"key": "customer_complaint", "label": "客诉案例"},
                     {"key": "material_defect", "label": "材料不良案例"},
                     {"key": "process_defect", "label": "制程不良案例"},
                     {"key": "equipment_failure", "label": "设备故障案例"},
                 ]},
                {"key": "role_case", "label": "岗位经验案例（本职能域可见）",
                 "subcategories": [
                     {"key": "process_experience", "label": "工艺经验"},
                     {"key": "equipment_experience", "label": "设备经验"},
                     {"key": "quality_experience", "label": "质量经验"},
                     {"key": "rd_experience", "label": "研发经验"},
                 ]},
            ],
            "ontology": registry.to_meta(),
            "ontology_count": registry.count(),
            # 演示开关透出给前端：前端**只渲染**服务端允许的东西，
            # 不自己决定"要不要开放三方对照"（否则生产环境会被前端打开）。
            "demo_compare": bool(getattr(settings, "CASE_DEMO_MODE", False)
                                 and getattr(settings, "CASE_DEMO_COMPARE", False)),
            "answerer": str(getattr(settings, "CASE_ANSWERER", "llm")),
            "actions": runtime.describe_permissions(),
            "rbac": _get_case_rbac().matrix(),
            "principal": {
                "user_id": principal.user_id,
                "tenant": principal.tenant,
                "dept": principal.dept,
                "plant": principal.plant,
                "roles": list(principal.roles),
                "employment_type": principal.employment_type,
                "max_classification_rank": principal.max_classification_rank,
                "principal_tokens": list(principal.principal_tokens),
                # tv/pv 必须可见：演示时要能一眼看出"令牌是 v2、主体版本号是多少"，
                # 撤权重放（pv 失效）才会 401 这件事才讲得清。
                "token_version": principal.token_version,
                "principal_version": principal.principal_version,
            },
            "health": runtime.health(),
            # B5：演示态必须"显式且刺眼"。AUTH_SECRET 未配置时令牌在进程重启后
            # 全部失效——这不能只写进日志，必须让演示现场看见。
            "demo_warnings": _demo_warnings(),
            # R2：hidden_count 的口径写在 meta 里，避免"条"被理解成 chunk 数
            "hidden_count_unit": "case",
            "hidden_count_note": "hidden_count 是**案例数**（按 case_id 去重），不是 chunk 数",
        },
        "message": "ok",
    }


@app.get("/api/v2/cases/stats")
def api_case_stats(request: Request) -> dict[str, Any]:
    """案例域统计（顶部数字卡片）：口径与检索同源，fail-closed。

    案例计数复用 ``CaseSearchService.stats``（与 ``/cases/search`` 同一套
    filter 编译 + 二次授权 + 披露判定）——卡片数字与检索结果必须对得上，
    否则现场一追问就穿。申请相关计数（我的申请 / 待我审批）复用
    ``/cases/access/requests`` 的同一查询（``list_for_requester`` /
    ``list_pending(dept=...)``），不另写 SQL。

    失败语义：任何异常按空统计返回（``code=0`` + 全 0），**不 500**——
    统计卡片是增强信息，不该让整页报错；前端对空数据安静降级。
    """
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "search")
    runtime = _case_runtime()
    try:
        data = runtime.search.stats(principal, action="query")
    except Exception as exc:  # noqa: BLE001 - 统计失败降级为空，绝不带崩主链路
        logger.warning("[cases/stats] 统计降级为空: %s", exc)
        data = {"visible_total": 0, "restricted_total": 0,
                "restricted_truncated": False,
                "by_category": {}, "by_rank": {}, "by_status": {}}
    # 申请相关计数：与申请列表端点同一查询，避免两处口径漂移
    try:
        runtime.access.reap_expired()
        mine = runtime.access.list_for_requester(principal.user_id)
        my_requests: dict[str, int] = {}
        for row in mine:
            key = str(row.get("status") or "unknown")
            my_requests[key] = my_requests.get(key, 0) + 1
        data["my_requests"] = my_requests
        data["my_requests_total"] = len(mine)
        data["pending_for_me"] = len(
            runtime.access.list_pending(dept=principal.dept))
    except Exception as exc:  # noqa: BLE001 - 同上：申请计数失败只缺这一块
        logger.warning("[cases/stats] 申请计数降级: %s", exc)
        data.setdefault("my_requests", {})
        data.setdefault("my_requests_total", 0)
        data.setdefault("pending_for_me", 0)
    return {"code": 0, "data": data, "message": "ok"}


@app.post("/api/v2/cases/search")
def api_case_search(req: CaseSearchRequest, request: Request) -> dict[str, Any]:
    """检索（filter 在召回前生效 + 返回前二次授权；结果含 hidden_count）。"""
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "search")
    runtime = _case_runtime()
    k = max(1, min(int(req.k or 5), int(getattr(settings, "CASE_K_MAX", 20))))
    response = runtime.search.search(principal, req.query, action=req.action, k=k)
    return {"code": 0, "data": _search_or_raise(response), "message": "ok"}


@app.post("/api/v2/cases/ask")
def api_case_ask(req: CaseAskRequest, request: Request) -> dict[str, Any]:
    """带引用的问答（每条结论必须挂 (case_id, chunk_id, content_version)）。"""
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "ask")
    runtime = _case_runtime()
    k = max(1, min(int(req.k or 5), int(getattr(settings, "CASE_K_MAX", 20))))
    result = runtime.qa.answer(principal, req.question, k=k, action=req.action)
    if result.search is not None and result.search.denied_code:
        _search_or_raise(result.search)
    return {"code": 0, "data": result.to_dict(), "message": "ok"}


@app.get("/api/v2/cases/ontology/coverage")
def api_case_ontology_coverage(request: Request) -> dict[str, Any]:
    """**语料 ↔ 本体对齐度**（PRD FR-08：本体覆盖率看板）。

    治理视图用：告诉质量体系"哪些编码还没登记本体"——覆盖率低的地方，
    术语归一会落空、关系扩展也扩不出召回词（这正是"换更强模型也救不了"的那类问题）。

    只读、只给码不给案例内容；不参与任何权限判定（它是治理面，不是检索面）。
    """
    _case_principal(request)
    runtime = _case_runtime()
    from ..services.case_relation import ontology_coverage
    from ..contracts.ontology import get_ontology_registry
    from ..services.case_store import VISIBLE_STATUS

    data = ontology_coverage(runtime.store.list_cases(status=VISIBLE_STATUS),
                             ontology=get_ontology_registry())
    runtime.audit.append(actor=_case_principal(request).user_id,
                         action="case.ontology.coverage",
                         detail={"cases": data["cases"]})
    return {"code": 0, "data": data, "message": "ok"}


# ---------- FR-09 本体缺口登记（申请 / 列表 / 裁决） ----------


class OntologyGapRequestModel(BaseModel):
    """登记申请（**没有词表写入入口**：批准 ≠ 写词表，需质量体系人工登记）。"""

    vocab: str = "defect_mode"
    term: str = ""
    code: str = ""
    hint: str = ""
    source: str = "manual"


class OntologyGapDecideModel(BaseModel):
    approve: bool = True
    reason: str = ""


def _gap_error(exc: Exception) -> HTTPException:
    from ..services.case_ontology_gap import (
        GapAlreadyRegistered,
        GapInvalid,
        GapNotFound,
        GapTransitionError,
    )

    if isinstance(exc, GapAlreadyRegistered):
        return HTTPException(status_code=409, detail={
            "code": "CASE_ONTOLOGY_ALREADY_REGISTERED", "message": str(exc)})
    if isinstance(exc, GapNotFound):
        return HTTPException(status_code=404, detail={
            "code": "CASE_ONTOLOGY_GAP_NOT_FOUND", "message": str(exc)})
    if isinstance(exc, GapTransitionError):
        return HTTPException(status_code=409, detail={
            "code": "CASE_ONTOLOGY_GAP_TRANSITION", "message": str(exc)})
    if isinstance(exc, GapInvalid):
        return HTTPException(status_code=400, detail={
            "code": "CASE_ONTOLOGY_GAP_INVALID", "message": str(exc)})
    return HTTPException(status_code=403, detail={
        "code": "CASE_ONTOLOGY_GAP_DENIED", "message": str(exc)})


@app.post("/api/v2/cases/ontology/gap")
def api_case_ontology_gap_request(req: OntologyGapRequestModel,
                                  request: Request) -> dict[str, Any]:
    """提交本体缺口登记申请（PRD FR-09：术语未登记时的正向治理入口）。

    纪律：**只入申请队列，不入词表**（承接 N7：不做自动本体抽取/自动关系发现）；
    已在词表里的词会被 409 挡回（防重复造词）。
    """
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "ontology-gap")
    runtime = _case_runtime()
    from ..services.case_ontology_gap import GapError

    try:
        data = runtime.ontology_gap.request(
            principal, vocab=req.vocab, term=req.term, code=req.code,
            hint=req.hint, source=req.source)
    except GapError as exc:
        raise _gap_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - 领域异常之外的意外也走统一错误码
        raise _gap_error(exc) from exc
    return {"code": 0, "data": data, "message": "ok"}


@app.get("/api/v2/cases/ontology/gap")
def api_case_ontology_gap_list(request: Request, scope: str = Query("dept")) -> dict[str, Any]:
    """登记申请列表：``mine`` = 我提交的；``dept`` = 待我裁决（治理待办）。

    与访问申请同一口径：列表本身只回"申请了什么词 + 状态 + 留痕"，
    **不含**任何案例正文与权限明细。
    """
    principal = _case_principal(request)
    runtime = _case_runtime()
    mine = runtime.ontology_gap.list(
        requester_id=principal.user_id, limit=50)
    pending = runtime.ontology_gap.list(status="pending", limit=50)
    return {"code": 0, "data": {
        "mine": mine,
        "pending": pending,
        "pending_count": len(pending),
        "can_decide": bool(_get_case_rbac().has_any(principal.roles, "case.ingest")),
        "scope": "mine" if str(scope) == "mine" else "dept",
    }, "message": "ok"}


@app.post("/api/v2/cases/ontology/gap/{gap_id}/decide")
def api_case_ontology_gap_decide(gap_id: str, req: OntologyGapDecideModel,
                                 request: Request) -> dict[str, Any]:
    """裁决登记申请（需 ``case.ingest``；不能裁决自己提交的；驳回必须写理由）。"""
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "ontology-gap-decide")
    runtime = _case_runtime()
    from ..services.case_ontology_gap import GapError

    try:
        data = runtime.ontology_gap.decide(
            principal, gap_id, approve=bool(req.approve), reason=req.reason)
    except GapError as exc:
        raise _gap_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _gap_error(exc) from exc
    return {"code": 0, "data": data, "message": "ok"}


@app.get("/api/v2/cases/distribution")
def api_case_distribution(request: Request, top: int = Query(12)) -> dict[str, Any]:
    """**段 × 缺陷模式**分布热力（PRD FR-10）。

    数据源与检索**完全同源**（当前主体可见集合），因此热力图与检索结果永远对得上；
    空白格是真实统计（"该段确实没出现过这类问题"），不是"没查"。
    """
    principal = _case_principal(request)
    runtime = _case_runtime()
    data = runtime.search.distribution(principal, action="query",
                                       top=max(2, min(int(top or 12), 24)))
    return {"code": 0, "data": data, "message": "ok"}


@app.post("/api/v2/cases/compare")
def api_case_compare(req: CaseCompareRequest, request: Request) -> dict[str, Any]:
    """**三方对照**（演示专用；PRD FR-06）：同一句话，三种查法并列。

    | 组 | 配置 | 用途 |
    |---|---|---|
    | A | 无本体：原文直接检索，单轮，无关系 | 基线 |
    | B | 单跳本体：受控词表同义扩展，单轮 | 现状能力 |
    | C | 本体增强 Agentic：意图分解 + 有界多跳 + 自检再检索 | 目标能力 |

    三条纪律：

    1. **三组的可见范围完全相同**：同一主体、同一安全内核（过滤器下推 + 二次授权），
       对照的只能是"检索智能"，绝不是"能看到多少"——否则这个接口就成了权限旁路；
    2. **数字全部来自当次真实检索**，不做任何"看起来更好"的修饰；
    3. 命中更多 ≠ 质量更高：response 里同时给三组的段分布与唯一案例，
       由讲解员按"少而准"的诚实口径解读（沿用既有口径）。

    默认关闭（``CASE_DEMO_COMPARE=false``）：生产环境不允许出现第二/第三种检索行为。
    """
    if not (bool(getattr(settings, "CASE_DEMO_MODE", False))
            and bool(getattr(settings, "CASE_DEMO_COMPARE", False))):
        raise HTTPException(status_code=403, detail={
            "code": "CASE_COMPARE_DISABLED",
            "message": "三方对照仅演示环境开放（需 CASE_DEMO_MODE 且 CASE_DEMO_COMPARE）"})
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "compare")
    runtime = _case_runtime()
    k = max(1, min(int(req.k or 5), int(getattr(settings, "CASE_K_MAX", 20))))
    question = str(req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail={
            "code": "CASE_QUERY_EMPTY", "message": "请提供 question（对照演示需要同一句话）"})

    from ..services.case_relation import segment_label, segment_of

    def variant(label: str, desc: str, search) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        segments: dict[str, int] = {}
        for hit in search.hits or ():
            case_id = str(hit.get("case_id") or "")
            if case_id in seen:
                continue
            seen.add(case_id)
            group = segment_of(hit.get("line_code"))
            if group:
                segments[group] = segments.get(group, 0) + 1
            rows.append({"case_id": case_id, "title": str(hit.get("title") or ""),
                         "score": round(float(hit.get("score") or 0.0), 4),
                         "line_code": str(hit.get("line_code") or ""),
                         "segment": segment_label(group) if group else "职能 / 通用"})
        return {"label": label, "desc": desc,
                "hit_count": len(search.hits or ()), "case_count": len(seen),
                "top": rows[:6], "case_ids": sorted(seen),
                "segments": {segment_label(g): n for g, n in sorted(segments.items())},
                "hidden_count": int(search.hidden_count or 0),
                "candidates": int(search.candidates or 0)}

    # A：无本体（原文检索，单轮）
    normalized = runtime.qa.normalize_query(question)
    plain = runtime.search.search(principal, question, action=req.action, k=k)
    # B：单跳本体（同义扩展，单轮）
    single = runtime.search.search(principal, normalized, action=req.action, k=k)
    # C：本体增强 Agentic（完整闭环）
    agentic = runtime.qa.answer(principal, question, k=k, action=req.action)
    full = agentic.search
    if full is None:
        _search_or_raise(plain)
        full = plain

    groups = [variant("A · 无本体", "原文直接检索：不归一、无关系、单轮", plain),
              variant("B · 单跳本体", "受控词表同义扩展后单轮检索（升级前能力）", single),
              variant("C · 本体增强 Agentic",
                      "意图分解 + 有条件多跳 + 自检再检索（本次升级）", full)]
    a_ids = set(groups[0]["case_ids"])
    b_ids = set(groups[1]["case_ids"])
    c_ids = set(groups[2]["case_ids"])
    gains = {
        "b_only": sorted(b_ids - a_ids),
        "c_only": sorted(c_ids - b_ids),
        "c_beyond_b": len(c_ids - b_ids),
        "c_segments": len(groups[2]["segments"]),
        "summary": (f"B 比 A 多 {len(b_ids - a_ids)} 个案例（本体归一）；"
                    f"C 比 B 多 {len(c_ids - b_ids)} 个案例"
                    f"（关系扩展，覆盖 {len(groups[2]['segments'])} 个工艺段）"),
    }
    runtime.audit.append(actor=principal.user_id, action="case.compare",
                         detail={"k": k, "b_only": len(gains["b_only"]),
                                 "c_beyond_b": gains["c_beyond_b"]})
    return {"code": 0, "message": "ok", "data": {
        "question": question,
        "normalized": normalized,
        "k": k,
        "variants": groups,
        "gains": gains,
        "agentic": agentic.agentic,
        "answerer": agentic.answerer,
        "answered": agentic.answered,
        "notice": ("对照口径：三组可见范围完全一致，差异只来自检索智能；"
                   "命中更多不等于质量更高。"),
    }}


@app.post("/api/v2/cases/ingest")
def api_case_ingest(req: CaseIngestRequest, request: Request) -> dict[str, Any]:
    """案例入库（需 ``case.ingest``；落 ``status=review``，**不自动 approve**）。"""
    principal = _case_principal(request)
    if not _get_case_rbac().has_any(principal.roles, "case.ingest"):
        raise HTTPException(status_code=403, detail={
            "code": "CASE_ACTION_DENIED",
            "message": f"角色 {list(principal.roles)} 不持有 case.ingest（入库需质量/工艺岗）"})
    runtime = _case_runtime()
    try:
        result = runtime.ingest.ingest(req.draft or {})
    except Exception as exc:  # noqa: BLE001 - 统一映射为架构 §9.2 错误码
        raise _case_error(exc) from exc
    runtime.audit.append(actor=principal.user_id, action="case.ingest",
                         case_id=result.case_id,
                         detail={"redaction_kinds": list(result.redaction_kinds)})
    return {"code": 0, "data": {
        "case_id": result.case_id, "status": result.status,
        "chunks": result.chunks, "redaction_kinds": list(result.redaction_kinds),
        "acl_tokens": list(result.acl_tokens),
        "visibility_class": result.visibility_class,
        "notes": list(result.notes),
        "next_step": "案例已进入 review；发布需人工审核（HITL case_publish），"
                     "Agent 不得自行 approve",
    }, "message": "ok"}


@app.post("/api/v2/cases/{case_id}/request-access")
def api_case_request_access(case_id: str, req: CaseAccessRequestModel,
                            request: Request) -> dict[str, Any]:
    """提交跨部门访问申请（**不直接授权**；授权只在审批通过那一刻发生）。

    涉密（L3）与未结案案例不走这条通道；已能看到的案例再建申请是 409。
    """
    principal = _case_principal(request)
    runtime = _case_runtime()
    from ..services.case_access import (
        AccessAlreadyVisible,
        AccessDuplicate,
        AccessNotFound,
        AccessNotRequestable,
        AccessRequestError,
    )

    try:
        created = runtime.access.request_access(
            principal, case_id, reason=req.reason, scope=req.scope)
    except AccessDuplicate as exc:
        raise HTTPException(status_code=409, detail={
            "code": "CASE_ACCESS_REQUEST_DUPLICATED", "message": str(exc)}) from exc
    except AccessAlreadyVisible as exc:
        raise HTTPException(status_code=409, detail={
            "code": "CASE_ACCESS_ALREADY_VISIBLE", "message": str(exc)}) from exc
    except AccessNotRequestable as exc:
        raise HTTPException(status_code=403, detail={
            "code": "CASE_ACCESS_NOT_REQUESTABLE", "message": str(exc)}) from exc
    except AccessNotFound as exc:
        raise HTTPException(status_code=404, detail={
            "code": "CASE_NOT_FOUND_OR_NOT_VISIBLE", "message": str(exc)}) from exc
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "CASE_ACCESS_REQUEST_INVALID", "message": str(exc)}) from exc

    # Agent 只做路由与留痕，**不批准**（既有铁律：Agent 不得持 approve）
    route = runtime.access_agent.push(str(created.get("request_id") or ""))
    return {"code": 0, "data": {
        "request": created,
        "route": dict(route),
        # 演示专用自动审批的可用性预检（不落任何库）：前端据此决定
        # 提交后是直接提示"等待审批"，还是弹窗走模拟审批链路。
        "demo_approval": _case_demo_approval_preview(runtime, created, principal),
        "message": str(route.get("reason") or "申请已提交"),
    }, "message": "ok"}


def _case_demo_approval_preview(runtime, request_row: Mapping[str, Any],
                                principal: _CasePrincipal) -> dict[str, Any]:
    """这条申请能不能走演示自动审批（resolve 的 dry-run，不写任何库）。"""
    approver = getattr(runtime, "access_approver", None)
    if approver is None:  # pragma: no cover - 装配缺失时应fail-closed
        return {"available": False, "reason": "审批人提供者未装配"}
    case_id = str((request_row or {}).get("case_id") or "")
    case = runtime.store.get_case(case_id)
    if case is None:
        return {"available": False, "reason": "案例不存在或不可见"}
    try:
        return approver.preview(case, str(principal.user_id))
    except Exception as exc:  # noqa: BLE001 - 预检失败只影响演示体验，不拦申请
        logger.warning("[case-api] 演示审批预检失败：%s", exc)
        return {"available": False, "reason": "预检失败，请人工审批"}


@app.get("/api/v2/cases/access/requests")
def api_case_access_requests(request: Request,
                             scope: str = Query("mine")) -> dict[str, Any]:
    """申请列表：``scope=mine`` 我提交的 / ``scope=dept`` 待我审批的。"""
    principal = _case_principal(request)
    runtime = _case_runtime()
    runtime.access.reap_expired()
    if str(scope or "mine") == "dept":
        rows = runtime.access.list_pending(dept=principal.dept)
    else:
        rows = runtime.access.list_for_requester(principal.user_id)
    return {"code": 0, "data": {
        "scope": str(scope or "mine"),
        "requests": [{k: v for k, v in row.items() if k != "requester_id"}
                     | {"requester_id": _mask_user(row.get("requester_id"))}
                     for row in rows],
    }, "message": "ok"}


@app.post("/api/v2/cases/access/requests/{request_id}/decide")
def api_case_access_decide(request_id: str, req: CaseAccessDecision,
                           request: Request) -> dict[str, Any]:
    """审批：批准 → 写入 user 维度授权（带有效期）+ 重建该案例索引。"""
    principal = _case_principal(request)
    runtime = _case_runtime()
    from ..services.case_access import (
        AccessNotApprover,
        AccessNotFound,
        AccessRequestError,
        AccessSelfApproval,
        AccessTransitionError,
    )

    try:
        decided = runtime.access.decide(principal, request_id,
                                        approve=bool(req.approve),
                                        reason=req.reason)
    except AccessSelfApproval as exc:
        # 职责分离违反：单独错误码，方便审计与前端给出明确提示
        raise HTTPException(status_code=403, detail={
            "code": "CASE_ACCESS_SELF_APPROVAL_FORBIDDEN",
            "message": str(exc)}) from exc
    except AccessNotApprover as exc:
        raise HTTPException(status_code=403, detail={
            "code": "CASE_ACCESS_NOT_APPROVER", "message": str(exc)}) from exc
    except AccessNotFound as exc:
        raise HTTPException(status_code=404, detail={
            "code": "CASE_ACCESS_REQUEST_NOT_FOUND", "message": str(exc)}) from exc
    except AccessTransitionError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "CASE_ACCESS_INVALID_TRANSITION", "message": str(exc)}) from exc
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "CASE_ACCESS_DECISION_INVALID", "message": str(exc)}) from exc
    return {"code": 0, "data": decided, "message": "ok"}


@app.post("/api/v2/cases/access/requests/{request_id}/demo-approve")
def api_case_access_demo_approve(request_id: str, request: Request) -> dict[str, Any]:
    """演示专用：模拟「职能部门审批通过」的回调（**预留接口，仅演示**）。

    生产形态：职能部门负责人在真实审批流（OA / IM 审批）里点同意，系统回调
    同一个 :meth:`CaseAccessService.decide`；本端点只是把"谁点的同意"换成
    演示身份，让评审现场能看到完整链路。三点硬约束：

    1. 只有**申请人本人**能触发（避免拿别人的申请号刷授权）；
    2. 审批人身份由服务端按案例归属职能部门解析（``DemoAgentApprover``），
       解析不出 → 409，**不降级找"差不多的人"批**；
    3. 授权副作用走的仍是 ``decide()`` 同一条路（ACL + 有效期 + 索引重建），
       审计 action 打 ``.demo`` 后缀，哈希链上可见"这不是真人点的同意"。

    开关：``CASE_DEMO_MODE=true`` 且 ``CASE_ACCESS_AUTO_APPROVE_DEMO=true``；
    任一为 false → 403 ``CASE_DEMO_APPROVAL_DISABLED``（回到人工审批）。
    """
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "access")
    runtime = _case_runtime()
    from ..services.case_auto_approve import (
        DemoApprovalDisabled,
        DemoApprovalNotRequester,
        DemoApproverUnavailable,
    )
    from ..services.case_access import (
        AccessNotFound,
        AccessRequestError,
        AccessTransitionError,
    )

    approver = getattr(runtime, "access_approver", None)
    if approver is None:  # pragma: no cover - 装配缺失
        raise HTTPException(status_code=503, detail={
            "code": "CASE_APPROVER_UNAVAILABLE",
            "message": "审批人提供者未装配（access_approver 缺失）"})
    try:
        decided = approver.approve(runtime.access, principal, request_id)
    except DemoApprovalDisabled as exc:
        raise HTTPException(status_code=403, detail={
            "code": "CASE_DEMO_APPROVAL_DISABLED", "message": str(exc)}) from exc
    except DemoApprovalNotRequester as exc:
        raise HTTPException(status_code=403, detail={
            "code": "CASE_ACCESS_NOT_REQUESTER", "message": str(exc)}) from exc
    except DemoApproverUnavailable as exc:
        raise HTTPException(status_code=409, detail={
            "code": "CASE_ACCESS_DEMO_APPROVER_UNAVAILABLE",
            "message": str(exc)}) from exc
    except AccessNotFound as exc:
        raise HTTPException(status_code=404, detail={
            "code": "CASE_ACCESS_REQUEST_NOT_FOUND", "message": str(exc)}) from exc
    except AccessTransitionError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "CASE_ACCESS_INVALID_TRANSITION", "message": str(exc)}) from exc
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "CASE_ACCESS_DECISION_INVALID", "message": str(exc)}) from exc
    return {"code": 0, "data": {
        "request": decided,
        "demo": True,
        "message": "职能部门已审批通过：授权已写入（带有效期，到期自动回收）",
    }, "message": "ok"}


@app.get("/api/v2/cases/{case_id}/access-hint")
def api_case_access_hint(case_id: str, request: Request) -> dict[str, Any]:
    """这条案例"能不能申请"（前端据此显示申请按钮）。不泄漏任何正文。"""
    principal = _case_principal(request)
    runtime = _case_runtime()
    from ..services.case_access import AccessNotFound

    try:
        hint = runtime.access.requestable_hint(principal, case_id)
    except AccessNotFound as exc:
        raise HTTPException(status_code=404, detail={
            "code": "CASE_NOT_FOUND_OR_NOT_VISIBLE", "message": str(exc)}) from exc
    return {"code": 0, "data": hint, "message": "ok"}


@app.get("/api/v2/cases/audit/verify")
def api_case_audit_verify(request: Request, limit: int = 20) -> dict[str, Any]:
    """审计链校验（hash chain；返回最近 N 条，**不含正文与查询明文**）。"""
    principal = _case_principal(request)
    runtime = _case_runtime()
    chain = runtime.audit.verify_chain()
    return {"code": 0, "data": {
        "chain": chain,
        "recent": runtime.audit.recent(limit=max(1, min(int(limit or 20), 200))),
        "actor": principal.user_id,
    }, "message": "ok"}


@app.get("/api/v2/cases/{case_id}")
def api_case_detail(case_id: str, request: Request,
                    response: Response) -> dict[str, Any]:
    """单案例详情（**无权时 404 而非 403**，防存在性探测）。

    R1 裁定：原始来源单号只在**这个窄面**返回——调用方已通过 ``case.read``
    且 ``cls_rank`` 达标（``read_case`` 成功即证明）。响应头 ``X-Case-Trace: full``
    把这件事实显式化，方便网关/审计按头过滤。
    """
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "read")
    runtime = _case_runtime()
    case = runtime.search.read_case(principal, case_id, action="read")
    if case is None:
        raise HTTPException(status_code=404, detail={
            "code": "CASE_NOT_FOUND_OR_NOT_VISIBLE",
            "message": "案例不存在，或你没有查看权限"})
    response.headers["X-Case-Trace"] = "full"
    return {"code": 0, "data": case, "message": "ok"}


@app.post("/api/v2/cases/{case_id}/cite")
def api_case_cite(case_id: str, req: CaseCiteRequest, request: Request) -> dict[str, Any]:
    """把案例引用到工单（只引用脱敏知识层；返回引用三元组）。

    三元组 ``(case_id, chunk_id, content_version)`` 里的 ``chunk_id`` 是
    ``f"{case_id}::{chunk_type}"``：它必须与用户实际阅读的那一段一致，
    否则"可溯源"只是口号。因此：

    * 前端传 ``chunk_type`` / ``content_version``（来自命中卡片）；
    * 未传时退化为 ``summary``（保守：摘要一定是知识层且最稳定）；
    * 内容版本已变（``content_version`` 不匹配）→ ``409 CASE_CITATION_STALE``，
      **不返回一个"看起来对"的旧引用**。
    """
    principal = _case_principal(request)
    _case_guard_rate_limit(request, principal, "cite")
    runtime = _case_runtime()
    case = runtime.search.read_case(principal, case_id, action="cite")
    if case is None:
        raise HTTPException(status_code=404, detail={
            "code": "CASE_NOT_FOUND_OR_NOT_VISIBLE",
            "message": "案例不存在，或你没有引用权限"})

    from ..services.case_index import CHUNK_TYPES

    chunk_type = str(req.chunk_type or "summary").strip() or "summary"
    if chunk_type not in CHUNK_TYPES:
        raise HTTPException(status_code=400, detail={
            "code": "CASE_CITE_INVALID_CHUNK",
            "message": f"未知的引用片段类型 {chunk_type!r}（允许：{list(CHUNK_TYPES)}）"})
    current_version = int(case.get("content_version") or 1)
    requested_version = int(req.content_version or 0)
    if requested_version and requested_version != current_version:
        raise HTTPException(status_code=409, detail={
            "code": "CASE_CITATION_STALE",
            "message": f"片段内容已更新（v{requested_version} → v{current_version}），"
                       "请重新检索后再引用"})
    if not runtime.index_service.verify_citation(case_id, chunk_type, current_version):
        raise HTTPException(status_code=409, detail={
            "code": "CASE_CITATION_STALE",
            "message": "引用校验失败：该片段已不在当前知识层（案例可能已撤权或被更新）"})

    chunk_id = f"{case_id}::{chunk_type}"
    citation = {"case_id": case_id, "chunk_id": chunk_id,
                "content_version": current_version}
    runtime.audit.append(actor=principal.user_id, action="case.cite",
                         case_id=case_id, chunk_id=chunk_id,
                         detail={"target_ticket_type": req.target_ticket_type,
                                 "target_ticket_id": req.target_ticket_id})
    # R1：引用回执默认只给不可反推的 ref；只有调用方**同时**具备 case.read
    # （即他本来就能从详情拿到真号）时才回原文单号，避免 cite 成为绕过详情闸门的后门。
    ticket_id = str(case.get("source_ticket_id") or "")
    source_ticket: dict[str, str] = {
        "ticket_type": str(case.get("source_ticket_type") or ""),
        "ticket_ref": source_ticket_ref(ticket_id),
    }
    trace_full = runtime.search.trace_allowed(principal, case_id)
    if trace_full:
        source_ticket["ticket_id"] = ticket_id
    return {"code": 0, "data": {
        "citation": citation,
        "case_code": case.get("case_code"),
        "chunk_type": chunk_type,
        # 溯源锚点：ref 恒有；原文单号需 case.read（见上）
        "source_ticket": source_ticket,
        "trace": "full" if trace_full else "ref",
        "target": {"ticket_type": req.target_ticket_type,
                   "ticket_id": req.target_ticket_id},
        "layer": "knowledge",
        "note": "引用内容是脱敏知识层；证据层引用需额外授权（case.evidence.read）",
    }, "message": "ok"}


# ---------- 静态前端挂载（/demo-ui → demo/ui 目录） ----------

_UI_DIR = Path(__file__).resolve().parent / "ui"


class _RevalidateStaticFiles(StaticFiles):
    """演示前端静态资源：强制浏览器每次协商缓存（ETag → 304 空体，成本极低）。

    为什么不能靠启发式缓存：``index.html`` 只给 10 个具名资源注入了 ``?v=<mtime>``，
    ``scenes/*.js`` 等仍是无版本号的固定 URL。若让它们按启发式缓存驻留，改动同样
    不会生效。统一 no-cache（回源校验）兼顾两者：有版本号的命中 304，无版本号的
    变更后立即拿到新内容，代价只是一个空响应。
    """

    async def get_response(self, path: str, scope: Mapping[str, Any]) -> Any:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            # index.html 走外壳同款 no-store（含 /demo-ui 无斜杠时的 html 兜底）
            if path.endswith("index.html") or path in ("", "."):
                response.headers["cache-control"] = "no-store, no-cache, must-revalidate"
            else:
                response.headers["cache-control"] = "no-cache"
        return response


if _UI_DIR.is_dir():
    # html=True：/demo-ui 与 /demo-ui/ 均返回 index.html（单页应用）。
    # 直接双击 index.html（file://）亦可打开：前端自动探测后端，
    # 不可达时进入演示模式（页面完整渲染 + 明确降级提示）。
    app.mount("/demo-ui", _RevalidateStaticFiles(directory=str(_UI_DIR), html=True),
              name="demo-ui")

# ---------- 演示场景数据挂载（T10 追加：/demo-story → data/demo_storyline/frontend） ----------
# 场景数据文件按 T05 数据契约放 data/demo_storyline/frontend/，而它不在 /demo-ui 静态目录内，
# 浏览器无法用相对路径取到。这里补一条**只读**挂载把该目录暴露给演示前端：
# 仅静态文件、无写接口、不引用任何真实库（隔离红线见 config_demo.assert_isolated）。
_STORY_FRONTEND_DIR = Path(__file__).resolve().parents[4] / "data" / "demo_storyline" / "frontend"

if _STORY_FRONTEND_DIR.is_dir():
    app.mount("/demo-story", StaticFiles(directory=str(_STORY_FRONTEND_DIR)),
              name="demo-story")


__all__ = ["app", "get_orchestrator", "EventHub"]
