"""LangGraph 全局状态定义。"""
from typing import TypedDict, Any


class AgentState(TypedDict, total=False):
    # 输入
    user_query: str

    # 业务Agent输出
    scenario: str
    product_model: str
    line_code: str
    batch_no: str | None
    customer: str | None
    urgency: str
    task_plan: list[str]

    # 制造Agent
    oee_data: dict
    stop_info: dict | None
    confirmed_stop: bool
    production_summary: str

    # 品质Agent
    quality_data: dict
    dominant_defect: dict | None
    quality_summary: str

    # 供应链Agent
    supply_data: dict
    abnormal_material: dict | None
    supply_summary: str

    # 设备Agent
    equipment_data: dict
    abnormal_alarms: list
    equipment_summary: str

    # 仓储Agent
    inventory_data: dict
    shipment_data: dict
    available_qty: int
    warehouse_summary: str

    # FA Agent
    fa_cases: list
    fa_rating: str
    root_cause: str
    eta_hours: int
    verification_items: list
    fa_summary: str

    # 报告Agent
    internal_brief: dict
    customer_report: str
