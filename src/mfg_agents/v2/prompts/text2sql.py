"""Text2SQL 明细探查提示词（复用 V1 ``SCHEMA_PROMPT`` 的定位说明）。

仅收敛"明细探查"通道的 prompt 组装；schema 约束仍复用 V1 资产
``tools.sql_tools.SCHEMA_PROMPT``（零破坏，惰性导入避免循环）。

V4 迭代（多 Agent 协同贯通）：
- V1 SCHEMA_PROMPT 只覆盖 8 张旧表，看板事实表（稼动/报废/报工/订单履约/
  SPC 测量等）对 LLM 不可见——这是明细探查答非所问的根因之一；
- 追加 V2 表清单 + 口径纪律 + few-shot（聚合进 WHERE / 混聚无 GROUP BY
  是 LLM 高发病，配套语义 lint（linter.py）双保险）。
"""
from __future__ import annotations

__all__ = ["build_detail_prompt"]

# V2 看板事实表（与 data/init.sql / seed_*.sql 对齐；时间字段均为 TEXT 日期 'YYYY-MM-DD'）
# 2026-10-22 校正：旧清单多列与真实库不一致（order_fulfillment 无 risk_level /
# work_report 无 record_date 且不是「步数」表 / equipment_run_time 无 run_minutes），
# 导致模型按提示词生成 SQL 后执行报 "no such column" → 整类问题落人工兜底。
# 清单以 PRAGMA table_info 实库为准，新增岗位高频表（库存/来料/项目/测试/治理待办）。
_V2_SCHEMA = """
V2 看板事实表（同样可查，列名必须严格照抄）：
equipment_run_time(id, record_date, line_code, product_model, planned_run_min,
                   actual_run_min, stop_min, stop_reason)
scrap_record(id, record_date, line_code, product_model, output_qty, scrap_qty)
work_report(id, report_date, line_code, product_model, wo_no, process_step,
            should_report, reported)
order_fulfillment(id, order_no, customer, customer_level, product_model, qty,
                  order_date, due_date, ship_date, stage, risk_type, delay_days,
                  on_time, in_full, kit_done_qty, kit_total_qty, progress,
                  shortage_note, owner)
spc_measurement(id, characteristic, subgroup_no, seq, value, usl, lsl, measured_at,
                source, ingested_by, ingested_at, process_stage, product_category)
std_standard_audit(id, audit_date, process_stage, team, step, completeness_rate,
                   perfection_rate, compliance_rate)
equipment_log(id, equipment_id, line_code, log_time, alarm_code, param_name,
              param_value, is_abnormal)
material_batch(id, batch_no, part_no, part_name, supplier, incoming_result,
               coa_url, arrival_time)
incoming_batch(id, batch_no, part_no, part_name, supplier, arrival_time, qty,
               qualified_qty, inspect_status, warehouse, bin_location, coa_url,
               age_days)
material_inventory(id, part_no, part_name, category, warehouse, bin_location,
                   batch_no, supplier, on_hand_qty, reserved_qty, frozen_qty,
                   safety_stock, age_days, last_move_date, unit_cost, updated_at)
finished_inventory(id, product_model, warehouse, batch_no, on_hand_qty,
                   reserved_qty, safety_stock, age_days, last_move_date, updated_at)
customer_complaint(id, complaint_no, customer, product_model, batch_no,
                   defect_desc, status)
project_milestone(id, product_model, phase, milestone, status, owner,
                  planned_end, actual_end, risk_note)
test_records(id, product_model, batch_no, test_type, test_item, result,
             defect_desc, tester, test_date)
knowledge_bom(id, part_no, part_name, version, ecn_id, params_json,
              effective_date, status, note, created_at)
knowledge_ecn(id, ecn_id, title, part_no, effective_date, status, doc_ref,
              created_at)
platform_workflow_task(id, task_uid, wf_id, title, status, priority, assignees,
                       context_json, hitl_prompt, history_json, governance_json,
                       created_at, updated_at, sla_due_at)

列名纪律：只能使用上面逐字列出的列；不确定的列不要猜（如订单风险列是 risk_type
不是 risk_level，齐套是 kit_done_qty / kit_total_qty，工单报工是 should_report /
reported）。查不到对应表/列时不要拼凑近义列名，返回空结果并说明缺口。
"""

# 口径纪律 + few-shot：拦住两类高发错误（配合 linter.py 的硬伤拦截双保险）
_LINT_RULES = """
口径纪律（生成后自查，违反会被审核链拒绝）：
1. WHERE 子句中禁止出现聚合函数（SUM/COUNT/AVG/MIN/MAX）——聚合比较必须用 HAVING 或子查询；
2. SELECT 里既有聚合列又有普通列时必须写 GROUP BY（否则普通列取值不可预期）；
3. "最低/最高的 N 个"类问题：必须 GROUP BY 分组列 + ORDER BY + LIMIT N；
4. 时间窗口统一用 BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'（"最近7天"= 2026-10-08 ~ 2026-10-14，
   "当日"= 2026-10-15，"当月"= 2026-10-01 ~ 2026-10-15）。

示例：
问题：最近7天 OTD 准时交付率最低的 5 个型号
SQL：SELECT product_model, ROUND(100.0 * SUM(CASE WHEN ship_status IN ('已发货','在途') AND eta >= date('now') THEN 1 ELSE 0 END) / COUNT(*), 1) AS otd_rate FROM shipment WHERE eta BETWEEN '2026-10-08' AND '2026-10-14' GROUP BY product_model ORDER BY otd_rate LIMIT 5
"""


def build_detail_prompt(question: str) -> str:
    """构造明细探查 prompt（V1 schema + V2 表清单 + 口径纪律 + 问题）。"""
    from ...common.sql_tools import SCHEMA_PROMPT  # V1 资产复用
    return f"{SCHEMA_PROMPT}{_V2_SCHEMA}{_LINT_RULES}\n问题：{question}"
