/**
 * 数据速查 + OA 协同 · demo 展示屏（由原「办公与数据速查」拆分而来）
 *
 * 拆分动机：办公办理（会议室/订餐/我的预订）与制造数据只读速查是两类任务、
 * 两套权限口径、两条变更节奏，混在一屏会导致心智混淆与回归互相牵制。
 * 如今拆为两屏、共用同一份渲染实现，按当前屏名（ctx.state.screen）决定内容：
 *   - screen=data → 「数据速查」：数据问答 + 制造数据速查卡（只读）
 *   - screen=oa   → 「OA 协同」  ：办公问答 + 会议室 / 餐厅 / 我的预订订单
 *
 * 数据面（后端为 demo 后端：内存种子数据，进程重启复位；真实接入适配层已就绪，
 * OFFICE_BACKEND=feishu 即把会议室切到飞书日历，本页零改动）：
 *   POST /api/v2/office/chat              企业助手对话（含工具调用留痕 steps / HITL）
 *   GET  /api/v2/office/meta              后端实现 / LLM 可用性 / 高影响阈值
 *   GET  /api/v2/office/snapshot          制造数据速查（材料库存/缺料/成品/来料；按岗位权限过滤，不需 LLM）
 *   GET  /api/v2/office/rooms             会议室目录（容量筛选）
 *   GET  /api/v2/office/available         某时段可用会议室（确定性查询，不需要 LLM）
 *   GET  /api/v2/office/restaurants       餐厅目录
 *   GET  /api/v2/office/menu/:id          餐厅菜单
 *   GET  /api/v2/office/my/:actor         我的预订与订单
 *   POST /api/v2/office/cancel            取消本人预订 / 订单
 *
 * 注册方式：`window.Screens.render_data` / `window.Screens.render_oa`（app.js 按 `render_<screenId>` 查找）。
 * 样式：app.css 内 `.of-` 命名空间（沿用平台浅色主题变量）。
 *
 * 边界（不夸大）：办理动作一律由后端 Agent/后端服务执行，本页只做展示与触发；
 * 制造数据为**只读**（口径=注册查询，可用量=在库-预留），越权块如实显示"无权限"；
 * LLM 不可用时禁用对话并如实说明，速查卡与"我的预订"仍可正常使用。
 */
(function () {
  "use strict";

  const SEND_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>';

  // 快捷示例：按屏分流，直接喂给企业助手 Agent，展示工具调用链路
  const DATA_QUICK = [
    "M8 螺栓还有多少库存",
    "A 型号成品现在能发多少",
    "最近有没有不合格的来料批次",
    "哪些料库龄超过 90 天了",
    "哪些料低于安全库存了",
    "帮我追溯 BAT-LI-3000 这批来料",
  ];

  // OA 侧快捷示例刻意写成「完整案例」：**具体日期 + 办理事由 + 事项类型**三件齐全，
  // 点一下即走完整链路（查可用/查菜单 → 预订或下单 → 报销/请假/补卡转人工确认），
  // 不触发询问弹窗；与右侧「OA 自助办理」按钮的「只给意图 → 弹窗反问补齐」形成对照。
  // 日期按当天动态推算且跳过周末（演示隔天或周末跑，也不会出现"周日补上班卡"这类怪样例）。
  function oaQuick() {
    const bookDay = workday(1);     // 下一个工作日：可预订的会议室 / 订餐
    const leaveDay = workday(3);    // 再往后一个工作日：请假
    const fixDay = workday(-1);     // 上一个工作日：已发生，用于补卡
    const tripDay = workday(-5);    // 上一周的工作日：已发生，用于差旅报销
    return [
      { kind: "会议室预订",
        q: `帮我订 ${bookDay} 14:00-15:00 一间 8 人会议室，开 SMT 良率周会` },
      { kind: "订餐",
        q: `订 ${bookDay} 中午 12:00 园区食堂红烧狮子头套餐 2 份，加班用餐` },
      { kind: "查我的",
        q: `查我 ${bookDay} 的会议室预订和订餐订单，是否订成功` },
      { kind: "费用报销",
        q: `报销 ${tripDay} 供应商驻厂差旅费 1286 元（高铁 980 + 住宿 306）` },
      { kind: "请假申请",
        q: `请 ${leaveDay} 一天年假，回老家参加婚礼` },
      { kind: "补考勤",
        q: `补 ${fixDay} 早上 08:30 的上班卡，产线调试忘打卡` },
    ];
  }

  // OA 自助办理入口 → 种子问句（缺失项由助手 ask_user 弹窗反问补齐）
  const ENTRY_SEED = {
    expense: "我要报销一笔费用",
    leave: "我要请假",
    attendance: "我要补考勤打卡",
  };

  // OA 材料上传：报销材料（发票必传，出差报告/行程单/付款凭证等选传）与请假材料（病假条）
  const EXP_UPLOAD_KINDS = ["发票", "出差报告", "行程单", "付款凭证", "其他"];
  const LEAVE_UPLOAD_KINDS = ["病假条", "其他"];
  const EXP_CATEGORIES = ["差旅", "招待", "办公用品", "交通", "其他"];
  // 报销必传材料（发票）；与后端 EXPENSE_REQUIRED_KINDS 同口径，仅用于上传时的徽标提示
  function kindRequired(kind) { return kind === "发票" || kind === "病假条"; }
  function kindBadgeCls(kind) { return kindRequired(kind) ? "badge-amber" : "badge-gray"; }
  function kindBadgeText(kind) { return kindRequired(kind) ? "必传" : "选传"; }

  function fmtSize(n) {
    const v = Number(n) || 0;
    if (v < 1024) return `${v} B`;
    if (v < 1048576) return `${(v / 1024).toFixed(1)} KB`;
    return `${(v / 1048576).toFixed(1)} MB`;
  }

  /** 材料胶囊（只读展示；点击预览/下载）——单据里的附件、上传列表共用 */
  function attChipsHtml(atts, esc) {
    const list = atts || [];
    if (!list.length) return "";
    return '<div class="of-atts">' + list.map((a) => {
      const id = String(a.attachment_id || "");
      const name = String(a.filename || id);
      return `<button type="button" class="of-att" data-att="${esc(id)}"`
        + ` title="${esc(`${a.kind || "其他"} · ${name} · ${fmtSize(a.size)}`)}">`
        + `<span class="of-att-kind">${esc(a.kind || "其他")}</span>`
        + `<span class="of-att-name">${esc(name)}</span></button>`;
    }).join("") + '</div>';
  }

  // OA 申请单据状态（与后端 status 口径一致）
  const FORM_STATUS = {
    pending_approval: { t: "待审批", c: "badge-amber" },
    approved: { t: "已通过", c: "badge-green" },
    rejected: { t: "已驳回", c: "badge-red" },
    cancelled: { t: "已撤销", c: "badge-gray" },
  };
  function formStatusText(s) { return (FORM_STATUS[s] || { t: s || "-" }).t; }
  function formStatusCls(s) { return (FORM_STATUS[s] || { c: "badge-gray" }).c; }

  // ---------- OA 办公桌面（手机桌面式三级：文件夹 → 应用 → 展开功能） ----------
  // 一级桌面＝文件夹瓷片（瓷片内嵌该文件夹的应用图标）；二级＝文件夹窗口（应用瓷片宫格）；
  // 三级＝应用窗口（把该应用的功能面板整块搬进窗口展示，右上角关闭）。
  // 后续集成更多 OA 功能：在 OA_APPS 里加一行即可（面板放 .of-panels 里，配 data-app 同名即可）。
  const OA_FOLDERS = [
    { id: "office", name: "办公办理", icon: "approve" },
    { id: "space", name: "空间与订餐", icon: "grid" },
    { id: "it", name: "IT 服务", icon: "cpu" },
    { id: "vehicle", name: "停车与车辆", icon: "truck" },
    { id: "mine", name: "我的事务", icon: "orders" },
  ];
  const OA_APPS = [
    { id: "expense", folder: "office", name: "费用报销", icon: "report",
      desc: "登记发票等材料 · 整理报销科目 · 交给助手报销" },
    { id: "leave", folder: "office", name: "请假申请", icon: "clock",
      desc: "年假 / 事假 / 病假 / 调休 · 部门主管审批" },
    { id: "attendance", folder: "office", name: "补考勤打卡", icon: "check",
      desc: "上下班补卡 · 漏打卡补录 · HR 专员审批" },
    { id: "rooms", folder: "space", name: "会议室预订", icon: "grid",
      desc: "按楼栋 / 容量筛选 · 查可用并让助手预订" },
    { id: "dining", folder: "space", name: "餐厅订餐", icon: "box",
      desc: "园区食堂 / 商务餐厅 / 夜宵档 · 让助手订餐" },
    // IT 服务（归口 IT 运维专员）
    { id: "it_repair", folder: "it", name: "IT 故障报修", icon: "warn",
      desc: "报修终端 / 网络 / 系统故障 · IT 运维派单处理" },
    { id: "it_access", folder: "it", name: "账号与权限申请", icon: "shield",
      desc: "系统账号与权限开通 / 调整 · IT 运维核验后开通" },
    { id: "it_device", folder: "it", name: "设备领用申请", icon: "cpu",
      desc: "笔记本 / 显示器 / 打印机等 IT 资产领用" },
    // 停车与车辆（归口行政后勤专员）
    { id: "parking", folder: "vehicle", name: "车位申请", icon: "grid",
      desc: "固定 / 月租 / 临时车位申请 · 行政后勤分配" },
    { id: "visitor_vehicle", folder: "vehicle", name: "访客车辆报备", icon: "truck",
      desc: "来访车辆提前报备 · 门岗按报备放行" },
    { id: "business_car", folder: "vehicle", name: "公务用车申请", icon: "arrow",
      desc: "公务出行派车 · 含用车时段与目的地" },
    { id: "mine", folder: "mine", name: "我的预订与订单", icon: "orders",
      desc: "会议预订与订餐订单 · 可取消" },
    { id: "myforms", folder: "mine", name: "我的申请单据", icon: "layers",
      desc: "假期余额 · 报销 / 请假 / 补卡 / 服务工单进度" },
  ];

  // 服务工单表单（一个渲染器覆盖 6 类应用：字段声明即表单，提交仍是拼自然语言交给助手）
  // 字段 key 与 agent 工具入参一致：title/location/urgency 走显式入参，其余放 extra
  const SERVICE_SCHEMA = {
    it_repair: {
      name: "IT 故障报修", att: ["故障照片"], urgency: true,
      badge: "IT 运维派单 · 提交后人工确认",
      fields: [
        { key: "title", label: "故障现象", type: "text", required: true,
          ph: "如：MES 终端开机黑屏 / 车间网络断" },
        { key: "location", label: "设备与位置", type: "text", required: true,
          ph: "如：SMT 车间 2 号线" },
        { key: "urgency", label: "紧急程度", type: "select",
          options: ["一般", "紧急", "非常紧急"] },
      ],
      hint: "紧急故障（产线停线相关）请选「非常紧急」；IT 运维会按紧急度派单并回访。",
    },
    it_access: {
      name: "账号与权限申请", att: ["授权审批单"], urgency: false,
      badge: "IT 运维核验 · 提交后人工确认",
      fields: [
        { key: "title", label: "申请事由", type: "text", required: true,
          ph: "如：承担工单录入职责，需开通权限" },
        { key: "system", label: "系统", type: "text", required: true,
          ph: "如：MES / OA / ERP" },
        { key: "permission", label: "权限项", type: "select", required: true,
          options: ["查询", "录入", "审批", "导出", "管理员"] },
        { key: "account", label: "工号 / 账号（默认本人）", type: "text", ph: "如：12345" },
      ],
      hint: "权限按最小必要开通；「管理员」类权限需部门主管会签，IT 运维不直接放行。",
    },
    it_device: {
      name: "设备领用申请", att: [], urgency: false,
      badge: "IT 资产核验 · 提交后人工确认",
      fields: [
        { key: "title", label: "领用用途", type: "text", required: true,
          ph: "如：新员工入职配置 / 产线扫码作业" },
        { key: "device_type", label: "设备类型", type: "select", required: true,
          options: ["笔记本电脑", "台式机", "显示器", "打印机", "扫码枪", "工牌/门禁卡"] },
        { key: "qty", label: "数量", type: "number", ph: "如：1" },
        { key: "need_date", label: "期望领用日", type: "date" },
      ],
      hint: "设备领用需归还登记；离职/转岗时按资产台账回收。",
    },
    parking: {
      name: "车位申请", att: ["行驶证"], urgency: false,
      badge: "行政后勤分配 · 提交后人工确认",
      fields: [
        { key: "title", label: "申请事由", type: "text", required: true,
          ph: "如：自驾通勤，申请固定车位" },
        { key: "parking_type", label: "车位类型", type: "select",
          options: ["固定车位", "月租车位", "临时车位"] },
        { key: "area", label: "区域", type: "select",
          options: ["A 栋地面", "B 栋地面", "C 栋地面", "D 栋地面", "地库 B1", "地库 B2"] },
        { key: "plate", label: "车牌号", type: "text", required: true, ph: "如：苏A12345" },
        { key: "start", label: "起租日期", type: "date" },
      ],
      hint: "车位紧张时按登记顺序排队；固定车位按月统计，调离岗位后自动释放。",
    },
    visitor_vehicle: {
      name: "访客车辆报备", att: [], urgency: false,
      badge: "门岗放行 · 提交后人工确认",
      fields: [
        { key: "title", label: "来访事由", type: "text", required: true,
          ph: "如：供应商来厂技术交流" },
        { key: "plate", label: "车牌号", type: "text", required: true, ph: "如：苏A88888" },
        { key: "visit_date", label: "来访日期", type: "date", required: true },
        { key: "visit_time", label: "来访时段", type: "text", ph: "如：09:00-11:00" },
        { key: "host", label: "被访人 / 部门", type: "text", ph: "如：采购部 韩雪" },
        { key: "visitor", label: "访客姓名", type: "text" },
      ],
      hint: "报备后门岗按车牌放行；超时报备或未报备车辆需现场登记并由被访人确认。",
    },
    business_car: {
      name: "公务用车申请", att: ["用车申请单"], urgency: false,
      badge: "行政后勤派车 · 提交后人工确认",
      fields: [
        { key: "title", label: "用车事由", type: "text", required: true,
          ph: "如：赴苏州分厂对接量产问题" },
        { key: "dest", label: "目的地", type: "text", required: true, ph: "如：苏州分厂" },
        { key: "use_from", label: "用车起始", type: "date", required: true },
        { key: "use_time", label: "出发时间", type: "text", ph: "如：08:00" },
        { key: "passengers", label: "乘车人数", type: "number", ph: "如：3" },
        { key: "need_driver", label: "是否需司机", type: "select", options: ["需要", "不需要"] },
      ],
      hint: "用车结束后请及时还车登记里程；跨市用车需行政后勤确认车辆与司机排班。",
    },
  };
  function oaFolder(id) { return OA_FOLDERS.filter((f) => f.id === id)[0] || null; }
  function oaApp(id) { return OA_APPS.filter((a) => a.id === id)[0] || null; }
  function oaAppsOf(folderId) { return OA_APPS.filter((a) => a.folder === folderId); }
  function isSvcApp(id) { return Object.prototype.hasOwnProperty.call(SERVICE_SCHEMA, id); }

  // IQC 结论徽章（与后端 inspect_status 口径一致）
  const SNAP_STATUS = {
    pass: { t: "合格", c: "badge-green" },
    fail: { t: "不合格", c: "badge-red" },
    pending: { t: "待检", c: "badge-gray" },
    deviation: { t: "让步接收", c: "badge-amber" },
  };

  // 速查四块标题（dataset key → 中文名；申请弹窗首行「受理」用）
  const SNAP_TITLE = {
    material_stock: "材料库存",
    shortage: "缺料清单",
    finished_stock: "成品库存与可发量",
    incoming_batch: "来料批次与 IQC 结论",
  };

  function pad(n) { return String(n).padStart(2, "0"); }
  function dateStr(offsetDays) {
    const d = new Date();
    d.setDate(d.getDate() + (offsetDays || 0));
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  /** 相对今天偏移 offset 天，落在周末则往同一方向推到最近工作日。
   *  快捷案例的日期走这里：「周日补上班卡」「周六订会议室」这类样例会让演示出戏。 */
  function workday(offsetDays) {
    const d = new Date();
    d.setDate(d.getDate() + (offsetDays || 0));
    const step = (offsetDays || 0) >= 0 ? 1 : -1;
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + step);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function nowHM() {
    const d = new Date();
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /** 当前登录岗位 → 办公操作人（预订/订单归属；与后端 actor 口径一致） */
  function actorOf(ctx) {
    const role = (ctx.currentRole && ctx.currentRole()) || {};
    return { name: role.user || "演示用户", uid: role.uid || "", dept: role.dept || "", roles: role.roles || [] };
  }

  // 元数据（后端实现 / LLM 可用性）跨重渲染复用
  let metaCache = null;

  function initialState(mode) {
    const greet = mode === "oa"
      ? "您好！我是 OA 协同助手，可办办公事务（预订会议室、订餐、查/取消本人预订与订单）"
        + "与 OA 自助办理（费用报销、请假申请、补考勤打卡）。办理时若关键信息不全，"
        + "我会弹出表单向您反问补齐；报销/请假/补卡提交后一律先发起人工确认，不会直接放行。"
      : "您好！我是数据速查助手，可查制造数据（材料库存、成品库存与可发量、来料批次与检验结论、"
        + "库龄呆滞、缺料缺口、批次追溯，均为只读）。数据走平台统一口径注册查询，受岗位数据权限约束；"
        + "越权数据我会如实告知无权限，不会绕过。";
    return {
      messages: [{ role: "bot", time: nowHM(), steps: [], hitl: null, gaps: [], content: greet }],
      sending: false,
      filter: "all",            // 会议室筛选：all | cap8 | cap20 | approval
      tower: "",                // 楼栋筛选："" | A栋 | B栋 | C栋 | D栋
      avail: null,              // 可用性查询结果
      snap: null,               // 制造数据速查（office/snapshot）
      snapQuery: "",            // 速查关键词（料号/型号/批号/名称；命中后点行即交给助手问答）
      form: { date: dateStr(1), start: "14:00", end: "15:00", cap: "8" },
      menuId: "",
      uploads: [],                              // 已登记材料（演示态只记文件名/大小；发票必传、出差报告等选传）
      upKind: EXP_UPLOAD_KINDS[0],              // 材料类别（默认发票）
      expCat: EXP_CATEGORIES[0],                // 报销类别（默认差旅）
      expAmount: "", expNote: "", expDate: "",  // 报销表单草稿
      org: null,                                // 整理报销科目结果
    };
  }

  // ---------- 渲染片段 ----------

  function stepsHtml(steps, esc) {
    if (!steps || !steps.length) return "";
    return steps.map((s) => {
      const args = s.args && Object.keys(s.args).length
        ? `(${esc(JSON.stringify(s.args))})` : "";
      return `<div class="of-step"><span class="${s.ok ? "ok" : "bad"}">${s.ok ? "✓" : "✗"}</span>`
        + `<span><b>${esc(s.tool)}</b>${args}</span></div>`;
    }).join("");
  }

  function messageHtml(m, ctx) {
    const UI = ctx.UI;
    if (m.role === "user") {
      const role = ctx.currentRole() || {};
      const initial = String(role.user || role.name || "我").slice(0, 1);
      return `<div class="msg user"><div class="msg-avatar user">${UI.esc(initial)}</div>`
        + `<div class="msg-bubble"><div class="msg-text">${UI.esc(m.content)}</div></div></div>`;
    }
    const steps = (m.steps && m.steps.length)
      ? `<div class="of-steps"><div class="of-step-head">工具调用留痕 · ${m.steps.length} 步`
        + `${m.elapsed ? ` · ${(m.elapsed / 1000).toFixed(1)}s` : ""}</div>`
        + stepsHtml(m.steps, UI.esc) + "</div>"
      : "";
    const used = (m.used && m.used.length)
      ? `<div class="of-used">依据：${UI.esc(m.used.join("；"))}</div>` : "";
    const hitl = m.hitl
      ? `<div class="of-hitl">已发起人工确认 · 工单 ${UI.esc(m.hitl.task_uid || "")}`
        + `（${UI.esc(m.hitl.status || "pending")}）· 行政专员处理后方可落地</div>`
      : "";
    const gaps = (m.gaps && m.gaps.length)
      ? `<div class="of-gaps">未能办理/获取：${UI.esc(m.gaps.join("；"))}</div>` : "";
    return `<div class="msg"><div class="msg-avatar agent">${UI.icon("cpu", 15)}</div>`
      + `<div class="msg-bubble"><div class="msg-text${m.pending ? " of-pending" : ""}">${UI.esc(m.content)}</div>`
      + steps + used + hitl + gaps + "</div></div>";
  }

  function roomLineHtml(r, esc) {
    const eq = (r.equipment || []).map(
      (e) => `<span class="badge badge-gray">${esc(e)}</span>`).join(" ");
    const appr = r.need_approval
      ? '<span class="badge badge-amber">需授权</span>' : "";
    return `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(r.name)} <span class="of-code">${esc(r.room_id)}</span></div>`
      + `<div class="of-item-sub">${esc(r.floor || "-")} · 可容纳 ${esc(r.capacity)} 人 ${eq} ${appr}</div>`
      + `</div><button class="btn btn-outline btn-sm" data-ask="${esc(r.name)}">让助手预订</button></div>`;
  }

  function roomListHtml(S, ctx) {
    const esc = ctx.UI.esc;
    const all = S.rooms || [];
    const cap = S.filter === "cap8" ? 8 : S.filter === "cap20" ? 20 : 0;
    let rooms = all.filter((r) => !cap || Number(r.capacity) >= cap);
    if (S.filter === "approval") rooms = rooms.filter((r) => r.need_approval);
    if (S.tower) rooms = rooms.filter((r) => String(r.floor || "").startsWith(S.tower));
    if (!rooms.length) return '<div class="of-note">没有符合条件的会议室。</div>';
    return rooms.map((r) => roomLineHtml(r, esc)).join("");
  }

  function menuHtml(S, ctx) {
    const esc = ctx.UI.esc;
    const m = S.menu;
    if (!m) return "";
    const items = (m.items || []).map((i) => `<div class="of-item">`
      + `<div class="of-item-main"><div class="of-item-title">${esc(i.name)} `
      + `<span class="of-code">${esc(i.item_id)}</span></div>`
      + `<div class="of-item-sub">单价 ¥${esc(i.price)}${i.tag ? " · " + esc(i.tag) : ""}</div></div>`
      + `<button class="btn btn-outline btn-sm" data-ask="${esc(m.name)}的${esc(i.name)}">让助手订餐</button>`
      + `</div>`).join("");
    return `<div class="of-menu-head">${esc(m.name)}（${esc(m.type)}）`
      + `<span class="of-code">${(m.items || []).length} 道</span>`
      + (m.need_approval ? '<span class="badge badge-amber">商务客餐 · 需人工确认</span>' : "")
      + "</div>" + items;
  }

  function mineHtml(S, esc) {
    const bookings = (S.mine && S.mine.bookings) || [];
    const orders = (S.mine && S.mine.orders) || [];
    if (!bookings.length && !orders.length) {
      return '<div class="of-note">暂无预订与订单。可以先用上方对话让助手办理一单。</div>';
    }
    const bk = bookings.map((b) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(b.room_name)} <span class="of-code">${esc(b.booking_id)}</span></div>`
      + `<div class="of-item-sub">${esc(b.date)} ${esc(b.start)}-${esc(b.end)} · ${esc(b.title)}`
      + ` · ${esc(b.attendees)} 人${b.need_approval ? " · 需授权" : ""}</div></div>`
      + `<button class="btn btn-danger btn-sm" data-cancel="booking" data-id="${esc(b.booking_id)}">取消</button></div>`).join("");
    const od = orders.map((o) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(o.restaurant_name)} <span class="of-code">${esc(o.order_id)}</span></div>`
      + `<div class="of-item-sub">${esc(o.meal_time)} · ${(o.items || []).map(
        (i) => `${esc(i.name)}×${esc(i.qty)}`).join("、")} · 合计 ¥${esc(o.total_amount)}`
      + `${o.need_approval ? " · 需审批" : ""}</div></div>`
      + `<button class="btn btn-danger btn-sm" data-cancel="order" data-id="${esc(o.order_id)}">取消</button></div>`).join("");
    return `<div class="of-mine-label">会议室预订（${bookings.length}）</div>${bk || '<div class="of-note">无</div>'}`
      + `<div class="of-mine-label">订餐订单（${orders.length}）</div>${od || '<div class="of-note">无</div>'}`;
  }

  /** OA 申请单据（报销 / 请假 / 补卡 + 假期余额；只读展示，提交动作在对话里完成） */
  function myFormsHtml(S, esc) {
    const mine = (S && S.mine) || {};
    const exps = mine.expenses || [];
    const leaves = mine.leaves || [];
    const fixes = mine.attendance_fixes || [];
    const svcs = mine.service_requests || [];
    const bal = mine.leave_balance || null;
    if (!exps.length && !leaves.length && !fixes.length && !svcs.length && !bal) {
      return '<div class="of-note">暂无 OA 申请单据。可用上方「OA 自助办理」「IT 服务」'
        + '「停车与车辆」应用或对话发起报销 / 请假 / 补卡 / 服务工单。</div>';
    }
    const balHtml = bal
      ? `<div class="of-bal">年假余额 <b>${esc(bal.annual_remaining)}</b> 天 · 调休余额 `
        + `<b>${esc(bal.comp_remaining)}</b> 天 · 本年已休 <b>${esc(bal.used_this_year)}</b> 天</div>`
      : "";
    const ex = exps.map((e) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(e.category)}报销 <span class="of-code">${esc(e.expense_id)}</span></div>`
      + `<div class="of-item-sub">¥${esc(e.amount)} · ${esc(e.expense_date)} · ${esc(e.note || "-")}`
      + `${e.high_value ? ' <span class="badge badge-amber">大额</span>' : ""}`
      + `${(e.missing_required || []).length ? ` <span class="badge badge-red">缺${esc((e.missing_required || []).join("、"))}</span>` : ""}`
      + ` <span class="badge ${formStatusCls(e.status)}">${esc(formStatusText(e.status))}</span></div>`
      + attChipsHtml(e.attachments, esc) + `</div></div>`).join("");
    const lv = leaves.map((l) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(l.leave_type)} <span class="of-code">${esc(l.leave_id)}</span></div>`
      + `<div class="of-item-sub">${esc(l.start_date)} ~ ${esc(l.end_date)} · ${esc(l.days)} 天`
      + `${l.reason ? " · " + esc(l.reason) : ""}`
      + `${l.long_leave ? ' <span class="badge badge-amber">长假</span>' : ""}`
      + `${l.sick_note_pending ? ' <span class="badge badge-red">待补病假条</span>' : ""}`
      + ` <span class="badge ${formStatusCls(l.status)}">${esc(formStatusText(l.status))}</span></div>`
      + attChipsHtml(l.attachments, esc) + `</div>`
      + `${l.sick_note_pending ? `<button class="btn btn-primary btn-sm" data-supplement="${esc(l.leave_id)}">补充病假条</button>` : ""}`
      + `</div>`).join("");
    const kb = fixes.map((a) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(a.fix_type)} <span class="of-code">${esc(a.fix_id)}</span></div>`
      + `<div class="of-item-sub">${esc(a.work_date)} ${esc(a.time)}`
      + `${a.reason ? " · " + esc(a.reason) : ""}`
      + ` <span class="badge ${formStatusCls(a.status)}">${esc(formStatusText(a.status))}</span></div></div></div>`).join("");
    const sv = svcs.map((r) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(r.kind_name || r.kind)} <span class="of-code">${esc(r.request_id)}</span></div>`
      + `<div class="of-item-sub">${esc(r.title || "-")}`
      + `${r.location ? " · " + esc(r.location) : ""}`
      + `${r.urgency && r.urgency !== "一般" ? ` <span class="badge badge-amber">${esc(r.urgency)}</span>` : ""}`
      + `${r.assignee ? ` · 归口 ${esc(r.assignee)}` : ""}`
      + ` <span class="badge ${formStatusCls(r.status)}">${esc(formStatusText(r.status))}</span></div>`
      + attChipsHtml(r.attachments, esc) + `</div></div>`).join("");
    return balHtml
      + `<div class="of-mine-label">费用报销（${exps.length}）</div>${ex || '<div class="of-note">无</div>'}`
      + `<div class="of-mine-label">请假申请（${leaves.length}）</div>${lv || '<div class="of-note">无</div>'}`
      + `<div class="of-mine-label">补考勤打卡（${fixes.length}）</div>${kb || '<div class="of-note">无</div>'}`
      + `<div class="of-mine-label">服务工单（${svcs.length}）</div>${sv || '<div class="of-note">无</div>'}`;
  }

  // ---------- 办公桌面：一级文件夹瓷片 / 二级应用瓷片 / 通用窗口 ----------

  let oaWinClose = null;   // 当前窗口的关闭函数（同一时刻只允许一个窗口）

  function closeOaWin() {
    if (oaWinClose) { const fn = oaWinClose; oaWinClose = null; fn(); }
  }

  /** 一级桌面宫格：文件夹瓷片（内嵌该文件夹的应用图标，像手机桌面上的文件夹） */
  function deskHtml(ctx) {
    const icon = ctx.UI.icon;
    const esc = ctx.UI.esc;
    return OA_FOLDERS.map((f) => {
      const apps = oaAppsOf(f.id);
      const mini = apps.slice(0, 4).map((a) => `<span class="of-desk-mini">${icon(a.icon, 13)}</span>`).join("");
      return `<button type="button" class="of-desk-tile" data-folder="${esc(f.id)}"`
        + ` title="${esc(`${f.name}：${apps.map((a) => a.name).join(" / ")}`)}">`
        + `<span class="of-desk-ico">${mini}</span>`
        + `<span class="of-desk-name">${esc(f.name)}</span>`
        + `<span class="of-desk-cnt">共 ${apps.length} 项</span></button>`;
    }).join("");
  }

  /** 服务工单面板（6 类服务共用同一个渲染器）：SERVICE_SCHEMA 即表单；材料登记控件用
   *  data 属性定位（同页可多实例），面板搬进应用窗口后绑定依然有效 */
  function servicePanelHtml(kind, ctx, S) {
    const s = SERVICE_SCHEMA[kind];
    const esc = ctx.UI.esc;
    const fields = s.fields.map((f) => {
      const req = f.required ? ' <span class="of-req">*</span>' : "";
      const body = f.type === "select"
        ? `<select class="of-svc-input" data-svc-key="${esc(f.key)}">
             <option value="">请选择…</option>
             ${(f.options || []).map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("")}
           </select>`
        : `<input class="of-svc-input" data-svc-key="${esc(f.key)}"
             type="${f.type === "date" ? "date" : f.type === "number" ? "number" : "text"}"
             placeholder="${esc(f.ph || "")}"/>`;
      return `<label class="of-svc-field"><span class="of-svc-label">${esc(f.label)}${req}</span>${body}</label>`;
    }).join("");
    const kinds = s.att || [];
    const attRow = kinds.length ? `
              <div class="of-upload-row">
                <span class="of-upload-label">可选材料</span>
                <span class="chip-row" data-up-kinds>
                  ${kinds.map((k, i) => `<button type="button" class="chip${i === 0 ? " active" : ""}"
                    data-upkind="${esc(k)}">${esc(k)}</button>`).join("")}
                </span>
                <label class="btn btn-outline btn-sm of-upload-pick">选择文件
                  <input type="file" data-up-file multiple hidden />
                </label>
                <span class="of-upload-hint">演示态只登记文件名与大小，不上传原文件</span>
              </div>
              <div data-up-list>${uploadsHtml(S, esc, kinds)}</div>` : "";
    return `
          <div class="of-panel" data-app="${esc(kind)}">
          <section class="of-a" id="of-svc-${esc(kind)}">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">${esc(s.name)}</div>
                <div class="card-extra"><span class="badge badge-purple">对话式提单</span>
                  <span class="badge badge-gray">${esc(s.badge)}</span></div></div>
              <div class="of-svc-form">${fields}</div>${attRow}
              <div class="of-upload-hint">${esc(s.hint)}</div>
              <button class="btn btn-primary btn-sm" data-svc-submit="${esc(kind)}">提交给助手</button>
            </div>
          </section>
          </div>`;
  }

  /** 服务工单提交文案：按面板已填字段拼自然语言（缺项交给助手 clarify 反问补齐），
   *  末尾带事项类型，便于助手稳定识别到对应 kind */
  function svcQuery(kind, val) {
    const urgency = val("urgency") ? `，紧急程度${val("urgency")}` : "";
    if (kind === "it_repair") {
      return `报修：${val("location")} ${val("title")}${urgency}。（IT 故障报修）`;
    }
    if (kind === "it_access") {
      return `申请 ${val("system")} 系统的「${val("permission")}」权限，`
        + `账号 ${val("account") || "本人"}，事由：${val("title")}。（账号与权限申请）`;
    }
    if (kind === "it_device") {
      return `领用 ${val("qty") || 1} 台 ${val("device_type")}，用途：${val("title")}`
        + `${val("need_date") ? `，期望 ${val("need_date")} 领用` : ""}。（设备领用申请）`;
    }
    if (kind === "parking") {
      return `申请 ${val("area")} ${val("parking_type") || "车位"}，车牌 ${val("plate")}`
        + `${val("start") ? `，起租 ${val("start")}` : ""}，事由：${val("title")}。（车位申请）`;
    }
    if (kind === "visitor_vehicle") {
      return `报备访客车辆 ${val("plate")}，${val("visit_date")}`
        + `${val("visit_time") ? " " + val("visit_time") : ""} 来访，被访 ${val("host")}`
        + `${val("visitor") ? `，访客 ${val("visitor")}` : ""}，事由：${val("title")}。（访客车辆报备）`;
    }
    return `申请公务用车，${val("use_from")}${val("use_time") ? " " + val("use_time") : ""} `
      + `出发去 ${val("dest")}，事由：${val("title")}`
      + `${val("passengers") ? `，乘车 ${val("passengers")} 人` : ""}`
      + `${val("need_driver") ? `，${val("need_driver")}司机` : ""}。（公务用车申请）`;
  }

  /** 提交某块服务工单面板：拼 query + 带上该面板类别下已登记的材料编号，交给助手办理 */
  function submitServicePanel(panel, ctx, S, send) {
    if (!panel || typeof send !== "function") return;
    const kind = panel.dataset.app;
    const schema = SERVICE_SCHEMA[kind];
    if (!schema) return;
    const val = (key) => {
      const el = panel.querySelector(`[data-svc-key="${key}"]`);
      return el ? String(el.value || "").trim() : "";
    };
    const q = svcQuery(kind, val).replace(/\s+/g, " ").replace("： ", "：").trim();
    const kinds = schema.att || [];
    const ids = (S.uploads || [])
      .filter((u) => kinds.indexOf(String(u.kind || "")) >= 0)
      .map((u) => u.attachment_id);
    send(q, undefined, ids.length ? ids : null);
  }

  /** 二级文件夹窗口内容：该文件夹下的应用瓷片宫格（点瓷片开三级应用窗口） */
  function appTilesHtml(folderId, ctx) {
    const icon = ctx.UI.icon;
    const esc = ctx.UI.esc;
    return `<div class="of-apps">` + oaAppsOf(folderId).map((a) => `<button type="button" class="of-app-tile" data-app="${esc(a.id)}">
        <span class="of-app-ico">${icon(a.icon, 22)}</span>
        <span class="of-app-name">${esc(a.name)}</span>
        <span class="of-app-desc">${esc(a.desc)}</span>
      </button>`).join("") + "</div>";
  }

  /**
   * 通用窗口：沿用「我的申请单据 · 展开」那套窗口范式（标题 + 关闭 + 贴内容自适应高度 + 滚动条）。
   * 传 node ＝把某个应用的功能面板整块搬进窗口（关闭时搬回 .of-panels，既有绑定与状态原样保留）；
   * 传 html ＝窗口内容为一段 HTML（文件夹窗口用）。
   * 关闭：右上角「关闭」、点遮罩、Esc；开新窗口前先关旧窗口（同一时刻只留一个）。
   */
  function openOaWin(opts, ctx) {
    closeOaWin();
    const esc = ctx.UI.esc;
    const mask = document.createElement("div");
    mask.className = "of-viewer-mask";
    mask.innerHTML = `
      <div class="of-viewer${opts.cls ? " " + opts.cls : ""}" role="dialog" aria-modal="true" aria-label="${esc(opts.title)}">
        <div class="of-viewer-head">
          <div class="of-viewer-title">${esc(opts.title)}${opts.badge ? ` <span class="badge badge-gray">${esc(opts.badge)}</span>` : ""}</div>
          <div class="of-viewer-actions"><button type="button" class="btn btn-outline btn-sm" data-close="1">关闭</button></div>
        </div>
        <div class="of-viewer-body"></div>
      </div>`;
    const body = mask.querySelector(".of-viewer-body");
    if (opts.node) body.appendChild(opts.node);
    else body.innerHTML = opts.html || "";
    document.body.appendChild(mask);
    oaWinClose = close;
    function onKey(e) { if (e.key === "Escape") close(); }
    function close() {
      if (opts.node && opts.node.parentNode === body) {
        const store = document.querySelector(".of-panels");
        if (store) store.appendChild(opts.node);
        else opts.node.remove();
      }
      // 连叠在上面的其它浮窗（如申请单据的「展开」）一起收掉，避免留下孤儿窗口
      document.querySelectorAll(".of-viewer-mask").forEach((m) => m.remove());
      document.removeEventListener("keydown", onKey);
      if (oaWinClose === close) oaWinClose = null;
    }
    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-close]")) { close(); return; }
      // 只认二级窗口里的应用瓷片：不能用 [data-app] —— 搬进来的面板外层 .of-panel 也带这个属性
      const tile = e.target.closest(".of-app-tile[data-app]");
      if (tile) openAppWin(tile.dataset.app, ctx);
    });
    document.addEventListener("keydown", onKey);
  }

  /** 二级：文件夹窗口（应用瓷片宫格） */
  function openFolderWin(folderId, ctx) {
    const f = oaFolder(folderId);
    if (!f) return;
    openOaWin({
      title: f.name,
      badge: `共 ${oaAppsOf(f.id).length} 项`,
      html: appTilesHtml(f.id, ctx),
      cls: "of-viewer-folder",
    }, ctx);
  }

  /** 三级：应用窗口（把该应用的功能面板整块搬进窗口） */
  function openAppWin(appId, ctx) {
    const app = oaApp(appId);
    if (!app) return;
    const folder = oaFolder(app.folder);
    // 先收掉当前窗口（面板会归位到 .of-panels），再取面板节点，否则取到的是"面板还在旧窗口里"的空引用
    closeOaWin();
    const node = document.querySelector(`.of-panels > .of-panel[data-app="${appId}"]`);
    openOaWin({
      title: app.name,
      badge: folder ? folder.name : "",
      node: node || null,
      html: node ? "" : '<div class="of-note">该应用面板尚未接入。</div>',
      cls: "of-viewer-app",
    }, ctx);
  }

  /**
   * 反问弹窗：按后端 clarify.fields 渲染「多选 chips / 输入框」，用户补齐后组装文本回给助手。
   * type: select→单选 chips；multiselect→可切换 chips；date/时间/number/text→对应输入框。
   * @param {object} clarify  {question, fields:[{key,label,type,options?,required?}]}
   * @param {object} ctx      渲染上下文（取 UI.toast / UI.esc）
   * @param {string} base     原始请求（补全文本的前缀，保证多轮反问不丢上下文）
   * @param {function} onSubmit 提交回调（收到组装后的完整文本）
   */
  function openClarify(clarify, ctx, base, onSubmit) {
    if (!clarify || !clarify.fields || !clarify.fields.length) return;
    const esc = ctx.UI.esc;
    const fieldHtml = (f, i) => {
      const key = f.key || ("f" + i);
      const label = String(f.label || key);
      const t = String(f.type || "text");
      // 已知值预填：助手从原话解析出的值随 clarify 下发，弹窗打开即"半填"
      const pre = f.value == null ? "" : String(f.value);
      let body;
      if (t === "select") {
        const opts = (f.options || []).map((o) =>
          `<option value="${esc(o)}"${String(o) === pre ? " selected" : ""}>${esc(o)}</option>`).join("");
        body = opts
          ? `<select class="of-clarify-select"><option value=""${pre ? "" : " selected"}>请选择…</option>${opts}</select>`
          : `<input class="of-clarify-input" type="text" placeholder="请输入" value="${esc(pre)}" />`;
      } else if (t === "multiselect") {
        const chosen = pre ? pre.split(/[、,，;；\s]+/) : [];
        const opts = (f.options || []).map((o) =>
          `<button type="button" class="of-clarify-chip${chosen.indexOf(String(o)) >= 0 ? " active" : ""}" data-val="${esc(o)}">${esc(o)}</button>`).join("");
        body = `<div class="of-clarify-chips" data-type="multiselect">${opts || '<span class="of-note">暂无可选项，请直接在对话里说明</span>'}</div>`;
      } else {
        const itype = t === "date" ? "date"
          : (t === "time" || t === "时间") ? "time"
            : t === "number" ? "number" : "text";
        body = `<input class="of-clarify-input" type="${itype}"${t === "number" ? ' step="any" min="0"' : ""} value="${esc(pre)}" />`;
      }
      return `<div class="of-clarify-field" data-key="${esc(key)}" data-label="${esc(label)}"`
        + ` data-type="${esc(t)}" data-required="${f.required ? "1" : "0"}">`
        + `<div class="of-clarify-label">${esc(label)}${f.required ? ' <span class="of-req">*</span>' : ""}</div>`
        + body + "</div>";
    };
    const mask = document.createElement("div");
    mask.className = "of-clarify-mask";
    mask.innerHTML = `<div class="of-clarify" role="dialog" aria-modal="true">
      <div class="of-clarify-head">
        <div class="of-clarify-title">请补充办理信息</div>
        <button type="button" class="of-clarify-close" data-close="1" aria-label="关闭">×</button>
      </div>
      <div class="of-clarify-q">${esc(clarify.question || "")}</div>
      <div class="of-clarify-body">${(clarify.fields || []).map(fieldHtml).join("")}</div>
      <div class="of-clarify-foot">
        <button type="button" class="btn btn-outline btn-sm" data-close="1">取消</button>
        <button type="button" class="btn btn-primary btn-sm" data-submit="1">提交</button>
      </div></div>`;
    document.body.appendChild(mask);
    const close = () => { if (mask.parentNode) mask.parentNode.removeChild(mask); };

    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-close]")) { close(); return; }
      const chip = e.target.closest(".of-clarify-chip");
      if (!chip) return;
      const box = chip.parentNode;
      if (box.dataset.type === "multiselect") chip.classList.toggle("active");
      else box.querySelectorAll(".of-clarify-chip").forEach(
        (c) => c.classList.toggle("active", c === chip));
    });

    mask.querySelector("[data-submit]").addEventListener("click", () => {
      const parts = [];
      for (const field of mask.querySelectorAll(".of-clarify-field")) {
        const type = field.dataset.type;
        let val = "";
        if (type === "select") {
          const sel = field.querySelector(".of-clarify-select");
          if (sel) val = (sel.value || "").trim();
          else { const inp = field.querySelector(".of-clarify-input"); val = ((inp && inp.value) || "").trim(); }
        } else if (type === "multiselect") {
          val = Array.from(field.querySelectorAll(".of-clarify-chip.active"))
            .map((c) => c.dataset.val).join("、");
        } else {
          const inp = field.querySelector(".of-clarify-input");
          val = ((inp && inp.value) || "").trim();
        }
        if (field.dataset.required === "1" && !val) {
          ctx.UI.toast(`请填写：${field.dataset.label}`, "err");
          return;
        }
        if (val) parts.push(`${field.dataset.label}=${val}`);
      }
      close();
      onSubmit(`${base}${parts.length ? "；补充信息：" + parts.join("；") : ""}`);
    });
  }

  // ---------- 制造数据速查（只读；口径=后端注册查询，前端只做同构渲染） ----------

  function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
  function fmt(v) { return num(v).toLocaleString("zh-CN"); }

  function ageBandHtml(rows) {
    const bands = [[0, 30, "0-30"], [31, 60, "31-60"], [61, 90, "61-90"], [91, 1e9, "90+"]];
    const counts = bands.map(([a, b]) => (rows || []).filter(
      (r) => { const d = num(r.age_days); return d >= a && d <= b; }).length);
    const max = Math.max(1, ...counts);
    const cols = bands.map(([a, b, label], i) => {
      const h = Math.round((counts[i] / max) * 100);
      const bad = label === "90+" && counts[i] > 0 ? " bad" : "";
      return `<div class="of-age-col"><div class="of-age-num">${counts[i]}</div>`
        + `<div class="of-age-track"><div class="of-age-bar${bad}" style="height:${h}%"></div></div>`
        + `<div class="of-age-cap">${label}</div></div>`;
    }).join("");
    return `<div class="of-age"><div class="of-age-head">库龄分档（天）</div>`
      + `<div class="of-age-bars">${cols}</div></div>`;
  }

  function snapTbl(heads, rows) {
    return `<div class="of-tbl"><table><thead><tr>${heads.map((h) => `<th>${h}</th>`).join("")}`
      + `</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  const SNAP_LIMIT = 7;        // 无关键词时每块预览行数
  const SNAP_HIT_LIMIT = 12;   // 有关键词时每块最多展示的命中行数

  /** 关键词可命中的字段（料号/型号/批号/名称等，与表格列一致） */
  function rowText(key, r) {
    const cols = {
      material_stock: [r.part_no, r.part_name, r.warehouse, r.bin_location],
      shortage: [r.part_no, r.part_name],
      finished_stock: [r.product_model, r.warehouse, r.batch_no],
      incoming_batch: [r.batch_no, r.supplier, r.part_no],
    }[key] || [];
    return cols.filter((v) => v !== undefined && v !== null && v !== "")
      .join(" ").toLowerCase();
  }

  /** 速查块主体：q 为小写关键词，命中行保持「点行即交给助手问答」 */
  function blockBody(key, b, esc, q) {
    if (!b || !b.ok) {
      const denied = !!(b && b.authorized === false);
      const msg = denied ? (b.message || "当前岗位无权限查询")
        : `暂不可用（${(b && b.message) || "后端未返回"}）`;
      // 无权限块：给出「申请查询权限」入口（与案例检索同构）。
      const apply = (denied && b.requestable !== false)
        ? `<div class="of-apply-bar">`
          + `<button class="btn btn-primary btn-sm" data-apply-table="${esc(key)}">申请查询权限</button>`
          + `<span class="of-apply-hint">提交申请，由${esc(b.owner_label || "职责岗")}审批通过后即可查询</span>`
          + `</div>`
        : "";
      return `<div class="of-note">${esc(msg)}</div>${apply}`;
    }
    const all = b.rows || [];
    const rows = q ? all.filter((r) => rowText(key, r).includes(q)) : all;
    if (!rows.length) {
      return q
        ? '<div class="of-note">无匹配记录：换个关键词，或直接在下方对话里提问。</div>'
        : '<div class="of-note">暂无数据。</div>';
    }
    const limit = q ? SNAP_HIT_LIMIT : SNAP_LIMIT;
    const more = rows.length > limit
      ? `<div class="of-note">${q ? `匹配 ${rows.length} 行` : `共 ${rows.length} 行`}，此处显示前 ${limit} 行；点行可交给助手问答</div>` : "";
    if (key === "material_stock") {
      const body = rows.slice(0, limit).map((r) => {
        const short = num(r.available_qty) < num(r.safety_stock);
        return `<tr class="${short ? "of-short" : ""}" data-q="${esc(`${r.part_no} 还有多少库存`)}">`
          + `<td class="of-mono">${esc(r.part_no)}</td><td>${esc(r.part_name || "-")}</td>`
          + `<td>${esc(r.warehouse || "-")}<span class="of-code">${esc(r.bin_location || "")}</span></td>`
          + `<td class="of-num">${fmt(r.on_hand_qty)}</td>`
          + `<td class="of-num${short ? " of-bad" : ""}">${fmt(r.available_qty)}</td>`
          + `<td class="of-num">${fmt(r.age_days)}</td></tr>`;
      }).join("");
      return snapTbl(["料号", "名称", "仓库·库位", "在库", "可用", "库龄"], [body]) + more
        + ageBandHtml(rows);
    }
    if (key === "shortage") {
      const body = rows.slice(0, limit).map((r) => `<tr data-q="${esc(`${r.part_no} 缺多少料`)}">`
        + `<td class="of-mono">${esc(r.part_no)}</td><td>${esc(r.part_name || "-")}</td>`
        + `<td class="of-num">${fmt(r.available_qty)}</td>`
        + `<td class="of-num">${fmt(r.safety_stock)}</td>`
        + `<td><span class="badge badge-red">缺 ${fmt(r.shortage_qty)}</span></td></tr>`).join("");
      return snapTbl(["料号", "名称", "可用", "安全库存", "缺口"], [body]) + more;
    }
    if (key === "finished_stock") {
      const body = rows.slice(0, limit).map((r) => `<tr data-q="${esc(`${r.product_model} 成品现在能发多少`)}">`
        + `<td>${esc(r.product_model)}</td><td>${esc(r.warehouse || "-")}</td>`
        + `<td class="of-mono">${esc(r.batch_no || "-")}</td>`
        + `<td class="of-num">${fmt(r.on_hand_qty)}</td>`
        + `<td class="of-num">${fmt(r.available_qty)}</td>`
        + `<td class="of-num">${fmt(r.age_days)}</td></tr>`).join("");
      return snapTbl(["型号", "仓库", "批号", "在库", "可发 ATP", "库龄"], [body]) + more;
    }
    if (key === "incoming_batch") {
      const body = rows.slice(0, limit).map((r) => {
        const st = SNAP_STATUS[r.inspect_status] || { t: r.inspect_status || "-", c: "badge-gray" };
        return `<tr data-q="${esc(`${r.batch_no} 这批来料检验结论是什么`)}">`
          + `<td class="of-mono">${esc(r.batch_no)}</td><td>${esc(r.supplier || "-")}</td>`
          + `<td class="of-mono">${esc(r.part_no)}</td>`
          + `<td class="of-num">${fmt(r.qty)}</td>`
          + `<td class="of-num">${fmt(r.qualified_qty)}</td>`
          + `<td><span class="badge ${st.c}">${esc(st.t)}</span></td>`
          + `<td class="of-mono">${esc(String(r.arrival_time || "").slice(5, 16))}</td></tr>`;
      }).join("");
      return snapTbl(["批号", "供应商", "料号", "到货数", "合格数", "IQC 结论", "到货时间"], [body]) + more;
    }
    return "";
  }

  function snapHtml(S, esc) {
    const snap = S.snap;
    if (!snap) return '<div class="of-note">制造数据加载中…</div>';
    const kq = (S.snapQuery || "").trim();
    const q = kq.toLowerCase();
    const blocks = snap.blocks || {};
    const defs = [
      ["material_stock", "材料库存"],
      ["shortage", "缺料清单"],
      ["finished_stock", "成品库存与可发量"],
      ["incoming_batch", "来料批次与 IQC 结论"],
    ];
    let hit = 0;
    let total = 0;
    const cards = defs.map(([key, title]) => {
      const b = blocks[key] || {};
      const all = b.rows || [];
      const n = q ? all.filter((r) => rowText(key, r).includes(q)).length : all.length;
      if (q && b.ok) { hit += n; total += all.length; }
      const cnt = b.ok
        ? `<span class="of-snap-count">${q ? `命中 ${n} / 共 ${all.length}` : `${b.count} 条`}</span>` : "";
      return `<div class="of-snap-block">
        <div class="of-snap-head">
          <div class="of-snap-title">${esc(title)} ${cnt}</div>
        </div>
        <div class="of-snap-note">${esc(b.note || "")}</div>
        ${blockBody(key, b, esc, q)}
      </div>`;
    }).join("");
    const hitLine = q
      ? `<div class="of-snap-hit">关键词「<b>${esc(kq)}</b>」命中 <b>${hit}</b> 条（四块共 ${total} 条）`
        + `${hit ? "；点表格行即交给助手问答" : "；换个关键词，或直接在下方对话里提问"}</div>`
      : "";
    const foot = `<div class="of-snap-foot">口径：可用量 = 在库 − 预留（冻结量不计可用）；库龄按天，≥90 天视为呆滞。`
      + `数据源：${esc(snap.source || "统一注册查询")}（只读）`
      + `${snap.degraded ? ' · <span class="of-bad">含降级块</span>' : ""}</div>`;
    return hitLine + `<div class="of-snap-grid">${cards}</div>` + foot;
  }

  // ---------- 报销材料上传与科目整理 ----------

  /** 已登记材料清单（含必传/选传徽标 + 查看登记/移除；演示态不上传原文件） */
  function uploadsHtml(S, esc, kinds) {
    let ups = S.uploads || [];
    // kinds 传入时只显示该面板相关的材料（报销面板与服务工单面板各看各的类别）
    if (kinds && kinds.length) {
      ups = ups.filter((a) => kinds.indexOf(String(a.kind || "其他")) >= 0);
    }
    if (!ups.length) {
      return '<div class="of-note">尚未登记材料。选择类别后「选择文件」登记（演示态只记文件名与大小，'
        + '不上传原文件；发票为报销必传，出差报告 / 行程单 / 付款凭证为选传）。</div>';
    }
    return `<div class="of-upload-list">` + ups.map((a) => {
      const id = String(a.attachment_id || "");
      return `<div class="of-upload-item">
        <span class="badge badge-purple">${esc(a.kind || "其他")}</span>
        <span class="of-upload-name" title="${esc(a.filename || id)}">${esc(a.filename || id)}</span>
        <span class="of-upload-size">≈${esc(fmtSize(a.size))}</span>
        <span class="badge badge-gray">模拟</span>
        <span class="badge ${kindBadgeCls(a.kind)}">${esc(kindBadgeText(a.kind))}</span>
        <button class="btn btn-outline btn-sm" data-att-view="${esc(id)}">查看登记</button>
        <button class="btn btn-danger btn-sm" data-att-del="${esc(id)}">移除</button>
      </div>`;
    }).join("") + "</div>";
  }

  /** 整理报销科目结果面板（二级科目拆分 + 必传/选传材料核对） */
  function orgPanelHtml(S, esc) {
    const o = S.org;
    if (!o) {
      return '<div class="of-note">填写类别与金额后点「整理报销科目」：按事由自动拆分二级科目，'
        + '并核对该类别必传 / 选传材料。</div>';
    }
    if (!o.ok) return `<div class="of-note of-bad">整理失败：${esc(o.message || "未知原因")}</div>`;
    const items = (o.items || []).map((it) => `<div class="of-org-line">`
      + `<span class="of-org-sub">${esc(it.subject)}</span>`
      + `<span class="of-org-amt">¥${esc(it.amount == null ? "-" : it.amount)}</span>`
      + `<span class="of-org-note">${esc(it.note || "")}</span></div>`).join("");
    const req = (o.required_kinds || []).map((k) =>
      `<span class="badge badge-amber">${esc(k)}·必传</span>`).join(" ");
    const opt = (o.optional_kinds || []).map((k) =>
      `<span class="badge badge-gray">${esc(k)}·选传</span>`).join(" ");
    const miss = (o.missing_required || []).length
      ? `<div class="of-org-miss">缺必传材料：${esc((o.missing_required || []).join("、"))}`
        + `（可继续提交，财务审批时会要求补件）</div>` : "";
    return `<div class="of-org">
      <div class="of-org-head">「${esc(o.category)}」科目整理 · 合计 ¥${esc(o.amount)}</div>
      <div class="of-org-items">${items}</div>
      <div class="of-org-kinds">${req} ${opt}</div>${miss}
      <div class="of-org-hint">可用二级科目：${esc((o.subjects || []).join(" / "))}</div></div>`;
  }

  /**
   * 病假条补件弹窗：上传图片（kind=病假条）→ 取得 attachment_id → 挂到该请假单上。
   * 与"请病假后续补充病假条"的真实场景对应；补件仅限本人单据（后端校验归属）。
   */
  function openSupplement(leaveId, ctx, api, actor, onDone) {
    const esc = ctx.UI.esc;
    const mask = document.createElement("div");
    mask.className = "of-clarify-mask";
    mask.innerHTML = `<div class="of-clarify" role="dialog" aria-modal="true">
      <div class="of-clarify-head">
        <div class="of-clarify-title">补充病假条 · ${esc(leaveId)}</div>
        <button type="button" class="of-clarify-close" data-close="1" aria-label="关闭">×</button>
      </div>
      <div class="of-clarify-q">选择病假条图片登记（演示态只记文件名与大小、不上传原文件），提交后挂到该请假单，HR 复核即可放行。</div>
      <div class="of-clarify-body">
        <div class="of-clarify-field" data-type="file">
          <div class="of-clarify-label">病假条图片</div>
          <input class="of-clarify-input" id="of-supp-file" type="file" accept="image/*" />
        </div>
      </div>
      <div class="of-clarify-foot">
        <button type="button" class="btn btn-outline btn-sm" data-close="1">取消</button>
        <button type="button" class="btn btn-primary btn-sm" data-submit="1">登记并补件</button>
      </div></div>`;
    document.body.appendChild(mask);
    const close = () => { if (mask.parentNode) mask.parentNode.removeChild(mask); };
    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-close]")) { close(); return; }
      if (!e.target.closest("[data-submit]")) return;
      const inp = mask.querySelector("#of-supp-file");
      const file = inp && inp.files && inp.files[0];
      if (!file) { ctx.UI.toast("请选择病假条图片", "err"); return; }
      const btn = mask.querySelector("[data-submit]");
      btn.disabled = true;
      btn.textContent = "登记中…";
      api.officeUpload(file, "病假条", actor, `补件 ${leaveId}`)
        .then((meta) => api.officeLeaveAttachment(leaveId, meta.attachment_id, actor))
        .then(async (out) => {
          close();
          ctx.UI.toast("病假条已登记并补件，待 HR 复核", "ok");
          if (onDone) await onDone(out);
        })
        .catch((err) => {
          btn.disabled = false;
          btn.textContent = "登记并补件";
          ctx.UI.toast(`补件失败：${ctx.UI.friendlyError(err).text}`, "err", 5000);
        });
    });
  }

  // ---------- 主渲染（两屏共用；按 ctx.state.screen 决定内容） ----------

  function render(root, ctx) {
    const { UI, api, state } = ctx;
    const esc = UI.esc;
    const isOa = state.screen === "oa";
    const mode = isOa ? "oa" : "data";
    const LABEL = isOa ? "OA 协同" : "数据速查";
    state.screenState = state.screenState || {};
    const S = state.screenState[mode] || (state.screenState[mode] = initialState(mode));
    const actor = actorOf(ctx);
    const online = api.online();
    const QUICK = isOa ? oaQuick() : DATA_QUICK.map((q) => ({ kind: "", q }));

    const desc = isOa
      ? `办公事务办理入口：<b>会议室日程与预订</b>（按楼栋 / 容量 / 是否需授权筛选）、<b>餐厅与菜单</b>（园区食堂 / 商务餐厅 / 夜宵档）、`
        + `<b>我的预订与订单</b>；以及 <b>OA 自助办理</b>——<b>费用报销</b> / <b>请假申请</b> / `
        + `<b>补考勤打卡</b>，对话式提单、关键信息缺失时弹窗反问补齐。<b>材料登记</b>（演示态"虚假上传"：只记文件名与大小，不上传原文件）：报销可登记发票（必传）、`
        + `出差报告 / 行程单 / 付款凭证（选传），并由助手<b>整理报销科目</b>（按事由拆二级科目 + 材料核对）；`
        + `请病假可提交后<b>补充病假条图片</b>。报告厅 / 培训室 / 商务客餐 / 超 500 元订单，以及报销 / 请假 / 补卡`
        + `均先发起人工确认；办理动作由后端 Agent 执行并全程留痕可审计。`
      : `制造数据只读速查：<b>材料库存</b>、<b>成品库存与可发量</b>、<b>来料批次与检验结论</b>、`
        + `<b>库龄呆滞</b>、<b>缺料缺口</b>、<b>批次追溯</b>。数据走平台统一口径注册查询，`
        + `受岗位数据权限约束；<b>只读</b>，不办理任何写操作。`;

    // 切屏 / 重渲染前先清掉残留浮窗：窗口挂在 body 上、不在 root 里，不清会留下孤儿节点与重名 id
    document.querySelectorAll(".of-viewer-mask, .of-clarify-mask").forEach((n) => n.remove());
    oaWinClose = null;

    root.innerHTML = `
      <div class="content-inner content-wide">
        <div class="of-top">
          <div class="page-title">${LABEL}
            <span class="badge badge-outline" id="of-backend">后端检测中…</span>
            <span class="badge badge-outline" id="of-llm">LLM 检测中…</span>
          </div>
          <div class="page-desc of-desc">${desc}</div>
        </div>

        <div class="of-grid${isOa ? " of-grid-oa" : " of-grid-data"}">
          <!-- 一、对话：置顶（问答即入口） -->
          <section class="of-a of-a-chat">
            <div class="card of-chat-card">
              <div class="card-head of-chat-head"><div class="card-title">${LABEL}对话</div>
                <div class="card-extra"><span class="badge badge-purple">Agentic Agent</span>
                  <span class="badge badge-gray">${isOa ? "办公办理 · 写操作留痕" : "数据速查 · 只读"}</span></div></div>
              <div class="of-chat"><div class="chat-scroll" id="of-chat"></div></div>
              <div class="of-quick" id="of-quick">
                ${QUICK.map((it) => (it.kind
                  ? `<button class="chip chip-hist" data-quick="${esc(it.q)}" title="${esc(it.q)}"><span class="chip-kind">${esc(it.kind)}</span>${esc(it.q)}</button>`
                  : `<span class="chip chip-hist chip-ask">`
                    + `<button type="button" class="chip-main" data-quick="${esc(it.q)}" title="${esc(it.q)}">${esc(it.q)}</button>`
                    + `<button type="button" class="chip-case" data-case-ask="${esc(it.q)}" title="问案例：带着这句话去案例检索室提问">问案例</button>`
                    + `</span>`)).join("")}
              </div>
              <div class="chat-inputbar of-inputbar">
                <div class="chat-input-row">
                  <input id="of-input" class="chat-input" autocomplete="off"
                    placeholder="${isOa
                      ? "例如：订明天 14 点的 8 人会议室 / 中午订两份狮子头套餐 / 查我的预订与订单"
                      : "例如：M8 螺栓还有多少库存 / A 型号成品能发多少 / 哪些料库龄超 90 天"}" />
                  <button class="chat-send" id="of-send">${SEND_ICON}</button>
                </div>
                <div class="chat-hint">${isOa
                  ? "工具调用由后端 Agent 自主决策并全程留痕；写操作与高影响事项先人工确认"
                  : "数据查询只读、受岗位权限约束；口径=统一注册查询（可用量=在库−预留）"}</div>
              </div>
            </div>
          </section>

          ${isOa ? `
          <!-- 办公桌面：像手机桌面一样（文件夹瓷片 → 文件夹窗口 → 应用窗口），点瓷片即展开对应功能 -->
          <section class="of-a of-a-desktop">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">办公桌面</div>
                <div class="card-extra"><span class="badge badge-purple">文件夹 → 应用 → 展开功能</span>
                  <span class="badge badge-gray">点瓷片打开 · 也可直接在右侧对话提问</span></div></div>
              <div class="of-desk" id="of-desk">${deskHtml(ctx)}</div>
            </div>
          </section>` : ""}

          ${isOa ? "" : `
          <!-- 二、制造数据速查：输入料号/型号/批号定位到行 → 点行即交给助手问答 -->
          <section class="of-a of-a-snap">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">制造数据速查</div>
                <div class="card-extra"><span class="badge badge-purple">只读 · 统一口径</span>
                  <button class="btn btn-outline btn-sm" id="of-snap-refresh">刷新</button></div></div>
              <div class="of-snap-search">
                <input id="of-snap-search" type="text" autocomplete="off"
                  placeholder="搜料号 / 型号 / 批号 / 名称（如 BAT-LI-3000、A型号、MAT-CON-1014）"
                  value="${esc(S.snapQuery || "")}"/>
                <button class="btn btn-outline btn-sm" id="of-snap-clear"${S.snapQuery ? "" : " hidden"}>清除</button>
              </div>
              <div id="of-snap">${snapHtml(S, esc)}</div>
            </div>
          </section>`}

          ${isOa ? `
          <!-- 面板仓库（隐藏）：各应用的功能面板；点桌面瓷片时整块搬进应用窗口，DOM 与既有绑定原样保留。
               新增 OA 功能：在 OA_APPS 加一行 + 这里加一个同 data-app 的 .of-panel 即可。 -->
          <div class="of-panels" hidden>
          ${Object.keys(SERVICE_SCHEMA).map((k) => servicePanelHtml(k, ctx, S)).join("")}
          <div class="of-panel" data-app="expense">
          <!-- 费用报销：三入口 + 报销材料登记 + 金额表单 + 整理科目（材料随本次报销一并提交） -->
          <section class="of-a of-a-entry">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">OA 自助办理</div>
                <div class="card-extra"><span class="badge badge-purple">对话式提单 · 上传票据 · 整理科目</span>
                  <span class="badge badge-gray">关键信息弹窗反问 · 提交后人工确认</span></div></div>
              <div class="of-entry">
                <div class="of-entry-item">
                  <div class="of-entry-title">费用报销</div>
                  <div class="of-entry-sub">差旅 / 招待 / 办公用品 / 交通 · 按票据报销，财务专员审批</div>
                  <button class="btn btn-primary btn-sm" data-entry="expense">我要报销</button>
                </div>
                <div class="of-entry-item">
                  <div class="of-entry-title">请假申请</div>
                  <div class="of-entry-sub">年假 / 事假 / 病假 / 调休 / 婚假 / 产假 · 部门主管审批</div>
                  <button class="btn btn-primary btn-sm" data-entry="leave">我要请假</button>
                </div>
                <div class="of-entry-item">
                  <div class="of-entry-title">补考勤打卡</div>
                  <div class="of-entry-sub">上班 / 下班补卡 · 漏打卡补录，HR 专员审批</div>
                  <button class="btn btn-primary btn-sm" data-entry="attendance">我要补卡</button>
                </div>
              </div>

              <div class="of-sub-head">报销材料登记（模拟上传）
                <span class="badge badge-purple">只记文件名/大小 · 不上传原文件</span>
                <span class="badge badge-gray">发票必传 · 出差报告等选传</span></div>
              <div class="of-upload-row">
                <span class="of-upload-label">材料类别</span>
                <span class="chip-row" id="of-up-kinds">
                  ${EXP_UPLOAD_KINDS.map((k) => `<button class="chip${S.upKind === k ? " active" : ""}"
                    data-upkind="${esc(k)}">${esc(k)}${k === "发票" ? ' <span class="of-req">*</span>' : ""}</button>`).join("")}
                </span>
                <label class="btn btn-outline btn-sm of-upload-pick">选择文件
                  <input type="file" id="of-up-file" multiple hidden />
                </label>
                <span class="of-upload-hint">单份 ≤ ${esc((metaCache && metaCache.attachment_max_mb) || 10)} MB；演示态仅登记文件名/大小，不上传原文件</span>
              </div>
              <div id="of-uploads">${uploadsHtml(S, esc, EXP_UPLOAD_KINDS)}</div>
              <div class="of-exp-form">
                <select id="of-exp-cat" title="报销类别" class="of-exp-cat">
                  ${EXP_CATEGORIES.map((c) => `<option value="${esc(c)}"${S.expCat === c ? " selected" : ""}>${esc(c)}</option>`).join("")}
                </select>
                <input id="of-exp-amount" class="of-exp-amount" type="number" min="0" step="any"
                  placeholder="金额（元）" value="${esc(S.expAmount)}"/>
                <input id="of-exp-note" class="of-exp-note" type="text"
                  placeholder="事由，如：高铁 980 + 住宿 306" value="${esc(S.expNote)}"/>
                <input id="of-exp-date" type="date" title="费用发生日期" value="${esc(S.expDate)}"/>
                <button class="btn btn-outline btn-sm" id="of-exp-organize">整理报销科目</button>
                <button class="btn btn-primary btn-sm" id="of-exp-submit">交给助手报销</button>
              </div>
              <div class="of-upload-hint">「交给助手报销」会把已登记材料（${(S.uploads || []).length} 份）随本次请求一并提交；也可直接在下方对话说明，缺失项由助手弹窗反问。</div>
              <div id="of-org">${orgPanelHtml(S, esc)}</div>
            </div>
          </section>
          </div>

          <div class="of-panel" data-app="leave">
          <!-- 请假申请：对话式提单（关键信息缺失由助手弹窗反问补齐） -->
          <section class="of-a" id="of-leave-panel">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">请假申请</div>
                <div class="card-extra"><span class="badge badge-purple">对话式提单</span>
                  <span class="badge badge-gray">部门主管审批 · 长假先人工确认</span></div></div>
              <div class="of-entry">
                <div class="of-entry-item">
                  <div class="of-entry-title">年假 / 事假 / 病假 / 调休 / 婚假 / 产假</div>
                  <div class="of-entry-sub">一句话即可：请假类型 + 起止日期 + 事由；信息缺失时助手会弹窗反问补齐</div>
                  <button class="btn btn-primary btn-sm" data-entry="leave">我要请假</button>
                </div>
              </div>
              <div class="of-upload-hint">病假可在提交后补充病假条图片；长假（≥3 天）与病假提交后一律先发起人工确认，不会直接放行。</div>
            </div>
          </section>
          </div>

          <div class="of-panel" data-app="attendance">
          <!-- 补考勤打卡：漏打卡补录，HR 专员审批 -->
          <section class="of-a" id="of-att-panel">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">补考勤打卡</div>
                <div class="card-extra"><span class="badge badge-purple">对话式提单</span>
                  <span class="badge badge-gray">HR 专员审批 · 提交后人工确认</span></div></div>
              <div class="of-entry">
                <div class="of-entry-item">
                  <div class="of-entry-title">上班 / 下班补卡</div>
                  <div class="of-entry-sub">说明日期 + 时间（如「补昨天 08:30 的上班卡」）+ 事由；信息缺失时助手会弹窗反问补齐</div>
                  <button class="btn btn-primary btn-sm" data-entry="attendance">我要补卡</button>
                </div>
              </div>
              <div class="of-upload-hint">补卡每次仅限一个时点；提交后先发起人工确认，不会直接放行。</div>
            </div>
          </section>
          </div>

          <div class="of-panel" data-app="rooms">
          <!-- 会议室目录：按楼栋 / 容量筛选 → 查可用 → 让助手预订 -->
          <section class="of-a of-a-rooms">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">会议室目录</div>
                <div class="card-extra" id="of-room-count"></div></div>
              <div class="chip-row" id="of-room-filter">
                <button class="chip${S.filter === "all" ? " active" : ""}" data-filter="all">全部</button>
                <button class="chip${S.filter === "cap8" ? " active" : ""}" data-filter="cap8">≥8 人</button>
                <button class="chip${S.filter === "cap20" ? " active" : ""}" data-filter="cap20">≥20 人</button>
                <button class="chip${S.filter === "approval" ? " active" : ""}" data-filter="approval">需授权</button>
                <span class="of-chip-sep"></span>
                ${["", "A栋", "B栋", "C栋", "D栋"].map((t) => `<button class="chip${S.tower === t ? " active" : ""}"
                  data-tower="${t}">${t || "全楼栋"}</button>`).join("")}
              </div>
              <div class="of-form">
                <input id="of-date" type="date" value="${esc(S.form.date)}" title="日期"/>
                <input id="of-start" type="time" value="${esc(S.form.start)}" title="开始"/>
                <input id="of-end" type="time" value="${esc(S.form.end)}" title="结束"/>
                <input id="of-cap" class="of-cap" type="number" min="1" value="${esc(S.form.cap)}" title="最少人数"/>
                <button class="btn btn-primary btn-sm of-form-submit" id="of-avail">查可用</button>
              </div>
              <div id="of-rooms-scroll">
                <div id="of-avail-result"></div>
                <div id="of-rooms">${roomListHtml(S, ctx)}</div>
              </div>
            </div>
          </section>
          </div>

          <div class="of-panel" data-app="dining">
          <!-- 餐厅与菜单 -->
          <section class="of-a of-a-rests">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">餐厅与菜单</div>
                <div class="card-extra"><span class="badge badge-gray" id="of-rest-count">${(S.restaurants || []).length
                  ? `${S.restaurants.length} 个餐厅可选` : "餐厅加载中…"}</span></div></div>
              <div class="chip-row" id="of-rests">
                ${(S.restaurants || []).map((r) => `<button class="chip${S.menuId === r.restaurant_id ? " active" : ""}"
                  data-menu="${esc(r.restaurant_id)}">${esc(r.name)}${r.need_approval ? "（客餐）" : ""}</button>`).join("")
                || '<span class="of-note">加载中…</span>'}
              </div>
              <div id="of-menu">${menuHtml(S, ctx)}</div>
            </div>
          </section>
          </div>

          <div class="of-panel" data-app="mine">
          <!-- 我的预订与订单 -->
          <section class="of-a of-a-mine">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">我的预订与订单</div>
                <div class="card-extra"><span class="badge badge-gray">操作人 ${esc(actor.name)}</span>
                  <button class="btn btn-outline btn-sm" id="of-refresh">刷新</button></div></div>
              <div id="of-mine">${mineHtml(S, esc)}</div>
            </div>
          </section>
          </div>

          <div class="of-panel" data-app="myforms">
          <!-- 我的申请单据：假期余额 + 报销/请假/补卡/服务工单单据（打开即全部展开，窗口自管理滚动） -->
          <section class="of-a of-a-myforms">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">我的申请单据</div>
                <div class="card-extra"><span class="badge badge-gray">报销 · 请假 · 补卡 · 服务工单</span>
                  <button class="btn btn-outline btn-sm" id="of-myform-refresh">刷新</button></div></div>
              <div id="of-myforms">${myFormsHtml(S, esc)}</div>
            </div>
          </section>
          </div>
          </div>` : ""}
        </div>
      </div>`;

    const $ = (sel) => document.querySelector(sel);
    const chat = $("#of-chat");
    const paintChat = () => {
      chat.innerHTML = S.messages.map((m) => messageHtml(m, ctx)).join("");
      chat.scrollTop = chat.scrollHeight;
    };
    paintChat();

    const toast = (msg, kind, ms) => UI.toast(msg, kind || "info", ms);

    // 两屏各自的加载器 / 绘制器（仅对应模式赋值，另一模式保持空实现）
    let loadMine = async () => {};
    let loadSnap = async () => {};
    let paintRooms = () => {};
    let paintAvail = () => {};
    let paintMenu = () => {};
    let loadMenu = async () => {};
    // 反问补全轮次计数（按原始请求 base 计数）：兜底防止"反问→补充→再反问"无限弹框
    const clarifyRounds = new Map();

    // ---------- 对话（企业助手 Agent；两屏共用后端能力） ----------

    const send = async (text, base, attachmentIds) => {
      const input = $("#of-input");
      const q = (text || input.value || "").trim();
      if (!q || S.sending) return;
      // 反问补全的原始请求前缀：首次请求即 base；多轮反问沿用最初的 base，避免上下文丢失
      const clarifyBase = base || q;
      if (!online) { toast("后端离线：办理与查询需要连接后端", "err"); return; }
      if (metaCache && metaCache.llm_available === false) {
        toast(`LLM 服务不可用：${LABEL}暂无法自主办理，请先用下方卡片`, "err");
        return;
      }
      // 携带材料：显式传入优先；报销/病假意图时自动带上已上传材料（避免"传了没用上"）
      const ups = (S.uploads || []).map((u) => u.attachment_id);
      const autoIds = (/报销|病假|假条/.test(q) && ups.length) ? ups : null;
      const withAtt = attachmentIds || autoIds;
      if (text) toast(`已交给${LABEL}：${q}`, "info");
      input.value = "";
      S.sending = true;
      S.messages.push({ role: "user", content: q, time: nowHM() });
      S.messages.push({ role: "bot", content: "思考中…", time: nowHM(), pending: true, steps: [] });
      paintChat();
      // 对话卡置顶、卡片在下方：发问后把对话区带回视野，避免"点了没反应"的错觉
      const chatCard = root.querySelector(".of-chat-card");
      if (chatCard && chatCard.scrollIntoView) {
        chatCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }

      const replaceLastBot = (msg) => {
        const i = S.messages.map((m) => m.role).lastIndexOf("bot");
        if (i >= 0) S.messages[i] = msg;
      };
      try {
        const data = await api.officeChat(q, {
          user_id: actor.uid || actor.name, name: actor.name,
          dept: actor.dept, roles: actor.roles,
        }, withAtt);
        replaceLastBot({
          role: "bot", time: nowHM(),
          content: data.answer || "（助手未返回内容）",
          steps: data.steps || [],
          used: data.data_used || [],
          elapsed: data.elapsed_ms || 0,
          hitl: data.hitl_task || null,
          gaps: data.data_gaps || [],
        });
        // 反问态：助手需关键信息 → 弹出表单（下拉/多选/输入）收集，补齐后携带 base 再提交
        if (data.clarify) {
          paintChat();
          const n = (clarifyRounds.get(clarifyBase) || 0) + 1;
          clarifyRounds.set(clarifyBase, n);
          if (n > 2) {
            // 兜底：同一请求反复要求补充 → 不再弹窗，提示用户一次说清（后端已断环）
            toast("信息已补充但仍不完整，请直接在对话中一次性说明需求", "err", 5000);
          } else {
            openClarify(data.clarify, ctx, clarifyBase, (t) => send(t, clarifyBase, withAtt));
          }
        }
        // 办公办理完成后刷新"我的预订与订单 / 我的申请单据"，让留痕与状态闭环可见（数据屏无此面板）
        if (isOa) await loadMine();
      } catch (err) {
        replaceLastBot({
          role: "bot", time: nowHM(),
          content: `⚠️ ${(err && err.message) || err}`, steps: [], hitl: null, gaps: [],
        });
        toast(`办理失败：${UI.friendlyError(err).text}`, "err", 5000);
      } finally {
        S.sending = false;
        paintChat();
      }
    };

    $("#of-send").addEventListener("click", () => send());
    $("#of-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    /**
     * 「问案例」：与报告中心深链同构（写预填键 + 切屏），到位后由案例屏自动提问。
     * 本屏只负责把这句话递过去——检索/回答口径全在案例域，不在速查屏重造一遍。
     */
    function askCase(question) {
      const q = String(question || "").trim();
      if (!q) return;
      try { sessionStorage.setItem("cases_ask_prefill", q); } catch (err) { /* 隐私模式忽略 */ }
      ctx.navigate("cases");
    }

    // 示例胶囊双动作：正文=问数（本屏对话）、尾部「问案例」=带同一句话去案例检索室提问
    $("#of-quick").addEventListener("click", (e) => {
      const ask = e.target.closest("[data-case-ask]");
      if (ask) { askCase(ask.dataset.caseAsk); return; }
      const q = e.target.closest("[data-quick]");
      if (q) send(q.dataset.quick);
    });

    // ---------- OA 协同：会议室 / 餐厅 / 我的预订与订单 ----------

    if (isOa) {
      paintRooms = () => {
        const box = $("#of-rooms");
        if (box) box.innerHTML = roomListHtml(S, ctx);
        const cnt = $("#of-room-count");
        if (cnt) {
          const total = (S.rooms || []).length;
          const shown = box ? box.querySelectorAll(".of-item").length : total;
          cnt.textContent = (S.tower || S.filter !== "all") && shown !== total
            ? `共 ${total} 间 · 符合 ${shown} 间` : `共 ${total} 间`;
        }
        // 「让助手预订」绑在重绘之后（与 paintMenu 同构）：筛选/重载都会换掉按钮节点，绑一次不够
        document.querySelectorAll("#of-rooms [data-ask]").forEach((btn) => {
          btn.addEventListener("click", () => send(
            `帮我订 ${S.form.date} ${S.form.start}-${S.form.end} 的「${btn.dataset.ask}」，主题项目讨论`));
        });
      };
      paintRooms();

      $("#of-room-filter").addEventListener("click", (e) => {
        const byCap = e.target.closest("[data-filter]");
        const byTower = e.target.closest("[data-tower]");
        if (byCap) {
          S.filter = byCap.dataset.filter;
          document.querySelectorAll("#of-room-filter [data-filter]").forEach((c) => {
            c.classList.toggle("active", c.dataset.filter === S.filter);
          });
        } else if (byTower) {
          S.tower = byTower.dataset.tower;
          document.querySelectorAll("#of-room-filter [data-tower]").forEach((c) => {
            c.classList.toggle("active", c.dataset.tower === S.tower);
          });
        } else {
          return;
        }
        paintRooms();
      });

      paintAvail = () => {
        const box = $("#of-avail-result");
        if (!box) return;
        if (!S.avail) { box.innerHTML = ""; return; }
        const rooms = S.avail.available_rooms || [];
        const head = `<div class="of-mine-label">${esc(S.avail.date)} ${esc(S.avail.start)}-${esc(S.avail.end)}`
          + ` 可用 ${rooms.length} 间</div>`;
        box.innerHTML = head + (rooms.length
          ? rooms.map((r) => roomLineHtml(r, esc)).join("")
          : '<div class="of-note">该时段没有可用会议室，换个时间或减少人数再试。</div>');
        box.querySelectorAll("[data-ask]").forEach((btn) => {
          btn.addEventListener("click", () => send(
            `帮我订 ${S.avail.date} ${S.avail.start}-${S.avail.end} 的「${btn.dataset.ask}」，主题项目讨论`));
        });
      };

      $("#of-avail").addEventListener("click", async () => {
        if (!online) { toast("后端离线：无法查询可用会议室", "err"); return; }
        S.form = {
          date: $("#of-date").value, start: $("#of-start").value,
          end: $("#of-end").value, cap: $("#of-cap").value,
        };
        try {
          S.avail = await api.officeAvailable(S.form.date, S.form.start, S.form.end,
            S.form.cap || null);
          paintAvail();
        } catch (err) {
          S.avail = null;
          paintAvail();
          toast(`查询失败：${UI.friendlyError(err).text}`, "err", 5000);
        }
      });

      paintMenu = () => {
        const box = $("#of-menu");
        if (box) box.innerHTML = menuHtml(S, ctx);
        box.querySelectorAll("[data-ask]").forEach((btn) => {
          btn.addEventListener("click", () => send(`中午订个餐，来两份${btn.dataset.ask}`));
        });
      };

      loadMenu = async (rid) => {
        if (!online) { toast("后端离线：无法加载菜单", "err"); return; }
        try {
          S.menuId = rid;
          S.menu = await api.officeMenu(rid);
          document.querySelectorAll("#of-rests .chip").forEach((c) => {
            c.classList.toggle("active", c.dataset.menu === rid);
          });
          paintMenu();
        } catch (err) {
          toast(`菜单加载失败：${UI.friendlyError(err).text}`, "err", 5000);
        }
      };
      $("#of-rests").addEventListener("click", (e) => {
        const chip = e.target.closest("[data-menu]");
        if (chip) loadMenu(chip.dataset.menu);
      });

      const bindCancels = () => {
        const box = $("#of-mine");
        if (!box) return;
        box.querySelectorAll("[data-cancel]").forEach((btn) => {
          btn.addEventListener("click", async () => {
            const kind = btn.dataset.cancel;
            try {
              await api.officeCancel(kind, btn.dataset.id, actor.name);
              toast(`已取消 ${btn.dataset.id}`, "ok");
              await loadMine();
            } catch (err) {
              toast(`取消失败：${UI.friendlyError(err).text}`, "err", 5000);
            }
          });
        });
      };

      /** 单据里的附件与补件按钮绑定（面板可能被搬进应用窗口，故默认按 document 作用域查，
       *  可传 scope 限定范围） */
      const bindFormAtts = (scope) => {
        const box = scope || document;
        box.querySelectorAll("[data-att]").forEach((el) => {
          el.addEventListener("click", () =>
            window.open(api.officeAttachmentUrl(el.dataset.att), "_blank"));
        });
        box.querySelectorAll("[data-supplement]").forEach((el) => {
          el.addEventListener("click", () => openSupplement(
            el.dataset.supplement, ctx, api, actor.name, async () => { await loadMine(); }));
        });
      };

      loadMine = async () => {
        if (!online) return;
        try {
          S.mine = await api.officeMine(actor.name);
          const box = $("#of-mine");
          if (box) box.innerHTML = mineHtml(S, esc);
          const fb = $("#of-myforms");
          if (fb) fb.innerHTML = myFormsHtml(S, esc);
          bindCancels();
          bindFormAtts();
        } catch (err) { /* 静默：列表刷新失败不影响主流程 */ }
      };
      bindCancels();
      bindFormAtts();

      $("#of-refresh").addEventListener("click", async () => {
        await loadMine();
        toast("已刷新", "info");
      });

      // OA 自助办理三入口：点击即用种子问句发起，缺失项由助手弹窗反问补齐
      document.querySelectorAll("[data-entry]").forEach((btn) => {
        btn.addEventListener("click", () => send(ENTRY_SEED[btn.dataset.entry]));
      });

      // ---- 报销材料上传：类别芯片 / 选文件上传 / 清单预览与移除 / 科目整理 / 交给助手 ----
      const upBox = $("#of-uploads");
      const bindUploadList = () => {
        if (!upBox) return;
        upBox.querySelectorAll("[data-att-view]").forEach((el) => {
          el.addEventListener("click", () =>
            window.open(api.officeAttachmentUrl(el.dataset.attView), "_blank"));
        });
        upBox.querySelectorAll("[data-att-del]").forEach((el) => {
          el.addEventListener("click", () => {
            S.uploads = (S.uploads || []).filter(
              (u) => u.attachment_id !== el.dataset.attDel);
            paintUploads();
          });
        });
      };
      const paintUploads = () => {
        if (upBox) upBox.innerHTML = uploadsHtml(S, esc, EXP_UPLOAD_KINDS);
        bindUploadList();
      };
      bindUploadList();

      const kindBox = $("#of-up-kinds");
      if (kindBox) {
        kindBox.addEventListener("click", (e) => {
          const chip = e.target.closest("[data-upkind]");
          if (!chip) return;
          S.upKind = chip.dataset.upkind;
          kindBox.querySelectorAll("[data-upkind]").forEach((c) => {
            c.classList.toggle("active", c.dataset.upkind === S.upKind);
          });
        });
      }

      const fileInput = $("#of-up-file");
      if (fileInput) {
        fileInput.addEventListener("change", async (e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = "";
          if (!files.length) return;
          if (!online) { toast("后端离线：无法登记材料", "err"); return; }
          let ok = 0;
          for (const f of files) {
            try {
              const meta = await api.officeUpload(f, S.upKind, actor.name);
              S.uploads.push(meta);
              ok += 1;
            } catch (err) {
              toast(`登记失败（${f.name}）：${UI.friendlyError(err).text}`, "err", 5000);
            }
          }
          paintUploads();
          if (ok) toast(`已登记 ${ok} 份材料（类别：${S.upKind}，仅记文件名/大小）`, "ok");
        });
      }

      const expOrganizeBtn = $("#of-exp-organize");
      if (expOrganizeBtn) {
        expOrganizeBtn.addEventListener("click", async () => {
          const cat = $("#of-exp-cat").value;
          const amountRaw = ($("#of-exp-amount").value || "").trim();
          const note = ($("#of-exp-note").value || "").trim();
          S.expCat = cat; S.expAmount = amountRaw; S.expNote = note;
          S.expDate = $("#of-exp-date").value;
          if (!online) { toast("后端离线：无法整理科目", "err"); return; }
          if (!(parseFloat(amountRaw) > 0)) { toast("请先填写报销金额（正数）", "err"); return; }
          try {
            S.org = await api.officeExpenseOrganize({
              category: cat, amount: parseFloat(amountRaw), note,
              attachment_kinds: (S.uploads || []).map((u) => u.kind),
            });
          } catch (err) {
            S.org = { ok: false, message: UI.friendlyError(err).text };
          }
          const ob = $("#of-org");
          if (ob) ob.innerHTML = orgPanelHtml(S, esc);
        });
      }

      const expSubmitBtn = $("#of-exp-submit");
      if (expSubmitBtn) {
        expSubmitBtn.addEventListener("click", () => {
          const cat = $("#of-exp-cat").value;
          const amount = ($("#of-exp-amount").value || "").trim();
          const note = ($("#of-exp-note").value || "").trim();
          const date = $("#of-exp-date").value;
          S.expCat = cat; S.expAmount = amount; S.expNote = note; S.expDate = date;
          const ids = (S.uploads || []).map((u) => u.attachment_id);
          let q = `我要报销${cat}费用`;
          if (amount) q += `，金额 ${amount} 元`;
          if (note) q += `，事由：${note}`;
          if (date) q += `，发生日期 ${date}`;
          send(q, undefined, ids.length ? ids : null);
        });
      }

      const myformBtn = $("#of-myform-refresh");
      if (myformBtn) {
        myformBtn.addEventListener("click", async () => {
          await loadMine();
          toast("已刷新申请单据", "info");
        });
      }

      // 办公桌面（一级）：点文件夹瓷片 → 文件夹窗口（二级）→ 应用窗口（三级）
      const desk = $("#of-desk");
      if (desk) {
        desk.addEventListener("click", (e) => {
          const tile = e.target.closest("[data-folder]");
          if (tile) openFolderWin(tile.dataset.folder, ctx);
        });
      }

      // 服务工单面板（IT / 停车 / 车辆）：提交 + 可选材料登记。
      // 作用域限面板自身（面板会被搬进应用窗口，作用域内查找与绑定都不受影响）
      document.querySelectorAll(".of-panels .of-panel[data-app]").forEach((panel) => {
        const kind = panel.dataset.app;
        if (!isSvcApp(kind)) return;
        const schema = SERVICE_SCHEMA[kind];
        const submitBtn = panel.querySelector("[data-svc-submit]");
        if (submitBtn) submitBtn.addEventListener("click", () => submitServicePanel(panel, ctx, S, send));
        const up = panel.querySelector(".of-upload-row");
        const listBox = panel.querySelector("[data-up-list]");
        if (!up || !listBox) return;
        const kinds = schema.att || [];
        const bindItems = () => {
          listBox.querySelectorAll("[data-att-view]").forEach((el) => el.addEventListener("click",
            () => window.open(api.officeAttachmentUrl(el.dataset.attView), "_blank")));
          listBox.querySelectorAll("[data-att-del]").forEach((el) => el.addEventListener("click", () => {
            S.uploads = (S.uploads || []).filter((u) => u.attachment_id !== el.dataset.attDel);
            paint();
          }));
        };
        const paint = () => { listBox.innerHTML = uploadsHtml(S, esc, kinds); bindItems(); };
        bindItems();
        up.querySelector("[data-up-kinds]").addEventListener("click", (e) => {
          const chip = e.target.closest("[data-upkind]");
          if (!chip) return;
          S.upKind = chip.dataset.upkind;
          up.querySelectorAll("[data-upkind]").forEach((c) =>
            c.classList.toggle("active", c.dataset.upkind === S.upKind));
        });
        up.querySelector("[data-up-file]").addEventListener("change", async (e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = "";
          if (!files.length) return;
          if (!online) { toast("后端离线：无法登记材料", "err"); return; }
          const upKind = kinds.indexOf(S.upKind) >= 0 ? S.upKind : kinds[0];
          let ok = 0;
          for (const f of files) {
            try {
              S.uploads.push(await api.officeUpload(f, upKind, actor.name));
              ok += 1;
            } catch (err) {
              toast(`登记失败（${f.name}）：${UI.friendlyError(err).text}`, "err", 5000);
            }
          }
          paint();
          if (ok) toast(`已登记 ${ok} 份材料（类别：${upKind}，仅记文件名/大小）`, "ok");
        });
      });
    }

    // ---------- 数据速查：制造数据只读快照 ----------

    if (!isOa) {
      // ---- 数据查询权限申请 · 审批弹窗（与案例检索同构）----
      // 无权限块点「申请查询权限」→ 受理 → 解析职责岗 → 推送审批 → 审批通过 → 写入授权。
      // 授权按岗位角色写入服务端运行时叠加层（三个权限探测点统一生效）。
      const dataFlowSteps = (flow) => [
        "已受理查询权限申请（" + flow.title + "）",
        "解析申请理由与数据归属…",
        "推断职责岗：" + flow.ownerLabel + "（依据：数据表归属职责）",
        "已推送至 " + flow.ownerLabel + " 审批通道，职责岗审批中…",
        flow.ownerLabel + "审批通过，正在写入授权…",
      ];

      const paintDataFlow = (mask, flow) => {
        const steps = dataFlowSteps(flow);
        const rows = steps.map((label, i) => {
          let st = "wait";
          if (flow.status === "failed" && i === 4) st = "fail";
          else if (flow.status === "approved" || i < flow.step) st = "done";
          else if (i === flow.step && flow.status === "running") st = "run";
          const ico = st === "done" ? "✓" : st === "fail" ? "✕" : st === "run" ? "•" : "";
          return `<div class="case-auto-step is-${st}"><span class="case-auto-ico">${ico}</span>`
            + `<span>${esc(label)}</span></div>`;
        }).join("");
        let result = "";
        if (flow.status === "approved") {
          const exp = String(flow.expires || "").slice(0, 10);
          result = `<div class="case-auto-result is-ok"><b>${esc(flow.ownerLabel)}已审批通过</b>`
            + `<div>授权已生效（按岗位角色生效，带有效期${exp ? "，有效期至 " + esc(exp) + "，到期自动回收" : ""}）。</div>`
            + `<div>关闭后刷新速查卡即可看到该数据——岗位没变，变的是表的可见范围。</div></div>`;
        } else if (flow.status === "manual") {
          result = `<div class="case-auto-result is-err"><b>申请已提交</b>`
            + `<div>${esc(flow.error || "请等待职责岗审批后授权。")}</div></div>`;
        } else if (flow.status === "failed") {
          result = `<div class="case-auto-result is-err"><b>审批未能完成</b>`
            + `<div>${esc(flow.error || "未知原因")}</div>`
            + `<div>请稍后重试，或联系职责岗处理。</div></div>`;
        }
        const bar = flow.status === "approved"
          ? '<button type="button" class="btn btn-primary btn-sm" data-done="1">完成</button>'
          : ((flow.status === "manual" || flow.status === "failed")
            ? '<button type="button" class="btn btn-outline btn-sm" data-close="1">知道了</button>'
            : '<span class="case-hint">执行中，请稍候…</span>');
        mask.innerHTML = `<div class="of-clarify" role="dialog" aria-modal="true">
          <div class="of-clarify-head"><div class="of-clarify-title">数据查询权限审批</div>
            <button type="button" class="of-clarify-close" data-close="1" aria-label="关闭">×</button></div>
          <div class="case-auto-steps">${rows}</div>
          ${result}
          <div class="of-clarify-foot">${bar}</div>
          <div class="case-auto-note">审批通过后授权即时生效（按岗位角色生效，带有效期，到期自动回收）。</div>
        </div>`;
      };

      const openDataApply = (key) => {
        const title = SNAP_TITLE[key] || key;
        const blk = (S.snap && S.snap.blocks && S.snap.blocks[key]) || {};
        const ownerLabel = blk.owner_label || "职责岗";
        const mask = document.createElement("div");
        mask.className = "of-clarify-mask";
        let flow = null;
        const close = () => {
          if (flow && flow.timers) flow.timers.forEach(clearTimeout);
          if (mask.parentNode) mask.parentNode.removeChild(mask);
        };

        const fillForm = () => {
          mask.innerHTML = `<div class="of-clarify" role="dialog" aria-modal="true">
            <div class="of-clarify-head">
              <div class="of-clarify-title">申请查询权限 · ${esc(title)}</div>
              <button type="button" class="of-clarify-close" data-close="1" aria-label="关闭">×</button></div>
            <div class="of-clarify-q">当前岗位无权限查询该数据。请说明申请用途，提交后推送至
              <b>${esc(ownerLabel)}</b> 审批，通过后授权即时生效。</div>
            <div class="of-clarify-body">
              <div class="of-clarify-field" data-key="reason" data-type="text" data-required="1" data-label="申请理由">
                <div class="of-clarify-label">申请理由 <span class="of-req">*</span></div>
                <input class="of-clarify-input" id="of-apply-reason" type="text"
                  placeholder="请说明查询用途，例如：核对生产齐套与交付风险" /></div>
            </div>
            <div class="of-clarify-foot">
              <button type="button" class="btn btn-outline btn-sm" data-close="1">取消</button>
              <button type="button" class="btn btn-primary btn-sm" data-submit="1">提交申请并推送</button></div>
          </div>`;
        };

        const start = async (reason) => {
          flow = { dataset: key, title, ownerLabel, requestId: "",
                   step: 0, status: "running", error: "", expires: "", timers: [] };
          paintDataFlow(mask, flow);
          let data;
          try {
            data = await api.officeDataAccessRequest({
              dataset: key, reason, roles: actor.roles, dept: actor.dept,
              actor: actor.uid || actor.name });
          } catch (err) {
            flow.status = "failed";
            flow.error = UI.friendlyError(err).text;
            paintDataFlow(mask, flow);
            return;
          }
          const demo = data.demo_approval || {};
          const request = data.request || {};
          if (!demo.available) {
            flow.status = "manual";
            flow.error = demo.reason || "请等待职责岗审批后授权。";
            paintDataFlow(mask, flow);
            return;
          }
          flow.ownerLabel = demo.owner_label || flow.ownerLabel;
          flow.requestId = request.request_id || "";
          const tick = (n) => {
            if (flow.status === "running") { flow.step = n; paintDataFlow(mask, flow); }
          };
          flow.timers.push(setTimeout(() => tick(1), 420));
          flow.timers.push(setTimeout(() => tick(2), 920));
          flow.timers.push(setTimeout(() => tick(3), 1480));
          flow.timers.push(setTimeout(async () => {
            if (flow.status !== "running") return;
            flow.step = 4; paintDataFlow(mask, flow);
            try {
              const out = await api.officeDataAccessApprove(
                flow.requestId, actor.uid || actor.name);
              flow.status = "approved";
              flow.step = 5;
              flow.expires = ((out && out.request) || {}).expires_at || "";
              paintDataFlow(mask, flow);
            } catch (err) {
              flow.status = "failed";
              flow.error = UI.friendlyError(err).text;
              paintDataFlow(mask, flow);
            }
          }, 1980));
        };

        fillForm();
        document.body.appendChild(mask);
        mask.addEventListener("click", (e) => {
          if (e.target.closest("[data-done]")) {
            const ok = !!flow && flow.status === "approved";
            close();
            if (ok) {
              loadSnap().then(
                () => toast("授权已生效：该数据已进入你的查询范围", "ok", 3600));
            }
            return;
          }
          if (e.target === mask || e.target.closest("[data-close]")) { close(); return; }
          if (!e.target.closest("[data-submit]")) return;
          const inp = mask.querySelector("#of-apply-reason");
          const reason = ((inp && inp.value) || "").trim();
          if (reason.length < 6) { toast("请说明申请理由（至少 6 个字）", "err"); return; }
          start(reason);
        });
      };

      const bindSnap = () => {
        document.querySelectorAll("#of-snap [data-q]").forEach((el) => {
          el.addEventListener("click", () => send(el.dataset.q));
        });
        document.querySelectorAll("#of-snap [data-apply-table]").forEach((el) => {
          el.addEventListener("click", () => openDataApply(el.dataset.applyTable));
        });
      };
      const paintSnap = () => {
        const box = $("#of-snap");
        if (box) box.innerHTML = snapHtml(S, esc);
        bindSnap();
      };

      // 关键词速查：料号/型号/批号/名称 → 过滤四块明细；命中后点行即交给助手问答
      const snapSearch = $("#of-snap-search");
      const snapClear = $("#of-snap-clear");
      const syncSnapClear = () => {
        if (snapClear) snapClear.hidden = !(S.snapQuery || "").trim();
      };
      if (snapSearch) {
        snapSearch.addEventListener("input", () => {
          S.snapQuery = snapSearch.value;
          syncSnapClear();
          paintSnap();
        });
      }
      if (snapClear) {
        snapClear.addEventListener("click", () => {
          S.snapQuery = "";
          if (snapSearch) { snapSearch.value = ""; snapSearch.focus(); }
          syncSnapClear();
          paintSnap();
        });
      }
      syncSnapClear();
      bindSnap();

      loadSnap = async () => {
        if (!online) {
          S.snap = (window.DEMO && window.DEMO.OFFICE_SNAPSHOT) || null;
          paintSnap();
          return;
        }
        try {
          const data = await api.officeSnapshot(actor.roles);
          S.snap = data.blocks ? data : (window.DEMO && window.DEMO.OFFICE_SNAPSHOT);
        } catch (err) {
          S.snap = (window.DEMO && window.DEMO.OFFICE_SNAPSHOT) || null;
        }
        paintSnap();
      };

      const snapBtn = $("#of-snap-refresh");
      if (snapBtn) {
        snapBtn.addEventListener("click", async () => {
          await loadSnap();
          toast("已刷新制造数据速查", "info");
        });
      }
    }

    // ---------- 首屏数据 ----------

    if (online) {
      const jobs = [
        api.officeMeta().then((meta) => {
          metaCache = meta;
          const be = $("#of-backend");
          const llm = $("#of-llm");
          if (be) {
            be.textContent = meta.backend === "demo"
              ? "后端：演示数据（内存）" : `后端：真实（${meta.backend_impl}）`;
            be.title = meta.switch_hint || "";
          }
          if (llm) {
            llm.textContent = meta.llm_available ? "LLM：可用" : "LLM：不可用（对话办理暂停）";
            llm.classList.toggle("badge-green", !!meta.llm_available);
            llm.classList.toggle("badge-red", !meta.llm_available);
          }
        }),
      ];
      if (isOa) {
        jobs.push(
          api.officeRooms().then((data) => {
            S.rooms = data.rooms || [];
            paintRooms();   // 重绘内部已含「让助手预订」绑定，这里不再重复绑
          }),
          api.officeRestaurants().then((data) => {
            S.restaurants = data.restaurants || [];
            const box = $("#of-rests");
            if (box) {
              box.innerHTML = S.restaurants.map((r) => `<button class="chip${S.menuId === r.restaurant_id ? " active" : ""}"
                data-menu="${esc(r.restaurant_id)}">${esc(r.name)}${r.need_approval ? "（客餐）" : ""}</button>`).join("");
              const cnt = $("#of-rest-count");
              if (cnt) cnt.textContent = `${S.restaurants.length} 个餐厅可选`;
              if (S.restaurants.length) loadMenu(S.restaurants[0].restaurant_id);
            }
          }),
          loadMine(),
          api.officeAvailable(S.form.date, S.form.start, S.form.end, S.form.cap)
            .then((res) => { S.avail = res; paintAvail(); })
            .catch(() => { /* 首屏可用性查询失败不打扰 */ })
        );
      } else {
        jobs.push(loadSnap());
      }
      Promise.allSettled(jobs);
    } else {
      const be = $("#of-backend");
      if (be) { be.textContent = "后端：离线（演示模式）"; be.classList.add("badge-amber"); }
      const llm = $("#of-llm");
      if (llm) llm.textContent = "LLM：未连接";
      if (!isOa) {
        loadSnap();
        toast("后端离线：数据速查显示演示数据", "info", 4000);
      } else {
        toast("后端离线：办公办理需连后端", "info", 4000);
      }
    }
  }

  window.Screens = window.Screens || {};
  window.Screens.render_data = render;
  window.Screens.render_oa = render;
})();
