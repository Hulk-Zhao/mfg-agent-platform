"""pytest 全局配置。

V1 基线的 tests/test_*.py 五个文件是**演示脚本**（顶层直接执行、无 pytest
测试函数，且依赖 Ollama / 运行中的服务），在 pytest 收集期即报错。
处理方式（V1 文件零改动）：
- collect_ignore 跳过这些脚本的收集——它们仍可 `uv run python tests/test_e2e.py` 手动运行；
- 等价覆盖移至 tests/test_v1_regression.py（离线可跑的 pytest 回归：
  图可编译、路由规则、SQL 只读守卫、域 Agent 结论）。

测试确定性（重要）：
接入云端 LLM（DeepSeek）后，若单测真实调用外网，会造成**慢、计费、结果波动**。
默认屏蔽实网 LLM：`llm_available()` 恒为 False（走 degraded 分支）。
- 需要真实 LLM 的用例：`@pytest.mark.live_llm` + 环境变量 `MFG_LIVE_LLM=1`；
- 直接测试探针实现的用例：`@pytest.mark.llm_probe`（它们自行 mock 网络）。
"""
import os

import pytest

collect_ignore = [
    "test_e2e.py",
    "test_sql.py",
    "test_events.py",
    "test_stream.py",
    "test_chunk.py",
]


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
    from mfg_agents.tools import llm_factory
    llm_factory.reset_probe_cache()
    monkeypatch.setattr(llm_factory, "_probe", lambda timeout: False)
    yield
    llm_factory.reset_probe_cache()
