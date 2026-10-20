"""OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）：契约 + 工具 + 归口。

覆盖点：
- 服务契约（demo 实现）：6 类 kind 提交成功与单号前缀、非法 kind / 缺必填 / 非法紧急度
  一律 fail-closed、本人过滤与倒序、归口按 kind 分派；
- 统一工具层：office.submit_service_request 注册与 tier/readonly、缺 kind 报错、
  结果里带 assignee；office.my_requests 汇总含 service_requests 并计入 count；
- agent 侧：流程识别 _detect_flow、归口 _OFFICE_ASSIGNEE/_OFFICE_FLOW、已知值预填
  （车牌号 / 紧急度）；
- HTTP 汇总：/api/v2/office/my/{actor} 带 service_requests 字段（向后兼容）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.agents.office import (
    _OFFICE_ASSIGNEE,
    _OFFICE_FLOW,
    _detect_flow,
    _known_values,
)
from mfg_agents.v2.services.office import (
    SERVICE_ASSIGNEE,
    SERVICE_ID_PREFIX,
    SERVICE_KIND_NAMES,
    SERVICE_KINDS,
    SERVICE_REQUIRED,
    DemoOfficeBackend,
    OfficeError,
)

# 每类 kind 的一组合法必填（title/location 走显式入参，其余放 extra）
_CASES: dict[str, dict] = {
    "it_repair": {"title": "MES 终端开机黑屏", "location": "SMT 车间 2 号线",
                  "urgency": "紧急"},
    "it_access": {"title": "承担工单录入职责", "extra": {"system": "MES", "permission": "录入"}},
    "it_device": {"title": "新员工入职配置", "extra": {"device_type": "笔记本电脑"}},
    "parking": {"title": "自驾通勤申请固定车位", "extra": {"plate": "苏A12345"}},
    "visitor_vehicle": {"title": "供应商来厂技术交流",
                        "extra": {"plate": "苏A88888", "visit_date": "2026-10-22"}},
    "business_car": {"title": "赴苏州分厂对接量产问题",
                     "extra": {"dest": "苏州分厂", "use_from": "2026-10-22 08:00"}},
}


@pytest.fixture
def backend() -> DemoOfficeBackend:
    return DemoOfficeBackend()


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


# ---------- 1. 服务契约（demo 实现） ----------


def test_submit_all_kinds(backend) -> None:
    """6 类服务工单都能提交，单号前缀/中文名/归口/状态口径一致。"""
    for kind in SERVICE_KINDS:
        case = _CASES[kind]
        req = backend.submit_service_request(
            kind, case["title"], case.get("detail", "补充说明"), "李伟",
            urgency=case.get("urgency", "一般"), location=case.get("location", ""),
            extra=case.get("extra"))
        assert req["request_id"].startswith(SERVICE_ID_PREFIX[kind]), kind
        assert req["kind"] == kind and req["kind_name"] == SERVICE_KIND_NAMES[kind]
        assert req["assignee"] == SERVICE_ASSIGNEE[kind]
        assert req["status"] == "pending_approval" and req["need_approval"] is True
        assert req["applicant"] == "李伟"
    assert len(backend.list_service_requests()) == len(SERVICE_KINDS)


def test_submit_fail_closed(backend) -> None:
    """非法 kind / 缺必填 / 非法紧急度 / 缺申请人 一律拒绝，不落单据。"""
    with pytest.raises(OfficeError):
        backend.submit_service_request("bad_kind", "x", "", "李伟")
    with pytest.raises(OfficeError):      # parking 缺车牌
        backend.submit_service_request("parking", "申请车位", "", "李伟")
    with pytest.raises(OfficeError):      # business_car 缺目的地与用车时间
        backend.submit_service_request("business_car", "去苏州分厂", "", "李伟")
    with pytest.raises(OfficeError):      # 非法紧急度
        backend.submit_service_request("it_repair", "黑屏", "", "李伟",
                                       urgency="马上立刻", location="SMT 2 号线")
    with pytest.raises(OfficeError):      # 缺申请人
        backend.submit_service_request("it_repair", "黑屏", "", "",
                                       location="SMT 2 号线")
    assert backend.list_service_requests() == []
    # 必填清单自带（SERVICE_REQUIRED）→ 报错信息用中文标签，便于前端/Agent 转述
    assert SERVICE_REQUIRED["it_repair"] == ["title", "location"]


def test_list_filter_and_order(backend) -> None:
    """列表按本人过滤（口径与报销/请假一致：按创建时间倒序，同秒并列时保持提交顺序）。"""
    first = backend.submit_service_request("it_repair", "打印机卡纸", "", "李伟",
                                           location="行政楼 3 层")
    second = backend.submit_service_request("parking", "申请临停车位", "", "李伟",
                                            extra={"plate": "苏A66666"})
    backend.submit_service_request("it_device", "领用扫码枪", "", "王五",
                                   extra={"device_type": "扫码枪"})
    mine = backend.list_service_requests("李伟")
    assert {r["request_id"] for r in mine} == {first["request_id"], second["request_id"]}
    assert {r["applicant"] for r in mine} == {"李伟"}
    assert [r["applicant"] for r in backend.list_service_requests("王五")] == ["王五"]
    allr = backend.list_service_requests()
    assert len(allr) == 3 and {r["applicant"] for r in allr} == {"李伟", "王五"}


# ---------- 2. 统一工具层 ----------


def test_office_tool_submit_service_request(orch) -> None:
    """工具层：注册齐全、tier/readonly 正确、缺参 fail-closed、结果带归口；汇总计入 count。"""
    from mfg_agents.v2.mcp_tools.tools import (build_domain_toolsets,
                                              register_platform_tools)
    from mfg_agents.v2.mcp_tools.registry import ToolRegistry

    tools = {t.name: t for t in build_domain_toolsets(orch)["office"]}
    assert "office.submit_service_request" in tools
    tool = tools["office.submit_service_request"]
    assert tool.readonly is False and tool.tier == 2

    rt = orch.runtime
    old = rt.office
    rt.office = DemoOfficeBackend()
    try:
        registry = register_platform_tools(ToolRegistry(), orch)
        ctx = {"user_id": "u-it", "name": "李伟", "roles": ["manufacturing"]}
        out = registry.call("office.submit_service_request", {
            "kind": "it_repair", "title": "MES 终端开机黑屏",
            "location": "SMT 车间 2 号线", "urgency": "紧急"}, user_ctx=ctx)
        assert out["ok"] is True
        assert out["service_request"]["request_id"].startswith("ITRP")
        assert out["assignee"] == "IT 运维专员" and out["need_approval"] is True

        # 缺 kind → 参数层 fail-closed（不臆造提交）
        with pytest.raises(ValueError):
            registry.call("office.submit_service_request", {"title": "黑屏"}, user_ctx=ctx)
        # 业务必填缺失 → 结构化失败，不抛异常
        bad = registry.call("office.submit_service_request", {
            "kind": "parking", "title": "申请车位"}, user_ctx=ctx)
        assert bad["ok"] is False and "必填" in bad["error"]

        mine = registry.call("office.my_requests", {}, user_ctx=ctx)
        assert len(mine["service_requests"]) == 1 and mine["count"] == 1
    finally:
        rt.office = old


# ---------- 3. agent 侧：流程识别 / 归口 / 已知值预填 ----------


@pytest.mark.parametrize("text,kind", [
    ("SMT 2 号线 MES 终端黑屏了，赶紧报修", "it_repair"),
    ("申请 MES 工单录入权限", "it_access"),
    ("我要领用一台笔记本", "it_device"),
    ("申请 B 栋地面固定车位", "parking"),
    ("明天有访客车辆来访，车牌苏A88888，需要报备", "visitor_vehicle"),
    ("申请公务用车，明天去苏州分厂", "business_car"),
])
def test_detect_flow_service_kinds(text: str, kind: str) -> None:
    """口语问法能稳定识别到对应服务 kind（且不与报销/请假/补卡抢路由）。"""
    assert _detect_flow(text) == kind


def test_service_flow_and_assignee_configured() -> None:
    """6 类服务的流程留痕与归口齐备（IT 类 → IT 运维专员；停车/车辆 → 行政后勤专员）。"""
    for kind in SERVICE_KINDS:
        assert kind in _OFFICE_FLOW and kind in _OFFICE_ASSIGNEE
        assert len(_OFFICE_FLOW[kind]) == 3          # 识别 → 归口核验 → 办理
    assert _OFFICE_ASSIGNEE["it_repair"] == "IT 运维专员"
    assert _OFFICE_ASSIGNEE["parking"] == "行政后勤专员"


def test_known_values_prefill_for_service() -> None:
    """车牌号与紧急程度能从原话确定性预填（弹窗打开即半填，不靠 LLM）。"""
    known = _known_values("parking", "申请车位，车牌 苏A12345，紧急")
    assert known["plate"] == "苏A12345"
    assert known["urgency"] == "紧急"
    known2 = _known_values("visitor_vehicle", "2026-10-22 有访客车辆来访")
    assert known2["visit_date"] == "2026-10-22"


# ---------- 4. HTTP 汇总形状（向后兼容） ----------


def test_office_my_includes_service_requests() -> None:
    """/api/v2/office/my/{actor} 带 service_requests 字段（旧字段保持不变）。"""
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    resp = TestClient(app).get("/api/v2/office/my/李伟")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "service_requests" in data
    assert set(data) >= {"bookings", "orders", "expenses", "leaves",
                         "attendance_fixes", "service_requests", "leave_balance"}
