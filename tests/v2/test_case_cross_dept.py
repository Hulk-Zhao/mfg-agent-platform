"""案例助手 · 跨部门通行角色（总经理全部门可见）测试。

背景（2026-10-19 用户裁定）：「总经理应该权限都有」——实测 gm 受限 30 条、
比任何单一部门岗位看得都少，根因是案例 ACL 的部门/工厂维对 gm 照挡。

被测对象：``case_policy.yaml`` 的 ``cross_dept_roles: [gm]`` +
``AclCompiler`` 阶段 4 的 cross-dept 路径 + ``AclEvaluator`` 步骤 10.5 豁免。

这套用例钉死的不变量（每条都对应"豁免不能变成越权"）：

| 不变量 | 用例 |
| --- | --- |
| 总经理全部门可见：受限清单清零，可见数不少于任何单一部门岗位 | `test_gm_sees_all_departments` |
| 豁免**只**对配置里的角色生效：其他岗位照旧受限 | `test_exempt_only_applies_to_configured_role` |
| 涉密（L3）对总经理**仍然**不可见（硬门禁不因豁免放宽） | `test_gm_still_blocked_by_secret` |
| 未结案案例对总经理**仍然**不可见（BR-06 照常） | `test_gm_still_blocked_by_unclosed` |
| 显式 deny 授权条目优先于豁免（deny 是一票否决） | `test_explicit_deny_beats_exempt` |
| 编译器与评估器语义一致（否则召回被二次授权整批丢弃） | `test_compiler_and_evaluator_agree` |
"""
from __future__ import annotations

import pytest

REQUESTER = "equipment"
APPROVER = "qa"
GM = "gm"


@pytest.fixture(autouse=True)
def _clean_access_state():
    """与 test_case_stats.py 同口径：只清申请表与 user 维 ACL 行。"""
    from mfg_agents.v2.security import case_rate_limit
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    case_rate_limit.reset_case_rate_limit()
    runtime = get_case_runtime()
    with runtime.store._conn() as conn:   # noqa: SLF001
        touched = [str(r["case_id"]) for r in conn.execute(
            "SELECT DISTINCT case_id FROM case_acl WHERE dim='user'").fetchall()]
        conn.execute("DELETE FROM case_access_request")
        conn.execute("DELETE FROM case_acl WHERE dim='user'")
    for case_id in touched:
        entries = runtime.store.acl_entries(case_id)
        tokens = [f"{e['dim']}:{e['value']}" for e in entries
                  if str(e.get("effect")) == "allow"]
        runtime.store.set_visibility(case_id, 0 if not tokens else 1, tokens)
        runtime.index_service.sync_case(case_id)
    yield
    case_rate_limit.reset_case_rate_limit()


# ---------------------------------------------------------------- 工具


def _session(client, role: str, dept: str = "", plant: str = "P1", **extra) -> str:
    body = {"role": role, "dept": dept, "plant": plant, "name": f"{role}-cd"}
    body.update(extra)
    resp = client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stats(client, token: str) -> dict:
    resp = client.get("/api/v2/cases/stats", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _detail(client, token: str, case_id: str) -> int:
    return client.get(f"/api/v2/cases/{case_id}", headers=_auth(token)).status_code


def _runtime():
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    return get_case_runtime()


def _make_case(case_id: str, *, cls_rank: int = 1, status_path: bool = True,
               extra_acl: list[dict] | None = None) -> None:
    """造一条 active 案例（密级 / ACL 可控；status_path=False 时停在 review）。"""
    runtime = _runtime()
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": f"跨部门用例 {case_id}",
        "summary": "用例造的数据", "phenomenon": "偏移", "root_cause_text": "原因",
        "countermeasure_text": "对策", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": cls_rank, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": f"8D-{case_id}",
        "owner_dept": "quality", "plant": "P2",   # 故意放 P2：部门/工厂维都对 gm 无匹配
    })
    acl = [{"dim": "dept", "value": "quality"}, {"dim": "plant", "value": "P2"}]
    acl.extend(extra_acl or [])
    runtime.store.set_acl(case_id, acl, allow_widen=True)
    if status_path:
        runtime.store.transition(case_id, "review")
        runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)


# ---------------------------------------------------------------- 用例


def test_gm_sees_all_departments(case_client):
    """总经理受限清零，且可见数不少于任何单一部门岗位。"""
    gm = _stats(case_client, _session(case_client, GM, max_classification_rank=3))
    qa = _stats(case_client, _session(case_client, APPROVER, dept="quality",
                                      max_classification_rank=3))
    eq = _stats(case_client, _session(case_client, REQUESTER, dept="equipment",
                                      max_classification_rank=3))
    assert gm["restricted_total"] == 0, (
        f"总经理仍有 {gm['restricted_total']} 条受限——跨部门通行没生效")
    assert gm["visible_total"] >= max(qa["visible_total"], eq["visible_total"]), (
        "总经理的可见数必须不少于任何单一部门岗位（权限都有）")


def test_exempt_only_applies_to_configured_role(case_client):
    """豁免只对 cross_dept_roles 里配置的角色生效：qa/equipment 照旧受限。"""
    qa = _stats(case_client, _session(case_client, APPROVER, dept="quality"))
    eq = _stats(case_client, _session(case_client, REQUESTER, dept="equipment"))
    assert qa["restricted_total"] > 0, "qa 不应被豁免（配置里只有 gm）"
    assert eq["restricted_total"] > 0, "equipment 不应被豁免（配置里只有 gm）"


def test_gm_still_blocked_by_secret(case_client):
    """涉密（cls_rank=3）对总经理仍然不可见：豁免不是越权通行证（E1/PRD §7.4）。"""
    _make_case("QC-CD-SECRET-1", cls_rank=3)
    # 密级上限给满 L3：让 404 只能来自"涉密拒绝"，而不是主体上限（对照才成立）
    gm_token = _session(case_client, GM, max_classification_rank=3)
    assert _detail(case_client, gm_token, "QC-CD-SECRET-1") == 404, (
        "涉密案例对总经理必须 404（deny-secret-classification 是治理红线）")
    # L2 及以下则正常可见（对照：证明 404 是密级挡的，不是别的）
    _make_case("QC-CD-L2-OK", cls_rank=2)
    assert _detail(case_client, gm_token, "QC-CD-L2-OK") == 200


def test_gm_still_blocked_by_unclosed(case_client):
    """未结案（review）案例对总经理仍然不可见（BR-06 照常）。"""
    _make_case("QC-CD-REVIEW-1", status_path=False)   # 停在 review
    gm_token = _session(case_client, GM)
    assert _detail(case_client, gm_token, "QC-CD-REVIEW-1") == 404


def test_explicit_deny_beats_exempt():
    """显式 deny 授权条目优先于跨部门豁免（evaluator 步骤 9 在豁免之前）。"""
    from mfg_agents.v2.security.acl_evaluator import get_acl_evaluator
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    runtime = get_case_runtime()
    case_id = "QC-CD-DENY-GM"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "deny 优先用例",
        "summary": "用例造的数据", "phenomenon": "偏移", "root_cause_text": "原因",
        "countermeasure_text": "对策", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": f"8D-{case_id}",
        "owner_dept": "quality", "plant": "P1",
    })
    runtime.store.set_acl(case_id, [
        {"dim": "dept", "value": "quality"},
        {"dim": "user", "value": "u-gm", "effect": "deny"},   # 点名拒绝总经理
    ], allow_widen=True)
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    principal = runtime.resolver.from_payload(
        {"sub": "u-gm", "roles": ["gm"], "dept": "", "plant": "P1",
         "tenant": "T001", "employment_type": "employee",
         "max_classification_rank": 3, "pv": 1, "tv": 2, "name": "gm"},
        verify_pv=False)
    rows = runtime.store.eval_rows(case_id)
    decision = get_acl_evaluator().authorize(principal, rows, "read")
    assert not decision.allow, "显式 deny:user:u-gm 必须压过跨部门豁免"


def test_compiler_and_evaluator_agree():
    """编译器 cross-dept 路径与评估器豁免语义一致：同一批案例两边判定相同。

    编译器说可见、评估器说不可见 → 检索结果被二次授权整批丢弃（历史上真
    出过这种"两套实现漂移"的故障，第二阶段双保险就是为兜它）。
    """
    from mfg_agents.v2.security.acl_compiler import get_acl_compiler
    from mfg_agents.v2.security.acl_evaluator import get_acl_evaluator
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    runtime = get_case_runtime()
    principal = runtime.resolver.from_payload(
        {"sub": "u-gm", "roles": ["gm"], "dept": "", "plant": "P1",
         "tenant": "T001", "employment_type": "employee",
         "max_classification_rank": 3, "pv": 1, "tv": 2, "name": "gm"},
        verify_pv=False)
    filter_node = get_acl_compiler().compile(principal, "query")
    candidate_ids = runtime.index_service.hard_visible_case_ids(filter_node, 500)
    assert candidate_ids, "编译器判 gm 可见的候选不应为空"
    rows_by_case = runtime.store.eval_rows_many(candidate_ids)
    passed, dropped = get_acl_evaluator().filter_authorized(
        principal, rows_by_case, "query")
    assert passed, "评估器应放行编译器判可见的案例（语义一致）"
    # 允许极少量双保险丢弃，但整批丢弃 = 两套实现漂移，必须红
    assert len(passed) > len(dropped), (
        f"二次授权丢弃 {len(dropped)}/{len(passed) + len(dropped)}——"
        "编译器与评估器的跨部门语义漂移了")
