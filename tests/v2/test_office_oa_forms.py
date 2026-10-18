"""OA 自助办理（报销 / 请假 / 补考勤）与反问式参数补全（ask_user→clarify→提交→HITL）。

覆盖点：
- 服务契约（demo 实现）：报销/请假/补卡提交与快照、参数校验与非法拒绝、
  假期余额、列表按本人过滤与倒序；
- Agent 反问：ask_user 触发 → 输出 clarify（含字段定义，供前端弹窗渲染）→
  用户补齐后再 submit → request_approval（三类申请一律 HITL，按事项分派归口）；
- 统一工具层：office 域新增 4 工具注册与 tier/readonly、my_requests 汇总、缺参 fail-closed；
- HTTP 汇总/元数据：/office/my/{actor} 含 OA 单据与余额、/office/meta 含报销/长假阈值。
"""
from __future__ import annotations

import json

import pytest

from mfg_agents.v2.services.office import DemoOfficeBackend, OfficeError
from mfg_agents.v2.agents.office import (
    _ATTENDANCE_FIX_TYPES,
    _EXPENSE_CATEGORIES,
    _LEAVE_TYPES,
    _clarify_options,
    _detect_flow,
    _enrich_clarify_fields,
    _find_amount,
    _find_dates,
    _find_enum,
    _find_time,
    _known_values,
)


# ---------- 脚本化 LLM（与 test_office_agent 同范式） ----------


class _Resp:
    """脚本化 LLM 的响应（content + tool_calls）。"""

    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _ScriptedLLM:
    """按脚本依次返回响应（最后一条重复用于收敛）。"""

    def __init__(self, script: list[_Resp]):
        self._script = list(script)
        self._i = 0

    def invoke(self, messages):  # noqa: ANN001, ARG002
        idx = min(self._i, len(self._script) - 1)
        self._i += 1
        return self._script[idx]


@pytest.fixture
def backend() -> DemoOfficeBackend:
    return DemoOfficeBackend()


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


def _final(answer: str, **extra) -> _Resp:
    payload = {"answer": answer, "data_used": [], "needs_hitl": False,
               "data_gaps": [], **extra}
    return _Resp(content=json.dumps(payload, ensure_ascii=False))


def _agent(orch, backend, script):
    from mfg_agents.v2.agents.office import OfficeAssistantAgent
    agent = OfficeAssistantAgent(orchestrator=orch, backend=backend)
    agent._llm = _ScriptedLLM(script)   # 注入脚本化 LLM（跳过网络与探测）
    return agent


# ============================================================
# 1. 服务契约（demo 实现）
# ============================================================


def test_service_submit_expense(backend):
    e = backend.submit_expense("差旅", 1200.5, "2026-10-15", "上海出差机票",
                               "张三", "INV-9")
    assert e["expense_id"].startswith("BX") and len(e["expense_id"]) == 7
    assert e["status"] == "pending_approval" and e["need_approval"] is True
    assert e["high_value"] is False and e["amount"] == 1200.5
    assert e["applicant"] == "张三" and e["invoice_no"] == "INV-9"
    # 大额阈值（≥5000）→ 标记高影响
    big = backend.submit_expense("招待", 6000, "2026-10-15", "客户接待", "张三")
    assert big["high_value"] is True


def test_service_expense_validation(backend):
    with pytest.raises(OfficeError):    # 类别非法
        backend.submit_expense("餐饮", 100, "2026-10-15", "x", "张三")
    with pytest.raises(OfficeError):    # 金额必须为正
        backend.submit_expense("差旅", 0, "2026-10-15", "x", "张三")
    with pytest.raises(OfficeError):
        backend.submit_expense("差旅", -5, "2026-10-15", "x", "张三")


def test_service_apply_leave_natural_days(backend):
    l = backend.apply_leave("年假", "2026-10-20", "2026-10-21", "张三", "陪产")
    assert l["leave_id"].startswith("QJ")
    assert l["days"] == 2.0                     # 含端点：20/21 → 2 天
    assert l["long_leave"] is False
    # 达到长假阈值（≥3 天）→ 标记长假
    three = backend.apply_leave("年假", "2026-10-20", "2026-10-22", "张三", "小长假")
    assert three["days"] == 3.0 and three["long_leave"] is True
    long_l = backend.apply_leave("事假", "2026-11-01", "2026-11-10", "张三", "长事假")
    assert long_l["long_leave"] is True and long_l["days"] == 10.0
    with pytest.raises(OfficeError):    # 假期类型非法
        backend.apply_leave("探亲假", "2026-11-01", "2026-11-02", "张三")
    with pytest.raises(OfficeError):    # 结束早于开始
        backend.apply_leave("年假", "2026-11-05", "2026-11-01", "张三")


def test_service_leave_balance(backend):
    bal = backend.leave_balance("张三")
    assert set(bal) >= {"annual_remaining", "comp_remaining", "used_this_year"}


def test_service_attendance_fix(backend):
    f = backend.submit_attendance_fix("上班补卡", "2026-10-16", "09:05",
                                      "张三", "忘打卡")
    assert f["fix_id"].startswith("KB")
    assert f["status"] == "pending_approval" and f["need_approval"] is True
    with pytest.raises(OfficeError):    # 补卡类型非法
        backend.submit_attendance_fix("中午补卡", "2026-10-16", "12:00", "张三")
    with pytest.raises(OfficeError):    # 时间格式非法
        backend.submit_attendance_fix("上班补卡", "2026-10-16", "9点", "张三")


def test_service_lists_filter_and_order(backend):
    backend.submit_expense("交通", 30, "2026-10-10", "打车", "张三")
    backend.submit_expense("交通", 40, "2026-10-11", "打车", "李四")
    zhang = backend.list_expenses("张三")
    assert [e["applicant"] for e in zhang] == ["张三"]
    assert len(backend.list_expenses()) == 2
    backend.apply_leave("调休", "2026-10-20", "2026-10-20", "张三", "调休")
    assert len(backend.list_leaves("张三")) == 1
    backend.submit_attendance_fix("下班补卡", "2026-10-16", "19:00", "张三", "加班")
    assert len(backend.list_attendance_fixes("张三")) == 1
    assert backend.list_attendance_fixes("王五") == []


# ============================================================
# 2. Agent 反问 → 补齐 → 提交 → HITL
# ============================================================


def test_agent_ask_user_returns_clarify(orch, backend):
    """关键信息缺失 → ask_user 反问：输出 clarify（含字段定义），且不落任何单据。"""
    fields = [
        {"key": "category", "label": "报销类别", "type": "select",
         "options": ["差旅", "招待", "办公用品", "交通", "其他"], "required": True},
        {"key": "amount", "label": "报销金额（元）", "type": "number", "required": True},
        {"key": "expense_date", "label": "发生日期", "type": "date", "required": True},
        {"key": "note", "label": "报销事由", "type": "text", "required": True},
    ]
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "请补充报销信息：类别、金额、发生日期与事由。",
        "fields": fields}}])]
    out = _agent(orch, backend, script).run(
        "我要报销一笔费用", user_ctx={"user_id": "u1", "name": "张三"})

    assert out["clarify"] and out["clarify"]["fields"]
    assert out["clarify"]["fields"][0]["key"] == "category"
    assert out["answer"] == "请补充报销信息：类别、金额、发生日期与事由。"
    assert out["needs_hitl"] is False
    assert out["steps"][-1]["tool"] == "ask_user" and out["steps"][-1]["ok"] is True
    assert backend.list_expenses("张三") == []       # 未补齐前不得臆造提交


def test_agent_expense_after_clarify_triggers_hitl(orch, backend):
    """补齐后再 submit → request_approval（报销归口财务专员），单据与大额标记落地。"""
    script = [
        _Resp(tool_calls=[{"name": "submit_expense", "id": "c1", "args": {
            "category": "差旅", "amount": 6800, "expense_date": "2026-10-15",
            "note": "深圳出差机票与住宿", "invoice_no": "INV-2026-1015"}}]),
        _Resp(tool_calls=[{"name": "request_approval", "id": "c2", "args": {
            "title": "报销单确认 BX00001",
            "prompt": "差旅报销 6800 元（大额）待财务核验", "category": "expense"}}]),
        _final("报销单 BX00001（差旅 6800 元）已提交，属大额，已发起人工确认，待财务专员审批。",
               needs_hitl=True, hitl_title="报销单确认 BX00001",
               hitl_prompt="差旅报销 6800 元（大额）待财务核验",
               hitl_category="expense"),
    ]
    out = _agent(orch, backend, script).run(
        "我要报销一笔费用；补充信息：报销类别=差旅；报销金额（元）=6800；"
        "发生日期=2026-10-15；报销事由=深圳出差机票与住宿",
        user_ctx={"user_id": "u1", "name": "张三"})

    assert [s["tool"] for s in out["steps"]] == ["submit_expense", "request_approval"]
    assert all(s["ok"] for s in out["steps"])
    assert out["needs_hitl"] is True and out["hitl_task"]["task_uid"]
    assert out["hitl_task"]["assignee"] == "财务专员"
    expenses = backend.list_expenses("张三")
    assert len(expenses) == 1 and expenses[0]["high_value"] is True


def test_agent_leave_assignee_is_dept_manager(orch, backend):
    """请假一律 HITL，归口部门主管；长假标记可见；工单进既有治理链。"""
    script = [
        _Resp(tool_calls=[{"name": "apply_leave", "id": "c1", "args": {
            "leave_type": "年假", "start_date": "2026-10-20",
            "end_date": "2026-10-24", "reason": "年假出行"}}]),
        _Resp(tool_calls=[{"name": "request_approval", "id": "c2", "args": {
            "title": "请假单确认 QJ00001", "prompt": "年假 5 天待主管确认",
            "category": "leave"}}]),
        _final("请假单 QJ00001（年假 5 天）已提交，已发起人工确认，待部门主管审批。",
               needs_hitl=True, hitl_title="请假单确认 QJ00001",
               hitl_prompt="年假 5 天待主管确认", hitl_category="leave"),
    ]
    out = _agent(orch, backend, script).run(
        "我要请假", user_ctx={"user_id": "u1", "name": "张三"})

    assert out["hitl_task"]["assignee"] == "部门主管"
    leaves = backend.list_leaves("张三")
    assert leaves[0]["days"] == 5.0 and leaves[0]["long_leave"] is True
    pending = orch.runtime.workflow.list_tasks()
    assert any(t.task_uid == out["hitl_task"]["task_uid"] for t in pending)


# ============================================================
# 2b. 反问防死循环 + 下拉选项兜底（回归：请假无限弹框 / 没有下拉选项）
# ============================================================


def test_agent_no_reclarify_after_supplement(orch, backend):
    """用户已补充信息（消息含"补充信息："）后，即使 LLM 再要 ask_user 也不再弹窗。"""
    script = [
        _Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
            "question": "请补充休假起止日期。",
            "fields": [{"key": "start_date", "label": "开始日期", "type": "date"}]}}]),
        _final("请假单 QJ00001 已提交，已发起人工确认，待部门主管审批。"),
    ]
    out = _agent(orch, backend, script).run(
        "我要请假；补充信息：请假类型=年假；请假事由=1",
        user_ctx={"user_id": "u1", "name": "张三"})

    assert out["clarify"] is None                       # 不再二次弹框（断环）
    assert "QJ00001" in out["answer"]


def test_ask_user_select_options_backfilled(orch, backend):
    """LLM 漏填 options 的类型类字段：后端按 key/label 兜底补全规范选项。"""
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "请补充请假类型与起止日期。",
        "fields": [
            {"key": "leave_type", "label": "请假类型", "type": "select", "required": True},
            {"key": "category", "label": "报销类别", "type": "select"},   # 不相关字段不误补
            {"key": "start_date", "label": "开始日期", "type": "date"},
        ]}}])]
    out = _agent(orch, backend, script).run(
        "我要请假", user_ctx={"user_id": "u1", "name": "张三"})

    f = {x["key"]: x for x in out["clarify"]["fields"]}
    assert f["leave_type"]["options"] == ["年假", "事假", "病假", "调休", "婚假", "产假"]
    assert f["category"]["options"] == ["差旅", "招待", "办公用品", "交通", "其他"]
    assert "options" not in f["start_date"]             # date 字段不补选项


def test_ask_user_known_values_prefilled(orch, backend):
    """已知值随 clarify 下发（弹窗预填）：能解析出的字段带 value，缺失项不带。"""
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "已知假期类型与开始日期，请补充请假事由。",
        "fields": [
            {"key": "leave_type", "label": "请假类型", "type": "select", "value": "年假"},
            {"key": "start_date", "label": "开始日期", "type": "date", "value": "2026-10-20"},
            {"key": "reason", "label": "请假事由", "type": "text"},
        ]}}])]
    out = _agent(orch, backend, script).run(
        "我要请年假，2026-10-20 开始", user_ctx={"user_id": "u1", "name": "张三"})

    f = {x["key"]: x for x in out["clarify"]["fields"]}
    assert f["leave_type"]["value"] == "年假"                 # 已知 → 预填
    assert f["start_date"]["value"] == "2026-10-20"
    assert "value" not in f["reason"]                        # 缺失 → 留空待补


def test_ask_user_known_values_enriched_from_query(orch, backend):
    """LLM 只列缺失项时，原话中已解析出的字段仍确定性并入弹窗（半填）。"""
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "请补充结束日期与事由。",
        "fields": [
            {"key": "end_date", "label": "结束日期", "type": "date"},
            {"key": "reason", "label": "请假事由", "type": "text"},
        ]}}])]
    out = _agent(orch, backend, script).run(
        "我要请年假，2026-10-20 开始", user_ctx={"user_id": "u1", "name": "张三"})

    f = {x["key"]: x for x in out["clarify"]["fields"]}
    assert f["leave_type"]["value"] == "年假"                 # 原话解析 → 并入
    assert f["leave_type"]["options"]                        # 且带规范下拉选项
    assert f["start_date"]["value"] == "2026-10-20"
    assert "value" not in f["end_date"]                      # 未解析 → 留空待补
    assert "value" not in f["reason"]


# ============================================================
# 2c. 已知值解析与弹窗预填（单元级：确定性抽取，不依赖 LLM）
# ============================================================


@pytest.mark.parametrize("text,expected", [
    ("我要报销一笔费用", "expense"),
    ("帮我提交差旅报销", "expense"),
    ("我要补考勤打卡", "attendance"),
    ("我要补卡", "attendance"),
    ("我要请年假", "leave"),
    ("想休个婚假", "leave"),
    ("帮我订个会议室", ""),
    ("中午订两份餐", ""),
])
def test_detect_flow(text, expected):
    assert _detect_flow(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("2026-10-20", ["2026-10-20"]),
    ("2026/10/5", ["2026-10-05"]),
    ("2026.1.3", ["2026-01-03"]),
    ("2026-10-20 到 2026-10-21", ["2026-10-20", "2026-10-21"]),
    ("没有日期", []),
])
def test_find_dates(text, expected):
    assert _find_dates(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("报销 1200.5 元", "1200.5"),
    ("金额 300", "300"),
    ("花费800", "800"),
    ("这里没有钱", ""),
])
def test_find_amount(text, expected):
    assert _find_amount(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("9:05", "09:05"),
    ("19:00", "19:00"),
    ("上午九点", ""),
])
def test_find_time(text, expected):
    assert _find_time(text) == expected


def test_find_enum_matches_first_option():
    assert _find_enum("我要请年假", _LEAVE_TYPES) == "年假"
    assert _find_enum("随便说说", _LEAVE_TYPES) == ""


def test_known_values_leave_type_and_dates():
    assert _known_values("leave", "我要请年假") == {"leave_type": "年假"}
    assert _known_values("leave", "2026-10-20 到 2026-10-21 请假") == {
        "start_date": "2026-10-20", "end_date": "2026-10-21"}


def test_known_values_expense_and_attendance():
    assert _known_values("expense", "我要报销差旅费 1200 元") == {
        "category": "差旅", "amount": "1200"}
    assert _known_values("attendance", "我要补卡（上班），2026-10-16 09:05") == {
        "fix_type": "上班补卡", "work_date": "2026-10-16", "time": "09:05"}


def test_clarify_options_backfill():
    assert _clarify_options("fix_type", "") == list(_ATTENDANCE_FIX_TYPES)
    assert _clarify_options("category", "") == list(_EXPENSE_CATEGORIES)
    assert _clarify_options("leave_type", "请假类型") == list(_LEAVE_TYPES)
    assert _clarify_options("note", "备注") == []


def test_enrich_no_flow_returns_unchanged():
    fields = [{"key": "x", "label": "X", "type": "text"}]
    assert _enrich_clarify_fields("帮我订个会议室", fields) == fields


def test_enrich_appends_known_fields_for_expense():
    fields = [{"key": "note", "label": "报销事由", "type": "text"}]
    out = _enrich_clarify_fields("我要报销差旅费 1200 元", fields)
    f = {x["key"]: x for x in out}
    assert f["category"]["value"] == "差旅"
    assert f["category"]["options"] == list(_EXPENSE_CATEGORIES)
    assert f["amount"]["value"] == "1200"
    assert "expense_date" not in f            # 原话无日期 → 不作为已知项并入
    assert "value" not in f["note"]


def test_enrich_keeps_llm_provided_value():
    fields = [{"key": "leave_type", "label": "请假类型", "type": "select",
               "value": "事假"}]
    out = _enrich_clarify_fields("我要请年假 2026-10-20", fields)
    f = {x["key"]: x for x in out}
    assert f["leave_type"]["value"] == "事假"      # LLM 已给的优先，不被原话覆盖
    assert f["start_date"]["value"] == "2026-10-20"


def test_enrich_fills_missing_value_on_existing_field():
    fields = [{"key": "start_date", "label": "开始日期", "type": "date"}]
    out = _enrich_clarify_fields("我要请年假 2026-10-20", fields)
    f = {x["key"]: x for x in out}
    assert f["start_date"]["value"] == "2026-10-20"


def test_enrich_ignores_supplement_segment():
    fields = [{"key": "reason", "label": "请假事由", "type": "text"}]
    out = _enrich_clarify_fields("我要请年假；补充信息：开始日期=2026-10-20", fields)
    f = {x["key"]: x for x in out}
    assert f["leave_type"]["value"] == "年假"
    assert "start_date" not in f                  # 补充段不参与原话解析


def test_enrich_does_not_mutate_input():
    fields = [{"key": "reason", "label": "请假事由", "type": "text"}]
    _enrich_clarify_fields("我要请年假", fields)
    assert fields == [{"key": "reason", "label": "请假事由", "type": "text"}]


def test_agent_expense_known_values_enriched(orch, backend):
    """报销：LLM 只问事由，原话里的类别/金额仍并入弹窗预填。"""
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "请补充报销事由。",
        "fields": [{"key": "note", "label": "报销事由", "type": "text"}]}}])]
    out = _agent(orch, backend, script).run(
        "我要报销差旅费 1200 元", user_ctx={"user_id": "u1", "name": "张三"})
    f = {x["key"]: x for x in out["clarify"]["fields"]}
    assert f["category"]["value"] == "差旅"
    assert f["amount"]["value"] == "1200"
    assert "value" not in f["note"]
    assert backend.list_expenses("张三") == []    # 未补齐前不得臆造提交


def test_agent_attendance_known_values_enriched(orch, backend):
    """补卡：LLM 只问事由，原话里的类型/日期/时间仍并入弹窗预填。"""
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "请补充补卡事由。",
        "fields": [{"key": "reason", "label": "补卡事由", "type": "text"}]}}])]
    out = _agent(orch, backend, script).run(
        "我要补卡（上班），2026-10-16 09:05", user_ctx={"user_id": "u1", "name": "张三"})
    f = {x["key"]: x for x in out["clarify"]["fields"]}
    assert f["fix_type"]["value"] == "上班补卡"
    assert f["work_date"]["value"] == "2026-10-16"
    assert f["time"]["value"] == "09:05"


def test_agent_leave_two_dates_prefilled(orch, backend):
    """请假：起止两个日期都从原话解析并预填，事由留空待补。"""
    script = [_Resp(tool_calls=[{"name": "ask_user", "id": "c1", "args": {
        "question": "请补充请假事由。",
        "fields": [{"key": "reason", "label": "请假事由", "type": "text"}]}}])]
    out = _agent(orch, backend, script).run(
        "我要请年假，2026-10-20 到 2026-10-21",
        user_ctx={"user_id": "u1", "name": "张三"})
    f = {x["key"]: x for x in out["clarify"]["fields"]}
    assert f["leave_type"]["value"] == "年假"
    assert f["start_date"]["value"] == "2026-10-20"
    assert f["end_date"]["value"] == "2026-10-21"
    assert "value" not in f["reason"]


# ============================================================
# 3. 统一工具层：注册 / 调用 / 缺参守卫
# ============================================================

def test_office_oa_tools_via_registry(orch):
    from mfg_agents.v2.mcp_tools import ToolRegistry
    from mfg_agents.v2.mcp_tools.tools import register_platform_tools
    registry = register_platform_tools(ToolRegistry(), orch)
    ctx = {"user_id": "u-oa", "name": "赵六"}

    exp = registry.call("office.submit_expense", {
        "category": "办公用品", "amount": 320, "expense_date": "2026-10-14",
        "note": "打印耗材"}, user_ctx=ctx)
    assert exp["ok"] is True and exp["expense"]["expense_id"].startswith("BX")
    lv = registry.call("office.apply_leave", {
        "leave_type": "调休", "start_date": "2026-10-20",
        "end_date": "2026-10-20", "reason": "调休"}, user_ctx=ctx)
    assert lv["ok"] is True and lv["leave"]["days"] == 1.0
    fx = registry.call("office.submit_attendance_fix", {
        "fix_type": "上班补卡", "work_date": "2026-10-16", "time": "09:02",
        "reason": "闸机故障"}, user_ctx=ctx)
    assert fx["ok"] is True and fx["fix"]["fix_id"].startswith("KB")

    mine = registry.call("office.my_requests", {}, user_ctx=ctx)
    assert mine["count"] == 3
    assert mine["leave_balance"]["annual_remaining"] == 8.5
    assert len(mine["expenses"]) == 1 and len(mine["leaves"]) == 1

    # 缺参 fail-closed（不臆造提交）
    with pytest.raises(ValueError):
        registry.call("office.submit_expense", {"category": "交通"}, user_ctx=ctx)
    # 非法业务参数 → 结构化失败（不抛异常、不落单据）
    bad = registry.call("office.apply_leave", {
        "leave_type": "探亲假", "start_date": "2026-10-20",
        "end_date": "2026-10-21"}, user_ctx=ctx)
    assert bad["ok"] is False and "非法" in bad["error"]


# ============================================================
# 4. HTTP 汇总 / 元数据
# ============================================================


def test_office_my_and_meta_endpoints(orch):
    from fastapi.testclient import TestClient
    from mfg_agents.v2.demo.api import app
    client = TestClient(app)

    meta = client.get("/api/v2/office/meta").json()["data"]
    assert meta["expense_approval_amount"] == 5000.0
    assert meta["leave_long_days"] == 3.0

    mine = client.get("/api/v2/office/my/张三").json()["data"]
    assert set(mine) >= {"expenses", "leaves", "attendance_fixes", "leave_balance"}
