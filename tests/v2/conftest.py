"""v2 测试公共夹具。

只放**可选**夹具（无 autouse），不影响既有用例的收集与执行。
案例域需要一套隔离的运行时：临时 SQLite + 内存向量后端 + 幂等种子，
否则测试会踩到 ``data/case.db`` 的真实数据、互相污染。
"""
from __future__ import annotations

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
        # k 上限放宽到 50：红队要验"k 放大也不补齐候选"，
        # 默认上限 20 会把 k=50 直接钳掉，测不到那条不变量。
        "CASE_K_MAX": 50,
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
