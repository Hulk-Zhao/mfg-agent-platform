"""Text2SQL 明细探查提示词（复用 V1 ``SCHEMA_PROMPT`` 的定位说明）。

仅收敛"明细探查"通道的 prompt 组装；schema 约束仍复用 V1 资产
``tools.sql_tools.SCHEMA_PROMPT``（零破坏，惰性导入避免循环）。
"""
from __future__ import annotations

__all__ = ["build_detail_prompt"]


def build_detail_prompt(question: str) -> str:
    """构造明细探查 prompt（与迁移前 f-string 逐字节一致）。"""
    from ...tools.sql_tools import SCHEMA_PROMPT  # V1 资产复用
    return f"{SCHEMA_PROMPT}\n\n问题：{question}"
