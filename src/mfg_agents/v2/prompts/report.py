"""报告提示词 + 禁词表 + 模板化降级函数（从 ``core/node_registry.py`` 抽取，逐字节一致）。

迁移纪律（§6.3）：字符串逐字节复制；``node_registry`` 对外符号
（``FORBIDDEN_WORDS`` 等）保持可导入（由金标准测试守护）。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "FORBIDDEN_WORDS",
    "build_report_prompt",
    "_apply_forbidden_words",
    "_fa_template",
    "_customer_report_template",
]

# V1 对客脱敏禁词表（report.py 机制保留为兜底，主通道为字段级脱敏）
FORBIDDEN_WORDS: dict[str, str] = {
    "设备故障": "产线例行校准",
    "供应商来料不良": "批次质量复核",
    "品质不良超标": "工艺参数优化",
    "停线": "计划内暂停",
    "根因": "主因分析",
    "维修": "维护校准",
}


def build_report_prompt(brief: dict[str, Any]) -> str:
    """构造对客报告生成 prompt（与迁移前 f-string 逐字节一致）。"""
    return (
        "你是制造业业务接口人。基于以下内部数据，生成给客户的正式回复（200字以内）。\n\n"
        f"【内部数据】\n{brief}\n\n"
        "【规则】\n1. 禁止出现：设备故障、供应商来料不良、品质不良超标、停线、根因、维修\n"
        "2. 统一替换为：产线例行校准、批次质量复核、工艺参数优化、计划内暂停\n"
        "3. 必须包含：当前状态、预计恢复时间（基于ETA换算）、补救措施、发货影响\n"
        "4. 语气专业诚恳，不推诿\n5. 只输出报告正文，不要标题"
    )


def _apply_forbidden_words(text: str) -> str:
    """V1 Prompt 禁词表兜底（主通道为字段级脱敏，这里是双保险）。"""
    for word, replacement in FORBIDDEN_WORDS.items():
        text = text.replace(word, replacement)
    return text


def _fa_template(defect: dict, alarms: list) -> tuple[str, int, str, list[str]]:
    """LLM 不可用时的 FA 模板化结论（规则推导，非编造；标记 degraded）。"""
    qty = float(defect.get("total_qty", 0) or 0)
    if qty >= 100:
        rating = "A"
    elif qty >= 40:
        rating = "B"
    elif qty >= 10:
        rating = "C"
    else:
        rating = "D"
    defect_type = defect.get("defect_type", "未知不良")
    if alarms:
        first = alarms[0]
        root = (f"设备参数异常（{first.get('param_name', '')}="
                f"{first.get('param_value', '')}）与{defect_type}不良相关，疑似工艺偏差")
    else:
        root = f"主导不良为{defect_type}，疑似来料/工艺因素，待 PQE 现场确认"
    eta = 2
    verif = ["复测设备参数至基准范围", "加严 IPQC 检验频次", "确认来料批次 COA"]
    return rating, eta, root, verif


def _customer_report_template(brief: dict, ctx: Any) -> str:
    """degraded 模式对客报告模板（结构化、无编造数字）。"""
    if not brief.get("停线确认"):
        return ("尊敬的客户：经核查，当前产线运行正常，无停线情况，"
                "订单交付按原计划推进，感谢关注。")
    eta = ctx.get("eta_hours", 2)
    return (f"尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核"
            f"与工艺参数优化。根据当前评估，预计约 {eta} 小时恢复，"
            f"届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。")
