"""FA（Failure Analysis）提示词（从 ``core/node_registry.py::fa`` 抽取，逐字节一致）。

仅承载 FA 分析节点的 prompt 模板；FA 的**降级模板函数** ``_fa_template``
放在 ``prompts/report.py``（与报告模板同域，避免循环依赖）。
"""
from __future__ import annotations

from typing import Any

__all__ = ["build_fa_prompt"]


def build_fa_prompt(line: str, defect_type: str, alarms: list[dict[str, Any]],
                    case_text: str) -> str:
    """构造 FA 分析 prompt（与迁移前 f-string 逐字节一致）。

    :param line: 线体
    :param defect_type: 主导不良类型（空串时代入"未知"）
    :param alarms: 设备告警列表（含 param_name/param_value）
    :param case_text: 历史相似案例文本
    """
    return (
        "你是制造业Failure Analysis专家。基于以下信息判定异常等级并预估复线时间。\n\n"
        f"【当前异常】\n- 线体：{line}\n"
        f"- 主导不良：{defect_type or '未知'}\n"
        f"- 设备异常：{[a.get('param_name', '') + '=' + a.get('param_value', '') for a in alarms]}\n\n"
        f"【历史相似案例】\n{case_text}\n\n"
        '请输出（JSON格式）：{"rating":"A/B/C/D","root_cause":"一句话",'
        '"eta_hours":2,"verification":["..."]}\n只输出JSON。'
    )
