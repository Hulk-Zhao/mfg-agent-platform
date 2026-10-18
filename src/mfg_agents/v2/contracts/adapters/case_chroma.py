"""``ChromaCaseIndex``：Chroma（开发 / POC 后端，≤ 5 万 chunk）。

架构位置：§6.3.4（双后端兼容策略）。Chroma 的四条**已知限制**在此逐条处理：

| # | 限制 | 本实现的对策 |
|---|---|---|
| ① | 元数据无专用索引，多维 AND 退化全表扫描 | 代码层面无法解决（这是选型结论），只在 ``health()`` 里给出提示 |
| ② | ``$and`` 需 ≥ 2 操作数 | ``to_chroma_where`` 对单条件**不**包 ``$and`` |
| ③ | ``where={}`` 报错，无过滤必须传 ``None`` | 空 ``where`` 一律不传（但永假过滤器翻成 ``{"chunk_id": {"$in": []}}``，绝不当成"无过滤"） |
| ④ | 数组不允许空数组 | "该维度无约束"写入哨兵值 ``"__ANY__"``；读回时把哨兵视为空 |

**惰性 import**：``chromadb`` 未安装时构造即抛带安装提示的明确异常（而不是在
第一次检索时炸）。演示默认后端是 ``memory``，本类不会在 demo 路径上被实例化。
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from ..case_acl import FilterNode, KnowledgeLayerViolation
from ..case_index import CaseVectorIndex, IndexHealth, KnowledgeChunk, SearchHit
from ...security.filter_translator import (
    CHROMA_ANY_SENTINEL,
    to_chroma_where,
)

logger = logging.getLogger(__name__)

__all__ = ["ChromaCaseIndex", "CHROMA_COLLECTION"]

#: 知识层 collection 名（唯一；与 Milvus 同名，便于对账脚本切换后端）。
CHROMA_COLLECTION = "case_knowledge"


def _require_chromadb():
    """惰性导入 chromadb；缺失时给出可执行的安装提示。"""
    try:
        import chromadb  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "ChromaCaseIndex 需要 chromadb（已在 pyproject 的既有依赖内）。"
            "请先 `uv sync` 或 `pip install chromadb`；"
            "若只想跑演示，请使用默认后端 memory（CASE_VECTOR_BACKEND=memory）。"
        ) from exc
    return chromadb


def _metadata_for_write(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """写入侧元数据整形：空数组 → ``__ANY__`` 哨兵（限制 ④），标量原样。"""
    out: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if isinstance(value, (list, tuple, set)):
            items = [str(v) for v in value if str(v)]
            out[str(key)] = items or [CHROMA_ANY_SENTINEL]
        elif isinstance(value, bool):
            out[str(key)] = bool(value)
        elif isinstance(value, (int, float)):
            out[str(key)] = value
        elif value is None:
            # None 在 fail-closed 语义下等于"无授权"，但 Chroma 不收 None → 用空串
            out[str(key)] = ""
        else:
            out[str(key)] = str(value)
    return out


def _metadata_from_read(metadata: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """读回侧元数据整形：把 ``__ANY__`` 哨兵还原成"空 = 无约束"。"""
    out: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if isinstance(value, (list, tuple)):
            items = [str(v) for v in value if str(v) != CHROMA_ANY_SENTINEL]
            out[str(key)] = items
        else:
            out[str(key)] = value
    return out


class ChromaCaseIndex(CaseVectorIndex):
    """Chroma 实现（``where`` 过滤 + 持久化目录或 HttpClient）。"""

    backend = "chroma"

    def __init__(self, persist_dir: Optional[str] = None,
                 collection: str = CHROMA_COLLECTION,
                 host: Optional[str] = None,
                 port: Optional[int] = None) -> None:
        chromadb = _require_chromadb()
        if host:
            client = chromadb.HttpClient(host=host, port=int(port or 8000))
        else:
            client = chromadb.PersistentClient(path=str(persist_dir or "data/chroma_case"))
        self._collection = client.get_or_create_collection(
            name=str(collection or CHROMA_COLLECTION),
            metadata={"hnsw:space": "cosine"},
        )
        self._collection_name = str(collection or CHROMA_COLLECTION)

    # ---------- 写入 ----------

    def upsert_chunks(self, chunks: Sequence[KnowledgeChunk]) -> int:
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for chunk in chunks or ():
            if not isinstance(chunk, KnowledgeChunk):
                raise TypeError(
                    "只允许写入 KnowledgeChunk（证据层内容禁止进入知识层索引）: "
                    f"{type(chunk).__name__}")
            if chunk.is_knowledge_layer is not True:  # pragma: no cover - 构造期已拦
                raise KnowledgeLayerViolation("证据层内容禁止写入 Chroma 知识层")
            ids.append(chunk.chunk_id)
            documents.append(chunk.text)
            metadatas.append(_metadata_for_write({**chunk.metadata,
                                                  "case_id": chunk.case_id,
                                                  "chunk_type": chunk.chunk_type,
                                                  "content_version": chunk.content_version,
                                                  "is_knowledge_layer": True}))
        if not ids:
            return 0
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    def delete_by_case(self, case_id: str) -> int:
        target = str(case_id or "")
        existing = self._collection.get(where={"case_id": {"$eq": target}}, include=[])
        ids = list(existing.get("ids") or [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    # ---------- 检索 ----------

    def search(self, query: str, filter: FilterNode, k: int = 5) -> list[SearchHit]:
        where = to_chroma_where(filter)          # 永假 → {"chunk_id": {"$in": []}}
        payload: dict[str, Any] = {
            "query_texts": [str(query or "")],
            "n_results": max(1, int(k)),
            "include": ["metadatas", "documents", "distances"],
        }
        # 限制 ③：无过滤必须传 None；但这里 where 永不为空 dict（永假也是非空）
        if where:
            payload["where"] = where
        raw = self._collection.query(**payload)
        ids = (raw.get("ids") or [[]])[0]
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        hits: list[SearchHit] = []
        for index, chunk_id in enumerate(ids):
            metadata = _metadata_from_read(
                metadatas[index] if index < len(metadatas) else {})
            distance = float(distances[index]) if index < len(distances) else 1.0
            hits.append(SearchHit(
                case_id=str(metadata.get("case_id", "")),
                chunk_id=str(chunk_id),
                chunk_type=str(metadata.get("chunk_type", "")),
                content_version=int(metadata.get("content_version") or 1),
                text=str(documents[index]) if index < len(documents) else "",
                score=round(max(0.0, 1.0 - distance), 6),
                metadata=metadata,
            ))
        return hits

    def count(self, filter: FilterNode) -> int:
        where = to_chroma_where(filter)
        payload: dict[str, Any] = {"include": []}
        if where:
            payload["where"] = where
        raw = self._collection.get(**payload)
        return len(list(raw.get("ids") or []))

    def health(self) -> IndexHealth:
        try:
            total = int(self._collection.count())
            return IndexHealth(
                backend=self.backend, ready=True, chunk_count=total,
                detail="chroma: 元数据无专用索引，多维 AND 会退化为全表扫描（ADR-006：仅 dev/POC）")
        except Exception as exc:  # noqa: BLE001
            return IndexHealth(backend=self.backend, ready=False,
                               detail=f"chroma 不可用: {exc}")
