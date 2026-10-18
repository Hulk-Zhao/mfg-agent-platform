"""案例域 ACL 编译器单测：动作门禁 / 显式 deny / 跨维 OR / 快慢路径 / 无身份。

对照 ``docs/case-assistant-ontology-vector-auth-architecture.md`` §7.3（五阶段）
与 §11.5（单元测试判据）。

关键断言（都是"违反即越权"的硬约束）：
- ``AclCompiler.compile()`` 签名里**没有** ``query`` / ``query_text``；
- ``export`` / ``share`` 在阶段 0 就短路为 ``FALSE_FILTER``（不进检索）；
- 跨维 ``ANY`` 必须 ``raise MixedDimensionOrError``；
- 无身份 / 缺租户 / 未识别用工类型 → ``FALSE_FILTER``（fail-closed）。
"""
from __future__ import annotations

import inspect

import pytest

from mfg_agents.v2.contracts.case_acl import (
    FALSE_FILTER,
    MAX_PRINCIPAL_TOKENS,
    FilterNode,
    MixedDimensionOrError,
    Op,
    Principal,
    is_false_filter,
    validate_filter_node,
)
from mfg_agents.v2.security.acl_compiler import (
    AclCompiler,
    CasePolicyError,
    CasePolicySet,
    EnvContext,
    get_acl_compiler,
)
from mfg_agents.v2.security.case_rbac import get_case_rbac


@pytest.fixture(scope="module")
def compiler() -> AclCompiler:
    return get_acl_compiler()


def make_principal(**overrides) -> Principal:
    base = dict(
        user_id="u-pqe",
        tenant="T001",
        dept="quality",
        roles=("pqe",),
        plant="P1",
        max_classification_rank=1,
        employment_type="employee",
        token_version=2,
        principal_version=1,
    )
    base.update(overrides)
    return Principal(**base)


def collect_fields(node: FilterNode) -> set[str]:
    """递归收集 AST 里出现的字段名（断言用）。"""
    fields: set[str] = set()
    if node.field:
        fields.add(node.field)
    for child in node.children:
        fields |= collect_fields(child)
    return fields


def find_node(node: FilterNode, op: Op) -> list[FilterNode]:
    out: list[FilterNode] = []
    if node.op is op:
        out.append(node)
    for child in node.children:
        out.extend(find_node(child, op))
    return out


# ---------------------------------------------------------------- 签名纪律

def test_compile_signature_has_no_query_text(compiler: AclCompiler) -> None:
    """裁定 A10：编译器签名里没有 query_text、没有 LLM 输出位。"""
    params = set(inspect.signature(compiler.compile).parameters)
    assert "query" not in params
    assert "query_text" not in params
    assert "llm_output" not in params
    assert params == {"principal", "action", "policy", "env"}

    explained = set(inspect.signature(compiler.compile_explained).parameters)
    assert "query" not in explained and "query_text" not in explained


# ---------------------------------------------------------------- 阶段 0：动作门禁

@pytest.mark.parametrize("action", ["export", "share", "ingest"])
def test_action_gate_short_circuits_to_false(compiler: AclCompiler, action: str) -> None:
    node, reason = compiler.compile_explained(make_principal(), action)
    assert is_false_filter(node)
    assert reason, "拒绝必须给出可读原因（写审计）"


def test_unknown_action_raises(compiler: AclCompiler) -> None:
    with pytest.raises(CasePolicyError):
        compiler.compile(make_principal(), "download_everything")


# ---------------------------------------------------------------- 阶段 1：显式 DENY

def test_unknown_employment_type_denied(compiler: AclCompiler) -> None:
    node, reason = compiler.compile_explained(
        make_principal(employment_type="guest"), "query")
    assert is_false_filter(node)
    assert "deny-unknown-employment-type" in reason


def test_missing_tenant_denied(compiler: AclCompiler) -> None:
    node, reason = compiler.compile_explained(make_principal(tenant=""), "query")
    assert is_false_filter(node)
    assert "tenant" in reason or "租户" in reason


def test_anonymous_principal_denied(compiler: AclCompiler) -> None:
    node, _ = compiler.compile_explained(make_principal(user_id=""), "query")
    assert is_false_filter(node)
    node_none, _ = compiler.compile_explained(None, "query")
    assert is_false_filter(node_none)


# ---------------------------------------------------------------- 阶段 2：RBAC

def test_rbac_denied_when_role_lacks_action(compiler: AclCompiler) -> None:
    """``admin_hr`` 只有 query/preview/read，请求 cite → 拒绝。"""
    node, reason = compiler.compile_explained(
        make_principal(roles=("admin_hr",), dept="admin"), "cite")
    assert is_false_filter(node)
    assert "case.cite" in reason


def test_rbac_denied_for_unrecognized_role(compiler: AclCompiler) -> None:
    """未识别角色 → 空权限集（fail-closed），不是"默认放行"。"""
    node, _ = compiler.compile_explained(
        make_principal(roles=("ceo_of_everything",)), "query")
    assert is_false_filter(node)


# ---------------------------------------------------------------- 阶段 3：硬维度

def test_hard_dimensions_always_present(compiler: AclCompiler) -> None:
    principal = make_principal(max_classification_rank=2)
    node = compiler.compile(principal, "query")
    fields = collect_fields(node)
    assert {"tenant", "is_knowledge_layer", "status", "cls_rank"} <= fields

    tenant_eq = [n for n in find_node(node, Op.EQ) if n.field == "tenant"]
    assert tenant_eq and tenant_eq[0].value == "T001"

    layer_eq = [n for n in find_node(node, Op.EQ) if n.field == "is_knowledge_layer"]
    assert layer_eq and layer_eq[0].value is True

    status_in = [n for n in find_node(node, Op.IN) if n.field == "status"]
    assert status_in and "active" in status_in[0].value
    # BR-06：未结案与已撤权永不出现
    for forbidden in ("draft", "review", "rejected", "withdrawn"):
        assert forbidden not in status_in[0].value

    cls_lte = [n for n in find_node(node, Op.LTE) if n.field == "cls_rank"]
    assert cls_lte and cls_lte[0].value == 2


def test_cite_action_narrows_status_whitelist(compiler: AclCompiler) -> None:
    """``cite`` 只允许 active/deprecated（PRD §8.3）。"""
    node = compiler.compile(make_principal(), "cite")
    status_in = [n for n in find_node(node, Op.IN) if n.field == "status"]
    assert status_in[0].value == ["active", "deprecated"]


# ---------------------------------------------------------------- 阶段 4：快/慢路径

def test_employee_uses_fast_and_slow_path(compiler: AclCompiler) -> None:
    """正式员工：``vc==0`` OR ``array_any(acl_tokens, tokens)``。"""
    node = compiler.compile(make_principal(), "query")
    any_nodes = find_node(node, Op.ANY)
    assert len(any_nodes) == 1
    children = any_nodes[0].children
    assert {c.op for c in children} == {Op.EQ, Op.ARRAY_ANY}
    assert {c.field for c in children} == {"visibility_class", "acl_tokens"}

    tokens = children[1].value
    assert "dept:quality" in tokens
    assert "plant:P1" in tokens
    assert "etype:employee" in tokens


def test_non_employee_uses_fast_path_only(compiler: AclCompiler) -> None:
    """外包/临时/供应商账号：只走 ``vc==0`` 快路径（**收紧**，不是放宽）。"""
    node = compiler.compile(make_principal(employment_type="outsource"), "query")
    assert not find_node(node, Op.ANY), "非正式员工不得进入令牌交集慢路径"
    assert not find_node(node, Op.ARRAY_ANY)
    vc_eq = [n for n in find_node(node, Op.EQ) if n.field == "visibility_class"]
    assert vc_eq and vc_eq[0].value == 0


def test_pv_mismatch_denied(compiler: AclCompiler) -> None:
    principal = make_principal(principal_version=3)
    node, reason = compiler.compile_explained(
        principal, "query", env=EnvContext(expected_principal_version=4))
    assert is_false_filter(node)
    assert "pv" in reason


# ---------------------------------------------------------------- 阶段 5：自检

def test_compiled_node_passes_self_check(compiler: AclCompiler) -> None:
    node = compiler.compile(make_principal(), "query")
    validate_filter_node(node)  # 不抛错即通过三重自检


def test_cross_dimension_or_raises() -> None:
    """跨维 OR 是"OR 语义越权"的典型形态，编译期必须 raise。"""
    bad = FilterNode(Op.ALL, children=(
        FilterNode(Op.EQ, "tenant", "T001"),
        FilterNode(Op.ANY, children=(
            FilterNode(Op.EQ, "owner_dept", "quality"),
            FilterNode(Op.ARRAY_ANY, "acl_tokens", ("dept:equipment",)),
        )),
    ))
    with pytest.raises(MixedDimensionOrError):
        validate_filter_node(bad)


def test_any_with_nested_group_raises() -> None:
    bad = FilterNode(Op.ALL, children=(
        FilterNode(Op.ANY, children=(
            FilterNode(Op.ALL, children=(FilterNode(Op.EQ, "plant", "P1"),)),
            FilterNode(Op.EQ, "plant", "P2"),
        )),
    ))
    with pytest.raises(MixedDimensionOrError):
        validate_filter_node(bad)


def test_false_filter_is_not_empty_filter() -> None:
    """``FALSE_FILTER`` 是显式永假；``None`` 才是"空 filter"（必须 raise）。"""
    assert is_false_filter(FALSE_FILTER)
    validate_filter_node(FALSE_FILTER)
    with pytest.raises(Exception):
        validate_filter_node(None)


# ---------------------------------------------------------------- 主体派生

def test_principal_tokens_derived_and_ordered() -> None:
    """主体 token 的派生顺序是**稳定契约**，不只是集合相等。

    审计 ``row_hash`` 对 payload 做规范化时依赖这个顺序：一旦某次派生用
    ``set`` 迭代，跨进程（PYTHONHASHSEED 不同）就会排出不同序列，
    链校验会随机失效——而且是那种"本地跑好好的、线上偶发失败"的坏味道。
    """
    principal = make_principal(
        roles=("pqe", "qa"), line_ids=("SMT-1",), customer_ids=("CUS-9",))
    # 末尾的 user:<user_id> 是后加的一档：把操作者本人也纳入匹配维度，
    # 以支持"仅本人可见"这类过滤。顺序仍固定在最后。
    assert principal.principal_tokens == (
        "role:pqe", "role:qa", "dept:quality", "plant:P1",
        "line:SMT-1", "cus:CUS-9", "etype:employee", "user:u-pqe",
    )


def test_principal_tokens_ignore_external_value() -> None:
    """``principal_tokens`` 不可外部注入（传入被忽略）。"""
    principal = Principal(user_id="u", tenant="T001", dept="quality",
                          roles=("pqe",), plant="P1",
                          principal_tokens=("dept:equipment",))
    assert "dept:equipment" not in principal.principal_tokens
    assert "dept:quality" in principal.principal_tokens


def test_principal_tokens_capped_at_limit() -> None:
    many = tuple(f"role{r}" for r in range(200))
    principal = Principal(user_id="u", tenant="T001", roles=many)
    assert len(principal.principal_tokens) == MAX_PRINCIPAL_TOKENS


def test_max_classification_clamped_and_defaults() -> None:
    assert Principal(user_id="u", tenant="T", max_classification_rank=99).max_classification_rank == 3
    assert Principal(user_id="u", tenant="T", max_classification_rank=-5).max_classification_rank == 0
    assert Principal(user_id="u", tenant="T", max_classification_rank="bad").max_classification_rank == 0


# ---------------------------------------------------------------- 策略与 RBAC 表

def test_policy_visible_status_per_action() -> None:
    policy = CasePolicySet.from_yaml()
    assert policy.visible_status("query") == (
        "active", "deprecated", "superseded", "archived")
    assert policy.visible_status("export") == ()
    assert policy.is_action_enabled("query") is True
    assert policy.is_action_enabled("export") is False
    assert policy.is_action_enabled("share") is False


def test_default_open_rule_exempts_only_role_and_dept() -> None:
    """裁定 A6：``quality-knowledge-default-open`` 只豁免 role/dept 两维。"""
    policy = CasePolicySet.from_yaml()
    rule = policy.rule_by_id("quality-knowledge-default-open")
    assert rule is not None
    assert set(rule.dimensions_exempt) == {"role", "dept"}
    assert rule.effect == "allow"


def test_case_rbac_has_no_share_holder() -> None:
    """N6：外发分享默认无任何角色持有。"""
    rbac = get_case_rbac()
    assert rbac.roles_with("case.share") == ()


def test_case_rbac_export_limited_to_few_roles() -> None:
    rbac = get_case_rbac()
    holders = set(rbac.roles_with("case.export"))
    assert holders == {"gm", "quality_director"}


def test_case_rbac_rejects_unknown_role() -> None:
    from mfg_agents.v2.security.case_rbac import CaseRbacError, CaseRbacRegistry

    with pytest.raises(CaseRbacError):
        CaseRbacRegistry({"invented_role": ("query",)})


def test_case_rbac_rejects_unknown_action() -> None:
    from mfg_agents.v2.security.case_rbac import CaseRbacError, CaseRbacRegistry

    with pytest.raises(CaseRbacError):
        CaseRbacRegistry({"pqe": ("publish_everything",)})


# ---------------------------------------------------------------- Agent 治理纪律


def test_agent_roles_cannot_hold_approval_action():
    """Agent 身份角色**不得**持有 ``case.access.approve``（B9 的注册表层护栏）。

    为什么在注册表层堵：服务层已经有"审批人 ≠ 申请人"和"必须持有审批权"两道门，
    但如果某个 agent 角色真的拿到了审批动作，程序化自审批就通了。
    与 ``ingest / export / share`` 同一套 fail-fast 纪律——配置写错直接**加载失败**，
    不会静默生效。

    两层断言：
    1. ``AGENT_FORBIDDEN_ACTIONS`` 里必须含 ``access.approve``（这是**加载期**护栏，
       真正生效的地方：将来有人给 agent 角色加这个动作，构造期就炸）；
    2. 当前注册表里已存在的 agent 角色**一个都不能**持有它。
       （当前平台的 32 个治理角色里没有 agent 身份角色，所以第 2 条今天是空集——
       这正是为什么第 1 条更重要：护栏要能在"将来有人加 agent 角色"时生效。）
    """
    from mfg_agents.v2.contracts.permissions import get_permission_registry
    from mfg_agents.v2.security.case_rbac import (
        AGENT_FORBIDDEN_ACTIONS,
        get_case_rbac,
    )

    assert "access.approve" in AGENT_FORBIDDEN_ACTIONS, \
        "审批动作未列入 Agent 禁用清单：将来给 agent 角色加这个动作就会静默生效"

    registry = get_permission_registry()
    agents = {r["key"] for r in registry.describe()["roles"]
              if (role := registry.role(r["key"])) is not None and role.is_agent}
    holders = set(get_case_rbac().roles_with("case.access.approve"))
    leaked = agents & holders
    assert not leaked, f"Agent 角色持有审批权：{sorted(leaked)}"
