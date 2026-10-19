"""演示实例独立配置（T04）。

演示环境独立库、独立开关；本文件只放演示专属配置，不引用任何真实企业连接串。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# 演示标识总开关：演示 UI 与演示 API 的一切界面徽标、数据对象由此驱动
DEMO_MODE: bool = os.getenv("MFG_DEMO_MODE", "1") not in ("0", "false", "False")
DEMO_LABEL: str = os.getenv("MFG_DEMO_LABEL", "演示数据")

# 演示实例独立 sqlite 库（治理/会签留痕），与生产库物理隔离
DEMO_DB_PATH: str = os.getenv("MFG_DEMO_DB", str(ROOT / "data" / "demo_storyline" / "demo_governance.db"))

# 访客角色（只读，见 spec-v1.md 第 4 节）
DEMO_GUEST_ROLES: tuple[str, ...] = ("gm-demo", "biz-demo", "rd-demo", "qa-demo", "mfg-demo")

# 外部连接串黑名单自检：演示代码出现任何真实连接特征时启动即失败
_FORBIDDEN_PREFIXES = ("mysql://production", "postgres://prod", "sap-prod", "10.0.", "192.168.")


def assert_isolated() -> None:
    """启动自检：演示配置不得指向任何真实环境（T12 隔离脚本的运行时兜底）。"""
    for value in (DEMO_DB_PATH,):
        if not value:
            continue
        lowered = value.lower()
        for bad in _FORBIDDEN_PREFIXES:
            if bad in lowered:
                raise RuntimeError(f"演示配置包含外部连接特征: {bad!r} 出现在 {value!r}")
