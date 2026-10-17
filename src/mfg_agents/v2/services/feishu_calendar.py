"""飞书（Lark）开放平台适配层 —— 会议室预订（P1-A）。

定位（与 ``services/office.py`` 的接口先行立场一致）：
本文件是 ``OfficeBackend`` 契约的**真实实现**之一，把办公助手的会议室能力
接到飞书日历上。分层如下，三层各自可独立替换/测试：

1. ``FeishuTransport`` —— 传输层（默认 stdlib ``urllib``，零第三方依赖，与
   ``tools/llm_factory.py`` 一致）。测试注入 Fake 即可完全离线跑通。
2. ``FeishuCalendarClient`` —— 飞书 API 薄封装：租户令牌缓存、错误码归一、
   会议室列表/详情/忙闲、日程创建与删除。**只认官方字段名**，不做业务加工。
3. ``FeishuCalendarBackend`` —— 适配层：把飞书字段翻译成 ``OfficeBackend``
   契约字段（room_id/name/floor/capacity/equipment/need_approval），并承载
   平台的业务规则（容量校验、时段冲突拒绝、本人归属、高影响标记）。

订餐侧（P1-B）暂委托给 ``DemoOfficeBackend``：会议室走真实飞书、订餐仍是
demo 数据，**Agent 与统一工具层零改动**；P1-B 接入美餐/美团时只需替换
``meal_backend``。

涉及的飞书接口（均为服务端 API，tenant_access_token 鉴权）：
- ``POST /open-apis/auth/v3/tenant_access_token/internal``  租户令牌
- ``GET  /open-apis/vc/v1/rooms``                            查询会议室列表
- ``POST /open-apis/vc/v1/rooms/mget``                       批量查询会议室详情
- ``GET  /open-apis/meeting_room/freebusy/batch_get``        查询会议室忙闲
- ``POST /open-apis/calendar/v4/calendars/:id/events``       创建日程（= 预订会议室）
- ``GET  /open-apis/calendar/v4/calendars/:id/events``       查询日程
- ``DELETE /open-apis/calendar/v4/calendars/:id/events/:id`` 删除日程（= 取消预订）

字段/路径映射点集中在本文件，租户差异只改这里，不改 Agent 与工具层。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

from ...config import settings
from .office import DemoOfficeBackend, OfficeBackend, OfficeError

logger = logging.getLogger(__name__)

__all__ = ["FeishuError", "FeishuConfig", "FeishuTransport", "UrllibTransport",
           "FeishuCalendarClient", "FeishuCalendarBackend", "build_feishu_backend"]


class FeishuError(OfficeError):
    """飞书接口错误。

    刻意继承 ``OfficeError``：Agent 与统一工具层对办公后端错误的既有处理
    （``except OfficeError`` → ``{"ok": False, "error": ...}`` 反馈给模型）
    无需任何改动即可覆盖真实接入后的失败分支。
    """


# ---------- 配置 ----------


@dataclass
class FeishuConfig:
    """飞书接入配置（来源：环境变量 → ``config.Settings``）。"""

    app_id: str = ""
    app_secret: str = ""
    base_url: str = "https://open.feishu.cn"
    calendar_id: str = ""
    room_level_id: str = ""
    tz: str = "Asia/Shanghai"
    timeout_s: int = 10

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        return cls(
            app_id=settings.FEISHU_APP_ID,
            app_secret=settings.FEISHU_APP_SECRET,
            base_url=settings.FEISHU_BASE_URL,
            calendar_id=settings.FEISHU_CALENDAR_ID,
            room_level_id=settings.FEISHU_ROOM_LEVEL_ID,
            tz=settings.FEISHU_TZ,
            timeout_s=settings.FEISHU_TIMEOUT_S,
        )

    def validate(self) -> None:
        """缺关键凭据即报错（配置错误必须显式暴露，不允许静默降级到 demo）。"""
        missing = [name for name, value in (
            ("FEISHU_APP_ID", self.app_id),
            ("FEISHU_APP_SECRET", self.app_secret),
            ("FEISHU_CALENDAR_ID", self.calendar_id),
        ) if not str(value).strip()]
        if missing:
            raise FeishuError(
                "飞书接入配置不完整，缺少环境变量: " + ", ".join(missing)
                + "（会议室预订需自建应用凭据 + 承载日程的日历 ID）")


# ---------- 传输层 ----------


class FeishuTransport(Protocol):
    """传输契约：``(method, path, *, params, body, headers) -> 飞书原始 JSON``。

    只做 HTTP，不做鉴权缓存与错误码解释；测试注入 Fake 即可离线验证全链路。
    """

    def __call__(self, method: str, path: str, *,
                 params: Optional[dict] = None,
                 body: Optional[dict] = None,
                 headers: Optional[dict] = None) -> dict[str, Any]: ...


class UrllibTransport:
    """默认传输：stdlib ``urllib``（不引入 requests/httpx，与项目现状一致）。"""

    def __init__(self, config: FeishuConfig) -> None:
        self.cfg = config

    def __call__(self, method: str, path: str, *,
                 params: Optional[dict] = None,
                 body: Optional[dict] = None,
                 headers: Optional[dict] = None) -> dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + path
        if params:
            # doseq=True：room_ids 这类数组参数需展开为重复 query 参数
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
        hdrs = {"Content-Type": "application/json; charset=utf-8",
                **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 飞书的错误码也走 HTTP 体（code/msg），能解析就交给上层统一处理
            raw = exc.read().decode("utf-8", "ignore")
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                raise FeishuError(
                    f"飞书接口 HTTP {exc.code}: {raw[:200]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FeishuError(
                f"飞书接口网络异常: {type(exc).__name__}: {exc}") from None


# ---------- 时间工具（契约用 'YYYY-MM-DD'+'HH:MM'，飞书用 RFC3339 / unix 秒） ----------


def _zone(config: FeishuConfig) -> ZoneInfo:
    try:
        return ZoneInfo(config.tz)
    except Exception:  # noqa: BLE001 —— 时区配置错误不应让整条链路崩
        logger.warning("[feishu] 时区不可识别，回落 Asia/Shanghai: %s", config.tz)
        return ZoneInfo("Asia/Shanghai")


def _dt(date: str, hhmm: str, config: FeishuConfig) -> datetime:
    """契约的 (date, 'HH:MM') → 带时区的 datetime（非法输入即报错）。"""
    text = f"{str(date).strip()} {str(hhmm).strip()}"
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(
            tzinfo=_zone(config))
    except ValueError:
        raise OfficeError(
            f"日期时间格式非法: {text!r}（应为 YYYY-MM-DD 与 HH:MM）") from None


def _hhmm_minutes(hhmm: str) -> int:
    try:
        hh, mm = str(hhmm).strip().split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        raise OfficeError(f"时间格式非法: {hhmm!r}（应为 HH:MM）") from None


# ---------- API 薄封装 ----------


# 会议室作为日程参会人的类型（飞书日程参会人支持 user / chat / resource / third_party，
# 会议室即 resource）。若目标租户要求走 VC 会议室预约（user_access_token），
# 只需改 ``FeishuCalendarClient.create_event`` 一处，本文件之外零改动。
ROOM_ATTENDEE_TYPE = "resource"


class FeishuCalendarClient:
    """飞书会议室/日历 API 薄封装：令牌缓存 + 错误码归一 + 分页。"""

    def __init__(self, config: FeishuConfig,
                 transport: Optional[FeishuTransport] = None) -> None:
        self.cfg = config
        self._transport = transport or UrllibTransport(config)
        self._token = ""
        self._token_expire_at = 0.0

    # ---------- 鉴权 ----------

    def tenant_token(self, force: bool = False) -> str:
        """租户令牌（进程内缓存，提前 300s 过期以避开边界）。"""
        if not force and self._token and time.time() < self._token_expire_at:
            return self._token
        resp = self._transport(
            "POST", "/open-apis/auth/v3/tenant_access_token/internal",
            body={"app_id": self.cfg.app_id, "app_secret": self.cfg.app_secret})
        code = int(resp.get("code", 0) or 0)
        token = str(resp.get("tenant_access_token") or "")
        if code != 0 or not token:
            raise FeishuError(
                f"获取 tenant_access_token 失败: [{code}] {resp.get('msg')}"
                "（检查 FEISHU_APP_ID / FEISHU_APP_SECRET）")
        self._token = token
        expire = int(resp.get("expire", 7200) or 7200)
        self._token_expire_at = time.time() + max(60, expire - 300)
        return self._token

    # ---------- 通用调用 ----------

    def _call(self, method: str, path: str, *,
              params: Optional[dict] = None,
              body: Optional[dict] = None,
              auth: bool = True) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.tenant_token()}"} if auth else {}
        resp = self._transport(method, path, params=params, body=body,
                               headers=headers)
        code = int(resp.get("code", 0) or 0)
        if code != 0:
            raise FeishuError(f"飞书接口错误 [{code}] {resp.get('msg')}（{path}）")
        return resp.get("data") or {}

    # ---------- 会议室 ----------

    def list_rooms(self, *, room_level_id: str = "",
                   page_size: int = 100) -> list[dict[str, Any]]:
        """查询会议室列表（自动翻页）。"""
        rooms: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if room_level_id:
                params["room_level_id"] = room_level_id
            if page_token:
                params["page_token"] = page_token
            data = self._call("GET", "/open-apis/vc/v1/rooms", params=params)
            rooms.extend(data.get("rooms") or [])
            page_token = data.get("page_token") or None
            if not data.get("has_more") or not page_token:
                break
        return rooms

    def rooms_mget(self, room_ids: list[str]) -> list[dict[str, Any]]:
        """批量查询会议室详情（补齐容量/设备等列表接口可能不返回的字段）。"""
        if not room_ids:
            return []
        data = self._call("POST", "/open-apis/vc/v1/rooms/mget",
                          body={"room_ids": list(room_ids)})
        return data.get("rooms") or data.get("items") or []

    def room_freebusy(self, room_ids: list[str], time_min: str,
                      time_max: str) -> dict[str, list[dict[str, Any]]]:
        """查询会议室忙闲：返回 ``{room_id: [忙碌时段...]}``（区间内为空即空闲）。"""
        if not room_ids:
            return {}
        data = self._call("GET", "/open-apis/meeting_room/freebusy/batch_get",
                          params={"room_ids": list(room_ids),
                                  "time_min": time_min, "time_max": time_max})
        raw = data.get("free_busy") or {}
        if isinstance(raw, dict):
            return {str(k): list(v or []) for k, v in raw.items()}
        # 官方文档响应表格写作列表、示例为映射；两种形态都兼容
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in raw:
            grouped.setdefault(str(item.get("room_id")), []).append(item)
        return grouped

    # ---------- 日程（= 会议室预订） ----------

    def create_event(self, calendar_id: str, *, summary: str, start_ts: int,
                     end_ts: int, room_id: str, description: str = "",
                     tz: str = "") -> dict[str, Any]:
        body = {
            "summary": summary,
            "description": description,
            "start_time": {"timestamp": str(start_ts),
                           "timezone": tz or self.cfg.tz},
            "end_time": {"timestamp": str(end_ts),
                         "timezone": tz or self.cfg.tz},
            "attendees": [{"type": ROOM_ATTENDEE_TYPE, "attendee_id": room_id}],
        }
        data = self._call("POST",
                          f"/open-apis/calendar/v4/calendars/{calendar_id}/events",
                          body=body)
        return data.get("event") or {}

    def list_events(self, calendar_id: str, *, start_ts: int, end_ts: int,
                    page_size: int = 100) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            params: dict[str, Any] = {"start_time": str(start_ts),
                                      "end_time": str(end_ts),
                                      "page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._call("GET",
                              f"/open-apis/calendar/v4/calendars/{calendar_id}/events",
                              params=params)
            events.extend(data.get("items") or [])
            page_token = data.get("page_token") or None
            if not data.get("has_more") or not page_token:
                break
        return events

    def delete_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        return self._call(
            "DELETE",
            f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}")


# ---------- 适配层（飞书字段 → OfficeBackend 契约） ----------


class FeishuCalendarBackend(OfficeBackend):
    """飞书会议室预订 + （暂）demo 订餐的办公后端实现。

    业务规则与 demo 后端保持一致，Agent 侧表现不变：
    - 容量不足 / 时段冲突 / 非本人取消 → 显式拒绝（绝不静默覆盖他人预订）；
    - 容量 ≥ ``approval_capacity`` 的会议室标记 ``need_approval``，
      供 Agent 触发 HITL 人工确认（与 ``OFFICE_AGENT_CONFIG.hitl_rule`` 对齐）。

    已知边界（适配层阶段的诚实说明）：预订索引为**进程级留痕**，重启即丢；
    接真实环境时"我的预订"应改为查日历日程（``list_events``，需操作人 open_id）。
    """

    def __init__(self, client: FeishuCalendarClient,
                 config: Optional[FeishuConfig] = None,
                 meal_backend: Optional[OfficeBackend] = None,
                 approval_capacity: int = 20,
                 room_cache_ttl_s: float = 60.0) -> None:
        self.client = client
        self.cfg = config or client.cfg
        self.meal = meal_backend or DemoOfficeBackend()
        self.approval_capacity = int(approval_capacity)
        self.room_cache_ttl_s = float(room_cache_ttl_s)
        self._lock = threading.RLock()
        self._seq = 0
        self._bookings: dict[str, dict[str, Any]] = {}
        self._rooms_cache: Optional[tuple[float, list[dict[str, Any]]]] = None

    # ---------- 通用 ----------

    def _next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"FS{self._seq:05d}"

    def _now_iso(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _to_room(self, raw: dict[str, Any]) -> dict[str, Any]:
        """飞书会议室 → 契约字段（**字段映射点**：租户字段差异只改这里）。"""
        capacity = int(raw.get("capacity") or 0)
        return {
            "room_id": str(raw.get("room_id") or ""),
            "name": str(raw.get("name") or raw.get("room_id") or ""),
            "floor": str(raw.get("description") or raw.get("custom_room_id") or ""),
            "capacity": capacity,
            "equipment": list(raw.get("device") or raw.get("devices")
                              or raw.get("equipment") or []),
            "need_approval": capacity >= self.approval_capacity,
        }

    def _all_rooms(self) -> list[dict[str, Any]]:
        """全部会议室（列表 + 详情补齐 + 短 TTL 缓存：避免每次问询都打接口）。"""
        cached = self._rooms_cache
        if cached and time.time() - cached[0] < self.room_cache_ttl_s:
            return cached[1]
        raw_rooms = self.client.list_rooms(room_level_id=self.cfg.room_level_id)
        need_detail = [r for r in raw_rooms if not r.get("capacity")]
        if need_detail:
            details = {str(d.get("room_id")): d
                       for d in self.client.rooms_mget(
                           [str(r.get("room_id")) for r in need_detail])}
            merged = []
            for r in raw_rooms:
                rid = str(r.get("room_id"))
                merged.append({**(details.get(rid) or {}), **r} if rid in details
                              else r)
            raw_rooms = merged
        rooms = [self._to_room(r) for r in raw_rooms]
        self._rooms_cache = (time.time(), rooms)
        return rooms

    def _room(self, room_id: str) -> dict[str, Any]:
        for room in self._all_rooms():
            if room["room_id"] == room_id:
                return room
        raise OfficeError(f"会议室不存在: {room_id!r}")

    # ---------- 会议室（真实飞书） ----------

    def list_rooms(self, capacity_min: Optional[int] = None,
                   equipment: Optional[list[str]] = None) -> list[dict[str, Any]]:
        out = []
        for room in self._all_rooms():
            if (capacity_min is not None
                    and int(room["capacity"]) < int(capacity_min)):
                continue
            if equipment and not set(equipment).issubset(set(room["equipment"])):
                continue
            out.append(dict(room))
        return out

    def _busy(self, room_ids: list[str], date: str, start: str, end: str
              ) -> dict[str, list[dict[str, Any]]]:
        return self.client.room_freebusy(
            room_ids,
            _dt(date, start, self.cfg).isoformat(),
            _dt(date, end, self.cfg).isoformat())

    def find_available_rooms(self, date: str, start: str, end: str,
                             capacity_min: Optional[int] = None,
                             equipment: Optional[list[str]] = None
                             ) -> list[dict[str, Any]]:
        if _hhmm_minutes(start) >= _hhmm_minutes(end):
            raise OfficeError(f"开始时间必须早于结束时间（{start}-{end}）")
        rooms = self.list_rooms(capacity_min, equipment)
        if not rooms:
            return []
        busy = self._busy([r["room_id"] for r in rooms], date, start, end)
        return [r for r in rooms if not busy.get(r["room_id"])]

    def book_room(self, room_id: str, date: str, start: str, end: str,
                  title: str, attendees: int, booker: str) -> dict[str, Any]:
        if _hhmm_minutes(start) >= _hhmm_minutes(end):
            raise OfficeError(f"开始时间必须早于结束时间（{start}-{end}）")
        room = self._room(room_id)
        if int(attendees) > int(room["capacity"]):
            raise OfficeError(
                f"人数 {attendees} 超出 {room['name']} 容量 {room['capacity']}")
        conflicts = self._busy([room_id], date, start, end).get(room_id) or []
        if conflicts:
            who = (conflicts[0].get("organizer_info") or {}).get("name") or "他人"
            raise OfficeError(
                f"时段冲突：{room['name']} 在 {date} {start}-{end} "
                f"已被 {who} 预订，请改选其他时段或房间")
        start_dt, end_dt = _dt(date, start, self.cfg), _dt(date, end, self.cfg)
        event = self.client.create_event(
            self.cfg.calendar_id, summary=title or "未命名会议",
            start_ts=int(start_dt.timestamp()), end_ts=int(end_dt.timestamp()),
            room_id=room_id,
            description=f"由办公助手代 {booker} 预订（会议室：{room['name']}）")
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise OfficeError("飞书创建日程未返回 event_id（预订未落地）")
        booking = {
            "booking_id": self._next_id(),
            "room_id": room_id, "room_name": room["name"],
            "date": date, "start": start, "end": end,
            "title": title or "未命名会议", "attendees": int(attendees),
            "booker": booker, "status": "reserved", "created_at": self._now_iso(),
            "need_approval": bool(room.get("need_approval")),
            "feishu_event_id": event_id,
            "feishu_status": str(event.get("status") or ""),
        }
        with self._lock:
            self._bookings[booking["booking_id"]] = booking
        logger.info("[feishu] 会议室预订成功 %s → event %s",
                    booking["booking_id"], event_id)
        return dict(booking)

    def cancel_booking(self, booking_id: str, booker: str) -> dict[str, Any]:
        with self._lock:
            booking = self._bookings.get(booking_id)
            if booking is None:
                raise OfficeError(
                    f"预订记录不存在: {booking_id!r}（非本进程登记的飞书预订无法取消）")
            if booking["booker"] != booker:
                raise OfficeError(
                    f"只能取消本人预订（该单属于 {booking['booker']}）")
            self.client.delete_event(self.cfg.calendar_id,
                                     booking["feishu_event_id"])
            booking["status"] = "cancelled"
            booking["cancelled_at"] = self._now_iso()
            return dict(booking)

    def list_bookings(self, booker: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(b) for b in self._bookings.values()
                     if b["status"] == "reserved"]
        if booker:
            items = [b for b in items if b["booker"] == booker]
        return sorted(items, key=lambda b: (b["date"], b["start"]))

    # ---------- 订餐（P1-B 待接入美餐/美团；当前委托 demo 后端） ----------

    def list_restaurants(self) -> list[dict[str, Any]]:
        return self.meal.list_restaurants()

    def get_menu(self, restaurant_id: str) -> dict[str, Any]:
        return self.meal.get_menu(restaurant_id)

    def place_order(self, restaurant_id: str, items: list[dict[str, Any]],
                    meal_time: str, orderer: str, note: str = ""
                    ) -> dict[str, Any]:
        return self.meal.place_order(restaurant_id, items, meal_time,
                                     orderer, note)

    def cancel_order(self, order_id: str, orderer: str) -> dict[str, Any]:
        return self.meal.cancel_order(order_id, orderer)

    def list_orders(self, orderer: Optional[str] = None) -> list[dict[str, Any]]:
        return self.meal.list_orders(orderer)


# ---------- 装配入口 ----------


def build_feishu_backend(config: Optional[FeishuConfig] = None,
                         transport: Optional[FeishuTransport] = None,
                         meal_backend: Optional[OfficeBackend] = None
                         ) -> FeishuCalendarBackend:
    """按环境变量装配飞书办公后端（凭据缺失即报错，不静默回落 demo）。"""
    cfg = config or FeishuConfig.from_env()
    cfg.validate()
    return FeishuCalendarBackend(FeishuCalendarClient(cfg, transport), cfg,
                                 meal_backend)
