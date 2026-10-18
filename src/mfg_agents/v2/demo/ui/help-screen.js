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
    { name: "explore_data", desc: "自然语言明细探查（SQL 生成 → 八道防线审核）", kind: "只读" },
    { name: "list_scenarios", desc: "列出平台协同场景（复杂闭环入口指引）", kind: "只读" },
    { name: "create_hitl_task", desc: "发起人工确认工单（Agent 只发起，永不放行）", kind: "写/HITL" },
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
      let s = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${fill}" stroke="${stroke}" stroke-width="1.4"/>`;
      s += `<text x="${x + w / 2}" y="${y + 25}" text-anchor="middle" font-size="13" font-weight="700" fill="${tc}">${esc(title)}</text>`;
      (o.lines || []).forEach((t, i) => {
        s += `<text x="${x + 16}" y="${y + 45 + i * 16}" font-size="${fs}" fill="${lc}">${esc(t)}</text>`;
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

    return `<svg viewBox="0 0 880 950" style="width:100%;max-width:900px;display:block;margin:0 auto" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arrowHelp" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#5B7E9E"/></marker></defs>

      ${box(24, 8, 256, 56, "#185FA5", "#124A85", "员工门户 · Web", { lines: ["制造问数 · 协同场景 · 办公自助"], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)" })}
      ${box(312, 8, 256, 56, "#185FA5", "#124A85", "外部智能体 · A2A", { lines: ["message/send · tasks/get"], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)" })}
      ${box(600, 8, 256, 56, "#185FA5", "#124A85", "MCP 客户端", { lines: ["JSON-RPC 2.0 over stdio"], titleColor: "#fff", lineColor: "rgba(255,255,255,.85)" })}
      ${note(24, 78, "旁路端点（不进总路由）：/kbqa 制度问答 · /cases/* 案例检索 · /office/chat 办公")}
      ${arrow(440, 64, 440, 104, "提问")}

      ${box(24, 106, 832, 126, "#E8F1FA", "#6E9CC7", "两级路由 TwoLevelRouter —— 按代价分派，简单问题不跑重型链路", {
      lines: [
        "① 制造数据速查词命中        → 数据直答（qa）",
        "② 场景触发词 / V1 规则命中   → 协同场景（scenario）",
        "③ 办公关键词命中            → 办公助手（office）",
        "④ L1 LLM 闭集分类（6 类 + few-shot）→ 域组 / 闲聊",
        "⑤ 全部未命中                → 关键词模糊降级（degrade）",
      ]
    })}

      ${arrow(122, 232, 122, 270, "① 协同场景")}
      ${arrow(334, 232, 334, 270, "② 数据直答")}
      ${arrow(546, 232, 546, 270, "③ 办公事务")}
      ${arrow(758, 232, 758, 270, "④ 闲聊/转人工")}

      ${box(24, 272, 196, 120, "#FDF6E7", "#C9A94E", "协同场景 DAG", {
      lines: ["7 个 YAML 声明式场景", "运行时编译为 LangGraph", "5 类节点 agent/fan-out", "fan_in/workflow/hitl", "产出：工单 + 报告"], titleColor: "#6B5000", lineColor: "#7A5B00"
    })}
      ${box(236, 272, 196, 120, "#EAF6EF", "#6FAE88", "数据直答", {
      lines: ["指标库 → 注册查询", "→ 明细探查（三通道）", "Text2SQL 八道防线", "秒级 · 无工单", "confidence 0.95/0.85/0.6"], titleColor: "#155E35", lineColor: "#1F6B41"
    })}
      ${box(448, 272, 196, 120, "#F0EBFA", "#9D82C9", "办公助手", {
      lines: ["会议室 / 订餐 / 报销", "请假 / 补考勤", "独立 ReAct Agentic", "写操作限本人名下", "高影响 → 人工确认"], titleColor: "#43326E", lineColor: "#4E3D7A"
    })}
      ${box(660, 272, 196, 120, "#F4F6F8", "#9FB0C0", "闲聊 / 转人工", {
      lines: ["轻回应 + 引导回领域", "域内查不到 → 转人工", "不臆造结论", "", ""], titleColor: "#3A4A5A", lineColor: "#55646F"
    })}

      ${arrow(440, 392, 440, 430, "直答查不到 → 域智能体接管")}

      ${box(24, 432, 832, 84, "#EAF6EF", "#6FAE88", "Agentic 域智能体 · 17 个域 + 1 个综合办公助手", {
      lines: [
        "ReAct 工具循环（bind_tools → 调用 → 结果回灌），最多 8 步、单步超时 45s",
        "步数耗尽输出「已尽力结论 + 数据缺口」，不编造；提示词即边界：意图 / 职责 / 工具 / HITL 准则",
      ], titleColor: "#155E35", lineColor: "#1F6B41"
    })}

      ${arrow(440, 516, 440, 554, "工具调用")}

      ${box(24, 556, 832, 68, "#fff", "#A9BFD3", "统一工具层 ToolRegistry · tier 0/1/2 治理分级", {
      lines: ["HTTP / MCP / A2A 三端同源 · 每个工具必带 user_ctx · 一次注册，多处暴露，能力边界 = 工具边界"]
    })}

      ${arrow(440, 624, 440, 662, "只读访问 · 权限同源")}

      ${box(24, 664, 408, 120, "#FFF9EC", "#D9B45B", "知识层 · 检索", {
      lines: [
        "主 RAG：Milvus + BGE-M3 混合召回 + Reranker 重排",
        "案例库：memory / chroma / milvus 三实现可插拔",
        "制度 FAQ：kbqa_faq 表双闸直答，命中即返回",
        "目的：能确定性答的不交给模型，交给模型的必可溯源",
      ], titleColor: "#6B5000", lineColor: "#7A5B00", fontSize: 10.5
    })}
      ${box(448, 664, 408, 120, "#F0F5FA", "#8FAFC9", "数据层 · 只读", {
      lines: [
        "DataSource 契约：演示 SQLite ⇄ 真实 ERP/MES/QMS",
        "表级 ACL（17 张表，fail-closed）+ 字段按岗位脱敏",
        "只读执行 row_cap=200 · 结果校验 · provenance 溯源",
        "目的：宁可转人工，也不放宽校验返回错误数字",
      ], titleColor: "#1F3B57", lineColor: "#42566B", fontSize: 10.5
    })}

      ${box(24, 812, 832, 84, "#1F3B57", "#123049", "治理底盘 · 贯穿全流程", {
      lines: [
        "HITL 8 槽位 · 工作流状态机 / SLA / 会签 · 三套权限分域（业务数据 / 治理待办 / 案例访问）· 审计留痕",
        "Agent 只发起不放行 · 关键决策人工在环 · 私有化部署，数据不出厂",
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
        if (resp.data && resp.data.agents && resp.data.agents.length) {
          agents = resp.data.agents;
          tools = resp.data.tools || tools;
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

  // ---------------------------------------------------------------- 渲染

  function render(root, ctx) {
    root.innerHTML = `
      <div class="content-inner content-wide">
        <div class="page-title">帮助中心</div>
        <div class="page-desc">平台由 <b>18 个智能体</b>协同（17 个域智能体 + 1 个综合办公助手）。核心设计不是"一个大模型包打天下"，而是<b>按代价分派链路、按权限前置检索、按槽位管住决策</b>：能确定性回答的不交给模型，交给模型的必须可溯源，涉及关键决策的一律人工在环。下面每一块都说明<b>用了什么技术</b>与<b>为什么这么设计</b>。</div>

        <div class="section-title">协同架构 <span class="section-extra">分层视图 · 与代码现状对齐</span></div>
        <div class="card" style="padding:14px">${architectureSvg()}</div>

        <div class="section-title">路由：一句话进来，先判断代价 <span class="section-extra">TwoLevelRouter · 五步判定</span></div>
        <div class="grid grid-2">
          <div class="card" style="padding:13px"><b style="color:var(--primary)">五步判定，命中即返回</b>
            ${techGoal(
        "① 制造数据速查词 → <b>qa</b>　② 场景触发词 / V1 规则 → <b>scenario</b>　③ 办公关键词 → <b>office</b>　④ L1 LLM 闭集分类（6 类 + few-shot）→ 域组 / <b>chitchat</b>　⑤ 全未命中 → 关键词模糊降级（<b>degrade</b>）。LLM 不可用时自动跳过第 ④ 步。",
        "路由的意义是<b>别让简单问题跑重型链路</b>：查个库存不该触发跨部门取证和工单。前三步是毫秒级纯规则，只有规则判不了才花钱调模型；模型挂了还有第 ⑤ 步兜底，不会整个平台不可用。")}
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
          <div class="card" style="padding:13px"><b style="color:var(--primary)">③ 制造数据问答（Text2SQL）· 三通道 + 八道防线</b>
            ${techGoal(
        "三通道降级：指标库（confidence 0.95）→ 预注册查询（0.85）→ 明细探查（0.6）。八道防线：user_ctx 校验 → 正则禁用词 / 多语句 / 仅 SELECT → 结构检查（禁 SELECT INTO）→ <b>表级 ACL（17 张表，sqlglot AST 解析，解析失败 fail-closed）</b> → 只读执行 + row_cap 200 → 结果校验（空结果 / 行数爆炸）→ 字段脱敏（按角色，含别名映射）→ 失败转人工工单 + provenance 全溯源。",
        "自然语言转 SQL 最大的风险<b>不是查不出来，是查错了还自信地返回数字</b>。所以宁可转人工，也不放宽校验——业务宁可等一条工单，也不能拿错数字去对客承诺。")}
          </div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">④ 协同场景（scenario）· 声明式 DAG</b>
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
                <td><b>主 RAG</b><br/><span style="font-size:11px;color:var(--text-3)">制度 / 案例全文</span></td>
                <td><b>Milvus</b> · BGE-M3 <b>稠密 + 稀疏混合召回</b>（<code>WeightedRanker(0.7, 0.3)</code>，候选数 <code>max(12, k×3)</code>）→ <b>BGE-Reranker-v2-M3</b> 交叉编码重排 → 按分降序截断</td>
                <td><b style="color:var(--ok)">Milvus</b><br/><span style="font-size:11px;color:var(--text-3)">.env: VECTOR_BACKEND=milvus</span></td>
              </tr>
              <tr>
                <td><b>制造案例索引</b><br/><span style="font-size:11px;color:var(--text-3)">case assistant</span></td>
                <td><code>CaseIndex</code> 契约三实现：<b>memory</b>（哈希 256 维 + 2-gram，<code>0.6×余弦 + 0.4×关键词</code>，先过滤后打分）／ <b>chroma</b>（本地持久化）／ <b>milvus</b>（生产规模，dim 1024）</td>
                <td><b style="color:var(--ok)">memory</b>（默认）<br/><span style="font-size:11px;color:var(--text-3)">CASE_VECTOR_BACKEND 未显式配置</span></td>
              </tr>
              <tr>
                <td><b>重排</b></td>
                <td><code>deterministic</code>（默认，状态权重 × 新鲜度 × 有效性，纯函数、可复现）／ <code>bge</code>（交叉编码模型，可注入替换）</td>
                <td><b style="color:var(--ok)">deterministic</b><br/><span style="font-size:11px;color:var(--text-3)">CASE_RERANK 默认</span></td>
              </tr>
              <tr>
                <td><b>关系库</b></td>
                <td><code>DataSource</code> 契约：SQLite（演示）⇄ MySQL / 真实 ERP · MES · QMS，环境变量切换零改码</td>
                <td><b style="color:var(--ok)">SQLite</b><br/><span style="font-size:11px;color:var(--text-3)">演示数据集</span></td>
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
          <div style="font-size:12px;color:var(--text-3);padding:6px 0 4px">工具由 <code>ToolRegistry</code> 一次注册、多处暴露（HTTP / MCP / A2A），按 tier 0/1/2 治理分级；每个工具调用必带 <code>user_ctx</code>，因此<b>工具不是权限后门</b>。清单实时同步自后端，数量随部署形态变化。</div>
          <div id="help-tools-body"></div>
        </div>

        <div class="section-title">治理与安全</div>
        <div class="card" style="padding:13px;font-size:12.5px;line-height:2;color:var(--text-2)">
          <b style="color:var(--text)">关键决策人工在环</b>：8 个 HITL 槽位（复线 / 对客 ETA / 来料处置 / 8D 关闭 / 对客回复 / 交期承诺 / 停机窗口 / SPC 处置），每槽位三个动词 propose · cosign · approve，共 24 条权限；权限注册表在<b>构造期</b>就禁止 Agent 持有任何 <code>*.approve</code>——智能体只发起、不放行。<br/>
          <b style="color:var(--text)">三套权限各管一域，互不替代</b>：<b>业务数据</b>看 <code>data_scope</code>（看板 allow_roles + 字段脱敏，未登记看板 fail-closed）；<b>治理待办</b>看 <code>workflow_scope</code>（判据是"你是否持有该 HITL 槽位的权限"，跨域一律 404）；<b>案例访问</b>看 <code>case_acl</code>（6 个动词各声明可见状态白名单）。三者正交——一个岗位可以"能看全厂订单，但只管停机窗口"。<br/>
          <b style="color:var(--text)">全程可审计</b>：工作流审计落 <code>platform_workflow_task</code>（含状态机历史）；案例检索落 <code>CaseAuditLog</code>（只记哈希不记正文，支持一键校验审计链对账）；问答落 <code>kbqa_query_log</code>（含 channel 与耗时）。每次查询都记录 SQL 原文、命中行、脱敏标记与耗时（结果卡「数据溯源」可展开）。<br/>
          <b style="color:var(--text)">最小泄漏面</b>：无权案例统一 404（不确认存在性）；hidden_count 只在候选不足时计算；审计只记哈希不记正文；导出默认拒绝、外发恒定拒绝。<br/>
          <b style="color:var(--text)">诚实降级</b>：数据源未接入或查询失败时明示缺口，绝不编造数字；Agentic 步数耗尽输出"已尽力结论 + 数据缺口"；服务不可用时界面明确标注演示模式。
        </div>
      </div>`;
    loadAgents(ctx);
  }

  window.HelpScreen = { render };
})();
