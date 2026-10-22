"""注册库与指标库 + 表级权限（Text2SQLService 的配置/口径层）。

从 ``services/text2sql.py`` 拆分而来：``TABLE_ACL`` / ``MetricLibrary`` /
``QueryLibrary`` 归此文件（对外符号经 ``services.text2sql`` 包重导出，
import 路径不变）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

import yaml

__all__ = ["TABLE_ACL", "MetricLibrary", "QueryLibrary",
           "table_acl", "grant_table_access", "revoke_table_access",
           "reset_table_grants"]

# ---------- 表级权限（白名单，fail-closed；coordinator=总控跨域取证角色） ----------
# 角色口径与登录岗位（demo/ui demo-data.js ROLES）保持一致：
#   gm=总经理（五域全局 · 只读汇总）→ 全域可读；
#   material_control=物控（物料 · 库存 · 齐套）、sales=业务经理（订单 · 对客 ETA · 出货）
#   为本岗位核心场景表。
TABLE_ACL: dict[str, set[str]] = {
    # V3.1：quality 加入产出/停线表——质量分析（FPY/良率）本职需要产出与停线数据
    # R5 真机（2026-10-22）：sales 加入——对客交期答复（hitl.eta）必须用近 7 日
    # 产出推算完工日；缺此权限时业务经理的交付答复永远 NO_DATA（真机实测）。
    # 仅开放产出聚合（planned/actual/oee），停线根因表仍不对 sales 开放。
    "production_output": {"manufacturing", "planning", "coordinator", "quality", "gm",
                          "supply_chain_manager", "sales",
                          # 2026-10-22 岗位推荐问题可答性：IE 的节拍/产能、物控的
                          # 在产型号判断都以产出表为唯一事实源，缺权限会让本职问题
                          # 永远落「表级权限不足」。
                          "ie_engineer", "material_control"},
    "line_stop_event": {"manufacturing", "coordinator", "quality", "gm"},
    # 2026-10-22：test_engineer 加入——「失败项集中在哪些」是 TE 本职，
    # 但不良明细只有 quality_defect 一张事实表。
    "quality_defect": {"quality", "manufacturing", "coordinator", "gm",
                       "test_engineer"},
    # V3.6 SPC 域：测量值表（spc.ingest 落库 / spc 取数），口径与 quality_defect 一致
    "spc_measurement": {"quality", "manufacturing", "coordinator", "gm"},
    # V4 看板口径直查（board_qa）：看板事实表补登记（此前仅看板 API 经 data_scope
    # 可见，Text2SQL 明细探查查这些表会被表级 ACL 拒绝）。角色口径对齐同类表。
    # 2026-10-22：设备岗/维保岗加入——稼动损失分析、点检异常是本职取数，
    # 而设备事实只有这两张表。
    "equipment_run_time": {"manufacturing", "planning", "coordinator", "quality", "gm",
                           "supply_chain_manager", "equipment", "equipment_manager",
                           "maintenance_tech"},
    "scrap_record": {"manufacturing", "planning", "coordinator", "quality", "gm",
                     "supply_chain_manager"},
    "work_report": {"manufacturing", "planning", "coordinator", "quality", "gm",
                    "supply_chain_manager", "ie_engineer", "material_control"},
    "std_standard_audit": {"quality", "manufacturing", "coordinator", "gm"},
    # 订单履约事实表（orders 看板）：角色口径对齐 shipment（业务/仓库/协同/销售/GM）
    # 2026-10-22：计划/采购/物控/IE 加入——齐套、插单影响、欠料清单必须读订单履约
    # 与在制工单；缺权限时这些岗位的推荐问题全部落「表级权限不足」。
    "order_fulfillment": {"business", "warehouse", "coordinator", "sales", "gm",
                          "supply_chain_manager", "planning", "supply_chain",
                          "procurement", "material_control", "ie_engineer"},
    # V3.3：material_control 加入——齐套检查/到货批次是物控核心场景
    "material_batch": {"procurement", "quality", "coordinator", "material_control", "gm",
                       "supply_chain_manager",
                       # 2026-10-22：仓储岗加入——超龄料清单/收料进度是仓储本职。
                       "warehouse", "warehouse_manager"},
    # R7（2026-10-22）：quality 加入——质量追溯三路并查的「设备状态」一路必须
    # 能读该批次产线的设备日志；设备台账的写操作仍只在设备域。
    # 2026-10-22：equipment / maintenance_tech 加入——设备告警与点检异常是本职，
    # 此前「设备岗查不到设备日志」是权限配置漏项（推荐问题必然不可答）。
    "equipment_log": {"manufacturing", "coordinator", "gm", "equipment_manager",
                      "quality", "equipment", "maintenance_tech"},
    # R5 真机（2026-10-22）：sales 加入——对客 ETA 的 ATP（在库−预留）是业务经理
    # 核心口径；缺此权限时交期草稿的现货数会静默算成 0（R5 真机实测）。
    "inventory": {"warehouse", "supply_chain", "coordinator", "gm", "sales",
                  # 2026-10-22：物控/采购/供应链管理加入——ATP 与缺口推算是其本职，
                  # 只给 material_inventory 不足以回答「这个料号库存和在途」。
                  "material_control", "procurement", "supply_chain_manager"},
    # V3.7 库存域（材料库存/成品库存/来料批次综合）· 数据速查数据源。
    # **按岗位隔离（fail-closed）**：仅仓储 / 物控 / 采购 / 供应链管理职能可读；
    # 其余岗位在「数据速查」页如实显示无权限，可走权限申请（services/data_access.py）
    # 经演示自动审批获得授权——授权写入本文件尾部的运行时叠加层，三个生效点统一读 table_acl()。
    "material_inventory": {"warehouse", "warehouse_manager", "material_control",
                           "supply_chain_manager", "procurement", "coordinator", "gm"},
    "finished_inventory": {"warehouse", "warehouse_manager", "material_control",
                           "supply_chain_manager", "coordinator", "gm"},
    "incoming_batch": {"warehouse", "warehouse_manager", "material_control",
                       "supply_chain_manager", "procurement", "quality",
                       "coordinator", "gm"},
    # sales=业务经理：对客 ETA / 出货风险 / 客诉响应核心场景
    "shipment": {"business", "warehouse", "coordinator", "sales", "gm",
                 "supply_chain_manager"},
    "customer_complaint": {"quality", "business", "coordinator", "sales", "gm",
                           # 2026-10-22：客服岗加入——客诉受理与对客回复是其本职。
                           "customer_service"},
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
    # 2026-10-22：NPI / 项目 / 测试 / 结构 / 产品工程岗位加入——里程碑与测试记录
    # 是这些岗位推荐问题的直接事实源（此前只给管理岗，本职角色反而查不到）。
    "project_milestone": {"manufacturing", "planning", "quality", "procurement",
                          "material_control", "sales", "warehouse",
                          "rd_engineering", "finance", "gm", "quality_director",
                          "qa_manager", "qa", "sqe", "pqe", "equipment", "coordinator",
                          "equipment_manager", "rd_manager", "supply_chain_manager",
                          "procurement_manager", "warehouse_manager", "finance_manager",
                          "npi_engineer", "project_manager", "test_engineer",
                          "rd_test_engineer", "ie_engineer", "me_engineer",
                          "product_engineering"},
    "test_records": {"quality", "rd_engineering", "manufacturing", "gm",
                     "quality_director", "qa_manager", "coordinator",
                     "npi_engineer", "project_manager", "test_engineer",
                     "rd_test_engineer", "ie_engineer", "me_engineer",
                     "product_engineering", "rd_manager"},
}


# ---------- 运行时授权叠加层（数据查询权限申请 · 演示自动审批） ----------
# 语义：数据权限是**表级 × 角色**的，没有逐行/逐用户过滤，因此「授权」按**角色**落
# （案例域的申请是按 user 落 ACL——那是案例有逐条 ACL 的结构差异，不是标准不同）。
# 演示自动审批（``services/data_access.py``）把获批角色写入本叠加层；三个权限生效点
# （``auditor`` / ``agents.office`` / ``demo.api``）统一通过 ``table_acl()`` 取值，
# 读到的即「静态白名单 ∪ 已授权角色」。进程内存储（重启复位，demo 可接受）；
# 测试用 ``reset_table_grants()`` 隔离。
_GRANT_OVERLAY: dict[str, set[str]] = {}


def table_acl(table: str) -> set[str]:
    """有效表级权限 = 静态白名单 ∪ 运行时授权叠加层（fail-closed 语义不变）。"""
    return set(TABLE_ACL.get(table, set())) | set(_GRANT_OVERLAY.get(table, set()))


def grant_table_access(table: str, roles: Iterable[str]) -> None:
    """把获批角色写入叠加层（唯一合法来源：``data_access.demo_approve`` 的审批通过路径）。"""
    granted = _GRANT_OVERLAY.setdefault(str(table), set())
    granted.update(str(r) for r in roles if r)


def revoke_table_access(table: str, roles: Iterable[str]) -> None:
    """回收授权（TTL 到期惰性回收；供 ``data_access.reap_expired`` 调用）。"""
    granted = _GRANT_OVERLAY.get(str(table))
    if not granted:
        return
    for role in roles:
        granted.discard(str(role))
    if not granted:
        _GRANT_OVERLAY.pop(str(table), None)


def reset_table_grants() -> None:
    """清空叠加层（测试隔离用）。"""
    _GRANT_OVERLAY.clear()


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
        # 2026-10-22：按**关键词长度加权**而不是命中个数。等个数时短词（"订单"）
        # 会顶掉更具体的长词查询（"履约风险"），导致「哪些订单有履约风险」被
        # 需要 :model 的 orders_by_model 空手命中。长词信息量更大，优先它。
        scored = [(sum(len(w) for w in spec.get("keywords", []) if w in question), name)
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
