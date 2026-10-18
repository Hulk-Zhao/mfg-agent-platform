"""案例检索/读取的请求限流（B8：审计链灌水防护）。

**为什么不用"审计采样"来治理灌水**
B1 要求"不存在"与"存在但无权"两条路径做**等价工作**（查询次数、判定调用、
审计写入都对齐），否则耗时差本身就是存在性预言机。若为了省 IO 而对部分 404
跳过审计写入，就等于把 B1 刚消掉的时间差又请回来——治一个病、添一个病。

因此治理手段选**限流**：掐断探测流量而不是削减可追溯性。攻击者扫
``QA-0001..QA-9999`` 时在入口就被 429 挡住，审计链里不会灌进 1 万行。

**两级实现**
- Redis 在线：复用 :func:`core.cache.rate_limit_exceeded`（跨实例共享，
  多副本部署时的唯一正确选择）；
- Redis 不可用（demo 默认，``REDIS_URL`` 为空）：退回**进程内固定窗口**计数。
  进程内只在单实例下有效，这是已知边界，写在这里而不是假装它是分布式方案。

**fail-closed 还是 fail-open**
限流本身**fail-open**（Redis 故障时放行），理由与 ``core.cache`` 一致：
限流是保护措施，不该成为可用性事故的来源。但它**不能** fail-open 到"审计也没
了"——审计写入始终由调用方无条件执行，与限流结果无关。
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

from ...common.config import settings
from ..core import cache

__all__ = [
    "case_rate_limit_bucket",
    "check_case_rate_limit",
    "reset_case_rate_limit",
]

#: 进程内固定窗口：key → (窗口起始秒, 计数)
_WINDOW: Dict[str, Tuple[float, int]] = {}
_LOCK = threading.Lock()

#: 窗口长度（秒），与 core.cache 的默认保持一致
WINDOW_S = 60


def case_rate_limit_bucket(*, user_id: str, client_ip: str) -> str:
    """限流桶键：用户与来源 IP 各一个桶。

    单用用户会被"共享账号"绕过（一个账号被拿去做扫描器，所有人都被限），
    单用 IP 会被 NAT 后的同一出口误伤（一个工厂一个出口号）。两者都算，
    任一超限即拦。
    """
    return f"case:{user_id or 'anonymous'}|{client_ip or '-'}"


def _local_exceeded(bucket: str, limit: int, window_s: int) -> bool:
    """进程内固定窗口计数（Redis 不可用时的降级路径）。"""
    now = time.monotonic()
    with _LOCK:
        start, count = _WINDOW.get(bucket, (now, 0))
        if now - start >= window_s:
            start, count = now, 0
        count += 1
        _WINDOW[bucket] = (start, count)
        return count > limit


def check_case_rate_limit(bucket: str, limit: int = 0,
                          window_s: int = WINDOW_S) -> bool:
    """是否已超限（True = 应返回 429）。

    Args:
        bucket: :func:`case_rate_limit_bucket` 生成的桶键。
        limit: ``0`` 或负数表示取 ``CASE_RATE_LIMIT_PER_MIN``；负数还表示**不限流**
            （用于本地调试，正式环境不要配负数）。
    """
    resolved = int(limit or settings.CASE_RATE_LIMIT_PER_MIN)
    if resolved <= 0:
        return False
    if cache.available():
        # Redis 在线：跨实例共享计数（生产多副本的唯一正确形态）
        return cache.rate_limit_exceeded(bucket, resolved, window_s)
    return _local_exceeded(bucket, resolved, window_s)


def reset_case_rate_limit() -> None:
    """清空进程内计数（测试隔离用；Redis 侧由窗口过期自动回收）。"""
    with _LOCK:
        _WINDOW.clear()
