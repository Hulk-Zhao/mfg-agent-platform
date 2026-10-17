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

    # 向量库后端：chroma（默认，本地持久化/HttpClient）| milvus（Docker standalone）
    VECTOR_BACKEND: str = os.getenv("VECTOR_BACKEND", "chroma")
    MILVUS_URI: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    # Milvus 模式：BGE-M3 嵌入 + BGE-Reranker-v2-M3 重排（本地模型路径，
    # 相对路径按运行目录解析；来源 D:\EDURAG\Itcast_qa_system\rag_qa\models）
    MILVUS_EMBEDDING_MODEL: str = os.getenv("MILVUS_EMBEDDING_MODEL", "models/bge-m3")
    MILVUS_RERANKER_MODEL: str = os.getenv("MILVUS_RERANKER_MODEL", "models/bge-reranker-v2-m3")
    # 混合召回候选数（送入重排前的 top-k，取 max(candidates, n_results*3)）
    MILVUS_RERANKER_CANDIDATES: int = int(os.getenv("MILVUS_RERANKER_CANDIDATES", "12"))

    # Redis 缓存层（可选）：留空即禁用缓存；连接失败自动降级（见 v2/core/cache.py）
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    # 查询结果缓存 TTL（秒）/ 单 IP 每分钟查询上限
    CACHE_TTL_S: int = int(os.getenv("CACHE_TTL_S", "600"))
    RATE_LIMIT_PER_MIN: int = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))

    # LLM 提供方：显式设置优先；留空则自动解析（有 DS_KEY → deepseek，否则 ollama）
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "")

    # Ollama（本地优先；未启动/模型未 pull 时自动降级）
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

    # OpenAI 兼容（通用兜底）
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-flash")

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

    # 办公后端选择：demo（内存种子，默认）| feishu（飞书开放平台适配层）
    # 选 feishu 时缺凭据即启动报错（配置错误必须显式暴露，不静默回落 demo）
    OFFICE_BACKEND: str = os.getenv("OFFICE_BACKEND", "demo")

    # 飞书（Lark）开放平台——会议室预订（P1-A，见 v2/services/feishu_calendar.py）
    # 自建应用凭据（开发者后台 → 凭据与基础信息）；缺失时 build_feishu_backend 显式报错
    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")
    FEISHU_BASE_URL: str = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn")
    # 承载会议室日程的日历 ID（应用主日历或会议室共享日历；GET /open-apis/calendar/v4/calendars 可查）
    FEISHU_CALENDAR_ID: str = os.getenv("FEISHU_CALENDAR_ID", "")
    # 会议室层级 ID（留空 = 根层级，列出全部会议室）
    FEISHU_ROOM_LEVEL_ID: str = os.getenv("FEISHU_ROOM_LEVEL_ID", "")
    # 时区（日程时间戳按此解析；与 RFC3339 输出一致）
    FEISHU_TZ: str = os.getenv("FEISHU_TZ", "Asia/Shanghai")
    FEISHU_TIMEOUT_S: int = int(os.getenv("FEISHU_TIMEOUT_S", "10"))

    # 认证：岗位令牌签名密钥（F2）。留空则用进程级随机密钥（重启即失效并告警）；
    # 对外演示 / 生产必须显式配置，否则等同「链接即权限」。
    AUTH_SECRET: str = os.getenv("AUTH_SECRET", "")
    # 令牌有效期（秒），默认 8 小时（覆盖一个工作日）
    AUTH_TOKEN_TTL_S: int = int(os.getenv("AUTH_TOKEN_TTL_S", str(8 * 3600)))


settings = Settings()
