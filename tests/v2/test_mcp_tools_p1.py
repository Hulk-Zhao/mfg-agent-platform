"""P1 工具层落地测试：7 个新工具（37 个工具 / 多 server 分治）。

覆盖点：
- 各域归组：键恰为 data/asset/quality/spc/workflow/knowledge/office、各域工具数与归属正确（含 P1 增量 7 个、办公助手域 10 个、SPC 域 3 个）、展平 == 37、单域 ≤ 15；
- 7 个新工具的 tier/readonly 标记（quality.8d 为 tier=1/readonly=False，其余 tier=0/readonly=True）；
- equipment.predict：固定输入 → 确定性打分；输出"草稿/未确认"标记；无数据诚实降级不编造分数；
- quality.8d：匿名 403 且零工单；合法身份生成 8D 草稿；关闭会签只发起（工单可被 workflow.pending 查到）；
- supply.risk / schedule.what_if / metric.dict / scenario.explain：结构化返回 + 缺口/未找到降级；
- doc.extract：文本规则抽取带 source_span；仅图片显式降级。
"""
from __future__ import annotations

import math

import pytest

from mfg_agents.v2.mcp_tools import ToolRegistry
from mfg_agents.v2.mcp_tools.tools import (
    DOMAIN_TOOLSETS,
    _equipment_risk_score,
    build_domain_toolsets,
    build_platform_tools,
    register_domain_toolsets,
    register_platform_tools,
)
from mfg_agents.v2.services.workflow import PermissionDenied

_DOMAINS = {"data", "asset", "quality", "spc", "workflow", "knowledge", "office"}

# P1 增量 7 个工具（名字一字不差）
_P1_NEW = {
    "equipment.predict", "quality.8d", "supply.risk", "schedule.what_if",
    "doc.extract", "metric.dict", "scenario.explain",
}
_READONLY_TIER0 = {n for n in _P1_NEW if n != "quality.8d"}


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


@pytest.fixture(scope="module")
def registry(orch) -> ToolRegistry:
    return register_platform_tools(ToolRegistry(), orch)


# ============================================================
# 1. 五 server 归组（P0 16 + P1 7 = 23）
# ============================================================


def test_domain_toolsets_split_p1(orch):
    """键恰为各域；各域工具数/归属正确；展平全集 == 37；每域 ≤ 15。"""
    toolsets = build_domain_toolsets(orch)
    assert set(toolsets) == _DOMAINS

    expect = {
        "data": ["platform.describe", "data.list_queries", "data.run_query",
                 "data.metric", "data.sql_readonly", "metric.dict",
                 "schedule.what_if"],
        "asset": ["asset.status", "inventory.atp", "equipment.predict",
                  "supply.risk"],
        "quality": ["quality.trace", "quality.8d"],
        "spc": ["spc.capability", "spc.pareto", "spc.ingest"],
        "workflow": ["scenario.list", "scenario.run", "scenario.explain",
                     "workflow.pending", "workflow.act", "workflow.governance",
                     "workflow.create"],
        "knowledge": ["knowledge.search", "report.draft", "doc.extract", "spc.report"],
        "office": ["office.list_rooms", "office.find_available_rooms",
                   "office.book_room", "office.cancel_booking",
                   "office.my_bookings", "office.list_restaurants",
                   "office.menu", "office.place_order", "office.cancel_order",
                   "office.my_orders"],
    }
    for domain, names in expect.items():
        assert [t.name for t in toolsets[domain]] == names, domain
        assert len(toolsets[domain]) <= 15, f"{domain} 超过 15 个工具"

    flat = {t.name for domain in toolsets.values() for t in domain}
    assert flat == {t.name for t in build_platform_tools(orch)}
    assert len(flat) == 37 == len(build_platform_tools(orch))
    # 各域工具数：7 / 4 / 2 / 3 / 7 / 4 / 10
    assert {d: len(toolsets[d]) for d in toolsets} == {
        "data": 7, "asset": 4, "quality": 2, "spc": 3, "workflow": 7,
        "knowledge": 4, "office": 10}
    # P1 增量 7 个全部落地且归组
    assert _P1_NEW <= flat


def test_register_domain_toolsets_p1(orch):
    regs = register_domain_toolsets(orch)
    assert set(regs) == _DOMAINS
    assert {t.name for t in regs["asset"].list()} == {
        "asset.status", "inventory.atp", "equipment.predict", "supply.risk"}
    assert {t.name for t in regs["quality"].list()} == {
        "quality.trace", "quality.8d"}
    assert {t.name for t in regs["knowledge"].list()} == {
        "knowledge.search", "report.draft", "doc.extract", "spc.report"}


# ============================================================
# 2. P1 新工具 tier / readonly 标记
# ============================================================


def test_p1_new_tools_tier_and_readonly(registry):
    by_name = {t.name: t for t in registry.list()}
    for name in sorted(_P1_NEW):
        assert name in by_name, f"P1 工具未注册: {name}"
    # 只读自动放行（tier=0, readonly=True）
    for name in _READONLY_TIER0:
        assert by_name[name].tier == 0, name
        assert by_name[name].readonly is True, name
    # 写类只发起（tier=1, readonly=False）
    assert by_name["quality.8d"].tier == 1
    assert by_name["quality.8d"].readonly is False


# ============================================================
# 3. equipment.predict：确定性打分 + 草稿标记 + 诚实降级
# ============================================================


def _row(is_abnormal: int, alarm: str, value: str) -> dict:
    return {"is_abnormal": is_abnormal, "alarm_code": alarm,
            "param_name": "P1", "param_value": value, "log_time": ""}


def test_equipment_predict_scoring_is_deterministic():
    """给定固定 rows → 固定 risk_score/risk_level（规则型，无 ML）。"""
    # 高分：全异常 + 近期告警满 + 参数上升 → 60 + 25 + 15 = 100（高）
    high_rows = [_row(1, f"E{i}", str(10 - i)) for i in range(5)]
    high = _equipment_risk_score(high_rows)
    assert high["risk_score"] == 100 and high["risk_level"] == "高"
    assert high["risk_score"] == _equipment_risk_score(high_rows)["risk_score"]

    # 中分：异常占比 0.5 + 近期告警 1.0 + 无趋势 → 30 + 25 + 0 = 55（中）
    mid_rows = [_row(1 if i < 5 else 0, "E" if i < 5 else "", "10")
                for i in range(10)]
    mid = _equipment_risk_score(mid_rows)
    assert mid["risk_score"] == 55 and mid["risk_level"] == "中"

    # 低分：无异常 + 无告警 + 无趋势 → 0（低）
    low_rows = [_row(0, "", "10") for _ in range(4)]
    low = _equipment_risk_score(low_rows)
    assert low["risk_score"] == 0 and low["risk_level"] == "低"

    # 空行不打分（由上层判数据缺口）
    empty = _equipment_risk_score([])
    assert empty["risk_score"] is None and empty["risk_level"] is None


def test_equipment_predict_tool_draft_and_degrade(registry):
    ctx = {"user_id": "u-eq", "roles": ["equipment_manager"]}
    res = registry.call("equipment.predict", {"equipment_id": "PRT-01"},
                        user_ctx=ctx)
    assert res["is_draft"] is True and res["confirmed"] is False
    assert res["risk_score"] is not None and res["risk_level"] in {"高", "中", "低"}
    draft = res["maintenance_draft"]
    assert draft["is_draft"] is True and draft["confirmed"] is False
    assert "草稿" in draft["note"] or "未确认" in draft["note"]
    assert draft["suggested_actions"]  # 有据可依的建议动作

    # 设备不存在 → 诚实降级，risk_score 置 null（不编造分数）
    miss = registry.call("equipment.predict", {"equipment_id": "NOPE-99"},
                         user_ctx=ctx)
    assert miss["data_gap"] is True
    assert miss["risk_score"] is None and miss["risk_level"] is None
    assert miss["maintenance_draft"] is None
    assert miss["is_draft"] is True and miss["confirmed"] is False

    # 无表级权限（空 roles）→ 审核拦截即数据缺口，仍不编造分数
    denied = registry.call("equipment.predict", {"equipment_id": "PRT-01"},
                           user_ctx={"user_id": "u-x", "roles": []})
    assert denied["data_gap"] is True and denied["risk_score"] is None


# ============================================================
# 4. quality.8d：匿名 403 零工单 + 8D 草稿 + 会签链
# ============================================================


def test_quality_8d_anonymous_denied_zero_tasks(registry, orch):
    wf = orch.runtime.workflow
    before = len(wf.list_tasks())
    with pytest.raises(PermissionDenied):
        registry.call("quality.8d", {"batch_no": "MAT-CAP-1013"}, user_ctx={})
    # 缺 user_id（仅有 roles）同样拒绝
    with pytest.raises(PermissionDenied):
        registry.call("quality.8d", {"batch_no": "MAT-CAP-1013"},
                      user_ctx={"roles": ["quality"]})
    assert len(wf.list_tasks()) == before, "拒绝路径不得产生任何工单"


def test_quality_8d_draft_schema_and_signoff_task(registry, orch):
    wf = orch.runtime.workflow
    res = registry.call(
        "quality.8d",
        {"batch_no": "MAT-CAP-1013", "request_signoff": True,
         "fields": {"D1": "跨部门小组（质量/工程/供应商）"}},
        user_ctx={"user_id": "u-qd", "name": "质量总监", "roles": ["quality"]})
    assert res["is_draft"] is True
    assert res["requires_signoff"] is True
    # 8D 结构齐全（D1–D8）
    assert set(res["stages"]) == {"D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"}
    for code, stage in res["stages"].items():
        assert set(stage) == {"name", "content", "status", "provenance"}, code
    # 用户提供 → 已填且来源 user_fields
    assert res["stages"]["D1"]["provenance"] == "user_fields"
    assert res["stages"]["D1"]["status"] == "已填"
    # 批次取证 → D2/D4 已填；其余无据 → 待补（绝不编造根因）
    assert res["stages"]["D2"]["provenance"] == "batch_trace"
    assert res["stages"]["D4"]["status"] == "已填"
    assert res["stages"]["D5"]["status"] == "待补"
    assert res["stages"]["D5"]["content"] == "待补"

    # 关闭会签：只发起一张工单，已纳入治理链（slot=d8_close + actions）
    st = res["signoff_task"]
    assert st is not None and st["task_uid"].startswith("wf-")
    assert st["wf_id"] == "hitl_d8_close" and st["slot"] == "d8_close"
    assert st["is_governed"] is True

    # 工单可被 workflow.pending 查到
    pending = registry.call("workflow.pending", {},
                            user_ctx={"user_id": "u-qd", "roles": []})
    assert st["task_uid"] in {t["task_uid"] for t in pending["tasks"]}

    # ⚠️ 事实说明（不掩盖）：按既有设计，`required_signoffs` 在建单时为空，
    # 必须在 select_action 选定动作后按该动作的 cosign 列表生成。
    assert st["signoff_status"]["total"] == 0
    task = wf.select_action(
        st["task_uid"], "close", actor="质量总监",
        user_ctx={"user_id": "u-qd", "name": "质量总监",
                  "roles": ["quality_director"]},
        comment="措施已验证通过，申请关闭")
    status = task.signoff_status()
    # close 动作 cosign = PQE / SQE / 工程 → 三方会签，会签链真正可用
    assert status["total"] == 3
    assert {r["role"] for r in status["required"]} == {
        "pqe", "sqe", "rd_engineering"}


# ============================================================
# 5. supply.risk / schedule.what_if / metric.dict / scenario.explain
# ============================================================


def test_supply_risk_structured_and_gap(registry):
    res = registry.call("supply.risk", {"part_no": "CAP-0805-22uF"},
                        user_ctx={"user_id": "u-p", "roles": ["quality"]})
    assert res["risk_level"] == "高"  # 该料号存在 incoming_result=fail
    assert res["fail_count"] >= 1
    assert res["basis_rows"] and all("batch_no" in r for r in res["basis_rows"])

    # 未命中 → 明确数据缺口，不编造风险
    miss = registry.call("supply.risk", {"part_no": "NOPE-999"},
                         user_ctx={"user_id": "u-p", "roles": ["quality"]})
    assert miss["data_gap"] is True and miss["risk_level"] is None
    assert miss["basis_rows"] == []


def test_schedule_what_if_deterministic(registry):
    res = registry.call("schedule.what_if",
                        {"model": "A型号", "demand_qty": 5000},
                        user_ctx={"user_id": "u-g", "roles": ["gm"]})
    assert res["available_source"] == "atp"
    assert res["available_qty"] == 400.0   # A型号 ATP = 1200 - 800
    assert res["gap_qty"] == 4600.0
    assert isinstance(res["estimated_days"], int)
    assert res["estimated_days"] == math.ceil(4600.0 / res["daily_output_avg"])
    assert res["feasible"] is True
    assert res["data_gap"] is False


def test_schedule_what_if_missing_daily_output_degrades(registry):
    """降级路径：日均产出缺失 → estimated_days 为 null + 原因。"""
    res = registry.call(
        "schedule.what_if",
        {"model": "Z型号", "demand_qty": 100, "available_qty": 0},
        user_ctx={"user_id": "u-g", "roles": ["gm"]})
    assert res["gap_qty"] == 100.0
    assert res["estimated_days"] is None
    assert "日均产出" in res["estimated_days_reason"]
    assert res["data_gap"] is True  # 产出缺失 → 显式缺口


def test_metric_dict(registry):
    all_ = registry.call("metric.dict", {}, user_ctx={})
    assert all_["found"] is True
    assert all_["count"] == len(all_["metrics"]) >= 1
    ids = {m["id"] for m in all_["metrics"]}
    assert "otd_rate" in ids

    one = registry.call("metric.dict", {"metric_id": "otd_rate"}, user_ctx={})
    assert one["found"] is True and one["metric"]["id"] == "otd_rate"
    assert one["metric"]["formula"]           # 口径公式（yaml 的 sql）
    assert one["metric"]["description"]

    # 未找到 → 明示，不编造
    miss = registry.call("metric.dict", {"metric_id": "no_such_metric"},
                         user_ctx={})
    assert miss["found"] is False and miss["metric"] is None
    assert "未找到" in miss["note"]


def test_scenario_explain(registry):
    all_ = registry.call("scenario.explain", {}, user_ctx={})
    assert all_["found"] is True and all_["count"] >= 1
    exp = all_["scenarios"][0]
    for key in ("key", "name", "version", "domains", "entry", "entry_hint",
                "capabilities", "outputs", "trigger_examples", "hitl_slots",
                "related_tools"):
        assert key in exp, key
    assert exp["related_tools"] == []  # registry 未声明映射，留空不臆造

    one = registry.call("scenario.explain", {"scenario_key": "line_stop_v2"},
                        user_ctx={})
    assert one["found"] is True and one["scenario"]["key"] == "line_stop_v2"
    assert one["scenario"]["hitl_slots"]      # 复线场景声明了 HITL 槽位

    miss = registry.call("scenario.explain", {"scenario_key": "no_such"},
                         user_ctx={})
    assert miss["found"] is False and miss["scenario"] is None
    assert "未找到" in miss["note"]


# ============================================================
# 6. doc.extract：文本规则抽取 + 仅图片降级
# ============================================================


def test_doc_extract_fields_with_source_span(registry):
    text = ("批次号：MAT-CAP-1013 供应商：三星电机 日期：2026-10-09 "
            "数量：500 检验结论：不合格")
    res = registry.call("doc.extract", {"text": text}, user_ctx={})
    assert res["degraded"] is False
    for field in ("批次号", "供应商", "日期", "数量", "检验结论"):
        item = res["fields"][field]
        assert item["value"] is not None, field
        assert isinstance(item["source_span"], list)
        assert len(item["source_span"]) == 2 and item["source_span"][0] < item["source_span"][1]
    assert res["fields"]["检验结论"]["value"] == "不合格"
    assert res["extracted_count"] == 5


def test_doc_extract_missing_field_left_blank(registry):
    res = registry.call("doc.extract",
                        {"text": "批次号：X-123", "fields": ["批次号", "供应商"]},
                        user_ctx={})
    assert res["fields"]["批次号"]["value"] == "X-123"
    assert res["fields"]["供应商"]["value"] is None
    assert res["fields"]["供应商"]["status"] == "未提取"


def test_doc_extract_image_only_degrades(registry):
    res = registry.call("doc.extract", {"image_hint": "现场为拍照件，无文本"},
                        user_ctx={})
    assert res["degraded"] is True
    assert "多模态" in res["note"]
    assert res["extracted_count"] == 0
    assert all(v["value"] is None for v in res["fields"].values())
