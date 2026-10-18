/**
 * 案例助手（cases）· 详情屏
 *
 * 数据面（全部走案例域接口，令牌与门户令牌**互相独立**）：
 *   POST /api/v2/cases/session       选岗 → 签发 tv=2 案例令牌（含维度令牌）
 *   GET  /api/v2/cases/meta          分类 / 受控词表 / 动作矩阵 / 我的维度 / tv·pv
 *   POST /api/v2/cases/search        检索（filter 由服务端按身份编译并下推向量库）
 *   POST /api/v2/cases/ask           带引用问答（引用校验失败或不够相关即拒答）
 *   GET  /api/v2/cases/{id}          详情（脱敏知识层；无权 → 404）
 *   POST /api/v2/cases/{id}/cite     引用到工单 → (case_id, chunk_id, content_version)
 *   GET  /api/v2/cases/audit/verify  审计哈希链校验
 *   POST /api/v2/cases/ingest        入库（需 case.ingest；落 review，不自动发布）
 *
 * 跨部门访问申请（授权链路）：
 *   POST /api/v2/cases/{id}/request-access          提交申请（不直接授权）
 *   GET  /api/v2/cases/access/requests              申请列表（mine / dept）
 *   POST /api/v2/cases/access/requests/{id}/decide  人工审批（职能部门负责人）
 *   POST /api/v2/cases/access/requests/{id}/demo-approve
 *                                                     演示专用：模拟职能部门审批回调
 *                                                     （预留接口，仅演示；审计打 .demo 标记）
 *
 * ── 五条展示纪律（改这个文件之前先读）──────────────────────────────
 * 1. **不放任何自定义过滤入口**：检索请求模型里没有 filter 字段。页面只展示
 *    服务端编译出的 `filter_hash` 与 `filter_explain`（人类可读描述），
 *    **绝不回传 expr / AST**，也绝不自己拼过滤条件。
 * 2. **hidden_count 与 notice 必须显著**：这是产品差异化的可见证据，不折叠、不省略。
 * 3. **候选不足不放宽**：结果少于 k 时如实展示后端 notice，不补齐、不"优化"。
 * 4. **离线/401/403 各有明确文案**：不编造任何兜底数据（假数据会让"授权差异"失真）。
 * 5. **令牌只在本屏用**：不碰 app 级 api._post/_get（它们自动带门户令牌且 401 会把
 *    用户踢回登录页）。本屏自带 caseFetch()，令牌存 localStorage 的 mfg_case_token。
 *
 * 注册方式：`window.Screens.render_cases`（app.js 的 renderScreen 按
 * `render_<screenId>` 查找，无需改 screens.js）。
 */
(function () {
  "use strict";

  /* ================= 常量 ================= */

  /** 案例令牌（与门户 state.token 完全无关；用 localStorage 便于刷新后仍在）。 */
  const TOKEN_KEY = "mfg_case_token";
  /** 上次选的身份，刷新后可复原表单。 */
  const IDENTITY_KEY = "mfg_case_identity";

  const DEFAULT_IDENTITY = {
    role: "qa", dept: "quality", plant: "P1",
    employment_type: "employee", max_classification_rank: 1,
  };

  const STATUS_META = {
    active: { label: "生效", cls: "badge-green" },
    review: { label: "审核中", cls: "badge-amber" },
    draft: { label: "草稿", cls: "badge-gray" },
    deprecated: { label: "待复核", cls: "badge-gray" },
    rejected: { label: "已驳回", cls: "badge-red" },
    withdrawn: { label: "已撤回", cls: "badge-red" },
    archived: { label: "已归档", cls: "badge-gray" },
  };

  const CHUNK_LABEL = {
    summary: "摘要", phenomenon: "现象", root_cause: "根因", countermeasure: "对策",
  };

  const ACTION_LABEL = { query: "检索", preview: "预览", read: "读全文", cite: "引用" };
  const RETRIEVAL_ACTIONS = ["query", "cite", "read"];

  /* ============ 中文化映射表（硬约束：中文文案只放这里，不散落在模板里） ============ */
  const ZH = {
    /** 审计动作 → 中文 */
    action: {
      "case.query": "案例检索", "case.query.denied": "案例检索·已拒绝",
      "case.preview": "预览摘要", "case.read": "查看详情",
      "case.cite": "引用到工单", "case.ingest": "案例入库",
      "case.answer": "问答", "case.answer.refused": "问答·已拒答",
      "case.access.requested": "访问申请", "case.access.approved": "访问申请·已批准",
      "case.access.approved.demo": "访问申请·已批准（演示代批）",
      "case.access.rejected": "访问申请·已驳回", "case.access.cancelled": "访问申请·已撤回",
      "case.access.expired": "访问申请·已过期", "case.access.pushed": "访问申请·已推送",
      "case.access.push.denied": "访问申请·推送失败",
    },
    /** 审计表头 → 中文 */
    auditCol: {
      ts: "时间", actor: "操作人", action: "动作", case_id: "案例",
      filter_hash: "过滤指纹", hit_count: "命中数", hidden_count: "不可见数",
      query_hash: "查询指纹",
    },
    /** 部门 → 中文（后端也会给 label；这里兜底登录前阶段） */
    dept: {
      quality: "品质部", equipment: "设备工程部", management: "总经理室",
      manufacturing: "制造部", procurement: "采购部", supply_chain: "供应链部",
      rd_engineering: "研发部", pm: "项目管理部", sales: "销售部",
      service: "客户服务部", finance: "财务部", warehouse: "仓储部",
      admin: "行政人事部", ehs: "环境安全部",
    },
    /** 令牌维度 → 中文 */
    tokenDim: {
      role: "岗位", dept: "部门", plant: "工厂", line: "产线", prj: "项目",
      cus: "客户", sup: "供应商", pf: "产品族", etype: "用工类型", user: "个人特批",
    },
    /** 状态 */
    requestStatus: {
      pending: "待审批", approved: "已批准", rejected: "已驳回",
      expired: "已过期", cancelled: "已撤回",
    },
  };

  /** `role:qa` 这类令牌 → 「岗位：质量工程」；无法翻译的原样返回。 */
  function tokenText(token, meta) {
    const raw = String(token || "");
    const idx = raw.indexOf(":");
    if (idx < 0) return raw;
    const dim = ZH.tokenDim[raw.slice(0, idx)] || raw.slice(0, idx);
    const value = raw.slice(idx + 1);
    return dim + "：" + (roleLabel(value, meta) || (ZH.dept[value] || value));
  }

  /** 岗位 key → 中文名（meta 到位后用服务端口径）。 */
  function roleLabel(key, meta) {
    const roles = (meta && meta.rbac && meta.rbac.roles) || [];
    const hit = roles.filter(function (r) { return r.key === key; })[0];
    return hit ? (hit.name || hit.key) : "";
  }

  /** `u-equipment_manager` → 「陈工（设备经理）」（中文岗位名 + 姓名）。 */
  function actorLabel(userId, meta) {
    const raw = String(userId || "");
    const key = raw.replace(/^u-/, "");
    const name = roleLabel(key, meta);
    return name ? raw + "（" + name + "）" : raw;
  }

  /** 令牌列表 → 人类可读；原始串进"技术详情"。 */
  function tokensHuman(tokens, meta) {
    return (tokens || []).map(function (t) { return tokenText(t, meta); })
      .filter(Boolean);
  }

  const EMPLOYMENT_LABEL = {
    employee: "正式员工", outsource: "外协", temp: "临时工",
    supplier: "供应商", customer: "客户",
  };

  /** 分类筛选（**只是结果内的显示过滤**，不参与召回、不参与授权）。 */
  const CATEGORY_FILTERS = [
    ["all", "全部"],
    ["customer_complaint", "客诉"],
    ["material_defect", "材料不良"],
    ["process_defect", "制程不良"],
    ["equipment_failure", "设备故障"],
    ["process_experience", "工艺经验"],
    ["equipment_experience", "设备经验"],
    ["quality_experience", "质量经验"],
  ];

  /** 对比模式的默认部门（演示建议值，可手改；判定始终在服务端）。 */
  const DEFAULT_DEPT = {
    qa: "quality", qa_manager: "quality", pqe: "quality", sqe: "quality",
    quality_director: "quality",
    equipment: "equipment", equipment_manager: "equipment", maintenance_tech: "equipment",
    manufacturing: "production", gm: "management", project_manager: "management",
    procurement: "procurement", planning: "planning", material_control: "planning",
    customer_service: "customer_service", sales: "sales",
    finance: "finance", warehouse: "warehouse",
    rd_engineering: "rd", me_engineer: "rd", test_engineer: "rd",
  };

  /** 首次建会话前的岗位候选（纯展示；登录后由 meta.rbac.roles 整体替换）。

   第三列是**部门**：级联选择器在 meta 到位前也要能按部门过滤，
   否则"先选部门"这一步会把岗位清空（meta 拿不到，因为要令牌）。
   这份数据由 `security/case_rbac.py` 的矩阵导出，与服务端同源。
   */
  const FALLBACK_ROLES = [
    ["admin_hr", "行政人事", "admin"],
    ["customer_service", "客户服务", "service"],
    ["ehs", "EHS 安全环保", "ehs"],
    ["equipment", "设备工程", "equipment"],
    ["equipment_manager", "设备经理", "equipment"],
    ["finance", "财务", "finance"],
    ["finance_manager", "财务经理", "finance"],
    ["gm", "总经理", "management"],
    ["ie_engineer", "IE 工业工程", "rd_engineering"],
    ["maintenance_tech", "设备维保技师", "equipment"],
    ["manufacturing", "制造", "manufacturing"],
    ["material_control", "物控", "supply_chain"],
    ["me_engineer", "结构工程师", "rd_engineering"],
    ["npi_engineer", "NPI 导入工程师", "rd_engineering"],
    ["planning", "计划", "supply_chain"],
    ["pqe", "PQE", "quality"],
    ["procurement", "采购", "procurement"],
    ["procurement_manager", "采购经理", "procurement"],
    ["product_engineering", "产品/工艺工程", "rd_engineering"],
    ["project_manager", "项目经理", "pm"],
    ["qa", "QA", "quality"],
    ["qa_manager", "QA经理", "quality"],
    ["quality_director", "质量总监", "quality"],
    ["rd_engineering", "工程", "rd_engineering"],
    ["rd_manager", "研发经理", "rd_engineering"],
    ["rd_test_engineer", "研发测试工程师", "rd_engineering"],
    ["sales", "业务", "sales"],
    ["sqe", "SQE", "quality"],
    ["supply_chain_manager", "供应链经理", "supply_chain"],
    ["test_engineer", "工程测试工程师", "rd_engineering"],
    ["warehouse", "仓库", "warehouse"],
    ["warehouse_manager", "仓储经理", "warehouse"],
  ];

  /* ================= 存储 ================= */

  function readStore(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
  }

  function writeStore(key, value) {
    try {
      if (value === null || value === undefined || value === "") localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (err) { /* 隐私模式：降级为内存态，不影响会话 */ }
  }

  function readIdentity() {
    const raw = readStore(IDENTITY_KEY);
    if (!raw) return Object.assign({}, DEFAULT_IDENTITY);
    try { return Object.assign({}, DEFAULT_IDENTITY, JSON.parse(raw) || {}); }
    catch (err) { return Object.assign({}, DEFAULT_IDENTITY); }
  }

  /* ================= 网络 ================= */

  /**
   * 案例域专用请求。
   *
   * 与 app.js 的 `api._post` 有三点不同，都是刻意的：
   * 1. 用 `caseToken` 而非 `state.token`（tv=1 的门户令牌在案例域会被 401）；
   * 2. 401 **不**调用 `handleAuthExpired`（那会把用户踢回登录页，破坏"换岗看看"的沉浸感）；
   * 3. 把 FastAPI 的 `{detail:{code,message}}` 摊平成 `Error{status, code}`，
   *    便于本屏用 `CASE_*` 错误码做精确文案，而不是"请求失败"。
   */
  async function caseFetch(base, path, options) {
    const opts = options || {};
    const headers = {};
    if (opts.token) headers.Authorization = "Bearer " + opts.token;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.method = opts.method || "POST";
      opts.body = JSON.stringify(opts.body);
    }
    opts.headers = headers;
    const resp = await fetch(base + path, opts);
    const raw = await resp.text();
    let payload = {};
    if (raw) {
      try { payload = JSON.parse(raw); } catch (err) {
        throw new Error("响应不是 JSON（HTTP " + resp.status + "）：" + raw.slice(0, 120));
      }
    }
    if (!resp.ok || payload.code !== 0) {
      const detail = payload.detail;
      let message = payload.message || ("HTTP " + resp.status);
      let code = "";
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail === "object") {
        message = detail.message || message;
        code = detail.code || "";
      }
      const err = new Error(message);
      err.status = resp.status;
      err.code = code;
      throw err;
    }
    return payload.data;
  }

  /* ================= 渲染小件 ================= */

  function statusBadge(UI, status, label) {
    const meta = STATUS_META[status] || { label: status || "未知", cls: "badge-gray" };
    return UI.badge(label || meta.label, meta.cls, true);
  }

  /** 部门清单（来自 meta.rbac.departments；登录前用 ZH.dept 兜底）。 */
  function deptOptions(UI, meta, selected) {
    const depts = (meta && meta.rbac && meta.rbac.departments) || [];
    const keys = depts.length ? depts.map(function (d) { return d.key; })
      : Object.keys(ZH.dept);
    const labels = {};
    depts.forEach(function (d) { labels[d.key] = d.label; });
    return keys.map(function (key) {
      return '<option value="' + UI.esc(key) + '"'
        + (key === selected ? " selected" : "") + ">"
        + UI.esc(labels[key] || ZH.dept[key] || key) + "</option>";
    }).join("");
  }

  /** 岗位下拉：**按部门过滤**（级联第二级）；英文 key 只做括号补充。 */
  function roleOptions(UI, meta, selected, dept) {
    const roles = (meta && meta.rbac && meta.rbac.roles) || [];
    const list = roles.length ? roles : FALLBACK_ROLES.map(function (row) {
      return { key: row[0], name: row[1], dept: row[2] };
    });
    const scoped = dept ? list.filter(function (r) { return r.dept === dept; }) : list;
    if (!scoped.length) {
      return '<option value="">该部门下暂无岗位</option>';
    }
    return scoped.map(function (r) {
      return '<option value="' + UI.esc(r.key) + '"'
        + (r.key === selected ? " selected" : "") + ">"
        + UI.esc(r.name || r.key) + "（" + UI.esc(r.key) + "）</option>";
    }).join("");
  }

  /* ================= 状态 ================= */

  function initialState() {
    return {
      identity: readIdentity(),
      token: readStore(TOKEN_KEY) || "",
      session: null,            // 建会话时返回的 principal（含 token 与 tv/pv）
      meta: null, metaError: "", metaLoading: false,
      authError: "",            // 401/403 的专门文案（不弹 toast 了事）
      tab: "ask",        // 主视觉是问答（布局裁定：问答在上、检索在下）
      query: "锡膏印刷偏移 虚焊 连锡",
      action: "query",
      k: 20,
      category: "all",
      searching: false,
      result: null,
      error: null,
      showWhy: false,
      detail: null,
      cite: null,
      asking: false,
      answer: null,
      compare: null, compareA: "qa", compareB: "equipment",
      compareDeptA: null, compareDeptB: null, comparing: false,
      audit: null,
      ingestForm: null, ingestResult: null,
      // 跨部门访问申请
      myRequests: null, deptRequests: null, accessLoading: false,
      accessForm: null, accessResult: null,
      // 顶部统计卡片（/cases/stats）：与检索同源；失败安静降级，不刷黄条
      stats: null, statsLoading: false, statsError: "",
      // 演示说明黄条是否收起（收起后保留一行入口，信息不丢）
      warnCollapsed: false,
      // 演示专用自动审批：执行弹窗状态 + 每个案例的路由信息（供"再演一次"）
      autoFlow: null, autoRoutes: {},
    };
  }

  /* ================= 主渲染 ================= */

  function render(root, ctx) {
    const UI = ctx.UI;
    const api = ctx.api;
    const state = ctx.state;
    state.screenState = state.screenState || {};
    const S = state.screenState.cases || (state.screenState.cases = initialState());

    if (!api.online()) {
      root.innerHTML = '<div class="case-offline">'
        + '<div class="case-offline-ico">制造案例检索需要后端</div>'
        + "<p>本屏要证明的是<b>授权在召回前生效</b>：同一句话，不同岗位拿到不同结果，越权一律查不到。"
        + "这类结论没法用前端假数据演示——离线时本屏不做任何降级。</p>"
        + '<p class="case-hint">启动方式：<code>python -m uvicorn mfg_agents.v2.demo.api:app --port 8004</code></p>'
        + "</div>";
      return;
    }

    const base = api.base();
    const hasToken = !!S.token;

    root.innerHTML = ''
      + '<div class="case-root">'
      + identityBar(UI, S)
      + (S.authError ? authErrorBar(UI, S) : "")
      + statsBar(UI, S)
      + '  <div class="case-body">'
      + '    <aside class="case-side">' + sidePanel(UI, S) + "</aside>"
      + '    <section class="case-main">' + mainPanel(UI, S) + "</section>"
      + "  </div>"
      + (S.detail ? detailOverlay(UI, S) : "")
      + (S.ingestForm ? ingestOverlay(UI, S) : "")
      + (S.autoFlow ? accessAutoOverlay(UI, S) : "")
      + "</div>";

    bind(root, ctx, S, base);
    if (!S.meta && hasToken && !S.metaLoading && !S.metaError) loadMeta(root, ctx, S, base);
  }

  /** 身份条：demo 的核心说服点——令牌是可见的，四类前缀一眼看到。 */
  function identityBar(UI, S) {
    const id = S.identity;
    const etypeOptions = ["employee", "outsource", "temp", "supplier", "customer"]
      .map(function (t) {
        return '<option value="' + t + '"' + (id.employment_type === t ? " selected" : "")
          + ">" + (EMPLOYMENT_LABEL[t] || t) + "</option>";
      }).join("");
    const rankOptions = [1, 2, 3].map(function (n) {
      return '<option value="' + n + '"' + (Number(id.max_classification_rank) === n ? " selected" : "")
        + ">≤ L" + n + "</option>";
    }).join("");

    return '<header class="case-identity">'
      + '<div class="case-identity-left">'
      + '<div class="case-id-row">'
      + '<label>部门<select id="case-dept">' + deptOptions(UI, S.meta, id.dept) + "</select></label>"
      + '<label>岗位<select id="case-role">'
      + roleOptions(UI, S.meta, id.role, id.dept) + "</select></label>"
      + '<label>工厂<input id="case-plant" value="' + UI.esc(id.plant) + '" placeholder="P1"></label>'
      + '<label>用工类型<select id="case-etype">' + etypeOptions + "</select></label>"
      + '<label>可见密级<select id="case-rank">' + rankOptions + "</select></label>"
      + '<button class="case-btn" id="case-session">'
      + (S.token ? "重建案例会话" : "建立案例会话") + "</button>"
      + (S.token ? '<button class="case-btn case-btn-ghost" id="case-logout">退出</button>' : "")
      + "</div>"
      + '<div class="case-id-note">演示态：在这里选择岗位即可建立会话；'
      + '生产环境由公司统一登录系统注入身份，此选岗接口会关闭。'
      + "岗位名称写错不会被放过——服务端按权限注册表逐个校验，未识别直接拒绝。</div>"
      + demoWarnings(UI, S)
      + "</div>"
      + principalPanel(UI, S)
      + "</header>";
  }

  /**
   * 演示态说明（演示模式 / 未配置密钥等）——必须"显式"，但**可以收起**。
   *
   * 优化点：原来每次开屏都顶着两三行黄条，现场截图与讲解都碍事，且文案里
   * 全是环境变量名，外行看不懂。现在：文案由后端说人话（变量名退到括号里
   * 只给运维看）；整块可收起成一行"演示说明（N 条）"，需要细看再展开——
   * 信息一条不丢，噪音可控。
   */
  function demoWarnings(UI, S) {
    const list = (S.meta && S.meta.demo_warnings) || [];
    if (!list.length) return "";
    const toggle = '<button class="case-warn-toggle" id="case-warn-toggle" type="button">'
      + (S.warnCollapsed ? "演示说明（" + list.length + " 条）· 展开" : "收起")
      + "</button>";
    if (S.warnCollapsed) {
      return '<div class="case-warn-bar is-collapsed">' + toggle + "</div>";
    }
    return '<div class="case-warn-bar">' + toggle
      + list.map(function (w) {
        return '<div class="case-warn-line"><b>演示说明</b>' + UI.esc(w) + "</div>";
      }).join("")
      + "</div>";
  }

  function principalPanel(UI, S) {
    const p = (S.meta && S.meta.principal) || (S.session && S.session.principal) || null;
    if (!S.token) {
      return '<div class="case-principal case-principal-empty">'
        + '<div class="case-principal-name">尚未建立案例会话</div>'
        + '<div class="case-principal-meta">选好岗位后点「建立案例会话」，'
        + "即可看到服务端签发的维度令牌。</div></div>";
    }
    if (!p) return '<div class="case-principal case-principal-empty">读取主体中…</div>';
    const rawTokens = p.principal_tokens || [];
    const tokens = tokensHuman(rawTokens, S.meta).map(function (t) {
      return '<code class="case-token">' + UI.esc(t) + "</code>";
    }).join("");
    const techTokens = rawTokens.length
      ? '<details class="case-tech"><summary>技术详情（原始维度令牌）</summary><code>'
        + UI.esc(rawTokens.join("\n")) + "</code></details>" : "";
    return '<div class="case-principal">'
      + '<div class="case-principal-name">' + UI.esc(p.user_id || "-")
      + "<i>" + UI.esc((p.roles || []).join(",") || "-") + "</i>"
      + '<span class="case-tv">tv=' + UI.esc(String(p.token_version)) + " · pv="
      + UI.esc(String(p.principal_version)) + "</span></div>"
      + '<div class="case-principal-meta">租户 ' + UI.esc(p.tenant || "-")
      + " · 部门 " + UI.esc(p.dept || "-")
      + " · 工厂 " + UI.esc(p.plant || "-")
      + " · " + UI.esc(EMPLOYMENT_LABEL[p.employment_type] || p.employment_type || "-")
      + " · 可见密级 ≤ L" + UI.esc(String(p.max_classification_rank))
      + "</div>"
      + '<div class="case-token-row">' + (tokens
        || '<span class="case-hint">无维度令牌（fail-closed：查不到任何带业务 ACL 的案例）</span>')
      + "</div>" + techTokens + "</div>";
  }

  function authErrorBar(UI, S) {
    return '<div class="case-auth-error">'
      + '<b>' + UI.esc(S.authError) + "</b></div>";
  }

  /* ================= 顶部统计卡片 ================= */

  /** 子类兜底中文名：种子数据里有 10 类子类，meta 受控词表只登记了 8 类——
      没登记的（equipment_fault / design_defect / supplier_quality）不能让中文
      界面里冒出英文 key。词表补了这里的映射即以词表为准。 */
  const SUBCATEGORY_LABEL = {
    equipment_fault: "设备异常",
    design_defect: "设计缺陷",
    supplier_quality: "供应商质量",
  };

  /** 分类/子类的展示名：优先取 meta 受控词表，其次兜底映射，最后退回 key 本身。 */
  function catLabel(meta, key) {
    const cats = (meta && meta.categories) || [];
    for (let i = 0; i < cats.length; i++) {
      if (cats[i].key === key) return cats[i].label || key;
      const subs = cats[i].subcategories || [];
      for (let j = 0; j < subs.length; j++) {
        if (subs[j].key === key) return subs[j].label || key;
      }
    }
    return SUBCATEGORY_LABEL[key] || key;
  }

  /**
   * 顶部统计卡片：可见案例 / 受限可申请 / 我的申请·待审 / 待我审批 + 三类分布。
   *
   * 纪律（"加载失败优化"的落地处）：
   * - 统计失败**安静降级**（一行小字 + 说明不影响检索审批），不弹 toast、不刷
   *   黄条——它不是关键路径，没资格让整页变红；
   * - 数字全部来自服务端 /cases/stats（与检索同一套过滤 + 二次授权），
   *   前端不做任何本地估算——"卡片数字 vs 检索结果"现场必须对得上；
   * - 卡片可点击直达能处理的区域（受限 → 侧栏清单；申请 → 申请页），
   *   让"看到数字"变成"一步到位"。
   */
  function statsBar(UI, S) {
    if (!S.token) return "";
    if (S.statsLoading) {
      return '<div class="case-stats is-loading">' + UI.spinner() + " 统计计算中…</div>";
    }
    if (!S.stats) {
      return S.statsError
        ? '<div class="case-stats case-stats-fail">统计暂时没算出来（'
          + UI.esc(S.statsError) + "），不影响检索与审批功能，可点右上角刷新重试。</div>"
        : "";
    }
    const st = S.stats;
    const pendingMine = (st.my_requests && st.my_requests.pending) || 0;
    const cards = [
      { n: st.visible_total, label: "可见案例", jump: "" },
      { n: st.restricted_total, label: "受限可申请", jump: "restricted", warn: true },
      { n: pendingMine, label: "我的申请·待审", jump: "mine" },
      { n: st.pending_for_me || 0, label: "待我审批", jump: "dept" },
    ];
    const cardHtml = cards.map(function (c) {
      const jump = c.jump
        ? ' data-stats-jump="' + c.jump + '" role="button" tabindex="0"'
        : "";
      return '<div class="case-stat' + (c.warn && c.n ? " is-warn" : "") + '"' + jump + ">"
        + "<b>" + c.n + "</b><span>" + UI.esc(c.label) + "</span></div>";
    }).join("");
    // 分布行：子类（按数量 top 6 + 其他）/ 密级（固定序）/ 状态（固定序）
    const distRow = function (title, counts, order, labelOf) {
      const lf = labelOf || function (k) { return catLabel(S.meta, k); };
      const keys = order
        || Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
      const shown = keys.filter(function (k) { return counts[k]; });
      if (!shown.length) return "";
      // 固定序之外的 key（新密级档 / 新状态 / 词表未登记的子类）补在后面：
      // 宁可多显示一档，也不能让"各档加起来 ≠ 可见总数"。
      const extra = order
        ? Object.keys(counts).filter(function (k) {
            return counts[k] && order.indexOf(k) < 0;
          }).sort(function (a, b) { return counts[b] - counts[a]; })
        : [];
      const all = shown.concat(extra);
      const parts = all.slice(0, 6).map(function (k) {
        return '<span class="case-dist-chip"><i>' + UI.esc(lf(k)) + "</i>"
          + UI.esc(counts[k]) + "</span>";
      });
      const rest = all.length - parts.length;
      if (rest > 0) {
        parts.push('<span class="case-dist-chip is-rest"><i>其他</i>' + rest + " 类</span>");
      }
      return '<div class="case-dist-row"><em>' + UI.esc(title) + "</em>" + parts.join("") + "</div>";
    };
    const trunc = st.restricted_truncated
      ? '<span class="case-dist-note">（数据量较大，受限数未逐个点清）</span>' : "";
    return '<div class="case-stats">'
      + '<button class="case-stats-refresh" id="case-stats-refresh" type="button" '
      + ' title="重新统计">' + UI.icon("refresh") + "</button>"
      + '<div class="case-stats-cards">' + cardHtml + "</div>"
      + '<div class="case-stats-dist">'
      + distRow("按类型", st.by_subcategory || {}, null)
      + distRow("按密级", st.by_rank || {}, ["L0", "L1", "L2", "L3"])
      + distRow("按状态", st.by_status || {},
                ["active", "review", "deprecated", "withdrawn", "archived"],
                function (k) { return (STATUS_META[k] && STATUS_META[k].label) || k; })
      + trunc
      + "</div></div>";
  }

  /**
   * 加载统计卡片。失败**不打扰用户**：只记 statsError，卡片区一行小字带过。
   * （对比 meta：它失败要拦路 + 重试按钮，因为挡着所有功能；统计失败只少
   * 一块信息，检索、申请、审批照常能用。）
   */
  async function loadStats(root, ctx, S, base) {
    if (!S.token || S.statsLoading) return;
    S.statsLoading = true;
    render(root, ctx);
    try {
      S.stats = await caseFetch(base, "/api/v2/cases/stats", { token: S.token });
      S.statsError = "";
    } catch (err) {
      S.stats = null;
      if (err && err.status === 401) {
        S.statsError = "案例会话已失效";
      } else {
        const f = ctx.UI.friendlyError(err);
        S.statsError = f.text;
      }
    }
    S.statsLoading = false;
    render(root, ctx);
  }

  /* ================= 左栏 ================= */

  function sidePanel(UI, S) {
    if (!S.token) {
      return '<div class="case-side-empty">请先建立案例会话。<br>'
        + '<span class="case-hint">同一句话，换个岗位结果不同——这就是本屏要证明的事。</span></div>';
    }
    if (S.metaError) {
      // 加载失败优化：主提示说人话 + 给重试入口；原始错误进折叠区，
      // 不再把 "Failed to fetch" / "xxx is not defined" 直接甩给用户。
      const f = UI.friendlyError(S.metaError);
      return '<div class="case-side-empty">'
        + '<div class="case-load-fail-title">' + UI.icon("warn", 28) + " 页面信息加载失败</div>"
        + '<div class="case-load-fail-text">' + UI.esc(f.text) + "</div>"
        + '<button class="case-btn case-btn-wide" id="case-meta-retry">重新加载</button>'
        + (f.tech
          ? '<details class="case-tech"><summary>技术详情（便于反馈）</summary><code>'
            + UI.esc(f.tech) + "</code></details>"
          : "")
        + "</div>";
    }
    if (!S.meta) return '<div class="case-side-empty">' + UI.spinner() + " 加载元信息…</div>";

    // 本体增强收益由问答结果携带（后端 case_qa._ontology_gain），检索态无此数据。
    const gain = (S.result && S.result.ontology_gain) || null;

    // 本次结果分类分布：按 hits 的 category 聚合（无检索结果时不渲染该块）。
    // 注意：此变量曾被引用但漏定义，侧栏整块 render 抛 ReferenceError，
    // 页面停在旧 HTML——这类"引用未定义变量"的崩溃必须 fail-safe，
    // 因为 render 一次失败 = 整屏不再刷新（2026-10-18 实测修复）。
    const distRows = (function () {
      const hits = (S.result && S.result.hits) || [];
      if (!hits.length) return "";
      const counts = {};
      hits.forEach(function (h) {
        const key = h.category || "未分类";
        counts[key] = (counts[key] || 0) + 1;
      });
      return Object.keys(counts).map(function (key) {
        return '<div class="case-act-row"><span>' + UI.esc(key)
          + "</span><b>" + counts[key] + " 条</b></div>";
      }).join("");
    })();

    const p = S.meta.principal || {};
    const mine = ((S.meta.rbac && S.meta.rbac.roles) || []).filter(function (r) {
      return (p.roles || []).indexOf(r.key) >= 0;
    })[0];
    const myActions = (mine && mine.actions) || [];

    const actionChips = myActions.length
      ? myActions.map(function (a) {
        return '<span class="case-chip case-chip-on">' + UI.esc(ACTION_LABEL[a.replace(/^case\./, "")] || a) + "</span>";
      }).join("")
      : '<span class="case-chip">无</span>';

    const allActions = ((S.meta.rbac && S.meta.rbac.actions) || []).map(function (a) {
      const on = myActions.indexOf(a.key) >= 0;
      return '<div class="case-act-row' + (on ? "" : " case-act-off") + '">'
        + "<span>" + UI.esc(a.label || a.key) + "</span><b>"
        + (a.roles || []).length + " 个角色</b></div>";
    }).join("");

    const VOCAB_LABEL = { defect_mode: "缺陷模式", root_cause: "根因", countermeasure: "对策" };
    const vocab = S.meta.ontology || {};
    const vocabRows = Object.keys(vocab).map(function (key) {
      return '<div class="case-act-row"><span>' + UI.esc(VOCAB_LABEL[key] || key)
        + "</span><b>" + (vocab[key] || []).length + " 条</b></div>";
    }).join("");

    const canIngest = myActions.indexOf("case.ingest") >= 0;

    return '<div class="case-side-block">'
      + '<div class="case-side-title">本体与资产</div>'
      + '<div class="case-hint">受控词表把"同一现象的不同叫法"归一，'
      + "检索因此更准、口径更一致。</div>"
      + (vocabRows || '<span class="case-hint">-</span>')
      + (gain ? '<div class="case-gain"><b>本体增强带来的差别</b>'
        + UI.esc(gain.summary || "") + "</div>" : "")
      + (distRows ? '<div class="case-side-title">本次结果分类分布</div>'
        + '<div class="case-dist">' + distRows + "</div>" : "")
      + '<div class="case-side-title">我的动作</div>'
      + '<div class="case-chip-row">' + actionChips + "</div>"
      + '<div class="case-side-title">动作门禁（持有角色数）</div>'
      + (allActions || '<span class="case-hint">-</span>')
      + '<div class="case-side-title">治理</div>'
      + '<button class="case-btn case-btn-wide" id="case-audit">校验审计链</button>'
      + (canIngest
        ? '<button class="case-btn case-btn-wide case-btn-ghost" id="case-ingest-open">录入案例（进入待审）</button>'
        : '<div class="case-hint">当前岗位无 <code>case.ingest</code>：连入口都看不到（前端隐藏只是体验，服务端同样 403）。</div>')
      + "</div>";
  }

  /* ================= 主区 ================= */

  function mainPanel(UI, S) {
    if (!S.token) {
      return '<div class="case-gate"><h3>建立案例会话后开始</h3>'
        + "<p>案例检索不看你是谁，看你的<b>维度令牌</b>：角色、部门、工厂、用工类型、可见密级。"
        + "选一个岗位，服务端把它编译成过滤条件，再下推到向量库——过滤发生在召回之前。</p>"
        + '<p class="case-hint">越权检索不会返回"空结果 + 提示无权限"，而是根本进不了候选集。</p></div>';
    }
    // 布局裁定：**问答在上、检索在下**。问答是主视觉（本体增强 Agentic RAG 的
    // 卖点在这里），检索是次级工具。取消平铺页签，避免四个入口抢注意力。
    const sideLinks = '<div class="case-side-links">'
      + '<button class="case-link' + (S.tab === "compare" ? " is-active" : "")
      + '" data-tab="compare">岗位对比</button>'
      + '<button class="case-link' + (S.tab === "audit" ? " is-active" : "")
      + '" data-tab="audit">审计链</button>'
      + '<button class="case-link' + (S.tab === "access" ? " is-active" : "")
      + '" data-tab="access">访问申请待办</button>'
      + "</div>";

    const askBlock = '<section class="case-block case-block-ask">'
      + '<h3 class="case-block-title">智能问答'
      + '<span class="case-hint">本体增强 Agentic RAG：术语归一 → 同义扩展 → 三元组检索 → 权限过滤 → 引用校验</span>'
      + "</h3>" + askWithTrace(UI, S, S.answer) + "</section>";

    const searchOpen = S.tab !== "search" && !S.result;
    const searchBlock = '<section class="case-block case-block-search">'
      + '<details class="case-block-fold"' + (searchOpen ? "" : " open") + ">"
      + "<summary>案例检索（按关键词查历史案例）</summary>"
      + searchPane(UI, S) + "</details></section>";

    let body = askBlock + searchBlock + sideLinks;
    if (S.tab === "compare") body += '<section class="case-block">' + comparePane(UI, S) + "</section>";
    else if (S.tab === "audit") body += '<section class="case-block">' + auditPane(UI, S) + "</section>";
    else if (S.tab === "access") body += '<section class="case-block">' + accessPane(UI, S) + "</section>";
    return body;
  }

  /* ================= 访问申请 ================= */

  function accessPane(UI, S) {
    if (S.tab !== "access") return "";
    if (!S.token) return '<div class="case-hint case-pad">请先建立案例会话。</div>';
    if (S.accessLoading) return '<div class="case-loading">' + UI.spinner() + " 加载申请…</div>";
    const mine = S.myRequests || [];
    const todo = S.deptRequests || [];
    const row = function (r, approver) {
      // 我提交的申请：pending 且服务端预检过自动审批可用 → 给"再演一次"入口
      // （弹窗被中途关掉/没看清时，演示现场不用重新申请）。
      const canReplay = !approver && r.status === "pending"
        && S.autoRoutes && S.autoRoutes[r.case_id];
      return '<tr><td>' + UI.esc(r.case_id || "") + "</td>"
        + "<td>" + UI.esc((r.reason || "").slice(0, 24)) + "</td>"
        + "<td>" + UI.esc(ZH.requestStatus[r.status] || r.status || "") + "</td>"
        + "<td>" + UI.esc((r.decision_reason || "").slice(0, 20)) + "</td>"
        + "<td>" + (approver && r.status === "pending"
          ? '<button class="case-mini" data-act="access-approve" data-rid="'
            + UI.esc(r.request_id) + '">批准</button>'
          + '<button class="case-mini" data-act="access-reject" data-rid="'
            + UI.esc(r.request_id) + '">驳回</button>'
          : (canReplay
            ? '<button class="case-mini case-mini-key" data-act="auto-run" data-rid="'
              + UI.esc(r.request_id) + '" data-id="' + UI.esc(r.case_id)
              + '">模拟审批（演示）</button>'
            : '<span class="case-hint">-</span>')) + "</td></tr>";
    };
    const table = function (title, rows, approver) {
      return "<h4>" + title + "</h4>"
        + (rows.length
          ? '<table class="case-table"><thead><tr><th>案例</th><th>申请理由</th>'
            + "<th>状态</th><th>审批意见</th><th>操作</th></tr></thead><tbody>"
            + rows.map(function (r) { return row(r, approver); }).join("")
            + "</tbody></table>"
          : '<div class="case-hint">暂无记录</div>');
    };
    return '<div class="case-pad">'
      + '<div class="case-hint">跨部门案例可发起访问申请；批准后由服务端写入授权'
      + "（带有效期，到期自动回收）。<b>演示模式</b>：提交后弹窗自动模拟职能部门审批通过"
      + "（仅演示，审计链以 .demo 后缀标记）；正式环境由职能部门负责人在本页「待我审批」中"
      + "裁决，Agent 只负责推送与留痕，不代替人审批。</div>"
      + table("我提交的申请", mine, false)
      + table("待我审批（本部门收到的申请）", todo, true)
      + "</div>";
  }

  /** 受限案例（"有但看不到"）清单 + 申请入口。 */
  function restrictedPane(UI, S, r) {
    const list = (r && r.restricted) || [];
    if (!list.length) return "";
    const applied = S.myRequests || [];
    /** 该案例的**最近一条**申请（同一条案例可能先撤回再申请）。 */
    const statusOf = function (caseId) {
      let latest = null;
      for (let i = 0; i < applied.length; i++) {
        const item = applied[i];
        if (item.case_id !== caseId) continue;
        if (!latest || String(item.created_at || "") > String(latest.created_at || "")) {
          latest = item;
        }
      }
      return latest;
    };
    const rows = list.map(function (item) {
      const req = statusOf(item.case_id);
      const applyBtn = item.requestable
        ? '<button class="case-mini case-mini-key" data-act="access-open" data-id="'
          + UI.esc(item.case_id) + '">申请访问</button>'
        : '<span class="case-hint">不可申请</span>';
      let btn;
      if (req && req.status === "pending") {
        btn = '<span class="case-chip case-chip-on">已推送，等待审批</span>';
      } else if (req && req.status === "approved") {
        btn = '<span class="case-chip case-chip-done">已批准，授权已生效</span>';
      } else if (req && req.status === "expired") {
        btn = applyBtn;   // 到期已回收 → 可以重新申请
      } else {
        btn = applyBtn;   // 无记录 / 已驳回 / 已撤回 → 都可重新申请
      }
      return "<tr><td>" + UI.esc(item.title || item.case_id) + "</td>"
        + '<td>' + UI.esc(item.reason || "") + "</td><td>" + btn + "</td></tr>";
    }).join("");
    return '<div class="case-block case-restricted"><h4>其他职能域的案例（可见但不可读）</h4>'
      + '<div class="case-hint">这些案例确实存在，但不在你的权限范围内。需要时可发起访问申请，'
      + "由职能部门审批后授权（演示模式下提交后将自动弹窗模拟审批通过）。</div>"
      + '<table class="case-table"><thead><tr><th>案例</th><th>说明</th><th>操作</th></tr></thead>'
      + "<tbody>" + rows + "</tbody></table>"
      + (S.accessForm ? accessForm(UI, S) : "")
      + "</div>";
  }

  function accessForm(UI, S) {
    return '<div class="case-access-form">'
      + "<h4>申请访问：" + UI.esc(S.accessForm.case_id || "") + "</h4>"
      + '<p class="case-hint">请说明用途（至少 10 个字），审批人需要判断是否合理。</p>'
      + '<textarea id="case-access-reason" class="case-textarea" rows="3" '
      + 'placeholder="例如：需要引用该案例做设备异常分析">' + UI.esc(S.accessForm.reason || "")
      + "</textarea>"
      + '<div class="case-access-act">'
      + '<button class="case-btn" data-act="access-submit" data-id="'
      + UI.esc(S.accessForm.case_id) + '">提交申请并推送</button>'
      + '<button class="case-btn case-btn-ghost" data-act="access-close">取消</button>'
      + "</div></div>";
  }

  /* ================= 演示专用：自动审批执行弹窗 ================= */

  /** 执行步骤（固定五步；前四步演示节奏，第五步对应服务端 demo-approve 调用）。 */
  function autoFlowSteps(flow) {
    const dept = flow.dept || "职能部门";
    return [
      "智能 Agent 已受理访问申请（" + (flow.case_title || flow.case_id || "") + "）",
      "解析申请理由与案例归属…",
      "推断职能部门：" + dept + "（依据：" + (flow.basis || "案例归属信息") + "）",
      "已推送至 " + dept + " 审批通道，职能部门审批中…",
      dept + "审批通过，正在写入授权…",
    ];
  }

  /**
   * 自动审批弹窗（**仅演示**，见 services/case_auto_approve.py）。
   *
   * 展示纪律：弹窗里的每一行都对应服务端真实发生的一步（受理/路由在
   * request-access 时已落审计）；最后一步的"审批通过"是 demo-approve 的返回，
   * 授权写入与人工审批走同一条 decide()。失败时**不遮掩**：原文展示错误码与
   * 原因，并说明已转为人工待审批。
   */
  function accessAutoOverlay(UI, S) {
    const flow = S.autoFlow;
    if (!flow) return "";
    const steps = autoFlowSteps(flow);
    const rows = steps.map(function (label, i) {
      let state = "wait";
      if (flow.status === "failed" && i === 4) state = "fail";
      else if (flow.status === "approved" || i < flow.step) state = "done";
      else if (i === flow.step && flow.status === "running") state = "run";
      const ico = state === "done" ? "✓" : state === "fail" ? "✕" : state === "run" ? "•" : "";
      return '<div class="case-auto-step is-' + state + '">'
        + '<span class="case-auto-ico">' + ico + "</span>"
        + "<span>" + UI.esc(label) + "</span></div>";
    }).join("");

    let result = "";
    if (flow.status === "approved") {
      const expiry = String(flow.expires || "").slice(0, 10);
      result = '<div class="case-auto-result is-ok">'
        + "<b>" + UI.esc(flow.dept) + "已审批通过</b>"
        + "<div>授权已写入服务端（个人特批：仅你本人可见"
        + (expiry ? "，有效期至 " + UI.esc(expiry) + "，到期自动回收" : "") + "）。</div>"
        + "<div>关闭后重新检索即可看到该案例——过滤条件没有变，变的是候选集。</div></div>";
    } else if (flow.status === "failed") {
      result = '<div class="case-auto-result is-err">'
        + "<b>演示审批未能完成</b>"
        + "<div>" + UI.esc(flow.error || "未知原因") + "</div>"
        + "<div>已转为人工待审批：请到「访问申请待办」由职能部门负责人处理。</div></div>";
    }

    const bar = flow.status === "approved"
      ? '<button class="case-btn" data-act="auto-close">完成</button>'
      : (flow.status === "failed"
        ? '<button class="case-btn case-btn-ghost" data-act="auto-close">知道了</button>'
        : '<span class="case-hint">执行中，请稍候…</span>');

    return '<div class="case-modal"><div class="case-modal-card case-auto-card">'
      + '<div class="case-modal-head"><b>智能 Agent · 跨部门访问审批</b>'
      + UI.badge("演示", "badge-amber", true) + "</div>"
      + '<div class="case-auto-steps">' + rows + "</div>"
      + result
      + '<div class="case-auto-bar">' + bar + "</div>"
      + '<div class="case-auto-note">此弹窗为<b>演示专用</b>：模拟职能部门审批回调，'
      + "审批动作由演示代批身份完成，审计链以 .demo 后缀显式标记，"
      + "授权写入与人工审批走同一条服务端路径。生产环境由真实审批流实现同一个 "
      + "AccessApprover 接口回调，服务端零改动。</div>"
      + "</div></div>";
  }

  function searchPane(UI, S) {
    const r = S.result;
    const catOptions = CATEGORY_FILTERS.map(function (pair) {
      return '<option value="' + pair[0] + '"' + (S.category === pair[0] ? " selected" : "")
        + ">" + pair[1] + "</option>";
    }).join("");
    const actionOptions = RETRIEVAL_ACTIONS.map(function (a) {
      return '<option value="' + a + '"' + (S.action === a ? " selected" : "")
        + ">动作：" + ACTION_LABEL[a] + "</option>";
    }).join("");

    return '<div class="case-searchbar">'
      + '<input id="case-q" class="case-input" value="' + UI.esc(S.query) + '" '
      + 'placeholder="试试：锡膏印刷偏移 / 虚焊 / 供应商五金件尺寸超差" autocomplete="off">'
      + '<select id="case-action" class="case-select">' + actionOptions + "</select>"
      + '<select id="case-k" class="case-select">'
      + [5, 10, 20, 50].map(function (n) {
        return '<option value="' + n + '"' + (S.k === n ? " selected" : "") + ">前 " + n + " 条</option>";
      }).join("") + "</select>"
      + '<select id="case-cat" class="case-select" title="仅过滤当前结果，不参与召回与授权">' + catOptions + "</select>"
      + '<button class="case-btn" id="case-search">检索</button>'
      + "</div>"
      + '<div class="case-policy-note">过滤条件由服务端按你的身份编译并下推；'
      + "<b>本页没有、也不接受自定义过滤条件</b>（请求模型里没有 filter 字段，传了会被忽略）。"
      + ' 分类下拉只过滤<b>已返回的结果</b>，不改变召回，也不改变授权。</div>'
      + degradeBar(UI, r || {})
      + noticeBar(UI, r || {})
      + whyPanel(UI, S, r)
      + '<div class="case-hits">' + hitList(UI, S, r) + "</div>"
      + restrictedPane(UI, S, r);
  }

  function degradeBar(UI, data) {
    if (!data.degraded) return "";
    return '<div class="case-degrade"><b>降级告警</b>'
      + "<span>" + UI.esc(data.notice || "向量索引不可用或部分能力降级，结果可能不完整") + "</span></div>";
  }

  /** hidden_count 与 notice：产品差异化证据，必须显著、不折叠。 */
  function noticeBar(UI, data) {
    // 「有但看不到」有两个来源，都要在同一条提示里讲清楚：
    //   1. restricted —— 其他职能域的案例（总是算，可发起访问申请）；
    //   2. hidden_count —— 候选不足时额外被过滤掉的条数（受"只在不足时计数"保护）。
    // 语料上百例后内部岗位几乎总能取满 k，只靠 hidden_count 演示会"什么都看不到"。
    const restricted = (data && data.restricted) || [];
    if (!data || (!data.notice && !data.hidden_count && !restricted.length)) return "";
    const tone = (data.hidden_count > 0 || restricted.length) ? "warn" : "info";
    const lines = [];
    if (restricted.length) {
      lines.push("<b>" + restricted.length + "</b> 条案例属于其他职能域，"
        + "可见但不可读，可发起访问申请");
    }
    if (data.hidden_count > 0) {
      // 单位是**案例数**（服务端按 case_id 去重）。写成"条"会让人以为是 chunk 数，
      // 现场一问"到底藏了几个案例"就答不上来。
      const unit = data.hidden_count_unit === "case" || !data.hidden_count_unit
        ? " 条<b>案例</b>" : " 条";
      lines.push("另有 <b>" + data.hidden_count + "</b>" + unit + "因权限范围不可见"
        + (data.hidden_count_only_when_short === false ? "" : "（只在候选不足时计数，避免泄漏总量）"));
    }
    if (data.notice) lines.push(UI.esc(data.notice));
    return '<div class="case-notice case-notice-' + tone + '">'
      + '<span class="case-notice-ico">!</span><div>' + lines.join("<br>") + "</div></div>";
  }

  /** 「为什么看不到」：只展示 hash 与人类可读解释，**不展示 expr/AST**。 */
  function whyPanel(UI, S, r) {
    if (!r) return "";
    const facts = [
      ["过滤指纹（filter_hash）", (r.filter_hash || "-").slice(0, 24)],
      ["候选 chunk 数", String(r.candidates == null ? "-" : r.candidates)],
      ["向量后端", r.backend || "-"],
      ["二次授权丢弃", String(r.second_stage_dropped || 0)],
    ];
    const rows = facts.map(function (pair) {
      return '<div class="case-why-row"><span>' + UI.esc(pair[0]) + "</span><code>"
        + UI.esc(pair[1]) + "</code></div>";
    }).join("");
    const explain = (r.filter_explain || []).map(function (line) {
      return "<li>" + UI.esc(line) + "</li>";
    }).join("");
    return '<details class="case-why"' + (S.showWhy ? " open" : "") + ">"
      + "<summary>为什么是这些结果？（技术详情：服务端编译出的过滤条件）</summary>"
      + rows
      + (explain ? '<div class="case-side-title">编译结果（人类可读，不是原始表达式）</div>'
        + '<ul class="case-why-list">' + explain + "</ul>" : "")
      + '<div class="case-hint">服务端只回传指纹与可读描述，不回传原始表达式：'
      + "前端既改不了过滤条件，也不需要知道内部字段名。</div>"
      + "</details>";
  }

  function hitList(UI, S, r) {
    if (S.searching) return '<div class="case-loading">' + UI.spinner() + " 检索中（服务端先过滤再召回）…</div>";
    if (S.error) {
      return '<div class="case-deny"><b>' + UI.esc(S.error.code || "请求失败") + "</b>"
        + "<p>" + UI.esc(S.error.message) + "</p>"
        + '<p class="case-hint">越权/门禁拒绝是预期行为，不是故障。</p></div>';
    }
    if (!r) {
      return '<div class="case-hint case-pad">输入关键词后回车。建议先搜「锡膏印刷偏移 虚焊 连锡」。</div>';
    }
    const all = r.hits || [];
    const hits = S.category === "all" ? all
      : all.filter(function (h) { return h.subcategory === S.category; });
    if (!hits.length) {
      const filteredOut = all.length - hits.length;
      return '<div class="case-empty"><b>0 条可见</b>'
        + (filteredOut > 0 ? "<p>当前分类下 0 条（另有 " + filteredOut
          + " 条被分类筛选挡在显示层，切回「全部」可见）。</p>" : "")
        + (r.hidden_count > 0 ? "<p>另有 <b>" + r.hidden_count + "</b> 条<b>案例</b>在你当前权限范围之外——"
          + "系统不会为了凑数放宽过滤条件。</p>" : "")
        + (filteredOut === 0 && !r.hidden_count ? "<p>没有匹配的案例。</p>" : "")
        + "</div>";
    }
    // 同一案例的多个 chunk 折叠成一张卡：命中列表按"案例"读，不按"片段"读。
    // 否则一次检索 20 张卡其实只有 10 个案例，现场会被问穿"到底几条案例"。
    const groups = groupByCase(hits);
    return groups.map(function (g, i) { return hitCard(UI, S, g, i); }).join("");
  }

  /** 命中项按 case_id 归并（保持首次出现的顺序 = 分数顺序）。 */
  function groupByCase(hits) {
    const order = [];
    const map = {};
    hits.forEach(function (hit) {
      const key = hit.case_id;
      if (!map[key]) {
        map[key] = {case_id: key, primary: hit, chunks: []};
        order.push(key);
      }
      map[key].chunks.push(hit);
    });
    return order.map(function (key) { return map[key]; });
  }

  function citeKey(hit) {
    return hit.chunk_id || (hit.case_id + "::" + (hit.chunk_type || "summary"));
  }

  function chunkRow(UI, S, hit) {
    const score = typeof hit.score === "number" ? hit.score.toFixed(2) : "-";
    const cited = S.cite && S.cite.citation
      && S.cite.citation.chunk_id === citeKey(hit);
    return '<li class="case-chunk-row' + (cited ? " case-chunk-cited" : "") + '">'
      + '<span class="case-chunk-tag">' + UI.esc(CHUNK_LABEL[hit.chunk_type] || hit.chunk_type || "-") + "</span>"
      + '<span class="case-chunk-score">' + score + "</span>"
      + '<span class="case-chunk-text">' + UI.esc(hit.text || "") + "</span>"
      + '<span class="case-chunk-act">'
      + '<button class="case-mini" data-act="cite" data-id="' + UI.esc(hit.case_id) + '"'
      + ' data-chunk="' + UI.esc(hit.chunk_type || "summary") + '"'
      + ' data-ver="' + UI.esc(String(hit.content_version || 1)) + '">引用此段</button>'
      + (cited ? '<i class="case-cited-flag">已引用</i>' : "")
      + "</span></li>";
  }

  function hitCard(UI, S, group, index) {
    const hit = group.primary;
    const chunks = group.chunks || [hit];
    const citedChunks = chunks.filter(function (c) {
      return S.cite && S.cite.citation && S.cite.citation.chunk_id === citeKey(c);
    });

    const chips = [];
    if (hit.plant) chips.push(hit.plant + (hit.line_code ? " · " + hit.line_code : ""));
    if (hit.defect_mode_code) chips.push(hit.defect_mode_code);
    if (hit.cls_rank) chips.push("密级 L" + hit.cls_rank);
    if (hit.content_version) chips.push("v" + hit.content_version);

    const score = typeof hit.score === "number" ? hit.score.toFixed(2) : "-";
    const multi = chunks.length > 1;

    return '<article class="case-hit" data-case-id="' + UI.esc(hit.case_id) + '" tabindex="0">'
      + '<div class="case-hit-score">' + score + "<i>#" + (index + 1) + "</i></div>"
      + '<div class="case-hit-body">'
      + '<div class="case-hit-top"><h4>' + UI.esc(hit.title || hit.case_id) + "</h4>"
      + statusBadge(UI, hit.status, hit.status_badge)
      + (multi ? '<span class="case-chunk-count" title="该案例命中了几个知识层片段">'
        + chunks.length + " 个片段</span>" : "")
      + "</div>"
      + '<div class="case-hit-meta">' + UI.esc(hit.category || "")
      + (hit.subcategory ? " / " + UI.esc(hit.subcategory) : "")
      + (chips.length ? " · " + chips.map(UI.esc).join(" · ") : "") + "</div>"
      + (hit.text ? '<p class="case-hit-text">' + UI.esc(hit.text) + "</p>" : "")
      + (multi ? '<details class="case-chunk-list"><summary>展开 ' + chunks.length
        + " 个片段（引用要精确到段）</summary><ul>"
        + chunks.map(function (c) { return chunkRow(UI, S, c); }).join("")
        + "</ul></details>" : "")
      + '<div class="case-hit-foot">'
      + '<span class="case-foot-cn">案例编号与片段编号见「技术详情」</span>'
      + '<details class="case-tech"><summary>技术详情</summary><code>'
      + UI.esc([hit.case_id, hit.chunk_id || "", "content_version="
                + String(hit.content_version || 1),
                "filter_hash=" + String((S.result && S.result.filter_hash) || "").slice(0, 16)]
               .filter(Boolean).join("\n")) + "</code></details>"
      + '<span class="case-hit-actions">'
      + '<button class="case-mini" data-act="detail" data-id="' + UI.esc(hit.case_id) + '">看全文</button>'
      + '<button class="case-mini" data-act="cite" data-id="' + UI.esc(hit.case_id) + '"'
      + ' data-chunk="' + UI.esc(hit.chunk_type || "summary") + '"'
      + ' data-ver="' + UI.esc(String(hit.content_version || 1)) + '">引用</button>'
      + "</span></div>"
      + (citedChunks.length ? '<div class="case-cite-inline">已引用（引用凭据）'
        + '<details class="case-tech"><summary>技术详情</summary><code>'
        + UI.esc(S.cite.citation.case_id + "#" + S.cite.citation.chunk_id
                + "@v" + String(S.cite.citation.content_version)) + "</code></details>"
        + '<span class="case-hint">（三元组已落审计；这就是"可溯源"的凭据）</span></div>' : "")
      + "</div></article>";
  }

  /** 问答面板 = 处理轨迹 + 结论卡片（轨迹来自后端，前端只渲染）。 */
  function askWithTrace(UI, S, a) {
    const pane = askPane(UI, S);
    return a ? tracePane(UI, S, a) + pane : pane;
  }

  /** 本体增强处理轨迹（数据来自后端 /cases/ask 的 trace，前端只渲染）。 */
  function tracePane(UI, S, a) {
    const trace = a.trace || [];
    if (!trace.length) return "";
    const rows = trace.map(function (step) {
      const items = (step.items || []).map(function (x) {
        return '<li>' + UI.esc(x) + "</li>";
      }).join("");
      return '<li class="case-trace-step"><span class="case-trace-no">'
        + UI.esc(String(step.step)) + "</span>"
        + '<div class="case-trace-body"><b>' + UI.esc(step.title || "") + "</b>"
        + "<p>" + UI.esc(step.detail || "") + "</p>"
        + (items ? '<ul class="case-trace-items">' + items + "</ul>" : "")
        + "</div></li>";
    }).join("");
    return '<details class="case-trace" open><summary>处理轨迹（这一步 Agent 做了什么、为什么）'
      + '</summary><ol class="case-trace-list">' + rows + "</ol></details>";
  }

  function askPane(UI, S) {
    const a = S.answer;
    let body = '<div class="case-hint case-pad">提问后系统检索案例、拼装带引用的结论；'
      + "任何一条结论挂不上 (case_id, chunk_id, content_version) 就会被丢弃，"
      + "全丢、或最高匹配度低于阈值 → 拒答（不编造）。</div>";
    if (S.asking) body = '<div class="case-loading">' + UI.spinner() + " 检索并组装引用…</div>";
    else if (a) {
      const claims = (a.claims || []).map(function (c, i) {
        const cites = (c.citations || []).map(function (x) {
          return '<span class="case-cite-cn">案例引用</span>'
            + '<details class="case-tech"><summary>技术详情</summary><code>'
            + UI.esc(x.case_id + "#" + x.chunk_id + "@v" + String(x.content_version))
            + "</code></details>";
        }).join("");
        return "<li><em>[" + (i + 1) + "]</em><span>" + UI.esc(c.text) + "</span>"
          + (cites || '<i class="case-warn-inline">无引用</i>') + "</li>";
      }).join("");
      const notice = a.search && a.search.notice
        ? '<div class="case-hint case-notice-inline">后端提示：' + UI.esc(a.search.notice) + "</div>" : "";
      body = '<div class="case-answer' + (a.answered ? "" : " case-answer-deny") + '">'
        + "<h4>" + (a.answered ? "结论" : "拒答") + "</h4>"
        + "<p>" + UI.esc(a.answered ? a.answer : (a.reason || "（无内容）")) + "</p>"
        + (claims ? '<div class="case-side-title">逐条结论与出处</div>'
          + '<ul class="case-claims">' + claims + "</ul>" : "")
        + (a.dropped_claims ? '<div class="case-warn">有 ' + a.dropped_claims
          + " 条结论因引用校验失败被丢弃。</div>" : "")
        + notice
        + (a.search ? whyPanel(UI, S, a.search) : "")
        + "</div>";
    }
    return '<div class="case-searchbar">'
      + '<input id="case-question" class="case-input" placeholder="例如：锡膏印刷偏移通常怎么处置？" autocomplete="off">'
      + '<button class="case-btn" id="case-ask">提问</button>'
      + "</div>" + body;
  }

  function comparePane(UI, S) {
    const roles = (S.meta && S.meta.rbac && S.meta.rbac.roles) || [];
    const opts = function (sel) {
      const list = roles.length ? roles : FALLBACK_ROLES.map(function (p) { return { key: p[0], name: p[1] }; });
      return list.map(function (r) {
        return '<option value="' + r.key + '"' + (sel === r.key ? " selected" : "") + ">"
          + r.key + " · " + (r.name || "") + "</option>";
      }).join("");
    };
    const deptA = S.compareDeptA == null ? (DEFAULT_DEPT[S.compareA] || "") : S.compareDeptA;
    const deptB = S.compareDeptB == null ? (DEFAULT_DEPT[S.compareB] || "") : S.compareDeptB;

    let out = '<div class="case-hint case-pad">同一句话、两个岗位、同一个索引：'
      + "差异只能来自授权。这是本屏最有说服力的一屏。</div>";
    if (S.comparing) out += '<div class="case-loading">' + UI.spinner() + " 两个岗位并行检索…</div>";
    if (S.compare) {
      const c = S.compare;
      const col = function (label, data, only) {
        const ids = (data.hits || []).map(function (h) { return h.case_id; });
        const uniq = ids.filter(function (v, i) { return ids.indexOf(v) === i; });
        return '<div class="case-cmp-col"><h5>' + UI.esc(label) + "</h5>"
          + '<div class="case-cmp-num">' + (data.hits ? data.hits.length : 0) + " 条</div>"
          + '<div class="case-cmp-sub">另有 ' + (data.hidden_count || 0) + " 条不可见</div>"
          + '<div class="case-cmp-sub">候选 ' + (data.candidates || 0) + " chunk</div>"
          + '<div class="case-cmp-list">' + (uniq.map(function (id) {
            const mark = only.indexOf(id) >= 0;
            return '<code class="' + (mark ? "case-cmp-only" : "") + '">' + UI.esc(id) + "</code>";
          }).join("") || '<span class="case-hint">0 条</span>') + "</div></div>";
      };
      out += '<div class="case-cmp">' + col(c.labelA, c.a, c.onlyA) + col(c.labelB, c.b, c.onlyB) + "</div>"
        + '<div class="case-cmp-diff">'
        + "<div><b>" + UI.esc(c.labelA) + " 独有</b>：" + (c.onlyA.map(UI.esc).join("、") || "—") + "</div>"
        + "<div><b>" + UI.esc(c.labelB) + " 独有</b>：" + (c.onlyB.map(UI.esc).join("、") || "—") + "</div>"
        + "</div>";
    }
    return '<div class="case-searchbar">'
      + '<select id="case-cmp-a" class="case-select">' + opts(S.compareA) + "</select>"
      + '<input id="case-cmp-dept-a" class="case-dept-input" value="' + UI.esc(deptA)
      + '" placeholder="部门" title="授权维度，可改">'
      + '<select id="case-cmp-b" class="case-select">' + opts(S.compareB) + "</select>"
      + '<input id="case-cmp-dept-b" class="case-dept-input" value="' + UI.esc(deptB)
      + '" placeholder="部门" title="授权维度，可改">'
      + '<input id="case-cmp-q" class="case-input" value="' + UI.esc(S.query) + '">'
      + '<button class="case-btn" id="case-compare">跑对比</button>'
      + "</div>" + out;
  }

  function auditPane(UI, S) {
    const a = S.audit;
    let out = '<div class="case-hint case-pad">审计只落<b>哈希与计数</b>'
      + "（查询哈希、过滤指纹、命中数、不可见数），不落查询原文、不落案例正文——"
      + "审计本身不能变成第二个泄漏面。</div>";
    if (a) {
      const chain = a.chain || {};
      out += '<div class="case-audit-sum">链状态：' + (chain.valid
        ? '<b class="case-ok">完整</b>'
        : '<b class="case-bad">断裂 @ seq ' + UI.esc(String(chain.broken_seq)) + "</b>")
        + " · 行数 " + (chain.rows || 0) + " · " + UI.esc(chain.detail || "") + "</div>";
      const rows = (a.recent || []).map(function (row) {
        const actionZh = ZH.action[row.action] || row.action;
        const actorZh = actorLabel(row.actor, S.meta);
        const detail = ['<details class="case-tech"><summary>技术详情</summary><code>'
          + UI.esc([row.action, row.actor, row.case_id || "-",
                    "filter_hash=" + String(row.filter_hash || "").slice(0, 12),
                    "query_hash=" + String(row.query_hash || "").slice(0, 12),
                    "row_hash=" + String(row.row_hash || "").slice(0, 12),
                    "prev_hash=" + String(row.prev_hash || "").slice(0, 12)]
            .join("\n")) + "</code></details>"].join("");
        return "<tr><td>" + row.seq + "</td><td>" + UI.esc(actionZh) + "</td><td>"
          + UI.esc(actorZh) + "</td><td>" + UI.esc(row.case_id || "-") + "</td><td>"
          + (row.hit_count || 0) + "</td><td>" + (row.hidden_count || 0) + "</td><td>"
          + detail + "</td></tr>";
      }).join("");
      out += '<table class="case-audit-table"><thead><tr><th>#</th><th>动作</th><th>操作人</th>'
        + "<th>案例</th><th>命中数</th><th>不可见数</th><th>技术信息</th></tr></thead><tbody>"
        + (rows || '<tr><td colspan="7">暂无记录</td></tr>') + "</tbody></table>";
      out += '<div class="case-hint">改动任意一行后重跑校验，链会断在那一行（'
        + "记录指纹覆盖全字段，篡改无法自愈）。</div>";
    }
    return '<div class="case-searchbar"><button class="case-btn" id="case-audit-run">重新校验</button></div>' + out;
  }

  /* ================= 弹层 ================= */

  function detailOverlay(UI, S) {
    const d = S.detail;
    if (d.loading) {
      return '<div class="case-modal"><div class="case-modal-card">'
        + '<div class="case-modal-head"><b>加载中…</b>'
        + '<button class="case-mini" data-act="close">关闭</button></div></div></div>';
    }
    if (d.error) {
      return '<div class="case-modal"><div class="case-modal-card">'
        + '<div class="case-modal-head"><b>' + UI.esc(d.error.code || "无法打开") + "</b>"
        + '<button class="case-mini" data-act="close">关闭</button></div>'
        + '<div class="case-deny"><p>' + UI.esc(d.error.message) + "</p>"
        + '<p class="case-hint">404 而不是 403：无权访问时不告诉你"这份案例存在"。</p></div>'
        + "</div></div>";
    }
    const c = d.case || {};
    const section = function (title, text) {
      if (!text) return "";
      return '<div class="case-detail-sec"><h5>' + title + "</h5><p>" + UI.esc(text) + "</p></div>";
    };
    return '<div class="case-modal"><div class="case-modal-card">'
      + '<div class="case-modal-head"><b>' + UI.esc(c.title || c.case_id) + "</b>"
      + statusBadge(UI, c.status, c.status_badge)
      + '<button class="case-mini" data-act="close">关闭</button></div>'
      + '<div class="case-detail-meta">'
      + UI.esc(c.case_code || c.case_id) + " · " + UI.esc(c.category || "")
      + (c.subcategory ? " / " + UI.esc(c.subcategory) : "")
      + " · " + UI.esc(c.plant || "-") + " " + UI.esc(c.line_code || "")
      + " · " + UI.esc(c.defect_mode_code || "-")
      + " · 密级 L" + UI.esc(String(c.cls_rank || 0))
      + " · v" + UI.esc(String(c.content_version))
      + (c.source_ticket_id ? " · 来源 " + UI.esc(c.source_ticket_type) + " "
        + UI.esc(c.source_ticket_id) : "")
      + "</div>"
      + section("摘要", c.summary)
      + section("现象", c.phenomenon)
      + section("根因", c.root_cause_text)
      + section("对策", c.countermeasure_text)
      + '<div class="case-detail-foot">'
      + '<span class="case-hint">以上为脱敏后的知识层内容；证据层（图纸/照片/原始工单）不在此接口返回。</span>'
      + '<button class="case-btn" data-act="cite" data-id="' + UI.esc(c.case_id) + '"'
      + ' data-chunk="summary" data-ver="' + UI.esc(String(c.content_version || 1)) + '">引用到工单</button>'
      + "</div>"
      + (S.cite ? '<div class="case-cite-result"><div class="case-side-title">引用三元组（已落审计）</div>'
        + '<code class="case-cite">' + UI.esc(S.cite.citation.case_id) + "#"
        + UI.esc(S.cite.citation.chunk_id) + "@v"
        + UI.esc(String(S.cite.citation.content_version)) + "</code>"
        + '<div class="case-hint">' + UI.esc(S.cite.note || "") + "</div>"
        + '<div class="case-hint">目标：' + UI.esc((S.cite.target || {}).ticket_type || "") + " "
        + UI.esc((S.cite.target || {}).ticket_id || "") + "</div></div>" : "")
      + "</div></div>";
  }

  function ingestOverlay(UI, S) {
    const f = S.ingestForm || {};
    const field = function (key, label, placeholder, area) {
      const value = UI.esc(f[key] || "");
      return '<label class="case-form-row"><span>' + label + "</span>"
        + (area ? '<textarea data-f="' + key + '" rows="3" placeholder="' + placeholder + '">' + value + "</textarea>"
          : '<input data-f="' + key + '" value="' + value + '" placeholder="' + placeholder + '">')
        + "</label>";
    };
    const res = S.ingestResult;
    return '<div class="case-modal"><div class="case-modal-card">'
      + '<div class="case-modal-head"><b>录入案例（落 review，不自动发布）</b>'
      + '<button class="case-mini" data-act="close-ingest">关闭</button></div>'
      + '<div class="case-form">'
      + field("title", "标题", "客诉：锡膏印刷偏移导致连锡")
      + field("phenomenon", "现象", "SPI 检出面积偏差 18%-25%，回流后连锡", true)
      + field("root_cause_text", "根因", "锡膏回温不足、刮刀压力偏低", true)
      + field("countermeasure_text", "对策", "回温 4h 以上，压力恢复 6kg", true)
      + field("source_ticket_type", "来源工单类型", "8d")
      + field("source_ticket_id", "来源工单号", "8D-2026-XXXX")
      + field("defect_mode_code", "缺陷模式码（可选）", "DM-SMT-012")
      + (res ? '<div class="case-cite-result"><div class="case-side-title">已入库（待审）</div>'
        + "<div>case_id <code>" + UI.esc(res.case_id) + "</code> · status <b>" + UI.esc(res.status) + "</b></div>"
        + "<div>脱敏命中：" + ((res.redaction_kinds || []).map(UI.esc).join("、") || "无") + "</div>"
        + "<div>ACL 令牌：" + ((res.acl_tokens || []).map(function (t) {
          return "<code>" + UI.esc(t) + "</code>";
        }).join("") || "无（默认开放）") + "</div>"
        + '<div class="case-hint">' + UI.esc(res.next_step || "") + "</div></div>" : "")
      + "</div>"
      + '<div class="case-form-foot">'
      + '<span class="case-hint">入库必然落 review 且不自动发布；写入库的正文已脱敏（不可逆）。</span>'
      + '<button class="case-btn" data-act="ingest-submit">提交</button>'
      + "</div></div></div>";
  }

  /* ================= 交互 ================= */

  function bind(root, ctx, S, base) {
    const UI = ctx.UI;

    const repaint = function () { render(root, ctx); };
    const on = function (selector, event, handler) {
      const el = root.querySelector(selector);
      if (el) el.addEventListener(event, handler);
    };

    /* ---- 统计卡片：刷新 + 点击直达 ---- */
    on("#case-stats-refresh", "click", function () { loadStats(root, ctx, S, base); });
    // 演示说明黄条：收起/展开（信息不丢，噪音可控）
    on("#case-warn-toggle", "click", function () {
      S.warnCollapsed = !S.warnCollapsed;
      repaint();
    });    Array.prototype.forEach.call(root.querySelectorAll("[data-stats-jump]"), function (el) {
      const go = function () {
        const where = el.getAttribute("data-stats-jump");
        if (where === "restricted") {
          // 受限清单常驻左栏：滚到它，比切 tab 更近
          const side = root.querySelector(".case-side");
          if (side) side.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
        S.tab = "access";
        render(root, ctx);
        doAccessList();
      };
      el.addEventListener("click", go);
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
      });
    });

    /** 统一处理本屏错误：401 清令牌 + 专门文案；403 展示拒绝码；其余原样。 */
    const handle = function (err, prefix) {
      if (err.status === 401) {
        S.token = "";
        writeStore(TOKEN_KEY, null);
        S.authError = "案例会话已失效（" + (err.code || "401") + "）：请重新选择身份并建立会话";
        S.meta = null;
      } else if (err.status === 403) {
        S.authError = "请求被拒绝：" + UI.friendlyError(err).text;
      } else {
        // 加载失败优化：裸 err.message（"Failed to fetch" / "xxx is not defined"）
        // 对用户不可读也不可操作，统一走友好化转译；多给 1.8s 阅读时间。
        UI.toast(UI.friendlyError(err).text, "err", 5000);
      }
    };

    on("#case-session", "click", function () {
      const roleEl = root.querySelector("#case-role");
      const deptEl = root.querySelector("#case-dept");
      const plantEl = root.querySelector("#case-plant");
      const etypeEl = root.querySelector("#case-etype");
      const rankEl = root.querySelector("#case-rank");
      const dept = ((deptEl && deptEl.value) || "").trim();
      let role = (roleEl && roleEl.value) || S.identity.role;
      // 兜底：若所选岗位不属于所选部门（级联尚未刷新的竞态），自动取该部门第一个岗位。
      // 不这么做的话会出现"部门=设备、岗位=QA"这种自相矛盾的身份被提交上去。
      const allowed = rolesOfDept(S.meta, dept);
      if (!role || (dept && allowed.indexOf(role) < 0)) {
        role = allowed[0] || S.identity.role;
        if (roleEl) roleEl.value = role;
      }
      const identity = {
        role: role,
        dept: dept,
        plant: ((plantEl && plantEl.value) || "").trim(),
        employment_type: (etypeEl && etypeEl.value) || "employee",
        max_classification_rank: parseInt((rankEl && rankEl.value) || "1", 10) || 1,
      };
      S.identity = identity;
      writeStore(IDENTITY_KEY, JSON.stringify(identity));
      S.meta = null;
      S.metaError = "";
      S.metaLoading = false;
      S.authError = "";
      S.result = null;
      S.error = null;
      S.answer = null;
      S.compare = null;
      S.audit = null;
      S.detail = null;
      S.tab = "search";
      // 换身份 = 换可见范围：旧统计必须作废，否则卡片会短暂显示上一岗位的数字
      S.stats = null;
      S.statsError = "";
      stopAutoTimers();
      S.autoFlow = null;

      caseFetch(base, "/api/v2/cases/session", {
        body: {
          role: identity.role, dept: identity.dept,
          plant: identity.plant, employment_type: identity.employment_type,
          max_classification_rank: identity.max_classification_rank,
          name: identity.role + "（演示）",
        },
      }).then(function (data) {
        S.token = data.token;
        S.session = data;
        writeStore(TOKEN_KEY, data.token);
        UI.toast("已建立案例会话：" + identity.role + " · tv="
          + data.principal.token_version + " pv=" + data.principal.principal_version, "ok");
        repaint();
        // 新身份 = 新可见范围 → 统计卡片立即重算（旧数字不能停留）
        loadStats(root, ctx, S, base);
      }).catch(function (err) {
        S.authError = "建立会话失败：" + ctx.UI.friendlyError(err).text;
        repaint();
      });
    });

    on("#case-role", "change", function (ev) {
      const deptEl = root.querySelector("#case-dept");
      const suggested = DEFAULT_DEPT[ev.target.value];
      if (deptEl && suggested) deptEl.value = suggested;
    });

    on("#case-logout", "click", function () {
      S.token = "";
      writeStore(TOKEN_KEY, null);
      S.session = null;
      S.meta = null;
      S.result = null;
      S.detail = null;
      S.answer = null;
      S.audit = null;
      S.compare = null;
      S.authError = "";
      // 退出即无可见范围：统计卡片一起清掉（旧数字留着等于误导）
      S.stats = null;
      S.statsError = "";
      stopAutoTimers();
      S.autoFlow = null;
      repaint();
    });

    on("#case-q", "keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); doSearch(); }
    });
    on("#case-search", "click", doSearch);
    on("#case-cat", "change", function (ev) {
      S.category = ev.target.value;
      repaint();
    });
    on("#case-question", "keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); doAsk(); }
    });
    on("#case-ask", "click", doAsk);
    on("#case-compare", "click", doCompare);
    on("#case-audit", "click", function () { S.tab = "audit"; repaint(); doAudit(); });
    on("#case-audit-run", "click", doAudit);
    // 页面信息加载失败后的重试入口（清错误态重新拉 meta）
    on("#case-meta-retry", "click", function () {
      S.metaError = "";
      S.metaLoading = false;
      loadMeta(root, ctx, S, base);
    });
    on("#case-ingest-open", "click", function () {
      S.ingestForm = {}; S.ingestResult = null; repaint();
    });

    Array.prototype.forEach.call(root.querySelectorAll(".case-link[data-tab]"),
      function (el) {
        el.addEventListener("click", function () {
          S.tab = el.getAttribute("data-tab");
          repaint();
          if (S.tab === "audit" && !S.audit) doAudit();
          if (S.tab === "access" && !S.myRequests) doAccessList();
        });
      });

    // 部门级联：切部门后岗位下拉只保留该部门的岗位
    on("#case-dept", "change", function () {
      const deptEl = root.querySelector("#case-dept");
      const roleEl = root.querySelector("#case-role");
      const dept = deptEl ? deptEl.value : "";
      S.identity.dept = dept;
      const allowed = rolesOfDept(S.meta, dept);
      if (allowed.indexOf(S.identity.role) < 0) {
        S.identity.role = allowed[0] || "";
        if (roleEl && S.identity.role) roleEl.value = S.identity.role;
      }
      repaint();
    });

    Array.prototype.forEach.call(root.querySelectorAll("[data-act]"), function (el) {
      el.addEventListener("click", function (ev) {
        ev.stopPropagation();
        const act = el.getAttribute("data-act");
        const id = el.getAttribute("data-id");
        if (act === "close") { S.detail = null; repaint(); }
        else if (act === "close-ingest") { S.ingestForm = null; repaint(); }
        else if (act === "detail") openDetail(id);
        else if (act === "cite") {
          doCite(id, el.getAttribute("data-chunk"), el.getAttribute("data-ver"));
        }
        else if (act === "ingest-submit") submitIngest();
        else if (act === "access-open") { S.accessForm = { case_id: id, reason: "" }; repaint(); }
        else if (act === "access-close") { S.accessForm = null; repaint(); }
        else if (act === "access-submit") {
          doAccessSubmit(id, (root.querySelector("#case-access-reason") || {}).value || "");
        }
        else if (act === "auto-run") {
          // 「再演一次」：对一条 pending 申请重放演示审批弹窗
          const replay = requestRowOf(el.getAttribute("data-rid"));
          if (replay) startAutoApprove(replay, caseTitleOf(id));
        }
        else if (act === "auto-close") { closeAutoFlow(); }
        else if (act === "access-approve") {
          doAccessDecide(el.getAttribute("data-rid"), true);
        }
        else if (act === "access-reject") {
          doAccessDecide(el.getAttribute("data-rid"), false);
        }
      });
    });

    Array.prototype.forEach.call(root.querySelectorAll(".case-hit"), function (el) {
      el.addEventListener("click", function () { openDetail(el.getAttribute("data-case-id")); });
    });

    function doSearch() {
      const q = ((root.querySelector("#case-q") || {}).value || "").trim();
      const action = (root.querySelector("#case-action") || {}).value || "query";
      const k = parseInt(((root.querySelector("#case-k") || {}).value || "20"), 10) || 20;
      S.query = q;
      S.action = action;
      S.k = k;
      if (!q) { UI.toast("请输入关键词", "err"); return; }
      S.searching = true;
      S.error = null;
      S.authError = "";
      repaint();
      caseFetch(base, "/api/v2/cases/search", {
        token: S.token, body: { query: q, k: k, action: action },
      }).then(function (data) {
        S.searching = false;
        S.result = data;
        if (!(data.hits || []).length) {
          UI.toast("0 条可见" + (data.hidden_count
            ? "，另有 " + data.hidden_count + " 条在权限范围外" : ""), "info");
        }
        repaint();
      }).catch(function (err) {
        S.searching = false;
        S.result = null;
        S.error = err;
        handle(err, "检索失败");
        repaint();
      });
    }

    function doAsk() {
      const q = ((root.querySelector("#case-question") || {}).value || "").trim();
      if (!q) { UI.toast("请输入问题", "err"); return; }
      S.asking = true;
      S.answer = null;
      repaint();
      caseFetch(base, "/api/v2/cases/ask", {
        token: S.token, body: { question: q, k: 5, action: "read" },
      }).then(function (data) {
        S.asking = false;
        S.answer = data;
        repaint();
      }).catch(function (err) {
        S.asking = false;
        S.answer = {
          answered: false, answer: "", claims: [], citations: [],
          reason: UI.friendlyError(err).text,
        };
        handle(err, "问答失败");
        repaint();
      });
    }

    function doCompare() {
      const a = (root.querySelector("#case-cmp-a") || {}).value || S.compareA;
      const b = (root.querySelector("#case-cmp-b") || {}).value || S.compareB;
      const deptA = ((root.querySelector("#case-cmp-dept-a") || {}).value || "").trim();
      const deptB = ((root.querySelector("#case-cmp-dept-b") || {}).value || "").trim();
      const q = ((root.querySelector("#case-cmp-q") || {}).value || S.query || "").trim();
      S.compareA = a; S.compareB = b;
      S.compareDeptA = deptA; S.compareDeptB = deptB;
      S.query = q;
      if (!q) { UI.toast("请输入关键词", "err"); return; }
      if (!deptA || !deptB) {
        UI.toast("部门是授权维度：留空会让两边的部门域案例都不可见，差异被抹平", "err", 4200);
      }
      S.comparing = true;
      S.compare = null;
      repaint();
      const one = function (role, dept) {
        return caseFetch(base, "/api/v2/cases/session", {
          body: {
            role: role, dept: dept, plant: S.identity.plant || "P1",
            employment_type: "employee", max_classification_rank: 1,
            name: role + "（对比）",
          },
        }).then(function (sess) {
          return caseFetch(base, "/api/v2/cases/search", {
            token: sess.token, body: { query: q, k: S.k || 20, action: "query" },
          }).then(function (data) { return data; })
            .catch(function (err) {
              return { hits: [], hidden_count: 0, candidates: 0,
                       error: UI.friendlyError(err).text };
            });
        });
      };
      Promise.all([one(a, deptA), one(b, deptB)]).then(function (pair) {
        const uniq = function (arr) {
          return arr.filter(function (v, i) { return arr.indexOf(v) === i; });
        };
        const idsA = uniq((pair[0].hits || []).map(function (h) { return h.case_id; }));
        const idsB = uniq((pair[1].hits || []).map(function (h) { return h.case_id; }));
        S.compare = {
          labelA: a, labelB: b, a: pair[0], b: pair[1],
          onlyA: idsA.filter(function (x) { return idsB.indexOf(x) < 0; }),
          onlyB: idsB.filter(function (x) { return idsA.indexOf(x) < 0; }),
        };
        S.comparing = false;
        repaint();
      }).catch(function (err) {
        S.comparing = false;
        UI.toast("对比失败：" + UI.friendlyError(err).text, "err");
        repaint();
      });
    }

    function doAudit() {
      caseFetch(base, "/api/v2/cases/audit/verify?limit=20", { token: S.token })
        .then(function (data) {
          S.audit = data;
          if (S.tab !== "audit") S.tab = "audit";
          repaint();
        })
        .catch(function (err) { handle(err, "审计校验失败"); repaint(); });
    }

    function openDetail(id) {
      if (!id) return;
      S.detail = { loading: true };
      S.cite = null;
      repaint();
      caseFetch(base, "/api/v2/cases/" + encodeURIComponent(id), { token: S.token })
        .then(function (data) { S.detail = { case: data }; repaint(); })
        .catch(function (err) { S.detail = { error: err }; repaint(); });
    }

    /** 某部门下的岗位 key 列表（级联用）。

     * 两级回退：先用 meta（服务端口径），为空则用内置兜底表，再为空则不过滤。
     * 自动化脚本与 meta 到达的竞态下，这保证了"部门"始终能选出一个岗位。
     */
    function rolesOfDept(meta, dept) {
      const fromMeta = ((meta && meta.rbac && meta.rbac.roles) || [])
        .filter(function (r) { return !dept || r.dept === dept; })
        .map(function (r) { return r.key; });
      if (fromMeta.length) return fromMeta;
      const fromFallback = FALLBACK_ROLES
        .filter(function (row) { return !dept || row[2] === dept; })
        .map(function (row) { return row[0]; });
      if (fromFallback.length) return fromFallback;
      return FALLBACK_ROLES.map(function (row) { return row[0]; });
    }

    function doAccessList() {
      if (!S.token) return;
      S.accessLoading = true;
      repaint();
      Promise.all([
        caseFetch(base, "/api/v2/cases/access/requests?scope=mine",
          { token: S.token }),
        caseFetch(base, "/api/v2/cases/access/requests?scope=dept",
          { token: S.token }),
      ]).then(function (res) {
        S.accessLoading = false;
        S.myRequests = res[0].requests || [];
        S.deptRequests = res[1].requests || [];
        repaint();
      }).catch(function (err) {
        S.accessLoading = false;
        handle(err, "加载申请失败");
        repaint();
      });
    }

    function doAccessSubmit(caseId, reason) {
      if (!caseId) return;
      caseFetch(base, "/api/v2/cases/" + encodeURIComponent(caseId)
        + "/request-access",
        { token: S.token, body: { reason: String(reason || "").trim() } })
        .then(function (data) {
          S.accessForm = null;
          S.accessResult = data;
          doAccessList();
          // 申请数变了 → 统计卡片同步刷新（数字与申请列表必须对得上）
          loadStats(root, ctx, S, base);
          // 服务端预检可用 → 弹窗走一遍"智能 Agent 执行 → 职能部门审批通过"；
          // 不可用（无审批岗 / 开关关闭）→ 保持原行为：只提示已提交、等待人工审批。
          const demo = (data && data.demo_approval) || {};
          if (demo.available) {
            startAutoApprove(data, caseTitleOf(caseId));
          } else {
            UI.toast(data.message || "申请已提交", "ok");
          }
        }).catch(function (err) {
          UI.toast("申请提交失败：" + ctx.UI.friendlyError(err).text, "err");
        });
    }

    /** 从最近一次检索的受限清单里找案例标题（弹窗第一行要显示人话）。 */
    function caseTitleOf(caseId) {
      const list = (S.result && S.result.restricted) || [];
      for (let i = 0; i < list.length; i++) {
        if (list[i].case_id === caseId) return list[i].title || "";
      }
      return "";
    }

    /** 我提交的申请里按 request_id 找行（「再演一次」入口用）。 */
    function requestRowOf(requestId) {
      const rows = S.myRequests || [];
      for (let i = 0; i < rows.length; i++) {
        if (rows[i].request_id === requestId) {
          return { request: rows[i],
                   route: (S.autoRoutes || {})[rows[i].case_id] || {} };
        }
      }
      return null;
    }

    /* ---------- 演示自动审批（仅演示；服务端见 case_auto_approve.py） ---------- */

    function startAutoApprove(data, title) {
      const request = (data && data.request) || {};
      const route = (data && data.route) || {};
      if (!request.request_id) {
        UI.toast("申请信息不完整，无法开始演示审批", "err");
        return;
      }
      if (request.case_id) S.autoRoutes[request.case_id] = route;
      stopAutoTimers();
      const flow = {
        request_id: request.request_id,
        case_id: request.case_id || "",
        case_title: title || "",
        dept: route.pushed_to_label || route.pushed_to || "职能部门",
        basis: route.basis || "案例归属信息",
        step: 0, status: "running", error: "", expires: "", timers: [],
      };
      S.autoFlow = flow;
      repaint();
      const alive = function () {
        return !!S.autoFlow && S.autoFlow === flow && flow.status === "running";
      };
      const tick = function (next) { if (alive()) { flow.step = next; repaint(); } };
      flow.timers.push(setTimeout(function () { tick(1); }, 420));
      flow.timers.push(setTimeout(function () { tick(2); }, 920));
      flow.timers.push(setTimeout(function () { tick(3); }, 1480));
      flow.timers.push(setTimeout(function () {
        if (!alive()) return;
        flow.step = 4;
        repaint();
        callDemoApprove(flow);
      }, 1980));
    }

    function callDemoApprove(flow) {
      // 注意：caseFetch 只在有 body 时才发 POST——演示审批是动作型调用，
      // 必须显式带一个空 body，否则会退化成 GET（405）。
      caseFetch(base, "/api/v2/cases/access/requests/"
        + encodeURIComponent(flow.request_id) + "/demo-approve",
        { token: S.token, body: {} })
        .then(function (data) {
          if (!S.autoFlow || S.autoFlow !== flow) return;
          flow.status = "approved";
          flow.step = autoFlowSteps(flow).length;
          flow.expires = ((data && data.request) || {}).expires_at || "";
          repaint();
        }).catch(function (err) {
          if (!S.autoFlow || S.autoFlow !== flow) return;
          flow.status = "failed";
          flow.error = UI.friendlyError(err).text;
          repaint();
        });
    }

    function closeAutoFlow() {
      const flow = S.autoFlow;
      if (!flow) return;
      const approved = flow.status === "approved";
      stopAutoTimers();
      S.autoFlow = null;
      repaint();
      doAccessList();
      // 授权生效后重新检索：受限清单与命中集即时反映"现在能看到了"
      if (S.result && S.query) doSearch();
      // 演示代批通过 → 我的申请·待审 -1、受限 -1、可见 +1：卡片必须同步
      loadStats(root, ctx, S, base);
      if (approved) UI.toast("授权已生效：该案例已进入你的检索范围", "ok", 3600);
    }

    function stopAutoTimers() {
      const flow = S.autoFlow;
      if (flow && flow.timers) {
        flow.timers.forEach(function (t) { clearTimeout(t); });
        flow.timers = [];
      }
    }

    function doAccessDecide(requestId, approve) {
      if (!requestId) return;
      caseFetch(base, "/api/v2/cases/access/requests/"
        + encodeURIComponent(requestId) + "/decide",
        { token: S.token, body: { approve: !!approve,
          reason: approve ? "同意本岗位引用" : "本岗位暂不开放该案例" } })
        .then(function () {
          UI.toast(approve ? "已批准：授权已生效（带有效期）" : "已驳回", "ok");
          doAccessList();
          // 裁决改变待审数与授权范围 → 统计卡片同步刷新
          loadStats(root, ctx, S, base);
        }).catch(function (err) {
          UI.toast("审批失败：" + ctx.UI.friendlyError(err).text, "err");
        });
    }

    function doCite(id, chunkType, version) {
      if (!id) return;
      const chunk = chunkType || "summary";
      const ver = parseInt(version || "0", 10) || 0;
      caseFetch(base, "/api/v2/cases/" + encodeURIComponent(id) + "/cite", {
        token: S.token,
        body: {
          target_ticket_type: "8d",
          target_ticket_id: "8D-DEMO-0001",
          note: "演示引用",
          // 精确到用户点的那一段：引用三元组要能对回本人读到的内容
          chunk_type: chunk,
          content_version: ver,
        },
      }).then(function (data) {
        S.cite = data;
        repaint();
        UI.toast("已引用：" + data.citation.chunk_id, "ok");
      }).catch(function (err) {
        S.cite = null;              // 旧引用标记必须撤掉，否则页面会显示"已引用"的假象
        handle(err, "引用被拒");
        repaint();
      });
    }

    function submitIngest() {
      const form = {};
      Array.prototype.forEach.call(root.querySelectorAll("[data-f]"), function (el) {
        form[el.getAttribute("data-f")] = (el.value || "").trim();
      });
      S.ingestForm = form;
      caseFetch(base, "/api/v2/cases/ingest", { token: S.token, body: { draft: form } })
        .then(function (data) {
          S.ingestResult = data;
          repaint();
          UI.toast("已入库（待审）", "ok");
        })
        .catch(function (err) {
          UI.toast("入库失败：" + UI.friendlyError(err).text, "err");
          S.ingestResult = {
            case_id: "-", status: "-", redaction_kinds: [], acl_tokens: [],
            next_step: UI.friendlyError(err).text,
          };
          repaint();
        });
    }
  }

  function loadMeta(root, ctx, S, base) {
    S.metaLoading = true;
    caseFetch(base, "/api/v2/cases/meta", { token: S.token })
      .then(function (data) {
        S.meta = data;
        S.metaError = "";
        S.metaLoading = false;
        // render 抛错不能进 .catch——那会把"渲染 bug"误记成"meta 加载失败"，
        // 用户看到的重试按钮根本治不好（2026-10-18 distRows 事教训）。
        try { render(root, ctx); } catch (e) { console.error("[cases] meta 渲染失败:", e); }
        // 统计卡片跟随 meta 加载（数字口径与检索同源，晚一步无妨）
        loadStats(root, ctx, S, base);
      })
      .catch(function (err) {
        S.metaLoading = false;
        if (err.status === 401) {
          S.token = "";
          writeStore(TOKEN_KEY, null);
          S.authError = "案例会话已失效（" + (err.code || "401") + "）：请重新选择身份并建立会话";
        } else {
          // 存错误对象而非拼好的字符串：展示层统一走 UI.friendlyError 做
          // 友好化转译（kind/status 决定说人话还是透传后端业务文案），
          // 技术原文进 <details>，不直接裸露给用户。
          S.metaError = err;
        }
        render(root, ctx);
      });
  }

  window.Screens = window.Screens || {};
  window.Screens.render_cases = render;

  // 供自动化检查使用（纯函数，不依赖 DOM）
  window.CaseScreen = { initialState: initialState, STATUS_META: STATUS_META };
})();
