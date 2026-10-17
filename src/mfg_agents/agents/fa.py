"""FA分析Agent：RAG检索 + LLM推理。"""
from typing import Any
from ..common.rag_tools import search_cases
from ..common.llm_factory import get_llm


def fa_agent(state: dict[str, Any]) -> dict[str, Any]:
    defect = state.get("dominant_defect", {}) or {}
    alarms = state.get("abnormal_alarms", []) or []
    line = state.get("line_code", "SMT-1")
    events = []

    query = f"{line}线 {defect.get('defect_type','')} 设备告警={','.join(a.get('alarm_code','') for a in alarms[:3])}"

    events.append({"type": "tool_start", "tool": "RAG检索", "detail": f"查询: {query}"})
    cases = search_cases(query, n_results=3)
    events.append({"type": "tool_end", "tool": "RAG检索", "detail": f"匹配{len(cases)}条历史案例"})

    case_text = "\n".join(f"- {c['document']} 评级={c['metadata'].get('rating','?')} 复线={c['metadata'].get('recovery_hours','?')}h"
                          for c in cases)
    prompt = f"""
你是制造业Failure Analysis专家。基于以下信息判定异常等级并预估复线时间。

【当前异常】
- 线体：{line}
- 主导不良：{defect.get('defect_type','未知')}
- 设备异常：{[a.get('param_name')+'='+a.get('param_value') for a in alarms]}

【历史相似案例】
{case_text}

请输出（JSON格式）：
{{"rating":"A/B/C/D","root_cause":"一句话","eta_hours":2,"verification":["..."]}}
只输出JSON。
"""
    events.append({"type": "llm_start", "detail": "FA模型推理中（评级/根因/复线时间）..."})
    # 根因推断是多因素权衡（评级/ETA/验证项），开轻量思考；与 V2 FA 节点档位对齐
    llm = get_llm(temperature=0.1, thinking="low")
    resp = llm.invoke(prompt)
    content = resp.content if hasattr(resp, "content") else str(resp)

    import json, re
    rating, eta, root, verif = "C", 2, "待分析", []
    try:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            data = json.loads(m.group(0))
            rating = data.get("rating", "C")
            eta = data.get("eta_hours", 2)
            root = data.get("root_cause", "待分析")
            verif = data.get("verification", [])
    except Exception:
        pass

    return {
        "fa_cases": cases,
        "fa_rating": rating,
        "root_cause": root,
        "eta_hours": eta,
        "verification_items": verif,
        "fa_summary": f"评级{rating}，根因：{root}，预计{eta}h复线",
        "_events": events,
    }
