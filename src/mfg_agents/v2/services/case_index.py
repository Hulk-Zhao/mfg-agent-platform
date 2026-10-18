"""CaseIndexService：chunk 生成 + ``acl_tokens`` / ``visibility_class`` + 索引维护。

架构位置：§6.3（向量库）、§8.2 步 8（索引）、裁定 A3（快慢路径）、A8（chunk 主键）。

四类 chunk（顺序即展示优先级）：``summary`` / ``phenomenon`` / ``root_cause`` /
``countermeasure``。主键固定 ``f"{case_id}::{chunk_type}"``（**不含 content_version**），
因此更新是同 id upsert 覆盖，**不产生孤儿 chunk**——这是"撤权/更新即时生效"的前提。

``visibility_class`` 计算规则（写入侧唯一实现）：

- 案例没有任何业务 ACL 条目 → ``vc=0``（OPEN，全维无约束，绝大多数质量案例）；
- 有任一条目 → ``vc=1``（SCOPED，必须做令牌交集）。

``acl_tokens`` 由业务 ACL 条目派生（``dept:quality`` / ``plant:P2`` / ``role:gm`` …）。
**注意**：``vc=1`` 的案例在快路径主体（非正式员工）眼里不可见——这是收紧，不是放宽。

未发布状态（draft/review/rejected/withdrawn）的案例**只写 chunk 映射行、不进索引**
（BR-06）：``vector_status='pending'``，等发布后由 :meth:`publish_case` 落到索引。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..contracts.case_acl import FilterNode, KnowledgeLayerViolation
from ..contracts.case_index import (
    CaseVectorIndex,
    IndexHealth,
    KnowledgeChunk,
)
from ..contracts.ontology import OntologyRegistry, get_ontology_registry
from .case_store import VISIBLE_STATUS, CaseStore

logger = logging.getLogger(__name__)

#: 回退路径（后端无聚合能力）一次最多扫描的命中数。
#: 演示库规模 <1k chunk；生产应让后端实现 ``count_cases`` 而不是靠扫描。
MAX_CASE_SCAN = 100000

__all__ = [
    "CHUNK_TYPES",
    "CHUNK_TYPE_PRIORITY",
    "CaseIndexService",
    "create_case_index",
    "compute_acl_tokens",
    "compute_visibility_class",
]

#: 四类 chunk。
CHUNK_TYPES: tuple[str, ...] = (
    "summary", "phenomenon", "root_cause", "countermeasure",
)

#: 去重时的 chunk_type 优先级（数字越小越优先，架构 §6.3.3）。
CHUNK_TYPE_PRIORITY: dict[str, int] = {
    "summary": 0, "phenomenon": 1, "countermeasure": 2, "root_cause": 3,
}

#: chunk_type 优先级见 `CHUNK_TYPE_PRIORITY`（去重时保留最高优先级的 2 条）。


def compute_acl_tokens(acl_rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """业务 ACL 条目 → 令牌元组（``dept:quality`` …）。``deny`` 条目不计入。"""
    tokens: list[str] = []
    for row in acl_rows or ():
        if str(row.get("effect") or "allow").lower() != "allow":
            continue
        dim = str(row.get("dim") or "").strip()
        value = str(row.get("value") or "").strip()
        if not dim or not value:
            continue
        token = f"{dim}:{value}"
        if token not in tokens:
            tokens.append(token)
    return tuple(tokens)


def compute_visibility_class(tokens: Sequence[str]) -> int:
    """有令牌 = 1（SCOPED，需数组运算）；无令牌 = 0（OPEN，快路径）。"""
    return 0 if not list(tokens or ()) else 1


def create_case_index(backend: Optional[str] = None, **kwargs: Any) -> CaseVectorIndex:
    """按后端名创建索引实例（``memory`` 默认）。

    这是"可替换适配器"的唯一装配点：接 Milvus 只改环境变量
    ``CASE_VECTOR_BACKEND=milvus`` + 注入 embedder，业务代码零改动。
    """
    from ...common.config import settings

    name = str(backend or getattr(settings, "CASE_VECTOR_BACKEND", "memory")).strip().lower()
    if name in ("memory", "mem", "inprocess", ""):
        from ..contracts.adapters.case_memory import MemoryCaseIndex

        return MemoryCaseIndex()
    if name == "chroma":
        from ..contracts.adapters.case_chroma import ChromaCaseIndex

        return ChromaCaseIndex(
            persist_dir=kwargs.get("persist_dir") or getattr(settings, "CASE_CHROMA_DIR", None),
            host=kwargs.get("host"),
            port=kwargs.get("port"),
        )
    if name == "milvus":
        from ..contracts.adapters.case_milvus import MilvusCaseIndex

        return MilvusCaseIndex(
            uri=str(kwargs.get("uri") or getattr(settings, "MILVUS_URI", "")),
            collection=str(kwargs.get("collection") or "case_knowledge"),
            dim=int(kwargs.get("dim") or 1024),
            embedder=kwargs.get("embedder"),
        )
    raise ValueError(
        f"未知案例向量后端: {name!r}（合法值：memory | chroma | milvus）")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:32]


@dataclass
class _ChunkPlan:
    chunk_type: str
    text: str


class CaseIndexService:
    """知识层 chunk 与向量索引的维护者（唯一写索引入口）。"""

    def __init__(self, store: CaseStore, index: CaseVectorIndex,
                 ontology: Optional[OntologyRegistry] = None) -> None:
        self._store = store
        self._index = index
        self._ontology = ontology or get_ontology_registry()

    @property
    def index(self) -> CaseVectorIndex:
        return self._index

    # ---------- chunk 规划 ----------

    def plan_chunks(self, case: Mapping[str, Any]) -> list[_ChunkPlan]:
        """生成四类 chunk 的文本（**只取关系库里已脱敏的知识层字段**）。"""
        defect = str(case.get("defect_mode_code") or "")
        root = str(case.get("root_cause_code") or "")
        defect_name = self._term_name("defect_mode", defect)
        root_name = self._term_name("root_cause", root)
        plans: list[_ChunkPlan] = []
        summary = str(case.get("summary") or "").strip()
        if summary:
            plans.append(_ChunkPlan("summary", summary))
        phenomenon = str(case.get("phenomenon") or "").strip()
        if phenomenon:
            label = f"缺陷模式：{defect_name}。" if defect_name else ""
            plans.append(_ChunkPlan("phenomenon", f"{label}{phenomenon}"))
        root_text = str(case.get("root_cause_text") or "").strip()
        if root_text:
            label = f"根因：{root_name}。" if root_name else ""
            plans.append(_ChunkPlan("root_cause", f"{label}{root_text}"))
        counter = str(case.get("countermeasure_text") or "").strip()
        if counter:
            plans.append(_ChunkPlan("countermeasure", counter))
        return plans

    def _term_name(self, vocab: str, code: str) -> str:
        if not code:
            return ""
        term = self._ontology.term(vocab, code)
        return term.name if term else ""

    # ---------- chunk 元数据 ----------

    def build_metadata(self, case: Mapping[str, Any],
                       tokens: Sequence[str]) -> dict[str, Any]:
        """chunk 过滤元数据：**键必须落在 ``FILTERABLE_FIELDS`` 内** + 展示字段。"""
        metadata: dict[str, Any] = {
            "tenant": str(case.get("tenant") or ""),
            "category": str(case.get("category") or ""),
            "subcategory": str(case.get("subcategory") or ""),
            "status": str(case.get("status") or ""),
            "cls_rank": int(case.get("cls_rank") or 0),
            "visibility_class": compute_visibility_class(tokens),
            "is_knowledge_layer": True,
            "owner_dept": str(case.get("owner_dept") or ""),
            "plant": str(case.get("plant") or ""),
            "defect_mode_code": str(case.get("defect_mode_code") or ""),
            "root_cause_code": str(case.get("root_cause_code") or ""),
            "content_version": int(case.get("content_version") or 1),
            "published_at": str(case.get("published_at") or ""),
            "last_verified_at": str(case.get("last_verified_at") or ""),
            "verified": bool(case.get("verified")),
            "acl_tokens": [str(t) for t in tokens],
            # ---- 展示字段（不参与过滤） ----
            "line_code": str(case.get("line_code") or ""),
            "title": str(case.get("title") or ""),
            "citation_count": int(case.get("citation_count") or 0),
            "source_ticket_type": str(case.get("source_ticket_type") or ""),
            "source_ticket_id": str(case.get("source_ticket_id") or ""),
        }
        return metadata

    def build_chunks(self, case: Mapping[str, Any]) -> list[KnowledgeChunk]:
        """按案例生成 ``KnowledgeChunk`` 列表（含 acl_tokens 元数据）。"""
        case_id = str(case.get("case_id") or "")
        acl_rows = self._store.acl_entries(case_id)
        tokens = compute_acl_tokens(acl_rows)
        metadata = self.build_metadata(case, tokens)
        version = int(case.get("content_version") or 1)
        chunks: list[KnowledgeChunk] = []
        for plan in self.plan_chunks(case):
            chunks.append(KnowledgeChunk(
                case_id=case_id,
                chunk_type=plan.chunk_type,
                content_version=version,
                text=plan.text,
                metadata=metadata,
                is_knowledge_layer=True,
            ))
        return chunks

    # ---------- 索引维护 ----------

    def sync_case(self, case_id: str) -> int:
        """按案例同步索引：可见状态 → upsert；不可见状态 → 删除索引并置 pending。

        Returns:
            写入索引的 chunk 数（不可见状态为 0）。
        """
        case = self._store.get_case(case_id)
        if case is None:
            self._index.delete_by_case(case_id)
            return 0
        chunks = self.build_chunks(case)
        rows = [{
            "chunk_id": chunk.chunk_id,
            "chunk_type": chunk.chunk_type,
            "content_version": chunk.content_version,
            "vector_status": "pending",
            "text_hash": _text_hash(chunk.text),
            "indexed_at": "",
        } for chunk in chunks]
        self._store.replace_chunks(case_id, rows)

        if str(case.get("status") or "") not in VISIBLE_STATUS:
            # BR-06：未结案/已撤权**不进索引**（先删后判，保证撤权即时失效）
            self._index.delete_by_case(case_id)
            self._store.set_chunk_status(case_id, "stale")
            return 0

        written = self._index.upsert_chunks(chunks)
        self._store.set_chunk_status(case_id, "indexed", _now_iso())
        return written

    def publish_case(self, case_id: str) -> int:
        """发布后落索引（``status`` 必须是可见状态，否则只写映射行）。"""
        return self.sync_case(case_id)

    def delete_case(self, case_id: str) -> int:
        """撤权/删除：索引立即失效（不等定时重建，BR-04）。"""
        removed = self._index.delete_by_case(case_id)
        self._store.set_chunk_status(case_id, "stale")
        return removed

    def rebuild(self) -> dict[str, int]:
        """全量重建（向量库是可重建的派生索引）。"""
        stats = {"cases": 0, "chunks": 0, "skipped": 0}
        for case in self._store.list_cases():
            stats["cases"] += 1
            written = self.sync_case(str(case["case_id"]))
            if written:
                stats["chunks"] += written
            else:
                stats["skipped"] += 1
        logger.info("[case_index] 重建完成: %s", stats)
        return stats

    def health(self) -> IndexHealth:
        return self._index.health()

    # ---------- 计数（按案例去重） ----------

    def count_cases(self, filter_node: FilterNode) -> int:
        """满足过滤条件的**案例数**（不是 chunk 数）。

        为什么需要这一层：``hidden_count`` 的单位必须是案例（R2）。索引里一个案例
        有 2–4 个 chunk，直接数 chunk 会把"藏了 6 个案例"报成"16 条"，现场一被追问
        就穿。后端能高效去重就用后端能力，不能就用检索回退后按 ``case_id`` 去重，
        **两种情况语义一致**。
        """
        counter = getattr(self._index, "count_cases", None)
        if callable(counter):
            return int(counter(filter_node))
        # 通用回退：拉全量命中再按案例去重（正则后端没有聚合能力，只能这样）
        hits = self._index.search("", filter_node, MAX_CASE_SCAN)
        return len({str(hit.case_id) for hit in hits})

    # ---------- 引用校验（BR-05） ----------

    def verify_citation(self, case_id: str, chunk_type: str,
                        content_version: int) -> bool:
        """引用校验：``(case_id, chunk_id, content_version)`` 必须与当前知识层一致。

        **版本与文本双一致**（R3 裁定）：只比 ``content_version`` 是纸糊的护栏——
        ``update_fields`` 改文本并不会 bump 版本，于是"文本被改、版本没变"的引用
        会一路放行，引用内容与用户当时读到的已经不是同一段。

        因此这里做三步：
        1. 案例存在且状态可见（BR-06）；
        2. ``content_version`` 与案例当前版本一致；
        3. 用 ``case_chunk.text_hash``（写入时固化的 sha256 前 32 位）与
           "按当前案例重建的该段文本"的哈希比对——不一致说明有人绕过写入管道
           直接改了库或代码，**必须丢弃该断言**。
        """
        case = self._store.get_case(case_id)
        if case is None:
            return False
        if str(case.get("status") or "") not in VISIBLE_STATUS:
            return False
        if int(case.get("content_version") or 1) != int(content_version):
            return False

        chunk_id = f"{case_id}::{str(chunk_type)}"
        recorded = ""
        for row in self._store.chunk_rows(case_id):
            if str(row.get("chunk_id")) == chunk_id:
                recorded = str(row.get("text_hash") or "")
                break
        current = _text_hash(self.chunk_text(case_id, str(chunk_type)))
        if not current:
            # 该片段在当前内容下已不存在（正文被清空）→ 引用无从谈起
            return False
        if not recorded:
            # 老库回填：``case_chunk.text_hash`` 是后加的列，旧数据是空串。
            # 此时若按"缺 hash 即不可信"处理，升级后**所有**引用都会被丢弃
            # （现场表现是"问什么都答不上来"且不报错）。所以按当前文本重建一次
            # 并写回，让后续校验回到双一致语义。
            self._store.backfill_chunk_text_hash(case_id, chunk_id, current)
            return True
        if current != recorded:
            logger.warning(
                "[case_index] 引用校验失败：%s 的文本哈希与写入时不一致"
                "（版本一致但文本被改，拒绝该引用）", chunk_id)
            return False
        return True

    def chunk_text(self, case_id: str, chunk_type: str) -> str:
        """回查某 chunk 的知识层文本（引用校验/组装上下文用）。"""
        case = self._store.get_case(case_id)
        if case is None:
            return ""
        for plan in self.plan_chunks(case):
            if plan.chunk_type == str(chunk_type):
                return plan.text
        return ""

    def ensure_layer(self, chunks: Iterable[KnowledgeChunk]) -> None:
        """写入前二次断言：全部是知识层（闸门 1 的运行期形态）。"""
        for chunk in chunks:
            if not isinstance(chunk, KnowledgeChunk) or chunk.is_knowledge_layer is not True:
                raise KnowledgeLayerViolation("证据层内容禁止进入知识层索引")
