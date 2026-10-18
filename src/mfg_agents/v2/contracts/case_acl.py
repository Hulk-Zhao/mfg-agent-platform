"""案例域 ACL 契约：``Principal`` / ``FilterNode`` / 过滤字段白名单 / 异常族。

架构位置：``docs/case-assistant-ontology-vector-auth-architecture.md`` §4.5（主体）、
§7.1（Filter DSL）、§7.4（防注入）。

三条不可妥协的约定（本模块是它们的唯一载体）：

1. **跨维度只允许 AND**。``Op.ANY``（OR）**只**允许出现在"同一维度的多值展开"，
   编译器/翻译器都会校验；混维度 ``ANY`` 一律 ``raise MixedDimensionOrError``。
   这是"OR 语义越权"最常见的实现错误，在契约层物理阻断。
2. **``FALSE_FILTER`` 是显式永假节点**，不是"没有过滤"。翻译器必须把它翻成后端
   的恒假表达式（Milvus ``1 == 0`` / Chroma ``{"chunk_id": {"$in": []}}`` /
   内存谓词恒 ``False``）。**绝不允许**把它翻成空 filter——空 filter = 全放行 = 越权。
3. **``principal_tokens`` 由构造期派生**，外部传入的值会被忽略。它决定"你能看哪些"，
   因此不允许调用方自行拼装。

本模块零第三方依赖（仅标准库），Python 3.11 兼容。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    "ACTIONS",
    "RETRIEVAL_ACTIONS",
    "CASE_PERMISSION_PREFIX",
    "FILTERABLE_FIELDS",
    "VISIBILITY_FIELDS",
    "TOKEN_DIMS",
    "MAX_PRINCIPAL_TOKENS",
    "MAX_FILTER_VALUE_LEN",
    "SAFE_FILTER_VALUE_RE",
    "DANGEROUS_CHARS",
    "Op",
    "FilterNode",
    "FALSE_FILTER",
    "Principal",
    "CaseAuthError",
    "MixedDimensionOrError",
    "UnknownFilterField",
    "UnsafeFilterValue",
    "EmptyFilterNotAllowed",
    "KnowledgeLayerViolation",
    "is_false_filter",
    "validate_filter_node",
    "assert_fields_whitelisted",
    "assert_values_safe",
    "assert_no_cross_dimension_or",
    "principal_token",
    "derive_principal_tokens",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 六个检索动词 + 入库动作。前六个取自 PRD §7.5 / 架构 §4.7；
#: ``ingest`` 不是检索动作（编译期会短路为 FALSE_FILTER），只为 RBAC 复用同一命名空间。
ACTIONS: tuple[str, ...] = (
    "query", "preview", "read", "cite", "export", "share", "ingest",
)

#: 真正会进检索的动作（``share`` 恒定拒绝，仍在此列以便记录审计原因）。
RETRIEVAL_ACTIONS: tuple[str, ...] = (
    "query", "preview", "read", "cite", "export", "share",
)

#: RBAC 权限 key 前缀：``case.<action>``。
CASE_PERMISSION_PREFIX = "case."

#: 向量库可过滤字段白名单（架构 §7.1）。字段名只能来自这里，其余一律 ``raise``。
FILTERABLE_FIELDS: frozenset[str] = frozenset({
    "tenant", "category", "subcategory", "status", "cls_rank",
    "visibility_class", "is_knowledge_layer", "owner_dept", "plant",
    "defect_mode_code", "root_cause_code", "content_version",
    "published_at", "last_verified_at", "verified", "acl_tokens",
})

#: 视为"同一个可见性维度"的字段对：只有这两者可组 ``ANY``（裁定 A3 的快/慢路径）。
VISIBILITY_FIELDS: frozenset[str] = frozenset({"visibility_class", "acl_tokens"})

#: ``acl_tokens`` 里允许出现的维度前缀（与 `derive_principal_tokens` 顺序一致）。
TOKEN_DIMS: tuple[str, ...] = (
    "role", "dept", "plant", "line", "prj", "cus", "sup", "pf", "etype", "user",
)

#: 主体令牌上限（模板/表达式膨胀保护）。超限**截断**（截断只会变严，不会放宽）。
MAX_PRINCIPAL_TOKENS = 64

#: 过滤值长度上限。
MAX_FILTER_VALUE_LEN = 64

#: 过滤值安全正则（架构 §7.4 原文）：字母/数字/``_-. :@`` 与中文，长度 1..64。
SAFE_FILTER_VALUE_RE = re.compile(
    r"^[A-Za-z0-9_\-\.:@\u4e00-\u9fa5]{1,64}$")

#: 显式拒绝的危险字符（双引号/单引号/反斜杠/分号/括号/尖括号/比较符/逻辑符/通配符/NUL）。
DANGEROUS_CHARS: frozenset[str] = frozenset(
    {'"', "'", "\\", ";", "(", ")", "{", "}", "[", "]", "<", ">", "=",
     "!", "&", "|", "%", "*", "\x00"})


# ---------------------------------------------------------------------------
# 异常族
# ---------------------------------------------------------------------------

class CaseAuthError(Exception):
    """案例域鉴权/过滤契约异常基类（一律 fail-closed）。"""


class MixedDimensionOrError(CaseAuthError):
    """``ANY`` 节点出现跨维度子节点（OR 语义越权），编译期即拒绝。"""


class UnknownFilterField(CaseAuthError):
    """过滤字段不在 ``FILTERABLE_FIELDS`` 白名单内。"""


class UnsafeFilterValue(CaseAuthError):
    """过滤值含危险字符 / 超长 / 类型不支持（注入防护）。"""


class EmptyFilterNotAllowed(CaseAuthError):
    """空过滤器被交给翻译器：空 filter = 全放行 = 越权，必须显式拒绝。"""


class KnowledgeLayerViolation(CaseAuthError):
    """证据层内容试图进入知识层索引（BR-08 硬门禁）。"""


# ---------------------------------------------------------------------------
# Filter DSL
# ---------------------------------------------------------------------------

class Op(str, Enum):
    """过滤算子。``ANY`` 只允许同维多值展开；跨维只允许 ``ALL``。"""

    ALL = "all"
    ANY = "any"
    EQ = "eq"
    IN = "in"
    LTE = "lte"
    GTE = "gte"
    ARRAY_ANY = "array_any"


@dataclass(frozen=True)
class FilterNode:
    """不可变过滤 AST 节点。

    Attributes:
        op: 算子。
        field: 字段名（叶子节点必填，必须在 ``FILTERABLE_FIELDS`` 内）。
        value: 比较值（``IN`` / ``ARRAY_ANY`` 为序列，其余为标量）。
        children: 子节点（仅 ``ALL`` / ``ANY`` 使用）。
    """

    op: Op
    field: str = ""
    value: Any = None
    children: tuple["FilterNode", ...] = ()

    def __post_init__(self) -> None:
        # 允许传字符串算子（便于测试与 YAML 反序列化），统一归一为 Op。
        if not isinstance(self.op, Op):
            try:
                object.__setattr__(self, "op", Op(str(self.op)))
            except ValueError as exc:  # pragma: no cover - 由 validate 覆盖
                raise CaseAuthError(f"非法过滤算子: {self.op!r}") from exc
        if self.children is None:
            object.__setattr__(self, "children", ())
        elif not isinstance(self.children, tuple):
            object.__setattr__(self, "children", tuple(self.children))

    def describe(self) -> str:
        """紧凑可读描述（仅用于日志/报错，**不可**用于构造后端表达式）。"""
        if self.op in (Op.ALL, Op.ANY):
            return f"{self.op.value}({', '.join(c.describe() for c in self.children)})"
        return f"{self.op.value}({self.field}={self.value!r})"


#: 显式永假节点：``ALL`` 且无子节点（架构 §7.1 的特殊约定）。
FALSE_FILTER = FilterNode(Op.ALL, children=())


def is_false_filter(node: Optional[FilterNode]) -> bool:
    """是否为显式永假节点（``ALL`` 且无子节点）。"""
    return (
        isinstance(node, FilterNode)
        and node.op is Op.ALL
        and not node.children
        and not node.field
    )


def principal_token(dim: str, value: str) -> str:
    """维度令牌：``dept:quality`` / ``plant:P1`` / ``etype:employee``。"""
    return f"{dim}:{value}"


def derive_principal_tokens(principal: "Principal") -> tuple[str, ...]:
    """按 `TOKEN_DIMS` 顺序派生主体令牌（确定性，服务端唯一）。

    超过 ``MAX_PRINCIPAL_TOKENS`` 时按维度优先级截断（截断只能变严：
    案例若依赖被截掉的令牌，则匹配不到 → 不可见 → fail-closed）。
    """
    raw: list[str] = []
    raw.extend(principal_token("role", r) for r in principal.roles)
    if principal.dept:
        raw.append(principal_token("dept", principal.dept))
    if principal.plant:
        raw.append(principal_token("plant", principal.plant))
    raw.extend(principal_token("line", v) for v in principal.line_ids)
    raw.extend(principal_token("prj", v) for v in principal.project_ids)
    raw.extend(principal_token("cus", v) for v in principal.customer_ids)
    raw.extend(principal_token("sup", v) for v in principal.supplier_ids)
    raw.extend(principal_token("pf", v) for v in principal.product_families)
    if principal.employment_type:
        raw.append(principal_token("etype", principal.employment_type))
    # ★ 跨部门访问申请的授权落在 user 维度（审批通过后写一行 case_acl dim=user）。
    # 放在最后：它是"特批"，不是身份固有属性，截断时也该最后被砍掉。
    if principal.user_id:
        raw.append(principal_token("user", principal.user_id))

    seen: set[str] = set()
    out: list[str] = []
    for token in raw:
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out[:MAX_PRINCIPAL_TOKENS])


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Principal:
    """服务端权威主体（架构 §4.5）。

    载荷里的每个维度都参与"多维 AND 交集"判定；**缺失按"空 = 无授权"处理**
    （架构 §4.6）：``plant=""`` → 令牌里没有 ``plant:*`` → 依赖工厂维的案例不可见。

    Attributes:
        user_id: 用户标识（空 → 无身份 → 一切拒绝）。
        tenant: 租户/法人（维度 1，其他维度的根）。
        dept: 部门编码。
        roles: 治理角色 key 元组（已经 ``PermissionRegistry`` 归一化）。
        plant: 工厂/园区编码。
        line_ids / project_ids / customer_ids / supplier_ids / product_families:
            P1 维度，MVP 期可为空。
        max_classification_rank: 最大密级 0..3。
        employment_type: employee | outsource | temp | supplier | customer。
        token_version: 令牌 schema 版本（``tv``，当前 = 2）。
        principal_version: 撤权版本号（``pv``）。
        principal_tokens: **派生字段**，构造期计算，外部传入被忽略。
    """

    user_id: str = ""
    tenant: str = ""
    dept: str = ""
    roles: tuple[str, ...] = ()
    plant: str = ""
    line_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    customer_ids: tuple[str, ...] = ()
    supplier_ids: tuple[str, ...] = ()
    product_families: tuple[str, ...] = ()
    max_classification_rank: int = 0
    employment_type: str = "employee"
    token_version: int = 2
    principal_version: int = 1
    # 派生（构造时计算，不可外部传入）
    principal_tokens: tuple[str, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        for name in ("user_id", "tenant", "dept", "plant", "employment_type"):
            object.__setattr__(self, name, str(getattr(self, name) or "").strip())
        for name in ("roles", "line_ids", "project_ids", "customer_ids",
                     "supplier_ids", "product_families"):
            raw = getattr(self, name) or ()
            if isinstance(raw, str):
                raw = (raw,)
            cleaned: list[str] = []
            for item in raw:
                text = str(item or "").strip()
                if text and text not in cleaned:
                    cleaned.append(text)
            object.__setattr__(self, name, tuple(cleaned))
        try:
            rank = int(self.max_classification_rank)
        except (TypeError, ValueError):
            rank = 0  # 非法值 → 最严格解释（仅 public）
        object.__setattr__(self, "max_classification_rank", max(0, min(3, rank)))
        try:
            object.__setattr__(self, "token_version", int(self.token_version))
        except (TypeError, ValueError):
            object.__setattr__(self, "token_version", 0)
        try:
            object.__setattr__(self, "principal_version", int(self.principal_version))
        except (TypeError, ValueError):
            object.__setattr__(self, "principal_version", 0)
        # 派生令牌永远覆盖外部传入值
        object.__setattr__(self, "principal_tokens", derive_principal_tokens(self))

    @property
    def is_identified(self) -> bool:
        """是否具备可用身份（``user_id`` 与 ``tenant`` 都非空）。"""
        return bool(self.user_id and self.tenant)

    def has_token(self, dim: str, value: str) -> bool:
        """主体是否持有某维度令牌。"""
        return principal_token(dim, value) in self.principal_tokens


# ---------------------------------------------------------------------------
# AST 校验（编译器阶段 5 的三重自检；翻译器复用）
# ---------------------------------------------------------------------------

def _check_leaf_value(op: Op, value: Any, field_name: str) -> None:
    """叶子节点值校验（注入防护）。"""
    if op in (Op.IN, Op.ARRAY_ANY):
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise UnsafeFilterValue(
                f"字段 {field_name} 的 {op.value} 值必须是序列，收到 {type(value).__name__}")
        values = list(value)
        if not values:
            # 空序列 = 永假语义，但对 ARRAY_ANY 一律显式拒绝：
            # 让调用方用 FALSE_FILTER 表达"永假"，避免"空值"被误当成"无约束"。
            raise EmptyFilterNotAllowed(
                f"字段 {field_name} 的 {op.value} 值为空（永假语义请显式用 FALSE_FILTER）")
        if op is Op.ARRAY_ANY and len(values) > MAX_PRINCIPAL_TOKENS:
            raise UnsafeFilterValue(
                f"acl_tokens 令牌数 {len(values)} 超过上限 {MAX_PRINCIPAL_TOKENS}")
        for item in values:
            _check_scalar(item, field_name)
        return
    _check_scalar(value, field_name)


def _check_scalar(value: Any, field_name: str) -> None:
    """标量值校验：只允许 str / int / bool，且字符串必须匹配安全正则。"""
    if isinstance(value, bool) or isinstance(value, int):
        return
    if not isinstance(value, str):
        raise UnsafeFilterValue(
            f"字段 {field_name} 的值类型不支持: {type(value).__name__}")
    if len(value) > MAX_FILTER_VALUE_LEN:
        raise UnsafeFilterValue(
            f"字段 {field_name} 的值过长（{len(value)} > {MAX_FILTER_VALUE_LEN}）")
    bad = sorted({ch for ch in value if ch in DANGEROUS_CHARS})
    if bad:
        raise UnsafeFilterValue(
            f"字段 {field_name} 的值含危险字符 {bad!r}")
    if not SAFE_FILTER_VALUE_RE.match(value):
        raise UnsafeFilterValue(
            f"字段 {field_name} 的值不符合安全正则: {value!r}")


def assert_fields_whitelisted(node: Optional[FilterNode]) -> None:
    """递归校验字段名白名单（自检 #2）。"""
    if node is None:
        raise EmptyFilterNotAllowed("过滤器为 None：空 filter 等于全放行，拒绝")
    if node.op in (Op.ALL, Op.ANY):
        for child in node.children:
            assert_fields_whitelisted(child)
        return
    if node.field not in FILTERABLE_FIELDS:
        raise UnknownFilterField(
            f"过滤字段不在白名单内: {node.field!r}（合法字段：{sorted(FILTERABLE_FIELDS)}）")
    if node.op is Op.ARRAY_ANY and node.field != "acl_tokens":
        raise UnknownFilterField(
            f"array_any 只允许作用于 acl_tokens，收到 {node.field!r}")


def assert_values_safe(node: Optional[FilterNode]) -> None:
    """递归校验值安全性（自检 #3）。"""
    if node is None:
        raise EmptyFilterNotAllowed("过滤器为 None：空 filter 等于全放行，拒绝")
    if node.op in (Op.ALL, Op.ANY):
        for child in node.children:
            assert_values_safe(child)
        return
    _check_leaf_value(node.op, node.value, node.field)


def assert_no_cross_dimension_or(node: Optional[FilterNode],
                                 *,
                                 _depth: int = 0) -> None:
    """递归校验"跨维 OR"（自检 #1，裁定 A6 / BR-02）。

    规则：
    - 顶层 ``ALL`` 且无子节点 = ``FALSE_FILTER``（合法，唯一允许的空节点）；
    - 其余 ``ALL`` 必须有子节点；
    - ``ANY`` 子节点必须**同一 field**，或全部落在 ``VISIBILITY_FIELDS``
      （``visibility_class`` / ``acl_tokens`` 是"同一可见性维度"的两种满足方式）；
    - ``ANY`` 的子节点不得再是 ``ALL`` / ``ANY``（禁止嵌套组）。
    """
    if node is None:
        raise EmptyFilterNotAllowed("过滤器为 None：空 filter 等于全放行，拒绝")
    if node.op is Op.ALL:
        if not node.children:
            if _depth == 0:
                return  # FALSE_FILTER
            raise EmptyFilterNotAllowed("嵌套的空 ALL 节点无语义，拒绝")
        for child in node.children:
            assert_no_cross_dimension_or(child, _depth=_depth + 1)
        return
    if node.op is Op.ANY:
        if not node.children:
            raise EmptyFilterNotAllowed("空 ANY 节点等于『任何都放行』，拒绝")
        fields = {child.field for child in node.children}
        for child in node.children:
            if child.op in (Op.ALL, Op.ANY):
                raise MixedDimensionOrError(
                    "ANY 节点内不允许嵌套 ALL/ANY 组：跨维度只能 AND")
        if not fields.issubset(VISIBILITY_FIELDS) and len(fields) > 1:
            raise MixedDimensionOrError(
                "ANY 节点出现跨维度子节点（OR 语义越权）: "
                f"{sorted(fields)}；只有同一维度的多值才允许 ANY")
        return
    return


def validate_filter_node(node: Optional[FilterNode]) -> None:
    """三重自检入口（编译器阶段 5 / 翻译器入口共用）。"""
    assert_no_cross_dimension_or(node)
    assert_fields_whitelisted(node)
    assert_values_safe(node)
