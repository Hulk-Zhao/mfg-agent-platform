"""案例助手 · 跨部门访问申请测试（授权 + 审批）。

为什么这套流程值得单独写一个文件：它是**唯一一条能改变"谁能看到什么"的业务路径**。
别的地方出错是"少看到 / 多看到"，这里出错是"审批形同虚设"或者"授权收不回来"。
所以每条用例都钉死一个不变量：

| 不变量 | 用例 |
| --- | --- |
| 涉密 / 未结案**不走**申请通道 | `test_secret_case_is_not_requestable` 等 |
| 已可见的不该申请（避免刷审计） | `test_already_visible_is_rejected` |
| 同一人同一案例只能有一条 pending | `test_duplicate_pending_is_rejected` |
| 只有**职能部门**能审批 | `test_non_functional_dept_cannot_approve` |
| Agent **不自动批准** | `test_agent_push_does_not_grant` |
| 批准 → ACL 落 user 维 + 索引重建 + 检索可见 | `test_approved_grant_makes_case_visible` |
| 拒绝 → 仍然不可见 | `test_rejected_case_stays_invisible` |
| 授权到期自动回收 | `test_expired_grant_is_reaped` |
| 每次动作都留审计 | `test_access_actions_are_audited` |
| 筛选条件不放宽（`filter_hash` 只因主体令牌变化而变） | `test_filter_hash_changes_only_after_grant` |
"""
from __future__ import annotations

import pytest

#: 仅 dept:quality 可见（quality 域申请 → equipment 发起 → quality 审批）
QUALITY_CASE = "QC-PD-2026-0055"
#: 仅 P2 工厂可见（用 plant 维度做"另一种限制"）
PLANT_CASE = "QC-MD-2026-0021"
#: 全员开放（用来验证"已可见不可申请"）
OPEN_CASE = "QC-CC-2026-0087"

REQUESTER = "equipment"
#: 审批人必须是**归属职能部门的管理岗**（B9 之后只有 quality_director /
#: equipment_manager 持有 case.access.approve，普通工程师一律不给）。
APPROVER = "quality_director"


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """每个用例前重置案例域限流窗口。

    为什么需要：限流是按 (user, ip) 计的，而这些用例大量以同一个演示身份
    （u-equipment / u-qa_manager）连打几十个请求，会被 429 挡掉——那会让
    "申请流程有 bug"的误报出现。清窗口是让用例只验业务逻辑。
    """
    from mfg_agents.v2.security import case_rate_limit
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    case_rate_limit.reset_case_rate_limit()
    runtime = get_case_runtime()
    # 只清"上一条用例留下的东西"：申请单 + 个人特批授权。
    # ⚠️ 绝不能顺手把所有案例的 visibility_class 重置——那是种子数据的一部分，
    # 重置等于把"equipment 看不到品质部案例"的前提改掉，用例会假绿/假红。
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
    body = {"role": role, "dept": dept, "plant": plant, "name": f"{role}-acc"}
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


def _restricted(client, token: str, query: str = "案例") -> list[dict]:
    resp = client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "action": "query", "k": 50})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["restricted"]


def _search(client, token: str, query: str = "案例") -> dict:
    resp = client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "action": "query", "k": 50})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- 1. 申请入口的准入


def test_short_reason_is_rejected(case_client):
    """理由不足 10 字 → 400（审批人无从判断就没有信息量）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    resp = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                            headers=_auth(token), json={"reason": "想看"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_REQUEST_INVALID"


def test_already_visible_is_rejected(case_client):
    """已经能看到的案例再建申请 → 409（否则申请通道会被当成审计噪音来源）。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post(f"/api/v2/cases/{OPEN_CASE}/request-access",
                            headers=_auth(token),
                            json={"reason": "这条我已经能看到，只是想留个记录"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_ALREADY_VISIBLE"


def test_secret_case_is_not_requestable(case_client):
    """涉密（L3）**不走申请通道**：保密红线不由审批豁免。"""
    runtime = _runtime()
    case_id = "QC-RT-SECRET-0001"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "涉密案例",
        "summary": "L3 涉密内容", "phenomenon": "涉密",
        "root_cause_text": "涉密", "countermeasure_text": "涉密",
        "category": "quality_case", "subcategory": "process_defect",
        "cls_rank": 3, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-SECRET-1",
        "owner_dept": "quality",
    })
    runtime.store.set_visibility(case_id, 1, ["dept:quality"])
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    token = _session(case_client, REQUESTER, dept="equipment")
    resp = case_client.post(f"/api/v2/cases/{case_id}/request-access",
                            headers=_auth(token),
                            json={"reason": "需要看一下这条涉密案例的处置过程"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_NOT_REQUESTABLE"


def test_unpublished_case_is_not_requestable(case_client):
    """未结案（review）不接受访问申请。"""
    runtime = _runtime()
    case_id = "QC-RT-REVIEW-0001"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "待审案例",
        "summary": "还没结案", "phenomenon": "连锡", "root_cause_text": "压力",
        "countermeasure_text": "调整", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-REVIEW-1",
        "owner_dept": "quality",
    })
    runtime.store.set_visibility(case_id, 1, ["dept:quality"])
    runtime.store.transition(case_id, "review")
    runtime.index_service.sync_case(case_id)

    token = _session(case_client, REQUESTER, dept="equipment")
    resp = case_client.post(f"/api/v2/cases/{case_id}/request-access",
                            headers=_auth(token),
                            json={"reason": "需要参考这条尚未结案案例的经验"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_NOT_REQUESTABLE"


def test_withdrawn_case_is_not_requestable(case_client):
    """已撤权案例不接受申请（撤权是终态）。"""
    runtime = _runtime()
    case_id = "QC-RT-WD-0001"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "已撤权案例",
        "summary": "已撤权", "phenomenon": "连锡", "root_cause_text": "压力",
        "countermeasure_text": "调整", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-WD-1",
        "owner_dept": "quality",
    })
    runtime.store.set_visibility(case_id, 1, ["dept:quality"])
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.store.transition(case_id, "withdrawn")
    runtime.index_service.sync_case(case_id)

    token = _session(case_client, REQUESTER, dept="equipment")
    resp = case_client.post(f"/api/v2/cases/{case_id}/request-access",
                            headers=_auth(token),
                            json={"reason": "想看看这条已撤权案例的内容"})
    assert resp.status_code == 403, resp.text


def test_unknown_case_returns_404(case_client):
    """不存在的案例 → 404（申请通道不成为存在性探测接口）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    resp = case_client.post("/api/v2/cases/QC-NOT-EXIST-9999/request-access",
                            headers=_auth(token),
                            json={"reason": "申请查看这条不存在的案例内容"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "CASE_NOT_FOUND_OR_NOT_VISIBLE"


# ---------------------------------------------------------------- 2. 状态机


def test_duplicate_pending_is_rejected(case_client):
    """同一 (案例, 申请人) 已有 pending → 409 `CASE_ACCESS_REQUEST_DUPLICATED`。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    body = {"reason": "需要引用这条案例做设备异常分析"}
    first = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                             headers=_auth(token), json=body)
    assert first.status_code == 200, first.text
    second = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                              headers=_auth(token), json=body)
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "CASE_ACCESS_REQUEST_DUPLICATED"


def test_requester_can_cancel_pending(case_client):
    """申请人可撤回自己的 pending 申请。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "先申请看看，随后撤回这条申请"})
    rid = created.json()["data"]["request"]["request_id"]
    runtime = _runtime()
    principal = runtime.resolver.from_token(token)
    row = runtime.access.cancel(principal, rid)
    assert row["status"] == "cancelled"
    # 撤回后可以重新申请
    again = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                             headers=_auth(token),
                             json={"reason": "撤回后重新发起一次访问申请"})
    assert again.status_code == 200, again.text


def test_decide_twice_is_rejected(case_client):
    """已裁决的申请不能再裁决。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做工艺改善验证"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    first = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                             headers=_auth(approver),
                             json={"approve": False, "reason": "暂不开放"})
    assert first.status_code == 200, first.text
    second = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                              headers=_auth(approver),
                              json={"approve": True, "reason": "改主意了"})
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "CASE_ACCESS_INVALID_TRANSITION"


# ---------------------------------------------------------------- 3. 审批权限


def test_non_functional_dept_cannot_approve(case_client):
    """非职能部门（这里是 equipment，而案例属 quality）不能审批 → 403。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    wrong = _session(case_client, "equipment_manager", dept="equipment")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(wrong), json={"approve": True, "reason": "同意"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_NOT_APPROVER"


def test_functional_dept_can_approve(case_client):
    """职能部门（quality）可以审批。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(approver),
                            json={"approve": True, "reason": "同意本岗位引用"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "approved"


def test_decide_unknown_request_is_404(case_client):
    token = _session(case_client, APPROVER, dept="quality")
    resp = case_client.post("/api/v2/cases/access/requests/AR-NOPE/decide",
                            headers=_auth(token), json={"approve": True})
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------- 4. Agent 不越权


def test_agent_push_does_not_grant(case_client):
    """Agent 只推送与留痕：**不得**改变可见性（铁律：Agent 不得持 approve）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    assert _detail(case_client, token, QUALITY_CASE) == 404
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    assert created.status_code == 200
    route = created.json()["data"]["route"]
    assert route["ok"] is True and route["pushed_to"] == "quality"
    assert route["pushed_to_label"] == "品质部"
    # 推送之后仍然不可见
    assert _detail(case_client, token, QUALITY_CASE) == 404


def test_agent_route_is_recorded_in_audit(case_client):
    """推送留痕进审计（只记路由与理由模板，不记案例正文）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                     headers=_auth(token),
                     json={"reason": "需要引用该案例做设备异常分析"})
    runtime = _runtime()
    actions = [row["action"] for row in runtime.audit.recent(limit=50)]
    assert "case.access.requested" in actions
    assert "case.access.pushed" in actions


# ---------------------------------------------------------------- 5. 授权生效


def test_approved_grant_makes_case_visible(case_client):
    """批准后：申请人立刻可见（详情 200），且检索里不再列为受限。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    assert _detail(case_client, token, QUALITY_CASE) == 404
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    assert case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(approver),
                            json={"approve": True, "reason": "同意"}).status_code == 200
    assert _detail(case_client, token, QUALITY_CASE) == 200
    data = _search(case_client, token)
    assert QUALITY_CASE not in {x["case_id"] for x in data["restricted"]}


def test_grant_writes_user_acl_row(case_client):
    """授权落到关系库 ``case_acl``（dim=user，值是纯 user_id）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{PLANT_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                     headers=_auth(approver), json={"approve": True, "reason": "同意"})
    runtime = _runtime()
    rows = runtime.store.acl_entries(PLANT_CASE)
    user_rows = [r for r in rows if r["dim"] == "user"]
    assert user_rows, "授权没有写进 case_acl"
    assert all(":" not in str(r["value"]) for r in user_rows), \
        f"user 维的值应是纯 user_id：{user_rows}"
    assert int(runtime.store.get_case(PLANT_CASE)["visibility_class"]) == 1


def test_grant_changes_candidate_set_not_filter(case_client):
    """批准后变的是**候选集**，不是过滤条件本身。

    这一条钉的是安全叙事的核心：``filter_hash`` 由「主体令牌 + 动作」编译而来，
    与案例侧的 ACL 无关。所以授权前后它**必须不变**——如果变了，反而说明
    "为了让这次通过去改了过滤条件"，那正是要防的事。
    真正变的是：这条案例从"看不到"变成"看得到"。
    """
    token = _session(case_client, REQUESTER, dept="equipment")
    before = _search(case_client, token)
    assert QUALITY_CASE in {x["case_id"] for x in before["restricted"]}

    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                     headers=_auth(approver), json={"approve": True, "reason": "同意"})

    after = _search(case_client, token)
    assert after["filter_hash"] == before["filter_hash"], \
        "批准竟然改变了过滤条件本身——这说明有人为了放行去改了 filter"
    assert QUALITY_CASE not in {x["case_id"] for x in after["restricted"]}
    assert _detail(case_client, token, QUALITY_CASE) == 200


def test_grant_does_not_widen_for_others(case_client):
    """授权是**个人特批**：别人仍然看不到（user 维度不是部门维度）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                     headers=_auth(approver), json={"approve": True, "reason": "同意"})
    other = _session(case_client, "maintenance_tech", dept="equipment")
    assert _detail(case_client, other, QUALITY_CASE) == 404, \
        "个人特批被当成了部门授权"


def test_rejected_case_stays_invisible(case_client):
    """驳回后申请人仍然不可见（且不得残留 ACL 行）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(approver),
                            json={"approve": False, "reason": "本岗位暂不开放"})
    assert resp.status_code == 200 and resp.json()["data"]["status"] == "rejected"
    assert _detail(case_client, token, QUALITY_CASE) == 404
    runtime = _runtime()
    assert not [r for r in runtime.store.acl_entries(QUALITY_CASE)
                if r["dim"] == "user"]


# ---------------------------------------------------------------- 6. 有效期回收


def test_expired_grant_is_reaped(case_client):
    """授权到期 → 惰性回收：移除 ACL 行 + 重建索引 + 申请置 expired。"""
    runtime = _runtime()
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                     headers=_auth(approver), json={"approve": True, "reason": "同意"})
    assert _detail(case_client, token, QUALITY_CASE) == 200

    # 人为把到期时间拨到过去
    with runtime.store._conn() as conn:   # noqa: SLF001 - 测试直接改到期时间
        conn.execute(
            "UPDATE case_access_request SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE request_id=?", (rid,))
    assert runtime.access.reap_expired() >= 1
    assert _detail(case_client, token, QUALITY_CASE) == 404, "到期授权未回收"
    row = runtime.access.get(rid)
    assert row and row["status"] == "expired"


def test_grant_has_expiry_timestamp(case_client):
    """批准的授权必须带到期时间（有期限的授权才能被回收）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    decided = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                               headers=_auth(approver),
                               json={"approve": True, "reason": "同意"})
    assert decided.json()["data"]["expires_at"], "授权没有到期时间"


# ---------------------------------------------------------------- 7. 受限清单与待办


def test_restricted_list_exposes_only_safe_fields(case_client):
    """受限清单只给标题级信息：正文/密级/ACL 明细一律不出现。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    items = _restricted(case_client, token)
    assert items, "应当能看到受限案例（否则用户无从申请）"
    allowed = {"case_id", "title", "category", "reason", "requestable"}
    for item in items:
        assert set(item) == allowed, f"受限清单出现多余字段：{set(item) - allowed}"
    import json as _json
    blob = _json.dumps(items, ensure_ascii=False)
    assert "plant:" not in blob and "dept:" not in blob, "受限清单泄漏了 ACL 维度"
    assert "cls_rank" not in blob and "visibility_class" not in blob


def test_restricted_is_capped(case_client):
    """受限清单有上限（标题级信息也不能变成批量导出）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    assert len(_restricted(case_client, token)) <= 20


def test_dept_scope_lists_pending_requests(case_client):
    """职能部门能看到待办；申请人自己能看到自己提交的。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]

    mine = case_client.get("/api/v2/cases/access/requests?scope=mine",
                           headers=_auth(token)).json()["data"]["requests"]
    assert any(r["request_id"] == rid for r in mine)

    approver = _session(case_client, APPROVER, dept="quality")
    todo = case_client.get("/api/v2/cases/access/requests?scope=dept",
                           headers=_auth(approver)).json()["data"]["requests"]
    assert any(r["request_id"] == rid for r in todo)


def test_request_list_masks_requester_id(case_client):
    """待办列表不暴露完整申请人 id（审批人不该看到别人的账号全名）。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                     headers=_auth(token),
                     json={"reason": "需要引用该案例做设备异常分析"})
    approver = _session(case_client, APPROVER, dept="quality")
    rows = case_client.get("/api/v2/cases/access/requests?scope=dept",
                           headers=_auth(approver)).json()["data"]["requests"]
    assert rows
    for row in rows:
        assert "*" in str(row["requester_id"]), "申请人 id 未脱敏"


def test_access_actions_are_audited(case_client):
    """申请 / 批准 都落审计，且链仍然完整。"""
    token = _session(case_client, REQUESTER, dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    approver = _session(case_client, APPROVER, dept="quality")
    case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                     headers=_auth(approver), json={"approve": True, "reason": "同意"})
    runtime = _runtime()
    actions = [row["action"] for row in runtime.audit.recent(limit=80)]
    assert "case.access.requested" in actions
    assert "case.access.approved" in actions
    assert runtime.audit.verify_chain()["valid"] is True


def test_unknown_acl_dim_still_rejected(case_client):
    """user 维度也走同一套维度名校验（写错维度名 = 静默失效的老坑）。"""
    from mfg_agents.v2.services.case_store import CaseStoreError

    runtime = _runtime()
    with pytest.raises(CaseStoreError):
        runtime.store.set_acl(QUALITY_CASE,
                              [{"dim": "users", "value": "u-equipment"}],
                              allow_widen=True)


def test_user_acl_value_must_not_carry_prefix(case_client):
    """``value="user:xxx"`` 会被拒（否则令牌变成 user:user:xxx，永久失效）。"""
    from mfg_agents.v2.services.case_store import CaseStoreError

    runtime = _runtime()
    with pytest.raises(CaseStoreError):
        runtime.store.set_acl(QUALITY_CASE,
                              [{"dim": "user", "value": "user:u-equipment"}],
                              allow_widen=True)


@pytest.mark.parametrize("scope", ["mine", "dept"])
def test_request_list_requires_auth(case_client, scope):
    """申请列表必须鉴权。"""
    resp = case_client.get(f"/api/v2/cases/access/requests?scope={scope}")
    assert resp.status_code == 401, resp.text

# ---------------------------------------------------------------- B9：职责分离


def test_self_approval_is_forbidden(case_client):
    """申请人不能审批自己的申请（按 user_id 比，不按角色）。

    QA 探针 `_qa/probe_access_request.py` 的原始结论：同部门但被产线挡住的员工
    可以自己批自己的申请，拿到跨产线全文。这是上线阻塞级的授权绕过。
    """
    runtime = _runtime()
    case_id = "QC-RT-SELF-0001"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "P1 产线专有案例",
        "summary": "仅 P1 可见", "phenomenon": "连锡", "root_cause_text": "压力",
        "countermeasure_text": "调整", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-SELF-1",
        "owner_dept": "quality", "plant": "P1",
    })
    runtime.store.set_acl(case_id, [{"dim": "plant", "value": "P1"}],
                          allow_widen=True)
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    # 申请人：quality 部门、P2 工厂（被 plant 挡住）
    token = _session(case_client, "quality_director", dept="quality", plant="P2")
    assert _detail(case_client, token, case_id) == 404
    created = case_client.post(f"/api/v2/cases/{case_id}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用这条 P1 产线的案例做工艺改善"})
    assert created.status_code == 200, created.text
    rid = created.json()["data"]["request"]["request_id"]

    # 同一身份（甚至换成更高角色）再来批 → 必须 403
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(token),
                            json={"approve": True, "reason": "自己批自己"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACCESS_SELF_APPROVAL_FORBIDDEN"
    assert _detail(case_client, token, case_id) == 404, "自审批被拒后却拿到了访问权"


def test_non_manager_same_dept_cannot_approve(case_client):
    """同部门但**非管理岗**不能审批（`case.access.approve` 只给职能负责人）。"""
    token = _session(case_client, "equipment", dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    # qa_manager 是"同部门管理岗"，但本条用 equipment_manager（他部门管理岗）
    other_mgr = _session(case_client, "equipment_manager", dept="equipment")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(other_mgr),
                            json={"approve": True, "reason": "我们部门同意了"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] in (
        "CASE_ACCESS_NOT_APPROVER", "CASE_ACCESS_SELF_APPROVAL_FORBIDDEN")


def test_manager_of_other_dept_cannot_approve_either(case_client):
    """他部门管理岗也不能批（管理岗 ≠ 归属部门）。"""
    token = _session(case_client, "equipment", dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(_session(case_client, "equipment_manager",
                                                   dept="equipment")),
                            json={"approve": True, "reason": "同意"})
    assert resp.status_code == 403, resp.text


def test_functional_manager_can_approve(case_client):
    """归属职能部门的管理岗 → 200（职责分离 + RBAC 两道门都过）。"""
    token = _session(case_client, "equipment", dept="equipment")
    created = case_client.post(f"/api/v2/cases/{QUALITY_CASE}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用该案例做设备异常分析"})
    rid = created.json()["data"]["request"]["request_id"]
    # quality_director 是 quality 的负责人，持有 case.access.approve
    approver = _session(case_client, "quality_director", dept="quality")
    resp = case_client.post(f"/api/v2/cases/access/requests/{rid}/decide",
                            headers=_auth(approver),
                            json={"approve": True, "reason": "同意本岗位引用"})
    assert resp.status_code == 200, resp.text
    assert _detail(case_client, token, QUALITY_CASE) == 200


def test_plain_engineer_has_no_approve_action(case_client):
    """普通工程师一律不持有 ``case.access.approve``（注册表层断言）。"""
    from mfg_agents.v2.security.case_rbac import get_case_rbac

    rbac = get_case_rbac()
    holders = set(rbac.roles_with("case.access.approve"))
    assert "qa" not in holders and "equipment" not in holders
    assert "pqe" not in holders and "sqe" not in holders
    assert holders, "没有任何角色持有审批权：审批通道将完全不可用"
    for manager in ("quality_director", "equipment_manager"):
        assert manager in holders, f"{manager} 应持有审批权"


# ---------------------------------------------------------------- B10：涉密不披露


def test_secret_case_never_appears_in_restricted(case_client):
    """涉密案例**连标题都不许**出现在受限清单里（B10）。

    QA 探针原始结论：`quality_director`（rank 3）的 restricted 里出现了
    「制程（机密）：客户定制工艺窗口异常」，`requestable=false`——
    标记不构成过滤，标题照样泄漏。而同一条在检索链路是被
    `deny-secret-classification` 丢弃的，只有 restricted 这条旁路漏了。
    """
    runtime = _runtime()
    case_id = "QC-RT-SECRET-0002"
    title = "制程（机密）：客户定制工艺窗口异常"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": title,
        "summary": "机密摘要", "phenomenon": "机密", "root_cause_text": "机密",
        "countermeasure_text": "机密", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 3, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-SECRET-2",
        "owner_dept": "quality", "plant": "P2",
    })
    runtime.store.set_visibility(case_id, 1, ["dept:quality"])
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    token = _session(case_client, "quality_director", dept="quality", plant="P1",
                     max_classification_rank=3)
    resp = case_client.post("/api/v2/cases/search", headers=_auth(token),
                            json={"query": "机密 工艺窗口", "action": "query", "k": 50})
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]

    ids = {item["case_id"] for item in body["restricted"]}
    assert case_id not in ids, "涉密案例出现在受限清单里"
    assert case_id not in {h["case_id"] for h in body["hits"]}, "涉密案例出现在命中里"
    assert "机密" not in resp.text, "响应文本里出现了涉密标题字样"
    assert case_id not in resp.text and title not in resp.text


def test_unpublished_case_not_in_restricted(case_client):
    """未结案 / 已撤权案例同样不可披露存在。"""
    runtime = _runtime()
    for case_id, target in (("QC-RT-REV-0001", "review"),
                            ("QC-RT-WD-0002", "withdrawn")):
        runtime.store.create_case({
            "case_id": case_id, "case_code": case_id, "title": f"未结案案例 {case_id}",
            "summary": "还没结案", "phenomenon": "连锡", "root_cause_text": "压力",
            "countermeasure_text": "调整", "category": "quality_case",
            "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
            "source_ticket_type": "8d", "source_ticket_id": f"8D-RT-{case_id}",
            "owner_dept": "quality", "plant": "P2",
        })
        runtime.store.set_visibility(case_id, 1, ["dept:quality"])
        runtime.store.transition(case_id, "review")
        if target == "withdrawn":
            runtime.store.transition(case_id, "active")
            runtime.store.transition(case_id, "withdrawn")
        runtime.index_service.sync_case(case_id)

    token = _session(case_client, "quality_director", dept="quality", plant="P1",
                     max_classification_rank=3)
    resp = case_client.post("/api/v2/cases/search", headers=_auth(token),
                            json={"query": "连锡 未结案", "action": "query", "k": 50})
    assert resp.status_code == 200, resp.text
    ids = {item["case_id"] for item in resp.json()["data"]["restricted"]}
    assert "QC-RT-REV-0001" not in ids, "未结案案例出现在受限清单"
    assert "QC-RT-WD-0002" not in ids, "已撤权案例出现在受限清单"


def test_restricted_titles_never_leak_secret_wording(case_client):
    """受限清单的标题里不得出现"机密/涉密/秘密"字样（B10 的不变量固化）。

    ``requestable=true`` 只保证"这条可申请"，不保证**标题本身**可以被看到。
    涉密案例一旦带着标题进清单，即便打了"不可申请"的标记，也已经是泄漏——
    所以这里直接扫字样，把边界钉成不变量，防止将来有人改回去。
    """
    token = _session(case_client, "quality_director", dept="quality",
                     max_classification_rank=3)
    data = _search(case_client, token, query="案例")
    items = data["restricted"]
    assert items, "受限清单为空，用例失效"
    banned = ("机密", "涉密", "秘密", "绝密")
    for item in items:
        assert item.get("requestable") is True, \
            f"受限清单出现不可申请条目：{item}"
        assert not any(word in str(item.get("title") or "") for word in banned), \
            f"受限清单标题含涉密字样：{item}"
        # 也不应带正文或密级细节
        assert set(item) == {"case_id", "title", "category", "reason",
                             "requestable"}, f"受限清单字段超集：{sorted(item)}"


def test_approval_fails_closed_when_case_has_no_owning_department(case_client):
    """归属部门不明时**不得放行审批**（默认拒绝，不是"无处可推则不拦"）。

    早前实现是一条 `return`：案例既无 ``owner_dept``、ACL 里也没有 dept 维度时，
    任何持有 ``case.access.approve`` 的职能负责人都能批——而前置 RBAC 门禁只保证
    他是**某个**职能负责人，并不保证他与这条案例有关。归属不明时缺的那一维
    恰恰是"这条案例归谁管"，它是唯一不能猜的。

    这里造一条**无归属**的案例（owner_dept 为空、ACL 只有 plant 维度），
    用一个跟它毫无关系的部门主管去批，断言 403 且请求仍是 pending。
    """
    runtime = _runtime()
    case_id = "QC-RT-NODEPT-0001"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "无归属部门案例",
        "summary": "未指定归属", "phenomenon": "虚焊", "root_cause_text": "温度",
        "countermeasure_text": "调参", "category": "quality_case",
        "subcategory": "process_defect", "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-NODEPT-1",
        "plant": "P1",
    })
    # ACL 只有产线维度，**没有 dept** → 归属不可推
    runtime.store.set_acl(case_id, [{"dim": "plant", "value": "P1"}],
                          allow_widen=True)
    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    token = _session(case_client, "quality_director", dept="quality", plant="P2")
    created = case_client.post(f"/api/v2/cases/{case_id}/request-access",
                               headers=_auth(token),
                               json={"reason": "需要引用这条案例做工艺改善验证"})
    assert created.status_code == 200, created.text
    rid = created.json()["data"]["request"]["request_id"]

    # 换一个与该案例毫无关系的部门主管来批（必须换 user_id：
    # 同 user_id 会先被"禁止自审批"门禁拦下，那条另有用例覆盖，
    # 这里要验的是"归属不明 → 无人可批"这一层）
    other = _session(case_client, "quality_director", dept="equipment",
                     plant="P1", user_id="u-equipment-director")
    decided = case_client.post(
        f"/api/v2/cases/access/requests/{rid}/decide",
        headers=_auth(other), json={"approve": True, "reason": "同意"})
    assert decided.status_code == 403, decided.text
    assert decided.json()["detail"]["code"] == "CASE_ACCESS_NOT_APPROVER"

    # 归属不明 → 任何人都不放行；申请必须仍停在 pending，授权不得落库
    assert _detail(case_client, token, case_id) == 404
    assert runtime.store.get_case(case_id) is not None
