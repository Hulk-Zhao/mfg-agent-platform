"""CLI demo：一条命令跑通 V2 全链路（TwoLevelRouter → YamlDagExecutor）。

用法：
    uv run python -m mfg_agents.v2.demo.cli "A型号在SMT-1停线，何时复线？"
    uv run python -m mfg_agents.v2.demo.cli "..." --user-ctx '{"dept":"quality","roles":["quality"]}'

输出：路由结果 + 全链路事件流 + 最终简报 + 对客报告 + HITL 待确认
      + 工作流工单 + provenance（SQL 原文/命中行/节点耗时）+ 降级标记。

offline 说明：无 Ollama 时自动 degraded（路由规则降级 / FA 模板化 /
报告模板化），全部功能仍可跑通——这是硬性验收项。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any, Optional

from ..core.events import QueueSink
from ..core.orchestrator import create_default_orchestrator
from ..core.router import RouteResult  # noqa: F401（类型文档参考）
from ...common.llm_factory import llm_available

# 演示用户上下文（总控跨域取证角色；敏感字段 supplier 将按角色脱敏）
DEFAULT_USER_CTX = {
    "user_id": "u-manager",
    "dept": "manufacturing",
    "roles": ["manufacturing", "coordinator"],
}

_EVENT_STYLE = {
    "agent_start": ("▶", "cyan"),
    "agent_end": ("✓", "green"),
    "tool_start": ("  ·", "yellow"),
    "tool_end": ("  ·", "yellow"),
    "llm_start": ("  ✦", "magenta"),
    "fan_in": ("⇉", "blue"),
    "final": ("■", "green"),
    "error": ("✗", "red"),
}


def _print_event(event: dict) -> None:
    """事件流打印（V1 事件枚举兼容：agent_start/tool_start/llm_start/final/error）。"""
    symbol, _color = _EVENT_STYLE.get(event.get("type", ""), ("•", None))
    ts = datetime.now().strftime("%H:%M:%S")
    etype = event.get("type", "")
    if etype in ("tool_start", "tool_end"):
        head = f"{symbol} {ts}   [{event.get('tool', '')}]"
    elif etype == "llm_start":
        head = f"{symbol} {ts}   [LLM]"
    else:
        head = f"{symbol} {ts} {event.get('node', '') or event.get('label', '')}"
    detail = event.get("detail", "") or event.get("summary", "")
    elapsed = event.get("elapsed_ms")
    suffix = f" [{elapsed:.0f}ms]" if isinstance(elapsed, (int, float)) else ""
    status = event.get("status", "")
    if status == "degraded":
        suffix += " [degraded]"
    print(f"{head:<42} {detail}{suffix}")


def _section(title: str) -> None:
    print()
    print(f"---------- {title} " + "-" * max(0, 46 - len(title)))


def run(query: str, user_ctx: Optional[dict] = None) -> dict[str, Any]:
    """执行 demo 并打印全链路；返回结果 dict（供测试断言）。"""
    print("=" * 60)
    print(" V2 可接入框架 · 统一编排 Demo（mfg_agent_platform.v2）")
    print("=" * 60)
    print(f"问题：{query}")
    print(f"LLM 可用性：{'可用（完整模式）' if llm_available() else '不可用（degraded 降级模式）'}")

    orchestrator = create_default_orchestrator()
    result = orchestrator.handle(query, user_ctx or DEFAULT_USER_CTX,
                                 sink=QueueSink(callback=_print_event))

    route = result.route
    _section("路由结果（两级路由）")
    print(f"level={route.get('level')}  scenario={route.get('scenario_key')}  "
          f"domain={route.get('domain')}  confidence={route.get('confidence')}  "
          f"low_confidence={route.get('low_confidence')}")
    print(f"依据：{route.get('reason')}")
    print(f"实体：{json.dumps(route.get('entities', {}), ensure_ascii=False)}")

    _section("事件流")
    print("（上方即时输出为事件流；事件枚举与 V1 前端完全兼容）")

    _section("最终简报（内部）")
    if result.internal_brief:
        for key, value in result.internal_brief.items():
            print(f"  {key}: {value}")
    else:
        print("  （无简报：场景未匹配或提前结束）")

    _section("对客报告（脱敏）")
    print(result.report or "（无）")

    _section("HITL 待确认（不自动放行）")
    if result.hitl:
        for i, item in enumerate(result.hitl, 1):
            card = item.get("card", {})
            print(f"  {i}. [{item.get('status')}] {item.get('prompt')} "
                  f"（审批人：{'、'.join(item.get('approver_roles', []))}）")
            print(f"     工单: {item.get('task_uid')}  操作: "
                  f"{' / '.join(a.get('label', '') for a in item.get('actions', []))}")
    else:
        print("  （无待确认项）")

    _section("工作流工单（确定性流程）")
    if result.workflow_tasks:
        for ticket in result.workflow_tasks:
            t = ticket.get("ticket", {})
            sla = ticket.get("ticket", {}).get("sla_due_at", "")
            print(f"  {t.get('task_uid')} [{t.get('status')}] {t.get('title')} "
                  f"| 受理: {','.join(t.get('assignees', []))} | SLA: {sla}")
            print(f"     通知: {ticket.get('notice', {}).get('level', '')} → "
                  f"{','.join(ticket.get('notice', {}).get('targets', []))}")
    else:
        print("  （无工单）")

    _section("Provenance（可审计：SQL 原文 + 命中行 + 节点耗时）")
    for item in result.provenance:
        channel = item.get("channel")
        if channel == "node_timing":
            print(f"  [节点] {item.get('node')}: {item.get('elapsed_ms')}ms "
                  f"({item.get('status')})")
            continue
        domain = item.get("domain", "")
        sql = item.get("sql", "")
        if sql:
            print(f"  [{domain}] {channel}: {item.get('query_name') or item.get('metric_id') or ''}")
            print(f"      SQL: {sql}")
            print(f"      命中: {item.get('row_count', 0)} 行 | "
                  f"表: {item.get('tables', [])} | 脱敏: {item.get('masked', False)}")
        else:
            print(f"  [{domain}] {channel}: {item.get('query') or item.get('task_uid') or ''}")

    _section("降级标记")
    if result.degraded:
        print(f"  degraded=True（{'; '.join(result.degraded_reasons) or '存在降级节点'}）")
        print("  说明：LLM 不可用已自动降级——路由走规则、FA/报告走模板化，")
        print("        全部结论仍带 provenance，可人工复核后放行。")
    else:
        print("  degraded=False（完整模式）")
    print(f"\n总耗时: {result.elapsed_ms:.0f}ms | run_id: {result.run_id}")
    return result.to_dict()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="V2 统一编排 CLI demo")
    parser.add_argument("query", help="用户自然语言问题")
    parser.add_argument("--user-ctx", default=None,
                        help="用户上下文 JSON（dept/roles），控制数据权限与脱敏")
    args = parser.parse_args(argv)
    user_ctx = None
    if args.user_ctx:
        try:
            user_ctx = json.loads(args.user_ctx)
        except json.JSONDecodeError as exc:
            print(f"--user-ctx 不是合法 JSON: {exc}", file=sys.stderr)
            return 2
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    run(args.query, user_ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
