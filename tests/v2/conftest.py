"""v2 测试公共夹具。

只放**可选**夹具（无 autouse），不影响既有用例的收集与执行。
案例域需要一套隔离的运行时：临时 SQLite + 内存向量后端 + 幂等种子，
否则测试会踩到 ``data/case.db`` 的真实数据、互相污染。
"""
from __future__ import annotations

# ============================================================================
# ⚠️ 并发护栏：同一台机器上**同时跑两个 pytest** 会共享 SQLite 文件与
# ``--basetemp``，表现为成片的 setup error（曾经出现过"单跑全绿、全量一片红"
# 的假象，根因就是它）。这里在会话级做一次粗粒度检测：发现已有另一个
# pytest 进程在跑就直接报错，而不是让人去逐条排查 setup error。
#
# 另外：**不要用固定的 --basetemp**。请统一用
#   pytest tests/ -q --basetemp=./_qa/pt_$(date +%H%M%S)
# 否则两次运行会互相删对方的临时目录（Windows 的批量删除保护会直接 SIGKILL）。
# ============================================================================

from pathlib import Path

import pytest

#: 仓库根 → data/case_seed.json
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_SEED = _PROJECT_ROOT / "data" / "case_seed.json"


@pytest.fixture(scope="module")
def case_client(tmp_path_factory):
    """案例域的 HTTP 客户端（真实 FastAPI 路由 + 隔离库）。

    为什么必须走 HTTP 而不是直调服务层：令牌解析、错误码映射（401/403/404）、
    请求模型（**没有 filter 字段**）都只在 API 层成立。服务层测试证明不了
    "调用方无法注入过滤条件"这件事。

    环境覆盖在模块范围内生效，退出时逐项还原——避免把 ``CASE_DB`` 之类的
    全局配置泄漏给同进程的其它测试模块。
    """
    from mfg_agents.common.config import settings

    saved: dict[str, object] = {}
    overrides = {
        "CASE_DB": str(tmp_path_factory.mktemp("case_http") / "case.db"),
        "CASE_SEED_PATH": str(CASE_SEED),
        "CASE_ENABLED": True,
        "CASE_DEMO_MODE": True,
        "CASE_VECTOR_BACKEND": "memory",
        "CASE_EXPOSE_HIDDEN_COUNT": True,
        # k 上限放宽到 1000：种子已扩到 150 例（含插件/pack/整机/多职能域），
        # 部分主体可见 chunk 已超过 200 条，旧上限会把 k 直接钳掉，
        # "候选不足 / k 放大不补齐 / hidden_count 口径" 几条不变量就测不到。
        # 注意：这里放的是**测试环境**上限；产品默认值仍由 test_case_demo_corpus.py 守护。
        "CASE_K_MAX": 1000,
        # 演示"检索全可见"开关：测试里**必须显式关闭**，否则跨部门/跨工厂的
        # 越权守护用例（红队 + e2e）会被演示语义放行而失真。
        "CASE_DEMO_OPEN_QUERY": False,
        # B8 限流在测试里放宽：红队会以同一主体发起 100+ 次请求，
        # 用生产值（60/分钟）会把用例自己撞成 429。专项限流用例
        # （test_case_rate_limit.py）自己把阈值调低，不依赖这里。
        "CASE_RATE_LIMIT_PER_MIN": 100000,
    }
    for key, value in overrides.items():
        saved[key] = getattr(settings, key, None)
        setattr(settings, key, value)

    from mfg_agents.v2.services import case_runtime, principal_resolver

    case_runtime.reset_case_runtime()
    principal_resolver.reset_principal_version_store()
    from mfg_agents.v2.security import case_rate_limit as _crl
    _crl.reset_case_rate_limit()

    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    client = TestClient(app)
    try:
        yield client
    finally:
        _crl.reset_case_rate_limit()
        case_runtime.reset_case_runtime()
        principal_resolver.reset_principal_version_store()
        for key, value in saved.items():
            setattr(settings, key, value)


@pytest.fixture(scope="session", autouse=True)
def concurrent_pytest_guard():
    """会话级：检测是否**同时**有另一个 pytest 在跑（同机并行是最常见的假红来源）。"""
    import os
    import subprocess
    import sys

    if os.name == "nt":
        try:
            # tasklist 在中文 Windows 上输出 GBK，必须按 bytes 取再容错解码，
            # 否则 text=True 会直接抛 UnicodeDecodeError（检测器自己把测试搞挂）。
            raw = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                capture_output=True, timeout=15).stdout or b""
            out = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 检测失败不应挡测试
            yield
            return
        pids = [line.split(",")[1].strip('"')
                for line in out.splitlines()[1:] if line.count(",") > 1]
        mine = str(os.getpid())
        others = [p for p in pids if p.isdigit() and p != mine]
    else:
        try:
            out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                                 text=True, timeout=15).stdout
        except Exception:  # noqa: BLE001
            yield
            return
        others = []
        for line in out.splitlines():
            if "pytest" in line and str(os.getpid()) not in line.split()[0]:
                others.append(line.split()[0])
    if others:
        msg = (f"检测到其它 pytest 进程 {others}；并行跑测试会共享 SQLite 与 "
               "--basetemp，产生假红。请确认后再解读本次结果。")
        # 默认只警告：硬失败会在正常 CI（并行分片）里误伤。
        # 需要严格时置 CASE_TEST_STRICT_CONCURRENCY=1。
        strict = os.environ.get("CASE_TEST_STRICT_CONCURRENCY", "").strip() in (
            "1", "true", "TRUE", "yes")
        if strict:
            raise RuntimeError(f"[concurrent-pytest-guard] {msg}")
        print(f"\n[concurrent-pytest-guard] 警告：{msg}\n", file=sys.stderr)
    yield
