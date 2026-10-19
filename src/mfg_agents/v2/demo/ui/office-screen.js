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
  const OA_QUICK = [
    "帮我订 2026-10-20 14:00-15:00 一个 8 人的会议室，主题项目评审",
    "中午订个餐，来两份红烧狮子头套餐",
    "查一下我的会议室预订和订餐订单",
    "我要报销一笔费用",
    "我要请假",
    "我要补考勤打卡",
  ];

  // OA 自助办理入口 → 种子问句（缺失项由助手 ask_user 弹窗反问补齐）
  const ENTRY_SEED = {
    expense: "我要报销一笔费用",
    leave: "我要请假",
    attendance: "我要补考勤打卡",
  };

  // OA 申请单据状态（与后端 status 口径一致）
  const FORM_STATUS = {
    pending_approval: { t: "待审批", c: "badge-amber" },
    approved: { t: "已通过", c: "badge-green" },
    rejected: { t: "已驳回", c: "badge-red" },
    cancelled: { t: "已撤销", c: "badge-gray" },
  };
  function formStatusText(s) { return (FORM_STATUS[s] || { t: s || "-" }).t; }
  function formStatusCls(s) { return (FORM_STATUS[s] || { c: "badge-gray" }).c; }

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
      avail: null,              // 可用性查询结果
      snap: null,               // 制造数据速查（office/snapshot）
      snapQuery: "",            // 速查关键词（料号/型号/批号/名称；命中后点行即交给助手问答）
      form: { date: dateStr(1), start: "14:00", end: "15:00", cap: "8" },
      menuId: "",
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
    const bal = mine.leave_balance || null;
    if (!exps.length && !leaves.length && !fixes.length && !bal) {
      return '<div class="of-note">暂无 OA 申请单据。可用上方「OA 自助办理」或对话发起报销/请假/补卡。</div>';
    }
    const balHtml = bal
      ? `<div class="of-bal">年假余额 <b>${esc(bal.annual_remaining)}</b> 天 · 调休余额 `
        + `<b>${esc(bal.comp_remaining)}</b> 天 · 本年已休 <b>${esc(bal.used_this_year)}</b> 天</div>`
      : "";
    const ex = exps.map((e) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(e.category)}报销 <span class="of-code">${esc(e.expense_id)}</span></div>`
      + `<div class="of-item-sub">¥${esc(e.amount)} · ${esc(e.expense_date)} · ${esc(e.note || "-")}`
      + `${e.high_value ? ' <span class="badge badge-amber">大额</span>' : ""}`
      + ` <span class="badge ${formStatusCls(e.status)}">${esc(formStatusText(e.status))}</span></div></div></div>`).join("");
    const lv = leaves.map((l) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(l.leave_type)} <span class="of-code">${esc(l.leave_id)}</span></div>`
      + `<div class="of-item-sub">${esc(l.start_date)} ~ ${esc(l.end_date)} · ${esc(l.days)} 天`
      + `${l.reason ? " · " + esc(l.reason) : ""}`
      + `${l.long_leave ? ' <span class="badge badge-amber">长假</span>' : ""}`
      + ` <span class="badge ${formStatusCls(l.status)}">${esc(formStatusText(l.status))}</span></div></div></div>`).join("");
    const kb = fixes.map((a) => `<div class="of-item"><div class="of-item-main">`
      + `<div class="of-item-title">${esc(a.fix_type)} <span class="of-code">${esc(a.fix_id)}</span></div>`
      + `<div class="of-item-sub">${esc(a.work_date)} ${esc(a.time)}`
      + `${a.reason ? " · " + esc(a.reason) : ""}`
      + ` <span class="badge ${formStatusCls(a.status)}">${esc(formStatusText(a.status))}</span></div></div></div>`).join("");
    return balHtml
      + `<div class="of-mine-label">费用报销（${exps.length}）</div>${ex || '<div class="of-note">无</div>'}`
      + `<div class="of-mine-label">请假申请（${leaves.length}）</div>${lv || '<div class="of-note">无</div>'}`
      + `<div class="of-mine-label">补考勤打卡（${fixes.length}）</div>${kb || '<div class="of-note">无</div>'}`;
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
    const QUICK = isOa ? OA_QUICK : DATA_QUICK;

    const desc = isOa
      ? `办公事务办理入口：<b>会议室日程与预订</b>、<b>餐厅与菜单</b>（园区食堂 / 商务餐厅）、`
        + `<b>我的预订与订单</b>；以及 <b>OA 自助办理</b>——<b>费用报销</b> / <b>请假申请</b> / `
        + `<b>补考勤打卡</b>，对话式提单、关键信息缺失时弹窗反问补齐。报告厅 / 培训室 / 商务客餐 / `
        + `超 500 元订单，以及报销 / 请假 / 补卡均先发起人工确认；办理动作由后端 Agent 执行并全程留痕可审计。`
      : `制造数据只读速查：<b>材料库存</b>、<b>成品库存与可发量</b>、<b>来料批次与检验结论</b>、`
        + `<b>库龄呆滞</b>、<b>缺料缺口</b>、<b>批次追溯</b>。数据走平台统一口径注册查询，`
        + `受岗位数据权限约束；<b>只读</b>，不办理任何写操作。`;

    root.innerHTML = `
      <div class="content-inner content-wide">
        <div class="page-title">${LABEL}
          <span class="badge badge-outline" id="of-backend">后端检测中…</span>
          <span class="badge badge-outline" id="of-llm">LLM 检测中…</span>
        </div>
        <div class="page-desc of-desc">${desc}</div>

        <div class="of-grid">
          <!-- 一、对话：置顶（问答即入口） -->
          <section class="of-a of-a-chat">
            <div class="card of-chat-card">
              <div class="card-head of-chat-head"><div class="card-title">${LABEL}对话</div>
                <div class="card-extra"><span class="badge badge-purple">Agentic Agent</span>
                  <span class="badge badge-gray">${isOa ? "办公办理 · 写操作留痕" : "数据速查 · 只读"}</span></div></div>
              <div class="of-chat"><div class="chat-scroll" id="of-chat"></div></div>
              <div class="of-quick" id="of-quick">
                ${QUICK.map((q) => `<button class="chip chip-hist" data-quick="${esc(q)}">${esc(q.length > 26 ? q.slice(0, 26) + "…" : q)}</button>`).join("")}
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
          <!-- 三、OA 自助办理：报销 / 请假 / 补考勤（对话式提单，缺失项弹窗反问） -->
          <section class="of-a of-a-entry">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">OA 自助办理</div>
                <div class="card-extra"><span class="badge badge-purple">对话式提单</span>
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
            </div>
          </section>

          <!-- 四、办公事务：会议室目录 -->
          <section class="of-a of-a-rooms">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">会议室目录</div>
                <div class="card-extra" id="of-room-count"></div></div>
              <div class="chip-row" id="of-room-filter">
                <button class="chip${S.filter === "all" ? " active" : ""}" data-filter="all">全部</button>
                <button class="chip${S.filter === "cap8" ? " active" : ""}" data-filter="cap8">≥8 人</button>
                <button class="chip${S.filter === "cap20" ? " active" : ""}" data-filter="cap20">≥20 人</button>
                <button class="chip${S.filter === "approval" ? " active" : ""}" data-filter="approval">需授权</button>
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

          <!-- 五、餐厅与菜单 -->
          <section class="of-a of-a-rests">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">餐厅与菜单</div>
                <div class="card-extra"><span class="badge badge-gray">园区食堂 · 商务餐厅</span></div></div>
              <div class="chip-row" id="of-rests">
                ${(S.restaurants || []).map((r) => `<button class="chip${S.menuId === r.restaurant_id ? " active" : ""}"
                  data-menu="${esc(r.restaurant_id)}">${esc(r.name)}${r.need_approval ? "（客餐）" : ""}</button>`).join("")
                || '<span class="of-note">加载中…</span>'}
              </div>
              <div id="of-menu">${menuHtml(S, ctx)}</div>
            </div>
          </section>

          <!-- 六、我的预订与订单 -->
          <section class="of-a of-a-mine">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">我的预订与订单</div>
                <div class="card-extra"><span class="badge badge-gray">操作人 ${esc(actor.name)}</span>
                  <button class="btn btn-outline btn-sm" id="of-refresh">刷新</button></div></div>
              <div id="of-mine">${mineHtml(S, esc)}</div>
            </div>
          </section>

          <!-- 七、我的申请单据：报销 / 请假 / 补考勤（提交后状态闭环可见） -->
          <section class="of-a of-a-myforms">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">我的申请单据</div>
                <div class="card-extra"><span class="badge badge-gray">报销 · 请假 · 补卡</span>
                  <button class="btn btn-outline btn-sm" id="of-myform-refresh">刷新</button></div></div>
              <div id="of-myforms">${myFormsHtml(S, esc)}</div>
            </div>
          </section>` : ""}
        </div>
      </div>`;

    const $ = (sel) => root.querySelector(sel);
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

    const send = async (text, base) => {
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
        });
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
            openClarify(data.clarify, ctx, clarifyBase, (t) => send(t, clarifyBase));
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
    $("#of-quick").addEventListener("click", (e) => {
      const q = e.target.closest("[data-quick]");
      if (q) send(q.dataset.quick);
    });

    // ---------- OA 协同：会议室 / 餐厅 / 我的预订与订单 ----------

    if (isOa) {
      paintRooms = () => {
        const box = $("#of-rooms");
        if (box) box.innerHTML = roomListHtml(S, ctx);
        const cnt = $("#of-room-count");
        if (cnt) cnt.textContent = `共 ${(S.rooms || []).length} 间`;
      };
      paintRooms();

      $("#of-room-filter").addEventListener("click", (e) => {
        const chip = e.target.closest("[data-filter]");
        if (!chip) return;
        S.filter = chip.dataset.filter;
        root.querySelectorAll("#of-room-filter .chip").forEach((c) => {
          c.classList.toggle("active", c.dataset.filter === S.filter);
        });
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

      root.querySelectorAll("#of-rooms [data-ask]").forEach((btn) => {
        btn.addEventListener("click", () => send(
          `帮我订 ${S.form.date} ${S.form.start}-${S.form.end} 的「${btn.dataset.ask}」，主题项目讨论`));
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
          root.querySelectorAll("#of-rests .chip").forEach((c) => {
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

      loadMine = async () => {
        if (!online) return;
        try {
          S.mine = await api.officeMine(actor.name);
          const box = $("#of-mine");
          if (box) box.innerHTML = mineHtml(S, esc);
          const fb = $("#of-myforms");
          if (fb) fb.innerHTML = myFormsHtml(S, esc);
          bindCancels();
        } catch (err) { /* 静默：列表刷新失败不影响主流程 */ }
      };
      bindCancels();

      $("#of-refresh").addEventListener("click", async () => {
        await loadMine();
        toast("已刷新", "info");
      });

      // OA 自助办理三入口：点击即用种子问句发起，缺失项由助手弹窗反问补齐
      root.querySelectorAll("[data-entry]").forEach((btn) => {
        btn.addEventListener("click", () => send(ENTRY_SEED[btn.dataset.entry]));
      });
      const myformBtn = $("#of-myform-refresh");
      if (myformBtn) {
        myformBtn.addEventListener("click", async () => {
          await loadMine();
          toast("已刷新申请单据", "info");
        });
      }
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
        root.querySelectorAll("#of-snap [data-q]").forEach((el) => {
          el.addEventListener("click", () => send(el.dataset.q));
        });
        root.querySelectorAll("#of-snap [data-apply-table]").forEach((el) => {
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
            paintRooms();
            root.querySelectorAll("#of-rooms [data-ask]").forEach((btn) => {
              btn.addEventListener("click", () => send(
                `帮我订 ${S.form.date} ${S.form.start}-${S.form.end} 的「${btn.dataset.ask}」，主题项目讨论`));
            });
          }),
          api.officeRestaurants().then((data) => {
            S.restaurants = data.restaurants || [];
            const box = $("#of-rests");
            if (box) {
              box.innerHTML = S.restaurants.map((r) => `<button class="chip${S.menuId === r.restaurant_id ? " active" : ""}"
                data-menu="${esc(r.restaurant_id)}">${esc(r.name)}${r.need_approval ? "（客餐）" : ""}</button>`).join("");
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
