"""集中配置：从环境变量读取。
本地开发默认 SQLite（零依赖）；Docker部署通过环境变量切到MySQL。
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # 数据库：本地默认SQLite；Docker部署时设置 DB_URL=mysql+pymysql://...
    DB_URL: str = os.getenv(
        "DB_URL",
        f"sqlite:///{os.path.join(os.getcwd(), 'data', 'mfg_agent.db')}"
    )

    # ChromaDB：本地持久化（见 rag_tools.py）
    CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
    CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))

    # LLM
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")


settings = Settings()
