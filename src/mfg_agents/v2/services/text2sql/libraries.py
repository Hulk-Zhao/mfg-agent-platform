"""注册库与指标库 + 表级权限（Text2SQLService 的配置/口径层）。

从 ``services/text2sql.py`` 拆分而来：``TABLE_ACL`` / ``MetricLibrary`` /
``QueryLibrary`` 归此文件（对外符号经 ``services.text2sql`` 包重导出，
import 路径不变）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

__all__ = ["TABLE_ACL", "MetricLibrary", "QueryLibrary"]

# ---------- 表级权限（白名单，fail-closed；coordinator=总控跨域取证角色） ----------
TABLE_ACL: dict[str, set[str]] = {
    # V3.1：quality 加入产出/停线表——质量分析（FPY/良率）本职需要产出与停线数据
    "production_output": {"manufacturing", "planning", "coordinator", "quality"},
    "line_stop_event": {"manufacturing", "coordinator", "quality"},
    "quality_defect": {"quality", "manufacturing", "coordinator"},
    "material_batch": {"procurement", "quality", "coordinator"},
    "equipment_log": {"manufacturing", "coordinator"},
    "inventory": {"warehouse", "supply_chain", "coordinator"},
    "shipment": {"business", "warehouse", "coordinator"},
    "customer_complaint": {"quality", "business", "coordinator"},
}


class MetricLibrary:
    """指标库（YAML 指标定义即代码，§5.2）。"""

    def __init__(self, metrics: list[dict]) -> None:
        self._metrics = {str(m["id"]): m for m in metrics}

    @classmethod
    def load(cls, metrics_dir: str | Path) -> "MetricLibrary":
        metrics: list[dict] = []
        for path in sorted(Path(metrics_dir).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for metric in data.get("metrics", []):
                if not metric.get("id") or not metric.get("sql"):
                    raise ValueError(f"指标定义缺少 id/sql: {path.name}")
                metrics.append(metric)
        if not metrics:
            raise ValueError(f"指标库为空: {metrics_dir}")
        return cls(metrics)

    def get(self, metric_id: str) -> dict:
        if metric_id not in self._metrics:
            raise KeyError(f"指标未定义: {metric_id!r}")
        return self._metrics[metric_id]

    def match(self, question: str) -> Optional[dict]:
        """按关键词匹配指标（命中返回定义，否则 None）。"""
        best: Optional[dict] = None
        best_score = 0
        for metric in self._metrics.values():
            score = sum(1 for w in metric.get("keywords", []) if w in question)
            if score > best_score:
                best, best_score = metric, score
        return best


class QueryLibrary:
    """注册查询库（data/queries.yaml）。"""

    def __init__(self, queries: dict[str, dict]) -> None:
        self._queries = queries

    @classmethod
    def load(cls, queries_file: str | Path) -> "QueryLibrary":
        data = yaml.safe_load(Path(queries_file).read_text(encoding="utf-8")) or {}
        queries = data.get("queries", {})
        if not queries:
            raise ValueError(f"注册查询库为空: {queries_file}")
        for name, spec in queries.items():
            if not spec.get("sql"):
                raise ValueError(f"注册查询缺少 sql: {name}")
        return cls(queries)

    def get(self, name: str) -> dict:
        if name not in self._queries:
            raise KeyError(f"注册查询不存在: {name!r}")
        return self._queries[name]

    def all(self) -> dict[str, dict]:
        """全部注册查询（Agentic Agent 的工具目录来源；只读视图）。"""
        return dict(self._queries)

    def match(self, question: str) -> Optional[str]:
        best_name: Optional[str] = None
        best_score = 0
        for name, spec in self._queries.items():
            score = sum(1 for w in spec.get("keywords", []) if w in question)
            if score > best_score:
                best_name, best_score = name, score
        return best_name
