"""向量库抽象（§6.1，对齐 Itcast ``rag_qa/core/vector_store.py`` 思路）。

本次**仅定义契约 + Chroma 适配器**：``VectorStore`` ABC 用标准库 ``abc``，
**零新依赖**（禁止 pymilvus/milvus/faiss/sentence-transformers）。

- ``VectorHit``：检索命中（id/text/metadata/score）；
- ``ChromaVectorStore``：把现有 ChromaDB 用法（``tools/rag_tools.py`` 单
  collection ``failure_cases``）包装为适配器——**行为不变**（同 collection、
  同 n_results 语义、同 metadata）；内部转发到既有函数 ``_get_collection``。
- Milvus 接入位：未来新增 ``contracts/adapters/milvus.py::MilvusVectorStore``，
  实现本 ABC 即可，**零框架改动**（见 v2/README.md）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["VectorHit", "VectorStore", "ChromaVectorStore"]


@dataclass
class VectorHit:
    """向量检索命中项（score 越大越相关；Chroma 距离已换算）。"""

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class VectorStore(ABC):
    """向量库抽象（可插拔后端）。本次仅定义契约 + Chroma 适配器。"""

    name: str = "abstract"

    @abstractmethod
    def upsert(self, collection: str, ids: list[str], documents: list[str],
               metadatas: Optional[list[dict]] = None,
               embeddings: Optional[list[list[float]]] = None) -> int:
        """写入/更新向量（返回写入条数）。"""

    @abstractmethod
    def query(self, collection: str, query_text: str, k: int = 5,
              where: Optional[dict] = None) -> list[VectorHit]:
        """检索相似文档（返回命中列表，score 降序来源）。"""

    @abstractmethod
    def delete(self, collection: str, ids: list[str]) -> int:
        """按 id 删除（返回删除条数）。"""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """健康探针：``{"backend","ok","collection_count"}``。"""


class ChromaVectorStore(VectorStore):
    """ChromaDB 适配器：包装既有 ``tools/rag_tools.py`` 的持久化 collection。

    行为不变承诺：默认 collection 恒为 ``failure_cases``（与 V1 一致），
    query 语义与 ``rag_tools.search_cases`` 等价（同一 collection、同
    ``n_results`` 语义、同 metadata）。构造为惰性：未使用时不触发 Chroma 连接。
    """

    name = "chroma"

    def __init__(self, collection: str = "failure_cases") -> None:
        self._collection_name = collection
        self._col: Any = None

    def _collection(self) -> Any:
        """惰性获取底层 collection（复用 rag_tools 既有单例逻辑）。"""
        if self._col is None:
            from ...common.rag_tools import _get_collection  # V1 资产复用
            self._col = _get_collection()
        return self._col

    def upsert(self, collection: str, ids: list[str], documents: list[str],
               metadatas: Optional[list[dict]] = None,
               embeddings: Optional[list[list[float]]] = None) -> int:
        col = self._collection()
        col.upsert(
            ids=list(ids), documents=list(documents),
            metadatas=list(metadatas) if metadatas is not None else None,
            embeddings=list(embeddings) if embeddings is not None else None)
        return len(ids)

    def query(self, collection: str, query_text: str, k: int = 5,
              where: Optional[dict] = None) -> list[VectorHit]:
        col = self._collection()
        res = col.query(query_texts=[query_text], n_results=k, where=where)
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        hits: list[VectorHit] = []
        for idx, doc_id in enumerate(ids):
            distance = dists[idx] if idx < len(dists) else None
            score = 1.0 - float(distance) if distance is not None else 0.0
            hits.append(VectorHit(
                id=str(doc_id),
                text=docs[idx] if idx < len(docs) else "",
                metadata=(metas[idx] if idx < len(metas) and metas[idx] else {}),
                score=score))
        return hits

    def delete(self, collection: str, ids: list[str]) -> int:
        col = self._collection()
        col.delete(ids=list(ids))
        return len(ids)

    def health(self) -> dict[str, Any]:
        try:
            col = self._collection()
            count = col.count()
            return {"backend": self.name, "ok": True, "collection_count": count}
        except Exception as exc:  # noqa: BLE001 健康探针不应抛出
            return {"backend": self.name, "ok": False,
                    "collection_count": 0, "error": str(exc)}
