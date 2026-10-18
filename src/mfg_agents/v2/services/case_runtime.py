"""CaseRuntime：案例助手运行时的**唯一装配点**（依赖注入容器）。

为什么单独一个模块（而不是塞进 ``demo/api.py``）：四个入口（HTTP / MCP / A2A / IM）
必须共享**同一个** ``CaseSearchService``（架构 §9.5 "所有入口 → 同一个 search"）。
把装配逻辑放在服务层，路由只做"解析主体 + 调服务 + 映射错误码"。

替换点（都是构造参数，业务代码零改动）：

| 要接的东西 | 换哪里 |
|---|---|
| Milvus | ``CASE_VECTOR_BACKEND=milvus``（``create_case_index`` 内装配） |
| SSO / IdP | ``PrincipalResolver.from_payload`` 换成真实 IdP 的 claims 映射 |
| 工单系统（8D/IQC） | 调 ``CaseIngestService.ingest``，来源校验会挡住无来源数据 |
| LLM | ``CaseQAService(answerer=...)`` |
| 重排模型 | ``CaseSearchService(reranker=...)`` |
| 词表审批流 | 替换 ``OntologyRegistry`` 的加载源（YAML → 审批服务 API） |
| 访问申请审批人 | ``AccessApprover``（ABC，``services/case_auto_approve.py``） | ``DemoAgentApprover``（演示：职能管理岗代批） | 实现同一 ABC（如 OA/IM 审批流回调），在 ``CaseRuntime.build`` 装配处替换 |
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from ...common.config import settings
from .case_audit import CaseAuditLog
from .case_index import CaseIndexService, create_case_index
from .case_ingest import CaseIngestService
from .case_qa import CaseQAService
from .case_search import CaseSearchService
from .case_store import VISIBLE_STATUS, CaseStore
from .principal_resolver import PrincipalResolver
from .redaction import get_redaction_service

logger = logging.getLogger(__name__)

__all__ = ["CaseRuntime", "get_case_runtime", "reset_case_runtime"]


@dataclass
class CaseRuntime:
    """案例助手运行时（store / index / search / qa / ingest / audit 的装配结果）。"""

    store: CaseStore
    index_service: CaseIndexService
    search: CaseSearchService
    qa: CaseQAService
    ingest: CaseIngestService
    audit: CaseAuditLog
    resolver: PrincipalResolver
    #: 跨部门访问申请（授权 + 审批）
    access: Any = None
    #: 访问申请的路由 Agent（只路由，不审批）
    access_agent: Any = None
    #: 访问申请的审批人提供者（**预留接口**：演示实现 = 职能管理岗代批；
    #: 生产替换为真实审批流实现，装配处换掉即可，服务端零改动）
    access_approver: Any = None

    # ---------- 装配 ----------

    @classmethod
    def build(cls, db_path: Optional[str] = None,
              *, backend: Optional[str] = None,
              seed_path: Optional[str] = None,
              auto_seed: bool = True) -> "CaseRuntime":
        """装配一套运行时（测试可传临时库 + 指定后端）。"""
        store = CaseStore(db_path)
        index = create_case_index(backend)
        index_service = CaseIndexService(store, index)
        audit = CaseAuditLog(store.db_path)
        search = CaseSearchService(store, index_service, audit=audit)
        qa = CaseQAService(search, store, index_service, audit=audit)
        ingest = CaseIngestService(store, index_service,
                                   redaction=get_redaction_service())
        from ..agents.case_access_agent import CaseAccessAgent
        from ..services.case_access import CaseAccessService

        runtime = cls(store=store, index_service=index_service, search=search,
                      qa=qa, ingest=ingest, audit=audit,
                      resolver=PrincipalResolver(),
                      access=CaseAccessService(store, index_service, audit=audit),
                      access_agent=CaseAccessAgent(store, audit))
        from .case_auto_approve import DemoAgentApprover

        runtime.access_approver = DemoAgentApprover(store)
        if auto_seed:
            runtime.ensure_seeded(seed_path)
        return runtime

    # ---------- 种子与索引 ----------

    def ensure_seeded(self, seed_path: Optional[str] = None) -> dict[str, int]:
        """首次启动时幂等导入种子案例，并把可见案例落到索引。

        Returns:
            ``{"imported": n, "indexed": n, "skipped": n}``
        """
        stats = {"imported": 0, "indexed": 0, "skipped": 0}
        target = self._seed_target(seed_path)
        if self.store.count() == 0:
            if target is not None and target.is_file():
                # B3：种子与线上入库走同一套脱敏 + 复检（种子不是豁免通道）
                from .redaction import get_redaction_service

                stats["imported"] = self.store.seed_from_json(
                    target, redactor=get_redaction_service())
            else:
                logger.warning("[case_runtime] 未找到种子文件，案例库为空: %s", target)
        rebuilt = self.index_service.rebuild()
        stats["indexed"] = rebuilt.get("chunks", 0)
        stats["skipped"] = rebuilt.get("skipped", 0)
        return stats

    @staticmethod
    def _seed_target(seed_path: Optional[str]) -> Optional[Path]:
        raw = str(seed_path or getattr(settings, "CASE_SEED_PATH", "") or "").strip()
        return Path(raw) if raw else None

    # ---------- 运维入口 ----------

    def visible_cases(self) -> list[dict[str, Any]]:
        """可见状态的全部案例（运维/对账用，**不经用户身份**）。"""
        return self.store.list_cases(status=VISIBLE_STATUS)

    def health(self) -> dict[str, Any]:
        """案例域健康信息（供 ``/api/v2/cases/meta`` 与排障）。"""
        index_health = self.index_service.health()
        return {
            "backend": index_health.backend,
            "ready": index_health.ready,
            "chunk_count": index_health.chunk_count,
            "detail": index_health.detail,
            "cases": self.store.count(),
            "visible_cases": len(self.visible_cases()),
            "audit_rows": self.audit.count(),
            "demo_mode": bool(getattr(settings, "CASE_DEMO_MODE", True)),
            "expose_hidden_count": bool(
                getattr(settings, "CASE_EXPOSE_HIDDEN_COUNT", True)),
        }

    def describe_permissions(self) -> Mapping[str, Any]:
        """动作矩阵（供前端展示"当前主体可做什么"）。"""
        return self.search.policy.to_meta()


_RUNTIME: Optional[CaseRuntime] = None
_LOCK = threading.Lock()


def get_case_runtime() -> CaseRuntime:
    """运行时单例（首次调用时装配 + 幂等种子）。"""
    global _RUNTIME
    if _RUNTIME is None:
        with _LOCK:
            if _RUNTIME is None:
                _RUNTIME = CaseRuntime.build()
    return _RUNTIME


def reset_case_runtime() -> None:
    """清空单例（测试注入临时库用）。"""
    global _RUNTIME
    _RUNTIME = None
