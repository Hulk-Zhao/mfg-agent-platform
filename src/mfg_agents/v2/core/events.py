"""V2 事件总线：请求级 context-var 队列（修 D6 并发串话）。

V1 events.py 是全局单 queue.Queue，并发请求间事件互相串话。
V2 改为：
- 每个请求绑定自己的队列（contextvars.ContextVar，天然随请求线程/协程隔离）；
- EventHub 提供跨端点订阅（POST 执行 / GET SSE 读取同一请求队列）；
- 事件 type 枚举与 V1 完全一致（agent_start/tool_start/tool_end/llm_start/
  agent_end/final/error），前端零改动。
"""
from __future__ import annotations

import logging
import queue
import threading
from contextvars import ContextVar, Token
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 请求级事件队列：默认 None（未绑定时 push 静默丢弃，保证 CLI/测试无副作用）
_current_queue: ContextVar[Optional[queue.Queue]] = ContextVar(
    "mfg_v2_event_queue", default=None)


def bind_queue() -> tuple[queue.Queue, Token]:
    """为当前请求上下文创建并绑定事件队列，返回 (队列, 解绑令牌)。"""
    q: queue.Queue = queue.Queue()
    token = _current_queue.set(q)
    return q, token


def bind_existing(q: queue.Queue) -> Token:
    """把 EventHub 中已存在的请求队列绑定到当前上下文（API 双端点模式）。"""
    return _current_queue.set(q)


def unbind(token: Token) -> None:
    """解绑当前请求上下文的事件队列。"""
    _current_queue.reset(token)


def push(event: dict) -> None:
    """向当前请求队列推送事件（V1 兼容签名；未绑定时丢弃）。"""
    q = _current_queue.get()
    if q is not None:
        q.put(event)


class QueueSink:
    """事件 sink：绑定请求队列期间，把事件同时送入队列与可选回调（CLI 打印用）。"""

    def __init__(self, callback: Optional[Callable[[dict], None]] = None) -> None:
        self._callback = callback

    def emit(self, event: dict) -> None:
        push(event)
        if self._callback is not None:
            self._callback(event)


class EventHub:
    """跨端点事件枢纽：request_id → 请求队列（供 SSE 端点订阅）。

    修 D6：不再有全局单队列；每个请求一个队列，按 request_id 寻址，
    并发请求之间物理隔离。队列在读取完成后（或超过 TTL 数量上限）回收。
    """

    def __init__(self, max_pending: int = 256) -> None:
        self._queues: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._max_pending = max_pending

    def open(self, request_id: str) -> queue.Queue:
        with self._lock:
            if request_id not in self._queues:
                self._evict_if_needed()
                self._queues[request_id] = queue.Queue()
            return self._queues[request_id]

    def get(self, request_id: str) -> Optional[queue.Queue]:
        with self._lock:
            return self._queues.get(request_id)

    def close(self, request_id: str) -> None:
        with self._lock:
            self._queues.pop(request_id, None)

    def _evict_if_needed(self) -> None:
        while len(self._queues) >= self._max_pending:
            oldest = next(iter(self._queues))
            self._queues.pop(oldest, None)


# 进程级单例（仅索引请求队列，不承载业务状态）
hub = EventHub()
