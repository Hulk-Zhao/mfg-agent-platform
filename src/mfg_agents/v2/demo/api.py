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
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ...config import settings
from ..contracts.im_gateway import ReplyCard
from ..contracts.adapters.wecom import WeComAdapter
from ..core.events import EventHub, QueueSink, bind_existing, hub, unbind
from ..core.orchestrator import V2Orchestrator, create_default_orchestrator

logger = logging.getLogger(__name__)

app = FastAPI(title="mfg-agent-platform v2", version="2.0.0")

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
        hub.close(request_id)

    return StreamingResponse(gen(), media_type="text/event-stream")


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


__all__ = ["app", "get_orchestrator", "EventHub"]
