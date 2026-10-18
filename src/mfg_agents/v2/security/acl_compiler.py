"""AclCompiler：主体 + 动作 + 策略 → ``FilterNode``（**唯一 FilterAST 生产点**）。

架构位置：§7.3（编译算法）、§7.5（执行顺序）、裁定 A10（禁止 LLM 生成 filter）。

**签名纪律（不可妥协，架构 §16.3 第 1 条）**：

```python
compile(principal, action, policy, env) -> FilterNode
```

参数里**没有** ``query_text``，也没有任何 LLM 输出位——从函数签名层面物理阻断
"让查询内容（或模型输出）影响权限过滤条件"这条路径。任何试图加 ``query`` 参数的改动
都应被打回。

五阶段（顺序不可调换）：

0. 动作门禁：``export`` / ``share`` 之类未启用的动作 → 短路 ``FALSE_FILTER``；
1. 显式 DENY（主体级，一票否决）→ 短路 ``FALSE_FILTER``；
2. RBAC：``case.<action>`` 不在角色矩阵内 → 短路 ``FALSE_FILTER``；
3. 硬维度：``tenant`` / ``is_knowledge_layer`` / ``status IN visible_status`` /
   ``cls_rank <= max_classification_rank``；
4. 软维度（快/慢路径）：``visibility_class == 0``（快路径）
   **或** ``array_contains_any(acl_tokens, principal_tokens)``（慢路径）；
5. 三重自检：跨维 OR / 字段白名单 / 值安全。

**关于快路径的诚实说明**：它**不是**语义放宽，而是"该主体不享受默认开放"的收紧
（非正式员工只拿全维无约束的案例）。真正的性能收益来自数据分布：绝大多数质量案例
``visibility_class=0``，慢路径的数组运算只落在少数受限案例上。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..contracts.case_acl import (
    ACTIONS,
    FILTERABLE_FIELDS,
    MAX_PRINCIPAL_TOKENS,
    RETRIEVAL_ACTIONS,
    FilterNode,
    FALSE_FILTER,
    Op,
    Principal,
    assert_fields_whitelisted,
    assert_no_cross_dimension_or,
    assert_values_safe,
    is_false_filter,
)
from .case_rbac import CaseRbacRegistry, get_case_rbac

logger = logging.getLogger(__name__)

__all__ = [
    "CasePolicyError",
    "PolicyRule",
    "ActionPolicy",
    "EnvContext",
    "CasePolicySet",
    "DEFAULT_CASE_POLICY_PATH",
    "AclCompiler",
    "get_acl_compiler",
    "reset_acl_compiler",
]

#: ``v2/data/case_policy.yaml``（本模块在 v2/security/ 下 → ../data/）
DEFAULT_CASE_POLICY_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "case_policy.yaml"
)


class CasePolicyError(Exception):
    """策略文件加载/校验失败（配置错误必须显式暴露，不静默降级放行）。"""


@dataclass(frozen=True)
class EnvContext:
    """环境上下文（P1 维度：访问窗口 / 通道）。MVP 只记录，不参与判定。"""

    channel: str = "http"
    now: str = ""
    expected_principal_version: Optional[int] = None


@dataclass(frozen=True)
class ActionPolicy:
    """单个动作的策略：可见状态白名单 + 是否启用 + 拒绝原因。"""

    action: str
    label: str = ""
    visible_status: tuple[str, ...] = ()
    enabled: bool = True
    deny_reason: str = ""


@dataclass(frozen=True)
class PolicyRule:
    """声明式策略规则（allow / deny 同构）。

    ``when`` 的键可以是 ``resource.<field>`` / ``action`` / ``subject.<field>``。
    编译器只能匹配 ``subject.*`` 与 ``action``（它看不到资源）；``resource.*``
    由 ``AclEvaluator`` 的二次授权路径执行。
    """

    id: str
    when: Mapping[str, Any] = field(default_factory=dict)
    effect: str = "deny"
    dimensions_exempt: tuple[str, ...] = ()
    reason: str = ""

    def matches_subject(self, principal: Optional[Principal],
                        action: Optional[str] = None) -> bool:
        """是否匹配"主体 + 动作"（不看资源）。"""
        for key, expected in self.when.items():
            values = _as_str_list(expected)
            if key == "action":
                if action is None or action not in values:
                    return False
                continue
            if not key.startswith("subject."):
                # 资源级条件无法在此判定 → 该规则不匹配主体阶段
                return False
            actual = getattr(principal, key.split(".", 1)[1], None) if principal else None
            text = str(actual or "").strip().lower()
            if text not in {v.lower() for v in values}:
                return False
        return True


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return [str(value)]


class CasePolicySet:
    """``case_policy.yaml`` 的只读视图（构造期自检）。"""

    def __init__(self,
                 actions: Mapping[str, ActionPolicy],
                 allow_rules: Sequence[PolicyRule] = (),
                 deny_rules: Sequence[PolicyRule] = (),
                 subject_deny_rules: Sequence[PolicyRule] = (),
                 settings: Optional[Mapping[str, Any]] = None) -> None:
        if not actions:
            raise CasePolicyError("案例策略缺少 actions 段")
        self._actions: dict[str, ActionPolicy] = {}
        for key, policy in actions.items():
            action = str(key or "").strip()
            if action not in ACTIONS:
                raise CasePolicyError(
                    f"策略声明了未知动作: {action!r}（合法动作：{list(ACTIONS)}）")
            self._actions[action] = policy
        missing = [a for a in ACTIONS if a not in self._actions]
        if missing:
            raise CasePolicyError(f"策略缺少动作定义: {missing}")
        self._allow_rules = tuple(allow_rules)
        self._deny_rules = tuple(deny_rules)
        self._subject_deny_rules = tuple(subject_deny_rules)
        self._settings: dict[str, Any] = dict(settings or {})
        for rule in (*self._allow_rules, *self._deny_rules, *self._subject_deny_rules):
            if rule.effect not in ("allow", "deny"):
                raise CasePolicyError(f"规则 {rule.id} 的 effect 非法: {rule.effect!r}")

    # ---------- 加载 ----------

    @classmethod
    def from_yaml(cls, path: Optional[str | Path] = None) -> "CasePolicySet":
        import yaml  # pyyaml 既有依赖

        target = Path(path) if path else DEFAULT_CASE_POLICY_PATH
        if not target.is_file():
            raise CasePolicyError(f"案例策略文件不存在: {target}")
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise CasePolicyError(f"案例策略解析失败: {target}（{exc}）") from exc
        if not isinstance(raw, dict):
            raise CasePolicyError(f"案例策略格式非法（应为 mapping）: {target}")

        actions: dict[str, ActionPolicy] = {}
        for key, item in (raw.get("actions") or {}).items():
            item = item if isinstance(item, dict) else {}
            actions[str(key)] = ActionPolicy(
                action=str(key),
                label=str(item.get("label", "")),
                visible_status=tuple(str(s) for s in (item.get("visible_status") or ())),
                enabled=bool(item.get("enabled", True)),
                deny_reason=str(item.get("deny_reason", "")),
            )

        def _rules(section: str) -> list[PolicyRule]:
            out: list[PolicyRule] = []
            for item in (raw.get(section) or []):
                if not isinstance(item, dict):
                    raise CasePolicyError(f"{section} 条目格式非法: {item!r}")
                rule_id = str(item.get("id", "")).strip()
                if not rule_id:
                    raise CasePolicyError(f"{section} 条目缺少 id")
                when = item.get("when") or {}
                if not isinstance(when, dict):
                    raise CasePolicyError(f"规则 {rule_id} 的 when 必须是 mapping")
                out.append(PolicyRule(
                    id=rule_id,
                    when=when,
                    effect=str(item.get("effect", "deny")).strip().lower(),
                    dimensions_exempt=tuple(
                        str(d) for d in (item.get("dimensions_exempt") or ())),
                    reason=str(item.get("reason", "")),
                ))
            return out

        policy = cls(
            actions=actions,
            allow_rules=_rules("allow_rules"),
            deny_rules=_rules("deny_rules"),
            subject_deny_rules=_rules("subject_deny_rules"),
            settings=raw.get("settings") or {},
        )
        logger.info("[case_policy] 案例策略加载完成: %s（%d 动作 / %d allow / %d deny）",
                    target, len(actions), len(policy._allow_rules), len(policy._deny_rules))
        return policy

    # ---------- 查询 ----------

    def action_policy(self, action: Any) -> ActionPolicy:
        key = str(action or "").strip()
        policy = self._actions.get(key)
        if policy is None:
            raise CasePolicyError(f"未知案例动作: {key!r}")
        return policy

    def visible_status(self, action: Any) -> tuple[str, ...]:
        """该动作允许的案例状态（BR-06）。"""
        return self.action_policy(action).visible_status

    def actions(self) -> tuple[str, ...]:
        return tuple(self._actions)

    def is_action_enabled(self, action: Any, principal: Optional[Principal] = None
                          ) -> bool:
        """动作门禁（阶段 0）：是否允许进入检索。

        ``principal`` 参与判定是为了给未来的"按主体类型放开"留位置；
        当前实现对 ``enabled: false`` 的动作一律拒绝（export/share/ingest）。
        """
        _ = principal  # 保留位：未来按 employment_type / clearance 放开导出
        policy = self._actions.get(str(action or "").strip())
        return bool(policy and policy.enabled)

    def deny_reason(self, action: Any) -> str:
        policy = self._actions.get(str(action or "").strip())
        return policy.deny_reason if policy else "未知动作"

    def rule_by_id(self, rule_id: str) -> Optional[PolicyRule]:
        for rule in (*self._allow_rules, *self._deny_rules, *self._subject_deny_rules):
            if rule.id == rule_id:
                return rule
        return None

    def subject_deny_match(self, principal: Optional[Principal],
                           action: Optional[str] = None) -> Optional[PolicyRule]:
        """阶段 1：主体级显式 DENY（命中即一票否决）。"""
        for rule in self._subject_deny_rules:
            if rule.effect == "deny" and rule.matches_subject(principal, action):
                return rule
        return None

    def resource_deny_match(self, scalars: Mapping[str, Any],
                            action: Optional[str] = None) -> Optional[PolicyRule]:
        """资源级显式 DENY（供 ``AclEvaluator`` 的独立路径执行）。"""
        for rule in self._deny_rules:
            if rule.effect != "deny":
                continue
            matched = True
            for key, expected in rule.when.items():
                values = _as_str_list(expected)
                if key == "action":
                    if action is None or action not in values:
                        matched = False
                        break
                    continue
                if not key.startswith("resource."):
                    matched = False
                    break
                actual = scalars.get(key.split(".", 1)[1])
                if str(actual) not in values:
                    matched = False
                    break
            if matched:
                return rule
        return None

    def default_open_rule(self, principal: Optional[Principal],
                          action: Optional[str] = None) -> Optional[PolicyRule]:
        """命中的"默认开放"allow 规则（裁定 A6 的精确位置）。"""
        for rule in self._allow_rules:
            if rule.effect != "allow":
                continue
            if rule.matches_subject(principal, action):
                return rule
        return None

    def fast_path_only(self, principal: Optional[Principal]) -> bool:
        """是否只走 ``visibility_class == 0`` 快路径。

        依据 ``settings.fast_path_employment_types``：只有正式员工享受"质量案例
        全员共享"默认值；非正式员工（外包/临时/供应商/客户账号）不参与默认开放，
        因此拿不到任何带 dept/plant 约束的案例。**这是收紧，不是放宽。**
        """
        allowed = {str(v).lower() for v in
                   (self._settings.get("fast_path_employment_types") or ["employee"])}
        etype = str(getattr(principal, "employment_type", "") or "").lower()
        return etype not in allowed

    def setting(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def settings(self) -> Mapping[str, Any]:
        return dict(self._settings)

    # ---------- 展示 ----------

    def to_meta(self) -> dict[str, Any]:
        """``/api/v2/cases/meta`` 用：动作矩阵 + 显式 deny 清单。"""
        return {
            "actions": [
                {
                    "key": key,
                    "label": policy.label,
                    "enabled": policy.enabled,
                    "visible_status": list(policy.visible_status),
                    "deny_reason": policy.deny_reason,
                }
                for key, policy in sorted(self._actions.items())
            ],
            "deny_rules": [
                {"id": r.id, "when": dict(r.when), "reason": r.reason}
                for r in (*self._deny_rules, *self._subject_deny_rules)
            ],
            "allow_rules": [
                {"id": r.id, "when": dict(r.when),
                 "dimensions_exempt": list(r.dimensions_exempt), "reason": r.reason}
                for r in self._allow_rules
            ],
            "settings": dict(self._settings),
        }


class AclCompiler:
    """主体 → ``FilterNode``（**唯一**生产点；签名不含 query_text / LLM 输出）。"""

    def __init__(self, policy: Optional[CasePolicySet] = None,
                 rbac: Optional[CaseRbacRegistry] = None) -> None:
        self._policy = policy or CasePolicySet.from_yaml()
        self._rbac = rbac or get_case_rbac()

    @property
    def policy(self) -> CasePolicySet:
        return self._policy

    # ---------- 主入口 ----------

    def compile(self, principal: Optional[Principal], action: str,
                policy: Optional[CasePolicySet] = None,
                env: Optional[EnvContext] = None) -> FilterNode:
        """编译为 ``FilterNode``（拒绝时返回显式 ``FALSE_FILTER``，绝不返回"无过滤"）。"""
        node, _reason = self.compile_explained(principal, action, policy=policy, env=env)
        return node

    def compile_explained(self, principal: Optional[Principal], action: str,
                          policy: Optional[CasePolicySet] = None,
                          env: Optional[EnvContext] = None
                          ) -> tuple[FilterNode, str]:
        """同 :meth:`compile`，额外返回拒绝原因（供审计 / notice / 单测断言）。

        **注意**：``action`` 之外没有任何"查询内容"入参——这是刻意设计。
        """
        active_policy = policy or self._policy
        act = str(action or "").strip()
        if act not in ACTIONS:
            raise CasePolicyError(f"未知案例动作: {act!r}（合法动作：{list(ACTIONS)}）")

        # ── 阶段 0：动作门禁（不进检索）──────────────────────────────
        if act not in RETRIEVAL_ACTIONS:
            return FALSE_FILTER, f"动作 {act} 不是检索动作，不进检索"
        if not active_policy.is_action_enabled(act, principal):
            return FALSE_FILTER, active_policy.deny_reason(act) or f"动作 {act} 未启用"

        # ── 阶段 1：显式 DENY（主休级，一票否决）─────────────────────
        deny_rule = active_policy.subject_deny_match(principal, act)
        if deny_rule is not None:
            return FALSE_FILTER, f"显式拒绝规则命中 {deny_rule.id}：{deny_rule.reason}"

        # ── 阶段 0.5：主体可用性（fail-closed，PR-1）────────────────
        if principal is None or not principal.user_id:
            return FALSE_FILTER, "无身份主体"
        if not principal.tenant:
            return FALSE_FILTER, "主体缺少租户维度"
        if env is not None and env.expected_principal_version is not None:
            if int(principal.principal_version) != int(env.expected_principal_version):
                return FALSE_FILTER, "主体撤权版本号不匹配（pv）"

        # ── 阶段 2：RBAC（与 ABAC 取交集）───────────────────────────
        if not self._rbac.has_any(principal.roles, f"case.{act}"):
            return FALSE_FILTER, f"角色集合 {list(principal.roles)} 不持有 case.{act}"

        # ── 阶段 3：硬维度（不随案例变化）───────────────────────────
        hard: list[FilterNode] = [
            FilterNode(Op.EQ, "tenant", principal.tenant),
            FilterNode(Op.EQ, "is_knowledge_layer", True),   # BR-08 硬断言
            FilterNode(Op.IN, "status",
                       list(active_policy.visible_status(act))),  # BR-06
            FilterNode(Op.LTE, "cls_rank", int(principal.max_classification_rank)),
        ]
        if not active_policy.visible_status(act):
            return FALSE_FILTER, f"动作 {act} 无可见状态白名单"

        # ── 阶段 4：软维度（快路径 / 慢路径）───────────────────────
        if active_policy.fast_path_only(principal):
            soft = FilterNode(Op.EQ, "visibility_class", 0)
            path = "fast"
        else:
            tokens = list(principal.principal_tokens)[:MAX_PRINCIPAL_TOKENS]
            soft = FilterNode(Op.ANY, children=(
                FilterNode(Op.EQ, "visibility_class", 0),
                FilterNode(Op.ARRAY_ANY, "acl_tokens", tokens),
            ))
            path = "fast+slow"

        node = FilterNode(Op.ALL, children=tuple([*hard, soft]))

        # ── 阶段 5：三重自检 ───────────────────────────────────────
        assert_no_cross_dimension_or(node)
        assert_fields_whitelisted(node)
        assert_values_safe(node)
        if is_false_filter(node):  # pragma: no cover - 防御：编译结果不得是永假
            raise CasePolicyError("编译结果意外为永假过滤器（内部缺陷）")

        reason = (f"action={act} path={path} "
                  f"status={list(active_policy.visible_status(act))} "
                  f"cls<= {principal.max_classification_rank} "
                  f"tokens={len(principal.principal_tokens)}")
        return node, reason

    # ---------- 供 hidden_count 使用的"仅硬维度"过滤器 ----------

    def compile_hard_only(self, principal: Principal, action: str,
                          policy: Optional[CasePolicySet] = None) -> FilterNode:
        """只含安全硬维度的过滤器（tenant / layer / status / cls）。

        用途**仅限** ``hidden_count`` 计数查询（架构 §7.6）：它已经排除涉密、
        未结案、证据层、跨租户内容，因此只泄漏"存在 N 条你因部门/工厂/客户范围
        看不到的案例"这一个整数，不泄漏任何受保护内容。
        """
        active_policy = policy or self._policy
        statuses = list(active_policy.visible_status(action))
        if not statuses:
            return FALSE_FILTER
        return FilterNode(Op.ALL, children=(
            FilterNode(Op.EQ, "tenant", principal.tenant),
            FilterNode(Op.EQ, "is_knowledge_layer", True),
            FilterNode(Op.IN, "status", statuses),
            FilterNode(Op.LTE, "cls_rank", int(principal.max_classification_rank)),
        ))


_COMPILER: Optional[AclCompiler] = None


def get_acl_compiler() -> AclCompiler:
    """编译器单例（首次调用时加载策略与角色表；失败即抛错，不降级）。"""
    global _COMPILER
    if _COMPILER is None:
        _COMPILER = AclCompiler(policy=CasePolicySet.from_yaml(),
                                rbac=get_case_rbac())
    return _COMPILER


def reset_acl_compiler() -> None:
    """清空单例（测试注入自定义策略用）。"""
    global _COMPILER
    _COMPILER = None
