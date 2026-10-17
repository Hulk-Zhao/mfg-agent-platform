/**
 * 智造协同平台 · 运营协同门户（app.js）
 *
 * 核心层：应用状态 / 后端 API 客户端（含降级探测）/ 侧边栏路由 / 通用 UI 组件。
 * 六屏渲染逻辑在 screens.js；演示数据在 demo-data.js。
 *
 * 设计纪律：
 * - 真实优先：问数 / SSE 事件流 / 人工确认 审批一律走真实后端；
 * - 降级不崩：后端不可达时进入演示模式，页面完整渲染 + 明确提示，无未捕获异常；
 * - 零依赖零构建：原生 JS + 内联 SVG。
 */
(function () {
  "use strict";

  /* ============================================================
   * 一、常量：导航 / 图标 / 角色
   * ============================================================ */

  const NAV_ITEMS = [
    { id: "dashboard", label: "工作台", title: "工作台", sub: "制造运营协同门户", icon: "grid", group: "运营视图" },
    { id: "orders", label: "订单履约看板", title: "订单履约看板", sub: "订单全链路管道 · 风险订单 · 部门协同", icon: "orders", group: "运营视图" },
    { id: "chat", label: "协同问数", title: "协同问数", sub: "对话式跨部门协同分析（真实后端链路）", icon: "chat", group: "智能协同", core: true },
    { id: "alerts", label: "预警中心", title: "预警中心", sub: "多域预警与联动处置", icon: "alert", group: "智能协同" },
    { id: "reports", label: "报告中心", title: "报告中心", sub: "内部简报与对客脱敏报告", icon: "report", group: "智能协同" },
    { id: "hitl", label: "审批确认中心", title: "审批确认中心", sub: "人工在环 · 关键决策审批（真实工单）", icon: "approve", group: "流程治理" },
    { id: "help", label: "帮助中心", title: "帮助中心", sub: "智能体协同架构 · 各智能体职责与边界 · 使用指引", icon: "info", group: "流程治理" },
  ];

  // 后端候选基址：同源优先（http(s) 托管时），随后是本机 8004 端口
  const API_PORT_CANDIDATES = ["http://127.0.0.1:8004", "http://localhost:8004"];

  // GET 请求超时（毫秒）。本地毫秒级，公网隧道单次往返约 1-2s；
  // 给 12s 余量，但必须有上限——否则网络抖动会让"正在加载…"永远转圈。
  const DEFAULT_TIMEOUT_MS = 12000;

  // 后端健康探测超时（毫秒）。
  // 必须是 GET 超时的 2 倍以上：公网隧道（Cloudflare / cpolar）下单次
  // 往返实测 1.2-2s，探测并发 2 个候选基址时更慢；早期版本用 1800ms，
  // 公网下必定 abort → 误判"后端离线"→ 全站退回演示数据。实测证据：
  // 同一 URL curl 200，页面却显示"后端离线 · 演示数据"。
  const PROBE_TIMEOUT_MS = 9000;

  // probe() 的 in-flight Promise（并发守卫，见下方 probe 注释）
  let probeInFlight = null;

  const ICONS = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    orders: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4a2 2 0 0 1 6 0"/><path d="M9 11h6M9 15h4"/>',
    chat: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
    alert: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/>',
    approve: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>',
    refresh: '<path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    x: '<path d="M18 6L6 18M6 6l12 12"/>',
    chevron: '<path d="M9 18l6-6-6-6"/>',
    database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
    flask: '<path d="M9 3h6"/><path d="M10 3v6L5.3 18.4A2 2 0 0 0 7 21.4h10a2 2 0 0 0 1.7-3L14 9V3"/><path d="M7.5 15h9"/>',
    truck: '<path d="M1 3h15v13H1z"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.2"/><circle cx="18.5" cy="18.5" r="2.2"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.09a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    shield: '<path d="M12 22s8-3 8-10V5l-8-3-8 3v7c0 7 8 10 8 10z"/>',
    zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
    warn: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
    send: '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>',
    arrow: '<path d="M5 12h14M12 5l7 7-7 7"/>',
    box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05"/><path d="M12 22.08V12"/>',
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    link: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    filter: '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
    play: '<path d="M5 3l14 9-14 9V3z" fill="currentColor" stroke="none"/>',
    sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    eyeoff: '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="M1 1l22 22"/>',
  };

  /* ============================================================
   * 二、应用状态
   * ============================================================ */

  const state = {
    screen: "dashboard",
    role: "gm",
    backend: { online: false, base: "", checking: true },
    perms: null,     // 治理权限目录快照（/api/v2/governance/permissions）
    screenState: {}, // 各屏持久状态（对话记录、审批处理结果等，重渲染不丢）
  };

  function currentRole() {
    return window.DEMO.ROLES.find((r) => r.id === state.role) || window.DEMO.ROLES[0];
  }

  /* ---------- 登录态（演示版免密码：选择岗位身份即登录） ---------- */

  const USER_KEY = "mfg_gateway_user_v1";

  function loadUser() {
    try {
      const raw = localStorage.getItem(USER_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      const role = window.DEMO.ROLES.find((r) => r.id === parsed.roleId);
      return role ? { roleId: role.id } : null;
    } catch (err) {
      return null;
    }
  }

  function saveUser(roleId) {
    try {
      localStorage.setItem(USER_KEY, JSON.stringify({ roleId, at: Date.now() }));
    } catch (err) { /* 隐私模式/存储禁用时忽略 */ }
  }

  function clearUser() {
    try { localStorage.removeItem(USER_KEY); } catch (err) { /* ignore */ }
  }

  /** 头像字母：优先岗位显式 abbr（GM/MFG/SQE…），缺失时回退中文名首字。 */
  function roleAbbr(role) {
    return role.abbr || role.name.slice(0, 1);
  }

  function roleCardHtml(role, selected) {
    const cls = `role-card${selected ? " selected" : ""}`;
    const abbr = roleAbbr(role);
    const wideCls = abbr.length >= 4 ? " wide" : "";
    return `<button class="${cls}" type="button" data-role="${esc(role.id)}" aria-pressed="${selected}">
      <span class="role-avatar tone-${esc(role.tone || "blue")}${wideCls}">${esc(abbr)}</span>
      <span class="role-main">
        <span class="role-name">${esc(role.name)}</span>
        <span class="role-user">${esc(role.user)} · ${esc(role.dept)}</span>
        <span class="role-scope">${esc(role.scope || "")}</span>
        <span class="role-scenarios">${(role.scenarios || []).map((s) => `<em>${esc(s)}</em>`).join("")}</span>
      </span>
      <span class="role-tick">${icon("check", 13)}</span>
    </button>`;
  }

  /** 按域分组渲染登录卡片：组标题 sticky（滚动时当前域标题始终可见）。
   *  分组缺失（ROLE_GROUPS 未定义）时回退为单组平铺，保证旧数据可用。 */
  function roleGroupsHtml(roles, selected) {
    const groups = (window.DEMO.ROLE_GROUPS || []).filter((g) => (g.roles || []).length);
    if (!groups.length) {
      return roles.map((r) => roleCardHtml(r, r.id === selected)).join("");
    }
    const byId = {};
    roles.forEach((r) => { byId[r.id] = r; });
    const seen = {};
    let html = "";
    groups.forEach((g) => {
      const cards = (g.roles || [])
        .map((id) => byId[id])
        .filter(Boolean)
        .map((r) => { seen[r.id] = 1; return roleCardHtml(r, r.id === selected); })
        .join("");
      if (!cards) return;
      html += `<div class="role-group-title section-title">${esc(g.name)}`
        + `<span class="section-extra">× ${(g.roles || []).length}</span></div>`
        + `<div class="role-group-grid">${cards}</div>`;
    });
    const rest = roles.filter((r) => !seen[r.id]);
    if (rest.length) {
      html += `<div class="role-group-title section-title">其他`
        + `<span class="section-extra">× ${rest.length}</span></div>`
        + `<div class="role-group-grid">${rest.map((r) => roleCardHtml(r, r.id === selected)).join("")}</div>`;
    }
    return html;
  }

  function renderLogin() {
    const root = document.getElementById("login-root");
    if (!root) return;
    const roles = window.DEMO.ROLES;
    let selected = roles.some((r) => r.id === state.role) ? state.role : "";

    root.innerHTML = `
      <div class="login-shell">
        <section class="login-brand">
          <div class="login-brand-top">
            <div class="login-logo">${icon("box", 22)}</div>
            <div>
              <div class="login-brand-name">智造协同平台</div>
              <div class="login-brand-sub">Manufacturing Collaboration</div>
            </div>
          </div>
          <div class="login-brand-body">
            <h1>13 个智能体协同<br/>16 个部门一个入口<br/>一个入口完成协同</h1>
            <p>跨部门并行取证 · 跨域工单闭环 · 数据权限随岗位隔离</p>
            <ul class="login-points">
              <li><span>01</span><div><b>5 大协同场景</b>停线应急 / 来料异常 / 客诉 8D / 出货 ATP / 设备维保</div></li>
              <li><span>02</span><div><b>关键决策人在环</b>复线、对客 ETA、退换货、8D 关闭均需人工确认</div></li>
              <li><span>03</span><div><b>结论可审计</b>SQL 溯源 + 敏感字段按岗位脱敏 + 降级明示</div></li>
            </ul>
          </div>
          <div class="login-stats">
            <div class="login-stat"><b>13</b><span>智能体角色</span></div>
            <div class="login-stat"><b>5</b><span>协同场景</span></div>
            <div class="login-stat"><b>10</b><span>MCP 工具</span></div>
            <div class="login-stat"><b>390</b><span>自动化测试</span></div>
          </div>
          <div class="login-brand-foot">演示环境 · 免密码 · 选择岗位身份进入对应数据视图</div>
        </section>
        <section class="login-panel">
          <div class="login-panel-head">
            <h2>选择岗位身份</h2>
            <p>角色定义直接可见：决定数据权限、默认视图与可审批事项。演示版免密码，进入后可随时切换。</p>
          </div>
          <div class="role-grid">${roleGroupsHtml(roles, selected)}</div>
          <div class="login-panel-foot">
            <div class="login-selected" id="login-selected">${selected
              ? `已选择：${esc(currentRole().name)} · ${esc(currentRole().user)}`
              : "请选择一个岗位身份"}</div>
            <button class="login-enter" id="login-enter" type="button" ${selected ? "" : "disabled"}>
              进入系统 ${icon("arrow", 15)}
            </button>
          </div>
        </section>
      </div>`;

    root.hidden = false;
    const cards = root.querySelectorAll(".role-card");
    const btn = root.querySelector("#login-enter");
    const selLabel = root.querySelector("#login-selected");
    cards.forEach((card) => {
      const pick = () => {
        selected = card.dataset.role;
        cards.forEach((c) => {
          const on = c === card;
          c.classList.toggle("selected", on);
          c.setAttribute("aria-pressed", String(on));
        });
        const role = roles.find((r) => r.id === selected);
        if (selLabel && role) selLabel.textContent = `已选择：${role.name} · ${role.user}`;
        if (btn) btn.disabled = false;
      };
      card.addEventListener("click", pick);
      card.addEventListener("dblclick", () => { if (selected) enterApp(selected); });
    });
    if (btn) btn.addEventListener("click", () => { if (selected) enterApp(selected); });
  }

  function enterApp(roleId) {
    const role = window.DEMO.ROLES.find((r) => r.id === roleId) || window.DEMO.ROLES[0];
    state.role = role.id;
    saveUser(role.id);
    const root = document.getElementById("login-root");
    if (root) { root.hidden = true; root.innerHTML = ""; }
    updateChrome();
    toast(`已进入：${role.name} · ${role.user}`, "ok", 2600);
    startApp();
  }

  /* ============================================================
   * 三、通用 UI 工具
   * ============================================================ */

  function icon(name, size = 16, cls = "") {
    const path = ICONS[name] || ICONS.info;
    return `<svg class="${cls}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" `
      + `stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" `
      + `aria-hidden="true">${path}</svg>`;
  }

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function badge(text, cls = "badge-gray", dot = false) {
    return `<span class="badge ${cls}">${dot ? '<span class="badge-dot"></span>' : ""}${esc(text)}</span>`;
  }

  function spinner(cls = "") {
    return `<span class="spinner ${cls}"></span>`;
  }

  function toast(message, type = "info", duration = 3200) {
    const root = document.getElementById("toast-root");
    if (!root) return;
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    const ico = type === "ok" ? "check" : type === "err" ? "warn" : "info";
    node.innerHTML = `${icon(ico, 15)}<span>${esc(message)}</span>`;
    root.appendChild(node);
    setTimeout(() => {
      node.classList.add("leaving");
      setTimeout(() => node.remove(), 260);
    }, duration);
  }

  /** ISO 时间 → 本地 HH:MM:SS */
  function fmtClock(iso) {
    if (!iso) return "--:--:--";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "--:--:--";
    return d.toLocaleTimeString("zh-CN", { hour12: false });
  }

  /** ISO 时间 → 本地日期时间 */
  function fmtDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /** 剩余毫秒 → "1h12m05s" 样式 */
  function fmtRemain(ms) {
    if (ms <= 0) return "已超时";
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
    if (m > 0) return `${m}m${String(sec).padStart(2, "0")}s`;
    return `${sec}s`;
  }

  /** 演示数据标记 / 真实链路标记 */
  function demoFlag(text = "演示数据") {
    return `<span class="demo-flag">${icon("info", 11)}${esc(text)}</span>`;
  }

  function realFlag(text = "真实链路") {
    return `<span class="real-flag">${icon("check", 11)}${esc(text)}</span>`;
  }

  /* ============================================================
   * 四、后端 API 客户端（含降级探测）
   * ============================================================ */

  /**
   * 后端错误响应 → Error（带 status/detail，便于前端做具体提示）。
   * 403 的 detail 是"需：质量总监 / QA经理"这类中文串；
   * 409 会签未完成时 detail 是 {message, missing, progress} 结构化对象。
   */
  function apiError(status, data) {
    const detail = data ? data.detail : null;
    let message = (data && data.message) || `HTTP ${status}`;
    if (typeof detail === "string" && detail) {
      message = detail;
    } else if (detail && typeof detail === "object") {
      message = detail.message || message;
      if (Array.isArray(detail.missing) && detail.missing.length) {
        const who = detail.missing.map((m) => m.role_name || m.role).join("、");
        message = `${message}——待 ${who}${detail.progress ? `（${detail.progress}）` : ""}`;
      }
    }
    const err = new Error(message);
    err.status = status;
    err.detail = detail;
    err.kind = status === 403 ? "permission_denied"
      : (status === 409 && detail && detail.missing) ? "signoff_incomplete"
        : status === 409 ? "conflict" : status === 400 ? "bad_request" : "http";
    return err;
  }

  const api = {
    base() { return state.backend.base; },
    online() { return state.backend.online; },

    /** 探测后端：同源优先，随后本机 8004；全部失败 → 演示模式。
     *  候选基址并行探测（任一成功即用），总耗时 ≤ 单次超时，避免串行等待。
     *
     *  并发守卫：boot()（已登录用户）与 enterApp()（登录页点岗位）都可能触发
     *  startApp() → probe()。公网隧道下探测耗时 1-2s，两次并发时后返回的
     *  候选会互相覆盖 state.backend，导致前端停在"检测中"且误判离线。
     *  复用同一个 in-flight Promise 即可。 */
    probe() {
      if (probeInFlight) return probeInFlight;
      probeInFlight = (async () => {
        const candidates = [];
        if (/^https?:$/.test(location.protocol)) candidates.push(location.origin);
        API_PORT_CANDIDATES.forEach((c) => { if (!candidates.includes(c)) candidates.push(c); });
        const probeOne = async (base) => {
          try {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
            const resp = await fetch(`${base}/api/v2/health`, { signal: ctrl.signal });
            clearTimeout(timer);
            if (resp.ok) {
              const data = await resp.json();
              if (data && data.status === "ok") return base;
            }
          } catch (err) { /* 候选失败 */ }
          return null;
        };
        const results = await Promise.all(candidates.map(probeOne));
        const base = results.find((r) => r !== null) || null;
        if (base) {
          state.backend = { online: true, base, checking: false };
          return true;
        }
        state.backend = { online: false, base: "", checking: false };
        return false;
      })().finally(() => { probeInFlight = null; });
      return probeInFlight;
    },

    async _post(path, body) {
      const resp = await fetch(`${this.base()}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.code !== 0) {
        throw apiError(resp.status, data);
      }
      return data.data;
    },

    /** 公网隧道下单次往返约 1-2s；给足余量但必须有上限，
     *  否则网络抖动时前端会无限转圈（"正在加载待办队列…"永不消失）。 */
    async _get(path, timeoutMs) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs || DEFAULT_TIMEOUT_MS);
      let resp;
      try {
        resp = await fetch(`${this.base()}${path}`, { signal: ctrl.signal });
      } finally {
        clearTimeout(timer);
      }
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.code !== 0) {
        throw apiError(resp.status, data);
      }
      return data.data;
    },

    /** 统一问数入口（TwoLevelRouter → YamlDagExecutor） */
    query(query, requestId, userCtx) {
      return this._post("/api/v2/query", { query, request_id: requestId, user_ctx: userCtx });
    },

    /** 预开请求事件队列（保证 SSE 实时订阅，不丢事件） */
    openEventQueue(requestId) {
      return this._post("/api/v2/events/open", { request_id: requestId });
    },

    /** 订阅 SSE 事件流，返回 EventSource（调用方负责 close） */
    subscribeEvents(requestId, onEvent) {
      const url = `${this.base()}/api/v2/events?request_id=${encodeURIComponent(requestId)}&wait_s=90`;
      const es = new EventSource(url);
      es.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data);
          onEvent(event);
        } catch (err) {
          console.warn("[sse] 事件解析失败:", err);
        }
      };
      return es;
    },

    /** 人工确认 待办队列（审批即待办：传 roles 则只返回我的待办） */
    workflowPending(roles) {
      const list = (roles || []).filter(Boolean);
      const qs = list.length
        ? "?" + list.map((r) => `role=${encodeURIComponent(r)}`).join("&")
        : "";
      return this._get(`/api/v2/workflow/pending${qs}`);
    },

    /** 单工单详情（会签后局部刷新卡片） */
    workflowTask(taskId) {
      return this._get(`/api/v2/workflow/${encodeURIComponent(taskId)}`);
    },

    /** 治理权限目录（角色×权限矩阵 + 7 个 HITL 槽位；前端门控提示用） */
    governancePermissions() { return this._get("/api/v2/governance/permissions"); },

    /** 选定处置动作（按该动作 propose/cosign 生成会签要求）。uid：一人一签判定依据 */
    workflowSelectAction(taskId, actionKey, approver, roles, dept, comment, uid) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/select-action`,
        { approver, comment: comment || "", action_key: actionKey,
          role: (roles || []).join(","),
          user_ctx: { user_id: uid || approver, dept, roles: roles || [] } });
    },

    /** 会签（多角色背书，缺一不可）。uid：一人一签判定依据 */
    workflowCosign(taskId, approver, roles, dept, comment, decision, uid) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/cosign`,
        { approver, comment: comment || "", decision: decision || "",
          role: (roles || []).join(","),
          user_ctx: { user_id: uid || approver, dept, roles: roles || [] } });
    },

    /** 人工确认 审批通过（waiting_hitl → running / done）。uid：一人一签判定依据 */
    workflowApprove(taskId, approver, comment, final, decision, roles, dept, uid) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/approve`,
        { approver, comment, final: !!final, decision: decision || "",
          role: (roles || []).join(","),
          user_ctx: roles ? { user_id: uid || approver, dept, roles } : undefined });
    },

    /** 人工确认 审批驳回（→ closed）。uid：一人一签判定依据 */
    workflowReject(taskId, approver, comment, decision, roles, dept, uid) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/reject`,
        { approver, comment, decision: decision || "",
          role: (roles || []).join(","),
          user_ctx: roles ? { user_id: uid || approver, dept, roles } : undefined });
    },
  };

  /* ============================================================
   * 五、侧边栏 / 顶栏 / 路由
   * ============================================================ */

  function renderNav() {
    const nav = document.getElementById("main-nav");
    if (!nav) return;
    let html = "";
    let lastGroup = "";
    NAV_ITEMS.forEach((item) => {
      if (item.group !== lastGroup) {
        html += `<div class="nav-group-label">${esc(item.group)}</div>`;
        lastGroup = item.group;
      }
      html += `<button class="nav-item${item.id === state.screen ? " active" : ""}" `
        + `data-screen="${item.id}" title="${esc(item.label)}">${icon(item.icon, 17)}`
        + `<span>${esc(item.label)}</span></button>`;
    });
    nav.innerHTML = html;
    nav.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => navigate(btn.dataset.screen));
    });
  }

  function updateChrome() {
    const item = NAV_ITEMS.find((n) => n.id === state.screen) || NAV_ITEMS[0];
    const title = document.getElementById("topbar-title");
    const sub = document.getElementById("topbar-sub");
    if (title) title.textContent = item.title;
    if (sub) sub.textContent = item.sub;
    renderNav();
    const role = currentRole();
    const avatar = document.getElementById("user-avatar");
    const name = document.getElementById("user-name");
    const roleEl = document.getElementById("user-role");
    if (avatar) {
      const abbr = roleAbbr(role);
      avatar.textContent = abbr;
      avatar.classList.toggle("wide", abbr.length >= 4);
    }
    if (name) name.textContent = role.user;
    if (roleEl) roleEl.textContent = role.name;

    // 后端状态
    const dot = document.getElementById("backend-dot");
    const text = document.getElementById("backend-text");
    const envMode = document.getElementById("env-mode");
    if (state.backend.checking) {
      if (dot) dot.className = "dot dot-checking";
      if (text) text.textContent = "连接检测中";
    } else if (state.backend.online) {
      if (dot) dot.className = "dot dot-ok";
      if (text) text.textContent = "后端在线 · 真实链路";
      if (envMode) { envMode.textContent = "完整模式"; envMode.className = "env-value online"; }
    } else {
      if (dot) dot.className = "dot dot-off";
      if (text) text.textContent = "后端离线 · 演示模式";
      if (envMode) { envMode.textContent = "演示模式"; envMode.className = "env-value offline"; }
    }
  }

  function navigate(screenId) {
    const target = NAV_ITEMS.find((n) => n.id === screenId);
    if (!target) return;
    state.screen = screenId;
    if (location.hash !== `#/${screenId}`) location.hash = `#/${screenId}`;
    updateChrome();
    renderScreen();
  }

  function refreshScreen() {
    renderScreen();
  }

  function renderScreen() {
    const root = document.getElementById("screen-root");
    if (!root) return;
    let fn = window.Screens && window.Screens[`render_${state.screen}`];
    if (!fn && state.screen === "help" && window.HelpScreen) fn = window.HelpScreen.render;
    if (typeof fn !== "function") {
      root.innerHTML = `<div class="empty">屏幕未注册: ${esc(state.screen)}</div>`;
      return;
    }
    try {
      fn(root, {
        state, api, UI: { icon, esc, badge, toast, spinner, fmtClock, fmtDateTime, fmtRemain, demoFlag, realFlag },
        DEMO: window.DEMO, perms: state.perms,
        navigate, refreshScreen, setRole, currentRole,
      });
    } catch (err) {
      console.error("[screen] 渲染失败:", err);
      root.innerHTML = `<div class="empty">页面渲染异常：${esc(err && err.message ? err.message : String(err))}</div>`;
    }
  }

  function setRole(roleId) {
    state.role = roleId;
    saveUser(roleId);
    updateChrome();
    refreshScreen();
  }

  /* ============================================================
   * 六、全局搜索（演示数据范围）
   * ============================================================ */

  function wireGlobalSearch() {
    const input = document.getElementById("global-search");
    if (!input) return;
    let panel = null;

    const closePanel = () => { if (panel) { panel.remove(); panel = null; } };

    const search = (kw) => {
      const results = [];
      const k = kw.trim();
      if (!k) return results;
      window.DEMO.ORDERS.risks.forEach((o) => {
        if ((o.no + o.product + o.customer + o.shortage).includes(k)) {
          results.push({ screen: "orders", title: `${o.no} · ${o.customer}`, sub: o.shortage, type: "订单" });
        }
      });
      window.DEMO.ALERTS.forEach((a) => {
        if ((a.title + a.detail + a.id).includes(k)) {
          results.push({ screen: "alerts", title: a.title, sub: `${a.id} · ${a.owner}`, type: "预警" });
        }
      });
      window.DEMO.DEMO_人工确认.forEach((t) => {
        if ((t.title + t.task_uid).includes(k)) {
          results.push({ screen: "hitl", title: t.title, sub: `${t.task_uid} · ${t.approver_roles.join("、")}`, type: "工单" });
        }
      });
      // "AI 搜问"范式：任何关键词都可直达协同问数（权限内检索的统一入口）
      const isTaskId = /^wf-[0-9a-f]{6,}$/i.test(k);
      const isBatch = /^batch[-_]/i.test(k);
      results.unshift({
        screen: "chat", ask: k,
        title: `去问数：「${k.length > 22 ? k.slice(0, 22) + "…" : k}」`,
        sub: isTaskId ? "识别为工单号 → 走协同链路查询该工单"
          : isBatch ? "识别为批次号 → 走协同链路查询该批次"
          : "以自然语言发起跨部门协同问数",
        type: isTaskId || isBatch ? "直达" : "问数",
      });
      return results.slice(0, 8);
    };

    const renderPanel = (results) => {
      closePanel();
      panel = document.createElement("div");
      panel.style.cssText = "position:absolute;top:34px;right:0;width:340px;background:#fff;border:1px solid var(--border);"
        + "border-radius:10px;box-shadow:var(--shadow-pop);z-index:100;overflow:hidden;";
      let html = "";
      if (!results.length) {
        html = `<div style="padding:16px;font-size:12.5px;color:var(--text-3);text-align:center;">未命中演示数据</div>`;
      } else {
        results.forEach((r, i) => {
          html += `<div class="search-hit" data-idx="${i}" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;gap:9px;align-items:center;">`
            + `<span class="badge badge-blue">${esc(r.type)}</span>`
            + `<div style="min-width:0;"><div style="font-size:12.8px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(r.title)}</div>`
            + `<div style="font-size:11.5px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(r.sub)}</div></div></div>`;
        });
        html += `<div style="padding:7px 14px;font-size:11px;color:var(--text-3);background:#F8FAFD;">检索范围为演示数据（订单 / 预警 / 工单）</div>`;
      }
      panel.innerHTML = html;
      const wrap = input.closest(".topbar-search");
      if (wrap) {
        wrap.style.position = "relative";
        wrap.appendChild(panel);
        panel.querySelectorAll(".search-hit").forEach((hit) => {
          hit.addEventListener("mouseenter", () => { hit.style.background = "#F2F7FC"; });
          hit.addEventListener("mouseleave", () => { hit.style.background = "#fff"; });
          hit.addEventListener("click", () => {
            const hit2 = results[Number(hit.dataset.idx)];
            if (hit2.ask) {
              try { sessionStorage.setItem("chat_prefill", hit2.ask); } catch (e) { /* 忽略 */ }
            }
            navigate(hit2.screen);
            closePanel();
            input.value = "";
          });
        });
      }
    };

    input.addEventListener("input", () => renderPanel(search(input.value)));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const r = search(input.value);
        if (r.length) {
          if (r[0].ask) {
            try { sessionStorage.setItem("chat_prefill", r[0].ask); } catch (e) { /* 忽略 */ }
          }
          navigate(r[0].screen);
          closePanel();
          input.value = "";
        } else toast("未命中演示数据，请尝试「SMT-1」「华为」「缺料」等关键词", "info");
      }
      if (e.key === "Escape") { closePanel(); input.blur(); }
    });
    document.addEventListener("click", (e) => {
      if (panel && !panel.contains(e.target) && e.target !== input) closePanel();
    });
  }

  /* ============================================================
   * 七、用户菜单 / 启动
   * ============================================================ */

  function wireUserMenu() {
    const chip = document.getElementById("user-chip");
    if (!chip) return;
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      const existing = document.getElementById("user-menu");
      if (existing) { existing.remove(); return; }
      const role = currentRole();
      const menu = document.createElement("div");
      menu.id = "user-menu";
      menu.className = "user-menu";
      menu.innerHTML = `
        <div class="user-menu-head">
          <div class="user-menu-name">${esc(role.user)}</div>
          <div class="user-menu-role">${esc(role.name)} · ${esc(role.dept)}</div>
          <div class="user-menu-scope">${esc(role.scope || "")}</div>
        </div>
        <button class="user-menu-item" type="button" id="menu-switch">切换岗位身份</button>
        <button class="user-menu-item danger" type="button" id="menu-logout">退出登录</button>`;
      const host = chip.parentElement;
      if (!host) return;
      host.style.position = "relative";
      host.appendChild(menu);
      menu.querySelector("#menu-switch").addEventListener("click", () => {
        menu.remove();
        renderLogin();
      });
      menu.querySelector("#menu-logout").addEventListener("click", () => {
        menu.remove();
        clearUser();
        state.role = "";   // 退出后必须重新选择身份，不保留上次选中
        renderLogin();
      });
      const close = (ev) => {
        if (!menu.contains(ev.target) && ev.target !== chip) {
          menu.remove();
          document.removeEventListener("click", close);
        }
      };
      setTimeout(() => document.addEventListener("click", close), 0);
    });
  }

  let chromeWired = false;

  function initChrome() {
    if (chromeWired) return;
    chromeWired = true;
    renderNav();
    updateChrome();
    wireGlobalSearch();
    wireUserMenu();

    const refreshBtn = document.getElementById("btn-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", () => {
      refreshScreen();
      toast("已刷新当前页面", "info", 1600);
    });

    window.addEventListener("hashchange", () => {
      const hash = location.hash.replace(/^#\/?/, "");
      if (hash && hash !== state.screen && NAV_ITEMS.some((n) => n.id === hash)) navigate(hash);
    });
  }

  async function startApp() {
    await api.probe();
    await loadPermCatalog();
    updateChrome();

    const hash = location.hash.replace(/^#\/?/, "");
    navigate(NAV_ITEMS.some((n) => n.id === hash) ? hash : "dashboard");

    if (!state.backend.online) {
      toast("后端离线：当前为演示模式（问数回放 / 看板演示数据）", "info", 5000);
    }
  }

  /**
   * 加载治理权限目录（角色×权限矩阵 + HITL 槽位）。
   * 前端只用它做"门控提示"（灰显/文案），安全校验始终在后端。
   * 加载失败不阻塞：前端退化为"按钮可用 + 后端 403 给出明确原因"。
   */
  async function loadPermCatalog() {
    if (!state.backend.online) return null;
    try {
      state.perms = await api.governancePermissions();
    } catch (err) {
      console.warn("[governance] 权限目录加载失败，前端门控退化为后端裁决:", err);
      state.perms = null;
    }
    return state.perms;
  }

  async function boot() {
    initChrome();
    const saved = loadUser();
    if (saved) {
      state.role = saved.roleId;
      updateChrome();
      await startApp();
    } else {
      renderLogin();
    }
  }

  /* ============================================================
   * 八、导出
   * ============================================================ */

  window.App = {
    state, api, navigate, refreshScreen, setRole,
    currentRole,
    showLogin: renderLogin,
    enterApp,
    UI: { icon, esc, badge, toast, spinner, fmtClock, fmtDateTime, fmtRemain, demoFlag, realFlag },
    NAV_ITEMS,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
