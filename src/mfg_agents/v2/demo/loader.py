"""演示数据装载层（T04）。

从 data/demo_storyline/ 装载合成演示数据集，以独立只读 API 暴露给演示屏。
独立库、独立配置：本模块不连任何真实业务数据源。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = ROOT / "data" / "demo_storyline"

# 装载层支持的对象清单（与 spec-v1.md 第 2 节一一对应）
OBJECTS = (
    "customers", "materials", "boms", "suppliers", "equipment", "sales_orders",
    "quotes", "work_orders", "inspections", "lots", "purchase_orders",
    "spc_series", "events", "faqs", "cases",
)


class DemoDataError(RuntimeError):
    """演示数据缺失或损坏。"""


@lru_cache(maxsize=1)
def load_all() -> dict[str, list[dict[str, Any]]]:
    data: dict[str, list[dict[str, Any]]] = {}
    for name in OBJECTS:
        path = DATA_DIR / f"{name}.jsonl"
        if not path.exists():
            raise DemoDataError(
                f"演示数据缺失: {path}。请先运行 python tools/demo_gen/gen.py")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        data[name] = rows
    return data


def get_object(name: str) -> list[dict[str, Any]]:
    if name not in OBJECTS:
        raise DemoDataError(f"未知演示对象: {name}")
    return load_all()[name]


def get_one(name: str, key: str, value: str) -> dict[str, Any]:
    for row in get_object(name):
        if row.get(key) == value:
            return row
    raise DemoDataError(f"{name} 中不存在 {key}={value}")


def story_timeline(scene: str | None = None) -> list[dict[str, Any]]:
    """事件流时间轴，可按场景过滤（S1..S6）。"""
    events = sorted(load_all()["events"], key=lambda e: e["ts"])
    if scene:
        events = [e for e in events if e.get("scene") == scene]
    return events


def demo_banner() -> dict[str, Any]:
    """演示标识：所有演示 API 响应都带此字段，前端强制渲染。"""
    return {"is_demo": True, "label": "演示数据", "data_dir": str(DATA_DIR)}
