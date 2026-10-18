"""健康检查：结构化依赖探针（替代早期硬编码 ``{"status": "ok"}``）。

为什么必须改
------------
早期实现直接返回 ``{"status": "ok", "version": ...}``，**不探测任何依赖**。
后果有三层：

1. Docker ``HEALTHCHECK`` 与将来的 K8s 探针会把"进程活着"当成"服务可用"——
   DB / Milvus / Redis / LLM 全挂也照报健康，编排层不会重启、不会摘流量。
2. 前端顶栏那颗"后端在线"圆点探的就是这个端点。绿灯只代表 uvicorn 进程存在，
   却在用户点开问数、迟迟出不来结果时给出完全错误的预期。
3. 排障时没有任何细分信息，只能逐项手工试。

设计纪律
--------
- **绝不触发重初始化**：不调用 ``get_orchestrator()``（首次调用会建库、加载 7 个
  场景 YAML、连接向量库，耗时数秒且改变进程状态）。探针只做轻量只读检查。
- **绝不抛异常**：任何单项失败都收敛成 ``down``，健康检查自身不允许 500。
- **并行 + 有界**：5 项互相独立，并行执行，总耗时 = 最慢的单项（约 0.6s），
  而不是各自超时之和。这个端点是前端首屏探测的必经之路，串行的每一毫秒都
  会被用户感知成"登录后卡住了"。

状态语义
--------
``status``：``ok``（全部就绪）/ ``degraded``（服务可达但部分能力降级）。
**刻意不提供 ``error``**——HTTP 200 本身已证明进程可达；把"依赖挂了"表达成
不可达会让前端退回演示模式，反而丢失真实故障信息。

``checks`` 每项形如::

    {"state": "ok" | "down" | "disabled", "detail": "...", "ms": 1.2}

``disabled`` 表示"未配置且设计上允许不配"（如未设 ``REDIS_URL``），
不计入降级——否则默认的零依赖形态会永远显示黄色。
"""
from __future__ import annotations

import logging
import os
import socket
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ...common.config import settings
from ...common.platform_schema import is_sqlite_target, sqlite_path_from_url
from ..core import cache

logger = logging.getLogger(__name__)

__all__ = ["build_health_report"]

#: 单项探测的耗时上限（秒）。向量库 / LLM 走网络，给得宽一点。
_SQLITE_TIMEOUT_S = 1.0
_TCP_TIMEOUT_S = 0.6


def _probe(label: str, fn: Callable[[], tuple[str, str]]) -> dict[str, Any]:
    """执行单项探测并计时；任何异常收敛为 ``down``，绝不外抛。"""
    started = time.perf_counter()
    try:
        state, detail = fn()
    except Exception as exc:  # noqa: BLE001 - 健康检查不允许失败
        state, detail = "down", f"{type(exc).__name__}: {exc}"
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    if state == "down":
        logger.warning("[health] %s down: %s", label, detail)
    return {"state": state, "detail": detail, "ms": elapsed}


def _probe_sqlite(db_url: str, label: str) -> tuple[str, str]:
    """只读打开 SQLite 并执行 ``SELECT 1``（不建库、不写、不改 schema）。"""
    path = sqlite_path_from_url(db_url)
    if not path or path == ":memory:":
        # 内存库：进程内必然可用（连接由持有者管理），无需外部依赖
        return "ok", "内存库（进程内）"
    if not Path(path).exists():
        return "down", f"文件不存在：{path}（请先运行 init_db）"
    conn = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro",
                           uri=True, timeout=_SQLITE_TIMEOUT_S)
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return "ok", f"{label}只读连接正常"


def _probe_business_db() -> tuple[str, str]:
    """业务数据源：SQLite 走文件只读探测；MySQL 等走 1s 超时的连接探测。"""
    db_url = settings.DB_URL
    if is_sqlite_target(db_url):
        return _probe_sqlite(db_url, "业务库")
    from sqlalchemy import create_engine, text

    engine = create_engine(
        db_url, connect_args={"connect_timeout": 1}, pool_pre_ping=False)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    scheme = db_url.split("://", 1)[0]
    return "ok", f"{scheme} 连接正常"


def _probe_workflow_db() -> tuple[str, str]:
    """治理库：当前仅支持 SQLite（见 WorkflowEngine.assert_sqlite_backed）。"""
    db_url = settings.WORKFLOW_DB
    if not is_sqlite_target(db_url):
        return "down", f"治理库配置为非 SQLite（{db_url!r}），引擎将拒绝启动"
    return _probe_sqlite(db_url, "治理库")


def _probe_redis() -> tuple[str, str]:
    url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if not url:
        return "disabled", "未配置 REDIS_URL（缓存与限流关闭，业务不受影响）"
    if cache.available():
        return "ok", "连接正常"
    return "down", f"不可达：{url}（已降级为无缓存）"


def _tcp_reachable(host: str, port: int) -> None:
    """TCP 连通性探测；不可达时直接抛异常（由 ``_probe`` 收敛为 down）。

    **用总预算而不是每地址超时**：``socket.create_connection(..., timeout=X)``
    的 X 是**每个地址族**各算一次——``localhost`` 会同时解析出 ``::1`` 与
    ``127.0.0.1``，两个都不可达时实际耗时是 2X。实测 Milvus 未起时这一项
    花掉 1.2s，是整个健康检查（进而整个前端首屏探测）的最大单项。
    这里自己遍历地址列表，共用一份 deadline，保证总耗时不超过预算。
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    deadline = time.monotonic() + _TCP_TIMEOUT_S
    last_exc: Exception | None = None
    for family, socktype, proto, _canon, addr in infos:
        remain = deadline - time.monotonic()
        if remain <= 0:
            break
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(remain)
        try:
            sock.connect(addr)
            return                      # 任一地址族通了即视为可达
        except OSError as exc:
            last_exc = exc
        finally:
            sock.close()
    detail = f"最后错误：{last_exc}" if last_exc else "无可用地址"
    raise TimeoutError(f"{host}:{port} 在 {_TCP_TIMEOUT_S}s 内不可达（{detail}）")


def _probe_vector() -> tuple[str, str]:
    backend = (getattr(settings, "VECTOR_BACKEND", "chroma") or "chroma").strip().lower()
    if backend == "milvus":
        uri = getattr(settings, "MILVUS_URI", "") or ""
        parsed = urlparse(uri if "//" in uri else f"//{uri}")
        host, port = parsed.hostname or "localhost", parsed.port or 19530
        _tcp_reachable(host, port)
        return "ok", f"Milvus {host}:{port} 可达"

    # Chroma 两种形态（与 common/rag_tools._get_collection 的实际分支一致）：
    # CHROMA_HOST 非本机 → HttpClient；否则 → 本地 PersistentClient。
    chroma_host = (os.getenv("CHROMA_HOST", "localhost") or "localhost").strip()
    if chroma_host not in ("localhost", "127.0.0.1", "::1"):
        _tcp_reachable(chroma_host, int(getattr(settings, "CHROMA_PORT", 8000)))
        return "ok", f"ChromaDB HttpClient {chroma_host} 可达"

    persist_dir = Path(os.getcwd()) / "data" / "chroma"
    # chroma.sqlite3 是 PersistentClient 的元数据文件：目录存在但无此文件
    # 说明只被 makedirs 创建过、从未真正写入集合，不能算就绪。
    if not (persist_dir / "chroma.sqlite3").exists():
        return "down", f"ChromaDB 未初始化：{persist_dir}（请先运行 common.seed）"
    return "ok", f"ChromaDB 本地持久化：{persist_dir}"


def _probe_llm() -> tuple[str, str]:
    from ...common.llm_factory import llm_available

    provider = (getattr(settings, "LLM_PROVIDER", "") or "").strip()
    if llm_available():
        return "ok", f"{provider or 'auto'} 就绪"
    if not provider:
        return "disabled", "未配置 LLM 提供方（离线降级：规则路由 + 模板结论）"
    return "down", f"{provider} 不可用（相关节点将标记 degraded）"


def build_health_report() -> dict[str, Any]:
    """构建健康报告。返回结构对前端是稳定契约，字段变更需同步 app.js。

    **并行探测**：5 项依赖之间没有任何顺序依赖，串行执行等于把各自的超时相加
    （实测 Milvus 未起时 vector 0.6s + redis 0.22s + llm 0.14s ≈ 1.4s，而前端
    首屏探测就要等这么久）。并行后总耗时 = 最慢的一项。

    按名字延迟解析探测函数（``globals()``），不用字典快照——
    测试用 ``monkeypatch.setattr(health_mod, "_probe_vector", ...)`` 替换单项，
    快照会绕过替换，让"依赖挂掉要报 degraded"的用例静默失去意义。
    """
    from concurrent.futures import ThreadPoolExecutor

    order = ("business_db", "workflow_db", "redis", "vector", "llm")
    fn_names = {
        "business_db": "_probe_business_db",
        "workflow_db": "_probe_workflow_db",
        "redis": "_probe_redis",
        "vector": "_probe_vector",
        "llm": "_probe_llm",
    }

    with ThreadPoolExecutor(max_workers=len(order)) as pool:
        futures = {k: pool.submit(_probe, k, globals()[fn_names[k]]) for k in order}
        checks = {k: futures[k].result() for k in order}   # 保持稳定键序
    down = [k for k, v in checks.items() if v["state"] == "down"]

    if down:
        status, mode = "degraded", "degraded"
    elif checks["llm"]["state"] == "disabled":
        # LLM 缺席 = README 明确支持的离线降级形态：
        # 全链路可跑、结论走模板，但不等同"完整模式"。
        status, mode = "ok", "offline"
    else:
        status, mode = "ok", "full"

    return {
        "status": status,
        "mode": mode,
        "version": "2.0.0",
        "degraded_components": down,
        "checks": checks,
    }
