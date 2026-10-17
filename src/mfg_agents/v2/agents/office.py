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
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..services.office import OfficeBackend, OfficeError, get_office_backend
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
    "default": [("office_intent", "办公助手 · 意图识别", 5_000),
                ("office_handle", "办公助手 · 事务办理", 18_000)],
}
_OFFICE_CATEGORY_CN = {"room": "会议室预订", "meal": "订餐"}

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
    {"name": "request_approval", "desc": "发起人工确认（高影响事项：报告厅/客餐/超预算）", "kind": "写/HITL"},
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
             "以及高影响办公事项的人工确认；② 制造数据速查（只读）——查询材料"
             "库存、成品库存与可发量、来料批次与检验结论、库龄呆滞、缺料缺口、"
             "批次追溯。"),
    "boundary": ("边界：制造数据查询一律**只读**——不得改动任何库存/批次数据，"
                 "不得承诺出入库/调拨/盘点等写操作；若用户要求这类操作，如实说明"
                 "本助手不办理并引导走对应系统。只读查询受岗位数据权限约束，越权"
                 "数据如实告知无权限。办公写操作你只能在**本人**名下预订/取消；"
                 "不得代他人下单或取消他人订单。会议室时段冲突时不得静默覆盖，"
                 "必须改选并如实告知冲突原因。"),
    "hitl_rule": ("HITL 准则：当预订的是**需授权资源**（报告厅/培训室等 capacity≥20 "
                  "或后端标记 need_approval）、或下单的是**商务客餐/宴请**、或订单"
                  "金额达到或超过 500 元时，你必须停下调 request_approval 发起人工"
                  "确认工单，并在回答里说明「已提交待确认」；普通会议室与员工工作餐"
                  "可直接办理，无需 HITL。制造数据为只读查询，不触发 HITL。"),
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
- request_approval(title, prompt, category)：发起人工确认（高影响事项；你只发起，绝不放行）

### 制造数据速查（只读；转发统一口径注册查询，受岗位数据权限约束）
- query_material_stock(part_no?, keyword?)：查材料库存——不传参给全量；传料号给该料号各仓在库/预留/冻结/可用/安全库存/库龄；用户给的是口语名称/规格（如"螺栓""TYPE-C"）时用 keyword 模糊搜索
- query_finished_stock(model?)：查成品库存与可发量 ATP——不传型号给全量；传型号（如 A型号）给各仓可发量
- query_incoming_batch(batch_no?, supplier?, unqualified_only?)：查来料批次与 IQC 结论——传批号按批号查、传供应商给该供应商来料批次、问"不合格/异常来料"时置 unqualified_only=true、都不传给近期批次（含待检/让步接收）
- query_stock_aging(days?)：查库龄/呆滞 TOP（days 缺省 90，按库龄倒序）
- query_shortage()：查缺料清单（可用量低于安全库存，含缺口数量）
- query_batch_trace(batch_no)：按批号做批次追溯（来料综合→传统检验结论→不良记录）

## 工作方式（ReAct）
1. 先判断需要什么：订会议室先查可用（find_available_rooms），订餐先看菜单（get_menu）；查数据先选对工具（材料/成品/来料/库龄/缺料/追溯）；
2. 日期/时间缺失时先向用户澄清（可在最终回答里追问），不要臆造日期或时段；料号/型号/批号缺失且用户未指明时，可用不传参的全量速查工具给出概览；
3. 写操作前想清楚参数；冲突/失败时读工具返回的原因并换时段或换房间再试；
4. 制造数据为只读：只陈述查询结果并标注口径（可用量=在库-预留，库龄按天），不得编造、不得承诺写操作；工具返回"无权限"时如实转述，不得绕过；
5. {hitl_rule}
6. 完成或达到边界时，输出最终 JSON（不要输出其他文字）：
{{"answer": "面向员工的完整中文回答（含预订号/订单号、时间、地点、金额等关键信息；数据查询含关键数值与口径）",
  "data_used": ["用到的工具与关键数据，如 query_shortage() 5 项低于安全库存"],
  "needs_hitl": true/false,
  "hitl_title": "需人工确认事项标题（needs_hitl=true 时必填）",
  "hitl_prompt": "给审批人的说明（needs_hitl=true 时必填）",
  "hitl_category": "room|meal 之一（needs_hitl=true 时必填）",
  "data_gaps": ["未能办理/获取的信息及原因"]}}

注意：只输出一个 JSON 对象；预订号/订单号必须来自工具返回；不得编造会议室、菜品、价格或库存数值。"""


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

    # ---------- LLM（懒加载；工具循环不需要深度思考） ----------

    def _get_llm(self) -> Any:
        if self._llm is None:
            from ...tools.llm_factory import get_llm
            self._llm = get_llm(temperature=0.0, tier="L1", max_tokens=900,
                                timeout=45, thinking="disabled")
            self._llm = self._llm.bind_tools(self._tool_schemas())
        return self._llm

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
            from ..services.text2sql import TABLE_ACL
            allowed = TABLE_ACL.get(table, set())
            if not (self._roles() & allowed):
                return {"ok": False,
                        "error": f"当前岗位无权限查询该数据（{table}），请走对应职责岗或申请授权"}
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
                # 供应商/不合格筛选：取全量扫描（上限 60）后客户端过滤，
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
            task = wf.create_task(
                wf_id=f"office_{category}", title=title[:80], context=context,
                assignees=["行政专员"], priority="P2", hitl=True,
                hitl_prompt=prompt[:300])
            return {"task_uid": task.task_uid, "status": task.status,
                    "note": "已创建人工确认工单，等待行政专员处理"}

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
                               "category": {"type": "string", "enum": ["room", "meal"]}},
                "required": ["title", "prompt"]}),
        }

    # 工具描述（给 Agent 的能力说明；与系统提示词的工具清单一致）
    _TOOL_DESCRIPTIONS = {m["name"]: m["desc"] for m in OFFICE_TOOLS_META}

    def _tool_schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": name, "description": self._TOOL_DESCRIPTIONS.get(name, ""),
            "parameters": schema}}
            for name, (_, schema) in self._tools().items()]

    # ---------- 主循环 ----------

    def run(self, query: str, user_ctx: Optional[dict] = None) -> dict[str, Any]:
        """执行一次办公任务；返回 {answer, report, steps, hitl_task, data_gaps, ...}。"""
        self.user_ctx = dict(user_ctx or {})
        t0 = time.perf_counter()
        steps: list[AgenticStep] = []
        hitl_task: Optional[dict] = None
        final: Optional[dict] = None
        llm = self._get_llm()

        from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

        messages: list[Any] = [
            SystemMessage(content=_SYSTEM_TEMPLATE.format(**self.cfg)),
            ("user", f"员工问题：{query}"),
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
            "needs_hitl": bool(final.get("needs_hitl")) or hitl_task is not None,
            "data_gaps": list(final.get("data_gaps") or []),
            "data_used": list(final.get("data_used") or []),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
