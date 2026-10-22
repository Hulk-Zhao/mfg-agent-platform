"""RAG 知识库工具：向量库后端可插拔（chroma 默认 / milvus）。

- chroma（默认）：本地持久化（./data/chroma），无需独立服务；
  若环境变量 CHROMA_HOST 显式设置为非 localhost，则走 HttpClient 模式。
- milvus：环境变量 VECTOR_BACKEND=milvus 时启用，连接 Docker standalone
  （MILVUS_URI，默认 http://localhost:19530）。适配器模拟 chroma collection
  的 upsert/query/delete/count 接口，``add_case``/``search_cases`` 及 V2 的
  ``ChromaVectorStore`` 均无需感知后端差异。

Milvus 模式检索链路（对齐 EDURAG Itcast_qa_system/rag_qa/core/vector_store.py）：
- embedding：BGE-M3 本地模型（MILVUS_EMBEDDING_MODEL，默认 models/bge-m3），
  dense(1024, IP) + sparse 双路混合召回（WeightedRanker 0.7/0.3）；
- 重排序：BGE-Reranker-v2-M3 本地模型（MILVUS_RERANKER_MODEL）对候选重排，
  取 top n_results 返回。

distance 语义桥接：Milvus 混合检索返回的融合分越大越相关（IP 语义），
chroma 语义 distance 越小越好、下游 score = 1 - distance；故适配器输出
``distances = 1 - 融合分``，下游换算后即还原为融合分，零改动兼容。
"""
import os
from typing import Any

from .config import settings

# 多 collection 缓存：failure_cases（FA 相似案例）与 enterprise_faq
# （企业制度问答）并存，各自独立连接/加载，互不影响。
_collections: dict[str, Any] = {}

COLLECTION_FAILURE_CASES = "failure_cases"
COLLECTION_ENTERPRISE_FAQ = "enterprise_faq"


def _dense_fp32(vec: Any) -> Any:
    """BGE-M3 在 GPU(fp16) 下输出 float16 向量，而 collection 的
    dense_vector 字段为 FLOAT_VECTOR(fp32)——Milvus 对类型强校验，
    混用直接报 "vector type must be the same"。统一在适配器边界转 fp32：
    模型保持 fp16 计算速度，存储/检索侧始终 fp32。"""
    import numpy as np
    return np.asarray(vec, dtype="float32")


def _get_collection(name: str = COLLECTION_FAILURE_CASES):
    if name in _collections:
        return _collections[name]

    if settings.VECTOR_BACKEND == "milvus":
        _collections[name] = MilvusCollectionAdapter(
            uri=settings.MILVUS_URI, name=name)
        return _collections[name]

    # 只有显式配置了远端 CHROMA_HOST 才用HttpClient；否则本地持久化
    host = os.getenv("CHROMA_HOST", "localhost")
    if host and host not in ("localhost", "127.0.0.1", ""):
        import chromadb
        client = chromadb.HttpClient(host=host, port=settings.CHROMA_PORT)
    else:
        # 本地文件持久化，数据落在 ./data/chroma
        import chromadb
        persist_dir = os.path.join(os.getcwd(), "data", "chroma")
        os.makedirs(persist_dir, exist_ok=True)
        client = chromadb.PersistentClient(path=persist_dir)

    _collections[name] = client.get_or_create_collection(name)
    return _collections[name]


def _model_path(path: str) -> str:
    """模型路径解析：绝对路径直用；相对路径按运行目录解析。"""
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def _sparse_to_dict(row: Any) -> dict[int, float]:
    """BGE-M3 稀疏向量行 → {维度索引: 权重}（兼容 coo/csr 两种格式）。"""
    if hasattr(row, "col"):  # 新版 milvus-model：coo_array
        indices, values = row.col, row.data
    else:  # 旧版：csr_matrix
        indices, values = row.indices, row.data
    return {int(idx): float(v) for idx, v in zip(indices, values)}


class MilvusCollectionAdapter:
    """Milvus 适配器：模拟 chroma collection 的 upsert/query/delete/count。

    schema：id VARCHAR(主键,512) / document VARCHAR(65535) / metadata JSON /
    dense_vector FLOAT_VECTOR(dim=BGE-M3 dense 1024, IP) /
    sparse_vector SPARSE_FLOAT_VECTOR(IP)。

    惰性：构造不触发连接与模型加载；首次使用时连接 Milvus 并加载
    BGE-M3 / Reranker（CPU 自适应，CUDA 可用则启用 fp16）。
    """

    def __init__(self, uri: str = "http://localhost:19530",
                 name: str = "failure_cases") -> None:
        self._uri = uri
        self._name = name
        self._client: Any = None
        self._ef: Any = None
        self._reranker: Any = None

    # ---------- 惰性资源 ----------

    def _ensure(self) -> Any:
        """惰性连接 Milvus 并确保 collection/索引存在（幂等）。"""
        if self._client is not None:
            return self._client
        from pymilvus import DataType, MilvusClient

        # timeout 必须显式给：pymilvus 默认 None=无限等，Milvus 半死时
        # search 会永久挂住线程（2026-10-18 事故链的一环——前端无超时 +
        # 后端无超时 = 「思考中」转 6 分钟）。15s 覆盖检索/建索引全部 RPC。
        self._client = MilvusClient(uri=self._uri, timeout=15)
        if not self._client.has_collection(self._name):
            schema = self._client.create_schema(
                auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR,
                             is_primary=True, max_length=512)
            schema.add_field("document", DataType.VARCHAR, max_length=65535)
            schema.add_field("metadata", DataType.JSON)
            schema.add_field("dense_vector", DataType.FLOAT_VECTOR,
                             dim=self._dense_dim())
            schema.add_field("sparse_vector",
                             DataType.SPARSE_FLOAT_VECTOR)
            index = self._client.prepare_index_params()
            index.add_index(field_name="dense_vector", index_type="AUTOINDEX",
                            metric_type="IP")
            index.add_index(field_name="sparse_vector",
                            index_type="SPARSE_INVERTED_INDEX",
                            metric_type="IP")
            self._client.create_collection(
                self._name, schema=schema, index_params=index)
        # search 前必须 load（幂等，已加载时静默）
        try:
            self._client.load_collection(self._name)
        except Exception:  # noqa: BLE001
            pass
        return self._client

    def _embedder(self):
        """惰性加载 BGE-M3（dense+sparse 双路编码）。"""
        if self._ef is None:
            from milvus_model.hybrid import BGEM3EmbeddingFunction
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._ef = BGEM3EmbeddingFunction(
                model_name_or_path=_model_path(settings.MILVUS_EMBEDDING_MODEL),
                device=device, use_fp16=(device == "cuda"))
        return self._ef

    def _dense_dim(self) -> int:
        return int(self._embedder().dim["dense"])

    def _reranker_model(self):
        """惰性加载 BGE-Reranker-v2-M3（CrossEncoder）。"""
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._reranker = CrossEncoder(
                _model_path(settings.MILVUS_RERANKER_MODEL), device=device)
        return self._reranker

    # ---------- chroma collection 兼容接口 ----------

    def upsert(self, ids: list[str], documents: list[str],
               metadatas: list[dict] | None = None,
               embeddings: Any = None) -> int:
        """写入/更新（embeddings 参数仅作 chroma 接口兼容，恒用 BGE-M3 编码）。"""
        client = self._ensure()
        docs = list(documents)
        vecs = self._embedder()(docs)  # {"dense": [n,1024], "sparse": matrix}
        metas = metadatas if metadatas is not None else [{}] * len(docs)
        data = []
        for i, (doc_id, doc, meta) in enumerate(zip(ids, docs, metas)):
            data.append({
                "id": str(doc_id),
                "document": doc,
                "metadata": meta or {},
                "dense_vector": _dense_fp32(vecs["dense"][i]),
                "sparse_vector": _sparse_to_dict(vecs["sparse"][i]),
            })
        res = client.upsert(self._name, data=data)
        return int(res.get("upsert_count", len(data)))

    def query(self, query_texts: list[str], n_results: int = 3,
              where: dict | None = None, **_: Any) -> dict[str, list]:
        """混合召回 + 重排序，返回 chroma 风格嵌套结构（单查询语义）。"""
        if where:  # 当前无调用方使用；后续需 chroma where→Milvus filter 翻译
            raise NotImplementedError(
                "MilvusCollectionAdapter 暂不支持 where 过滤")
        client = self._ensure()
        query = list(query_texts)[0]
        vecs = self._embedder()([query])
        dense_q = _dense_fp32(vecs["dense"][0])
        sparse_q = _sparse_to_dict(vecs["sparse"][0])

        # 候选数：保证 >= n_results*3，供重排序挑选
        limit = max(settings.MILVUS_RERANKER_CANDIDATES, n_results * 3)
        from pymilvus import AnnSearchRequest, WeightedRanker

        dense_req = AnnSearchRequest(
            data=[dense_q], anns_field="dense_vector",
            param={"metric_type": "IP", "params": {}}, limit=limit)
        sparse_req = AnnSearchRequest(
            data=[sparse_q], anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {}}, limit=limit)
        hits = client.hybrid_search(
            self._name, reqs=[dense_req, sparse_req],
            ranker=WeightedRanker(0.7, 0.3), limit=limit,
            output_fields=["document", "metadata"])[0]

        # BGE-Reranker 对 (query, document) 配对重排
        pairs = [[query, h["entity"].get("document", "")] for h in hits]
        if pairs:
            scores = self._reranker_model().predict(pairs)
            order = sorted(range(len(hits)),
                           key=lambda i: float(scores[i]), reverse=True)
            hits = [hits[i] for i in order]

        top = hits[:n_results]
        # 融合分越大越好 → chroma 语义 distance = 1 - 分数（score=1-d 还原）
        return {
            "ids": [[str(h["id"]) for h in top]],
            "documents": [[h["entity"].get("document", "") for h in top]],
            "metadatas": [[h["entity"].get("metadata") or {} for h in top]],
            "distances": [[1.0 - float(h["distance"]) for h in top]],
        }

    def delete(self, ids: list[str]) -> int:
        client = self._ensure()
        res = client.delete(self._name, ids=[str(i) for i in ids])
        return int(res.get("delete_cnt", len(ids)))

    def count(self) -> int:
        client = self._ensure()
        try:  # 刷盘使 growing 段计入统计（无数据/重复 flush 静默）
            client.flush(self._name)
        except Exception:  # noqa: BLE001
            pass
        stats = client.get_collection_stats(self._name)
        return int(stats.get("row_count", 0))


def add_case(doc_id: str, document: str, metadata: dict[str, Any]):
    col = _get_collection(COLLECTION_FAILURE_CASES)
    col.upsert(ids=[doc_id], documents=[document], metadatas=[metadata])


def search_cases(query: str, n_results: int = 3) -> list[dict[str, Any]]:
    col = _get_collection(COLLECTION_FAILURE_CASES)
    res = col.query(query_texts=[query], n_results=n_results)
    out = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    for d, m in zip(docs, metas):
        out.append({"document": d, "metadata": m or {}})
    return out


# ---------- 企业制度问答（enterprise_faq collection） ----------


def add_kb_chunks(ids: list[str], documents: list[str],
                  metadatas: list[dict[str, Any]]) -> int:
    """写入企业制度知识块（metadata 建议含 {dept, source, chunk_index}）。"""
    col = _get_collection(COLLECTION_ENTERPRISE_FAQ)
    return int(col.upsert(ids=list(ids), documents=list(documents),
                          metadatas=list(metadatas)))


def search_kb(query: str, n_results: int = 3) -> list[dict[str, Any]]:
    """企业制度问答检索：返回 [{document, metadata, score}]。

    score = 1 - distance（Milvus 模式下即 BGE-M3 融合分经重排后的分值），
    仅作展示/调参参考，不参与业务判断。
    """
    col = _get_collection(COLLECTION_ENTERPRISE_FAQ)
    res = col.query(query_texts=[query], n_results=n_results)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0] if res.get("distances") else []
    out = []
    for i, (d, m) in enumerate(zip(docs, metas)):
        score = None
        if i < len(dists) and dists[i] is not None:
            score = round(1.0 - float(dists[i]), 4)
        out.append({"document": d, "metadata": m or {}, "score": score})
    return out


def kb_count() -> int:
    """企业制度知识库块数（用于前端展示/健康检查）。"""
    return int(_get_collection(COLLECTION_ENTERPRISE_FAQ).count())
