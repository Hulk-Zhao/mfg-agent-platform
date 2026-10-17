"""LangGraph 图构建：5个业务场景的路由与fan-out/fan-in编排。"""
from langgraph.graph import StateGraph, START, END
from .state import AgentState
from ..common.business import business_agent
from ..agents.manufacturing import ManufacturingAgent
from ..agents.quality import quality_agent
from ..agents.supply_chain import supply_chain_agent
from ..agents.equipment import equipment_agent
from ..agents.fa import fa_agent
from ..agents.warehouse import warehouse_agent
from ..agents.report import report_agent

_mfg = ManufacturingAgent()


# ---------- 路由函数 ----------

def route_after_business(state: AgentState) -> str:
    """业务Agent识别场景后，决定先调哪个专业Agent。"""
    s = state.get("scenario", "line_stop")
    return {
        "line_stop": "manufacturing",
        "incoming_quality": "supply_chain",
        "customer_complaint": "quality",
        "delivery_risk": "manufacturing",
        "equipment_warning": "equipment",
    }.get(s, "manufacturing")


def route_after_manufacturing(state: AgentState) -> list[str] | str:
    """制造Agent后：停线场景fan-out三并行；出货风险去仓储。"""
    s = state.get("scenario")
    if s == "line_stop":
        if state.get("confirmed_stop"):
            return ["quality", "supply_chain", "equipment"]
        return "report"  # 没停线，直接回复
    if s == "delivery_risk":
        return "warehouse"
    return "report"


def route_after_supply(state: AgentState) -> str:
    if state.get("scenario") == "incoming_quality":
        return "quality"
    return "fa"  # line_stop fan-in


def route_after_quality(state: AgentState) -> str:
    return "fa"


def route_after_equipment(state: AgentState) -> str:
    return "fa"


# ---------- 构建图 ----------

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("business", business_agent)
    g.add_node("manufacturing", _mfg.run)
    g.add_node("quality", quality_agent)
    g.add_node("supply_chain", supply_chain_agent)
    g.add_node("equipment", equipment_agent)
    g.add_node("warehouse", warehouse_agent)
    g.add_node("fa", fa_agent)
    g.add_node("report", report_agent)

    g.add_edge(START, "business")
    g.add_conditional_edges("business", route_after_business)
    g.add_conditional_edges("manufacturing", route_after_manufacturing)
    g.add_conditional_edges("supply_chain", route_after_supply)
    g.add_conditional_edges("quality", route_after_quality)
    g.add_conditional_edges("equipment", route_after_equipment)
    g.add_edge("warehouse", "report")
    g.add_edge("fa", "report")
    g.add_edge("report", END)

    return g.compile()


# 单例
app = build_graph()
