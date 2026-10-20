"""集中配置：从环境变量读取。
本地开发默认 SQLite（零依赖）；Docker部署通过环境变量切到MySQL。

V2 扩展（可接入框架）：场景目录、工作流库、IM 回调配置、LLM 分级模型。
V1 既有配置项默认值全部保持不变（零破坏）。
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

#: 本地零依赖默认 SQLite 路径（DB_URL / WORKFLOW_DB 共用，保证单库）
_DEFAULT_SQLITE_URL = f"sqlite:///{os.path.join(os.getcwd(), 'data', 'mfg_agent.db')}"


def _env_or(name: str, default: str) -> str:
    """读取环境变量，**空白值视同未设置**。

    直接 ``os.getenv(name, default)`` 有个坑：``.env`` 里写了 ``DB_URL=``
    （留空待填）时返回的是空字符串而不是默认值，而空串会被
    ``sqlite3.connect("")`` 当成**临时匿名库**——进程能跑，但所有数据写完即丢，
    且不报任何错。这种"静默连到空库"比启动失败更难排查，故此处统一归一。
    """
    return (os.getenv(name) or "").strip() or default


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量（``1/true/yes/on`` 为真，``0/false/no/off`` 为假）。

    未设置或空白 → 用默认值；**无法识别的写法按默认值处理**——布尔项写错不至于让
    服务起不来，但会走默认（安全的）分支。
    """
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on", "y"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return bool(default)


def _resolve_workflow_db(db_url: str) -> str:
    """解析 WORKFLOW_DB 的默认值：**绝不继承非 SQLite 的 DB_URL**。

    为什么必须单独解析：治理写路径（WorkflowEngine）直连 sqlite3，不走
    SQLAlchemy，只认文件路径。历史上 ``WORKFLOW_DB`` 直接默认成 ``DB_URL``，
    一旦 DB_URL 切成 ``mysql+pymysql://…``，这个 URL 会被当作文件路径交给
    ``sqlite3.connect()``——Linux 下静默建出一个文件名即整条 URL 的怪库，
    Windows 下文件名含 ``:`` 直接抛异常。两者都是事故，故在此切断继承。

    - 显式配置 ``WORKFLOW_DB`` → 原样使用（由 WorkflowEngine 再做 scheme 校验）；
    - ``DB_URL`` 是 SQLite（含裸路径）→ 沿用，保持"本地单库"既有行为；
    - ``DB_URL`` 是 MySQL 等非 SQLite → 回落默认 SQLite 文件。
    """
    explicit = (os.getenv("WORKFLOW_DB") or "").strip()
    if explicit:
        return explicit
    if "://" not in db_url or db_url.split("://", 1)[0].lower().startswith("sqlite"):
        return db_url
    return _DEFAULT_SQLITE_URL


class Settings:
    # 数据库：本地默认SQLite；Docker部署时设置 DB_URL=mysql+pymysql://...
    # 留空（含 .env 里写 `DB_URL=`）→ 回落本地 SQLite，不会连到匿名临时库
    DB_URL: str = _env_or("DB_URL", _DEFAULT_SQLITE_URL)

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
    # 注意：本文件位于 common/ 下，v2/ 与其同级（mfg_agents/v2/），
    # 故需再上一级（parent.parent）。历史上本文件在 mfg_agents/ 根下用 parent，
    # 上移到 common/ 后必须改为 parent.parent，否则解析成 common/v2 而加载不到场景。
    V2_SCENARIOS_DIR: str = os.getenv(
        "V2_SCENARIOS_DIR",
        str(Path(__file__).resolve().parent.parent / "v2" / "scenarios")
    )

    # 工作流库（SQLite：platform_workflow_task 工单/状态机/SLA/HITL 队列）
    # ⚠ 治理写路径**当前只支持 SQLite**（WorkflowEngine 直连 sqlite3，不走 SQLAlchemy）。
    # 因此不复用 DB_URL：切换 MySQL 时治理库必须显式指向本地 SQLite 文件，
    # 否则会把 MySQL URL 当文件路径交给 sqlite3.connect（静默数据分裂）。
    # 详见 README「生产化清单」与该函数的 docstring。
    WORKFLOW_DB: str = _resolve_workflow_db(DB_URL)

    # 企业微信回调配置（自建应用：URL+Token+AES；留空时回调端点 fail-fast 报错）
    IM_TOKEN: str = os.getenv("IM_TOKEN", "")
    IM_AES_KEY: str = os.getenv("IM_AES_KEY", "")
    # 注意：回调侧本字段是**企业 ID（CorpID）**（解密后归属校验用它），非应用 AgentId
    IM_APPID: str = os.getenv("IM_APPID", "")

    # 企业微信**应用消息**（主动推送 / @ 责任人）凭据：
    # gettoken(corpid+corpsecret) → message/send(touser+agentid)。
    # 三者齐全时现场群通道的「责任人」投递走真实 API；缺一即如实记账为 failed。
    # IM_CORP_ID 留空则回落 IM_APPID（回调侧同一字段，避免同一份凭据配两遍）。
    IM_CORP_ID: str = os.getenv("IM_CORP_ID", "")
    IM_AGENT_ID: str = os.getenv("IM_AGENT_ID", "")
    IM_CORP_SECRET: str = os.getenv("IM_CORP_SECRET", "")

    # 现场看板推送的 IM 触达：工段 → 现场群映射目录（结构在 YAML，凭据在 env）
    # 目录缺省用仓库内 v2/data/im_groups.yaml；群机器人 key / 责任人 userid 由
    # IM_GROUP_KEY_<SLUG> / IM_GROUP_MEMBERS_<SLUG> 注入（见 services/im_groups.py）。
    IM_GROUPS_PATH: str = os.getenv("IM_GROUPS_PATH", "")

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

    # ================= 案例助手（Case Assistant）=================
    # 说明：以下 CASE_* 均为**新增项**，不改变任何既有字段的语义与默认值。
    #
    # 案例库（关系库 = 真相源；向量库是可重建的派生索引）
    CASE_DB: str = _env_or(
        "CASE_DB", os.path.join(os.getcwd(), "data", "case.db"))
    # 案例总开关：false 时案例路由返回 503（架构 §11.6 回滚开关）
    CASE_ENABLED: bool = _env_bool("CASE_ENABLED", True)
    # 演示模式：允许 POST /api/v2/cases/session 自选演示身份（生产必须 false）
    CASE_DEMO_MODE: bool = _env_bool("CASE_DEMO_MODE", True)
    # 演示模式：检索/预览的业务维（部门/工厂/产线/客户/供应商/项目）对正式员工放行，
    # 但 read/cite（读全文/引用）仍按 ACL 判定 → "检索全可见、查看需申请"。
    # 硬维度（租户/密级/状态/知识层）与显式拒绝照常独立否决。
    # 只在演示环境开启；生产必须 false（否则等于放弃"权限前置"的差异化）。
    CASE_DEMO_OPEN_QUERY: bool = _env_bool("CASE_DEMO_OPEN_QUERY", False)
    # 向量后端：memory（默认，纯内存确定性）| chroma（dev/POC）| milvus（生产）
    CASE_VECTOR_BACKEND: str = os.getenv("CASE_VECTOR_BACKEND", "memory")
    # Chroma 持久化目录（仅 CASE_VECTOR_BACKEND=chroma 时使用）
    CASE_CHROMA_DIR: str = os.getenv("CASE_CHROMA_DIR", "data/chroma_case")
    # 「另有 N 条因权限不可见」是否展示（架构 §7.6 / §15 P3，默认开启）
    CASE_EXPOSE_HIDDEN_COUNT: bool = _env_bool("CASE_EXPOSE_HIDDEN_COUNT", True)
    # 重排适配器：deterministic（默认，确定性加权）| bge（生产，需模型）
    CASE_RERANK: str = os.getenv("CASE_RERANK", "deterministic")
    # 种子数据（首次启动时幂等导入；置空则不导入）
    CASE_SEED_PATH: str = os.getenv(
        "CASE_SEED_PATH", os.path.join(os.getcwd(), "data", "case_seed.json"))
    # 默认租户（多工厂多法人时由主数据/SSO 提供）
    CASE_DEFAULT_TENANT: str = os.getenv("CASE_DEFAULT_TENANT", "T001")
    # 默认返回条数 / 上限
    CASE_K_DEFAULT: int = int(os.getenv("CASE_K_DEFAULT", "5"))
    # k 上限：刻意压到 30。语料扩到 ~100 例 / 350+ chunk 后，若 k 上限过大，
    # 任何内部岗位都能取满 k —— "候选不足 + 不可见 N 条"这条差异化提示就永远
    # 演示不出来（k 必须真的能截断）。
    CASE_K_MAX: int = int(os.getenv("CASE_K_MAX", "30"))
    # 脱敏补充词表（逗号分隔，命中的词替换为 <PERSON>）。
    # 用途：词表外的人名（客户联系人、现场工程师）在没有 NER 的演示环境里
    # 只能靠"显式声明"兜住——宁可让运维多填一行，也不要放过一个真名。
    CASE_REDACTION_EXTRA_TERMS: str = os.getenv("CASE_REDACTION_EXTRA_TERMS", "")
    # 跨部门访问申请的授权有效期（天；到期惰性回收）
    CASE_ACCESS_GRANT_TTL_DAYS: int = int(
        os.getenv("CASE_ACCESS_GRANT_TTL_DAYS", "30"))
    # 演示专用：跨部门访问申请的「自动审批」开关（默认开，**仅演示**）。
    #
    # 打开时的链路：提交申请 → 前端弹窗分步展示「智能 Agent 受理 → 推断职能部门 →
    # 推送审批通道 → 职能部门审批通过」→ 服务端由 DemoAgentApprover 以**归属
    # 职能部门管理岗的演示身份**走同一条 decide()（ACL 写入 / 有效期 / 索引重建 /
    # 审计一条不少，审计 action 打 ``.demo`` 后缀显式标记）。
    #
    # 为什么默认开：本开关只在 CASE_DEMO_MODE=true 时生效（见
    # services/case_auto_approve.AccessApprover），而 CASE_DEMO_MODE 本身就是
    # "演示环境"的部署开关；评审现场要能直接看到完整审批链路。
    #
    # 预留接口：生产置 false，由真实审批流（OA / IM 审批）实现同一个
    # AccessApprover 接口——职能部门负责人本人点同意后回调 decide()，
    # 服务端代码零改动。Agent 依旧只路由不审批（AGENT_FORBIDDEN_ACTIONS 不变）。
    CASE_ACCESS_AUTO_APPROVE_DEMO: bool = _env_bool(
        "CASE_ACCESS_AUTO_APPROVE_DEMO", True)
    # B8：单主体（user+IP 组合桶）每分钟可发起的案例检索/读取次数上限。
    # 超限返回 429 —— 用限流掐断探测流量，而不是削减审计写入：
    # 少写一条审计就会把 B1 刚消掉的"时间侧信道"请回来。
    # 配成 0 或负数表示关闭（仅限本地调试）。
    CASE_RATE_LIMIT_PER_MIN: int = int(os.getenv("CASE_RATE_LIMIT_PER_MIN", "60"))

    # ---------- 有界 Agentic 检索闭环（PRD FR-02 / FR-03）----------
    # 总开关：false 时问答退化为"单轮检索"（即升级前行为），用于回滚与对照基线。
    CASE_AGENTIC_ENABLED: bool = _env_bool("CASE_AGENTIC_ENABLED", True)
    # 本体关系跳数硬上限（PRD ≤3）：触顶即终止，如实返回当前结果。
    CASE_AGENTIC_MAX_HOPS: int = int(os.getenv("CASE_AGENTIC_MAX_HOPS", "3"))
    # 主查询轮数上限：复合问句最多拆几路去召回（第 4 步起的关系扩展轮**另计**，
    # 由 MAX_HOPS 约束，总轮数上限 = MAX_ROUNDS + MAX_HOPS）。
    CASE_AGENTIC_MAX_ROUNDS: int = int(os.getenv("CASE_AGENTIC_MAX_ROUNDS", "3"))
    # 相对收益阈值：本轮新增案例数 / 已命中案例数 低于该值即认为"无收益"并终止。
    # 0 表示关闭该判据（仅靠轮数与跳数刹车）。
    CASE_AGENTIC_GAIN_THRESHOLD: float = float(
        os.getenv("CASE_AGENTIC_GAIN_THRESHOLD", "0.05"))

    # ---------- 作答器（PRD FR-01）----------
    # llm：大模型作答（不可达/超时/解析失败 → 自动降级为模板，响应里标注）；
    # template：确定性模板作答（离线可跑，测试基线用）。
    CASE_ANSWERER: str = os.getenv("CASE_ANSWERER", "llm")
    # 作答调用超时（秒）与输出字数上限——云端模型必须显式设限，
    # 否则长文本生成会拖垮演示链路（对齐 KBQA 输出纪律）。
    CASE_ANSWER_TIMEOUT: float = float(os.getenv("CASE_ANSWER_TIMEOUT", "20"))
    CASE_ANSWER_MAX_CHARS: int = int(os.getenv("CASE_ANSWER_MAX_CHARS", "200"))

    # ---------- 三方对照演示（PRD FR-06）----------
    # 仅演示环境开放：同一问题并列「无本体 / 单跳本体 / 本体增强 Agentic」三组结果。
    # 默认 false——生产环境不允许出现第二/第三种检索行为。
    CASE_DEMO_COMPARE: bool = _env_bool("CASE_DEMO_COMPARE", False)

    # ================= 数据速查：查询权限申请（演示自动审批）=================
    # 说明：以下 DATA_ACCESS_* 均为**新增项**，不改变任何既有字段的语义与默认值。
    #
    # 数据域没有逐条 ACL，权限是「表级 × 角色」（TABLE_ACL）；无权限岗位在「数据速查」
    # 页可发起查询权限申请，演示模式下由**表归属职责岗**的演示身份走同一条
    # "审批通过 → 写入运行时授权叠加层"的路径（见 services/data_access.py，事件打
    # .demo 后缀）。与案例一致：**仅在 CASE_DEMO_MODE=true 时生效**；生产置 false，
    # 由真实审批流回调同一路径（服务端代码零改动）。
    DATA_ACCESS_AUTO_APPROVE_DEMO: bool = _env_bool(
        "DATA_ACCESS_AUTO_APPROVE_DEMO", True)
    # 数据查询授权的有效期（天；到期由查询路径惰性回收）。
    DATA_ACCESS_GRANT_TTL_DAYS: int = int(
        os.getenv("DATA_ACCESS_GRANT_TTL_DAYS", "30"))

    # ================= 企业问答助手（kbqa）FAQ 前置层 =================
    # ADR-001：L1 制度直答阈值 + L0 缓存版本命名空间 + 晋升管理端点保护。
    # 以下均为**新增项**，不改变任何既有字段的语义与默认值。
    #
    # L1 匹配阈值：中文二元组覆盖度归一分（关键词×2/标题×3/内容×1 加权，封顶 1.0）。
    # **初始值须用真实查询分布定标**（GET /api/v2/kbqa/stats 是取数处），
    # 不要当成恒定真理——定标前它只保证"宁落 L2 不错答"。
    # 0.70 的来历：单个 2 字符关键词命中 = 2×2/6 ≈ 0.67，必须**独自过不了闸**——
    # "酒店发票抬头怎么写"只该命中含"发票"关键词的制度是不够的（答案不覆盖
    # "抬头"），要求关键词之外还有标题或内容佐证，避免 L1 抢答半相关答案。
    KBQA_MATCH_THRESHOLD: float = float(os.getenv("KBQA_MATCH_THRESHOLD", "0.70"))
    # top1 与 top2 的分差下限：两条都能答时不许乱指，回落 L2 更安全
    KBQA_MARGIN_THRESHOLD: float = float(os.getenv("KBQA_MARGIN_THRESHOLD", "0.15"))
    # FAQ 晋升管理端点令牌：留空 = 仅 127.0.0.1 可调（本地管理态）；
    # 配置后任意来源凭 X-FAQ-Admin-Token 头访问
    KBQA_ADMIN_TOKEN: str = os.getenv("KBQA_ADMIN_TOKEN", "")
    # query_log 遥测写入开关：false 时只跑问答不落度量（调试用）
    KBQA_LOG_WRITES: bool = _env_bool("KBQA_LOG_WRITES", True)
    # DESIGN-2 意图路由：L1 miss 且域内词表不命中 → 约束 LLM 直答（channel=direct）。
    # 实测依据：thinking-disabled 裸 LLM ~1.0s vs search_kb+LLM ~2.6s，省 1.6s。
    # 总开关做逃生阀：置 false 时全部回落 L2 RAG（迭代一行为，零质量回退）。
    KBQA_DIRECT_ENABLED: bool = _env_bool("KBQA_DIRECT_ENABLED", True)
    # 直答 max_tokens：prompt 约束 200 token（约 150 字）以内，此值即上限；
    # 非制度类问题允许助手按自身理解作答，故与 RAG 的 400 区分
    KBQA_DIRECT_MAX_TOKENS: int = int(os.getenv("KBQA_DIRECT_MAX_TOKENS", "200"))


settings = Settings()
