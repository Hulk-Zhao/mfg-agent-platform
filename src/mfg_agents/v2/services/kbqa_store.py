"""企业问答助手 FAQ 数据仓库（ADR-001 L1 数据面）。

职责边界（为什么单独一个 store 而不走 ``contracts/data_source.py``）：

- kbqa 是**独立轻量通道**（见 ``services/kbqa.py`` 模块 docstring），其检索路径
  刻意不经过 orchestrator / TwoLevelRouter；若改走 ``DataSourceRegistry``，
  首次问答就会触发编排器初始化（建库 + 加载场景 + 连向量库），
  既拖慢首问，也把"Milvus 挂了 FAQ 还能用"这条降级路径堵死。
- 本 store 只服务 kbqa 三张自有表 + ``kbqa_policy`` 制度主数据的**只读镜像**，
  SQL 固定、参数化，无命名查询间接层；行数有显式 LIMIT 封顶。

写路径纪律（ADR-001 §4）：

- 晋升只写 ``kbqa_faq``，**绝不写 ``kbqa_policy`` 制度主数据**；
- 全部写操作走 ``engine.begin()`` 事务；``log_query`` 例外——
  遥测写入失败只能吞掉并告警，**不能把问答主路径带崩**。

方言纪律：运行期 SQL 只用两方言共通写法（COUNT/MAX/IFNULL/参数化 INSERT），
不出现 ``"sqlite" in url`` 这类子串判定；库切换由 ``settings.DB_URL`` 经
SQLAlchemy 统一承载（与 ``common/init_db.py`` 同范式）。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from ...common.config import settings

logger = logging.getLogger(__name__)

#: 惰性引擎（模块导入不建连接；首次使用才连库，失败由调用方降级）
_engine: Any = None
_engine_lock = threading.Lock()

#: FAQ 语料加载行数上限（kbqa_policy 48 行 + kbqa_faq 增长；500 足够且封顶内存）
CORPUS_LIMIT = 500


def get_engine():
    """惰性获取 SQLAlchemy 引擎（线程安全双检）。"""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            from sqlalchemy import create_engine

            _engine = create_engine(
                settings.DB_URL, future=True, pool_pre_ping=True)
            logger.info("[kbqa_store] 引擎已建立: %s", settings.DB_URL)
    return _engine


def reset_engine() -> None:
    """重置惰性引擎（测试钩子：换库后必须调用，否则复用旧连接）。"""
    global _engine
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:  # noqa: BLE001 - 测试清理失败不致命
                pass
        _engine = None


# ---------------------------------------------------------------- 读：语料与版本


def faq_corpus(limit: int = CORPUS_LIMIT) -> list[dict[str, Any]]:
    """L1 语料：``kbqa_policy``（制度主数据）+ ``kbqa_faq``（晋升对）并集。

    返回统一行结构：``{ref, question, answer, keywords, dept, dept_cn, src}``，
    ``src`` ∈ ``policy`` / ``faq``。政策行取 title 为问、content 为答；
    晋升行问/答即字面值。
    """
    engine = get_engine()
    rows: list[dict[str, Any]] = []
    with engine.connect() as conn:
        from sqlalchemy import text

        policy = conn.execute(
            text("SELECT policy_no AS ref, title AS question, content AS answer, "
                 "keywords, dept, dept_cn FROM kbqa_policy LIMIT :lim"),
            {"lim": int(limit)}).mappings().all()
        faq = conn.execute(
            text("SELECT id AS ref, question, answer, "
                 "keywords, dept, dept_cn FROM kbqa_faq "
                 "WHERE status = 'active' LIMIT :lim"),
            {"lim": int(limit)}).mappings().all()
    for r in policy:
        rows.append({**dict(r), "src": "policy"})
    for r in faq:
        rows.append({**dict(r), "ref": str(r["ref"]), "src": "faq"})
    return rows


def faq_version() -> str:
    """FAQ 数据版本戳：``policy:{count}:{max_id}|faq:{count}:{max_id}``。

    嵌入 L0 缓存键（ADR-001 D2）：任何 FAQ 写操作改变计数/最大 id → 版本变 →
    旧缓存键自然不可达，无需主动失效扫描。不覆盖"行内更新"（MVP 无更新路径，
    制度主数据由种子维护）；若未来加更新，需引入 updated_at 进戳。
    """
    engine = get_engine()
    with engine.connect() as conn:
        from sqlalchemy import text

        row = conn.execute(text(
            "SELECT (SELECT COUNT(*) FROM kbqa_policy) AS p_cnt, "
            "       (SELECT IFNULL(MAX(id), 0) FROM kbqa_policy) AS p_max, "
            "       (SELECT COUNT(*) FROM kbqa_faq WHERE status = 'active') AS f_cnt, "
            "       (SELECT IFNULL(MAX(id), 0) FROM kbqa_faq) AS f_max"
        )).mappings().first()
    if not row:
        return "policy:0:0|faq:0:0"
    return (f"policy:{row['p_cnt']}:{row['p_max']}"
            f"|faq:{row['f_cnt']}:{row['f_max']}")


# ---------------------------------------------------------------- 写：晋升管线


def insert_faq(question: str, answer: str, keywords: str = "",
               origin: str = "manual", source_ref: str = "",
               dept: str = "", dept_cn: str = "",
               created_by: str = "") -> int:
    """写入一条 FAQ 问答对（publish 的落点）；返回新行 id。"""
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        result = conn.execute(text(
            "INSERT INTO kbqa_faq (question, answer, keywords, origin, "
            "source_ref, dept, dept_cn, created_by) "
            "VALUES (:q, :a, :kw, :origin, :ref, :dept, :dept_cn, :by)"),
            {"q": question, "a": answer, "kw": keywords, "origin": origin,
             "ref": source_ref, "dept": dept, "dept_cn": dept_cn,
             "by": created_by})
        return int(result.lastrowid)


def insert_draft(question: str, answer: str, origin: str = "rag_feedback",
                 query_log_id: Optional[int] = None) -> int:
    """写入一条待审草稿（用户好评的 RAG 回答进评审队列）；返回新行 id。"""
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        result = conn.execute(text(
            "INSERT INTO kbqa_faq_draft (question, answer, origin, query_log_id) "
            "VALUES (:q, :a, :origin, :qid)"),
            {"q": question, "a": answer, "origin": origin,
             "qid": query_log_id})
        return int(result.lastrowid)


def list_drafts(status: str = "pending") -> list[dict[str, Any]]:
    """列出待审（或指定状态）草稿，新→旧。"""
    engine = get_engine()
    with engine.connect() as conn:
        from sqlalchemy import text

        rows = conn.execute(text(
            "SELECT id, question, answer, origin, query_log_id, status, "
            "reviewer, created_at FROM kbqa_faq_draft "
            "WHERE status = :st ORDER BY id DESC LIMIT 200"),
            {"st": status}).mappings().all()
    return [dict(r) for r in rows]


def publish_draft(draft_id: int, reviewer: str = "") -> int:
    """草稿过审 → 写入 ``kbqa_faq``（active）+ 草稿标记 published。

    同一事务内完成两笔写（不会出现"FAQ 进了、草稿没标"的中间态）；
    非 pending 状态的草稿再次 publish 抛 ``ValueError``（幂等由调用方转 409）。
    """
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        draft = conn.execute(text(
            "SELECT id, question, answer, origin, query_log_id, status "
            "FROM kbqa_faq_draft WHERE id = :id"),
            {"id": int(draft_id)}).mappings().first()
        if not draft:
            raise KeyError(f"草稿不存在: {draft_id}")
        if draft["status"] != "pending":
            raise ValueError(f"草稿非待审状态: {draft['status']}")
        result = conn.execute(text(
            "INSERT INTO kbqa_faq (question, answer, keywords, origin, "
            "source_ref, created_by) VALUES (:q, :a, :kw, 'rag_promoted', :ref, :by)"),
            {"q": draft["question"], "a": draft["answer"],
             # 关键词列 = 问题本身：FAQ 行的标题就是它的问法，
             # 不填关键词的话标题单独只有 0.5 分，过不了 L1 阈值——
             # 晋升出去的问答对将永远匹配不到它自己的问题。
             "kw": draft["question"],
             "ref": f"draft:{draft_id}", "by": reviewer or "admin"})
        faq_id = int(result.lastrowid)
        conn.execute(text(
            "UPDATE kbqa_faq_draft SET status = 'published', reviewer = :by "
            "WHERE id = :id"),
            {"by": reviewer or "admin", "id": int(draft_id)})
    return faq_id


# ---------------------------------------------------------------- 写：度量


def log_query(session_id: str, question: str, channel: str,
              confidence: Optional[float] = None,
              top_score: Optional[float] = None,
              latency_ms: Optional[int] = None) -> None:
    """查询度量落库。**遥测失败绝不抛**——不能因统计写失败弄丢一次回答。"""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text(
                "INSERT INTO kbqa_query_log (session_id, question, channel, "
                "confidence, top_score, latency_ms) "
                "VALUES (:sid, :q, :ch, :conf, :score, :ms)"),
                {"sid": session_id or None, "q": question[:500],
                 "ch": channel, "conf": confidence, "score": top_score,
                 "ms": latency_ms})
    except Exception as exc:  # noqa: BLE001 - 遥测降级是设计行为
        logger.warning("[kbqa_store] query_log 写入失败，已降级跳过: %s", exc)


def channel_stats(days: int = 7) -> dict[str, Any]:
    """近 N 天 channel 分布 + 低置信 RAG 占比（ADR KG 判据 / 阈值定标取数处）。

    时间窗在 Python 侧算截止点再作参数传入——**不用** ``datetime('now', ...)``
    （SQLite 专有）也不用 ``DATE_SUB(NOW(), INTERVAL ...)``（MySQL 专有），
    两方言共通只有 "参数化比较" 这一种写法。
    """
    from datetime import datetime, timedelta

    since = (datetime.now() - timedelta(days=int(days))).strftime(
        "%Y-%m-%d %H:%M:%S")
    engine = get_engine()
    with engine.connect() as conn:
        from sqlalchemy import text

        dist = conn.execute(text(
            "SELECT channel, COUNT(*) AS n FROM kbqa_query_log "
            "WHERE created_at >= :since GROUP BY channel"),
            {"since": since}).mappings().all()
        low = conn.execute(text(
            "SELECT COUNT(*) AS n FROM kbqa_query_log "
            "WHERE channel = 'rag' AND created_at >= :since "
            "AND (confidence IS NULL OR confidence < 0.5)"),
            {"since": since}).mappings().first()
        rag_total = conn.execute(text(
            "SELECT COUNT(*) AS n FROM kbqa_query_log "
            "WHERE channel = 'rag' AND created_at >= :since"),
            {"since": since}).mappings().first()
    distribution = {str(r["channel"]): int(r["n"]) for r in dist}
    rag_n = int(rag_total["n"]) if rag_total else 0
    low_n = int(low["n"]) if low else 0
    return {
        "days": int(days),
        "distribution": distribution,
        "total": sum(distribution.values()),
        "rag_total": rag_n,
        "rag_low_confidence": low_n,
        "rag_low_confidence_ratio": round(low_n / rag_n, 4) if rag_n else None,
    }
