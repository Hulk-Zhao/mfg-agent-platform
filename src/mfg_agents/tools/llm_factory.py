"""LLM 工厂：统一返回 ChatModel。"""
from langchain_core.language_models.chat_models import BaseChatModel
from ..config import settings


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    if settings.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.OLLAMA_MODEL,
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
