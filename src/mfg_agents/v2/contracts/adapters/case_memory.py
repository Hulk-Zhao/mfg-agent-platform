"""``MemoryCaseIndex``：**演示默认**的纯内存向量索引（零第三方依赖）。

为什么默认它：demo 必须在"没有 Chroma、没有 Milvus、没有 GPU、没有网络"的机器上
一条命令跑起来，且**结果确定**（同一 query + 同一 filter 必然同一结果，便于单测断言
"不同岗位得到不同结果"）。它不是玩具：它的过滤路径与生产后端**同契约**
（``FilterNode`` → ``to_predicate``），因此可以当作"权限语义的参照实现"。

检索算法（确定性，无随机数、无模型调用）：

1. **先过滤**：``to_predicate(filter)`` 逐条判定元数据——过滤发生在打分之前，
   不存在"先召回后过滤"（BR-01）；
2. 向量侧：中文按 **字符 1-2 gram** 哈希到 256 维并 L2 归一化，做余弦相似度；
3. 关键词侧：查询与文本的 2-gram 覆盖率（Jaccard 型）；
4. 混合分 = ``0.6 * cosine + 0.4 * keyword``（权重沿用"dense 为主、sparse 为辅"的
   产品共识，不做调参）。

说明（诚实边界）：哈希 embedding 不具备语义泛化能力（"锡膏偏移" 不会自动匹配
"印刷偏位"）——那是 Ontology 同义扩展 + 生产 BGE-M3 的职责。本类的目标是把
**权限链路**跑通并可断言，不是把召回率做到生产水平。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from ..case_acl import FilterNode
from ..case_index import CaseVectorIndex, IndexHealth, KnowledgeChunk, SearchHit
from ...security.filter_translator import to_predicate

__all__ = ["MemoryCaseIndex", "EMBED_DIM", "embed_text"]

#: 哈希 embedding 维度（256 足够区分 demo 语料，且单条编码 < 1ms）。
EMBED_DIM = 256

#: 中文/英文/数字切分（保留中文单字与字母数字块）。
_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5]|[A-Za-z0-9]+")


def _tokens(text: str) -> list[str]:
    """粗切分：中文单字 + 英文/数字块（供 1-2 gram 构造）。"""
    return _TOKEN_RE.findall(str(text or "").lower())


def _grams(text: str) -> list[str]:
    """字符 1-2 gram（中文按字、英文按块，够用且确定）。"""
    tokens = _tokens(text)
    grams: list[str] = list(tokens)
    for first, second in zip(tokens, tokens[1:]):
        grams.append(f"{first}{second}")
    return grams


def _bucket(gram: str) -> tuple[int, float]:
    """gram → (维度下标, 符号)。符号来自哈希，避免所有 gram 同向相加导致相似度虚高。"""
    digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % EMBED_DIM, (1.0 if (value >> 63) & 1 else -1.0)


def embed_text(text: str) -> list[float]:
    """确定性哈希 embedding（L2 归一化）。"""
    vector = [0.0] * EMBED_DIM
    for gram in _grams(text):
        index, sign = _bucket(gram)
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0.0:
        return vector
    return [v / norm for v in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


def _keyword_score(query_grams: set[str], text_grams: set[str]) -> float:
    if not query_grams or not text_grams:
        return 0.0
    overlap = len(query_grams & text_grams)
    return overlap / float(len(query_grams))


class MemoryCaseIndex(CaseVectorIndex):
    """纯内存索引（默认后端）。线程内共享；写入后立即对检索可见。"""

    backend = "memory"

    def __init__(self) -> None:
        # chunk_id -> (chunk, row, embedding, gram 集合)
        self._store: dict[str, tuple[KnowledgeChunk, dict[str, Any],
                                     list[float], set[str]]] = {}

    # ---------- 写入 ----------

    def upsert_chunks(self, chunks: Sequence[KnowledgeChunk]) -> int:
        written = 0
        for chunk in chunks or ():
            if not isinstance(chunk, KnowledgeChunk):
                # 闸门 4 的运行期形态：证据层/别的类型一律拒绝
                raise TypeError(
                    "只允许写入 KnowledgeChunk（证据层内容禁止进入知识层索引）: "
                    f"{type(chunk).__name__}")
            row = chunk.to_row()
            self._store[chunk.chunk_id] = (
                chunk,
                row,
                embed_text(chunk.text),
                set(_grams(chunk.text)),
            )
            written += 1
        return written

    def delete_by_case(self, case_id: str) -> int:
        target = str(case_id or "")
        doomed = [cid for cid in self._store if cid.startswith(f"{target}::")]
        for cid in doomed:
            del self._store[cid]
        return len(doomed)

    # ---------- 检索 ----------

    def search(self, query: str, filter: FilterNode, k: int = 5) -> list[SearchHit]:
        # ① 先编译谓词——空 filter 会在这里 raise（绝不放行"不过滤"）
        predicate = to_predicate(filter)
        query_vector = embed_text(query)
        query_grams = set(_grams(query))
        hits: list[SearchHit] = []
        for chunk, row, vector, grams in self._store.values():
            # ② 过滤在打分之前：不满足权限条件的 chunk 根本不参与计算
            if not predicate(row):
                continue
            score = 0.6 * _cosine(query_vector, vector) + 0.4 * _keyword_score(
                query_grams, grams)
            hits.append(SearchHit(
                case_id=chunk.case_id,
                chunk_id=chunk.chunk_id,
                chunk_type=chunk.chunk_type,
                content_version=chunk.content_version,
                text=chunk.text,
                score=round(float(score), 6),
                metadata=dict(row),
            ))
        hits.sort(key=lambda h: (-h.score, h.chunk_id))
        return hits[:max(0, int(k))]

    def count(self, filter: FilterNode) -> int:
        predicate = to_predicate(filter)
        return sum(1 for _chunk, row, _v, _g in self._store.values() if predicate(row))

    def count_cases(self, filter: FilterNode) -> int:
        """满足条件的**案例数**（按 ``case_id`` 去重；隐藏条数的口径，R2）。"""
        predicate = to_predicate(filter)
        return len({str(row.get("case_id") or "")
                    for _chunk, row, _v, _g in self._store.values()
                    if predicate(row)})

    def filtered_case_ids(self, filter: FilterNode, cap: int = 0) -> list[str]:
        """只做过滤、**不打分**的案例 id 扫描（受限清单用）。

        为什么必须单独一条路径：``search()`` 会给每个 chunk 算余弦 + 关键词分，
        而"受限清单"只关心"哪些案例通过了硬维度"，与相关性毫无关系。
        语料 ~100 例 / ~400 chunk 时，那点打分开销会让每次检索多出几十毫秒，
        29 条用例的服务层测试直接跑成 5 分钟。**这条路径不做 embedding、不排序**。
        """
        predicate = to_predicate(filter)
        seen: list[str] = []
        known: set[str] = set()
        for _chunk, row, _v, _g in self._store.values():
            if not predicate(row):
                continue
            case_id = str(row.get("case_id") or "")
            if not case_id or case_id in known:
                continue
            known.add(case_id)
            seen.append(case_id)
            if cap and len(seen) >= int(cap):
                break
        return seen

    def health(self) -> IndexHealth:
        return IndexHealth(backend=self.backend, ready=True,
                           chunk_count=len(self._store), detail="in-process")

    # ---------- 测试/运维辅助 ----------

    def chunk_ids(self) -> list[str]:
        return sorted(self._store)

    def rows(self) -> list[Mapping[str, Any]]:
        """全部元数据行（**不含** text 之外的可疑内容；仅供对账与单测）。"""
        return [dict(row) for _c, row, _v, _g in self._store.values()]

    def clear(self) -> None:
        self._store.clear()

    def texts(self) -> Iterable[str]:
        return (chunk.text for chunk, _r, _v, _g in self._store.values())
