"""Text-to-SQL 工具：把自然语言转成只读SQL并执行。"""
import re
from typing import Any
from ..db.mysql_client import run_sql
from .llm_factory import get_llm
from ..events import push

SCHEMA_PROMPT = """
/no_think
你是制造业数据库专家。当前数据库为 SQLite，有以下表：

production_output(id, record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min)
line_stop_event(id, line_code, product_model, start_time, end_time, stop_code, root_cause, status)
quality_defect(id, batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time)
material_batch(id, batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time)
equipment_log(id, equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal)
inventory(id, product_model, warehouse, on_hand_qty, reserved_qty)
shipment(id, order_no, product_model, qty, ship_status, eta)
customer_complaint(id, complaint_no, customer, product_model, batch_no, defect_desc, status)

注意：
- product_model 的精确值为 'A型号'、'B型号'（中文，无空格）
- line_code 的精确值为 'SMT-1'、'DIP-2'
- "今天"固定用 record_date='2026-10-15'
- "近2小时"用 log_time >= datetime('now','-2 hours') 或 inspect_time >= datetime('now','-2 hours')
- 时间字段是TEXT类型

规则：
1. 只生成SELECT，禁止INSERT/UPDATE/DELETE/DROP
2. 只输出SQL本身，不要解释、不要markdown代码块
"""


def _extract_sql(text: str) -> str:
    """从LLM返回中提取SQL，兼容thinking标签和markdown包裹。"""
    # 去掉 <think>...</think> 内容（qwen3.5 thinking模型）
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)

    # 尝试 ```sql ... ``` 包裹
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    if m:
        text = m.group(1)

    # 找第一个 SELECT 开头的语句
    m = re.search(r"(SELECT\s+.*?)(;|$)", text, re.S | re.I)
    if m:
        text = m.group(1)

    sql = text.strip().rstrip(";").strip()

    # 后处理：LLM常把中文型号写成带空格（'A 型号'），统一去掉
    sql = re.sub(r"(['\"])\s*([A-Za-z])\s+型号\s*(['\"])", r"\1\2型号\3", sql)

    # 安全检查
    if re.search(r"\b(insert|update|delete|drop|alter|create|truncate|grant)\b", sql, re.I):
        raise ValueError(f"非法SQL: {sql}")
    if not sql.upper().startswith("SELECT"):
        raise ValueError(f"未提取到有效SQL: {sql[:200]}")
    return sql


def text_to_sql(question: str, top_k: int = 20) -> dict[str, Any]:
    llm = get_llm(temperature=0)
    push({"type": "llm_start", "detail": "正在生成SQL查询..."})
    resp = llm.invoke(f"{SCHEMA_PROMPT}\n\n问题：{question}")
    raw = resp.content if hasattr(resp, "content") else str(resp)
    try:
        sql = _extract_sql(raw)
    except Exception as e:
        return {"sql": "", "error": str(e), "rows": [], "raw": raw[:500]}

    push({"type": "tool_start", "tool": "SQL查询", "detail": sql})
    try:
        df = run_sql(sql)
        if len(df) > top_k:
            df = df.head(top_k)
        rows = df.to_dict(orient="records")
        push({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(rows)}行"})
        return {
            "sql": sql,
            "rows": rows,
            "row_count": len(rows),
            "columns": list(df.columns),
        }
    except Exception as e:
        push({"type": "tool_end", "tool": "SQL查询", "detail": f"错误: {e}"})
        return {"sql": sql, "error": str(e), "rows": []}
