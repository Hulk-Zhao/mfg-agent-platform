"""案例助手 · 顶部统计卡片``GET /cases/stats`` 测试。

被测对象：``services/case_search.py::CaseSearchService.stats`` 与
``demo/api.py::api_case_stats``。

这套用例钉死的不变量（每条都对应"卡片数字不能骗人"）：

| 不变量 | 用例 |
| --- | --- |
| 统计必须**与检索同源**：卡片数字、受限清单、检索结果三者对得上 | `test_stats_matches_search_scope` |
| 分布之和 == 可见总数（不允许"分类加起来对不上总数"） | `test_distribution_sums_to_visible_total` |
| 换身份 → 数字跟着变（可见范围是服务端裁决的，不是写死的） | `test_stats_differs_by_role` |
| 统计只给**数字**，不给标题/正文（不能变成批量导出） | `test_stats_exposes_counts_only` |
| 申请相关计数与申请列表同一口径 | `test_my_requests_counted_after_submit` |
| 授权生效后卡片即时反映（可见 +1、待审 -1） | `test_stats_reflects_granted_access` |
| 待我审批排除本部门发起（职责分离） | `test_pending_for_me_excludes_own_dept` |
| 无令牌 → 401（统计不是公开信息） | `test_stats_requires_token` |
"""
from __future__ import annotations

import pytest

#: 仅 dept:quality 可见（equipment 看不见 → 走申请 → 代批后可见 +1）
QUALITY_CASE = "QC-PD-2026-0055"

REQUESTER = "equipment"
APPROVER = "qa"


@pytest.fixture(autouse=True)
def _clean_access_state():
    """每条用例前清空上一条留下的申请单与个人特批授权。

    与 ``test_case_auto_approve.py`` 同口径：只清 ``case_access_request`` 与
    ``dim='user'`` 的 ACL 行，**不动种子案例自带的 ACL/visibility**。
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
    body = {"role": role, "dept": dept, "plant": plant, "name": f"{role}-stats"}
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


def _search_case_ids(client, token: str, query: str, k: int = 50) -> set[str]:
    resp = client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "k": k, "action": "query"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return {str(h["case_id"]) for h in data.get("hits", [])}


def _submit(client, token: str, case_id: str,
            reason: str = "需要引用该案例做本岗位异常分析") -> dict:
    resp = client.post(f"/api/v2/cases/{case_id}/request-access",
                       headers=_auth(token), json={"reason": reason})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- 用例


def test_stats_requires_token(case_client):
    """统计是身份范围内的信息：无令牌 401，不返回任何数字。"""
    resp = case_client.get("/api/v2/cases/stats")
    assert resp.status_code == 401


def test_stats_matches_search_scope(case_client):
    """★ 卡片数字必须与检索同源：检索能查到的案例数不超过卡片上的可见总数。

    为什么用「不超过」而不是「相等」：检索是 TopK 召回（k 条上限），
    可见总数是一次全量扫描；两者允许"检索少"，绝不允许"检索多"。
    """
    token = _session(case_client, REQUESTER, dept=REQUESTER)
    stats = _stats(case_client, token)
    hits = _search_case_ids(case_client, token, "SMT 焊接 缺陷", k=50)
    assert stats["visible_total"] > 0, "演示库里 equipment 至少能看见部分案例"
    assert len(hits) <= stats["visible_total"], (
        f"检索出 {len(hits)} 个案例 > 卡片可见总数 {stats['visible_total']}："
        "统计与检索的过滤口径漂移了")


def test_distribution_sums_to_visible_total(case_client):
    """分类/密级/状态三类分布各自求和都等于可见总数（不允许对不上）。"""
    token = _session(case_client, REQUESTER, dept=REQUESTER)
    stats = _stats(case_client, token)
    total = stats["visible_total"]
    for key in ("by_category", "by_subcategory", "by_rank", "by_status"):
        dist = stats[key]
        assert sum(dist.values()) == total, (
            f"{key} 求和 {sum(dist.values())} != 可见总数 {total}")
    assert total > 0
    # 密级档位必须是可展示的 L<数字>。``cls_rank == 0``（公开级）是 falsy，
    # 若误写成 ``case.get("cls_rank") or "?"`` 会全部落进 "L?"，求和仍对得上
    # （所以上面那条断言抓不到），但前端固定序里没有这档 → 密级分布看不全。
    for rank_key in stats["by_rank"]:
        assert rank_key.startswith("L") and rank_key[1:].isdigit(), (
            f"密级档位不可展示：{rank_key}（cls_rank=0 被当成了未知？）")


def test_stats_differs_by_role(case_client):
    """换身份 → 数字跟着变：可见范围是服务端裁决的，不是前端写死的。"""
    qa_token = _session(case_client, APPROVER, dept="quality")
    eq_token = _session(case_client, REQUESTER, dept="equipment")
    qa_stats = _stats(case_client, qa_token)
    eq_stats = _stats(case_client, eq_token)
    assert qa_stats["visible_total"] != eq_stats["visible_total"], (
        "质量岗与设备岗的可见案例数相同 —— 授权差异没生效，统计在报假数")


def test_stats_exposes_counts_only(case_client):
    """统计只给数字：响应里不能夹带任何案例标题、正文或 case_id 清单。

    卡片是"看板"，不是"检索接口"——一旦返回标题集合，就等于绕过了
    逐条授权直接拿到受限案例的标题。
    """
    token = _session(case_client, REQUESTER, dept=REQUESTER)
    payload = case_client.get("/api/v2/cases/stats",
                              headers=_auth(token)).json()["data"]
    blob = repr(payload)
    for forbidden in ("case_id", "title", "summary", "root_cause_text"):
        assert forbidden not in blob, f"统计响应里出现了 {forbidden}"


def test_my_requests_counted_after_submit(case_client):
    """提交申请后：我的申请总数 1、待审 1（与申请列表同一口径）。"""
    token = _session(case_client, REQUESTER, dept=REQUESTER)
    before = _stats(case_client, token)
    assert before["my_requests"].get("pending", 0) == 0

    _submit(case_client, token, QUALITY_CASE)
    after = _stats(case_client, token)
    assert after["my_requests_total"] == 1
    assert after["my_requests"].get("pending", 0) == 1


def test_stats_reflects_granted_access(case_client):
    """★ 授权生效后卡片必须即时反映：待审 -1、已批准 +1、可见 +1。

    这是"演示自动审批"讲得通的关键证据——弹窗结束、卡片数字同步变化，
    观众能立刻看到"授权真的生效了"。
    """
    token = _session(case_client, REQUESTER, dept=REQUESTER)
    before = _stats(case_client, token)
    data = _submit(case_client, token, QUALITY_CASE)
    request_id = data["request"]["request_id"]

    resp = case_client.post(
        f"/api/v2/cases/access/requests/{request_id}/demo-approve",
        headers=_auth(token), json={})
    assert resp.status_code == 200, resp.text

    after = _stats(case_client, token)
    assert after["my_requests"].get("pending", 0) == 0, "待审应归零"
    assert after["my_requests"].get("approved", 0) == 1, "应有一条已批准"
    assert after["visible_total"] == before["visible_total"] + 1, (
        "授权生效后该案例应进入可见范围（卡片可见数 +1）")


def test_pending_for_me_excludes_own_dept(case_client):
    """待我审批统计与申请列表同口径：不含本部门发起的（职责分离）。"""
    requester = _session(case_client, REQUESTER, dept=REQUESTER)
    _submit(case_client, requester, QUALITY_CASE)

    # 申请人自己：本部门发起的申请不计入"待我审批"
    assert _stats(case_client, requester)["pending_for_me"] == 0
    # 职能部门侧：能看到这条待办
    approver = _session(case_client, APPROVER, dept="quality")
    assert _stats(case_client, approver)["pending_for_me"] == 1
