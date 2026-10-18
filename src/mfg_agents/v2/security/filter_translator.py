"""FilterTranslator：``FilterNode`` AST → 各向量后端原生 filter。

架构位置：§7.4（防注入）。三个后端：

- ``to_milvus_expr``   → Milvus 布尔表达式字符串（``hybrid_search(filter=expr)``）
- ``to_chroma_where``  → Chroma ``where`` dict（处理 §6.3.4 的四条已知限制）
- ``to_predicate``     → ``Callable[[Mapping], bool]``，供**纯内存后端**使用

防注入七条（全部落在本模块，单测 ``tests/v2/test_case_filter_translator.py``）：

1. 字段名白名单（``FILTERABLE_FIELDS``）—— 不匹配即 ``raise UnknownFilterField``；
2. 值类型白名单：只允许 ``str`` / ``int`` / ``bool``；
3. 值正则 ``^[A-Za-z0-9_\\-\\.:@\\u4e00-\\u9fa5]{1,64}$``；
4. 危险字符显式拒绝（``"`` ``\\`` ``;`` ``()`` ``{}`` ``[]`` ``<>`` ``=`` ``!`` ``&`` ``|`` ``%`` ``*`` NUL）；
5. ``expr`` 长度 ≤ 4096，``acl_tokens`` 值个数 ≤ 64，超限 ``raise`` 而非截断；
6. **永假节点必须翻成恒假表达式**，空 AST（``None``）必须 ``raise``；
7. 审计只记 ``filter_hash = sha256(canonical_json)``，不落明文（明文可能含客户号）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping, Optional

from ..contracts.case_acl import (
    FALSE_FILTER,
    FilterNode,
    Op,
    UnsafeFilterValue,
    EmptyFilterNotAllowed,
    is_false_filter,
    validate_filter_node,
)

__all__ = [
    "MAX_EXPR_LEN",
    "CHROMA_ANY_SENTINEL",
    "to_milvus_expr",
    "to_chroma_where",
    "to_predicate",
    "canonical_filter_json",
    "filter_hash",
    "false_filter_expr_for",
    "explain_filter",
]

#: Milvus 表达式长度上限（超限 raise，绝不截断——截断 = 静默放宽 = 越权）。
MAX_EXPR_LEN = 4096

#: Chroma 不允许空数组元数据；"该维度无约束"写入此哨兵值（架构 §6.3.4 限制 ④）。
CHROMA_ANY_SENTINEL = "__ANY__"


# ---------------------------------------------------------------------------
# 内部：字面量与表达式片段
# ---------------------------------------------------------------------------

def _milvus_literal(value: Any) -> str:
    """标量 → Milvus 字面量（字符串用双引号包裹并转义，不拼接字段名）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise UnsafeFilterValue(f"不支持的字面量类型: {type(value).__name__}")
    # 走到这里说明已通过安全正则（无引号/反斜杠），转义仍保留以防御未来放宽正则
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _milvus_node(node: FilterNode) -> str:
    op = node.op
    if op is Op.ALL:
        return "(" + " and ".join(_milvus_node(c) for c in node.children) + ")"
    if op is Op.ANY:
        return "(" + " or ".join(_milvus_node(c) for c in node.children) + ")"
    if op is Op.EQ:
        return f"{node.field} == {_milvus_literal(node.value)}"
    if op is Op.IN:
        items = ", ".join(_milvus_literal(v) for v in node.value)
        return f"{node.field} in [{items}]"
    if op is Op.LTE:
        return f"{node.field} <= {_milvus_literal(node.value)}"
    if op is Op.GTE:
        return f"{node.field} >= {_milvus_literal(node.value)}"
    if op is Op.ARRAY_ANY:
        items = ", ".join(_milvus_literal(v) for v in node.value)
        return f"array_contains_any({node.field}, [{items}])"
    raise UnsafeFilterValue(f"不支持的算子: {op!r}")  # pragma: no cover - Enum 已封闭


def to_milvus_expr(node: Optional[FilterNode]) -> str:
    """AST → Milvus ``filter`` 表达式。

    Raises:
        EmptyFilterNotAllowed: ``node is None``（空 filter = 全放行 = 越权）。
        UnknownFilterField / UnsafeFilterValue: 字段或值不合规（注入防护）。
    """
    if node is None:
        raise EmptyFilterNotAllowed(
            "空过滤器禁止翻译：Milvus 空 filter 等于全放行（越权）")
    validate_filter_node(node)
    if is_false_filter(node):
        # 显式永假：必须给后端一个恒假表达式，而不是空串
        return "1 == 0"
    expr = _milvus_node(node)
    if len(expr) > MAX_EXPR_LEN:
        raise UnsafeFilterValue(
            f"filter 表达式长度 {len(expr)} 超过上限 {MAX_EXPR_LEN}")
    return expr


def _chroma_condition(node: FilterNode) -> dict:
    op = node.op
    if op is Op.EQ:
        return {node.field: {"$eq": node.value}}
    if op is Op.IN:
        return {node.field: {"$in": list(node.value)}}
    if op is Op.LTE:
        return {node.field: {"$lte": node.value}}
    if op is Op.GTE:
        return {node.field: {"$gte": node.value}}
    if op is Op.ARRAY_ANY:
        # Chroma 的 $contains 只接受单值 → 展开为 O(#tokens) 个 $or（§6.3.4）
        values = list(node.value)
        if len(values) == 1:
            return {node.field: {"$contains": values[0]}}
        return {"$or": [{node.field: {"$contains": v}} for v in values]}
    raise UnsafeFilterValue(f"Chroma 不支持的算子: {op!r}")  # pragma: no cover


def to_chroma_where(node: Optional[FilterNode]) -> dict:
    """AST → Chroma ``where`` dict。

    已知限制处理（§6.3.4）：
    - ``$and`` 需 ≥ 2 操作数 → 单条件**不**包 ``$and``；
    - ``where`` 不能是 ``{}`` → 永假节点返回 ``{"chunk_id": {"$in": []}}``；
    - 空数组不允许 → "无约束"由写入侧的 ``__ANY__`` 哨兵表达（见 adapter）。
    """
    if node is None:
        raise EmptyFilterNotAllowed(
            "空过滤器禁止翻译：Chroma where={} 会被当成无过滤（越权）")
    validate_filter_node(node)
    if is_false_filter(node):
        return {"chunk_id": {"$in": []}}
    return _chroma_node(node)


def _chroma_node(node: FilterNode) -> dict:
    op = node.op
    if op is Op.ALL:
        parts = [_chroma_node(c) for c in node.children]
        if len(parts) == 1:
            return parts[0]           # 单条件不包 $and（Chroma 要求 ≥2 操作数）
        return {"$and": parts}
    if op is Op.ANY:
        parts = [_chroma_node(c) for c in node.children]
        if len(parts) == 1:
            return parts[0]
        return {"$or": parts}
    return _chroma_condition(node)


# ---------------------------------------------------------------------------
# 内存后端谓词
# ---------------------------------------------------------------------------

def _scalar_ok(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None


def _predicate_for(node: FilterNode) -> Callable[[Mapping[str, Any]], bool]:
    op = node.op
    if op is Op.ALL:
        subs = [_predicate_for(c) for c in node.children]
        def _all(row: Mapping[str, Any]) -> bool:
            return all(fn(row) for fn in subs)
        return _all
    if op is Op.ANY:
        subs = [_predicate_for(c) for c in node.children]
        def _any(row: Mapping[str, Any]) -> bool:
            return any(fn(row) for fn in subs)
        return _any
    if op is Op.EQ:
        expected = node.value
        field_name = node.field
        def _eq(row: Mapping[str, Any]) -> bool:
            actual = row.get(field_name)
            if isinstance(expected, bool):
                # SQLite 存 0/1，Python 侧可能是 bool/int：统一按真值比较
                return bool(actual) is expected
            return actual == expected
        return _eq
    if op is Op.IN:
        allowed = list(node.value)
        field_name = node.field
        def _in(row: Mapping[str, Any]) -> bool:
            return row.get(field_name) in allowed
        return _in
    if op is Op.LTE:
        bound = node.value
        field_name = node.field
        def _lte(row: Mapping[str, Any]) -> bool:
            actual = row.get(field_name)
            return _scalar_ok(actual) and actual <= bound
        return _lte
    if op is Op.GTE:
        bound = node.value
        field_name = node.field
        def _gte(row: Mapping[str, Any]) -> bool:
            actual = row.get(field_name)
            return _scalar_ok(actual) and actual >= bound
        return _gte
    if op is Op.ARRAY_ANY:
        wanted = frozenset(node.value)
        field_name = node.field
        def _array_any(row: Mapping[str, Any]) -> bool:
            raw = row.get(field_name)
            if raw is None or isinstance(raw, (str, bytes)):
                # 空数组 / 非数组 → 恒假（fail-closed，符合 §12.4 硬约束 3）
                return False
            try:
                present = set(raw)
            except TypeError:  # pragma: no cover - 脏数据兜底
                return False
            return bool(present & wanted)
        return _array_any
    raise UnsafeFilterValue(f"不支持的算子: {op!r}")  # pragma: no cover


def to_predicate(node: Optional[FilterNode]) -> Callable[[Mapping[str, Any]], bool]:
    """AST → 内存后端谓词。**检索前**先过谓词，再算分（不是"先召回后过滤"）。

    永假节点返回恒 ``False`` 的谓词（而不是"不过滤"）。
    """
    if node is None:
        raise EmptyFilterNotAllowed(
            "空过滤器禁止构造谓词：不过滤等于全放行（越权）")
    validate_filter_node(node)
    if is_false_filter(node):
        def _never(row: Mapping[str, Any]) -> bool:  # noqa: ARG001 - 签名一致
            return False
        return _never
    return _predicate_for(node)


# ---------------------------------------------------------------------------
# 审计用哈希
# ---------------------------------------------------------------------------

def _node_as_dict(node: FilterNode) -> dict:
    return {
        "op": node.op.value,
        "field": node.field,
        "value": list(node.value) if isinstance(node.value, (list, tuple)) else node.value,
        "children": [_node_as_dict(c) for c in node.children],
    }


def canonical_filter_json(node: Optional[FilterNode]) -> str:
    """AST → 规范化 JSON（排序键、无空白，可直接哈希与复现）。"""
    if node is None:
        raise EmptyFilterNotAllowed("过滤器为 None，无法规范化")
    return json.dumps(_node_as_dict(node), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def filter_hash(node: Optional[FilterNode]) -> str:
    """``sha256(canonical_json(AST))``——审计只记这个，不记明文 filter。"""
    return hashlib.sha256(canonical_filter_json(node).encode("utf-8")).hexdigest()


def false_filter_expr_for(backend: str) -> Any:
    """按后端返回"恒假"表达式（供测试与对账脚本直接使用）。"""
    key = str(backend or "").strip().lower()
    if key == "milvus":
        return to_milvus_expr(FALSE_FILTER)
    if key == "chroma":
        return to_chroma_where(FALSE_FILTER)
    return to_predicate(FALSE_FILTER)


# ---------------------------------------------------------------------------
# 人类可读解释（给前端「为什么看不到」面板用）
# ---------------------------------------------------------------------------

_OP_TEXT = {
    Op.EQ: "=",
    Op.IN: "∈",
    Op.LTE: "≤",
    Op.GTE: "≥",
    Op.ARRAY_ANY: "任一命中",
    Op.ALL: "且",
    Op.ANY: "或",
}

#: ``acl_tokens`` 的含义说明。**刻意不展开令牌值**：令牌是授权凭据，
#: 回传"你命中哪几个令牌"等于告诉前端如何构造令牌（前端本来也不该知道）。
_ACL_TOKENS_TEXT = ("acl_tokens 任一命中（role / dept / plant / line / project / "
                    "customer / supplier / product_family / employment_type）")


def _explain_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _explain_node(node: FilterNode, depth: int) -> str:
    """把一个节点翻成短句（递归，深度上限防止超长文本）。"""
    if depth > 6:                                    # pragma: no cover - 防御
        return "…"
    if node.op in (Op.ALL, Op.ANY):
        if not node.children:
            # 空 ALL = 永假（显式拒绝），空 ANY 也该被 validate 拦掉
            return "永假（无任何条件满足时一律拒绝）"
        joiner = " 且 " if node.op is Op.ALL else " 或 "
        parts = [_explain_node(child, depth + 1) for child in node.children]
        if node.op is Op.ANY and len(parts) > 1:
            return "(" + joiner.join(parts) + ")"
        return joiner.join(parts)
    if node.field == "acl_tokens":
        return _ACL_TOKENS_TEXT
    if node.field == "visibility_class":
        return f"visibility_class {_OP_TEXT.get(node.op, node.op.value)} {_explain_value(node.value)}"
    if node.field == "is_knowledge_layer":
        return "仅知识层（证据层不进检索）"
    return (f"{node.field} {_OP_TEXT.get(node.op, node.op.value)} "
            f"{_explain_value(node.value)}")


def explain_filter(node: Optional[FilterNode], *, limit: int = 12) -> list[str]:
    """``FilterNode`` → 人类可读字符串数组（供前端展示「为什么看不到」）。

    为什么返回数组而不是一个字符串：前端要按行渲染，且顺序即语义（先硬维度、
    后软维度）。为什么**不回传** expr / 原始 AST：那是内部表达，暴露给浏览器
    既不安全（用户可以照着改自己请求）也没有意义（前端改不了 filter）。

    Args:
        node: 过滤树；``None`` 返回空数组（调用方应自行处理"未编译"的情况）。
        limit: 最多返回多少行（超出截断并追加"…"）。

    Returns:
        形如 ``["tenant = T001", "status ∈ [active, deprecated]", ...]`` 的数组。
    """
    if node is None:
        return []
    lines: list[str] = []
    if is_false_filter(node):
        return ["本次请求被拒绝：策略编译结果为永假（不进行任何检索）"]
    for child in node.children:
        lines.append(_explain_node(child, 0))
    if not lines:
        lines.append(_explain_node(node, 0))
    if len(lines) > limit:                            # pragma: no cover - 防御
        lines = lines[:limit] + ["…"]
    return lines
