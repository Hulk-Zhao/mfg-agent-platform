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

import hashlib
import re
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = ["OfficeError", "OfficeBackend", "DemoOfficeBackend",
           "get_office_backend", "set_office_backend",
           "AttachmentStore", "get_attachment_store", "set_attachment_store",
           "organize_expense", "EXPENSE_SUBJECTS", "EXPENSE_REQUIRED_KINDS",
           "ATTACHMENT_KINDS", "EXPENSE_ATTACHMENT_KINDS", "LEAVE_ATTACHMENT_KINDS",
           "SERVICE_KINDS", "SERVICE_KIND_NAMES", "SERVICE_ASSIGNEE", "SERVICE_URGENCIES",
           "SERVICE_REQUIRED", "SERVICE_FIELD_LABELS", "SERVICE_ATTACHMENT_KINDS"]


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
                       note: str, applicant: str, invoice_no: str = "",
                       attachments: Optional[list[dict[str, Any]]] = None,
                       subject: str = "",
                       breakdown: Optional[list[dict[str, Any]]] = None
                       ) -> dict[str, Any]: ...

    @abstractmethod
    def list_expenses(self, applicant: Optional[str] = None
                      ) -> list[dict[str, Any]]: ...

    # OA 自助办理：请假
    @abstractmethod
    def apply_leave(self, leave_type: str, start_date: str, end_date: str,
                    applicant: str, reason: str = "",
                    days: Optional[float] = None,
                    attachments: Optional[list[dict[str, Any]]] = None
                    ) -> dict[str, Any]: ...

    @abstractmethod
    def list_leaves(self, applicant: Optional[str] = None
                    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def leave_balance(self, applicant: str) -> dict[str, Any]: ...

    # OA 自助办理：病假条等材料补件（提交后补充，仅限本人单据）
    @abstractmethod
    def supplement_leave_document(self, leave_id: str,
                                  attachment: dict[str, Any],
                                  applicant: str) -> dict[str, Any]: ...

    # OA 自助办理：补考勤
    @abstractmethod
    def submit_attendance_fix(self, fix_type: str, work_date: str, time: str,
                              applicant: str, reason: str = ""
                              ) -> dict[str, Any]: ...

    @abstractmethod
    def list_attendance_fixes(self, applicant: Optional[str] = None
                              ) -> list[dict[str, Any]]: ...

    # OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）：
    # 一个 kind 参数覆盖后续更多 OA 服务，新增一类只加 kind，不新增方法
    @abstractmethod
    def submit_service_request(self, kind: str, title: str, detail: str, applicant: str,
                               urgency: str = "一般", location: str = "",
                               extra: Optional[dict[str, Any]] = None,
                               attachments: Optional[list[dict[str, Any]]] = None
                               ) -> dict[str, Any]: ...

    @abstractmethod
    def list_service_requests(self, applicant: Optional[str] = None
                              ) -> list[dict[str, Any]]: ...


# ---------- demo 实现（内存种子数据） ----------

# 会议室种子：``need_approval`` 标记高影响资源（需人工确认）
# 覆盖 A/B/C/D 四栋、容量 4~80 人：日常小会（4~8 人）到报告厅/多功能厅（50~80 人），
# 让「≥8 人 / ≥20 人 / 需授权」三个筛选项都点得动，也给「人数超容量」「时段冲突」
# 这类反例留足可选项。容量 ≥20 的与提示词口径一致，一律标记 need_approval。
_ROOMS: list[dict[str, Any]] = [
    # A 栋 2F —— 日常会议层（部门周会、评审会）
    {"room_id": "MR-A-201", "name": "A栋201 · 小会议室", "floor": "A栋2F",
     "capacity": 6, "equipment": ["白板"], "need_approval": False},
    {"room_id": "MR-A-202", "name": "A栋202 · 中会议室", "floor": "A栋2F",
     "capacity": 12, "equipment": ["白板", "投影"], "need_approval": False},
    {"room_id": "MR-A-203", "name": "A栋203 · 洽谈室", "floor": "A栋2F",
     "capacity": 4, "equipment": ["白板", "电视"], "need_approval": False},
    {"room_id": "MR-A-301", "name": "A栋301 · 大会议室", "floor": "A栋3F",
     "capacity": 24, "equipment": ["白板", "投影", "视频会议"],
     "need_approval": True},
    # B 栋 3F/4F —— 项目与培训
    {"room_id": "MR-B-301", "name": "B栋301 · 视频会议室", "floor": "B栋3F",
     "capacity": 16, "equipment": ["视频会议", "投影"], "need_approval": False},
    {"room_id": "MR-B-302", "name": "B栋302 · 项目讨论室", "floor": "B栋3F",
     "capacity": 10, "equipment": ["白板", "电视"], "need_approval": False},
    {"room_id": "MR-B-303", "name": "B栋303 · 面试间", "floor": "B栋3F",
     "capacity": 6, "equipment": ["电视"], "need_approval": False},
    {"room_id": "MR-B-401", "name": "B栋401 · 培训室", "floor": "B栋4F",
     "capacity": 30, "equipment": ["视频会议", "投影", "音响"],
     "need_approval": True},
    {"room_id": "MR-B-402", "name": "B栋402 · 品质分析室", "floor": "B栋4F",
     "capacity": 12, "equipment": ["白板", "投影"], "need_approval": False},
    # C 栋 1F/3F —— 对外接待与大型活动
    {"room_id": "MR-C-101", "name": "C栋101 · 报告厅", "floor": "C栋1F",
     "capacity": 80, "equipment": ["视频会议", "投影", "音响", "舞台"],
     "need_approval": True},
    {"room_id": "MR-C-201", "name": "C栋201 · 贵宾接待室", "floor": "C栋2F",
     "capacity": 8, "equipment": ["电视", "白板"], "need_approval": True},
    {"room_id": "MR-C-301", "name": "C栋301 · 多功能厅", "floor": "C栋3F",
     "capacity": 50, "equipment": ["投影", "音响", "舞台"], "need_approval": True},
    # D 栋 —— 贴近产线，班前会 / 现场问题分析
    {"room_id": "MR-D-101", "name": "D栋101 · 产线会议室", "floor": "D栋1F",
     "capacity": 10, "equipment": ["白板"], "need_approval": False},
    {"room_id": "MR-D-102", "name": "D栋102 · 现场作战室", "floor": "D栋1F",
     "capacity": 8, "equipment": ["白板", "电视"], "need_approval": False},
]

# 餐厅与菜单种子：``need_approval`` 标记商务宴请类（需人工确认）
# 四个餐厅 28 道菜：一食堂（套餐/盖饭/汤）、二食堂（香锅/面档/轻食/早餐）、
# 商务餐厅（客餐套餐/桌餐/茶歇，含 ≥500 元整单）、三食堂夜宵档（需自取）。
_RESTAURANTS: list[dict[str, Any]] = [
    {"restaurant_id": "RT-01", "name": "园区一食堂", "type": "员工食堂",
     "deliver": True, "need_approval": False,
     "menu": [
         {"item_id": "M101", "name": "红烧狮子头套餐", "price": 15.0, "tag": "荤"},
         {"item_id": "M102", "name": "青椒肉丝套餐", "price": 14.0, "tag": "荤"},
         {"item_id": "M103", "name": "香菇青菜套餐", "price": 12.0, "tag": "素"},
         {"item_id": "M104", "name": "番茄鸡蛋盖饭", "price": 13.0, "tag": "荤"},
         {"item_id": "M105", "name": "梅菜扣肉套餐", "price": 16.0, "tag": "荤"},
         {"item_id": "M106", "name": "咖喱鸡块饭", "price": 16.0, "tag": "荤"},
         {"item_id": "M107", "name": "清炒时蔬套餐", "price": 11.0, "tag": "素"},
         {"item_id": "M108", "name": "紫菜蛋花汤", "price": 3.0, "tag": "汤"},
     ]},
    {"restaurant_id": "RT-02", "name": "园区二食堂", "type": "员工食堂",
     "deliver": True, "need_approval": False,
     "menu": [
         {"item_id": "M201", "name": "麻辣香锅", "price": 18.0, "tag": "辣"},
         {"item_id": "M202", "name": "牛肉面", "price": 16.0, "tag": "荤"},
         {"item_id": "M203", "name": "杂粮轻食沙拉", "price": 20.0, "tag": "素"},
         {"item_id": "M204", "name": "酸辣粉", "price": 12.0, "tag": "辣"},
         {"item_id": "M205", "name": "番茄牛腩面", "price": 18.0, "tag": "荤"},
         {"item_id": "M206", "name": "扬州炒饭", "price": 14.0, "tag": "荤"},
         {"item_id": "M207", "name": "手工蒸饺（8 只）", "price": 10.0, "tag": "荤"},
         {"item_id": "M208", "name": "豆浆 + 茶叶蛋", "price": 6.0, "tag": "早"},
     ]},
    {"restaurant_id": "RT-03", "name": "商务餐厅", "type": "商务宴请",
     "deliver": True, "need_approval": True,
     "menu": [
         {"item_id": "M301", "name": "商务套餐A（含汤/果盘）", "price": 88.0, "tag": "客餐"},
         {"item_id": "M302", "name": "商务套餐B（含汤/果盘）", "price": 128.0, "tag": "客餐"},
         {"item_id": "M303", "name": "商务简餐（一荤两素）", "price": 58.0, "tag": "客餐"},
         {"item_id": "M304", "name": "桌餐（8 人席）", "price": 688.0, "tag": "客餐"},
         {"item_id": "M305", "name": "会议茶歇（20 人份）", "price": 388.0, "tag": "茶歇"},
         {"item_id": "M306", "name": "时令果盘", "price": 38.0, "tag": "茶歇"},
     ]},
    {"restaurant_id": "RT-04", "name": "园区三食堂（夜宵档）", "type": "员工食堂",
     "deliver": False, "need_approval": False,
     "menu": [
         {"item_id": "M401", "name": "牛肉炒面", "price": 16.0, "tag": "夜宵"},
         {"item_id": "M402", "name": "蛋炒饭", "price": 12.0, "tag": "夜宵"},
         {"item_id": "M403", "name": "小馄饨", "price": 10.0, "tag": "夜宵"},
         {"item_id": "M404", "name": "关东煮（5 串）", "price": 12.0, "tag": "夜宵"},
         {"item_id": "M405", "name": "烤肠", "price": 5.0, "tag": "夜宵"},
         {"item_id": "M406", "name": "冰豆浆", "price": 4.0, "tag": "饮"},
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

# ---------- OA 材料上传（报销票据 / 出差报告 / 病假条 / IT 与车辆材料等） ----------
# 材料类别全集（与前端上传控件、工具 schema、落盘存储保持一致）
ATTACHMENT_KINDS: list[str] = ["发票", "出差报告", "行程单", "付款凭证", "病假条",
                               "故障照片", "授权审批单", "行驶证", "用车申请单", "其他"]
# 报销可选上传的材料（发票之外的说明性材料，均为**选传**）
EXPENSE_ATTACHMENT_KINDS: list[str] = ["发票", "出差报告", "行程单", "付款凭证", "其他"]
# 请假可上传的材料（病假条为病假的补件材料）
LEAVE_ATTACHMENT_KINDS: list[str] = ["病假条", "其他"]

# ---------- OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位申请 / 访客车辆报备 / 公务用车） ----------
# 服务工单类型（kind）：新增一类 OA 服务只需在此加一个 kind（不必新增后端方法）
SERVICE_KINDS: list[str] = ["it_repair", "it_access", "it_device",
                            "parking", "visitor_vehicle", "business_car"]
# kind → 中文名（徽标、单据列表、工单标题统一用它）
SERVICE_KIND_NAMES: dict[str, str] = {
    "it_repair": "IT 故障报修",
    "it_access": "账号与权限申请",
    "it_device": "设备领用申请",
    "parking": "车位申请",
    "visitor_vehicle": "访客车辆报备",
    "business_car": "公务用车申请",
}
# kind → 单号前缀（写操作留痕，与报销 BX / 请假 QJ 同构）
SERVICE_ID_PREFIX: dict[str, str] = {
    "it_repair": "ITRP", "it_access": "ITAC", "it_device": "ITDV",
    "parking": "PK", "visitor_vehicle": "VS", "business_car": "BC",
}
# kind → 归口（审批人标签；与既有「财务专员 / 部门主管 / HR 专员」同构的自由文本标签，
# 不涉及治理槽位与角色权限，故无需改 permissions / 角色配置）
SERVICE_ASSIGNEE: dict[str, str] = {
    "it_repair": "IT 运维专员", "it_access": "IT 运维专员", "it_device": "IT 运维专员",
    "parking": "行政后勤专员", "visitor_vehicle": "行政后勤专员",
    "business_car": "行政后勤专员",
}
# 紧急程度枚举（IT 报修常用；其余 kind 默认"一般"）
SERVICE_URGENCIES: list[str] = ["一般", "紧急", "非常紧急"]
# kind → 必填项（键取自 title/detail/location 与 extra；缺一即 reject，fail-closed 不靠 LLM 自觉）
SERVICE_REQUIRED: dict[str, list[str]] = {
    "it_repair": ["title", "location"],
    "it_access": ["title", "system", "permission"],
    "it_device": ["title", "device_type"],
    "parking": ["title", "plate"],
    "visitor_vehicle": ["title", "plate", "visit_date"],
    "business_car": ["title", "dest", "use_from"],
}
# 必填项的中文名（错误信息与前端提示共用）
SERVICE_FIELD_LABELS: dict[str, str] = {
    "title": "事由 / 现象", "detail": "补充说明", "location": "设备与位置",
    "system": "系统", "permission": "权限项", "device_type": "设备类型",
    "plate": "车牌", "visit_date": "来访日期", "dest": "目的地",
    "use_from": "用车起始时间",
}
# 服务工单可上传的材料类别（前端上传芯片用；须在 ATTACHMENT_KINDS 白名单内）
SERVICE_ATTACHMENT_KINDS: list[str] = ["故障照片", "授权审批单", "行驶证", "用车申请单", "其他"]
# 报销**必传**材料（按报销类别；未列出的类别不强制，缺件仅作提示不阻断演示）
EXPENSE_REQUIRED_KINDS: dict[str, list[str]] = {
    "差旅": ["发票"], "招待": ["发票"], "办公用品": ["发票"],
    "交通": ["发票"], "其他": [],
}
# 报销二级科目（一级=报销类别；"整理报销科目"按此清单归类）
EXPENSE_SUBJECTS: dict[str, list[str]] = {
    "差旅": ["城市间交通", "住宿费", "伙食补助", "市内交通", "其他差旅"],
    "招待": ["客户餐饮", "商务礼品", "会务茶歇"],
    "办公用品": ["办公耗材", "设备配件", "软件服务"],
    "交通": ["公共交通", "停车费", "油费过路费"],
    "其他": ["其他"],
}
# 事由 → 二级科目 的确定性映射（关键词命中即归类；用于"整理报销科目"）
_SUBJECT_KEYWORDS: list[tuple[tuple[str, ...], str, str]] = [
    (("高铁", "动车", "火车", "机票", "飞机", "城际"), "差旅", "城市间交通"),
    (("住宿", "酒店", "宾馆", "民宿"), "差旅", "住宿费"),
    (("餐补", "伙食", "餐费", "餐饮", "工作餐"), "差旅", "伙食补助"),
    (("打车", "出租", "网约", "滴滴", "地铁", "公交", "市内"), "差旅", "市内交通"),
    (("油费", "加油", "过路", "高速"), "交通", "油费过路费"),
    (("停车",), "交通", "停车费"),
    (("客户餐", "宴请", "招待餐"), "招待", "客户餐饮"),
    (("礼品", "伴手礼", "礼盒"), "招待", "商务礼品"),
    (("茶歇", "会务"), "招待", "会务茶歇"),
    (("耗材", "墨盒", "打印", "文具", "硒鼓"), "办公用品", "办公耗材"),
    (("配件", "设备", "工装"), "办公用品", "设备配件"),
    (("软件", "订阅", "SaaS", "license"), "办公用品", "软件服务"),
]
# 单份材料大小上限（MB）：真实场景必须限制，避免大文件拖垮上传/审批链路
ATTACHMENT_MAX_MB: float = 10.0


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


def _required_kinds(category: str) -> list[str]:
    return list(EXPENSE_REQUIRED_KINDS.get(str(category or "").strip(), []))


def _missing_required(category: str, provided: Any) -> list[str]:
    """按报销类别核对**必传**材料：返回缺失的必传类别（选传缺件不计）。"""
    have = {str(k).strip() for k in (provided or []) if str(k).strip()}
    return [k for k in _required_kinds(category) if k not in have]


def organize_expense(category: str, amount: float, note: str = "",
                     attachment_kinds: Optional[list[str]] = None) -> dict[str, Any]:
    """整理报销科目（确定性规则，不需要 LLM）：输出二级科目清单 + 材料核对 + 事由拆分。

    - 校验类别与金额（非法即 ``OfficeError``）；
    - 从「事由」里按关键词把金额拆到二级科目（如「高铁 980 + 住宿 306」→ 两条明细）；
    - 拆分后余额归入该类别兜底科目（差旅→其他差旅、交通→公共交通…）；
    - 按类别给出**必传/选传**材料与缺失的必传项（缺件只作提示，不阻断提交）。
    """
    cat = str(category or "").strip()
    if cat not in _EXPENSE_CATEGORIES:
        raise OfficeError(f"报销类别非法: {category!r}（可选: {_EXPENSE_CATEGORIES}）")
    try:
        amt = float(amount)
    except (ValueError, TypeError):
        raise OfficeError(f"报销金额非法: {amount!r}") from None
    if amt <= 0:
        raise OfficeError(f"报销金额必须为正数（当前 {amt}）")

    subjects = list(EXPENSE_SUBJECTS.get(cat, ["其他"]))
    default_subject = subjects[-1]

    # 事由拆分：按分隔符切段，段内命中关键词 + 尾部数字 → 一条明细
    items: list[dict[str, Any]] = []
    first_subject = ""      # 首个命中的二级科目（无金额明细时作整单兜底科目）
    for piece in re.split(r"[+＋、,，;；/]|以及|和", str(note or "")):
        seg = piece.strip()
        if not seg:
            continue
        hit: Optional[tuple[str, str]] = None
        for kws, c1, subj in _SUBJECT_KEYWORDS:
            if any(k in seg for k in kws):
                hit = (c1, subj)
                break
        if not hit:
            continue
        if not first_subject:
            first_subject = hit[1]
        nums = re.findall(r"(\d+(?:\.\d+)?)", seg)
        if not nums:
            continue        # 只有科目词、没有金额：不构成一条明细（按整单归入兜底科目）
        items.append({"subject": hit[1], "subject_l1": hit[0],
                      "amount": float(nums[-1]), "note": seg})

    known = round(sum(i["amount"] for i in items if i["amount"]), 2)
    rest = round(amt - known, 2)
    if not items:
        items = [{"subject": first_subject or default_subject, "subject_l1": cat,
                  "amount": round(amt, 2), "note": str(note or "").strip()}]
        rest = 0.0
    elif rest > 0.005:
        items.append({"subject": default_subject, "subject_l1": cat,
                      "amount": rest, "note": "未细分的余额"})

    provided = sorted({str(k).strip() for k in (attachment_kinds or []) if str(k).strip()})
    required = _required_kinds(cat)
    missing = _missing_required(cat, provided)
    return {
        "ok": True, "category": cat, "amount": round(amt, 2),
        "subjects": subjects,
        "items": items, "items_total": round(amt, 2),
        "required_kinds": required,
        "optional_kinds": [k for k in EXPENSE_ATTACHMENT_KINDS if k not in required],
        "provided_kinds": provided, "missing_required": missing,
        "note": f"按「{cat}」科目整理 {len(items)} 条明细"
                + (f"，缺必传材料：{'、'.join(missing)}" if missing else ""),
    }


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
        self._service_requests: dict[str, dict[str, Any]] = {}
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
                       note: str, applicant: str, invoice_no: str = "",
                       attachments: Optional[list[dict[str, Any]]] = None,
                       subject: str = "",
                       breakdown: Optional[list[dict[str, Any]]] = None
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
        atts = [dict(a) for a in (attachments or []) if isinstance(a, dict)]
        provided = [str(a.get("kind") or "") for a in atts]
        missing = _missing_required(cat, provided)
        expense = {
            "expense_id": self._next_id("BX"),
            "category": cat, "amount": round(amt, 2),
            "expense_date": str(expense_date or ""), "note": note or "",
            "invoice_no": invoice_no or "",
            "subject": str(subject or "").strip(),
            "breakdown": [dict(b) for b in (breakdown or [])],
            "attachments": atts,
            "attachment_count": len(atts),
            "required_kinds": _required_kinds(cat),
            "missing_required": missing,
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
                    days: Optional[float] = None,
                    attachments: Optional[list[dict[str, Any]]] = None
                    ) -> dict[str, Any]:
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
        atts = [dict(a) for a in (attachments or []) if isinstance(a, dict)]
        has_sick_note = any(str(a.get("kind") or "") == "病假条" for a in atts)
        leave = {
            "leave_id": self._next_id("QJ"),
            "leave_type": lt, "start_date": str(start_date or ""),
            "end_date": str(end_date or ""), "days": round(use_days, 2),
            "reason": reason or "", "applicant": applicant,
            "attachments": atts, "attachment_count": len(atts),
            # 病假需病假条：提交时未附则标记待补（提交后可由本人补充）
            "sick_note_required": lt == "病假",
            "sick_note_pending": lt == "病假" and not has_sick_note,
            "status": "pending_approval", "need_approval": True,
            "long_leave": use_days >= LEAVE_LONG_DAYS,
            "created_at": _now_iso(),
        }
        with self._lock:
            self._leaves[leave["leave_id"]] = leave
        return dict(leave)

    def supplement_leave_document(self, leave_id: str,
                                  attachment: dict[str, Any],
                                  applicant: str) -> dict[str, Any]:
        """提交后补充请假材料（如病假条）：仅限本人、未撤销的请假单。

        真实场景：HR 审批常在缺件时"挂起待补"，补件后状态回到待审批；
        demo 用内存态 + 落盘附件实现同一语义（补件留痕 ``supplements``）。
        治理不放松：归属校验在此，他人不可代补。
        """
        if not isinstance(attachment, dict) or not attachment.get("attachment_id"):
            raise OfficeError("补件材料为空（需先上传取得 attachment_id）")
        with self._lock:
            leave = self._leaves.get(str(leave_id or ""))
            if leave is None:
                raise OfficeError(f"请假单不存在: {leave_id!r}")
            if leave["applicant"] != applicant:
                raise OfficeError(f"只能补充本人请假单的材料（该单属于 {leave['applicant']}）")
            if leave["status"] == "cancelled":
                raise OfficeError("已撤销的请假单不能补充材料")
            atts = leave.setdefault("attachments", [])
            atts.append(dict(attachment))
            leave["attachment_count"] = len(atts)
            leave["supplemented_at"] = _now_iso()
            leave.setdefault("supplements", []).append({
                "attachment_id": attachment.get("attachment_id"),
                "kind": attachment.get("kind"), "at": leave["supplemented_at"]})
            if str(attachment.get("kind") or "") == "病假条":
                leave["sick_note_pending"] = False
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

    # ---------- OA 服务工单（IT / 停车 / 车辆） ----------

    def submit_service_request(self, kind: str, title: str, detail: str, applicant: str,
                               urgency: str = "一般", location: str = "",
                               extra: Optional[dict[str, Any]] = None,
                               attachments: Optional[list[dict[str, Any]]] = None
                               ) -> dict[str, Any]:
        """提交一条服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）。

        口径：kind 非法、必填项缺失一律 ``OfficeError``（fail-closed，不靠 LLM 自觉）；
        结构化字段放 ``extra``（车牌 / 系统 / 权限项 / 设备类型…），必填清单见 ``SERVICE_REQUIRED``；
        单据一律 ``pending_approval`` + ``need_approval``（与报销/请假/补卡同口径），归口见 ``SERVICE_ASSIGNEE``。
        """
        k = str(kind or "").strip()
        if k not in SERVICE_KINDS:
            raise OfficeError(f"服务工单类型非法: {kind!r}（可选: {SERVICE_KINDS}）")
        if not str(applicant or "").strip():
            raise OfficeError("服务工单申请人缺失")
        urge = str(urgency or "").strip() or "一般"
        if urge not in SERVICE_URGENCIES:
            raise OfficeError(f"紧急程度非法: {urgency!r}（可选: {SERVICE_URGENCIES}）")
        atts = [dict(a) for a in (attachments or []) if isinstance(a, dict)]
        fields: dict[str, Any] = {
            "title": str(title or "").strip(),
            "detail": str(detail or "").strip(),
            "location": str(location or "").strip(),
        }
        for key, value in (extra or {}).items():
            if value not in (None, "", []):
                fields[str(key)] = value
        missing = [key for key in SERVICE_REQUIRED[k]
                   if not str(fields.get(key) or "").strip()]
        if missing:
            raise OfficeError("服务工单缺少必填项: "
                              + "、".join(SERVICE_FIELD_LABELS.get(m, m) for m in missing))
        request = {
            "request_id": self._next_id(SERVICE_ID_PREFIX[k]),
            "kind": k, "kind_name": SERVICE_KIND_NAMES[k],
            "title": fields["title"], "detail": fields["detail"],
            "urgency": urge, "location": fields["location"],
            "extra": {key: value for key, value in fields.items()
                      if key not in ("title", "detail", "location")},
            "assignee": SERVICE_ASSIGNEE[k],
            "attachments": atts, "attachment_count": len(atts),
            "applicant": applicant, "status": "pending_approval",
            "need_approval": True,
            "created_at": _now_iso(),
        }
        with self._lock:
            self._service_requests[request["request_id"]] = request
        return dict(request)

    def list_service_requests(self, applicant: Optional[str] = None
                              ) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(r) for r in self._service_requests.values()
                     if r["status"] != "cancelled"]
        if applicant:
            items = [r for r in items if r["applicant"] == applicant]
        return sorted(items, key=lambda r: r["created_at"], reverse=True)


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


# ---------- OA 材料登记表（演示态：只登记材料清单，**不接收/不保存原文件**） ----------


class AttachmentStore:
    """OA 材料登记表：只登记元数据（文件名/类别/大小/上传人），**不落盘**。

    设计立场（与 ``OfficeBackend`` 同源：接口先行、demo 可换真）：
    - **演示态不接收文件**：用户在前端选文件只为取「文件名 + 大小」，真实字节
      从不提交到本机（端点不收 UploadFile）；元数据留内存，进程重启即复位；
    - **可追溯**：每条材料有稳定 ``attachment_id``、上传人、时间、类别与大小，
      绑定到报销/请假单据后即形成完整材料清单（真实场景接对象存储 + 单据库）；
    - **治理约束**：上传人必填、类别白名单、单份大小上限；解析时只认本人材料，
      避免"借用他人票据"。
    """

    MAX_BYTES: int = int(ATTACHMENT_MAX_MB * 1024 * 1024)

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self._seq = 0

    @staticmethod
    def _safe_name(filename: str) -> str:
        """取基名并清洗非法字符，防路径穿越（``../`` / 盘符 / 控制符）。"""
        base = Path(str(filename or "")).name.strip() or "upload.bin"
        base = re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", base)
        return base[:80] or "upload.bin"

    @staticmethod
    def _nominal_size(filename: str) -> int:
        """未给大小时按文件名生成**稳定假大小**（40KB~1.8MB），仅用于演示展示。"""
        digest = hashlib.md5(filename.encode("utf-8")).hexdigest()[:6]
        return int(digest, 16) % 1_800_000 + 40_000

    def save(self, filename: str, kind: str, uploader: str, size: int = 0,
             content_type: str = "", note: str = "") -> dict[str, Any]:
        """登记一份材料（**不接收/不保存字节**）；返回对外元数据。

        ``size`` 为客户端声明的文件大小（仅登记用于展示）；缺省按文件名生成稳定假大小。
        """
        k = str(kind or "").strip() or "其他"
        if k not in ATTACHMENT_KINDS:
            raise OfficeError(f"材料类别非法: {kind!r}（可选: {ATTACHMENT_KINDS}）")
        who = str(uploader or "").strip()
        if not who:
            raise OfficeError("上传人缺失")
        safe = self._safe_name(filename)
        try:
            n = int(size or 0)
        except (ValueError, TypeError):
            n = 0
        if n <= 0:
            n = self._nominal_size(safe)
        if n > self.MAX_BYTES:
            raise OfficeError(
                f"文件过大：{n / 1024 / 1024:.1f}MB 超过上限 {ATTACHMENT_MAX_MB}MB")
        with self._lock:
            self._seq += 1
            aid = f"ATT{self._seq:05d}"
            meta = {
                "attachment_id": aid, "filename": safe, "kind": k,
                "size": n,
                "content_type": content_type or "application/octet-stream",
                "note": str(note or ""), "uploader": who,
                "uploaded_at": _now_iso(),
                # 演示态标记：只登记清单、未保存原文件（前端据此提示"模拟上传"）
                "virtual": True,
                "url": f"/api/v2/office/attachment/{aid}",
            }
            self._items[aid] = meta
            return dict(meta)

    def get(self, attachment_id: str) -> Optional[dict[str, Any]]:
        """取材料元数据；不存在返回 None。"""
        with self._lock:
            meta = self._items.get(str(attachment_id or ""))
            return dict(meta) if meta else None

    def list_by_uploader(self, uploader: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._items.values()
                    if m["uploader"] == str(uploader or "")]

    def resolve(self, attachment_ids: Any, uploader: str
                ) -> tuple[list[dict[str, Any]], list[str]]:
        """把 attachment_id 列表解析为**属于该上传人**的材料元数据。

        返回 ``(metadatas, invalid_ids)``：他人材料 / 不存在 / 未登记一律计入
        ``invalid_ids`` 并剔除——杜绝借用他人票据或臆造 id（fail-closed）。
        """
        ids = attachment_ids if isinstance(attachment_ids, (list, tuple)) else []
        metas: list[dict[str, Any]] = []
        invalid: list[str] = []
        who = str(uploader or "")
        for raw in ids:
            aid = str(raw or "").strip()
            if not aid:
                continue
            meta = self.get(aid)
            if meta is None or meta["uploader"] != who:
                invalid.append(aid)
                continue
            metas.append(dict(meta))
        return metas, invalid


_attachment_store: Optional[AttachmentStore] = None
_attachment_lock = threading.Lock()


def get_attachment_store() -> AttachmentStore:
    """取材料登记表单例（演示态：仅内存元数据；可用 set 替换）。"""
    global _attachment_store
    with _attachment_lock:
        if _attachment_store is None:
            _attachment_store = AttachmentStore()
        return _attachment_store


def set_attachment_store(store: Optional[AttachmentStore]) -> None:
    """替换材料登记表（真实接入点：注入对象存储适配器；测试可注入空表）。"""
    global _attachment_store
    with _attachment_lock:
        _attachment_store = store
