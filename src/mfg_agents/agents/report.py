"""报告Agent：基于内部分析结果，生成对客脱敏报告 + 内部简报。"""
from typing import Any
from ..tools.llm_factory import get_llm


def report_agent(state: dict[str, Any]) -> dict[str, Any]:
    internal_brief = {
        "停线确认": state.get("confirmed_stop"),
        "生产": state.get("production_summary"),
        "品质": state.get("quality_summary"),
        "供应链": state.get("supply_summary"),
        "设备": state.get("equipment_summary"),
        "FA评级": state.get("fa_summary"),
        "仓储": state.get("warehouse_summary", ""),
    }

    # 对客报告：严格脱敏
    prompt = f"""
你是制造业业务接口人。基于以下内部数据，生成给客户的正式回复（200字以内）。

【内部数据】
{internal_brief}

【规则】
1. 禁止出现：设备故障、供应商来料不良、品质不良超标、停线、根因、维修
2. 统一替换为：产线例行校准、批次质量复核、工艺参数优化、计划内暂停
3. 必须包含：当前状态、预计恢复时间（基于ETA换算）、补救措施、发货影响
4. 语气专业诚恳，不推诿
5. 只输出报告正文，不要标题
"""
    llm = get_llm(temperature=0.3)
    resp = llm.invoke(prompt)
    customer_report = resp.content if hasattr(resp, "content") else str(resp)

    return {
        "internal_brief": internal_brief,
        "customer_report": customer_report,
    }
