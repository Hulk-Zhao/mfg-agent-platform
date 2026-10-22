"""业务知识问答路由（R4/D5：服务端前置分流）。

根因（2026-10-22 读码核实）：``kbqa_intent._DOMAIN_WORDS`` 显式收录
「工艺/图纸/受控/变更/ECN」，业务知识问题（"B 型支架焊接参数用哪一版"）
经 ``kbqa.py::answer()`` 的意图路由必然 ``domain=True`` → 进 L2 RAG
检索 Milvus ``enterprise_faq`` **制度语料** → 无命中 → 返回「未在制度中
找到相关内容」。UI 上加双 tab 不改变服务端路由一步——分流必须做在
``answer()`` 入口之前。

词表纪律（不对称代价，沿用 kbqa_intent 的论证）：

- 漏判（业务问题进了制度链）= 制度兜底话术 = **质量事故**；
- 误判（制度问题判成业务）= 多查一次本地知识轨、落空后回落制度链
  = **只损失一次 sqlite 查询**。

因此词表宁宽勿窄；调用方（``kbqa.answer``）对误判的承诺是「先查业务轨，
落空回落」——本模块不保证命中即业务，只保证「像业务问题」。
"""
from __future__ import annotations

import re

from .kbqa_faq_match import normalize

#: 业务知识词表（CJK，子串命中即判「像业务问题」；normalize 已小写）。
#: 与 kbqa_intent._DOMAIN_WORDS 故意重叠（工艺/图纸/变更/受控）——那里收
#: 是为了保 RAG 出处通道，这里收是为了把它从制度语料里捞出来。
_BUSINESS_WORDS: frozenset[str] = frozenset({
    # 工艺与作业
    "工艺参数", "工艺卡", "作业指导", "焊接", "焊点", "焊缝", "回流焊",
    "波峰焊", "点胶", "贴片", "锡膏", "钢网", "电镀", "阳极", "炉温",
    "温度曲线", "扭矩", "拧紧", "校准", "公差", "首件",
    # 物料与版本
    "料号", "物料清单", "版本", "版次", "旧版", "新版", "现行版本",
    # 变更与受控
    "变更", "受控", "图号", "工程图", "图纸", "审图",
    # 质量参数（口径类问法，非制度类）
    "圆度", "平面度", "孔径", "规格限",
})

#: 拉丁/数字缩写（词边界匹配，大小写不敏感；与 kbqa_intent._LATIN_RE 同范式）
_BUSINESS_LATIN_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"BOM|ECN|ECM|SOP|PCB|PCBA|FPC|SMT|AOI|ICT|FCT|CPK|PPK|GR&R|MSDS"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

#: 「有实质内容」判定（与 kbqa_intent 同口径）
_CONTENT_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


def matched_word(query: str) -> str:
    """命中词（空串 = 不像业务问题）。"""
    norm = normalize(query)
    if not norm or not _CONTENT_RE.search(norm):
        return ""
    for word in sorted(_BUSINESS_WORDS, key=len, reverse=True):
        if word in norm:
            return word
    m = _BUSINESS_LATIN_RE.search(norm)
    return m.group(1) if m else ""


def is_business_query(query: str) -> bool:
    """是否「像业务知识问题」（服务端前置分流判据，纯函数 sub-ms）。"""
    return bool(matched_word(query))
