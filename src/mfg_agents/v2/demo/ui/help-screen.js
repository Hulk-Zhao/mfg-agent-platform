/**
 * 智造协同平台 · 帮助中心（独立文件，避免污染 screens.js）
 * 数据来源：GET /api/v2/agents/configs（单一事实源）；离线用内置兜底。
 */
(function () {
  "use strict";

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
    { key: "gm", abbr: "GM", name: "经营总览智能体", role: "跨域汇总：交付/质量/风险工单的整体态势", boundary: "只做跨域汇总与升级判断，不做单域深挖", hitl_rule: "重大客户升级/高层决策 → 人工确认" },
    { key: "office", abbr: "OA", name: "办公与数据速查智能体", role: "办公事务（预订会议室、订餐、查我的预订与订单、高影响事项人工确认）+ 制造数据只读速查（材料库存、成品库存与可发量、来料批次与检验结论、库龄呆滞、缺料缺口、批次追溯）", boundary: "办公写操作只能本人名下预订/取消，时段冲突不得静默覆盖；制造数据一律只读，受岗位数据权限约束，不越权、不承诺出入库/调拨/盘点", hitl_rule: "报告厅/培训室、商务客餐、超 500 元订单 → 必须人工确认；数据查询为只读，不触发 HITL" },
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

  function architectureSvg() {
    // 配色分层：深蓝底框用白色系文字，浅底框用深色系；层间距加大防遮挡
    const box = (x, y, w, h, fill, stroke, label, sub, lc) => {
      const dark = lc === "#fff";
      const labelColor = lc || "#1F3B57";
      const subColor = dark ? "rgba(255,255,255,.82)" : "#4A5A6B";
      return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${fill}" stroke="${stroke}" stroke-width="1.4"/>`
        + `<text x="${x + w / 2}" y="${y + (sub ? 26 : 30)}" text-anchor="middle" font-size="14" font-weight="700" fill="${labelColor}">${esc(label)}</text>`
        + (sub ? `<text x="${x + w / 2}" y="${y + 48}" text-anchor="middle" font-size="11" fill="${subColor}">${esc(sub)}</text>` : "");
    };
    const arrow = (x1, y1, x2, y2, label) =>
      `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#5B7E9E" stroke-width="2" marker-end="url(#arrowHelp)"/>`
      + (label ? `<rect x="${(x1 + x2) / 2 - 52}" y="${(y1 + y2) / 2 - 17}" width="104" height="20" rx="5" fill="#EFF5FB"/>`
        + `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 3}" text-anchor="middle" font-size="10.5" font-weight="600" fill="#3D5A75">${esc(label)}</text>` : "");
    const layerLabel = (y, text) =>
      `<text x="380" y="${y}" text-anchor="middle" font-size="11.5" font-weight="700" fill="#2E4E6B">${esc(text)}</text>`;
    return `<svg viewBox="0 0 780 560" style="width:100%;max-width:880px;display:block;margin:0 auto" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arrowHelp" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#5B7E9E"/></marker></defs>

      ${box(285, 10, 210, 62, "#185FA5", "#124A85", "员工 / 外部智能体", "门户 · 协同问数 · A2A / MCP", "#fff")}
      ${arrow(390, 72, 390, 118, "提问")}

      ${box(240, 120, 300, 60, "#E8F1FA", "#6E9CC7", "智能总控 · 两级路由", "L0 规则快路径 / L1 语义分类 + few-shot")}
      ${layerLabel(212, "↓")}
      ${layerLabel(206, "")}
      ${arrow(390, 180, 390, 216, "多策略分派（多意图路由）")}

      ${box(28, 218, 210, 72, "#FDF6E7", "#C9A94E", "协同场景 DAG", "停线 / 来料 / 客诉8D", "#6B5000")}
      ${box(272, 218, 210, 72, "#EAF6EF", "#6FAE88", "数据直答", "指标库 → 注册查询 → 明细探查", "#155E35")}
      ${box(518, 218, 234, 72, "#F0EBFA", "#9D82C9", "Agentic 域智能体", "14 个角色 · 自主工具循环", "#43326E")}
      <text x="132" y="308" text-anchor="middle" font-size="10.5" fill="#7A5B00">HITL 工单 / 报告</text>
      <text x="377" y="308" text-anchor="middle" font-size="10.5" fill="#155E35">无工单 · 秒级</text>
      <text x="635" y="308" text-anchor="middle" font-size="10.5" fill="#43326E">8 步上限 · HITL 自判</text>
      ${arrow(132, 316, 132, 352)}
      ${arrow(377, 316, 377, 352)}
      ${arrow(635, 316, 635, 352)}
      ${layerLabel(344, "统一工具层（与 MCP / A2A 同源 · 全部走 ACL 与脱敏）")}

      ${box(60, 356, 204, 56, "#fff", "#A9BFD3", "Text2SQL 八道防线", "指标库 / 注册查询 / 明细探查")}
      ${box(286, 356, 204, 56, "#fff", "#A9BFD3", "工作流引擎", "状态机 / SLA / 会签 / 审计")}
      ${box(512, 356, 240, 56, "#fff", "#A9BFD3", "知识检索 · 通知 · 场景注册表", "ChromaDB / IM / YAML 场景")}
      ${arrow(390, 412, 390, 448, "只读访问 · 权限同源")}

      ${box(120, 450, 520, 52, "#F4F8FC", "#9FB8CE", "数据层", "演示制造数据集 →（DataSource 契约）→ 真实 ERP / MES / QMS")}
      <text x="390" y="530" text-anchor="middle" font-size="11" font-weight="650" fill="#2E4E6B">治理铁律：关键决策人工在环（HITL 白名单）· Agent 只发起不放行 · 全程 provenance 可审计 · 数据不出厂</text>
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
    const src = document.getElementById("help-agents-source");
    if (src) src.textContent = source;
  }

  function render(root, ctx) {
    const online = ctx.api.online();
    root.innerHTML = `
      <div class="content-inner content-wide">
        <div class="page-title">帮助中心</div>
        <div class="page-desc">平台由 14 个智能体协同工作（7 大域 + 6 个工程专业角色 + 1 个办公与数据速查助手），另有独立的案例访问路由 Agent 负责案例权限申请的分派。智能总控先判断你的问题类型，再分派给最合适的执行路径；制造案例检索走独立的本地增强 Agentic RAG 链路。各智能体的能力边界由提示词约束，全部工具调用可审计。</div>

        <div class="section-title">协同架构 <span class="section-extra">分层视图 · 工具即能力边界</span></div>
        <div class="card" style="padding:16px">${architectureSvg()}</div>

        <div class="section-title">五类问题走五条路径 <span class="section-extra">多意图路由（不是所有问题都跑重型链路）</span></div>
        <div class="grid grid-2">
          <div class="card" style="padding:13px"><b style="color:var(--primary)">① 协同场景</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">命中场景触发词（停线 / 客诉单号 / 出货风险…）→ 跑场景 DAG：多部门并行取证 → 工单 → 人工确认 → 报告。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">② 数据直答</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">数据查询型问题（良率 / 产出 / 库存 / 制度）→ 指标库 / 注册查询 / 明细探查直接回答，秒级、无工单。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">③ 域智能体自主取证</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">直答查不到 → 域智能体接管：自主决定查什么、组合什么数据、是否需要人工确认（最多 8 步，防卡死）。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">④ 人工兜底 / 闲聊</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">闲聊给轻回应并引导回领域；领域内查不到的转人工跟进——不臆造结论。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">⑤ 办公与数据速查（独立 Agentic Agent）</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">两件事都由它管：① 办公事务（会议室预订 / 订餐）走独立助手，命中办公意图关键词即直达，自主调工具办理，报告厅/客餐等高影响事项停下发起人工确认；② 制造数据速查（材料库存 / 成品库存与可发量 / 来料批次与检验结论 / 库龄呆滞 / 缺料缺口 / 批次追溯）为只读，转发平台统一口径注册查询，受岗位数据权限约束。</div></div>
        </div>

        <div class="section-title">本地增强 Agentic RAG · 制造案例检索 <span class="section-extra">授权发生在检索之前 · 无引用不输出</span></div>
        <div class="grid grid-2">
          <div class="card" style="padding:13px"><b style="color:var(--primary)">权限前置：同一句话，岗位不同结果不同</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">检索前由服务端按你的身份编译过滤条件并<b>下推给向量库</b>（在召回前生效，不是召回后筛选）；返回前再用关系库 ACL 做<b>第二次授权</b>（独立代码路径）。无权案例统一 404，不泄漏"存在性"；结果不足时如实返回并附 hidden_count，<b>绝不放宽过滤重试补齐</b>。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">检索链路：混合召回 + 确定性重排</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">受控词表（缺陷模式 / 根因 / 对策）先把"同一现象的不同叫法"归一，再做同义扩展与向量检索；重排按 状态权重 × 新鲜度 × 有效性 计算，同案例去重（≤2 块）。全程确定性可复现，不依赖大模型。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">无引用不输出</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">每条结论必须挂 <b>(案例ID, 块ID, 内容版本)</b> 三元组；引用校验器回查知识层，版本不一致即丢弃该结论（提示"案例已更新，请复核"）；全部断言被丢弃就<b>明示拒答</b>——引用合法不等于答的是所问，相关度低于阈值直接拒绝。演示态用确定性模板作答（全链路本地可跑），接 LLM 只需注入同一个 Answerer 协议。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">向量后端可插拔 + 独立权限表</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">向量层一套契约三个实现：<b>内存 / ChromaDB / Milvus</b>，环境变量切换零代码改动。案例域权限独立于 HITL 目录：六个动词（检索命中 / 预览 / 读全文 / 引用 / 导出 / 外发）各声明可见状态白名单——未结案与已撤权永不进索引；<b>导出默认拒绝</b>（可看 ≠ 可带走，必须走申请审批），<b>外发分享恒定拒绝</b>。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">访问申请：Agent 只路由不批准</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">看到无权案例可发起访问申请，CaseAccessAgent 按 受控词表前缀 → 职能部门 推断路由（SMT 过程类 → 品质部、机加模具类 → 设备工程部）并留痕；<b>推断不出来就明说"无法推断"，绝不自动批准、不编造路由</b>——路由错了比不推更糟。留痕只记推送对象与理由摘要，不含案例正文。</div></div>
          <div class="card" style="padding:13px"><b style="color:var(--primary)">全程可对账的审计链</b><div style="font-size:12.3px;color:var(--text-2);margin-top:5px;line-height:1.7">每次检索/问答落审计：主体、动作、命中数、hidden_count、查询哈希、过滤哈希——<b>只记哈希不记正文</b>。页面提供"校验审计链"一键对账：逐环核对哈希与计数，能证明"你看到的这批结果，就是当时按这套权限算出来的那批"。</div></div>
        </div>

        <div class="section-title">智能体配置 <span class="section-extra" id="help-agents-source">加载中…</span></div>
        <div class="grid agent-grid" id="help-agents-grid"><div class="empty">正在加载智能体配置…</div></div>

        <div class="section-title">智能体可用工具 <span class="section-extra">与 MCP / A2A 同源 · 能力边界 = 工具边界</span></div>
        <div class="card" style="padding:6px 14px 10px"><div id="help-tools-body"></div></div>

        <div class="section-title">治理与安全</div>
        <div class="card" style="padding:13px;font-size:12.5px;line-height:2;color:var(--text-2)">
          <b style="color:var(--text)">关键决策人工在环</b>：复线、退换货/特采、对客承诺、8D 关闭、ECN 会签等白名单决策必须人工确认，智能体（无论场景还是 Agentic）只发起、不放行。<br/>
          <b style="color:var(--text)">权限同源</b>：所有数据访问（场景 / 直答 / 智能体 / MCP / A2A）走同一套表级 ACL 与字段脱敏，工具不是后门。财务制度等敏感数据仅限授权岗位（如总经理）查询。<br/>
          <b style="color:var(--text)">全程可审计</b>：每次查询与每步工具调用都记录 SQL 原文、命中行、脱敏标记与耗时（结果卡「数据溯源」可展开）。<br/>
          <b style="color:var(--text)">案例域最小泄漏面</b>：无权案例统一 404（不确认存在性）；hidden_count 只在候选不足时计算；审计只记哈希不记正文；导出默认拒绝、外发恒定拒绝。<br/>
          <b style="color:var(--text)">诚实降级</b>：数据源未接入或查询失败时明示缺口，绝不编造数字；服务不可用时界面明确标注演示模式。
        </div>
      </div>`;
    loadAgents(ctx);
  }

  window.HelpScreen = { render };
})();
