"""办公助手（独立 Agentic Agent）测试。

覆盖点：
- OfficeBackend 契约（demo 实现）：会议室预订/冲突拒绝/取消、订餐计价/审批标记、
  本人归属与越权拒绝、参数校验；
- 路由：办公关键词命中 → strategy=office（LLM 不可用时同样命中，离线可演示）；
- 统一工具层：14 个 office 工具注册、tier/readonly 标记、可被 registry.call；
- Agent 循环：脚本化 LLM 驱动 ReAct，工具调用落地、冲突反馈后换房重试、
  高影响资源触发 request_approval（HITL 只发起）；
- 集成：orchestrator 办公通道离线诚实降级（不崩）。
"""
from __future__ import annotations

import json

import pytest

from mfg_agents.v2.services.office import DemoOfficeBackend, OfficeError


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


# ============================================================
# 1. 后端契约（demo 实现）
# ============================================================


def test_backend_room_lifecycle(backend):
    avail = backend.find_available_rooms("2026-10-20", "14:00", "15:00",
                                         capacity_min=8)
    assert any(r["room_id"] == "MR-A-202" for r in avail)

    b = backend.book_room("MR-A-202", "2026-10-20", "14:00", "15:00",
                          "项目评审", 8, "张三")
    assert b["booking_id"].startswith("BK") and b["status"] == "reserved"
    # 预订后该房间该时段不再可用
    after = backend.find_available_rooms("2026-10-20", "14:00", "15:00")
    assert all(r["room_id"] != "MR-A-202" for r in after)
    # 取消后恢复可用
    backend.cancel_booking(b["booking_id"], "张三")
    assert backend.list_bookings("张三") == []


def test_backend_conflict_rejected(backend):
    backend.book_room("MR-B-301", "2026-10-20", "14:00", "15:00", "周会", 8, "张三")
    with pytest.raises(OfficeError, match="冲突"):
        backend.book_room("MR-B-301", "2026-10-20", "14:30", "15:30",
                          "撞车", 8, "李四")


def test_backend_validation(backend):
    with pytest.raises(OfficeError, match="开始时间"):
        backend.book_room("MR-A-201", "2026-10-20", "15:00", "14:00",
                          "倒挂", 4, "张三")
    with pytest.raises(OfficeError, match="容量"):
        backend.book_room("MR-A-201", "2026-10-20", "10:00", "11:00",
                          "超员", 50, "张三")
    with pytest.raises(OfficeError, match="不存在"):
        backend.book_room("MR-X-999", "2026-10-20", "10:00", "11:00",
                          "无房", 4, "张三")


def test_backend_order_pricing_and_approval_flag(backend):
    o = backend.place_order("RT-01", [{"item_id": "M101", "qty": 2},
                                     {"item_id": "M103", "qty": 1}],
                            "午餐", "张三")
    assert o["order_id"].startswith("OD")
    assert o["total_amount"] == 42.0 and o["need_approval"] is False
    # 商务客餐 → 标记需审批
    o2 = backend.place_order("RT-03", [{"item_id": "M302", "qty": 1}],
                             "午餐", "张三")
    assert o2["need_approval"] is True
    # 超额度 → 需审批
    o3 = backend.place_order("RT-02", [{"item_id": "M201", "qty": 40}],
                             "午餐", "张三")
    assert o3["total_amount"] >= 500 and o3["need_approval"] is True
    with pytest.raises(OfficeError, match="菜品"):
        backend.place_order("RT-01", [{"item_id": "NOPE", "qty": 1}],
                            "午餐", "张三")


def test_backend_ownership_enforced(backend):
    b = backend.book_room("MR-A-201", "2026-10-20", "09:00", "10:00",
                          "晨会", 4, "张三")
    with pytest.raises(OfficeError, match="只能取消本人"):
        backend.cancel_booking(b["booking_id"], "李四")


# ============================================================
# 2. 路由：办公意图快路径（LLM 不可用也命中）
# ============================================================


@pytest.mark.parametrize("query", [
    "帮我订个明天下午2点的会议室",
    "帮我预订一个10人会议室",
    "中午订个餐",
    "我要点餐，来一份牛肉面",
    "查一下我的会议室预订",
])
def test_router_office_intent(orch, query):
    route = orch.runtime.router.route(query)
    assert route.strategy == "office"
    assert route.domain == "office"
    assert route.level == "L0"


# ============================================================
# 3. 统一工具层：注册 / 标记 / 调用
# ============================================================


def test_office_tools_registered(orch):
    from mfg_agents.v2.mcp_tools.tools import build_domain_toolsets
    toolsets = build_domain_toolsets(orch)
    office = toolsets["office"]
    assert len(office) == 14 and len(office) <= 15
    by_name = {t.name: t for t in office}
    # 只读工具：tier=0 / readonly=True；写工具：tier=2 / readonly=False
    for name in ("office.list_rooms", "office.find_available_rooms",
                 "office.my_bookings", "office.menu", "office.my_requests"):
        assert by_name[name].readonly is True and by_name[name].tier == 0
    for name in ("office.book_room", "office.cancel_booking",
                 "office.place_order", "office.cancel_order",
                 "office.submit_expense", "office.apply_leave",
                 "office.submit_attendance_fix"):
        assert by_name[name].readonly is False and by_name[name].tier == 2


def test_office_tool_call_via_registry(orch):
    from mfg_agents.v2.mcp_tools import ToolRegistry
    from mfg_agents.v2.mcp_tools.tools import register_platform_tools
    registry = register_platform_tools(ToolRegistry(), orch)
    ctx = {"user_id": "u-office", "name": "王五"}
    rooms = registry.call("office.list_rooms", {"capacity_min": 10}, user_ctx=ctx)
    assert rooms["count"] >= 1
    booked = registry.call("office.book_room", {
        "room_id": "MR-B-301", "date": "2026-10-21", "start": "09:00",
        "end": "10:00", "title": "站会", "attendees": 6}, user_ctx=ctx)
    assert booked["ok"] is True and booked["booking"]["booker"] == "王五"
    # 越权取消（他人预订）→ 结构化错误而非抛异常
    denied = registry.call("office.cancel_booking",
                           {"booking_id": booked["booking"]["booking_id"]},
                           user_ctx={"user_id": "u-other", "name": "赵六"})
    assert denied["ok"] is False and "只能取消本人" in denied["error"]


# ============================================================
# 4. Agent 循环：脚本化 LLM 驱动 ReAct
# ============================================================


def _agent(orch, backend, script):
    from mfg_agents.v2.agents.office import OfficeAssistantAgent
    agent = OfficeAssistantAgent(orchestrator=orch, backend=backend)
    agent._llm = _ScriptedLLM(script)   # 注入脚本化 LLM（跳过网络与探测）
    return agent


def test_agent_loop_books_room(orch, backend):
    script = [
        _Resp(tool_calls=[{"name": "find_available_rooms", "id": "c1", "args": {
            "date": "2026-10-22", "start": "14:00", "end": "15:00",
            "capacity_min": 8}}]),
        _Resp(tool_calls=[{"name": "book_room", "id": "c2", "args": {
            "room_id": "MR-A-202", "date": "2026-10-22", "start": "14:00",
            "end": "15:00", "title": "项目评审", "attendees": 8}}]),
        _final("已为您预订 A栋202 · 中会议室（2026-10-22 14:00-15:00）。"),
    ]
    out = _agent(orch, backend, script).run(
        "明天下午2点订个8人会议室", user_ctx={"user_id": "u1", "name": "张三"})

    assert [s["tool"] for s in out["steps"]] == [
        "find_available_rooms", "book_room"]
    assert all(s["ok"] for s in out["steps"])
    assert out["needs_hitl"] is False
    bookings = backend.list_bookings("张三")
    assert len(bookings) == 1 and bookings[0]["room_id"] == "MR-A-202"


def test_agent_recovers_from_conflict(orch, backend):
    backend.book_room("MR-A-202", "2026-10-22", "14:00", "15:00",
                      "占位", 8, "李四")
    script = [
        _Resp(tool_calls=[{"name": "book_room", "id": "c1", "args": {
            "room_id": "MR-A-202", "date": "2026-10-22", "start": "14:00",
            "end": "15:00", "title": "评审", "attendees": 8}}]),   # 冲突 → 失败
        _Resp(tool_calls=[{"name": "book_room", "id": "c2", "args": {
            "room_id": "MR-B-301", "date": "2026-10-22", "start": "14:00",
            "end": "15:00", "title": "评审", "attendees": 8}}]),   # 换房成功
        _final("原会议室时段冲突，已改订 B栋301 · 视频会议室。"),
    ]
    out = _agent(orch, backend, script).run(
        "订个会议室", user_ctx={"user_id": "u1", "name": "张三"})

    assert out["steps"][0]["ok"] is False       # 首次冲突被如实反馈
    assert out["steps"][1]["ok"] is True        # Agent 读原因后换房成功
    assert backend.list_bookings("张三")[0]["room_id"] == "MR-B-301"


def test_agent_triggers_hitl_for_approval_resource(orch, backend):
    script = [
        _Resp(tool_calls=[{"name": "book_room", "id": "c1", "args": {
            "room_id": "MR-C-101", "date": "2026-11-03", "start": "09:00",
            "end": "11:00", "title": "全员大会", "attendees": 60}}]),
        _Resp(tool_calls=[{"name": "request_approval", "id": "c2", "args": {
            "title": "报告厅预订确认", "prompt": "预订 C栋101 报告厅用于全员大会",
            "category": "room"}}]),
        _final("报告厅属需授权资源，已提交人工确认，待行政专员审批。",
               needs_hitl=True, hitl_title="报告厅预订确认",
               hitl_prompt="预订 C栋101 报告厅用于全员大会", hitl_category="room"),
    ]
    out = _agent(orch, backend, script).run(
        "在报告厅订个全员大会场地", user_ctx={"user_id": "u1", "name": "张三"})

    assert out["needs_hitl"] is True
    assert out["hitl_task"] and out["hitl_task"]["task_uid"]
    # 工单确实进入既有治理链（可被 workflow.pending 查到），Agent 只发起不放行
    pending = orch.runtime.workflow.list_tasks()
    assert any(t.task_uid == out["hitl_task"]["task_uid"] for t in pending)


# ============================================================
# 5. 集成：orchestrator 办公通道（离线诚实降级，不崩）
# ============================================================


def test_orchestrator_office_channel_offline_degrades(orch):
    """LLM 不可用（默认离线）时，办公通道如实回落人工兜底，不抛异常。"""
    result = orch.handle("帮我订个会议室", {"user_id": "u1", "name": "张三"})
    data = result.to_dict()
    assert data["route"]["strategy"] == "office"
    assert data["fallback"] == "manual_ticket"
