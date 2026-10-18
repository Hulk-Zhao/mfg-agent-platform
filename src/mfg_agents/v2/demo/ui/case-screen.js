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

  /** 首次建会话前的岗位候选（纯展示；登录后由 meta.rbac.roles 整体替换）。 */
  const FALLBACK_ROLES = [
    ["qa", "QA"], ["pqe", "PQE"], ["sqe", "SQE"], ["qa_manager", "QA经理"],
    ["quality_director", "质量总监"], ["gm", "总经理"],
    ["equipment", "设备工程"], ["equipment_manager", "设备经理"],
    ["maintenance_tech", "设备维保技师"], ["manufacturing", "制造"],
    ["product_engineering", "产品/工艺工程"], ["ie_engineer", "IE 工业工程"],
    ["me_engineer", "结构工程师"], ["test_engineer", "工程测试工程师"],
    ["rd_manager", "研发经理"], ["rd_engineering", "工程"],
    ["npi_engineer", "NPI 导入工程师"], ["project_manager", "项目经理"],
    ["supply_chain_manager", "供应链经理"], ["procurement_manager", "采购经理"],
    ["procurement", "采购"], ["planning", "计划"], ["material_control", "物控"],
    ["warehouse_manager", "仓储经理"], ["warehouse", "仓库"],
    ["customer_service", "客户服务"], ["sales", "业务"],
    ["finance_manager", "财务经理"], ["finance", "财务"],
    ["admin_hr", "行政人事"], ["ehs", "EHS 安全环保"],
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

  function roleOptions(meta, selected) {
    const roles = (meta && meta.rbac && meta.rbac.roles) || [];
    if (!roles.length) {
      return FALLBACK_ROLES.map(function (pair) {
        return '<option value="' + pair[0] + '"' + (pair[0] === selected ? " selected" : "")
          + ">" + pair[0] + " · " + pair[1] + "</option>";
      }).join("");
    }
    return roles.map(function (r) {
      return '<option value="' + r.key + '"' + (r.key === selected ? " selected" : "") + ">"
        + r.key + " · " + (r.name || "") + "（" + (r.actions || []).length + " 项）</option>";
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
      tab: "search",
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
      + '  <div class="case-body">'
      + '    <aside class="case-side">' + sidePanel(UI, S) + "</aside>"
      + '    <section class="case-main">' + mainPanel(UI, S) + "</section>"
      + "  </div>"
      + (S.detail ? detailOverlay(UI, S) : "")
      + (S.ingestForm ? ingestOverlay(UI, S) : "")
      + "</div>";

    bind(root, ctx, S, base);
    if (!S.meta && hasToken && !S.metaLoading && !S.metaError) loadMeta(root, ctx, S, base);
  }

  /** 身份条：demo 的核心说服点——令牌是可见的，四类前缀一眼看到。 */
  function identityBar(UI, S) {
    const id = S.identity;
    const roles = roleOptions(S.meta, id.role);
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
      + '<label>岗位<select id="case-role">' + roles + "</select></label>"
      + '<label>部门<input id="case-dept" value="' + UI.esc(id.dept) + '" placeholder="quality"></label>'
      + '<label>工厂<input id="case-plant" value="' + UI.esc(id.plant) + '" placeholder="P1"></label>'
      + '<label>用工类型<select id="case-etype">' + etypeOptions + "</select></label>"
      + '<label>可见密级<select id="case-rank">' + rankOptions + "</select></label>"
      + '<button class="case-btn" id="case-session">'
      + (S.token ? "重建案例会话" : "建立案例会话") + "</button>"
      + (S.token ? '<button class="case-btn case-btn-ghost" id="case-logout">退出</button>' : "")
      + "</div>"
      + '<div class="case-id-note">演示态：生产由 SSO/IdP 注入身份，此处的选岗接口'
      + '（<code>POST /cases/session</code>）在生产由 <code>CASE_DEMO_MODE=false</code> 关闭。'
      + "前端无法自造角色：服务端会用权限注册表归一化，未识别直接 400。</div>"
      + demoWarnings(UI, S)
      + "</div>"
      + principalPanel(UI, S)
      + "</header>";
  }

  /** 演示态风险（AUTH_SECRET 未配置 / 演示开关）——必须"显式且刺眼"，不能只写日志。 */
  function demoWarnings(UI, S) {
    const list = (S.meta && S.meta.demo_warnings) || [];
    if (!list.length) return "";
    return '<div class="case-warn-bar">'
      + list.map(function (w) {
        return '<div class="case-warn-line"><b>演示风险</b>' + UI.esc(w) + "</div>";
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
    const tokens = (p.principal_tokens || []).map(function (t) {
      return '<code class="case-token">' + UI.esc(t) + "</code>";
    }).join("");
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
      + "</div></div>";
  }

  function authErrorBar(UI, S) {
    return '<div class="case-auth-error">'
      + '<b>' + UI.esc(S.authError) + "</b></div>";
  }

  /* ================= 左栏 ================= */

  function sidePanel(UI, S) {
    if (!S.token) {
      return '<div class="case-side-empty">请先建立案例会话。<br>'
        + '<span class="case-hint">同一句话，换个岗位结果不同——这就是本屏要证明的事。</span></div>';
    }
    if (S.metaError) {
      return '<div class="case-side-empty">元信息加载失败：<br>' + UI.esc(S.metaError) + "</div>";
    }
    if (!S.meta) return '<div class="case-side-empty">' + UI.spinner() + " 加载元信息…</div>";

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
      + '<div class="case-side-title">我的动作</div>'
      + '<div class="case-chip-row">' + actionChips + "</div>"
      + '<div class="case-side-title">动作门禁（持有角色数）</div>'
      + (allActions || '<span class="case-hint">-</span>')
      + '<div class="case-side-title">受控词表</div>'
      + (vocabRows || '<span class="case-hint">-</span>')
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
    const tabs = [["search", "检索"], ["ask", "问答"], ["compare", "岗位对比"], ["audit", "审计"]];
    const bar = '<div class="case-tabs">' + tabs.map(function (t) {
      return '<button class="case-tab' + (S.tab === t[0] ? " is-active" : "")
        + '" data-tab="' + t[0] + '">' + t[1] + "</button>";
    }).join("") + "</div>";

    let body = "";
    if (S.tab === "search") body = searchPane(UI, S);
    else if (S.tab === "ask") body = askPane(UI, S);
    else if (S.tab === "compare") body = comparePane(UI, S);
    else body = auditPane(UI, S);
    return bar + body;
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
      + '<div class="case-hits">' + hitList(UI, S, r) + "</div>";
  }

  function degradeBar(UI, data) {
    if (!data.degraded) return "";
    return '<div class="case-degrade"><b>降级告警</b>'
      + "<span>" + UI.esc(data.notice || "向量索引不可用或部分能力降级，结果可能不完整") + "</span></div>";
  }

  /** hidden_count 与 notice：产品差异化证据，必须显著、不折叠。 */
  function noticeBar(UI, data) {
    if (!data || (!data.notice && !data.hidden_count)) return "";
    const tone = data.hidden_count > 0 ? "warn" : "info";
    const lines = [];
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
      + "<summary>为什么是这些结果？（服务端编译出的过滤条件）</summary>"
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
      + "<code>" + UI.esc(hit.case_id) + "</code>"
      + (hit.chunk_id ? '<span class="case-chunk">' + UI.esc(hit.chunk_id) + "</span>" : "")
      + '<span class="case-hit-actions">'
      + '<button class="case-mini" data-act="detail" data-id="' + UI.esc(hit.case_id) + '">看全文</button>'
      + '<button class="case-mini" data-act="cite" data-id="' + UI.esc(hit.case_id) + '"'
      + ' data-chunk="' + UI.esc(hit.chunk_type || "summary") + '"'
      + ' data-ver="' + UI.esc(String(hit.content_version || 1)) + '">引用</button>'
      + "</span></div>"
      + (citedChunks.length ? '<div class="case-cite-inline">已引用 <code>'
        + UI.esc(S.cite.citation.case_id) + "#" + UI.esc(S.cite.citation.chunk_id)
        + "@v" + UI.esc(String(S.cite.citation.content_version)) + "</code>"
        + '<span class="case-hint">（三元组已落审计；这就是"可溯源"的凭据）</span></div>' : "")
      + "</div></article>";
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
          return '<code class="case-cite">' + UI.esc(x.case_id) + "#" + UI.esc(x.chunk_id)
            + "@v" + UI.esc(String(x.content_version)) + "</code>";
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
        return "<tr><td>" + row.seq + "</td><td>" + UI.esc(row.action) + "</td><td>"
          + UI.esc(row.actor) + "</td><td>" + UI.esc(row.case_id || "-") + "</td><td>"
          + (row.hit_count || 0) + "</td><td>" + (row.hidden_count || 0) + "</td><td><code>"
          + UI.esc((row.row_hash || "").slice(0, 10)) + "</code></td></tr>";
      }).join("");
      out += '<table class="case-audit-table"><thead><tr><th>#</th><th>动作</th><th>主体</th>'
        + "<th>案例</th><th>命中</th><th>不可见</th><th>row_hash</th></tr></thead><tbody>"
        + (rows || '<tr><td colspan="7">暂无记录</td></tr>') + "</tbody></table>";
      out += '<div class="case-hint">改动任意一行后重跑校验，链会断在那一行（'
        + "row_hash 覆盖全字段，篡改无法自愈）。</div>";
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

    /** 统一处理本屏错误：401 清令牌 + 专门文案；403 展示拒绝码；其余原样。 */
    const handle = function (err, prefix) {
      if (err.status === 401) {
        S.token = "";
        writeStore(TOKEN_KEY, null);
        S.authError = "案例会话已失效（" + (err.code || "401") + "）：请重新选择身份并建立会话";
        S.meta = null;
      } else if (err.status === 403) {
        S.authError = "请求被拒绝：" + (err.code || "403") + " —— " + err.message;
      } else {
        UI.toast((prefix || "请求失败") + "：" + err.message, "err");
      }
    };

    on("#case-session", "click", function () {
      const roleEl = root.querySelector("#case-role");
      const deptEl = root.querySelector("#case-dept");
      const plantEl = root.querySelector("#case-plant");
      const etypeEl = root.querySelector("#case-etype");
      const rankEl = root.querySelector("#case-rank");
      const identity = {
        role: (roleEl && roleEl.value) || S.identity.role,
        dept: ((deptEl && deptEl.value) || "").trim(),
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
      }).catch(function (err) {
        S.authError = "建立会话失败：" + (err.code ? err.code + " " : "") + err.message;
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
    on("#case-ingest-open", "click", function () {
      S.ingestForm = {}; S.ingestResult = null; repaint();
    });

    Array.prototype.forEach.call(root.querySelectorAll(".case-tab"), function (el) {
      el.addEventListener("click", function () {
        S.tab = el.getAttribute("data-tab");
        repaint();
        if (S.tab === "audit" && !S.audit) doAudit();
      });
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
          reason: (err.code || "") + " " + err.message,
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
              return { hits: [], hidden_count: 0, candidates: 0, error: err.code || err.message };
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
        UI.toast("对比失败：" + err.message, "err");
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
          UI.toast("入库失败：" + (err.code || err.message), "err");
          S.ingestResult = {
            case_id: "-", status: "-", redaction_kinds: [], acl_tokens: [],
            next_step: (err.code || "") + " " + err.message,
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
        render(root, ctx);
      })
      .catch(function (err) {
        S.metaLoading = false;
        if (err.status === 401) {
          S.token = "";
          writeStore(TOKEN_KEY, null);
          S.authError = "案例会话已失效（" + (err.code || "401") + "）：请重新选择身份并建立会话";
        } else {
          S.metaError = (err.code ? err.code + "：" : "") + err.message;
        }
        render(root, ctx);
      });
  }

  window.Screens = window.Screens || {};
  window.Screens.render_cases = render;

  // 供自动化检查使用（纯函数，不依赖 DOM）
  window.CaseScreen = { initialState: initialState, STATUS_META: STATUS_META };
})();
