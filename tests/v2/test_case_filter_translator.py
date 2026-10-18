"""FilterTranslator 注入防护单测：≥ 20 条恶意输入必须 raise 或安全翻译。

对照架构 §7.4（防注入七条）与 §11.5「Filter 注入防护：≥ 20 条恶意输入全部 raise」。

判据：**任何**"看起来像注入"的输入都要么 ``raise``，要么被翻译成**语义等价且无注入
风险**的表达式；绝不允许出现"翻译成功但语义被放宽"（那等于越权）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.contracts.case_acl import (
    FALSE_FILTER,
    FilterNode,
    MixedDimensionOrError,
    Op,
    UnsafeFilterValue,
    EmptyFilterNotAllowed,
    UnknownFilterField,
)
from mfg_agents.v2.security.filter_translator import (
    MAX_EXPR_LEN,
    canonical_filter_json,
    filter_hash,
    to_chroma_where,
    to_milvus_expr,
    to_predicate,
)

SAFE = FilterNode(Op.ALL, children=(
    FilterNode(Op.EQ, "tenant", "T001"),
    FilterNode(Op.IN, "status", ("active", "deprecated")),
    FilterNode(Op.LTE, "cls_rank", 1),
    FilterNode(Op.EQ, "is_knowledge_layer", True),
    FilterNode(Op.ANY, children=(
        FilterNode(Op.EQ, "visibility_class", 0),
        FilterNode(Op.ARRAY_ANY, "acl_tokens", ("dept:quality", "plant:P1")),
    )),
))


# ---------------------------------------------------------------- 正例

def test_safe_filter_translates_to_all_backends() -> None:
    expr = to_milvus_expr(SAFE)
    assert "tenant ==" in expr and "array_contains_any(acl_tokens" in expr
    assert "1 == 0" not in expr

    where = to_chroma_where(SAFE)
    assert "$and" in where

    predicate = to_predicate(SAFE)
    row = {"tenant": "T001", "status": "active", "cls_rank": 1,
           "is_knowledge_layer": True, "visibility_class": 1,
           "acl_tokens": ["dept:quality"]}
    assert predicate(row) is True
    assert predicate({**row, "acl_tokens": []}) is False
    assert predicate({**row, "tenant": "T999"}) is False


def test_false_filter_translates_to_constant_false() -> None:
    """永假节点必须翻成恒假表达式，绝不可翻成空 filter（空 = 全放行 = 越权）。"""
    assert to_milvus_expr(FALSE_FILTER) == "1 == 0"
    assert to_chroma_where(FALSE_FILTER) == {"chunk_id": {"$in": []}}
    assert to_predicate(FALSE_FILTER)({"tenant": "T001"}) is False


def test_single_condition_not_wrapped_in_and() -> None:
    """Chroma 的 ``$and`` 需 ≥2 操作数（§6.3.4 限制 ②）。"""
    single = FilterNode(Op.ALL, children=(FilterNode(Op.EQ, "tenant", "T001"),))
    assert to_chroma_where(single) == {"tenant": {"$eq": "T001"}}


# ---------------------------------------------------------------- 恶意输入（≥ 20）

MALICIOUS_VALUES = [
    'T001" or "1',                     # 1 引号 + OR
    'T001" || "x',                     # 2 Milvus 逻辑或
    'T001" && tenant != "x',           # 3 逻辑与
    '"; drop table case; --',          # 4 SQL 注入尝试
    "T001' or '1'='1",                 # 5 单引号注入
    "T001\\",                          # 6 反斜杠转义
    "T001\x00X",                       # 7 NUL 字节
    "T001) or (1==1",                  # 8 括号闭合
    "T001\nnewline",                   # 9 换行（拆表达式）
    "tenant",                          # 10 合法正则但无危险字符 → 允许（值域）
    "T001{TEST}",                      # 11 花括号
    "T001[0]",                         # 12 方括号
    "T001 % 2",                        # 13 百分号
    "T001*",                           # 14 通配符
    "A" * 65,                          # 15 超长（> 64）
    "序号一",                          # 16 中文（合法，允许）
    "T001<>&",                         # 17 尖括号与与号
    "T001=T001",                       # 18 等号
]


@pytest.mark.parametrize("value", MALICIOUS_VALUES)
def test_malicious_values_raise_or_translate_safely(value: str) -> None:
    node = FilterNode(Op.EQ, "tenant", value)
    try:
        expr = to_milvus_expr(node)
    except UnsafeFilterValue:
        return  # 拒绝即通过
    # 允许翻译时，必须证明"值被当成字符串字面量"，没有拼进表达式结构
    assert expr.startswith("tenant == ")
    assert "; drop" not in expr.lower()
    assert "||" not in expr
    assert "&&" not in expr
    # Chroma 侧同值也必须可安全翻译
    where = to_chroma_where(node)
    assert where == {"tenant": {"$eq": value}}


MALICIOUS_FIELDS = [
    "tenant; drop table case",
    "tenant || 1==1",
    "acl_tokens[0]",
    "../../etc/passwd",
    "tenant)",
    "",
    "x" * 80,
]


@pytest.mark.parametrize("field", MALICIOUS_FIELDS)
def test_malicious_fields_rejected(field: str) -> None:
    with pytest.raises(UnknownFilterField):
        to_milvus_expr(FilterNode(Op.EQ, field, "T001"))


def test_empty_filter_rejected_for_every_backend() -> None:
    """空 filter（``None``）必须 raise——它等于"不过滤"。"""
    for translator in (to_milvus_expr, to_chroma_where, to_predicate):
        with pytest.raises(EmptyFilterNotAllowed):
            translator(None)


def test_empty_any_rejected() -> None:
    with pytest.raises(EmptyFilterNotAllowed):
        to_predicate(FilterNode(Op.ANY, children=()))


def test_empty_in_list_rejected() -> None:
    with pytest.raises(EmptyFilterNotAllowed):
        to_milvus_expr(FilterNode(Op.IN, "status", ()))


def test_empty_array_any_rejected() -> None:
    with pytest.raises(EmptyFilterNotAllowed):
        to_milvus_expr(FilterNode(Op.ARRAY_ANY, "acl_tokens", ()))


def test_too_many_tokens_rejected_not_truncated() -> None:
    """超限**必须 raise**，绝不截断（截断 = 静默放宽 = 越权，架构 R-A3）。"""
    tokens = tuple(f"dept:d{index}" for index in range(65))
    with pytest.raises(UnsafeFilterValue):
        to_milvus_expr(FilterNode(Op.ARRAY_ANY, "acl_tokens", tokens))


def test_expression_length_limit() -> None:
    """表达式超过 4096 字符必须 raise（而不是发一个被截断的 expr）。"""
    # 用大量 IN 值构造超长表达式
    values = tuple(f"v{index:04d}" for index in range(500))
    node = FilterNode(Op.ALL, children=tuple(
        FilterNode(Op.IN, "defect_mode_code", values) for _ in range(3)))
    with pytest.raises(UnsafeFilterValue):
        to_milvus_expr(node)
    assert MAX_EXPR_LEN == 4096


def test_cross_dimension_or_rejected_at_translation() -> None:
    bad = FilterNode(Op.ALL, children=(
        FilterNode(Op.ANY, children=(
            FilterNode(Op.EQ, "owner_dept", "quality"),
            FilterNode(Op.EQ, "plant", "P1"),
        )),
    ))
    with pytest.raises(MixedDimensionOrError):
        to_milvus_expr(bad)


def test_array_any_only_on_acl_tokens() -> None:
    with pytest.raises(Exception):
        to_milvus_expr(FilterNode(Op.ARRAY_ANY, "status", ("active",)))


def test_unsupported_value_type_rejected() -> None:
    with pytest.raises(UnsafeFilterValue):
        to_milvus_expr(FilterNode(Op.EQ, "tenant", {"nested": "dict"}))
    with pytest.raises(UnsafeFilterValue):
        to_milvus_expr(FilterNode(Op.EQ, "tenant", 3.5))


def test_filter_hash_is_stable_and_has_no_plaintext() -> None:
    """审计只记哈希：同一 AST 稳定，且哈希串里看不到值。"""
    digest = filter_hash(SAFE)
    assert len(digest) == 64
    assert digest == filter_hash(SAFE)
    assert "quality" not in digest
    canonical = canonical_filter_json(SAFE)
    assert canonical.startswith("{")
    assert '"op":"all"' in canonical


def test_predicate_fails_closed_on_missing_field() -> None:
    """缺字段（None）→ 不匹配（fail-closed），不是"默认通过"。"""
    predicate = to_predicate(FilterNode(Op.LTE, "cls_rank", 1))
    assert predicate({}) is False
    assert predicate({"cls_rank": 2}) is False
    assert predicate({"cls_rank": 1}) is True


def test_predicate_array_any_fail_closed_on_string() -> None:
    """脏数据（字符串而非数组）→ 恒假，而不是抛错或被当成包含。"""
    predicate = to_predicate(FilterNode(Op.ARRAY_ANY, "acl_tokens", ("dept:quality",)))
    assert predicate({"acl_tokens": "dept:quality"}) is False
