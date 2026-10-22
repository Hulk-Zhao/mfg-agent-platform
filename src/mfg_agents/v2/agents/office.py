"""企业综合助手 Agentic Agent（V3.4 立 · V3.7 扩为综合助手）。

设计立场（与 ``quality`` / ``supply_chain`` 等域组智能体并列，但独立成型）：
- **单独一个 Agentic Agent**：能力边界 = 「办公事务办理」+「制造数据只读速查」。
  办公侧：会议室预订、订餐、我的预订/订单查询、高影响事项人工确认；
  数据侧（V3.7）：材料库存、成品库存与可发量 ATP、来料批次与 IQC 结论、
  库龄呆滞、缺料缺口、批次追溯。能力即工具：没有对应工具就做不了事。
- **接口先行、demo 可换真**：办公动作转发 ``OfficeBackend`` 契约
  （见 ``services/office.py``）——demo 用内存实现，接真实飞书/钉钉/美餐 API
  时替换后端实现即可；制造数据只读工具转发 ``DataSourceRegistry.run_query``，
  与协同问数**同源同口径**（口径即代码、SQL 常量串不拼接用户输入）。
- **治理铁律不放松**：写操作（预订/下单）在 demo 后端落地并**全程留痕**；
  对高影响事项（报告厅/培训室、商务客餐、超预算订单）Agent 必须停下调
  ``request_approval`` 发起人工确认——Agent 只发起、永不放行。
  **制造数据一律只读**：不得改动库存/批次，不得承诺出入库/调拨/盘点等写操作；
  只读查询受岗位数据权限（TABLE_ACL）约束，不越权。
- **防卡死**：最大步数硬限制，超限输出"已尽力结论 + 缺口"，绝不静默挂起。

ReAct 循环：LLM(bind_tools) → tool_calls → 执行 → 观察 → … → final。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

from ..services.office import (
    _ATTENDANCE_FIX_TYPES,
    _EXPENSE_CATEGORIES,
    _LEAVE_TYPES,
    SERVICE_ASSIGNEE,
    SERVICE_ATTACHMENT_KINDS,
    SERVICE_FIELD_LABELS,
    SERVICE_KIND_NAMES,
    SERVICE_KINDS,
    SERVICE_REQUIRED,
    SERVICE_URGENCIES,
    OfficeBackend,
    OfficeError,
    get_attachment_store,
    get_office_backend,
)
from .agentic import AgenticStep, AgenticDomainAgent
from .hitl import single_tool_flow

logger = logging.getLogger(__name__)

__all__ = ["OFFICE_AGENT_CONFIG", "OFFICE_TOOLS_META", "OfficeAssistantAgent"]

# 办公助手 HITL 工单的流程留痕：办公工单不经 DAG（无 ctx.node_trace()），
# 按工具调用链补造「已执行环节」，供审批摘要卡渲染流程进展与各职能部门耗时。
_OFFICE_FLOW: dict[str, list[tuple[str, str, int]]] = {
    "room": [("office_intent", "办公助手 · 意图识别", 5_000),
             ("office_room_check", "行政 · 场地与时段核验", 16_000),
             ("office_book", "办公助手 · 预订办理", 23_000)],
    "meal": [("office_intent", "办公助手 · 意图识别", 5_000),
             ("office_catering_check", "后勤 · 餐厅与时段核验", 14_000),
             ("office_order", "办公助手 · 下单办理", 21_000)],
    "expense": [("office_intent", "办公助手 · 意图识别", 5_000),
                ("office_expense_fill", "办公助手 · 报销单据整理", 15_000),
                ("office_expense_approve", "财务 · 票据与额度核验", 24_000)],
    "leave": [("office_intent", "办公助手 · 意图识别", 5_000),
              ("office_leave_fill", "办公助手 · 请假单整理", 14_000),
              ("office_leave_approve", "部门主管 · 假期与排班核验", 23_000)],
    "attendance": [("office_intent", "办公助手 · 意图识别", 5_000),
                   ("office_att_fill", "办公助手 · 补卡单整理", 13_000),
                   ("office_att_approve", "HR · 考勤核验", 22_000)],
    # OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）：识别 → 归口受理
    "it_repair": [("office_intent", "办公助手 · 意图识别", 5_000),
                  ("office_it_triage", "IT 运维 · 故障分级与派单", 12_000),
                  ("office_it_handle", "IT 运维 · 上门/远程处理", 24_000)],
    "it_access": [("office_intent", "办公助手 · 意图识别", 5_000),
                  ("office_it_access_check", "IT 运维 · 账号与权限核验", 13_000),
                  ("office_it_access_grant", "IT 运维 · 权限开通", 23_000)],
    "it_device": [("office_intent", "办公助手 · 意图识别", 5_000),
                  ("office_it_asset_check", "IT 资产 · 库存与领用核验", 12_000),
                  ("office_it_asset_issue", "IT 资产 · 设备发放", 22_000)],
    "parking": [("office_intent", "办公助手 · 意图识别", 5_000),
                ("office_parking_check", "行政后勤 · 车位余量与资质核验", 13_000),
                ("office_parking_assign", "行政后勤 · 车位分配", 23_000)],
    "visitor_vehicle": [("office_intent", "办公助手 · 意图识别", 5_000),
                        ("office_visitor_check", "行政后勤 · 来访信息核对", 11_000),
                        ("office_visitor_pass", "行政后勤 · 车辆放行报备", 20_000)],
    "business_car": [("office_intent", "办公助手 · 意图识别", 5_000),
                     ("office_car_check", "行政后勤 · 车辆与司机排班", 13_000),
                     ("office_car_dispatch", "行政后勤 · 派车办理", 23_000)],
    "default": [("office_intent", "办公助手 · 意图识别", 5_000),
                ("office_handle", "办公助手 · 事务办理", 18_000)],
}
_OFFICE_CATEGORY_CN = {"room": "会议室预订", "meal": "订餐",
                       "expense": "费用报销", "leave": "请假申请",
                       "attendance": "补考勤打卡",
                       # 服务工单：类别名直接取 kind 的中文名（与单据/徽标同口径）
                       **SERVICE_KIND_NAMES}

# 各类事项的审批归口（按事项性质分派，而非一律行政专员）
_OFFICE_ASSIGNEE = {"room": "行政专员", "meal": "行政专员",
                    "expense": "财务专员", "leave": "部门主管",
                    "attendance": "HR 专员",
                    # 服务工单：IT 类 → IT 运维专员；停车/车辆类 → 行政后勤专员
                    **SERVICE_ASSIGNEE}


# ---------- OA 服务工单（IT / 停车 / 车辆）：口语关键词 + 规范选项 ----------
_IT_REPAIR_HINTS = ("报修", "报障", "故障", "坏了", "黑屏", "蓝屏", "宕机",
                    "死机", "断网", "打不开", "连不上")
_IT_ACCESS_HINTS = ("权限", "账号", "账户", "密码", "开通", "授权")
_IT_DEVICE_HINTS = ("领用", "资产", "笔记本", "电脑", "台式机", "显示器",
                    "打印机", "扫码枪", "门禁卡", "工牌")
_PARKING_HINTS = ("车位", "停车", "月租车位", "固定车位")
_VISITOR_HINTS = ("访客", "来访", "报备", "放行", "客人")
_CAR_HINTS = ("公务用车", "派车", "用车", "还车", "公车")
_IT_PERMISSION_ITEMS = ["查询", "录入", "审批", "导出", "管理员"]
_IT_DEVICE_TYPES = ["笔记本电脑", "台式机", "显示器", "打印机", "扫码枪", "工牌/门禁卡"]
_PARKING_TYPES = ["固定车位", "月租车位", "临时车位"]
_PARKING_AREAS = ["A 栋地面", "B 栋地面", "C 栋地面", "D 栋地面", "地库 B1", "地库 B2"]


def _find_plate(text: str) -> str:
    """抽取车牌号（如 苏A12345 / 沪B88888 / 京AD12345）。"""
    m = re.search(r"([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
                  r"[A-Z][A-Z0-9]{4,6})", text)
    return m.group(1) if m else ""


def _clarify_options(key: str, label: str) -> list[str]:
    """按字段 key/label 关键词补全反问字段的规范选项（仅在 options 为空时兜底）。

    背景：ask_user 的字段定义由 LLM 生成，类型/类别类字段常漏填 options，
    前端弹窗就会"没有下拉选项"。此处对已知枚举字段补上权威选项。
    """
    text = f"{key} {label}"
    if "补卡" in text or key in ("fix_type", "attendance_type"):
        return list(_ATTENDANCE_FIX_TYPES)
    if ("报销" in text or "费用" in text
            or key in ("category", "expense_category", "category_name")):
        return list(_EXPENSE_CATEGORIES)
    if "假" in text and ("类型" in text or "类别" in text
                        or key in ("leave_type", "leave_kind")):
        return list(_LEAVE_TYPES)
    # ---- OA 服务工单（IT / 停车 / 车辆）----
    if key in ("urgency", "level") or "紧急" in text:
        return list(SERVICE_URGENCIES)
    if key in ("permission", "permissions", "permission_items"):
        return list(_IT_PERMISSION_ITEMS)
    if key in ("device_type", "asset_type"):
        return list(_IT_DEVICE_TYPES)
    if key in ("parking_type", "spot_type"):
        return list(_PARKING_TYPES)
    if key in ("area", "parking_area"):
        return list(_PARKING_AREAS)
    return []


# 反问弹窗的规范字段模板（按事项类别）：把"已从原话解析出的已知值"并入弹窗预填，
# 保证用户打开弹窗即"半填"状态，只需人工补真正缺失项。
_CLARIFY_SLOTS: dict[str, list[dict]] = {
    "leave": [
        {"key": "leave_type", "label": "请假类型", "type": "select",
         "options": list(_LEAVE_TYPES), "required": True},
        {"key": "start_date", "label": "开始日期", "type": "date", "required": True},
        {"key": "end_date", "label": "结束日期", "type": "date", "required": True},
    ],
    "expense": [
        {"key": "category", "label": "报销类别", "type": "select",
         "options": list(_EXPENSE_CATEGORIES), "required": True},
        {"key": "amount", "label": "报销金额（元）", "type": "number", "required": True},
        {"key": "expense_date", "label": "费用发生日期", "type": "date", "required": True},
    ],
    "attendance": [
        {"key": "fix_type", "label": "补卡类型", "type": "select",
         "options": list(_ATTENDANCE_FIX_TYPES), "required": True},
        {"key": "work_date", "label": "补卡日期", "type": "date", "required": True},
        {"key": "time", "label": "补卡时间", "type": "time", "required": True},
    ],
    # ---- OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）----
    # key 与工具入参对齐：title/location/urgency 走显式入参，其余结构化字段放 extra
    "it_repair": [
        {"key": "title", "label": "故障现象", "type": "text", "required": True},
        {"key": "location", "label": "设备与位置", "type": "text", "required": True},
        {"key": "urgency", "label": "紧急程度", "type": "select",
         "options": list(SERVICE_URGENCIES), "required": False},
    ],
    "it_access": [
        {"key": "title", "label": "申请事由", "type": "text", "required": True},
        {"key": "system", "label": "系统（如 MES / OA / ERP）", "type": "text", "required": True},
        {"key": "permission", "label": "权限项", "type": "multiselect",
         "options": list(_IT_PERMISSION_ITEMS), "required": True},
    ],
    "it_device": [
        {"key": "title", "label": "领用用途", "type": "text", "required": True},
        {"key": "device_type", "label": "设备类型", "type": "select",
         "options": list(_IT_DEVICE_TYPES), "required": True},
    ],
    "parking": [
        {"key": "title", "label": "申请事由", "type": "text", "required": True},
        {"key": "parking_type", "label": "车位类型", "type": "select",
         "options": list(_PARKING_TYPES), "required": False},
        {"key": "area", "label": "区域", "type": "select",
         "options": list(_PARKING_AREAS), "required": False},
        {"key": "plate", "label": "车牌号", "type": "text", "required": True},
    ],
    "visitor_vehicle": [
        {"key": "title", "label": "来访事由", "type": "text", "required": True},
        {"key": "plate", "label": "车牌号", "type": "text", "required": True},
        {"key": "visit_date", "label": "来访日期", "type": "date", "required": True},
    ],
    "business_car": [
        {"key": "title", "label": "用车事由", "type": "text", "required": True},
        {"key": "dest", "label": "目的地", "type": "text", "required": True},
        {"key": "use_from", "label": "用车起始时间（YYYY-MM-DD HH:MM）", "type": "text",
         "required": True},
    ],
}
_LEAVE_HINTS = ("请假", "休假", "年假", "事假", "病假", "调休", "婚假", "产假")


def _detect_flow(text: str) -> str:
    if "报销" in text:
        return "expense"
    if "补卡" in text or "补考勤" in text:
        return "attendance"
    if any(h in text for h in _LEAVE_HINTS):
        return "leave"
    # OA 服务工单（顺序即优先级：先报修/权限，再访客/用车，最后车位/设备领用）
    if any(h in text for h in _IT_REPAIR_HINTS):
        return "it_repair"
    if any(h in text for h in _IT_ACCESS_HINTS):
        return "it_access"
    if any(h in text for h in _VISITOR_HINTS):
        return "visitor_vehicle"
    if any(h in text for h in _CAR_HINTS):
        return "business_car"
    if any(h in text for h in _PARKING_HINTS):
        return "parking"
    if any(h in text for h in _IT_DEVICE_HINTS):
        return "it_device"
    return ""


def _find_enum(text: str, options: list[str]) -> str:
    for o in options:
        if o in text:
            return o
    return ""


def _find_dates(text: str) -> list[str]:
    """抽取 YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD，规范化为 YYYY-MM-DD。"""
    found = re.findall(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    return [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in found]


# 相对时间词表（长词在前：'大后天' 必须先于 '后天' 匹配）
_RELATIVE_DAY_WORDS: list[tuple[str, int]] = [
    ("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0),
    ("昨天", -1), ("前天", -2),
]
_RELATIVE_DAY_OFFSET: dict[str, int] = dict(_RELATIVE_DAY_WORDS)
_RELATIVE_DAY_RE = re.compile("|".join(w for w, _ in _RELATIVE_DAY_WORDS))


def _find_relative_dates(text: str, today: Optional[date] = None) -> list[str]:
    """抽取"今天/明天/后天/大后天/昨天/前天"，按出现顺序换算为 YYYY-MM-DD（去重）。

    "明天"这类相对时间**本身不定**在哪一天，必须以当前日期为基准换算——Agent 亦可
    先调 ``get_current_time`` 取基准。换算只做确定性加减，不做业务推理。
    """
    base = today or datetime.now().date()
    out: list[str] = []
    for m in _RELATIVE_DAY_RE.finditer(text):
        d = (base + timedelta(days=_RELATIVE_DAY_OFFSET[m.group()])).isoformat()
        if d not in out:
            out.append(d)
    return out


def _find_amount(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", text)
    if not m:
        m = re.search(r"(?:金额|报销|费用|花费|共计?)\D{0,4}(\d+(?:\.\d+)?)", text)
    return m.group(1) if m else ""


def _find_time(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _known_values(flow: str, text: str) -> dict[str, str]:
    """按事项类别从用户原话抽取已知字段值（仅抽确定可识别的）。

    日期优先取原话里的显式日期；没有显式日期时，把"明天/后天"等相对时间按当前日期
    确定性换算（否则弹窗里日期一直空着，用户会被反复追问"明天是哪天"）。
    """
    known: dict[str, str] = {}
    explicit = _find_dates(text)
    dates = explicit or _find_relative_dates(text)
    # 相对单日（"明天请假"/"后天请假"）语义明确=当天一天 → 起止同日预填；
    # 显式单日（"10-20 开始"）语义可为多天起点，只填开始日期、不替用户定结束日。
    relative_single = bool(dates) and not explicit and len(dates) == 1
    if flow == "leave":
        t = _find_enum(text, _LEAVE_TYPES)
        if t:
            known["leave_type"] = t
        if dates:
            known["start_date"] = dates[0]
        if len(dates) > 1:
            known["end_date"] = dates[1]
        elif relative_single:
            known["end_date"] = dates[0]
    elif flow == "expense":
        cat = _find_enum(text, _EXPENSE_CATEGORIES)
        if cat:
            known["category"] = cat
        amt = _find_amount(text)
        if amt:
            known["amount"] = amt
        if dates:
            known["expense_date"] = dates[0]
    elif flow == "attendance":
        if "上班补卡" in text or "上班" in text:
            known["fix_type"] = "上班补卡"
        elif "下班补卡" in text or "下班" in text:
            known["fix_type"] = "下班补卡"
        if dates:
            known["work_date"] = dates[0]
        tm = _find_time(text)
        if tm:
            known["time"] = tm
    elif flow in SERVICE_KINDS:
        # 服务工单：紧急程度 / 车牌号 / 日期时段（日期只做确定性预填，不替用户推理业务）
        urge = _find_enum(text, SERVICE_URGENCIES)
        if urge and urge != "一般":
            known["urgency"] = urge
        plate = _find_plate(text)
        if plate:
            known["plate"] = plate
        if dates:
            known["visit_date"] = dates[0]
            known["use_from"] = dates[0]
        tm = _find_time(text)
        if tm and dates:
            known["use_from"] = f"{dates[0]} {tm}"
    return known


def _enrich_clarify_fields(text: str, fields: list[dict]) -> list[dict]:
    """把原话中解析到的已知值并入弹窗字段：已有的补 value，缺的补字段（带 value）。

    保证互通：LLM 只列"要问的缺失项"时，已知项也会作为预填字段出现在弹窗里，
    用户打开即看到已解析的类型/日期/金额，只需补空着的部分。
    """
    base = text.split("；补充信息：")[0]
    flow = _detect_flow(base)
    if not flow:
        return fields
    known = _known_values(flow, base)
    if not known:
        return fields
    out = [dict(f) for f in fields]
    by_key = {f.get("key"): f for f in out}
    for slot in _CLARIFY_SLOTS[flow]:
        k = slot["key"]
        if k in by_key:
            f = by_key[k]
            if not str(f.get("value") or "").strip() and k in known:
                f["value"] = known[k]
        elif k in known:
            out.append({**slot, "value": known[k]})
    return out


# 企业综合助手可用工具元数据（帮助界面 / MCP / 前端展示共用；与 _tools() 保持同步）
OFFICE_TOOLS_META: list[dict[str, str]] = [
    # ---- 办公事务（写/留痕/HITL） ----
    {"name": "list_rooms", "desc": "按容量/设备筛选会议室（含位置与是否需授权）", "kind": "只读"},
    {"name": "find_available_rooms", "desc": "查某时段可用会议室（含冲突规避）", "kind": "只读"},
    {"name": "list_my_bookings", "desc": "查我的会议室预订", "kind": "只读"},
    {"name": "list_restaurants", "desc": "列出园区餐厅（食堂/商务餐厅）", "kind": "只读"},
    {"name": "get_menu", "desc": "查某餐厅菜单与价格", "kind": "只读"},
    {"name": "list_my_orders", "desc": "查我的订餐订单", "kind": "只读"},
    {"name": "book_room", "desc": "预订会议室（自动查冲突，返回预订号）", "kind": "写/留痕"},
    {"name": "cancel_booking", "desc": "取消本人会议室预订", "kind": "写/留痕"},
    {"name": "place_order", "desc": "下单订餐（按菜单计价，返回订单号与金额）", "kind": "写/留痕"},
    {"name": "cancel_order", "desc": "取消本人订餐订单", "kind": "写/留痕"},
    # ---- OA 自助办理（报销/请假/补考勤；三类均走人工确认） ----
    {"name": "get_current_time", "desc": "查当前日期与时间（含星期）；把'今天/明天/后天'等相对时间换算为具体日期时先调它", "kind": "只读·时间"},
    {"name": "organize_expense", "desc": "整理报销科目（按事由拆二级科目 + 必传/选传材料核对），提交报销前先调它", "kind": "只读·规则"},
    {"name": "submit_expense", "desc": "提交费用报销单（类别/金额/日期/事由/材料，返回报销号）", "kind": "写/HITL"},
    {"name": "list_my_expenses", "desc": "查我的报销单与状态", "kind": "只读"},
    {"name": "apply_leave", "desc": "提交请假申请（类型/起止日期/事由，返回请假单号）", "kind": "写/HITL"},
    {"name": "list_my_leaves", "desc": "查我的请假申请与状态", "kind": "只读"},
    {"name": "get_leave_balance", "desc": "查我的年假/调休余额", "kind": "只读"},
    {"name": "supplement_leave_document", "desc": "为请假单补件（如病假条，需先上传取得 attachment_id）", "kind": "写/HITL"},
    {"name": "submit_attendance_fix", "desc": "提交补考勤打卡申请（上班/下班补卡 + 日期时间）", "kind": "写/HITL"},
    {"name": "list_my_attendance_fixes", "desc": "查我的补卡申请与状态", "kind": "只读"},
    {"name": "submit_service_request", "desc": "提交 OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位申请 / 访客车辆报备 / 公务用车），按 kind 归口 IT 运维或行政后勤", "kind": "写/HITL"},
    {"name": "ask_user", "desc": "关键信息缺失时向用户反问（弹窗式多选/输入），补齐后再提交", "kind": "只读·澄清"},
    {"name": "request_approval", "desc": "发起人工确认（高影响事项：报告厅/客餐/超预算；报销/请假/补卡一律发起）", "kind": "写/HITL"},
    # ---- 制造数据只读速查（V3.7；转发统一注册查询，受岗位数据权限约束） ----
    {"name": "query_material_stock", "desc": "查材料库存（料号↔在库/预留/冻结/可用/安全库存/库龄）", "kind": "只读·数据"},
    {"name": "query_finished_stock", "desc": "查成品库存与可发量 ATP（型号/仓库）", "kind": "只读·数据"},
    {"name": "query_incoming_batch", "desc": "查来料批次与 IQC 结论（批号/供应商/近期）", "kind": "只读·数据"},
    {"name": "query_stock_aging", "desc": "查库龄/呆滞 TOP（默认库龄≥90 天）", "kind": "只读·数据"},
    {"name": "query_shortage", "desc": "查缺料清单（可用量低于安全库存，含缺口）", "kind": "只读·数据"},
    {"name": "query_batch_trace", "desc": "按批号做批次追溯（来料→检验→不良线索）", "kind": "只读·数据"},
]

MAX_STEPS = 8          # 防卡死：单次任务最大工具循环步数
TOOL_TIMEOUT_S = 30    # 办公工具为本地/外部 API，超时较数据链路更短

# 办公助手 Agent 配置（提示词即边界：意图 / 职责 / 工具 / HITL 准则）
OFFICE_AGENT_CONFIG: dict[str, str] = {
    "abbr": "OA",
    "name": "企业综合助手智能体",
    "role": ("你是企业的综合事务助手智能体，服务全体员工与管理人员。"
             "意图有两类：① 办公事务——帮员工预订会议室、查询会议室可用情况、"
             "订餐（食堂/商务餐厅）、查询自己的预订与订单、取消本人预订/订单，"
             "以及 **OA 自助办理**（费用报销、请假申请、补考勤打卡）与 **OA 服务工单**"
             "（IT 故障报修、账号与权限申请、设备领用申请、车位申请、访客车辆报备、"
             "公务用车申请），以及高影响办公事项的人工确认；② 制造数据速查（只读）——"
             "查询材料库存、成品库存与可发量、来料批次与检验结论、库龄呆滞、缺料缺口、批次追溯。"),
    "boundary": ("边界：制造数据查询一律**只读**——不得改动任何库存/批次数据，"
                 "不得承诺出入库/调拨/盘点等写操作；若用户要求这类操作，如实说明"
                 "本助手不办理并引导走对应系统。只读查询受岗位数据权限约束，越权"
                 "数据如实告知无权限。办公写操作你只能在**本人**名下预订/取消/申请；"
                 "不得代他人下单、取消、报销或请假。会议室时段冲突时不得静默覆盖，"
                 "必须改选并如实告知冲突原因。报销/请假/补卡单**禁止臆造**金额、"
                 "日期、事由或类型——关键字段缺失时先用 ask_user 反问补齐，再提交。"
                 "OA 服务工单（IT 报修/账号权限/设备领用/车位/访客车辆/公务用车）的关键"
                 "字段（系统、权限项、设备类型、车牌号、来访日期、用车时间、目的地）同样"
                 "**禁止臆造**，缺失时先 ask_user 反问补齐。"
                 "上传材料（attachment_id）只能绑定**本人上传**的材料，不得借用/臆造"
                 "他人材料编号。"
                 "相对时间（今天/明天/后天/大后天/昨天/下周等）**必须先调 get_current_time**"
                 "取回当前日期再换算成具体 YYYY-MM-DD 后办理；「明天是哪一天」不是缺失"
                 "信息，禁止因此反问用户，也禁止臆造日期。"),
    "hitl_rule": ("HITL 准则：当预订的是**需授权资源**（报告厅/培训室等 capacity≥20 "
                  "或后端标记 need_approval）、或下单的是**商务客餐/宴请**、或订单"
                  "金额达到或超过 500 元时，你必须停下调 request_approval 发起人工"
                  "确认工单，并在回答里说明「已提交待确认」；普通会议室与员工工作餐"
                  "可直接办理，无需 HITL。**报销/请假/补考勤三类申请提交后一律**停下"
                  "调 request_approval 发起人工确认（报销 category=expense、请假 "
                  "category=leave、补卡 category=attendance），不得直接放行。"
                  "**OA 服务工单提交后同样一律**停下调 request_approval 发起人工确认"
                  "（category 用该工单的 kind：it_repair/it_access/it_device/parking/"
                  "visitor_vehicle/business_car），不得直接放行。"
                  "制造数据为只读查询，不触发 HITL。"),
}


_SYSTEM_TEMPLATE = """{role}
{boundary}

## 工具（你的全部能力，没有工具就做不了事）
### 办公事务
- list_rooms(capacity_min?, equipment?)：按容量/设备筛选会议室（含位置、是否需授权）
- find_available_rooms(date, start, end, capacity_min?, equipment?)：查某时段可用会议室
- book_room(room_id, date, start, end, title, attendees)：预订会议室（自动查冲突，返回预订号）
- cancel_booking(booking_id)：取消本人预订
- list_my_bookings()：查我的预订
- list_restaurants()：列出园区餐厅（食堂/商务餐厅）
- get_menu(restaurant_id)：查某餐厅菜单与价格
- place_order(restaurant_id, items, meal_time, note?)：下单订餐（items=[{{"item_id","qty"}}]）
- cancel_order(order_id)：取消本人订单
- list_my_orders()：查我的订单

### OA 自助办理（报销/请假/补考勤；提交后一律走人工确认）
- get_current_time()：查当前日期与时间（含星期）——把"今天/明天/后天/大后天/昨天/下周"等相对时间换算为具体日期时**先调它**取基准
- organize_expense(category, amount, note?, attachment_kinds?)：整理报销科目——按事由拆二级科目明细、给出该类别必传/选传材料核对（attachment_kinds 缺省用本次已上传材料的类别）
- submit_expense(category, amount, expense_date, note, invoice_no?, attachment_ids?, subject?, breakdown?)：提交报销单（category∈{{差旅/招待/办公用品/交通/其他}}；attachment_ids=本次已上传材料的编号列表；返回报销号）
- list_my_expenses()：查我的报销单与状态
- apply_leave(leave_type, start_date, end_date, reason, days?, attachment_ids?)：提交请假单（leave_type∈{{年假/事假/病假/调休/婚假/产假}}；days 缺省按自然日含端点；attachment_ids=本次已上传材料；返回请假号）
- list_my_leaves()：查我的请假申请
- get_leave_balance()：查我的年假/调休余额
- supplement_leave_document(leave_id, attachment_id?)：给请假单补件（如病假条；attachment_id 缺省用本次已上传材料；返回补件后的请假单）
- submit_attendance_fix(fix_type, work_date, time, reason)：提交补卡申请（fix_type∈{{上班补卡/下班补卡}}，返回补卡号）
- list_my_attendance_fixes()：查我的补卡申请
- submit_service_request(kind, title, detail?, urgency?, location?, extra?, attachment_ids?)：提交 OA 服务工单（kind∈{{it_repair/it_access/it_device/parking/visitor_vehicle/business_car}}；title=一句话事由或故障现象；location=设备与位置（IT 报修必填）；urgency∈{{一般/紧急/非常紧急}}；extra=结构化字段对象，按 kind 放 system/permission/device_type/plate（车牌）/visit_date（来访日期）/use_from（用车起始时间）/dest（目的地）；attachment_ids=本次已上传材料；返回服务工单号）
- ask_user(question, fields)：关键信息缺失时向用户反问（弹窗式多选/输入）；fields=[{{"key","label","type"(select/multiselect/date/时间/number/text),"options"?,"required"?}}]
- request_approval(title, prompt, category)：发起人工确认（category∈{{room/meal/expense/leave/attendance/it_repair/it_access/it_device/parking/visitor_vehicle/business_car}}；你只发起，绝不放行）

### 制造数据速查（只读；转发统一口径注册查询，受岗位数据权限约束）
- query_material_stock(part_no?, keyword?)：查材料库存——不传参给全量；传料号给该料号各仓在库/预留/冻结/可用/安全库存/库龄；用户给的是口语名称/规格（如"螺栓""TYPE-C"）时用 keyword 模糊搜索
- query_finished_stock(model?)：查成品库存与可发量 ATP——不传型号给全量；传型号（如 A型号）给各仓可发量
- query_incoming_batch(batch_no?, supplier?, unqualified_only?)：查来料批次与 IQC 结论——传批号按批号查、传供应商给该供应商来料批次、问"不合格/异常来料"时置 unqualified_only=true、都不传给近期批次（含待检/让步接收）
- query_stock_aging(days?)：查库龄/呆滞 TOP（days 缺省 90，按库龄倒序）
- query_shortage()：查缺料清单（可用量低于安全库存，含缺口数量）
- query_batch_trace(batch_no)：按批号做批次追溯（来料综合→传统检验结论→不良记录）

## 工作方式（ReAct）
1. 先判断需要什么：订会议室先查可用（find_available_rooms），订餐先看菜单（get_menu）；查数据先选对工具（材料/成品/来料/库龄/缺料/追溯）；报销/请假/补卡/服务工单先确认关键字段是否齐全；
   - **材料（附件）**：用户本次已上传材料时，消息里会带「本次已上传材料（attachment_ids）」。报销/病假提交时把 attachment_ids **原样**传入对应工具（不得臆造或借用编号）；报销前可先调 organize_expense 整理二级科目并把结果讲给用户听；报销缺**必传材料（发票）**时如实提示、不阻断提交；病假缺病假条时可提示"提交后用上传入口补交"，或用 supplement_leave_document 补挂已上传的病假条；
   - **相对时间先换算（重要）**：用户说"今天/明天/后天/大后天/昨天/下周/本周末"等相对时间时，必须先调 get_current_time() 取当前日期，再换算成具体 YYYY-MM-DD（如"明天"=返回日期的次日）；单日请假（"明天请假"）的 start_date 与 end_date 填同一天。反问预填与最终办理一律使用换算后的具体日期；**不得**因"明天不具体"反问用户"哪天"，也不得臆造日期；回答中回显日期（如"2026-10-20（明天，周二）"）；
2. **关键信息缺失时必须先用 ask_user 反问**（弹窗式补齐），不要臆造日期、金额、类型或事由，也不要直接把缺失项写进最终回答——反问是一次独立工具调用，调用后即结束本轮，等用户补齐再继续；
   - 报销缺什么就问什么：类别、金额、发生日期、事由；
   - 请假缺什么就问什么：假期类型、开始日期、结束日期、事由；
   - 补卡缺什么就问什么：补卡类型（上班/下班）、补卡日期、补卡时间、事由；
   - 服务工单缺什么就问什么：IT 报修→故障现象 + 设备与位置（紧急程度选填）；账号与权限申请→系统 + 权限项（多选）+ 事由；设备领用→设备类型 + 用途；车位申请→车牌号（车位类型/区域选填）；访客车辆报备→车牌号 + 来访日期 + 来访事由；公务用车→用车起始时间 + 目的地 + 事由；
   - 会议室的日期/时段缺失、订餐日期缺失同样可用 ask_user 反问；
   - **一次反问必须把该事项的全部缺失字段一次列全**（禁止挤牙膏式多轮追问）；类型/类别类字段一律用 type="select" 并给出 options（请假类型 options=["年假","事假","病假","调休","婚假","产假"]、报销类别 options=["差旅","招待","办公用品","交通","其他"]、补卡类型 options=["上班补卡","下班补卡"]）；
   - **能解析出的字段必须预填**：调用 ask_user 时，凡能从用户原话中解析出的字段值，一律写进该 field 的 `value`（弹窗据此自动填入，用户只补真正缺失项）；例：用户说"我要请年假 3 天"→ leave_type.value="年假"，并从已有日期/默认近日推算 start_date/end_date 的 value，只让用户补事由；用户说"帮我给明天请假"→ 先 get_current_time 换算"明天"的具体日期，start_date/end_date 都预填该日期（单日），只让用户补假期类型与事由；
   - **若用户原话已给出全部关键字段，就直接调用对应工具办理，不要弹窗**（自然语言一步到位）；
   - **若用户回复中已含"补充信息："**，说明反问已补齐：严禁再次调用 ask_user，必须直接用已有信息调用对应工具办理（确仍不足时在回答里说明，而不是再弹窗）；
3. 写操作前想清楚参数；冲突/失败时读工具返回的原因并换时段或换房间再试；
4. 制造数据为只读：只陈述查询结果并标注口径（可用量=在库-预留，库龄按天），不得编造、不得承诺写操作；工具返回"无权限"时如实转述，不得绕过；
5. {hitl_rule}
6. 完成或达到边界时，输出最终 JSON（不要输出其他文字）：
{{"answer": "面向员工的完整中文回答（含预订号/订单号/报销号/请假号/补卡号/服务工单号、时间、地点、金额等关键信息；数据查询含关键数值与口径）",
  "data_used": ["用到的工具与关键数据，如 query_shortage() 5 项低于安全库存"],
  "needs_hitl": true/false,
  "hitl_title": "需人工确认事项标题（needs_hitl=true 时必填）",
  "hitl_prompt": "给审批人的说明（needs_hitl=true 时必填）",
  "hitl_category": "room|meal|expense|leave|attendance|it_repair|it_access|it_device|parking|visitor_vehicle|business_car 之一（needs_hitl=true 时必填）",
  "data_gaps": ["未能办理/获取的信息及原因"]}}

注意：只输出一个 JSON 对象；预订号/订单号/报销号/请假号/补卡号/服务工单号必须来自工具返回；不得编造会议室、菜品、价格、库存数值、报销金额、假期日期或补卡时间。"""


@dataclass
class OfficeAssistantAgent:
    """企业综合助手 Agentic Agent：ReAct 工具循环 + 高影响自判 HITL + 硬性防卡死。

    能力两组：办公事务办理（写/留痕/HITL）+ 制造数据只读速查（转发注册查询）。
    """

    orchestrator: Any                          # 用于访问 workflow / 运行时
    backend: Optional[OfficeBackend] = None    # 办公后端；缺省用进程级单例
    max_steps: int = MAX_STEPS

    def __post_init__(self) -> None:
        if self.backend is None:
            runtime_office = getattr(getattr(self.orchestrator, "runtime", None),
                                     "office", None)
            self.backend = runtime_office or get_office_backend()
        self.cfg = OFFICE_AGENT_CONFIG
        self._llm = None
        self.user_ctx: dict[str, Any] = {}
        self._active_query: str = ""     # 本轮用户原话（供 ask_user 解析已知值预填）
        # 本次请求携带的上传材料（已解析为本人材料元数据；提交报销/病假时绑定）
        self._pending_attachments: list[dict[str, Any]] = []

    # ---------- LLM（懒加载；工具循环不需要深度思考） ----------

    def _get_llm(self, exclude: Optional[set[str]] = None) -> Any:
        """取（绑定工具的）LLM；``exclude`` 用于本轮禁用个别工具。

        关键用途：用户已通过弹窗补充信息（消息含"补充信息："）时，本轮禁用
        ask_user，从根上断开"反问→补充→再反问"的无限弹框循环。
        """
        if self._llm is None:
            from ...common.llm_factory import get_llm
            self._llm = get_llm(temperature=0.0, tier="L1", max_tokens=900,
                                timeout=45, thinking="disabled")
        # 测试注入的脚本化 LLM 无 bind_tools：原样返回（不绑定工具）
        if not hasattr(self._llm, "bind_tools"):
            return self._llm
        skip = exclude or set()
        schemas = [s for s in self._tool_schemas()
                   if s["function"]["name"] not in skip]
        return self._llm.bind_tools(schemas)

    # ---------- 身份 ----------

    def _actor(self) -> str:
        """操作人身份（预订/订单归属；取姓名，缺省用 user_id）。"""
        return (str(self.user_ctx.get("name") or "").strip()
                or str(self.user_ctx.get("user_id") or "").strip()
                or "演示用户")

    def _fail(self, exc: OfficeError) -> dict[str, Any]:
        """业务错误 → 结构化观察（Agent 可据此换方案，而非当作崩溃）。"""
        return {"ok": False, "error": str(exc)}

    # ---------- 制造数据只读工具（V3.7；转发统一注册查询 + 岗位权限核验） ----------

    def _roles(self) -> set[str]:
        """操作人平台域角色集合（user_ctx.roles 为权威来源）。"""
        return {str(r) for r in (self.user_ctx.get("roles") or [])}

    def _data_query(self, query_name: str, params: Optional[dict] = None,
                    table: str = "") -> dict[str, Any]:
        """执行注册查询（只读、参数化）：先做岗位表级权限核验，再转发统一通道。

        - 与协同问数**同源同口径**（SQL 常量串在 queries.yaml，不拼接用户输入）；
        - 越权即 fail-closed 返回"无权限"，Agent 如实转述、不得绕过；
        - 任何异常转结构化观察，不冒泡成崩溃。
        """
        if table:
            from ..services.text2sql import table_acl
            if not (self._roles() & table_acl(table)):
                return {"ok": False,
                        "error": f"当前岗位无权限查询该数据（{table}）；请走对应职责岗，"
                                 "或在「数据速查」页申请查询权限"}
        reg = getattr(getattr(self.orchestrator, "runtime", None),
                      "data_registry", None)
        if reg is None:
            return {"ok": False, "error": "数据源未装配，无法查询"}
        try:
            rows = reg.run_query(query_name, params or {})
        except Exception as exc:  # noqa: BLE001 —— 失败反馈给 Agent
            return {"ok": False, "error": f"查询失败：{type(exc).__name__}: {str(exc)[:120]}"}
        return {"ok": True, "query": query_name, "source": "registered_query",
                "count": len(rows), "rows": rows}

    # ---------- 工具实现（能力即工具；全部走 OfficeBackend 契约） ----------

    def _tools(self) -> dict[str, Any]:
        be = self.backend

        def list_rooms(capacity_min: Optional[int] = None,
                       equipment: Optional[list[str]] = None) -> dict:
            rooms = be.list_rooms(capacity_min, equipment)
            return {"rooms": rooms, "count": len(rooms)}

        def find_available_rooms(date: str, start: str, end: str,
                                 capacity_min: Optional[int] = None,
                                 equipment: Optional[list[str]] = None) -> dict:
            try:
                rooms = be.find_available_rooms(date, start, end,
                                                capacity_min, equipment)
            except OfficeError as exc:
                return self._fail(exc)
            return {"date": date, "start": start, "end": end,
                    "available_rooms": rooms, "count": len(rooms)}

        def list_my_bookings() -> dict:
            items = be.list_bookings(self._actor())
            return {"bookings": items, "count": len(items)}

        def book_room(room_id: str, date: str, start: str, end: str,
                      title: str = "", attendees: int = 1) -> dict:
            try:
                booking = be.book_room(room_id, date, start, end, title,
                                        attendees, self._actor())
            except OfficeError as exc:
                return self._fail(exc)
            return {"ok": True, "booking": booking,
                    "need_approval": booking.get("need_approval", False)}

        def cancel_booking(booking_id: str) -> dict:
            try:
                booking = be.cancel_booking(booking_id, self._actor())
            except OfficeError as exc:
                return self._fail(exc)
            return {"ok": True, "booking": booking}

        def list_restaurants() -> dict:
            items = be.list_restaurants()
            return {"restaurants": items, "count": len(items)}

        def get_menu(restaurant_id: str) -> dict:
            try:
                return {"ok": True, "menu": be.get_menu(restaurant_id)}
            except OfficeError as exc:
                return self._fail(exc)

        def place_order(restaurant_id: str, items: list[dict],
                        meal_time: str = "午餐", note: str = "") -> dict:
            try:
                order = be.place_order(restaurant_id, items, meal_time,
                                       self._actor(), note)
            except OfficeError as exc:
                return self._fail(exc)
            return {"ok": True, "order": order,
                    "need_approval": order.get("need_approval", False)}

        def cancel_order(order_id: str) -> dict:
            try:
                order = be.cancel_order(order_id, self._actor())
            except OfficeError as exc:
                return self._fail(exc)
            return {"ok": True, "order": order}

        def list_my_orders() -> dict:
            items = be.list_orders(self._actor())
            return {"orders": items, "count": len(items)}

        # ---- OA 自助办理：报销 / 请假 / 补考勤（写/留痕/HITL） ----

        def get_current_time() -> dict:
            """当前日期时间（含星期）：把"明天/后天"等相对时间换算为具体日期的基准。

            只读时钟查询——Agent 自身不掌握"今天是哪天"，涉及相对时间（请假/报销/
            补卡的日期）时必须先调它取基准，再换算成具体 YYYY-MM-DD。
            """
            now = datetime.now()
            return {"ok": True, "date": now.strftime("%Y-%m-%d"),
                    "time": now.strftime("%H:%M"),
                    "datetime": now.strftime("%Y-%m-%d %H:%M"),
                    "weekday": f"星期{'一二三四五六日'[now.weekday()]}",
                    "note": "今天=date；明天=date+1；后天=date+2"}

        def _resolve_attachments(ids: Any) -> tuple[list[dict], list[str]]:
            """把 attachment_ids 解析为**本人**材料元数据；缺省用本次请求携带的材料。

            返回 ``(metadas, invalid_ids)``：他人材料 / 臆造 id 一律剔除（fail-closed）。
            """
            if ids:
                return get_attachment_store().resolve(ids, self._actor())
            return [dict(a) for a in self._pending_attachments], []

        def organize_expense(category: str, amount: float, note: str = "",
                             attachment_kinds: Optional[list[str]] = None) -> dict:
            """整理报销科目（确定性规则）：二级科目拆分 + 必传/选传材料核对。"""
            from ..services.office import organize_expense as _organize
            kinds = attachment_kinds
            if kinds is None:
                kinds = [str(a.get("kind") or "") for a in self._pending_attachments]
            try:
                return _organize(category, amount, note, kinds)
            except OfficeError as exc:
                return self._fail(exc)

        def submit_expense(category: str, amount: float, expense_date: str,
                           note: str = "", invoice_no: str = "",
                           attachment_ids: Optional[list[str]] = None,
                           subject: str = "",
                           breakdown: Optional[list[dict]] = None) -> dict:
            metas, invalid = _resolve_attachments(attachment_ids)
            try:
                expense = be.submit_expense(category, amount, expense_date, note,
                                            self._actor(), invoice_no, metas,
                                            subject, breakdown)
            except OfficeError as exc:
                return self._fail(exc)
            out: dict[str, Any] = {"ok": True, "expense": expense,
                                   "need_approval": expense.get("need_approval", False),
                                   "high_value": expense.get("high_value", False)}
            if expense.get("missing_required"):
                out["missing_required_kinds"] = expense["missing_required"]
            if invalid:
                out["invalid_attachment_ids"] = invalid
            return out

        def list_my_expenses() -> dict:
            items = be.list_expenses(self._actor())
            return {"expenses": items, "count": len(items)}

        def apply_leave(leave_type: str, start_date: str, end_date: str,
                        reason: str = "", days: Optional[float] = None,
                        attachment_ids: Optional[list[str]] = None) -> dict:
            metas, invalid = _resolve_attachments(attachment_ids)
            try:
                leave = be.apply_leave(leave_type, start_date, end_date,
                                       self._actor(), reason, days, metas)
            except OfficeError as exc:
                return self._fail(exc)
            out: dict[str, Any] = {"ok": True, "leave": leave,
                                   "need_approval": leave.get("need_approval", False),
                                   "long_leave": leave.get("long_leave", False)}
            if leave.get("sick_note_pending"):
                out["sick_note_pending"] = True
            if invalid:
                out["invalid_attachment_ids"] = invalid
            return out

        def list_my_leaves() -> dict:
            items = be.list_leaves(self._actor())
            return {"leaves": items, "count": len(items)}

        def get_leave_balance() -> dict:
            return {"ok": True, "balance": be.leave_balance(self._actor())}

        def supplement_leave_document(leave_id: str,
                                      attachment_id: str = "") -> dict:
            """给请假单补件（如病假条）：仅限本人单据；材料需为本人上传。"""
            metas, invalid = _resolve_attachments([attachment_id] if attachment_id else None)
            if not metas:
                return {"ok": False,
                        "error": f"材料无效或不属于本人：{attachment_id or '（无）'}"}
            try:
                leave = be.supplement_leave_document(leave_id, metas[0], self._actor())
            except OfficeError as exc:
                return self._fail(exc)
            return {"ok": True, "leave": leave, "attachment": metas[0]}

        def submit_attendance_fix(fix_type: str, work_date: str, time: str,
                                  reason: str = "") -> dict:
            try:
                fix = be.submit_attendance_fix(fix_type, work_date, time,
                                               self._actor(), reason)
            except OfficeError as exc:
                return self._fail(exc)
            return {"ok": True, "fix": fix,
                    "need_approval": fix.get("need_approval", False)}

        def list_my_attendance_fixes() -> dict:
            items = be.list_attendance_fixes(self._actor())
            return {"fixes": items, "count": len(items)}

        def submit_service_request(kind: str, title: str = "", detail: str = "",
                                   urgency: str = "", location: str = "",
                                   extra: Optional[dict] = None,
                                   attachment_ids: Optional[list[str]] = None) -> dict:
            """提交 OA 服务工单（IT 报修/账号权限/设备领用/车位/访客车辆/公务用车）。

            一个工具覆盖 6 类服务：kind 决定归口（IT 运维 / 行政后勤）与单号前缀；
            必填校验在后端 fail-closed（见 SERVICE_REQUIRED），缺项即结构化返回原因供反问。
            """
            metas, invalid = _resolve_attachments(attachment_ids)
            try:
                req = be.submit_service_request(
                    kind=kind, title=title, detail=detail, applicant=self._actor(),
                    urgency=urgency or "一般", location=location,
                    extra=dict(extra or {}), attachments=metas)
            except OfficeError as exc:
                return self._fail(exc)
            out: dict[str, Any] = {"ok": True, "service_request": req,
                                   "need_approval": req.get("need_approval", False),
                                   "assignee": req.get("assignee", "")}
            if invalid:
                out["invalid_attachment_ids"] = invalid
            return out

        def ask_user(question: str, fields: list[dict]) -> dict:
            """关键信息缺失时反问：返回弹窗所需字段定义（前端渲染下拉/多选/输入）。

            语义：Agent 调用后即结束本轮循环，把问题交给用户；前端据 clarify.fields
            渲染弹窗（select→下拉选项；multiselect→可切换 chips；date/时间/number/
            text→输入框），用户补齐后再提交，参数即齐备。

            兜底：LLM 常漏填 select/multiselect 的 options，前端就会"没有下拉选项"；
            此处按 key/label 命中规范枚举自动补全，保证类型/类别类字段一定有选项可选。
            """
            norm: list[dict] = []
            for f in (fields or []):
                if not (isinstance(f, dict) and f.get("key")):
                    continue
                g = dict(f)
                opts = [str(o) for o in (g.get("options") or []) if str(o).strip()]
                if str(g.get("type") or "text") in ("select", "multiselect") and not opts:
                    opts = _clarify_options(str(g.get("key")),
                                           str(g.get("label") or ""))
                if opts:
                    g["options"] = opts
                # 已知值预填：把 LLM 从原话解析出的值透传给前端，弹窗打开即"半填"
                pre = str(g.get("value") or "").strip()
                if pre:
                    g["value"] = pre
                else:
                    g.pop("value", None)
                norm.append(g)
            # 已知值预填：把原话里已能解析出的字段并入弹窗（已有的补 value、
            # 缺的补字段），用户打开即"半填"，只需人工补真正缺失的项
            norm = _enrich_clarify_fields(getattr(self, "_active_query", ""), norm)
            return {"ok": True, "clarify": {
                "question": str(question or "请补充以下信息"),
                "fields": norm}}

        # ---- 制造数据只读速查（转发统一注册查询；只读、受权限约束） ----

        def query_material_stock(part_no: Optional[str] = None,
                                 keyword: Optional[str] = None) -> dict:
            """材料库存速查：料号精确优先，查不到或给的是口语词 → 关键字模糊。

            用户口语常给名称/规格（"M8 螺栓"而非料号 SCREW-M8-20），
            故精确料号无果时退化为 LIKE 模糊搜索（料号或名称包含）。
            """
            if part_no and not keyword:
                res = self._data_query("material_stock_by_part",
                                       {"part_no": part_no}, "material_inventory")
                if res.get("ok") and res.get("count"):
                    return res
                keyword = part_no  # 精确无果 → 用原词模糊
            if keyword:
                return self._data_query("material_stock_search", {"kw": keyword},
                                        "material_inventory")
            return self._data_query("material_stock_all", None, "material_inventory")

        def query_finished_stock(model: Optional[str] = None) -> dict:
            if model:
                return self._data_query("finished_stock_by_model",
                                        {"model": model}, "finished_inventory")
            return self._data_query("finished_stock_all", None, "finished_inventory")

        def query_incoming_batch(batch_no: Optional[str] = None,
                                 supplier: Optional[str] = None,
                                 unqualified_only: bool = False) -> dict:
            if batch_no:
                return self._data_query("incoming_batch_by_no",
                                        {"batch_no": batch_no}, "incoming_batch")
            if supplier or unqualified_only:
                # 供应商/不合格筛选：取全量扫描（上限 300）后客户端过滤，
                # 避免 :supplier 绑定需从口语抽取供应商名的脆弱环节，
                # 也避免"近期 10 条"窗口漏掉更早的不合格批次。
                res = self._data_query("incoming_batch_scan", None,
                                       "incoming_batch")
                if res.get("ok"):
                    rows = res.get("rows", [])
                    if supplier:
                        rows = [r for r in rows
                                if supplier in str(r.get("supplier") or "")]
                    if unqualified_only:
                        rows = [r for r in rows
                                if str(r.get("inspect_status")) in ("fail", "deviation")]
                    res["rows"] = rows
                    res["count"] = len(rows)
                    res["filter"] = {k: v for k, v in (
                        ("supplier", supplier),
                        ("unqualified_only", unqualified_only)) if v}
                return res
            return self._data_query("incoming_batch_recent", None, "incoming_batch")

        def query_stock_aging(days: int = 90) -> dict:
            res = self._data_query("material_aging_top", None, "material_inventory")
            if res.get("ok"):
                thr = int(days or 90)
                aging = [r for r in res.get("rows", [])
                         if int(r.get("age_days") or 0) >= thr]
                res["rows"] = aging
                res["count"] = len(aging)
                res["threshold_days"] = thr
            return res

        def query_shortage() -> dict:
            return self._data_query("material_shortage", None, "material_inventory")

        def query_batch_trace(batch_no: str) -> dict:
            return self._data_query("batch_trace_full",
                                    {"batch_no": batch_no}, "incoming_batch")

        def request_approval(title: str, prompt: str, category: str = "room") -> dict:
            """发起人工确认（治理：只 propose，工单走既有治理链，Agent 永不放行）。"""
            wf = self.orchestrator.runtime.workflow
            steps = _OFFICE_FLOW.get(category, _OFFICE_FLOW["default"])
            context = {
                "category": category, "origin": "office_agent",
                "actor": self._actor(),
                "reason": (f"办公事务（{_OFFICE_CATEGORY_CN.get(category, '办公办理')}）"
                           f"；现需人工确认：{prompt}"),
                "flow": single_tool_flow(steps, f"office_{category}", prompt,
                                         seed=f"{title}|{prompt}"),
            }
            assignee = _OFFICE_ASSIGNEE.get(category, "行政专员")
            # R9：办公办理按归口岗位写行归属；归一化失败 → 空域（保持旧可见性）
            from ..services import workflow_scope
            owner_scope = workflow_scope.owner_scope_from_assignees(
                wf.permission_registry, [assignee])
            task = wf.create_task(
                wf_id=f"office_{category}", title=title[:80], context=context,
                assignees=[assignee], priority="P2", hitl=True,
                hitl_prompt=prompt[:300], owner_scope=owner_scope)
            return {"task_uid": task.task_uid, "status": task.status,
                    "assignee": assignee,
                    "note": f"已创建人工确认工单，等待{assignee}处理"}

        return {
            "list_rooms": (list_rooms, {
                "type": "object", "properties": {
                    "capacity_min": {"type": "integer", "description": "最小容纳人数"},
                    "equipment": {"type": "array", "items": {"type": "string"},
                                  "description": "需要的设备，如 ['视频会议','投影']"}},
                }),
            "find_available_rooms": (find_available_rooms, {
                "type": "object",
                "properties": {"date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                               "start": {"type": "string", "description": "开始 HH:MM"},
                               "end": {"type": "string", "description": "结束 HH:MM"},
                               "capacity_min": {"type": "integer"},
                               "equipment": {"type": "array", "items": {"type": "string"}}},
                "required": ["date", "start", "end"]}),
            "list_my_bookings": (list_my_bookings, {"type": "object", "properties": {}}),
            "book_room": (book_room, {
                "type": "object",
                "properties": {"room_id": {"type": "string"},
                               "date": {"type": "string"},
                               "start": {"type": "string"},
                               "end": {"type": "string"},
                               "title": {"type": "string", "description": "会议主题"},
                               "attendees": {"type": "integer"}},
                "required": ["room_id", "date", "start", "end"]}),
            "cancel_booking": (cancel_booking, {
                "type": "object", "properties": {"booking_id": {"type": "string"}},
                "required": ["booking_id"]}),
            "list_restaurants": (list_restaurants, {"type": "object", "properties": {}}),
            "get_menu": (get_menu, {
                "type": "object", "properties": {"restaurant_id": {"type": "string"}},
                "required": ["restaurant_id"]}),
            "place_order": (place_order, {
                "type": "object",
                "properties": {"restaurant_id": {"type": "string"},
                               "items": {"type": "array", "items": {
                                   "type": "object",
                                   "properties": {"item_id": {"type": "string"},
                                                  "qty": {"type": "integer"}},
                                   "required": ["item_id", "qty"]}},
                               "meal_time": {"type": "string", "description": "档期，如 午餐/晚餐"},
                               "note": {"type": "string"}},
                "required": ["restaurant_id", "items"]}),
            "cancel_order": (cancel_order, {
                "type": "object", "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"]}),
            "list_my_orders": (list_my_orders, {"type": "object", "properties": {}}),
            "get_current_time": (get_current_time, {"type": "object", "properties": {}}),
            "organize_expense": (organize_expense, {
                "type": "object",
                "properties": {"category": {"type": "string",
                                           "enum": ["差旅", "招待", "办公用品", "交通", "其他"]},
                               "amount": {"type": "number", "description": "金额（元）"},
                               "note": {"type": "string", "description": "报销事由（含明细，如 高铁 980 + 住宿 306）"},
                               "attachment_kinds": {"type": "array",
                                                    "items": {"type": "string"},
                                                    "description": "本次材料类别（缺省用本次已上传材料的类别）"}},
                "required": ["category", "amount"]}),
            "submit_expense": (submit_expense, {
                "type": "object",
                "properties": {"category": {"type": "string",
                                            "enum": ["差旅", "招待", "办公用品", "交通", "其他"]},
                               "amount": {"type": "number", "description": "金额（元）"},
                               "expense_date": {"type": "string", "description": "发生日期 YYYY-MM-DD"},
                               "note": {"type": "string", "description": "事由"},
                               "invoice_no": {"type": "string", "description": "发票号（可选）"},
                               "attachment_ids": {"type": "array",
                                                  "items": {"type": "string"},
                                                  "description": "本次已上传材料的编号（如 ['ATT00001']）"},
                               "subject": {"type": "string", "description": "主要二级科目（可选）"},
                               "breakdown": {"type": "array",
                                             "items": {"type": "object",
                                                       "properties": {
                                                           "subject": {"type": "string"},
                                                           "amount": {"type": "number"},
                                                           "note": {"type": "string"}}},
                                             "description": "整理后的科目明细（可选，来自 organize_expense）"}},
                "required": ["category", "amount", "expense_date", "note"]}),
            "list_my_expenses": (list_my_expenses, {"type": "object", "properties": {}}),
            "apply_leave": (apply_leave, {
                "type": "object",
                "properties": {"leave_type": {"type": "string",
                                              "enum": ["年假", "事假", "病假", "调休", "婚假", "产假"]},
                               "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                               "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                               "reason": {"type": "string", "description": "请假事由"},
                               "days": {"type": "number", "description": "天数（缺省按自然日含端点计算）"},
                               "attachment_ids": {"type": "array", "items": {"type": "string"},
                                                  "description": "本次已上传材料编号（如病假条）"}},
                "required": ["leave_type", "start_date", "end_date", "reason"]}),
            "list_my_leaves": (list_my_leaves, {"type": "object", "properties": {}}),
            "get_leave_balance": (get_leave_balance, {"type": "object", "properties": {}}),
            "supplement_leave_document": (supplement_leave_document, {
                "type": "object",
                "properties": {"leave_id": {"type": "string", "description": "请假单号（如 QJ00001）"},
                               "attachment_id": {"type": "string",
                                                 "description": "补件材料编号（缺省用本次已上传材料）"}},
                "required": ["leave_id"]}),
            "submit_attendance_fix": (submit_attendance_fix, {
                "type": "object",
                "properties": {"fix_type": {"type": "string",
                                            "enum": ["上班补卡", "下班补卡"]},
                               "work_date": {"type": "string", "description": "补卡日期 YYYY-MM-DD"},
                               "time": {"type": "string", "description": "补卡时间 HH:MM"},
                               "reason": {"type": "string", "description": "补卡事由"}},
                "required": ["fix_type", "work_date", "time", "reason"]}),
            "list_my_attendance_fixes": (list_my_attendance_fixes,
                                         {"type": "object", "properties": {}}),
            "submit_service_request": (submit_service_request, {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(SERVICE_KINDS),
                             "description": "服务类型：it_repair=IT 故障报修 / it_access=账号与权限申请 / "
                                            "it_device=设备领用申请 / parking=车位申请 / "
                                            "visitor_vehicle=访客车辆报备 / business_car=公务用车申请"},
                    "title": {"type": "string",
                              "description": "一句话事由（报修填故障现象、申请填事由）"},
                    "detail": {"type": "string", "description": "补充说明（可选）"},
                    "urgency": {"type": "string", "enum": list(SERVICE_URGENCIES),
                                "description": "紧急程度（缺省 一般）"},
                    "location": {"type": "string",
                                 "description": "设备与位置（IT 报修必填，如「SMT 车间 2 号线」）"},
                    "extra": {"type": "object",
                              "description": "结构化补充字段：system / permission / device_type / "
                                             "plate（车牌）/ visit_date（来访日期）/ use_from（用车起始）"
                                             " / dest（目的地）等，必填项见各 kind 口径"},
                    "attachment_ids": {"type": "array", "items": {"type": "string"},
                                       "description": "本次已上传材料编号（如故障照片 / 行驶证 / 授权审批单）"}},
                "required": ["kind", "title"]}),
            "ask_user": (ask_user, {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "向用户提出的问题"},
                    "fields": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "label": {"type": "string"},
                            "type": {"type": "string",
                                     "enum": ["select", "multiselect", "date",
                                              "time", "number", "text"]},
                            "options": {"type": "array", "items": {"type": "string"}},
                            "value": {"type": "string",
                                      "description": "已从用户原话中解析出的字段值（有则预填弹窗，"
                                                     "无则留空由用户补）"},
                            "required": {"type": "boolean"}},
                        "required": ["key", "label", "type"]}},
                },
                "required": ["question", "fields"]}),
            "query_material_stock": (query_material_stock, {
                "type": "object",
                "properties": {"part_no": {"type": "string",
                                           "description": "料号，如 CAP-0402-10uF；缺省给全量"},
                               "keyword": {"type": "string",
                                           "description": "口语关键词（料号或名称片段，如 M8、螺栓、TYPE-C）；精确料号查不到时可用"}}}),
            "query_finished_stock": (query_finished_stock, {
                "type": "object",
                "properties": {"model": {"type": "string",
                                         "description": "产品型号，如 A型号；缺省给全量"}}}),
            "query_incoming_batch": (query_incoming_batch, {
                "type": "object",
                "properties": {"batch_no": {"type": "string", "description": "来料批号"},
                               "supplier": {"type": "string", "description": "供应商名称"},
                               "unqualified_only": {"type": "boolean",
                                                    "description": "仅看不合格/让步接收批次（问'不合格来料'时置 true）"}}}),
            "query_stock_aging": (query_stock_aging, {
                "type": "object",
                "properties": {"days": {"type": "integer",
                                        "description": "库龄阈值（天），缺省 90（呆滞口径）"}}}),
            "query_shortage": (query_shortage, {"type": "object", "properties": {}}),
            "query_batch_trace": (query_batch_trace, {
                "type": "object",
                "properties": {"batch_no": {"type": "string", "description": "批号"}},
                "required": ["batch_no"]}),
            "request_approval": (request_approval, {
                "type": "object",
                "properties": {"title": {"type": "string"},
                               "prompt": {"type": "string"},
                               "category": {"type": "string",
                                            "description": "事项类别（决定审批归口：room→行政专员、"
                                                           "meal→行政专员、expense→财务专员、"
                                                           "leave→部门主管、attendance→HR 专员）",
                                            "enum": ["room", "meal", "expense",
                                                     "leave", "attendance"]}},
                "required": ["title", "prompt", "category"]}),
        }

    # 工具描述（给 Agent 的能力说明；与系统提示词的工具清单一致）
    _TOOL_DESCRIPTIONS = {m["name"]: m["desc"] for m in OFFICE_TOOLS_META}

    def _tool_schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": name, "description": self._TOOL_DESCRIPTIONS.get(name, ""),
            "parameters": schema}}
            for name, (_, schema) in self._tools().items()]

    # ---------- 主循环 ----------

    def run(self, query: str, user_ctx: Optional[dict] = None,
            attachments: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """执行一次办公任务；返回 {answer, report, steps, hitl_task, data_gaps, ...}。

        ``attachments``：本次请求携带的**本人上传材料**元数据（服务端已解析校验）。
        提交报销/病假时绑定到单据；未传 ``attachment_ids`` 的工具调用自动用这批材料。
        """
        self.user_ctx = dict(user_ctx or {})
        self._active_query = query
        self._pending_attachments = [dict(a) for a in (attachments or [])
                                     if isinstance(a, dict)]
        t0 = time.perf_counter()
        steps: list[AgenticStep] = []
        hitl_task: Optional[dict] = None
        clarify: Optional[dict] = None
        final: Optional[dict] = None
        # 用户已通过弹窗补充信息 ⇒ 本轮禁用 ask_user：从根上断开
        # "反问 → 补充 → 再反问" 的无限弹框循环（信息够则提交，不够则在回答里说明）。
        answered = "补充信息：" in query
        llm = self._get_llm(exclude={"ask_user"} if answered else None)

        from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

        user_line = f"员工问题：{query}"
        if self._pending_attachments:
            desc = "、".join(
                f"{a.get('attachment_id')}({a.get('kind')}·{a.get('filename')})"
                for a in self._pending_attachments)
            user_line += (f"\n本次已上传材料（提交报销/病假时请把 attachment_ids 原样传入，"
                          f"如 attachment_ids=[\"{self._pending_attachments[0].get('attachment_id')}\"]）："
                          f"{desc}")
        if answered:
            user_line += ("\n（该问题已附用户通过表单补充的信息：视为反问已补齐，"
                          "请勿再调用 ask_user，直接用已有信息调用相应工具办理。）")
        messages: list[Any] = [
            SystemMessage(content=_SYSTEM_TEMPLATE.format(**self.cfg)),
            ("user", user_line),
        ]

        for step_no in range(1, self.max_steps + 1):
            resp = llm.invoke(messages)
            tool_calls = getattr(resp, "tool_calls", None) or []
            if not tool_calls:
                final = AgenticDomainAgent._parse_final(
                    getattr(resp, "content", "") or "")
                break
            messages.append(AIMessage(content=resp.content or "",
                                      tool_calls=resp.tool_calls))
            paused = False
            for tc in tool_calls:
                name = tc["name"]
                args = dict(tc.get("args") or {})
                st = AgenticStep(step=step_no, tool=name, args=args)
                fn = self._tools().get(name)
                if fn is None:
                    st.ok, st.summary = False, f"未知工具: {name}"
                    payload: dict[str, Any] = {"error": st.summary}
                else:
                    try:
                        payload = fn[0](**args)
                        # 业务失败以 {"ok": False, "error": ...} 返回（不抛异常）：
                        # 步骤状态须据此如实标记，供审计与前端区分成功/被拒
                        st.ok = (bool(payload.get("ok", True))
                                 if isinstance(payload, dict) else True)
                        st.summary = json.dumps(payload, ensure_ascii=False)[:80]
                        if name == "request_approval":
                            hitl_task = payload
                    except Exception as exc:  # noqa: BLE001 —— 失败反馈给 Agent
                        st.ok = False
                        st.summary = f"{type(exc).__name__}: {str(exc)[:80]}"
                        payload = {"error": str(exc)[:200]}
                steps.append(st)
                messages.append(ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False)[:1500],
                    tool_call_id=tc["id"]))
                # 反问即结束本轮：把问题与字段定义交给用户，等补齐后再继续；
                # 已补充过信息（answered）时忽略反问，兜底杜绝无限弹框
                if (name == "ask_user" and not answered
                        and isinstance(payload, dict)):
                    cl = payload.get("clarify")
                    if cl:
                        clarify = cl
                        paused = True
                        break
            if paused:
                break

        if clarify is not None:
            # 反问态：助手把问题作为回答抛出，前端据 clarify.fields 弹窗收集
            final = {"answer": str(clarify.get("question") or "请补充以下信息"),
                     "data_used": [], "needs_hitl": False, "data_gaps": []}
        if final is None:
            final = {"answer": "已在最大步数内完成部分办理，未能形成完整结论——"
                               "已把已完成的操作列在下方，请人工确认或换一种说法。",
                     "data_used": [], "needs_hitl": False,
                     "data_gaps": ["达到最大工具循环步数"]}

        answer = str(final.get("answer") or "")
        steps_text = "；".join(
            f"{s.tool}({json.dumps(s.args, ensure_ascii=False)[:40]})" for s in steps)
        report = (f"{answer}\n\n（由{self.cfg['name']}自主完成：{len(steps)} 步工具调用"
                  f"{'：' + steps_text if steps_text else ''}；"
                  f"耗时 {time.perf_counter() - t0:.1f}s）")
        return {
            "answer": answer,
            "report": report,
            "steps": [s.to_dict() for s in steps],
            "hitl_task": hitl_task,
            "clarify": clarify,
            "needs_hitl": bool(final.get("needs_hitl")) or hitl_task is not None,
            "data_gaps": list(final.get("data_gaps") or []),
            "data_used": list(final.get("data_used") or []),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
