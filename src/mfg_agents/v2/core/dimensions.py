"""板级筛选维度白名单（**唯一事实源**，零依赖）。

制造看板 / 品质看板 / SPC 看板的「工段 / 产品分类」两个板级筛选维度，全仓库
**只有这一份取值定义**（前端 ``MFG_STAGE_CHIPS`` / ``MFG_PCAT_OPTIONS`` 同源）。

**为什么必须单独成模块（而不是留在 ``services/dashboard.py``）**：
``services/dashboard.py`` 依赖 ``core/spc``（过程能力内核），而 ``core/spc``
的 ``normalize_measurements`` 又要按同一份白名单校验录入的板级维度。若白名单
只定义在 ``services/dashboard.py``，``core.spc`` 反向 import 它会形成
**循环依赖**（dashboard → core.spc → dashboard）。故下沉到无依赖的
``core/dimensions``，两侧各自 import。

**谁引用谁**（引用方均**再导出**、不改名，既有调用方零改动）：
- ``core/spc.py`` → ``SPC_STAGES`` / ``SPC_PRODUCT_CATEGORIES``（SPC 语义别名）；
- ``services/dashboard.py`` → ``MFG_STAGES`` / ``MFG_PRODUCT_CATEGORIES`` /
  ``PRODUCT_CATEGORIES``（看板语义原名）。

取值口径（与 ``docs/spc-dashboard-upgrade-design.md`` §2.1.3 一致）：
- ``MFG_STAGES``：工段 = SMT / PCM / PACK / 整机；
- ``MFG_PRODUCT_CATEGORIES``：产品分类 = 消费电子 / 智能电源 / 储能电源 / 车载。
"""
from __future__ import annotations

__all__ = [
    "PRODUCT_CATEGORIES",
    "MFG_STAGES",
    "MFG_PRODUCT_CATEGORIES",
]

# 产品分类维度的固定顺序（前端下拉选项顺序与后端切片顺序同源；仅保留实际出现过的）
PRODUCT_CATEGORIES: list[str] = ["消费电子", "智能电源", "储能电源", "车载"]

# 制造看板板级「工段」筛选白名单（接口按此白名单 fail-fast，非法值 → 400）
MFG_STAGES: tuple[str, ...] = ("SMT", "PCM", "PACK", "整机")

# 制造看板板级「产品分类」筛选白名单（与前端 MFG_PCAT_OPTIONS 同源）
MFG_PRODUCT_CATEGORIES: tuple[str, ...] = tuple(PRODUCT_CATEGORIES)
