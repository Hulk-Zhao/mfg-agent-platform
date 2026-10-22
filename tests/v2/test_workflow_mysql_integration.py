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
