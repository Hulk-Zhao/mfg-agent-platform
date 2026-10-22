"""R9 行级待办可见性（D13）：持槽 ∧ 行归属。

守四件事：
1. ``owner_scope`` 落库/读回/迁移（老库加列默认空 = 既有可见性不变）；
2. 同岗位、不同部门/厂区的两名用户，看到不同的行（PRD 验收）；
3. 行归属不匹配 → ``hidden``；空 scope（存量）→ 仍按槽位判定；
4. HTTP 待办队列按**权威令牌身份**执行行级裁决（不是只看角色）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mfg_agents.common.platform_schema import ensure_platform_schema_sqlite
from mfg_agents.v2.services import workflow_scope as ws
from mfg_agents.v2.services.workflow import WorkflowEngine

_ACTIONS = [{
    "key": "close", "label": "关闭 8D", "propose": ["质量总监"],
    "cosign": ["PQE"], "impact": "结案", "followup": "归档",
}]


@pytest.fixture()
def engine(tmp_path):
    url = "sqlite:///" + str(tmp_path / "wf.db").replace("\\", "/")
    return WorkflowEngine(url)


def _make(engine, owner_scope=None) -> object:
    return engine.create_task(
        wf_id="hitl_d8_close", title="8D 关闭确认",
        context={"slot": "d8_close", "actions": _ACTIONS},
        assignees=["质量总监", "PQE"], priority="P1", hitl=True,
        owner_scope=owner_scope)


def _ctx(user_id: str, dept: str = "", site: str = "") -> dict:
    scope = {"user_id": user_id, "roles": ["qa_manager"]}
    if dept:
        scope["dept"] = dept
    if site:
        scope["site"] = site
    return scope


# ---------------------------------------------------------------- 数据模型


def test_owner_scope_roundtrip_and_normalization(engine):
    task = _make(engine, {"roles": ["qa_manager", "quality", "qa_manager"],
                          "dept": "质量部 A", "assignees": "忽略"})
    loaded = engine.get(task.task_uid)
    assert loaded.owner_scope == {"roles": ["qa_manager", "quality"],
                                  "dept": "质量部 A"}
    assert loaded.to_dict()["owner_scope"]["dept"] == "质量部 A"


def test_owner_scope_migration_on_legacy_db(tmp_path):
    """老库加列：不 DROP 重建，存量行 owner_scope 为空域（既有可见性不变）。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE platform_workflow_task ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, task_uid TEXT NOT NULL UNIQUE,"
        " wf_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',"
        " priority TEXT NOT NULL DEFAULT 'P1', assignees TEXT DEFAULT '',"
        " context_json TEXT DEFAULT '{}', hitl_prompt TEXT DEFAULT '',"
        " history_json TEXT DEFAULT '[]', governance_json TEXT DEFAULT '{}',"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sla_due_at TEXT)")
    conn.execute(
        "INSERT INTO platform_workflow_task "
        "(task_uid, wf_id, title, status, created_at, updated_at) "
        "VALUES ('wf-old', 'legacy', '老工单', 'waiting_hitl', 'x', 'x')")
    conn.commit()
    conn.close()

    ensure_platform_schema_sqlite("sqlite:///" + str(db).replace("\\", "/"))
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(platform_workflow_task)")}
    raw = conn.execute(
        "SELECT owner_scope FROM platform_workflow_task WHERE task_uid='wf-old'"
    ).fetchone()[0]
    conn.close()
    assert "owner_scope" in cols
    assert raw in ("{}", "", None)  # 默认空域，不伪造归属


# ---------------------------------------------------------------- 判据层


def test_same_role_different_dept_sees_different_rows(engine):
    """PRD 验收：同岗位两名不同数据范围的用户，可见行数不同。"""
    task_a = _make(engine, {"roles": ["qa_manager"], "dept": "质量部 A"})
    task_b = _make(engine, {"roles": ["qa_manager"], "dept": "质量部 B"})
    legacy = _make(engine)  # 存量无归属 → 两边都能看到

    a = _ctx("u-a", dept="质量部 A")
    b = _ctx("u-b", dept="质量部 B")
    tasks = [task_a, task_b, legacy]

    pairs_a = ws.visible_tasks(engine, tasks, ["qa_manager"], user_ctx=a)
    pairs_b = ws.visible_tasks(engine, tasks, ["qa_manager"], user_ctx=b)
    uids_a = [t.task_uid for t, _ in pairs_a]
    uids_b = [t.task_uid for t, _ in pairs_b]

    assert task_a.task_uid in uids_a and task_b.task_uid not in uids_a
    assert task_b.task_uid in uids_b and task_a.task_uid not in uids_b
    assert legacy.task_uid in uids_a and legacy.task_uid in uids_b
    assert len(uids_a) != len(uids_b) or task_a.task_uid != task_b.task_uid


def test_owner_user_is_exact_match(engine):
    task = _make(engine, {"user_id": "u-owner", "roles": ["qa_manager"]})
    assert ws.classify(engine, task, ["qa_manager"],
                       user_ctx=_ctx("u-owner")) != ws.VIS_HIDDEN
    assert ws.classify(engine, task, ["qa_manager"],
                       user_ctx=_ctx("u-other")) == ws.VIS_HIDDEN


def test_site_scope_mismatch_is_hidden(engine):
    task = _make(engine, {"roles": ["qa_manager"], "site": "华东厂"})
    assert ws.classify(engine, task, ["qa_manager"],
                       user_ctx=_ctx("u-a", site="华东厂")) != ws.VIS_HIDDEN
    assert ws.classify(engine, task, ["qa_manager"],
                       user_ctx=_ctx("u-b", site="华南厂")) == ws.VIS_HIDDEN


def test_empty_scope_keeps_legacy_visibility(engine):
    task = _make(engine)
    assert task.owner_scope == {}
    assert ws.classify(engine, task, ["qa_manager"],
                       user_ctx=_ctx("u-a", dept="质量部 A")) != ws.VIS_HIDDEN
    assert ws.classify(engine, task, ["qa_manager"],
                       user_ctx=_ctx("u-b", dept="质量部 B")) != ws.VIS_HIDDEN


def test_missing_user_ctx_does_not_tighten_legacy_callers(engine):
    """服务层老调用方只传 role_names —— 不传身份就不做行级收紧。"""
    task = _make(engine, {"roles": ["qa_manager"], "dept": "质量部 A"})
    assert ws.classify(engine, task, ["qa_manager"]) != ws.VIS_HIDDEN


def test_owner_scope_from_assignees_ignores_free_text(engine):
    scope = ws.owner_scope_from_assignees(
        engine.permission_registry, ["质量总监", "行政专员", "看不懂的岗位"])
    assert scope.get("roles"), "至少应归一化出能识别的角色"
    assert all(isinstance(r, str) and r for r in scope["roles"])
    assert ws.owner_scope_from_assignees(
        engine.permission_registry, ["看不懂的岗位"]) == {}


# ---------------------------------------------------------------- HTTP 通道


def test_pending_endpoint_applies_owner_scope(client_factory, monkeypatch, tmp_path):
    """待办队列必须按令牌里的 user_id/dept 做行级裁决。"""
    import mfg_agents.v2.demo.api as api

    url = "sqlite:///" + str(tmp_path / "api_wf.db").replace("\\", "/")
    engine = WorkflowEngine(url)
    task_a = _make(engine, {"roles": ["qa_manager"], "dept": "质量部 A"})
    task_b = _make(engine, {"roles": ["qa_manager"], "dept": "质量部 B"})
    monkeypatch.setattr(api, "_workflow_engine", lambda: engine)

    client = client_factory()
    def _login(user_id: str, dept: str) -> dict:
        resp = client.post("/api/v2/auth/login", json={
            "roles": ["qa_manager"], "user_id": user_id,
            "name": user_id, "dept": dept})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['data']['token']}"}

    data_a = client.get("/api/v2/workflow/pending",
                        headers=_login("u-a", "质量部 A")).json()["data"]
    data_b = client.get("/api/v2/workflow/pending",
                        headers=_login("u-b", "质量部 B")).json()["data"]
    uids_a = {t["task_uid"] for t in data_a}
    uids_b = {t["task_uid"] for t in data_b}

    assert task_a.task_uid in uids_a and task_b.task_uid not in uids_a
    assert task_b.task_uid in uids_b and task_a.task_uid not in uids_b


@pytest.fixture()
def client_factory():
    from mfg_agents.v2.demo.api import app

    clients: list[TestClient] = []

    def make() -> TestClient:
        c = TestClient(app)
        clients.append(c)
        return c

    yield make
