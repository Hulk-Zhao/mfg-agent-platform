"""提示词集中管理（对齐 Itcast ``rag_qa/core/prompts.py`` 思路）。

本包把所有 prompt / 禁词表 / 模板函数集中在一处，供 router / node_registry /
text2sql 引用（字符串逐字节一致，仅"搬家"）。向后兼容：全部常量在此重导出。
"""
from __future__ import annotations

from .fa import build_fa_prompt
from .report import (
    FORBIDDEN_WORDS,
    _apply_forbidden_words,
    _customer_report_template,
    _fa_template,
    build_report_prompt,
)
from .routing import DOMAIN_GROUPS, build_l1_prompt, format_groups
from .text2sql import build_detail_prompt

__all__ = [
    "DOMAIN_GROUPS",
    "build_l1_prompt",
    "format_groups",
    "build_fa_prompt",
    "build_report_prompt",
    "FORBIDDEN_WORDS",
    "_apply_forbidden_words",
    "_fa_template",
    "_customer_report_template",
    "build_detail_prompt",
]
