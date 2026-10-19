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

# 装载层支持的对象清单（与 spec-v1.md 第 2 节一一对应；
# T16 追加 agent_replays / rag_replays：智能体轨迹与 RAG 检索的演示回放包）
OBJECTS = (
    "customers", "materials", "boms", "suppliers", "equipment", "sales_orders",
    "quotes", "work_orders", "inspections", "lots", "purchase_orders",
    "spc_series", "events", "faqs", "cases",
    "agent_replays", "rag_replays",
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


def get_replay(replay_id: str) -> dict[str, Any]:
    """按 replay_id 取回放包（T16）。

    回放包分两个文件存放（智能体轨迹 / RAG 检索），对外是同一命名空间：
    先查 ``agent_replays`` 再查 ``rag_replays``，未命中抛 ``DemoDataError``
    （端点转 404，不泄漏内部结构）。
    """
    for name in ("agent_replays", "rag_replays"):
        for package in get_object(name):
            if package.get("replay_id") == replay_id:
                return package
    raise DemoDataError(f"回放包不存在: {replay_id}")


def story_timeline(scene: str | None = None) -> list[dict[str, Any]]:
    """事件流时间轴，可按场景过滤（S1..S6）。"""
    events = sorted(load_all()["events"], key=lambda e: e["ts"])
    if scene:
        events = [e for e in events if e.get("scene") == scene]
    return events


def demo_banner() -> dict[str, Any]:
    """演示标识：所有演示 API 响应都带此字段，前端强制渲染。"""
    return {"is_demo": True, "label": "演示数据", "data_dir": str(DATA_DIR)}
