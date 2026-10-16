"""SharedContext：跨域共享事实层（只增不改，带 source 溯源）。

替代 V1 单体 AgentState 的跨域共享（D3：单体状态在跨域并行下互相覆盖）。
规则（B.3 第 7 条）：
- 只增不改：任何域写入事实必须带 source（哪个 Agent / 哪条 SQL）；
- 下游 LLM 引用数字必须能溯源到 source，否则判为幻觉缺陷。
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class ContextRecord:
    """一条事实记录：值 + 来源 + 域 + 时间戳（只增不改的最小单元）。"""

    key: str
    value: Any
    source: str
    domain: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SharedContext:
    """只增不改的共享事实表。

    - merge(domain, data, source)：把一个域的产出按 key 逐条追加记录；
    - get(key)：取该 key 最新一次写入的值（先写赢展示，历史不丢）；
    - snapshot()：全部 key 的最新值扁平字典（给报告/LLM 用）；
    - provenance(key)：该 key 的全部写入记录（审计用）。
    """

    def __init__(self) -> None:
        self._records: dict[str, list[ContextRecord]] = {}
        self._lock = threading.Lock()

    def merge(self, domain: str, data: dict[str, Any], source: str = "") -> None:
        """把 data 中每个 key 作为一条只增记录写入（线程安全，支持并行域写入）。"""
        if not isinstance(data, dict):
            raise TypeError(f"SharedContext.merge 仅接受 dict，收到 {type(data)!r}")
        with self._lock:
            for key, value in data.items():
                self._records.setdefault(key, []).append(
                    ContextRecord(key=key, value=value, source=source, domain=domain))

    def get(self, key: str, default: Any = None) -> Any:
        """取 key 最新值；从未写入返回 default。"""
        records = self._records.get(key)
        return records[-1].value if records else default

    def history(self, key: str) -> list[ContextRecord]:
        """取 key 的全部写入记录（只增历史）。"""
        return list(self._records.get(key, []))

    def provenance(self, key: Optional[str] = None) -> list[dict[str, Any]]:
        """溯源记录；key 为空时返回全部 key 的全部记录（按时间排序）。"""
        with self._lock:
            if key is not None:
                return [r.to_dict() for r in self._records.get(key, [])]
            all_records = [r for recs in self._records.values() for r in recs]
        all_records.sort(key=lambda r: r.timestamp)
        return [r.to_dict() for r in all_records]

    def snapshot(self) -> dict[str, Any]:
        """全部 key 的最新值（报告与 LLM 上下文的数据面）。"""
        return {key: recs[-1].value for key, recs in self._records.items()}

    def keys(self) -> list[str]:
        return list(self._records)
