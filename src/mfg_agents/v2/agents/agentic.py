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

from .hitl import single_tool_flow

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
    {"name": "run_scenario", "desc": "按场景 key 直接触发协同场景（复杂闭环建议入口）", "kind": "写/编排"},
    {"name": "search_knowledge", "desc": "检索知识库（财务/行政制度；工程知识随接入扩展）", "kind": "只读"},
    {"name": "query_project_milestones", "desc": "查项目/NPI 里程碑（EVT/DVT/PVT/MP 阶段与状态）", "kind": "只读"},
    {"name": "query_test_records", "desc": "查测试记录（可靠性/执行结果，引用记录编号）", "kind": "只读"},
    {"name": "draft_delivery_reply", "desc": "对客交期答复草稿（R5：ATP 现货 + 排产试算，确定性组装；放行须 hitl.eta 人工确认）", "kind": "只读/草稿"},
    {"name": "dispatch_followup", "desc": "齐套催交派发（R6：缺口清单→采购员待办，治理轨状态机；到货确认须人工操作）", "kind": "写/治理轨"},
    {"name": "trace_quality_chain", "desc": "质量追溯三路并查（R7：生产记录/来料检验/设备状态串行取证 + 同批在制工单，确定性成稿）", "kind": "只读/成稿"},
    # V4 工具层并轨：以下工具经 ToolRegistry 装配（与 MCP / A2A 单一事实源），
    # 按域附加给对应智能体（safe name：命名空间.动作 → 命名空间__动作）
    {"name": "quality__trace", "desc": "批次全链路质量追溯（不良/客诉/测试记录串联）", "kind": "只读"},
    {"name": "quality__8d", "desc": "8D 报告结构与进展查询（D1-D8 阶段留痕）", "kind": "只读"},
    {"name": "spc__capability", "desc": "SPC 过程能力（Cp/Cpk 双西格玛口径；缺规格返 data_gap）", "kind": "只读"},
    {"name": "spc__pareto", "desc": "不良柏拉图（按缺陷类型降序累积与关键少数）", "kind": "只读"},
    {"name": "asset__status", "desc": "设备状态与参数查询（稼动/告警/维保）", "kind": "只读"},
    {"name": "equipment__predict", "desc": "设备健康预测（异常风险与建议动作）", "kind": "只读"},
    {"name": "inventory__atp", "desc": "ATP 可承诺量（库存 − 已预留，按型号）", "kind": "只读"},
    {"name": "supply__risk", "desc": "供应风险评估（缺料/交期/齐套风险清单）", "kind": "只读"},
    {"name": "schedule__what_if", "desc": "排产 What-if 模拟（产能/插单可行性测算）", "kind": "只读"},
    {"name": "metric__dict", "desc": "指标字典（平台全部口径定义与负责域）", "kind": "只读"},
    {"name": "doc__extract", "desc": "文档要点提取（工艺文件/报告的结构化摘取）", "kind": "只读"},
]

# V4 工具层并轨：域 → 附加 ToolRegistry 工具（MCP 命名空间.动作）。
# 排除写类/治理工具（workflow.* 含终批动作——Agent 永不放行；spc.ingest 落库写）；
# 与既有闭包工具等价的（data.*/scenario.*/knowledge.search）不重复注入。
AGENT_EXTRA_TOOLS: dict[str, tuple[str, ...]] = {
    "quality": ("quality.trace", "quality.8d", "spc.capability",
                "spc.pareto", "spc.report", "metric.dict"),
    "manufacturing": ("asset.status", "equipment.predict", "spc.capability",
                      "spc.pareto", "metric.dict"),
    "supply_chain": ("inventory.atp", "supply.risk", "schedule.what_if",
                     "metric.dict"),
    "rd_engineering": ("quality.trace", "doc.extract", "metric.dict"),
    "test_engineer": ("quality.trace", "metric.dict"),
    "rd_test": ("quality.trace", "metric.dict"),
    "me_engineer": ("doc.extract", "metric.dict"),
    "ie_engineer": ("schedule.what_if", "asset.status", "metric.dict"),
    "npi_engineer": ("quality.trace", "metric.dict"),
    "project_manager": ("schedule.what_if", "supply.risk", "metric.dict"),
    "procurement": ("supply.risk", "inventory.atp", "metric.dict"),
    "warehouse": ("inventory.atp", "supply.risk", "metric.dict"),
    "gm": ("metric.dict", "supply.risk", "spc.pareto"),
    "finance": ("metric.dict",),
    "admin": ("metric.dict",),
    "ehs": ("metric.dict",),
    "customer_service": ("quality.trace", "metric.dict"),
}

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
        "abbr": "QA",
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
        "abbr": "MFG",
        "name": "制造运营智能体",
        "role": ("你是制造运营域智能体，服务制造经理/设备经理/现场负责人。"
                 "意图：回答停线、复线进度、OEE、产出达成、设备告警与维护类问题。"),
        "boundary": ("边界：只回答制造运营域问题；成本/财务、对客承诺不在你的职责内；"
                     "客诉数据不可查（ACL 拒绝是正常边界，不是故障）。"),
        "hitl_rule": ("HITL 准则：涉及「复线决策」「停机窗口确认」时必须发起"
                      " create_hitl_task 人工确认；纯状态查询不需要。"),
    },
    "supply_chain": {
        "abbr": "SC",
        "name": "供应链智能体",
        "role": ("你是供应链域智能体，服务供应链经理/计划/物控/业务经理。"
                 "意图：回答订单交付、齐套、缺料、ATP 可承诺量、库存与发货类问题"
                 "（采购专项与仓储专项已拆分，分别由采购智能体、仓储智能体承接）。"),
        "boundary": ("边界：只回答供应链域问题；采购/供应商专项走采购智能体、"
                     "仓储/发货专项走仓储智能体、质量判定走质量域、设备维修走制造域；"
                     "shipment 数据对业务/仓库角色可见，其他角色会被 ACL 拒绝。"),
        "hitl_rule": ("HITL 准则：涉及「对客交期承诺」「分批出货」「加急生产」"
                      "时必须发起 create_hitl_task；纯查询不需要。"),
    },
    "rd_engineering": {
        "abbr": "RD",
        "name": "研发工程智能体",
        "role": "你是研发工程域智能体，服务研发经理/硬件/软件/结构/测试/工程。意图：回答 ECN、NPI、图纸、工艺参数、根因分析类问题。",
        "boundary": "边界：只回答研发工程域问题；当前 BOM/ECN/图纸数据源未接入，遇到时诚实说明数据缺口，不得编造。",
        "hitl_rule": "HITL 准则：涉及「ECN 会签」「图纸发放」时发起 create_hitl_task。",
    },
    "finance": {
        "abbr": "FIN",
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
        "abbr": "ADM",
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
    "test_engineer": {
        "abbr": "TE",
        "name": "工程测试智能体（TE）",
        "role": ("你是工程测试域智能体，服务测试工程师/品质工程。"
                 "意图：回答测试执行、测试报告、失效分析、可靠性测试"
                 "（跌落/高低温/寿命）、测试计划与覆盖率类问题。"),
        "boundary": ("边界：只回答测试执行与可靠性域问题；软件/Bug 走研发测试；"
                     "结构设计走 ME。测试数据表已接入（test_records），"
                     "测试结论必须引用记录编号与数据。"),
        "hitl_rule": ("HITL 准则：涉及「测试放行判定」「可靠性不通过处置」"
                      "时发起 create_hitl_task；查询类不需要。"),
    },
    "me_engineer": {
        "abbr": "ME",
        "name": "结构工程智能体（ME）",
        "role": ("你是结构工程（ME）智能体，服务结构工程师。"
                 "意图：回答结构设计、模具、公差配合、装配问题、"
                 "变形/断裂/间隙、材料选型类问题。"),
        "boundary": ("边界：只回答结构工程域问题；电路/固件不在职责内。"
                     "3D 图纸/CAD 数据源未接入——遇到时诚实说明数据缺口。"),
        "hitl_rule": ("HITL 准则：涉及「结构变更」「模具修改」时发起"
                      " create_hitl_task；分析类不需要。"),
    },
    "ie_engineer": {
        "abbr": "IE",
        "name": "工业工程智能体（IE）",
        "role": ("你是工业工程（IE）智能体，服务 IE 工程师/生产管理。"
                 "意图：回答标准工时、产能测算、线平衡、效率改善、"
                 "人力配置、layout 类问题。"),
        "boundary": ("边界：只回答工业工程域问题；质量判定、财务成本不在职责内。"
                     "MES 工时明细数据源未接入时诚实说明，可用产出数据近似推算并注明口径。"),
        "hitl_rule": ("HITL 准则：涉及「产线重排」「工时标准变更」时发起"
                      " create_hitl_task；测算类不需要。"),
    },
    "npi_engineer": {
        "abbr": "NPI",
        "name": "NPI 导入智能体",
        "role": ("你是 NPI（新产品导入）智能体，服务 NPI 工程师/项目组。"
                 "意图：回答 EVT/DVT/PVT/MP 阶段、试产安排、DFM 问题、"
                 "导入进度与风险类问题。"),
        "boundary": ("边界：只回答 NPI 导入域问题；量产异常走制造域。"
                     "项目里程碑表已接入（project_milestone），阶段结论引用数据。"),
        "hitl_rule": ("HITL 准则：涉及「阶段转段判定」「试产放行」时发起"
                      " create_hitl_task；进度查询不需要。"),
    },
    "project_manager": {
        "abbr": "PM",
        "name": "项目经理智能体（PM）",
        "role": ("你是项目部智能体，服务项目经理/项目总监。"
                 "意图：回答项目里程碑、跨部门协调、风险跟踪、资源冲突、"
                 "项目健康度类问题。"),
        "boundary": ("边界：只回答项目管理域问题；单域技术深挖引导到对应域智能体。"
                     "项目里程碑表已接入，跨域风险汇总引用数据。"),
        "hitl_rule": ("HITL 准则：涉及「里程碑变更」「资源跨部门调配」"
                      "时发起 create_hitl_task；查询不需要。"),
    },
    "rd_test": {
        "abbr": "RD-T",
        "name": "研发测试智能体",
        "role": ("你是研发测试智能体，服务软件/固件测试。"
                 "意图：回答软件测试、固件验证、Bug 管理、版本回归类问题。"),
        "boundary": ("边界：只回答软件/固件测试域问题；硬件可靠性走工程测试。"
                     "Bug 管理系统数据源未接入时诚实说明。"),
        "hitl_rule": ("HITL 准则：涉及「版本放行」「Bug 遗留判定」时发起"
                      " create_hitl_task；查询不需要。"),
    },
    "gm": {
        "abbr": "GM",
        "name": "经营总览智能体",
        "role": "你是经营总览智能体，服务总经理。意图：跨域汇总——交付、质量、风险工单的整体态势与异常升级。",
        "boundary": "边界：你做跨域汇总与升级判断，不做单域深挖（那是各域智能体的职责）；只读汇总，不产生处置动作。",
        "hitl_rule": "HITL 准则：涉及「重大客户升级」「高层决策」时发起 create_hitl_task。",
    },
    # ---------- 采购 / 仓储（P1：从供应链域拆分为独立智能体） ----------
    "procurement": {
        "abbr": "PROC",
        "name": "采购智能体",
        "role": ("你是采购域智能体，服务采购经理/采购专员，承接原供应链域中的采购专项。"
                 "意图：回答供应商交期达成、供应商绩效、来料异常会签、SCAR 跟催、"
                 "询价比价与供应商索赔类问题。"),
        "boundary": ("边界：只回答采购与供应商域问题；库存/发货专项走仓储智能体，"
                     "质量判定走质量域；供应商价格字段对采购角色可见，其他角色会被 ACL 拒绝。"),
        "hitl_rule": ("HITL 准则：涉及「来料处置会签」「供应商索赔确认」时发起"
                      " create_hitl_task；纯查询不需要。"),
    },
    "warehouse": {
        "abbr": "WH",
        "name": "仓储智能体",
        "role": ("你是仓储物流域智能体，服务仓储经理/仓库主管/物控，"
                 "承接原供应链域中的仓储发货专项。"
                 "意图：回答库存准确、出入库、盘点差异、库龄、ATP 可承诺量与发货协同类问题。"),
        "boundary": ("边界：只回答仓储与发货域问题；采购/供应商专项走采购智能体；"
                     "shipment 数据对仓库/业务角色可见，其他角色会被 ACL 拒绝。"),
        "hitl_rule": ("HITL 准则：涉及「发货协同终批」「对客交期承诺」时必须发起"
                      " create_hitl_task；纯查询不需要。"),
    },
    # ---------- 安全环保 / 客户服务（P1：补齐部门智能体） ----------
    "ehs": {
        "abbr": "EHS",
        "name": "安全环保智能体（EHS）",
        "role": ("你是 EHS 安全环保域智能体，服务 EHS 工程师/安全员/现场负责人。"
                 "意图：回答安全隐患、环保危废、消防与职业健康、事故调查与整改闭环类问题。"),
        "boundary": ("边界：只回答安全环保域问题；生产排程、质量判定不在职责内。"
                     "当前隐患台账/危废联单数据源未接入时诚实说明数据缺口，"
                     "绝不编造整改状态。"),
        "hitl_rule": ("HITL 准则：涉及「停工整改」「危废转移审批」时发起"
                      " create_hitl_task；查询类不需要。"),
    },
    "customer_service": {
        "abbr": "CS",
        "name": "客户服务智能体",
        "role": ("你是客户服务/售后域智能体，服务客服专员/业务/售后团队。"
                 "意图：回答客诉受理与分派、售后与退换货、服务时效跟进、对客口径协同类问题。"),
        "boundary": ("边界：只回答客户服务与售后域问题；技术根因判定引导到质量/工程域智能体。"
                     "对客回复口径的最终放行不在你的职责内（需人工确认）。"),
        "hitl_rule": ("HITL 准则：涉及「对客回复口径」「退换货/售后承诺」时发起"
                      " create_hitl_task；纯查询不需要。"),
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
- run_scenario(scenario_key)：按场景 key 直接触发协同场景（复杂闭环建议入口）
- search_knowledge(question)：检索知识库（财务/行政制度；工程知识随接入扩展）
- query_project_milestones(...)：查项目/NPI 里程碑（EVT/DVT/PVT/MP 阶段与状态）
- query_test_records(...)：查测试记录（可靠性/执行结果，引用记录编号）
- draft_delivery_reply(model, qty, need_date)：组装对客交期答复草稿（ATP 现货 + 排产试算，数字全部来自注册查询；草稿不是承诺——对外发送前必须按返回提示建 hitl.eta 人工确认工单）
- dispatch_followup(work_order_set, limit)：齐套催交派发（缺口清单→采购员待办，落治理轨状态机；幂等键料号×供应商×工单集；到货确认必须人工操作，你不得代确认）
- trace_quality_chain(batch_no)：质量追溯三路并查（生产记录 → 来料检验 → 设备状态 + 同批在制工单，确定性成稿；对外发布前必须建 report_publish 人工确认工单）
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
  "hitl_slot": "{slots} 之一（needs_hitl=true 时必填）",
  "data_gaps": ["未能获取的数据及原因"]}}

注意：只输出一个 JSON 对象；数字必须来自工具返回；权限被拒时换思路或如实说明。"""


def _governance_slot_list() -> list[str]:
    """合法 HITL 槽位清单（提示词与工具 schema 的单一事实源）。

    2026-10-22 R1 前置修复：原来提示词与工具 schema 各写死 7 个槽，
    漏了 T15/T16 新增的 spc_action/order_review/report_publish——Agent
    建不出这三类工单（U1 插单评估要的正是 order_review）。改为从
    GOVERNANCE_SLOTS 动态生成，未来加槽位（如 spend_commit）零维护。
    """
    from ..services.workflow import GOVERNANCE_SLOTS
    return sorted(GOVERNANCE_SLOTS)


def _propose_role_names(slot: str) -> list[str]:
    """槽位 propose 角色中文名（PermissionRegistry 单一事实源）。

    2026-10-22 R1 前置修复：create_hitl_task 原硬编码 assignees=
    ["质量总监"]——所有域的工单都挂同一受理人，与槽位语义无关。改为按
    ``hitl.<slot>.propose`` 权限反查持有角色；权限目录故障时返回空列表
    （建单不阻断，受理人由人工兜底）。
    """
    from ..contracts.permissions import get_permission_registry
    try:
        reg = get_permission_registry()
        return reg.roles_with_permission_names(f"hitl.{slot}.propose")
    except Exception as exc:  # noqa: BLE001 - 权限目录故障不阻断建单
        logger.warning("[agentic] propose 角色解析失败（slot=%s）: %s", slot, exc)
        return []


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
        # V4：registry 附加工具的描述（bind_tools 时并入 function 描述）
        self._extra_desc: dict[str, str] = {}

    # ---------- LLM（懒加载；thinking 关闭——工具循环不需要深度思考） ----------

    def _get_llm(self) -> Any:
        if self._llm is None:
            from ...common.llm_factory import get_llm
            self._llm = get_llm(temperature=0.0, tier="L1", max_tokens=900,
                                timeout=45, thinking="disabled")
            self._llm = self._llm.bind_tools(self._tool_schemas())
        return self._llm

    # ---------- 工具实现（能力即工具；全部走既有服务层，权限同源） ----------

    def _slot_actions(self, slot: str) -> list[dict]:
        """按槽位取处置动作目录（单一事实源：scenarios/*.yaml 的 hitl 步骤）。

        2026-10-22 R1 前置修复：create_hitl_task 原来传 ``actions: []``，
        治理快照因「无处置动作」不初始化（``_init_governance`` 返回 {}），
        Agent 建出的工单人工选不了动作、会签链建不起来——U1 插单评估
        「Agent 发起 → 人工确认」闭环断在这里。改为按槽位从场景 YAML
        取动作清单（与 S1–S7 场景工单同口径）；无场景登记的槽位
        （如 report_publish）回落空列表，行为与修复前一致。

        场景注册表不可用（测试桩/装配失败）时返回空列表，不阻断建单。
        """
        try:
            registry = self.orchestrator.runtime.scenario_registry
            for meta in registry.list():
                for step in getattr(meta, "steps", []):
                    if str(step.get("type") or "").lower() != "hitl":
                        continue
                    node_slot = str(step.get("agent") or step.get("id") or "")
                    if node_slot.startswith("hitl_"):
                        node_slot = node_slot[len("hitl_"):]
                    if node_slot == slot:
                        return [dict(a) for a in (step.get("actions") or [])]
        except Exception as exc:  # noqa: BLE001 - 目录缺失不阻断建单（回落空动作）
            logger.warning("[agentic] 槽位动作目录解析失败（slot=%s）: %s", slot, exc)
        return []

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

        def run_scenario(scenario_key: str, query: str) -> dict:
            """Agent 判断问题属于复杂闭环时，直接触发对应协同场景。"""
            result = self.orchestrator.handle(query, user_ctx=self.user_ctx)
            d = result.to_dict()
            return {"scenario": d.get("scenario_key"), "report": (d.get("report") or "")[:300],
                    "hitl": len(d.get("hitl") or []), "run_id": d.get("run_id")}

        def search_knowledge(domain: str, kw: str) -> dict:
            """检索知识库（finance_policy/admin_policy；表级 ACL 同源生效）。"""
            registry = self.orchestrator.runtime.data_registry
            table = {"finance": "finance_policy", "admin": "admin_policy"}.get(domain)
            if not table:
                return {"note": f"知识域 {domain!r} 暂未接入", "items": []}
            rows = registry.execute_readonly_sql(
                f"SELECT policy_no, category, title, content FROM {table} "
                f"WHERE title LIKE '%'||:kw||'%' OR keywords LIKE '%'||:kw||'%' "
                f"OR content LIKE '%'||:kw||'%' LIMIT 4",
                {"kw": kw})
            return {"items": rows[:4], "row_count": len(rows)}

        def query_project_milestones(model: str) -> dict:
            registry = self.orchestrator.runtime.data_registry
            rows = registry.execute_readonly_sql(
                "SELECT * FROM project_milestone WHERE product_model = :m "
                "ORDER BY planned_end", {"m": model})
            return {"rows": rows[:6], "row_count": len(rows)}

        def query_test_records(model: str) -> dict:
            registry = self.orchestrator.runtime.data_registry
            rows = registry.execute_readonly_sql(
                "SELECT * FROM test_records WHERE product_model = :m "
                "ORDER BY test_date DESC LIMIT 5", {"m": model})
            return {"rows": rows[:5], "row_count": len(rows)}

        def draft_delivery_reply(model: str, qty: int, need_date: str) -> dict:
            """对客交期答复草稿（R5）：ATP 现货 + 排产试算 → 草稿 + 建单提示。

            数字全部来自注册查询（atp_by_model / plan_by_model），确定性组装、
            不调 LLM、不编造；草稿不是承诺——按返回的 hitl 提示调
            create_hitl_task(slot="eta") 建人工确认工单，Agent 永不直接放行
            （闸门理由：对客交期超期即违约，见 gate_reasons.yaml）。
            """
            from ..services.delivery_reply import compose_delivery_reply

            def _run(name: str, params: dict) -> list[dict]:
                qr = t2s.query_registered(name, params or {},
                                          user_ctx=self.user_ctx)
                # 查询被 ACL 拦截/缺参时 rows 为空——必须抛出，交给
                # compose_delivery_reply 记为 data_gap 并判 NO_DATA；
                # 否则空结果会被当成「零库存」继续算，静默编造交期（R5 真机实测）。
                if qr.fallback or any("审核拦截" in str(w) for w in (qr.warnings or [])):
                    raise RuntimeError(
                        f"注册查询 {name} 不可用（{'；'.join(map(str, qr.warnings or [])) or qr.fallback}）")
                return qr.rows or []

            rep = compose_delivery_reply(model=model, qty=qty,
                                         need_date=need_date, run_query=_run)
            return {
                "verdict": rep.verdict, "draft": rep.draft,
                "on_hand": rep.on_hand, "first_ship": rep.first_ship,
                "to_produce": rep.to_produce,
                "daily_capacity": round(rep.daily_capacity, 1),
                "finish_date": rep.finish_date, "slack_days": rep.slack_days,
                "data_gaps": rep.data_gaps,
                "needs_hitl": True, "hitl_slot": "eta",
                "hitl_title": f"对客交期答复确认（{model} {qty} 套）",
                "hitl_prompt": rep.draft,
                "note": "草稿已生成；对外发送必须经人工确认——请调 "
                        "create_hitl_task(slot='eta') 建单，勿擅自承诺",
            }

        def dispatch_followup(work_order_set: str, limit: int = 10) -> dict:
            """齐套催交派发（R6）：缺口清单 → 采购员待办（治理轨状态机）。

            数据全部来自注册查询（material_shortage / supplier_followup）；
            幂等键 = 料号 × 供应商 × 工单集，重复派发不会产生第二条待办。
            到货确认是人工动作（演示态带「演示回写」徽标），Agent 只派发、
            不改到货状态——这是「Agent 只推进、不放行」的同一铁律。
            """
            from ..services import procurement_followup as followup_svc

            if self.domain not in ("supply_chain", "procurement", "warehouse"):
                return {"error": f"催交派发仅限供应链/采购/仓储域（当前 {self.domain}）",
                        "needs_hitl": False}
            label = str(work_order_set or "").strip()
            if not label:
                return {"error": "缺少工单集 work_order_set（如「2026-10-26 开工批次」）",
                        "needs_hitl": False}

            def _run(name: str, params: dict) -> list[dict]:
                qr = t2s.query_registered(name, params or {},
                                          user_ctx=self.user_ctx)
                if qr.fallback or any("审核拦截" in str(w) for w in (qr.warnings or [])):
                    raise RuntimeError(
                        "；".join(map(str, qr.warnings or [])) or str(qr.fallback))
                return qr.rows or []

            try:
                plan = followup_svc.build_followup_gaps(
                    _run, limit=max(1, min(int(limit or 10), 50)))
                rows = followup_svc.dispatch_followups(
                    label, plan["gaps"],
                    actor=str(self.user_ctx.get("user_id") or ""))
            except followup_svc.FollowupDataError as exc:
                return {"error": str(exc), "needs_hitl": False}

            return {
                "work_order_set": label,
                "dispatched": len(rows),
                "total_gaps": plan["total_gaps"],
                "truncated": plan["truncated"],
                "missing_supplier": plan["missing_supplier"],
                "basis": plan["basis"],
                "followups": [{
                    "followup_id": r.get("followup_id"),
                    "part_no": r.get("part_no"), "part_name": r.get("part_name"),
                    "supplier": r.get("supplier"),
                    "shortage_qty": r.get("shortage_qty"),
                    "status": r.get("status"), "owner": r.get("owner"),
                    "sla_due_at": r.get("sla_due_at"),
                    "demo_writeback": r.get("demo_writeback"),
                } for r in rows[:10]],
                "needs_hitl": False,
                "note": ("催交待办已落治理轨状态机（订单看板可跟踪）。"
                         "到货确认必须人工操作，演示态带「演示回写」徽标；"
                         "重复派发同缺口返回既有单（幂等键：料号×供应商×工单集）。"),
            }

        def trace_quality_chain(batch_no: str) -> dict:
            """质量追溯三路并查（R7）：生产记录 → 来料检验 → 设备状态 + 同批影响面。

            串行四查（D11：不新增并行编排原语），确定性成稿，数字全部来自
            注册查询；每一路失败/无权限记 data_gap，不拿空结果当"没有异常"。
            成稿自动随本轮结果归档进报告中心；对外发布须建 report_publish 工单。
            """
            from ..services.quality_trace_report import compose_quality_trace

            if self.domain not in ("quality", "customer_service"):
                return {"error": f"质量追溯仅限质量/客服域（当前 {self.domain}）"}
            batch = str(batch_no or "").strip()
            if not batch:
                return {"error": "缺少批次号 batch_no"}

            def _run(name: str, params: dict) -> list[dict]:
                qr = t2s.query_registered(name, params or {},
                                          user_ctx=self.user_ctx)
                if qr.fallback or any("审核拦截" in str(w) for w in (qr.warnings or [])):
                    raise RuntimeError(
                        "；".join(map(str, qr.warnings or [])) or str(qr.fallback))
                return qr.rows or []

            rep = compose_quality_trace(batch, _run)
            out = rep.to_dict()
            out["needs_hitl_hint"] = {
                "slot": "report_publish",
                "title": f"追溯报告发布确认（批次 {batch}）",
                "prompt": "追溯报告草稿已生成；对外发布/发客户前须经报告审定人工确认。",
            }
            out["note"] = ("三路串行取证已完成，报告随本轮归档；对外发布必须调 "
                           "create_hitl_task(slot='report_publish') 人工确认，勿擅自外发。")
            return out

        def create_hitl_task(title: str, prompt: str, slot: str) -> dict:
            """Agent 发起 HITL（治理：只 propose，工单走完整治理链，Agent 永不放行）。"""
            # 槽位白名单校验（R1 前置）：未知槽位直接拒，并把合法清单回给
            # Agent 自纠——否则脏槽位会静默建成「未纳管」工单，绕过治理链
            if slot not in set(_governance_slot_list()):
                return {"error": f"未知治理槽位 {slot!r}（合法槽位："
                                 f"{'|'.join(_governance_slot_list())}）",
                        "task_uid": None}
            # 流程留痕：域 Agent 无 DAG 轨迹，按「意图识别 → 数据取证判定」调用链补造，
            # 供审批摘要卡渲染流程进展与各职能部门耗时（与办公/MCP 单工具工单同口径）
            flow = single_tool_flow(
                [("agentic_intent", f"{self.cfg['name']} · 意图识别", 6_000),
                 ("agentic_evidence", f"{self.cfg['name']} · 数据取证与判定", 24_000)],
                f"agentic_{slot}", prompt, seed=f"{title}|{prompt}")
            task = wf.create_task(
                wf_id=f"agentic_{slot}", title=title[:80],
                context={"slot": slot, "kind": slot,
                         "actions": self._slot_actions(slot),
                         "origin": "agentic_agent", "domain": self.domain,
                         "reason": f"{title}；现需人工确认：{prompt}",
                         "flow": flow},
                assignees=_propose_role_names(slot) or ["治理角色待定"],
                priority="P2", hitl=True,
                hitl_prompt=prompt[:300])
            return {"task_uid": task.task_uid, "status": task.status,
                    "note": "已创建人工确认工单，等待对应岗位处理"}

        return {
            "run_scenario": (run_scenario, {
                "type": "object",
                "properties": {"scenario_key": {"type": "string"},
                               "query": {"type": "string"}},
                "required": ["scenario_key", "query"]}),
            "search_knowledge": (search_knowledge, {
                "type": "object",
                "properties": {"domain": {"type": "string",
                                          "enum": ["finance", "admin"]},
                               "kw": {"type": "string"}},
                "required": ["domain", "kw"]}),
            "query_project_milestones": (query_project_milestones, {
                "type": "object",
                "properties": {"model": {"type": "string"}},
                "required": ["model"]}),
            "query_test_records": (query_test_records, {
                "type": "object",
                "properties": {"model": {"type": "string"}},
                "required": ["model"]}),
            "draft_delivery_reply": (draft_delivery_reply, {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "产品型号，如 A型号"},
                    "qty": {"type": "integer", "description": "客户需求量（套）"},
                    "need_date": {"type": "string",
                                  "description": "约定交期：ISO 日期（2026-10-31）/ 月底 / 下月底"},
                },
                "required": ["model", "qty", "need_date"]}),
            "dispatch_followup": (dispatch_followup, {
                "type": "object",
                "properties": {
                    "work_order_set": {"type": "string",
                                       "description": "工单集标签（如「2026-10-26 开工批次」或订单号列表）"},
                    "limit": {"type": "integer",
                              "description": "最多派发缺口条数（默认 10，上限 50）"},
                },
                "required": ["work_order_set"]}),
            "trace_quality_chain": (trace_quality_chain, {
                "type": "object",
                "properties": {
                    "batch_no": {"type": "string",
                                 "description": "批次号，如 BATCH-A-1008"},
                },
                "required": ["batch_no"]}),
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
                                        "enum": _governance_slot_list()}},
                "required": ["title", "prompt", "slot"]}),
        } | self._registry_tools()

    # ---------- V4 工具层并轨：从 ToolRegistry 装配域附加工具 ----------

    def _registry_tools(self) -> dict[str, tuple[Any, dict]]:
        """按域附加 MCP 同源工具（ToolRegistry 单一事实源）。

        - safe name：``quality.trace`` → ``quality__trace``（function calling
          名称约束）；执行时直接调 ``tool.handler``（等价 ``registry.call``，
          权限/脱敏/留痕同源）；
        - 排除 schema 里的 ``user_ctx`` 参数：Agent 已持有调用者身份，
          不让模型看到/填写身份字段（身份由运行时注入，不可伪造）；
        - 结果统一截断（rows ≤5、JSON ≤600 字）——与闭包工具同口径，
          防 token 爆炸；registry 装配失败静默返回空（旧工具不受影响）。
        """
        names = AGENT_EXTRA_TOOLS.get(self.domain, ())
        if not names:
            return {}
        try:
            from ..mcp_tools.tools import build_platform_tools
            by_name = {t.name: t for t in build_platform_tools(self.orchestrator)}
        except Exception as exc:  # noqa: BLE001 —— registry 不可用不拖垮基础工具
            logger.warning("[agentic] ToolRegistry 装配失败（基础工具不受影响）: %s",
                           exc)
            return {}
        tools: dict[str, tuple[Any, dict]] = {}
        for name in names:
            tool = by_name.get(name)
            if tool is None:
                continue

            def _make(tool=tool):
                def handler(**kwargs: Any) -> dict:
                    res = tool.handler(args=dict(kwargs),
                                       user_ctx=dict(self.user_ctx or {}))
                    return self._trim_result(res)
                return handler

            schema = dict(tool.input_schema or {})
            props = dict(schema.get("properties") or {})
            props.pop("user_ctx", None)
            schema["properties"] = props
            if "user_ctx" in (schema.get("required") or []):
                schema["required"] = [r for r in schema["required"]
                                      if r != "user_ctx"]
            safe = name.replace(".", "__")
            self._extra_desc[safe] = str(tool.description or "")[:200]
            tools[safe] = (_make(tool), schema)
        return tools

    @staticmethod
    def _trim_result(res: Any) -> dict:
        """工具结果统一裁剪（rows/items ≤5 行、列 ≤8；其余 JSON 截断）。"""
        if isinstance(res, dict) and isinstance(res.get("rows"), list):
            out = dict(res)
            out["rows"] = [{k: v for k, v in list(r.items())[:8]}
                           for r in res["rows"][:5] if isinstance(r, dict)]
            out["row_count"] = len(res["rows"])
            if isinstance(res.get("items"), list):
                out["items"] = res["items"][:5]
            return out
        text = json.dumps(res, ensure_ascii=False, default=str)
        return {"data": text[:600]}

    # 工具描述（给 Agent 的能力说明；与系统提示词的工具清单一致）
    _TOOL_DESCRIPTIONS = {
        "query_metric": "查平台统一口径指标（当前可用：otd_rate 准交率）",
        "list_available_queries": "列出所有可用的预注册查询（含 keywords 与参数说明）",
        "run_registered_query": "按名称执行预注册查询（参数化、走权限与脱敏）",
        "explore_data": "自然语言明细探查（内部走 SQL 生成与八道防线审核）",
        "list_scenarios": "列出平台协同场景（复杂闭环问题应建议走对应场景）",
        "create_hitl_task": "发起人工确认工单（治理白名单决策时使用；只发起不放行）",
        "run_scenario": "按场景 key 直接触发协同场景（Agent 判断属复杂闭环时使用）",
        "search_knowledge": "检索财务/行政制度知识库（更多知识域随接入扩展）",
        "query_project_milestones": "查项目/NPI 里程碑（EVT/DVT/PVT/MP）",
        "query_test_records": "查测试记录（可靠性/执行结果）",
    }

    def _tool_schemas(self) -> list[dict]:
        descs = {**self._extra_desc, **self._TOOL_DESCRIPTIONS}
        return [{"type": "function", "function": {
            "name": name, "description": descs.get(name, ""),
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

        sys_text = _SYSTEM_TEMPLATE.format(
            role=self.cfg["role"], boundary=self.cfg["boundary"],
            hitl_rule=self.cfg["hitl_rule"],
            slots="|".join(_governance_slot_list()))
        # V4：按域附加工具的运行时说明（registry 装配后动态拼入——静态模板
        # 不知道每个域拿到哪些附加工具，写死会与实际能力清单失真）
        if self._extra_desc:
            extras = "\n".join(f"- {name}：{desc}"
                               for name, desc in sorted(self._extra_desc.items()))
            sys_text += (f"\n\n## 按域附加工具（与 MCP/A2A 同源装配）\n{extras}"
                         f"\n（工具名中的 __ 对应能力命名空间 ·，如 quality__trace 即 quality.trace）")
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
