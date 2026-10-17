"""YamlDagExecutor：读场景 YAML → 构建 LangGraph 子图并执行（D2）。

支持 5 种节点类型：
- agent    ：调用域 Agent（handler 注册表按 key 查找）
- fan_out  ：并行多分支（LangGraph 超步并行；支持 condition 条件分支）
- fan_in   ：汇聚（等所有分支完成后聚合）
- workflow ：调用工作流引擎（确定性流程，LLM 只产建议）
- hitl     ：人工确认节点（IM 卡片，进入 waiting_hitl，绝不自动放行）

节点级韧性：timeout_s 超时 + on_error: degrade|abort；
任一分支 degraded 不阻塞整体（这是停线应急"三域并行取证"的硬性要求）。
"""
from __future__ import annotations

import ast
import contextvars
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from langgraph.graph import END, START, StateGraph

from ..contracts.scenario import ScenarioMeta
from .domain_state import DagState
from .shared_context import SharedContext

logger = logging.getLogger(__name__)

# handler 契约：接收 NodeTask 与共享上下文，返回域结果 dict
NodeHandler = Callable[[Any, SharedContext, Any], dict]


class _NullSink:
    """默认空 sink：未注入事件出口时丢弃事件（handler 可无条件调用 emit）。"""

    def emit(self, event: dict[str, Any]) -> None:
        return None


@dataclass
class NodeTask:
    """传递给域 Agent handler 的任务上下文。"""

    step_id: str
    agent: str
    params: dict[str, Any]          # YAML step 的全部字段
    entities: dict[str, Any]        # 入口节点抽取的实体
    query: str                      # 用户原始问题
    user_ctx: dict[str, Any]        # 用户上下文（dept/roles，权限核验用）


@dataclass
class NodeResult:
    """单节点执行记录（provenance：耗时 + 状态）。"""

    node_id: str
    domain: str
    status: str
    elapsed_ms: float
    error: Optional[str] = None


@dataclass
class DagRunResult:
    """一次场景 DAG 执行的结果聚合。"""

    scenario_key: str
    domains: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    hitl: list[dict[str, Any]] = field(default_factory=list)
    workflow: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    node_status: dict[str, str] = field(default_factory=dict)
    final: dict[str, Any] = field(default_factory=dict)

    @property
    def degraded_nodes(self) -> list[str]:
        return [n for n, s in self.node_status.items() if s == "degraded"]


# ---------- 条件表达式安全求值（白名单 AST） ----------

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare,
    ast.Name, ast.Attribute, ast.Constant, ast.Load,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.List, ast.Tuple,
)


class _AttrDict(dict):
    """支持属性访问的字典（让条件表达式可写 ``domains.manufacturing.status``）。"""

    def __getattr__(self, item: str) -> Any:
        try:
            value = self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc
        return _wrap_attr(value)


def _wrap_attr(value: Any) -> Any:
    """递归把嵌套 dict 包成 _AttrDict（属性链式访问）。"""
    if isinstance(value, dict):
        return _AttrDict({k: _wrap_attr(v) for k, v in value.items()})
    return value


def safe_eval_condition(expr: str, env: dict[str, Any]) -> bool:
    """在受限 AST 白名单内求值条件表达式（禁函数调用/导入/下标等）。"""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"条件表达式含不允许的语法: {ast.dump(node)}")
    wrapped = _wrap_attr(env)
    return bool(eval(compile(tree, "<dag-condition>", "eval"),
                     {"__builtins__": {}}, wrapped))


class YamlDagExecutor:
    """场景 DAG 执行器：YAML → LangGraph 子图 → 执行 → 聚合。"""

    def __init__(self, scenario: ScenarioMeta,
                 handlers: dict[str, NodeHandler],
                 ctx: Optional[SharedContext] = None,
                 sink: Optional[Any] = None,
                 default_timeout_s: float = 30.0) -> None:
        self.scenario = scenario
        self.handlers = handlers
        self.ctx = ctx if ctx is not None else SharedContext()
        self.sink = sink if sink is not None else _NullSink()
        self.default_timeout_s = default_timeout_s
        self._timings: dict[str, float] = {}
        self._status: dict[str, str] = {}
        self._lock = threading.Lock()

    # ---------- 图构建 ----------

    def build(self) -> Any:
        """按 YAML steps 构建 LangGraph 子图（运行时构图，非静态 add_node）。"""
        g = StateGraph(DagState)
        prev: list[str] = []
        for step in self.scenario.steps:
            sid = str(step["id"])
            stype = str(step.get("type", "agent"))
            if stype == "fan_out":
                branch_ids = self._add_fan_out(g, step, prev)
                prev = branch_ids
            elif stype == "fan_in":
                self._add_fan_in(g, step, prev)
                prev = [sid]
            else:
                node = self._make_typed_node(step)
                g.add_node(sid, node)
                for p in prev:
                    g.add_edge(p, sid)
                prev = [sid]
        for p in prev:
            g.add_edge(p, END)
        # START 指向首个可执行节点（首步为 fan_out 时指向其第一个分支）
        first_step = self.scenario.steps[0]
        if str(first_step.get("type")) == "fan_out":
            start_targets = [str(b["id"])
                             for b in first_step.get("branches", [])]
        else:
            start_targets = [str(first_step["id"])]
        for target in start_targets:
            g.add_edge(START, target)
        return g.compile()

    def _add_fan_out(self, g: StateGraph, step: dict, prev: list[str]) -> list[str]:
        """注册 fan_out：无 condition 时 prev→全部分支；有 condition 时条件路由。"""
        branches = step.get("branches", [])
        branch_ids: list[str] = []
        for branch in branches:
            bid = str(branch["id"])
            branch_ids.append(bid)
            g.add_node(bid, self._make_typed_node(branch))
        condition = step.get("condition")
        if condition:
            else_step = step.get("else")
            if not else_step:
                raise ValueError(f"fan_out[{step['id']}] 有条件分支但缺少 else 目标")

            def router(state: DagState, _cond: str = str(condition)) -> list[str]:
                """条件路由：满足 → 全部分支（fan-out）；否则 → else 目标节点。"""
                env = self._condition_env(state)
                try:
                    ok = safe_eval_condition(_cond, env)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("条件求值失败，按安全路径(no)处理: %s", exc)
                    ok = False
                return list(branch_ids) if ok else [str(else_step)]

            for p in prev:
                g.add_conditional_edges(p, router)
        else:
            for p in prev:
                for bid in branch_ids:
                    g.add_edge(p, bid)
        return branch_ids

    def _add_fan_in(self, g: StateGraph, step: dict, prev: list[str]) -> None:
        """注册 fan_in：等待全部分支（LangGraph 汇聚语义），输出聚合事件。"""
        sid = str(step["id"])

        def fanin_node(state: DagState) -> dict:
            domains = state.get("domains", {})
            degraded = sorted(d for d, st in domains.items()
                              if st.get("status") == "degraded")
            ev = {"type": "fan_in", "node": sid,
                  "domains": sorted(domains), "degraded": degraded,
                  "summary": f"汇聚 {len(domains)} 域结果"
                             + (f"（降级: {','.join(degraded)}）" if degraded else "")}
            self._emit(ev)
            return {"events": [ev]}

        fanin_node.__name__ = f"node_{sid}"
        g.add_node(sid, fanin_node)
        for p in prev:
            g.add_edge(p, sid)

    # ---------- 节点构造 ----------

    def _make_typed_node(self, step: dict[str, Any]) -> Callable[[DagState], dict]:
        """agent / workflow / hitl 三类节点统一包装：超时 + 降级 + 计时 + 事件。"""
        sid = str(step["id"])
        stype = str(step.get("type", "agent"))
        agent_key = str(step.get("agent") or step.get("workflow") or step.get("id"))
        timeout_s = float(step.get("timeout_s", self.default_timeout_s))
        on_error = str(step.get("on_error", "abort"))
        label = str(step.get("label") or agent_key)
        icon = str(step.get("icon") or {"agent": "🤖", "workflow": "🗂️",
                                        "hitl": "✋"}.get(stype, "🤖"))

        def node(state: DagState) -> dict:
            t0 = time.perf_counter()
            handler = self.handlers.get(agent_key)
            if handler is None:
                raise KeyError(f"节点 {sid!r} 未注册 handler: {agent_key!r}")
            self._emit({"type": "agent_start", "node": sid, "label": label, "icon": icon})
            task = NodeTask(
                step_id=sid, agent=agent_key, params=dict(step),
                entities=dict(state.get("entities") or {}),
                query=state.get("query", ""),
                user_ctx=dict(state.get("user_ctx") or {}),
            )
            out: dict[str, Any]
            try:
                # copy_context：把图线程的 contextvar（含请求事件队列）传播到
                # worker 线程，修复 BUG-5（tool 事件在 worker 线程被静默丢弃）。
                # 每个节点复制独立 Context 且仅 submit 一个任务，无并发进入冲突。
                worker_ctx = contextvars.copy_context()
                pool = ThreadPoolExecutor(max_workers=1)
                try:
                    future = pool.submit(worker_ctx.run, handler, task,
                                         self.ctx, self.sink)
                    out = future.result(timeout=timeout_s)
                finally:
                    pool.shutdown(wait=False)
            except FutureTimeout:
                if on_error != "degrade":
                    raise TimeoutError(f"节点 {sid!r} 超时 {timeout_s}s（on_error=abort）")
                out = {"domain": agent_key, "status": "degraded", "payload": {},
                       "summary": f"节点超时({timeout_s:.0f}s)，已降级继续",
                       "error": f"timeout after {timeout_s}s"}
                logger.warning("节点 %s 超时降级", sid)
            except Exception as exc:  # noqa: BLE001
                if on_error != "degrade":
                    raise
                out = {"domain": agent_key, "status": "degraded", "payload": {},
                       "summary": f"节点异常，已降级继续: {exc}",
                       "error": str(exc)}
                logger.warning("节点 %s 异常降级: %s", sid, exc)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            domain = str(out.get("domain") or agent_key)
            payload = out.get("payload") or {}
            # 写入 SharedContext（带 source 溯源；并行域安全）
            self.ctx.merge(domain, payload, source=f"agent:{agent_key}")
            with self._lock:
                self._timings[sid] = round(elapsed_ms, 1)
                self._status[sid] = str(out.get("status", "ok"))
            # 节点留痕进共享上下文：供下游 HITL 节点快照「流程进展 / 各域耗时」
            self.ctx.record_node(sid, label, domain,
                                 str(out.get("status", "ok")), elapsed_ms)
            self._emit({"type": "agent_end", "node": sid, "label": label,
                        "summary": out.get("summary", ""),
                        "status": out.get("status", "ok"),
                        "elapsed_ms": round(elapsed_ms, 1)})
            result: dict[str, Any] = {
                "domains": {domain: {
                    "domain": domain,
                    "status": str(out.get("status", "ok")),
                    "payload": payload,
                    "summary": str(out.get("summary", "")),
                    "elapsed_ms": round(elapsed_ms, 1),
                    "error": out.get("error"),
                }},
                "events": [],
            }
            if stype == "hitl":
                result["hitl"] = [payload] if payload else []
            if stype == "workflow":
                result["workflow"] = [payload] if payload else []
            return result

        node.__name__ = f"node_{sid}"
        return node

    # ---------- 执行 ----------

    def run(self, query: str, user_ctx: Optional[dict] = None,
            entities: Optional[dict] = None) -> DagRunResult:
        """执行场景 DAG，返回聚合结果（含每节点耗时 provenance）。

        entities：入口路由抽取的实体（型号/线体/紧急度），随初始状态入图，
        供各域 Agent handler 使用（见 orchestrator.handle）。
        """
        graph = self.build()
        init_state: DagState = {
            "query": query,
            "user_ctx": dict(user_ctx or {}),
            "entities": dict(entities or {}),
        }
        final_state = graph.invoke(init_state)
        return DagRunResult(
            scenario_key=self.scenario.key,
            domains=final_state.get("domains", {}),
            events=final_state.get("events", []),
            hitl=final_state.get("hitl", []),
            workflow=final_state.get("workflow", []),
            timings=dict(self._timings),
            node_status=dict(self._status),
            final=final_state,
        )

    # ---------- 工具 ----------

    def _condition_env(self, state: DagState) -> dict[str, Any]:
        """条件求值环境：实体 + SharedContext 最新值 + 域状态。"""
        env: dict[str, Any] = dict(state.get("entities") or {})
        env.update(self.ctx.snapshot())
        env["domains"] = {d: st.get("payload", {})
                          for d, st in (state.get("domains") or {}).items()}
        return env

    def _emit(self, event: dict[str, Any]) -> None:
        if self.sink is not None:
            self.sink.emit(event)
