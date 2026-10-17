"""A2A 协议最小闭环（Agent-to-Agent，Google 2025.4 / Linux 基金会治理）。

定位（主理人裁定）：**我们平台以工具被调用，不生产自主 Agentic Agent**。
A2A 让外部智能体（如企业内其他团队的 Agent、Claude/GPT 生态）通过标准
协议消费平台能力——它们收到的是协同场景的结构化结果（Task + Artifact），
而不是拿到一个"自主循环的黑盒"。

实现范围（最小闭环，够用即可，防过度工程）：
- ``GET /.well-known/agent.json``：Agent Card（技能清单来自场景注册表）
- ``POST /a2a`` JSON-RPC：``message/send``（文本 → 场景链路 → Task）
  与 ``tasks/get``（按 task_id 查结果）
- 不做：流式（streaming）、push notification、多轮任务状态机、
  长任务生命周期——需要时再按规范扩展（K 记录在 PRD §6.5）

身份与治理：外部调用方通过 ``message.metadata.user_ctx`` 传身份，
治理链（ACL/脱敏/会签）与 HTTP 端点**同一套**——A2A 不是绕过治理的后门。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from .core.orchestrator import V2Orchestrator

logger = logging.getLogger(__name__)

__all__ = ["build_a2a_router", "A2A_TASK_TTL_S"]

A2A_TASK_TTL_S = 3600  # 任务结果进程内保留 1 小时（演示级；生产换持久化）


def _agent_card(orch: V2Orchestrator, base_url: str) -> dict[str, Any]:
    """Agent Card：A2A 客户端发现能力的入口（技能=协同场景）。"""
    skills = []
    for s in orch.runtime.scenario_registry.list():
        skills.append({
            "id": s.key,
            "name": s.name,
            "description": f"跨部门协同场景：{s.name}"
                           f"（参与域：{'、'.join(s.domains)}）",
            "tags": list(s.domains),
        })
    skills.append({
        "id": "platform.data-query",
        "name": "跨部门数据问数",
        "description": "指标（OTD 等）/ 注册查询 / 明细探查（走表级 ACL 与脱敏）",
        "tags": ["data", "text2sql"],
    })
    return {
        "name": "mfg-agent-platform",
        "description": "制造业跨部门协同平台：以确定性工具与协同场景对外提供能力，"
                       "LLM 只做编排（本平台不部署自主 Agentic Agent）",
        "url": base_url,
        "version": "3.1.0",
        "protocolVersion": "0.2.9",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "provider": {"organization": "mfg-agent-platform", "url": base_url},
        "skills": skills,
    }


def build_a2a_router(orch: Optional[V2Orchestrator] = None) -> APIRouter:
    """A2A 路由（FastAPI 挂载；任务表进程内）。"""
    router = APIRouter()
    tasks: dict[str, dict[str, Any]] = {}

    def _orch() -> V2Orchestrator:
        nonlocal orch
        if orch is None:
            from .demo.api import get_orchestrator
            orch = get_orchestrator()
        return orch

    @router.get("/.well-known/agent.json")
    def agent_card(request: Request) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        return _agent_card(_orch(), base)

    @router.post("/a2a")
    async def a2a_entry(request: Request) -> dict[str, Any]:
        """A2A JSON-RPC 入口：message/send / tasks/get。"""
        try:
            msg = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"请求体不是合法 JSON: {exc}") from exc
        method = str(msg.get("method") or "")
        msg_id = msg.get("id")
        params = dict(msg.get("params") or {})

        try:
            if method == "message/send":
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": _message_send(params, tasks)}
            if method == "tasks/get":
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": _tasks_get(params, tasks)}
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"未知方法: {method!r}"}}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("A2A 处理异常 method=%s", method)
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32603, "message": f"内部错误: {exc}"}}

    def _message_send(params: dict, store: dict) -> dict[str, Any]:
        message = dict(params.get("message") or {})
        parts = message.get("parts") or []
        text = "".join(str(p.get("text") or "") for p in parts
                       if isinstance(p, dict)).strip()
        if not text:
            raise HTTPException(400, "message.parts 缺少文本内容")
        metadata = dict(message.get("metadata") or {})
        user_ctx = dict(metadata.get("user_ctx") or {})
        if not user_ctx.get("user_id"):
            raise HTTPException(403, "A2A 调用必须提供 metadata.user_ctx.user_id"
                                     "（治理链需要可核验身份）")
        result = _orch().handle(text, user_ctx=user_ctx).to_dict()
        task_id = f"a2a-{uuid.uuid4().hex[:12]}"
        now = time.time()
        for k in [k for k, v in store.items() if now - v["ts"] > A2A_TASK_TTL_S]:
            store.pop(k, None)
        artifact = {
            "artifactId": f"art-{uuid.uuid4().hex[:8]}",
            "name": "scenario-result",
            "parts": [{"kind": "data", "data": result},
                      {"kind": "text", "text": str(result.get("report") or "")}],
        }
        task = {"id": task_id, "contextId": result.get("run_id", ""),
                "status": {"state": "completed"},
                "artifacts": [artifact], "ts": now}
        store[task_id] = task
        return {"id": task_id, "contextId": result.get("run_id", ""),
                "status": {"state": "completed"}, "artifacts": [artifact],
                "kind": "task"}

    def _tasks_get(params: dict, store: dict) -> dict[str, Any]:
        task_id = str((params.get("id") or params.get("taskId") or ""))
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, f"任务不存在或已过期: {task_id!r}")
        return {"id": task["id"], "contextId": task.get("contextId", ""),
                "status": task["status"], "artifacts": task["artifacts"],
                "kind": "task"}

    return router
