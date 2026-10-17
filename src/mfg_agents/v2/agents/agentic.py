"""Agentic 域组智能体（V3.2）：有边界的自主决策循环。

设计立场（主理人 2026-10-17 裁定）：
- **每个域组一个 Agentic Agent**：提示词约束其意图与边界，Agent 自主
  决定需要什么数据（调哪些工具）、该做什么、什么时候触发 HITL；
- **工具即能力**：Agent 的全部能力来自统一工具集（与 MCP 同源的
  ToolRegistry 通道），没有工具就做不了事——能力边界 = 工具边界；
- **治理铁律不放松**：Agent 身份（actor_kind=agent）可以**发起** HITL
  工单（它判断需要人工确认时创建），但**永不放行**（permissions.yaml
  启动自检禁止 Agent 持 *.approve）；每一步工具调用全部留痕可审计；
- **防卡死**：最大步数硬限制 + 工具超时 + 循环预算，超限输出
  "已尽力结论 + 数据缺口"，绝不静默挂起。

ReAct 循环：LLM(bind_tools) → tool_calls → 执行 → 观察 → … → final。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["DOMAIN_AGENT_CONFIGS", "AgenticDomainAgent", "AgenticStep"]

# Agent 可用工具元数据（帮助界面 / MCP / 前端展示共用；与 _tools() 保持同步）
AGENT_TOOLS_META: list[dict[str, str]] = [
    {"name": "query_metric", "desc": "查平台统一口径指标（当前：otd_rate 准交率）", "kind": "只读"},
    {"name": "list_available_queries", "desc": "列出全部预注册查询（含 keywords 与参数）", "kind": "只读"},
    {"name": "run_registered_query", "desc": "执行预注册查询（参数化、走表级 ACL 与脱敏）", "kind": "只读"},
    {"name": "explore_data", "desc": "自然语言明细探查（SQL 生成 → 八道防线审核 → 只读执行）", "kind": "只读"},
    {"name": "list_scenarios", "desc": "列出平台协同场景（复杂闭环问题的入口指引）", "kind": "只读"},
    {"name": "create_hitl_task", "desc": "发起人工确认工单（Agent 只发起，永不放行）", "kind": "写/HITL"},
]

MAX_STEPS = 8          # 防卡死：单次任务最大工具循环步数
TOOL_TIMEOUT_S = 45    # 单工具执行超时


@dataclass
class AgenticStep:
    """Agent 循环的一步（审计留痕：思考摘要 + 工具 + 参数 + 结果规模）。"""

    step: int
    thought: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "thought": self.thought, "tool": self.tool,
                "args": self.args, "summary": self.summary, "ok": self.ok}


# ---------- 域组 Agent 配置（提示词即边界：意图 / 职责 / 工具 / HITL 准则） ----------

DOMAIN_AGENT_CONFIGS: dict[str, dict[str, str]] = {
    "quality": {
        "name": "质量域智能体",
        "role": ("你是电子制造企业的质量域智能体，服务 SQE/PQE/QA/质量总监。"
                 "意图：回答来料质量、制程不良、客诉与 8D、SPC/CPK、良率与合格率类问题。"),
        "boundary": ("边界：只回答质量域问题；需要产出/停线背景数据时可查"
                     " production_output / line_stop_event（你的 ACL 允许）；"
                     "供应商敏感字段会按调用者岗位自动脱敏，你不得在回答里复述被脱敏的原文。"),
        "hitl_rule": ("HITL 准则：当问题涉及「处置决策」（退货/特采/报废）、"
                      "「对客回复口径」、「8D 关闭」时，你必须停下调 create_hitl_task "
                      "发起人工确认工单——这些是治理白名单决策，Agent 永不自行拍板；"
                      "纯数据查询永远不需要 HITL。"),
    },
    "manufacturing": {
        "name": "制造运营智能体",
        "role": ("你是制造运营域智能体，服务制造经理/现场负责人。"
                 "意图：回答停线、复线进度、OEE、产出达成、设备告警与维护类问题。"),
        "boundary": ("边界：只回答制造运营域问题；成本/财务、对客承诺不在你的职责内；"
                     "客诉数据不可查（ACL 拒绝是正常边界，不是故障）。"),
        "hitl_rule": ("HITL 准则：涉及「复线决策」「停机窗口确认」时必须发起"
                      " create_hitl_task 人工确认；纯状态查询不需要。"),
    },
    "supply_chain": {
        "name": "供应链智能体",
        "role": ("你是供应链域智能体，服务计划/物控/采购/仓库/业务。"
                 "意图：回答订单交付、齐套、缺料、ATP 可承诺量、库存与发货类问题。"),
        "boundary": ("边界：只回答供应链域问题；质量判定、设备维修不在职责内；"
                     "shipment 数据对业务/仓库角色可见，其他角色会被 ACL 拒绝。"),
        "hitl_rule": ("HITL 准则：涉及「对客交期承诺」「分批出货」「加急生产」"
                      "时必须发起 create_hitl_task；纯查询不需要。"),
    },
    "rd_engineering": {
        "name": "研发工程智能体",
        "role": "你是研发工程域智能体，服务硬件/软件/结构/测试/工程。意图：回答 ECN、NPI、图纸、工艺参数、根因分析类问题。",
        "boundary": "边界：只回答研发工程域问题；当前 BOM/ECN/图纸数据源未接入，遇到时诚实说明数据缺口，不得编造。",
        "hitl_rule": "HITL 准则：涉及「ECN 会签」「图纸发放」时发起 create_hitl_task。",
    },
    "finance": {
        "name": "财务智能体",
        "role": ("你是财务域智能体，服务财务经理/成本会计/管理层。"
                 "意图：回答成本构成、费用与报销、开票与应收、工单费用归集类问题。"),
        "boundary": ("边界：只回答财务域问题；质量判定、生产排程不在职责内。"
                     "当前财务数据源（ERP 财务模块）尚未接入——遇到成本/开票类"
                     "数据请求时诚实说明数据缺口与建议接入源，绝不编造数字。"),
        "hitl_rule": ("HITL 准则：涉及「费用审批」「开票确认」「成本核销」"
                      "时发起 create_hitl_task；纯查询不需要。"),
    },
    "admin": {
        "name": "行政人事智能体",
        "role": ("你是职能支撑域智能体，服务行政/HR/各岗位员工。"
                 "意图：回答行政流程（用章/会议室/访客）、培训与资质、"
                 "考勤假期类问题，以及平台使用帮助。"),
        "boundary": ("边界：只回答职能支撑与平台使用问题；生产/质量/供应链业务"
                     "数据不在职责内（引导用户换对应问题或联系相关岗位）。"
                     "行政制度库已接入（会议室/餐饮/班车/社团/活动），可查 admin_policy。"),
        "hitl_rule": ("HITL 准则：涉及「用章审批」「培训资质确认」时发起"
                      " create_hitl_task；一般咨询不需要。"),
    },
    "gm": {
        "name": "经营总览智能体",
        "role": "你是经营总览智能体，服务总经理。意图：跨域汇总——交付、质量、风险工单的整体态势与异常升级。",
        "boundary": "边界：你做跨域汇总与升级判断，不做单域深挖（那是各域智能体的职责）；只读汇总，不产生处置动作。",
        "hitl_rule": "HITL 准则：涉及「重大客户升级」「高层决策」时发起 create_hitl_task。",
    },
}

_SYSTEM_TEMPLATE = """{role}
{boundary}

## 工具（你的全部能力，没有工具就做不了事）
- query_metric(metric_id)：查平台统一口径指标（当前可用：otd_rate 准交率）
- list_available_queries()：列出所有可用的预注册查询（含各自 keywords 与参数）
- run_registered_query(query_name, params)：执行预注册查询（参数化、走权限与脱敏）
- explore_data(question)：自然语言明细探查（内部走 SQL 生成与八道防线审核，权限不足会被拒绝）
- list_scenarios()：列出平台协同场景（复杂闭环问题应建议用户走对应场景）
- create_hitl_task(title, prompt, slot)：发起人工确认工单（你只发起，绝不放行）

## 工作方式（ReAct）
1. 先判断问题类型：能一次查清的直接查；需要多步的组合多次工具调用；
2. 每次调用前想清楚"我还缺什么信息"；数据不满意可换查询/换参数再试；
3. 回答必须基于工具返回的数据——查不到就说查不到并说明数据缺口，绝不编造；
4. {hitl_rule}
5. 完成或达到边界时，输出最终 JSON（不要输出其他文字）：
{{"answer": "面向提问者的完整中文回答（含关键数字与口径）",
  "data_used": ["用到的数据来源，如 production_output(10-14) 2行"],
  "needs_hitl": true/false,
  "hitl_title": "需要人工确认的事项标题（needs_hitl=true 时必填）",
  "hitl_prompt": "给审批人的说明（needs_hitl=true 时必填）",
  "hitl_slot": "disposition|restart|eta|d8_close|complaint_reply|delivery_commit|maintenance_window 之一（needs_hitl=true 时必填）",
  "data_gaps": ["未能获取的数据及原因"]}}

注意：只输出一个 JSON 对象；数字必须来自工具返回；权限被拒时换思路或如实说明。"""


@dataclass
class AgenticDomainAgent:
    """域组 Agentic Agent：ReAct 工具循环 + HITL 自判 + 硬性防卡死。"""

    domain: str
    orchestrator: Any                      # 用于访问 text2sql / workflow / registry
    max_steps: int = MAX_STEPS

    def __post_init__(self) -> None:
        cfg = DOMAIN_AGENT_CONFIGS.get(self.domain)
        if cfg is None:
            raise ValueError(f"未知域组 Agent: {self.domain!r}"
                             f"（可选: {sorted(DOMAIN_AGENT_CONFIGS)}）")
        self.cfg = cfg
        self._llm = None

    # ---------- LLM（懒加载；thinking 关闭——工具循环不需要深度思考） ----------

    def _get_llm(self) -> Any:
        if self._llm is None:
            from langchain_openai import ChatOpenAI

            from ...config import settings
            self._llm = ChatOpenAI(
                model=settings.DS_MODEL_L1,
                base_url=settings.DS_URL, api_key=settings.DS_KEY,
                temperature=0.0, max_tokens=900, timeout=45,
                extra_body={"thinking": {"type": "disabled"}},
            ).bind_tools(self._tool_schemas())
        return self._llm

    # ---------- 工具实现（能力即工具；全部走既有服务层，权限同源） ----------

    def _tools(self) -> dict[str, Any]:
        t2s = self.orchestrator.runtime.text2sql
        wf = self.orchestrator.runtime.workflow

        def query_metric(metric_id: str) -> dict:
            qr = t2s.query_metric(metric_id, user_ctx=self.user_ctx)
            return {"channel": qr.channel, "rows": qr.rows[:5],
                    "row_count": len(qr.rows or []), "warnings": qr.warnings}

        def list_available_queries() -> dict:
            lib = t2s.queries
            return {"queries": [
                {"name": name, "keywords": spec.get("keywords", []),
                 "desc": spec.get("desc", ""), "params": spec.get("params", [])}
                for name, spec in sorted(lib.all().items())]}

        def run_registered_query(query_name: str, params: dict | None = None) -> dict:
            qr = t2s.query_registered(query_name, params or {},
                                      user_ctx=self.user_ctx)
            return {"channel": qr.channel, "rows": (qr.rows or [])[:5],
                    "row_count": len(qr.rows or []),
                    "sql": qr.sql[:200], "warnings": qr.warnings}

        def explore_data(question: str) -> dict:
            qr = t2s.query_detail(question, user_ctx=self.user_ctx)
            return {"channel": qr.channel, "rows": (qr.rows or [])[:5],
                    "row_count": len(qr.rows or []), "sql": qr.sql[:200],
                    "fallback": qr.fallback, "warnings": qr.warnings}

        def list_scenarios() -> dict:
            return {"scenarios": [
                {"key": m.key, "name": m.name, "domains": list(m.domains)}
                for m in self.orchestrator.runtime.scenario_registry.list()]}

        def create_hitl_task(title: str, prompt: str, slot: str) -> dict:
            """Agent 发起 HITL（治理：只 propose，工单走完整治理链，Agent 永不放行）。"""
            task = wf.create_task(
                wf_id=f"agentic_{slot}", title=title[:80],
                context={"slot": slot, "kind": slot, "actions": [],
                         "origin": "agentic_agent", "domain": self.domain},
                assignees=["质量总监"], priority="P2", hitl=True,
                hitl_prompt=prompt[:300])
            return {"task_uid": task.task_uid, "status": task.status,
                    "note": "已创建人工确认工单，等待对应岗位处理"}

        return {
            "query_metric": (query_metric, {
                "type": "object",
                "properties": {"metric_id": {"type": "string",
                                             "description": "指标 id，如 otd_rate"}},
                "required": ["metric_id"]}),
            "list_available_queries": (list_available_queries, {
                "type": "object", "properties": {}}),
            "run_registered_query": (run_registered_query, {
                "type": "object",
                "properties": {"query_name": {"type": "string"},
                               "params": {"type": "object",
                                          "description": "如 {\"line\":\"SMT-1\",\"model\":\"A型号\"}"}},
                "required": ["query_name"]}),
            "explore_data": (explore_data, {
                "type": "object",
                "properties": {"question": {"type": "string",
                                            "description": "自然语言数据问题"}},
                "required": ["question"]}),
            "list_scenarios": (list_scenarios, {
                "type": "object", "properties": {}}),
            "create_hitl_task": (create_hitl_task, {
                "type": "object",
                "properties": {"title": {"type": "string"},
                               "prompt": {"type": "string"},
                               "slot": {"type": "string",
                                        "enum": ["disposition", "restart", "eta",
                                                 "d8_close", "complaint_reply",
                                                 "delivery_commit",
                                                 "maintenance_window"]}},
                "required": ["title", "prompt", "slot"]}),
        }

    # 工具描述（给 Agent 的能力说明；与系统提示词的工具清单一致）
    _TOOL_DESCRIPTIONS = {
        "query_metric": "查平台统一口径指标（当前可用：otd_rate 准交率）",
        "list_available_queries": "列出所有可用的预注册查询（含 keywords 与参数说明）",
        "run_registered_query": "按名称执行预注册查询（参数化、走权限与脱敏）",
        "explore_data": "自然语言明细探查（内部走 SQL 生成与八道防线审核）",
        "list_scenarios": "列出平台协同场景（复杂闭环问题应建议走对应场景）",
        "create_hitl_task": "发起人工确认工单（治理白名单决策时使用；只发起不放行）",
    }

    def _tool_schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": name, "description": self._TOOL_DESCRIPTIONS.get(name, ""),
            "parameters": schema}}
            for name, (_, schema) in self._tools().items()]

    # ---------- 主循环 ----------

    def run(self, query: str, user_ctx: Optional[dict] = None) -> dict[str, Any]:
        """执行一次 Agentic 任务；返回 {answer, steps, hitl, data_gaps, degraded}。"""
        self.user_ctx = dict(user_ctx or {})
        t0 = time.perf_counter()
        steps: list[AgenticStep] = []
        messages: list[Any] = []
        hitl_task: Optional[dict] = None
        final: Optional[dict] = None
        llm = self._get_llm()

        sys_text = _SYSTEM_TEMPLATE.format(**self.cfg)
        from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

        messages.append(SystemMessage(content=sys_text))
        messages.append(("user", f"员工问题：{query}"))

        for step_no in range(1, self.max_steps + 1):
            resp = llm.invoke(messages)
            tool_calls = getattr(resp, "tool_calls", None) or []
            if not tool_calls:
                # 最终回答（解析 JSON；解析失败把原文当 answer——降级不崩）
                final = self._parse_final(getattr(resp, "content", "") or "")
                break
            messages.append(AIMessage(content=resp.content or "",
                                      tool_calls=resp.tool_calls))
            for tc in tool_calls:
                name = tc["name"]
                args = dict(tc.get("args") or {})
                st = AgenticStep(step=step_no, tool=name, args=args)
                if name == "create_hitl_task":
                    st.thought = "判断需要人工确认（治理白名单决策）"
                fn = self._tools().get(name)
                if fn is None:
                    st.ok, st.summary = False, f"未知工具: {name}"
                    result_payload = {"error": st.summary}
                else:
                    try:
                        result_payload = fn[0](**args)
                        st.ok = True
                        st.summary = (f"返回 {len(result_payload.get('rows', []))} 行"
                                      if isinstance(result_payload, dict)
                                      and "rows" in result_payload
                                      else json.dumps(result_payload, ensure_ascii=False)[:80])
                        if name == "create_hitl_task":
                            hitl_task = result_payload
                    except Exception as exc:  # noqa: BLE001 —— 工具失败作为观察反馈给 Agent
                        st.ok = False
                        st.summary = f"{type(exc).__name__}: {str(exc)[:80]}"
                        result_payload = {"error": str(exc)[:200]}
                steps.append(st)
                messages.append(ToolMessage(
                    content=json.dumps(result_payload, ensure_ascii=False)[:1500],
                    tool_call_id=tc["id"]))

        if final is None:
            # 防卡死兜底：步数耗尽，输出已尽力结论
            final = {"answer": "已在最大步数内完成部分取证，未能形成完整结论——"
                               "已把已获取的数据列在数据溯源中，请人工跟进或换一种问法。",
                     "data_used": [], "needs_hitl": False,
                     "data_gaps": ["达到最大工具循环步数"]}

        answer = str(final.get("answer") or "")
        steps_text = "；".join(
            f"{s.tool}({json.dumps(s.args, ensure_ascii=False)[:40]})" for s in steps)
        report = (f"{answer}\n\n（由{self.cfg['name']}自主完成：{len(steps)} 步工具调用"
                  f"{'：' + steps_text if steps_text else ''}；"
                  f"耗时 {time.perf_counter() - t0:.1f}s）")
        return {
            "answer": answer,
            "report": report,
            "steps": [s.to_dict() for s in steps],
            "hitl_task": hitl_task,
            "needs_hitl": bool(final.get("needs_hitl")) or hitl_task is not None,
            "data_gaps": list(final.get("data_gaps") or []),
            "data_used": list(final.get("data_used") or []),
            "degraded": final is not None and not steps and False,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    @staticmethod
    def _parse_final(content: str) -> dict[str, Any]:
        """解析最终 JSON；失败时把原文作为 answer（Agent 降级不崩）。"""
        import re as _re

        m = _re.search(r"\{.*\}", content or "", _re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict) and data.get("answer"):
                    return data
            except json.JSONDecodeError:
                pass
        return {"answer": (content or "").strip()[:800] or "（无有效输出）",
                "data_used": [], "needs_hitl": False, "data_gaps": []}
