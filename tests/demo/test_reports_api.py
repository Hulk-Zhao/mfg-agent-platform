"""报告中心 API 契约测试（TestClient + 隔离报告库 + 隔离治理库）。

覆盖：无令牌 fail-closed / 就地生成 / 列表筛选 / 两层可见性 /
审定治理链（发起 → 驳回必填理由 → 会签未齐禁止终批）/ 派发三重门控 / 导出。

库隔离：``report_store.resolve_report_db`` 被 monkeypatch 到临时文件，
``api._workflow_engine`` 被替换为临时 SQLite 引擎——**绝不触碰演示库**。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from mfg_agents.v2.services import report_store as store
from mfg_agents.v2.services.auth import issue_token
from mfg_agents.v2.services.workflow import WorkflowEngine


# ---------- 夹具 ----------


@pytest.fixture
def reports_db(tmp_path):
    return tmp_path / "reports.db"


@pytest.fixture
def engine(tmp_path):
    return WorkflowEngine(f"sqlite:///{tmp_path / 'platform.db'}")


@pytest.fixture
def client(reports_db, engine, monkeypatch) -> TestClient:
    from mfg_agents.v2.demo import api as api_module

    monkeypatch.setattr(store, "resolve_report_db",
                        lambda db_path=None: str(reports_db))
    monkeypatch.setattr(api_module, "_workflow_engine", lambda: engine)
    return TestClient(api_module.app)


def _auth(roles: list[str], *, user_id: str = "u-test", name: str = "演示用户"
          ) -> dict[str, str]:
    token, _ = issue_token(user_id=user_id, name=name, dept="", roles=roles)
    return {"Authorization": f"Bearer {token}"}


BIZ = _auth(["business"], user_id="u-biz", name="业务经理")
GM = _auth(["gm"], user_id="u-gm", name="总经理")
RD = _auth(["rd_engineering"], user_id="u-rd", name="研发工程师")


def _gen(client: TestClient, headers: dict[str, str], **ctx: Any):
    return client.post("/api/v2/reports/generate",
                       json={"source_module": "manual", "context": ctx},
                       headers=headers)


# ---------- 鉴权 ----------


def test_read_endpoints_require_token(client):
    for path in ("/api/v2/reports", "/api/v2/reports/stats"):
        assert client.get(path).status_code == 401, path


def test_unknown_report_is_404(client):
    assert client.get("/api/v2/reports/rpt-nope", headers=BIZ).status_code == 404


# ---------- 生成 + 列表 ----------


def test_generate_then_list_and_filter(client):
    resp = _gen(client, BIZ, title="预警处置报告 · 缺料 M7020",
                summary="缺料影响 SMT-1 排产", refs=["alert-1"], risk_level="B")
    assert resp.status_code == 200, resp.text
    report = resp.json()["data"]
    assert report["status"] == "draft"
    assert report["risk_level"] == "B"
    assert report["source_module"] == "manual"

    listed = client.get("/api/v2/reports", headers=BIZ).json()["data"]
    assert listed["total"] == 1
    assert listed["items"][0]["report_id"] == report["report_id"]

    hit = client.get("/api/v2/reports", params={"q": "缺料"}, headers=BIZ).json()["data"]
    assert hit["total"] == 1
    miss = client.get("/api/v2/reports", params={"q": "不存在的关键词"},
                      headers=BIZ).json()["data"]
    assert miss["total"] == 0

    stats = client.get("/api/v2/reports/stats", headers=BIZ).json()["data"]
    assert stats["total"] == 1 and stats["draft"] == 1


def test_generate_requires_title(client):
    resp = _gen(client, BIZ, summary="没有标题")
    assert resp.status_code == 400


def test_generate_from_task_uses_real_task_facts(client, engine):
    task = engine.create_task(
        wf_id="impact_assessment", title="停线影响评估工单",
        context={"run_id": "run-x", "line_code": "SMT-1"}, assignees=["计划"],
        priority="P1", hitl=True, hitl_prompt="评估停线影响")
    resp = client.post("/api/v2/reports/generate",
                       json={"source_module": "task",
                             "context": {"title": "工单处置报告", "task_uid": task.task_uid}},
                       headers=BIZ)
    assert resp.status_code == 200, resp.text
    report = resp.json()["data"]
    # 工单事实由后端补，不由前端编造
    assert report["internal_brief"]["工单"].startswith("停线影响评估工单")
    assert report["internal_brief"]["优先级"] == "P1"
    assert [t["task_uid"] for t in report["linked"]["tasks"]] == [task.task_uid]


# ---------- 两层可见性 ----------


def test_internal_report_is_404_for_non_internal_role(client):
    rid = _gen(client, BIZ, title="内部专报").json()["data"]["report_id"]
    assert client.get(f"/api/v2/reports/{rid}", headers=RD).status_code == 404
    assert client.get(f"/api/v2/reports/{rid}", headers=BIZ).status_code == 200


def test_customer_report_masks_internal_brief_for_non_internal_role(client):
    rid = _gen(client, BIZ, title="对客报告", visibility="customer",
               customer_report="尊敬的客户：……").json()["data"]["report_id"]
    as_rd = client.get(f"/api/v2/reports/{rid}", headers=RD).json()["data"]
    assert as_rd["internal_hidden"] is True
    assert as_rd["internal_brief"] == {}
    assert as_rd["customer_report"].startswith("尊敬的客户")


# ---------- 审定治理链 ----------


def test_review_submit_then_reject_requires_reason(client):
    rid = _gen(client, BIZ, title="审定链路报告").json()["data"]["report_id"]

    submitted = client.post(f"/api/v2/reports/{rid}/review",
                            json={"action": "submit", "comment": "结论已复核"},
                            headers=BIZ)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()["data"]
    assert body["report"]["status"] == "pending_review"
    assert body["task"]["governance"]["slot"] == "report_publish"
    # 会签要求已按所选动作生成（缺一不可）
    assert body["task"]["signoff_status"]["progress"].startswith("0/")

    # 驳回不填理由 → 400，且状态不变
    no_reason = client.post(f"/api/v2/reports/{rid}/review",
                            json={"action": "reject", "comment": ""}, headers=BIZ)
    assert no_reason.status_code == 400
    assert client.get(f"/api/v2/reports/{rid}", headers=BIZ).json()["data"]["status"] \
        == "pending_review"

    rejected = client.post(f"/api/v2/reports/{rid}/review",
                           json={"action": "reject", "comment": "数据缺口未闭合"},
                           headers=BIZ)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["report"]["status"] == "rejected"


def test_review_approve_blocked_until_cosign_complete(client):
    rid = _gen(client, BIZ, title="终批门控报告").json()["data"]["report_id"]
    client.post(f"/api/v2/reports/{rid}/review",
                json={"action": "submit", "comment": "发起"}, headers=BIZ)

    # 会签未齐 → 409（不是静默放行）
    resp = client.post(f"/api/v2/reports/{rid}/review",
                       json={"action": "approve", "comment": "同意"}, headers=GM)
    assert resp.status_code == 409, resp.text


def test_review_submit_requires_propose_permission(client):
    rid = _gen(client, BIZ, title="权限门控报告").json()["data"]["report_id"]
    # 研发工程师不持有 hitl.report_publish.propose → 403
    resp = client.post(f"/api/v2/reports/{rid}/review",
                       json={"action": "submit", "comment": "越权发起"}, headers=RD)
    assert resp.status_code == 403


# ---------- 派发三重门控 ----------


def test_dispatch_requires_approved_status(client, reports_db):
    rid = _gen(client, BIZ, title="派发门控报告", refs=["ref-1"]).json()["data"]["report_id"]
    # 未审定即派发 → 409
    early = client.post(f"/api/v2/reports/{rid}/dispatch",
                        json={"comment": "直接派发"}, headers=GM)
    assert early.status_code == 409

    store.transition_status(rid, "pending_review", actor="业务经理",
                            note="提交审定", db_path=reports_db)
    store.transition_status(rid, "approved", actor="业务经理",
                            note="审定通过", db_path=reports_db)

    # 意见必填
    no_comment = client.post(f"/api/v2/reports/{rid}/dispatch",
                             json={"comment": ""}, headers=GM)
    assert no_comment.status_code == 400

    ok = client.post(f"/api/v2/reports/{rid}/dispatch",
                     json={"comment": "派发给客户 A，口径与 8D 一致"}, headers=GM)
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["status"] == "dispatched"


def test_dispatch_requires_approve_permission(client, reports_db):
    rid = _gen(client, BIZ, title="派发权限报告", refs=["ref-1"]).json()["data"]["report_id"]
    store.transition_status(rid, "pending_review", actor="x", note="", db_path=reports_db)
    store.transition_status(rid, "approved", actor="x", note="", db_path=reports_db)
    resp = client.post(f"/api/v2/reports/{rid}/dispatch",
                       json={"comment": "越权派发"}, headers=BIZ)
    assert resp.status_code == 403


def test_dispatch_rejects_report_without_provenance(client, reports_db):
    rid = _gen(client, BIZ, title="无溯源报告").json()["data"]["report_id"]
    report = store.get_report(rid, reports_db)
    report["provenance"] = []
    store.upsert_report(report, reports_db)
    store.transition_status(rid, "pending_review", actor="x", note="", db_path=reports_db)
    store.transition_status(rid, "approved", actor="x", note="", db_path=reports_db)
    resp = client.post(f"/api/v2/reports/{rid}/dispatch",
                       json={"comment": "无溯源也派发"}, headers=GM)
    assert resp.status_code == 400


# ---------- 归档幂等 + 导出 ----------


def test_archive_run_result_is_idempotent(client, reports_db):
    from mfg_agents.v2.services import report_service

    result = {
        "run_id": "run-abc", "scenario_key": "line_stop_v2", "query": "A型号停线",
        "report": "对客口径……", "internal_brief": {"停线确认": "是"},
        "provenance": [{"domain": "fa", "note": "根因"}],
        "route": {"scenario": "line_stop_v2"}, "degraded": False,
    }
    first = report_service.archive_run_result(result, actor="业务经理")
    second = report_service.archive_run_result(result, actor="业务经理")
    assert first == second == "rpt-run-abc"
    _, total = store.list_reports(db_path=reports_db)
    assert total == 1


def test_archive_ignores_result_without_report_content(client, reports_db):
    from mfg_agents.v2.services import report_service

    assert report_service.archive_run_result({"run_id": "run-empty"}) is None
    _, total = store.list_reports(db_path=reports_db)
    assert total == 0


def test_export_md_and_html(client):
    rid = _gen(client, BIZ, title="导出测试报告", summary="摘要一行",
               refs=["ref-1"]).json()["data"]["report_id"]

    md = client.get(f"/api/v2/reports/{rid}/export", params={"fmt": "md"},
                    headers=BIZ)
    assert md.status_code == 200
    assert "导出测试报告" in md.text
    assert "数据溯源" in md.text

    html = client.get(f"/api/v2/reports/{rid}/export", params={"fmt": "html"},
                      headers=BIZ)
    assert html.status_code == 200
    assert "<!DOCTYPE html>" in html.text
