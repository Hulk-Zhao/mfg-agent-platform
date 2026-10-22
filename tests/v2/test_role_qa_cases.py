"""岗位问答案例契约（R2 可答性兜底）。

守三件事：
1. 覆盖：31 个岗位 × colleague.yaml 的每条推荐问题，一条不漏；
2. 只补不抢：真实链路完整时不得被案例覆盖，未形成完整结论时才补齐并标注案例编号；
3. 不编造：案例必须带结论与取证来源，缺口如实登记。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from mfg_agents.v2.core.orchestrator import OrchestratorResult, V2Orchestrator
from mfg_agents.v2.services.role_qa_cases import (
    RoleQACases,
    get_role_qa_cases,
    normalize_question,
)

ROOT = Path(__file__).resolve().parents[2]
COLLEAGUE = ROOT / "src/mfg_agents/v2/data/colleague.yaml"
CASES = ROOT / "src/mfg_agents/v2/data/role_qa_cases.yaml"
DEMO_DATA = ROOT / "src/mfg_agents/v2/demo/ui/demo-data.js"


def _colleagues() -> list[dict]:
    return yaml.safe_load(COLLEAGUE.read_text(encoding="utf-8"))["colleagues"]


def _cases() -> list[dict]:
    return yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]


def _role_ids() -> set[str]:
    text = DEMO_DATA.read_text(encoding="utf-8")
    return set(re.findall(r'\{ id: "([^"]+)"', text))


def test_cases_cover_every_colleague_question():
    expected = {(c["role"], normalize_question(q))
                for c in _colleagues() for q in c["questions"]}
    actual = {(c["role"], normalize_question(c["question"])) for c in _cases()}
    assert expected == actual, f"缺案例: {sorted(expected - actual)}；多案例: {sorted(actual - expected)}"


def test_case_ids_unique_and_roles_are_roles_ids():
    cases = _cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    roles = {c["role"] for c in cases}
    assert roles <= _role_ids()
    assert len(roles) == 31


def test_case_payload_shape():
    for case in _cases():
        assert case["conclusion"].strip(), case["id"]
        assert isinstance(case["facts"], list) and case["facts"], case["id"]
        assert case.get("sources") or case.get("gaps"), case["id"]
        # 「依据」里不允许出现未取证占位
        assert "TODO" not in case["conclusion"]


def test_match_normalizes_punctuation():
    cases = RoleQACases()
    # 同一句带不带问号/空格都必须命中
    assert cases.match("gm", "这个月准交率完成多少？")
    assert cases.match("gm", " 这个月准交率完成多少 ")
    # 岗位不能串：gm 的案例不能给 mfg 用
    assert cases.match("mfg", "这个月准交率完成多少") is None
    assert cases.match("", "这个月准交率完成多少") is None


def _incomplete_result(**over) -> OrchestratorResult:
    base = dict(query="今天有哪些设备告警", run_id="run-t",
                route={"strategy": "qa"}, scenario_key=None,
                report="该问题需要人工跟进", fallback="manual_ticket",
                degraded=True, degraded_reasons=["执行失败"])
    base.update(over)
    return OrchestratorResult(**base)


def test_case_fills_incomplete_result():
    orch = V2Orchestrator.__new__(V2Orchestrator)
    out = orch._apply_role_case(
        _incomplete_result(),
        "今天有哪些设备告警",
        {"user_id": "u-eq", "roles": ["equipment"], "colleague_role": "eq"},
    )
    assert out.route["strategy"] == "role_case"
    assert out.route["case_id"].startswith("RQ-")
    assert out.fallback is None and out.degraded is False
    assert out.report and "建议下一步" in out.report
    assert out.provenance and out.provenance[0]["channel"] == "compute"


def test_case_does_not_override_complete_result():
    orch = V2Orchestrator.__new__(V2Orchestrator)
    complete = OrchestratorResult(
        query="今天有哪些设备告警", run_id="run-t",
        route={"strategy": "qa"}, scenario_key=None,
        report="真实链路给出的完整结论", degraded=False,
    )
    out = orch._apply_role_case(
        complete, "今天有哪些设备告警",
        {"user_id": "u-eq", "roles": ["equipment"], "colleague_role": "eq"})
    assert out is complete
    assert out.route["strategy"] == "qa"
    assert "case_id" not in out.route


def test_no_colleague_role_no_case():
    orch = V2Orchestrator.__new__(V2Orchestrator)
    out = orch._apply_role_case(
        _incomplete_result(), "今天有哪些设备告警",
        {"user_id": "u-eq", "roles": ["equipment"]})
    assert out.route["strategy"] == "qa"
    assert out.fallback == "manual_ticket"


def test_agentic_step_exhaustion_is_treated_incomplete():
    orch = V2Orchestrator.__new__(V2Orchestrator)
    out = orch._apply_role_case(
        _incomplete_result(
            route={"strategy": "agentic"},
            report="已在最大步数内完成部分取证，未能形成完整结论",
            fallback=None, degraded=False),
        "哪些订单有履约风险",
        {"user_id": "u-gm", "roles": ["gm"], "colleague_role": "gm"})
    assert out.route["strategy"] == "role_case"


def test_chitchat_is_treated_incomplete():
    orch = V2Orchestrator.__new__(V2Orchestrator)
    out = orch._apply_role_case(
        _incomplete_result(route={"strategy": "chitchat"}, report="你好！",
                           fallback=None, degraded=False),
        "需要升级处理的问题清单",
        {"user_id": "u-pm", "roles": ["project_manager"], "colleague_role": "pm"})
    assert out.route["strategy"] == "role_case"


def test_singleton_case_catalog_loaded():
    assert len(get_role_qa_cases()) == 93
