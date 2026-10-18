"""办公后端服务：会议室预订 / 订餐（接口先行，demo 可替换真实系统）。

设计立场（与平台既有 ``DataSource`` / ``IMGateway`` 契约同源）：
- **接口先行**：``OfficeBackend`` 抽象出「会议室」与「订餐」两类企业常用能力
  的确定性方法；``DemoOfficeBackend`` 用内存种子数据实现。**替换真实飞书/
  钉钉/美餐/美团企业版 API 时，只需实现同一接口并在运行时注入**，Agent 与
  工具层零改动——这就是"demo 展示、后续可真实接入接口"的落地方式。
- **写操作留痕**：预订/下单一律返回 ``*_id`` 与完整快照（含操作人、时间），
  供审计与撤销；冲突（同房间同时段重叠）必须显式拒绝，**绝不静默覆盖他人预订**。
- **无副作用演示**：demo 实现全部内存态，进程重启即复位，不触达外网；
  真实实现只需在同一方法签名下转发外部 API。
- **需授权资源显式标记**：``need_approval`` 标记高影响资源（报告厅/培训室/
  商务餐厅客餐），供 Agent 触发 HITL 人工确认（治理白名单决策）。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

__all__ = ["OfficeError", "OfficeBackend", "DemoOfficeBackend",
           "get_office_backend", "set_office_backend"]


class OfficeError(Exception):
    """办公后端业务错误（资源不存在 / 时间冲突 / 参数非法）。"""


# ---------- 接口契约（真实系统实现此接口即可替换） ----------


class OfficeBackend(ABC):
    """办公能力后端契约（会议室 + 订餐）。

    真实实现（飞书/钉钉/美餐）在保持方法签名与返回结构不变的前提下替换本类；
    Agent 与统一工具层只依赖本契约，不依赖具体厂商。
    """

    # 会议室
    @abstractmethod
    def list_rooms(self, capacity_min: Optional[int] = None,
                   equipment: Optional[list[str]] = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def find_available_rooms(self, date: str, start: str, end: str,
                             capacity_min: Optional[int] = None,
                             equipment: Optional[list[str]] = None
                             ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def book_room(self, room_id: str, date: str, start: str, end: str,
                  title: str, attendees: int, booker: str) -> dict[str, Any]: ...

    @abstractmethod
    def cancel_booking(self, booking_id: str, booker: str) -> dict[str, Any]: ...

    @abstractmethod
    def list_bookings(self, booker: Optional[str] = None) -> list[dict[str, Any]]: ...

    # 订餐
    @abstractmethod
    def list_restaurants(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_menu(self, restaurant_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def place_order(self, restaurant_id: str, items: list[dict[str, Any]],
                    meal_time: str, orderer: str, note: str = ""
                    ) -> dict[str, Any]: ...

    @abstractmethod
    def cancel_order(self, order_id: str, orderer: str) -> dict[str, Any]: ...

    @abstractmethod
    def list_orders(self, orderer: Optional[str] = None) -> list[dict[str, Any]]: ...

    # OA 自助办理：报销
    @abstractmethod
    def submit_expense(self, category: str, amount: float, expense_date: str,
                       note: str, applicant: str, invoice_no: str = ""
                       ) -> dict[str, Any]: ...

    @abstractmethod
    def list_expenses(self, applicant: Optional[str] = None
                      ) -> list[dict[str, Any]]: ...

    # OA 自助办理：请假
    @abstractmethod
    def apply_leave(self, leave_type: str, start_date: str, end_date: str,
                    applicant: str, reason: str = "",
                    days: Optional[float] = None) -> dict[str, Any]: ...

    @abstractmethod
    def list_leaves(self, applicant: Optional[str] = None
                    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def leave_balance(self, applicant: str) -> dict[str, Any]: ...

    # OA 自助办理：补考勤
    @abstractmethod
    def submit_attendance_fix(self, fix_type: str, work_date: str, time: str,
                              applicant: str, reason: str = ""
                              ) -> dict[str, Any]: ...

    @abstractmethod
    def list_attendance_fixes(self, applicant: Optional[str] = None
                              ) -> list[dict[str, Any]]: ...


# ---------- demo 实现（内存种子数据） ----------

# 会议室种子：``need_approval`` 标记高影响资源（需人工确认）
_ROOMS: list[dict[str, Any]] = [
    {"room_id": "MR-A-201", "name": "A栋201 · 小会议室", "floor": "A栋2F",
     "capacity": 6, "equipment": ["白板"], "need_approval": False},
    {"room_id": "MR-A-202", "name": "A栋202 · 中会议室", "floor": "A栋2F",
     "capacity": 12, "equipment": ["白板", "投影"], "need_approval": False},
    {"room_id": "MR-B-301", "name": "B栋301 · 视频会议室", "floor": "B栋3F",
     "capacity": 16, "equipment": ["视频会议", "投影"], "need_approval": False},
    {"room_id": "MR-B-401", "name": "B栋401 · 培训室", "floor": "B栋4F",
     "capacity": 30, "equipment": ["视频会议", "投影", "音响"],
     "need_approval": True},
    {"room_id": "MR-C-101", "name": "C栋101 · 报告厅", "floor": "C栋1F",
     "capacity": 80, "equipment": ["视频会议", "投影", "音响", "舞台"],
     "need_approval": True},
]

# 餐厅与菜单种子：``need_approval`` 标记商务宴请类（需人工确认）
_RESTAURANTS: list[dict[str, Any]] = [
    {"restaurant_id": "RT-01", "name": "园区一食堂", "type": "员工食堂",
     "deliver": True, "need_approval": False,
     "menu": [
         {"item_id": "M101", "name": "红烧狮子头套餐", "price": 15.0, "tag": "荤"},
         {"item_id": "M102", "name": "青椒肉丝套餐", "price": 14.0, "tag": "荤"},
         {"item_id": "M103", "name": "香菇青菜套餐", "price": 12.0, "tag": "素"},
         {"item_id": "M104", "name": "番茄鸡蛋盖饭", "price": 13.0, "tag": "荤"},
     ]},
    {"restaurant_id": "RT-02", "name": "园区二食堂", "type": "员工食堂",
     "deliver": True, "need_approval": False,
     "menu": [
         {"item_id": "M201", "name": "麻辣香锅", "price": 18.0, "tag": "辣"},
         {"item_id": "M202", "name": "牛肉面", "price": 16.0, "tag": "荤"},
         {"item_id": "M203", "name": "杂粮轻食沙拉", "price": 20.0, "tag": "素"},
     ]},
    {"restaurant_id": "RT-03", "name": "商务餐厅（客餐）", "type": "商务宴请",
     "deliver": True, "need_approval": True,
     "menu": [
         {"item_id": "M301", "name": "商务套餐A（含汤/果盘）", "price": 88.0, "tag": "客餐"},
         {"item_id": "M302", "name": "商务套餐B（含汤/果盘）", "price": 128.0, "tag": "客餐"},
     ]},
]

# 单笔餐费（元）达到该额度 → 视为高影响，建议人工确认
ORDER_APPROVAL_AMOUNT: float = 500.0

# ---------- OA 自助办理（报销 / 请假 / 补考勤） ----------
# 单笔报销（元）达到该额度 → 视为高影响，需人工确认
EXPENSE_APPROVAL_AMOUNT: float = 5000.0
# 请假天数达到该阈值 → 视为长假，需人工确认（部门主管）
LEAVE_LONG_DAYS: float = 3.0
# 合法枚举（与前端弹窗选项、工具 schema 保持一致）
_LEAVE_TYPES: list[str] = ["年假", "事假", "病假", "调休", "婚假", "产假"]
_EXPENSE_CATEGORIES: list[str] = ["差旅", "招待", "办公用品", "交通", "其他"]
_ATTENDANCE_FIX_TYPES: list[str] = ["上班补卡", "下班补卡"]


def _to_minutes(hhmm: str) -> int:
    """'HH:MM' → 当日分钟数（用于时段冲突判定）。"""
    text = str(hhmm or "").strip()
    try:
        hh, mm = text.split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        raise OfficeError(f"时间格式非法: {hhmm!r}（应为 HH:MM）") from None


def _overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    """两个时段是否重叠（半开区间 [start, end)）。"""
    a0, a1 = _to_minutes(start_a), _to_minutes(end_a)
    b0, b1 = _to_minutes(start_b), _to_minutes(end_b)
    return a0 < b1 and b0 < a1


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(text: str) -> datetime:
    """'YYYY-MM-DD' → datetime（用于请假天数计算）；非法即报错。"""
    try:
        return datetime.strptime(str(text or "").strip(), "%Y-%m-%d")
    except (ValueError, TypeError):
        raise OfficeError(f"日期格式非法: {text!r}（应为 YYYY-MM-DD）") from None


def _natural_days(start_date: str, end_date: str) -> float:
    """按自然日含端点计算天数（2026-10-20 ~ 2026-10-20 = 1 天）。"""
    delta = (_parse_date(end_date) - _parse_date(start_date)).days
    if delta < 0:
        raise OfficeError(f"结束日期不能早于开始日期（{start_date} ~ {end_date}）")
    return float(delta + 1)


class DemoOfficeBackend(OfficeBackend):
    """内存实现：种子会议室/餐厅 + 预订/订单记录（线程安全，进程级）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rooms = [dict(r) for r in _ROOMS]
        self._restaurants = [dict(r) for r in _RESTAURANTS]
        self._bookings: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._expenses: dict[str, dict[str, Any]] = {}
        self._leaves: dict[str, dict[str, Any]] = {}
        self._attendance_fixes: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._seq += 1
            return f"{prefix}{self._seq:05d}"

    # ---------- 会议室 ----------

    def list_rooms(self, capacity_min: Optional[int] = None,
                   equipment: Optional[list[str]] = None) -> list[dict[str, Any]]:
        out = []
        for room in self._rooms:
            if capacity_min is not None and int(room["capacity"]) < int(capacity_min):
                continue
            if equipment and not set(equipment).issubset(set(room["equipment"])):
                continue
            out.append(dict(room))
        return out

    def _room(self, room_id: str) -> dict[str, Any]:
        for room in self._rooms:
            if room["room_id"] == room_id:
                return room
        raise OfficeError(f"会议室不存在: {room_id!r}")

    def _room_conflicts(self, room_id: str, date: str, start: str, end: str
                        ) -> list[dict[str, Any]]:
        with self._lock:
            return [b for b in self._bookings.values()
                    if b["room_id"] == room_id and b["date"] == date
                    and b["status"] == "reserved"
                    and _overlap(start, end, b["start"], b["end"])]

    def find_available_rooms(self, date: str, start: str, end: str,
                             capacity_min: Optional[int] = None,
                             equipment: Optional[list[str]] = None
                             ) -> list[dict[str, Any]]:
        _to_minutes(start), _to_minutes(end)  # 参数校验（非法即报错）
        available = []
        for room in self.list_rooms(capacity_min, equipment):
            if not self._room_conflicts(room["room_id"], date, start, end):
                available.append(room)
        return available

    def book_room(self, room_id: str, date: str, start: str, end: str,
                  title: str, attendees: int, booker: str) -> dict[str, Any]:
        if _to_minutes(start) >= _to_minutes(end):
            raise OfficeError(f"开始时间必须早于结束时间（{start}-{end}）")
        room = self._room(room_id)
        if int(attendees) > int(room["capacity"]):
            raise OfficeError(
                f"人数 {attendees} 超出 {room['name']} 容量 {room['capacity']}")
        conflicts = self._room_conflicts(room_id, date, start, end)
        if conflicts:
            c = conflicts[0]
            raise OfficeError(
                f"时段冲突：{room['name']} 在 {date} {c['start']}-{c['end']} "
                f"已被 {c['booker']} 预订（{c['title']}），请改选其他时段或房间")
        booking = {
            "booking_id": self._next_id("BK"),
            "room_id": room_id, "room_name": room["name"],
            "date": date, "start": start, "end": end,
            "title": title or "未命名会议", "attendees": int(attendees),
            "booker": booker, "status": "reserved", "created_at": _now_iso(),
            "need_approval": bool(room.get("need_approval")),
        }
        with self._lock:
            self._bookings[booking["booking_id"]] = booking
        return dict(booking)

    def cancel_booking(self, booking_id: str, booker: str) -> dict[str, Any]:
        with self._lock:
            booking = self._bookings.get(booking_id)
            if booking is None:
                raise OfficeError(f"预订记录不存在: {booking_id!r}")
            if booking["booker"] != booker:
                raise OfficeError(
                    f"只能取消本人预订（该单属于 {booking['booker']}）")
            booking["status"] = "cancelled"
            booking["cancelled_at"] = _now_iso()
            return dict(booking)

    def list_bookings(self, booker: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(b) for b in self._bookings.values()
                     if b["status"] == "reserved"]
        if booker:
            items = [b for b in items if b["booker"] == booker]
        return sorted(items, key=lambda b: (b["date"], b["start"]))

    # ---------- 订餐 ----------

    def list_restaurants(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "menu"}
                for r in self._restaurants]

    def _restaurant(self, restaurant_id: str) -> dict[str, Any]:
        for r in self._restaurants:
            if r["restaurant_id"] == restaurant_id:
                return r
        raise OfficeError(f"餐厅不存在: {restaurant_id!r}")

    def get_menu(self, restaurant_id: str) -> dict[str, Any]:
        r = self._restaurant(restaurant_id)
        return {"restaurant_id": r["restaurant_id"], "name": r["name"],
                "type": r["type"], "need_approval": r.get("need_approval", False),
                "items": [dict(i) for i in r["menu"]]}

    def place_order(self, restaurant_id: str, items: list[dict[str, Any]],
                    meal_time: str, orderer: str, note: str = ""
                    ) -> dict[str, Any]:
        r = self._restaurant(restaurant_id)
        if not items:
            raise OfficeError("订单明细为空（items 至少一项）")
        menu = {i["item_id"]: i for i in r["menu"]}
        lines: list[dict[str, Any]] = []
        total_qty, total_amount = 0, 0.0
        for raw in items:
            item_id = str(raw.get("item_id") or "").strip()
            qty = int(raw.get("qty") or 0)
            if item_id not in menu:
                raise OfficeError(
                    f"{r['name']} 菜单无此菜品: {item_id!r}"
                    f"（可选: {sorted(menu)}）")
            if qty <= 0:
                raise OfficeError(f"菜品 {item_id} 数量必须为正整数")
            price = float(menu[item_id]["price"])
            lines.append({"item_id": item_id, "name": menu[item_id]["name"],
                          "price": price, "qty": qty,
                          "subtotal": round(price * qty, 2)})
            total_qty += qty
            total_amount += price * qty
        total_amount = round(total_amount, 2)
        order = {
            "order_id": self._next_id("OD"),
            "restaurant_id": restaurant_id, "restaurant_name": r["name"],
            "meal_time": meal_time or "午餐", "items": lines,
            "total_qty": total_qty, "total_amount": total_amount,
            "note": note, "orderer": orderer, "status": "placed",
            "created_at": _now_iso(),
            "need_approval": bool(r.get("need_approval"))
                             or total_amount >= ORDER_APPROVAL_AMOUNT,
        }
        with self._lock:
            self._orders[order["order_id"]] = order
        return dict(order)

    def cancel_order(self, order_id: str, orderer: str) -> dict[str, Any]:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise OfficeError(f"订单不存在: {order_id!r}")
            if order["orderer"] != orderer:
                raise OfficeError(f"只能取消本人订单（该单属于 {order['orderer']}）")
            order["status"] = "cancelled"
            order["cancelled_at"] = _now_iso()
            return dict(order)

    def list_orders(self, orderer: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(o) for o in self._orders.values()
                     if o["status"] == "placed"]
        if orderer:
            items = [o for o in items if o["orderer"] == orderer]
        return sorted(items, key=lambda o: o["created_at"])

    # ---------- OA 自助办理：报销 ----------

    def submit_expense(self, category: str, amount: float, expense_date: str,
                       note: str, applicant: str, invoice_no: str = ""
                       ) -> dict[str, Any]:
        cat = str(category or "").strip()
        if cat not in _EXPENSE_CATEGORIES:
            raise OfficeError(
                f"报销类别非法: {category!r}（可选: {_EXPENSE_CATEGORIES}）")
        try:
            amt = float(amount)
        except (ValueError, TypeError):
            raise OfficeError(f"报销金额非法: {amount!r}") from None
        if amt <= 0:
            raise OfficeError(f"报销金额必须为正数（当前 {amt}）")
        if not str(applicant or "").strip():
            raise OfficeError("报销申请人缺失")
        expense = {
            "expense_id": self._next_id("BX"),
            "category": cat, "amount": round(amt, 2),
            "expense_date": str(expense_date or ""), "note": note or "",
            "invoice_no": invoice_no or "",
            "applicant": applicant, "status": "pending_approval",
            "need_approval": True,
            "high_value": amt >= EXPENSE_APPROVAL_AMOUNT,
            "created_at": _now_iso(),
        }
        with self._lock:
            self._expenses[expense["expense_id"]] = expense
        return dict(expense)

    def list_expenses(self, applicant: Optional[str] = None
                      ) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(e) for e in self._expenses.values()
                     if e["status"] != "cancelled"]
        if applicant:
            items = [e for e in items if e["applicant"] == applicant]
        return sorted(items, key=lambda e: e["created_at"], reverse=True)

    # ---------- OA 自助办理：请假 ----------

    def apply_leave(self, leave_type: str, start_date: str, end_date: str,
                    applicant: str, reason: str = "",
                    days: Optional[float] = None) -> dict[str, Any]:
        lt = str(leave_type or "").strip()
        if lt not in _LEAVE_TYPES:
            raise OfficeError(
                f"请假类型非法: {leave_type!r}（可选: {_LEAVE_TYPES}）")
        if not str(applicant or "").strip():
            raise OfficeError("请假申请人缺失")
        span = _natural_days(start_date, end_date)
        if days is None:
            use_days = span
        else:
            try:
                use_days = float(days)
            except (ValueError, TypeError):
                raise OfficeError(f"请假天数非法: {days!r}") from None
            if use_days <= 0:
                raise OfficeError(f"请假天数必须为正数（当前 {use_days}）")
        leave = {
            "leave_id": self._next_id("QJ"),
            "leave_type": lt, "start_date": str(start_date or ""),
            "end_date": str(end_date or ""), "days": round(use_days, 2),
            "reason": reason or "", "applicant": applicant,
            "status": "pending_approval", "need_approval": True,
            "long_leave": use_days >= LEAVE_LONG_DAYS,
            "created_at": _now_iso(),
        }
        with self._lock:
            self._leaves[leave["leave_id"]] = leave
        return dict(leave)

    def list_leaves(self, applicant: Optional[str] = None
                    ) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(l) for l in self._leaves.values()
                     if l["status"] != "cancelled"]
        if applicant:
            items = [l for l in items if l["applicant"] == applicant]
        return sorted(items, key=lambda l: l["created_at"], reverse=True)

    def leave_balance(self, applicant: str) -> dict[str, Any]:
        """年假/调休余额（demo 固定额度；真实实现查 HR 系统）。"""
        return {"applicant": applicant, "annual_remaining": 8.5,
                "comp_remaining": 2.0, "used_this_year": 3.5}

    # ---------- OA 自助办理：补考勤 ----------

    def submit_attendance_fix(self, fix_type: str, work_date: str, time: str,
                              applicant: str, reason: str = ""
                              ) -> dict[str, Any]:
        ft = str(fix_type or "").strip()
        if ft not in _ATTENDANCE_FIX_TYPES:
            raise OfficeError(
                f"补卡类型非法: {fix_type!r}（可选: {_ATTENDANCE_FIX_TYPES}）")
        if not str(applicant or "").strip():
            raise OfficeError("补卡申请人缺失")
        if not str(work_date or "").strip():
            raise OfficeError("补卡日期缺失（work_date=YYYY-MM-DD）")
        try:
            _to_minutes(time)
        except OfficeError:
            raise OfficeError(f"补卡时间非法: {time!r}（应为 HH:MM）") from None
        fix = {
            "fix_id": self._next_id("KB"),
            "fix_type": ft, "work_date": str(work_date), "time": str(time),
            "reason": reason or "", "applicant": applicant,
            "status": "pending_approval", "need_approval": True,
            "created_at": _now_iso(),
        }
        with self._lock:
            self._attendance_fixes[fix["fix_id"]] = fix
        return dict(fix)

    def list_attendance_fixes(self, applicant: Optional[str] = None
                              ) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(a) for a in self._attendance_fixes.values()
                     if a["status"] != "cancelled"]
        if applicant:
            items = [a for a in items if a["applicant"] == applicant]
        return sorted(items, key=lambda a: a["created_at"], reverse=True)


# ---------- 进程级单例（默认 demo 实现；测试/生产可 set 替换） ----------

_backend: Optional[OfficeBackend] = None
_backend_lock = threading.Lock()


def get_office_backend() -> OfficeBackend:
    """取办公后端（默认 DemoOfficeBackend；可用 ``set_office_backend`` 替换）。"""
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = DemoOfficeBackend()
        return _backend


def set_office_backend(backend: OfficeBackend) -> None:
    """替换办公后端（真实系统接入点：注入飞书/钉钉/美餐适配器）。"""
    global _backend
    with _backend_lock:
        _backend = backend
