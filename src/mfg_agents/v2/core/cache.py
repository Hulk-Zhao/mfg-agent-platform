"""V2 Redis 缓存层（可选；未配置或连接失败时静默降级为无缓存）。

职责（均为演示友好的轻量用途，非强一致）：

1. **查询结果缓存** ``mfg:q:{hash(问题+角色)}``（TTL 10 分钟）——
   同一问题同一角色重复提问直接回放上次结果，省去 DAG 重跑。
2. **会话/待办上下文** ``mfg:sess:{user_id}``（TTL 2 小时）——
   最近一次查询的 request_id / 场景 / 待确认工单，供后续追问与「上次那单」指代。
3. **限流** ``mfg:rl:{bucket}``（INCR + 过期窗口）——
   公网隧道下防单 IP 刷问；超限由调用方决定拒绝或排队。

设计要点：

- **惰性**：模块导入不连接 Redis；首次调用才建立连接。
- **降级**：`REDIS_URL` 缺失 → 全部函数返回 None / 放行；连接或读写异常
  一律静默并按 30s 冷却重试，**绝不因缓存故障阻断业务**。
- **不缓存 SSE 事件队列**：事件流按请求隔离（见 ``core.events``），
  与缓存无关。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable

from ...common.config import settings

logger = logging.getLogger(__name__)

#: 连接失败后的冷却秒数（期间直接跳过 Redis，避免每次请求都等超时）
_RETRY_COOLDOWN_S = 30.0

_client: Any = None
_failed_until: float = 0.0


def available() -> bool:
    """Redis 是否可用（惰性探测，失败静默）。"""
    return get_client() is not None


def get_client() -> Any | None:
    """惰性获取 Redis 客户端；未配置 / 不可用时返回 ``None``。"""
    global _client, _failed_until
    if _client is not None:
        return _client
    url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if not url:
        return None
    if time.time() < _failed_until:
        return None
    try:
        import redis  # 惰性导入：未安装 redis 包时也不影响启动

        client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=0.5, socket_timeout=1.0)
        client.ping()
        _client = client
        logger.info("[cache] redis 已连接：%s", url)
        return _client
    except Exception as exc:  # noqa: BLE001 - 任何异常都降级
        _failed_until = time.time() + _RETRY_COOLDOWN_S
        logger.warning("[cache] unavailable, bypass: %s", exc)
        return None


def _reset() -> None:
    """标记当前连接失效，下次调用重连。"""
    global _client, _failed_until
    _client = None
    _failed_until = time.time() + 1.0


def cache_get(key: str) -> Any | None:
    """读取 JSON 缓存；未命中 / 不可用返回 ``None``。"""
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:  # noqa: BLE001
        _reset()
        logger.warning("[cache] get 失败，降级：%s", exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def cache_set(key: str, value: Any, ttl: int = 600) -> None:
    """写入 JSON 缓存（带 TTL 秒）；不可用时静默跳过。"""
    client = get_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        _reset()
        logger.warning("[cache] set 失败，降级：%s", exc)


def cache_delete(key: str) -> None:
    """删除缓存键（例：工单审批后失效相关查询缓存）。"""
    client = get_client()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception:  # noqa: BLE001
        _reset()


def query_key(question: str, user_ctx: dict[str, Any] | None = None) -> str:
    """查询缓存键：``mfg:q:{sha1(问题|部门|角色)}``（同一问题同一角色才复用）。"""
    ctx = user_ctx or {}
    roles = ctx.get("roles") or []
    raw = "|".join([question.strip(), str(ctx.get("dept", "")),
                    ",".join(sorted(str(r) for r in roles))])
    return "mfg:q:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def remember_session(user_id: str, payload: dict[str, Any], ttl: int = 7200) -> None:
    """记住用户最近一次查询上下文（request_id / 场景 / 待确认工单）。"""
    if not user_id:
        return
    cache_set(f"mfg:sess:{user_id}", payload, ttl=ttl)


def recall_session(user_id: str) -> dict[str, Any] | None:
    """读取用户最近一次查询上下文；无则返回 ``None``。"""
    if not user_id:
        return None
    return cache_get(f"mfg:sess:{user_id}")


def rate_limit_exceeded(bucket: str, limit: int, window_s: int = 60) -> bool:
    """固定窗口计数限流：``window_s`` 内访问超过 ``limit`` 次返回 True。

    Redis 不可用时恒返回 False（放行），保证业务不因限流故障中断。
    """
    client = get_client()
    if client is None:
        return False
    key = f"mfg:rl:{bucket}"
    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_s)
        return int(count) > limit
    except Exception as exc:  # noqa: BLE001
        _reset()
        logger.warning("[cache] rate_limit 失败，放行：%s", exc)
        return False


def get_or_compute(key: str, ttl: int, producer: Callable[[], Any]) -> Any:
    """读缓存 → 命中即返回；未命中 → 执行 ``producer`` 并回填缓存。"""
    hit = cache_get(key)
    if hit is not None:
        return hit
    value = producer()
    if value is not None:
        cache_set(key, value, ttl=ttl)
    return value
