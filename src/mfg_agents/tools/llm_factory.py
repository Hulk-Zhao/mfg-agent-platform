"""LLM 工厂：统一返回 ChatModel（V1 行为零破坏 + V2 分级扩展）。

V2 扩展（ARCHITECTURE-V2.md §6.2）：
- get_llm(tier="L1"|"L2")：分级模型路由。默认 tier="L2"、
  temperature=0.0，与 V1 调用点行为完全一致（V1 代码零改动）；
- llm_available()：LLM 可用性探针（带 TTL 缓存）。路由降级、
  FA/报告模板化、Text2SQL 人工兜底都依赖此探针实现 offline 可跑。
"""
import json
import time
import urllib.error
import urllib.request
from langchain_core.language_models.chat_models import BaseChatModel

from ..config import settings

_TIERS = ("L1", "L2")

# 探针缓存：(monotonic 时间戳, 可用性)
_probe_cache: dict[str, tuple[float, bool]] = {}


def get_llm(temperature: float = 0.0, *, tier: str = "L2") -> BaseChatModel:
    """返回 ChatModel。

    tier="L2"（默认）：大模型，分析推理/RAG/报告生成（qwen3.5:9b 本地优先）；
    tier="L1"：小模型，意图分类/路由/SQL 初版（qwen3.5:4b，高频低敏）。
    """
    if tier not in _TIERS:
        raise ValueError(f"tier 必须是 {_TIERS}，收到 {tier!r}")
    if settings.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.OLLAMA_MODEL if tier == "L2" else settings.OLLAMA_MODEL_L1,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=temperature,
        )
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        base_url=settings.OPENAI_BASE_URL,
        api_key=settings.OPENAI_API_KEY,
        temperature=temperature,
    )


def llm_available(timeout: float = 1.5, cache_ttl: float = 30.0) -> bool:
    """探测 LLM 服务**可用且已就绪**（默认缓存 30s，避免每次路由都发探测请求）。

    ollama：GET {base}/api/tags，且**校验 L1/L2 两级配置的模型均已 pull**；
            模型缺失 → False（否则 ChatOllama 会 model-not-found，生产也不会降级）。
    openai 兼容：GET {base}/models，校验配置的 OPENAI_MODEL 存在。
    探测失败（连接拒绝/超时/解析失败）返回 False——调用方据此走降级路径。
    """
    now = time.monotonic()
    cached = _probe_cache.get("status")
    if cached is not None and now - cached[0] < cache_ttl:
        return cached[1]
    available = _probe(timeout)
    _probe_cache["status"] = (now, available)
    return available


def _normalize_model_name(name: str) -> str:
    """模型名归一化：去空白/小写；无 tag 时补 ``:latest``（对齐 Ollama 默认语义）。"""
    lowered = (name or "").strip().lower()
    if not lowered:
        return ""
    return lowered if ":" in lowered else lowered + ":latest"


def _probe(timeout: float) -> bool:
    if settings.LLM_PROVIDER == "ollama":
        return _probe_ollama(timeout)
    return _probe_openai(timeout)


def _http_get_json(url: str, timeout: float) -> object:
    """GET url 并解析 JSON；任何网络/解析异常返回 None。"""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            raw = resp.read()
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _probe_ollama(timeout: float) -> bool:
    """Ollama 探针：服务可达 **且** L1/L2 两级配置模型均已安装。"""
    url = settings.OLLAMA_BASE_URL.rstrip("/") + "/api/tags"
    payload = _http_get_json(url, timeout)
    if not isinstance(payload, dict):
        return False
    installed: set[str] = set()
    for model in payload.get("models") or []:
        if isinstance(model, dict):
            name = model.get("name") or model.get("model") or ""
            normalized = _normalize_model_name(str(name))
            if normalized:
                installed.add(normalized)
    # 同时校验 L1（小模型）与 L2（大模型）——任一缺失都视为不可用（走 degraded）
    required = {
        _normalize_model_name(settings.OLLAMA_MODEL),      # L2
        _normalize_model_name(settings.OLLAMA_MODEL_L1),   # L1
    }
    required.discard("")
    if not required:
        return bool(installed)
    return required <= installed


def _probe_openai(timeout: float) -> bool:
    """OpenAI 兼容探针：服务可达 **且** 配置模型存在（未知配置时仅判可达）。"""
    url = settings.OPENAI_BASE_URL.rstrip("/") + "/models"
    payload = _http_get_json(url, timeout)
    if not isinstance(payload, dict):
        return False
    configured = (settings.OPENAI_MODEL or "").strip()
    if not configured:
        return True
    ids = {str(m.get("id") or "") for m in (payload.get("data") or [])
           if isinstance(m, dict)}
    return configured in ids


def reset_probe_cache() -> None:
    """清空探针缓存（测试注入用）。"""
    _probe_cache.clear()
