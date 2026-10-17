"""pytest 全局配置。

历史：V1 原型层（src/mfg_agents/{agents,graph,ui,api.py}）曾有五个 tests/test_*.py
演示脚本（顶层直接执行、无 pytest 测试函数，且依赖 Ollama / 运行中的服务），
在 pytest 收集期即报错，故用 collect_ignore 跳过。

V1 原型层移除后，这些脚本中依赖 V1 图的四个（test_e2e / test_events /
test_stream / test_chunk）已随之删除；仅保留 test_sql.py——它只依赖
mfg_agents.common.sql_tools，仍是手动运行的 Text2SQL 演示脚本。

V1 的行为回归基线已迁入 tests/v2/test_common_baseline.py（场景关键词路由、
SQL 只读守卫、事件队列契约），域结论基线固化为常量后由
tests/v2/test_e2e_v2.py::test_domain_conclusions_baseline 守护。

测试确定性（重要）：
接入云端 LLM（DeepSeek）后，若单测真实调用外网，会造成**慢、计费、结果波动**。
默认屏蔽实网 LLM：`llm_available()` 恒为 False（走 degraded 分支）。
- 需要真实 LLM 的用例：`@pytest.mark.live_llm` + 环境变量 `MFG_LIVE_LLM=1`；
- 直接测试探针实现的用例：`@pytest.mark.llm_probe`（它们自行 mock 网络）。
"""
import os
from pathlib import Path

import pytest

collect_ignore = [
    "test_sql.py",
]


@pytest.fixture(scope="session", autouse=True)
def _ensure_demo_db():
    """确保演示库存在：全新克隆没有 data/mfg_agent.db（运行时产物不入库），
    部分测试直接读取该库。仅在文件缺失时执行 init_db（幂等：DROP+CREATE+种子），
    不覆盖本地已有库。"""
    from mfg_agents.common.config import settings
    db_file = settings.DB_URL.replace("sqlite:///", "")
    if not Path(db_file).exists():
        from mfg_agents.common.init_db import init_db
        init_db()
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live_llm: 需要真实 LLM 网络的用例（默认不执行，需 MFG_LIVE_LLM=1）")
    config.addinivalue_line(
        "markers", "llm_probe: 测试探针实现本身的用例（自行 mock 网络，保持真实 _probe）")


@pytest.fixture(autouse=True)
def _offline_llm_by_default(request, monkeypatch):
    """默认让 LLM 探针返回不可用，保证单测确定性（不命中云端 API）。"""
    if os.getenv("MFG_LIVE_LLM") == "1":
        yield
        return
    if request.node.get_closest_marker("live_llm") or request.node.get_closest_marker("llm_probe"):
        yield
        return
    from mfg_agents.common import llm_factory
    llm_factory.reset_probe_cache()
    monkeypatch.setattr(llm_factory, "_probe", lambda timeout: False)
    yield
    llm_factory.reset_probe_cache()
