"""AclEvaluator：返回结果前的**二次授权**（BR-01 后半，独立代码路径）。

为什么必须有第二套代码（架构 R-A4）：编译期与二次校验若共用同一实现，一个 filter
翻译 bug 就会让"前置过滤"和"二次兜底"同时失效——那等于没有兜底。因此：

| | 输入 | 算法 |
|---|---|---|
| ``AclCompiler`` | 主体 + 动作 + 策略 | 递归遍历 **AST**，产出过滤表达式 |
| ``AclEvaluator`` | 主体 + **关系库权威 ACL 行** + 动作 | 行集**集合运算**，完全不碰 AST |

它直接读关系库的 ``case_acl`` 行（不读向量库 metadata）——向量索引是可重建的派生
数据，关系库才是真相源。即使索引元数据过期或被伪造，这里也不会漏放。

行格式（``case_acl_rows`` 的每一项）::

    {"case_id": "...", "dim": "dept", "value": "quality", "effect": "allow"}

除业务 ACL 条目外，调用方还须把案例的标量属性以"系统行"形式传入：
``dim`` ∈ ``{tenant, status, cls_rank, layer, visibility_class}``，``effect="system"``。
这样评估器只需要一种数据结构（行），不需要认识 ``Case`` 类型——这正是"独立代码路径"
的工程形态。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..contracts.case_acl import (
    ACTIONS,
    TOKEN_DIMS,
    Principal,
    principal_token,
)
from .acl_compiler import CasePolicySet, EnvContext, get_acl_compiler
from .case_rbac import CaseRbacRegistry, get_case_rbac

logger = logging.getLogger(__name__)

__all__ = ["AuthDecision", "AclEvaluator", "SYSTEM_DIMS", "get_acl_evaluator"]

#: 系统行维度（由案例标量属性合成，不是业务 ACL 授权条目）。
SYSTEM_DIMS: tuple[str, ...] = (
    "tenant", "status", "cls_rank", "layer", "visibility_class",
)

#: 业务 ACL 维度 → 主体令牌前缀（与 ``TOKEN_DIMS`` 一致）。
_DIM_TO_TOKEN = {
    "dept": "dept",
    "plant": "plant",
    "role": "role",
    "line": "line",
    "prj": "prj",
    "cus": "cus",
    "sup": "sup",
    "pf": "pf",
    "etype": "etype",
    # ★ 跨部门访问申请的授权维度（审批通过后写 case_acl dim=user）。
    # 加进映射后，授权对编译器（ARRAY_ANY on acl_tokens）与二次授权（这里）
    # 同时生效——**不需要第二套判定**。
    "user": "user",
}


@dataclass(frozen=True)
class AuthDecision:
    """二次授权结论。

    Attributes:
        allow: 是否放行。
        reason: 拒绝原因（可读，写审计用；不含案例正文）。
        matched: 命中的授权条目（``dim:value``），便于审计复现。
        rule_id: 命中的显式拒绝规则 id（如有）。
    """

    allow: bool
    reason: str = ""
    matched: tuple[str, ...] = ()
    rule_id: str = ""

    def __bool__(self) -> bool:  # 便于 `if decision:`
        return self.allow


class AclEvaluator:
    """对关系库权威 ACL 行求值（不使用 AST，不读向量 metadata）。"""

    def __init__(self, rbac: Optional[CaseRbacRegistry] = None,
                 policy: Optional[CasePolicySet] = None) -> None:
        self._rbac = rbac or get_case_rbac()
        self._policy = policy or get_acl_compiler().policy

    # ---------- 主入口 ----------

    def authorize(self, principal: Optional[Principal],
                  case_acl_rows: Optional[Sequence[Mapping[str, Any]]],
                  action: str,
                  env: Optional[EnvContext] = None) -> AuthDecision:
        """对一条案例的权威 ACL 行求值。

        Args:
            principal: 服务端权威主体。
            case_acl_rows: 该案例的 ACL 行 + 系统标量行（见模块 docstring）。
            action: 六个检索动词之一。
            env: 环境上下文（含 ``pv`` 期望值）。

        Returns:
            ``AuthDecision``；任何不确定情况一律 ``allow=False``（fail-closed）。
        """
        act = str(action or "").strip()
        if act not in ACTIONS:
            return AuthDecision(False, f"未知动作 {act!r}")

        # ── 1. 主体可用性 ─────────────────────────────────────────
        if principal is None or not principal.user_id:
            return AuthDecision(False, "无身份主体（user_id 为空）", rule_id="deny-anonymous-principal")
        if env is not None and env.expected_principal_version is not None:
            if int(principal.principal_version) != int(env.expected_principal_version):
                return AuthDecision(False, "主体撤权版本号不匹配（pv）",
                                    rule_id="principal-revoked")

        rows = [r for r in (case_acl_rows or ()) if isinstance(r, Mapping)]
        if not rows:
            return AuthDecision(False, "案例缺少权威 ACL 行（fail-closed）")

        scalars: dict[str, str] = {}
        allow_dims: dict[str, list[str]] = defaultdict(list)
        deny_dims: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            dim = str(row.get("dim") or "").strip()
            value = str(row.get("value") or "").strip()
            effect = str(row.get("effect") or "allow").strip().lower()
            if not dim:
                continue
            if dim in SYSTEM_DIMS or effect == "system":
                scalars[dim] = value
                continue
            if effect == "deny":
                deny_dims[dim].append(value)
            else:
                allow_dims[dim].append(value)

        # ── 2. 主体级显式拒绝 ────────────────────────────────────
        subject_rule = self._policy.subject_deny_match(principal, act)
        if subject_rule is not None:
            return AuthDecision(False, f"显式拒绝 {subject_rule.id}：{subject_rule.reason}",
                                rule_id=subject_rule.id)

        # ── 3. 资源级显式拒绝（声明式，与编译器同源但不同执行路径）──
        resource_rule = self._policy.resource_deny_match(scalars, act)
        if resource_rule is not None:
            return AuthDecision(
                False, f"显式拒绝 {resource_rule.id}：{resource_rule.reason}",
                rule_id=resource_rule.id)

        # ── 4. RBAC（与 ABAC 取交集）────────────────────────────
        if not self._rbac.has_any(principal.roles, f"case.{act}"):
            return AuthDecision(False, f"角色不持有 case.{act}")

        # ── 5. 全局硬门禁：知识层 ───────────────────────────────
        layer = scalars.get("layer", "")
        if layer != "knowledge":
            return AuthDecision(False, f"非知识层内容（layer={layer or '未标注'}）",
                                rule_id="deny-evidence-layer")

        # ── 6. 租户维（AND）────────────────────────────────────
        if scalars.get("tenant") != principal.tenant:
            return AuthDecision(False, "跨租户案例")

        # ── 7. 状态维（BR-06）──────────────────────────────────
        visible = self._policy.visible_status(act)
        status = scalars.get("status", "")
        if status not in visible:
            return AuthDecision(False, f"状态 {status or '未标注'} 不在可见白名单内")

        # ── 8. 密级维（Lte）────────────────────────────────────
        try:
            cls_rank = int(scalars.get("cls_rank", "99"))
        except (TypeError, ValueError):
            return AuthDecision(False, "密级字段非法（fail-closed）")
        if cls_rank > int(principal.max_classification_rank):
            return AuthDecision(False, f"密级 {cls_rank} 超过主体上限")

        # ── 9. 显式 deny 授权条目（比 allow 优先）────────────────
        for dim, values in deny_dims.items():
            prefix = _DIM_TO_TOKEN.get(dim)
            if not prefix:
                continue  # 未知维度按"无约束"忽略（它不会放宽任何东西）
            for value in values:
                if principal_token(prefix, value) in principal.principal_tokens:
                    return AuthDecision(False, f"命中显式拒绝条目 {dim}:{value}")

        # ── 9.5 演示模式：检索/预览业务维豁免（硬门禁与显式拒绝已在上方执行）──
        # 与 acl_compiler 阶段 4 的 demo-open 同一语义（两套实现必须同步改，
        # 否则召回放行、二次授权整批丢弃）。只放宽"业务范围"，不动任何安全约束。
        if self._demo_open(act, principal):
            return AuthDecision(True, "演示模式：检索/预览业务维豁免（硬门禁照常）")

        # ── 10. 可见性维（快/慢路径，与编译器语义等价但算法独立）────
        try:
            vc = int(scalars.get("visibility_class", "1"))
        except (TypeError, ValueError):
            return AuthDecision(False, "visibility_class 非法（fail-closed）")
        if vc == 0:
            return AuthDecision(True, "可见性快路径（vc=0，全维无约束）")

        # ── 10.5 跨部门通行角色（与编译器 cross-dept 路径同一语义）────
        # 总经理等通行角色免做"令牌交集"匹配（role/dept/plant 等业务维全通）；
        # 上面的显式 deny（步骤 9）与硬门禁（步骤 5-8）已经独立执行过——
        # 豁免只放宽"业务范围"，不放宽任何安全约束。改这里必须同步改
        # acl_compiler 的阶段 4，否则检索召回会被二次授权整批丢弃。
        if self._policy.cross_dept_pass(principal):
            return AuthDecision(True, "跨部门通行角色（业务维豁免，硬门禁照常）")

        matched: list[str] = []
        for dim, values in allow_dims.items():
            prefix = _DIM_TO_TOKEN.get(dim)
            if not prefix:
                continue
            for value in values:
                if principal_token(prefix, value) in principal.principal_tokens:
                    matched.append(f"{dim}:{value}")
        if matched:
            return AuthDecision(True, "令牌交集命中", matched=tuple(sorted(set(matched))))
        return AuthDecision(False, "受限案例：主体令牌与案例 ACL 无交集")

    @staticmethod
    def _demo_open(act: str, principal) -> bool:
        """演示模式：检索/预览对正式员工放行业务维（与编译器 demo-open 同步）。

        只放宽"业务范围"（部门/工厂/产线/客户/供应商/项目），
        硬门禁（租户/密级/状态/知识层）与显式拒绝已在调用点上方独立执行。
        """
        if act not in ("query", "preview"):
            return False
        from ...common.config import settings

        if not bool(getattr(settings, "CASE_DEMO_OPEN_QUERY", False)):
            return False
        return str(getattr(principal, "employment_type", "employee")) == "employee"

    # ---------- 批量 ----------
    def filter_authorized(self, principal: Optional[Principal],
                          candidate_rows: Mapping[str, Sequence[Mapping[str, Any]]],
                          action: str,
                          env: Optional[EnvContext] = None
                          ) -> tuple[list[str], list[str]]:
        """批量二次授权：返回 ``(通过 case_id 列表, 被丢弃 case_id 列表)``。"""
        passed: list[str] = []
        dropped: list[str] = []
        for case_id, rows in (candidate_rows or {}).items():
            decision = self.authorize(principal, rows, action, env)
            (passed if decision.allow else dropped).append(str(case_id))
        return passed, dropped


_EVALUATOR: Optional[AclEvaluator] = None


def get_acl_evaluator() -> AclEvaluator:
    """评估器单例。"""
    global _EVALUATOR
    if _EVALUATOR is None:
        _EVALUATOR = AclEvaluator(rbac=get_case_rbac(),
                                  policy=get_acl_compiler().policy)
    return _EVALUATOR


def reset_acl_evaluator() -> None:
    """清空单例（测试用）。"""
    global _EVALUATOR
    _EVALUATOR = None
