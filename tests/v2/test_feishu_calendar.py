"""飞书日历适配层测试（P1-A）。

全部离线：用 ``FakeTransport`` 伪造飞书 HTTP 响应，验证
令牌缓存 → 会议室列表/忙闲 → 日程预订/取消 → 契约字段翻译 的完整链路，
以及适配层与 demo 后端一致的业务规则（容量校验 / 冲突拒绝 / 本人归属）。
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from mfg_agents.v2.services.feishu_calendar import (
    FeishuCalendarBackend,
    FeishuCalendarClient,
    FeishuConfig,
    FeishuError,
    build_feishu_backend,
)
from mfg_agents.v2.services.office import DemoOfficeBackend, OfficeBackend, OfficeError

_TZ = ZoneInfo("Asia/Shanghai")


def _ts(date: str, hhmm: str) -> int:
    return int(datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H:%M")
               .replace(tzinfo=_TZ).timestamp())


class FakeTransport:
    """按 (method, path) 返回预置响应；记录全部调用供断言（含鉴权头）。"""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[dict] = []

    def __call__(self, method, path, *, params=None, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "params": params or {},
                           "body": body or {}, "headers": headers or {}})
        for (m, p), resp in self.routes.items():
            if m == method and p == path:
                return resp
        raise AssertionError(f"未预置的调用: {method} {path}")

    def calls_of(self, path: str) -> list[dict]:
        return [c for c in self.calls if c["path"] == path]


def _cfg() -> FeishuConfig:
    return FeishuConfig(app_id="cli_demo", app_secret="secret",
                        calendar_id="cal_demo", room_level_id="",
                        tz="Asia/Shanghai")


def _token_route() -> dict:
    return {("POST", "/open-apis/auth/v3/tenant_access_token/internal"):
            {"code": 0, "msg": "success",
             "tenant_access_token": "t-demo", "expire": 7200}}


def _rooms_route(rooms: list[dict]) -> dict:
    return {("GET", "/open-apis/vc/v1/rooms"):
            {"code": 0, "msg": "success",
             "data": {"rooms": rooms, "has_more": False}}}


def _freebusy_route(mapping: dict) -> dict:
    return {("GET", "/open-apis/meeting_room/freebusy/batch_get"):
            {"code": 0, "msg": "success",
             "data": {"free_busy": mapping}}}


_ROOM_A = {"room_id": "omm_a", "name": "A栋201 · 小会议室",
           "capacity": "6", "description": "A栋2F", "device": ["白板"]}
_ROOM_C = {"room_id": "omm_c", "name": "C栋101 · 报告厅",
           "capacity": "80", "description": "C栋1F", "device": ["投影", "音响"]}


def _backend(routes: dict) -> tuple[FeishuCalendarBackend, FakeTransport]:
    transport = FakeTransport(routes)
    client = FeishuCalendarClient(_cfg(), transport)
    return FeishuCalendarBackend(client, _cfg()), transport


# ---------- 配置 / 契约 ----------


def test_build_backend_fails_fast_without_credentials() -> None:
    """缺凭据必须显式报错（不能静默回落 demo，业务办理不能假装成功）。"""
    cfg = FeishuConfig(app_id="", app_secret="", calendar_id="")
    with pytest.raises(FeishuError) as exc:
        build_feishu_backend(cfg)
    assert "FEISHU_APP_ID" in str(exc.value)
    assert "FEISHU_CALENDAR_ID" in str(exc.value)


def test_feishu_error_is_office_error() -> None:
    """FeishuError 继承 OfficeError：Agent/工具层既有失败处理零改动生效。"""
    assert issubclass(FeishuError, OfficeError)


def test_backend_satisfies_contract() -> None:
    backend, _ = _backend({**_token_route(), **_rooms_route([_ROOM_A])})
    assert isinstance(backend, OfficeBackend)


# ---------- 鉴权与错误码 ----------


def test_tenant_token_cached_across_calls() -> None:
    backend, transport = _backend({**_token_route(), **_rooms_route([_ROOM_A])})
    backend.list_rooms()
    backend.list_rooms()
    assert len(transport.calls_of(
        "/open-apis/auth/v3/tenant_access_token/internal")) == 1
    # 业务请求必须带 Bearer 令牌（不是匿名裸调）
    assert transport.calls_of("/open-apis/vc/v1/rooms")[0]["headers"][
        "Authorization"] == "Bearer t-demo"


def test_api_error_code_raises_feishu_error() -> None:
    routes = {**_token_route(),
              ("GET", "/open-apis/vc/v1/rooms"):
                  {"code": 99991663, "msg": "tenant token invalid"}}
    backend, _ = _backend(routes)
    with pytest.raises(FeishuError) as exc:
        backend.list_rooms()
    assert "99991663" in str(exc.value)


# ---------- 会议室：列表 / 忙闲 ----------


def test_list_rooms_translates_fields_and_filters() -> None:
    backend, _ = _backend({**_token_route(), **_rooms_route([_ROOM_A, _ROOM_C])})
    rooms = backend.list_rooms()
    assert [r["room_id"] for r in rooms] == ["omm_a", "omm_c"]
    # 飞书 capacity 是字符串 → 契约要求 int；device → equipment；description → floor
    assert rooms[0]["capacity"] == 6 and rooms[0]["floor"] == "A栋2F"
    assert rooms[0]["equipment"] == ["白板"]
    # 容量 ≥20 视为高影响资源（供 Agent 触发 HITL）
    assert rooms[0]["need_approval"] is False
    assert rooms[1]["need_approval"] is True
    # 按容量/设备过滤
    assert [r["room_id"] for r in backend.list_rooms(capacity_min=30)] == ["omm_c"]
    assert backend.list_rooms(equipment=["视频会议"]) == []


def test_room_cache_avoids_repeated_calls() -> None:
    backend, transport = _backend({**_token_route(), **_rooms_route([_ROOM_A])})
    backend.list_rooms()
    backend.list_rooms()
    assert len(transport.calls_of("/open-apis/vc/v1/rooms")) == 1


def test_list_rooms_enriches_missing_capacity_via_mget() -> None:
    """列表接口未返回容量时，用批量详情补齐（字段差异只影响适配层）。"""
    routes = {**_token_route(),
              ("GET", "/open-apis/vc/v1/rooms"):
                  {"code": 0, "data": {"rooms": [{"room_id": "omm_a", "name": "A201"}],
                                       "has_more": False}},
              ("POST", "/open-apis/vc/v1/rooms/mget"):
                  {"code": 0, "data": {"items": [{"room_id": "omm_a", "name": "A201",
                                                  "capacity": "8"}]}}}
    backend, _ = _backend(routes)
    assert backend.list_rooms()[0]["capacity"] == 8


def test_find_available_rooms_excludes_busy_and_validates_input() -> None:
    routes = {**_token_route(), **_rooms_route([_ROOM_A, _ROOM_C]),
              **_freebusy_route({"omm_a": [
                  {"start_time": "2026-10-20T14:00:00+08:00",
                   "end_time": "2026-10-20T15:00:00+08:00",
                   "uid": "u1", "organizer_info": {"name": "王五"}}]})}
    backend, transport = _backend(routes)
    rooms = backend.find_available_rooms("2026-10-20", "14:00", "15:00")
    assert [r["room_id"] for r in rooms] == ["omm_c"]
    # 忙闲查询用 RFC3339（飞书要求）
    fb = transport.calls_of("/open-apis/meeting_room/freebusy/batch_get")[0]
    assert fb["params"]["time_min"] == "2026-10-20T14:00:00+08:00"
    assert fb["params"]["time_max"] == "2026-10-20T15:00:00+08:00"
    assert fb["params"]["room_ids"] == ["omm_a", "omm_c"]
    with pytest.raises(OfficeError):
        backend.find_available_rooms("2026-10-20", "15:00", "14:00")


# ---------- 会议室：预订 / 取消 ----------


def _booking_routes(busy: dict | None = None) -> dict:
    return {**_token_route(), **_rooms_route([_ROOM_A, _ROOM_C]),
            **_freebusy_route(busy or {}),
            ("POST", "/open-apis/calendar/v4/calendars/cal_demo/events"):
                {"code": 0, "data": {"event": {"event_id": "evt_1",
                                               "status": "confirmed"}}},
            ("DELETE", "/open-apis/calendar/v4/calendars/cal_demo/events/evt_1"):
                {"code": 0, "data": {}}}


def test_book_room_creates_feishu_event_and_returns_platform_booking() -> None:
    backend, transport = _backend(_booking_routes())
    booking = backend.book_room("omm_a", "2026-10-20", "14:00", "15:00",
                                "项目评审", 6, "李四")
    assert booking["status"] == "reserved"
    assert booking["booking_id"].startswith("FS")
    assert booking["feishu_event_id"] == "evt_1"
    assert booking["need_approval"] is False
    body = transport.calls_of(
        "/open-apis/calendar/v4/calendars/cal_demo/events")[0]["body"]
    assert body["start_time"]["timestamp"] == str(_ts("2026-10-20", "14:00"))
    assert body["end_time"]["timestamp"] == str(_ts("2026-10-20", "15:00"))
    assert body["start_time"]["timezone"] == "Asia/Shanghai"
    # 会议室以 resource 参会人身份进日程（= 飞书会议室预订）
    assert body["attendees"] == [{"type": "resource", "attendee_id": "omm_a"}]
    assert backend.list_bookings("李四")[0]["booking_id"] == booking["booking_id"]
    assert backend.list_bookings("王五") == []


def test_book_room_rejects_conflict_and_does_not_create_event() -> None:
    busy = {"omm_a": [{"start_time": "2026-10-20T14:30:00+08:00",
                       "end_time": "2026-10-20T15:30:00+08:00",
                       "organizer_info": {"name": "王五"}}]}
    backend, transport = _backend(_booking_routes(busy))
    with pytest.raises(OfficeError) as exc:
        backend.book_room("omm_a", "2026-10-20", "14:00", "15:00",
                          "项目评审", 6, "李四")
    assert "时段冲突" in str(exc.value) and "王五" in str(exc.value)
    assert transport.calls_of(
        "/open-apis/calendar/v4/calendars/cal_demo/events") == []


def test_book_room_rejects_over_capacity() -> None:
    backend, _ = _backend(_booking_routes())
    with pytest.raises(OfficeError) as exc:
        backend.book_room("omm_a", "2026-10-20", "14:00", "15:00", "评审", 9, "李四")
    assert "超出" in str(exc.value)


def test_high_impact_room_flagged_for_hitl() -> None:
    backend, _ = _backend(_booking_routes())
    booking = backend.book_room("omm_c", "2026-10-20", "09:00", "11:00",
                                "全员大会", 60, "李四")
    assert booking["need_approval"] is True


def test_cancel_booking_enforces_ownership_and_calls_feishu() -> None:
    backend, transport = _backend(_booking_routes())
    booking = backend.book_room("omm_a", "2026-10-20", "14:00", "15:00",
                                "项目评审", 6, "李四")
    with pytest.raises(OfficeError):
        backend.cancel_booking(booking["booking_id"], "王五")
    assert transport.calls_of(
        "/open-apis/calendar/v4/calendars/cal_demo/events/evt_1") == []

    out = backend.cancel_booking(booking["booking_id"], "李四")
    assert out["status"] == "cancelled"
    assert len(transport.calls_of(
        "/open-apis/calendar/v4/calendars/cal_demo/events/evt_1")) == 1
    assert backend.list_bookings("李四") == []
    with pytest.raises(OfficeError):
        backend.cancel_booking("FS99999", "李四")


def test_unknown_room_or_bad_time_rejected() -> None:
    backend, _ = _backend(_booking_routes())
    with pytest.raises(OfficeError):
        backend.book_room("omm_zzz", "2026-10-20", "14:00", "15:00", "x", 1, "李四")
    with pytest.raises(OfficeError):
        backend.book_room("omm_a", "2026/10/20", "14:00", "15:00", "x", 1, "李四")


# ---------- 订餐：P1-B 前仍委托 demo ----------


def test_meal_calls_delegate_to_demo_backend() -> None:
    backend, _ = _backend({**_token_route(), **_rooms_route([_ROOM_A])})
    assert isinstance(backend.meal, DemoOfficeBackend)
    assert backend.list_restaurants()[0]["restaurant_id"] == "RT-01"
    menu = backend.get_menu("RT-01")
    order = backend.place_order("RT-01", [{"item_id": "M101", "qty": 2}],
                                "午餐", "李四")
    assert order["total_amount"] == menu["items"][0]["price"] * 2
    assert backend.cancel_order(order["order_id"], "李四")["status"] == "cancelled"
    assert json.dumps(backend.list_orders("李四"), ensure_ascii=False) == "[]"
