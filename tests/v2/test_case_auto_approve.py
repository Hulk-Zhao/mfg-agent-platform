"""案例助手 · 演示自动审批（预留接口 + 演示实现）测试。

被测对象：``services/case_auto_approve.py`` 的 ``AccessApprover``（预留接口）与
``DemoAgentApprover``（演示实现），以及 ``POST /cases/access/requests/{id}/demo-approve``。

这套用例钉死的不变量（每条都对应一个"演示便利"不能换来的东西）：

| 不变量 | 用例 |
| --- | --- |
| 自动审批走的是**同一条** decide（ACL/有效期/索引重建/审计） | `test_demo_approve_grants_access` |
| 演示审批在审计里**显式标记**（`.demo` 后缀），人工审批一个字节不变 | `test_demo_approve_is_marked_in_audit` / `test_manual_decide_action_unchanged` |
| 开关关着 → 403，回到人工审批 | `test_demo_approval_disabled_by_flag` 等 |
| 只有申请人本人能触发 | `test_only_requester_can_trigger_demo_approval` |
| 解析不出有资格的审批岗 → **fail-closed 409**，不降级找人批 | `test_demo_approve_fails_closed_when_no_approver_role` |
| B9 职责分离不因演示豁免（唯一审批岗是申请人自己 → 拒） | `test_demo_approval_skips_self_when_requester_is_only_approver` |
| 授权仍是**个人特批**，别人看不到 | `test_demo_grant_is_personal` |
"""
from __future__ import annotations

import pytest

#: 仅 dept:quality 可见（equipment 发起 → quality 审批岗代批）
QUALITY_CASE = "QC-PD-2026-0055"
#: 无人持有审批岗的职能部门（finance 没有 case.access.approve 角色）
NO_APPROVER_DEPT = "finance"
#: 审批岗恰好只有申请人自己时的场景案例
SELF_ONLY_CASE = "QC-AA-SELF-0001"
#: 无审批岗职能部门的案例
NO_APPROVER_CASE = "QC-AA-NODEPT-0001"

REQUESTER = "equipment"


@pytest.fixture(autouse=True)
def _clean_access_state():
    """每条用例前清空上一条留下的申请单与个人特批授权。

    与 ``test_case_access_request.py`` 同口径：只清 ``case_access_request`` 与
    ``dim='user'`` 的 ACL 行，**不动种子案例自带的 ACL/visibility**——
    重置种子可见性会把"equipment 看不到品质部案例"的前提改掉，用例会假绿。
    """
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
    body = {"role": role, "dept": dept, "plant": plant, "name": f"{role}-aa"}
    body.update(extra)
    resp = client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _runtime():
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    return get_case_runtime()


def _detail(client, token: str, case_id: str) -> int:
    return client.get(f"/api/v2/cases/{case_id}", headers=_auth(token)).status_code


def _submit(client, token: str, case_id: str,
            reason: str = "需要引用该案例做本岗位异常分析") -> dict:
    resp = client.post(f"/api/v2/cases/{case_id}/request-access",
                       headers=_auth(token), json={"reason": reason})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _make_case(runtime, case_id: str, *, owner_dept: str, plant: str = "P1",
               acl: list[dict] | None = None) -> None:
    """造一条 active 案例（owner_dept / ACL 可控，用于 fail-closed 场景）。"""
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": f"自动审批用例 {case_id}",
        "summary": "用例造的数据", "phenomenon": "连锡", "root_cause_text": "压力",
        "countermeasure_text": "调整", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": f"8D-{case_id}",
        "owner_dept": owner_dept, "plant": plant,
    })
    runtime.store.set_acl(case_id, acl or [{"dim": "plant", "value": plant}],
                          allow_widen=True)
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)


def _new_actions(runtime, before_count: int) -> list[str]:
    """只取 ``before_count`` 之后新写的审计动作。

    审计日志是**追加式**的，同一模块内前几条用例的审批记录仍在链上；
    不断言"全链没有 .demo"，而是只看本次动作——否则用例之间会互相污染。
    """
    total = runtime.audit.count()
    return [row["action"] for row in runtime.audit.recent(limit=max(0, total - before_count))]


# ---------------------------------------------------------------- 1. 主链路


def test_demo_approve_grants_access(case_client):
    """演示自动审批 → 与人工审批同样的效果：详情 200、受限清单移除、ACL 落库。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    assert _detail(case_client, token, QUALITY_CASE) == 404
    data = _submit(case_client, token, QUALITY_CASE)
    rid = data["request"]["request_id"]
    assert data["demo_approval"]["available"] is True

    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                            headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["demo"] is True
    assert body["request"]["status"] == "approved"
    assert body["request"]["expires_at"], "授权没有到期时间"
    assert "演示" in body["request"]["decision_reason"]

    # 授权立刻生效（索引已重建）
    assert _detail(case_client, token, QUALITY_CASE) == 200
    search = case_client.post("/api/v2/cases/search", headers=_auth(token),
                              json={"query": "案例", "action": "query", "k": 50})
    restricted = {x["case_id"] for x in search.json()["data"]["restricted"]}
    assert QUALITY_CASE not in restricted

    # ACL 落到关系库（dim=user，纯 user_id，不带前缀）
    runtime = _runtime()
    user_rows = [r for r in runtime.store.acl_entries(QUALITY_CASE)
                 if r["dim"] == "user"]
    assert user_rows, "授权没有写进 case_acl"
    assert all(":" not in str(r["value"]) for r in user_rows)


def test_demo_approve_is_marked_in_audit(case_client):
    """演示审批在审计链里显式标记（`.demo` 后缀），且链仍然完整。"""
    runtime = _runtime()
    before = runtime.audit.count()
    token = _session(case_client, REQUESTER, dept="equipment")
    rid = _submit(case_client, token, QUALITY_CASE)["request"]["request_id"]
    assert case_client.post(
        f"/api/v2/cases/access/requests/{rid}/demo-approve",
        headers=_auth(token)).status_code == 200
    actions = _new_actions(runtime, before)
    assert "case.access.approved.demo" in actions, \
        f"演示审批没有打 .demo 标记：{actions}"
    assert "case.access.requested" in actions
    assert "case.access.pushed" in actions
    assert runtime.audit.verify_chain()["valid"] is True


def test_manual_decide_action_unchanged(case_client):
    """人工审批路径一个字节不变：action 仍是 ``case.access.approved``（无 .demo）。"""
    runtime = _runtime()
    before = runtime.audit.count()
    token = _session(case_client, REQUESTER, dept="equipment")
    rid = _submit(case_client, token, QUALITY_CASE)["request"]["request_id"]
    approver = _session(case_client, "quality_director", dept="quality")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(approver),
                            json={"approve": True, "reason": "同意本岗位引用"})
    assert resp.status_code == 200, resp.text
    actions = _new_actions(runtime, before)
    assert "case.access.approved" in actions
    assert "case.access.approved.demo" not in actions, \
        "人工审批被误打了演示标记"


# ---------------------------------------------------------------- 2. 开关与准入


def test_demo_approval_disabled_by_flag(case_client, monkeypatch):
    """专用开关关掉 → 预览不可用 + 触发 403，申请停在 pending（回到人工审批）。"""
    from mfg_agents.common.config import settings

    monkeypatch.setattr(settings, "CASE_ACCESS_AUTO_APPROVE_DEMO", False)
    token = _session(case_client, REQUESTER, dept="equipment")
    data = _submit(case_client, token, QUALITY_CASE)
    assert data["demo_approval"]["available"] is False
    rid = data["request"]["request_id"]

    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                            headers=_auth(token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_DEMO_APPROVAL_DISABLED"
    assert _detail(case_client, token, QUALITY_CASE) == 404
    runtime = _runtime()
    assert runtime.access.get(rid)["status"] == "pending"


def test_demo_approval_disabled_by_demo_mode(case_client, monkeypatch):
    """``CASE_DEMO_MODE=false``（生产形态）→ 一律不自动批。"""
    from mfg_agents.common.config import settings

    token = _session(case_client, REQUESTER, dept="equipment")
    rid = _submit(case_client, token, QUALITY_CASE)["request"]["request_id"]
    monkeypatch.setattr(settings, "CASE_DEMO_MODE", False)
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                            headers=_auth(token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_DEMO_APPROVAL_DISABLED"


def test_only_requester_can_trigger_demo_approval(case_client):
    """别人不能拿你的申请号触发审批（避免刷授权）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    rid = _submit(case_client, token, QUALITY_CASE)["request"]["request_id"]
    other = _session(case_client, "maintenance_tech", dept="equipment")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                            headers=_auth(other))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_NOT_REQUESTER"
    assert _detail(case_client, token, QUALITY_CASE) == 404


def test_demo_approve_twice_is_409(case_client):
    """已批准的申请不能再触发（状态机不因演示开口子）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    rid = _submit(case_client, token, QUALITY_CASE)["request"]["request_id"]
    first = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                             headers=_auth(token))
    assert first.status_code == 200, first.text
    second = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                              headers=_auth(token))
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "CASE_ACCESS_INVALID_TRANSITION"


def test_demo_approve_unknown_request_is_404(case_client):
    token = _session(case_client, REQUESTER, dept="equipment")
    resp = case_client.post("/api/v2/cases/access/requests/AR-NOPE/demo-approve",
                            headers=_auth(token))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_REQUEST_NOT_FOUND"


def test_demo_approve_requires_auth(case_client):
    resp = case_client.post("/api/v2/cases/access/requests/AR-NOPE/demo-approve")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------- 3. fail-closed


def test_demo_approve_fails_closed_when_no_approver_role(case_client):
    """该职能部门没有审批岗（finance）→ 409 fail-closed，不降级找人批。"""
    runtime = _runtime()
    # plant=P2：申请人是 P1 的 equipment，被产线挡住 → 确实看不到（否则会先撞
    # "已可见不可申请"，测不到 fail-closed 那一层）。
    _make_case(runtime, NO_APPROVER_CASE, owner_dept=NO_APPROVER_DEPT, plant="P2")
    token = _session(case_client, REQUESTER, dept="equipment")
    data = _submit(case_client, token, NO_APPROVER_CASE)
    assert data["demo_approval"]["available"] is False
    rid = data["request"]["request_id"]

    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                            headers=_auth(token))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_DEMO_APPROVER_UNAVAILABLE"
    assert "人工" in resp.json()["detail"]["message"], \
        "失败文案要给出下一步动作（人工审批），不能只说'不行'"
    # 申请必须仍停在 pending，授权不得落库，案例依旧不可见
    assert runtime.access.get(rid)["status"] == "pending"
    assert not [r for r in runtime.store.acl_entries(NO_APPROVER_CASE)
                if r["dim"] == "user"]
    assert _detail(case_client, token, NO_APPROVER_CASE) == 404


def test_demo_approval_skips_self_when_requester_is_only_approver(case_client):
    """B9 不因演示豁免：唯一有资格的审批岗恰好是申请人自己 → 拒。"""
    runtime = _runtime()
    _make_case(runtime, SELF_ONLY_CASE, owner_dept="quality")
    # 申请人 = quality_director（P2 工厂被产线挡住，所以确实看不到）
    token = _session(case_client, "quality_director", dept="quality", plant="P2")
    assert _detail(case_client, token, SELF_ONLY_CASE) == 404
    data = _submit(case_client, token, SELF_ONLY_CASE)
    assert data["demo_approval"]["available"] is False
    rid = data["request"]["request_id"]

    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/demo-approve",
                            headers=_auth(token))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_DEMO_APPROVER_UNAVAILABLE"
    assert _detail(case_client, token, SELF_ONLY_CASE) == 404
    assert not [r for r in runtime.store.acl_entries(SELF_ONLY_CASE)
                if r["dim"] == "user"]


# ---------------------------------------------------------------- 4. 授权形态


def test_demo_grant_is_personal(case_client):
    """演示代批写出的授权仍是**个人特批**：同部门其他人照样看不到。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    rid = _submit(case_client, token, QUALITY_CASE)["request"]["request_id"]
    assert case_client.post(
        f"/api/v2/cases/access/requests/{rid}/demo-approve",
        headers=_auth(token)).status_code == 200
    other = _session(case_client, "maintenance_tech", dept="equipment")
    assert _detail(case_client, other, QUALITY_CASE) == 404, \
        "个人特批被演示代批写成了部门授权"


def test_demo_approver_identity_is_functional_manager(case_client):
    """服务层：quality 案例解析出的演示审批人是 quality_director（部门口径同源）。"""
    from mfg_agents.v2.services.case_auto_approve import DemoAgentApprover

    runtime = _runtime()
    case = runtime.store.get_case(QUALITY_CASE)
    approver = DemoAgentApprover(runtime.store)
    principal, role_key = approver.resolve(case, "u-equipment")
    assert role_key == "quality_director"
    assert principal.dept == "quality"
    assert principal.user_id == "u-quality_director"
    assert principal.roles == ("quality_director",)

    preview = approver.preview(case, "u-equipment")
    assert preview["available"] is True
    assert preview["approver_role"] == "quality_director"
