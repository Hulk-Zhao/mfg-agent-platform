"""业务Agent：规则匹配意图识别，跳过LLM。"""
from typing import Any


# 场景关键词规则
SCENE_RULES = [
    ("line_stop", ["停线", "停了", "什么时候复", "何时复", "急单", "出货影响"]),
    ("incoming_quality", ["来料", "IQC", "锡膏", "批次有问题", "供应商", "COA"]),
    ("customer_complaint", ["投诉", "8D", "客户", "不良率", "华为"]),
    ("delivery_risk", ["发货", "能发", "交期", "库存", "在途", "催"]),
    ("equipment_warning", ["设备", "参数", "预警", "维护", "告警"]),
]


def business_agent(state: dict[str, Any]) -> dict[str, Any]:
    query = state.get("user_query", "")

    # 默认场景
    scenario = "line_stop"
    for scene, keywords in SCENE_RULES:
        if any(k in query for k in keywords):
            scenario = scene
            break

    # 简单的型号/线体抽取
    model = "A型号"
    for m in ["A型号", "B型号", "A", "B"]:
        if m in query:
            model = "A型号" if m.startswith("A") else "B型号"
            break

    line = "SMT-1"
    if "DIP" in query:
        line = "DIP-2"
    elif "SMT" in query:
        import re
        m = re.search(r"SMT-(\d)", query)
        if m:
            line = f"SMT-{m.group(1)}"

    urgency = "high" if any(k in query for k in ["急", "催", "投诉"]) else "normal"

    return {
        "scenario": scenario,
        "product_model": model,
        "line_code": line,
        "batch_no": None,
        "customer": None,
        "urgency": urgency,
        "task_plan": [
            "1) 制造Agent：确认停线/OEE",
            "2) 品质/供应链/设备Agent并行：多维度取证",
            "3) FA Agent：RAG评级与复线预估",
            "4) 报告Agent：对客脱敏输出",
        ],
    }
