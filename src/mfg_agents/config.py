"""集中配置：从环境变量读取。
本地开发默认 SQLite（零依赖）；Docker部署通过环境变量切到MySQL。

V2 扩展（可接入框架）：场景目录、工作流库、IM 回调配置、LLM 分级模型。
V1 既有配置项默认值全部保持不变（零破坏）。
"""
import os
from pathlib import Path
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

    # LLM 提供方：显式设置优先；留空则自动解析（有 DS_KEY → deepseek，否则 ollama）
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "")

    # Ollama（本地优先；未启动/模型未 pull 时自动降级）
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

    # OpenAI 兼容（通用兜底）
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")

    # DeepSeek 云端（直接复用环境变量 ds_url / ds_key；Windows 环境变量大小写不敏感）
    # 统一使用快模型 deepseek-flash；思考模式由 llm_factory 按节点需要控制
    DS_URL: str = os.getenv("DS_URL") or os.getenv("ds_url") or "https://api.deepseek.com"
    DS_KEY: str = os.getenv("DS_KEY") or os.getenv("ds_key") or ""
    DS_MODEL_L1: str = os.getenv("DS_MODEL_L1", "deepseek-flash")
    DS_MODEL_L2: str = os.getenv("DS_MODEL_L2", "deepseek-flash")

    # ================= V2 可接入框架 =================
    # LLM 分级：L1 小模型（意图分类/SQL 初版，高频低敏）/ L2 大模型（分析/报告）
    OLLAMA_MODEL_L1: str = os.getenv("OLLAMA_MODEL_L1", "qwen3.5:4b")

    # 场景目录（YAML 声明式场景；新场景零代码新增）
    V2_SCENARIOS_DIR: str = os.getenv(
        "V2_SCENARIOS_DIR",
        str(Path(__file__).resolve().parent / "v2" / "scenarios")
    )

    # 工作流库（SQLite：platform_workflow_task 工单/状态机/SLA/HITL 队列）
    WORKFLOW_DB: str = os.getenv("WORKFLOW_DB", DB_URL)

    # 企业微信回调配置（自建应用：URL+Token+AES；留空时回调端点 fail-fast 报错）
    IM_TOKEN: str = os.getenv("IM_TOKEN", "")
    IM_AES_KEY: str = os.getenv("IM_AES_KEY", "")
    IM_APPID: str = os.getenv("IM_APPID", "")


settings = Settings()
