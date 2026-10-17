"""LLM 工厂：统一返回 ChatModel（V1 行为零破坏 + V2 分级扩展）。

V2 扩展（ARCHITECTURE-V2.md §6.2）：
- get_llm(tier="L1"|"L2")：分级模型路由。默认 tier="L2"、
  temperature=0.0，与 V1 调用点行为完全一致（V1 代码零改动）；
- llm_available()：LLM 可用性探针（带 TTL 缓存）。路由降级、
  FA/报告模板化、Text2SQL 人工兜底都依赖此探针实现 offline 可跑。

提供方解析（_provider）：显式 LLM_PROVIDER 优先；未设置时若存在
DeepSeek 凭据（环境变量 ds_url/ds_key）则走 deepseek，否则回落 ollama。
"""
import json
import logging
import time
import urllib.error
import urllib.request

from langchain_core.language_models.chat_models import BaseChatModel

from ..config import settings

logger = logging.getLogger(__name__)

_TIERS = ("L1", "L2")

# 探针缓存：(monotonic 时间戳, 可用性)
_probe_cache: dict[str, tuple[float, bool]] = {}


def _provider() -> str:
    """解析当前 LLM 提供方：显式配置优先，其次看 DeepSeek 凭据，最后回落 Ollama。"""
    explicit = (settings.LLM_PROVIDER or "").strip().lower()
    if explicit:
        return explicit
    if (settings.DS_KEY or "").strip():
        return "deepseek"
    return "ollama"


# 思考档位 → DeepSeek 网关参数映射：
# - disabled：关闭思考（thinking.type=disabled），高频/结构化节点零推理延迟；
# - low/medium/high：reasoning_effort 分档（同一参数族，网关按档位分配思考预算）。
_THINKING_MODES: dict[str, dict] = {
    "disabled": {"extra_body": {"thinking": {"type": "disabled"}}},
    "low": {"reasoning_effort": "low"},
    "medium": {"reasoning_effort": "medium"},
    "high": {"reasoning_effort": "high"},
}


def get_llm(temperature: float = 0.0, *, tier: str = "L2",
            max_tokens: int | None = None,
            timeout: float | None = None,
            thinking: str | None = None) -> BaseChatModel:
    """返回 ChatModel。

    tier="L2"（默认）：分析/报告类模型；tier="L1"：低延迟模型（意图/路由/SQL 初版）。
    max_tokens / timeout：云端模型建议显式设置，避免长文本生成拖慢演示链路。
    thinking：思考强度 —— "disabled"（默认，关闭思考）/ "low" / "medium" / "high"
    （reasoning_effort 分档）。非法值直接报错，避免拼写错误静默变成全量思考。
    """
    if tier not in _TIERS:
        raise ValueError(f"tier 必须是 {_TIERS}，收到 {tier!r}")
    mode = (thinking or "disabled").strip().lower()
    if mode not in _THINKING_MODES:
        raise ValueError(f"thinking 必须是 {sorted(_THINKING_MODES)}，收到 {thinking!r}")
    provider = _provider()
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        kwargs: dict = {}
        if max_tokens is not None:
            kwargs["num_predict"] = max_tokens
        return ChatOllama(
            model=settings.OLLAMA_MODEL if tier == "L2" else settings.OLLAMA_MODEL_L1,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=temperature,
            **kwargs,
        )
    if provider == "deepseek":
        from langchain_openai import ChatOpenAI
        extra: dict = dict(_THINKING_MODES[mode])
        return ChatOpenAI(
            model=settings.DS_MODEL_L2 if tier == "L2" else settings.DS_MODEL_L1,
            base_url=settings.DS_URL,
            api_key=settings.DS_KEY,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **extra,
        )
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        base_url=settings.OPENAI_BASE_URL,
        api_key=settings.OPENAI_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
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
    provider = _provider()
    if provider == "ollama":
        return _probe_ollama(timeout)
    if provider == "deepseek":
        return _probe_cloud(
            timeout, settings.DS_URL, settings.DS_KEY,
            {settings.DS_MODEL_L2, settings.DS_MODEL_L1})
    return _probe_cloud(
        timeout, settings.OPENAI_BASE_URL, settings.OPENAI_API_KEY,
        {settings.OPENAI_MODEL})


def _http_get_json(url: str, timeout: float,
                   headers: dict[str, str] | None = None) -> object:
    """GET url 并解析 JSON；任何网络/解析异常返回 None。"""
    try:
        req = urllib.request.Request(url, method="GET", headers=headers or {})
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


def _probe_cloud(timeout: float, base_url: str, api_key: str,
                 required_models: set[str]) -> bool:
    """云端 OpenAI 兼容探针（DeepSeek/OpenAI）：需鉴权，且校验配置模型存在。

    关键：/models 通常要求 Authorization 头，缺头会 401——旧实现未带头，
    导致"有 key 的云端服务被误判为不可用"。
    """
    headers = {"Accept": "application/json"}
    if (api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    url = (base_url or "").rstrip("/") + "/models"
    payload = _http_get_json(url, timeout, headers=headers)
    if not isinstance(payload, dict):
        return False
    required = {str(m).strip() for m in required_models if str(m or "").strip()}
    if not required:
        return True
    ids = {str(m.get("id") or "") for m in (payload.get("data") or [])
           if isinstance(m, dict)}
    if not required <= ids:
        # 模型名与账号不匹配：打印可用清单，便于通过 DS_MODEL_L1/L2 纠正配置
        logger.warning(
            "配置模型未在 /models 返回: 配置=%s 可用=%s（请设置 DS_MODEL_L1/DS_MODEL_L2）",
            sorted(required), sorted(ids))
        return False
    return True


def reset_probe_cache() -> None:
    """清空探针缓存（测试注入用）。"""
    _probe_cache.clear()
