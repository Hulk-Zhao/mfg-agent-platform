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

  /* ============================================================
   * 一、导航
   *
   * `roles` 声明"哪些岗位可以看到本屏"，**省略 = 全员可见**。
   * 为什么必须有这层过滤：31 个岗位 × 11 屏平铺，一线岗位（MT / WH / EHS）
   * 与总经理看到完全相同的菜单——V3-UX 方案里已诊断过"操作工只需 3 个功能
   * 却面对几十个菜单 = 信息过载"，此前只做了简报/下钻，导航收敛一直未落地。
   *
   * 收敛原则（按岗位职责，不按部门）：
   *   - 与本人职责无关的**运营看板**隐藏（HR 不看 SPC，业务不看稼动率）；
   *   - **协同问数 / 咨询助手 / 预警 / 审批 / 办公 / 帮助**保持全员可见——
   *     异常可能来自任何岗位，会签也可能落到任何角色头上；
   *   - 每个岗位至少保留 7 项，不存在"菜单空了"的岗位。
   *
   * ⚠ 这属于**展示层收敛**，不是权限控制：真实数据权限由后端
   * /dashboard/* 的声明式数据范围目录裁决（见 docs 架构体检报告 P0-2）。
   * ============================================================ */
  /**
   * 侧边栏导航。**按使用场景排序，不按技术模块**：
   *
   *   运营视图（看现状）→ 智能协同（问原因/找办法）→ 流程治理（做决策）→ 复盘沉淀（出成果）
   *
   * 顺序即演示脚本：客户跟着从上往下点，走完就是「发现异常 → 追问根因 → 审批处置 → 产出报告」
   * 一个闭环，而不是逐屏介绍 12 个功能。分组必须相邻（renderNav 遇到 group 变化就输出
   * 组标题），跨组调 item 时务必同时挪位置。
   *
   * `roles` 省略 = 全员可见；声明则只有列出的岗位能看到（`visibleNav()` 过滤）。
   */
  const NAV_ITEMS = [
    { id: "dashboard", label: "工作台", title: "工作台", sub: "制造运营协同门户", icon: "grid", group: "运营视图" },
    { id: "orders", label: "订单履约看板", title: "订单履约看板", sub: "订单全链路管道 · 风险订单 · 部门协同", icon: "orders", group: "运营视图",
      roles: ["gm", "mfg", "biz", "cs", "pm", "pln", "mc", "scm", "procm", "proc", "whm", "wh", "finm", "fin"] },
    { id: "mfgboard", label: "制造综合看板", title: "制造综合看板", sub: "制造 + 品质双看板 · 稼动率/报废率/报工完成率/直通率/标准三率", icon: "activity", group: "运营视图",
      roles: ["gm", "mfg", "eq", "eqm", "mt", "qd", "qa", "sqe", "pqe", "pe", "ie", "te", "rdm", "rd", "me", "rdt", "npi", "pm", "ehs"] },
    { id: "spc", label: "SPC 过程能力看板", title: "SPC 过程能力看板", sub: "过程能力指数 Cpk/Pp/Ppk · 判定与越限预警 · 不良柏拉图", icon: "spc", group: "运营视图",
      roles: ["gm", "mfg", "qd", "pqe", "sqe", "qa", "pe", "te", "ie"] },
    // 预警是「看见异常」，属于运营视图；放在看板之后，形成「指标 → 异常」的自然下探。
    { id: "alerts", label: "预警中心", title: "预警中心", sub: "多域预警与联动处置", icon: "alert", group: "运营视图" },
    // 智能协同 = 拿到异常之后，问原因、找依据、找先例、办琐事。
    { id: "chat", label: "协同问数", title: "协同问数", sub: "对话式跨部门协同分析（真实后端链路）", icon: "chat", group: "智能协同", core: true },
    { id: "kbqa", label: "企业制度问答", title: "企业制度问答", sub: "企业制度 / 流程规范知识库问答 · 结论带出处（Milvus + LLM）", icon: "book", group: "智能协同" },
    { id: "cases", label: "制造案例检索", title: "制造案例检索", sub: "历史质量问题案例检索 · 权限前置过滤 · 引用可溯源", icon: "book", group: "智能协同" },
    // 「办公与数据速查」已拆分为两屏：数据速查（只读、受岗位数据权限约束）与 OA 协同（办公办理、走 HITL）。
    { id: "data", label: "数据速查", title: "数据速查", sub: "材料 / 成品 / 批次库存只读速查（统一口径 · 受岗位权限约束）", icon: "box", group: "智能协同" },
    { id: "oa", label: "OA 协同", title: "OA 协同", sub: "会议室 / 订餐办理 + 我的预订订单（高影响先人工确认）", icon: "clock", group: "智能协同" },
    // 流程治理 = 唯一需要「签字负责」的动作，单独成组，别和查询混在一起。
    // roles 与 permissions.yaml 的槽位持有者**必须一致**：行政人事不持有任何
    // hitl.* 权限，菜单就不该出现——否则点进去只有一句"本岗位不参与治理审批"，
    // 是让人白跑一趟。`test_待办可见性.py` 会交叉校验这两份数据。
    { id: "hitl", label: "审批确认中心", title: "审批确认中心", sub: "人工在环 · 关键决策审批（真实工单）", icon: "approve", group: "流程治理",
      roles: ["gm", "mfg", "eq", "sqe", "pqe", "qa", "rd", "pe", "pln", "mc", "proc",
              "wh", "biz", "fin", "qd", "eqm", "rdm", "scm", "procm", "whm", "finm",
              "ie", "npi", "pm", "te", "me", "rdt", "ehs", "cs", "mt"] },
    // 复盘沉淀 = 事情办完之后的产出与沉淀。
    { id: "reports", label: "报告中心", title: "报告中心", sub: "内部简报与对客脱敏报告", icon: "report", group: "复盘沉淀",
      roles: ["gm", "mfg", "qd", "sqe", "pqe", "qa", "cs", "biz", "pm"] },
    { id: "help", label: "帮助中心", title: "帮助中心", sub: "智能体协同架构 · 各智能体职责与边界 · 使用指引", icon: "info", group: "复盘沉淀" },
  ];

  // T05：场景屏由 scenes/registry.js 声明并合并进 NAV_ITEMS（T06–T11 只追加登记行，不改本文件）；
  // push 在字面量之外，test_架构守卫.py 的 NAV_ITEMS 正则解析不到，故不破坏导航收敛守卫。
  if (window.SceneRegistry && typeof window.SceneRegistry.navItems === "function") {
    window.SceneRegistry.navItems().forEach(function (item) { NAV_ITEMS.push(item); });
  }

  /** 岗位是否可见某屏。`roles` 省略 / 空数组 = 全员可见。
   *  同时匹配岗位 id 与其治理角色列表（demo-data 的 ROLES[].roles），
   *  避免两处角色口径不一致时菜单消失。 */
  function navVisible(item, role) {
    if (!item.roles || !item.roles.length) return true;
    if (!role) return true;
    const own = [role.id].concat(Array.isArray(role.roles) ? role.roles : []);
    return item.roles.some((r) => own.indexOf(r) !== -1);
  }

  /** 当前岗位可见的导航项（保持 NAV_ITEMS 原有顺序）。
   *  不在 state 里缓存：岗位切换时天然重算，不需要失效逻辑。 */
  function visibleNav() {
    const role = currentRole();
    const list = NAV_ITEMS.filter((item) => navVisible(item, role));
    return list.length ? list : NAV_ITEMS;
  }

  /** 旧屏名 → 新屏名。原 #/office（办公与数据速查）已拆为 #/data 与 #/oa，
   *  统一映射到 OA 协同，避免历史书签与演示脚本断链。 */
  const SCREEN_ALIAS = { office: "oa" };
  function canonScreen(id) { return SCREEN_ALIAS[id] || id; }


  // 后端候选基址：同源优先（http(s) 托管时），随后是本机 8004 端口
  const API_PORT_CANDIDATES = ["http://127.0.0.1:8004", "http://localhost:8004"];

  // GET 请求超时（毫秒）。本地毫秒级，公网隧道单次往返约 1-2s；
  // 给 12s 余量，但必须有上限——否则网络抖动会让"正在加载…"永远转圈。
  const DEFAULT_TIMEOUT_MS = 12000;

  // 协同问数（POST /api/v2/query）超时（毫秒）。这条链路最慢会走到
  // 「场景 DAG + RAG 根因分析 + LLM 报告」或「跨域协同 / 域智能体自主决策」
  // （agentic 单步 LLM 45s、最多 8 步）——用通用 12s 预算会在后端还在推理时
  // 就 abort 请求，用户只看到 "问数请求失败：signal is aborted without reason"
  // 这类 DOMException 原文（截图反馈）。给足推理预算（90s，与 kbqa 的 75s 同
  // 量级）；链路过程有 SSE 节点逐级点亮，等待不闷。
  const ASK_TIMEOUT_MS = 90000;

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
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    warn: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
    send: '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>',
    arrow: '<path d="M5 12h14M12 5l7 7-7 7"/>',
    box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05"/><path d="M12 22.08V12"/>',
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    spc: '<path d="M4 3v17h17"/><path d="M6 8h15M6 15h15"/><path d="M6.5 12.5l3-3.5 3 4 3-5 3 3.5 1.5-1.5"/>',
    layers: '<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
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
    token: "",       // 岗位访问令牌（F2）：HITL 写接口凭它鉴权；演示模式为空
    backend: { online: false, base: "", checking: true, health: null },
    perms: null,     // 治理权限目录快照（/api/v2/governance/permissions）
    permsError: "",  // 权限目录加载失败的用户友好提示（成功时清空）
    gates: [],       // HITL 闸门理由目录（/api/v2/governance/gates；R3）
    colleagues: [],  // 岗位 AI 同事能力面（/api/v2/colleagues，colleague.yaml 快照）
    colleague: "",   // 当前同事（role id；空 = 跟当前岗位）
    screenState: {}, // 各屏持久状态（对话记录、审批处理结果等，重渲染不丢）
  };

  function currentRole() {
    return window.DEMO.ROLES.find((r) => r.id === state.role) || window.DEMO.ROLES[0];
  }

  /* ---------- 岗位 AI 同事（D1/R2：colleague.yaml 的前端消费） ---------- */

  const COLLEAGUE_KEY = "mfg_colleague_v1";

  function colleagueOf(roleKey) {
    const id = String(roleKey || "");
    if (!id || !Array.isArray(state.colleagues)) return null;
    return state.colleagues.find((c) => c && c.role === id) || null;
  }

  /** 当前同事：手动切过 → 手动选择；否则跟当前岗位（同事默认随岗）。 */
  function currentColleague() {
    const picked = colleagueOf(state.colleague);
    if (picked) return picked;
    const role = currentRole();
    return colleagueOf(role && role.id) || null;
  }

  /** 切换同事（不换岗位：只是换「对话场景与推荐问题」的视角）。 */
  function setColleague(roleKey) {
    const c = colleagueOf(roleKey);
    if (!c) {
      console.warn("[colleague] 未知同事 role:", roleKey);
      return;
    }
    state.colleague = c.role;
    try { localStorage.setItem(COLLEAGUE_KEY, c.role); } catch (err) { /* 隐私模式忽略 */ }
    refreshScreen();
  }

  /* ---------- 登录态（演示版免密码：选择岗位身份即登录，并换取访问令牌） ---------- */

  const USER_KEY = "mfg_gateway_user_v1";
  /* 会话标记（sessionStorage）：仅在同一标签页内存活。
     产品口径（2026-10-20）：拿到公网链接的访客，每次新开链接默认落在登录界面；
     只有同标签页刷新才直接恢复会话，关掉标签页 / 新开链接一律先见登录页。 */
  const SESSION_FLAG_KEY = "mfg_gateway_session_v1";

  function saveUser(roleId, token) {
    try {
      localStorage.setItem(USER_KEY,
        JSON.stringify({ roleId, token: token || "", at: Date.now() }));
      sessionStorage.setItem(SESSION_FLAG_KEY, "1");
    } catch (err) { /* 隐私模式/存储禁用时忽略 */ }
  }

  /**
   * 读取上次登录态（刷新/重开页面后恢复会话）。
   * 岗位可能已被从 ROLES 移除或数据被手工篡改 → 一律降级为 null（回登录页），
   * 绝不返回脏 roleId，否则 currentRole() 会静默回退到 ROLES[0]，表现为"进了别人的岗位"。
   */
  function loadUser() {
    try {
      const raw = localStorage.getItem(USER_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      const id = data && data.roleId;
      if (!id) return null;
      const exists = window.DEMO.ROLES.some((r) => r.id === id);
      return exists ? { roleId: id, token: String(data.token || "") } : null;
    } catch (err) {
      return null;   // 隐私模式/存储禁用/JSON 损坏 → 当作未登录
    }
  }

  function clearUser() {
    try {
      localStorage.removeItem(USER_KEY);
      sessionStorage.removeItem(SESSION_FLAG_KEY);
    } catch (err) { /* ignore */ }
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
            <h1>18 个智能体协同<br/>16 个部门一个入口<br/>一个入口完成协同</h1>
            <p>跨部门并行取证 · 跨域工单闭环 · 数据权限随岗位隔离</p>
            <ul class="login-points">
              <li><span>01</span><div><b>7 大协同场景（YAML 声明式）</b>停线应急 / 来料异常 / 客诉 8D / 出货风险 / 设备维保 / 订单变更 / SPC 异常 · 新场景 = 新 YAML + 复用节点</div></li>
              <li><span>02</span><div><b>关键决策人在环（10 个 HITL 槽位）</b>复线 / 对客 ETA / 来料处置 / 8D 关闭 / 对客回复 / 交期承诺 / 停机窗口 / SPC 处置 / 接单评审 / 报告审定 · Agent 只发起、不放行</div></li>
              <li><span>03</span><div><b>结论可审计（权限同源 + 签名落库 + 全链路留痕）</b>业务数据 / 治理待办 / 案例访问三套权限分域 · 会签签名落 DB 账本（唯一索引防重复）· SQL 溯源 + 敏感字段脱敏 + 失败明示降级</div></li>
            </ul>
          </div>
          <div class="login-stats">
            <div class="login-stat"><b>18</b><span>智能体角色</span></div>
            <div class="login-stat"><b>7</b><span>协同场景</span></div>
            <div class="login-stat"><b>43</b><span>平台工具</span></div>
            <div class="login-stat"><b>2100+</b><span>自动化测试</span></div>
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
    state.token = "";          // 换岗位必须换令牌（令牌与岗位绑定），由 ensureSession 重新签发
    saveUser(role.id, "");
    const root = document.getElementById("login-root");
    if (root) { root.hidden = true; root.innerHTML = ""; }
    updateChrome();
    toast(`已进入：${role.name} · ${role.user}`, "ok", 2600);
    startApp();
  }

  /* ---------- 岗位令牌（F2）：登录换令牌 + 启动时校验/静默补签 ---------- */

  /**
   * 向服务端换取岗位访问令牌。后端离线（演示模式）时令牌置空——此模式下
   * 审批操作只做本地回放，本就不会命中后端写接口。
   */
  async function loginAs(roleId) {
    const role = window.DEMO.ROLES.find((r) => r.id === roleId);
    if (!role || !state.backend.online) { state.token = ""; return null; }
    try {
      const data = await api.login({
        roles: role.roles || [], role: role.id,
        user_id: role.uid || "", name: role.user || "", dept: role.dept || "",
      });
      state.token = (data && data.token) || "";
      saveUser(role.id, state.token);
      return data;
    } catch (err) {
      state.token = "";
      console.warn("[auth] 岗位令牌获取失败，审批写操作将被后端以 401 拒绝:", err);
      toast("岗位令牌获取失败：人工确认写操作暂不可用", "warn", 4000);
      return null;
    }
  }

  /** 令牌仍有效？（服务端 /auth/me 校验；失败不弹错，交给 ensureSession 补签） */
  async function tokenValid() {
    if (!state.token) return false;
    try { await api.me(); return true; } catch (err) { return false; }
  }

  /** 保证进入应用时持有可用令牌：有效则复用，失效/缺失则按当前岗位静默补签。 */
  async function ensureSession() {
    if (!state.backend.online || !state.role) { state.token = ""; return; }
    if (await tokenValid()) return;
    await loginAs(state.role);
  }

  /** 令牌失效（401）：清登录态并回到登录页，避免"看似已登录却处处写不进去"。 */
  let authExpiredAt = 0;
  function handleAuthExpired(detail) {
    const now = Date.now();
    if (now - authExpiredAt < 1000) return;   // 连发请求时只提示一次
    authExpiredAt = now;
    clearUser();
    state.token = "";
    state.role = "";
    const msg = (detail && detail.detail) ? String(detail.detail)
      : "登录状态已失效，请重新选择岗位";
    toast(msg, "bad", 4000);
    renderLogin();
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

  /** 演示数据标记 / 实时数据标记 */
  function demoFlag(text = "演示数据") {
    return `<span class="demo-flag">${icon("info", 11)}${esc(text)}</span>`;
  }

  function realFlag(text = "实时数据") {
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

  /* ============================================================
   * 用户友好错误文案（"加载失败优化"）
   * 原则：主提示说人话、告诉用户下一步做什么；技术原文进 tech 字段，
   * 由展示层放 <details> 折叠区，不主动裸露。
   * 没有这一层时，用户看到的是 "Failed to fetch"、
   * "throwHoss is not defined"、"HTTP 500" 这类原文——看不懂，
   * 也不知道该找谁。生产现场一句大白话 + 一个重试入口就够了。
   * ============================================================ */
  function friendlyError(err) {
    const msg = typeof err === "string" ? err
      : (err && err.message ? String(err.message) : "");
    const tech = msg;
    if (!err) return { text: "加载失败，请稍后重试", tech: "" };
    if (err.name === "AbortError" || /abort|timeout/i.test(msg))
      return { text: "加载超时：后端响应太慢，请稍后重试", tech };
    // 403/409/400 的 message 是后端写好的中文可操作文案（如"需要：质量总监"），
    // 直接透传比再包一层更清楚。
    if (err.kind === "permission_denied" || err.kind === "signoff_incomplete"
        || err.kind === "conflict" || err.kind === "bad_request")
      return { text: msg || "请求未被接受，请检查权限或输入后重试", tech };
    if (err.status >= 500)
      return { text: "服务端暂时出错，请稍后重试；若反复出现，请把本提示反馈给管理员", tech };
    if (err.status === 404)
      return { text: "请求的内容不存在或已失效，刷新页面可重试", tech };
    if (err.status === 429)
      return { text: "操作太频繁，请隔几秒再试", tech };
    // fetch 网络层失败不经 apiError 包装，message 是浏览器原文
    if (/failed to fetch|networkerror|load failed|err_internet|err_network|err_connection|econnrefused/i.test(msg))
      return { text: "网络连接失败：后端服务可能没有启动，请检查服务后重试", tech };
    // 页面脚本自身出错（引用错误/类型错误/语法错误）——刷新通常能恢复
    if (/is not defined|is not a function|cannot read|of (undefined|null)|unexpected token|syntaxerror/i.test(msg))
      return { text: "页面加载异常，请刷新页面重试；若反复出现，请把本提示反馈给管理员", tech };
    return { text: "操作未完成，请稍后重试；若反复出现，请把本提示反馈给管理员", tech };
  }

  const api = {
    base() { return state.backend.base; },
    online() { return state.backend.online; },

    /** 探测后端：**同源优先**，同源不可用才竞速本机 8004；全部失败 → 演示模式。
     *
     *  **两段式，顺序敏感**：
     *  ① 同源单打——页面由后端自己托管时（`http://host:port/` 打开），同源就是
     *     正确后端，直接采用，不再并行探测其余候选；
     *  ② 同源不可用（file:// 直开、前端与后端分离部署）→ 竞速本机候选，
     *     第一个成功立即采用并取消其余。
     *
     *  为什么①必须真正"优先"而不是只把同源丢进竞速池：本机同时挂着另一份
     *  **旧实例**时（典型：新代码起在 8005、8004 还遗留旧进程），两端
     *  `/api/v2/health` 都只有几毫秒，竞速是"先返回者胜"→ 结果近似抛硬币，
     *  前端会随机连到旧后端，现场表现为"改了不生效""页面还是旧行为"。
     *  同源优先把这个不确定性彻底去掉：谁托管页面，就用谁做后端。
     *
     *  第二轮仍保留竞速（而非集合）：`Promise.all` 等全部完成时，总耗时会由
     *  最慢的候选决定——`localhost:8004` 可能解析到未监听的 IPv6，白等满 9s
     *  才判在线，现场就是"卡在检测中"。
     *
     *  并发守卫：boot()（已登录用户）与 enterApp()（登录页点岗位）都可能触发
     *  startApp() → probe()。公网隧道下探测耗时 1-2s，两次并发时后返回的
     *  候选会互相覆盖 state.backend，导致前端停在"检测中"且误判离线。
     *  复用同一个 in-flight Promise 即可。 */
    probe() {
      if (probeInFlight) return probeInFlight;
      probeInFlight = (async () => {
        const controllers = [];
        const probeOne = async (base) => {
          const ctrl = new AbortController();
          controllers.push(ctrl);
          const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
          try {
            const resp = await fetch(`${base}/api/v2/health`, { signal: ctrl.signal });
            if (resp.ok) {
              const data = await resp.json();
              // status 只看"后端是否可达"：ok 与 degraded 都算在线。
              // degraded 表示部分依赖（向量库 / LLM / 治理库）降级，链路仍可跑，
              // 绝不能据此退回演示数据——那会掩盖真实故障。细分见 health.checks。
              if (data && (data.status === "ok" || data.status === "degraded")) {
                return { base, health: data };
              }
            }
          } catch (err) { /* 候选失败，或已被更快的候选取消 */ }
          finally { clearTimeout(timer); }
          return null;
        };

        // ① 同源优先：http(s) 托管时先单独打同源（不打其它候选，避免竞速误选旧实例）
        const sameOrigin = /^https?:$/.test(location.protocol) ? location.origin : "";
        let hit = sameOrigin ? await probeOne(sameOrigin) : null;

        // ② 同源不可用 → 竞速本机候选（file:// 直开 / 前后端分离部署的老路）
        if (!hit) {
          const candidates = API_PORT_CANDIDATES.filter((c) => c !== sameOrigin);
          if (candidates.length) {
            const attempts = candidates.map(probeOne);
            hit = await new Promise((resolve) => {
              let pending = attempts.length;
              let settled = false;
              attempts.forEach((p) => p.then((h) => {
                if (h && !settled) {
                  settled = true;
                  controllers.forEach((c) => c.abort());   // 其余探测立刻放弃，不再等
                  resolve(h);
                  return;
                }
                if (--pending === 0 && !settled) resolve(null);
              }));
            });
          }
        }

        if (hit) {
          state.backend = { online: true, base: hit.base, checking: false, health: hit.health || null };
          return true;
        }
        state.backend = { online: false, base: "", checking: false, health: null };
        return false;
      })().finally(() => { probeInFlight = null; });
      return probeInFlight;
    },

    /** POST 提交（登录 / 工单 / 问答等）。**必须带超时**：
     *
     *  2026-10-18 事故复盘——kbqa 提问走 _post，而后端冷启动（BGE-M3 预热
     *  与请求争用）/ Milvus 无 timeout 悬挂时，无超时的 fetch 会永远等待：
     *  前端「思考中…」转圈 6 分钟不消失，S.sending 锁死后续全部提问，
     *  用户无法区分"慢"与"死"。_get 一直有 AbortController，_post 是漏的。
     *
     *  超时时长由调用方按链路预算传：kbqaQuery 传 75s（覆盖后端 rag 路径
     *  LLM 60s 超时 + 余量），其余轻操作走默认 12s。 */
    async _post(path, body, timeoutMs) {
      const headers = { "Content-Type": "application/json" };
      if (state.token) headers.Authorization = `Bearer ${state.token}`;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs || DEFAULT_TIMEOUT_MS);
      let resp;
      try {
        resp = await fetch(`${this.base()}${path}`, {
          method: "POST",
          headers,
          body: JSON.stringify(body || {}),
          signal: ctrl.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.code !== 0) {
        if (resp.status === 401) handleAuthExpired(data);   // 令牌失效 → 回登录页
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
        const opts = { signal: ctrl.signal };
        if (state.token) opts.headers = { Authorization: `Bearer ${state.token}` };
        resp = await fetch(`${this.base()}${path}`, opts);
      } finally {
        clearTimeout(timer);
      }
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.code !== 0) {
        throw apiError(resp.status, data);
      }
      // 信封里的 scope（数据范围裁决凭据：谁看的 / 脱敏了哪些字段）挂到返回体上。
      // 放在信封而不是 data 里，是为了让 data 严格保持"看板载荷"语义；
      // 但调用方拿到的必须是同一个对象，否则"脱敏了没"这件事前端就无从呈现。
      const payload = data.data;
      if (data.scope && payload && typeof payload === "object") {
        payload.scope = data.scope;
      }
      return payload;
    },

    /** 岗位登录（F2）：用岗位身份换访问令牌 */
    login(payload) { return this._post("/api/v2/auth/login", payload); },

    /** 当前登录态校验（F2）：令牌有效则返回权威 user_ctx */
    me() { return this._get("/api/v2/auth/me", 6000); },

    /** 统一问数入口（TwoLevelRouter → YamlDagExecutor）。
     *  askCtx：结构化问数上下文（module/kpi/window/filters/view），看板问数
     *  口径直查用——与自然语言并行传递，后端未命中时静默回落纯文本链路。 */
    query(query, requestId, userCtx, askCtx) {
      const body = { query, request_id: requestId, user_ctx: userCtx };
      if (askCtx && typeof askCtx === "object") body.ask_ctx = askCtx;
      // 超时预算单列：问数链路含场景 DAG / RAG / LLM，不能用 12s 通用预算
      // （见 ASK_TIMEOUT_MS 注释——超时会以 "signal is aborted" 形态暴露给用户）。
      return this._post("/api/v2/query", body, ASK_TIMEOUT_MS);
    },

    /** 企业咨询助手：Milvus 制度库检索 + LLM 生成带出处回答（独立通道）。
     *
     *  75s 超时预算对齐后端最慢路径：rag = search_kb(≤15s Milvus 超时) +
     *  LLM(timeout=60s)；direct = LLM(timeout=30s)。前端多等 15s 余量，
     *  保证"后端先超时降级、前端不提前杀请求"。 */
    kbqaQuery(query, topK) {
      return this._post("/api/v2/kbqa", { query, top_k: topK || 3 }, 75000);
    },

    /** 企业咨询助手元数据：支持类别面板 + 知识库块数 */
    kbqaMeta() { return this._get("/api/v2/kbqa/meta"); },

    /** 企业咨询助手反馈：好评（rating>=4）的 RAG 回答进 FAQ 晋升待审队列 */
    kbqaFeedback(query, rating, extra) {
      return this._post("/api/v2/kbqa/feedback",
        Object.assign({ query: query, rating: rating || 0 }, extra || {}));
    },

    /* ---------- 企业综合助手（Agentic Agent：办公事务 + 制造数据只读速查） ---------- */

    /** 企业助手对话（强制定向该 Agent；返回含工具调用留痕 steps）
     *  attachments：本次携带的上传材料编号（attachment_id 列表），后端解析为
     *  本人材料元数据后交给 Agent，提交报销/病假时绑定到单据。 */
    officeChat(query, userCtx, attachments) {
      const body = { query, user_ctx: userCtx || null };
      if (attachments && attachments.length) body.attachments = attachments;
      return this._post("/api/v2/office/chat", body);
    },
    /** OA 材料登记（演示态"虚假上传"）：**只登记文件名与大小，不上传文件字节**。
     *  返回材料元数据（含 attachment_id 与 virtual=true）。 */
    officeUpload(file, kind, actor, note) {
      return this._post("/api/v2/office/upload", {
        filename: (file && file.name) || "",
        kind: kind || "其他",
        actor: actor || "",
        note: note || "",
        size: (file && file.size) || 0,
        content_type: (file && file.type) || "",
      });
    },
    /** 材料登记信息地址（演示态无原文件：打开为登记卡片说明） */
    officeAttachmentUrl(attachmentId) {
      return `${this.base()}/api/v2/office/attachment/${encodeURIComponent(attachmentId)}`;
    },
    /** 整理报销科目（确定性规则，无需 LLM）：二级科目拆分 + 必传/选传材料核对 */
    officeExpenseOrganize(payload) {
      return this._post("/api/v2/office/expense/organize", payload || {});
    },
    /** 给请假单补件（如病假条）：补件材料须为本人上传 */
    officeLeaveAttachment(leaveId, attachmentId, actor) {
      return this._post(`/api/v2/office/leave/${encodeURIComponent(leaveId)}/attachment`,
        { attachment_id: attachmentId, actor: actor || "" });
    },
    /** 办公能力元数据：后端实现（demo/真实）/ LLM 可用性 / 高影响阈值 */
    officeMeta() { return this._get("/api/v2/office/meta", 6000); },
    /** 制造数据速查快照（材料库存/缺料/成品/来料批次；按岗位权限过滤，无需 LLM） */
    officeSnapshot(roles) {
      const list = Array.isArray(roles) ? roles.join(",") : (roles || "");
      const qs = list ? `?roles=${encodeURIComponent(list)}` : "";
      return this._get(`/api/v2/office/snapshot${qs}`);
    },
    /** 数据查询权限申请：提交（dataset = 四块 key），返回 { request, demo_approval } */
    officeDataAccessRequest(payload) {
      return this._post("/api/v2/office/data-access/request", payload || {});
    },
    /** 数据查询权限申请：演示专用自动审批（模拟职责岗审批回调） */
    officeDataAccessApprove(requestId, actor) {
      return this._post(
        `/api/v2/office/data-access/requests/${encodeURIComponent(requestId)}/demo-approve`,
        { actor: actor || "" });
    },
    /** 我提交的数据查询权限申请（含已裁决状态） */
    officeDataAccessRequests(actor) {
      const qs = `?actor=${encodeURIComponent(actor || "")}`;
      return this._get(`/api/v2/office/data-access/requests${qs}`);
    },
    /** 会议室目录（可按最小容量筛选） */
    officeRooms(capacityMin) {
      const qs = capacityMin ? `?capacity_min=${encodeURIComponent(capacityMin)}` : "";
      return this._get(`/api/v2/office/rooms${qs}`);
    },
    /** 餐厅目录（员工食堂 / 商务餐厅） */
    officeRestaurants() { return this._get("/api/v2/office/restaurants"); },
    /** 某时段可用会议室（无需 LLM 的确定性查询） */
    officeAvailable(date, start, end, capacityMin) {
      const qs = `?date=${encodeURIComponent(date)}&start=${encodeURIComponent(start)}`
        + `&end=${encodeURIComponent(end)}`
        + (capacityMin ? `&capacity_min=${encodeURIComponent(capacityMin)}` : "");
      return this._get(`/api/v2/office/available${qs}`);
    },
    /** 某餐厅菜单（价格 + 是否需人工确认） */
    officeMenu(restaurantId) {
      return this._get(`/api/v2/office/menu/${encodeURIComponent(restaurantId)}`);
    },
    /** 我的会议室预订与订餐订单 */
    officeMine(actor) {
      return this._get(`/api/v2/office/my/${encodeURIComponent(actor)}`);
    },
    /** 取消本人预订 / 订单（kind: booking | order） */
    officeCancel(kind, id, actor) {
      return this._post("/api/v2/office/cancel", { kind, id, actor: actor || "" });
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

    /**
     * 人工确认 待办队列——**只能拿到「我的活」与「我治理域内的活」**。
     *
     * 后端以令牌角色为准（fail-closed）：无令牌 401、越权扩宽 role 403、
     * 无治理职责的岗位返回空队列。`roles` 只能收窄，不能借别人的眼睛。
     * 每项带 `visibility`：`mine` 可动作 / `domain` 域内只读。
     */
    workflowPending(roles) {
      const list = (roles || []).filter(Boolean);
      const qs = list.length
        ? "?" + list.map((r) => `role=${encodeURIComponent(r)}`).join("&")
        : "";
      return this._getScoped(`/api/v2/workflow/pending${qs}`);
    },

    /** 单工单详情（会签后局部刷新卡片）——可见性与队列同源，跨域取不到（404） */
    workflowTask(taskId) {
      return this._getScoped(`/api/v2/workflow/${encodeURIComponent(taskId)}`);
    },

    /** 治理权限目录（角色×权限矩阵 + 10 个 HITL 槽位；前端门控提示用） */
    governancePermissions() { return this._get("/api/v2/governance/permissions"); },

    /** HITL 闸门理由目录（槽位 → 为什么必须人工在环；R3 审批卡透出） */
    governanceGates() { return this._get("/api/v2/governance/gates"); },

    /** 岗位 AI 同事能力面（colleague.yaml 快照；同事切换器与推荐问题消费） */
    colleagues() { return this._get("/api/v2/colleagues"); },

    /**
     * 受控读接口（看板 / 下钻）：令牌失效时**静默补签一次并重试**。
     *
     * 为什么单独一层：后端重启会让 AUTH_SECRET 重签、所有 8 小时令牌一起失效，
     * 开发期几乎每改一次代码就会遇上。若把 401 当普通失败，看板的降级策略会
     * 回落演示数据 —— 界面看着正常、数据其实是编的，属于最坏的一类「假阳性」。
     * 补签失败（后端真挂了）才把 401 抛出去。
     */
    async _getScoped(path, timeoutMs) {
      try {
        return await this._get(path, timeoutMs);
      } catch (err) {
        if (!err || err.status !== 401 || !state.role) throw err;
        const signed = await loginAs(state.role);
        if (!signed) throw err;
        return this._get(path, timeoutMs);
      }
    },

    /** 制造看板（稼动率/报废率/报工完成率/测试直通率；按岗位数据范围裁决）
     *  window: "window"(最近7天，默认) | "day"(当日) | "month"(当月)；
     *  stage/productCategory: 板级「工段 / 产品分类」筛选（缺省 = 全部），
     *  KPI 卡与 TOP5 即所选范围汇总（后端用固定映射折出这两个维度）。 */
    manufacturingDashboard(window, stage, prodCat) {
      return this._getScoped("/api/v2/dashboard/manufacturing"
        + `?window=${encodeURIComponent(window || "window")}`
        + (stage ? `&stage=${encodeURIComponent(stage)}` : "")
        + (prodCat ? `&product_category=${encodeURIComponent(prodCat)}` : ""), 8000);
    },

    /** 品质看板（不良率/直通率 FPY/来料批次合格率/客诉闭环率；按岗位数据范围裁决）
     *  window: "window"(最近7天，默认) | "day"(当日) | "month"(当月)；
     *  stage/category: 板级「工段 / 缺陷类别」筛选（缺省 = 全部），KPI 卡即所选范围汇总 */
    qualityDashboard(window, stage, category) {
      const qs = (stage ? `&stage=${encodeURIComponent(stage)}` : "")
        + (category ? `&category=${encodeURIComponent(category)}` : "");
      return this._getScoped("/api/v2/dashboard/quality"
        + `?window=${encodeURIComponent(window || "window")}${qs}`, 8000);
    },

    /** 标准三率「班组明细」弹窗：单个班组的三率拆解（按工序 / 按日）+ 不良原因归因。
     *  与 qualityDashboard 的 standard_three_rates 同一套口径，弹窗数字与大盘可对账。 */
    qualityTeam(stage, team, window) {
      return this._getScoped("/api/v2/dashboard/quality/team"
        + `?stage=${encodeURIComponent(stage)}&team=${encodeURIComponent(team)}`
        + `&window=${encodeURIComponent(window || "window")}`, 8000);
    },

    /** 订单履约看板（OTD/OTIF/延期订单率/齐套率；客户身份按岗位脱敏）
     *  window: "window"(最近7天，默认) | "day"(当日) | "month"(当月) */
    ordersDashboard(window) {
      return this._getScoped("/api/v2/dashboard/orders"
        + (window ? `?window=${encodeURIComponent(window)}` : ""), 8000);
    },

    /** 订单状态管道「点环节」弹窗：该环节订单清单 + 汇总。
     *  与 ordersDashboard 的 pipeline 同源同口径（同一张表、同一个 due_date 窗口）。 */
    orderStage(stage, window) {
      return this._getScoped("/api/v2/dashboard/orders/stage"
        + `?stage=${encodeURIComponent(stage || "")}`
        + `&window=${encodeURIComponent(window || "window")}`, 8000);
    },

    /** 订单详情弹窗：单张订单全字段（客户身份按岗位脱敏，与风险订单清单同一套规则）。 */
    orderDetail(orderNo) {
      return this._getScoped("/api/v2/dashboard/orders/detail"
        + `?order_no=${encodeURIComponent(orderNo || "")}`, 8000);
    },

    /** 订单履约 2 级看板「点行」弹窗：单分组（客户·等级 / 型号）的订单清单。
     *  与 dashboardDrill(screen=orders) 的 L2 同一套 DRILL_DEFS 聚合口径，
     *  弹窗头部指标值与 2 级看板该行逐字段一致（可对账）。 */
    ordersGroup(kpi, entity, window) {
      const ent = entity || {};
      const qs = Object.keys(ent).filter((k) => ent[k])
        .map((k) => `&${k}=${encodeURIComponent(ent[k])}`).join("");
      return this._getScoped("/api/v2/dashboard/orders/group"
        + `?kpi=${encodeURIComponent(kpi)}${qs}`
        + `&window=${encodeURIComponent(window || "window")}`, 8000);
    },

    /** SPC 过程能力看板（逐特性 Cpk/Pp/Ppk 判定 + 子组越限预警 + 不良柏拉图；按岗位裁决）
     *  板级筛选：window（day|month|window）/ stage（工段）/ product_category（产品分类）。 */
    spcDashboard(window, stage, productCategory) {
      const qs = [
        `window=${encodeURIComponent(window || "window")}`,
        stage ? `stage=${encodeURIComponent(stage)}` : "",
        productCategory ? `product_category=${encodeURIComponent(productCategory)}` : "",
      ].filter(Boolean).join("&");
      return this._getScoped(`/api/v2/dashboard/spc?${qs}`, 8000);
    },

    /** SPC 2 级看板「点特性」弹窗：单特性能力拆解 + 逐日越限 + 原始记录（与 L1 同源）。 */
    spcCapability(characteristic, window, stage, productCategory) {
      const qs = [
        `characteristic=${encodeURIComponent(characteristic || "")}`,
        `window=${encodeURIComponent(window || "window")}`,
        stage ? `stage=${encodeURIComponent(stage)}` : "",
        productCategory ? `product_category=${encodeURIComponent(productCategory)}` : "",
      ].filter(Boolean).join("&");
      return this._getScoped(`/api/v2/dashboard/spc/capability?${qs}`, 8000);
    },

    /** SPC 测量记录弹窗：越限明细（overOnly）/ 单日明细（day）——与看板同口径可对账。 */
    spcRecords(window, stage, productCategory, characteristic, day, overOnly) {
      const qs = [
        `window=${encodeURIComponent(window || "window")}`,
        stage ? `stage=${encodeURIComponent(stage)}` : "",
        productCategory ? `product_category=${encodeURIComponent(productCategory)}` : "",
        characteristic ? `characteristic=${encodeURIComponent(characteristic)}` : "",
        day ? `day=${encodeURIComponent(day)}` : "",
        overOnly ? "over_only=true" : "",
      ].filter(Boolean).join("&");
      return this._getScoped(`/api/v2/dashboard/spc/records?${qs}`, 8000);
    },

    /** 看板下钻：无 entity = L2（按维度分解）；带 entity = L3（单实体）；
     *  再带 step = L4（该工序逐日+工位）；再带 station = L5（该工位逐日+过站记录）；
     *  defectType = 不良类型下钻（L1 累计柏拉图点击，该类型按产线 · 型号分解）。
     *  window 与所看板级口径同源（"window"|"day"|"month"）；不良类型下钻另支持 "all"
     *  （不限窗口）；stage/category/prodCat 为柏拉图「工段/类别/产品分类」筛选同口径。 */
    dashboardDrill(screen, kpi, entity, step, station, defectType, window,
                   stage, category, prodCat) {
      const qs = `screen=${encodeURIComponent(screen)}&kpi=${encodeURIComponent(kpi)}`;
      let extra = entity
        ? Object.keys(entity).filter((k) => entity[k])
            .map((k) => `&${k}=${encodeURIComponent(entity[k])}`).join("")
        : "";
      if (step) extra += `&step=${encodeURIComponent(step)}`;
      if (station) extra += `&station=${encodeURIComponent(station)}`;
      if (defectType) extra += `&defect_type=${encodeURIComponent(defectType)}`;
      if (stage) extra += `&stage=${encodeURIComponent(stage)}`;
      if (category) extra += `&category=${encodeURIComponent(category)}`;
      if (prodCat) extra += `&prod_category=${encodeURIComponent(prodCat)}`;
      extra += `&window=${encodeURIComponent(window || "window")}`;
      return this._getScoped(`/api/v2/dashboard/drill?${qs}${extra}`, 8000);
    },

    /** 预警中心（真实优先）：五类预警，前四类由后端当前只读数据机械推导
     *  （SPC越限 / 缺料 / 订单临期 / 设备告警），第五类「现场推送」来自治理库的
     *  现场看板推送表（客诉 8D 分派后的现场处置队列）；等级与状态按可复算规则给出；
     *  订单客户名与现场推送客户名按岗位脱敏。全员可见（异常可能来自任何岗位），只需令牌。 */
    alerts() {
      return this._getScoped("/api/v2/alerts", 8000);
    },

    /* ---------- 现场看板（客诉 → 责任工段 推送队列：读 + 现场回写） ---------- */

    /** 现场看板推送队列（stage 缺省=全部工段；status 缺省=全部状态）。
     *  与预警中心「现场推送」同一张表，同源裁决 + 客户名按岗位脱敏。 */
    siteBoard(stage, status) {
      const qs = [];
      if (stage) qs.push(`stage=${encodeURIComponent(stage)}`);
      if (status) qs.push(`status=${encodeURIComponent(status)}`);
      return this._getScoped(`/api/v2/site-board${qs.length ? "?" + qs.join("&") : ""}`, 8000);
    },

    /** 现场接收推送（pushed → acked）：接收人由岗位令牌裁决，备注可选 */
    siteBoardAck(pushId, note) {
      return this._post(`/api/v2/site-board/${encodeURIComponent(pushId)}/ack`,
        { note: note || "" });
    },

    /** 现场处置闭环（acked → closed）：处置结论**必填**（后端空则 400） */
    siteBoardFeedback(pushId, feedback) {
      return this._post(`/api/v2/site-board/${encodeURIComponent(pushId)}/feedback`,
        { feedback: feedback || "" });
    },

    /* ---------- 齐套催交（R6：缺口清单 → 派发 → 供应商回复 → 到货确认 → 关闭） ---------- */

    /** 催交跟踪清单（治理轨状态机；响应带 db_scope / demo_writeback 徽标事实）。 */
    followups(status, workOrderSet) {
      const qs = [];
      if (status) qs.push(`status=${encodeURIComponent(status)}`);
      if (workOrderSet) qs.push(`work_order_set=${encodeURIComponent(workOrderSet)}`);
      return this._getScoped(`/api/v2/followups${qs.length ? "?" + qs.join("&") : ""}`, 8000);
    },

    /** 缺口清单预览（料号 / 缺口数 / 供应商 / 催交人）——派发前先看数。 */
    followupGaps(limit) {
      return this._getScoped(`/api/v2/followups/gaps?limit=${encodeURIComponent(limit || 20)}`, 8000);
    },

    /** 一键派发缺口清单到采购员待办（幂等：料号×供应商×工单集）。 */
    followupDispatch(workOrderSet, limit) {
      return this._post("/api/v2/followups/dispatch",
        { work_order_set: workOrderSet || "", limit: limit || 20 }, 20000);
    },

    /** 催交状态流转：reply / arrival / close / escalate（到货确认在演示态带「演示回写」徽标）。 */
    followupTransition(followupId, action, note, arrivalQty) {
      return this._post(`/api/v2/followups/${encodeURIComponent(followupId)}/transition`,
        { action, note: note || "", arrival_qty: arrivalQty == null ? null : Number(arrivalQty) });
    },

    /** R11 退供评审申请：超龄料退供/转用/报废，落 disposition 槽位待人工确认。 */
    agingReturnRequest(partNos, days, note) {
      return this._post("/api/v2/aging/return-request",
        { part_nos: partNos || [], days: days || 180, note: note || "" }, 20000);
    },

    /** 选定处置动作（按该动作 propose/cosign 生成会签要求）。
     *  操作者角色与身份由岗位令牌裁决（F2），请求体只带留痕信息。 */
    workflowSelectAction(taskId, actionKey, approver, comment) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/select-action`,
        { approver, comment: comment || "", action_key: actionKey });
    },

    /** 会签（多角色背书，缺一不可）。角色与 actor 由岗位令牌裁决 */
    workflowCosign(taskId, approver, comment, decision) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/cosign`,
        { approver, comment: comment || "", decision: decision || "" });
    },

    /** 人工确认 审批通过（waiting_hitl → running / done）。角色由令牌裁决 */
    workflowApprove(taskId, approver, comment, final, decision) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/approve`,
        { approver, comment, final: !!final, decision: decision || "" });
    },

    /** 人工确认 审批驳回（→ closed）。角色由令牌裁决 */
    workflowReject(taskId, approver, comment, decision) {
      return this._post(`/api/v2/workflow/${encodeURIComponent(taskId)}/reject`,
        { approver, comment, decision: decision || "" });
    },

    /* ---------- 报告中心（报告一等对象：列表 → 详情 → 审定 → 派发 → 导出） ---------- */

    /** 报告列表（筛选 + 关键字 + 分页）；返回 {items,total,limit,offset,scope} */
    reportsList(params) {
      const p = params || {};
      const qs = Object.keys(p)
        .filter((k) => p[k] !== "" && p[k] !== null && p[k] !== undefined)
        .map((k) => `${k}=${encodeURIComponent(p[k])}`).join("&");
      return this._get(`/api/v2/reports${qs ? "?" + qs : ""}`);
    },

    /** 顶部 KPI 概览条（各状态计数 + 含数据缺口数） */
    reportStats() { return this._get("/api/v2/reports/stats"); },

    /** 报告详情（内部简报 / 对客脱敏 / 溯源 / 关联 / 版本） */
    reportDetail(reportId) {
      return this._get(`/api/v2/reports/${encodeURIComponent(reportId)}`);
    },

    /** 按来源模块上下文生成报告（各协同屏「生成报告」按钮的统一入口） */
    reportGenerate(sourceModule, context) {
      return this._post("/api/v2/reports/generate",
        { source_module: sourceModule || "manual", context: context || {} });
    },

    /** 报告审定：action ∈ submit（发起）/ approve（通过）/ reject（驳回，理由必填） */
    reportReview(reportId, action, comment) {
      return this._post(`/api/v2/reports/${encodeURIComponent(reportId)}/review`,
        { action, comment: comment || "" });
    },

    /** 报告派发/对外发布（意见必填；后端另校验有溯源与已审定） */
    reportDispatch(reportId, comment) {
      return this._post(`/api/v2/reports/${encodeURIComponent(reportId)}/dispatch`,
        { comment: comment || "" });
    },

    /** 导出报告正文（md / html）。走带令牌的 fetch → Blob 下载，
     *  因为普通 <a href> 无法携带 Authorization 头。 */
    async reportExport(reportId, fmt) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), DEFAULT_TIMEOUT_MS);
      let resp;
      try {
        const opts = { signal: ctrl.signal };
        if (state.token) opts.headers = { Authorization: `Bearer ${state.token}` };
        resp = await fetch(
          `${this.base()}/api/v2/reports/${encodeURIComponent(reportId)}`
          + `/export?fmt=${encodeURIComponent(fmt || "md")}`, opts);
      } finally {
        clearTimeout(timer);
      }
      if (!resp.ok) {
        if (resp.status === 401) handleAuthExpired({});
        throw apiError(resp.status, {});
      }
      return resp.text();
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
    // 只渲染当前岗位可见项；分组标题按"可见项里出现过的分组"生成，
    // 避免出现"标题有、下面没内容"的空组。
    visibleNav().forEach((item) => {
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
    // 当前屏若对新岗位不可见（切换身份后），先回落到首个可见项，
    // 否则标题栏会显示一个菜单里根本不存在的页面。
    const allowed = visibleNav();
    let item = allowed.find((n) => n.id === state.screen);
    if (!item) {
      state.screen = allowed[0].id;
      item = allowed[0];
      // 同步地址栏，避免"URL 说在 SPC、实际显示工作台"的错位
      if (location.hash !== `#/${state.screen}`) location.hash = `#/${state.screen}`;
    }
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

    // 后端状态（四态）：检测中 / 完整 / 降级 / 离线。
    // 之前只有"在线=绿、离线=黄"两态，"在线"仅代表 uvicorn 进程活着——
    // 依赖全挂也显示绿灯，与实际体验背离。现按 health.checks 细分：
    //   绿 = 全依赖就绪；黄 = 可达但部分降级（点开 title 看清单）；红 = 不可达。
    const dot = document.getElementById("backend-dot");
    const text = document.getElementById("backend-text");
    const envMode = document.getElementById("env-mode");
    const chip = document.getElementById("backend-status");
    const health = state.backend.health;
    const degraded = (health && Array.isArray(health.degraded_components))
      ? health.degraded_components : [];
    const LABELS = {
      business_db: "业务库", workflow_db: "治理库", redis: "缓存",
      vector: "向量库", llm: "大模型",
    };

    if (state.backend.checking) {
      if (dot) dot.className = "dot dot-checking";
      if (text) text.textContent = "连接检测中";
      if (chip) chip.title = "正在探测后端 /api/v2/health";
    } else if (!state.backend.online) {
      if (dot) dot.className = "dot dot-err";
      if (text) text.textContent = "后端离线 · 演示模式";
      if (envMode) { envMode.textContent = "演示模式"; envMode.className = "env-value err"; }
      if (chip) chip.title = "无法连接后端（进程未启动或网络不通），当前展示演示数据";
    } else if (degraded.length) {
      if (dot) dot.className = "dot dot-warn";
      if (text) text.textContent = "后端在线 · 部分降级";
      if (envMode) { envMode.textContent = "降级模式"; envMode.className = "env-value warn"; }
      if (chip) {
        const detail = degraded
          .map((k) => `${LABELS[k] || k}：${(health.checks[k] || {}).detail || "不可用"}`)
          .join("\n");
        chip.title = `以下依赖不可用（链路仍可跑，相关能力降级）：\n${detail}`;
      }
    } else if (health && health.mode === "offline") {
      // 未配置 LLM：README 明确支持的离线降级形态（规则路由 + 模板结论）
      if (dot) dot.className = "dot dot-ok";
      if (text) text.textContent = "后端在线 · 离线降级";
      if (envMode) { envMode.textContent = "离线模式"; envMode.className = "env-value warn"; }
      if (chip) chip.title = "未配置 LLM 提供方：路由走规则、结论走模板，数字仍取自 SQL";
    } else {
      if (dot) dot.className = "dot dot-ok";
      if (text) text.textContent = "后端在线";
      if (envMode) { envMode.textContent = "完整模式"; envMode.className = "env-value online"; }
      if (chip) chip.title = "全部依赖就绪";
    }
  }

  function navigate(screenId) {
    screenId = canonScreen(screenId);
    // 只允许跳到当前岗位可见的屏：URL 里手输 #/spc 而该岗位无权限时，
    // 静默回落到首个可见项（不做错误提示——这属正常收敛，不是异常）。
    const allowed = visibleNav();
    const target = allowed.find((n) => n.id === screenId) || allowed[0];
    if (!target) return;
    state.screen = target.id;
    if (location.hash !== `#/${target.id}`) location.hash = `#/${target.id}`;
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
    // T05：场景屏由 SceneRegistry 分派，复用下面的 try/catch + friendlyError 路径
    if (!fn && window.SceneRegistry && window.SceneRegistry.has(state.screen)) {
      fn = (r, c) => window.SceneRegistry.render(state.screen, r, c);
    }
    if (typeof fn !== "function") {
      root.innerHTML = `<div class="empty">屏幕未注册: ${esc(state.screen)}</div>`;
      return;
    }
    try {
      fn(root, {
        state, api, UI: { icon, esc, badge, toast, spinner, fmtClock, fmtDateTime, fmtRemain, demoFlag, realFlag, friendlyError },
        DEMO: window.DEMO, perms: state.perms, gates: state.gates,
        navigate, refreshScreen, setRole, currentRole, visibleNav,
        currentColleague, setColleague,
      });
    } catch (err) {
      console.error("[screen] 渲染失败:", err);
      // 加载失败优化：render 抛错时绝不把 "xxx is not defined" / "Cannot read…"
      // 裸扔给用户——看不懂、也没法行动。说人话 + 刷新入口；技术原文进折叠区。
      const f = friendlyError(err);
      root.innerHTML = '<div class="empty">'
        + '<div class="case-load-fail-title">' + icon("warn", 28) + " 页面加载异常</div>"
        + '<div class="case-load-fail-text">' + esc(f.text) + "</div>"
        + '<button class="case-btn" onclick="location.reload()">刷新页面</button>'
        + (f.tech
          ? '<details class="case-tech"><summary>技术详情（便于反馈）</summary><code>'
            + esc(f.tech) + "</code></details>"
          : "")
        + "</div>";
    }
  }

  /** 快捷切换岗位（看板内一键换身份）：岗位变 → 令牌必须重签，否则写接口仍代表旧岗位。 */
  /**
   * 切换岗位（演示门户的「换个人看」）。
   *
   * 两处顺序不能省：
   * 1. **先纠正屏**——换岗位后当前屏可能已不在可见范围内（总经理在 SPC 上
   *    切成 HR），不纠正就会停在一个无权屏上，看到 403 面板像是坏了；
   * 2. **先补签令牌再渲染**——`state.token` 刚被清空，立刻 refreshScreen 会
   *    让首屏请求撞上 401，而看板的降级策略会把它当普通失败回落演示数据，
   *    于是"刚换岗位"这一下看到的是编造的数据。令牌就绪后再拉。
   */
  async function setRole(roleId) {
    // 岗位 id 必须是前端 ROLES 的 id（gm / mfg / wh …），不是权限目录的 key
    // （manufacturing / warehouse …）。传错时静默换岗会得到"没有令牌 + 菜单错乱"，
    // 排查成本极高——直接拒绝并说清原因。
    if (!window.DEMO || !(window.DEMO.ROLES || []).some((r) => r.id === roleId)) {
      console.warn("[role] 未知岗位 id:", roleId, "（应为 DEMO.ROLES 的 id）");
      return;
    }
    state.role = roleId;
    state.token = "";
    // 换岗即换同事：同事能力面按岗位配班，手动选择不跨岗位继承
    state.colleague = "";
    try { localStorage.removeItem(COLLEAGUE_KEY); } catch (err) { /* 忽略 */ }
    saveUser(roleId, "");
    const allowed = visibleNav();
    if (allowed.length && !allowed.some((n) => n.id === state.screen)) {
      state.screen = allowed[0].id;
      if (location.hash !== `#/${state.screen}`) location.hash = `#/${state.screen}`;
    }
    updateChrome();
    await ensureSession();   // 静默按新岗位补签令牌
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
      // 报告中心：关键词直接落到报告列表的检索框（prefill 由 reports-screen.js 消费）
      results.unshift({
        screen: "reports", ask: k, type: "报告",
        title: `在报告中心检索：「${k.length > 22 ? k.slice(0, 22) + "…" : k}」`,
        sub: "按标题 / 场景 / 来源模块过滤已归档报告",
      });
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
        html = `<div style="padding:16px;font-size: calc(12.5px * var(--font-scale));color:var(--text-3);text-align:center;">未命中演示数据</div>`;
      } else {
        results.forEach((r, i) => {
          html += `<div class="search-hit" data-idx="${i}" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;gap:9px;align-items:center;">`
            + `<span class="badge badge-blue">${esc(r.type)}</span>`
            + `<div style="min-width:0;"><div style="font-size: calc(12.8px * var(--font-scale));font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(r.title)}</div>`
            + `<div style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(r.sub)}</div></div></div>`;
        });
        html += `<div style="padding:7px 14px;font-size: calc(11px * var(--font-scale));color:var(--text-3);background:#F8FAFD;">检索范围为演示数据（订单 / 预警 / 工单）</div>`;
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
              // 报告中心用独立预填键：命中"报告"条目时不得污染协同问数的提问框
              const key = hit2.screen === "reports" ? "report_prefill" : "chat_prefill";
              try {
                sessionStorage.setItem(key, hit2.ask);
                if (key === "chat_prefill") {
                  // 结构化来源标注：AI 搜问进协同问数（无看板口径，仅标模块）
                  sessionStorage.setItem("chat_ask_ctx", JSON.stringify({ module: "search" }));
                } else {
                  sessionStorage.removeItem("chat_ask_ctx");
                }
              } catch (e) { /* 忽略 */ }
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
            const key = r[0].screen === "reports" ? "report_prefill" : "chat_prefill";
            try {
              sessionStorage.setItem(key, r[0].ask);
              if (key === "chat_prefill") {
                sessionStorage.setItem("chat_ask_ctx", JSON.stringify({ module: "search" }));
              } else {
                sessionStorage.removeItem("chat_ask_ctx");
              }
            } catch (e) { /* 忽略 */ }
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
      const permsNote = state.permsError
        ? `<div class="user-menu-perms-err">
           <b>权限清单加载失败</b>（入口的置灰提示暂不可用；能否执行由服务端把关，不影响数据安全）<br>
           ${esc(state.permsError)} · 刷新页面可重试</div>`
        : "";
      menu.innerHTML = `
        <div class="user-menu-head">
          <div class="user-menu-name">${esc(role.user)}</div>
          <div class="user-menu-role">${esc(role.name)} · ${esc(role.dept)}</div>
          <div class="user-menu-scope">${esc(role.scope || "")}</div>
        </div>
        ${permsNote}
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
        state.token = "";   // 退出即作废岗位令牌
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
      const raw = location.hash.replace(/^#\/?/, "");
      const hash = canonScreen(raw);
      // 旧屏名（如 #/office）即使已落在同一屏，也要把 URL 归一化到新屏名，
      // 避免书签 / 演示脚本停留在已废弃的旧屏名上。
      if (hash !== raw && visibleNav().some((n) => n.id === hash)) { navigate(hash); return; }
      // 可见性判断同样走 visibleNav()：手输无权限屏不应渲染出来
      if (hash && hash !== state.screen && visibleNav().some((n) => n.id === hash)) navigate(hash);
    });
  }

  async function startApp() {
    await api.probe();
    await ensureSession();       // F2：持有可用岗位令牌，写接口才有鉴权凭据
    await loadPermCatalog();
    await loadGateCatalog();
    await loadColleagueCatalog();
    updateChrome();

    const hash = canonScreen(location.hash.replace(/^#\/?/, ""));
    // 深链接也要过岗位可见性：直接分享 #/spc 给 HR 岗位时应落到工作台
    navigate(visibleNav().some((n) => n.id === hash) ? hash : "dashboard");

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
      state.permsError = "";   // 加载成功即清除上次失败提示
    } catch (err) {
      console.warn("[governance] 权限目录加载失败，前端门控退化为后端裁决:", err);
      state.perms = null;
      // 不能静默：用户要知道"入口门控暂时不准"，但更要明白数据权限
      // 始终由服务端把关（前端退化不影响安全，只影响提示）。
      state.permsError = friendlyError(err).text;
    }
    return state.perms;
  }

  /**
   * 加载 HITL 闸门理由目录（gate_reasons.yaml → /api/v2/governance/gates）。
   * 失败不阻塞：审批卡降级为「槽位中文名」不带 why——闸门理由是增强层，
   * 会签门控与安全校验始终在后端，不依赖本目录。
   */
  async function loadGateCatalog() {
    if (!state.backend.online) { state.gates = []; return null; }
    try {
      const resp = await api.governanceGates();
      const list = (resp && resp.gates) || [];   // _get 已解信封：resp 即载荷
      state.gates = Array.isArray(list) ? list : [];
    } catch (err) {
      console.warn("[governance] 闸门理由目录加载失败，审批卡降级为槽位名:", err);
      state.gates = [];
    }
    return state.gates;
  }

  /**
   * 加载岗位 AI 同事目录（colleague.yaml → /api/v2/colleagues）。
   * 失败不阻塞：同事切换器退化为「仅当前岗位同事不可用」的诚实态，
   * 对话/看板主链路不受影响（同事是形态层，不是依赖）。
   */
  async function loadColleagueCatalog() {
    if (!state.backend.online) { state.colleagues = []; return null; }
    try {
      const resp = await api.colleagues();
      // _get 已解信封：resp 即 data 载荷本身（{colleagues, count, source}），
      // 不能再取一层 .data（screens.js 里有同款踩坑记录）
      const list = (resp && resp.colleagues) || [];
      state.colleagues = Array.isArray(list) ? list : [];
      // 历史手动选择若已不在目录中（配置收编）→ 静默回落跟岗
      if (state.colleague && !colleagueOf(state.colleague)) state.colleague = "";
    } catch (err) {
      console.warn("[colleague] 同事目录加载失败，同事形态退化:", err);
      state.colleagues = [];
    }
    return state.colleagues;
  }

  async function boot() {
    initChrome();
    /* 会话恢复口径（2026-10-20）：公网链接访客默认落在登录界面。
       - 同一标签页刷新（sessionStorage 会话标记还在）→ 直接进应用，所在屏由
         location.hash 恢复（见 startApp）；
       - 新开 / 重开链接、新访客 → 一律先到登录界面；已选过岗位的回显
         「已选择：xxx」并预选中，点「进入系统」一键直达，不丢便利性。
       退出登录走 clearUser()，两个存储一起清。 */
    const saved = loadUser();
    let sameTab = false;
    try { sameTab = sessionStorage.getItem(SESSION_FLAG_KEY) === "1"; } catch (err) { sameTab = false; }
    if (saved && sameTab) {
      state.role = saved.roleId;
      state.token = saved.token;   // 令牌由 startApp → ensureSession 校验/补签
      const root = document.getElementById("login-root");
      if (root) { root.hidden = true; root.innerHTML = ""; }
      updateChrome();
      startApp();
      return;
    }
    state.role = saved ? saved.roleId : "";   // 仅用于登录页预选中上次岗位
    state.token = "";
    // 恢复手动选择的同事（不存在于目录时由 loadColleagueCatalog 回落跟岗）
    try {
      const c = localStorage.getItem(COLLEAGUE_KEY);
      if (c) state.colleague = String(c);
    } catch (err) { /* 隐私模式忽略 */ }
    renderLogin();
  }

  /* ============================================================
   * 八、导出
   * ============================================================ */

  window.App = {
    state, api, navigate, refreshScreen, setRole,
    currentRole,
    currentColleague, setColleague, colleagueOf,
    showLogin: renderLogin,
    enterApp,
    UI: { icon, esc, badge, toast, spinner, fmtClock, fmtDateTime, fmtRemain, demoFlag, realFlag, friendlyError },
    NAV_ITEMS,
    visibleNav,      // 当前岗位可见导航（帮助中心 / 测试复用同一口径）
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
