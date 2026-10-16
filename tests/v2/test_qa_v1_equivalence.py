"""QA 补测：V1 演示脚本等价覆盖缺口（test_events / test_chunk 的行为面）。

背景：tests/conftest.py 用 collect_ignore 跳过 5 个 V1 演示脚本，等价覆盖
移交 tests/test_v1_regression.py。QA 审查发现回归测试覆盖了图编译/路由/
SQL 守卫/域结论四项，但遗漏了两个演示脚本的行为面：
- test_events.py：图执行期间事件队列的 push/qsize 行为；
- test_chunk.py：app.stream(values 模式) 可迭代且 state 演进。

注意：V1 图执行依赖真实 LLM（无 degraded 模式），离线环境下
test_events/test_chunk/test_e2e 三个演示本身就不可跑——这正是
collect_ignore 处理的正当性证据。本文件用 skipif 守卫：
LLM 可用时补跑等价覆盖，离线时跳过（与演示脚本同条件）。
"""
import queue

import pytest

from mfg_agents.events import get_queue, init_queue, push
from mfg_agents.graph.builder import app
from mfg_agents.tools.llm_factory import llm_available

requires_llm = pytest.mark.skipif(
    not llm_available(), reason="V1 图执行需真实 LLM（Ollama），离线跳过")


@requires_llm
def test_v1_event_queue_push_during_graph_run():
    """等价 test_events.py：跑图期间 push 的事件进入队列。"""
    init_queue()
    q = get_queue()
    while not q.empty():
        q.get()  # 清空存量事件，保证断言只归本次运行
    final = app.invoke({"user_query": "A型号在SMT-1停线了，什么时候能复线？"})
    assert final.get("scenario") == "line_stop"
    assert final.get("customer_report")
    assert q.qsize() > 0, "图执行期间应有事件入队"


@requires_llm
def test_v1_graph_stream_values_mode():
    """等价 test_chunk.py：values 模式可迭代，state 随步骤演进。"""
    steps = list(app.stream({"user_query": "A型号在SMT-1停线了"},
                            stream_mode="values"))
    assert len(steps) >= 2, "至少入口与最终两个 state 快照"
    first, last = steps[0], steps[-1]
    assert "scenario" in first
    assert last.get("customer_report"), "最终快照应含对客报告"
    # state 演进：最终快照的字段数是超集（域结论逐步填充）
    assert set(last.keys()) >= set(first.keys())


def test_v1_event_queue_singleton_semantics():
    """init_queue 幂等 + push 入队 + get 取出（V1 events 模块契约）。"""
    init_queue()
    init_queue()  # 幂等，不炸
    q = get_queue()
    before = q.qsize()
    push({"type": "qa_probe", "detail": "x"})
    assert q.qsize() == before + 1
    ev = q.get()
    assert ev["type"] == "qa_probe"
