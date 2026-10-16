"""YamlDagExecutor 测试：5 种节点类型 / 并行 fan-out / 条件分支 / 超时降级。

用 stub handler 驱动，不依赖数据库与 LLM，专注编排语义。
"""
import pytest

from mfg_agents.v2.contracts.scenario import ScenarioRegistry
from mfg_agents.v2.core.shared_context import SharedContext
from mfg_agents.v2.core.yaml_dag import (
    DagRunResult,
    YamlDagExecutor,
    safe_eval_condition,
)

SCENARIOS_DIR = "src/mfg_agents/v2/scenarios"


def _stub_handlers():
    """记录调用顺序的 stub handler 集合（覆盖 line_stop_v2 全部节点）。"""
    calls: list[str] = []

    def make(name: str, domain: str, status: str = "ok"):
        def handler(task, ctx, sink):
            calls.append(name)
            sink.emit({"type": "tool_start", "tool": name, "detail": "stub"})
            return {"domain": domain, "status": status,
                    "payload": {f"{name}_done": True}, "summary": f"{name} ok"}
        return handler

    return calls, {
        "business": make("business", "orchestration"),
        "manufacturing": make("manufacturing", "manufacturing"),
        "quality": make("quality", "quality"),
        "supply_chain": make("supply_chain", "supply_chain"),
        "equipment": make("equipment", "equipment"),
        "fa": make("fa", "fa"),
        "impact_assessment": make("impact_assessment", "workflow"),
        "hitl_restart": make("hitl_restart", "hitl"),
        "hitl_eta": make("hitl_eta", "hitl"),
        "report": make("report", "report"),
    }


@pytest.fixture()
def scenario():
    registry = ScenarioRegistry(SCENARIOS_DIR)
    registry.load()
    return registry.get("line_stop_v2")


def _with_timeout(scenario, node_id: str, timeout_s: float):
    """覆写指定节点的 timeout_s（YAML 中 fa 显式声明 60s，测试需压到 0.3s）。"""
    import copy

    fast = copy.deepcopy(scenario)
    for step in fast.steps:
        if step.get("id") == node_id:
            step["timeout_s"] = timeout_s
        for branch in step.get("branches", []):
            if branch.get("id") == node_id:
                branch["timeout_s"] = timeout_s
    return fast


def test_full_dag_runs_all_nodes_in_order(scenario):
    calls, handlers = _stub_handlers()
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    executor = YamlDagExecutor(scenario, handlers, ctx)
    result: DagRunResult = executor.run("A型号在SMT-1停线，何时复线？")
    # 串行链 + 三分支并行全部执行
    assert calls[:2] == ["business", "manufacturing"]
    assert set(calls[2:5]) == {"quality", "supply_chain", "equipment"}
    assert calls[5:] == ["fa", "impact_assessment", "hitl_restart",
                         "hitl_eta", "report"]
    # 分域结果互不覆盖
    assert set(result.domains) >= {"orchestration", "manufacturing", "quality",
                                   "supply_chain", "equipment", "fa",
                                   "workflow", "hitl", "report"}
    assert result.timings["fa"] > 0
    assert result.node_status["report"] == "ok"


def test_fan_in_event_and_workflow_hitl_collection(scenario):
    _, handlers = _stub_handlers()
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    result = YamlDagExecutor(scenario, handlers, ctx).run("停线了")
    fan_in_events = [e for e in result.events if e.get("type") == "fan_in"]
    assert fan_in_events and fan_in_events[0]["domains"]
    assert len(result.workflow) == 1     # impact_assessment 工单
    assert len(result.hitl) == 2         # 两个 HITL 待确认


def test_condition_false_jumps_to_else(scenario):
    """未确认停线：fan_out 条件不满足 → 直接跳 report（等价 V1 行为）。"""
    calls, handlers = _stub_handlers()
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": False}, source="stub")
    result = YamlDagExecutor(scenario, handlers, ctx).run("没停线")
    assert calls == ["business", "manufacturing", "report"]
    assert "quality" not in result.domains
    assert result.hitl == [] and result.workflow == []


def test_degraded_branch_does_not_block_overall(scenario):
    """任一分支 degraded 不阻塞整体（停线应急硬性要求）。"""
    calls, handlers = _stub_handlers()
    handlers["equipment"] = lambda task, ctx, sink: {
        "domain": "equipment", "status": "degraded", "payload": {},
        "summary": "设备查询降级", "error": "timeout"}
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    result = YamlDagExecutor(scenario, handlers, ctx).run("停线了")
    assert "equipment" in result.degraded_nodes
    assert "report" in result.domains          # 后续节点照常执行
    assert result.node_status["equipment"] == "degraded"


def test_timeout_with_degrade_policy(scenario):
    """timeout_s 超时 + on_error=degrade → 节点降级，流程继续。"""
    import time

    calls, handlers = _stub_handlers()

    def slow(task, ctx, sink):
        time.sleep(2.0)
        return {"domain": "fa", "status": "ok", "payload": {}, "summary": "slow"}

    handlers["fa"] = slow
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    fast = _with_timeout(scenario, "fa", 0.3)
    result = YamlDagExecutor(fast, handlers, ctx).run("停线了")
    assert result.node_status["fa"] == "degraded"
    assert "report" in result.domains


def test_timeout_with_abort_policy_raises(scenario):
    """on_error=abort（默认）→ 超时直接抛出，快速失败。"""
    import time

    _, handlers = _stub_handlers()

    def slow(task, ctx, sink):
        time.sleep(2.0)
        return {"domain": "fa", "status": "ok", "payload": {}, "summary": "slow"}

    handlers["fa"] = slow
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    fast = _with_timeout(scenario, "fa", 0.3)
    # abort 需要把 YAML 的 on_error 改成 abort（默认即 abort）
    for step in fast.steps:
        if step.get("id") == "fa":
            step["on_error"] = "abort"
    with pytest.raises(TimeoutError):
        YamlDagExecutor(fast, handlers, ctx).run("停线了")


def test_missing_handler_fails_fast(scenario):
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    with pytest.raises(KeyError, match="未注册 handler"):
        YamlDagExecutor(scenario, {}, ctx).run("停线了")


def test_shared_context_merge_with_source(scenario):
    """handler 产出必须带 source 写入 SharedContext（可审计）。"""
    _, handlers = _stub_handlers()
    ctx = SharedContext()
    ctx.merge("manufacturing", {"confirmed_stop": True}, source="stub")
    YamlDagExecutor(scenario, handlers, ctx).run("停线了")
    prov = ctx.provenance("quality_done")
    assert prov and prov[0]["source"] == "agent:quality"


def test_safe_eval_condition_whitelist():
    assert safe_eval_condition("confirmed_stop", {"confirmed_stop": True}) is True
    assert safe_eval_condition("a > 1 and b == 'x'",
                               {"a": 2, "b": "x"}) is True
    assert safe_eval_condition("domains.manufacturing.status == 'ok'",
                               {"domains": {"manufacturing": {"status": "ok"}}})
    for evil in ["__import__('os').system('x')", "open('f')", "x;y"]:
        with pytest.raises((ValueError, SyntaxError)):
            safe_eval_condition(evil, {})
