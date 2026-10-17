"""DeepSeek 提供方单测：提供方自动解析 / 分级模型 / 鉴权探针。

背景：平台默认走环境变量 ds_url/ds_key 的 DeepSeek 云端（暂不用 Ollama）。
探针必须带 Authorization 头（/models 需鉴权），否则会被误判为不可用。
全部 mock urllib，不依赖网络。
"""
import json
import urllib.error

import pytest

from mfg_agents.config import settings
from mfg_agents.tools.llm_factory import _provider, get_llm, llm_available, reset_probe_cache

# 探针/分级实现本身的用例：自行 mock 网络，需真实 _probe 逻辑
pytestmark = pytest.mark.llm_probe


class _FakeResp:
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
def _ds_env(monkeypatch):
    """默认：未显式指定提供方 + 存在 DS_KEY（模拟真实环境变量）。"""
    reset_probe_cache()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "")
    monkeypatch.setattr(settings, "DS_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "DS_KEY", "sk-test-key")
    monkeypatch.setattr(settings, "DS_MODEL_L1", "deepseek-chat")
    monkeypatch.setattr(settings, "DS_MODEL_L2", "deepseek-chat")
    yield
    reset_probe_cache()


def _patch_models(monkeypatch, ids: list[str], captured: dict | None = None) -> None:
    body = json.dumps({"object": "list", "data": [{"id": i} for i in ids]}).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        if captured is not None:
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(200, body)

    monkeypatch.setattr("mfg_agents.tools.llm_factory.urllib.request.urlopen",
                        _fake_urlopen)


# ---------- 提供方解析 ----------

def test_provider_auto_detects_deepseek_with_key():
    assert _provider() == "deepseek"


def test_provider_falls_back_to_ollama_without_key(monkeypatch):
    monkeypatch.setattr(settings, "DS_KEY", "")
    assert _provider() == "ollama"


def test_explicit_provider_wins(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    assert _provider() == "ollama"  # 即使存在 DS_KEY，显式配置优先


# ---------- 分级模型 ----------

def test_get_llm_routes_tiers_to_deepseek_models(monkeypatch):
    monkeypatch.setattr(settings, "DS_MODEL_L1", "deepseek-chat")
    monkeypatch.setattr(settings, "DS_MODEL_L2", "deepseek-reasoner")
    l1 = get_llm(tier="L1")
    l2 = get_llm(tier="L2")
    assert getattr(l1, "model_name", None) == "deepseek-chat"
    assert getattr(l2, "model_name", None) == "deepseek-reasoner"
    assert "deepseek.com" in str(getattr(l1, "openai_api_base", ""))


def test_get_llm_disables_thinking_by_default():
    """用户要求：非推理节点默认关闭思考（thinking.type=disabled）。"""
    llm = get_llm(tier="L2", max_tokens=200, timeout=30)
    assert getattr(llm, "extra_body", None) == {"thinking": {"type": "disabled"}}


def test_get_llm_low_thinking_uses_reasoning_effort():
    llm = get_llm(tier="L2", thinking="low")
    assert getattr(llm, "reasoning_effort", None) == "low"


def test_get_llm_medium_high_thinking_use_reasoning_effort():
    """三档思考：medium/high 显式设置 reasoning_effort（不再依赖模型默认）。"""
    medium = get_llm(tier="L2", thinking="medium")
    high = get_llm(tier="L2", thinking="high")
    assert getattr(medium, "reasoning_effort", None) == "medium"
    assert getattr(high, "reasoning_effort", None) == "high"
    assert getattr(high, "extra_body", None) is None


def test_get_llm_rejects_unknown_thinking_mode():
    """非法档位直接报错，避免拼写错误静默变成全量思考。"""
    with pytest.raises(ValueError, match="thinking"):
        get_llm(tier="L2", thinking="ultra")


# ---------- 探针（含鉴权头） ----------

def test_probe_true_when_model_present_and_sends_auth(monkeypatch):
    captured: dict = {}
    _patch_models(monkeypatch, ["deepseek-chat", "deepseek-reasoner"], captured)
    assert llm_available() is True
    assert captured["url"].endswith("/models")
    assert captured["headers"].get("authorization") == "Bearer sk-test-key"


def test_probe_false_when_model_missing(monkeypatch):
    _patch_models(monkeypatch, ["some-other-model"])
    assert llm_available() is False


def test_probe_false_on_401(monkeypatch):
    monkeypatch.setattr(
        "mfg_agents.tools.llm_factory.urllib.request.urlopen",
        lambda *a, **k: _FakeResp(401, b'{"error":"invalid api key"}'))
    assert llm_available() is False


def test_probe_false_on_network_error(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(
        "mfg_agents.tools.llm_factory.urllib.request.urlopen", _raise)
    assert llm_available() is False
