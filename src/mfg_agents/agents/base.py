"""Agent 基类：统一日志与调用约定。"""
from datetime import datetime
from typing import Any, Callable


class BaseAgent:
    name: str = "base"
    description: str = ""

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [Agent:{self.name}] start")
        result = self.run(state)
        print(f"[{ts}] [Agent:{self.name}] done -> keys={list(result.keys())}")
        return result


def agent_node(fn: Callable[[dict], dict], name: str) -> Callable[[dict], dict]:
    """把普通函数包装成带日志的LangGraph节点。"""
    def wrapper(state: dict) -> dict:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [Node:{name}] start")
        out = fn(state)
        print(f"[{ts}] [Node:{name}] done")
        return out
    wrapper.__name__ = name
    return wrapper
