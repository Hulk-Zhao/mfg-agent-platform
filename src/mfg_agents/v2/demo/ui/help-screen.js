/**
 * 智造协同平台 · 帮助中心（独立文件，避免污染 screens.js）
 *
 * 内容纪律：本页所有技术描述必须与代码现状一致，不得照抄旧架构文档。
 *   - 智能体清单：GET /api/v2/agents/configs（单一事实源 = DOMAIN_AGENT_CONFIGS）
 *   - 工具清单：同一接口的 tools 字段
 *   - 离线时用内置兜底（FALLBACK_*），兜底必须与后端配置同构
 * ⚠ 改检索链路 / 路由 / 权限时同步改本页，否则帮助中心会再次失真。
 */
(function () {
  "use strict";

  // 18 个智能体：17 个域智能体 + 1 个综合办公助手（与后端 DOMAIN_AGENT_CONFIGS + OFFICE_AGENT_CONFIG 对齐）
  const FALLBACK_AGENTS = [
    { key: "quality", abbr: "QA", name: "质量域智能体", role: "来料质量、制程不良、客诉与 8D、SPC/CPK、良率", boundary: "只答质量域；产出/停线背景可查；敏感字段按岗位脱敏", hitl_rule: "处置决策/对客口径/8D 关闭 → 必须人工确认" },
    { key: "manufacturing", abbr: "MFG", name: "制造运营智能体", role: "停线、复线进度、OEE、产出达成、设备告警", boundary: "只答制造运营域；成本/对客承诺不在职责内", hitl_rule: "复线决策/停机窗口 → 人工确认" },
    { key: "supply_chain", abbr: "SC", name: "供应链智能体", role: "订单交付、齐套、缺料、ATP、库存与发货", boundary: "只答供应链域；质量判定/设备维修不在职责内", hitl_rule: "对客交期承诺/分批出货/加急 → 人工确认" },
    { key: "rd_engineering", abbr: "RD", name: "研发工程智能体", role: "ECN、BOM、图纸、工艺参数、根因分析", boundary: "只答研发域；BOM/图纸数据源未接入时诚实说明", hitl_rule: "ECN 会签/图纸发放 → 人工确认" },
    { key: "test_engineer", abbr: "TE", name: "工程测试智能体（TE）", role: "测试执行、测试报告、失效分析、可靠性（跌落/高低温）", boundary: "只答测试执行与可靠性域；软件 Bug 走研发测试", hitl_rule: "测试放行判定/可靠性不通过处置 → 人工确认" },
    { key: "rd_test", abbr: "RD-T", name: "研发测试智能体", role: "软件测试、固件验证、Bug 管理、版本回归", boundary: "只答软件/固件测试域；硬件可靠性走工程测试", hitl_rule: "版本放行/Bug 遗留判定 → 人工确认" },
    { key: "me_engineer", abbr: "ME", name: "结构工程智能体（ME）", role: "结构设计、模具、公差配合、装配问题、变形/断裂", boundary: "只答结构工程域；3D/CAD 数据源未接入时诚实说明", hitl_rule: "结构变更/模具修改 → 人工确认" },
    { key: "ie_engineer", abbr: "IE", name: "工业工程智能体（IE）", role: "标准工时、产能测算、线平衡、效率改善、人力配置", boundary: "只答工业工程域；MES 工时明细未接入时注明口径", hitl_rule: "产线重排/工时标准变更 → 人工确认" },
    { key: "npi_engineer", abbr: "NPI", name: "NPI 导入智能体", role: "EVT/DVT/PVT/MP 阶段、试产安排、DFM、导入进度", boundary: "只答 NPI 导入域；里程碑表已接入", hitl_rule: "阶段转段判定/试产放行 → 人工确认" },
    { key: "project_manager", abbr: "PM", name: "项目经理智能体（PM）", role: "项目里程碑、跨部门协调、风险跟踪、项目健康度", boundary: "只答项目管理域；单域技术深挖引导到对应域智能体", hitl_rule: "里程碑变更/资源跨部门调配 → 人工确认" },
    { key: "finance", abbr: "FIN", name: "财务智能体", role: "成本构成、费用报销、开票与应收、费用归集", boundary: "只答财务域；制度库已接入；ERP 数据源未接入时诚实说明", hitl_rule: "费用审批/开票确认/成本核销 → 人工确认" },
    { key: "admin", abbr: "ADM", name: "行政人事智能体", role: "行政流程、培训资质、考勤假期、平台使用帮助", boundary: "只答职能支撑与平台使用；行政制度库已接入", hitl_rule: "用章审批/培训资质确认 → 人工确认" },
    { key: "gm", abbr: "GM", name: "经营总览智能体", role: "跨域汇总：交付、质量、风险工单的整体态势与异常升级", boundary: "只做跨域汇总与升级判断，不做单域深挖；只读汇总，不产生处置动作", hitl_rule: "重大客户升级/高层决策 → 人工确认" },
    { key: "procurement", abbr: "PROC", name: "采购智能体", role: "供应商交期达成、供应商绩效、来料异常会签、SCAR 跟催、询价比价与索赔", boundary: "只答采购与供应商域；库存/发货走仓储，质量判定走质量域；供应商价格字段仅采购角色可见", hitl_rule: "来料处置会签/供应商索赔确认 → 人工确认" },
    { key: "warehouse", abbr: "WH", name: "仓储智能体", role: "库存准确、出入库、盘点差异、库龄、ATP 可承诺量与发货协同", boundary: "只答仓储与发货域；采购专项走采购智能体；shipment 数据仅仓库/业务角色可见", hitl_rule: "发货协同终批/对客交期承诺 → 人工确认" },
    { key: "ehs", abbr: "EHS", name: "安全环保智能体（EHS）", role: "安全隐患、环保危废、消防与职业健康、事故调查与整改闭环", boundary: "只答安全环保域；隐患台账/危废联单未接入时诚实说明，绝不编造整改状态", hitl_rule: "停工整改/危废转移审批 → 人工确认" },
    { key: "customer_service", abbr: "CS", name: "客户服务智能体", role: "客诉受理与分派、售后与退换货、服务时效跟进、对客口径协同", boundary: "只答客服与售后域；技术根因引导到质量/工程域；对客回复口径最终放行不在职责内", hitl_rule: "对客回复口径/退换货承诺 → 人工确认" },
    { key: "office", abbr: "OA", name: "企业综合助手智能体", role: "办公事务（会议室预订、订餐、查我的预订与订单、报销/请假/补卡自助）+ 制造数据只读速查（材料库存、成品库存与可发量、来料批次与检验结论、库龄呆滞、缺料缺口、批次追溯）", boundary: "办公写操作只能本人名下办理，时段冲突不得静默覆盖，报销/请假/补卡关键字段缺失先反问补齐；制造数据一律只读，受岗位数据权限约束，不越权、不承诺出入库/调拨/盘点", hitl_rule: "报告厅/培训室等需授权资源、商务客餐、≥500 元订单、报销/请假/补卡申请 → 必须人工确认；数据查询为只读，不触发 HITL" },
  ];
  const FALLBACK_TOOLS = [
    { name: "query_metric", desc: "查平台统一口径指标（当前：otd_rate 准交率）", kind: "只读" },
    { name: "list_available_queries", desc: "列出全部预注册查询（含 keywords 与参数）", kind: "只读" },
    { name: "run_registered_query", desc: "执行预注册查询（参数化、走表级 ACL 与脱敏）", kind: "只读" },
    { name: "explore_data", desc: "自然语言明细探查（SQL 生成 → 八道防线 + 语义 lint → 只读执行）", kind: "只读" },
    { name: "list_scenarios", desc: "列出平台协同场景（复杂闭环入口指引）", kind: "只读" },
    { name: "create_hitl_task", desc: "发起人工确认工单（Agent 只发起，永不放行）", kind: "写/HITL" },
    { name: "search_knowledge", desc: "检索知识库（财务/行政制度；工程知识随接入扩展）", kind: "只读" },
    { name: "query_project_milestones", desc: "查项目/NPI 里程碑（EVT/DVT/PVT/MP 阶段与状态）", kind: "只读" },
    { name: "query_test_records", desc: "查测试记录（可靠性/执行结果，引用记录编号）", kind: "只读" },
    // V4 工具层并轨：域智能体附加工具（经 ToolRegistry 装配，与 MCP / A2A 同源）
    { name: "quality__trace", desc: "批次全链路质量追溯（不良/客诉/测试记录串联）", kind: "只读" },
    { name: "quality__8d", desc: "8D 报告结构与进展查询（D1-D8 阶段留痕）", kind: "只读" },
    { name: "spc__capability", desc: "SPC 过程能力（Cp/Cpk 双西格玛口径；缺规格返 data_gap）", kind: "只读" },
    { name: "spc__pareto", desc: "不良柏拉图（按缺陷类型降序累积与关键少数）", kind: "只读" },
    { name: "asset__status", desc: "设备状态与参数查询（稼动/告警/维保）", kind: "只读" },
    { name: "equipment__predict", desc: "设备健康预测（异常风险与建议动作）", kind: "只读" },
    { name: "inventory__atp", desc: "ATP 可承诺量（库存 − 已预留，按型号）", kind: "只读" },
    { name: "supply__risk", desc: "供应风险评估（缺料/交期/齐套风险清单）", kind: "只读" },
    { name: "schedule__what_if", desc: "排产 What-if 模拟（产能/插单可行性测算）", kind: "只读" },
    { name: "metric__dict", desc: "指标字典（平台全部口径定义与负责域）", kind: "只读" },
    { name: "doc__extract", desc: "文档要点提取（工艺文件/报告的结构化摘取）", kind: "只读" },
    { name: "list_rooms", desc: "按容量/设备筛选会议室（含位置与是否需授权）", kind: "只读" },
    { name: "find_available_rooms", desc: "查某时段可用会议室（含冲突规避）", kind: "只读" },
    { name: "list_my_bookings", desc: "查我的会议室预订", kind: "只读" },
    { name: "list_restaurants", desc: "列出园区餐厅（食堂/商务餐厅）", kind: "只读" },
    { name: "get_menu", desc: "查某餐厅菜单与价格", kind: "只读" },
    { name: "list_my_orders", desc: "查我的订餐订单", kind: "只读" },
    { name: "book_room", desc: "预订会议室（自动查冲突，返回预订号）", kind: "写/留痕" },
    { name: "cancel_booking", desc: "取消本人会议室预订", kind: "写/留痕" },
    { name: "place_order", desc: "下单订餐（按菜单计价，返回订单号与金额）", kind: "写/留痕" },
    { name: "cancel_order", desc: "取消本人订餐订单", kind: "写/留痕" },
    { name: "request_approval", desc: "发起人工确认（高影响事项：报告厅/客餐/超预算）", kind: "写/HITL" },
  ];

  function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  /** 首屏只展示意图首句（"你是…，服务…" 属身份描述，重复无信息量），超长截断。 */
  function trimIntent(role) {
    const t = String(role).replace(/\s+/g, " ").trim();
    const m = t.match(/意图[：:]\s*([^]*)$/);
    const intent = (m ? m[1] : t).trim();
    const first = intent.split(/(?<=。)/)[0] || intent;
    return first.length > 78 ? first.slice(0, 78) + "…" : first;
  }

  /** 「技术 / 目的」双标签正文块——帮助中心的统一表达纪律：既说清用了什么，也说清为什么。 */
  function techGoal(tech, goal) {
    return `<div class="help-line"><span class="help-tag help-tag-tech">技术</span>${tech}</div>`
      + `<div class="help-line"><span class="help-tag help-tag-goal">目的</span>${goal}</div>`;
  }

  // ---------------------------------------------------------------- 架构图

  /**
   * 与代码现状对齐的分层图（v2/core/router.py · orchestrator.py · yaml_dag.py ·
   * mcp_tools/registry.py · common/rag_tools.py · services/text2sql/ · services/kbqa.py）。
   * 层间距 40px 留出箭头标签空间；多行正文左对齐，标题居中。
   */
  function architectureSvg() {
    const box = (x, y, w, h, fill, stroke, title, o) => {
      o = o || {};
      const tc = o.titleColor || "#1F3B57";
      const lc = o.lineColor || "#42566B";
      const fs = o.fontSize || 11;
      /* o.cls / o.titleCls / o.lineCls：主题自适应钩子（类选择器优先级高于
         SVG presentation attribute，app.css 据此在亮色主题下翻转强块配色） */
      const cls = o.cls ? ` class="${o.cls}"` : "";
      const tCls = o.titleCls ? ` class="${o.titleCls}"` : "";
      const lCls = o.lineCls ? ` class="${o.lineCls}"` : "";
      let s = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${fill}" stroke="${stroke}" stroke-width="1.4"${cls}/>`;
      s += `<text x="${x + w / 2}" y="${y + 25}" text-anchor="middle" font-size="13" font-weight="700" fill="${tc}"${tCls}>${esc(title)}</text>`;
      (o.lines || []).forEach((t, i) => {
        s += `<text x="${x + 16}" y="${y + 45 + i * 16}" font-size="${fs}" fill="${lc}"${lCls}>${esc(t)}</text>`;
      });
      return s;
    };
    const arrow = (x1, y1, x2, y2, label) => {
      let s = `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#5B7E9E" stroke-width="2" marker-end="url(#arrowHelp)"/>`;
      if (label) {
        const w = Math.max(88, label.length * 11.5 + 20);
        s += `<rect x="${(x1 + x2) / 2 - w / 2}" y="${(y1 + y2) / 2 - 17}" width="${w}" height="20" rx="5" fill="#EFF5FB" stroke="#CFE0EF" stroke-width="0.8"/>`
          + `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 2.5}" text-anchor="middle" font-size="10.5" font-weight="600" fill="#3D5A75">${esc(label)}</text>`;
      }
      return s;
    };
    const note = (x, y, t, color) =>
      `<text x="${x}" y="${y}" font-size="10.5" fill="${color || "#6B7C8D"}">${esc(t)}</text>`;

    return `<svg class="help-arch" viewBox="0 0 880 1036" style="width:100%;max-width:900px;display:block;margin:0 auto" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arrowHelp" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#5B7E9E"/></marker></defs>

      ${box(24, 8, 256, 56, "#185FA5", "#124A85", "员工门户 · Web", { cls: "arch-portal", titleCls: "arch-portal-title", lineCls: "arch-portal-line", lines: ["制造问数 · 协同场景 · 办公自助 · ask_ctx"], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)" })}
      ${box(312, 8, 256, 56, "#185FA5", "#124A85", "外部智能体 · A2A", { cls: "arch-portal", titleCls: "arch-portal-title", lineCls: "arch-portal-line", lines: ["message/send · tasks/get"], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)" })}
      ${box(600, 8, 256, 56, "#185FA5", "#124A85", "MCP 客户端", { cls: "arch-portal", titleCls: "arch-portal-title", lineCls: "arch-portal-line", lines: ["JSON-RPC 2.0 over stdio"], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)" })}
      ${note(24, 78, "旁路端点（不进总路由）：/kbqa · /cases/* · /office/chat")}
      ${arrow(440, 64, 440, 104, "提问 + ask_ctx")}

      ${box(24, 106, 832, 146, "#E8F1FA", "#6E9CC7", "两级路由 TwoLevelRouter —— 按代价分派，简单问题不跑重型链路", {
      lines: [
        "⓪ 结构化上下文 ask_ctx 命中看板 KPI  → 看板口径直查（board，毫秒级）",
        "① 制造数据速查词命中            → 数据直答（qa）",
        "② 场景触发词 / V1 规则命中       → 协同场景（scenario）",
        "③ 办公关键词命中                → 办公助手（office）",
        "④ 开放式态势汇总（无实体的「最需关注 / 整体情况 / 有什么风险」）→ 汇总问数（digest）",
        "⑤ L1 LLM 闭集分类 → 域组 / 闲聊；⑥ 全未命中 → 关键词模糊降级（degrade）",
      ]
    })}

      ${arrow(122, 252, 122, 292, "① 协同场景")}
      ${arrow(334, 252, 334, 292, "② 数据直答")}
      ${arrow(546, 252, 546, 292, "③ 办公事务")}
      ${arrow(758, 252, 758, 292, "④ 闲聊/转人工")}

      ${box(24, 294, 196, 120, "#FDF6E7", "#C9A94E", "协同场景 DAG", {
      lines: ["7 个 YAML 声明式场景", "运行时编译为 LangGraph", "5 类节点 agent/fan-out", "fan_in/workflow/hitl", "产出：工单 + 报告"], titleColor: "#6B5000", lineColor: "#7A5B00"
    })}
      ${box(236, 294, 196, 120, "#EAF6EF", "#6FAE88", "数据直答", {
      lines: ["⓪ 看板口径直查（同源函数）", "→ 指标库 → 注册查询", "→ 明细探查（四通道递进）", "八道防线 + 语义 lint", "秒级 · 无工单"], titleColor: "#155E35", lineColor: "#1F6B41"
    })}
      ${box(448, 294, 196, 120, "#F0EBFA", "#9D82C9", "办公助手", {
      lines: ["会议室 / 订餐 / 报销", "请假 / 补考勤", "独立 ReAct Agentic", "写操作限本人名下", "高影响 → 人工确认"], titleColor: "#43326E", lineColor: "#4E3D7A"
    })}
      ${box(660, 294, 196, 120, "#F4F6F8", "#9FB0C0", "闲聊 / 转人工", {
      lines: ["轻回应 + 引导回领域", "域内查不到 → 转人工", "不臆造结论", "", ""], titleColor: "#3A4A5A", lineColor: "#55646F"
    })}

      ${arrow(440, 414, 440, 452, "直答查不到 → 跨域协同 / 域智能体")}

      ${box(24, 454, 832, 62, "#E5F3FD", "#5E9BD6", "协同规划器 Planner —— 跨域问题拆 ≤4 个单域子任务，多域智能体并行取证，汇总器分节聚合", {
      lines: ["冲突不仲裁（不同口径如实并列）· 缺口合并标注 · 子任务间不互调（浅层星型，防递归失控）"],
      titleColor: "#0F4C81", lineColor: "#2C6186"
    })}

      ${arrow(440, 516, 440, 554, "子任务分发")}

      ${box(24, 556, 832, 84, "#EAF6EF", "#6FAE88", "Agentic 域智能体 · 17 个域 + 1 个综合办公助手", {
      lines: [
        "ReAct 工具循环（bind_tools → 调用 → 结果回灌），最多 8 步、单步超时 45s",
        "步数耗尽输出「已尽力结论 + 数据缺口」，不编造；提示词即边界：意图 / 职责 / 工具 / HITL 准则",
      ], titleColor: "#155E35", lineColor: "#1F6B41"
    })}

      ${arrow(440, 640, 440, 678, "工具调用")}

      ${box(24, 680, 832, 68, "#fff", "#A9BFD3", "统一工具层 ToolRegistry · tier 0/1/2 治理分级", {
      cls: "arch-toolbox", titleCls: "arch-toolbox-title", lineCls: "arch-toolbox-line",
      lines: ["HTTP / MCP / A2A / 域智能体四端同源装配 · 每个工具必带 user_ctx · 一次注册，多处暴露，能力边界 = 工具边界"]
    })}

      ${arrow(440, 748, 440, 786, "只读访问 · 权限同源")}

      ${box(24, 788, 408, 120, "#FFF9EC", "#D9B45B", "知识层 · 检索", {
      lines: [
        "主 RAG：Milvus + BGE-M3 混合召回 + Reranker 重排",
        "案例库：memory / chroma / milvus 三实现可插拔",
        "制度 FAQ：kbqa_faq 表双闸直答，命中即返回",
        "目的：能确定性答的不交给模型，交给模型的必可溯源",
      ], titleColor: "#6B5000", lineColor: "#7A5B00", fontSize: 10.5
    })}
      ${box(448, 788, 408, 120, "#F0F5FA", "#8FAFC9", "数据层 · 只读", {
      lines: [
        "DataSource 契约：演示 SQLite ⇄ 真实 ERP/MES/QMS",
        "表级 ACL + 看板 data_scope 裁决 + 字段按岗位脱敏",
        "只读执行 row_cap=200 · 结果校验 · provenance 溯源",
        "目的：宁可转人工，也不放宽校验返回错误数字",
      ], titleColor: "#1F3B57", lineColor: "#42566B", fontSize: 10.5
    })}

      ${box(24, 936, 832, 84, "#1F3B57", "#123049", "治理底盘 · 贯穿全流程", {
      cls: "arch-base", titleCls: "arch-base-title", lineCls: "arch-base-line",
      lines: [
        "HITL 8 槽位 · 工作流状态机 / SLA / 会签 · 三套权限分域（业务数据 / 治理待办 / 案例访问）· 审计留痕",
        "Agent 只发起不放行 · 协同取证只读不处置 · 关键决策人工在环 · 私有化部署，数据不出厂",
      ], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)"
    })}
    </svg>`;
  }

  // ---------------------------------------------------------------- 数据加载

  async function loadAgents(ctx) {
    const grid = document.getElementById("help-agents-grid");
    const toolsBox = document.getElementById("help-tools-body");
    if (!grid) return;
    let agents = FALLBACK_AGENTS;
    let tools = FALLBACK_TOOLS;
    let source = "内置配置（后端离线兜底）";
    try {
      if (ctx.api.online()) {
        const resp = await ctx.api._get("/api/v2/agents/configs");
        // `api._get` 已解信封：resp 即 data 载荷本身（不能再取一层 .data）
        if (resp && Array.isArray(resp.agents) && resp.agents.length) {
          agents = resp.agents;
          tools = resp.tools || tools;
          source = "实时同步自后端（单一事实源）";
        }
      }
    } catch (e) { /* 离线兜底 */ }
    grid.innerHTML = agents.map((a) => {
      const abbr = a.abbr || (a.name || a.key).slice(0, 1);
      return `
      <div class="card agent-card">
        <div class="agent-card-head">
          <span class="agent-badge">${esc(abbr)}</span>
          <div><div class="agent-name">${esc(a.name || a.key)}</div>
          <div class="agent-key">${esc(abbr)} Agent · key: ${esc(a.key)}</div></div>
        </div>
        <div class="agent-sec"><b>意图</b>${esc(trimIntent(a.role || ""))}</div>
        <details class="agent-more">
          <summary>边界与 HITL 准则</summary>
          <div class="agent-more-body">
            <div class="agent-sec"><b>边界</b>${esc(a.boundary || "")}</div>
            <div class="agent-sec hitl"><b>HITL</b>${esc(a.hitl_rule || "")}</div>
          </div>
        </details>
      </div>`;
    }).join("");
    if (toolsBox) {
      toolsBox.innerHTML = tools.map((t) => `<div class="brief-item tool-row"><span class="k">${esc(t.name)}</span><span class="v">${esc(t.desc)}（${esc(t.kind)}）</span></div>`).join("");
    }
    const cnt = document.getElementById("help-agents-count");
    if (cnt) cnt.textContent = agents.length + " 个";
    const src = document.getElementById("help-agents-source");
    if (src) src.textContent = source;
  }

  // ---------------------------------------------------------------- 数据口径词典（R8）

  let _dictEntries = [];
  let _dictSource = "";

  const DICT_BADGE = {
    registered_query: "badge-blue",
    metric_library: "badge-green",
    dashboard_kpi: "badge-amber",
  };

  function dictRowHtml(entry) {
    const tables = (entry.tables || []).join("、");
    const refs = (entry.refs || []).join(" · ");
    return `<div class="help-dict-row">
      <div class="help-dict-head">
        <b>${esc(entry.name)}</b>
        <span class="badge ${DICT_BADGE[entry.source] || "badge-gray"}">${esc(entry.source_label)}</span>
        <code>${esc(entry.id)}</code>
      </div>
      <div class="help-dict-def">${esc(entry.definition || "")}</div>
      <div class="help-dict-meta">
        ${entry.owner ? `<span>归属：${esc(entry.owner)}</span>` : ""}
        ${tables ? `<span>表：${esc(tables)}</span>` : ""}
        ${entry.unit ? `<span>单位：${esc(entry.unit)}</span>` : ""}
        ${entry.target != null ? `<span>目标：${esc(entry.target)}${esc(entry.unit || "")}</span>` : ""}
      </div>
      <div class="help-dict-refs">引用方：${esc(refs || "—")}</div>
      <button class="link-btn help-dict-open" type="button"
              data-dict-id="${esc(entry.id)}">查看完整口径</button>
    </div>`;
  }

  function renderDictEntries() {
    const box = document.getElementById("help-dict-body");
    if (!box) return;
    const input = document.getElementById("help-dict-q");
    const q = ((input && input.value) || "").trim().toLowerCase();
    const hit = _dictEntries.filter((entry) => {
      if (_dictSource && entry.source !== _dictSource) return false;
      if (!q) return true;
      const hay = [entry.id, entry.name, entry.definition, entry.owner,
        (entry.keywords || []).join(" "), (entry.tables || []).join(" "),
        (entry.refs || []).join(" ")].join(" ").toLowerCase();
      return hay.indexOf(q) !== -1;
    });
    box.innerHTML = hit.length
      ? hit.map(dictRowHtml).join("")
      : '<div class="empty">未找到匹配口径（不缩小范围硬凑结果）</div>';
  }

  async function loadMetricDict(ctx) {
    const box = document.getElementById("help-dict-body");
    if (!box) return;
    let entries = [];
    let coverage = null;
    let source = "内置配置（后端离线兜底）";
    let failure = "";
    const online = !!(ctx.api && ctx.api.online());
    try {
      if (online) {
        const resp = await ctx.api._get("/api/v2/metric-dict");
        // `api._get` 已解信封：resp 即 data 载荷本身
        if (resp && Array.isArray(resp.entries)) {
          entries = resp.entries;
          coverage = resp.coverage;
          source = "实时同步自后端 · 三源汇总";
        }
      }
    } catch (e) {
      failure = String((e && e.message) || e || "");
    }
    const src = document.getElementById("help-dict-source");
    if (!entries.length) {
      // 区分「后端没连上」与「后端在线但接口拒绝」——后者多半是登录态失效，
      // 不能笼统写成离线（真机排障时这句话误导过）。
      box.innerHTML = online
        ? '<div class="empty">口径接口不可用（登录态可能已失效，请重新登录后重试）'
          + (failure ? '：' + esc(failure) : "") + '</div>'
        : '<div class="empty">口径词典需要后端在线：前端不内嵌第二份口径，避免制造新的双源。</div>';
      if (src) src.textContent = source;
      return;
    }
    _dictEntries = entries;
    if (src) {
      const by = (coverage && coverage.by_source) || {};
      src.textContent = `${entries.length} 条 · 注册查询 ${by.registered_query || 0}`
        + ` / 指标库 ${by.metric_library || 0} / 看板 KPI ${by.dashboard_kpi || 0}`;
    }
    renderDictEntries();
  }

  // 来源过滤 / 搜索用**事件委托**：帮助中心重渲染会替换按钮节点，
  // 逐节点绑定会绑到已脱离 DOM 的旧节点上（真机表现为「点了没反应」）。
  document.addEventListener("click", function (ev) {
    const chip = ev.target && ev.target.closest
      && ev.target.closest("#help-dict-filters [data-dict-filter]");
    if (!chip) return;
    _dictSource = chip.getAttribute("data-dict-filter") || "";
    document.querySelectorAll("#help-dict-filters [data-dict-filter]")
      .forEach((c) => c.classList.toggle("is-active", c === chip));
    renderDictEntries();
  });

  document.addEventListener("input", function (ev) {
    if (ev.target && ev.target.id === "help-dict-q") renderDictEntries();
  });

  // ---------------------------------------------------------------- 治理槽位矩阵（R3）

  /** 「动钱」缺口说明（不美化）：本期无专属槽位，interim 口径 + 正式方案如实写清。 */
  function gateGapNoteHtml() {
    return `<b>已知缺口（不美化）</b>：「动钱」类操作（加急采购 / 退供折价 / 备件紧急采购）`
      + `当前<b>无专属槽位</b>——10 个槽位按闸门语义命名，无一对应资金承诺。`
      + `本期 interim 口径：复用「来料处置」的金额会签链（<code>fin</code> 角色在 cosign 链）；`
      + `正式方案为新增 <code>hitl.spend_commit</code>（资金支出承诺，6 处改动链，约 0.5 人日），`
      + `待治理角色拍板绑定矩阵后实施。详见 <code>docs/HITL闸门映射表.md</code>。`;
  }

  /**
   * 治理槽位矩阵（R3 验收②）：槽 × 三动词 × 持有角色 + 闸门类别 + 为什么必须人工在环。
   * 角色列实时取自权限目录（ctx.perms，/api/v2/governance/permissions），闸门列取自
   * 闸门理由目录（ctx.gates，/api/v2/governance/gates）——两侧都是只读快照，帮助中心
   * 不内嵌第二份。目录缺失时给诚实降级，不渲染半张表。
   */
  function gateMatrixHtml(ctx) {
    const perms = (ctx && ctx.perms) || null;
    const gates = (ctx && ctx.gates) || [];
    if (!perms || !Array.isArray(perms.slots) || !perms.slots.length) {
      return `<div class="empty-inline">权限目录未加载（后端离线或加载失败）——槽位矩阵不可用；`
        + `闸门语义与缺口见 <code>docs/HITL闸门映射表.md</code>。</div>`;
    }
    const gateOf = (key) => gates.find((g) => g && g.key === key) || null;
    const rolesOf = (permKey) => (perms.roles || [])
      .filter((r) => (r.permissions || []).indexOf(permKey) >= 0)
      .map((r) => r.name || r.key);
    const rows = perms.slots.map((s) => {
      const g = gateOf(s.key);
      const verbs = ["propose", "cosign", "approve"].map((v) => {
        const holders = rolesOf(`hitl.${s.key}.${v}`);
        return `<div class="help-gate-verb"><span class="help-gate-verb-k">${esc(v)}</span>`
          + `<span>${holders.length ? esc(holders.join("、")) : "—"}</span></div>`;
      }).join("");
      return `<tr>
        <td><b>${esc(s.name || s.key)}</b><br/>`
          + `<span style="font-size: calc(10.5px * var(--font-scale));color:var(--text-3)">${esc(s.key)}</span></td>
        <td>${verbs}</td>
        <td>${g ? `<span class="hitl-gate-cls">${esc(g.gate_class || "")}</span>` : "—"}</td>
        <td class="help-gate-why-cell">${esc((g && g.why) || "闸门理由未登记（gate_reasons.yaml 缺条）")}</td>
      </tr>`;
    }).join("");
    return `<table class="help-gate-matrix">
      <thead><tr><th style="width:14%">槽位</th>`
      + `<th style="width:36%">三动词 · 持有角色（permissions.yaml 实时）</th>`
      + `<th style="width:12%">闸门类别</th><th>为什么必须人工在环</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="help-gate-gap">${gateGapNoteHtml()}</div>`;
  }

  // ---------------------------------------------------------------- 落地方法论（R12）

  const METH_KEY = "mfg_launch_check_v1";

  const METH_SEVEN = [
    { t: "定场景与指标", d: "选 1 个高频断点（不是全都要），写成可验证的验收句，例如「交期当场答得上、且留痕」。" },
    { t: "盘数据与口径", d: "逐项确认事实源、时间口径、缺失字段；口径只认注册查询/指标库/看板 KPI 三源，不另写一套。" },
    { t: "配岗位与权限", d: "菜单可见性、看板数据范围、待办槽位三层分开配；需要行级归属时写 owner_scope，不拿自由文本当判据。" },
    { t: "接知识与规则", d: "制度、案例、工艺参数各走独立通道；没有的语料如实标注缺口，不用模型猜。" },
    { t: "小范围试点", d: "先 1 个岗位、1–2 周、只读优先；确认答案能对上看板数字，再开放写/回写。" },
    { t: "治理与人工在环", d: "涉及对外承诺/动钱/改排产的动作挂 HITL 槽位，会签与 SLA 可见；Agent 只发起、不放行。" },
    { t: "推广与复盘", d: "用报告中心留痕与「上线六项体检」逐项验收，复盘数据缺口与未达标项后再扩岗位。" },
  ];

  const METH_FIVE = [
    { t: "走现场", d: "看人怎么问、表怎么对、异常怎么闭环——先记录真实动作，不先讲功能。" },
    { t: "归断点", d: "归到三类：有数不问（入口）、口径不一（信任）、动作无回执（闭环）。" },
    { t: "映射事实源", d: "每个问题指向具体表/注册查询；指不到的写缺口，不承诺「以后能查」。" },
    { t: "定角色与闸门", d: "谁发起、谁会签、谁终批，与 permissions.yaml 对齐；跨部门必须留会签位。" },
    { t: "验闭环", d: "用一条真实问题跑完「问 → 结论 → 待确认 → 留痕」，失败就回上一步改口径/权限。" },
  ];

  const METH_EVOLUTION = [
    "数据速查", "单域问答", "场景闭环", "跨域协同",
    "治理回写", "经营对话", "生态链",
  ];

  const METH_CHECKS = [
    { id: "traceable", t: "数据口径可追溯",
      d: "注册查询/指标库/看板 KPI 三源可在口径词典查到；结论卡能逐条展开数据溯源。" },
    { id: "permission", t: "权限三层可验证",
      d: "菜单/看板/待办三层一致；行级归属（owner_scope）有测试夹具，同岗位不同范围可见行数不同。" },
    { id: "governance", t: "治理闸门有人签",
      d: "10 个 HITL 槽位会签链完整，审批中心能看到闸门理由与缺谁；Agent 不持任何 approve。" },
    { id: "chain", t: "关键链路真机跑通",
      d: "至少一条业务链路走完「问数 → 结论 → 待确认 → 留痕」，并在浏览器真机复验。" },
    { id: "degrade", t: "诚实降级可复现",
      d: "无数据/无权限/LLM 不可用时明示缺口或转人工，绝不编造数字；断网有演示态标注。" },
    { id: "boundary", t: "演示与真实边界清晰",
      d: "演示回写徽标不可关；对客承诺/动钱一律人工确认；数据真实性边界写进交付说明。" },
  ];

  function loadMethState() {
    try {
      const raw = localStorage.getItem(METH_KEY);
      const obj = raw ? JSON.parse(raw) : {};
      return obj && typeof obj === "object" ? obj : {};
    } catch (e) { return {}; }
  }

  function saveMethState(state) {
    try { localStorage.setItem(METH_KEY, JSON.stringify(state || {})); }
    catch (e) { /* 隐私模式忽略 */ }
  }

  function methodologyHtml() {
    const state = loadMethState();
    const done = METH_CHECKS.filter((c) => state[c.id]).length;
    const pct = Math.round((done / METH_CHECKS.length) * 100);
    const stepCards = METH_SEVEN.map((s, i) => `<div class="mth-step">
      <span class="mth-step-no">${i + 1}</span>
      <div><b>${esc(s.t)}</b><div class="mth-step-d">${esc(s.d)}</div></div>
    </div>`).join("");
    const mapCards = METH_FIVE.map((s, i) => `<div class="mth-map-item">
      <span class="mth-map-no">${i + 1}</span>
      <div><b>${esc(s.t)}</b><div class="mth-step-d">${esc(s.d)}</div></div>
    </div>`).join("");
    const evo = METH_EVOLUTION.map((s, i) =>
      `<span class="mth-evo"><em>${i + 1}</em>${esc(s)}</span>`).join("");
    const checks = METH_CHECKS.map((c, i) => `<label class="mth-check-row">
      <input type="checkbox" data-mth-check="${esc(c.id)}" ${state[c.id] ? "checked" : ""}/>
      <span class="mth-check-body"><b>${i + 1}. ${esc(c.t)}</b><span>${esc(c.d)}</span></span>
      <span class="mth-check-state">${state[c.id] ? "已通过" : "待检查"}</span>
    </label>`).join("");
    return `
      <div class="section-title">落地方法论 <span class="section-extra">交付物料 · 七步 / 五步 / 六项体检</span></div>
      <div class="card mth-card">
        <div class="mth-intro">
          这不是功能清单，而是<b>把平台落进现场</b>的检查顺序：先定场景与口径，再配权限与治理，
          证明一条链路跑通后才扩岗位。<b>六项体检可逐项勾选并导出</b>，作为上线评审的交付物。
        </div>

        <div class="mth-sub">七步落地流程</div>
        <div class="mth-grid mth-grid-7">${stepCards}</div>

        <div class="mth-sub">五步现场地图</div>
        <div class="mth-grid mth-grid-5">${mapCards}</div>

        <div class="mth-sub">七场景演进路径 <span class="mth-sub-note">由浅入深，不跳过取证与治理</span></div>
        <div class="mth-evo-line">${evo}</div>

        <div class="mth-sub">上线六项体检 <span class="mth-sub-note">勾选后自动保存；可导出 Markdown 交付评审</span></div>
        <div class="mth-progress-wrap">
          <div class="mth-progress-top">
            <span>完成度 <b id="mth-progress-text">${done}/${METH_CHECKS.length}</b></span>
            <span class="mth-progress-pct"><b id="mth-progress-pct">${pct}%</b></span>
          </div>
          <div class="mth-progress" role="progressbar" aria-valuemin="0"
               aria-valuemax="100" aria-valuenow="${pct}">
            <span id="mth-progress-bar" style="width:${pct}%"></span>
          </div>
        </div>
        <div class="mth-checks" id="mth-checks">${checks}</div>
        <div class="mth-actions">
          <button class="btn btn-primary" type="button" id="mth-export">
            导出体检清单（Markdown）</button>
          <button class="btn mth-reset" type="button" id="mth-reset">清空勾选</button>
          <span class="mth-actions-note" id="mth-save-note">勾选状态保存在本机浏览器</span>
        </div>
      </div>`;
  }

  /** 生成体检清单 Markdown（也是导出文件的内容；返回文本便于测试/复制）。 */
  function exportChecklist() {
    const state = loadMethState();
    const done = METH_CHECKS.filter((c) => state[c.id]).length;
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} `
      + `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const rows = METH_CHECKS.map((c, i) =>
      `| ${i + 1} | ${c.t} | ${c.d.replace(/\|/g, "/")} | ${state[c.id] ? "✅ 已通过" : "⬜ 待检查"} |`);
    const missing = METH_CHECKS.filter((c) => !state[c.id]);
    const verdict = missing.length ? `未通过（还有 ${missing.length} 项待检查）` : "通过（六项全部勾选）";
    const text = [
      "# 平台上线六项体检清单",
      "",
      `- 导出时间：${stamp}`,
      `- 完成度：${done}/${METH_CHECKS.length}（${Math.round(done / METH_CHECKS.length * 100)}%）`,
      `- 结论：${verdict}`,
      "",
      "| # | 检查项 | 通过标准 | 状态 |",
      "|---|---|---|---|",
      ...rows,
      "",
      "## 未通过项",
      ...(missing.length ? missing.map((c) => `- ${c.t}：${c.d}`) : ["- 无"]),
      "",
      "> 口径：本清单为上线评审交付物；勾选状态存本机浏览器，不替代真实链路验收。",
      "",
    ].join("\n");
    try {
      const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `上线体检清单-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1200);
    } catch (e) { /* 下载被拦截：文本仍可复制 */ }
    return text;
  }

  function updateMethProgress() {
    const boxes = document.querySelectorAll("[data-mth-check]");
    let done = 0;
    boxes.forEach((box) => {
      if (box.checked) done += 1;
      const stateEl = box.closest(".mth-check-row")
        && box.closest(".mth-check-row").querySelector(".mth-check-state");
      if (stateEl) stateEl.textContent = box.checked ? "已通过" : "待检查";
    });
    const pct = Math.round((done / METH_CHECKS.length) * 100);
    const t = document.getElementById("mth-progress-text");
    const p = document.getElementById("mth-progress-pct");
    const bar = document.getElementById("mth-progress-bar");
    if (t) t.textContent = `${done}/${METH_CHECKS.length}`;
    if (p) p.textContent = `${pct}%`;
    if (bar) {
      bar.style.width = `${pct}%`;
      const wrap = bar.parentElement;
      if (wrap) wrap.setAttribute("aria-valuenow", String(pct));
    }
    return done;
  }

  function bindMethodology(root) {
    if (!root) return;
    const exportBtn = root.querySelector("#mth-export");
    if (exportBtn) {
      exportBtn.addEventListener("click", () => {
        exportChecklist();
        const note = document.getElementById("mth-save-note");
        if (note) note.textContent = "已导出 Markdown（下载被拦截时可在控制台调用 HelpScreen.exportChecklist）";
      });
    }
    const resetBtn = root.querySelector("#mth-reset");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        saveMethState({});
        // 用 document 作用域而不是 root：帮助中心可能被重渲染替换过节点，
        // root 引用可能已脱离 DOM（真机表现为「存储清了、进度还在」）。
        document.querySelectorAll("[data-mth-check]").forEach((box) => { box.checked = false; });
        updateMethProgress();
        const note = document.getElementById("mth-save-note");
        if (note) note.textContent = "已清空本机勾选";
      });
    }
  }

  // 勾选状态：事件委托（帮助中心重渲染会替换节点，逐节点绑定会失效）
  document.addEventListener("change", function (ev) {
    const box = ev.target;
    if (!box || !box.matches || !box.matches("[data-mth-check]")) return;
    const state = loadMethState();
    if (box.checked) state[box.getAttribute("data-mth-check")] = true;
    else delete state[box.getAttribute("data-mth-check")];
    saveMethState(state);
    updateMethProgress();
  });

  // ---------------------------------------------------------------- 渲染

  function render(root, ctx) {
    root.innerHTML = `
      <div class="content-inner content-wide">
        <div class="page-title">帮助中心</div>
        <div class="page-desc">平台由 <b>18 个智能体</b>协同（17 个域智能体 + 1 个综合办公助手）。核心设计不是"一个大模型包打天下"，而是<b>按代价分派链路、按权限前置检索、按槽位管住决策</b>：能确定性回答的不交给模型，交给模型的必须可溯源，涉及关键决策的一律人工在环。看板点进来的问题走<b>结构化口径直查</b>（与看板同源同函数），跨部门问题由<b>协同规划器</b>拆解后多域智能体并行取证。下面每一块都说明<b>用了什么技术</b>与<b>为什么这么设计</b>。</div>

        ${methodologyHtml()}

        <div class="section-title">协同架构 <span class="section-extra">分层视图 · 与代码现状对齐</span></div>
        <div class="card" style="padding:14px">${architectureSvg()}</div>

        <div class="section-title">悬浮帮助助手 <span class="section-extra">场景识别 · 预置答疑 · 可拖动</span></div>
        <div class="card" style="padding:13px;font-size: calc(12.5px * var(--font-scale));line-height:2;color:var(--text-2)">
          屏幕右侧的<b style="color:var(--text)">悬浮帮助助手</b>（<code>float-help.js</code>）：按住按钮或面板顶栏可拖到任意位置，位置存 <code>localStorage</code>，首次打开默认在最右边；助手定时识别 <code>state.screen</code>（含生态链场景屏），<b style="color:var(--text)">切换场景即开新会话</b>——历史问答清空，换成当前场景的问候语与预置答疑选项（同场景内关闭再打开则保留会话）；点击选项后，预置答案以约 120 字/秒流式输出，答完后选项保留并自动收起，点「＋」重新展开。<b style="color:var(--text)">答疑内容为静态预置、不调后端</b>，与帮助中心同一纪律：改屏内交互 / 链路 / 权限口径时，必须同步改 <code>float-help.js</code> 里的 KB，否则助手会失真。
        </div>

        <div class="section-title">路由：一句话进来，先判断代价 <span class="section-extra">TwoLevelRouter · 七步判定</span></div>
        <div class="grid grid-2">
          <div class="card" style="padding:13px"><b style="color:var(--primary)">七步判定，命中即返回</b>
            ${techGoal(
        "⓪ 结构化上下文 ask_ctx 命中看板 KPI → <b>board</b>（复用看板聚合函数，口径同源）　① 制造数据速查词 → <b>qa</b>　② 场景触发词 / V1 规则 → <b>scenario</b>　③ 办公关键词 → <b>office</b>　④ 开放式态势汇总（含「最需关注 / 整体情况 / 有什么风险」等词、且<b>不含具体型号/线体/编码</b>）→ <b>digest</b>（跨域汇总问数）　⑤ L1 LLM 闭集分类（6 类 + few-shot）→ 域组 / <b>chitchat</b>　⑥ 全未命中 → 关键词模糊降级（<b>degrade</b>）。LLM 不可用时自动跳过第 ⑤ 步；第 ⑥ 步只在<b>场景触发词真命中</b>时才走 scenario，否则按数据直答处理。",
        "路由的意义是<b>别让简单问题跑重型链路</b>：查个库存不该触发跨部门取证和工单。第 ⓪ 步是看板问数的专属快路——你在看板上点什么 KPI，问数就用<b>同一个聚合函数</b>回答（当日/当月/最近7天档位、工段/类别筛选与你所看完全一致），不经 LLM、不重写 SQL、毫秒级返回；权限与脱敏和看板同一套裁决。第 ④ 步专门兜住管理者的开放式提问（「本月度最需关注什么」），此前它会落到「回一句问候」，或被降级路径<b>隐式指派</b>某个场景跑成一整套无关的 8D 链路——现在改为用各看板口径给出<b>有数据支撑的未达标清单</b>，且只读、不建工单。")}
          </div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">另有两条旁路端点，不进总路由</b>
            ${techGoal(
        "企业制度问答 <code>/api/v2/kbqa</code>、制造案例检索 <code>/api/v2/cases/*</code>、办公助手 <code>/api/v2/office/chat</code> 各自是独立端点，有独立的限流、缓存与鉴权。",
        "这三类问题<b>语义空间封闭且权限模型特殊</b>（制度看部门、案例看授权状态、办公看本人），塞进通用路由只会让规则互相打架。独立端点换来的是各自的策略可以独立演进。")}
          </div>
        </div>

        <div class="section-title">三条检索链路 · 技术与目的 <span class="section-extra">制度 / 案例 / 数据，三种风险三种打法</span></div>
        <div class="grid grid-2">
          <div class="card" style="padding:13px"><b style="color:var(--primary)">① 企业制度问答（kbqa）· 四层降级</b>
            ${techGoal(
        "L0 Redis 缓存（TTL 1800s，键含 <code>faq_version</code> 命名空间）→ L1 FAQ 表<b>双闸直答</b>（相似度 ≥ 0.70 <b>且</b>领先第二名 ≥ 0.15；评分 = 关键词×2（封顶 6）+ 标题×3 + 内容×1，除以 6 归一）→ <b>意图路由</b>（124 个域内词 + 24 个拉丁缩写，纯词面判定、微秒级）→ L2 RAG（向量召回 + LLM）。",
        "制度类提问高度重复，能用结构化 FAQ 答的<b>不调模型、不碰向量库</b>——零推理成本、零幻觉、结果可复现。双闸的意义是「不仅要看第一名够高，还要看它够不够突出」，避免两个相近条目里随机选一个。域外问题走约束直答而非硬塞进知识库，防止拿制度语料硬答无关问题。")}
          </div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">② 制造案例检索（case）· 权限前置</b>
            ${techGoal(
        "服务端把身份编译成过滤条件<b>下推给向量库</b>（召回前生效，不是召回后筛）→ 混合召回（受控词表归一 + 同义扩展）→ <b>确定性重排</b>（状态权重 × 新鲜度 × 有效性，同案例去重 ≤ 2 块）→ 关系库 ACL <b>二次授权</b> → 引用校验（案例ID / 块ID / 内容版本三元组）。",
        "案例是隐性知识，也是竞争资产，所以<b>授权必须发生在检索之前</b>：无权案例统一 404，连「存在性」都不泄漏；候选不足时如实返回并附 hidden_count，<b>绝不放宽过滤重试补齐</b>。可看 ≠ 可带走——导出默认拒绝，外发恒定拒绝。")}
          </div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">③ 制造数据问答（Text2SQL）· 四通道 + 八道防线 + 语义 lint</b>
            ${techGoal(
        "四通道递进：看板口径直查（<b>board</b>，复用看板聚合函数，毫秒级）→ 指标库（confidence 0.95）→ 预注册查询（0.85）→ 明细探查（0.6）。八道防线：user_ctx 校验 → 正则禁用词 / 多语句 / 仅 SELECT → 结构检查 → <b>表级 ACL（sqlglot AST 解析，fail-closed）</b> → 只读执行 + row_cap → 结果校验 → 字段脱敏 → 失败转人工工单 + provenance 全溯源。明细探查另加<b>语义 lint</b>：聚合函数进 WHERE、混聚缺 GROUP BY 直接拦截（这类 SQL 语法合法但口径必错）；缺 LIMIT 软警告提示复核。",
        "自然语言转 SQL 最大的风险<b>不是查不出来，是查错了还自信地返回数字</b>——「最近7天 OTD 最低的 5 个型号」曾经被生成出没有 GROUP BY、把 COUNT(*)&gt;0 写进 WHERE 的 SQL，还慢（17 秒）。所以宁可转人工，也不放宽校验——业务宁可等一条工单，也不能拿错数字去对客承诺。")}
          </div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">④ 多 Agent 协同取证（Planner）· 跨域问题并行编排</b>
            ${techGoal(
        "直答查不到且命中 <b>≥2 个域信号</b> → 协同规划器拆 ≤4 个单域子任务（LLM 语义拆解，不可用退化为按域关键词规则拆）→ 多域智能体<b>线程池并行</b> ReAct 取证（事件流实时显示各域进度）→ 汇总器分节聚合：结论按域呈现、数据缺口合并去重、<b>冲突不仲裁</b>（不同域口径不同时如实并列）。单域失败不拖垮整体；需要闭环处置时仍转对应协同场景。",
        "跨域问题（「不良率升高的同时 OTD 也降了，缺料对排产有什么影响」）单交给任何一个域智能体都是<b>答非所问</b>。Planner 是编排者不是同事：子任务之间不互相调用、不递归触发规划——协同结构恒为「规划器 → 执行者」浅层星型，防止 Agent 自由组网的递归失控；取证只读不处置，治理铁律（只发起不放行、步数受限、每步留痕）全部继承。")}
          </div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">⑤ 协同场景（scenario）· 声明式 DAG</b>
            ${techGoal(
        "7 个 YAML 场景，运行时编译为 LangGraph <code>StateGraph</code>；5 类节点 <code>agent / fan_out / fan_in / workflow / hitl</code>；节点级 <code>timeout_s</code> + <code>on_error: degrade|abort</code> 韧性策略；条件分支走受限 AST 白名单求值；<b>加载期校验 HITL 槽位</b>，场景里写了不存在的槽位直接启动失败。",
        "把编排知识从代码里搬到 YAML，<b>新增场景零代码</b>。加载期校验而不是运行期报错，是为了让「配错了」在开发阶段就暴露，而不是在客户面前演到一半才炸。")}
          </div>
        </div>

        <div class="section-title">向量检索底座 · 一套契约，三个后端 <span class="section-extra">关于 ChromaDB：它是可选项，不是当前形态</span></div>
        <div class="card" style="padding:4px 4px 8px">
          <table class="help-table">
            <thead><tr><th style="width:19%">用途</th><th style="width:53%">技术栈</th><th style="width:28%">当前部署</th></tr></thead>
            <tbody>
              <tr>
                <td><b>主 RAG</b><br/><span style="font-size: calc(11px * var(--font-scale));color:var(--text-3)">制度 / 案例全文</span></td>
                <td><b>Milvus</b> · BGE-M3 <b>稠密 + 稀疏混合召回</b>（<code>WeightedRanker(0.7, 0.3)</code>，候选数 <code>max(12, k×3)</code>）→ <b>BGE-Reranker-v2-M3</b> 交叉编码重排 → 按分降序截断</td>
                <td><b style="color:var(--ok)">Milvus</b><br/><span style="font-size: calc(11px * var(--font-scale));color:var(--text-3)">.env: VECTOR_BACKEND=milvus</span></td>
              </tr>
              <tr>
                <td><b>制造案例索引</b><br/><span style="font-size: calc(11px * var(--font-scale));color:var(--text-3)">case assistant</span></td>
                <td><code>CaseIndex</code> 契约三实现：<b>memory</b>（哈希 256 维 + 2-gram，<code>0.6×余弦 + 0.4×关键词</code>，先过滤后打分）／ <b>chroma</b>（本地持久化）／ <b>milvus</b>（生产规模，dim 1024）</td>
                <td><b style="color:var(--ok)">memory</b>（默认）<br/><span style="font-size: calc(11px * var(--font-scale));color:var(--text-3)">CASE_VECTOR_BACKEND 未显式配置</span></td>
              </tr>
              <tr>
                <td><b>重排</b></td>
                <td><code>deterministic</code>（默认，状态权重 × 新鲜度 × 有效性，纯函数、可复现）／ <code>bge</code>（交叉编码模型，可注入替换）</td>
                <td><b style="color:var(--ok)">deterministic</b><br/><span style="font-size: calc(11px * var(--font-scale));color:var(--text-3)">CASE_RERANK 默认</span></td>
              </tr>
              <tr>
                <td><b>关系库</b></td>
                <td><code>DataSource</code> 契约：SQLite（演示）⇄ MySQL / 真实 ERP · MES · QMS，环境变量切换零改码</td>
                <td><b style="color:var(--ok)">SQLite</b><br/><span style="font-size: calc(11px * var(--font-scale));color:var(--text-3)">演示数据集</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card" style="padding:13px;margin-top:10px">
          ${techGoal(
        "<b>ChromaDB 是案例索引的三实现之一</b>（<code>CASE_VECTOR_BACKEND=chroma</code>），不是当前部署形态：代码里向量后端默认值仍是 <code>chroma</code>（零依赖本地持久化，适合单机 POC），而当前环境配置为 <code>milvus</code>（混合召回 + 交叉编码重排，适合生产规模）。三者实现同一套 <code>CaseIndex</code> 契约。",
        "可插拔的目的不是「多支持几个数据库」，而是<b>换部署形态不改业务代码</b>：演示环境用内存后端保证确定性可复现（同一问题两次问出同样结果，便于验收），上生产换成 Milvus 只改一个环境变量。检索代码、权限代码、引用校验代码一行不动。")}
        </div>

        <div class="section-title">智能体配置 <span class="section-extra" id="help-agents-source">加载中…</span></div>
        <div class="grid agent-grid" id="help-agents-grid"><div class="empty">正在加载智能体配置…</div></div>

        <div class="section-title">智能体可用工具 <span class="section-extra">与 MCP / A2A 同源 · 能力边界 = 工具边界</span></div>
        <div class="card" style="padding:6px 14px 10px">
          <div style="font-size: calc(12px * var(--font-scale));color:var(--text-3);padding:6px 0 4px">工具由 <code>ToolRegistry</code> 一次注册、多处暴露（HTTP / MCP / A2A / <b>域智能体 function calling</b>），按 tier 0/1/2 治理分级；域智能体的附加工具（SPC 过程能力、质量追溯、ATP、供应风险等）直接从同一注册表按域装配——单一事实源，新增工具四处生效。每个工具调用必带 <code>user_ctx</code>，因此<b>工具不是权限后门</b>；含终批动作的治理工具（workflow.act）不对智能体开放。清单实时同步自后端，数量随部署形态变化。</div>
          <div id="help-tools-body"></div>
        </div>

        <div class="section-title">数据口径词典 <span class="section-extra" id="help-dict-source">加载中…</span></div>
        <div class="card" style="padding:13px">
          <div style="font-size: calc(12px * var(--font-scale));color:var(--text-3);line-height:1.8;margin-bottom:10px">
            口径<b>三源汇总</b>：注册查询（<code>queries.yaml</code>）+ 指标库（<code>metrics/*.yaml</code>）+
            看板 KPI（<code>dashboard.DRILL_DEFS</code>）。同名口径不合并、分别标注来源；
            看板口径为静态定义，本屏<b>不执行任何查询</b>。看板 KPI 卡、问数证据里的
            「口径」按钮都跳回同一词条——避免「各看各的表」。词典需要后端在线，前端不内嵌第二份。
          </div>
          <div class="help-dict-bar">
            <input id="help-dict-q" class="help-dict-search" type="search"
                   placeholder="搜索指标名 / 口径 / 关键词（如 OTD、齐套、批次追溯）" />
            <div id="help-dict-filters" class="help-dict-filters">
              <button class="help-dict-chip is-active" type="button" data-dict-filter="">全部</button>
              <button class="help-dict-chip" type="button" data-dict-filter="registered_query">注册查询</button>
              <button class="help-dict-chip" type="button" data-dict-filter="metric_library">指标库</button>
              <button class="help-dict-chip" type="button" data-dict-filter="dashboard_kpi">看板 KPI</button>
            </div>
          </div>
          <div id="help-dict-body"><div class="empty">正在加载口径词典…</div></div>
        </div>

        <div class="section-title">治理槽位与闸门 <span class="section-extra">为什么必须人确认 · 谁会签 · 缺谁（R3）</span></div>
        <div class="card" style="padding:13px">
          <div style="font-size: calc(12px * var(--font-scale));color:var(--text-3);margin-bottom:9px">
            三类硬闸门对标：<b style="color:var(--text)">对外消息</b>（说出的话收不回来）、<b style="color:var(--text)">动钱</b>（资金承诺，本期无专属槽位，见下方缺口）、<b style="color:var(--text)">改排产</b>（动产能承诺，影响一片订单交期）；平台另有两类自设闸门：质量关闭（8D/客诉结论即对客口径）与报告审定（发布即代表平台结论）。审批中心每张卡片常驻展示本表对应的「闸门理由 + 会签方及缺谁」。
          </div>
          <div id="help-gate-matrix"><div class="empty">正在加载治理目录…</div></div>
        </div>

        <div class="section-title">治理与安全</div>
        <div class="card" style="padding:13px;font-size: calc(12.5px * var(--font-scale));line-height:2;color:var(--text-2)">
          <b style="color:var(--text)">关键决策人工在环</b>：<b>10 个 HITL 槽位</b>（复线 / 对客 ETA / 来料处置 / 8D 关闭 / 对客回复 / 交期承诺 / 停机窗口 / SPC 处置 / 接单评审 / 报告审定），每槽位三个动词 propose · cosign · approve，共 30 条权限（完整矩阵见上方「治理槽位与闸门」，实时同步自 <code>permissions.yaml</code>）；权限注册表在<b>构造期</b>就禁止 Agent 持有任何 <code>*.approve</code>——智能体只发起、不放行。<br/>
          <b style="color:var(--text)">三套权限各管一域，互不替代</b>：<b>业务数据</b>看 <code>data_scope</code>（看板 allow_roles + 字段脱敏，未登记看板 fail-closed）；<b>治理待办</b>看 <code>workflow_scope</code>（判据是"你是否持有该 HITL 槽位的权限"，跨域一律 404）；<b>案例访问</b>看 <code>case_acl</code>（6 个动词各声明可见状态白名单）。三者正交——一个岗位可以"能看全厂订单，但只管停机窗口"。<br/>
          <b style="color:var(--text)">全程可审计</b>：工作流审计落 <code>platform_workflow_task</code>（含状态机历史）；案例检索落 <code>CaseAuditLog</code>（只记哈希不记正文，支持一键校验审计链对账）；问答落 <code>kbqa_query_log</code>（含 channel 与耗时）。每次查询都记录 SQL 原文、命中行、脱敏标记与耗时（结果卡「数据溯源」可展开）。<br/>
          <b style="color:var(--text)">最小泄漏面</b>：无权案例统一 404（不确认存在性）；hidden_count 只在候选不足时计算；审计只记哈希不记正文；导出默认拒绝、外发恒定拒绝。<br/>
          <b style="color:var(--text)">诚实降级</b>：数据源未接入或查询失败时明示缺口，绝不编造数字；Agentic 步数耗尽输出"已尽力结论 + 数据缺口"；服务不可用时界面明确标注演示模式。
        </div>
      </div>`;
    bindMethodology(root);
    loadAgents(ctx);
    // 数据口径词典（R8）：三源汇总只读快照，后端离线时不内嵌第二份
    loadMetricDict(ctx);
    // 治理槽位矩阵（R3）：权限目录 + 闸门理由目录都是只读快照，缺失时诚实降级
    const gateBox = document.getElementById("help-gate-matrix");
    if (gateBox) gateBox.innerHTML = gateMatrixHtml(ctx);
  }

  window.HelpScreen = { render, exportChecklist };
})();
