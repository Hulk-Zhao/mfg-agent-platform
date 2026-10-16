"""RAG 知识库工具：默认本地持久化（./data/chroma），无需独立ChromaDB服务。
若环境变量 CHROMA_HOST 显式设置为非 localhost，则走HttpClient模式。
"""
import os
from typing import Any
import chromadb
from ..config import settings

_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection

    # 只有显式配置了远端 CHROMA_HOST 才用HttpClient；否则本地持久化
    host = os.getenv("CHROMA_HOST", "localhost")
    if host and host not in ("localhost", "127.0.0.1", ""):
        client = chromadb.HttpClient(host=host, port=settings.CHROMA_PORT)
    else:
        # 本地文件持久化，数据落在 ./data/chroma
        persist_dir = os.path.join(os.getcwd(), "data", "chroma")
        os.makedirs(persist_dir, exist_ok=True)
        client = chromadb.PersistentClient(path=persist_dir)

    _collection = client.get_or_create_collection("failure_cases")
    return _collection


def add_case(doc_id: str, document: str, metadata: dict[str, Any]):
    col = _get_collection()
    col.upsert(ids=[doc_id], documents=[document], metadatas=[metadata])


def search_cases(query: str, n_results: int = 3) -> list[dict[str, Any]]:
    col = _get_collection()
    res = col.query(query_texts=[query], n_results=n_results)
    out = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    for d, m in zip(docs, metas):
        out.append({"document": d, "metadata": m or {}})
    return out
