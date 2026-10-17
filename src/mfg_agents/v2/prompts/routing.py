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


# Few-shot 例句（V3.1 提示词优化：覆盖员工口语化问法，提升 L1 分类命中率）。
# 选取原则：真实车间口语（非书面语）、每域 2 例、含代词省略与俗语。
L1_FEWSHOT: str = """示例（员工口语 → 类别）：
- "上周为什么停了那么多次线" → manufacturing
- "贴片机又报警了，啥情况" → manufacturing
- "齐套了没，还缺什么料" → supply_chain
- "A型号这周能交多少货" → supply_chain
- "客户退货那批处理得怎么样了" → quality
- "最近良率是不是不太行" → quality
- "新版图纸什么时候发下来" → rd_engineering
- "ECN变更谁在跟" → rd_engineering
- "报销单找谁批" → function
- "今天中午吃什么" → out_of_scope

"""


def build_l1_prompt(query: str) -> str:
    """构造 L1 闭集分类 prompt（V3.1：加入 few-shot 例句提升口语问法命中率）。

    变更说明：V3.1 前无 few-shot（见 test_prompts_golden 的版本注记），
    员工口语化提问分类不稳定——"齐套了没"这类省略主语的问法常被误判
    out_of_scope。few-shot 是对分类准确率影响最大、成本最低的优化。
    """
    return (
        "你是制造业协同平台的意图分类器。将用户问题分类到以下六个类别之一"
        "（闭集，禁止输出其他值）：\n"
        f"{format_groups()}\n\n"
        f"{L1_FEWSHOT}"
        f"用户问题：{query}\n"
        '只输出 JSON：{"domain":"<类别>","confidence":<0~1>}'
    )
