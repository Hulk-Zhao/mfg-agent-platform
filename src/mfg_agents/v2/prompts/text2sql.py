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
_V2_SCHEMA = """
V2 看板事实表（同样可查）：
equipment_run_time(id, record_date, line_code, product_model, run_minutes, plan_minutes)
scrap_record(id, record_date, line_code, product_model, output_qty, scrap_qty)
work_report(id, record_date, wo_no, line_code, product_model, reported_steps, total_steps)
order_fulfillment(id, order_no, customer, product_model, stage, due_date, plan_qty,
                  ship_qty, kit_total_qty, kit_short_qty, risk_level, owner,
                  shortage_note)
spc_measurement(id, entity, feature, measured_at, value, usl, lsl, source)
std_standard_audit(id, audit_date, process_stage, team, step, completeness_rate,
                   perfection_rate, compliance_rate)
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
