"""案例知识层索引契约：``CaseVectorIndex`` ABC + ``KnowledgeChunk`` 数据类型。

架构位置：§6.3（向量库设计）、§6.5 闸门 4（类型系统隔离）。

**``KnowledgeChunk`` 不含任何证据层字段**——这是刻意的类型设计（ADR-003）：
证据层内容用另一个 dataclass（``EvidenceContent``）表达，两者类型不同，误传会在
构造/断言处炸掉，而不是静默流进 LLM 上下文。构造时强制 ``is_knowledge_layer is True``，
违反即 ``KnowledgeLayerViolation``。

三个后端实现（都在 ``contracts/adapters/``）：

- ``case_memory.MemoryCaseIndex``  —— **默认**，纯内存、零依赖、确定性；
- ``case_chroma.ChromaCaseIndex``  —— 开发/POC（≤ 5 万 chunk）；
- ``case_milvus.MilvusCaseIndex``  —— 生产（惰性 import pymilvus，未安装即报错）。

三个实现共用同一套 ``FilterNode`` 契约与同一批单测用例（架构 §12.1 的双后端契约
测试思想）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .case_acl import FilterNode, KnowledgeLayerViolation

__all__ = [
    "IndexHealth",
    "KnowledgeChunk",
    "SearchHit",
    "EvidenceContent",
    "CaseVectorIndex",
]


@dataclass(frozen=True)
class KnowledgeChunk:
    """知识层 chunk——**唯一**允许进入向量库与 LLM 上下文的类型。

    Attributes:
        case_id: 所属案例。
        chunk_type: ``summary`` / ``phenomenon`` / ``root_cause`` / ``countermeasure``。
        content_version: 引用校验用（业务上不暴露版本号，见裁定 A8）。
        text: **已脱敏**的知识层文本。
        metadata: 过滤维度（键必须落在 ``FILTERABLE_FIELDS`` 内）+ 展示字段。
        is_knowledge_layer: 硬门禁，必须为 ``True``。
        score: 检索分数（写入时为 0，检索结果里由后端回填）。
    """

    case_id: str
    chunk_type: str
    content_version: int = 1
    text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    is_knowledge_layer: bool = True
    score: float = 0.0

    def __post_init__(self) -> None:
        # 闸门 1：写入侧拦截证据层（BR-08 / ADR-003）
        if self.is_knowledge_layer is not True:
            raise KnowledgeLayerViolation(
                f"证据层内容禁止进入知识层索引（case_id={self.case_id!r}）")
        if not str(self.case_id or "").strip():
            raise KnowledgeLayerViolation("chunk 缺少 case_id")
        if not str(self.chunk_type or "").strip():
            raise KnowledgeLayerViolation("chunk 缺少 chunk_type")

    @property
    def chunk_id(self) -> str:
        """``case_id::chunk_type``（**不含 content_version**，保证 upsert 覆盖无孤儿）。"""
        return f"{self.case_id}::{self.chunk_type}"

    def to_row(self) -> dict[str, Any]:
        """扁平化为"一行元数据"（内存后端谓词直接吃这个形状）。"""
        row: dict[str, Any] = dict(self.metadata or {})
        row.update({
            "chunk_id": self.chunk_id,
            "case_id": self.case_id,
            "chunk_type": self.chunk_type,
            "content_version": self.content_version,
            "is_knowledge_layer": True,
        })
        return row


@dataclass(frozen=True)
class EvidenceContent:
    """证据层内容（8D 原件 / 检测报告 / 客户邮件）。

    **禁止**进入向量库、禁止进入 prompt、禁止进日志。它存在的意义是让类型系统
    在有人误把证据层塞进检索链路时抛 ``TypeError``（闸门 4）。
    """

    evidence_id: str
    case_id: str
    uri: str = ""
    raw: bytes = b""
    cls_rank: int = 3


@dataclass(frozen=True)
class SearchHit:
    """一次检索命中（知识层）。"""

    case_id: str
    chunk_id: str
    chunk_type: str = ""
    content_version: int = 1
    text: str = ""
    score: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexHealth:
    """索引健康状态（``/health`` 与降级判定用）。"""

    backend: str
    ready: bool
    chunk_count: int = 0
    detail: str = ""


class CaseVectorIndex(ABC):
    """案例知识层向量索引契约（三后端可实现，可替换）。"""

    #: 后端标识（``memory`` / ``chroma`` / ``milvus``），审计与健康检查用。
    backend: str = "abstract"

    @abstractmethod
    def upsert_chunks(self, chunks: Sequence[KnowledgeChunk]) -> int:
        """幂等写入 chunk（同 ``chunk_id`` 覆盖，无孤儿）。

        Returns:
            实际写入条数。
        """

    @abstractmethod
    def delete_by_case(self, case_id: str) -> int:
        """按案例失效索引（撤权/下架/重建），返回删除条数。"""

    @abstractmethod
    def search(self, query: str, filter: FilterNode, k: int = 5) -> list[SearchHit]:
        """**先过滤再算分**的检索（过滤在召回前生效，BR-01 前半）。

        Args:
            query: 查询文本（只影响相关性打分，**不影响权限过滤**）。
            filter: 已编译的过滤 AST（永不允许传 ``None``）。
            k: 返回条数上限。
        """

    @abstractmethod
    def count(self, filter: FilterNode) -> int:
        """计满足过滤条件的 chunk 数（``hidden_count`` 用，不返回任何正文）。"""

    @abstractmethod
    def health(self) -> IndexHealth:
        """后端可用性（不可用时上层降级为 ``degraded``，**不编造结果**）。"""
