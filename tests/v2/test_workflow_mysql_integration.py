"""MySQL 真库集成：显式注入 SQLAlchemy 仓储后的完整治理链。

默认跳过（CI 无 MySQL）；本地验证方式：

    MFG_MYSQL_TEST_URL=mysql+pymysql://root:demo123@127.0.0.1:3307/mfg_agent_test \
        uv run python -m pytest tests/v2/test_workflow_mysql_integration.py -q

测试只使用传入 URL 指向的**一次性库**，结束后清理自己创建的行。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import text

import mfg_agents.v2 as v2_pkg
from mfg_agents.v2.contracts.permissions import reset_permission_registry
from mfg_agents.v2.core.orchestrator import build_workflow_engine
from mfg_agents.v2.services import report_store, site_board
from mfg_agents.v2.services.report_repository_sqlalchemy import (
    SQLAlchemyReportRepository,
)
from mfg_agents.v2.services.site_board_repository_sqlalchemy import (
    SQLAlchemySiteBoardRepository,
)
from mfg_agents.v2.services.workflow import WorkflowEngine
from mfg_agents.v2.services.workflow_repository_sqlalchemy import (
    SQLAlchemyWorkflowRepository,
)

MYSQL_URL = os.getenv("MFG_MYSQL_TEST_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not MYSQL_URL,
    reason="需要 MFG_MYSQL_TEST_URL 指向一次性 MySQL 库（例如 mfg_agent_test）")

SCENARIO_DIR = Path(v2_pkg.__file__).resolve().parent / "scenarios"

SQE_CTX: dict[str, Any] = {"user_id": "u-sqe", "dept": "quality", "roles": ["quality"]}
PQE_CTX: dict[str, Any] = {"user_id": "u-pqe", "dept": "quality", "roles": ["pqe"]}
BIZ_CTX: dict[str, Any] = {"user_id": "u-biz", "dept": "sales", "roles": ["business"]}
QA_CTX: dict[str, Any] = {"user_id": "u-qa", "dept": "quality", "roles": ["qa"]}
QD_CTX: dict[str, Any] = {"user_id": "u-qd", "dept": "quality", "roles": ["质量总监"]}


def _actions() -> list[dict[str, Any]]:
    data = yaml.safe_load(
        (SCENARIO_DIR / "incoming_quality_v2.yaml").read_text(encoding="utf-8"))
    step = next(s for s in data["steps"] if s.get("id") == "hitl_disposition")
    return step["actions"]


def test_mysql_governed_flow_with_signoff_ledger():
    reset_permission_registry()
    repo = SQLAlchemyWorkflowRepository(MYSQL_URL)
    engine = WorkflowEngine(MYSQL_URL, repository=repo)
    task = engine.create_task(
        wf_id="hitl_disposition", title="MySQL 治理链验证",
        context={"run_id": "run-mysql", "slot": "disposition",
                 "kind": "disposition", "actions": _actions()},
        assignees=["SQE", "PQE", "生产"], hitl=True)
    uid = task.task_uid
    try:
        engine.select_action(uid, "concession", "王倩", user_ctx=SQE_CTX,
                             comment="加严检验 + 客户告知")
        engine.cosign(uid, "王倩", user_ctx=PQE_CTX)
        engine.cosign(uid, "陈昊", user_ctx=BIZ_CTX)
        engine.cosign(uid, "李静", user_ctx=QA_CTX)
        engine.approve(uid, "李静", user_ctx=QD_CTX, final=True,
                       decision="特采（让步接收）")

        assert engine.get(uid).status == "done"
        rows = repo.list_signoffs(uid)
        assert [r["verb"] for r in rows] == [
            "cosign", "cosign", "cosign", "final_approve"]
        assert {r["actor_id"] for r in rows[:3]} == {"u-pqe", "u-biz", "u-qa"}
    finally:
        with repo.engine.begin() as conn:
            conn.execute(text("DELETE FROM wf_signoff WHERE task_uid = :u"),
                         {"u": uid})
            conn.execute(text(
                "DELETE FROM platform_workflow_task WHERE task_uid = :u"),
                {"u": uid})
        repo.engine.dispose()


def test_mysql_report_and_site_board_stores_use_sqlalchemy():
    """同一 WORKFLOW_DB（MySQL）下，报告/推送仓储按方言自动分派。"""
    assert isinstance(report_store._repo(MYSQL_URL), SQLAlchemyReportRepository)
    assert isinstance(site_board._repo(MYSQL_URL), SQLAlchemySiteBoardRepository)

    report_id = "rpt-mysql-it"
    push_id = "sb-mysql-it"
    try:
        report_store.upsert_report({
            "report_id": report_id,
            "title": "MySQL 报告",
            "scenario_key": "line_stop",
            "status": "draft",
            "internal_brief": {"summary": "x"},
        }, db_path=MYSQL_URL)
        assert report_store.get_report(report_id, db_path=MYSQL_URL)["title"] == "MySQL 报告"
        rows, total = report_store.list_reports(q="MySQL 报告", db_path=MYSQL_URL)
        assert total == 1 and rows[0]["report_id"] == report_id

        site_board.push_message(
            stage="SMT", title="MySQL 推送", complaint_no="C-MYSQL",
            push_id=push_id, db_path=MYSQL_URL)
        site_board.ack(push_id, actor="张三", note="已接收", db_path=MYSQL_URL)
        pushes = site_board.list_pushes(stage="SMT", db_path=MYSQL_URL)
        assert any(p["push_id"] == push_id and p["status"] == "acked"
                   for p in pushes)
    finally:
        report_repo = SQLAlchemyReportRepository(MYSQL_URL)
        with report_repo.engine.begin() as conn:
            conn.execute(text("DELETE FROM report_record WHERE report_id = :r"),
                         {"r": report_id})
        report_repo.engine.dispose()
        board_repo = SQLAlchemySiteBoardRepository(MYSQL_URL)
        with board_repo.engine.begin() as conn:
            conn.execute(text("DELETE FROM site_board_push WHERE push_id = :p"),
                         {"p": push_id})
        board_repo.engine.dispose()


def test_mysql_runtime_factory_injects_sqlalchemy_repository():
    """运行时装配入口：显式 WORKFLOW_DB=MySQL 时自动注入仓储。"""
    engine = build_workflow_engine(MYSQL_URL)
    assert isinstance(engine.repository, SQLAlchemyWorkflowRepository)
    task = engine.create_task("wf-mysql-factory", "MySQL 装配入口")
    try:
        assert engine.get(task.task_uid).title == "MySQL 装配入口"
    finally:
        with engine.repository.engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM platform_workflow_task WHERE task_uid = :u"),
                {"u": task.task_uid})
        engine.repository.engine.dispose()
