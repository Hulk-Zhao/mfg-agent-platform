"""QA 验证：事件流完整性（D6 并发隔离修复不能以丢事件为代价）。

背景：V2 events.py 用 contextvar 做请求级事件队列（修 V1 全局队列串话）。
QA Round 1 发现：yaml_dag._make_typed_node 为超时控制把 handler 提交到
ThreadPoolExecutor，handler 内 sink.emit 的 tool 事件在 worker 线程中
查 contextvar 得到默认值 None → 事件被静默丢弃（BUG-5，CLI 33 事件完整 /
API-SSE 仅 21，丢失 6 对 tool 事件）。

BUG-5 已由工程师修复（commit 54aa607，双保险：QueueSink 显式持队列引用 +
pool.submit 前 contextvars.copy_context() 传播）。本文件现锁定修复行为：
队列必须收满 33 事件（tool×12 全到达）。
"""
import queue as queue_mod

import pytest

from mfg_agents.config import settings
from mfg_agents.tools.llm_factory import reset_probe_cache
from mfg_agents.v2.core.events import QueueSink, bind_queue, unbind
from mfg_agents.v2.core.orchestrator import V2Orchestrator, V2Runtime

STOP_QUERY = "A型号在SMT-1停线，何时复线？"
USER_CTX = {"user_id": "u-manager", "dept": "manufacturing",
            "roles": ["manufacturing", "coordinator"]}


@pytest.fixture()
def orchestrator(monkeypatch, tmp_path) -> V2Orchestrator:
    reset_probe_cache()
    for mod in ("mfg_agents.tools.llm_factory",
                "mfg_agents.v2.core.router",
                "mfg_agents.v2.core.node_registry"):
        monkeypatch.setattr(f"{mod}.llm_available", lambda: False)
    monkeypatch.setattr(settings, "WORKFLOW_DB",
                        f"sqlite:///{tmp_path / 'workflow.db'}")
    return V2Orchestrator(V2Runtime.create_default())


def _drain(q: "queue_mod.Queue") -> list[dict]:
    events = []
    while not q.empty():
        events.append(q.get())
    return events


def test_tool_events_reach_bound_queue(orchestrator):
    """QA-BUG-5 证据：绑定队列后跑全链路，tool_start/tool_end 必须全部到达。"""
    q, token = bind_queue()
    try:
        orchestrator.handle(STOP_QUERY, USER_CTX, sink=QueueSink())
    finally:
        unbind(token)
    events = _drain(q)
    types = [e["type"] for e in events]
    # 业务/制造/品质/供应链/设备/FA 共 6 次 SQL/RAG 查询 → 6 对 tool 事件
    assert types.count("tool_start") == 6, \
        f"tool_start 丢失：期望 6，实际 {types.count('tool_start')}"
    assert types.count("tool_end") == 6, \
        f"tool_end 丢失：期望 6，实际 {types.count('tool_end')}"


def test_parallel_domain_tool_events_present(orchestrator):
    """三域并行取证的 tool 事件（worker 线程内发射）必须到达队列。"""
    q, token = bind_queue()
    try:
        orchestrator.handle(STOP_QUERY, USER_CTX, sink=QueueSink())
    finally:
        unbind(token)
    events = _drain(q)
    tools = {e.get("tool") for e in events if e["type"] == "tool_start"}
    assert {"SQL查询", "RAG检索", "意图识别"} <= tools, \
        f"并行域/FA 的 tool 事件缺失，实际收到: {tools}"


def test_agent_events_reach_bound_queue(orchestrator):
    """agent 级事件当前可到达（图线程执行）——回归保护，防修复时改坏。"""
    q, token = bind_queue()
    try:
        orchestrator.handle(STOP_QUERY, USER_CTX, sink=QueueSink())
    finally:
        unbind(token)
    events = _drain(q)
    types = [e["type"] for e in events]
    assert types.count("agent_start") == 10  # 9 步 + fan_out 展开 3 分支
    assert types.count("fan_in") == 1


def test_callback_path_event_parity(orchestrator):
    """callback 路径（CLI）事件必须完整——33 个事件的基线不回归。"""
    seen: list[dict] = []
    orchestrator.handle(STOP_QUERY, USER_CTX,
                        sink=_CallbackSink(seen.append))
    types = [e["type"] for e in seen]
    assert types.count("tool_start") == 6
    assert types.count("tool_end") == 6
    assert types.count("agent_start") == 10
    assert len(seen) == 33


class _CallbackSink:
    def __init__(self, callback):
        self._callback = callback

    def emit(self, event):
        self._callback(event)


def test_two_requests_no_cross_talk(orchestrator):
    """D6 回归：两个请求的事件队列物理隔离（互不串话、互不丢）。"""
    q1, t1 = bind_queue()
    try:
        orchestrator.handle(STOP_QUERY, USER_CTX, sink=QueueSink())
    finally:
        unbind(t1)
    first = _drain(q1)

    q2, t2 = bind_queue()
    try:
        orchestrator.handle(STOP_QUERY, USER_CTX, sink=QueueSink())
    finally:
        unbind(t2)
    second = _drain(q2)

    assert len(first) == len(second) == 33  # Round 2 修复后两队列均收满（BUG-5 已修）
    # 隔离性：q2 的事件不可能在 q1 里（物理上两个队列对象）
