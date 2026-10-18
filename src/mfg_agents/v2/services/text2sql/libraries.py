"""注册库与指标库 + 表级权限（Text2SQLService 的配置/口径层）。

从 ``services/text2sql.py`` 拆分而来：``TABLE_ACL`` / ``MetricLibrary`` /
``QueryLibrary`` 归此文件（对外符号经 ``services.text2sql`` 包重导出，
import 路径不变）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

__all__ = ["TABLE_ACL", "MetricLibrary", "QueryLibrary"]

# ---------- 表级权限（白名单，fail-closed；coordinator=总控跨域取证角色） ----------
# 角色口径与登录岗位（demo/ui demo-data.js ROLES）保持一致：
#   gm=总经理（五域全局 · 只读汇总）→ 全域可读；
#   material_control=物控（物料 · 库存 · 齐套）、sales=业务经理（订单 · 对客 ETA · 出货）
#   为本岗位核心场景表。
# 全部演示岗位的治理角色令牌（demo/ui/demo-data.js ROLES）+ 域级/跨域令牌。
# 供「对所有人开放」的表直接复用：新增岗位时只改这一处，不会漏配。
_ALL_ROLES: set[str] = {
    "gm", "manufacturing", "equipment", "sqe", "pqe", "qa",
    "rd_engineering", "product_engineering", "planning", "material_control",
    "procurement", "warehouse", "sales", "finance",
    "quality_director", "equipment_manager", "rd_manager", "supply_chain_manager",
    "procurement_manager", "warehouse_manager", "finance_manager",
    "ie_engineer", "npi_engineer", "project_manager", "test_engineer",
    "me_engineer", "rd_test_engineer", "admin_hr", "ehs", "customer_service",
    "maintenance_tech",
    # 域级 / 跨域令牌（沿用既有 ACL 口径）
    "quality", "qa_manager", "coordinator",
}
TABLE_ACL: dict[str, set[str]] = {
    # V3.1：quality 加入产出/停线表——质量分析（FPY/良率）本职需要产出与停线数据
    "production_output": {"manufacturing", "planning", "coordinator", "quality", "gm",
                          "supply_chain_manager"},
    "line_stop_event": {"manufacturing", "coordinator", "quality", "gm"},
    "quality_defect": {"quality", "manufacturing", "coordinator", "gm"},
    # V3.6 SPC 域：测量值表（spc.ingest 落库 / spc 取数），口径与 quality_defect 一致
    "spc_measurement": {"quality", "manufacturing", "coordinator", "gm"},
    # V3.3：material_control 加入——齐套检查/到货批次是物控核心场景
    "material_batch": {"procurement", "quality", "coordinator", "material_control", "gm",
                       "supply_chain_manager"},
    "equipment_log": {"manufacturing", "coordinator", "gm", "equipment_manager"},
    "inventory": {"warehouse", "supply_chain", "coordinator", "gm"},
    # V3.7 库存域（材料库存/成品库存/来料批次综合）· 数据速查数据源：
    # **对全部岗位开放只读**（需求变更：数据查询权限对所有岗位开通）。
    # 口径仍是注册查询（只读、fail-closed 机制不变，仅白名单放宽到全岗位）；
    # 若后续要恢复岗位隔离，把 _ALL_ROLES 换回具体岗位白名单即可。
    "material_inventory": set(_ALL_ROLES),
    "finished_inventory": set(_ALL_ROLES),
    "incoming_batch": set(_ALL_ROLES),
    # sales=业务经理：对客 ETA / 出货风险 / 客诉响应核心场景
    "shipment": {"business", "warehouse", "coordinator", "sales", "gm",
                 "supply_chain_manager"},
    "customer_complaint": {"quality", "business", "coordinator", "sales", "gm"},
    # V3.2 职能域：财务制度**高管限定**（PM 鉴权裁定——财务数据仅总经理/财务可咨询）
    "finance_policy": {"finance", "gm", "coordinator"},
    # 行政制度全员可读（公开制度，15 角色全量）
    "admin_policy": {"manufacturing", "planning", "quality", "procurement",
                     "material_control", "sales", "warehouse",
                     "rd_engineering", "finance", "gm", "quality_director",
                     "qa_manager", "qa", "sqe", "pqe", "equipment", "coordinator",
                     "equipment_manager", "rd_manager", "supply_chain_manager",
                     "procurement_manager", "warehouse_manager", "finance_manager"},
    # V3.8 企业制度库（16 类制造制度，公开制度全员可读，口径与 admin_policy 一致）
    "kbqa_policy": {"manufacturing", "planning", "quality", "procurement",
                    "material_control", "sales", "warehouse",
                    "rd_engineering", "finance", "gm", "quality_director",
                    "qa_manager", "qa", "sqe", "pqe", "equipment", "coordinator",
                    "equipment_manager", "rd_manager", "supply_chain_manager",
                    "procurement_manager", "warehouse_manager", "finance_manager"},
    # V3.3 项目/测试（NPI/PM/测试智能体数据底座）：项目信息透明全员可读；
    # 测试记录限测试/质量/研发/PM/制造相关角色
    "project_milestone": {"manufacturing", "planning", "quality", "procurement",
                          "material_control", "sales", "warehouse",
                          "rd_engineering", "finance", "gm", "quality_director",
                          "qa_manager", "qa", "sqe", "pqe", "equipment", "coordinator",
                          "equipment_manager", "rd_manager", "supply_chain_manager",
                          "procurement_manager", "warehouse_manager", "finance_manager"},
    "test_records": {"quality", "rd_engineering", "manufacturing", "gm",
                     "quality_director", "qa_manager", "coordinator"},
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

    def all(self) -> list[dict]:
        """全部指标定义（只读枚举，按 id 排序；仅供指标字典展示）。

        返回**浅拷贝**：调用方无法借返回值改动库内定义（口径单一事实源）。
        """
        return [dict(self._metrics[k]) for k in sorted(self._metrics)]

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

    def match(self, question: str, params: Optional[dict] = None) -> Optional[str]:
        """按关键词重叠打分取最优注册查询（严格大于才替换；平局取声明序先者）。

        V3.7：传入 ``params`` 时启用**可满足性优先**——同分/降分候选里，
        优先返回命名绑定已被 ``params`` 满足的查询，避免抽不到参数的重参查询
        顶掉无参速查（如"材料库存还有多少"被 :model 查询空手命中而缺参崩溃）。
        全部候选都不可满足时，仍返回最高分者（交由上层缺参守卫降级）。
        """
        scored = [(sum(1 for w in spec.get("keywords", []) if w in question), name)
                  for name, spec in self._queries.items()]
        best = [(s, n) for s, n in scored if s > 0]
        if not best:
            return None
        best.sort(key=lambda t: -t[0])     # 分数降序（稳定排序保留同分声明序）
        if params is not None:
            bound = set(params)
            for _score, name in best:      # 从高分到低分，取第一个参数可满足者
                sql = str(self._queries[name].get("sql", ""))
                needed = set(re.findall(r":([A-Za-z_]\w*)", sql))
                if not (needed - bound):
                    return name
        return best[0][1]
