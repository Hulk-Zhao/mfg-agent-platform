"""LLM 探针单测（G）：校验 llm_available() **不仅看服务可达，还校验配置模型已 pull**。

全部 monkeypatch urllib 响应，**不依赖网络**。覆盖：
1. /api/tags 含 L1+L2 模型 → True；
2. 缺 L1（或 L2）模型 → False（走 degraded，这是正确生产行为）；
3. 端口不通（urlopen 抛 URLError）→ False；
4. 模型名归一化（配置无 tag ↔ 已装 :latest）→ True。
"""
import json
import urllib.error

import pytest

from mfg_agents.common.config import settings
from mfg_agents.common.llm_factory import llm_available, reset_probe_cache

# 本文件测试探针实现本身（自行 mock urllib），保持真实 _probe 逻辑
pytestmark = pytest.mark.llm_probe


class _FakeResp:
    """最小 urlopen 响应桩（支持 with 上下文 + status + read）。"""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _ollama_env(monkeypatch):
    """固定为 ollama 且模型名可预期；每个用例清空探针缓存。"""
    reset_probe_cache()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.setattr(settings, "OLLAMA_MODEL_L1", "qwen3.5:4b")
    yield
    reset_probe_cache()


def _patch_tags(monkeypatch, names: list[str]) -> None:
    body = json.dumps({"models": [{"name": n} for n in names]}).encode("utf-8")
    monkeypatch.setattr(
        "mfg_agents.common.llm_factory.urllib.request.urlopen",
        lambda *a, **k: _FakeResp(200, body))


def test_probe_true_when_both_models_pulled(monkeypatch):
    _patch_tags(monkeypatch, ["qwen3.5:9b", "qwen3.5:4b", "llama3:8b"])
    assert llm_available() is True


def test_probe_false_when_l1_model_missing(monkeypatch):
    _patch_tags(monkeypatch, ["qwen3.5:9b"])  # 缺 L1 qwen3.5:4b
    assert llm_available() is False


def test_probe_false_when_l2_model_missing(monkeypatch):
    _patch_tags(monkeypatch, ["qwen3.5:4b"])  # 缺 L2 qwen3.5:9b
    assert llm_available() is False


def test_probe_false_when_port_unreachable(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(
        "mfg_agents.common.llm_factory.urllib.request.urlopen", _raise)
    assert llm_available() is False


def test_probe_false_when_http_not_200(monkeypatch):
    monkeypatch.setattr(
        "mfg_agents.common.llm_factory.urllib.request.urlopen",
        lambda *a, **k: _FakeResp(500, b"{}"))
    assert llm_available() is False


def test_probe_normalizes_untagged_config_to_latest(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3.5")     # 无 tag
    monkeypatch.setattr(settings, "OLLAMA_MODEL_L1", "qwen3.5:4b")
    _patch_tags(monkeypatch, ["qwen3.5:latest", "qwen3.5:4b"])
    assert llm_available() is True
