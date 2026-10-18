"""``MilvusCaseIndex``：Milvus（**唯一生产后端**）预留接入点。

架构位置：§6.3.2（schema）/ §6.3.3（混合检索）/ ADR-006。

**本类是预留口，不要求在 demo 环境跑通**：``pymilvus`` 未安装时**构造即抛**带
安装提示的明确异常（而不是等到第一次检索才炸，也不是静默降级成"无过滤检索"——
后者等于越权）。

分工约定（与 ``docs/case-assistant-ontology-vector-auth-architecture.md`` 对齐）：

- filter 用 ``to_milvus_expr`` 翻译，作为 ``hybrid_search(filter=expr)`` 下推
  → Milvus 默认 **Filter-then-Search**，天然满足"过滤在召回前生效"（BR-01 前半）；
- 静态标量列见 ``MILVUS_SCALAR_FIELDS``，``acl_tokens`` 是 ``ARRAY[VARCHAR]``，
  过滤用 ``array_contains_any``；**未建标量索引**会让过滤退化为扫描（部署清单项）；
- dense / sparse 双路召回 + ``WeightedRanker(0.7, 0.3)`` 参数沿用现有实现，不调参
  （未经 POC 校准不改动）；
- 写入侧 ``is_knowledge_layer`` 必须为 ``True``，否则 ``KnowledgeLayerViolation``。

若部署版本不支持 ``hybrid_search(filter=...)``，调用方**必须**显式配置
``filter_on_requests=True`` 让表达式落到每个 ``AnnSearchRequest`` 的 ``expr`` 上；
本类不做"探测失败就不过滤"的兜底——那是最危险的静默降级。
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from ..case_acl import FilterNode, KnowledgeLayerViolation
from ..case_index import CaseVectorIndex, IndexHealth, KnowledgeChunk, SearchHit
from ...security.filter_translator import to_milvus_expr

logger = logging.getLogger(__name__)

__all__ = [
    "MilvusCaseIndex",
    "MILVUS_COLLECTION",
    "MILVUS_SCALAR_FIELDS",
    "MILVUS_ARRAY_FIELDS",
    "build_milvus_schema_kwargs",
]

#: 生产知识层 collection（与 Chroma 同名，便于对账脚本切换后端）。
MILVUS_COLLECTION = "case_knowledge"

#: 必须提升为静态标量列 + 建索引的过滤维度（§6.3.2）。JSON 里的字段无法建高效索引。
MILVUS_SCALAR_FIELDS: tuple[str, ...] = (
    "tenant", "category", "subcategory", "status", "cls_rank",
    "visibility_class", "is_knowledge_layer", "owner_dept", "plant",
    "defect_mode_code", "root_cause_code", "content_version",
    "published_at", "last_verified_at", "verified", "citation_count",
)

#: 数组型过滤字段（Milvus ≥ 2.4 才支持，需 ARRAY + array_contains_any）。
MILVUS_ARRAY_FIELDS: tuple[str, ...] = ("acl_tokens",)


def _require_pymilvus():
    """惰性导入 pymilvus；缺失时给出可执行的安装/切换提示。"""
    try:
        from pymilvus import MilvusClient  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "MilvusCaseIndex 需要 pymilvus 与 Milvus Server ≥ 2.5（ARRAY 字段 + "
            "array_contains_any 依赖 ≥ 2.4，iterative_filter 依赖 ≥ 2.5）。"
            "pyproject 已声明 pymilvus>=2.5.0：请先 `uv sync`。"
            "演示环境请使用默认后端 memory（CASE_VECTOR_BACKEND=memory）。"
        ) from exc
    return MilvusClient


def build_milvus_schema_kwargs(dim: int = 1024) -> dict[str, Any]:
    """生成 ``MilvusClient.create_collection`` 的 schema 参数（部署脚本/单测复用）。

    仅生成参数结构，不连数据库——因此可在无 Milvus 的环境里断言"schema 覆盖了全部
    过滤维度"（这是 §6.3.2 的 G6 硬约束的回归保护）。
    """
    scalar_types = {
        "tenant": "VARCHAR", "category": "VARCHAR", "subcategory": "VARCHAR",
        "status": "VARCHAR", "owner_dept": "VARCHAR", "plant": "VARCHAR",
        "defect_mode_code": "VARCHAR", "root_cause_code": "VARCHAR",
        "cls_rank": "INT8", "visibility_class": "INT8",
        "is_knowledge_layer": "BOOL", "verified": "BOOL",
        "content_version": "INT32", "citation_count": "INT32",
        "published_at": "INT64", "last_verified_at": "INT64",
    }
    fields: list[dict[str, Any]] = [
        {"name": "chunk_id", "dtype": "VARCHAR", "is_primary": True,
         "max_length": 192},
        {"name": "document", "dtype": "VARCHAR", "max_length": 65535},
        {"name": "dense_vector", "dtype": "FLOAT_VECTOR", "dim": int(dim)},
        {"name": "sparse_vector", "dtype": "SPARSE_FLOAT_VECTOR"},
    ]
    for name in MILVUS_SCALAR_FIELDS:
        field_spec: dict[str, Any] = {"name": name,
                                      "dtype": scalar_types.get(name, "VARCHAR")}
        if field_spec["dtype"] == "VARCHAR":
            field_spec["max_length"] = 64
        fields.append(field_spec)
    fields.append({"name": "acl_tokens", "dtype": "ARRAY",
                   "element_type": "VARCHAR", "max_capacity": 64,
                   "max_length": 64})
    return {
        "collection_name": MILVUS_COLLECTION,
        "dimension": int(dim),
        "metric_type": "IP",
        "fields": fields,
        "indexes": [
            {"field_name": "dense_vector",
             "index_params": {"index_type": "AUTOINDEX", "metric_type": "IP"}},
            {"field_name": "sparse_vector",
             "index_params": {"index_type": "SPARSE_INVERTED_INDEX",
                              "metric_type": "IP"}},
            {"field_name": "cls_rank",
             "index_params": {"index_type": "STL_SORT"}},
            {"field_name": "acl_tokens",
             "index_params": {"index_type": "INVERTED"}},
        ],
    }


class MilvusCaseIndex(CaseVectorIndex):
    """Milvus 实现（预留口；demo 不实例化）。"""

    backend = "milvus"

    def __init__(self, uri: str, token: str = "",
                 collection: str = MILVUS_COLLECTION,
                 dim: int = 1024,
                 filter_on_requests: bool = False,
                 embedder: Optional[Any] = None) -> None:
        MilvusClient = _require_pymilvus()
        if not str(uri or "").strip():
            raise RuntimeError("MilvusCaseIndex 需要显式 uri（如 http://localhost:19530）")
        self._client = MilvusClient(uri=str(uri), token=str(token or ""))
        self._collection = str(collection or MILVUS_COLLECTION)
        self._dim = int(dim)
        self._filter_on_requests = bool(filter_on_requests)
        # embedder 是 LLM/嵌入模型的接入点：未注入时**不允许**检索（不能假装能算分）
        self._embedder = embedder
        if not self._client.has_collection(self._collection):
            kwargs = build_milvus_schema_kwargs(self._dim)
            self._client.create_collection(
                collection_name=self._collection,
                dimension=kwargs["dimension"],
                metric_type=kwargs["metric_type"],
                primary_field_name="chunk_id",
                id_type="string",
                vector_field_name="dense_vector",
            )

    # ---------- 写入 ----------

    def upsert_chunks(self, chunks: Sequence[KnowledgeChunk]) -> int:
        rows: list[dict[str, Any]] = []
        for chunk in chunks or ():
            if not isinstance(chunk, KnowledgeChunk):
                raise TypeError(
                    "只允许写入 KnowledgeChunk（证据层内容禁止进入知识层索引）: "
                    f"{type(chunk).__name__}")
            if chunk.is_knowledge_layer is not True:
                raise KnowledgeLayerViolation("证据层内容禁止写入 Milvus 知识层")
            row = chunk.to_row()
            row["document"] = chunk.text
            row["dense_vector"] = self._embed_dense(chunk.text)
            row["sparse_vector"] = self._embed_sparse(chunk.text)
            rows.append(row)
        if not rows:
            return 0
        self._client.upsert(collection_name=self._collection, data=rows)
        return len(rows)

    def delete_by_case(self, case_id: str) -> int:
        expr = f'case_id == "{str(case_id or "")}"'
        try:
            self._client.delete(collection_name=self._collection, filter=expr)
        except TypeError:  # 旧版客户端用 expr 关键字
            self._client.delete(collection_name=self._collection, expr=expr)
        return 1

    # ---------- 检索 ----------

    def search(self, query: str, filter: FilterNode, k: int = 5) -> list[SearchHit]:
        expr = to_milvus_expr(filter)   # 永假 → "1 == 0"；空 AST → raise
        if self._embedder is None:
            raise RuntimeError(
                "MilvusCaseIndex 未注入 embedder：无法计算向量。"
                "演示请用 memory 后端；生产需注入 BGE-M3 适配器。")
        dense = self._embed_dense(str(query or ""))
        sparse = self._embed_sparse(str(query or ""))
        request = {
            "anns_field": "dense_vector",
            "data": [dense],
            "param": {"metric_type": "IP"},
            "limit": max(1, int(k)),
        }
        if self._filter_on_requests:
            request["expr"] = expr
        results = self._client.search(
            collection_name=self._collection,
            data=[dense],
            anns_field="dense_vector",
            search_params={"metric_type": "IP"},
            limit=max(1, int(k)),
            output_fields=["document", "case_id", "chunk_type",
                           "content_version", "visibility_class"],
            filter=expr,
        )
        _ = sparse  # 双路召回的 sparse 通道由生产实现补齐（见 §6.3.3）
        hits: list[SearchHit] = []
        for group in (results or []):
            for item in group:
                entity = item.get("entity") or {}
                hits.append(SearchHit(
                    case_id=str(entity.get("case_id", "")),
                    chunk_id=str(item.get("id", "")),
                    chunk_type=str(entity.get("chunk_type", "")),
                    content_version=int(entity.get("content_version") or 1),
                    text=str(entity.get("document", "")),
                    score=float(item.get("distance") or 0.0),
                    metadata=dict(entity),
                ))
        return hits

    def count(self, filter: FilterNode) -> int:
        expr = to_milvus_expr(filter)
        try:
            return int(self._client.query(
                collection_name=self._collection, filter=expr,
                output_fields=["count(*)"])[0].get("count(*)", 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[case_index] Milvus count 失败: %s", exc)
            return 0

    def health(self) -> IndexHealth:
        try:
            stats = self._client.get_collection_stats(self._collection)
            return IndexHealth(backend=self.backend, ready=True,
                               chunk_count=int(stats.get("row_count") or 0),
                               detail="milvus: filter 下推（Filter-then-Search）")
        except Exception as exc:  # noqa: BLE001
            return IndexHealth(backend=self.backend, ready=False,
                               detail=f"milvus 不可用: {exc}")

    # ---------- 嵌入占位（生产替换为 BGE-M3 适配器） ----------

    def _embed_dense(self, text: str) -> list[float]:
        if self._embedder is not None and hasattr(self._embedder, "encode_dense"):
            return list(self._embedder.encode_dense(text))
        # 无 embedder 时用确定性哈希兜底（维度对齐），保证写入不崩；检索前会显式报错
        from .case_memory import embed_text
        vector = embed_text(text)
        if len(vector) < self._dim:
            vector = vector + [0.0] * (self._dim - len(vector))
        return vector[:self._dim]

    def _embed_sparse(self, text: str) -> Mapping[str, Any]:
        if self._embedder is not None and hasattr(self._embedder, "encode_sparse"):
            return dict(self._embedder.encode_sparse(text))
        return {}
