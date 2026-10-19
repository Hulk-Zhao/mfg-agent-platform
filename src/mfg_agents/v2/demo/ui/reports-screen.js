/**
 * 报告中心 · 报告一等对象（列表 → 详情 → 审定 → 派发 → 导出）
 *
 * 加载顺序：必须在 `screens.js` **之后**（本文件覆盖 `window.Screens.render_reports`，
 * 把原来的静态两栏替换为真实报告列表）。既有 `screens.js` 的函数体不改——
 * 只新增本文件，并在 `index.html` 加一行 script。
 *
 * 三条约定：
 *
 * 1. **后端离线降级**：`ctx.api.online()===false` 时回放 `DEMO.REPORTS`
 *    + localStorage 归档，页面挂「演示数据」徽标——绝不假装是真链路产出。
 * 2. **问数回链**：复用全站既有机制（`sessionStorage.chat_prefill` + navigate），
 *    不新造一套跳转协议。
 * 3. **深链回协同屏**：关联工单 / 预警 / 案例点击时写入各自的 prefill 键
 *    再 navigate——屏侧消费（见 screens.js 的 `hitl_prefill` 等）。
 */
(function () {
  "use strict";

  /* ---------------- 常量 ---------------- */

  var STATUS_CN = {
    draft: "草稿", pending_review: "待审定", approved: "已审定",
    rejected: "已退回", dispatched: "已派发", archived: "已归档",
  };
  var STATUS_CLS = {
    draft: "badge-gray", pending_review: "badge-amber", approved: "badge-green",
    rejected: "badge-red", dispatched: "badge-blue", archived: "badge-gray",
  };
  var SCENARIO_CN = {
    line_stop_v2: "停线应急", customer_complaint_v2: "客诉 8D",
    incoming_quality_v2: "来料异常", delivery_status_v2: "交付风险",
    equipment_warning_v2: "设备预警", order_change_impact_v2: "订单变更影响",
    spc_anomaly_v2: "SPC 异常", order_review_v2: "接单评审",
  };
  var SOURCE_CN = {
    chat: "协同问数", task: "审批工单", alert: "预警中心",
    case: "案例检索", kpi: "运营看板", manual: "手工生成",
  };
  var MODULE_JUMP = {
    task: { screen: "hitl", prefill: "hitl_prefill", label: "去审批中心" },
    alert: { screen: "alerts", prefill: "", label: "去预警中心" },
    case: { screen: "cases", prefill: "cases_prefill", label: "去案例检索" },
  };
  var LIMIT = 20;

  /** 模块级界面状态（跨重渲染保留筛选条件，与其它屏的 screenState 同思路）。 */
  var S = {
    q: "", scenario: "", status: "", visibility: "", risk: "",
    items: [], total: 0, stats: null, scope: null, err: "", offline: false,
    open: "", tab: "brief", detail: null, detailErr: "",
    genOpen: false, busy: false,
  };

  /* ---------------- 小工具 ---------------- */

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function statusCn(s) { return STATUS_CN[s] || s || "—"; }

  function scenarioCn(s) {
    if (!s) return "通用问答";
    return SCENARIO_CN[s] || s;
  }

  function sourceCn(s) { return SOURCE_CN[s] || s || "—"; }

  function takePrefill(key) {
    try {
      var v = sessionStorage.getItem(key);
      if (v) sessionStorage.removeItem(key);
      return v || "";
    } catch (e) { return ""; }
  }

  function fmtTime(iso) {
    var t = String(iso || "");
    return t ? t.slice(0, 16).replace("T", " ") : "—";
  }

  function briefRows(brief) {
    var keys = Object.keys(brief || {});
    if (!keys.length) return "";
    return keys.map(function (k) {
      return '<div class="brief-item"><span class="k">' + esc(k)
        + '</span><span class="v">' + esc(brief[k]) + "</span></div>";
    }).join("");
  }

  function riskBadge(level) {
    if (!level) return "";
    var cls = level === "A" ? "badge-red" : level === "B" ? "badge-amber"
      : level === "C" ? "badge-blue" : "badge-gray";
    return '<span class="badge ' + cls + '">风险 ' + esc(level) + "</span>";
  }

  function visLabel(v) { return v === "customer" ? "含对客脱敏" : "内部可见"; }

  /* ---------------- 数据加载 ---------------- */

  function queryParams(extra) {
    var p = { limit: LIMIT, offset: 0, q: S.q, scenario_key: S.scenario,
              status: S.status, visibility: S.visibility, risk_level: S.risk };
    if (extra) { Object.keys(extra).forEach(function (k) { p[k] = extra[k]; }); }
    return p;
  }

  async function load(ctx, keepDetail) {
    var UI = ctx.UI;
    if (!ctx.api || !ctx.api.online()) {
      S.offline = true;
      paintOffline(ctx);
      return;
    }
    S.offline = false;
    try {
      var res = await Promise.all([ctx.api.reportStats(), ctx.api.reportsList(queryParams())]);
      if (ctx.state.screen !== "reports") return;
      S.stats = res[0];
      S.items = (res[1] && res[1].items) || [];
      S.total = (res[1] && res[1].total) || 0;
      S.scope = (res[1] && res[1].scope) || null;
      S.err = "";
    } catch (err) {
      if (ctx.state.screen !== "reports") return;
      S.err = UI.friendlyError(err).text;
      S.items = []; S.total = 0; S.stats = null;
    }
    if (!keepDetail) { S.open = ""; S.detail = null; }
    paint(ctx);
  }

  async function loadDetail(ctx, reportId) {
    var UI = ctx.UI;
    S.detail = null; S.detailErr = "";
    paintDetail(ctx);
    try {
      var d = await ctx.api.reportDetail(reportId);
      if (ctx.state.screen !== "reports" || S.open !== reportId) return;
      S.detail = d;
    } catch (err) {
      if (ctx.state.screen !== "reports" || S.open !== reportId) return;
      S.detailErr = UI.friendlyError(err).text;
    }
    paintDetail(ctx);
  }

  /** 演示数据（离线）→ 与在线结构同构的报告行。 */
  function demoReports(ctx) {
    var out = [];
    try {
      var arch = JSON.parse(localStorage.getItem("report_archive") || "[]");
      arch.forEach(function (r, i) {
        out.push({
          report_id: "demo-arch-" + i,
          scenario_key: r.scenario || "qa",
          source_module: "chat",
          title: r.title || "未命名报告",
          visibility: "internal",
          status: "draft",
          risk_level: "",
          degraded: false,
          actor: "演示回放",
          internal_brief: r.brief || { 报告正文: r.content || "" },
          customer_report: r.content || "",
          provenance: new Array(Number(r.provenance_count) || 0).fill(null).map(function () {
            return { domain: "demo", note: "演示归档（协同问数回放）" };
          }),
          data_gaps: [],
          linked: { tasks: [], alerts: [], cases: [], policies: [] },
          versions: [{ ver: "v1.0", time: r.ts || "", actor: "演示回放",
                       badge: "初版", note: "协同问数归档（演示数据）" }],
          created_at: r.ts || "",
        });
      });
    } catch (e) { /* 隐私模式忽略 */ }

    var R = (ctx.DEMO && ctx.DEMO.REPORTS) || null;
    if (R) {
      out.unshift({
        report_id: "demo-line-stop",
        scenario_key: (R.internal.route || {}).scenario || "line_stop_v2",
        source_module: "chat",
        title: R.internal.title || "停线应急协同 · 内部简报",
        visibility: R.customer && R.customer.html ? "customer" : "internal",
        status: "draft",
        risk_level: "B",
        degraded: true,
        actor: "系统生成",
        run_id: R.internal.run_id || "",
        internal_brief: (R.internal.brief || []).reduce(function (acc, b) {
          acc[b.k] = b.v; return acc; }, {}),
        customer_report: (R.customer && R.customer.plain) || "",
        mask_map: R.mask_map || [],
        provenance: [{ channel: "report", scenario: "line_stop_v2",
                       note: "演示数据：内部简报 + 对客脱敏（禁词表兜底）" }],
        data_gaps: [],
        linked: { tasks: (R.internal.hitl || "").split(" / ").filter(Boolean).map(function (t) {
          var uid = t.trim().split(" ")[0];
          return { task_uid: uid, title: t.trim(), slot: "", status: "" };
        }), alerts: [], cases: [], policies: [] },
        versions: R.versions || [],
        created_at: (R.versions && R.versions[0] && R.versions[0].time) || "",
      });
    }
    return out;
  }

  /* ---------------- 渲染：骨架 ---------------- */

  function shell(ctx, UI) {
    var role = ctx.currentRole ? ctx.currentRole() : null;
    return ''
      + '<div class="content-inner rp-screen">'
      + '  <div class="page-head">'
      + '    <div class="page-title">' + UI.icon("report", 20) + "报告中心</div>"
      + '    <div class="head-extra">'
      + '      <button class="btn btn-outline btn-sm" id="rp-gen-btn" title="按来源模块生成报告">'
      + UI.icon("sparkle", 13) + "生成报告</button>"
      + '      <button class="btn btn-outline btn-sm" id="rp-refresh-btn" title="刷新报告列表">'
      + UI.icon("refresh", 13) + "刷新</button>"
      + '    </div>'
      + '    <div class="page-desc">报告一等对象：链路产出自动归档 → 筛选/详情 → 溯源/关联 → 审定 → 派发 → 导出。'
      + '      <span id="rp-mode-flag"></span>'
      + '      <span id="rp-role-flag"></span>'
      + "    </div>"
      + '  </div>'
      + '  <div class="rp-kpis" id="rp-kpis"></div>'
      + '  <div class="rp-toolbar card" id="rp-toolbar"></div>'
      + '  <div class="rp-gen card" id="rp-gen" hidden></div>'
      + '  <div class="rp-list" id="rp-list"></div>'
      + '  <div class="rp-detail" id="rp-detail"></div>'
      + "</div>";
  }

  function toolbarHtml(ctx, UI) {
    function opt(value, label, current) {
      return '<option value="' + esc(value) + '"'
        + (String(current) === String(value) ? " selected" : "") + ">"
        + esc(label) + "</option>";
    }
    var scenarioOpts = '<option value="">全部场景</option>'
      + Object.keys(SCENARIO_CN).map(function (k) {
        return opt(k, SCENARIO_CN[k], S.scenario);
      }).join("")
      + opt("qa", "通用问答", S.scenario);
    var statusOpts = '<option value="">全部状态</option>'
      + Object.keys(STATUS_CN).map(function (k) {
        return opt(k, STATUS_CN[k], S.status);
      }).join("");
    var visOpts = '<option value="">全部可见范围</option>'
      + opt("internal", "内部可见", S.visibility)
      + opt("customer", "含对客脱敏", S.visibility);
    var riskOpts = '<option value="">全部风险</option>'
      + ["A", "B", "C", "D"].map(function (k) { return opt(k, "风险 " + k, S.risk); }).join("");

    return ''
      + '<div class="rp-filters">'
      + '  <div class="rp-search">'
      + '    <input id="rp-q" type="text" placeholder="搜索：标题 / 场景 / 来源模块…"'
      + ' autocomplete="off" value="' + esc(S.q) + '" />'
      + "  </div>"
      + '  <select id="rp-f-scenario" class="rp-select">' + scenarioOpts + "</select>"
      + '  <select id="rp-f-status" class="rp-select">' + statusOpts + "</select>"
      + '  <select id="rp-f-visibility" class="rp-select">' + visOpts + "</select>"
      + '  <select id="rp-f-risk" class="rp-select">' + riskOpts + "</select>"
      + '  <button class="btn btn-outline btn-sm" id="rp-clear">清空筛选</button>'
      + "  <span class=\"rp-count\" id=\"rp-count\"></span>"
      + "</div>";
  }

  function kpisHtml(ctx, UI, stats, offline) {
    var s = stats || {};
    function card(label, value, cls, hint) {
      return '<div class="rp-kpi">'
        + '<div class="rp-kpi-num ' + (cls || "") + '">' + esc(value) + "</div>"
        + '<div class="rp-kpi-label">' + esc(label) + "</div>"
        + (hint ? '<div class="rp-kpi-hint">' + esc(hint) + "</div>" : "")
        + "</div>";
    }
    var body = card("报告总数", s.total || 0, "")
      + card("待审定", s.pending_review || 0, (s.pending_review ? "is-warn" : ""), "走 hitl.report_publish 会签")
      + card("已派发", s.dispatched || 0, (s.dispatched ? "is-ok" : ""))
      + card("含数据缺口", s.with_gaps || 0, (s.with_gaps ? "is-danger" : ""), "缺口报告不参与派发口径");
    if (offline) {
      body = card("演示报告", S.items.length, "", "离线回放 · 非真实链路产出");
    }
    return body;
  }

  /* ---------------- 渲染：列表 ---------------- */

  function listHtml(ctx, UI) {
    if (S.err) {
      return '<div class="empty">' + UI.icon("warn", 26)
        + '<div style="margin-top:8px">' + esc(S.err) + "</div>"
        + '<button class="case-btn" id="rp-retry" style="margin-top:10px">重试</button></div>';
    }
    if (!S.items.length) {
      return '<div class="empty">' + UI.icon("report", 26)
        + '<div style="margin-top:8px">暂无报告——在「协同问数」发起提问后，'
        + "生成的报告会自动归档到这里；也可用右上角「生成报告」按来源模块手工生成。</div></div>";
    }
    return S.items.map(function (r) {
      var open = S.open === r.report_id;
      return ''
        + '<div class="rp-item' + (open ? " is-open" : "") + '" data-rid="'
        + esc(r.report_id) + '" tabindex="0">'
        + '  <div class="rp-item-head">'
        + '    <span class="badge badge-blue">' + esc(scenarioCn(r.scenario_key)) + "</span>"
        + '    <span class="rp-item-title">' + esc(r.title) + "</span>"
        + "    " + riskBadge(r.risk_level)
        + '    <span class="badge ' + (r.visibility === "customer" ? "badge-amber" : "badge-gray")
        + '">' + esc(visLabel(r.visibility)) + "</span>"
        + (r.degraded ? '<span class="badge badge-amber">降级</span>' : "")
        + '    <span class="badge ' + (STATUS_CLS[r.status] || "badge-gray") + '">'
        + esc(statusCn(r.status)) + "</span>"
        + '    <span class="rp-item-time">' + esc(fmtTime(r.created_at)) + "</span>"
        + "  </div>"
        + '  <div class="rp-item-sub">'
        + "来源 " + esc(sourceCn(r.source_module))
        + " · 溯源 " + esc((r.provenance || []).length) + " 条"
        + ((r.data_gaps || []).length ? " · 数据缺口 " + esc(r.data_gaps.length) + " 项" : "")
        + ((r.linked && (r.linked.tasks || []).length)
            ? " · 关联工单 " + esc(r.linked.tasks.length) + " 个" : "")
        + (r.internal_hidden ? " · 内部简报对当前岗位不可见" : "")
        + "  </div>"
        + "</div>";
    }).join("");
  }

  /** 演示（离线）列表：全部来自演示数据，必须挂徽标。 */
  function paintOffline(ctx) {
    var UI = ctx.UI;
    S.offline = true;
    S.items = demoReports(ctx);
    S.total = S.items.length;
    S.stats = null;
    var flag = document.getElementById("rp-mode-flag");
    if (flag) flag.innerHTML = UI.demoFlag("后端离线 · 演示回放");
    var kp = document.getElementById("rp-kpis");
    if (kp) kp.innerHTML = kpisHtml(ctx, UI, null, true);
    var tb = document.getElementById("rp-toolbar");
    if (tb) tb.innerHTML = toolbarHtml(ctx, UI);
    wireToolbar(ctx);
    var list = document.getElementById("rp-list");
    if (list) list.innerHTML = '<div class="section-title">演示报告'
      + '<span class="section-extra">离线回放 · 仅演示链路结构</span></div>' + listHtml(ctx, UI);
    wireList(ctx);
    updateCount();
  }

  function paint(ctx) {
    var UI = ctx.UI;
    var flag = document.getElementById("rp-mode-flag");
    if (flag) {
      flag.innerHTML = S.offline ? UI.demoFlag("后端离线 · 演示回放")
        : UI.realFlag("报告中心真实归档（SQLite + 治理链）");
    }
    var kp = document.getElementById("rp-kpis");
    if (kp) kp.innerHTML = kpisHtml(ctx, UI, S.stats, S.offline);
    var tb = document.getElementById("rp-toolbar");
    if (tb) tb.innerHTML = toolbarHtml(ctx, UI);
    wireToolbar(ctx);
    var list = document.getElementById("rp-list");
    if (list) list.innerHTML = listHtml(ctx, UI);
    wireList(ctx);
    updateCount();
    paintDetail(ctx);
  }

  function updateCount() {
    var el = document.getElementById("rp-count");
    if (el) el.textContent = "共 " + S.total + " 份 · 显示 " + S.items.length + " 份";
  }

  /* ---------------- 渲染：详情 ---------------- */

  function paintDetail(ctx) {
    var box = document.getElementById("rp-detail");
    if (!box) return;
    var UI = ctx.UI;
    if (!S.open) { box.innerHTML = ""; return; }

    var r = S.detail;
    var head = '<div class="card-head">'
      + '<div class="card-title">' + UI.icon("report", 16) + "报告详情</div>"
      + '<div class="card-extra">'
      + '<span class="badge ' + (STATUS_CLS[(r && r.status) || "draft"] || "badge-gray") + '">'
      + esc(statusCn(r && r.status)) + "</span>"
      + '<button class="icon-btn rp-close" id="rp-detail-close" title="收起详情">✕</button>'
      + "</div></div>";

    if (S.detailErr) {
      box.innerHTML = '<div class="card rp-detail-card">' + head
        + '<div class="empty">' + UI.icon("warn", 24) + '<div style="margin-top:8px">'
        + esc(S.detailErr) + "</div></div></div>";
      wireDetail(ctx);
      return;
    }
    if (!r) {
      box.innerHTML = '<div class="card rp-detail-card">' + head
        + '<div class="empty">' + UI.spinner("lg") + '<div style="margin-top:10px">正在加载报告详情…</div></div></div>';
      wireDetail(ctx);
      return;
    }

    var meta = '<div class="rp-meta">'
      + "<span>报告号 <b>" + esc(r.report_id) + "</b></span>"
      + "<span>场景 " + esc(scenarioCn(r.scenario_key)) + "</span>"
      + "<span>来源 " + esc(sourceCn(r.source_module)) + "</span>"
      + "<span>生成人 " + esc(r.actor || "—") + "</span>"
      + "<span>" + esc(fmtTime(r.created_at)) + "</span>"
      + (r.run_id ? "<span>run_id " + esc(r.run_id) + "</span>" : "")
      + "</div>";

    var tabs = [
      ["brief", "内部简报"],
      ["customer", "对客脱敏"],
      ["trace", "数据溯源"],
      ["linked", "关联协同"],
      ["versions", "版本留痕"],
    ];
    var tabHtml = '<div class="rp-tabs">' + tabs.map(function (t) {
      return '<button class="rp-tab' + (S.tab === t[0] ? " active" : "")
        + '" data-tab="' + t[0] + '">' + esc(t[1]) + "</button>";
    }).join("") + "</div>";

    box.innerHTML = '<div class="card rp-detail-card">' + head
      + '<div class="rp-detail-body">' + meta + tabHtml
      + '<div class="rp-tab-body">' + tabBody(ctx, UI, r) + "</div>"
      + actionBar(ctx, UI, r) + "</div></div>";
    wireDetail(ctx);
  }

  function tabBody(ctx, UI, r) {
    if (S.tab === "brief") {
      if (r.internal_hidden || !Object.keys(r.internal_brief || {}).length) {
        return '<div class="rp-note">' + UI.icon("eyeoff", 13)
          + " 内部简报对当前岗位不可见（该报告为内部专报，或当前岗位不在内部可见名单内）。</div>";
      }
      return '<div class="brief-grid">' + briefRows(r.internal_brief) + "</div>";
    }
    if (S.tab === "customer") {
      if (!r.customer_report) {
        return '<div class="rp-note">该场景不产出对客报告（无对客模板或以内部简报为交付物）。</div>';
      }
      var map = (r.mask_map || []).map(function (m) {
        var from = typeof m === "string" ? m : (m.from || "");
        var to = typeof m === "string" ? "" : (m.to || "");
        return '<span class="mask-map-item"><s>' + esc(from) + "</s>→<b>" + esc(to) + "</b></span>";
      }).join("");
      return '<div class="rp-cust">'
        + '<div class="rp-cust-text">' + esc(r.customer_report) + "</div>"
        + (map ? '<div class="mask-legend"><div class="mask-legend-title">'
            + UI.icon("eyeoff", 13) + " 脱敏映射（禁词表兜底）</div>"
            + '<div class="mask-map-row">' + map + "</div></div>" : "")
        + "</div>";
    }
    if (S.tab === "trace") {
      var prov = (r.provenance || []).map(function (p, i) {
        if (typeof p === "string") return "<li>" + esc(p) + "</li>";
        return "<li><b>" + esc(p.domain || p.channel || "—") + "</b> "
          + esc(p.note || p.ref || JSON.stringify(p)) + "</li>";
      }).join("");
      var gaps = (r.data_gaps || []).length
        ? '<div class="rp-gap">' + UI.icon("warn", 13) + " 数据缺口<ul>"
          + r.data_gaps.map(function (g) { return "<li>" + esc(g) + "</li>"; }).join("")
          + "</ul></div>"
        : '<div class="rp-note">无数据缺口（各域取证完整）。</div>';
      return '<div class="rp-trace"><div class="rp-block-title">数据溯源（' + (r.provenance || []).length
        + ' 条）</div><ul class=\"rp-prov\">' + (prov || "<li>暂无溯源记录</li>") + "</ul>" + gaps + "</div>";
    }
    if (S.tab === "linked") {
      var L = r.linked || {};
      function group(key, label, jump) {
        var arr = L[key] || [];
        if (!arr.length) return "";
        return '<div class="rp-linked-group"><div class="rp-block-title">' + esc(label)
          + "（" + arr.length + "）</div>"
          + arr.map(function (t) {
            var id = t.task_uid || t.case_id || t.alert_id || t.id || "";
            var title = t.title || t.name || id;
            return '<div class="rp-linked-item" data-jump="' + esc(jump) + '" data-ref="'
              + esc(id) + '" tabindex="0">'
              + "<span>" + esc(title) + "</span>"
              + (id ? '<span class="rp-linked-ref">' + esc(id) + "</span>" : "")
              + '<span class="rp-linked-go">跳转 →</span></div>';
          }).join("") + "</div>";
      }
      var body = group("tasks", "关联工单", "task") + group("alerts", "关联预警", "alert")
        + group("cases", "关联案例", "case");
      return body || '<div class="rp-note">该报告暂无关联协同对象。</div>';
    }
    if (S.tab === "versions") {
      var vs = r.versions || [];
      if (!vs.length) return '<div class="rp-note">暂无版本留痕。</div>';
      return '<div class="version-list">' + vs.map(function (v) {
        return '<div class="version-item"><div class="version-dot"></div>'
          + '<div class="version-main">'
          + '<div class="version-title">' + esc(v.ver) + " "
          + '<span class="badge badge-blue">' + esc(v.badge || "留痕") + "</span></div>"
          + '<div class="version-meta"><span>' + esc(fmtTime(v.time)) + "</span><span>"
          + esc(v.actor || "—") + "</span></div>"
          + '<div class="version-note">' + esc(v.note || "") + "</div>"
          + "</div></div>";
      }).join("") + "</div>"
        + '<div class="rp-note">版本留痕记录状态流转与修订说明（不伪造内容差异对比）。</div>';
    }
    return "";
  }

  function actionBar(ctx, UI, r) {
    if (S.offline) {
      return '<div class="rp-actions"><span class="rp-note">离线演示模式：'
        + "审定 / 派发需后端在线（走真实治理链）。</span>"
        + '<button class="btn btn-outline btn-sm" data-act="ask">去问数追问</button>'
        + '<button class="btn btn-outline btn-sm" data-act="print">打印 / PDF</button>'
        + "</div>";
    }
    var st = r.status || "draft";
    var canSubmit = st === "draft" || st === "rejected";
    var canApprove = st === "pending_review";
    var canDispatch = st === "approved";
    return '<div class="rp-actions">'
      + '<button class="btn btn-outline btn-sm" data-act="ask">' + UI.icon("chat", 12) + "追问</button>"
      + (canSubmit ? '<button class="btn btn-sm" data-act="submit">发起审定</button>' : "")
      + (canApprove ? '<button class="btn btn-sm" data-act="approve">审定通过</button>' : "")
      + (canApprove ? '<button class="btn btn-outline btn-sm" data-act="reject">驳回</button>' : "")
      + (canDispatch ? '<button class="btn btn-sm" data-act="dispatch">派发</button>' : "")
      + '<button class="btn btn-outline btn-sm" data-act="print">' + UI.icon("report", 12) + "打印 / PDF</button>"
      + '<button class="btn btn-outline btn-sm" data-act="export-md">导出 MD</button>'
      + '<button class="btn btn-outline btn-sm" data-act="export-html">导出 HTML</button>'
      + "</div>";
  }

  /* ---------------- 事件绑定 ---------------- */

  function wireToolbar(ctx) {
    var tb = document.getElementById("rp-toolbar");
    if (!tb) return;
    var q = document.getElementById("rp-q");
    if (q) {
      var t = null;
      q.addEventListener("input", function () {
        clearTimeout(t);
        t = setTimeout(function () { S.q = q.value.trim(); load(ctx); }, 300);
      });
    }
    function bind(id, key) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("change", function () { S[key] = el.value; load(ctx); });
    }
    bind("rp-f-scenario", "scenario");
    bind("rp-f-status", "status");
    bind("rp-f-visibility", "visibility");
    bind("rp-f-risk", "risk");
    var clear = document.getElementById("rp-clear");
    if (clear) {
      clear.addEventListener("click", function () {
        S.q = ""; S.scenario = ""; S.status = ""; S.visibility = ""; S.risk = "";
        load(ctx);
      });
    }
  }

  function wireList(ctx) {
    var list = document.getElementById("rp-list");
    if (!list) return;
    var retry = document.getElementById("rp-retry");
    if (retry) retry.addEventListener("click", function () { load(ctx); });
    list.querySelectorAll("[data-rid]").forEach(function (item) {
      function toggle() {
        var id = item.dataset.rid;
        if (S.open === id) {
          S.open = ""; S.detail = null; paintDetail(ctx);
          item.classList.remove("is-open");
          return;
        }
        S.open = id; S.tab = "brief"; S.detail = null;
        list.querySelectorAll("[data-rid]").forEach(function (x) {
          x.classList.toggle("is-open", x.dataset.rid === id);
        });
        loadDetail(ctx, id);
        var box = document.getElementById("rp-detail");
        if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      item.addEventListener("click", toggle);
      item.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });
  }

  function wireDetail(ctx) {
    var box = document.getElementById("rp-detail");
    if (!box) return;
    var close = document.getElementById("rp-detail-close");
    if (close) {
      close.addEventListener("click", function () {
        S.open = ""; S.detail = null;
        var list = document.getElementById("rp-list");
        if (list) {
          list.querySelectorAll("[data-rid]").forEach(function (x) { x.classList.remove("is-open"); });
        }
        paintDetail(ctx);
      });
    }
    box.querySelectorAll(".rp-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        S.tab = btn.dataset.tab;
        box.querySelectorAll(".rp-tab").forEach(function (x) {
          x.classList.toggle("active", x.dataset.tab === S.tab);
        });
        var bodyEl = box.querySelector(".rp-tab-body");
        if (bodyEl && S.detail) bodyEl.innerHTML = tabBody(ctx, ctx.UI, S.detail);
        wireDetail(ctx);
      });
    });
    box.querySelectorAll("[data-jump]").forEach(function (el) {
      el.addEventListener("click", function () { jumpTo(ctx, el.dataset.jump, el.dataset.ref); });
    });
    box.querySelectorAll("[data-act]").forEach(function (btn) {
      btn.addEventListener("click", function () { doAction(ctx, btn.dataset.act); });
    });
  }

  /* ---------------- 动作 ---------------- */

  function jumpTo(ctx, kind, ref) {
    var map = MODULE_JUMP[kind];
    if (!map) return;
    if (map.prefill && ref) {
      try { sessionStorage.setItem(map.prefill, ref); } catch (e) { /* 忽略 */ }
    } else if (ref) {
      try { sessionStorage.setItem("chat_prefill", "请核查报告关联对象「" + ref + "」的处置进展"); } catch (e) { /* 忽略 */ }
    }
    ctx.navigate(map.screen);
  }

  async function doAction(ctx, act) {
    var UI = ctx.UI;
    if (!S.detail) return;
    var r = S.detail;

    if (act === "ask") {
      var q = "关于「" + r.title + "」，请补充说明结论的数据依据与风险影响。";
      try { sessionStorage.setItem("chat_prefill", q); } catch (e) { /* 忽略 */ }
      ctx.navigate("chat");
      return;
    }
    if (act === "print") {
      try {
        var html = await ctx.api.reportExport(r.report_id, "html");
        var iframe = document.createElement("iframe");
        iframe.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
        document.body.appendChild(iframe);
        iframe.contentDocument.open();
        iframe.contentDocument.write(html);
        iframe.contentDocument.close();
        setTimeout(function () {
          try { iframe.contentWindow.focus(); iframe.contentWindow.print(); } catch (e) { /* 忽略 */ }
          setTimeout(function () { iframe.remove(); }, 2500);
        }, 320);
      } catch (err) {
        UI.toast("打印失败：" + UI.friendlyError(err).text, "err", 5000);
      }
      return;
    }
    if (act === "export-md" || act === "export-html") {
      var fmt = act === "export-md" ? "md" : "html";
      try {
        var text = await ctx.api.reportExport(r.report_id, fmt);
        var mime = fmt === "html" ? "text/html;charset=utf-8" : "text/markdown;charset=utf-8";
        var blob = new Blob([text], { type: mime });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = String(r.title || "report").replace(/[\\/:*?"<>|]/g, "_") + "." + fmt;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        UI.toast("已导出报告（" + fmt.toUpperCase() + "）", "ok", 3000);
      } catch (err2) {
        UI.toast("导出失败：" + UI.friendlyError(err2).text, "err", 5000);
      }
      return;
    }

    // 治理动作：submit / approve / reject / dispatch —— 意见按动作强制要求
    var needComment = act === "reject" || act === "dispatch";
    var label = act === "submit" ? "发起审定" : act === "approve" ? "审定通过"
      : act === "reject" ? "驳回报告" : "派发报告";
    var comment = window.prompt(
      label + "：" + (needComment ? "请填写理由（必填）" : "可填写意见"), "");
    if (comment === null) return;
    if (needComment && !comment.trim()) {
      UI.toast("该动作必须填写理由/意见", "warn", 4000);
      return;
    }
    if (S.busy) return;
    S.busy = true;
    try {
      if (act === "dispatch") {
        await ctx.api.reportDispatch(r.report_id, comment.trim());
      } else {
        await ctx.api.reportReview(r.report_id, act, comment.trim());
      }
      UI.toast(label + " 已完成", "ok", 3500);
      await loadDetail(ctx, r.report_id);
      await load(ctx, true);
    } catch (err3) {
      var f = UI.friendlyError(err3);
      UI.toast(label + "失败：" + f.text, "err", 6000);
    } finally {
      S.busy = false;
    }
  }

  /* ---------------- 就地生成报告 ---------------- */

  function genPanelHtml(ctx, UI) {
    var opts = [
      ["manual", "手工生成（自由填写）"],
      ["task", "从审批工单生成"],
      ["alert", "从预警生成"],
      ["case", "从案例生成"],
      ["kpi", "从看板指标生成"],
    ].map(function (o) {
      return '<option value="' + o[0] + '">' + esc(o[1]) + "</option>";
    }).join("");
    return ''
      + '<div class="card-head"><div class="card-title">' + UI.icon("sparkle", 16)
      + '生成报告</div><div class="card-extra">'
      + '<button class="icon-btn" id="rp-gen-close" title="收起">✕</button></div></div>'
      + '<div class="rp-gen-body">'
      + '  <label class="rp-field"><span>来源模块</span>'
      + '<select id="rp-gen-src" class="rp-select">' + opts + "</select></label>"
      + '  <label class="rp-field"><span>标题（必填）</span>'
      + '<input id="rp-gen-title" type="text" placeholder="如：SMT-1 停线影响内部简报" /></label>'
      + '  <label class="rp-field"><span>摘要</span>'
      + '<textarea id="rp-gen-summary" rows="2" placeholder="关键结论 / 现象 / 影响面"></textarea></label>'
      + '  <label class="rp-field"><span>关联引用（工单号 / 预警号 / 案例号，逗号分隔）</span>'
      + '<input id="rp-gen-refs" type="text" placeholder="wf-xxxxxxxx / alert-1 / case-001" /></label>'
      + '  <label class="rp-field"><span>风险评级</span>'
      + '<select id="rp-gen-risk" class="rp-select"><option value="">不评级</option>'
      + ["A", "B", "C", "D"].map(function (l) {
        return '<option value="' + l + '">' + l + "</option>";
      }).join("") + "</select></label>"
      + '  <div class="rp-gen-actions">'
      + '<button class="btn btn-sm" id="rp-gen-ok">生成并入库</button>'
      + '<span class="rp-note">手工生成的报告溯源标记为 manual，不冒充实链路产出。</span>'
      + "</div>"
      + "</div>";
  }

  function wireGen(ctx) {
    var box = document.getElementById("rp-gen");
    if (!box) return;
    var close = document.getElementById("rp-gen-close");
    if (close) {
      close.addEventListener("click", function () { S.genOpen = false; box.hidden = true; });
    }
    var ok = document.getElementById("rp-gen-ok");
    if (ok) {
      ok.addEventListener("click", async function () {
        var UI = ctx.UI;
        var src = (document.getElementById("rp-gen-src") || {}).value || "manual";
        var title = ((document.getElementById("rp-gen-title") || {}).value || "").trim();
        if (!title) { UI.toast("请填写报告标题", "warn", 3000); return; }
        var summary = ((document.getElementById("rp-gen-summary") || {}).value || "").trim();
        var refs = ((document.getElementById("rp-gen-refs") || {}).value || "")
          .split(",").map(function (x) { return x.trim(); }).filter(Boolean);
        var risk = (document.getElementById("rp-gen-risk") || {}).value || "";
        if (!ctx.api.online()) {
          UI.toast("后端离线：生成报告需后端在线", "warn", 4000);
          return;
        }
        try {
          await ctx.api.reportGenerate(src, {
            title: title, summary: summary, refs: refs, risk_level: risk,
            task_uid: src === "task" ? (refs[0] || "") : "", ref: refs[0] || "",
          });
          UI.toast("报告已生成并归档", "ok", 3500);
          S.genOpen = false; box.hidden = true;
          await load(ctx);
        } catch (err) {
          var f = UI.friendlyError(err);
          UI.toast("生成失败：" + f.text, "err", 6000);
        }
      });
    }
  }

  /* ---------------- 入口 ---------------- */

  function render(root, ctx) {
    var UI = ctx.UI;
    var prefill = takePrefill("report_prefill");
    if (prefill) S.q = prefill;
    S.busy = false;

    root.innerHTML = shell(ctx, UI);
    var tb = document.getElementById("rp-toolbar");
    if (tb) tb.innerHTML = toolbarHtml(ctx, UI);
    var kp = document.getElementById("rp-kpis");
    if (kp) kp.innerHTML = kpisHtml(ctx, UI, null, false);
    var list = document.getElementById("rp-list");
    if (list) list.innerHTML = '<div class="empty">' + UI.spinner("lg")
      + '<div style="margin-top:10px">正在加载报告…</div></div>';
    wireToolbar(ctx);
    wireGen(ctx);

    var genBtn = document.getElementById("rp-gen-btn");
    if (genBtn) {
      genBtn.addEventListener("click", function () {
        var box = document.getElementById("rp-gen");
        if (!box) return;
        if (box.hidden) { box.innerHTML = genPanelHtml(ctx, UI); box.hidden = false; wireGen(ctx); }
        else box.hidden = true;
      });
    }
    var refresh = document.getElementById("rp-refresh-btn");
    if (refresh) refresh.addEventListener("click", function () { load(ctx); });

    var roleFlag = document.getElementById("rp-role-flag");
    if (roleFlag) {
      var role = ctx.currentRole ? ctx.currentRole() : null;
      if (role) roleFlag.innerHTML = "当前岗位 " + UI.badge(role.name, "badge-blue");
    }

    load(ctx);
  }

  /* ---------------- 各协同屏就地「生成报告」按钮（全局委托） ----------------
   * screens.js / case-screen.js 只负责渲染 `data-report-gen` 按钮（值是一段
   * JSON 上下文），交互统一在这里处理——避免每个屏各写一份生成逻辑。
   */
  function readGenPayload(btn) {
    var raw = btn.getAttribute("data-report-gen");
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (e) { return null; }
  }

  document.addEventListener("click", async function (ev) {
    var target = ev.target;
    var btn = (target && target.closest) ? target.closest("[data-report-gen]") : null;
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    var payload = readGenPayload(btn);
    if (!payload) return;

    var app = window.App;
    var api = app && app.api;
    var toast = (app && app.UI && app.UI.toast) || function () {};
    if (!api || !api.online()) {
      toast("后端离线：生成报告需后端在线", "warn", 4000);
      return;
    }
    if (btn.disabled) return;
    btn.disabled = true;
    var original = btn.textContent;
    btn.textContent = "生成中…";
    try {
      await api.reportGenerate(payload.source_module || "manual", payload.context || {});
      toast("报告已生成并归档，正在跳转报告中心…", "ok", 3000);
      setTimeout(function () {
        if (app && app.navigate) app.navigate("reports");
      }, 500);
    } catch (err) {
      toast("生成失败：" + String(err && err.message ? err.message : err), "err", 5000);
      btn.disabled = false;
      btn.textContent = original;
    }
  }, true);

  window.ReportsScreen = { render: render };

  // 覆盖 screens.js 的静态版 render_reports（本文件在其之后加载）
  if (window.Screens && typeof window.Screens === "object") {
    window.Screens.render_reports = render;
  }
})();
