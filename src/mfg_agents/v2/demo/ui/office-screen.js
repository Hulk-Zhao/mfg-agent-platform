/**
 * 企业综合助手 · demo 展示页（办公事务办理 + 制造数据只读速查）
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
 * 注册方式：`window.Screens.render_office`（app.js 按 `render_<screenId>` 查找）。
 * 样式：app.css 内 `.of-` 命名空间（沿用平台浅色主题变量）。
 *
 * 边界（不夸大）：办理动作一律由后端 Agent/后端服务执行，本页只做展示与触发；
 * 制造数据为**只读**（口径=注册查询，可用量=在库-预留），越权块如实显示"无权限"；
 * LLM 不可用时禁用对话并如实说明，速查卡与"我的预订"仍可正常使用。
 */
(function () {
  "use strict";

  const SEND_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>';

  // 快捷示例（办公办理 + 制造数据速查，直接喂给企业助手 Agent，展示工具调用链路）
  const QUICK = [
    "M8 螺栓还有多少库存",
    "A 型号成品现在能发多少",
    "最近有没有不合格的来料批次",
    "哪些料库龄超过 90 天了",
    "哪些料低于安全库存了",
    "帮我订 2026-10-20 14:00-15:00 一个 8 人的会议室，主题项目评审",
    "中午订个餐，来两份红烧狮子头套餐",
    "查一下我的会议室预订和订餐订单",
  ];

  // IQC 结论徽章（与后端 inspect_status 口径一致）
  const SNAP_STATUS = {
    pass: { t: "合格", c: "badge-green" },
    fail: { t: "不合格", c: "badge-red" },
    pending: { t: "待检", c: "badge-gray" },
    deviation: { t: "让步接收", c: "badge-amber" },
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

  function initialState() {
    return {
      messages: [{
        role: "bot", time: nowHM(), steps: [], hitl: null, gaps: [],
        content: "您好！我是办公与数据速查助手，既能办办公事务（预订会议室、订餐、查/取消本人预订与订单），"
          + "也能查制造数据（材料库存、成品库存与可发量、来料批次与检验结论、库龄呆滞、缺料缺口、批次追溯，均为只读）。"
          + "高影响事项（报告厅/培训室、商务客餐、超 500 元订单）我会先发起人工确认，不会直接放行。",
      }],
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
      return '<div class="of-note">暂无预订与订单。可以先用左侧快捷示例让助手办理一单。</div>';
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
      const msg = b && b.authorized === false ? (b.message || "当前岗位无权限查询")
        : `暂不可用（${(b && b.message) || "后端未返回"}）`;
      return `<div class="of-note">${esc(msg)}</div>`;
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

  // ---------- 主渲染 ----------

  function render(root, ctx) {
    const { UI, api, state } = ctx;
    const esc = UI.esc;
    state.screenState = state.screenState || {};
    const S = state.screenState.office || (state.screenState.office = initialState());
    const actor = actorOf(ctx);
    const online = api.online();

    root.innerHTML = `
      <div class="content-inner content-wide">
        <div class="page-title">办公与数据速查
          <span class="badge badge-outline" id="of-backend">后端检测中…</span>
          <span class="badge badge-outline" id="of-llm">LLM 检测中…</span>
        </div>
        <div class="page-desc of-desc">一个 Agentic Agent 同时管两件事：<b>办公事务</b>（会议室预订/订餐/我的预订与订单，
          报告厅·培训室·商务客餐·超 500 元订单一律先发起人工确认）与<b>制造数据速查</b>（材料库存、成品库存与可发量、
          来料批次与检验结论、库龄呆滞、缺料缺口、批次追溯）。数据查询为<b>只读</b>，走平台统一口径注册查询，
          受岗位数据权限约束；全程工具调用留痕可审计。</div>

        <div class="of-grid">
          <!-- 一、办公与数据速查对话：置顶（问答即入口） -->
          <section class="of-a of-a-chat">
            <div class="card of-chat-card">
              <div class="card-head of-chat-head"><div class="card-title">办公与数据速查对话</div>
                <div class="card-extra"><span class="badge badge-purple">Agentic Agent</span>
                  <span class="badge badge-gray">办公 + 数据速查（只读）</span></div></div>
              <div class="of-chat"><div class="chat-scroll" id="of-chat"></div></div>
              <div class="of-quick" id="of-quick">
                ${QUICK.map((q) => `<button class="chip chip-hist" data-quick="${esc(q)}">${esc(q.length > 26 ? q.slice(0, 26) + "…" : q)}</button>`).join("")}
              </div>
              <div class="chat-inputbar of-inputbar">
                <div class="chat-input-row">
                  <input id="of-input" class="chat-input" autocomplete="off"
                    placeholder="例如：M8 螺栓还有多少库存 / A 型号成品能发多少 / 订明天 14 点的 8 人会议室"/>
                  <button class="chat-send" id="of-send">${SEND_ICON}</button>
                </div>
                <div class="chat-hint">工具调用由后端 Agent 自主决策并全程留痕；写操作与高影响事项先人工确认；数据查询只读、受岗位权限约束</div>
              </div>
            </div>
          </section>

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
          </section>

          <!-- 三、办公事务：会议室目录 -->
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
              <div id="of-avail-result"></div>
              <div id="of-rooms">${roomListHtml(S, ctx)}</div>
            </div>
          </section>

          <!-- 四、餐厅与菜单 -->
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

          <!-- 五、我的预订与订单 -->
          <section class="of-a of-a-mine">
            <div class="card card-pad">
              <div class="card-head"><div class="card-title">我的预订与订单</div>
                <div class="card-extra"><span class="badge badge-gray">操作人 ${esc(actor.name)}</span>
                  <button class="btn btn-outline btn-sm" id="of-refresh">刷新</button></div></div>
              <div id="of-mine">${mineHtml(S, esc)}</div>
            </div>
          </section>
        </div>
      </div>`;

    const $ = (sel) => root.querySelector(sel);
    const chat = $("#of-chat");
    const paintChat = () => {
      chat.innerHTML = S.messages.map((m) => messageHtml(m, ctx)).join("");
      chat.scrollTop = chat.scrollHeight;
    };
    paintChat();

    const toast = (msg, kind) => UI.toast(msg, kind || "info");

    // ---------- 对话（办公助手 Agent） ----------

    const send = async (text) => {
      const input = $("#of-input");
      const q = (text || input.value || "").trim();
      if (!q || S.sending) return;
      if (!online) { toast("后端离线：办公办理与数据查询需要连接后端", "err"); return; }
      if (metaCache && metaCache.llm_available === false) {
        toast("LLM 服务不可用：办公与数据速查暂无法自主办理，请先看左侧速查卡", "err");
        return;
      }
      if (text) toast(`已交给办公与数据速查：${q}`, "info");
      input.value = "";
      S.sending = true;
      S.messages.push({ role: "user", content: q, time: nowHM() });
      S.messages.push({ role: "bot", content: "思考中…", time: nowHM(), pending: true, steps: [] });
      paintChat();
      // 对话卡置顶、速查/点击行在下方：发问后把对话区带回视野，避免"点了没反应"的错觉
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
        // 办理完成后刷新"我的预订与订单"，让留痕与状态闭环可见
        await loadMine();
      } catch (err) {
        replaceLastBot({
          role: "bot", time: nowHM(),
          content: `⚠️ ${(err && err.message) || err}`, steps: [], hitl: null, gaps: [],
        });
        toast(`办理失败：${(err && err.message) || err}`, "err");
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

    // ---------- 会议室面板 ----------

    const paintRooms = () => {
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

    const paintAvail = () => {
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
        toast(`查询失败：${(err && err.message) || err}`, "err");
      }
    });

    root.querySelectorAll("#of-rooms [data-ask]").forEach((btn) => {
      btn.addEventListener("click", () => send(
        `帮我订 ${S.form.date} ${S.form.start}-${S.form.end} 的「${btn.dataset.ask}」，主题项目讨论`));
    });

    // ---------- 餐厅 / 菜单 ----------

    const paintMenu = () => {
      const box = $("#of-menu");
      if (box) box.innerHTML = menuHtml(S, ctx);
      box.querySelectorAll("[data-ask]").forEach((btn) => {
        btn.addEventListener("click", () => send(`中午订个餐，来两份${btn.dataset.ask}`));
      });
    };

    const loadMenu = async (rid) => {
      if (!online) { toast("后端离线：无法加载菜单", "err"); return; }
      try {
        S.menuId = rid;
        S.menu = await api.officeMenu(rid);
        root.querySelectorAll("#of-rests .chip").forEach((c) => {
          c.classList.toggle("active", c.dataset.menu === rid);
        });
        paintMenu();
      } catch (err) {
        toast(`菜单加载失败：${(err && err.message) || err}`, "err");
      }
    };
    $("#of-rests").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-menu]");
      if (chip) loadMenu(chip.dataset.menu);
    });

    // ---------- 我的预订与订单 ----------

    async function loadMine() {
      if (!online) return;
      try {
        S.mine = await api.officeMine(actor.name);
        const box = $("#of-mine");
        if (box) box.innerHTML = mineHtml(S, esc);
        bindCancels();
      } catch (err) { /* 静默：列表刷新失败不影响主流程 */ }
    }

    function bindCancels() {
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
            toast(`取消失败：${(err && err.message) || err}`, "err");
          }
        });
      });
    }
    bindCancels();

    $("#of-refresh").addEventListener("click", async () => {
      await loadMine();
      toast("已刷新", "info");
    });

    // ---------- 制造数据速查（只读快照） ----------

    const bindSnap = () => {
      root.querySelectorAll("#of-snap [data-q]").forEach((el) => {
        el.addEventListener("click", () => send(el.dataset.q));
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

    const loadSnap = async () => {
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

    // ---------- 首屏数据 ----------

    if (online) {
      Promise.allSettled([
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
        loadSnap(),
        api.officeAvailable(S.form.date, S.form.start, S.form.end, S.form.cap)
          .then((res) => { S.avail = res; paintAvail(); })
          .catch(() => { /* 首屏可用性查询失败不打扰 */ }),
      ]);
    } else {
      const be = $("#of-backend");
      if (be) { be.textContent = "后端：离线（演示模式）"; be.classList.add("badge-amber"); }
      const llm = $("#of-llm");
      if (llm) llm.textContent = "LLM：未连接";
      loadSnap();
      toast("后端离线：办公办理需连后端；制造数据速查显示演示数据", "info", 4000);
    }
  }

  window.Screens = window.Screens || {};
  window.Screens.render_office = render;
})();
