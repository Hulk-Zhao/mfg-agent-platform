"""DomainState：分域状态 + LangGraph reducer（按域合并，域间不覆盖）。

V1 单体 AgentState 所有域字段平铺，跨域并行时存在互相覆盖风险（D3）。
V2 将状态拆为 {domain: DomainState} 的字典通道，reducer 保证：
- 不同域的写入互不影响（并行 fan-out 安全）；
- 同一域的重复写入以最后一次为准（单域内顺序执行）。
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict


class DomainState(TypedDict, total=False):
    """单域执行结果。"""

    domain: str                 # 域标识（manufacturing / quality / ...）
    status: str                 # ok | degraded | failed
    payload: dict[str, Any]     # 域产出（结论数据）
    summary: str                # 人类可读摘要（事件流展示）
    elapsed_ms: float           # 节点耗时（provenance）
    error: str | None           # 降级/失败原因


def merge_domain_states(
    left: dict[str, DomainState],
    right: dict[str, DomainState],
) -> dict[str, DomainState]:
    """LangGraph reducer：按域合并，域间不覆盖。"""
    merged = dict(left)
    for domain, state in right.items():
        merged[domain] = state
    return merged


def _append_list(left: list, right: list) -> list:
    """列表通道 reducer：追加（用于事件流 / HITL / 工单收集）。"""
    return list(left) + list(right)


class DagState(TypedDict, total=False):
    """YamlDagExecutor 的图状态。

    - domains: 分域结果（reducer 合并，并行安全）
    - events / hitl / workflow: 追加式收集
    - entities: 入口抽取的实体（单写，入口节点独占）
    - query: 用户原始问题
    - user_ctx: 用户上下文（dept/roles，权限核验用；init 时单写）
    """

    query: str
    user_ctx: dict[str, Any]
    entities: dict[str, Any]
    domains: Annotated[dict[str, DomainState], merge_domain_states]
    events: Annotated[list[dict[str, Any]], _append_list]
    hitl: Annotated[list[dict[str, Any]], _append_list]
    workflow: Annotated[list[dict[str, Any]], _append_list]
    final: dict[str, Any]
