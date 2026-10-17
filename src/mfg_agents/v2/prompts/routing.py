"""路由分类提示词（从 ``core/router.py`` 抽取，**逐字节一致**）。

包含 L1 闭集分类的域组定义与 prompt 模板。抽取纪律：字符串逐字节复制，
不重写、不"顺手优化"措辞（由 ``tests/v2/test_prompts_golden.py`` 守护）。
"""
from __future__ import annotations

__all__ = ["DOMAIN_GROUPS", "build_l1_prompt", "format_groups"]

# 五个域组定义（L1 prompt 的闭集；ARCHITECTURE-V2.md §2.1）
DOMAIN_GROUPS: dict[str, str] = {
    "supply_chain": "供应链域：订单/计划/物控/采购/库存/发货/交期/ATP",
    "rd_engineering": "研发工程域：BOM/ECN/NPI/图纸/工艺/测试",
    "quality": "质量域：来料检验IQC/不良分布/客诉/8D/SPC/CPK",
    "manufacturing": "制造运营域：停线/OEE/产出/设备告警/工艺参数/复线",
    "function": "职能支撑域：行政/财务/审批/开票/费用",
    "out_of_scope": "闲聊或超出平台范围",
}


def format_groups() -> str:
    """把域组渲染为 prompt 中的多行描述（``- key: desc``）。"""
    return "\n".join(f"- {k}: {v}" for k, v in DOMAIN_GROUPS.items())


def build_l1_prompt(query: str) -> str:
    """构造 L1 闭集分类 prompt（与迁移前 f-string 逐字节一致）。"""
    return (
        "你是制造业多智能体平台的意图分类器。将用户问题分类到以下六个类别之一"
        "（闭集，禁止输出其他值）：\n"
        f"{format_groups()}\n\n"
        f"用户问题：{query}\n"
        '只输出 JSON：{"domain":"<类别>","confidence":<0~1>}'
    )
