/**
 * 智造协同平台 · 帮助中心（独立文件，避免污染 screens.js）
 * 数据来源：GET /api/v2/agents/configs（单一事实源）；离线用内置兜底。
 */
(function () {
  "use strict";

  const FALLBACK_AGENTS = [
    { key: "quality", name: "质量域智能体", role: "来料质量、制程不良、客诉与 8D、SPC/CPK、良率", boundary: "只答质量域；产出/停线背景可查；敏感字段按岗位脱敏", hitl_rule: "处置决策/对客口径/8D 关闭 → 必须人工确认" },
    { key: "manufacturing", name: "制造运营智能体", role: "停线、复线进度、OEE、产出达成、设备告警", boundary: "只答制造运营域；成本/对客承诺不在职责内", hitl_rule: "复线决策/停机窗口 → 人工确认" },
    { key: "supply_chain", name: "供应链智能体", role: "订单交付、齐套、缺料、ATP、库存与发货", boundary: "只答供应链域；质量判定/设备维修不在职责内", hitl_rule: "对客交期承诺/分批出货/加急 → 人工确认" },
    { key: "rd_engineering", name: "研发工程智能体", role: "ECN、NPI、图纸、工艺参数、根因分析", boundary: "只答研发域；BOM/图纸数据源未接入时诚实说明", hitl_rule: "ECN 会签/图纸发放 → 人工确认" },
    { key: "finance", name: "财务智能体", role: "成本构成、费用报销、开票与应收、费用归集", boundary: "只答财务域；ERP 财务数据源未接入时诚实说明", hitl_rule: "费用审批/开票确认/成本核销 → 人工确认" },
    { key: "admin", name: "行政人事智能体", role: "行政流程、培训资质、考勤假期、平台使用帮助", boundary: "只答职能支撑与平台使用问题", hitl_rule: "用章审批/培训资质确认 → 人工确认" },
    { key: "gm", name: "经营总览智能体", role: "跨域汇总：交付/质量/风险工单的整体态势", boundary: "只做跨域汇总与升级判断，不做单域深挖", hitl_rule: "重大客户升级/高层决策 → 人工确认" },
  ];
  const FALLBACK_TOOLS = [
    { name: "query_metric", desc: "查平台统一口径指标（当前：otd_rate 准交率）", kind: "只读" },
    { name: "list_available_queries", desc: "列出全部预注册查询（含 keywords 与参数）", kind: "只读" },
    { name: "run_registered_query", desc: "执行预注册查询（参数化、走表级 ACL 与脱敏）", kind: "只读" },
    { name: "explore_data", desc: "自然语言明细探查（SQL 生成 → 八道防线审核）", kind: "只读" },
    { name: "list_scenarios", desc: "列出平台协同场景（复杂闭环入口指引）", kind: "只读" },
    { name: "create_hitl_task", desc: "发起人工确认工单（Agent 只发起，永不放行）", kind: "写/HITL" },
  ];

  function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  function architectureSvg() {
    const box = (x, y, w, h, fill, stroke, label, sub, lc) =>
      `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9" fill="${fill}" stroke="${stroke}"/>`
      + `<text x="${x + w / 2}" y="${y + (sub ? 22 : 26)}" text-anchor="middle" font-size="13" font-weight="650" fill="${lc || "#1F3B57"}">${esc(label)}</text>`
      + (sub ? `<text x="${x + w / 2}" y="${y + 40}" text-anchor="middle" font-size="10.5" fill="#5F6B7A">${esc(sub)}</text>` : "");
    const arrow = (x1, y1, x2, y2, label) =>
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#9DB8CE" stroke-width="1.6" marker-end="url(#arrowHelp)"/>`
      + (label ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 6}" text-anchor="middle" font-size="9.5" fill="#7A8CA0">${esc(label)}</text>` : "");
    return `<svg viewBox="0 0 760 470" style="width:100%;max-width:860px;display:block;margin:0 auto" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arrowHelp" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#9DB8CE"/></marker></defs>
      ${box(280, 8, 200, 48, "#185FA5", "#124A85", "员工 / 外部智能体", "门户 · 协同问数 · A2A / MCP", "#fff")}
      ${arrow(380, 56, 380, 86, "提问")}
      ${box(230, 88, 300, 52, "#E8F1FA", "#7FA8CE", "智能总控 · 两级路由", "L0 规则快路径 / L1 语义分类 + few-shot")}
      <text x="380" y="162" text-anchor="middle" font-size="10.5" fill="#5F6B7A">四策略分派（多意图路由）</text>
      ${arrow(300, 140, 130, 190)}
      ${arrow(360, 140, 360, 190)}
      ${arrow(430, 140, 590, 190)}
      ${box(28, 192, 200, 64, "#FDF6E7", "#D9C58A", "协同场景 DAG", "停线 / 来料 / 客诉8D / 出货 / 设备", "#7A5B00")}
      ${box(262, 192, 200, 64, "#EAF6EF", "#8CC5A5", "数据直答", "指标库 → 注册查询 → 明细探查", "#1A6B3C")}
      ${box(500, 192, 232, 64, "#F0EBFA", "#B39DDB", "Agentic 域智能体", "13 个专业角色 · 自主工具循环 · HITL 自判", "#4B3A78")}
      <text x="380" y="282" text-anchor="middle" font-size="10.5" fill="#5F6B7A">统一工具层（与 MCP / A2A 同源 · 全部走 ACL 与脱敏）</text>
      ${arrow(128, 256, 128, 300)}
      ${arrow(362, 256, 362, 300)}
      ${arrow(616, 256, 616, 300)}
      ${box(60, 302, 200, 46, "#fff", "#C9D6E2", "Text2SQL 八道防线", "指标库 / 注册查询 / 明细探查")}
      ${box(280, 302, 200, 46, "#fff", "#C9D6E2", "工作流引擎", "状态机 / SLA / 会签 / 审计")}
      ${box(500, 302, 232, 46, "#fff", "#C9D6E2", "知识检索 · 通知 · 场景注册表", "ChromaDB / IM / YAML 场景")}
      ${arrow(380, 348, 380, 378, "只读访问 · 权限与脱敏同源")}
      ${box(120, 380, 520, 46, "#F4F8FC", "#B8CCDE", "数据层：演示制造数据集 →（DataSource 契约）→ 真实 ERP / MES / QMS", "", "")}
      <text x="380" y="452" text-anchor="middle" font-size="10.5" fill="#5F6B7A">治理铁律：关键决策人工在环（HITL 白名单）· Agent 只发起不放行 · 全程 provenance 可审计 · 数据不出厂</text>
    </svg>`;
  }

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
    grid.innerHTML = agents.map((a) => `
      <div class="card agent-card">
        <div class="agent-card-head">
          <span class="agent-badge">${esc((a.name || a.key).slice(0, 1))}</span>
          <div><div class="agent-name">${esc(a.name || a.key)}</div>
          <div style="font-size:10.8px;color:var(--text-3)">key: ${esc(a.key)}</div></div>
        </div>
        <div class="agent-sec"><b>意图</b>${esc(a.role || "")}</div>
        <div class="agent-sec"><b>边界</b>${esc(a.boundary || "")}</div>
        <div class="agent-sec hitl"><b>HITL</b>${esc(a.hitl_rule || "")}</div>
      </div>`).join("");
    if (toolsBox) {
      toolsBox.innerHTML = tools.map((t) => `<div class="brief-item"><span class="k">${esc(t.name)}</span><span class="v">${esc(t.desc)}（${esc(t.kind)}）</span></div>`).join("");
    }
    const src = document.getElementById("help-agents-source");
    if (src) src.textContent = source;
  }

  function render(root, ctx) {
    const online = ctx.api.online();
    root.innerHTML = `
      <div class="content-inner">
        <div class="page-title">帮助中心</div>
        <div class="page-desc">平台由 13 个专业角色智能体协同工作（7 大域 + 6 个工程专业角色）：智能总控先判断你的问题类型，再分派给最合适的执行路径。各智能体的能力边界由提示词约束，全部工具调用可审计。</div>

        <div class="section-title">协同架构 <span class="section-extra">分层视图 · 工具即能力边界</span></div>
        <div class="card" style="padding:16px">${architectureSvg()}</div>

        <div class="section-title">四种问题走四种路径 <span class="section-extra">多意图路由（不是所有问题都跑重型链路）</span></div>
        <div class="grid grid-2">
          <div class="card" style="padding:13px"><b style="color:var(--primary)">① 协同场景</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">命中场景触发词（停线 / 客诉单号 / 出货风险…）→ 跑场景 DAG：多部门并行取证 → 工单 → 人工确认 → 报告。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">② 数据直答</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">数据查询型问题（良率 / 产出 / 库存 / 制度）→ 指标库 / 注册查询 / 明细探查直接回答，秒级、无工单。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">③ 域智能体自主取证</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">直答查不到 → 域智能体接管：自主决定查什么、组合什么数据、是否需要人工确认（最多 8 步，防卡死）。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">④ 人工兜底 / 闲聊</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">闲聊给轻回应并引导回领域；领域内查不到的转人工跟进——不臆造结论。</div></div>
        </div>

        <div class="section-title">域组智能体 <span class="section-extra" id="help-agents-source">加载中…</span></div>
        <div class="grid grid-2" id="help-agents-grid"><div class="empty">正在加载智能体配置…</div></div>

        <div class="section-title">智能体可用工具 <span class="section-extra">与 MCP / A2A 同源 · 能力边界 = 工具边界</span></div>
        <div class="card" style="padding:6px 14px 10px"><div id="help-tools-body"></div></div>

        <div class="section-title">治理与安全</div>
        <div class="card" style="padding:13px;font-size:12.5px;line-height:2;color:var(--text-2)">
          <b style="color:var(--text)">关键决策人工在环</b>：复线、退换货/特采、对客承诺、8D 关闭、ECN 会签等白名单决策必须人工确认，智能体（无论场景还是 Agentic）只发起、不放行。<br/>
          <b style="color:var(--text)">权限同源</b>：所有数据访问（场景 / 直答 / 智能体 / MCP / A2A）走同一套表级 ACL 与字段脱敏，工具不是后门。财务制度等敏感数据仅限授权岗位（如总经理）查询。<br/>
          <b style="color:var(--text)">全程可审计</b>：每次查询与每步工具调用都记录 SQL 原文、命中行、脱敏标记与耗时（结果卡「数据溯源」可展开）。<br/>
          <b style="color:var(--text)">诚实降级</b>：数据源未接入或查询失败时明示缺口，绝不编造数字；服务不可用时界面明确标注演示模式。
        </div>
      </div>`;
    loadAgents(ctx);
  }

  window.HelpScreen = { render };
})();
