/**
 * 智造协同平台 · 六屏渲染（screens.js）
 *
 * 屏幕：工作台 / 订单履约看板 / 协同问数 / 预警中心 / 报告中心 / 审批确认中心。
 * 每个 render_xxx(root, ctx) 独立渲染，可重复调用（状态经 ctx.state.screenState 持久）。
 *
 * 真实链路：协同问数（POST /api/v2/query + SSE /api/v2/events）、
 *           人工确认 审批（GET /api/v2/workflow/pending + approve/reject）。
 * 演示数据：订单看板 / 预警 / 报告 / 后端离线时的问数回放（均明确标注）。
 */
(function () {
  "use strict";

  /* ============================================================
   * 共享：结构化问数上下文（ask_ctx）——与 chat_prefill 搭对传递
   *
   * 各模块问数入口在写自然语言预填（chat_prefill）的同时，把结构化上下文
   * （module/screen/kpi/view/window/filters）写入 chat_ask_ctx；协同问数屏
   * 自动发送时一并携带——后端命中看板 KPI 时直接复用看板聚合函数，
   * 口径与看板同源（不经 LLM 不重写 SQL，毫秒级返回）。
   * ============================================================ */

  function writeAskPrefill(question, askCtx) {
    try {
      sessionStorage.setItem("chat_prefill", question);
      if (askCtx && typeof askCtx === "object") {
        sessionStorage.setItem("chat_ask_ctx", JSON.stringify(askCtx));
      } else {
        sessionStorage.removeItem("chat_ask_ctx");
      }
    } catch (e) { /* 隐私模式忽略 */ }
  }

  function readAskCtx() {
    try {
      const raw = sessionStorage.getItem("chat_ask_ctx");
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function clearAskCtx() {
    try { sessionStorage.removeItem("chat_ask_ctx"); } catch (e) { /* 忽略 */ }
  }

  /* ============================================================
   * 共享：SLA 倒计时（全局 ticker，1s 刷新）
   * ============================================================ */

  let _slaTimer = null;

  function ensureSlaTicker() {
    if (_slaTimer) return;
    _slaTimer = setInterval(() => {
      document.querySelectorAll("[data-sla-due]").forEach((row) => {
        const due = new Date(row.dataset.slaDue).getTime();
        const totalMs = Number(row.dataset.slaTotal) || 7200000;
        const now = Date.now();
        const remain = due - now;
        const elapsedPct = Math.min(100, Math.max(0, ((totalMs - remain) / totalMs) * 100));
        const timeEl = row.querySelector(".sla-time");
        const barEl = row.querySelector(".progress-bar");
        let cls = "ok";
        let text;
        if (remain <= 0) { cls = "over"; text = "已超时 " + fmtShort(-remain); }
        else {
          text = fmtRemain(remain);
          const pct = (remain / totalMs) * 100;
          if (pct <= 15) cls = "over";
          else if (pct <= 40) cls = "warn";
        }
        if (timeEl) { timeEl.textContent = text; timeEl.className = "sla-time " + cls; }
        if (barEl) { barEl.style.width = elapsedPct + "%"; barEl.className = "progress-bar " + cls; }
      });
    }, 1000);
  }

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

  function fmtShort(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    if (m >= 60) return `${Math.floor(m / 60)}h${m % 60}m`;
    return `${m}m`;
  }

  /** 工单 SLA 总时长（created_at → sla_due_at；异常值回退默认 2h） */
  function slaTotalMs(t) {
    const due = t && t.sla_due_at ? new Date(t.sla_due_at).getTime() : NaN;
    const created = t && t.created_at ? new Date(t.created_at).getTime() : NaN;
    if (Number.isFinite(due) && Number.isFinite(created) && due > created) return due - created;
    return 7200000;
  }

  /** 工单 SLA 截止时间（异常值回退 now + 2h，保证倒计时可渲染） */
  function slaDueIso(t) {
    const due = t && t.sla_due_at ? new Date(t.sla_due_at).getTime() : NaN;
    return Number.isFinite(due) ? t.sla_due_at : new Date(Date.now() + 7200000).toISOString();
  }

  function slaHtml(dueIso, totalMs) {
    ensureSlaTicker();
    const due = new Date(dueIso).getTime();
    const safeDue = Number.isFinite(due) ? dueIso : new Date(Date.now() + (Number(totalMs) || 7200000)).toISOString();
    const total = Number(totalMs) || 7200000;
    return `<div class="sla-row" data-sla-due="${safeDue}" data-sla-total="${total}">`
      + `<span class="sla-time ok">计算中…</span>`
      + `<div class="progress"><div class="progress-bar ok" style="width:0%"></div></div></div>`;
  }

  /* ============================================================
   * 共享：人工确认 审批动作（工作台 / 问数 / 审批中心共用）
   * ============================================================ */

  function processedMap(ctx) {
    const st = ctx.state.screenState;
    if (!st.hitlProcessed) st.hitlProcessed = {};
    return st.hitlProcessed;
  }

  /**
   * 合并"本地已处理"工单进待办展示列表：
   * 审批后工单会离开后端 waiting_hitl 队列，但门户仍需展示 before/after
   * （已处理徽标 + 处理横幅 + 状态机留痕），因此用本地缓存补齐已处理项。
   */
  function mergeProcessedTasks(ctx, pending) {
    const st = ctx.state.screenState;
    st.hitlCache = st.hitlCache || {};
    const map = processedMap(ctx);
    const display = [];
    const seen = new Set();
    (pending || []).forEach((t) => {
      st.hitlCache[t.task_uid] = t;
      seen.add(t.task_uid);
      display.push(t);
    });
    Object.keys(map).forEach((uid) => {
      if (!seen.has(uid) && st.hitlCache[uid]) display.push(st.hitlCache[uid]);
    });
    return display;
  }

  /** P0-4 强制填意见：驳回一律必填；同意时若所选动作标注 require_comment 则必填。 */
  function commentRule(action, selFull) {
    if (action === "reject") {
      return { title: "驳回理由（必填）", hint: "驳回必须说明理由，将写入工单留痕并通知发起方。" };
    }
    if (action === "approve" && selFull && selFull.require_comment) {
      return { title: "决策依据（必填）", hint: "该处置动作属高风险（如特采/让步接收/立即停机），必须填写决策依据。" };
    }
    return null;
  }

  /** 轻量意见输入弹窗：返回输入文本；取消返回 null。不引入框架，覆盖层样式复用 .comment-modal-*。 */
  function askComment(ctx, opts) {
    const UI = ctx.UI;
    const o = opts || {};
    return new Promise((resolve) => {
      const wrap = document.createElement("div");
      wrap.className = "comment-modal";
      wrap.innerHTML = `
        <div class="comment-modal-card" role="dialog" aria-modal="true">
          <div class="comment-modal-title">${UI.icon("warn", 15)}<span>${UI.esc(o.title || "填写决策意见")}</span></div>
          <div class="comment-modal-hint">${UI.esc(o.hint || "该操作将写入工单留痕，请填写理由/依据。")}</div>
          <textarea class="input comment-modal-input" rows="3" placeholder="${UI.esc(o.placeholder || "请输入理由/依据（必填）")}"></textarea>
          <div class="comment-modal-bar">
            <button type="button" class="btn btn-outline btn-sm" data-cancel>取消</button>
            <button type="button" class="btn btn-primary btn-sm" data-ok>提交</button>
          </div>
        </div>`;
      const input = wrap.querySelector(".comment-modal-input");
      const onKey = (e) => { if (e.key === "Escape") close(null); };
      const close = (val) => {
        document.removeEventListener("keydown", onKey);
        wrap.remove();
        resolve(val);
      };
      const submit = () => {
        const text = (input.value || "").trim();
        if (!text) { input.classList.add("is-invalid"); input.focus(); return; }
        close(text);
      };
      input.addEventListener("input", () => input.classList.remove("is-invalid"));
      wrap.querySelector("[data-cancel]").addEventListener("click", () => close(null));
      wrap.querySelector("[data-ok]").addEventListener("click", submit);
      wrap.addEventListener("click", (e) => { if (e.target === wrap) close(null); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(wrap);
      setTimeout(() => input.focus(), 30);
    });
  }

  async function hitlAction(ctx, taskUid, action) {
    const st = ctx.state.screenState;
    const map = processedMap(ctx);
    if (map[taskUid]) return;
    const role = ctx.currentRole();

    // 动作校验：有可选动作时必须先选定
    const acts = normalizeActions(_hitlActions[taskUid] || []);
    const govStatus = signoffOf(taskUid, acts);
    const sel = hitlSelMap(ctx)[taskUid]
      || (govStatus.action_key ? { key: govStatus.action_key, label: govStatus.selected_action } : null);
    // 回查完整动作对象，保证 require_comment 在「工作台列表 / 审批中心」两条入口都生效
    const selFull = sel
      ? (acts.find((a) => a.key === sel.key) || acts.find((a) => a.label === sel.label) || sel)
      : null;
    const commentEl = document.getElementById(`act-comment-${taskUid}`)
      || document.getElementById("hitl-comment");
    let commentText = commentEl && commentEl.value ? commentEl.value.trim() : "";
    if (action === "approve") {
      if (acts.length && !sel) {
        ctx.UI.toast("请先选择一个处置动作（按你的岗位权限）", "warn", 3200);
        return;
      }
      // 前端门控（后端仍二次校验）：会签未齐 / 无 approve 权限时直接提示
      const gate = approveGate(ctx, taskUid, acts);
      if (!gate.ok) {
        ctx.UI.toast(gate.reason, "warn", 4600);
        return;
      }
    }
    // P0-4 强制填意见：驳回一律必填；同意时若所选动作标注 require_comment 则必填
    const need = commentRule(action, selFull);
    if (need && !commentText) {
      const typed = await askComment(ctx, need);
      if (typed === null) return;        // 用户取消 → 不提交
      commentText = typed;
    }
    const decision = sel ? sel.label : "";
    if (!ctx.api.online()) {
      map[taskUid] = {
        action, status: action === "approve" ? "running" : "closed",
        actor: role.user, time: new Date().toISOString(), demo: true, decision,
      };
      ctx.UI.toast("后端离线——演示模式：已在界面标记为已处理（未真实调用后端）", "info", 4200);
      ctx.refreshScreen();
      return;
    }
    const comment = (action === "approve" ? "门户审批：同意" : "门户审批：驳回")
      + (commentText ? "；" + commentText : "");
    try {
      const task = action === "approve"
        ? await ctx.api.workflowApprove(taskUid, role.user, comment, false, decision)
        : await ctx.api.workflowReject(taskUid, role.user, comment, decision);
      const last = task.history && task.history.length ? task.history[task.history.length - 1] : null;
      map[taskUid] = {
        action, status: task.status,
        actor: (last && last.actor) || role.user,
        time: task.updated_at || new Date().toISOString(),
        decision,
      };
      delete hitlSelMap(ctx)[taskUid];  // 处理后清除选择，避免跨工单串用
      // 缓存刷新为审批后的工单（history 含 create + select_action + cosign + approve 留痕）
      ctx.state.screenState.hitlCache = ctx.state.screenState.hitlCache || {};
      ctx.state.screenState.hitlCache[taskUid] = task;
      _hitlGov[taskUid] = task;
      // 审批后保持详情停在该工单，便于查看 before/after 与状态机留痕
      ctx.state.screenState.hitlSelected = taskUid;
      ctx.UI.toast(`工单 ${taskUid} 已${action === "approve" ? "同意" : "驳回"} → ${statusLabel(task.status)}`, "ok", 4000);
      ctx.refreshScreen();
    } catch (err) {
      ctx.UI.toast(`审批失败：${apiErrorMessage(err)}`, "err", 5200);
    }
  }

  function hitlProcessedBanner(ctx, taskUid) {
    const info = processedMap(ctx)[taskUid];
    if (!info) return "";
    const approve = info.action === "approve";
    const label = approve ? "已同意" : "已驳回";
    const when = new Date(info.time);
    const time = Number.isNaN(when.getTime()) ? info.time : when.toLocaleString("zh-CN", { hour12: false });
    return `<div class="processed-banner${approve ? "" : " rejected"}">`
      + `${ctx.UI.icon(approve ? "check" : "x", 15)}`
      + `<span>${label} · 工单状态 <b>${ctx.UI.esc(statusLabel(info.status))}</b> · 处理人 ${ctx.UI.esc(info.actor)} · ${ctx.UI.esc(time)}`
      + `${info.demo ? "（演示模式，未真实调用后端）" : ""}</span></div>`;
  }

  /* ============================================================
   * 屏幕一：工作台
   * ============================================================ */

  /** 我的看板 KPI 卡：配置了 drill 映射（k.drill）→ 点卡片就地下钻分解；
   *  未映射 → 点卡片跳协同问数。卡内「问数」按钮始终跳协同问数（携带该卡预置问题）。 */
  function dashKpiCard(ctx, k) {
    const UI = ctx.UI;
    // 有 drill 还要「当前岗位能到达那个屏」，否则渲染出一个必然 403 的入口。
    const mapped = k.drill && k.drill.screen && k.drill.kpi
      && kpiDrillVisible(ctx, k.drill.screen);
    const ask = k.ask || "";
    // 结构化问数上下文：KPI 卡问数携带 drill 目标标识（screen/kpi）——
    // 后端命中看板口径时直查（同源同口径），未命中回落自然语言链路
    const askCtxAttrs = `data-ask-module="dashboard" data-ask-view="kpi"`
      + ` data-ask-screen="${UI.esc((k.drill || {}).screen || "")}"`
      + ` data-ask-kpi="${UI.esc((k.drill || {}).kpi || "")}"`;
    const attrs = mapped
      ? `data-dash-drill="1" data-screen="${UI.esc(k.drill.screen)}" data-kpi="${UI.esc(k.drill.kpi)}"
         title="点击下钻「${UI.esc(k.label)}」明细分解"`
      : `data-ask="${UI.esc(ask)}" ${askCtxAttrs} title="点击问数：${UI.esc(ask)}"`;
    const hint = mapped
      ? `${UI.icon("filter", 11)} 下钻分解`
      : `${UI.icon("chat", 11)} ${UI.esc(ask)}`;
    return `<div class="card stat-card stat-clickable" role="button" tabindex="0" ${attrs}>
      <div class="stat-icon ${UI.esc(k.tone || "blue")}">${UI.icon(k.icon, 19)}</div>
      <div class="stat-body">
        <div class="stat-num">${UI.esc(k.num)}</div>
        <div class="stat-label">${UI.esc(k.label)}</div>
        <div class="stat-trend ${k.up ? "up" : ""}">${UI.icon(k.up ? "warn" : "clock", 12)} ${UI.esc(k.trend)}</div>
        <div class="stat-ask">${hint}</div>
        ${mapped ? `<button class="stat-ask-btn" data-ask="${UI.esc(ask)}" ${askCtxAttrs}
                title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 11)} 问数</button>` : ""}
      </div>
    </div>`;
  }

  /** 下钻 screen 名 → 侧边栏屏 id（manufacturing / quality 是 mfgboard 的两个 Tab）。 */
  const SCREEN_TO_NAV = {
    orders: "orders",
    spc: "spc",
    manufacturing: "mfgboard",
    quality: "mfgboard",
  };

  /**
   * 某个下钻目标在当前岗位是否可达。
   *
   * KPI 卡写死带 `drill`，但卡是按岗位配置的、屏是按权限收敛的，两者不联动：
   * 行政人事的首张卡就带「下钻分解」，而 orders 屏它根本看不到——点下去只会
   * 吃一个 403 再跳到「无权查看」。明知不可点还摆一个可点的样子，比不给更糟。
   *
   * 返回 false 时调用方把卡片降级为"点此问数"。
   */
  function kpiDrillVisible(ctx, screen) {
    const navId = SCREEN_TO_NAV[screen];
    if (!navId) return true;                       // 未知 screen 交给后端裁决，不在前端猜
    const allowed = (ctx.visibleNav ? ctx.visibleNav() : []).map((n) => n.id);
    return !allowed.length || allowed.includes(navId);
  }

  /** 首页快捷入口候选（按岗位可见性过滤后再渲染）。 */
  const QUICK_ENTRIES = [
    { id: "chat", icon: "chat", bg: "primary-light", fg: "primary",
      label: "协同问数", sub: "对话式跨部门协同分析" },
    { id: "orders", icon: "orders", bg: "warn-bg", fg: "warn",
      label: "订单履约看板", sub: "订单管道 · 风险订单 · 部门协同" },
    { id: "hitl", icon: "approve", bg: "ok-bg", fg: "ok",
      label: "审批确认中心", sub: "复线决策 / 对客 ETA 审批" },
  ];

  /**
   * 快捷入口必须跟侧边栏用同一套可见性，否则会出现「菜单里没有、首页却能点进去」的矛盾
   * ——行政人事首页曾同时挂着「订单履约看板」和「审批确认中心」两个入口，
   * 点下去不是 403 就是被静默回落到别的屏。
   */
  function quickEntries(ctx) {
    const allowed = (ctx.visibleNav ? ctx.visibleNav() : []).map((n) => n.id);
    const list = allowed.length
      ? QUICK_ENTRIES.filter((q) => allowed.includes(q.id))
      : QUICK_ENTRIES;
    return list;
  }

  /** 工作台「我的 AI 同事」卡（D1/R2）：同事人设 + 推荐问题直达对话。
   *  离线 / 目录缺失 → 诚实降级为岗位问候 + 演示标注，不编同事人设。 */
  function colleagueCardHtml(ctx) {
    const UI = ctx.UI;
    const c = ctx.currentColleague();
    if (!c) {
      return `<div class="colleague-card is-offline" id="dash-colleague">
        <div class="colleague-card-head">${UI.icon("cpu", 15)}我的 AI 同事
          <span class="section-extra">按岗位配班 · 一句话触发闭环</span></div>
        <div class="colleague-card-body">
          <span class="empty-inline">${UI.demoFlag(ctx.api.online() ? "同事目录加载中" : "后端离线 · 同事配置未加载")}</span>
          <span class="colleague-card-greet">${UI.esc((ctx.currentRole() || {}).greeting || "")}</span>
        </div>
      </div>`;
    }
    const qs = (c.questions || []).slice(0, 3);
    return `<div class="colleague-card" id="dash-colleague">
      <div class="colleague-card-head">${UI.icon("cpu", 15)}我的 AI 同事
        <span class="section-extra">${UI.esc(c.name)} · 一句话触发闭环，关键动作人工确认</span></div>
      <div class="colleague-card-body">
        <div class="colleague-card-id">
          <span class="colleague-avatar">${UI.icon("cpu", 18)}</span>
          <div class="colleague-card-meta">
            <div class="colleague-card-name">${UI.esc(c.name)}${UI.badge("在线", "badge-green")}</div>
            <div class="colleague-card-greet">${UI.esc(c.greeting || "")}</div>
          </div>
          <select class="colleague-select" id="dash-colleague-select" aria-label="切换 AI 同事">
            ${colleagueOptionsHtml(ctx)}
          </select>
        </div>
        ${qs.length ? `<div class="colleague-card-qs">
          <span class="colleague-card-qs-label">${UI.icon("chat", 12)}可以直接问：</span>
          ${qs.map((q) => `<button type="button" class="chip" data-colleague-q="${UI.esc(q)}">${UI.esc(q)}</button>`).join("")}
        </div>` : ""}
      </div>
    </div>`;
  }

  function render_dashboard(root, ctx) {
    const UI = ctx.UI, DEMO = ctx.DEMO;
    const st = ctx.state.screenState;
    const role = ctx.currentRole();
    // 首页里所有指向别的屏的入口都必须先过可见性，否则会出现
    // 「侧边栏里没有、首页却能点」的矛盾入口。
    const hitlVisible = (ctx.visibleNav ? ctx.visibleNav() : [])
      .some((n) => n.id === "hitl");
    // 我的看板内联下钻状态（host=dashboard）；其它看板残留下钻状态在此复位
    let drill = st.boardDrill;
    if (drill && drill.host !== "dashboard") { drill = null; st.boardDrill = null; }
    const sum = DEMO.ALERT_SUMMARY[role.id] || DEMO.ALERT_SUMMARY.gm;
    const hour = new Date().getHours();
    const greet = hour < 6 ? "凌晨好" : hour < 12 ? "上午好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
    const today = new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" });

    root.innerHTML = `
      <div class="content-inner">
        <div class="hero">
          <div class="hero-greeting">${greet}，${UI.esc(role.user)}${UI.icon("sparkle", 18)}</div>
          <div class="hero-sub">${UI.esc(today)} · ${UI.esc(role.greeting)}。${UI.esc(role.desc)}。</div>
        </div>

        ${colleagueCardHtml(ctx)}

        <div class="briefing" id="briefing-bar">
          <div class="briefing-head">${UI.icon("sparkle", 14)}今日需要你关注
            <span class="section-extra">事找人：按岗位聚合（点击直达）</span></div>
          <div class="briefing-items" id="briefing-items">
            <span class="empty-inline">${UI.spinner("sm")} 正在聚合…</span>
          </div>
        </div>

        <div class="section-title">我的看板
          <span class="section-extra">统计范围：${UI.esc(role.name)}视角 · ${drill ? "下钻明细分解 · 面包屑逐级返回" : "点卡片下钻分解 / 问数"}</span>
          ${drill ? "" : UI.demoFlag("指标卡为岗位视角演示配置 · 下钻与看板屏取后端实时数据")}
        </div>
        <div id="dash-crumb"></div>
        <div id="dash-body">
          ${drill
            ? `<div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载下钻明细…</div></div>`
            : `<div class="grid grid-4">
            ${(DEMO.ROLE_KPIS[role.id] || []).map((k) => dashKpiCard(ctx, k)).join("")}
            <div class="card stat-card">
              <div class="stat-icon blue">${UI.icon("approve", 19)}</div>
              <div><div class="stat-num" id="dash-hitl-count">—</div><div class="stat-label">待人工确认</div>
              <div class="stat-trend">${UI.icon("clock", 12)} 停线工单 SLA 2h</div></div>
            </div>
          </div>`}
        </div>

        <div class="section-title">待办中心 <span class="section-extra" id="todo-source">待确认事项 · 加载中…</span></div>
        <div class="card">
          <div class="card-head">
            <div class="card-title">${UI.icon("approve", 16)}人工在环待确认</div>
            <div class="card-extra">
              ${hitlVisible
                ? `<button class="link-btn" data-goto="hitl">前往审批中心 ${UI.icon("arrow", 12)}</button>`
                : ""}
            </div>
          </div>
          <div id="todo-list"><div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载待办…</div></div></div>
        </div>

        <div class="section-title">快捷入口</div>
        <div class="grid grid-3">
          ${quickEntries(ctx).map((q) => `
          <button class="quick-entry" data-goto="${UI.esc(q.id)}">
            <span class="qe-ico" style="background:var(--${q.bg});color:var(--${q.fg})">${UI.icon(q.icon, 20)}</span>
            ${UI.esc(q.label)}<span style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3);font-weight:400">${UI.esc(q.sub)}</span>
          </button>`).join("")}
        </div>
      </div>`;

    root.querySelectorAll("[data-role]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.setRole(btn.dataset.role));
    });
    root.querySelectorAll("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.navigate(btn.dataset.goto));
    });
    // 我的 AI 同事卡：推荐问题直达对话 + 卡内切换同事（D1/R2）
    bindColleagueCard(ctx, root);

    // 我的看板 KPI 卡：有映射 → 就地 L2 下钻；未映射 → 跳协同问数
    root.querySelectorAll("[data-dash-drill]").forEach((el) => {
      const fn = () => setDrill(ctx, {
        level: 2, host: "dashboard", screen: el.dataset.screen, kpi: el.dataset.kpi,
      });
      el.addEventListener("click", fn);
      el.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); }
      });
    });
    // 卡内「问数」按钮 / 未映射卡整卡：跳协同问数（携带预置问题 + 结构化上下文）
    root.querySelectorAll("[data-ask]").forEach((btn) => {
      const go = (ev) => {
        if (ev) ev.stopPropagation();
        const q = btn.dataset.ask;
        if (!q) return;
        writeAskPrefill(q, {
          module: btn.dataset.askModule || "dashboard",
          screen: btn.dataset.askScreen || "",
          kpi: btn.dataset.askKpi || "",
          view: btn.dataset.askView || "kpi",
        });
        ctx.navigate("chat");
      };
      btn.addEventListener("click", go);
      // div 卡无原生键盘激活，补齐 Enter/Space（原生 <button> 不重复绑定）
      if (btn.tagName !== "BUTTON") {
        btn.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); }
        });
      }
    });

    // 处于下钻态：就地渲染 L2/L3（宿主 = 工作台看板区，复用看板下钻机件）
    if (drill) loadMfgDrill(ctx, drill);
    loadDashboardBriefing(ctx);
    loadDashboardTodos(ctx);
  }

  /** 今日简报（"事找人"）：待我动作 + 预警计数聚合，点击直达对应屏。 */
  async function loadDashboardBriefing(ctx) {
    const box = document.getElementById("briefing-items");
    if (!box) return;
    const UI = ctx.UI, role = ctx.currentRole();
    const items = [];
    try {
      if (ctx.api.online()) {
        const resp = await ctx.api.workflowPending(govRoles(ctx));
        // _get 返回的是载荷本身（数组），不是 {data} 信封——原实现取 resp.data
        // 永远拿到 undefined，简报里的「N 项待办」因此恒为 0（静默失效）。
        const all = Array.isArray(resp) ? resp : (resp.data || []);
        const scope = (resp && resp.scope) || {};
        const mine = all.filter((t) => t.visibility === "mine" && t.my_action);
        const hold = all.filter((t) => t.visibility !== "mine").length;
        // 超时只算「该我办但没办」的；域内待观察项超时责任不在本岗位。
        const overdue = mine.filter((t) =>
          t.sla_due_at && new Date(t.sla_due_at) < new Date()
          && !["done", "closed"].includes(t.status));
        if ((scope.slots || []).length === 0) {
          items.push(`<span class="briefing-item">${UI.icon("info", 13)}
            本岗位不参与治理审批</span>`);
        } else if (mine.length) {
          const byAct = {};
          mine.forEach((t) => { byAct[t.my_action] = (byAct[t.my_action] || 0) + 1; });
          const label = { select_action: "待我发起", cosign: "待我会签", approve: "待我终批" };
          items.push(`<button class="briefing-item hot" data-goto="hitl">${UI.icon("approve", 13)}
            <b>${mine.length} 项待办</b>：${Object.entries(byAct).map(([k, v]) => `${label[k] || k} ${v}`).join(" · ")}</button>`);
        } else if (hold) {
          // 没轮到我不是「没事」。不把域内积压说出来，简报给出的就是虚假的轻松感。
          items.push(`<button class="briefing-item" data-goto="hitl">${UI.icon("approve", 13)}
            <b>0 项待我办</b>：域内 ${hold} 条待观察（会签未到我 / 待终批）</button>`);
        }
        if (overdue.length) {
          items.push(`<button class="briefing-item danger" data-goto="hitl">${UI.icon("warn", 13)}
            <b>${overdue.length} 张工单超时</b>：SLA 已过，需关注升级</button>`);
        }
      }
    } catch (e) { /* 离线降级：下方演示数据兜底 */ }

    // 以下三条来自 DEMO.ALERT_SUMMARY——**演示数据，文案是写死的**
    // （"华为终端 S 级订单临期" 与真实系统状态无关）。因此只在后端离线时展示。
    //
    // 在线时继续拼，等于把编造信息混进真实链路：HR 岗位因 ALERT_SUMMARY 里
    // 预留的 order 计数，会凭空看到一条根本不存在的订单风险，而人事岗根本
    // 不承担订单职责。这类"看起来正常的假消息"比直接报错更难被发现。
    const sum = ctx.api.online() ? null
      : (ctx.DEMO.ALERT_SUMMARY[role.id] || ctx.DEMO.ALERT_SUMMARY.gm);
    if (sum && sum.spc) items.push(`<button class="briefing-item" data-goto="alerts">${UI.icon("activity", 13)}
      <b>SPC 越限 ${sum.spc} 项</b>：锡膏印刷 CPK 0.89 &lt; 1.33</button>`);
    if (sum && sum.shortage) items.push(`<button class="briefing-item" data-goto="alerts">${UI.icon("box", 13)}
      <b>缺料预警 ${sum.shortage} 项</b>：锡膏 M7020 到货延迟 36h</button>`);
    if (sum && sum.order) items.push(`<button class="briefing-item" data-goto="orders">${UI.icon("orders", 13)}
      <b>订单履约风险 ${sum.order} 单</b>：华为终端 S 级订单临期</button>`);
    if (!items.length) {
      // 在线时不能写"一切正常"——预警通道还没接真实数据源，
      // "没显示"只说明"没接入"，不说明"没异常"。
      box.innerHTML = ctx.api.online()
        ? `<span class="empty-inline">${UI.icon("info", 13)} 暂无待办 · 实时预警尚未接入</span>`
        : `<span class="empty-inline">${UI.icon("check", 13)} 当前无待办与预警（演示数据）</span>`;
      return;
    }
    box.innerHTML = items.join("");
    box.querySelectorAll("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.navigate(btn.dataset.goto));
    });
  }

  async function loadDashboardTodos(ctx) {
    const list = document.getElementById("todo-list");
    const source = document.getElementById("todo-source");
    const count = document.getElementById("dash-hitl-count");
    if (!list || ctx.state.screen !== "dashboard") return;
    const UI = ctx.UI, DEMO = ctx.DEMO;

    if (!ctx.api.online()) {
      if (source) source.innerHTML = `待确认事项 · ${UI.demoFlag("后端离线 · 演示数据")}`;
      if (count) count.textContent = String(DEMO.DEMO_TODOS.length);
      list.innerHTML = DEMO.DEMO_TODOS.map((t) => demoTodoHtml(ctx, t)).join("");
      bindTodoActions(ctx, list);
      return;
    }
    try {
      // 带角色：后端以令牌为准，只能拿到「我治理域内」的工单。
      // 不带角色就变成「全厂队列」——HR 会看到品质 8D 与停机窗口，这正是要堵的。
      const raw = await ctx.api.workflowPending(govRoles(ctx));
      if (ctx.state.screen !== "dashboard") return;
      const scope = raw.scope || {};
      const slots = scope.slots || [];
      const mine = raw.filter((t) => t.visibility === "mine");

      if (!slots.length) {
        // 无治理职责。措辞必须与「处理完了」区分开：
        // HR 看到「所有关键决策均已处理」会以为是自己办的，是误导。
        if (source) source.innerHTML = `待确认事项 · ${UI.realFlag("治理队列")}`;
        if (count) count.textContent = "—";
        list.innerHTML = `<div class="empty">${UI.icon("info", 28)}`
          + `<div style="margin-top:8px">本岗位不参与治理审批</div>`
          + `<div class="muted" style="margin-top:4px;font-size: calc(12px * var(--font-scale))">`
          + `复线 / 特采 / 8D 关闭 / 对客承诺等决策由对应治理岗位确认</div></div>`;
        return;
      }

      if (source) source.innerHTML = `待确认事项 · ${UI.realFlag("我的治理待办")}`;
      if (count) count.textContent = String(mine.length);
      const tasks = mergeProcessedTasks(ctx, mine);
      if (!tasks.length) {
        const hold = raw.length - mine.length;
        list.innerHTML = `<div class="empty">${UI.icon("check", 30)}`
          + `<div style="margin-top:8px">当前无待办——关键决策都已轮到别人</div>`
          + (hold > 0
            ? `<div class="muted" style="margin-top:4px;font-size: calc(12px * var(--font-scale))">`
              + `域内另有 ${hold} 条待观察（会签未到我 / 已签完待终批）
                 <button class="link-btn" data-goto="hitl">去审批中心</button></div>`
            : "")
          + `</div>`;
        list.querySelectorAll("[data-goto]").forEach((b) =>
          b.addEventListener("click", () => ctx.navigate(b.dataset.goto)));
        return;
      }
      list.innerHTML = tasks.slice(0, 4).map((t) => realTodoHtml(ctx, t)).join("");
      bindTodoActions(ctx, list);
    } catch (err) {
      if (ctx.state.screen !== "dashboard") return;
      if (source) source.innerHTML = `待确认事项 · ${UI.demoFlag("待办加载失败 · 演示数据")}`;
      if (count) count.textContent = String(DEMO.DEMO_TODOS.length);
      list.innerHTML = DEMO.DEMO_TODOS.map((t) => demoTodoHtml(ctx, t)).join("");
      bindTodoActions(ctx, list);
    }
  }

  /**
   * 列表内联审批摘要（工作台「待办中心」卡片）：
   * 把工单 context 已带的事因 / 上游各域依据结论 / 风险评级 / 预估复线 / 已选动作 / 会签进度
   * 压缩成 2-3 行，让审批人在列表即可判断，无需先点开详情（对齐竞品"卡片即详情、减少跳转"）。
   */
  function todoSummaryHtml(ctx, t) {
    const UI = ctx.UI;
    const c = t.context || {};
    const reason = c.reason || "";
    const brief = Array.isArray(c.brief) ? c.brief : [];
    const slot = slotOf(t);
    const signoff = taskSignoff(t);
    const rows = [];
    if (slot) {
      rows.push(gateReasonHtml(ctx, slot));
    }
    if (reason) {
      rows.push(`<div class="todo-brief reason">${UI.icon("warn", 12)}<span>${UI.esc(reason)}</span></div>`);
    }
    if (slot) {
      rows.push(signoffGapHtml(ctx, signoff, slot, c.actions));
    }
    if (brief.length) {
      const items = brief.slice(0, 3).map((b) => {
        const text = String(b.summary || "");
        const shown = text.length > 40 ? text.slice(0, 40) + "…" : text;
        return `<b>${UI.esc(b.label || "")}</b> ${UI.esc(shown)}`;
      }).filter(Boolean);
      if (items.length) {
        rows.push(`<div class="todo-brief">${UI.icon("list", 12)}<span>${items.join(" · ")}</span></div>`);
      }
    }
    const chips = [];
    if (c.fa_rating) chips.push(`风险评级 <b>${UI.esc(c.fa_rating)}</b>`);
    if (c.eta_hours != null && c.eta_hours !== "") chips.push(`预估复线 <b>${UI.esc(c.eta_hours)}h</b>`);
    if (slot) {
      chips.push(signoff.selected_action
        ? `动作 <b>${UI.esc(signoff.selected_action)}</b>`
        : "动作 <b>未选定</b>");
      chips.push(signoff.total
        ? `会签 <b>${UI.esc(signoff.progress)}</b>${signoff.satisfied ? " ✓" : ""}`
        : "无需会签");
    }
    if (chips.length) {
      rows.push(`<div class="todo-chips">${chips.map((x) => `<span class="todo-chip">${x}</span>`).join("")}</div>`);
    }
    return rows.length ? `<div class="todo-brief-wrap">${rows.join("")}</div>` : "";
  }

  function demoTodoHtml(ctx, t) {
    const UI = ctx.UI;
    const hoursLeft = Number(t.sla_hours_left);
    const due = new Date(Date.now() + (Number.isFinite(hoursLeft) ? hoursLeft : 1) * 3600000).toISOString();
    const processed = processedMap(ctx)[t.task_uid];
    const meta = t.meta || {};
    return `<div class="todo-item" data-task="${UI.esc(t.task_uid)}">
      <div class="todo-ico ${t.icon}">${UI.icon(t.icon === "eta" ? "users" : t.icon === "impact" ? "list" : "zap", 16)}</div>
      <div class="todo-main">
        <div class="todo-title">${UI.esc(t.title)}</div>
        <div class="todo-meta">
          <span>工单 <b>${UI.esc(t.task_uid)}</b></span>
          <span>审批人 <b>${UI.esc(t.approver)}</b></span>
          <span>线体/型号 <b>${UI.esc(meta.line || "-")} / ${UI.esc(meta.model || "-")}</b></span>
          <span>风险评级 <b>${UI.esc(meta.rating || "-")}</b></span>
        </div>
        <div style="margin-top:8px;max-width:280px">${slaHtml(due, 7200000)}</div>
        ${hitlProcessedBanner(ctx, t.task_uid)}
      </div>
      <div class="todo-actions">
        ${processed ? "" : `
        <button class="btn btn-primary btn-sm" data-approve="${UI.esc(t.task_uid)}">同意</button>
        <button class="btn btn-danger btn-sm" data-reject="${UI.esc(t.task_uid)}">驳回</button>`}
        <button class="btn btn-outline btn-sm" data-detail="${UI.esc(t.task_uid)}">详情</button>
      </div>
    </div>`;
  }

  function realTodoHtml(ctx, t) {
    const UI = ctx.UI;
    const due = slaDueIso(t);
    const total = slaTotalMs(t);
    const approvers = (t.assignees || []).join("、") || "—";
    const processed = processedMap(ctx)[t.task_uid];
    // 播种治理缓存：工作台列表不经过「审批中心」屏幕，直接点「同意」也必须走治理链语义
    if (t.governance && !_hitlGov[t.task_uid]) _hitlGov[t.task_uid] = t;
    if (!_hitlActions[t.task_uid]) {
      _hitlActions[t.task_uid] = normalizeActions((t.context && t.context.actions) || []);
    }
    // 列表页门控：未选动作 / 会签未齐 / 无审批权限 → 禁用「同意」，与后端 _approve 三道强制校验一致
    const gate = processed ? { ok: true, reason: "" } : todoGate(ctx, t);
    const gateHint = (!processed && !gate.ok && gate.reason)
      ? `<div class="todo-gate">${UI.icon("info", 12)}<span>${UI.esc(gate.reason)}</span></div>`
      : "";
    return `<div class="todo-item" data-task="${UI.esc(t.task_uid)}">
      <div class="todo-ico ${t.wf_id === "hitl_eta" ? "eta" : t.wf_id === "impact_assessment" ? "impact" : "restart"}">
        ${UI.icon(t.wf_id === "hitl_eta" ? "users" : t.wf_id === "impact_assessment" ? "list" : "zap", 16)}
      </div>
      <div class="todo-main">
        <div class="todo-title">${UI.esc(t.hitl_prompt || t.title)}</div>
        <div class="todo-meta">
          <span>工单 <b>${UI.esc(t.task_uid)}</b></span>
          <span>审批人 <b>${UI.esc(approvers)}</b></span>
          <span>优先级 <b>${UI.esc(t.priority || "P1")}</b></span>
          <span>run_id <b>${UI.esc((t.context && t.context.run_id) || "-")}</b></span>
        </div>
        ${todoSummaryHtml(ctx, t)}
        ${gateHint}
        <div style="margin-top:8px;max-width:280px">${slaHtml(due, total)}</div>
        ${hitlProcessedBanner(ctx, t.task_uid)}
      </div>
      <div class="todo-actions">
        ${processed ? "" : `
        <button class="btn btn-primary btn-sm" data-approve="${UI.esc(t.task_uid)}"${gate.ok ? "" : ` disabled title="${UI.esc(gate.reason)}"`}>同意</button>
        <button class="btn btn-danger btn-sm" data-reject="${UI.esc(t.task_uid)}">驳回</button>`}
        <button class="btn btn-outline btn-sm" data-detail="${UI.esc(t.task_uid)}">详情</button>
      </div>
    </div>`;
  }

  function bindTodoActions(ctx, list) {
    list.querySelectorAll("[data-approve]").forEach((btn) => {
      btn.addEventListener("click", () => hitlAction(ctx, btn.dataset.approve, "approve"));
    });
    list.querySelectorAll("[data-reject]").forEach((btn) => {
      btn.addEventListener("click", () => hitlAction(ctx, btn.dataset.reject, "reject"));
    });
    list.querySelectorAll("[data-detail]").forEach((btn) => {
      btn.addEventListener("click", () => {
        // 深链到具体工单：进入审批中心并选中该工单，避免二次翻找
        ctx.state.screenState.hitlSelected = btn.dataset.detail;
        ctx.navigate("hitl");
      });
    });
  }

  /* ============================================================
   * 屏幕二：订单履约看板
   * ============================================================ */

  /* ============================================================
   * 屏幕二：订单履约看板（L1 指标总览 / L2 维度分解 / L3 单实体明细）
   * 参考「制造综合看板」：三级可下钻 + 每级可协同问数
   * ============================================================ */

  /** 订单数量千分位展示。 */
  function fmtQty(v) {
    const n = Number(v || 0);
    return Number.isFinite(n) ? n.toLocaleString("en-US") : String(v == null ? "" : v);
  }

  /** 订单履约 L1 正文：KPI 卡 + TOP5 + 状态管道 + 风险订单 + 部门协同 + 口径说明。 */
  function ordersBody(UI, d, DEMO) {
    const grid = (d.kpis || []).length >= 4 ? "grid grid-4" : "grid grid-2";
    const pipe = d.pipeline || [];
    const total = pipe.reduce((s, p) => s + (p.count || 0), 0) || 1;
    const risks = d.risk_orders || [];
    const domains = ((DEMO && DEMO.ORDERS) || {}).domains || [];

    const kpiHtml = `<div class="section-title">核心指标 <span class="section-extra">点卡片看客户/型号明细分解 · 卡内「问数」可跳协同问数</span></div>
      <div class="${grid}">${(d.kpis || []).map((k) => mfgKpiCard(UI, k, "orders")).join("")}</div>`;

    const rankHtml = `<div class="section-title">重点关注 TOP5 <span class="section-extra">最需关注的最差 5 名 · 点行看实体逐日明细</span></div>
      <div class="grid grid-2">${(d.rankings || []).map((r) => mfgRankCard(UI, r, "orders", d.window && d.window.label)).join("")}</div>`;

    const pipeHtml = `<div class="section-title">订单状态管道 <span class="section-extra">按订单环节分布 · 标注各环节延期单数 · 点环节看该环节订单清单</span></div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">${UI.icon("activity", 16)}订单状态管道</div>
          <div class="card-extra">在单合计 <b style="color:var(--text)">${total}</b> 单</div>
        </div>
        <div class="pipeline">
          ${pipe.map((p, i) => `
            <button type="button" class="pipe-stage" data-pipe-stage="${UI.esc(p.stage)}"
              title="点看「${UI.esc(p.stage)}」环节订单清单（弹窗）">
              <div class="pipe-stage-name">${UI.esc(p.stage)}</div>
              <div class="pipe-stage-num">${UI.esc(p.count)}</div>
              <div class="pipe-stage-bar"><div class="progress-bar" style="width:${Math.round(((p.count || 0) / total) * 100)}%"></div></div>
              <div class="pipe-stage-pct">占比 ${Math.round(((p.count || 0) / total) * 100)}%${p.delayed ? ` · 延期 ${UI.esc(p.delayed)}` : ""}</div>
            </button>
            ${i < pipe.length - 1 ? `<div class="pipe-arrow">${UI.icon("chevron", 14)}</div>` : ""}
          `).join("")}
        </div>
      </div>`;

    const riskHtml = `<div class="section-title">风险订单 <span class="section-extra">延期风险 / 缺料关联 / 客诉 · 点行看订单详情 · 行内「问数」协同处置</span></div>
      <div class="card">
        ${risks.length ? risks.map((r) => {
          const levelCls = r.customer_level === "S" ? "badge-red" : r.customer_level === "A" ? "badge-amber" : "badge-gray";
          const ask = `订单 ${r.order_no}（${r.customer}）延期 ${r.delay_days} 天，当前环节「${r.stage}」，${r.shortage_note || "无缺料异常"}，如何推进按期交付？`;
          return `<div class="risk-order" role="button" tabindex="0"
              data-risk-order="${UI.esc(r.order_no)}"
              title="点看订单 ${UI.esc(r.order_no)} 详情（弹窗）">
            <div class="ro-head">
              <span class="ro-no">${UI.esc(r.order_no)}</span>
              ${UI.badge(r.customer, "badge-blue")}
              ${UI.badge("客户等级 " + r.customer_level, levelCls)}
              ${UI.badge("延期 " + r.delay_days + " 天", r.delay_days >= 3 ? "badge-red" : "badge-amber")}
              ${UI.badge(r.risk_type, "badge-outline")}
              <button class="row-ask" data-ask="${UI.esc(ask)}" title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 11)}问数</button>
            </div>
            <div class="ro-body">
              <span>产品 <b>${UI.esc(r.product_model)} × ${fmtQty(r.qty)}pcs</b></span>
              <span>环节 <b>${UI.esc(r.stage)}</b> · 责任人 <b>${UI.esc(r.owner)}</b></span>
            </div>
            ${r.shortage_note ? `<div class="ro-body"><span>缺料/异常 <b>${UI.esc(r.shortage_note)}</b></span></div>` : ""}
            <div class="ro-foot">
              <span style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3)">履约进度</span>
              <div class="progress"><div class="progress-bar ${r.progress < 50 ? "warn" : ""}" style="width:${r.progress}%"></div></div>
              <span style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3);font-variant-numeric:tabular-nums">${UI.esc(r.progress)}%</span>
            </div>
          </div>`;
        }).join("") : `<div class="empty">${UI.icon("check", 22)}<div style="margin-top:8px">窗口内无风险订单</div></div>`}
      </div>`;

    const domainHtml = domains.length ? `<div class="section-title">部门协同状态 <span class="section-extra">16 部门 → 5 域组 · 今日待办 / 已闭环 / 超时 · 点卡片看域内协同明细 ${UI.demoFlag()}</span></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr))">
        ${domains.map((dd) => {
          const todo = dd.depts.reduce((s, x) => s + x.todo, 0);
          const done = dd.depts.reduce((s, x) => s + x.done, 0);
          const over = dd.depts.reduce((s, x) => s + x.over, 0);
          return `<div class="card dept-domain" role="button" tabindex="0"
              data-domain="${UI.esc(dd.name)}"
              title="点看「${UI.esc(dd.name)}」协同明细（弹窗）">
            <div class="dept-domain-head">
              <span class="stat-icon ${dd.name === "制造域" ? "blue" : dd.name === "品质域" ? "purple" : dd.name === "供应链域" ? "green" : dd.name === "设备域" ? "amber" : "red"}" style="width:30px;height:30px;border-radius:8px">${UI.icon(dd.icon, 15)}</span>
              <span class="dept-domain-name">${UI.esc(dd.name)}</span>
              <span class="dept-domain-count">${dd.depts.length} 部门</span>
            </div>
            <table class="dept-table">
              <thead><tr><th>部门</th><th style="text-align:right">待办</th><th style="text-align:right">闭环</th><th style="text-align:right">超时</th></tr></thead>
              <tbody>
                ${dd.depts.map((x) => `<tr>
                  <td>${UI.esc(x.name)}</td>
                  <td class="num">${x.todo}</td>
                  <td class="num" style="color:var(--ok)">${x.done}</td>
                  <td class="num ${x.over > 0 ? "over" : ""}">${x.over}</td>
                </tr>`).join("")}
                <tr><td style="font-weight:600;color:var(--text)">小计</td>
                  <td class="num" style="font-weight:600">${todo}</td>
                  <td class="num" style="font-weight:600;color:var(--ok)">${done}</td>
                  <td class="num ${over > 0 ? "over" : ""}">${over}</td></tr>
              </tbody>
            </table>
          </div>`;
        }).join("")}
      </div>` : "";

    return kpiHtml + rankHtml + pipeHtml + riskHtml + domainHtml + mfgNotes(UI, d.notes);
  }

  function render_orders(root, ctx) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    // 板级时间口径：当日 / 当月 / 最近7天（默认最近7天，与后端 resolve_window 同键）
    const WINDOW_KEYS = ["day", "month", "window"];
    const winKey = WINDOW_KEYS.indexOf(st.ordersWindow) >= 0 ? st.ordersWindow : "window";
    const WINDOW_CN = { day: "当日", month: "当月", window: "最近7天" };
    // 下钻状态只在本屏有效；从其它看板进入时复位
    let drill = st.boardDrill;
    // 仅本屏下钻有效；其它看板/工作台内联下钻残留一律复位
    if (drill && (drill.screen !== "orders" || drill.host === "dashboard")) {
      drill = null; st.boardDrill = null;
    }

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("orders", 20)}订单履约看板
            ${UI.badge("OTD / OTIF / 延期 / 齐套", "badge-blue")}</div>
          <div class="page-desc">订单全链路状态管道 · 交付类 KPI 三级下钻（客户·等级 / 型号）· 风险订单协同处置。
            <span id="orders-window">统计窗口加载中…</span>
            <span id="orders-flag"></span><span id="orders-gen" class="mfg-gen"></span></div>
        </div>
        <div class="chip-row mfg-windows" id="orders-windows" style="margin-bottom:14px">
          <span class="chip-row-label">统计口径</span>
          ${WINDOW_KEYS.map((k) => `<button class="chip${k === winKey ? " active" : ""}"
            data-window="${k}">${UI.icon("clock", 13)}${WINDOW_CN[k]}</button>`).join("")}
          <span class="chip-row-note">· KPI / TOP5 / 状态管道 / 风险订单 / 下钻 均按所选口径</span>
        </div>
        <div id="orders-crumb"></div>
        <div id="orders-body">
          <div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载看板数据…</div></div>
        </div>
      </div>`;

    root.querySelectorAll("#orders-windows [data-window]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (winKey === btn.dataset.window) return;
        st.ordersWindow = btn.dataset.window;
        // 换口径保持当前下钻层级（L2–L5）：只把下钻对象的窗口改成新口径，不退回看板首页。
        // loadMfgDrill 以 drill.window 为首选窗口键，故就地改写即可同层刷新。
        if (drill) drill.window = btn.dataset.window;
        else st.boardDrill = null;
        ctx.refreshScreen();
      });
    });

    if (drill) loadMfgDrill(ctx, drill);
    else loadOrdersDashboard(ctx, winKey);
  }

  /**
   * 无权限占位：**明确说"当前岗位无权查看"，绝不回落演示数据**。
   *
   * 这是本屏最容易做错的地方——把 403 当普通失败、切演示数据，界面上看起来
   * "一切正常"，等于用编造的数据掩盖越权，比直接报错严重得多（与健康检查
   * 那个假阳性是同一类问题）。数据范围由服务端裁决，前端只负责如实呈现。
   */
  function scopedDenied(UI, err, label, box, flagEl) {
    const msg = (err && err.message) ? err.message : "当前岗位无权查看该看板";
    if (flagEl) {
      flagEl.innerHTML = UI.badge("无权限", "badge-red", true);
    }
    box.innerHTML = `<div class="empty">
      ${UI.icon("shield", 30)}
      <div style="margin-top:10px;font-weight:600">${UI.esc(label)} · 当前岗位无权查看</div>
      <div style="margin-top:6px;color:var(--text-2);font-size: calc(13px * var(--font-scale))">${UI.esc(msg)}</div>
      <div style="margin-top:8px;color:var(--text-3);font-size: calc(12px * var(--font-scale))">
        数据范围由服务端裁决（<span class="mono">v2/data/data_scope.yaml</span>），
        需要访问请联系对应责任岗位或管理员。
      </div>
    </div>`;
  }

  /** 脱敏角标：让"已按岗位脱敏"这件事在界面上可见，而不是只写在文档里。 */
  function maskedFlag(UI, masked) {
    if (!masked || !masked.length) return "";
    return `<span class="badge badge-amber" title="以下字段已按当前岗位脱敏：${UI.esc(masked.join("、"))}">`
      + `${UI.icon("shield", 11)} 已脱敏 ${UI.esc(masked.length)} 项</span>`;
  }

  /**
   * 订单履约看板 L1 数据加载（真实优先 + 演示降级，全程不抛）：
   * 在线且未降级 → /api/v2/dashboard/orders（按所选时间口径）；否则 → DEMO.ORDERS_DASHBOARD。
   */
  async function loadOrdersDashboard(ctx, windowKey) {
    const UI = ctx.UI;
    const cfg = BOARD_CFG.orders;
    const box = document.getElementById(cfg.body);
    if (!box) return;
    // 时间口径：默认最近 7 天（与后端 resolve_window 同键），非法值回落 "window"
    const win = ["day", "month", "window"].indexOf(windowKey) >= 0 ? windowKey : "window";
    let payload = null, source = "real", reason = "", scopeMeta = null;

    if (ctx.api.online()) {
      try {
        const d = await ctx.api.ordersDashboard(win);
        if (d && !d.degraded && (d.kpis || []).length) {
          payload = d;
          scopeMeta = d.scope || null;
        } else {
          reason = (d && d.degraded_reason) || "后端数据降级";
        }
      } catch (err) {
        if (err && err.kind === "permission_denied") {
          scopedDenied(UI, err, cfg.label || "订单履约看板", box,
                       document.getElementById(cfg.flag));
          return;
        }
        reason = UI.friendlyError(err).text;
        UI.toast("看板数据请求失败，已切换演示数据", "warn", 3600);
      }
    } else {
      reason = "后端离线 · 演示数据";
    }

    if (!payload) {
      source = "demo";
      payload = ctx.DEMO.ORDERS_DASHBOARD;
    }
    if (!payload) {
      box.innerHTML = `<div class="empty">${UI.icon("warn", 28)}<div style="margin-top:8px">暂无看板数据</div></div>`;
      return;
    }

    const winEl = document.getElementById(cfg.win);
    if (winEl && payload.window) {
      winEl.textContent = `${payload.window.label}（${payload.window.start} ~ ${payload.window.end}）`;
    }
    const flagEl = document.getElementById(cfg.flag);
    if (flagEl) {
      flagEl.innerHTML = (source === "demo"
        ? UI.demoFlag(reason || "演示数据")
        : "") + maskedFlag(UI, scopeMeta && scopeMeta.masked);
    }
    const genEl = document.getElementById(cfg.gen);
    if (genEl && payload.generated_at) genEl.textContent = " · 生成于 " + payload.generated_at;

    box.innerHTML = ordersBody(UI, payload, ctx.DEMO);
    mfgBind(ctx, box, payload);
    // 板级载荷挂到 screenState：弹窗离线兜底时按管道环节回查汇总数
    ctx.state.screenState.ordersPayload = payload;
    bindOrdersExtras(ctx, box, payload);
    // R6：催交跟踪面板（治理轨状态机；在线且有 orders 范围时渲染，失败静默不占位）
    loadFollowupPanel(ctx, box);
  }

  /* ------------------------------------------------------------
   * 订单履约看板模块点击详情（管道环节 / 风险订单 / 部门域卡）
   * 弹窗复用「班组三率明细」的 .std-modal 体系：先挂骨架再填数据；
   * 在线走 /api/v2/dashboard/orders/stage|detail（与看板同一套裁决与脱敏），
   * 离线降级到当前载荷已知信息并明确标注，绝不编造明细。
   * ------------------------------------------------------------ */

  /** 弹窗骨架（复用 .std-modal 样式）：返回 { body, close }，由调用方填正文。 */
  function openOrderModalShell(ctx, tag, title, sub) {
    const UI = ctx.UI;
    document.querySelectorAll(".std-modal").forEach((el) => el.remove());
    const overlay = document.createElement("div");
    overlay.className = "std-modal";
    overlay.innerHTML = `<div class="std-modal-card" role="dialog" aria-modal="true"
        aria-label="${UI.esc(title)}">
        <div class="std-modal-head">
          <span class="std-modal-tag">${UI.esc(tag)}</span>
          <b>${UI.esc(title)}</b>
          <span class="std-modal-sub">${UI.esc(sub)}</span>
          <button type="button" class="std-modal-x" data-std-close aria-label="关闭">×</button>
        </div>
        <div class="std-modal-body"><div class="std-modal-loading">
          ${UI.icon("info", 13)}正在加载详情…</div></div>
      </div>`;
    document.body.appendChild(overlay);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    function close() {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      document.body.classList.remove("std-modal-open");
    }
    document.addEventListener("keydown", onKey);
    document.body.classList.add("std-modal-open");
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay || (ev.target.closest && ev.target.closest("[data-std-close]"))) close();
    });
    return {
      body: overlay.querySelector(".std-modal-body"),
      head: overlay.querySelector(".std-modal-head"),
      close,
    };
  }

  /** 把弹窗正文填进骨架（等接口期间被关掉则丢弃）。 */
  function fillOrderModal(shell, html) {
    const body = shell.body;
    if (!body || !body.isConnected) return;
    const holder = document.createElement("div");
    holder.innerHTML = html;
    if (holder.firstElementChild) body.replaceWith(holder.firstElementChild);
  }

  /** 弹窗顶部入口：把「进入 N 级看板」放到标题右侧（关闭按钮左侧），常驻可见，
   *  不再坠在弹窗正文末尾（原来要滚到底部才能看到）。 */
  function setModalHeadEntry(shell, html, onGo) {
    const head = shell.head;
    if (!head) return;
    const box = document.createElement("span");
    box.className = "std-modal-go";
    box.innerHTML = html;
    head.insertBefore(box, head.querySelector(".std-modal-x"));
    const btn = box.querySelector("button") || box;
    btn.addEventListener("click", onGo);
  }

  /** 三类可点模块统一绑定（行内「问数」按钮自带 stopPropagation，不会误触）。 */
  function bindOrdersExtras(ctx, box, payload) {
    const keyGo = (el, fn) => {
      el.addEventListener("click", fn);
      el.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); }
      });
    };
    const win = ctx.state.screenState.ordersWindow || "window";
    box.querySelectorAll("[data-pipe-stage]").forEach((el) => {
      keyGo(el, () => openOrderStageModal(ctx, el.dataset.pipeStage, win));
    });
    box.querySelectorAll("[data-risk-order]").forEach((el) => {
      keyGo(el, () => {
        const no = el.dataset.riskOrder;
        const row = ((payload && payload.risk_orders) || [])
          .find((r) => r.order_no === no) || {};
        openOrderDetailModal(ctx, no, row);
      });
    });
    box.querySelectorAll("[data-domain]").forEach((el) => {
      keyGo(el, () => openOrderDomainModal(ctx, el.dataset.domain));
    });
  }

  /* ------------------------------------------------------------
   * R6 齐套催交跟踪（治理轨状态机）
   * 缺口清单 → 派发 → 供应商回复 → 到货确认 → 关闭；超 SLA → 升级。
   * 演示态的到货确认带「演示回写」徽标（不可关）：状态存演示治理库，
   * **没有**写回 ERP——对外话术必须诚实（D9/D10）。
   * ------------------------------------------------------------ */

  const FU_STATUS = {
    draft: ["缺口清单", "badge-blue"],
    dispatched: ["已派发", "badge-amber"],
    supplier_replied: ["供应商已回复", "badge-blue"],
    arrival_confirmed: ["到货已确认", "badge-green"],
    escalated: ["超时升级", "badge-red"],
    closed: ["已关闭", ""],
  };

  function fuRowHtml(UI, r) {
    const [label, cls] = FU_STATUS[r.status] || [r.status || "—", ""];
    const acts = [];
    if (r.status === "dispatched") {
      acts.push(`<button class="btn btn-sm" data-fu-action="reply" data-fu-id="${UI.esc(r.followup_id)}">供应商已回复</button>`);
      acts.push(`<button class="btn btn-sm btn-outline" data-fu-action="escalate" data-fu-id="${UI.esc(r.followup_id)}">升级</button>`);
    } else if (r.status === "supplier_replied") {
      acts.push(`<button class="btn btn-sm" data-fu-action="arrival" data-fu-id="${UI.esc(r.followup_id)}">到货确认</button>`);
      acts.push(`<button class="btn btn-sm btn-outline" data-fu-action="escalate" data-fu-id="${UI.esc(r.followup_id)}">升级</button>`);
    } else if (r.status === "arrival_confirmed") {
      acts.push(`<button class="btn btn-sm" data-fu-action="close" data-fu-id="${UI.esc(r.followup_id)}">关闭催交</button>`);
    } else if (r.status === "escalated") {
      acts.push(`<button class="btn btn-sm" data-fu-action="reply" data-fu-id="${UI.esc(r.followup_id)}">供应商已回复</button>`);
      acts.push(`<button class="btn btn-sm btn-outline" data-fu-action="close" data-fu-id="${UI.esc(r.followup_id)}">关闭</button>`);
    }
    const reply = r.supplier_reply
      ? `<div class="fu-note">${UI.icon("chat", 11)}供应商回复：${UI.esc(r.supplier_reply)}</div>` : "";
    return `<div class="fu-row">
      <div class="fu-main">
        <div class="fu-title"><span class="mono">${UI.esc(r.part_no || "—")}</span>
          <span class="fu-name">${UI.esc(r.part_name || "")}</span>
          ${UI.badge(label, cls)}</div>
        <div class="fu-meta">供应商 ${UI.esc(r.supplier || "—")} · 缺口
          <b class="neg">${UI.esc(fmtQty(r.shortage_qty))}</b> · 催交人 ${UI.esc(r.owner || "—")}
          · SLA ${UI.esc((r.sla_due_at || "").slice(5, 16).replace("T", " ") || "—")}</div>
        ${reply}
      </div>
      <div class="fu-acts">${acts.join("")}</div>
    </div>`;
  }

  function followupPanelHtml(UI, d) {
    const rows = (d && d.rows) || [];
    const summary = (d && d.summary) || {};
    const open = rows.filter((r) => r.status !== "closed").length;
    const demo = !!(d && d.demo_writeback);
    const demoBadge = demo
      ? `<span class="fu-demo" title="${UI.esc(d.writeback_label || "")}">${UI.icon("shield", 11)}演示回写</span>` : "";
    const body = rows.length
      ? rows.map((r) => fuRowHtml(UI, r)).join("")
      : `<div class="empty fu-empty">${UI.icon("check", 22)}
          <div style="margin-top:8px">暂无催交待办——派发缺口清单后在此跟踪状态</div></div>`;
    return `<div class="card fu-card">
      <div class="card-head">
        <div class="card-title">${UI.icon("orders", 16)}齐套催交跟踪 ${demoBadge}</div>
        <div class="fu-head-acts">
          <span class="fu-sum">待办 <b>${open}</b> · 已关闭 <b>${UI.esc(summary.closed || 0)}</b></span>
          <button class="btn btn-sm" id="fu-dispatch">${UI.icon("send", 12)}一键派发缺口</button>
        </div>
      </div>
      <div class="fu-basis">${UI.icon("info", 12)}
        缺口＝低于安全库存（同源查询）；到货确认在演示态为「演示回写」，状态存演示治理库，<b>不写回 ERP</b>。</div>
      <div class="fu-list">${body}</div>
    </div>`;
  }

  /** 催交面板：仅在线且有访问范围时渲染；无权限/失败静默不占位（看板已裁决）。 */
  async function loadFollowupPanel(ctx, host) {
    if (!ctx.api.online() || !host) return;
    let d;
    try {
      d = await ctx.api.followups();
    } catch (err) {
      return;
    }
    const el = document.createElement("div");
    el.id = "orders-followups";
    el.innerHTML = followupPanelHtml(ctx.UI, d);
    host.appendChild(el);
    bindFollowupPanel(ctx, el);
  }

  function bindFollowupPanel(ctx, el) {
    const UI = ctx.UI;
    const dispatch = el.querySelector("#fu-dispatch");
    if (dispatch) {
      dispatch.addEventListener("click", async () => {
        const set = window.prompt("派发到哪个工单集？（如：本周开工批次）", "本周开工批次");
        if (set == null) return;
        const label = String(set).trim();
        if (!label) { UI.toast("工单集不能为空", "warn"); return; }
        dispatch.disabled = true;
        try {
          const d = await ctx.api.followupDispatch(label, 20);
          UI.toast(`已派发 ${(d.rows || []).length} 条催交待办（工单集：${label}）`, "ok", 4000);
          ctx.refreshScreen();
        } catch (err) {
          dispatch.disabled = false;
          UI.toast(UI.friendlyError(err).text, "err", 5000);
        }
      });
    }
    el.querySelectorAll("[data-fu-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.fuId;
        const action = btn.dataset.fuAction;
        let note = "", qty = null;
        if (action === "reply") {
          note = window.prompt("供应商回复内容（必填）", "");
          if (!note || !String(note).trim()) { UI.toast("供应商回复内容不能为空", "warn"); return; }
        } else if (action === "arrival") {
          const raw = window.prompt("到货确认：填写到货数量/说明（可留空）", "");
          if (raw == null) return;
          note = String(raw).trim();
          const num = parseFloat(note);
          if (Number.isFinite(num) && num > 0) qty = num;
        } else if (action === "close") {
          note = window.prompt("关闭结论（必填：到货结果 / 后续安排）", "");
          if (!note || !String(note).trim()) { UI.toast("关闭需填写结论", "warn"); return; }
        } else if (action === "escalate" && !window.confirm("确认把该催交单升级（超 SLA/重大延迟）？")) {
          return;
        }
        btn.disabled = true;
        try {
          const d = await ctx.api.followupTransition(id, action, note, qty);
          if (action === "arrival" && d && d.demo_writeback) {
            UI.toast(`到货已确认 · ${d.writeback_label || "演示回写（未写回 ERP）"}`, "warn", 6000);
          } else {
            UI.toast("催交状态已更新", "ok", 3000);
          }
          ctx.refreshScreen();
        } catch (err) {
          btn.disabled = false;
          UI.toast(UI.friendlyError(err).text, "err", 5000);
        }
      });
    });
  }

  /** 环节弹窗正文：汇总小签 + 订单清单表。后端字段缺省时不编造（显示 —）。 */
  function orderStageModalBody(UI, d, offline, fallbackCount) {
    const s = (d && d.summary) || { count: fallbackCount == null ? 0 : fallbackCount };
    const rows = (d && d.orders) || [];
    const chips = `<div class="std-chips">
        <span class="std-chip">订单数 <b>${UI.esc(fmtQty(s.count))}</b></span>
        <span class="std-chip">延期 <b class="${(s.delayed || 0) > 0 ? "neg" : "pos"}">${UI.esc(fmtQty(s.delayed))}</b></span>
        <span class="std-chip">订单量 <b>${UI.esc(fmtQty(s.qty))}pcs</b></span>
        <span class="std-chip">平均进度 <b>${s.avg_progress == null ? "—" : UI.esc(s.avg_progress) + "%"}</b></span>
      </div>`;
    const offlineTip = offline
      ? `<div class="std-modal-warn">${UI.icon("warn", 12)}
         后端离线或接口受限，以下仅为看板已缓存的汇总，<b>不含</b>逐单明细。</div>`
      : ((d && d.truncated) ? `<div class="std-modal-warn">${UI.icon("warn", 12)}
         环节内共 ${UI.esc(fmtQty(d.summary.count))} 单，清单仅展示最紧急的前 ${rows.length} 单。</div>` : "");
    const table = rows.length ? `<table class="std-table">
        <thead><tr><th>订单号</th><th>客户</th><th>型号</th><th>数量</th><th>进度</th>
          <th>延期</th><th>风险类型</th><th>责任人</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td class="std-team">${UI.esc(r.order_no || "—")}</td>
          <td>${UI.esc(r.customer || "—")}</td>
          <td>${UI.esc(r.product_model || "—")}</td>
          <td class="num">${UI.esc(fmtQty(r.qty))}</td>
          <td class="num">${UI.esc(r.progress == null ? "—" : r.progress + "%")}</td>
          <td class="num ${(r.delay_days || 0) > 0 ? "std-delta neg" : ""}">${UI.esc(r.delay_days == null ? "—" : r.delay_days + " 天")}</td>
          <td>${UI.esc(r.risk_type || "—")}</td>
          <td>${UI.esc(r.owner || "—")}</td>
        </tr>`).join("")}</tbody></table>`
      : `<div class="empty">${UI.icon("check", 22)}
          <div style="margin-top:8px">${offline ? "离线演示数据无逐单明细" : "该口径内此环节无订单"}</div></div>`;
    return `<div class="std-modal-body">${offlineTip}${chips}${table}
      ${(d && d.notes || []).map((n) => `<div class="std-notes">${UI.icon("info", 12)}<span>${UI.esc(n)}</span></div>`).join("")}
    </div>`;
  }

  /** 点管道环节 → 弹窗：该环节订单清单（与看板 pipeline 同源可对账）。 */
  async function openOrderStageModal(ctx, stage, win) {
    const UI = ctx.UI;
    const shell = openOrderModalShell(ctx, "订单 · 环节", stage,
      `「${stage}」环节订单清单 · 口径与看板一致`);
    let data = null, offline = false;
    try {
      if (!ctx.api.online()) throw new Error("offline");
      data = await ctx.api.orderStage(stage, win);
    } catch (err) { offline = true; }
    const pipe = ((ctx.state.screenState.ordersPayload || {}).pipeline || [])
      .find((p) => p.stage === stage) || {};
    fillOrderModal(shell, orderStageModalBody(UI, data, offline, pipe.count));
  }

  /** 订单详情正文：兼容后端行（order_no/product_model…）与演示行（no/product…）。 */
  function orderDetailModalBody(UI, r, offline) {
    const g = (...keys) => { for (const k of keys) { if (r[k] !== undefined && r[k] !== null && r[k] !== "") return r[k]; } return null; };
    const no = g("order_no", "no");
    const level = g("customer_level", "level");
    const levelCls = level === "S" ? "badge-red" : level === "A" ? "badge-amber" : "badge-gray";
    const offlineTip = offline
      ? `<div class="std-modal-warn">${UI.icon("warn", 12)}
         后端离线或接口受限，以下来自看板风险订单行，交期 / 齐套等字段<b>缺省</b>。</div>`
      : "";
    if (!no && !offline) {
      return `<div class="std-modal-body"><div class="empty">${UI.icon("info", 26)}
        <div style="margin-top:8px">查无此订单号。</div>
        <div class="std-modal-hint">订单可能已归档到其它窗口，可回看板按口径重试。</div>
      </div></div>`;
    }
    const row = (k, v) => v == null ? "" : `<div class="std-cause-line" style="display:flex;gap:8px">
        <span style="min-width:64px;color:var(--text-3);font-size: calc(12px * var(--font-scale))">${UI.esc(k)}</span>
        <b style="font-size: calc(12.5px * var(--font-scale))">${v}</b></div>`;
    const kit = r.kit_rate != null
      ? `${UI.esc(r.kit_rate)}%（${UI.esc(fmtQty(r.kit_done_qty == null ? "—" : r.kit_done_qty))} / ${UI.esc(fmtQty(r.kit_total_qty == null ? "—" : r.kit_total_qty))}）`
      : null;
    const progress = Number(g("progress") || 0);
    return `<div class="std-modal-body">${offlineTip}
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <span class="ro-no">${UI.esc(no || "—")}</span>
        <span class="badge badge-blue">${UI.esc(g("customer") || "—")}</span>
        <span class="badge ${levelCls}">客户等级 ${UI.esc(level || "—")}</span>
        ${g("risk_type") ? `<span class="badge badge-outline">${UI.esc(g("risk_type"))}</span>` : ""}
        ${(g("delay_days") || 0) > 0 ? `<span class="badge badge-red">延期 ${UI.esc(g("delay_days"))} 天</span>` : `<span class="badge badge-gray">无延期</span>`}
      </div>
      <div class="std-chips">
        <span class="std-chip">产品 <b>${UI.esc(g("product_model", "product") || "—")}</b></span>
        <span class="std-chip">数量 <b>${UI.esc(fmtQty(g("qty")))}</b>pcs</span>
        <span class="std-chip">环节 <b>${UI.esc(g("stage") || "—")}</b></span>
        <span class="std-chip">责任人 <b>${UI.esc(g("owner") || "—")}</b></span>
      </div>
      <div class="std-notes">
        ${row("下单日期", g("order_date"))}
        ${row("客户交期", g("due_date"))}
        ${row("实际发货", g("ship_date"))}
        ${row("准时 / 足额", g("on_time") == null ? null : `${g("on_time") ? "准时" : "逾期"} / ${g("in_full") ? "足额" : "缺料"}`)}
        ${row("齐套率", kit)}
      </div>
      ${r.shortage_note ? `<div class="std-modal-warn">${UI.icon("warn", 12)}缺料/异常：${UI.esc(r.shortage_note)}</div>` : ""}
      <div class="std-notes"><div class="std-cause-line" style="display:flex;gap:8px;align-items:center">
        <span style="min-width:64px;color:var(--text-3);font-size: calc(12px * var(--font-scale))">履约进度</span>
        <div class="progress" style="flex:1"><div class="progress-bar ${progress < 50 ? "warn" : ""}" style="width:${progress}%"></div></div>
        <b style="font-size: calc(12px * var(--font-scale))">${UI.esc(progress)}%</b></div>
      </div>
    </div>`;
  }

  /** 点风险订单行 → 弹窗：在线取全字段明细；离线用看板行数据降级展示。 */
  async function openOrderDetailModal(ctx, orderNo, row) {
    const UI = ctx.UI;
    const shell = openOrderModalShell(ctx, "订单 · 详情", orderNo,
      "订单全字段明细 · 客户身份按岗位脱敏");
    let data = null, offline = false;
    try {
      if (!ctx.api.online()) throw new Error("offline");
      data = await ctx.api.orderDetail(orderNo);
      if (data && data.found === false) data = null;   // 查无此单 → 走空态
    } catch (err) { offline = true; }
    const r = (data && data.order && Object.keys(data.order).length)
      ? Object.assign({}, data.order, { kit_rate: data.kit_rate }) : (row || {});
    fillOrderModal(shell, orderDetailModalBody(UI, r, offline));
  }

  /** 订单履约 2 级看板「点行」弹窗正文：指标小结 + 订单清单（列随指标动态下发）。
   *  在线时与 2 级看板该行同一套 DRILL_DEFS 口径（头部指标值逐字段可对账）；
   *  离线降级为「只有该行已缓存的比率」并明确标注，绝不编造订单明细。 */
  function orderGroupModalBody(UI, d, offline, fb) {
    const row = fb || {};
    const w = (d && d.window) || {};
    const val = (d && d.value) != null
      ? `${UI.esc(d.value)}${UI.esc(d.unit || "%")}`
      : (row.value != null ? `${UI.esc(row.value)}%` : "—");
    const frac = (d && d.numerator) != null
      ? `${UI.esc(d.numerator)} ÷ ${UI.esc(d.denominator)}`
      : (row.numerator != null ? `${UI.esc(row.numerator)} ÷ ${UI.esc(row.denominator)}` : "—");
    const chips = `<div class="std-chips">
        <span class="std-chip">${UI.esc((d && d.label) || "指标值")}
          <b class="${UI.esc((d && d.status) || row.status || "")}">${val}</b></span>
        <span class="std-chip">目标 <b>${(d && d.target) != null
          ? UI.esc(d.target) + UI.esc(d.unit || "%") : "—"}</b></span>
        <span class="std-chip">分子 ÷ 分母 <b>${frac}</b></span>
        ${d && d.orders ? `<span class="std-chip">清单 <b>${UI.esc(d.orders.length)}</b> 单</span>` : ""}
      </div>`;
    const offlineTip = offline
      ? `<div class="std-modal-warn">${UI.icon("warn", 12)}
         后端离线或接口受限，以下仅为 2 级看板已缓存的该行比率，<b>不含</b>订单清单。</div>`
      : ((d && !d.found) ? `<div class="std-modal-warn">${UI.icon("info", 12)}
         该分组在「${UI.esc(w.label || "当前口径")}」内没有订单（不是数据丢失）。</div>` : "");
    if (offline || (d && d.found === false)) {
      return `<div class="std-modal-body">${offlineTip}${chips}
        ${(d && d.notes || []).map((n) => `<div class="std-notes">${UI.icon("info", 12)}<span>${UI.esc(n)}</span></div>`).join("")}
      </div>`;
    }
    const cols = (d && d.columns) || [];
    const rows = (d && d.orders) || [];
    const cell = (r, c) => {
      const v = r[c.key];
      if (c.key === "on_time" || c.key === "in_full") {
        if (v === null || v === undefined) return "—";
        return v === 1 ? `<span class="badge badge-gray">是</span>`
                       : `<span class="badge badge-red">否</span>`;
      }
      if (c.key === "customer_level") {
        return v ? `<span class="badge ${v === "S" ? "badge-red" : v === "A" ? "badge-amber" : "badge-gray"}">${UI.esc(v)}</span>` : "—";
      }
      if (c.key === "progress") return v == null ? "—" : `${UI.esc(v)}%`;
      if (c.key === "delay_days") {
        return `<span class="${(v || 0) > 0 ? "std-delta neg" : ""}">${v == null ? "—" : UI.esc(v)}</span>`;
      }
      return v === null || v === undefined || v === "" ? "—" : UI.esc(v);
    };
    const table = rows.length ? `<div class="drill-scroll"><table class="std-table">
        <thead><tr>${cols.map((c) => `<th>${UI.esc(c.label)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${cell(r, c)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table></div>`
      : `<div class="empty">${UI.icon("check", 22)}
          <div style="margin-top:8px">该口径内此分组无订单</div></div>`;
    return `<div class="std-modal-body">${offlineTip}${chips}${table}
      ${(d && d.notes || []).map((n) => `<div class="std-notes">${UI.icon("info", 12)}<span>${UI.esc(n)}</span></div>`).join("")}
    </div>`;
  }

  /** 订单履约 2 级看板点行 → 分组订单明细弹窗（对齐班组三率弹窗模式）。
   *  行缓存值（rowJson）仅作离线降级；在线以 /orders/group 的同口径聚合为准。 */
  async function openOrderGroupModal(ctx, kpi, entityJson, win, rowJson) {
    const UI = ctx.UI;
    let ent = {}, row = {};
    try { ent = JSON.parse(entityJson || "{}") || {}; } catch (e) { ent = {}; }
    try { row = JSON.parse(rowJson || "{}") || {}; } catch (e) { row = {}; }
    const name = row.name
      || [ent.customer, ent.level].filter(Boolean).join(" · ")
      || ent.model || "—";
    const shell = openOrderModalShell(ctx, "2 级 · 订单明细", name,
      "该分组订单清单 · 口径与看板一致 · 客户身份按岗位脱敏");
    let data = null, offline = false;
    try {
      if (!ctx.api.online()) throw new Error("offline");
      data = await ctx.api.ordersGroup(kpi, ent, win);
    } catch (err) {
      if (err && err.kind === "permission_denied") {
        // 403 如实拒绝，不降级成演示明细（与看板加载器同一纪律）
        fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">
          ${UI.icon("warn", 26)}<div style="margin-top:8px">当前岗位无权查看该分组订单明细。</div>
        </div></div>`);
        return;
      }
      offline = true;
    }
    fillOrderModal(shell, orderGroupModalBody(UI, data, offline, row));
    // 3 级入口移到弹窗顶部（标题右侧）；仅在确有订单清单时提供，走既有 L3 下钻机件
    // （面包屑 / 返回 / 口径切换全部复用）。
    if (!offline && data && data.found !== false && ((data.orders || []).length)) {
      setModalHeadEntry(shell, `<button type="button" class="btn btn-primary" data-order-group-drill3
        title="继续下钻到 3 级：该分组逐日明细 + 原始记录">进入 3 级看板</button>`,
      () => {
        shell.close();
        const host = ((ctx.state.screenState || {}).boardDrill || {}).host || "";
        setDrill(ctx, { level: 3, host, screen: "orders", kpi, entity: ent, window: win });
      });
    }
  }

  /** SPC 2 级看板行 → 该特性的能力明细弹窗：能力指数（双西格玛）+ 逐日越限 + 原始记录。
   *  口径跟随当前档位与板级筛选（工段 / 产品分类）；与 L1 卡片**同源**（同一后端聚合），
   *  弹窗内保留「进入 3 级看板」入口（走既有 setDrill：面包屑 / 返回 / 口径切换全部复用）。
   *  403 如实拒绝（不降级演示明细）；离线显式标注；无数据如实说「暂无」，绝不编造。 */
  async function openSpcCapabilityModal(ctx, spec) {
    const UI = ctx.UI;
    const name = spec.name || spec.characteristic || "—";
    const shell = openOrderModalShell(ctx, "2 级 · 过程能力", name,
      "该特性的能力指数与越限明细 · 口径与看板一致");
    let data = null, offline = false;
    try {
      if (!ctx.api.online()) throw new Error("offline");
      data = await ctx.api.spcCapability(spec.characteristic, spec.win,
        spec.stage, spec.productCategory);
    } catch (err) {
      if (err && err.kind === "permission_denied") {
        // 403 如实拒绝，不降级成演示明细（与看板加载器同一纪律）
        fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">
          ${UI.icon("warn", 26)}<div style="margin-top:8px">当前岗位无权查看该特性的过程能力明细。</div>
        </div></div>`);
        return;
      }
      if (err && err.status === 404) {
        // 服务端尚未部署该能力（旧版本服务未重启）→ 如实说明，不误导成「离线」
        fillOrderModal(shell, `<div class="std-modal-body"><div class="std-modal-warn">
          ${UI.icon("warn", 14)}<span>服务端尚未包含「过程能力明细」能力（版本待更新），
          数据本身在线；服务端更新后即可查看。</span>
        </div></div>`);
        return;
      }
      offline = true;
    }
    fillOrderModal(shell, spcCapabilityModalBody(UI, data, offline, name));
    // 3 级入口移到弹窗顶部（标题右侧）；仅在确有明细时提供，走既有 L3 下钻机件
    // （面包屑 / 返回 / 口径切换全部复用）。
    if (!offline && data && data.found !== false) {
      setModalHeadEntry(shell, `<button type="button" class="btn btn-primary"
        data-spc-drill3>进入 3 级看板</button>`,
      () => {
        shell.close();
        const host = ((ctx.state.screenState || {}).boardDrill || {}).host || "";
        setDrill(ctx, { level: 3, host, screen: "spc", kpi: "spc_capability",
          entity: { characteristic: spec.characteristic }, window: spec.win });
      });
    }
  }

  /** SPC 测量记录弹窗：越限明细（overOnly）/ 单日明细（day）。
   *  与看板同口径（同窗口 / 同维度筛选）→ 记录数与看板「越限条数 / 测量条数」可对账。
   *  数据源 GET /api/v2/dashboard/spc/records（403 如实拒绝 / 离线显式标注 / 空数据骨架）。 */
  async function openSpcRecordsModal(ctx, spec) {
    const UI = ctx.UI;
    const isOver = !!spec.overOnly;
    const title = isOver ? "越限明细 · 子组超差率" : "单日测量明细";
    const name = isOver ? (spec.stage ? `${spec.stage} 工段` : "全部特性")
      : (spec.characteristic || "全部特性") + (spec.day ? ` · ${spec.day}` : "");
    const shell = openOrderModalShell(ctx, title, name,
      isOver ? "超出规格限的测量记录 · 按超出幅度降序 · 口径与看板一致"
             : "该日全部测量记录 · 口径与看板一致");
    let data = null, offline = false;
    try {
      if (!ctx.api.online()) throw new Error("offline");
      data = await ctx.api.spcRecords(spec.win, spec.stage, spec.productCategory,
        spec.characteristic || "", spec.day || "", isOver);
    } catch (err) {
      if (err && err.kind === "permission_denied") {
        fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">
          ${UI.icon("warn", 26)}<div style="margin-top:8px">当前岗位无权查看该测量记录明细。</div>
        </div></div>`);
        return;
      }
      if (err && err.status === 404) {
        // 服务端尚未部署该能力（旧版本服务未重启）→ **如实说明原因**，
        // 不误导成「离线」——数据明明在线，只是服务端版本落后。
        fillOrderModal(shell, `<div class="std-modal-body"><div class="std-modal-warn">
          ${UI.icon("warn", 14)}<span>服务端尚未包含「测量记录明细」能力（版本待更新），
          数据本身在线；服务端更新后即可查看。</span>
        </div></div>`);
        return;
      }
      offline = true;
    }
    if (offline) {
      fillOrderModal(shell, `<div class="std-modal-body"><div class="std-modal-warn">
        ${UI.icon("warn", 14)}<span>离线演示数据不含测量记录明细，可恢复后端后重试。</span>
      </div></div>`);
      return;
    }
    if (!data || data.found === false) {
      fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">${UI.icon("info", 24)}
        <div style="margin-top:8px">当前口径与筛选范围内暂无${isOver ? "越限" : "测量"}记录。</div>
      </div></div>`);
      return;
    }
    const cell = (v) => (v === null || v === undefined || v === "" ? "—" : UI.esc(v));
    const cols = data.columns || [];
    const f = data.filter || {};
    const scopeCn = [f.stage ? `工段 ${f.stage}` : "", f.product_category ? `产品分类 ${f.product_category}` : ""]
      .filter(Boolean).join(" · ") || "全厂";
    const rows = (data.records || []).map((r) => `<tr>${cols.map((c) => {
      const raw = r[c.key];
      const v = (c.key === "source" && raw === "demo-seed") ? "演示种子" : raw;
      return `<td>${cell(v)}</td>`;
    }).join("")}</tr>`).join("");
    fillOrderModal(shell, `<div class="std-modal-body">
      <div class="std-modal-cols">
        <div class="std-modal-col"><i>${isOver ? "越限记录" : "测量记录"}</i>
          <b>${UI.esc(data.count)}</b><em>条（最多展示 ${UI.esc((data.records || []).length)} 条）</em></div>
        <div class="std-modal-col"><i>范围</i><b>${UI.esc(scopeCn)}</b>
          <em>${f.characteristic ? UI.esc(f.characteristic) : "全部特性"}${f.day ? " · " + UI.esc(f.day) : ""}</em></div>
      </div>
      <div class="card"><div class="drill-scroll"><table class="std-table drill-table">
        <thead><tr>${cols.map((c) => `<th>${UI.esc(c.label || c.key)}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody></table></div></div>
      <div class="std-modal-note">${UI.icon("info", 12)}<span>口径与看板一致（同窗口 · 同维度筛选）——
        ${isOver ? "越限 = 测量值超出规格上限或低于规格下限；超出幅度 = 超出量 ÷ 规格区间。"
                 : "「数据来源：演示种子」表示该行来自演示数据装载，非运行时录入。"}</span></div>
    </div>`);
  }

  /** SPC 能力明细弹窗正文：能力指数小签 + 逐日越限 + 原始记录表。 */
  function spcCapabilityModalBody(UI, d, offline, name) {
    if (offline) {
      return `<div class="std-modal-body"><div class="std-modal-warn">
        ${UI.icon("warn", 14)}<span>离线演示数据不含该特性的明细，可恢复后端后重试。</span>
      </div><div class="empty">${UI.icon("info", 24)}
        <div style="margin-top:8px">「${UI.esc(name)}」的明细暂不可用（离线）。</div></div></div>`;
    }
    if (!d) {
      return `<div class="std-modal-body"><div class="empty">${UI.icon("info", 24)}
        <div style="margin-top:8px">暂无法加载「${UI.esc(name)}」的明细。</div></div></div>`;
    }
    if (d.found === false) {
      return `<div class="std-modal-body"><div class="empty">${UI.icon("info", 24)}
        <div style="margin-top:8px">「${UI.esc(name)}」在当前口径与筛选范围内暂无测量记录。</div>
      </div></div>`;
    }
    const cell = (v) => (v === null || v === undefined ? "—" : UI.esc(v));
    const spec = d.spec || {};
    const specTxt = (spec.lsl == null && spec.usl == null) ? "—"
      : `${spec.lsl == null ? "—" : spec.lsl} ~ ${spec.usl == null ? "—" : spec.usl}`;
    const tone = SPC_VERDICT[d.verdict] || "empty";
    const kpis = `<div class="std-modal-cols">
      <div class="std-modal-col"><i>Cpk</i><b class="std-conform ${UI.esc(tone)}">${cell(d.cpk)}</b>
        <em>判定 ${UI.esc(d.verdict || "—")}</em></div>
      <div class="std-modal-col"><i>Cp</i><b>${cell(d.cp)}</b><em>组内 σ 口径</em></div>
      <div class="std-modal-col"><i>Ppk</i><b>${cell(d.ppk)}</b><em>整体 σ 口径</em></div>
      <div class="std-modal-col"><i>子组超差率</i><b>${cell(d.over_rate)}%</b>
        <em title="越限条数 ÷ 测量条数（子组口径）">${UI.esc(d.over_num)} ÷ ${UI.esc(d.over_den)}</em></div>
      <div class="std-modal-col"><i>样本 / 子组</i><b>${cell(d.n_total)} / ${cell(d.n_subgroups)}</b>
        <em>规格 ${UI.esc(specTxt)}（演示规格限 · 工艺侧待给定）</em></div>
    </div>`;
    const daily = (d.daily || []).length ? `<div class="section-title">子组超差率 · 逐日越限
        <span class="section-extra">与看板「子组超差率」同源（同批测量行现算）</span></div>
      <div class="card"><div class="std-modal-trend">${(d.daily || []).map((x) => `
        <div class="std-modal-trend-item"><span class="std-modal-trend-label">${UI.esc(x.date)}</span>
          <span class="std-modal-trend-val">${UI.esc(x.over_rate)}%</span></div>`).join("")}</div></div>` : "";
    const cols = d.columns || [];
    const recs = d.records || [];
    const table = recs.length ? `<div class="section-title">关联原始记录
        <span class="section-extra">最多 ${recs.length} 条 · 与聚合同源</span></div>
      <div class="card"><div class="drill-scroll"><table class="std-table drill-table">
        <thead><tr>${cols.map((c) => `<th>${UI.esc(c.label || c.key)}</th>`).join("")}</tr></thead>
        <tbody>${recs.map((r) => `<tr>${cols.map((c) => {
          // 「数据来源」显示翻译（与 mfgRecordCard 同口径）：demo-seed = 演示种子
          const raw = r[c.key];
          const v = (c.key === "source" && raw === "demo-seed") ? "演示种子" : raw;
          return `<td>${cell(v)}</td>`;
        }).join("")}</tr>`).join("")}</tbody>
      </table></div></div>` : "";
    return `<div class="std-modal-body">${kpis}${daily}${table}
      <div class="std-modal-note">${UI.icon("info", 12)}<span>双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R），
        Pp/Ppk 用整体 σ；判定线 ≥1.33 合格 / ≥1.00 偏低 / &lt;1.00 不合格。</span></div>
    </div>`;
  }

  /** 制造看板 2/3/4 级行 → 明细弹窗：拉「下一级」下钻载荷（工段 · 工序 · 工位 + 逐日 + 关联
   *  原始记录）在弹窗里就地展示，弹窗内保留「进入 N 级看板」入口（走既有 setDrill：
   *  面包屑 / 返回 / 口径切换全部复用）。在线以 /dashboard/drill 的同口径聚合为准；
   *  离线降级到演示数据并明确标注，缺数据时如实说「暂无」，绝不编造明细。 */
  async function openMfgDrillModal(ctx, spec) {
    const UI = ctx.UI;
    const lvl = Math.min(5, Math.max(3, spec.level || 3));
    const shell = openOrderModalShell(ctx, `${lvl} 级 · 明细详情`, spec.name || "—",
      "制造看板 · 与所看口径同源 · 关联原始记录可追溯 · 弹窗内可进入整页看板");
    if (!shell.body) return;
    const ss = ctx.state.screenState || {};
    // 与看板级筛选同口径：制造四指标声明了 scope（工段 / 产品分类），下钻一并生效
    const mStage = spec.screen === "manufacturing" ? (ss.mfgMStage || "") : "";
    const mPcat = spec.screen === "manufacturing" ? (ss.mfgProdCat || "") : "";
    let data = null, reason = "";
    try {
      if (!ctx.api.online()) throw new Error("offline");
      const d = await ctx.api.dashboardDrill(spec.screen, spec.kpi, spec.entity,
        spec.step || "", spec.station || "", "", spec.win || "window",
        mStage, "", mPcat);
      if (d && !d.degraded && d.level === lvl) data = d;
      else reason = (d && d.degraded_reason) || "后端数据降级";
    } catch (err) {
      if (err && err.kind === "permission_denied") {
        // 403 如实拒绝，不降级成演示明细（与看板加载器同一纪律）
        fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">
          ${UI.icon("warn", 26)}<div style="margin-top:8px">当前岗位无权查看该明细。</div>
        </div></div>`);
        return;
      }
      reason = UI.friendlyError(err).text;
    }
    if (!data) {
      data = mfgDrillFallback(ctx, {
        level: lvl, screen: spec.screen, kpi: spec.kpi, entity: spec.entity,
        step: spec.step || "", station: spec.station || "", window: spec.win || "window",
      }, reason || "后端离线 · 演示数据");
    }
    if (!data) {
      fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">
        ${UI.icon("warn", 26)}<div style="margin-top:8px">该分组在当前口径下暂无明细数据</div>
        <div style="margin-top:6px;font-size: calc(12.5px * var(--font-scale));color:var(--text-3)">
          离线演示数据只含 TOP5 分组；逐日明细与关联原始记录需后端在线</div>
      </div></div>`);
      return;
    }
    const bodyOf = { 3: mfgDrillL3Body, 4: mfgDrillL4Body, 5: mfgDrillL5Body };
    fillOrderModal(shell, `<div class="std-modal-body">
      ${data.degraded ? UI.demoFlag(data.degraded_reason || "演示数据") : ""}
      ${(bodyOf[lvl] || mfgDrillL3Body)(UI, data)}
    </div>`);
    // 弹窗正文里的行还要再绑一次：弹窗挂在 <body> 下、不在看板容器里，mfgBind 的常规绑定到不了。
    // 这样弹窗内才有「工序行 → 4 级弹窗」「工位行 → 5 级弹窗」「行内问数 → 协同问数」。
    const modalHost = document.querySelector(".std-modal");
    if (modalHost) mfgBind(ctx, modalHost, data, "");
    // 「进入 N 级看板」入口移到弹窗顶部（标题右侧）：走既有 setDrill（面包屑 / 返回 / 口径切换全部复用）
    setModalHeadEntry(shell, `<button type="button" class="btn btn-primary" data-mfg-modal-jump
      title="关闭弹窗并进入 ${lvl} 级看板（面包屑 / 返回 / 口径切换全部复用）">
      进入 ${lvl} 级看板</button>`,
    () => {
      shell.close();
      const host = ((ctx.state.screenState || {}).boardDrill || {}).host || "";
      const next = { level: lvl, host: host, screen: spec.screen, kpi: spec.kpi,
                     entity: spec.entity, window: spec.win || "window" };
      if (spec.step) next.step = spec.step;
      if (spec.station) next.station = spec.station;
      setDrill(ctx, next);
    });
  }

  /** 部门域卡正文：域内各部门待办/闭环/超时条形 + 闭环率（纯前端演示数据）。 */
  function orderDomainModalBody(UI, dd) {
    const todo = dd.depts.reduce((s, x) => s + x.todo, 0);
    const done = dd.depts.reduce((s, x) => s + x.done, 0);
    const over = dd.depts.reduce((s, x) => s + x.over, 0);
    const closed = todo + done;
    const chips = `<div class="std-chips">
        <span class="std-chip">待办 <b class="neg">${UI.esc(todo)}</b></span>
        <span class="std-chip">已闭环 <b class="pos">${UI.esc(done)}</b></span>
        <span class="std-chip">超时 <b class="${over > 0 ? "neg" : ""}">${UI.esc(over)}</b></span>
        <span class="std-chip">闭环率 <b>${closed ? UI.esc(Math.round(done / closed * 100)) + "%" : "—"}</b></span>
      </div>`;
    const rows = dd.depts.map((x) => {
      const total2 = x.todo + x.done;
      const rate = total2 ? Math.round(x.done / total2 * 100) : 0;
      return `<div class="std-cause-item">
        <div class="std-cause-line">
          <span class="std-cause-name">${UI.esc(x.name)}</span>
          <b>待办 ${UI.esc(x.todo)} · 闭环 ${UI.esc(x.done)}${x.over > 0 ? ` · <i style="color:var(--danger);font-style:normal">超时 ${UI.esc(x.over)}</i>` : ""}</b>
        </div>
        <div class="std-cause-bar"><i class="ok-fill" style="width:${Math.max(2, rate)}%"></i></div>
        <div class="std-cause-meta">闭环率 ${rate}%</div>
      </div>`;
    }).join("");
    return `<div class="std-modal-body">${chips}${rows}
      <div class="std-notes">${UI.icon("info", 12)}<span>今日待办 / 已闭环 / 超时为协同演示口径；超时 = SLA 临期未闭环事项。</span></div>
    </div>`;
  }

  /** 点部门域卡 → 弹窗：域内协同明细（数据与看板卡片同源，纯前端）。 */
  function openOrderDomainModal(ctx, name) {
    const UI = ctx.UI;
    const dd = (((ctx.DEMO || {}).ORDERS || {}).domains || [])
      .find((x) => x.name === name);
    if (!dd) return;
    const shell = openOrderModalShell(ctx, "订单 · 协同", name,
      `「${name}」${dd.depts.length} 个部门 · 今日待办 / 闭环 / 超时`);
    fillOrderModal(shell, orderDomainModalBody(UI, dd));
  }

  /* ============================================================
   * 屏幕三：协同问数（核心屏 · 真实后端）
   * ============================================================ */

  const PARALLEL_NODES = new Set([
    // line_stop
    "quality", "supply_chain", "equipment",
    // S2 来料异常
    "procurement", "planning", "material_control",
    // S3 客诉 8D
    "pqe_process", "sqe_supplier", "engineering", "business_sales",
    // S4 出货风险
    "business_order", "warehouse_atp", "manufacturing_output", "logistics",
    // S8 设备预警
    "maintenance", "planning_window", "engineering_param", "spare_parts",
    // V4 协同规划器：多域并行取证（collab_<domain>）
    "collab_quality", "collab_manufacturing", "collab_supply_chain",
    "collab_rd_engineering",
  ]);
  // 在线引导问题：**选能凸显问数亮点、且已实测命中各条链路的问法**——
  //   ① 跨域态势汇总（digest）：一次拿到全厂未达标清单，口径随提问；
  //   ② SPC 过程能力（双西格玛口径 + 工具并轨）：越限即走处置确认；
  //   ③ 协同场景闭环：三域并行取证 → 根因 → 工单 → 人工确认；
  //   ④ 指标库口径统一：秒级直答，与看板同口径；
  //   ⑤ 跨域取证 + 对客承诺确认。
  // 不用"排产/排程/设备参数在哪"这类当前无对应链路的问法——点了答不上来，
  // 反而看不出能力（旧版引导问题的实测结论）。
  const QUICK_QUESTIONS = [
    "本月度最需关注什么？",
    "回流焊峰值温度的过程能力 Cpk 是否越限？",
    "A型号在SMT-1停线，何时复线？",
    "最近7天 OTD 准时交付率是多少？",
    "A型号能发多少货",
  ];
  // 离线引导问题：离线靠**预置回放**（demo-data.CHAT_DEMOS 按问题原文索引），
  // 故只能用已收录的 5 条；在线才换成上面的亮点问法（避免离线点了报"未收录"）。
  const DEMO_QUICK_QUESTIONS = [
    "A型号在SMT-1停线，何时复线？",
    "今天锡膏批次有问题",
    "华为投诉BATCH-A-1008",
    "A型号能发多少货",
    "SMT-1设备参数异常吗",
    // R5 交期答复闭环（2026-10-22）：离线回放覆盖，演示口径与参考图场景 2 同型
    "SO-3108 月底能交吗",
  ];
  // 节点展示名（YAML label 未定义时的兜底；事件通常已带 label）
  const NODE_LABELS = {
    intake: "业务Agent · 意图识别",
    business: "业务 · 问题识别",
    confirm_stop: "制造 · 确认停线",
    quality: "品质 · 查质量数据",
    supply_chain: "供应链 · 查物料数据",
    equipment: "设备 · 查设备日志",
    fanin: "结果汇聚 · Fan-in",
    fa: "根因分析 · 案例匹配",
    impact_wf: "工作流 · 停线影响评估工单",
    hitl_restart: "人工确认 · 复线决策确认",
    hitl_eta: "人工确认 · 对客ETA承诺确认",
    report: "报告 · 生成简报与对客报告",
    // S2
    sqe_assess: "SQE · 来料不合格判定",
    procurement: "采购 · 供应商跟催",
    planning: "计划 · 排产影响",
    material_control: "物控 · 库存/替代",
    scar_wf: "工作流 · SCAR 处置工单",
    hitl_disposition: "人工确认 · 退换货/特采决策",
    report_incoming: "报告 · 来料异常简报",
    // S3
    qa_lead: "QA · 8D 牵头",
    pqe_process: "PQE · 过程分析",
    sqe_supplier: "SQE · 供应商侧",
    engineering: "工程 · 设计根因",
    business_sales: "业务 · 对客口径",
    capa_flow: "工作流 · 8D 整改工单",
    hitl_d8_close: "人工确认 · 8D 报告关闭",
    hitl_complaint_reply: "人工确认 · 对客回复确认",
    report_complaint: "报告 · 8D/对客报告",
    // S4
    business_order: "业务 · 订单/交期",
    warehouse_atp: "仓库 · ATP",
    manufacturing_output: "制造 · 产出",
    logistics: "物流 · 在途",
    atp_analysis: "ATP 分析 · 可承诺量",
    delivery_risk_wf: "工作流 · 出货风险工单",
    change_flow: "工作流 · 订单变更工单",
    manufacturing: "制造 · 产能评估",
    hitl_delivery_commit: "人工确认 · 对客交期承诺",
    report_delivery: "报告 · 出货风险简报",
    // S8
    equipment_check: "设备 · 查设备日志",
    maintenance: "制造 · 维保建议",
    planning_window: "计划 · 换产窗口",
    engineering_param: "工程 · 参数建议",
    spare_parts: "采购 · 备件需求",
    maintenance_flow: "工作流 · 维保工单",
    hitl_maintenance_window: "人工确认 · 停机窗口确认",
    report_equipment: "报告 · 设备简报",
    // V4 协同规划器：多域并行取证节点（事件通常已带 label，此处为兜底）
    collab_quality: "质量域智能体 · 并行取证",
    collab_manufacturing: "制造域智能体 · 并行取证",
    collab_supply_chain: "供应链智能体 · 并行取证",
    collab_rd_engineering: "研发工程智能体 · 并行取证",
  };

  function chatStore(ctx) {
    const st = ctx.state.screenState;
    // askOpen（协同问数轮次收缩，参考案例检索模块）：null=自动（只有最新一轮展开）；
    // 字符串=显式展开的轮次键（用户点开某轮摘要条）；"none"=全部收缩。新提问重置为 null。
    if (!st.chat) st.chat = { messages: [], seq: 0, askOpen: null };
    return st.chat;
  }

  /* ---------- 岗位 AI 同事（D1/R2：colleague.yaml 前端消费） ---------- */

  /** 同事下拉选项（目录来自 /api/v2/colleagues；离线/失败给诚实禁用项）。 */
  function colleagueOptionsHtml(ctx) {
    const UI = ctx.UI;
    const list = (ctx.state && ctx.state.colleagues) || [];
    const cur = (ctx.currentColleague() || {}).role || "";
    if (!list.length) {
      return `<option value="">${ctx.api.online() ? "同事目录加载中…" : "同事目录未加载（后端离线）"}</option>`;
    }
    return list.map((c) =>
      `<option value="${UI.esc(c.role)}" ${c.role === cur ? "selected" : ""}>${UI.esc(c.name)}</option>`).join("");
  }

  /** 同事问候行：名字 + 人设 + 擅长场景（没有同事配置时不硬造）。 */
  function colleagueIntroHtml(ctx) {
    const UI = ctx.UI;
    const c = ctx.currentColleague();
    if (!c) {
      return ctx.api.online()
        ? ""
        : `<div class="colleague-intro">${UI.demoFlag("后端离线 · 同事配置未加载")}<span class="colleague-intro-greet">当前为通用问数模式</span></div>`;
    }
    const scenes = (c.scenarios || []).map((s) =>
      `<span class="chip chip-scene">${UI.esc(s)}</span>`).join("");
    return `<div class="colleague-intro">
      <span class="colleague-intro-name">${UI.icon("cpu", 13)}${UI.esc(c.name)}</span>
      <span class="colleague-intro-greet">${UI.esc(c.greeting || "")}</span>
      ${scenes ? `<span class="colleague-intro-scenes">${scenes}</span>` : ""}
    </div>`;
  }

  /** 在线模式快捷问题：优先当前同事的推荐问题（岗位视角），否则平台默认。 */
  function chatQuickQuestions(ctx) {
    const c = ctx.currentColleague();
    if (c && Array.isArray(c.questions) && c.questions.length) return c.questions;
    return QUICK_QUESTIONS;
  }

  /** 工作台「我的 AI 同事」卡的问题按钮（点击 → 带预填进对话）。 */
  function bindColleagueCard(ctx, root) {
    const UI = ctx.UI;
    root.querySelectorAll("[data-colleague-q]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const q = btn.dataset.colleagueQ;
        if (!q) return;
        writeAskPrefill(q, { module: "dashboard", screen: "", kpi: "", view: "kpi" });
        ctx.navigate("chat");
      });
    });
    const sel = root.querySelector("#dash-colleague-select");
    if (sel) {
      sel.addEventListener("change", () => {
        ctx.setColleague(sel.value);
        UI.toast(`已切换为${(ctx.currentColleague() || {}).name || "同事"}视角`, "info", 1800);
      });
    }
  }

  /* ---------- 审批常用语（按槽位预置，一键填入；降本：意见输入时间 -80%） ---------- */

  const QUICK_COMMENTS = {
    disposition: ["制程参数可补偿，加严检验后放行", "客户已确认规格，同意让步接收", "风险不可接受，按退货处理"],
    restart: ["首件确认合格，同意按建议复线", "参数已复核，暂缓复线", "分批试产，加严巡检"],
    eta: ["按评估时间承诺客户", "加缓冲承诺，降低超期风险", "升级销售总监决策"],
    d8_close: ["纠正措施已验证，同意关闭", "验证不充分，退回补充", "客户未确认，延期关闭"],
    complaint_reply: ["口径与 8D 一致，同意发送", "加严承诺需内部保障后发送"],
    delivery_commit: ["可承诺量已核算，同意承诺", "分批出货先保重点客户", "转加急生产"],
    maintenance_window: ["备件已到位，按计划窗口停机", "先降速运行监控参数", "立即停机排查"],
  };

  function quickCommentChipsHtml(ctx, taskUid, slot) {
    const list = QUICK_COMMENTS[slot] || [];
    if (!list.length) return "";
    const UI = ctx.UI;
    return `<div class="quick-comments">
      ${list.map((c) => `<button type="button" class="chip chip-cmt" data-cmt="${UI.esc(c)}"
        data-target="${UI.esc(taskUid)}">${UI.esc(c)}</button>`).join("")}
    </div>`;
  }

  function bindQuickComments(scopeEl) {
    scopeEl.querySelectorAll("[data-cmt]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const target = document.getElementById(`act-comment-${chip.dataset.target}`)
          || document.getElementById("hitl-comment");
        if (target) {
          target.value = chip.dataset.cmt;
          target.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
    });
  }



  function chatHistory() {
    try { return JSON.parse(localStorage.getItem("chat_history") || "[]"); }
    catch (e) { return []; }
  }

  /** 报告归档（V3.1 竞品方案：报告作为一等对象——生成→归档→列表→详情→导出）。
   *  问数生成的报告自动入报告中心（localStorage，最多 30 份）。 */
  function saveReportArchive(result) {
    try {
      if (!result.report) return;
      const items = JSON.parse(localStorage.getItem("report_archive") || "[]");
      if (items.some((r) => r.run_id === result.run_id)) return;  // 幂等
      items.unshift({
        run_id: result.run_id || "",
        scenario: result.scenario_key || (result.route || {}).scenario_key || "qa 直答",
        title: (result.query || "").slice(0, 40) || "未命名报告",
        strategy: (result.route || {}).strategy || "",
        summary: String(result.report || "").replace(/\s+/g, " ").slice(0, 80),
        content: String(result.report || ""),
        brief: result.internal_brief || null,
        provenance_count: (result.provenance || []).length,
        ts: new Date().toISOString(),
      });
      localStorage.setItem("report_archive", JSON.stringify(items.slice(0, 30)));
    } catch (e) { /* 隐私模式：归档不可用不影响主流程 */ }
  }

  function loadReportArchive() {
    try { return JSON.parse(localStorage.getItem("report_archive") || "[]"); }
    catch (e) { return []; }
  }

  function saveChatHistory(question, result) {
    try {
      const items = chatHistory().filter((h) => h.q !== question);
      items.unshift({
        q: question,
        scenario: result.scenario_key || "",
        summary: String(result.report || "").replace(/\s+/g, " ").slice(0, 60),
        ts: new Date().toISOString(),
      });
      localStorage.setItem("chat_history",
        JSON.stringify(items.slice(0, 20)));
    } catch (e) { /* 隐私模式：历史不可用不影响主流程 */ }
  }

  function render_chat(root, ctx) {
    const UI = ctx.UI;
    const online = ctx.api.online();
    root.innerHTML = `
      <div class="content-inner">
        <div class="chat-screen-head">
          <div class="page-title">${UI.icon("chat", 19)}协同问数</div>
          ${online
            ? ""
            : `<span class="offline-banner" style="padding:5px 11px">${UI.icon("warn", 13)}后端离线——当前为演示模式：对话使用预置演示结果回放（已标注演示）</span>`}
          <div class="head-extra">
            <span style="font-size: calc(12px * var(--font-scale));color:var(--text-3)">问数角色</span>
            ${UI.badge(ctx.currentRole().name, "badge-blue")}
            <span class="colleague-switch" title="切换 AI 同事：换的是对话场景与推荐问题的视角，岗位数据权限不变">
              ${UI.icon("cpu", 13)}
              <span class="colleague-switch-name" id="colleague-name">${UI.esc(ctx.currentColleague() ? ctx.currentColleague().name : "智能总控")}</span>
              <select class="colleague-select" id="colleague-select" aria-label="切换 AI 同事">
                ${colleagueOptionsHtml(ctx)}
              </select>
            </span>
            <button class="btn btn-outline btn-sm" id="chat-clear">${UI.icon("refresh", 12)}清空对话</button>
          </div>
        </div>
        ${colleagueIntroHtml(ctx)}
        <div class="chat-wrap">
          <div class="chat-scroll" id="chat-scroll"></div>
          <div class="chat-inputbar">
            <div class="chat-chips" id="chat-chips">
              ${(online ? chatQuickQuestions(ctx) : DEMO_QUICK_QUESTIONS)
                .map((q) => `<button class="chip" data-q="${UI.esc(q)}">${UI.esc(q)}</button>`).join("")}
            </div>
            <div class="chat-history" id="chat-history">
              <span class="chat-history-label">${UI.icon("clock", 12)}最近问过（点击重问）</span>
              <span id="chat-history-items"></span>
            </div>
            <div class="chat-input-row">
              <textarea class="chat-input" id="chat-input" rows="1" placeholder="请输入制造业协同问题，例如：本月度最需关注什么？"></textarea>
              <button class="chat-send" id="chat-send" title="发送">${UI.icon("send", 17)}</button>
            </div>
            <div class="chat-hint">问数链路：看板 KPI 点「问数」→ 与看板同口径直查　·　开放式提问 → 跨域态势汇总　·　具体问题 → 数据直答 / 跨域取证 / 协同场景（工单 → 人工确认 → 报告）· 结论均可追溯</div>
          </div>
        </div>
      </div>`;

    const scroll = document.getElementById("chat-scroll");
    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");

    renderChatMessages(ctx, scroll);

    // 问数历史（本地保留，可一键重问——客户电话场景的答案可回溯）
    const histBox = document.getElementById("chat-history-items");
    const hist = chatHistory();
    if (histBox) {
      if (!hist.length) {
        histBox.innerHTML = `<span style="color:var(--text-3);font-size: calc(11.5px * var(--font-scale))">暂无（问完自动记录）</span>`;
      } else {
        histBox.innerHTML = hist.slice(0, 6).map((h) =>
          `<button class="chip chip-hist" data-hq="${UI.esc(h.q)}" title="${UI.esc(h.summary || "")}">${UI.esc(h.q.length > 18 ? h.q.slice(0, 18) + "…" : h.q)}</button>`).join("");
        histBox.querySelectorAll("[data-hq]").forEach((btn) => {
          btn.addEventListener("click", () => {
            input.value = btn.dataset.hq;
            doSend();
          });
        });
      }
    }

    document.getElementById("chat-clear").addEventListener("click", () => {
      chatStore(ctx).messages = [];
      chatStore(ctx).askOpen = null;
      renderChatMessages(ctx, scroll);
      ctx.UI.toast("对话已清空", "info", 1500);
    });

    // 同事切换器（D1/R2）：换同事 = 换对话场景与推荐问题视角（岗位权限不变）
    const colSel = document.getElementById("colleague-select");
    if (colSel) {
      colSel.addEventListener("change", () => {
        ctx.setColleague(colSel.value);
        ctx.UI.toast(`已切换为${(ctx.currentColleague() || {}).name || "同事"}视角`, "info", 1800);
      });
    }

    const doSend = () => {
      const text = input.value.trim();
      if (!text) { ctx.UI.toast("请输入问题", "info", 1500); return; }
      input.value = "";
      input.style.height = "auto";
      sendChatQueryRouter(ctx, scroll, text);
    };
    sendBtn.addEventListener("click", doSend);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
    });

    // 其他屏下钻带来的预填问题（KPI 卡/全局搜索）：自动发送一次。
    // ask_ctx 与预填搭对：本轮自动发送携带结构化上下文（用后即清——
    // 用户手动输入的后续问题不继承旧口径，防止跨 KPI 串口径）。
    let prefill = null;
    try { prefill = sessionStorage.getItem("chat_prefill"); } catch (e) { /* 忽略 */ }
    if (prefill) {
      let prefillAskCtx = null;
      try {
        prefillAskCtx = readAskCtx();
        sessionStorage.removeItem("chat_prefill");
        clearAskCtx();
      } catch (e) { /* 忽略 */ }
      sendChatQueryRouter(ctx, scroll, prefill, prefillAskCtx);
    }
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
    });
    document.getElementById("chat-chips").querySelectorAll("[data-q]").forEach((chip) => {
      chip.addEventListener("click", () => { input.value = chip.dataset.q; doSend(); });
    });
  }

  function userCtxFor(ctx) {
    const r = ctx.currentRole();
    // colleague_role：当前 AI 同事的岗位 id（仅用于「岗位案例」可答性兜底，
    // 不参与权限判定——数据范围仍以 roles 为权威）。
    const c = ctx.currentColleague();
    return {
      user_id: "u-" + r.id, dept: r.dept, roles: r.roles.slice(),
      colleague_role: (c && c.role) || "",
    };
  }

  function nearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  }

  function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
  }

  function renderChatMessages(ctx, scroll, opts) {
    const chat = chatStore(ctx);
    if (!chat.messages.length) {
      const who = (ctx.currentColleague() || {}).name || "智能总控";
      scroll.innerHTML = `<div class="empty" style="margin:auto">${ctx.UI.icon("chat", 34)}<div style="margin-top:10px;font-size: calc(13px * var(--font-scale))">向${ctx.UI.esc(who)}提问，或点击上方快捷问题开始</div></div>`;
      return;
    }
    // 轮次划分（收缩功能的前提）：一条 user 消息开启一轮，其后所有 bot 消息归属该轮。
    // 首条 user 之前的散件正常流程不会出现，兜底直渲染不参与收缩。
    const rounds = [];
    let loose = [];
    chat.messages.forEach((m) => {
      if (m.kind === "user") rounds.push({ key: m.id, q: m, items: [] });
      else if (rounds.length) rounds[rounds.length - 1].items.push(m);
      else loose.push(m);
    });
    const processed = processedMap(ctx);
    const roundOpen = (r, i) => {
      // 强制展开优先于一切收缩态：Agent 还在跑的轮、有未处理人工确认的轮——
      // 折叠掉这两类会让用户漏看进行中的链路与待办（收缩只针对已完结的历史）。
      if (r.items.some((m) => m.kind === "flow" && m.status === "running")) return true;
      if (r.items.some((m) => m.kind === "hitl" && !processed[m.taskUid])) return true;
      if (chat.askOpen === "none") return false;
      if (typeof chat.askOpen === "string" && chat.askOpen) return chat.askOpen === r.key;
      return i === rounds.length - 1;   // 默认：只有最新一轮展开
    };
    let folded = 0;
    let html = loose.map((m) => renderChatMessage(ctx, m)).join("");
    html += rounds.map((r, i) => {
      const open = roundOpen(r, i);
      if (!open) folded += 1;
      return renderChatRound(ctx, r, open);
    }).join("");
    if (folded) {
      html = `<div class="chat-fold-hint">上方 ${folded} 轮历史回答已收缩（只留一行摘要），点摘要条可重新展开。</div>` + html;
    }
    scroll.innerHTML = html;
    bindChatActions(ctx, scroll);
    bindQuickComments(scroll);
    // 轮次收缩/展开切换：与案例检索屏同构——点摘要条=展开该轮，再点或点「收起这一轮」=收成摘要。
    // 切换时保持滚动位置（展开的是历史轮，跳到底部会丢失展开内容的视线）。
    scroll.querySelectorAll("[data-turn-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const k = btn.dataset.turnToggle;
        const cur = (chat.askOpen == null || chat.askOpen === "")
          ? (rounds.length ? rounds[rounds.length - 1].key : "") : chat.askOpen;
        chat.askOpen = (cur === k) ? "none" : k;
        const st = scroll.scrollTop;
        renderChatMessages(ctx, scroll, { keepScroll: true });
        scroll.scrollTop = st;
      });
    });
    // 兜底建议：点击"你可能想问"直接重新发问（把兜底从死胡同变成入口）
    scroll.querySelectorAll("[data-sq]").forEach((btn) => {
      btn.addEventListener("click", () => {
        sendChatQueryRouter(ctx, scroll, btn.dataset.sq);
      });
    });
    if (opts && opts.keepScroll) return;
    scrollToBottom(scroll);
  }

  /** 一轮对话：用户气泡始终可见；展开=完整 bot 消息 + 收起按钮，收缩=一行摘要条（点击展开）。 */
  function renderChatRound(ctx, round, open) {
    const UI = ctx.UI;
    let html = renderChatMessage(ctx, round.q);
    if (open) {
      html += round.items.map((m) => renderChatMessage(ctx, m)).join("");
      html += `<div class="chat-fold-row"><button class="chat-fold-toggle" data-turn-toggle="${UI.esc(round.key)}" title="把这一轮回答收成一行摘要">收起这一轮</button></div>`;
    } else {
      html += chatFoldedBarHtml(ctx, round);
    }
    return html;
  }

  /** 收缩态轮次标签：按轮内最后一张回答卡的类型给中文标签。 */
  const ROUND_TAG = {
    flow: "执行中", result: "场景结果", qa: "数据直答", digest: "态势汇总",
    chitchat: "闲聊回应", fallback: "转人工", conclusion: "智能体结论",
    hitl: "待人工确认", error: "执行异常",
  };

  /** 轮摘要：优先最后一张回答卡的 headline/report 首行；无回答卡再看 HITL/错误。 */
  function roundSummaryOf(items) {
    const ANSWER_KINDS = ["result", "qa", "digest", "chitchat", "fallback", "conclusion"];
    let ans = null;
    items.forEach((m) => { if (ANSWER_KINDS.indexOf(m.kind) >= 0) ans = m; });
    if (ans) {
      const r = ans.result || {};
      const head = term("headline", r) || String(r.report || "").split("\n")[0] || "";
      return { kind: ans.kind, main: head };
    }
    const hitl = items.find((m) => m.kind === "hitl");
    if (hitl) return { kind: "hitl", main: hitl.prompt || "" };
    const err = items.find((m) => m.kind === "error");
    if (err) return { kind: "error", main: err.text || "" };
    return { kind: "flow", main: "" };
  }

  function clipLine(text, n) {
    const t = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n) + "…" : t;
  }

  /** 收缩态的一轮回答：与展开态同一份数据，只留一行摘要条（标签 + 摘要），点整条展开。 */
  function chatFoldedBarHtml(ctx, round) {
    const UI = ctx.UI;
    const s = roundSummaryOf(round.items);
    const tag = ROUND_TAG[s.kind] || "回答";
    return `<div class="msg"><div class="msg-avatar agent">${UI.icon("cpu", 15)}</div>`
      + `<button class="chat-foldbar" data-turn-toggle="${UI.esc(round.key)}" title="展开这一轮的完整回答">`
      + `<span class="chat-foldbar-caret">▸</span>`
      + `<span class="chat-foldbar-tag">${UI.esc(tag)}</span>`
      + `<span class="chat-foldbar-main">${UI.esc(clipLine(s.main, 72) || "（本轮无文本结论）")}</span>`
      + `</button></div>`;
  }

  function renderChatMessage(ctx, m) {
    const UI = ctx.UI;
    if (m.kind === "user") {
      return `<div class="msg user" data-msg-id="${m.id}">
        <div class="msg-avatar user">${UI.esc(ctx.currentRole().name.slice(0, 1))}</div>
        <div class="msg-bubble"><div class="msg-text">${UI.esc(m.text)}</div></div>
      </div>`;
    }
    const avatar = `<div class="msg-avatar agent">${UI.icon("cpu", 15)}</div>`;
    let body = "";
    if (m.kind === "flow") body = flowPanelHtml(ctx, m);
    else if (m.kind === "result") body = resultCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "qa") body = qaAnswerCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "digest") body = digestCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "business") body = businessCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "aging") body = agingCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "chitchat") body = chitchatCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "fallback") body = fallbackCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "hitl") body = hitlChatCardHtml(ctx, m);
    else if (m.kind === "conclusion") body = conclusionCardHtml(ctx, m);
    else if (m.kind === "error") body = `<div class="result-card"><div class="result-head"><div class="result-title">${UI.icon("warn", 15)}执行异常</div></div><div class="result-body"><div class="offline-banner">${UI.icon("warn", 14)}<span>${UI.esc(m.text)}</span></div></div></div>`;
    return `<div class="msg" data-msg-id="${m.id}">${avatar}<div class="msg-panel">${body}</div></div>`;
  }

  function bindChatActions(ctx, scroll) {
    // 建议动作条（A4）：确认类按钮滚动定位到本轮的确认卡并高亮（不自动放行）
    scroll.querySelectorAll("[data-scroll-hitl]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = scroll.querySelector(`[data-hitl-task="${btn.dataset.scrollHitl}"]`);
        if (!target) { ctx.navigate("hitl"); return; }
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.classList.add("hitl-flash");
        setTimeout(() => target.classList.remove("hitl-flash"), 1800);
      });
    });
    // 工单类按钮：跳审批确认中心
    scroll.querySelectorAll("[data-act-goto]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.navigate(btn.dataset.actGoto));
    });
    scroll.querySelectorAll("[data-hitl-task]").forEach((card) => {
      const taskUid = card.dataset.hitlTask;
      wireActionChips(card, ctx, taskUid);
      wireCosign(card, ctx, taskUid, _hitlActions[taskUid] || []);
    });
    scroll.querySelectorAll("[data-approve]").forEach((btn) => {
      btn.addEventListener("click", () => hitlAction(ctx, btn.dataset.approve, "approve"));
    });
    scroll.querySelectorAll("[data-reject]").forEach((btn) => {
      btn.addEventListener("click", () => hitlAction(ctx, btn.dataset.reject, "reject"));
    });
  }

  /* ---------- 发送：真实链路 ---------- */

  async function sendChatQuery(ctx, scroll, text, askCtx) {
    const UI = ctx.UI;
    const chat = chatStore(ctx);
    chat.askOpen = null;   // 新提问 → 上方历史一律收缩，只展开这一轮（与案例检索屏同构）
    chat.messages.push({ kind: "user", id: "m" + ++chat.seq, text });
    const flow = { kind: "flow", id: "m" + ++chat.seq, status: "running", order: [], nodes: {}, current: null };
    chat.messages.push(flow);
    renderChatMessages(ctx, scroll);
    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");
    if (sendBtn) sendBtn.disabled = true;
    if (input) input.disabled = true;

    const requestId = "req-" + Math.random().toString(16).slice(2, 12);
    let es = null;
    let failed = false;

    try {
      await ctx.api.openEventQueue(requestId);
    } catch (err) {
      console.warn("[chat] 事件队列预开失败（查询端点仍会自建）:", err);
    }
    try {
      es = ctx.api.subscribeEvents(requestId, (event) => {
        if (ctx.state.screen !== "chat") return;
        handleFlowEvent(flow, event);
        const msgEl = scroll.querySelector(`[data-msg-id="${flow.id}"]`);
        if (msgEl) msgEl.outerHTML = renderChatMessage(ctx, flow);
        if (nearBottom(scroll)) scrollToBottom(scroll);
      });
    } catch (err) {
      failed = true;
    }

    let result = null;
    try {
      result = await ctx.api.query(text, requestId, userCtxFor(ctx), askCtx);
    } catch (err) {
      // 错误文案走统一友好层：abort（超时） / 网络不可达 / 后端 5xx 各有人话，
      // 不再把 "signal is aborted without reason" 这类 DOMException 原文甩给用户。
      console.warn("[chat] 问数请求失败:", err);
      const timedOut = err && (err.name === "AbortError"
        || /abort|timeout/i.test(String(err.message || "")));
      const text2 = timedOut
        ? "问数超时：该问题走了重型推理链路（跨域协同 / 根因分析 / 报告生成），"
          + "后端仍在计算。请稍后重试，或把问题问得更聚焦一些（如指定线体 / 型号 / 批次）。"
        : `问数未完成：${UI.friendlyError(err).text}`;
      chat.messages.push({ kind: "error", id: "m" + ++chat.seq, text: text2 });
      flow.status = "error";
      renderChatMessages(ctx, scroll);
      if (es) es.close();
      if (sendBtn) sendBtn.disabled = false;
      if (input) input.disabled = false;
      return;
    }

    // 查询已返回（全部事件已入队）；稍候关闭 SSE，让尾部事件完成渲染
    setTimeout(() => { if (es) { es.close(); es = null; } }, 600);

    finalizeFlow(ctx, flow, result);
    saveChatHistory(result.query || (chat.messages[0] || {}).text || "", result);
    saveReportArchive(result);
    chat.messages.push(resultCardMessage(ctx, result));
    (result.hitl || []).forEach((h) => {
      chat.messages.push({
        kind: "hitl", id: "m" + ++chat.seq, taskUid: h.task_uid || "",
        prompt: h.prompt || "", approvers: h.approver_roles || [],
        actions: (h.actions || []).map((a) => (typeof a === "string" ? { key: a, label: a } : a)),
        slot: h.slot || h.kind || "",
        governance: h.governance || null,
        signoffStatus: h.signoff_status || null,
        colleague: (ctx.currentColleague() || {}).name || "",
        runId: result.run_id, demo: false,
      });
    });
    renderChatMessages(ctx, scroll);
    if (sendBtn) sendBtn.disabled = false;
    if (input) { input.disabled = false; input.focus(); }
    if (failed) UI.toast("事件流订阅失败，结果仍来自真实查询", "info");
  }

  /* ---------- 发送：演示模式回放 ---------- */

  async function sendDemoQuery(ctx, scroll, text) {
    const UI = ctx.UI;
    const chat = chatStore(ctx);
    chat.askOpen = null;   // 新提问 → 上方历史一律收缩，只展开这一轮（与在线路径同构）
    chat.messages.push({ kind: "user", id: "m" + ++chat.seq, text });
    const flow = { kind: "flow", id: "m" + ++chat.seq, status: "running", order: [], nodes: {}, current: null };
    chat.messages.push(flow);
    renderChatMessages(ctx, scroll);
    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");
    if (sendBtn) sendBtn.disabled = true;
    if (input) input.disabled = true;

    const demos = window.DEMO.CHAT_DEMOS || {};
    // H-2：仅按问题**精确匹配**已收录回放；未收录必须走"需在线模式"明确提示分支，
    // 绝不静默回退到 line_stop（否则问题与结论不匹配 = 演示在说谎）。
    const key = String(text == null ? "" : text).trim();
    const demo = demos[text] || demos[key];
    if (!demo) {
      chat.messages.push({
        kind: "error", id: "m" + ++chat.seq,
        text: `后端离线：该问题未收录离线回放（当前覆盖 ${DEMO_QUICK_QUESTIONS.length} 个快捷问题）。请启用在线模式（起后端服务），或点击上方 ${DEMO_QUICK_QUESTIONS.length} 个快捷问题之一。`,
      });
      flow.status = "error";
      renderChatMessages(ctx, scroll);
      if (sendBtn) sendBtn.disabled = false;
      if (input) { input.disabled = false; input.focus(); }
      return;
    }
    for (const ev of demo.events) {
      await new Promise((r) => setTimeout(r, 160 + Math.random() * 240));
      if (ctx.state.screen !== "chat") break;
      const msgEl = scroll.querySelector(`[data-msg-id="${flow.id}"]`);
      if (!msgEl) break; // 对话已清空
      handleFlowEvent(flow, ev);
      msgEl.outerHTML = renderChatMessage(ctx, flow);
      if (nearBottom(scroll)) scrollToBottom(scroll);
    }

    finalizeFlow(ctx, flow, demo.result);
    flow.status = "done";
    chat.messages.push(resultCardMessage(ctx, demo.result, true));
    (demo.result.hitl || []).forEach((h) => {
      // 字段必须与在线分支（sendChatQuery）完全一致：离线/在线只差数据来源，
      // 卡片形态一致。2026-10-22 修：原先漏了 slot/signoffStatus/governance，
      // 导致 R3 闸门理由与会签缺口在最常用的演示路径上整块不渲染。
      chat.messages.push({
        kind: "hitl", id: "m" + ++chat.seq, taskUid: h.task_uid || "",
        prompt: h.prompt || "", approvers: h.approver_roles || [],
        actions: (h.actions || []).map((a) => (typeof a === "string" ? { key: a, label: a } : a)),
        slot: h.slot || h.kind || "",
        governance: h.governance || null,
        signoffStatus: h.signoff_status || null,
        colleague: (ctx.currentColleague() || {}).name || "",
        runId: demo.result.run_id, demo: true,
      });
    });
    if (ctx.state.screen === "chat") renderChatMessages(ctx, scroll);
    if (sendBtn) sendBtn.disabled = false;
    if (input) { input.disabled = false; input.focus(); }
  }

  /** 统一发送入口：在线走真实链路，离线走演示回放（回放不携带 ask_ctx） */
  function sendChatQueryRouter(ctx, scroll, text, askCtx) {
    if (ctx.api.online()) return sendChatQuery(ctx, scroll, text, askCtx);
    return sendDemoQuery(ctx, scroll, text);
  }

  /* ---------- 事件流 → 节点流水 ---------- */

  function handleFlowEvent(flow, event) {
    const t = event.type;
    if (t === "agent_start") {
      const id = event.node || "node-" + Math.random().toString(36).slice(2, 7);
      if (!flow.nodes[id]) {
        flow.nodes[id] = {
          id, label: NODE_LABELS[id] || event.label || id,
          group: PARALLEL_NODES.has(id) ? "parallel" : "seq",
          status: "running", elapsed: null, summary: "", tools: [],
        };
        flow.order.push(id);
      }
      flow.current = id;
    } else if (t === "tool_start" || t === "tool_end" || t === "llm_start") {
      const nodeId = flow.current || flow.order[flow.order.length - 1];
      if (nodeId && flow.nodes[nodeId]) {
        flow.nodes[nodeId].tools.push({
          name: event.tool || (t === "llm_start" ? "分析服务" : "工具"),
          text: event.detail || "",
        });
      }
    } else if (t === "fan_in") {
      const id = event.node || "fanin";
      if (!flow.nodes[id]) {
        flow.nodes[id] = { id, label: "结果汇聚 · Fan-in", group: "seq", status: "ok", elapsed: null, summary: "", tools: [] };
        flow.order.push(id);
      }
      flow.nodes[id].summary = event.summary || flow.nodes[id].summary;
    } else if (t === "agent_end") {
      const id = event.node;
      if (id && flow.nodes[id]) {
        flow.nodes[id].status = event.status || "ok";
        if (typeof event.elapsed_ms === "number") flow.nodes[id].elapsed = event.elapsed_ms;
        if (event.summary) flow.nodes[id].summary = event.summary;
      }
    } else if (t === "error") {
      flow.status = "error";
      flow.error = event.detail || event.summary || "执行异常";
    }
  }

  function finalizeFlow(ctx, flow, result) {
    flow.status = "done";
    // 缓存命中：查询直接回放、无进度事件 → 面板按"结果复用"呈现，避免误标"直答"
    flow.cached = !!(result && result.cached);
    // 策略留痕：无流程节点时面板文案要按真实策略区分（chitchat / digest / office / qa），
    // 否则"问候卡旁边写着数据直答"这类自相矛盾会再出现（截图反馈）。
    flow.strategy = ((result && result.route) || {}).strategy || "";
    flow.fallback = (result && result.fallback) || "";   // manual_ticket 时同界面文案也要区分
    const timings = (result && result.timings) || {};
    flow.order.forEach((id) => {
      const node = flow.nodes[id];
      if (node.status === "running") {
        node.status = timings[id] !== undefined ? "ok" : "ok";
        if (timings[id] !== undefined) node.elapsed = timings[id];
      }
    });
  }

  function flowPanelHtml(ctx, flow) {
    const UI = ctx.UI;
    const running = flow.order.filter((id) => flow.nodes[id].status === "running").length;
    // 无流程节点 ≠ 都是"数据直答"：chitchat（问候/引导）、digest（跨域汇总）、
    // office（办公）同样不经场景编排。此前一律标"直答完成/本问题为数据直答"，
    // 于是出现"执行过程写着数据直答、结果卡却是打个招呼"的自相矛盾（截图反馈）。
    const doneLabel = flow.cached ? "结果复用（缓存）"
      : flow.fallback === "manual_ticket" ? "已转人工跟进（未走场景链路）"
        : flow.strategy === "chitchat" ? "轻回应完成（不查数据 / 不建工单）"
          : flow.strategy === "digest" ? "汇总完成（不经场景节点）"
            : flow.strategy === "office" ? "办公办理完成"
              : "直答完成（不经场景节点）";
    let stateHtml;
    if (flow.status === "done") stateHtml = flow.order.length
      ? `${UI.icon("check", 12)} 已完成 · ${flow.order.length} 节点`
      : `${UI.icon("check", 12)} ${doneLabel}`;
    else if (flow.status === "error") stateHtml = `${UI.icon("warn", 12)} 执行异常`;
    else stateHtml = `${UI.spinner()} 执行中（${running} 个节点运行中）`;
    return `<div class="flow-panel">
      <div class="flow-head">${UI.icon("sparkle", 14)} 执行过程<span class="flow-state">${stateHtml}</span></div>
      <div class="flow-body">${flowBodyHtml(ctx, flow)}</div>
    </div>`;
  }

  function flowBodyHtml(ctx, flow) {
    let html = "";
    if (!flow.order.length) {
      if (flow.status !== "done") return `<div class="flow-empty">等待响应…</div>`;
      const text = flow.fallback === "manual_ticket"
        ? "该问题确定性链路未能作答：已转人工兜底跟进（见下方结果卡），未臆造结论。"
        : {
          chitchat: "语义未匹配到业务域：按轻回应 + 领域引导作答（见下方），未查数据、未建工单。",
          digest: "跨域态势汇总：不经场景编排，按各看板口径汇总未达标项（见下方结果卡）。",
          office: "办公事务办理：不经场景编排，由办公助手直接调用工具办理（见下方结果卡）。",
        }[flow.strategy] || "本问题为数据直答：未经场景编排，直接查询数据并返回（见下方结果卡）。";
      return `<div class="flow-empty">${text}</div>`;
    }
    let i = 0;
    while (i < flow.order.length) {
      const node = flow.nodes[flow.order[i]];
      if (node.group === "parallel") {
        const cols = [];
        let j = i;
        while (j < flow.order.length && flow.nodes[flow.order[j]].group === "parallel") {
          cols.push(flowNodeHtml(ctx, flow.nodes[flow.order[j]]));
          j++;
        }
        html += `<div class="flow-parallel-group"><div class="flow-parallel-cols">${cols.join("")}</div></div>`;
        i = j;
      } else {
        html += flowNodeHtml(ctx, node);
        i++;
      }
    }
    if (!html) html = `<div style="font-size: calc(12.5px * var(--font-scale));color:var(--text-3)">等待节点启动…</div>`;
    return html;
  }

  function flowNodeHtml(ctx, node) {
    const UI = ctx.UI;
    const statusCls = node.status === "running" ? "running" : node.status === "degraded" ? "degraded" : "ok";
    const dot = node.status === "running" ? "" : UI.icon("check", 9);
    const elapsed = node.elapsed != null ? `${Math.round(node.elapsed)}ms` : "";
    const degraded = node.status === "degraded" ? UI.badge("降级", "badge-amber") : "";
    const tools = node.tools.map((t) => {
      let cls = "";
      const n = t.name || "";
      if (n.indexOf("SQL") >= 0) cls = "sql";
      else if (n.indexOf("RAG") >= 0) cls = "rag";
      else if (n.indexOf("分析服务") >= 0) cls = "llm";
      return `<div class="flow-tool"><span class="flow-tool-tag ${cls}">${UI.esc(t.name)}</span><span>${UI.esc(t.text)}</span></div>`;
    }).join("");
    const summary = node.summary ? `<div class="flow-node-summary">${UI.esc(node.summary)}</div>` : "";
    return `<div class="flow-node ${statusCls}" data-node="${UI.esc(node.id)}">`
      + `<div class="flow-node-dot">${dot}</div>`
      + `<div class="flow-node-line"><span class="flow-node-label">${UI.esc(node.label)}</span>${degraded}<span class="flow-node-elapsed">${elapsed}</span></div>`
      + summary + tools + `</div>`;
  }

  /* ---------- 结果卡片 / 兜底卡片 ---------- */

  function resultCardMessage(ctx, result, demo) {
    const chat = chatStore(ctx);
    // 四策略分流（V4.1）：qa=数据直答卡 / digest=态势汇总卡 / fallback=人工兜底卡 /
    // 其余=场景结果卡。
    // 注意：qa 直答 scenario_key 也是空——不能再按"scenario_key 空=兜底"判定，
    // 否则查到真实数据的直答会被误渲染成"人工兜底"（截图反馈的 Bug）。
    if (result && result.route && result.route.strategy === "chitchat") {
      return { kind: "chitchat", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    if (result && result.route && result.route.strategy === "digest"
        && result.fallback !== "manual_ticket") {
      return { kind: "digest", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    if (result && result.route && result.route.strategy === "aging"
        && result.fallback !== "manual_ticket") {
      return { kind: "aging", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    if (result && result.route && result.route.strategy === "business"
        && result.fallback !== "manual_ticket") {
      return { kind: "business", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    if (result && result.route && result.route.strategy === "qa"
        && result.fallback !== "manual_ticket") {
      return { kind: "qa", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    // agentic / collab 结果无 scenario_key——旧逻辑会掉进下面的人工兜底卡，
    // 「AI 同事答了话、界面却写着转人工」是截图级误导。改走同事结论卡（D2）。
    // R7 追加 trace（批次追溯确定性成稿）：同样是"AI 跑完给出结论"，走结论卡；
    // 否则 scenario_key 为空会掉进人工兜底卡（与 D2 修的是同一个错标）。
    if (result && result.route
        && (result.route.strategy === "agentic" || result.route.strategy === "collab"
            || result.route.strategy === "role_case"
            || result.route.strategy === "trace")
        && result.fallback !== "manual_ticket") {
      return { kind: "conclusion", id: "m" + ++chat.seq, result, demo: !!demo,
               colleague: (ctx.currentColleague() || {}).name || "" };
    }
    if (result && (result.fallback === "manual_ticket" || !result.scenario_key)) {
      return { kind: "fallback", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    return { kind: "result", id: "m" + ++chat.seq, result, demo: !!demo };
  }

  /** 闲聊卡（chitchat 策略）：限长轻回应 + 引导回领域问题，不建工单。 */
  function chitchatCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const suggestions = result.suggestions || [];
    return `<div class="result-card chitchat-card">
      <div class="result-head">
        <div class="result-title">${UI.icon("chat", 15)}打个招呼</div>
        <div class="badges">${demo ? UI.demoFlag("演示回放") : ""}</div>
      </div>
      <div class="result-body">
        <div style="font-size: calc(13px * var(--font-scale));line-height:1.9">${UI.esc(result.report || "")}</div>
        ${suggestions.length ? `<div class="act-chips" style="margin-top:10px">${suggestions.map((sg) =>
          `<button type="button" class="chip chip-cmt" data-sq="${UI.esc(sg.example)}">${UI.esc(sg.name)}：${UI.esc(sg.example)}</button>`).join("")}</div>` : ""}
      </div>
    </div>`;
  }

  /** 态势汇总卡（digest 策略）：回答"本月度最需关注什么"这类决策支持型提问。
   *
   *  与数据直答卡的区别：这里呈现的是**跨域"未达标"清单**（各看板口径同源）+
   *  能力不足/S 待办/不可见屏与缺口，按严重度排序；不是单一查询的明细表。
   */
  function digestCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const brief = result.internal_brief || {};
    const prov = (result.provenance || [])[0] || {};
    const items = prov.items || [];
    const scope = brief["口径"] || "";
    const rowsHtml = items.map((it, i) => {
      const sev = it.status === "danger" ? "danger" : "warn";
      const gap = it.gap ? `缺口 ${Math.abs(it.gap)}pt` : "";
      return `<div class="digest-row ${sev}">
        <span class="digest-no">${i + 1}</span>
        <span class="digest-badge ${sev}">${sev === "danger" ? "严重" : "关注"}</span>
        <span class="digest-main">
          <b>${UI.esc(it.board)} · ${UI.esc(it.label)}</b>
          <span class="digest-num">${UI.esc(it.value)}</span>
          <span class="digest-meta">目标 ${UI.esc(it.target)}${gap ? " · " + UI.esc(gap) : ""}${it.note ? " · " + UI.esc(it.note) : ""}</span>
        </span>
        <button type="button" class="row-ask" data-sq="${UI.esc(it.board + " " + it.label + " " + scope + "为什么没达标？")}"
          title="就这一项继续问数">${UI.icon("chat", 11)}问数</button>
      </div>`;
    }).join("");
    return `<div class="result-card digest-card">
      <div class="result-head">
        <div class="result-title">${UI.icon("grid", 15)}态势汇总 · 最需关注</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : ""}
          ${scope ? UI.badge(scope, "badge-outline") : ""}
          ${UI.badge(`覆盖 ${UI.esc(brief["覆盖看板"] || "")}`, "badge-blue")}
        </div>
      </div>
      <div class="result-body">
        ${rowsHtml || `<div class="empty" style="padding:14px 0">按当前口径各看板指标均已达标，暂无明显需要升级的项。</div>`}
        <div class="digest-foot">${UI.esc((result.report || "").split("\n")
          .filter((l) => l.startsWith("·")).join("　"))}</div>
        <div style="margin-top:8px;font-size: calc(11.3px * var(--font-scale));color:var(--text-3)">
          汇总口径与各看板同源（未达标项 = 各看板 KPI 判定） · 只读汇总，不产生工单
        </div>
        ${evidenceHtml(ctx, result, true)}
      </div>
    </div>`;
  }

  /** 老板经营 · 大单进度卡（business 策略）。
   *
   *  一眼可见三件事：整体加权完成、选中大单的进度、主动风险。数字全部来自
   *  注册查询（后端确定性成稿），前端只负责画进度条与列风险，不再二次计算。 */
  function businessCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const brief = result.internal_brief || {};
    const bo = brief.big_order || {};
    const sel = bo.selected || {};
    const clamp = (v) => Math.max(0, Math.min(100, Number(v) || 0));
    const agg = clamp(bo.aggregate_progress);
    const cur = clamp(sel.progress);
    const risks = (bo.risks || []).map((r, i) =>
      `<div class="biz-risk"><span class="biz-risk-no">${i + 1}</span><span>${UI.esc(r)}</span></div>`
    ).join("");
    const finish = bo.finish_date
      ? `<b>${UI.esc(bo.finish_date)}</b>`
      + (bo.slack_days == null ? ""
        : bo.slack_days >= 0
          ? `（比要求日提前 ${bo.slack_days} 天）`
          : `（比要求日晚 ${Math.abs(bo.slack_days)} 天）`)
      : "无法估算（缺产能数据）";
    const gaps = result.degraded_reasons || [];
    return `<div class="result-card biz-card">
      <div class="result-head">
        <div class="result-title">${UI.icon("grid", 15)}老板经营 · 大单进度</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : UI.badge("实时链路", "badge-green")}
          ${UI.badge(`口径 ≥${UI.esc(bo.threshold || 5000)} 件在制`, "badge-outline")}
          ${bo.as_of ? UI.badge(`截至 ${UI.esc(bo.as_of)}`, "badge-gray") : ""}
        </div>
      </div>
      <div class="result-body">
        <div class="biz-summary">
          <div class="biz-summary-top">
            <span>在制大单 <b>${UI.esc(brief["在制大单"] || "0")}</b> 张 · 加权完成
              <b>${UI.esc(bo.aggregate_progress || 0)}%</b></span>
            <span class="biz-summary-meta">合计 ${UI.esc(bo.total_qty || 0)} 件 ·
              产能 ${UI.esc(bo.daily_capacity || 0)} 套/天</span>
          </div>
          <div class="biz-bar biz-bar-lg" role="progressbar" aria-valuenow="${agg}"
               aria-valuemin="0" aria-valuemax="100"><span style="width:${agg}%"></span></div>
        </div>
        ${sel.order_no ? `<div class="biz-focus">
          <div class="biz-focus-head">
            <b>${UI.esc(sel.order_no)}</b>
            <span>${UI.esc(sel.product_model || "—")} · ${UI.esc(sel.qty || 0)} 件</span>
            <span class="biz-stage">${UI.esc(sel.stage || "—")}</span>
          </div>
          <div class="biz-focus-meta">客户要求 ${UI.esc(sel.due_date || "—")} 交付 ·
            当前进度 <b>${UI.esc(sel.progress || 0)}%</b></div>
          <div class="biz-bar" role="progressbar" aria-valuenow="${cur}"
               aria-valuemin="0" aria-valuemax="100"><span style="width:${cur}%"></span></div>
          <div class="biz-finish">预计完工：${finish}
            <span class="biz-finish-note">（按该型号近 7 日实际产出均值推算，全产线合计、未按订单分配）</span></div>
        </div>` : `<div class="empty" style="padding:12px 0">当前没有符合条件的在制大单。</div>`}
        ${risks ? `<div class="biz-risks-title">${UI.icon("warn", 13)}主动风险（不等你问）</div>
          ${risks}` : ""}
        ${gaps.length ? `<div class="offline-banner" style="margin-top:10px">${UI.icon("warn", 14)}
          <span>数据缺口：${UI.esc(gaps.join("；"))}（不编造，已如实标注）</span></div>` : ""}
        ${sel.order_no ? `<div style="margin-top:9px">
          <button class="link-btn" type="button"
            data-sq="${UI.esc(`${sel.order_no} 的交付风险与 ATP 可承诺量如何？`)}">
            ${UI.icon("chat", 11)}就这张单继续问数</button></div>` : ""}
        ${evidenceHtml(ctx, result, true)}
      </div>
    </div>`;
  }

  /** 呆滞料盘点卡（aging 策略，R11）。
   *
   *  先给结论（条数/资金占用/最长库龄），再给供应商分布与 TOP 清单；
   *  退供按治理铁律走人工确认（interim 复用 disposition），卡片只提供发起入口。 */
  function agingCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const brief = result.internal_brief || {};
    const ag = brief.aging || {};
    const rows = (ag.rows || []).slice(0, 10);
    const suppliers = (ag.suppliers || []).map((s) =>
      `<span class="ag-supplier">${UI.esc(s.supplier)} · ${UI.esc(s.count)} 条 ·
        ¥${UI.esc(Number(s.value || 0).toLocaleString())}</span>`).join("");
    const table = rows.length ? `<div class="ag-table-wrap"><table class="std-table ag-table">
      <thead><tr><th>料号</th><th>名称</th><th>供应商</th><th>在库</th><th>库龄</th><th>金额</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td><b>${UI.esc(r.part_no)}</b></td><td>${UI.esc(r.part_name || "")}</td>
        <td>${UI.esc(r.supplier || "—")}</td>
        <td>${UI.esc(Number(r.on_hand_qty || 0).toLocaleString())}</td>
        <td>${UI.esc(r.age_days)} 天</td>
        <td>¥${UI.esc(Number((Number(r.on_hand_qty) || 0) * (Number(r.unit_cost) || 0)).toLocaleString())}</td>
      </tr>`).join("")}</tbody></table></div>
      ${(ag.rows || []).length > 10 ? `<div class="ag-more">仅列库龄最长的 10 条，共 ${UI.esc((ag.rows || []).length)} 条</div>` : ""}`
      : `<div class="empty" style="padding:12px 0">当前没有超过该库龄阈值的物料。</div>`;
    const returnParts = (ag.return_candidates || []).map((r) => r.part_no).filter(Boolean);
    const returnJson = UI.esc(JSON.stringify(returnParts));
    const gaps = ag.data_gaps || [];
    return `<div class="result-card ag-card">
      <div class="result-head">
        <div class="result-title">${UI.icon("database", 15)}呆滞料盘点 · 库龄 ≥ ${UI.esc(ag.days || brief["口径"] || 180)} 天</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : UI.badge("实时链路", "badge-green")}
          ${UI.badge(`${UI.esc((ag.rows || []).length)} 条超龄`, "badge-amber")}
        </div>
      </div>
      <div class="result-body">
        <div class="ag-summary">
          <div class="ag-stat"><b>${UI.esc((ag.rows || []).length)}</b><span>超龄料号</span></div>
          <div class="ag-stat"><b>¥${UI.esc(Number(ag.total_value || 0).toLocaleString())}</b><span>资金占用</span></div>
          <div class="ag-stat"><b>${UI.esc(ag.oldest_days || 0)} 天</b><span>最长库龄</span></div>
          <div class="ag-stat"><b>${UI.esc((ag.suppliers || []).length)}</b><span>涉及供应商</span></div>
        </div>
        ${suppliers ? `<div class="ag-suppliers">${suppliers}</div>` : ""}
        ${table}
        <div class="ag-policy">
          <b>处置口径</b>：转用优先 → 退供其次 → 报废兜底；评审通过前冻结补货。
          可转用料号因平台缺 BOM/替代关系表，<b>不做自动识别</b>（不编造）。
        </div>
        ${gaps.length ? `<div class="offline-banner" style="margin-top:8px">${UI.icon("warn", 14)}
          <span>数据缺口：${UI.esc(gaps.join("；"))}</span></div>` : ""}
        <div class="ag-actions">
          <button class="btn btn-primary" type="button"
            data-ag-return="1" data-days="${UI.esc(ag.days || 180)}"
            data-part-nos="${returnJson}"
            ${returnParts.length ? "" : "disabled"}
            title="发起退供评审（涉及资金回收/折价，必须人工确认）">
            ${UI.icon("shield", 13)} 发起退供评审（${returnParts.length} 个料号）
          </button>
          <span class="ag-gate-note">${UI.icon("lock", 12)} 动钱需人管：当前无专属槽位，
            interim 复用来料处置（disposition）会签链；Agent 只发起、不放行</span>
        </div>
        ${evidenceHtml(ctx, result, true)}
      </div>
    </div>`;
  }

  /** 数据直答卡（qa 策略）：轻量呈现——结论 + 数据行 + 溯源，无场景节点无工单。 */
  function qaAnswerCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const route = result.route || {};
    const sev = term("severity", result);
    const provs = (result.provenance || []).filter((p) => p && (p.rows || p.row_count));
    const rowsHtml = provs.slice(0, 3).map((p) => {
      const rows = (p.rows || []).slice(0, 4);
      if (!rows.length) return "";
      const cols = Object.keys(rows[0]).slice(0, 6);
      return `<div style="margin-top:9px;overflow:auto">
        <div style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3);margin-bottom:4px">${UI.esc(p.query_name ? term("queryLabel", p.query_name) : "查询结果")}（${p.row_count ?? rows.length} 行）</div>
        <table class="qa-table"><tr>${cols.map((c) => `<th>${UI.esc(c)}</th>`).join("")}</tr>
        ${rows.map((r) => `<tr>${cols.map((c) => `<td>${UI.esc(String(r[c] ?? ""))}</td>`).join("")}</tr>`).join("")}</table>
      </div>`;
    }).join("");
    return `<div class="result-card level-${sev}">
      <div class="result-head">
        <div class="result-title"><span class="status-dot ${sev}"></span>${UI.icon("chat", 15)}数据直答</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : ""}
          ${UI.badge(term("levelName", sev), sev === "alert" ? "badge-red" : sev === "warn" ? "badge-amber" : "badge-green")}
        </div>
      </div>
      <div class="result-body">
        <div class="head-line">${UI.esc(term("headline", result))}</div>
        ${result.report ? `<div style="font-size: calc(13px * var(--font-scale));line-height:1.9;white-space:pre-wrap;margin-top:8px">${UI.esc(result.report)}</div>` : ""}
        ${rowsHtml}
        <div style="margin-top:8px;font-size: calc(11.3px * var(--font-scale));color:var(--text-3)">直答模式：数据查询不经协同场景链路，无工单产生。数据来源：${UI.esc(term("domainLabel", route.domain))}</div>
        ${evidenceHtml(ctx, result, true)}
      </div>
    </div>`;
  }

  /** 8D 结构化渲染（D1-D8 网格排版；D4 根因 / D8 关闭高亮） */
  function d8GridHtml(title, d) {
    const UI = window.App && window.App.UI;
    const esc = UI ? UI.esc : (x) => String(x ?? "");
    const KEYS = [
      ["D1_team", "D1 团队"], ["D2_problem", "D2 问题"], ["D3_containment", "D3 围堵"],
      ["D4_root_cause", "D4 根因"], ["D5_permanent", "D5 纠正"],
      ["D6_validation", "D6 验证"], ["D7_prevent", "D7 预防"], ["D8_closure", "D8 关闭"],
    ];
    const items = KEYS.map(([key, label]) => {
      const val = d[key];
      if (val === undefined || val === null) return "";
      const hot = key === "D4_root_cause" ? " hot" : key === "D8_closure" ? " close" : "";
      return `<div class="d8-item${hot}"><span class="d8-k">${esc(label)}</span><span class="d8-v">${esc(val)}</span></div>`;
    }).join("");
    return `<div class="brief-item d8-block" style="grid-column:1/-1"><span class="k">${esc(title)}</span>
      <div class="d8-grid">${items}</div></div>`;
  }

  /* ---------- 术语映射（ui/terms.js；文件缺失时全部回退原文，不影响可用性） ---------- */

  /** 调用 terms.js 的同名函数；缺失时按语义给安全兜底值（注意 visibleBriefKeys 的键数组是第 2 参） */
  function term(name, a, b) {
    const t = window.TERMS;
    if (t && typeof t[name] === "function") return t[name](a, b);
    switch (name) {
      case "severity": return "normal";
      case "headline": return "";
      case "etaText": return "";
      case "originLabel": return "";
      case "hiddenBriefKeys": return [];
      case "visibleBriefKeys": return Array.isArray(b) ? b : [];
      case "viewLabel": return "通用";
      case "levelName": return a === "alert" ? "紧急" : a === "warn" ? "关注" : "正常";
      case "scenarioName": return a || "未知场景";
      default: return a || "-";
    }
  }

  const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
  function circled(i) { return CIRCLED[i] || "(" + (i + 1) + ")"; }

  function resultCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const brief = result.internal_brief || {};
    const degraded = !!result.degraded;
    const sev = term("severity", result);
    const faText = brief["FA评级"] || brief["风险评级"] || "";
    const ratingMatch = /评级\s*([A-D])/.exec(faText) || /([A-D])\s*级/.exec(faText);
    const eta = term("etaText", result);
    // data_gap：无数据源的项（不编造）——前端必须显式渲染（架构纪律）
    const gapNotes = Array.from(new Set(
      (result.provenance || []).filter((p) => p.channel === "data_gap")
        .map((p) => p.note).filter(Boolean)));

    // 结论网格：键序 = 后端 `_BRIEF_SPEC` 插入序（同类问题同结构）；按角色分层展示
    const allKeys = Object.keys(brief).filter((k) => !k.endsWith("#structured"));
    const roleId = (ctx.currentRole() || {}).id;
    const primaryKeys = term("visibleBriefKeys", roleId, allKeys);
    const foldedKeys = term("hiddenBriefKeys", roleId, allKeys);

    const briefItem = (k) => {
      const raw = brief[k];
      const label = term("briefLabel", k);
      const v = UI.esc(raw === true ? "是" : raw === false ? "否" : raw || "—");
      const extra = ((k === "FA评级" || k === "风险评级") && ratingMatch)
        ? ` <span class="rating-pill rating-${ratingMatch[1]}">${ratingMatch[1]} 级</span>` : "";
      return `<div class="brief-item"><span class="k">${UI.esc(label)}</span><span class="v">${v}${extra}</span></div>`;
    };

    // 结构化镜像键（label#structured，dict）→ 8D 网格专用排版（不参与角色分层过滤）
    const structuredHtml = Object.keys(brief)
      .filter((k) => k.endsWith("#structured") && brief[k]
        && typeof brief[k] === "object" && !Array.isArray(brief[k]))
      .map((k) => d8GridHtml(term("briefLabel", k.replace(/#structured$/, "")), brief[k])).join("");

    const tickets = (result.workflow_tasks || []).map((w) => {
      const t = w.ticket || {};
      return `<div class="brief-item" style="grid-column:1/-1"><span class="k">工单</span><span class="v">`
        + `<b>${UI.esc(t.task_uid || "-")}</b> [${UI.esc(t.status || "-")}] ${UI.esc(t.title || "")}`
        + ` | 受理：${UI.esc((t.assignees || []).join("、"))}`
        + `${t.sla_due_at ? " | SLA " + UI.fmtDateTime(t.sla_due_at) : ""}`
        + `</span></div>`;
    }).join("");

    const foldedHtml = foldedKeys.length ? `<details class="other-fold">
      <summary>其他角色关注字段（${foldedKeys.length}）<span class="fold-role">${UI.esc(term("viewLabel", roleId))}视图</span></summary>
      <div class="brief-grid" style="margin-top:8px">${foldedKeys.map(briefItem).join("")}</div>
    </details>` : "";

    return `<div class="result-card level-${sev}">
      <div class="result-head">
        <div class="result-title"><span class="status-dot ${sev}"></span>${UI.icon("file", 15)}${UI.esc(term("scenarioName", result.scenario_key))}</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : ""}
          ${UI.badge(term("levelName", sev), sev === "alert" ? "badge-red" : sev === "warn" ? "badge-amber" : "badge-green")}
        </div>
      </div>
      <div class="result-body">
        <div class="head-line">${UI.esc(term("headline", result))}</div>
        ${(degraded || gapNotes.length) ? (() => {
          const reasons = result.degraded_reasons || [];
          const svcDown = reasons.some((r) => /服务|模型|分析/.test(r));
          const dataGap = reasons.some((r) => /数据缺口|数据不完整|缺少数据源/.test(r)) || gapNotes.length > 0;
          const mode = svcDown ? "分析服务未就绪，部分结论采用模板化输出"
            : dataGap ? "部分协同环节缺少数据源，相关结论已标注缺口"
              : "部分环节采用受限输出";
          const detail = [
            reasons.length ? `受限原因：${UI.esc(reasons.join("；"))}` : "",
            gapNotes.length ? `数据缺口：${UI.esc(gapNotes.join("；"))}` : "",
          ].filter(Boolean).join("　·　");
          return `<div class="offline-banner" style="margin-bottom:12px">${UI.icon("warn", 14)}<span>`
            + `<b>${degraded ? "受限模式" : "数据说明"}：${UI.esc(mode)}</b>`
            + `${detail ? "　" + detail : ""}`
            + `。平台不编造数据，相关缺口已转人工/现场确认，全部结论可追溯来源、可人工复核后放行。</span></div>`;
        })() : ""}
        <div class="brief-grid">${primaryKeys.map(briefItem).join("")}${tickets}${structuredHtml}</div>
        ${foldedHtml}
        ${result.report ? `<details class="report-fold"><summary>${UI.icon("report", 13)}完整说明</summary>
          <div class="report-block-body">${UI.esc(result.report)}</div>
        </details>` : ""}
        ${eta ? `<div class="eta-line">${UI.icon("clock", 13)} 预计恢复：<b>${UI.esc(eta)} 小时</b></div>` : ""}
        ${actionBarHtml(ctx, result)}
        ${evidenceHtml(ctx, result)}
      </div>
    </div>`;
  }

  /**
   * 建议动作条（A4）：把"看完之后该怎么办"放到结论正下方，一键直达。
   * 优先级：人工确认（每项一个主按钮，滚动定位到该确认卡并高亮）> 工单（跳审批中心）> 追问建议。
   * 不做自动放行：确认动作仍需在确认卡内由对应岗位点选，符合"关键决策人在环路"。
   */
  function actionBarHtml(ctx, result) {
    const UI = ctx.UI;
    const hitl = result.hitl || [];
    const sug = (result.suggestions || []).slice(0, 3);
    const btns = [];

    hitl.forEach((h) => {
      const uid = h && h.task_uid;
      if (!uid) return;
      const acts = (h.actions || []);
      const label = acts.length ? acts[0].label : "前往确认";
      btns.push(`<button type="button" class="btn btn-primary btn-sm" data-scroll-hitl="${UI.esc(uid)}">`
        + `${UI.icon("approve", 13)}${UI.esc(label)}</button>`);
    });
    (result.workflow_tasks || []).forEach((w) => {
      const t = w.ticket || {};
      if (!t.task_uid) return;
      btns.push(`<button type="button" class="btn btn-sm" data-act-goto="hitl">`
        + `${UI.icon("orders", 13)}查看工单 ${UI.esc(t.task_uid)}</button>`);
    });
    const chips = sug.map((s) => `<button type="button" class="chip chip-cmt" data-sq="${UI.esc(s.example)}">`
      + `${UI.esc(s.name)}</button>`).join("");

    if (!btns.length && !chips) return "";
    const title = hitl.length ? "下一步建议（待你确认）" : "下一步建议";
    return `<div class="action-bar">
      <div class="action-bar-title">${UI.icon("sparkle", 13)}${UI.esc(title)}</div>
      ${btns.length ? `<div class="action-btns">${btns.join("")}</div>` : ""}
      ${chips ? `<div class="act-chips">${chips}</div>` : ""}
    </div>`;
  }

  function fallbackCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const route = result.route || {};
    const items = [
      ["识别结果", term("scenarioName", route.scenario_key) || "未匹配到已覆盖场景"],
      ["判定说明", route.reason || "-"],
    ].map(([k, v]) => `<div class="brief-item"><span class="k">${UI.esc(k)}</span><span class="v">${UI.esc(v)}</span></div>`).join("");
    return `<div class="result-card level-alert">
      <div class="result-head">
        <div class="result-title"><span class="status-dot alert"></span>${UI.icon("shield", 15)}人工兜底工单</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : ""}
          ${UI.badge("待人工跟进", "badge-amber")}
        </div>
      </div>
      <div class="result-body">
        <div class="offline-banner" style="margin-bottom:12px">${UI.icon("info", 14)}<span><b>${UI.esc(route.strategy === 'qa' ? '数据直答未能获取结果（权限或数据源限制），已转人工跟进。' : '当前问题超出平台已覆盖范围（或路由匹配度低），已转人工兜底工单处理。')}</b>人工兜底是平台设计的一部分——确定性流程之外的问题不臆造结论。</span></div>
        ${(result.suggestions || []).length ? `<div class="fallback-suggest">
          <div style="font-size: calc(12.3px * var(--font-scale));font-weight:650;margin-bottom:6px">${UI.icon("sparkle", 13)} 你可能想问（点击直接提问）</div>
          <div class="act-chips">${result.suggestions.map((s) =>
            `<button type="button" class="chip chip-cmt" data-sq="${UI.esc(s.example)}">${UI.esc(s.name)}：${UI.esc(s.example)}</button>`).join("")}</div>
        </div>` : ""}
        <div class="brief-grid">${items}</div>
        ${result.report ? `<div style="margin-top:10px;font-size: calc(12.8px * var(--font-scale));color:var(--text-2);line-height:1.8">${UI.icon("file", 13)} ${UI.esc(result.report)}</div>` : ""}
      </div>
    </div>`;
  }

  /**
   * 同事结论卡（D2/R1：agentic / collab 结果专用渲染）。
   *
   * 治两个错：① 这两类结果没有 scenario_key，旧渲染掉进「人工兜底工单」卡——
   * AI 同事明明答了话，界面却写着转人工（误导性截图级 bug）；② 结论缺
   * 「同事」主体与来源徽标。本卡：同事名领衔 + 结论 + 这一轮做了哪些事
   * （provenance 步骤流水）+ 数据缺口 + 证据折叠 + 建议动作条（待确认入口）。
   */
  function conclusionCardHtml(ctx, m) {
    const UI = ctx.UI;
    const result = m.result || {};
    const demo = !!m.demo;
    const brief = result.internal_brief || {};
    const gaps = result.degraded_reasons || [];
    const steps = (result.provenance || []).filter((p) => p && p.tool);
    const colleague = m.colleague || (ctx.currentColleague() || {}).name || "AI 同事";
    const stepsHtml = steps.length ? `<div class="concl-steps">
      <div class="concl-steps-title">${UI.icon("activity", 12)}这一轮做了</div>
      ${steps.slice(0, 8).map((p, i) => `<div class="concl-step">
        <span class="concl-step-no">${circled(i)}</span>
        <span class="concl-step-tool">${UI.esc(String(p.tool || ""))}</span>
        <span class="concl-step-sum">${UI.esc(String(p.summary || "").slice(0, 90))}</span>
      </div>`).join("")}
    </div>` : "";
    const firstLine = String(result.report || "").split("\n")[0] || "";
    const headLine = term("headline", result) || firstLine;
    // 正文首行常与卡头标题同句（服务端 report 以结论句开篇）——去掉，避免
    // 同一句话在卡里连排两遍（真机截图可见的观感缺陷）。
    const bodyLines = String(result.report || "").split("\n");
    if (bodyLines.length && bodyLines[0].trim() === String(headLine).trim()) {
      bodyLines.shift();
    }
    const bodyText = bodyLines.join("\n").trim();
    return `<div class="result-card conclusion-card">
      <div class="result-head">
        <div class="result-title"><span class="status-dot normal"></span>${UI.icon("cpu", 15)}${UI.esc(colleague)} · 结论</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放")
            : (result.route || {}).strategy === "role_case"
              ? UI.badge("岗位案例 · 已核验口径", "badge-blue")
              : UI.badge("实时链路", "badge-green")}
          ${UI.badge(brief["执行方式"] || "智能体执行", "badge-outline")}
        </div>
      </div>
      <div class="result-body">
        <div class="head-line">${UI.esc(headLine)}</div>
        ${bodyText ? `<div style="font-size: calc(13px * var(--font-scale));line-height:1.9;white-space:pre-wrap;margin-top:8px">${UI.esc(bodyText)}</div>` : ""}
        ${stepsHtml}
        ${gaps.length ? `<div class="offline-banner" style="margin-top:10px">${UI.icon("warn", 14)}<span>数据缺口：${UI.esc(gaps.join("；"))}（不编造，已如实标注）</span></div>` : ""}
        ${evidenceHtml(ctx, result)}
        ${actionBarHtml(ctx, result)}
      </div>
    </div>`;
  }

  /**
   * 分析过程与数据来源（A3）：默认折叠。展开后 = 自然语言步骤时间线 +
   * 数据来源业务口径 + 内层「原始查询语句」技术折叠（SQL 原文 / 表名 / 命中行 /
   * 脱敏标记 / 节点耗时**只**出现在最内层，正文不再暴露）。
   */
  function evidenceHtml(ctx, result, openFold) {
    const UI = ctx.UI;
    const all = result.provenance || [];
    const sqlItems = all.filter((p) => p && p.sql);
    const others = all.filter((p) => p && !p.sql && p.channel !== "node_timing");
    const total = sqlItems.length + others.length;
    if (!total) return "";

    const steps = [];
    sqlItems.forEach((p) => {
      const name = p.query_name ? term("queryLabel", p.query_name) : (term("originLabel", [p]) || "业务数据");
      steps.push(`核对 ${UI.esc(name)}（命中 ${UI.esc(String(p.row_count != null ? p.row_count : 0))} 条）`);
    });
    others.forEach((p) => {
      if (p.channel === "rag") steps.push(`匹配历史案例库 · ${UI.esc(String(p.case_count != null ? p.case_count : 0))} 条相似案例`);
      else if (p.channel === "workflow") steps.push(`生成工单 <b>${UI.esc(p.task_uid || "")}</b>（受理：${UI.esc((p.assignees || []).join("、"))}）`);
      else if (p.channel === "hitl") steps.push(`待人工确认 <b>${UI.esc(p.task_uid || "")}</b>（审批：${UI.esc((p.approver_roles || []).join("、"))}）`);
      else if (p.channel === "compute") steps.push(`平台按规则计算：${UI.esc(p.formula || "")}`);
      else if (p.channel === "template") steps.push(`规则模板输出：${UI.esc(p.note || "模板化结论")}`);
      else if (p.channel === "report") steps.push(`汇总生成内部简报${p.degraded ? "（受限模式）" : ""}`);
      else if (p.channel === "data_gap") steps.push(`缺少数据源：${UI.esc(p.note || "")}`);
    });
    const stepHtml = steps.map((s, i) => `<div class="reason-step"><span class="step-idx">${circled(i)}</span><span>${s}</span></div>`).join("");
    const origin = term("originLabel", sqlItems);

    return `<details class="reason-fold"${openFold ? " open" : ""}>
      <summary>${UI.icon("database", 14)}分析过程与数据来源<span class="prov-count">已核验 ${total} 项 · 耗时 ${UI.esc(String(Math.round((result.elapsed_ms || 0) / 100) / 10))} 秒</span></summary>
      <div class="reason-body">
        <div class="reason-steps">${stepHtml}</div>
        ${origin ? `<div class="reason-origin">${UI.esc(origin)}</div>` : ""}
        ${sqlFoldHtml(ctx, result)}
      </div>
    </details>`;
  }

  /** 内层技术折叠：SQL 原文 / 表名 / 命中行 / 脱敏标记 / 节点耗时 */
  function sqlFoldHtml(ctx, result) {
    const UI = ctx.UI;
    const all = result.provenance || [];
    const sqlItems = all.filter((p) => p && p.sql);
    const timings = all.filter((p) => p && p.channel === "node_timing");
    if (!sqlItems.length && !timings.length) return "";
    const sqlHtml = sqlItems.map((p) => {
      // R8：问数证据里的每个查询/指标都能跳回口径词典同一条目
      const dictId = p.query_name ? `query.${p.query_name}`
        : (p.metric_id ? `metric.${p.metric_id}` : "");
      return `
      <div class="prov-item">
        <div class="prov-item-head">
          ${UI.badge(term("domainLabel", p.domain), "badge-blue")}
          <span class="prov-item-name">${UI.esc(p.query_name || p.metric_id || "-")}</span>
          ${p.masked ? `<span class="mask-flag">${UI.icon("eyeoff", 12)}敏感字段已按角色脱敏</span>` : ""}
          ${dictId ? `<button class="link-btn prov-dict" type="button"
                  data-dict-id="${UI.esc(dictId)}"
                  title="在口径词典中查看该查询的口径定义与引用方">口径</button>` : ""}
        </div>
        <div class="sql-block">${UI.esc(p.sql)}</div>
        <div class="prov-item-meta">
          <span>命中 <b>${UI.esc(String(p.row_count != null ? p.row_count : 0))}</b> 行</span>
          <span>数据表：<b>${UI.esc((p.tables || []).map((t) => term("tableLabel", t)).join("、"))}</b></span>
          <span>脱敏：<b>${p.masked ? "是" : "否"}</b></span>
        </div>
      </div>`;
    }).join("");
    const timingHtml = timings.length
      ? `<div class="prov-item"><div class="prov-item-head">${UI.badge("处理耗时", "badge-gray")}<span style="font-size: calc(12px * var(--font-scale));color:var(--text-3)">${timings.length} 个环节</span></div>
         <div class="chip-row" style="margin-top:7px">${timings.map((t) => `<span class="badge ${t.status === "ok" ? "badge-green" : "badge-amber"}">${UI.esc(t.node || "-")} ${UI.esc(String(t.elapsed_ms != null ? Math.round(t.elapsed_ms) + "ms" : "-"))}</span>`).join("")}</div></div>`
      : "";
    return `<details class="sql-fold">
      <summary>查看原始查询语句（技术人员）<span class="prov-count">${sqlItems.length} 条</span></summary>
      <div class="prov-body">${sqlHtml}${timingHtml}</div>
    </details>`;
  }

  /* ---------- 人工确认 待确认卡片（对话流内） ---------- */

  // 演示岗位（前端登录身份）→ 治理角色 key（与 data/permissions.yaml 的 roles 对齐）。
  // 后端以 user_ctx.roles 为权威，前端此处只用于"预先灰显/提示"，不承担安全职责。
  // G6 会签独立性铁律：一个岗位只允许对应**一个**治理角色。
  // 同一身份持多个会签角色 = 一个人可签多票 = 会签造假入口。
  // 演示三方会签的正确方式：换身份登录（sqe/pqe/qa 已拆分为独立岗位）。
  // 治理角色 key 为**单一数据源**：直接取登录岗位自身的 roles 声明
  //（demo-data.js ROLES[].roles），不再手工维护第二份岗位→角色映射，避免漂移。

  // 场景 YAML / 演示数据里的中文写法 → 治理角色 key（与 permissions.yaml aliases 同步）
  const ROLE_KEY_BY_NAME = {
    sqe: "sqe", pqe: "pqe", qa: "qa",
    "质量总监": "quality_director", "品质总监": "quality_director",
    "qa经理": "qa_manager", "qa主管": "qa_manager",
    "制造现场负责人": "manufacturing", "制造": "manufacturing",
    "制造经理": "manufacturing", "生产": "manufacturing", "生产主管": "manufacturing",
    "设备工程": "equipment", "设备": "equipment",
    "业务": "sales", "业务经理": "sales", "销售总监": "sales", "销售": "sales",
    "计划": "planning",
    "物控": "material_control", "supply_chain": "material_control",
    "仓库": "warehouse", "物流": "warehouse",
    "工程": "rd_engineering", "研发": "rd_engineering",
    "采购": "procurement", "财务": "finance",
    "总经理": "gm", coordinator: "gm",
    quality: "sqe", business: "sales", manufacturing: "manufacturing",
    procurement: "procurement", warehouse: "warehouse",
    rd_engineering: "rd_engineering", planning: "planning",
    material_control: "material_control", finance: "finance",
    // 部门负责人（与 permissions.yaml 新治理角色的 aliases 同步）
    "设备经理": "equipment_manager", "设备总监": "equipment_manager", "设备主管": "equipment_manager",
    "研发经理": "rd_manager", "研发总监": "rd_manager", "工程经理": "rd_manager",
    "供应链经理": "supply_chain_manager", "供应链总监": "supply_chain_manager",
    "计划总监": "supply_chain_manager", scm: "supply_chain_manager",
    "采购总监": "procurement_manager", "采购主管": "procurement_manager", "采购管理": "procurement_manager",
    "仓储经理": "warehouse_manager", "仓库经理": "warehouse_manager",
    "物流经理": "warehouse_manager", "仓储总监": "warehouse_manager",
    "财务总监": "finance_manager", "财务主管": "finance_manager", "成本经理": "finance_manager",
    // P0/P1 新增岗位（与 permissions.yaml 的 aliases 同步）
    ie: "ie_engineer", "工业工程": "ie_engineer", "工业工程师": "ie_engineer",
    npi: "npi_engineer", "npi工程师": "npi_engineer", "导入工程师": "npi_engineer",
    pm: "project_manager", "项目经理": "project_manager", "项目总监": "project_manager",
    te: "test_engineer", "工程测试": "test_engineer", "测试工程师": "test_engineer",
    "可靠性工程师": "test_engineer",
    me: "me_engineer", "结构工程师": "me_engineer", "结构工程": "me_engineer",
    "研发测试": "rd_test_engineer", "软件测试": "rd_test_engineer", "固件测试": "rd_test_engineer",
    hr: "admin_hr", "行政人事": "admin_hr", "人事": "admin_hr", "行政": "admin_hr",
    ehs: "ehs", "安全环保": "ehs", "安全工程师": "ehs", "安全员": "ehs",
    "客服": "customer_service", "客户服务": "customer_service", "售后": "customer_service",
    "维保技师": "maintenance_tech", "设备维保": "maintenance_tech", "机修": "maintenance_tech",
  };

  const ROLE_LABEL = {
    gm: "总经理", quality_director: "质量总监", qa_manager: "QA经理",
    qa: "QA", sqe: "SQE", pqe: "PQE", procurement: "采购", planning: "计划",
    material_control: "物控", manufacturing: "制造", equipment: "设备工程",
    sales: "业务", warehouse: "仓库", rd_engineering: "工程", finance: "财务",
    equipment_manager: "设备经理", rd_manager: "研发经理",
    supply_chain_manager: "供应链经理", procurement_manager: "采购经理",
    warehouse_manager: "仓储经理", finance_manager: "财务经理",
    product_engineering: "产品工程",
    // 研发/工程/项目/职能岗位（P0）
    ie_engineer: "IE 工业工程", npi_engineer: "NPI 导入工程师",
    project_manager: "项目经理", test_engineer: "工程测试工程师",
    me_engineer: "结构工程师", rd_test_engineer: "研发测试工程师",
    admin_hr: "行政人事",
    // 安全环保 / 客户服务 / 设备维保岗位（P1）
    ehs: "EHS 安全环保", customer_service: "客户服务", maintenance_tech: "设备维保技师",
  };

  /** 中文写法/别名 → 治理角色 key（未识别返回 null，与后端一样 fail-closed） */
  function roleKey(name) {
    const k = String(name == null ? "" : name).trim().toLowerCase();
    return ROLE_KEY_BY_NAME[k] || null;
  }

  function roleKeysOf(names) {
    return (names || []).map(roleKey).filter(Boolean);
  }

  /** 当前登录身份对应的治理角色 key 列表（单一数据源：登录岗位自身的 roles 声明） */
  function govRoles(ctx) {
    const r = ctx.currentRole();
    return (r && Array.isArray(r.roles)) ? r.roles.slice() : [];
  }

  /** 当前身份会传给后端的 user_ctx（roles 为权威来源；uid 是一人一签的判定依据） */
  function govUserCtx(ctx) {
    const r = ctx.currentRole();
    return { user_id: r.uid || r.user, dept: r.dept, roles: govRoles(ctx) };
  }

  function roleLabel(key) {
    return ROLE_LABEL[key] || key;
  }

  function roleLabels(keys) {
    return (keys || []).map(roleLabel);
  }

  /** 权限目录（后端 /api/v2/governance/permissions 快照；未加载时返回 null） */
  function permCatalog(ctx) {
    return (ctx && ctx.perms) || null;
  }

  /** 持有某权限的角色中文名（用于"需：质量总监 / QA经理"提示） */
  function rolesWithPerm(ctx, permKey) {
    const cat = permCatalog(ctx);
    if (!cat || !cat.roles) return [];
    return cat.roles.filter((r) => (r.permissions || []).includes(permKey)).map((r) => r.name);
  }

  /** 当前身份是否持有某权限（目录未加载时返回 null = 未知，交由后端裁决） */
  function hasPerm(ctx, permKey) {
    const cat = permCatalog(ctx);
    if (!cat || !cat.roles) return null;
    const mine = govRoles(ctx);
    return cat.roles.some((r) => mine.includes(r.key)
      && (r.permissions || []).includes(permKey));
  }

  /** 工单的 HITL 槽位（restart/eta/disposition/...） */
  function slotOf(task) {
    const c = (task && task.context) || {};
    const gov = (task && task.governance) || {};
    const raw = gov.slot || c.slot || c.kind || "";
    if (raw) return String(raw);
    const wf = String((task && task.wf_id) || "");
    return wf.indexOf("hitl_") === 0 ? wf.slice(5) : "";
  }

  /** 治理链本地视图（优先用后端返回的 signoff_status，其次本地推算） */
  function govOf(taskUid) {
    return _hitlGov[taskUid] || null;
  }

  function signoffOf(taskUid, actions) {
    const gov = govOf(taskUid);
    if (gov && gov.signoff_status) return gov.signoff_status;
    const g = (gov && gov.governance) || {};
    const required = g.required_signoffs || [];
    const signedRoles = (g.signoffs || []).map((s) => s.role);
    const signed = required.filter((r) => signedRoles.indexOf(r.role) >= 0);
    const missing = required.filter((r) => signedRoles.indexOf(r.role) < 0)
      .map((r) => ({ role: r.role, role_name: r.role_name || roleLabel(r.role) }));
    return {
      required, signed, missing,
      satisfied: signed.length >= required.length,
      progress: signed.length + "/" + required.length,
      total: required.length, done: signed.length,
      selected_action: g.selected_action || "",
      action_key: g.action_key || "",
    };
  }

  /** 工单自带的会签进度（工作台列表可直接用；不依赖 _hitlGov 缓存，后端 to_dict 已回传 signoff_status） */
  function taskSignoff(task) {
    if (task && task.signoff_status) return task.signoff_status;
    const g = (task && task.governance) || {};
    const required = g.required_signoffs || [];
    const signedRoles = (g.signoffs || []).map((s) => s.role);
    const signed = required.filter((r) => signedRoles.indexOf(r.role) >= 0);
    const missing = required.filter((r) => signedRoles.indexOf(r.role) < 0)
      .map((r) => ({ role: r.role, role_name: r.role_name || roleLabel(r.role) }));
    return {
      required, signed, missing,
      satisfied: signed.length >= required.length,
      progress: signed.length + "/" + required.length,
      total: required.length, done: signed.length,
      selected_action: g.selected_action || "",
      action_key: g.action_key || "",
    };
  }

  /**
   * 列表页门控（工作台「待办中心」）：只用工单对象判断，不依赖 _hitlGov 缓存。
   * 与后端 _approve 的三道强制校验对齐：①已选处置动作 ②会签齐备 ③持有该 slot 的 approve 权限。
   * 未纳管工单（无 slot）保持宽松；权限目录未加载时返回未知，交由后端裁决。
   */
  function todoGate(ctx, task) {
    const slot = slotOf(task);
    if (!slot) return { ok: true, reason: "" };
    const status = taskSignoff(task);
    if (!status.action_key) {
      return { ok: false, reason: "未选定处置动作——请前往「详情」选定后再批" };
    }
    if (!status.satisfied) {
      const wait = (status.missing || []).map((m) => m.role_name || m.role).join("、");
      return { ok: false, reason: `会签未完成（${status.progress}）：待 ${wait}` };
    }
    if (hasPerm(ctx, "hitl." + slot + ".approve") === false) {
      const who = rolesWithPerm(ctx, "hitl." + slot + ".approve");
      return { ok: false, reason: "当前岗位无该动作审批权限（需 "
        + (who.length ? who.join(" / ") : "对应审批岗位") + "）" };
    }
    return { ok: true, reason: "" };
  }

  // 动作缓存（渲染时写入，事件处理时读取；避免把 JSON 塞进 DOM 属性）
  const _hitlActions = {};
  // 工单槽位缓存（离线演示时用于推算权限提示）
  const _hitlSlot = {};
  // 治理链缓存（select-action / cosign 后写入，渲染时读取）
  const _hitlGov = {};

  function normalizeActions(actions) {
    return (actions || []).map((a, i) => (typeof a === "string"
      ? { key: "act" + i, label: a, propose: [], cosign: [], impact: "", followup: "" }
      : a));
  }

  /** 动作是否可由当前身份发起（按动作的 propose 列表比对治理角色 key） */
  function actionAllowed(ctx, a) {
    const propose = roleKeysOf(a.propose || []);
    if (!propose.length) return true;
    const mine = govRoles(ctx);
    return propose.some((k) => mine.includes(k));
  }

  function hitlSelMap(ctx) {
    const st = ctx.state.screenState;
    if (!st.hitlSel) st.hitlSel = {};
    return st.hitlSel;
  }

  function actionChipsHtml(ctx, taskUid, actions) {
    const UI = ctx.UI;
    const sel = hitlSelMap(ctx)[taskUid];
    const gov = signoffOf(taskUid, actions);
    return normalizeActions(actions).map((a, i) => {
      const allowed = actionAllowed(ctx, a);
      const on = (sel && sel.key === a.key)
        || (gov.action_key && gov.action_key === a.key);
      const cosignNames = (a.cosign || []).join("、");
      const title = allowed
        ? "发起：" + ((a.propose || []).join("/") || "全体")
          + (cosignNames ? " · 会签：" + cosignNames : " · 无需会签")
        : "需 " + ((a.propose || []).join("/") || "对应岗位") + " 发起";
      return `<button type="button" class="act-chip${on ? " selected" : ""}${allowed ? "" : " disabled"}"
        data-act-task="${UI.esc(taskUid)}" data-act-idx="${i}" ${allowed ? "" : "disabled"}
        title="${UI.esc(title)}">${UI.esc(a.label)}${(a.cosign && a.cosign.length) ? `<em>会签 ${a.cosign.length}</em>` : ""}</button>`;
    }).join("");
  }

  function actionDetailInner(ctx, a) {
    const UI = ctx.UI;
    if (!a) return "选择动作后显示影响与会签要求";
    const cosignKeys = roleKeysOf(a.cosign || []);
    return `<b>${UI.esc(a.label)}</b>${a.impact ? " · " + UI.esc(a.impact) : ""}`
      + (cosignKeys.length
        ? `<div>需会签：${UI.esc(roleLabels(cosignKeys).join("、"))}（缺一不可）</div>`
        : `<div>无需会签，可直接终批</div>`)
      + (a.followup ? `<div>后续：${UI.esc(a.followup)}</div>` : "")
      + (a.require_comment ? `<div class="act-req">该动作需填写决策依据</div>` : "");
  }

  function actionDetailHtml(ctx, taskUid, actions) {
    const UI = ctx.UI;
    const sel = hitlSelMap(ctx)[taskUid];
    const gov = signoffOf(taskUid, actions);
    const key = (sel && sel.key) || gov.action_key;
    const a = key ? normalizeActions(actions).find((x) => x.key === key) : null;
    return `<div class="act-detail" data-act-detail="${UI.esc(taskUid)}">${actionDetailInner(ctx, a)}</div>`;
  }

  /* ---------- 会签面板（多人会签：进度 + 会签按钮 + 已签名单） ---------- */

  function signoffPanelHtml(ctx, taskUid, actions) {
    const UI = ctx.UI;
    const gov = govOf(taskUid);
    const status = signoffOf(taskUid, actions);
    const slot = gov ? slotOf(gov) : "";
    const selected = status.action_key || "";
    if (!selected) return "";
    const mine = govRoles(ctx);
    const signedRoles = (status.signed || []).map((r) => r.role);
    const myPending = (status.required || []).map((r) => r.role)
      .filter((k) => mine.includes(k) && signedRoles.indexOf(k) < 0);
    const cosignRows = (status.required || []).map((r) => {
      const sign = ((gov && gov.governance && gov.governance.signoffs) || [])
        .find((s) => s.role === r.role);
      const on = !!sign;
      return `<div class="signoff-row${on ? " signed" : ""}">
        <span class="signoff-role">${UI.esc(r.role_name || roleLabel(r.role))}</span>
        <span class="signoff-state">${on
          ? UI.icon("check", 12) + " " + UI.esc(sign.actor || "已签")
            + (sign.ts ? " · " + UI.esc(UI.fmtDateTime(sign.ts)) : "")
          : "待会签"}</span>
      </div>`;
    }).join("");
    const canCosign = myPending.length > 0 && (hasPerm(ctx, "hitl." + slot + ".cosign") !== false);
    const tail = (status.required || []).length === 0
      ? `<div class="signoff-note">${UI.icon("check", 12)} 该动作无需会签</div>`
      : (status.satisfied
        ? `<div class="signoff-note ok">${UI.icon("check", 12)} 会签已齐（${UI.esc(status.progress)}），可提交终批</div>`
        : `<div class="signoff-note warn">会签未完成（${UI.esc(status.progress)}）：待
            ${UI.esc((status.missing || []).map((m) => m.role_name).join("、"))}</div>`);
    const btn = ((status.required || []).length && !status.satisfied)
      ? `<button class="btn btn-sm btn-outline" id="hitl-cosign-${UI.esc(taskUid)}"
           ${canCosign ? "" : "disabled title=\"当前岗位不在待会签名单内\""}>
           ${UI.icon("approve", 13)}我要会签${myPending.length ? "（" + UI.esc(roleLabels(myPending).join("/")) + "）" : ""}</button>`
      : "";
    return `<div class="signoff-panel" data-signoff-task="${UI.esc(taskUid)}">
      <div class="signoff-head">会签要求 · 进度 <b>${UI.esc(status.progress)}</b></div>
      ${cosignRows}${tail}
      ${btn ? `<div class="signoff-actions">${btn}</div>` : ""}
    </div>`;
  }

  function wireCosign(root, ctx, taskUid, actions) {
    const btn = root.querySelector(`#hitl-cosign-${taskUid}`);
    if (!btn) return;
    btn.addEventListener("click", () => hitlCosign(ctx, taskUid, actions));
  }

  /** 会签：调后端 cosign，成功后局部刷新治理链与卡片 */
  async function hitlCosign(ctx, taskUid, actions) {
    const UI = ctx.UI;
    const role = ctx.currentRole();
    const gov = govOf(taskUid) || {};
    const status = signoffOf(taskUid, actions);
    const slot = slotOf(gov);
    if (!ctx.api.online()) {
      // 离线演示：本地记一笔会签（不写后端），仅供界面演示
      const g = (gov.governance = gov.governance || {});
      g.signoffs = g.signoffs || [];
      const mine = govRoles(ctx).filter((k) => (status.missing || [])
        .some((m) => m.role === k))[0];
      if (!mine) { UI.toast("当前岗位不在待会签名单内", "warn", 3200); return; }
      g.signoffs.push({ role: mine, actor: role.user, action: "cosign",
        ts: new Date().toISOString() });
      gov.signoff_status = signoffOf(taskUid, actions);
      UI.toast("演示模式：已在界面记录会签（未调用后端）", "info", 3600);
      ctx.refreshScreen();
      return;
    }
    const cmtEl = document.getElementById(`act-comment-${taskUid}`)
      || document.getElementById("hitl-comment");
    const comment = cmtEl && cmtEl.value ? cmtEl.value.trim() : "";
    try {
      const task = await ctx.api.workflowCosign(taskUid, role.user, comment, "");
      _hitlGov[taskUid] = task;
      UI.toast(`已会签（${(task.signoff_status || {}).progress || ""}）`
        + `${task.signoff_status && task.signoff_status.satisfied ? "，会签已齐可终批" : ""}`,
        "ok", 3600);
      ctx.refreshScreen();
    } catch (err) {
      UI.toast(`会签失败：${apiErrorMessage(err)}`, "err", 4500);
    }
  }

  /** API 错误对象 → 人类可读提示（403 说需谁，409 说缺谁） */
  function apiErrorMessage(err) {
    const detail = err && err.detail;
    if (detail && typeof detail === "object") {
      if (detail.missing && detail.missing.length) {
        return `${detail.message || "会签未完成"}——待 `
          + detail.missing.map((m) => m.role_name || m.role).join("、")
          + (detail.progress ? `（${detail.progress}）` : "");
      }
      return detail.message || JSON.stringify(detail);
    }
    return (err && err.message) ? err.message : String(err);
  }

  /** 点击动作 chip：选定处置动作（在线则真实调用 select-action 落库） */
  async function selectAction(ctx, taskUid, action) {
    const UI = ctx.UI;
    const role = ctx.currentRole();
    const sel = hitlSelMap(ctx);
    const cmtEl = document.getElementById(`act-comment-${taskUid}`)
      || document.getElementById("hitl-comment");
    const comment = cmtEl && cmtEl.value ? cmtEl.value.trim() : "";
    if (action.require_comment && !comment) {
      UI.toast("该动作需填写决策依据：请先在上方意见框填写，再点选该动作", "warn", 4600);
      if (cmtEl) cmtEl.focus();
      return;
    }
    sel[taskUid] = { key: action.key, label: action.label,
      require_comment: !!action.require_comment };
    if (!ctx.api.online()) {
      // 离线演示：本地按动作 cosign 推算会签要求
      const required = (action.cosign || []).map(roleKey).filter(Boolean)
        .filter((k, i, arr) => arr.indexOf(k) === i)
        .map((k) => ({ role: k, role_name: roleLabel(k), kind: "cosign", min_count: 1 }));
      _hitlGov[taskUid] = {
        status: "waiting_hitl",
        wf_id: "hitl_" + (_hitlSlot[taskUid] || ""),
        context: { actions: _hitlActions[taskUid] || [], slot: _hitlSlot[taskUid] || "" },
        governance: { selected_action: action.label, action_key: action.key,
          slot: _hitlSlot[taskUid] || "",
          required_signoffs: required, signoffs: [], approvals: [] },
        signoff_status: { required, signed: [], missing: required,
          satisfied: required.length === 0, progress: "0/" + required.length,
          total: required.length, done: 0,
          selected_action: action.label, action_key: action.key,
          slot: _hitlSlot[taskUid] || "" },
      };
      ctx.refreshScreen();
      return;
    }
    try {
      const task = await ctx.api.workflowSelectAction(taskUid, action.key,
        role.user, comment);
      _hitlGov[taskUid] = task;
      const st = task.signoff_status || {};
      UI.toast(st.total
        ? `已选定「${action.label}」→ 需会签 ${st.total} 人（${(st.required || []).map((r) => r.role_name).join("、")}）`
        : `已选定「${action.label}」→ 无需会签，可直接终批`, "ok", 4200);
      ctx.refreshScreen();
    } catch (err) {
      delete sel[taskUid];
      UI.toast(`选定动作失败：${apiErrorMessage(err)}`, "err", 4800);
      ctx.refreshScreen();
    }
  }

  function wireActionChips(root, ctx, taskUid) {
    const actions = normalizeActions(_hitlActions[taskUid] || []);
    root.querySelectorAll(`[data-act-task="${taskUid}"]`).forEach((btn) => {
      btn.addEventListener("click", () => {
        const a = actions[Number(btn.dataset.actIdx)];
        if (!a) return;
        selectAction(ctx, taskUid, a);
      });
    });
  }

  /* ---------- HITL 闸门公示（R3：为什么必须人确认 + 会签方及缺谁） ---------- */

  /** 闸门理由行（常驻）：槽位名 + gate_class + why；目录缺失降级为槽位中文名。 */
  function gateReasonHtml(ctx, slot) {
    const UI = ctx.UI;
    const key = String(slot || "");
    if (!key) return "";
    // 离线演示：后端不可达时 state.gates 为空数组（真值！）——必须判长度，
    // 否则 `||` 兜底永不触发。兜底目录由 tools/gen_gate_fallback.py 从同一份
    // gate_reasons.yaml 生成，测试守护字节一致，不存在第二事实源。
    const gateList = (ctx.state && ctx.state.gates && ctx.state.gates.length)
      ? ctx.state.gates
      : ((ctx.DEMO && ctx.DEMO.GATE_REASONS) || []);
    const gate = gateList.find((g) => g && g.key === key) || null;
    const name = (gate && gate.name) || SLOT_LABEL[key] || key;
    if (!gate || !gate.why) {
      return `<div class="hitl-gate"><span class="hitl-gate-cls">闸门</span><span>${UI.esc(name)}</span></div>`;
    }
    const cls = gate.gate_class ? `<span class="hitl-gate-cls">${UI.esc(gate.gate_class)}</span>` : "";
    return `<div class="hitl-gate">${cls}<span class="hitl-gate-name">${UI.esc(name)}</span>`
      + `<span class="hitl-gate-why">${UI.esc(gate.why)}</span></div>`;
  }

  /**
   * 会签方及缺谁（常驻）：required 全量 + 进度 + missing 点名（不再只在 gate 不 ok 时出现）。
   * 未选定动作时 required 尚未生成（后端 select_action 才建会签要求）——此时展示
   * 该槽位的**候选会签方**（permissions.yaml 实时）并标注「选定动作后生效」，
   * 诚实降级，不假装已经有会签链。
   */
  function signoffGapHtml(ctx, status, slot, actions) {
    const UI = ctx.UI;
    const st = status || {};
    const required = st.required || [];
    if (!required.length) {
      const key = String(slot || "");
      if (!key) return "";
      const perms = (ctx && ctx.perms) || (ctx.state && ctx.state.perms) || null;
      const roles = perms && Array.isArray(perms.roles)
        ? perms.roles.filter((r) => (r.permissions || []).indexOf(`hitl.${key}.cosign`) >= 0)
        : [];
      // 离线演示没有权限目录：候选会签方从回放动作自带的 cosign 推导（动作目录
      // 本来就是治理真值的一部分），标注「将会签 / 选定动作后生效」——
      // 不虚构权限，也不假装已有会签链。
      const fromActions = roles.length ? [] : [...new Set(
        (actions || []).reduce((acc, a) => acc.concat(
          Array.isArray(a && a.cosign) ? a.cosign : []), []))];
      const names = roles.length
        ? roles.map((r) => UI.esc(r.name || r.key))
        : fromActions.map((n) => UI.esc(n));
      if (!names.length) return "";
      return `<div class="signoff-gap"><span class="signoff-gap-label">将会签</span>`
        + `<span>${names.join("、")}</span>`
        + `<span class="signoff-gap-progress">选定动作后生效</span></div>`;
    }
    const names = required.map((r) => UI.esc(r.role_name || roleLabel(r.role)));
    const missing = (st.missing || []).map((r) => UI.esc(r.role_name || roleLabel(r.role)));
    const gap = missing.length
      ? `<span class="signoff-gap-missing">缺：${missing.join("、")}</span>`
      : `<span class="signoff-gap-ok">${UI.icon("check", 11)} 已齐</span>`;
    return `<div class="signoff-gap"><span class="signoff-gap-label">会签方</span>`
      + `<span>${names.join("、")}</span>`
      + `<span class="signoff-gap-progress">${UI.esc(st.progress || "0/0")}</span>`
      + `${gap}</div>`;
  }

  /* ---------- 签名流水（wf_signoff 只读审计 · 懒加载） ---------- */

  const SG_VERB_LABEL = {
    select_action: "选定动作", cosign: "会签", approve: "终批", reject: "驳回",
  };

  /** 签名账本行的单元格（顺序与表头一致）。 */
  function signoffRowsHtml(ctx, rows) {
    const UI = ctx.UI;
    if (!rows.length) {
      return `<div class="sg-flow-empty">${UI.icon("info", 12)}
        <span>该工单暂无签名事实。账本只记启用后的新签名，存量工单不回溯；
        工单判定仍以治理快照（governance_json）为准。</span></div>`;
    }
    const head = ["角色", "签署人", "动作", "决定", "时间", "意见"]
      .map((t) => `<span>${t}</span>`).join("");
    const body = rows.map((r) => {
      const actor = UI.esc(r.actor || "-")
        + (r.actor_id ? `<i class="sg-flow-sub">${UI.esc(r.actor_id)}</i>` : "")
        + (r.on_behalf_of ? `<i class="sg-flow-sub">代 ${UI.esc(r.on_behalf_of)}</i>` : "");
      const verb = `<span class="sg-flow-verb">${UI.esc(SG_VERB_LABEL[r.verb] || r.verb || "-")}</span>`
        + (r.overdue_signoff ? `<i class="sg-flow-over">超时</i>` : "");
      const cells = [
        UI.esc(r.role_name || roleLabel(r.role_key) || r.role_key || "-"),
        actor,
        verb,
        UI.esc(r.decision || "-"),
        UI.esc(r.ts ? UI.fmtDateTime(r.ts) : "-"),
        UI.esc(r.comment || "-"),
      ];
      return `<div class="sg-flow-row">${cells.map((c) => `<span>${c}</span>`).join("")}</div>`;
    }).join("");
    return `<div class="sg-flow-table"><div class="sg-flow-row sg-flow-head">${head}</div>${body}</div>`;
  }

  /** 折叠摘要：收起不预取（审批中心一屏多卡，展开才请求）。 */
  function signoffFlowHtml(ctx, taskUid) {
    const UI = ctx.UI;
    return `<details class="sg-flow" data-sg-task="${UI.esc(taskUid)}">
      <summary class="sg-flow-sum">
        ${UI.icon("shield", 12)}<span class="sg-flow-title">签名流水</span>
        <span class="sg-flow-tag">wf_signoff 只读审计</span>
        <span class="sg-flow-count" data-sg-count>展开加载</span>
      </summary>
      <div class="sg-flow-body" data-sg-body>
        <div class="sg-flow-note">展开后读取签名事实账本（会签 / 终批 / 驳回）</div>
      </div>
    </details>`;
  }

  /** 绑定签名流水：展开懒加载 + 导出 CSV（每次详情卡重渲染后重新绑定）。 */
  function wireSignoffFlow(root, ctx, taskUid) {
    const UI = ctx.UI;
    const box = root.querySelector("details.sg-flow");
    if (!box) return;
    const bodyEl = () => box.querySelector("[data-sg-body]");
    const load = async () => {
      if (box.dataset.sgLoaded === "1" || box.dataset.sgLoading === "1") return;
      // 离线演示没有真实账本：诚实说明，不伪造签名流水
      if (!ctx.api.online()) {
        const el = bodyEl();
        if (el) el.innerHTML = `<div class="sg-flow-note warn">${UI.icon("warn", 12)} 后端离线：签名账本需后端在线才能读取</div>`;
        return;
      }
      box.dataset.sgLoading = "1";
      const el = bodyEl();
      if (el) el.innerHTML = `<div class="sg-flow-note">正在读取签名账本…</div>`;
      try {
        const data = await ctx.api.workflowSignoffs(taskUid);
        const rows = (data && data.rows) || [];
        box.dataset.sgLoaded = "1";
        delete box.dataset.sgLoading;
        const count = box.querySelector("[data-sg-count]");
        if (count) count.textContent = rows.length + " 条";
        if (el) {
          el.innerHTML = `<div class="sg-flow-tools">
              <span class="sg-flow-src">来源 <b>${UI.esc((data && data.source) || "wf_signoff")}</b></span>
              <button class="btn btn-outline btn-sm" data-sg-export>${UI.icon("report", 12)}导出 CSV</button>
            </div>
            ${(data && data.note) ? `<div class="sg-flow-note">${UI.esc(data.note)}</div>` : ""}
            ${signoffRowsHtml(ctx, rows)}`;
          const btn = el.querySelector("[data-sg-export]");
          if (btn) btn.addEventListener("click", async () => {
            btn.disabled = true;
            try {
              const name = await ctx.api.downloadWorkflowSignoffs(taskUid);
              UI.toast("已导出签名账本（" + name + "）", "ok", 3000);
            } catch (err) {
              UI.toast("导出失败：" + UI.friendlyError(err).text, "err", 5000);
            } finally {
              btn.disabled = false;
            }
          });
        }
      } catch (err) {
        delete box.dataset.sgLoading;
        if (el) el.innerHTML = `<div class="sg-flow-note err">${UI.icon("warn", 12)} 读取失败：${UI.esc(UI.friendlyError(err).text)}（收起再展开可重试）</div>`;
      }
    };
    box.addEventListener("toggle", () => { if (box.open) load(); });
    if (box.open) load();
  }

  function hitlChatCardHtml(ctx, m) {
    const UI = ctx.UI;
    const processed = processedMap(ctx)[m.taskUid];
    const acts = normalizeActions(m.actions);
    _hitlActions[m.taskUid] = m.actions || [];
    if (m.slot) _hitlSlot[m.taskUid] = String(m.slot);
    if (m.governance && !_hitlGov[m.taskUid]) {
      _hitlGov[m.taskUid] = { wf_id: "hitl_" + (m.slot || ""), context: { actions: m.actions },
        governance: m.governance, signoff_status: m.signoffStatus || null };
    }
    const gate = approveGate(ctx, m.taskUid, acts, m.slot);
    const signoff = m.signoffStatus || signoffOf(m.taskUid, acts);
    return `<div class="hitl-card" data-hitl-task="${UI.esc(m.taskUid)}">
      <div class="hitl-card-head">${UI.icon("approve", 15)}待人工确认（不自动放行）</div>
      <div class="hitl-card-prompt">${UI.esc(m.prompt)}</div>
      <div class="hitl-card-meta">
        <span>审批人：<b>${UI.esc((m.approvers || []).join("、"))}</b></span>
        <span>工单：<b>${UI.esc(m.taskUid || "-")}</b></span>
        ${m.colleague ? `<span>发起：<b>${UI.esc(m.colleague)}</b></span>` : ""}
        ${m.runId ? `<span>run_id：<b>${UI.esc(m.runId)}</b></span>` : ""}
      </div>
      ${gateReasonHtml(ctx, m.slot || _hitlSlot[m.taskUid] || "")}
      ${signoffGapHtml(ctx, signoff, m.slot || _hitlSlot[m.taskUid] || "", m.actions)}
      ${acts.length ? `<div class="hitl-card-meta">可选动作（按岗位权限，灰显需对应岗位发起）：</div>
        <div class="act-chips">${actionChipsHtml(ctx, m.taskUid, m.actions)}</div>
        ${actionDetailHtml(ctx, m.taskUid, m.actions)}` : ""}
      ${signoffPanelHtml(ctx, m.taskUid, m.actions)}
      ${processed ? "" : `${quickCommentChipsHtml(ctx, m.taskUid, m.slot || _hitlSlot[m.taskUid] || "")}
      <input class="input act-comment" id="act-comment-${UI.esc(m.taskUid)}" placeholder="决策意见（同意可选；驳回必填）" />
      <div class="hitl-card-actions">
        <button class="btn btn-primary btn-sm" data-approve="${UI.esc(m.taskUid)}"
          ${gate.ok ? "" : `disabled title="${UI.esc(gate.reason)}"`}>${UI.icon("check", 13)}同意</button>
        <button class="btn btn-danger btn-sm" data-reject="${UI.esc(m.taskUid)}">${UI.icon("x", 13)}驳回</button>
      </div>
      ${gate.ok ? "" : `<div class="gate-hint">${UI.icon("info", 12)} ${UI.esc(gate.reason)}</div>`}`}
      ${hitlProcessedBanner(ctx, m.taskUid)}
      ${m.demo ? `<div style="margin-top:9px">${UI.demoFlag("演示回放 · 未真实创建工单")}</div>` : ""}
    </div>`;
  }

  /**
   * 「同意」按钮门控：会签未齐 / 当前岗位无 approve 权限 → 禁用并说明原因。
   * 后端仍会二次校验（前端只是 UX 提示，不承担安全职责）。
   */
  function approveGate(ctx, taskUid, actions, slotHint) {
    const gov = govOf(taskUid);
    const slot = (gov && slotOf(gov)) || slotHint || "";
    if (!gov || !slot) return { ok: true, reason: "" };
    const status = signoffOf(taskUid, actions);
    if (!status.action_key) {
      return { ok: false, reason: "请先在上方「处置动作」区选定一个动作，再点同意" };
    }
    if (!status.satisfied) {
      return { ok: false, reason: `会签未完成（${status.progress}）：待 `
        + status.missing.map((m) => m.role_name || m.role).join("、") };
    }
    const can = hasPerm(ctx, "hitl." + slot + ".approve");
    if (can === false) {
      const who = rolesWithPerm(ctx, "hitl." + slot + ".approve");
      return { ok: false, reason: "当前岗位无该动作的审批权限（需："
        + (who.length ? who.join(" / ") : "对应审批岗位") + "）" };
    }
    return { ok: true, reason: "" };
  }


  /* ============================================================
   * 屏幕四：预警中心
   *
   * 真实优先：五类预警由后端 `/api/v2/alerts` 机械推导——前四类来自**当前只读数据**
   * （SPC 过程能力 / 材料库存 / 订单履约 / 设备日志），与各看板同源可对账；
   * 第五类「现场推送」来自治理库的现场看板推送表（客诉 8D 分派后的现场处置队列），
   * 可在卡片上直接**现场接收 / 闭环处置**（写回 `/api/v2/site-board`）。
   * 后端离线 / 降级才回落到 DEMO.ALERTS 演示快照（明确标注来源）。
   * ============================================================ */

  // 五类预警（字面量必须与后端 ALERT_TYPES 完全一致——驱动筛选片与配色；
  // 守卫见 tests/demo/test_alerts_api.py 的 typeCls 对回用例）
  const ALERT_TYPES = ["SPC越限", "缺料", "订单临期", "设备告警", "现场推送"];

  // 预警缓存（按岗位隔离）：同岗位内切换筛选 / 展开不重复请求；
  // 换岗位必须重取——订单客户名按岗位脱敏，缓存串岗位会让脱敏失效。
  let _alertsCache = null;   // { role, data, source, reason }

  function render_alerts(root, ctx) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    if (!st.alertFilter) st.alertFilter = "全部";

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("alert", 20)}预警中心</div>
          <div class="page-desc">SPC 越限 / 缺料 / 订单临期 / 设备告警四类预警由当前只读数据机械推导（SPC 过程能力 · 材料库存 · 订单履约 · 设备日志）；现场推送为客诉 8D 分派到责任工段的处置队列（治理库实况），可在卡片上直接现场接收与闭环。每条标注联动部门。
            <span id="alert-window"></span>
            <span id="alert-flag"></span><span id="alert-gen" class="mfg-gen"></span></div>
        </div>
        <div class="chip-row" style="margin-bottom:14px" id="alert-filters"></div>
        <div class="card" id="alert-list">
          <div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载预警数据…</div></div>
        </div>
      </div>`;

    loadAlerts(ctx);
  }

  /**
   * 预警中心数据加载（真实优先 + 演示降级，全程不抛）：
   * 在线且未降级 → /api/v2/alerts（后端实时聚合）；否则 → DEMO.ALERTS。
   * **空列表也是有效结论**（当前数据下确无预警）——不因"没数据"回落演示快照，
   * 否则会把"无异常"伪装成"有异常"（与看板把 403 当降级同一类假阳性）。
   */
  async function loadAlerts(ctx) {
    const UI = ctx.UI;
    const roleId = (ctx.state.role || "");
    if (_alertsCache && _alertsCache.role === roleId) { paintAlerts(ctx); return; }

    let data = null, source = "real", reason = "";
    if (ctx.api.online()) {
      try {
        const d = await ctx.api.alerts();
        if (d && !d.degraded) data = d;
        else reason = (d && d.degraded_reason) || "后端数据降级";
      } catch (err) {
        reason = UI.friendlyError(err).text;
        UI.toast("预警数据请求失败，已切换演示数据", "warn", 3600);
      }
    } else {
      reason = "后端离线 · 演示数据";
    }

    if (!data) {
      source = "demo";
      data = { alerts: ctx.DEMO.ALERTS, types: ALERT_TYPES, counts: null,
               window: null, generated_at: "", rules: [] };
    }
    _alertsCache = { role: roleId, data, source, reason };
    paintAlerts(ctx);
  }

  function countAlertTypes(alerts, types) {
    const out = {};
    types.forEach((t) => { out[t] = 0; });
    alerts.forEach((a) => { out[a.type] = (out[a.type] || 0) + 1; });
    return out;
  }

  function paintAlerts(ctx) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    const cache = _alertsCache;
    if (!cache) return;
    const data = cache.data || {};
    const alerts = data.alerts || [];
    const types = data.types || ALERT_TYPES;
    const counts = data.counts || countAlertTypes(alerts, types);
    const filter = st.alertFilter;
    const list = filter === "全部" ? alerts : alerts.filter((a) => a.type === filter);

    // 口径与来源：真实 / 演示必须在界面上可区分，绝不混为一谈
    const winEl = document.getElementById("alert-window");
    if (winEl) {
      const w = data.window;
      winEl.textContent = (w && w.start) ? `统计口径 ${w.label}（${w.start} ~ ${w.end}）` : "";
    }
    const flagEl = document.getElementById("alert-flag");
    if (flagEl) {
      flagEl.innerHTML = cache.source === "demo"
        ? UI.demoFlag(cache.reason || "演示数据")
        : UI.realFlag("后端实时聚合 · 与各看板同源");
    }
    const genEl = document.getElementById("alert-gen");
    if (genEl) genEl.textContent = data.generated_at ? " · 生成于 " + data.generated_at : "";

    // 筛选片（计数来自真实数据，不再写死演示常量）
    const filters = document.getElementById("alert-filters");
    if (filters) {
      filters.innerHTML = ["全部"].concat(types).map((t) => {
        const n = t === "全部" ? alerts.length : (counts[t] || 0);
        return `<button class="chip${filter === t ? " active" : ""}" data-filter="${UI.esc(t)}">${UI.esc(t)} (${n})</button>`;
      }).join("");
      filters.querySelectorAll("[data-filter]").forEach((btn) => {
        btn.addEventListener("click", () => { st.alertFilter = btn.dataset.filter; paintAlerts(ctx); });
      });
    }

    const box = document.getElementById("alert-list");
    if (!box) return;
    box.innerHTML = list.length
      ? list.map((a) => alertItemHtml(ctx, a)).join("")
      : `<div class="empty">${filter === "全部" ? "当前数据下暂无预警" : "该类型暂无预警"}</div>`;

    box.querySelectorAll("[data-alert]").forEach((card) => {
      card.addEventListener("click", () => {
        st.expandedAlert = st.expandedAlert === card.dataset.alert ? null : card.dataset.alert;
        paintAlerts(ctx);
      });
    });
    box.querySelectorAll("[data-ask-alert]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        // 结构化来源标注：预警问数（无看板口径，供审计留痕与后续预警上下文关联）
        const card = btn.closest("[data-alert]");
        writeAskPrefill(btn.dataset.askAlert, {
          module: "alerts",
          alert: card ? card.dataset.alert : "",
        });
        ctx.navigate("chat");
      });
    });
    // 「现场推送」的写操作（其余四类预警只读，无这些按钮）
    box.querySelectorAll("[data-site-ack]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        siteBoardAckAction(ctx, btn.dataset.siteAck);
      });
    });
    box.querySelectorAll("[data-site-close]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        siteBoardCloseAction(ctx, btn.dataset.siteClose);
      });
    });
  }

  /** 单条预警卡片（数据驱动：字段全部来自真实预警载荷 / 演示快照同构）。 */
  function alertItemHtml(ctx, a) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    const typeCls = { "SPC越限": "badge-purple", "缺料": "badge-amber", "订单临期": "badge-red", "设备告警": "badge-blue", "现场推送": "badge-green" };
    const statusCls = { "待处置": "badge-red", "处置中": "badge-blue", "已闭环": "badge-green" };
    const tags = a.tags || [];
    return `<div class="alert-item alert-clickable" data-alert="${UI.esc(a.id)}" title="点击${st.expandedAlert === a.id ? "收起" : "查看细则"}">
      <div class="alert-level ${UI.esc(a.level)}">${UI.esc(a.level)}</div>
      <div class="alert-main">
        <div class="alert-title">${UI.esc(a.title)} ${UI.badge(a.type, typeCls[a.type] || "badge-gray")}</div>
        <div class="alert-detail">${UI.esc(a.detail)}</div>
        ${st.expandedAlert === a.id ? alertDetailHtml(ctx, a) : ""}
        <div class="alert-tags">
          <span class="tag-label">联动部门：</span>
          ${tags.map((t) => UI.badge(t, "badge-outline")).join("")}
        </div>
        <div class="alert-foot"><span>编号 ${UI.esc(a.id)}</span><span>责任人 ${UI.esc(a.owner)}</span><span>触发时间 ${UI.esc(a.time)}</span></div>
      </div>
      <div class="alert-status">${UI.badge(a.status, statusCls[a.status] || "badge-gray", true)}</div>
    </div>`;
  }

  /**
   * 预警细则（点开可见）：判定依据 + 影响分析 + 引导式处置步骤 + 响应时间线 + 快速操作。
   * 真实预警的等级 / 状态是机械规则推出的，故给出「判定依据」，
   * 库中没有处置生命周期就不填 steps / timeline（有则渲染，无则整段省略，绝不编造）。
   */
  function alertDetailHtml(ctx, a) {
    const UI = ctx.UI;
    const askQ = a.ask || {
      "SPC越限": "最近锡膏印刷 CPK 越限的详细数据与趋势",
      "缺料": "锡膏 M7020 的库存、在途与齐套情况",
      "订单临期": "A型号的 ATP 可承诺量与出货风险",
      "设备告警": "SMT-1设备参数异常吗",
    }[a.type] || a.title;
    return `<div class="alert-detail-panel">
      ${a.basis ? `<div class="adp-sec"><b>判定依据</b><span>${UI.esc(a.basis)}</span></div>` : ""}
      ${a.impact ? `<div class="adp-sec"><b>影响分析</b><span>${UI.esc(a.impact)}</span></div>` : ""}
      ${(a.steps || []).length ? `<div class="adp-sec"><b>处置步骤（引导式）</b>
        <ol class="adp-steps">${a.steps.map((st2) => `<li>${UI.esc(st2)}</li>`).join("")}</ol></div>` : ""}
      ${(a.timeline || []).length ? `<div class="adp-sec"><b>响应时间线</b>
        <div class="adp-timeline">${a.timeline.map((t) => `<div class="adp-tl-row"><span class="adp-tl-time">${UI.esc(t[0])}</span><span>${UI.esc(t[1])}</span><span class="adp-tl-who">${UI.esc(t[2])}</span></div>`).join("")}</div></div>` : ""}
      <div class="adp-actions">
        ${siteBoardActions(ctx, a)}
        <button class="btn btn-outline btn-sm" data-ask-alert="${UI.esc(askQ)}">${UI.icon("chat", 13)}去问数分析</button>
        ${a.status !== "已闭环" ? `<button class="btn btn-outline btn-sm" data-ask-alert="为预警 ${UI.esc(a.id)} 创建人工跟进工单">${UI.icon("approve", 13)}转人工跟进</button>` : ""}
        <button class="btn btn-outline btn-sm" data-report-gen="${UI.esc(JSON.stringify({
          source_module: "alert",
          context: {
            title: `预警处置报告 · ${a.title}`,
            summary: `${a.detail || ""}｜判定依据：${a.basis || "—"}｜状态：${a.status || ""}`,
            refs: [a.id],
            risk_level: /^[A-D]$/.test(String(a.level || "")) ? a.level : "",
          },
        }))}">${UI.icon("report", 13)}生成报告</button>
      </div>
    </div>`;
  }

  /**
   * 「现场推送」卡片的写操作按钮（其余四类预警是只读推导，无此按钮）。
   * 状态即真实回写状态：待处置 → 现场接收；处置中 → 闭环处置；已闭环 → 只看不留按钮。
   */
  function siteBoardActions(ctx, a) {
    if (a.type !== "现场推送") return "";
    const UI = ctx.UI;
    const pushId = ((a.entity || {}).push_id) || a.id || "";
    if (!pushId || a.status === "已闭环") return "";
    if (a.status === "处置中") {
      return `<button class="btn btn-primary btn-sm" data-site-close="${UI.esc(pushId)}">${UI.icon("report", 13)}闭环处置</button>`;
    }
    return `<button class="btn btn-primary btn-sm" data-site-ack="${UI.esc(pushId)}">${UI.icon("approve", 13)}现场接收</button>`;
  }

  /** 现场接收（pushed → acked）：成功后失效预警缓存并重绘，状态与计数同步刷新。 */
  async function siteBoardAckAction(ctx, pushId) {
    const UI = ctx.UI;
    try {
      await ctx.api.siteBoardAck(pushId, "");
      UI.toast("已接收现场推送，处置进度将回流到 8D 关闭确认", "ok", 3200);
      _alertsCache = null;
      loadAlerts(ctx);
    } catch (err) {
      UI.toast(UI.friendlyError(err).text, "warn", 4000);
    }
  }

  /** 现场闭环（acked → closed）：处置结论**必填**——与后端同口径（空则 400）。 */
  async function siteBoardCloseAction(ctx, pushId) {
    const UI = ctx.UI;
    const reason = await askComment(ctx, {
      title: "现场处置结论（必填）",
      hint: "结论写入现场看板留痕，并作为 8D 关闭确认的依据；请写清已采取的措施与结果。",
      placeholder: "例：已围堵首件 200pcs，参数复核后连续 3 批无同类不良",
    });
    if (!reason) return;
    try {
      await ctx.api.siteBoardFeedback(pushId, reason);
      UI.toast("现场已闭环，处置结论已留痕", "ok", 3200);
      _alertsCache = null;
      loadAlerts(ctx);
    } catch (err) {
      UI.toast(UI.friendlyError(err).text, "warn", 4000);
    }
  }

  /* ============================================================
   * 屏幕五：报告中心
   * ============================================================ */

  function render_reports(root, ctx) {
    const UI = ctx.UI, DEMO = ctx.DEMO;
    const R = DEMO.REPORTS;

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("report", 20)}报告中心</div>
          <div class="head-extra">
            <button class="btn btn-outline btn-sm" onclick="window.print()" title="打印 / 导出 PDF（浏览器打印对话框，内部简报与对客报告分别可打印）">${UI.icon("report", 13)}打印 / 导出</button>
          </div>
          <div class="page-desc">内部简报与对客脱敏报告同屏对比；脱敏词高亮标注（悬停可见禁词原文）。${UI.demoFlag()}内容结构复刻 CLI demo 真实输出</div>
        </div>

        <div class="section-title">协同问数生成 <span class="section-extra">自动归档最近 30 份 · 点击展开全文</span></div>
        <div class="card" style="padding:6px 14px 10px" id="qa-archive-list"></div>

        <div class="report-cols">
          <div class="report-doc">
            <div class="report-doc-head internal">
              ${UI.icon("file", 16)}
              <div><div class="report-doc-title">${UI.esc(R.internal.title)}</div>
              <div style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3)">run_id ${UI.esc(R.internal.run_id)} · ${UI.esc(R.internal.route.scenario)}</div></div>
              <div style="margin-left:auto">${UI.badge("内部可见", "badge-blue")}</div>
            </div>
            <div class="report-doc-body">
              <div class="brief-grid" style="grid-template-columns:1fr">
                ${R.internal.brief.map((b) => `<div class="brief-item"><span class="k">${UI.esc(b.k)}</span><span class="v">${UI.esc(b.v)}</span></div>`).join("")}
                <div class="brief-item"><span class="k">工作流</span><span class="v">${UI.esc(R.internal.workflow)}</span></div>
                <div class="brief-item"><span class="k">人工确认</span><span class="v">${UI.esc(R.internal.hitl)}</span></div>
              </div>
            </div>
          </div>

          <div class="report-doc">
            <div class="report-doc-head customer">
              ${UI.icon("shield", 16)}
              <div><div class="report-doc-title">${UI.esc(R.customer.title)}</div>
              <div style="font-size: calc(11.5px * var(--font-scale));color:var(--text-3)">字段级脱敏 + 禁词表兜底（node_registry.FORBIDDEN_WORDS）</div></div>
              <div style="margin-left:auto">${UI.badge("对客可见", "badge-amber")}</div>
            </div>
            <div class="report-doc-body">
              <div class="customer-text">${R.customer.html}</div>
              <div class="mask-legend">
                <div class="mask-legend-title">${UI.icon("eyeoff", 13)} 脱敏映射（高亮词悬停可见禁词原文）</div>
                <div class="mask-map-row">
                  ${R.mask_map.map((m) => `<span class="mask-map-item"><s>${UI.esc(m.from)}</s>→<b>${UI.esc(m.to)}</b></span>`).join("")}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="section-title">报告版本留痕 <span class="section-extra">可审计：每次修订留痕 ${UI.demoFlag()}</span></div>
        <div class="card">
          ${R.versions.map((v) => `
            <div class="version-item">
              <div class="version-dot"></div>
              <div class="version-main">
                <div class="version-title">${UI.esc(v.ver)} ${UI.badge(v.badge, v.badge_cls)}</div>
                <div class="version-meta"><span>${UI.esc(v.time)}</span><span>${UI.esc(v.actor)}</span></div>
                <div class="version-note">${UI.esc(v.note)}</div>
              </div>
            </div>`).join("")}
        </div>
      </div>`;

    renderQaArchive(ctx);
  }

  /* ============================================================
   * 屏幕六：审批确认中心
   * ============================================================ */

  function render_hitl(root, ctx) {
    const UI = ctx.UI;
    root.innerHTML = `
      <div class="content-inner hitl-screen">
        <div class="page-head">
          <div class="page-title">${UI.icon("approve", 20)}审批确认中心</div>
          <div class="page-desc">关键决策（复线、对客交期、退换货、8D 关闭等）须由对应岗位人工确认，绝不自动放行。${UI.realFlag("工作流引擎待办队列")}</div>
        </div>
        <div class="hitl-layout">
          <div class="card">
            <div class="card-head">
              <div class="card-title">${UI.icon("list", 16)}待审批列表</div>
              <div class="card-extra" id="hitl-source">加载中…</div>
            </div>
            <div class="hitl-search">
              <input id="hitl-search-input" type="text" placeholder="搜索：工单号 / 标题 / 审批岗位 / 优先级…" autocomplete="off" />
              <button class="hitl-search-clear" id="hitl-search-clear" title="清空" style="display:none">✕</button>
            </div>
            <div id="hitl-list"><div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载待办队列…</div></div></div>
          </div>
          <div id="hitl-detail"></div>
        </div>
      </div>`;
    loadHitlTasks(ctx);
  }

  async function loadHitlTasks(ctx) {
    const listEl = document.getElementById("hitl-list");
    const sourceEl = document.getElementById("hitl-source");
    if (!listEl || ctx.state.screen !== "hitl") return;
    const UI = ctx.UI;
    let tasks = [];
    let demo = false;
    let readonlyMode = false;
    let noGovernance = false;   // 岗位不具备任何 HITL 槽位权限（不参与治理审批）
    let mineCount = 0;          // 我要动作的
    let holdCount = 0;          // 我域内待观察的

    if (ctx.api.online()) {
      try {
        // 一次请求拿到「我的活」+「我域内待观察」，由后端按 HITL 槽位裁决。
        // 旧实现拉两次：先按角色，无待办时再拉全量兜底——第二次等于把全厂
        // 工单（含客户名 / 批次号）塞给 HR 这种无治理职责的岗位。
        const raw = await ctx.api.workflowPending(govRoles(ctx));
        const scope = raw.scope || {};
        noGovernance = !(scope.slots || []).length;
        if (!noGovernance) {
          const mineRaw = raw.filter((t) => t.visibility === "mine");
          const domainRaw = raw.filter((t) => t.visibility !== "mine");
          const mine = mergeProcessedTasks(ctx, mineRaw);
          // 域内但暂不轮到我：降级为只读（.readonly 行不可点发起动作），
          // 但仍然展示——否则审批中心看起来"没活"，其实是活还没到我这。
          const domain = mergeProcessedTasks(ctx, domainRaw)
            .map((t) => ({ ...t, _readonly: true }));
          tasks = mine.concat(domain);
          mineCount = mine.length;
          holdCount = tasks.length - mineCount;
          readonlyMode = mineCount === 0 && holdCount > 0;
        }
        tasks.forEach((t) => { if (t.task_uid && t.governance) _hitlGov[t.task_uid] = t; });
      } catch (err) {
        demo = true;
      }
    } else {
      demo = true;
    }
    if (ctx.state.screen !== "hitl") return;

    if (demo) {
      tasks = ctx.DEMO.DEMO_人工确认.map((t) => {
        const left = Number(t.sla_hours_left);
        const hoursLeft = Number.isFinite(left) ? left : 1;
        const due = new Date(Date.now() + hoursLeft * 3600000).toISOString();
        const created = new Date(Date.now() - Math.max(0.1, 2 - hoursLeft) * 3600000).toISOString();
        return {
          task_uid: t.task_uid, wf_id: t.wf_id, title: t.title,
          hitl_prompt: t.title, status: "waiting_hitl", priority: t.priority,
          assignees: t.approver_roles, sla_due_at: due, created_at: created,
          context: t.ctx, history: [], demo: true, my_action: null,
        };
      });
    }
    if (sourceEl) {
      sourceEl.innerHTML = demo
        ? UI.demoFlag("后端离线 · 演示数据")
        : noGovernance
          // 不是「没待办」，是「这个岗位不在这条链上」。两者措辞必须区分，
          // 否则 HR 会以为全厂的关键决策都已处理完毕。
          ? UI.badge("本岗位不参与治理审批", "badge-amber")
          : readonlyMode
            ? UI.badge("当前岗位无待办 · 只读展示域内待观察", "badge-amber")
            : UI.realFlag("我的待办 " + mineCount
                + (holdCount ? " · 域内待观察 " + holdCount : "")
                + "（" + roleLabels(govRoles(ctx)).join("/") + "）");
    }

    // 排序：需要「我」动作的待办永远置顶（醒目样式），其后按 SLA 剩余时间紧迫度；
    // 已处理沉底，只读（他人待办）排在可交互项之后。
    const st = ctx.state.screenState;
    const processedMapCache = processedMap(ctx);
    tasks.sort((a, b) => hitlRank(a, processedMapCache) - hitlRank(b, processedMapCache)
      || Date.parse(slaDueIso(a) || 0) - Date.parse(slaDueIso(b) || 0)
      || String(a.task_uid).localeCompare(String(b.task_uid)));

    if (!tasks.length) {
      listEl.innerHTML = noGovernance
        // 空态措辞是产品决策：说"当前无待审批工单"会让 HR 以为活干完了；
        // 真相是这个岗位压根不在这条治理链上。
        ? `<div class="empty">${UI.icon("info", 30)}
            <div style="margin-top:8px">本岗位不参与治理审批</div>
            <div style="font-size: calc(11.5px * var(--font-scale));margin-top:6px">
              复线 / 特采 / 8D 关闭 / 对客承诺 / 停机窗口等决策由对应治理岗位确认，
              本岗位无权也不需要处理</div></div>`
        : `<div class="empty">${UI.icon("check", 30)}<div style="margin-top:8px">当前无待审批工单</div>
            <div style="font-size: calc(11.5px * var(--font-scale));margin-top:6px">可在「协同问数」发起停线问数，人工确认 节点将生成真实待办</div></div>`;
      document.getElementById("hitl-detail").innerHTML = "";
      return;
    }

    // 列表渲染（供首次绘制与搜索过滤复用）；查询词记忆在 screenState，切屏回来不丢。
    const drawList = (query) => {
      const q = (query || "").trim().toLowerCase();
      const visible = q
        ? tasks.filter((t) => hitlSearchableText(t).includes(q))
        : tasks;
      if (!visible.length) {
        listEl.innerHTML = `<div class="empty"><div style="margin-top:8px">没有匹配「${UI.esc(query)}」的待办</div>
          <div style="font-size: calc(11.5px * var(--font-scale));margin-top:6px">试试工单号、标题关键词、审批岗位或优先级（如 P1）</div></div>`;
        return;
      }
      if (!st.hitlSelected || !visible.some((t) => t.task_uid === st.hitlSelected)) {
        st.hitlSelected = visible[0].task_uid;
      }
      listEl.innerHTML = visible.map((t) => hitlListItemHtml(ctx, t)).join("");
      listEl.querySelectorAll("[data-select]").forEach((el) => {
        el.addEventListener("click", () => {
          ctx.state.screenState.hitlSelected = el.dataset.select;
          listEl.querySelectorAll(".hitl-list-item").forEach((x) => x.classList.remove("selected"));
          el.classList.add("selected");
          const task = visible.find((x) => x.task_uid === el.dataset.select);
          renderHitlDetail(ctx, task);
        });
      });
      const current = visible.find((t) => t.task_uid === st.hitlSelected) || visible[0];
      renderHitlDetail(ctx, current);
    };

    // 深链：报告中心「关联工单」跳转时写入 hitl_prefill（工单号）→ 自动选中该工单
    try {
      const want = sessionStorage.getItem("hitl_prefill");
      if (want) {
        sessionStorage.removeItem("hitl_prefill");
        if (tasks.some((t) => t.task_uid === want)) st.hitlSelected = want;
      }
    } catch (e) { /* 隐私模式忽略 */ }

    drawList(st.hitlQuery || "");

    // 搜索框：实时过滤（只重绘左列，不打断右侧详情交互）
    const searchInput = document.getElementById("hitl-search-input");
    const searchClear = document.getElementById("hitl-search-clear");
    if (searchInput) {
      if (st.hitlQuery) searchInput.value = st.hitlQuery;
      searchInput.addEventListener("input", () => {
        st.hitlQuery = searchInput.value;
        if (searchClear) searchClear.style.display = searchInput.value ? "" : "none";
        drawList(searchInput.value);
      });
      if (searchClear) {
        searchClear.style.display = st.hitlQuery ? "" : "none";
        searchClear.addEventListener("click", () => {
          searchInput.value = "";
          st.hitlQuery = "";
          searchClear.style.display = "none";
          drawList("");
          searchInput.focus();
        });
      }
    }
  }

  /** 待办排序权重：我的待办(0) → 同队列可交互(1) → 他人只读(2)；已处理一律 +10 沉底。 */
  function hitlRank(t, processedMapCache) {
    let rank = t.my_action ? 0 : (t._readonly ? 2 : 1);
    if (processedMapCache && processedMapCache[t.task_uid]) rank += 10;
    return rank;
  }

  /** 搜索索引：标题/提示词 + 工单号 + 审批岗位 + 优先级 + 我的待办类型。 */
  function hitlSearchableText(t) {
    return [
      t.hitl_prompt || t.title || "",
      t.task_uid || "",
      (t.assignees || []).join("、"),
      t.priority || "",
      MY_ACTION_LABEL[t.my_action] || "",
      t._readonly ? "只读" : "",
    ].join(" ").toLowerCase();
  }

  // 「审批即待办」动作徽标：待我发起 / 待我会签 / 待我终批
  const MY_ACTION_LABEL = {
    select_action: "待我发起",
    cosign: "待我会签",
    approve: "待我终批",
  };

  function myActionBadge(ctx, t) {
    const UI = ctx.UI;
    const act = t.my_action || null;
    if (!act) return "";
    const cls = act === "approve" ? "badge-green"
      : act === "cosign" ? "badge-amber" : "badge-blue";
    return UI.badge(MY_ACTION_LABEL[act] || act, cls);
  }

  /** 列表行紧凑闸门片（槽位名 + 类别；完整理由在详情卡，行内不堆文案）。 */
  function gateChipHtml(ctx, slot) {
    const UI = ctx.UI;
    const key = String(slot || "");
    if (!key) return "";
    // 离线演示：后端不可达时 state.gates 为空数组（真值！）——必须判长度，
    // 否则 `||` 兜底永不触发。兜底目录由 tools/gen_gate_fallback.py 从同一份
    // gate_reasons.yaml 生成，测试守护字节一致，不存在第二事实源。
    const gateList = (ctx.state && ctx.state.gates && ctx.state.gates.length)
      ? ctx.state.gates
      : ((ctx.DEMO && ctx.DEMO.GATE_REASONS) || []);
    const gate = gateList.find((g) => g && g.key === key) || null;
    const name = (gate && gate.name) || SLOT_LABEL[key] || key;
    const cls = gate && gate.gate_class ? ` · ${gate.gate_class}` : "";
    return `<span class="hli-gate">${UI.esc(name + cls)}</span>`;
  }

  function hitlListItemHtml(ctx, t) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    const processed = processedMap(ctx)[t.task_uid];
    const mine = !!t.my_action; // 需要当前登录岗位动作的待办 → 置顶 + 醒目标识
    const due = slaDueIso(t);
    const total = slaTotalMs(t);
    const selected = st.hitlSelected === t.task_uid;
    const signoff = signoffOf(t.task_uid, (t.context && t.context.actions) || []);
    const progress = signoff.total
      ? `<span>会签 ${UI.esc(signoff.progress)}${signoff.satisfied ? " ✓" : ""}</span>` : "";
    return `<div class="hitl-list-item${mine ? " mine" : ""}${selected ? " selected" : ""}${processed ? " processed" : ""}" data-select="${UI.esc(t.task_uid)}">
      ${mine && !processed ? `<div class="hli-mine-flag">${UI.icon("zap", 12)}我的待办 · ${UI.esc(MY_ACTION_LABEL[t.my_action] || "需我处理")}</div>` : ""}
      <div class="hli-title">${UI.esc(t.hitl_prompt || t.title)}
        ${processed ? UI.badge("已处理", "badge-green") : UI.badge("待审批", "badge-red", true)}
        ${myActionBadge(ctx, t)}
        ${t._readonly ? UI.badge("只读", "badge-gray") : ""}
        ${t.demo ? UI.demoFlag("演示") : ""}
      </div>
      <div class="hli-meta">
        <span>${UI.esc(t.task_uid)}</span>
        <span>${UI.esc((t.assignees || []).join("、"))}</span>
        <span>${UI.esc(t.priority || "P1")}</span>
        ${gateChipHtml(ctx, slotOf(t))}
        ${progress}
      </div>
      <div class="hli-sla">${processed ? "" : slaHtml(due, total)}</div>
    </div>`;
  }

  // HITL 槽位 → 中文名（与 permissions.yaml 的 slots 对齐）
  const SLOT_LABEL = {
    restart: "复线决策", eta: "对客 ETA 承诺", disposition: "来料处置",
    d8_close: "8D 报告关闭", complaint_reply: "对客回复",
    delivery_commit: "交期承诺", maintenance_window: "停机窗口",
    // 2026-10-22 补齐 T15/T16 三个既有槽位（原映射缺这三项 → 审批中心
    // 一直渲染裸 key，如 "order_review"）；spc_action 同批补
    spc_action: "过程处置会签", order_review: "接单评审会签",
    report_publish: "报告审定与派发",
  };

  // 工单状态 → 中文（面向非技术审批人，界面不暴露状态机内部枚举）
  const STATUS_LABEL = {
    pending: "待启动", running: "执行中", waiting_hitl: "待人工确认",
    escalated: "已升级", done: "已完成", closed: "已关闭",
  };
  const statusLabel = (s) => STATUS_LABEL[s] || s || "-";

  // 处理历史动作 → 中文
  const HISTORY_ACTION_LABEL = {
    create: "创建工单", start: "开始执行", request_hitl: "发起人工确认",
    select_action: "选定处置动作", cosign: "会签", approve: "审批通过",
    reject: "驳回", escalate: "超时升级", done: "办结", close: "关闭",
  };
  const historyActionLabel = (a) => HISTORY_ACTION_LABEL[a] || a || "-";

  /** 报告中心 · 问数归档列表（点击展开全文；竞品方案：报告生命周期管理）。 */
  function renderQaArchive(ctx) {
    const box = document.getElementById("qa-archive-list");
    if (!box) return;
    const UI = ctx.UI;
    const items = loadReportArchive();
    if (!items.length) {
      box.innerHTML = `<div class="empty" style="font-size: calc(12.3px * var(--font-scale));color:var(--text-3)">暂无归档——在「协同问数」发起提问后，生成的报告会自动归档到这里</div>`;
      return;
    }
    box.innerHTML = items.map((r, i) => `
      <div class="qa-archive-item" data-rid="${i}">
        <div class="qar-head">
          <span class="badge badge-blue">${UI.esc(r.scenario)}</span>
          <span style="font-size: calc(12.5px * var(--font-scale));font-weight:600">${UI.esc(r.title)}</span>
          <span style="margin-left:auto;font-size: calc(11px * var(--font-scale));color:var(--text-3)">${UI.esc((r.ts || "").slice(5, 16).replace("T", " "))}</span>
        </div>
        <div style="font-size: calc(12px * var(--font-scale));color:var(--text-2);margin-top:3px">${UI.esc(r.summary)}…</div>
        ${r._open ? `<div class="report-block" style="margin-top:9px"><div class="report-block-title">报告全文（${UI.esc(r.strategy || "")} · 溯源 ${r.provenance_count} 条）</div><div class="report-block-body">${UI.esc(r.content)}</div></div>` : ""}
      </div>`).join("");
    box.querySelectorAll("[data-rid]").forEach((it) => {
      it.style.cursor = "pointer";
      it.addEventListener("click", () => {
        const items2 = loadReportArchive();
        const idx = Number(it.dataset.rid);
        const all = JSON.parse(localStorage.getItem("report_archive") || "[]");
        all[idx]._open = !all[idx]._open;
        localStorage.setItem("report_archive", JSON.stringify(all));
        renderQaArchive(ctx);
      });
    });
  }

  // 决策指引（按槽位：审批人该看什么、容易漏什么——降低经验依赖）
  const DECISION_GUIDES = {
    disposition: ["质量影响：不良是否影响功能/安全，还是仅外观？", "客户影响：该批次是否已发货？客户标准是否允许？", "成本与交期：退货/挑选的工时与费用 vs 特采的风险", "留痕：选择依据必须具体（写清参数/数据），便于追溯"],
    restart: ["首件确认：复线后第一件是否已检验合格？", "参数复核：漂移根因是否已消除（非临时调整）？", "关联预警：相关 SPC/设备预警是否已闭环？"],
    eta: ["产能确认：承诺时间是否有排产数据支撑？", "缓冲策略：是否预留合理缓冲（客户容忍度）？", "口径一致：与内部影响评估结论是否一致？"],
    d8_close: ["措施验证：纠正措施是否有数据证明有效？", "预防措施：是否已纳入标准（SOP/控制计划）？", "客户确认：客户是否已认可关闭？"],
    complaint_reply: ["口径一致：回复内容与 8D 结论是否一致？", "禁词检查：是否含停线/根因等内部词汇？", "承诺升级：是否做了内部无保障的承诺？"],
    delivery_commit: ["ATP 核算：可承诺量是否经库存与排产双重确认？", "客户分级：重点客户是否需要更高优先级？", "物流排期：出货计划是否与仓库/物流确认？"],
    maintenance_window: ["备件到位：维保所需备件是否确认可用？", "生产影响：停机窗口与排产的冲突是否评估？", "降速替代：是否可先降速运行观察？"],
  };

  function decisionGuideHtml(t) {
    const slot = (t.governance || {}).slot || t.wf_id || "";
    const tips = DECISION_GUIDES[slot];
    if (!tips || !tips.length) return "";
    return `<div class="decision-guide">
      <div class="dg-title">决策指引（${SLOT_LABEL[slot] || slot} · 审批前请逐项核对）</div>
      <ul>${tips.map((x) => `<li>${x}</li>`).join("")}</ul>
    </div>`;
  }

  /** 毫秒 → 中文可读时长（45秒 / 1分32秒），供「各职能部门耗时」展示。 */
  function fmtDurationMs(ms) {
    const n = Number(ms);
    if (!Number.isFinite(n)) return "-";
    if (n < 1000) return Math.round(n) + "毫秒";
    const s = Math.round(n / 1000);
    if (s < 60) return s + "秒";
    return Math.floor(s / 60) + "分" + String(s % 60).padStart(2, "0") + "秒";
  }

  /* 各环节「拟真」处理时长（秒）：后端 elapsed_ms 是机器计时（0.x–2s 级），
   * 直接透出对审批人既不可读也不可信；按环节角色给出业务量级的处理时长。
   * 同一环节的抖动由 id 派生（±18%）→ 刷新不跳动、观感自然。 */
  const NODE_COST_SEC = {
    // S1 停线
    business: 5, intake: 5, confirm_stop: 21, quality: 16, supply_chain: 14,
    equipment: 12, fanin: 3, fa: 88, impact_wf: 34, hitl_restart: 9, hitl_eta: 8, report: 12,
    // S2 来料
    sqe_assess: 25, procurement: 42, planning: 23, material_control: 19,
    scar_wf: 51, hitl_disposition: 9, report_incoming: 10,
    // S3 客诉 8D
    qa_lead: 96, pqe_process: 62, sqe_supplier: 48, engineering: 75, business_sales: 37,
    capa_flow: 58, hitl_d8_close: 11, hitl_complaint_reply: 9, report_complaint: 14,
    // S4 出货 / 订单变更
    business_order: 18, warehouse_atp: 24, manufacturing_output: 15, logistics: 13,
    atp_analysis: 32, delivery_risk_wf: 45, change_flow: 40, manufacturing: 27,
    hitl_delivery_commit: 9, report_delivery: 11,
    // S8 设备
    equipment_check: 12, maintenance: 28, planning_window: 16, engineering_param: 31,
    spare_parts: 21, maintenance_flow: 39, hitl_maintenance_window: 8, report_equipment: 10,
    // 办公助手 / MCP 单工具工单（无 DAG 轨迹，按工具调用链补造留痕）
    office_intent: 5, office_room_check: 16, office_book: 23, office_catering_check: 14,
    office_order: 21, office_handle: 18, mcp_intent: 6, mcp_tool_run: 29,
    quality_8d_trace: 34, quality_8d_draft: 47,
  };

  /** 环节展示名：优先后端下发的**中文** label；英文/缺失时用节点中文名兜底，
   * 再退化到域中文名——保证耗时汇总里不出现 complaint_8d_flow 这类英文标识。 */
  function nodeDisplayLabel(d) {
    const label = String((d && d.label) || "");
    if (/[\u4e00-\u9fa5]/.test(label)) return label;
    return NODE_LABELS[(d && d.node) || ""]
      || term("domainLabel", (d && d.domain) || "") || "-";
  }

  /** 环节耗时（毫秒）：``sim`` 快照（拟真留痕）直接用后端存的值（每条工单已带各自耗时）；
   *  真实链路快照按登记表折算，未登记环节按机器耗时确定性折算，兜底 8–90 秒。 */
  function nodeCostMs(d, sim) {
    if (sim) return Number((d && d.elapsed_ms)) || 0;
    const id = String((d && (d.node || d.label)) || "");
    let sec = NODE_COST_SEC[id];
    if (!sec) sec = 8 + Math.sqrt(Math.max(Number((d && d.elapsed_ms)) || 0, 0)) * 1.8;
    let h = 0;
    for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 997;
    return Math.round(sec * (0.82 + (h % 37) / 100)) * 1000;
  }

  /**
   * 审批摘要卡：面向审批人（尤其高层）把「为什么批、批到哪了、卡在谁、
   * 各域花了多久、依据是什么」一次讲清——不用点开报告也能判断。
   * 数据来自工单 context（reason / flow / brief，后端 hitl 工厂写入）；
   * 演示工单或老工单无此字段时整卡不渲染（优雅降级）。
   */
  function approveSummaryHtml(ctx, t) {
    const UI = ctx.UI;
    const c = t.context || {};
    const flow = c.flow || {};
    const sim = !!flow.sim;   // 拟真留痕快照：耗时直接取工单已存值（逐单不同）
    const done = Array.isArray(flow.completed) ? flow.completed : [];
    const brief = Array.isArray(c.brief) ? c.brief : [];
    const reason = c.reason || "";
    if (!reason && !done.length && !brief.length) return "";

    // 流程进展轨：已完成节点 ✓ → 当前等待节点 ⏳
    const nodeHtml = done.map((d) => {
      const tone = d.status === "ok" ? "done" : "warn";
      const name = nodeDisplayLabel(d);
      return `<span class="as-node ${tone}" title="${UI.esc(name)} · 处理耗时 ${fmtDurationMs(nodeCostMs(d, sim))}">
        ${UI.icon("check", 11)} ${UI.esc(name)}</span>`;
    }).join('<span class="as-arrow">→</span>');
    const curLabel = (flow.current && (flow.current.label || flow.current.node))
      || t.hitl_prompt || t.title || "人工确认";
    const currentNode = `<span class="as-node current">⏳ ${UI.esc(curLabel)}</span>`;

    // 卡点：谁在等 / 是否已超 SLA
    const processed = processedMap(ctx)[t.task_uid];
    const due = slaDueIso(t);
    const overdue = Boolean(due) && Date.parse(due) < Date.now();
    const who = (t.assignees || []).join("、") || "-";
    let blocker;
    if (processed) blocker = `已由 ${UI.esc(who)} 处理（${UI.esc(processed.status || "")}）`;
    else if (t._readonly) blocker = `等待 ${UI.esc(who)} 处理（当前岗位只读）`;
    else blocker = MY_ACTION_LABEL[t.my_action]
      ? `等待我：${UI.esc(MY_ACTION_LABEL[t.my_action])}`
      : `等待 ${UI.esc(who)} 确认`;

    // 各职能部门耗时：拟真处理时长，按最大值归一化的横条
    const costs = done.map((d) => ({ d, ms: nodeCostMs(d, sim) }));
    const maxMs = Math.max.apply(null, costs.map((c) => c.ms).concat([1]));
    const totalMs = costs.reduce((a, c) => a + c.ms, 0);
    const costHtml = costs.map(({ d, ms }) => {
      const w = Math.max(6, Math.round((ms / maxMs) * 100));
      const tone = d.status === "ok" ? "green" : "amber";
      const name = nodeDisplayLabel(d);
      return `<div class="as-cost-row">
        <span class="as-cost-name" title="${UI.esc(name)}">${UI.esc(name)}</span>
        <span class="as-cost-bar"><i class="${tone}" style="width:${w}%"></i></span>
        <span class="as-cost-val">${fmtDurationMs(ms)}</span>
      </div>`;
    }).join("");

    const briefHtml = brief.length ? `<div class="as-sec">
        <div class="as-sec-title">${UI.icon("report", 13)} 报告依据（上游各域结论）</div>
        ${brief.map((b) => `<div class="as-brief-row">
          <span class="as-brief-label">${UI.esc(b.label || "")}</span>
          <span class="as-brief-text">${UI.esc(b.summary || "")}</span></div>`).join("")}
      </div>` : "";

    return `<div class="approve-summary">
      ${reason ? `<div class="as-reason">${UI.icon("alert", 14)}<span><b>事因</b>${UI.esc(reason)}</span></div>` : ""}
      <div class="as-sec">
        <div class="as-sec-title">${UI.icon("activity", 13)} 流程进展</div>
        <div class="as-flow">${nodeHtml}${done.length ? '<span class="as-arrow">→</span>' : ""}${currentNode}</div>
        <div class="as-blocker ${overdue ? "overdue" : ""}">${UI.icon("clock", 12)} 卡点：${blocker}${overdue ? "（已超 SLA，请优先处理）" : ""}</div>
      </div>
      ${costHtml ? `<div class="as-sec">
        <div class="as-sec-title">${UI.icon("clock", 13)} 各职能部门耗时（合计 ${fmtDurationMs(totalMs)}）
          <span class="as-cost-legend"><i class="green"></i>正常环节<i class="amber"></i>降级环节</span></div>
        ${costHtml}
      </div>` : ""}
      ${briefHtml}
    </div>`;
  }

  function renderHitlDetail(ctx, t) {
    const UI = ctx.UI;
    const box = document.getElementById("hitl-detail");
    if (!box || !t) return;
    const c = t.context || {};
    const due = slaDueIso(t);
    const total = slaTotalMs(t);
    const processed = processedMap(ctx)[t.task_uid];
    const slot = slotOf(t);
    if (slot) _hitlSlot[t.task_uid] = slot;
    const kindLabel = SLOT_LABEL[slot]
      || (t.wf_id === "impact_assessment" ? "影响评估反馈" : (t.wf_id || "工单"));
    if (t.governance && !_hitlGov[t.task_uid]) _hitlGov[t.task_uid] = t;
    const gate = approveGate(ctx, t.task_uid, (c.actions || []), slot);

    const ctxItems = [
      ["工单类型", kindLabel],
      ["治理槽位", slot ? (SLOT_LABEL[slot] || slot) : "未纳管（老工单）"],
      ["已选动作", signoffOf(t.task_uid, c.actions).selected_action || "-"],
      ["我的待办", MY_ACTION_LABEL[t.my_action] || "只看不办"],
      ["运行编号", c.run_id || "-"],
      ["线体 / 型号", (c.line_code || "-") + " / " + (c.product_model || "-")],
      ["风险评级", c.fa_rating || "-"],
      ["预估复线", c.eta_hours != null ? c.eta_hours + " 小时" : "-"],
      ["受理人", (t.assignees || []).join("、") || "-"],
      ["创建时间", t.created_at ? UI.fmtDateTime(t.created_at) : "-"],
      ["SLA 截止", UI.fmtDateTime(due)],
    ].map(([k, v]) => `<div class="kv"><span class="kv-key">${UI.esc(k)}</span><span class="kv-val">${UI.esc(v)}</span></div>`).join("");

    const history = (t.history || []).slice().reverse().map((h) => `
      <div class="history-item">
        <span class="h-action">${UI.esc(historyActionLabel(h.action))}</span>
        <span>${UI.esc(statusLabel(h.from || "?"))} → ${UI.esc(statusLabel(h.to || "?"))}</span>
        <span class="h-ts">${UI.esc(h.ts ? UI.fmtDateTime(h.ts) : "")}</span>
        <span style="color:var(--text-3)">${UI.esc(h.actor || "")}${h.comment ? " · " + UI.esc(h.comment) : ""}</span>
      </div>`).join("");

    // 报告中心「就地生成报告」载荷：来源=工单，后端会补真实工单事实（不靠前端编造）
    const rpPayload = UI.esc(JSON.stringify({
      source_module: "task",
      context: {
        title: `工单处置报告 · ${t.title || kindLabel}`,
        task_uid: t.task_uid,
        refs: [t.task_uid],
        summary: `状态 ${t.status || "-"}｜优先级 ${t.priority || "-"}｜治理槽位 ${slot || "未纳管"}`,
      },
    }));

    box.innerHTML = `
      <div class="card card-pad">
        <div class="hitl-detail-head">
          <div class="hitl-detail-title">${UI.esc(t.hitl_prompt || t.title)}</div>
          <div style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap">
            ${processed ? UI.badge("已处理 · " + statusLabel(processed.status), "badge-green") : UI.badge(statusLabel(t.status || "waiting_hitl"), "badge-red", true)}
            ${UI.badge(t.priority || "P1", "badge-amber")}
            ${myActionBadge(ctx, t)}
            ${t.demo ? UI.demoFlag("演示数据") : UI.realFlag("真实工单")}
          </div>
        </div>
        <div style="margin-top:12px;font-size: calc(12.5px * var(--font-scale));color:var(--text-2);line-height:1.75">
          ${UI.icon("info", 13)} 关键决策必须人工确认后放行（规则：分析服务只出建议，不参与审批与过账）。多人会签的动作需全部会签人签署后才可终批，岗位权限由后端工作流引擎校验。
        </div>
        ${gateReasonHtml(ctx, slot)}
        ${signoffGapHtml(ctx, taskSignoff(t), slot, (t.context || {}).actions)}
        ${signoffFlowHtml(ctx, t.task_uid)}
        ${approveSummaryHtml(ctx, t)}
        <div style="margin-top:13px">${slaHtml(due, total)}</div>
        <div class="hitl-ctx-grid" style="margin-top:13px">${ctxItems}</div>
        ${(() => {
          const acts = (t.context && t.context.actions) || [];
          if (!acts.length) return "";
          _hitlActions[t.task_uid] = acts;
          return `<div class="section-title" style="margin:16px 0 4px">处置动作（按岗位权限，灰显需对应岗位发起）</div>
            <div class="act-chips">${actionChipsHtml(ctx, t.task_uid, acts)}</div>
            ${actionDetailHtml(ctx, t.task_uid, acts)}
            ${signoffPanelHtml(ctx, t.task_uid, acts)}`;
        })()}

        ${history ? `<div class="section-title" style="margin:16px 0 4px">处理历史（状态机留痕）</div><div class="history-list">${history}</div>` : ""}
        ${decisionGuideHtml(t) }

        <div class="rp-actions" style="margin-top:12px">
          <button class="btn btn-outline btn-sm" data-report-gen="${rpPayload}">${UI.icon("report", 13)}生成本工单报告</button>
          <span class="rp-note">归档到报告中心：后端按工单真实字段生成简报，可继续走审定 / 派发。</span>
        </div>

        ${processed ? hitlProcessedBanner(ctx, t.task_uid) : (t._readonly
          ? `<div class="gate-hint">${UI.icon("info", 13)} 当前岗位对该工单无待办动作（只读视图）：审批/会签由对应岗位执行，安全校验在后端。</div>`
          : `
        <div class="hitl-actions-bar">
          ${quickCommentChipsHtml(ctx, t.task_uid, (t.governance || {}).slot || "")}
          <input class="input hitl-comment" id="hitl-comment" placeholder="决策意见（可选，将写入工单处理记录）" />
          <button class="btn btn-success" id="hitl-approve"
            ${gate.ok ? "" : `disabled title="${UI.esc(gate.reason)}"`}>${UI.icon("check", 14)}同意</button>
          <button class="btn btn-danger" id="hitl-reject">${UI.icon("x", 14)}驳回</button>
        </div>
        ${gate.ok ? "" : `<div class="gate-hint">${UI.icon("info", 13)} ${UI.esc(gate.reason)}</div>`}`)}
      </div>`;

    wireActionChips(document, ctx, t.task_uid);
    bindQuickComments(document);
    wireCosign(document, ctx, t.task_uid, (c.actions || []));
    wireSignoffFlow(document, ctx, t.task_uid);
    const approveBtn = document.getElementById("hitl-approve");
    const rejectBtn = document.getElementById("hitl-reject");
    if (approveBtn) approveBtn.addEventListener("click", () => hitlAction(ctx, t.task_uid, "approve"));
    if (rejectBtn) rejectBtn.addEventListener("click", () => hitlAction(ctx, t.task_uid, "reject"));
  }

  /* ============================================================
   * 屏幕六：制造综合看板（制造 + 品质 · 双子 Tab）
   * ============================================================ */

  // 指标 status（ok/warn/danger）→ 视觉 tone（green/amber/red）
  const MFG_TONE = { ok: "green", warn: "amber", danger: "red" };
  const MFG_KPI_ICON = {
    availability: "gear", scrap_rate: "box", report_rate: "list", test_fpy: "flask",
    defect_rate: "warn", fpy: "check",
    // 品质看板（行业扩展：进料端 / 客户端）
    incoming_pass_rate: "box", complaint_close_rate: "chat",
    // 订单履约看板
    otd: "truck", otif: "check", delay_rate: "warn", kit_rate: "box",
    // SPC 过程能力看板
    spc_capability: "spc",
  };

  // 品质看板板级「工段 / 缺陷类别」筛选白名单（与后端 QUALITY_STAGES /
  // QUALITY_CATEGORIES 同源：前端只出选项，取值合法性由接口 fail-fast 裁决）。
  const QUALITY_STAGE_CHIPS = ["SMT", "PCM", "PACK", "整机"];
  const QUALITY_CAT_CHIPS = [["性能不良", "性能"], ["外观不良", "外观"]];

  // 制造看板板级「产品分类 / 工段」筛选白名单（与后端 MFG_PRODUCT_CATEGORIES /
  // MFG_STAGES 同源：前端只出选项，取值合法性由接口 fail-fast 裁决）。
  // 后端用固定映射折出这两个维度：工段按产线前缀（SMT-* → SMT、DIP-* → PCM、
  // PACK-* → PACK、ASSY-* → 整机），产品分类按型号归属。
  const MFG_STAGE_CHIPS = ["SMT", "PCM", "PACK", "整机"];
  const MFG_PCAT_OPTIONS = ["消费电子", "智能电源", "储能电源", "车载"];

  // 看板通用配置：下钻容器 id + 面包屑首节点名（制造 / 品质 / 订单履约 共用同一套机件）
  const BOARD_CFG = {
    manufacturing: { name: "制造看板", body: "mfg-body", crumb: "mfg-crumb",
                     win: "mfg-window", flag: "mfg-flag", gen: "mfg-gen" },
    quality: { name: "品质看板", body: "mfg-body", crumb: "mfg-crumb",
               win: "mfg-window", flag: "mfg-flag", gen: "mfg-gen" },
    orders: { name: "订单履约看板", body: "orders-body", crumb: "orders-crumb",
              win: "orders-window", flag: "orders-flag", gen: "orders-gen" },
    spc: { name: "SPC 过程能力看板", body: "spc-body", crumb: "spc-crumb",
           win: "spc-window", flag: "spc-flag", gen: "spc-gen" },
  };

  // 我的看板内联下钻：复用同一套下钻机件，仅把宿主容器换成工作台的看板区
  const DASHBOARD_CFG = { name: "我的看板", body: "dash-body", crumb: "dash-crumb" };

  // 板级筛选在「2 级及以下下钻」里是否仍然生效（与后端 DRILL_DEFS 的 scope / q_scope 声明同源）：
  //   window          = 是否跟随顶部「统计口径」（快照口径 / 来料固定全量口径的指标不跟随）；
  //   stage / category= 品质看板「工段 / 类别」（quality_defect 同源的不良率 / 直通率）；
  //   product_category= 制造看板「产品分类」（四个 KPI 全部声明了 scope；测试直通率无产线 → 工段不适用）。
  // 未登记的 KPI = 全不生效 → 2 级看板整条筛选行不渲染（不给「点了没反应」的假控件）。
  // 后端新增 / 调整筛选声明时，这里必须同步（tests/v2/test_dashboard_drill.py 有交叉校验）。
  const DRILL_FILTER_SCOPE = {
    "manufacturing:availability": { window: true, stage: true, product_category: true },
    "manufacturing:scrap_rate": { window: true, stage: true, product_category: true },
    "manufacturing:report_rate": { window: true, stage: true, product_category: true },
    "manufacturing:test_fpy": { window: true, product_category: true },
    // 制造域另有 oee / production_achieve（仅工作台「我的看板」内联下钻可达）：只跟时间口径，
    // 不吃板级「工段 / 产品分类」——登记在此，保证与后端 DRILL_DEFS 声明逐项对齐（有交叉校验）。
    "manufacturing:oee": { window: true },
    "manufacturing:production_achieve": { window: true },
    "quality:defect_rate": { window: true, stage: true, category: true },
    "quality:fpy": { window: true, stage: true, category: true },
    "quality:incoming_pass_rate": { window: true },
    "quality:test_pass_rate": { window: true },
    "quality:std_conformity": { window: true },
    "quality:incoming_batch_quality": {},
    "quality:incoming_line_defect": {},
    "quality:complaint_close_rate": {},
    // SPC 过程能力看板：两个指标都吃「统计口径 / 工段 / 产品分类」
    // （后端 DRILL_DEFS 两指标均声明 spc_scope，与 L1 卡片同源）
    "spc:spc_capability": { window: true, stage: true, product_category: true },
    "spc:spc_pareto": { window: true, stage: true, product_category: true },
  };
  const BOARD_FILTER_ALL = { window: true, stage: true, category: true, product_category: true };

  /** 当前下钻目标吃哪些板级筛选：L1（无下钻）= 全部生效；标准三率 2 级 = 只跟统计口径；
   *  不良类型下钻的筛选在柏拉图卡片内自选、行自带筛选参数 → 不算板级筛选（整条隐藏）。 */
  function drillFilterScope(drill) {
    if (!drill) return BOARD_FILTER_ALL;
    if (drill.stdStage) return { window: true };
    if (drill.defectType) return {};
    return DRILL_FILTER_SCOPE[`${drill.screen}:${drill.kpi}`] || {};
  }

  /** 下钻宿主配置：host=dashboard 用工作台容器，否则按 screen 取对应看板配置。 */
  function boardCfgOf(drill) {
    return (drill && drill.host === "dashboard")
      ? DASHBOARD_CFG
      : (BOARD_CFG[(drill && drill.screen) || "manufacturing"] || BOARD_CFG.manufacturing);
  }

  /** 下钻实体序列化：优先后端下发的 entity 字典，兼容旧演示数据的 line/model/wo。 */
  function drillEntity(it) {
    if (it && it.entity && Object.keys(it.entity).length) {
      const e = Object.assign({}, it.entity);
      delete e.name; // name 仅供展示，回传会污染下钻查询参数
      return JSON.stringify(e);
    }
    return JSON.stringify({ line: (it && it.line) || "", model: (it && it.model) || "",
                            wo: (it && it.wo) || "" });
  }
  // 指标 status → 中文标签 / 徽标配色（下钻表「状态」列）
  const MFG_STATUS_CN = { ok: "达标", warn: "预警", danger: "危险" };
  const MFG_BADGE = { ok: "badge-green", warn: "badge-amber", danger: "badge-red" };

  /** 迷你趋势线：把后端 kpi.trend（7 点数值数组）画成内联 SVG 折线 + 淡色面积。 */
  function mfgSpark(values, tone) {
    const vals = (values || []).filter((v) => typeof v === "number" && !Number.isNaN(v));
    const w = 132, h = 26, pad = 2;
    const color = { green: "#2DA44E", amber: "#D4A017", red: "#CF222E" }[tone] || "#185FA5";
    // 「当日」口径只有 1 个数据点：画一条水平基准线（容错渲染，不退化为空白）
    if (vals.length === 1) {
      const y = (h / 2).toFixed(1);
      return `<svg class="spark spark-single" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" `
        + `preserveAspectRatio="none" aria-hidden="true">`
        + `<path d="M${pad} ${y} L${w - pad} ${y}" fill="none" stroke="${color}" `
        + `stroke-width="1.6" stroke-linecap="round" stroke-dasharray="4 3"/></svg>`;
    }
    if (vals.length < 2) return "";
    const mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    const rng = (mx - mn) || 1;
    const step = (w - pad * 2) / (vals.length - 1);
    const pts = vals.map((v, i) => [pad + i * step, h - pad - ((v - mn) / rng) * (h - pad * 2)]);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    const area = `${line} L${pts[pts.length - 1][0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" `
      + `preserveAspectRatio="none" aria-hidden="true">`
      + `<path d="${area}" fill="${color}" fill-opacity="0.10"/>`
      + `<path d="${line}" fill="none" stroke="${color}" stroke-width="1.6" `
      + `stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  }

  /** KPI 卡：卡主体点击 = 下钻 L2（明细分解）；卡内「问数」按钮 = 跳协同问数。
   *  ent 非空时（如 SPC 逐特性卡）卡主体直接带 data-drill-entity → 点击直下 L3。 */
  function mfgKpiCard(UI, kpi, screen, ent) {
    const tone = MFG_TONE[kpi.status] || "blue";
    const ok = kpi.status === "ok";
    const delta = (kpi.delta > 0 ? "+" : "") + kpi.delta;
    const dir = kpi.higher_is_better ? (ok ? "已达标" : "低于目标") : (ok ? "已达标" : "高于目标");
    const ico = MFG_KPI_ICON[kpi.key] || "activity";
    const entAttr = ent && Object.keys(ent).length
      ? ` data-drill-entity="${UI.esc(JSON.stringify(ent))}"` : "";
    // R8：每张 KPI 卡都能回到口径词典的同一条目（三源统一 id）
    const dictId = `board.${screen}.${kpi.key}`;
    return `<div class="card stat-card stat-clickable" role="button" tabindex="0"
            data-kpi="${UI.esc(kpi.key)}" data-screen="${UI.esc(screen)}"${entAttr}
            title="点击查看「${UI.esc(kpi.label)}」明细分解">
      <div class="stat-icon ${tone}">${UI.icon(ico, 19)}</div>
      <div class="stat-body">
        <div class="stat-num">${UI.esc(kpi.value)}<span class="stat-unit">${UI.esc(kpi.unit)}</span></div>
        <div class="stat-label">${UI.esc(kpi.label)} · 目标 ${UI.esc(kpi.target)}${UI.esc(kpi.unit)}</div>
        <div class="stat-trend ${ok ? "down" : "up"}">${UI.icon(ok ? "check" : "warn", 12)} ${UI.esc(delta)}pt · ${UI.esc(dir)}</div>
        ${kpi.scope_note ? `<div class="stat-scope">${UI.icon("info", 11)}${UI.esc(kpi.scope_note)}</div>` : ""}
        <div class="spark-wrap">${mfgSpark(kpi.trend, tone)}</div>
        <div class="stat-actions">
          <button class="stat-ask-btn" data-ask="${UI.esc(kpi.ask || "")}" data-ask-view="kpi"
                  title="跳转协同问数：${UI.esc(kpi.ask || "")}">
            ${UI.icon("chat", 11)} 问数</button>
          <button class="stat-def-btn" type="button" data-dict-id="${UI.esc(dictId)}"
                  title="查看「${UI.esc(kpi.label)}」口径定义与引用方">
            ${UI.icon("info", 11)} 口径</button>
        </div>
      </div>
    </div>`;
  }

  /** TOP5 排行卡：行主体点击 = 下钻 L3（该实体明细）；行内「问数」= 跳协同问数。
   *  ``winLabel`` 为板级当前口径（当日/当月/最近7天），``scopeCn`` 为板级「工段 / 类别」
   *  筛选范围（缺省空串），两者叠加进问数文案，避免问数与所看口径不符。 */
  function mfgRankCard(UI, ranking, screen, winLabel, scopeCn) {
    const hb = ranking.higher_is_better;
    const dir = hb ? "越高越好 · 取最低 5" : "越低越好 · 取最高 5";
    const scope = scopeCn ? `${scopeCn} ` : "";
    const ask = `${winLabel || "最近7天"}${scope}${ranking.label}${hb ? "最低" : "最高"}的 5 项分别是哪些？`;
    const rows = (ranking.items || []).map((it) => {
      const worst = it.status === "danger";
      // 后端已按岗位脱敏并移除下钻键（masked:true）→ 行不再可点击。
      // 只剩「客户 A」这种代号还允许下钻，点一下就按代号查回真实客户，等于白脱敏。
      const locked = !!it.masked;
      const ent = locked ? "" : drillEntity(it);
      const drillAttrs = locked
        ? ` title="该行已按当前岗位脱敏，不支持下钻"`
        : ` role="button" tabindex="0" data-drill-entity="${UI.esc(ent)}" data-kpi="${UI.esc(ranking.key)}"
              data-screen="${UI.esc(screen)}"
              title="点击查看「${UI.esc(it.name)}」逐日明细"`;
      return `<div class="rank-row${worst ? " worst" : ""}${locked ? " locked" : ""}"${drillAttrs}>
        <span class="rank-no">${UI.esc(it.rank)}</span>
        <span class="rank-name">${worst ? UI.icon("warn", 12) : ""}${UI.esc(it.name)}</span>
        <span class="rank-val ${UI.esc(it.status)}">${UI.esc(it.value)}${UI.esc(ranking.unit)}</span>
        <button class="row-ask" data-ask="${UI.esc(ask)}" data-ask-view="ranking"
                title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 11)}问数</button>
      </div>`;
    }).join("");
    return `<div class="card">
      <div class="card-head">
        <div class="card-title">${UI.icon("list", 16)}${UI.esc(ranking.label)} · TOP5</div>
        <div class="card-extra">目标 ${UI.esc(ranking.target)}${UI.esc(ranking.unit)} · ${UI.esc(dir)}</div>
      </div>
      ${rows ? `<div class="rank-list">${rows}</div>` : `<div class="empty">暂无数据</div>`}
    </div>`;
  }

  /** 来料质量 TOP10 卡（``view="incoming_top10"`` 专用）：**固定全量口径，不受任何筛选影响**。
   *  - 卡主体点击 = 2 级看板（该指标按供应商**全量**分解，不只 TOP10）；
   *  - 行主体点击 = 3 级明细（来料批次合格率 → 详细 IQC 检验数据；上线不良 → 上线不良明细）；
   *  - 行内「问数」= 跳协同问数。 */
  function mfgIncomingTopCard(UI, ranking) {
    const hb = ranking.higher_is_better;
    const dir = hb ? "越高越好 · 取最低 10" : "越低越好 · 取最高 10";
    const items = ranking.items || [];
    const ask = `${ranking.label}最需关注的 10 家供应商分别是哪些？如何推动供应商改善？`;
    const rows = items.map((it) => {
      const ent = drillEntity(it);
      return `<div class="rank-row${it.status === "danger" ? " worst" : ""}"
          role="button" tabindex="0" data-drill-entity="${UI.esc(ent)}"
          data-kpi="${UI.esc(ranking.key)}" data-screen="quality"
          title="点击下钻 3 级明细：${UI.esc(it.name)}">
        <span class="rank-no">${UI.esc(it.rank)}</span>
        <span class="rank-name">${UI.icon("warn", 12)}${UI.esc(it.name)}</span>
        <span class="rank-val ${UI.esc(it.status)}">${UI.esc(it.value)}${UI.esc(ranking.unit)}</span>
        <button class="row-ask" data-ask="${UI.esc(ask)}" data-ask-view="ranking"
                title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 11)}问数</button>
      </div>`;
    }).join("");
    return `<div class="card stat-clickable" role="button" tabindex="0"
        data-kpi="${UI.esc(ranking.key)}" data-screen="quality"
        title="点击查看「${UI.esc(ranking.label)}」2 级看板（全量供应商分解）">
      <div class="card-head">
        <div class="card-title">${UI.icon("box", 16)}${UI.esc(ranking.label)} · TOP10</div>
        <div class="card-extra">目标 ${hb ? "≥" : "≤"} ${UI.esc(ranking.target)}${UI.esc(ranking.unit)} · ${UI.esc(dir)}</div>
      </div>
      <div class="incoming-note">${UI.icon("shield", 11)}固定全量口径 · 不受统计口径 / 工段 / 类别筛选影响
        · 点卡片看全量供应商 2 级看板 · 点行看详细明细</div>
      ${rows ? `<div class="rank-list">${rows}</div>` : `<div class="empty">暂无数据</div>`}
    </div>`;
  }

  /** 标准三率 1 级看板（工段）：整体公式 + 4 个工段卡；点工段卡**下钻**到 2 级班组看板。
   *  符合率 = 完整率 × 完善率 × 遵守率；工段/班组与整体同一套口径（后端 _std_by_stage）。
   *  ``by_stage`` 缺失（后端旧版 / 旧缓存演示数据）时退回原「分型号」表，不白屏。 */
  /** 三率数值统一 1 位小数：整数 80 → 80.0。
   *  否则同一屏会出现「88%」与「87.6%」混排，评审看着像口径不一致（其实只是 JS 掉了 .0）。 */
  function stdPct(v) {
    const n = Number(v);
    return Number.isFinite(n) ? (Math.round(n * 10) / 10).toFixed(1) : "—";
  }

  function mfgStdThreeRates(UI, s) {
    if (!s || !s.overall) return "";
    const o = s.overall;
    const stages = s.by_stage || [];
    const cards = stages.map((st) => {
      const bars = [["完整", st.completeness_rate], ["完善", st.perfection_rate],
                    ["遵守", st.compliance_rate]].map(([name, v]) => `
          <span class="std-bar-row"><i>${name}</i>
            <span class="std-bar"><b class="${UI.esc(st.status)}"
              style="width:${Math.max(0, Math.min(100, Number(v) || 0))}%"></b></span>
            <em>${UI.esc(stdPct(v))}%</em>
          </span>`).join("");
      return `<button type="button" class="std-stage" data-std-stage="${UI.esc(st.stage)}"
          title="下钻查看「${UI.esc(st.stage)}」各班组（2 级看板）">
        <span class="std-stage-head">
          <span class="std-stage-name">${UI.esc(st.stage)}</span>
          <span class="std-stage-line">${UI.esc(st.line || "—")}</span>
        </span>
        <span class="std-stage-val std-conform ${UI.esc(st.status)}">${UI.esc(stdPct(st.conformity_rate))}<i>%</i></span>
        <span class="std-stage-bars">${bars}</span>
        <span class="std-stage-foot">${UI.esc(st.team_count)} 个班组 · ${UI.esc(st.audit_rows)} 条审核
          <span class="std-stage-go">班组明细${UI.icon("arrow", 11)}</span></span>
      </button>`;
    }).join("");
    const modelRows = (s.by_model || []).map((m) => `<tr>
      <td>${UI.esc(m.model)}</td>
      <td>${UI.esc(stdPct(m.completeness_rate))}%</td>
      <td>${UI.esc(stdPct(m.perfection_rate))}%</td>
      <td>${UI.esc(stdPct(m.compliance_rate))}%</td>
      <td class="std-conform ${UI.esc(m.status)}">${UI.esc(stdPct(m.conformity_rate))}%</td>
    </tr>`).join("");
    return `<div class="card std-card">
      <div class="card-head">
        <div class="card-title">${UI.icon("shield", 16)}标准三率 · 符合率</div>
        <div class="card-extra">符合率 = 完整率 × 完善率 × 遵守率</div>
      </div>
      <div class="std-formula">
        <span class="std-term"><b>${UI.esc(stdPct(o.completeness_rate))}%</b><i>完整率</i></span>
        <em>×</em>
        <span class="std-term"><b>${UI.esc(stdPct(o.perfection_rate))}%</b><i>完善率</i></span>
        <em>×</em>
        <span class="std-term"><b>${UI.esc(stdPct(o.compliance_rate))}%</b><i>遵守率</i></span>
        <em>=</em>
        <span class="std-conform ${UI.esc(o.status)}">${UI.esc(stdPct(o.conformity_rate))}%</span>
        <span class="std-scope">整体 · ${UI.esc(o.audit_rows || 0)} 条审核记录</span>
      </div>
      ${stages.length ? `<div class="std-level">
          <span class="std-level-tag">1 级看板 · 工段</span>
          <span class="std-level-hint">点任一工段卡下钻到 2 级看板（该工段班组明细 + 汇总）· 四段加权即回整体</span>
        </div>
        <div class="grid grid-4 std-stage-grid">${cards}</div>`
        : (modelRows ? `<table class="std-table">
          <thead><tr><th>型号</th><th>完整率</th><th>完善率</th><th>遵守率</th><th>符合率</th></tr></thead>
          <tbody>${modelRows}</tbody>
        </table>` : "")}
    </div>`;
  }

  /** 该工段窗口内 TOP3 不良类型：与累计柏拉图同源（pareto_fact 的 stage 维度本地切片）。
   *  仅作下钻页的「为什么这一段差」参照，不参与三率计算；无数据返回空数组。 */
  function stdStageTopDefects(d, stageName, limit) {
    const rows = ((d && d.pareto_fact) || {}).rows || [];
    if (!rows.length) return [];
    const w = d.window || {};
    const inWin = (r) => (!w.start || r.d >= w.start) && (!w.end || r.d <= w.end);
    const by = {};
    rows.filter((r) => r.stage === stageName && inWin(r))
      .forEach((r) => { by[r.type] = (by[r.type] || 0) + (r.qty || 0); });
    const total = Object.keys(by).reduce((a, k) => a + by[k], 0);
    return Object.keys(by).map((k) => ({ name: k, qty: by[k] }))
      .sort((a, b) => b.qty - a.qty).slice(0, limit || 3)
      .map((x) => ({ name: x.name, qty: x.qty,
                     share: total ? Math.round((x.qty / total) * 1000) / 10 : 0 }));
  }

  /** 标准三率 2 级看板（下钻页）：工段汇总（公式 + 4 个派生指标）+ 班组明细 + TOP3 不良。
   *  汇总与明细来自同一批审核记录（后端按工段/班组分别聚合），明细加权即回汇总。 */
  function stdL2Body(UI, d, stage) {
    const s = (d && d.standard_three_rates) || {};
    const o = s.overall || {};
    const t = s.targets || {};
    // 目标符合率 = 三个目标率相乘（与后端 _STD_CONFORMITY_TARGET 同算：100×98×90/10000 = 88.2）
    const target = Math.round((t.completeness_rate || 100) * (t.perfection_rate || 98)
                              * (t.compliance_rate || 90)) / 10000;
    const teams = (stage.teams || []).slice()
      .sort((a, b) => a.conformity_rate - b.conformity_rate);
    const okCnt = teams.filter((x) => x.status === "ok").length;
    const badCnt = teams.filter((x) => x.status !== "ok").length;
    const worst = teams[0];
    const gap = Math.round((stage.conformity_rate - target) * 10) / 10;
    const vsOverall = Math.round((stage.conformity_rate - (o.conformity_rate || 0)) * 10) / 10;
    const rows = teams.map((x, i) => {
      const delta = Math.round((x.conformity_rate - stage.conformity_rate) * 10) / 10;
      return `<tr class="std-row-click" data-std-team="${UI.esc(x.team)}"
          data-std-team-stage="${UI.esc(stage.stage)}"
          title="查看「${UI.esc(x.team)}」的三率拆解与不良原因">
        <td class="std-team">${i < 3 ? `<span class="std-top-tag">TOP${i + 1}</span>` : ""}${
          x.status === "danger" ? UI.icon("warn", 11) : ""}${UI.esc(x.team)}
          <span class="std-row-go">${UI.icon("arrow", 10)}</span></td>
        <td>${UI.esc(stdPct(x.completeness_rate))}%</td>
        <td>${UI.esc(stdPct(x.perfection_rate))}%</td>
        <td>${UI.esc(stdPct(x.compliance_rate))}%</td>
        <td class="std-conform ${UI.esc(x.status)}">${UI.esc(stdPct(x.conformity_rate))}%</td>
        <td class="std-delta ${delta < 0 ? "neg" : "pos"}">${delta > 0 ? "+" : ""}${UI.esc(stdPct(delta))}pt</td>
        <td>${UI.badge(MFG_STATUS_CN[x.status] || x.status, MFG_BADGE[x.status] || "badge-gray")}</td>
        <td class="std-dim">${UI.esc(x.audit_rows)} 条 · ${UI.esc(x.audit_items)} 项</td>
      </tr>`;
    }).join("");
    const topDefects = stdStageTopDefects(d, stage.stage, 3);
    const defBlock = topDefects.length
      ? `<div class="std-topdef">
          <span class="std-topdef-title">${UI.icon("list", 12)}该工段 TOP3 不良类型
            <i>窗口内累计 · 与累计柏拉图同源</i></span>
          <span class="std-topdef-list">${topDefects.map((x, i) => `<span class="std-topdef-item">
            <b>#${i + 1}</b>${UI.esc(x.name)}<em>${UI.esc(x.qty)} 件 · ${UI.esc(x.share)}%</em>
          </span>`).join("")}</span>
        </div>` : "";
    return `<div class="section-title">标准三率 · 2 级看板（${UI.esc(stage.stage)}）
        <span class="section-extra">汇总 = 该工段全部班组审核记录加权 · 与整体同一套口径</span></div>
      <div class="card std-card">
        <div class="std-l2-head">
          <span class="std-l2-tag">2 级看板 · 班组</span>
          <span class="std-l2-name">${UI.esc(stage.stage)}${stage.line ? " · " + UI.esc(stage.line) : ""}</span>
          <span class="std-l2-sum">全厂 ${UI.esc(stdPct(o.conformity_rate))}% · 该段 ${
            vsOverall > 0 ? "+" : ""}${UI.esc(stdPct(vsOverall))}pt ｜ 目标 ${UI.esc(stdPct(target))}%</span>
        </div>
        <div class="std-formula">
          <span class="std-term"><b>${UI.esc(stdPct(stage.completeness_rate))}%</b><i>完整率</i></span>
          <em>×</em>
          <span class="std-term"><b>${UI.esc(stdPct(stage.perfection_rate))}%</b><i>完善率</i></span>
          <em>×</em>
          <span class="std-term"><b>${UI.esc(stdPct(stage.compliance_rate))}%</b><i>遵守率</i></span>
          <em>=</em>
          <span class="std-conform ${UI.esc(stage.status)}">${UI.esc(stdPct(stage.conformity_rate))}%</span>
          <span class="std-scope">该工段汇总 · ${UI.esc(stage.audit_rows)} 条 / ${
            UI.esc(stage.audit_items)} 项审核</span>
        </div>
        <div class="grid grid-4 std-kpis">
          <div class="std-mini"><i>班组达标</i><b>${UI.esc(okCnt)} / ${UI.esc(teams.length)}</b>
            <em>未达标 ${UI.esc(badCnt)} 个</em></div>
          <div class="std-mini"><i>最低班组</i>
            <b class="std-conform ${worst ? UI.esc(worst.status) : ""}">${worst ? UI.esc(stdPct(worst.conformity_rate)) : "—"}%</b>
            <em>${worst ? UI.esc(worst.team) : "无班组数据"}</em></div>
          <div class="std-mini"><i>与目标差</i>
            <b class="${gap < 0 ? "std-neg" : "std-pos"}">${gap > 0 ? "+" : ""}${UI.esc(stdPct(gap))}pt</b>
            <em>目标 ${UI.esc(stdPct(target))}%</em></div>
          <div class="std-mini"><i>短板环节</i>
            <b>${UI.esc(stdPct(Math.min(stage.completeness_rate, stage.perfection_rate, stage.compliance_rate)))}%</b>
            <em>${stage.completeness_rate <= stage.perfection_rate && stage.completeness_rate <= stage.compliance_rate
              ? "完整率最低（标准缺失）"
              : (stage.perfection_rate <= stage.compliance_rate ? "完善率最低（标准与现场不一致）"
                : "遵守率最低（现场未按标准作业）")}</em></div>
        </div>
        ${defBlock}
        ${rows ? `<table class="std-table std-team-table">
          <thead><tr><th>班组</th><th>完整率</th><th>完善率</th><th>遵守率</th><th>符合率</th>
            <th>与工段差</th><th>状态</th><th>审核量</th></tr></thead>
          <tbody>${rows}</tbody></table>
          <div class="std-l2-note">${teams.length} 个班组按符合率升序（最差在前，TOP1–3 标出需关注）；<b>单击任一班组行</b>可看该班组的三率拆解与不良原因；审核量 = 审核条数 · 审核项数——样本少时别急着下结论。</div>`
          : `<div class="empty">该工段暂无班组级审核记录</div>`}
      </div>`;
  }

  /** 班组弹窗正文：三率公式 + 与工段/全厂/目标差 + 按工序拆解 + 不良原因归因 + 逐日趋势。
   *  离线（接口取不到）时退化为「只有比率」，并在顶部明说缺的是什么——绝不编原因。 */
  function stdTeamModalBody(UI, d, opts) {
    const o = d || {};
    const r = o.rates || {};
    const w = o.window || {};
    const t = o.targets || {};
    const target = o.target_conformity
      || Math.round((t.completeness_rate || 100) * (t.perfection_rate || 98)
                    * (t.compliance_rate || 90)) / 10000;
    const dlt = o.deltas || {};
    const dims = o.dim_totals || {};
    const chip = (label, v, unit) => `<span class="std-chip">${UI.esc(label)}
      <b class="${v > 0 ? "pos" : (v < 0 ? "neg" : "")}">${v > 0 ? "+" : ""}${UI.esc(stdPct(v))}${UI.esc(unit || "pt")}</b></span>`;
    const offlineTip = opts.offline
      ? `<div class="std-modal-warn">${UI.icon("warn", 12)}
         接口未取到该班组明细（后端离线或权限受限），下面只有 2 级看板已缓存的比率，
         <b>不含</b>工序拆解与原因归因。</div>` : "";
    if (!o.found && !opts.offline) {
      return `<div class="std-modal-body">${offlineTip}
        <div class="empty">${UI.icon("info", 26)}
          <div style="margin-top:8px">该班组在「${UI.esc(w.label || "当前口径")}」内没有审核记录。</div>
          <div class="std-modal-hint">换个口径（当日 / 当月）或到 2 级看板看其他班组。</div>
        </div></div>`;
    }

    const stepRows = (o.by_step || []).map((s) => `<tr>
        <td class="std-step-name">${UI.esc(s.step)}</td>
        <td>${UI.esc(stdPct(s.completeness_rate))}%</td>
        <td>${UI.esc(stdPct(s.perfection_rate))}%</td>
        <td>${UI.esc(stdPct(s.compliance_rate))}%</td>
        <td class="std-conform ${UI.esc(s.status)}">${UI.esc(stdPct(s.conformity_rate))}%</td>
        <td class="std-dim">${UI.esc(s.nonconform_items)} 项 / ${UI.esc(s.audit_items)}</td>
      </tr>`).join("");

    const DIM_ORDER = ["completeness", "perfection", "compliance"];
    const DIM_UNIT = { completeness: "行", perfection: "行", compliance: "项" };
    const DIM_GAP = { completeness: "缺标准文件", perfection: "标准不完善", compliance: "不符合项" };
    const groups = DIM_ORDER.map((dim) => {
      const items = (o.causes || []).filter((c) => c.rate_dim === dim);
      if (!items.length) return "";
      const gap = dims[dim] || 0;
      const bar = items.map((c) => `<div class="std-cause-item">
          <div class="std-cause-line">
            <span class="std-cause-name">${c.is_primary ? '<i class="std-cause-flag">主因</i>' : ""}${UI.esc(c.cause)}</span>
            <b>${UI.esc(c.occurrences)} ${UI.esc(c.unit || "项")}</b>
          </div>
          <div class="std-cause-bar"><i style="width:${Math.max(2, Math.min(100, Number(c.share) || 0))}%"></i></div>
          <div class="std-cause-meta">占本维度 ${UI.esc(stdPct(c.share))}%
            ${(c.steps || []).length ? `· 涉及 ${c.steps.map((s) => UI.esc(s)).join(" / ")}` : ""}
            ${c.defect_type ? `· 关联不良 ${UI.esc(c.defect_type)}` : ""}</div>
        </div>`).join("");
      return `<div class="std-cause-dim">
          <div class="std-cause-dim-head">
            <b>${UI.esc(items[0].rate_dim_label || dim)}</b>
            <em>${UI.esc(DIM_GAP[dim] || "")} ${UI.esc(gap)} ${UI.esc(DIM_UNIT[dim] || "项")}</em>
          </div>${bar}
        </div>`;
    }).join("");

    const trend = (o.by_day || []).map((x) => x.conformity_rate);
    const trendVals = trend.filter((v) => typeof v === "number");
    const first = trendVals.length ? trendVals[0] : null;
    const last = trendVals.length ? trendVals[trendVals.length - 1] : null;
    const trendBlock = trendVals.length ? `<div class="std-modal-trend">
        <span class="std-modal-trend-label">逐日符合率
          <i>${UI.esc(w.start || "")} ~ ${UI.esc(w.end || "")}</i></span>
        ${mfgSpark(trend, r.status === "ok" ? "green" : (r.status === "warn" ? "amber" : "red"))}
        <span class="std-modal-trend-val">首 ${UI.esc(stdPct(first))}% → 末 ${UI.esc(stdPct(last))}%</span>
      </div>` : "";

    const notes = (o.notes || []).map((n) => `<div class="std-modal-note">${UI.icon("info", 11)}<span>${UI.esc(n)}</span></div>`).join("");

    return `<div class="std-modal-body">${offlineTip}
      <div class="std-formula">
        <span class="std-term"><b>${UI.esc(stdPct(r.completeness_rate))}%</b><i>完整率</i></span>
        <em>×</em>
        <span class="std-term"><b>${UI.esc(stdPct(r.perfection_rate))}%</b><i>完善率</i></span>
        <em>×</em>
        <span class="std-term"><b>${UI.esc(stdPct(r.compliance_rate))}%</b><i>遵守率</i></span>
        <em>=</em>
        <span class="std-conform ${UI.esc(r.status)}">${UI.esc(stdPct(r.conformity_rate))}%</span>
        <span class="std-scope">${UI.esc(w.label || "")} · ${UI.esc(r.audit_rows || 0)} 条 / ${UI.esc(r.audit_items || 0)} 项</span>
      </div>
      <div class="std-chips">
        ${chip("与工段差", dlt.vs_stage || 0)}
        ${chip("与全厂差", dlt.vs_overall || 0)}
        ${chip("与目标差", dlt.vs_target || 0)}
        <span class="std-chip">目标符合率 <b>${UI.esc(stdPct(target))}%</b></span>
      </div>
      <div class="std-modal-cols">
        <div class="std-modal-col">
          <div class="std-modal-title">${UI.icon("list", 12)}按工序拆解
            <i>哪道工序把三率拉下来（同口径逐工序重算）</i></div>
          ${stepRows ? `<table class="std-table std-step-table">
            <thead><tr><th>工序</th><th>完整率</th><th>完善率</th><th>遵守率</th>
              <th>符合率</th><th>不符合 / 审核项</th></tr></thead>
            <tbody>${stepRows}</tbody></table>` : `<div class="empty">该口径内无工序级明细</div>`}
        </div>
        <div class="std-modal-col">
          <div class="std-modal-title">${UI.icon("shield", 12)}不良原因归因
            <i>按三率维度归因 · 数量与明细可对账</i></div>
          ${groups || `<div class="empty">该口径内无归因明细</div>`}
        </div>
      </div>
      ${trendBlock}
      ${notes ? `<div class="std-notes">${notes}</div>` : ""}
    </div>`;
  }

  /** 点班组行 → 弹窗。先挂骨架再填数据（接口 200ms~2s，空白等太久会像没反应）。 */
  async function openStdTeamModal(ctx, stage, team, win) {
    const UI = ctx.UI;
    document.querySelectorAll(".std-modal").forEach((el) => el.remove());
    const overlay = document.createElement("div");
    overlay.className = "std-modal";
    overlay.innerHTML = `<div class="std-modal-card" role="dialog" aria-modal="true"
        aria-label="${UI.esc(team)} 三率明细">
        <div class="std-modal-head">
          <span class="std-modal-tag">2 级 · 班组明细</span>
          <b>${UI.esc(team)}</b>
          <span class="std-modal-sub">${UI.esc(stage)}${stage ? " · " : ""}三率拆解与不良原因</span>
          <button type="button" class="std-modal-x" data-std-close aria-label="关闭">×</button>
        </div>
        <div class="std-modal-body"><div class="std-modal-loading">
          ${UI.icon("info", 13)}正在拉取该班组的三率拆解与不良原因…</div></div>
      </div>`;
    document.body.appendChild(overlay);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    function close() {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      document.body.classList.remove("std-modal-open");
    }
    document.addEventListener("keydown", onKey);
    document.body.classList.add("std-modal-open");
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay || (ev.target.closest && ev.target.closest("[data-std-close]"))) close();
    });

    const body = overlay.querySelector(".std-modal-body");
    let data = null;
    let offline = false;
    try {
      if (!ctx.api.online()) throw new Error("offline");
      data = await ctx.api.qualityTeam(stage, team, win || "window");
    } catch (err) {
      offline = true;
    }
    if (!body.isConnected) return;       // 等待期间被关掉了
    // 离线兜底：用 2 级看板缓存的该班组比率拼一个只读骨架（明确标注缺工序/原因）
    if (offline) {
      const st = (ctx.state.screenState || {}).stdPayload || {};
      const cached = ((st.data || {}).standard_three_rates || {}).by_stage || [];
      const hit = (cached.find((x) => x.stage === stage) || {}).teams || [];
      const t = hit.find((x) => x.team === team) || {};
      data = {
        found: !!t.team, window: { label: "演示数据" }, targets: {},
        rates: t, by_step: [], by_day: [], causes: [], dim_totals: {}, notes: [],
      };
    }
    const holder = document.createElement("div");
    holder.innerHTML = stdTeamModalBody(UI, data, { offline });
    if (holder.firstElementChild) body.replaceWith(holder.firstElementChild);
  }

  /** 口径说明（后端 notes）：指标定义即口径，保证与真实链路一致。 */
  function mfgNotes(UI, notes) {
    if (!(notes || []).length) return "";
    return `<div class="section-title">口径说明 <span class="section-extra">指标定义即口径 · 与后端数据同源</span></div>
      <div class="card"><div class="kpi-def">
        ${notes.map((n) => `<div class="kpi-def-row">${UI.icon("info", 12)}<span>${UI.esc(n)}</span></div>`).join("")}
      </div></div>`;
  }

  function mfgManufacturingBody(UI, d) {
    const grid = (d.kpis || []).length >= 4 ? "grid grid-4" : "grid grid-2";
    const scopeCn = mfgMfgScopeCn(d.filter);
    const scopeTag = scopeCn ? `汇总口径：${UI.esc(scopeCn)} · ` : "";
    return `<div class="section-title">核心指标 <span class="section-extra">${scopeTag}与顶部筛选卡同源 · 点卡片看明细分解 · 卡内「问数」可跳协同问数</span></div>
      <div class="${grid}">${(d.kpis || []).map((k) => mfgKpiCard(UI, k, "manufacturing")).join("")}</div>
      <div class="section-title">重点关注 TOP5 <span class="section-extra">${scopeTag}同口径最需关注的最差 5 名 · 点行看实体逐日明细</span></div>
      <div class="grid grid-2">${(d.rankings || []).map((r) => mfgRankCard(UI, r, "manufacturing", d.window && d.window.label, scopeCn)).join("")}</div>
      ${mfgParetoCard(UI, d, "manufacturing", MFG_STOP_PARETO)}
      ${mfgParetoCard(UI, d, "manufacturing", MFG_SCRAP_PARETO)}
      ${mfgNotes(UI, d.notes)}`;
  }

  // 制造看板的两张「原因累计柏拉图」配置（与品质不良柏拉图同一套双面板机件、同一份事实表结构；
  // 制造侧没有缺陷类别维度 → cats:false，原因维度暂无下钻口径 → drillable:false，只保留「问数」）。
  const MFG_STOP_PARETO = {
    factKey: "stop_pareto_fact", hostId: "mfg-stop-pareto-fact", stateKey: "mfgStopPareto",
    title: "停机原因累计柏拉图", label: "停机原因", qtyLabel: "停机(分钟)", valueWord: "停机",
    askWord: "停机损失", askTail: "的产线 / 型号分布如何？主要集中在哪条线？",
    emptyWord: "停机", drillable: false, cats: false,
    extra: "左 · 月别（当月累计） ｜ 右 · 日别（当日） · 顶部产品分类/工段 · 按停机分钟累计 · 行内「问数」可追问根因",
  };
  const MFG_SCRAP_PARETO = {
    factKey: "scrap_pareto_fact", hostId: "mfg-scrap-pareto-fact", stateKey: "mfgScrapPareto",
    title: "报废原因累计柏拉图", label: "报废原因", qtyLabel: "报废(件)", valueWord: "报废",
    askWord: "报废损失", askTail: "的产线 / 型号分布如何？主要集中在哪类原因？",
    emptyWord: "报废", drillable: false, cats: false,
    extra: "左 · 月别（当月累计） ｜ 右 · 日别（当日） · 顶部产品分类/工段 · 按报废件数累计 · 行内「问数」可追问根因",
  };

  /** 制造看板板级「产品分类 / 工段」筛选的中文范围标签（缺省 = 空串，表示未筛选）。 */
  function mfgMfgScopeCn(filter) {
    const f = filter || {};
    const bits = [];
    if (f.product_category) bits.push(`产品分类 ${f.product_category}`);
    if (f.stage) bits.push(`工段 ${f.stage}`);
    return bits.join(" · ");
  }

  /** 制造看板筛选条（图1 形态）：产品分类下拉 + 工段胶囊（全部 / SMT / PCM / PACK / 整机）。
   *  选中即按该范围重算 4 张 KPI 卡与 TOP5；测试直通率无产线维度，工段对它不适用
   *  （由后端标注「筛选不适用」，前端不伪造分项）。
   *  ``fs`` = 当前下钻目标吃哪些板级筛选（见 drillFilterScope）：下钻态下只出真正生效的项。 */
  function mfgMfgScopeChips(UI, st, fs) {
    const scope = fs || BOARD_FILTER_ALL;
    const pcat = st.mfgProdCat || "";
    const stage = st.mfgMStage || "";
    const chip = (val, label, on) => `<button class="chip${on ? " active" : ""}"
      data-mstage="${UI.esc(val)}" title="按「工段 ${UI.esc(label)}」范围汇总 4 张 KPI 卡">${UI.esc(label)}</button>`;
    const pcatGroup = scope.product_category
      ? `<span class="chip-row-label">产品分类</span>`
        + `<select class="chip-select" data-pcat title="按「产品分类」范围汇总 4 张 KPI 卡">`
        + `<option value="">全部</option>`
        + MFG_PCAT_OPTIONS.map((p) => `<option value="${UI.esc(p)}"${p === pcat ? " selected" : ""}>${UI.esc(p)}</option>`).join("")
        + `</select>`
      : "";
    const stageGroup = scope.stage
      ? `<span class="chip-row-label">工段</span>`
        + chip("", "全部", !stage)
        + MFG_STAGE_CHIPS.map((s) => chip(s, s, stage === s)).join("")
      : "";
    return pcatGroup + stageGroup;
  }

  /** 品质看板板级「工段 / 缺陷类别」筛选的中文范围标签（缺省 = 空串，表示未筛选）。 */
  function mfgScopeCn(filter) {
    const f = filter || {};
    const bits = [];
    if (f.stage) bits.push(`工段 ${f.stage}`);
    if (f.category) bits.push(`类别 ${f.category}`);
    return bits.join(" · ");
  }

  /** 品质看板筛选条：工段（全部 / SMT / PCM / PACK / 整机）+ 类别（全部 / 性能 / 外观）。
   *  选中即按该范围重算 4 张 KPI 卡的汇总值（无该维度的两项由后端标注「筛选不适用」）。
   *  ``fs`` = 当前下钻目标吃哪些板级筛选（见 drillFilterScope）：下钻态下只出真正生效的项
   *  （来料 / 客诉 / 标准三率 2 级都不吃工段·类别 → 这两组不渲染）。 */
  function mfgScopeChips(UI, st, fs) {
    const scope = fs || BOARD_FILTER_ALL;
    const stage = st.mfgStage || "";
    const cat = st.mfgCat || "";
    const chip = (attr, val, label, on) => `<button class="chip${on ? " active" : ""}"
      ${attr}="${UI.esc(val)}" title="按「${UI.esc(label)}」范围汇总 4 张 KPI 卡">${UI.esc(label)}</button>`;
    const stageGroup = scope.stage
      ? `<span class="chip-row-label">工段</span>`
        + chip("data-stage", "", "全部", !stage)
        + QUALITY_STAGE_CHIPS.map((s) => chip("data-stage", s, s, stage === s)).join("")
      : "";
    const catGroup = scope.category
      ? `<span class="chip-row-label">类别</span>`
        + chip("data-cat", "", "全部", !cat)
        + QUALITY_CAT_CHIPS.map(([v, cn]) => chip("data-cat", v, cn, cat === v)).join("")
      : "";
    return stageGroup + catGroup;
  }

  function mfgQualityBody(UI, d) {
    const grid = (d.kpis || []).length >= 4 ? "grid grid-4" : "grid grid-2";
    const scopeCn = mfgScopeCn(d.filter);
    const scopeTag = scopeCn ? `汇总口径：${UI.esc(scopeCn)} · ` : "";
    // 来料质量 TOP10（``incoming_top10``）：独立字段、固定全量口径，L1 只出 TOP10；
    // 已并入 TOP10 的配套榜（``view="merged_into_top10"``，如来料批次合格率）跳过不画，
    // 否则同一指标会出两张卡。全量供应商改在 2 级看板看，不再渲染全量排序表。
    const ranks = d.rankings || [];
    const incomingRanks = d.incoming_top10 || [];
    const cardRanks = ranks.filter((r) => r.view !== "merged_into_top10");
    // 主体一：核心指标 + 重点关注 TOP5 —— 二者共用顶部筛选卡片（统计口径 / 工段 / 类别）同源同口径
    // 三张 TOP5 卡凑成一排；凑不满三张时回落两列，避免出现半行空位。
    const rankGrid = cardRanks.length === 3 ? "grid grid-3" : "grid grid-2";
    const bodyTop = `<div class="mfg-group" data-mfg-group="metrics">
      <div class="section-title">核心指标 <span class="section-extra">${scopeTag}与顶部筛选卡同源 · 点卡片看明细分解 · 卡内「问数」可跳协同问数</span></div>
      <div class="${grid}">${(d.kpis || []).map((k) => mfgKpiCard(UI, k, "quality")).join("")}</div>
      <div class="section-title">重点关注 TOP5 <span class="section-extra">${scopeTag}同口径最需关注的最差 5 名 · 点行看实体逐日明细</span></div>
      <div class="${rankGrid}">${cardRanks.map((r) => mfgRankCard(UI, r, "quality", d.window && d.window.label, scopeCn)).join("")}</div>
    </div>`;
    // 主体一之二：来料质量 TOP10（左 = 来料批次合格率 / 右 = 来料上线不良率）——
    // **固定全量口径**，不受板级统计口径与工段 / 类别 / 柏拉图筛选影响；L1 只出 TOP10。
    const bodyIncoming = incomingRanks.length ? `<div class="mfg-group" data-mfg-group="incoming">
      <div class="section-title">来料质量关注 TOP10 <span class="section-extra">固定全量口径 · 不受统计口径 / 工段 / 类别 / 柏拉图筛选影响 · 点卡片看全量供应商 2 级看板</span></div>
      <div class="grid grid-2">${incomingRanks.map((r) => mfgIncomingTopCard(UI, r)).join("")}</div>
    </div>` : "";
    // 主体二：标准三率（不受筛选影响，独立居中）
    const bodyStd = `<div class="mfg-group" data-mfg-group="standard">
      ${mfgStdThreeRates(UI, d.standard_three_rates)}
    </div>`;
    // 主体三：不良累计柏拉图（自带筛选卡：顶部 产品分类/工段 · 底部 缺陷类别）
    const bodyPareto = `<div class="mfg-group" data-mfg-group="pareto">
      ${mfgParetoCard(UI, d, "quality")}
    </div>`;
    return bodyTop + bodyIncoming + bodyStd + bodyPareto + mfgNotes(UI, d.notes);
  }

  /* ---------------- 二级 / 三级下钻（看板内就地展开，不跳协同问数） ---------------- */

  /** 面包屑：看板 › 指标 › 不良类型 › 实体 › 工序 › 工位（2–5 级，末级高亮）。 */
  function mfgCrumbHtml(UI, d, rootName) {
    const tabName = rootName || (BOARD_CFG[d.screen] || {}).name || "看板";
    const lvl = d.level || 2;
    const ent = d.entity || {};
    const parts = [`<span class="crumb-node">${UI.esc(tabName)}</span>`,
      `<span class="crumb-sep">›</span>`,
      `<span class="crumb-node ${lvl === 2 && !d.defect_type ? "crumb-cur" : ""}">${UI.esc(d.label || "")}</span>`];
    const push = (text, isCur) => {
      if (!text) return;
      parts.push(`<span class="crumb-sep">›</span>`,
        `<span class="crumb-node${isCur ? " crumb-cur" : ""}">${UI.esc(text)}</span>`);
    };
    // 不良类型下钻（L1 累计柏拉图点击）：维度是「不良类型」而非实体
    if (d.defect_type) {
      push(`${d.pareto_label || "不良类型"}「${d.defect_type}」`, lvl === 2);
    }
    if (lvl >= 3) push(ent.name || "", lvl === 3);
    // L5 的下钻参数只带工序名与工位号，其余从 parent 还原来补全面包屑
    const stepName = (d.step && d.step.step) || (d.parent && d.parent.step) || "";
    if (lvl >= 4) push(stepName, lvl === 4);
    const stn = d.station || {};
    const stnName = stn.station_name || stn.station_no || (d.parent && d.parent.station) || "";
    if (lvl >= 5) push(stnName, true);
    const back = { 2: "返回看板", 3: "返回二级看板", 4: "返回三级看板", 5: "返回工序明细" }[lvl]
      || "返回看板";
    return `<div class="mfg-crumb">
      <button class="crumb-back" data-crumb-back>${UI.icon("arrow", 12)} ${UI.esc(back)}</button>
      <span class="crumb-path">${parts.join("")}</span>
    </div>`;
  }

  /** 达标判定（与后端 dashboard._status 同口径）：越高越好允许下沿 10%，越低越好允许上沿 50%。 */
  function mfgStatusOf(value, target, hi) {
    if (hi) return value >= target ? "ok" : (value >= target * 0.9 ? "warn" : "danger");
    return value <= target ? "ok" : (value <= target * 1.5 ? "warn" : "danger");
  }

  /* ---- 三级看板细化（工段 / 工序 / 工位 / 班次）渲染块 ---- */

  /** 工序损失瀑布条：按损失量占比切分的 100% 条带，最大损失工序标红并高亮。 */
  function mfgStepLossBar(UI, steps) {
    const items = (steps || []).filter((s) => (s.loss || 0) > 0)
      .sort((a, b) => (b.loss || 0) - (a.loss || 0));
    if (!items.length) return "";
    const total = items.reduce((a, b) => a + (b.loss || 0), 0) || 1;
    const segs = items.map((s, i) => `<span class="wf-seg ${UI.esc(s.status)}${i === 0 ? " hot" : ""}"
      style="width:${((s.loss / total) * 100).toFixed(2)}%"
      title="${UI.esc(s.step)}：损失 ${UI.esc(s.loss)} · 占比 ${UI.esc(s.share)}%"></span>`).join("");
    const legend = items.map((s, i) => `<span class="wf-item${i === 0 ? " hot" : ""}">
      <i class="wf-dot ${UI.esc(s.status)}"></i>${UI.esc(s.step)}<b>${UI.esc(s.share)}%</b></span>`).join("");
    return `<div class="wf-wrap">
      <div class="wf-title">工序损失瀑布（短板标红）</div>
      <div class="wf-bar">${segs}</div>
      <div class="wf-legend">${legend}</div>
    </div>`;
  }

  /** 工段 / 工序分解卡：工段分组 → 工序行（值/目标/状态/分子÷分母/损失占比）；点工序行 → L4。 */
  function mfgStageCard(UI, d) {
    const stages = d.stages || [];
    if (!stages.length) return "";
    const allSteps = [];
    stages.forEach((st) => (st.steps || []).forEach((sp) => allSteps.push(sp)));
    const ent = UI.esc(drillEntity(d.entity));
    const body = stages.map((st) => {
      const head = `<tr class="stage-row">
        <td colspan="7">${UI.icon("layers", 12)}<b>${UI.esc(st.stage)}</b>
          <span class="stage-sum">${UI.esc(st.value)}${UI.esc(d.unit)}
            · 分子 ÷ 分母 ${UI.esc(st.num)} ÷ ${UI.esc(st.den)}</span>
          ${UI.badge(MFG_STATUS_CN[st.status] || st.status, MFG_BADGE[st.status] || "badge-gray")}</td></tr>`;
      const rows = (st.steps || []).map((sp) => `<tr class="drill-row" role="button" tabindex="0"
              data-drill-step="${UI.esc(sp.step)}" data-drill-entity="${ent}"
              data-kpi="${UI.esc(d.kpi)}" data-screen="${UI.esc(d.screen)}"
              ${d.screen === "manufacturing"
                ? `data-mfg-modal="4" data-mfg-name="${UI.esc(sp.step)}"` : ""}
              title="点击查看「${UI.esc(sp.step)}」的工位明细">
        <td class="drill-name">${sp.status === "danger" ? UI.icon("warn", 11) : ""}${UI.esc(sp.step)}</td>
        <td class="drill-val ${UI.esc(sp.status)}">${UI.esc(sp.value)}${UI.esc(d.unit)}</td>
        <td class="drill-dim">${UI.esc(d.target)}${UI.esc(d.unit)}</td>
        <td>${UI.badge(MFG_STATUS_CN[sp.status] || sp.status, MFG_BADGE[sp.status] || "badge-gray")}</td>
        <td class="drill-frac">${UI.esc(sp.num)} ÷ ${UI.esc(sp.den)}</td>
        <td class="drill-share"><span class="share-bar" style="width:${Math.min(100, sp.share || 0)}%"></span>${UI.esc(sp.share)}%</td>
        <td class="drill-op">${UI.icon("arrow", 11)}</td>
      </tr>`).join("");
      return head + rows;
    }).join("");
    return `<div class="section-title">工段 · 工序分解
        <span class="section-extra">点工序行下钻到工位明细 · 占比 = 该工序损失 ÷ 全部工序损失</span></div>
      <div class="card">
        ${mfgStepLossBar(UI, allSteps)}
        <div class="drill-scroll"><table class="std-table drill-table">
          <thead><tr><th>工序</th><th>值</th><th>目标</th><th>状态</th>
            <th>分子 ÷ 分母</th><th>损失占比</th><th></th></tr></thead>
          <tbody>${body}</tbody></table></div>
      </div>`;
  }

  /** 工位明细卡：工位号/工位名/设备/班次/责任人/节拍/主缺陷模式/过站数/值；点行 → L5。 */
  function mfgStationCard(UI, d, title, extra, hideStep) {
    const list = d.stations || [];
    if (!list.length) return "";
    const ent = UI.esc(drillEntity(d.entity));
    const rows = list.map((s) => `<tr class="drill-row" role="button" tabindex="0"
            data-drill-station="${UI.esc(s.station_no)}" data-drill-step="${UI.esc(s.step)}"
            data-drill-entity="${ent}" data-kpi="${UI.esc(d.kpi)}" data-screen="${UI.esc(d.screen)}"
            ${d.screen === "manufacturing"
              ? `data-mfg-modal="5" data-mfg-name="${UI.esc(s.station_name || s.station_no)}"` : ""}
            title="点击查看「${UI.esc(s.station_name || s.station_no)}」逐日明细 + 过站记录">
      ${hideStep ? "" : `<td class="drill-name">${UI.esc(s.step)}</td>`}
      <td>${UI.esc(s.station_no)}</td>
      <td>${UI.esc(s.station_name)}</td>
      <td class="drill-dim">${UI.esc(s.equipment_no)}</td>
      <td>${UI.badge(UI.esc(s.shift), "badge-gray")}</td>
      <td>${UI.esc(s.operator)}</td>
      <td class="num">${UI.esc(s.cycle_time_s)}</td>
      <td class="drill-dim">${UI.esc(s.defect_mode || "—")}</td>
      <td class="drill-frac">${UI.esc(s.num)} ÷ ${UI.esc(s.den)}</td>
      <td class="drill-val ${UI.esc(s.status)}">${UI.esc(s.value)}${UI.esc(d.unit)}</td>
      <td class="drill-op">${UI.icon("arrow", 11)}</td>
    </tr>`).join("");
    return `<div class="section-title">${UI.esc(title || "工位明细")}
        <span class="section-extra">${UI.esc(extra || "点工位行下钻到逐日 + 过站记录")}</span></div>
      <div class="card"><div class="drill-scroll"><table class="std-table drill-table">
        <thead><tr>${hideStep ? "" : "<th>工序</th>"}<th>工位号</th><th>工位名</th><th>设备编号</th>
          <th>班次</th><th>责任人</th><th>节拍(s)</th><th>主缺陷模式</th>
          <th>分子 ÷ 分母</th><th>值</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div></div>`;
  }

  /** 班次对比卡（L5）：同一工位白班 / 夜班。 */
  function mfgShiftCard(UI, d) {
    const list = d.shifts || [];
    if (!list.length) return "";
    const rows = list.map((x) => `<tr>
      <td>${UI.badge(UI.esc(x.shift), "badge-gray")}</td>
      <td class="drill-frac">${UI.esc(x.num)} ÷ ${UI.esc(x.den)}</td>
      <td class="drill-val ${UI.esc(x.status)}">${UI.esc(x.value)}${UI.esc(d.unit)}</td>
    </tr>`).join("");
    return `<div class="section-title">班次对比
        <span class="section-extra">同一工位 · 白班 / 夜班（求和 = 该工位值）</span></div>
      <div class="card"><table class="std-table drill-table">
        <thead><tr><th>班次</th><th>分子 ÷ 分母</th><th>值</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
  }

  /** 累计柏拉图 SVG：柱 = 损失量（左轴），折线 = 累计占比（右轴 0–100%），虚线 = 关键少数阈值。
   *  ``size`` 可选（``{w,h}``）——双面板并排时用更紧凑的画布，缺省沿用整幅 640×190。 */
  function mfgParetoChart(UI, list, max, threshold, vitalCount, size) {
    const W = (size && size.w) || 640, H = (size && size.h) || 190;
    const padL = 46, padR = 42, padT = 14, padB = 22;
    const pw = W - padL - padR, ph = H - padT - padB;
    const band = pw / (list.length || 1);
    const bw = Math.min(48, Math.max(10, band * 0.5));
    let bars = "", line = "", dots = "";
    list.forEach((x, i) => {
      const cx = padL + band * (i + 0.5);
      const bh = Math.max(1, (x.value / max) * ph);
      const y = padT + ph - bh;
      bars += `<rect x="${(cx - bw / 2).toFixed(1)}" y="${y.toFixed(1)}"`
        + ` width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2"`
        + ` class="pbar${i < vitalCount ? " vital" : ""}"/>`;
      const cy = padT + ph - (x.cum / 100) * ph;
      line += `${i === 0 ? "M" : "L"}${cx.toFixed(1)},${cy.toFixed(1)} `;
      dots += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3" class="pdot"/>`;
    });
    const ty = padT + ph - (threshold / 100) * ph;
    // X 轴下方：每根柱标出该类型的**占比**（与右侧列表「占比」列同源）——
    // 避免读图时要来回对照列表才知道哪根柱占了多少。缺 share 的旧数据不标，不编造。
    const shares = list.map((x, i) => {
      if (x.share === undefined || x.share === null) return "";
      const cx = padL + band * (i + 0.5);
      return `<text x="${cx.toFixed(1)}" y="${(padT + ph + 13).toFixed(1)}"`
        + ` class="pshare" text-anchor="middle">${UI.esc(x.share)}%</text>`;
    }).join("");
    const grid = [0, 50, 100].map((p) => {
      const y = padT + ph - (p / 100) * ph;
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="paxis"/>`
        + `<text x="${W - padR + 6}" y="${(y + 3.5).toFixed(1)}" class="ptick">${p}%</text>`
        + `<text x="${padL - 6}" y="${(y + 3.5).toFixed(1)}" class="ptick" text-anchor="end">`
        + `${Math.round((max * p) / 100)}</text>`;
    }).join("");
    // 阈值虚线 + 「关键少数」标签：**绘制在柱体之后**，并用浅琥珀药丸底承托文字——
    // 柱子再高也不会盖住标签（此前标签先画、被高柱遮死）。
    const tag = `关键少数 ${threshold}%`;
    const tagW = 16 + [...tag].reduce(
      (w, ch) => w + (ch.codePointAt(0) > 0x2E80 ? 11 : 6.4), 0);
    const tagY = Math.max(padT + 12, Math.min(ty - 4, H - padB - 2));
    const thr = `<line x1="${padL}" y1="${ty.toFixed(1)}" x2="${W - padR}" y2="${ty.toFixed(1)}" class="pthr"/>`
      + `<rect x="${padL + 6}" y="${(tagY - 11).toFixed(1)}" width="${tagW.toFixed(1)}"`
      + ` height="14" rx="7" class="pthr-pill"/>`
      + `<text x="${padL + 13}" y="${tagY.toFixed(1)}" class="pthr-label">${tag}</text>`;
    return `<div class="pareto-chart"><svg viewBox="0 0 ${W} ${H}" role="img"
        aria-label="累计不良柏拉图：柱为损失量，折线为累计占比">
      ${grid}
      ${bars}
      ${thr}
      <path d="${line.trim()}" class="pline"/>
      ${dots}
      ${shares}
    </svg></div>`;
  }

  /** 累计柏拉图卡（分发）：有 ``pareto_fact`` 预聚合事实表 → 交互式分析组件
   *  （左右两块「月别 / 日别」 + 产品分类/类别/工段 本地筛选，零网络往返）；否则回退静态渲染。
   *  ``opt`` 用于「同构但不同源」的复用（制造看板的停机 / 报废原因事实表）：
   *  ``factKey`` 事实表字段名、``hostId``/``stateKey`` 容器与筛选状态键、``title``/``label`` 文案、
   *  ``qtyLabel`` 损失量列名、``drillable`` 行是否可下钻、``cats`` 是否显示缺陷类别筛选。 */
  function mfgParetoCard(UI, d, screen, opt) {
    const o = opt || {};
    const fact = d && d[o.factKey || "pareto_fact"];
    if (fact && (fact.rows || []).length) return mfgParetoFactCard(UI, d, o);
    return o.factKey ? "" : mfgParetoStaticCard(UI, d, screen);
  }

  /** 交互式柏拉图外壳：顶部筛选卡（产品分类下拉 + 工段 chips）+ 左右两块并排面板
   *  （左 · 月别 = 当月累计；右 · 日别 = 当日）+ 底部筛选（缺陷类别：性能不良 / 外观不良）。
   *  已去掉「粒度」切换与「期间」下拉；控件与内容由 mfgParetoBind 渲染。 */
  function mfgParetoFactCard(UI, d, opt) {
    const o = opt || {};
    const extra = o.extra
      || "左 · 月别（当月累计） ｜ 右 · 日别（当日） · 顶部产品分类/工段 · 底部缺陷类别 · 只列前 10 名（其余并入「其他」） · 点类型下钻产线 · 型号分解";
    return `<div class="section-title">${UI.esc(o.title || "不良累计柏拉图")}
        <span class="section-extra">${UI.esc(extra)}</span></div>
      <div class="card" id="${UI.esc(o.hostId || "mfg-pareto-fact")}">
        <div class="card-head">
          <div class="card-title">${UI.icon("list", 16)}${UI.esc(o.label || d.pareto_label || "不良类型")}累计柏拉图</div>
        </div>
        <div data-p-body><div class="empty">${UI.spinner("lg")}<div style="margin-top:8px">绘制中…</div></div></div>
      </div>`;
  }

  /** 交互式柏拉图渲染 + 绑定：``pareto_fact`` 本地切片 → 两块各自按类型求和 → 降序累计 →
   *  关键少数（累计首达阈值）→ 复用 mfgParetoChart 重绘；全程无网络往返。
   *  左块期间 = 最新月（当月）、右块 = 最新日（当日）；行可下钻（``window`` = 本块期间，
   *  叠加 stage/category/prod_category，与当前筛选同口径），行内「问数」跳协同问数。 */
  function mfgParetoBind(ctx, box, d, opt) {
    const o = opt || {};
    const UI = ctx.UI;
    const factKey = o.factKey || "pareto_fact";
    const host = box.querySelector("#" + (o.hostId || "mfg-pareto-fact"));
    const fact = d && d[factKey];
    if (!host || !fact || !(fact.rows || []).length) return;
    const body = host.querySelector("[data-p-body]");
    if (!body) return;
    const months = fact.months || [], days = fact.days || [];
    const cats = o.cats === false ? [] : (fact.categories || []);
    const stages = fact.stages || [];
    const pcats = fact.prod_cats || [];
    const threshold = d.pareto_threshold || 80;
    const screen = d.screen || "quality";
    const kpi = d.pareto_kpi || "defect_rate";
    const qtyLabel = o.qtyLabel || "损失量";
    const drillable = o.drillable !== false;   // 制造原因维度无对应下钻口径 → 行不可点
    const askWord = o.askWord || "不良";
    // 控制状态挂 screenState：下钻往返后按原筛选恢复（两块期间固定，不进状态）
    const stateKey = o.stateKey || "mfgPareto";
    const st = (ctx.state.screenState[stateKey] = ctx.state.screenState[stateKey] || {});
    if (st.pcat && pcats.indexOf(st.pcat) < 0) st.pcat = "";
    if (st.cat && cats.indexOf(st.cat) < 0) st.cat = "";
    if (st.stage && stages.indexOf(st.stage) < 0) st.stage = "";

    const chip = (on, attr, val, label) => `<button class="chip${on ? " active" : ""}"
      ${attr}="${UI.esc(val)}">${UI.esc(label)}</button>`;

    /** 单块聚合：期间过滤（月 = 前缀匹配 / 日 = 精确匹配）+ 共享筛选 → 类型降序累计。
     *  展示口径与后端 ``_pareto_cum`` 同源：**只保留前 10 项，其余（含数据中本就
     *  名为「其他」的项）合并为末位「其他」行**（``aggregated`` → 不可下钻）。 */
    const paneOf = (gran, period) => {
      const rows = (fact.rows || []).filter((r) => (gran === "month"
        ? String(r.d || "").slice(0, 7) === period : r.d === period)
        && (!st.pcat || r.pc === st.pcat)
        && (!st.cat || r.cat === st.cat) && (!st.stage || r.stage === st.stage));
      const byType = {};
      rows.forEach((r) => { byType[r.type] = (byType[r.type] || 0) + (r.qty || 0); });
      const total = Object.keys(byType).reduce((a, k) => a + byType[k], 0);
      const head = [], rest = [];
      Object.keys(byType).map((name) => ({ name: name, value: byType[name] }))
        .sort((a, b) => b.value - a.value)
        .forEach((x) => {
          if (x.name === "其他") { rest.push(x); return; }   // 数据中已有的「其他」并入
          if (head.length < 10) head.push(x); else rest.push(x);
        });
      if (rest.length) {
        head.push({ name: "其他", aggregated: true, folded: rest.length,
                    value: rest.reduce((a, b) => a + b.value, 0) });
      }
      let acc = 0;
      const list = head.map((x) => {
        const share = total ? Math.round((x.value / total) * 1000) / 10 : 0;
        acc = Math.round((acc + share) * 10) / 10;
        return { name: x.name, value: x.value, share: share, cum: acc,
                 aggregated: !!x.aggregated, folded: x.folded };
      });
      // 关键少数 = 累计占比首达阈值（含该项）——与后端 _pareto_cum/_vital_few 同源口径
      const vital = [];
      for (let i = 0; i < list.length; i += 1) {
        if (list[i].aggregated) break;   // 「其他」是合并桶，不作为关键少数
        vital.push(list[i].name);
        if (list[i].cum >= threshold) break;
      }
      return { list: list, total: total,
               max: Math.max.apply(null, list.map((x) => x.value)) || 1, vital: vital };
    };

    const paneHtml = (gran, title, period) => {
      const p = paneOf(gran, period);
      const scopeCn = [st.pcat || "全部产品分类", st.stage || "全部工段"]
        .concat(cats.length ? [st.cat || "全部类别"] : [])
        .concat([period || "—"]).join(" · ");
      const askOf = (t) => `${scopeCn} 的「${t}」${askWord}${o.askTail || "按产线 · 型号分布如何？"}`;
      const head = `<div class="pareto-pane-head">
        <span class="pareto-pane-title">${UI.esc(title)}</span>
        <span class="pareto-pane-meta">${UI.esc(period || "—")}</span>
        <span class="pareto-pane-extra">合计 ${p.total} · 关键少数 ${p.vital.length} 类 · 累计 ≥ ${threshold}%</span>
      </div>`;
      if (!p.list.length) {
        return `<div class="pareto-pane" data-p-pane="${UI.esc(gran)}">${head}
          <div class="empty">${UI.icon("warn", 22)}
            <div style="margin-top:8px">该筛选条件下暂无${UI.esc(o.emptyWord || "不良")}明细</div></div></div>`;
      }
      const rows = p.list.map((x, i) => {
        const hot = !x.aggregated && p.vital.indexOf(x.name) >= 0;
        // 行下钻只对「有对应下钻口径」的模块开放（品质不良类型 → 产线·型号分解）；
        // 制造原因维度（停机 / 报废）暂无该口径 → 行不带 data-drill-*，改开「明细弹窗」：
        // 该原因按 日期 · 产品分类 · 工段 本地切片（与当前筛选同口径），不再点了没反应。
        const rowAttrs = drillable
          ? ` drillable" role="button" tabindex="0"
          data-drill-pareto="${UI.esc(x.name)}" data-kpi="${UI.esc(kpi)}"
          data-screen="${UI.esc(screen)}" data-drill-window="${UI.esc(gran)}"
          data-drill-stage="${UI.esc(st.stage)}" data-drill-category="${UI.esc(st.cat)}"
          data-drill-prod-cat="${UI.esc(st.pcat)}"
          title="点击下钻：${UI.esc(x.name)} 在 ${UI.esc(scopeCn)} 下的产线 · 型号分解`
          : ` drillable" role="button" tabindex="0"
          data-p-reason="1" data-p-type="${UI.esc(x.name)}"
          data-p-fact="${UI.esc(factKey)}" data-p-state="${UI.esc(stateKey)}"
          data-p-win="${UI.esc(gran)}" data-p-ref="${UI.esc(period || "")}"
          data-p-qtylabel="${UI.esc(qtyLabel)}" data-p-valueword="${UI.esc(o.valueWord || "损失")}"
          title="点击查看「${UI.esc(x.name)}」明细：按日期 · 产品分类 · 工段分解（当前 ${UI.esc(x.value)} · 占比 ${UI.esc(x.share)}%）`;
        // 归并行（「其他」）无单一口径可下钻 → 只做展示，不给假入口。
        const aggAttrs = `" title="${UI.esc(`其余 ${x.folded} 类合并 · 占比 ${x.share}% · 不参与下钻`)}"`;
        return `<div class="pareto-row${hot ? " vital" : ""}${x.aggregated ? aggAttrs : rowAttrs}>
          <span class="pareto-no">${i + 1}</span>
          <span class="pareto-name" title="${UI.esc(o.valueWord || "损失")} ${UI.esc(x.value)} · 占比 ${UI.esc(x.share)}% · 累计 ${UI.esc(x.cum)}%"
            >${UI.esc(x.name)}</span>
          <span class="pareto-bar"><i style="width:${((x.value / p.max) * 100).toFixed(1)}%"></i></span>
          <span class="pareto-val">${UI.esc(x.value)}</span>
          <span class="pareto-share">${UI.esc(x.share)}%</span>
          <span class="pareto-cum">${UI.esc(x.cum)}%</span>
          ${x.aggregated ? `<span class="pareto-ask"></span>`
            : `<button class="row-ask pareto-ask" data-ask="${UI.esc(askOf(x.name))}"
            title="跳转协同问数：${UI.esc(askOf(x.name))}">${UI.icon("chat", 11)}问数</button>`}
        </div>`;
      }).join("");
      return `<div class="pareto-pane" data-p-pane="${UI.esc(gran)}">${head}
        ${mfgParetoChart(UI, p.list, p.max, threshold, p.vital.length, { w: 560, h: 170 })}
        <div class="pareto-head"><span class="pareto-no">#</span><span class="pareto-name">类型</span>
          <span class="pareto-bar">${UI.esc(qtyLabel)}</span><span class="pareto-val">数量</span>
          <span class="pareto-share">占比</span><span class="pareto-cum">累计</span>
          <span class="pareto-ask"></span></div>
        <div class="pareto-list">${rows}</div></div>`;
    };

    const toolbar = `<div class="pareto-toolbar">
      <label class="pareto-select"><span>产品分类</span>
        <select data-p-pcat>
          <option value=""${st.pcat ? "" : " selected"}>全部</option>
          ${pcats.map((p) => `<option value="${UI.esc(p)}"${
            st.pcat === p ? " selected" : ""}>${UI.esc(p)}</option>`).join("")}
        </select></label>
      <div class="chip-row">
        <span class="chip-row-label">工段</span>
        ${chip(!st.stage, "data-p-stage", "", "全部")}
        ${stages.map((s) => chip(st.stage === s, "data-p-stage", s, s)).join("")}
      </div>
      <span class="chip-row-note">· 顶部筛选同时作用于月别 / 日别两块</span>
    </div>`;

    // 底部筛选：缺陷类别（性能不良 / 外观不良）—— 只作为本柏拉图的筛选，不影响上方指标与 TOP5。
    // ``cats`` 为空（制造停机 / 报废原因）时不渲染这一行：不给没有选项的空筛选条。
    const bottomFilter = (o.cats === false || !cats.length) ? "" : `<div class="pareto-bottom-filter">
      <span class="chip-row-label">缺陷类别</span>
      ${chip(!st.cat, "data-p-cat", "", "全部")}
      ${cats.map((c) => chip(st.cat === c, "data-p-cat", c, c)).join("")}
      <span class="chip-row-note">· 仅作用于下方两块柏拉图</span>
    </div>`;

    const paint = () => {
      body.innerHTML = toolbar + `<div class="pareto-dual">
        ${paneHtml("month", "月别 · 当月", months[0] || "")}
        ${paneHtml("day", "日别 · 当日", days[0] || "")}
      </div>` + bottomFilter;
      // 控件重绑（每次重绘 DOM 全换）
      // 产品分类下拉：重绘后**显式回写**选中值（不依赖 <option selected> 属性的解析结果），
      // 并把状态里已不存在于选项集的值收敛回「全部」——避免「选了分类，下拉却显示全部」。
      const sel = body.querySelector("[data-p-pcat]");
      if (sel) {
        if (st.pcat && pcats.indexOf(st.pcat) < 0) st.pcat = "";
        sel.value = st.pcat || "";
        sel.addEventListener("change", () => {
          st.pcat = pcats.indexOf(sel.value) >= 0 ? sel.value : "";
          paint();
        });
      }
      body.querySelectorAll("[data-p-cat]").forEach((b) => b.addEventListener("click", () => {
        if (st.cat === b.dataset.pCat) return;
        st.cat = b.dataset.pCat; paint();
      }));
      body.querySelectorAll("[data-p-stage]").forEach((b) => b.addEventListener("click", () => {
        if (st.stage === b.dataset.pStage) return;
        st.stage = b.dataset.pStage; paint();
      }));
      mfgBind(ctx, body, d);   // 行下钻（window=月/日 + 三项筛选）与行内问数重新绑定
    };
    paint();
  }

  /** 静态累计柏拉图卡：柱 + 累计折线 + 阈值参考线；L1（带 screen / pareto_kpi）可点击类型下钻。 */
  function mfgParetoStaticCard(UI, d, screen) {
    const items = d.pareto || [];
    if (!items.length) return "";
    const threshold = d.pareto_threshold || 80;
    // cum_share：后端已算（L1 与 L3 同源口径）；离线演示数据缺省时按 share 就地累积
    let acc = 0;
    const list = items.map((x) => {
      const share = x.share === undefined ? 0 : x.share;
      acc = x.cum_share === undefined ? Math.round((acc + share) * 10) / 10 : x.cum_share;
      return { name: x.name, value: x.value || 0, share: share, cum: acc,
               aggregated: !!x.aggregated, folded: x.folded_count };
    });
    const total = list.reduce((a, b) => a + b.value, 0) || 1;
    const max = Math.max.apply(null, list.map((x) => x.value)) || 1;
    // 关键少数 = 累计占比首达 threshold 的类型（含该项）；后端已给则直接采用
    const vitalOf = (arr) => {
      const out = [];
      for (let i = 0; i < arr.length; i += 1) {
        if (arr[i].aggregated) break;   // 「其他」是合并桶，不作为关键少数
        out.push(arr[i].name);
        if (arr[i].cum >= threshold) break;
      }
      return out;
    };
    const vital = (d.pareto_vital && d.pareto_vital.length)
      ? d.pareto_vital.filter((n) => n !== "其他") : vitalOf(list);
    const drillable = !!(screen && d.pareto_kpi);
    const rows = list.map((x, i) => {
      const hot = !x.aggregated && vital.indexOf(x.name) >= 0;
      const clickable = drillable && !x.aggregated;   // 归并行无单一口径可下钻
      const attrs = x.aggregated
        ? ` title="其余 ${UI.esc(x.folded || 0)} 类合并 · 占比 ${UI.esc(x.share)}% · 不参与下钻"`
        : (clickable
          ? ` role="button" tabindex="0" data-drill-pareto="${UI.esc(x.name)}"`
            + ` data-kpi="${UI.esc(d.pareto_kpi)}" data-screen="${UI.esc(d.screen || screen)}"`
            + ` title="点击下钻：${UI.esc(x.name)} 的产线 · 型号分解"`
          : ` title="${UI.esc(x.name)}：损失 ${UI.esc(x.value)} · 占比 ${UI.esc(x.share)}%`
            + ` · 累计 ${UI.esc(x.cum)}%"`);
      return `<div class="pareto-row${hot ? " vital" : ""}${clickable ? " drillable" : ""}"${attrs}>
        <span class="pareto-no">${i + 1}</span>
        <span class="pareto-name"${hot ? ` title="关键少数：累计占比首达 ${threshold}%"` : ""}
          >${UI.esc(x.name)}</span>
        <span class="pareto-bar"><i style="width:${((x.value / max) * 100).toFixed(1)}%"></i></span>
        <span class="pareto-val">${UI.esc(x.value)}</span>
        <span class="pareto-share">${UI.esc(x.share)}%</span>
        <span class="pareto-cum">${UI.esc(x.cum)}%</span>
      </div>`;
    }).join("");
    return `<div class="section-title">${UI.esc(d.pareto_label || "缺陷模式")}累计柏拉图
        <span class="section-extra">按损失量降序 · 前 10 名以外并入「其他」 · 累计占比 = 前 N 类 ÷ 合计${drillable
          ? " · 点类型下钻产线 · 型号分解" : ""}</span></div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">${UI.icon("list", 16)}${UI.esc(d.pareto_label || "缺陷模式")}累计柏拉图</div>
          <div class="card-extra">合计 ${total} · 关键少数 ${vital.length} 类 · 累计 ≥ ${threshold}%</div>
        </div>
        ${mfgParetoChart(UI, list, max, threshold, vital.length)}
        <div class="pareto-head">
          <span class="pareto-no">#</span><span class="pareto-name">类型</span>
          <span class="pareto-bar">损失量</span><span class="pareto-val">数量</span>
          <span class="pareto-share">占比</span><span class="pareto-cum">累计</span>
        </div>
        <div class="pareto-list">${rows}</div>
      </div>`;
  }

  /** 制造看板「停机 / 报废原因」柏拉图行明细弹窗：该原因按 日期 · 产品分类 · 工段
   *  本地切片（数据取自同一次看板载荷的原因事实表，零网络往返，离线同样可用）。
   *  行「点了没反应」的假入口由此收口——凡是能看见的原因行，都能点开看明细。 */
  function openMfgReasonModal(ctx, el, payload) {
    const UI = ctx.UI;
    const fact = (payload || {})[el.dataset.pFact || ""] || null;
    const type = el.dataset.pType || "";
    const gran = el.dataset.pWin === "month" ? "month" : "day";
    const period = el.dataset.pRef || "";
    const qtyLabel = el.dataset.pQtylabel || "损失量";
    const valueWord = el.dataset.pValueword || "损失";
    const st = ((ctx.state.screenState || {})[el.dataset.pState || ""]) || {};
    const pcatCn = st.pcat || "全部产品分类";
    const stageCn = st.stage || "全部工段";
    const shell = openOrderModalShell(ctx, "原因明细", type || "—",
      `${pcatCn} · ${stageCn} · ${period || "—"} · 按日期 · 产品分类 · 工段分解`);
    if (!shell.body) return;
    const rows = fact ? (fact.rows || []).filter((r) => r.type === type
      && (gran === "month" ? String(r.d || "").slice(0, 7) === period : r.d === period)
      && (!st.pcat || r.pc === st.pcat) && (!st.stage || r.stage === st.stage)) : [];
    if (!rows.length) {
      fillOrderModal(shell, `<div class="std-modal-body"><div class="empty">
        ${UI.icon("info", 26)}
        <div style="margin-top:8px">该原因在当前口径下暂无明细数据</div>
        <div class="std-modal-hint">换个期间（月别 / 日别）或调整顶部产品分类 / 工段筛选后重试。</div>
      </div></div>`);
      return;
    }
    const total = rows.reduce((a, b) => a + (b.qty || 0), 0) || 1;
    const byKey = (key) => {
      const m = {};
      rows.forEach((r) => { const k = r[key] || "—"; m[k] = (m[k] || 0) + (r.qty || 0); });
      return Object.keys(m).map((k) => ({ name: k, qty: m[k] })).sort((a, b) => b.qty - a.qty);
    };
    const table = (list, head) => `<table class="std-table">
        <thead><tr><th>${UI.esc(head)}</th><th>${UI.esc(qtyLabel)}</th><th>占比</th></tr></thead>
        <tbody>${list.map((x) => `<tr>
          <td>${UI.esc(x.name)}</td><td class="num">${UI.esc(x.qty)}</td>
          <td class="num">${(Math.round((x.qty / total) * 1000) / 10).toFixed(1)}%</td>
        </tr>`).join("")}</tbody></table>`;
    const dayList = byKey("d");
    const scopeList = byKey("pc");
    const stageList = byKey("stage");
    const ask = `${pcatCn} · ${stageCn} · ${period || "—"} 的「${type}」${valueWord}合计 ${total}，`
      + `按产线 · 型号分布如何？如何降低该类${valueWord}？`;
    fillOrderModal(shell, `<div class="std-modal-body">
      <div class="std-chips">
        <span class="std-chip">${UI.esc(qtyLabel)}合计 <b>${UI.esc(total)}</b></span>
        <span class="std-chip">涉及天数 <b>${UI.esc(dayList.length)}</b></span>
        <span class="std-chip">产品分类 <b>${UI.esc(scopeList.length)}</b></span>
        <span class="std-chip">工段 <b>${UI.esc(stageList.length)}</b></span>
      </div>
      <div class="std-modal-cols">
        <div class="std-modal-col">
          <div class="std-modal-title">${UI.icon("list", 12)}按日期（降序）</div>
          ${table(dayList, "日期")}
        </div>
        <div class="std-modal-col">
          <div class="std-modal-title">${UI.icon("layers", 12)}按产品分类 · 工段</div>
          ${table(scopeList, "产品分类")}
          <div style="margin-top:10px">${table(stageList, "工段")}</div>
        </div>
      </div>
      <div class="std-notes">${UI.icon("info", 12)}
        <span>口径与左侧柏拉图同源（同一份原因事实表本地切片）：期间 ${UI.esc(period || "—")}，
          筛选 ${UI.esc(pcatCn)} · ${UI.esc(stageCn)}。制造原因维度暂无「按产线 · 型号」下钻口径，
          故以明细弹窗呈现，不做整页下钻。</span></div>
      <div class="std-chips" style="margin-top:12px">
        <button type="button" class="row-ask" data-ask="${UI.esc(ask)}"
          title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 12)} 问数</button>
      </div>
    </div>`);
    // 弹窗挂在 <body> 下、不在看板容器里，行内「问数」需补绑一次（同 openMfgDrillModal）。
    const host = document.querySelector(".std-modal");
    if (host) mfgBind(ctx, host, payload, "");
  }

  /** 品质看板「不良类型下钻」明细行弹窗：该 产线 · 型号 行在该不良类型下的
   *  分子 ÷ 分母 · 不良率 · 逐日趋势（行数据即 L2 载荷本身，不再点了没反应）。 */
  function openParetoTermModal(ctx, row, payload) {
    const UI = ctx.UI;
    const d = payload || {};
    const r = row || {};
    const tone = MFG_TONE[r.status] || "blue";
    const typeCn = `${d.pareto_label || "不良类型"}「${d.defect_type || ""}」`;
    const shell = openOrderModalShell(ctx, "明细行", r.name || "—",
      `${typeCn} · 按${d.dimension || "产线 · 型号"}分解`);
    if (!shell.body) return;
    const w = d.window || {};
    const trend = (r.trend || []).filter((v) => typeof v === "number" && !Number.isNaN(v));
    const first = trend.length ? trend[0] : null;
    const last = trend.length ? trend[trend.length - 1] : null;
    const trendBlock = trend.length >= 2 ? `<div class="std-modal-trend">
        <span class="std-modal-trend-label">逐日${UI.esc(d.label || "")}
          <i>${UI.esc(w.start || "")} ~ ${UI.esc(w.end || "")}</i></span>
        ${mfgSpark(trend, tone)}
        <span class="std-modal-trend-val">首 ${UI.esc(first)}${UI.esc(d.unit || "")}
          → 末 ${UI.esc(last)}${UI.esc(d.unit || "")}</span>
      </div>` : "";
    const ask = `${typeCn}在「${r.name || ""}」的不良率 ${r.value}${d.unit || ""}，如何降低？`;
    fillOrderModal(shell, `<div class="std-modal-body">
      <div class="drill-hero">
        <div class="drill-hero-main">
          <div class="drill-hero-label">${UI.esc(d.label || "")} · ${UI.esc(typeCn)}</div>
          <div class="drill-hero-value"><span class="big ${UI.esc(r.status)}">${UI.esc(r.value)}</span>
            <span class="stat-unit">${UI.esc(d.unit || "")}</span></div>
          <div class="drill-hero-sub">目标 ${UI.esc(d.target)}${UI.esc(d.unit || "")}
            · 分子 ÷ 分母 ${UI.esc(r.numerator)} ÷ ${UI.esc(r.denominator)}</div>
        </div>
        <div class="drill-hero-side">
          ${UI.badge(MFG_STATUS_CN[r.status] || r.status || "—", MFG_BADGE[r.status] || "badge-gray")}
          <div class="drill-def">${UI.icon("info", 12)}<span>${UI.esc(d.definition || "")}</span></div>
        </div>
      </div>
      ${trendBlock}
      <div class="std-notes">${UI.icon("info", 12)}
        <span>该行即「${UI.esc(typeCn)} × ${UI.esc(r.name || "")}」的明细口径（分子 = 该类型不良数，
          分母 = 该实体受检总数）。本层为不良类型维度终点，继续按实体分解请返回看板从
          「${UI.esc(d.label || "")}」2 级入口进入。</span></div>
      <div class="std-chips" style="margin-top:12px">
        <button type="button" class="row-ask" data-ask="${UI.esc(ask)}"
          title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 12)} 问数</button>
      </div>
    </div>`);
    const host = document.querySelector(".std-modal");
    if (host) mfgBind(ctx, host, payload, "");
  }

  /** 下钻 hero 卡（3/4/5 级共用）：大字值 + 状态 + 口径 + 去问数。 */
  function mfgDrillHero(UI, d, title) {
    const delta = (d.delta > 0 ? "+" : "") + d.delta;
    // 分子/分母的业务语义标签（后端 num_label/den_label 下发，如「越限条数 ÷ 测量条数」；
    // 未配置的指标回退「分子 / 分母」——用户反馈：光写「分子÷分母」看不出 8% 是什么）
    const numCn = d.num_label || "分子", denCn = d.den_label || "分母";
    return `<div class="card drill-hero-card">
        <div class="drill-hero">
          <div class="drill-hero-main">
            <div class="drill-hero-label">${UI.esc(title || "")}</div>
            <div class="drill-hero-value"><span class="big ${UI.esc(d.status)}">${UI.esc(d.value)}</span>
              <span class="stat-unit">${UI.esc(d.unit)}</span></div>
            <div class="drill-hero-sub">目标 ${UI.esc(d.target)}${UI.esc(d.unit)} · 偏差 ${UI.esc(delta)}pt
              · ${UI.esc(numCn)} ÷ ${UI.esc(denCn)} ${UI.esc(d.numerator)} ÷ ${UI.esc(d.denominator)}</div>
          </div>
          <div class="drill-hero-side">
            ${UI.badge(MFG_STATUS_CN[d.status] || d.status, MFG_BADGE[d.status] || "badge-gray")}
            <div class="drill-def">${UI.icon("info", 12)}<span>${UI.esc(d.definition || "")}</span></div>
          </div>
        </div>
        <div class="drill-hero-ops">
          ${(d.screen === "spc" && d.kpi === "spc_capability")
            ? `<button class="row-ask" role="button" tabindex="0"
            data-spc-records="overlimit"
            title="查看本口径下全部越限测量记录（超出规格限的行，按超出幅度降序）">
            ${UI.icon("warn", 12)} 越限明细（${UI.esc(d.numerator)} 条）</button>` : ""}
          <button class="row-ask" data-ask="${UI.esc(d.ask || "")}"
            title="跳转协同问数：${UI.esc(d.ask || "")}">${UI.icon("chat", 12)} 去问数</button>
        </div>
      </div>`;
  }

  /** 逐日明细卡（3/4/5 级共用）。
   *  表头用指标的业务语义标签（num_label/den_label，如「越限条数/测量条数」），
   *  并注明「— = 当日无数据」——用户反馈：光看「分子/分母」与整列「—」不知道在看什么。
   *  SPC 屏逐日行可单击 → 当日测量明细弹窗（与看板同口径可对账）；其它屏行保持不可点。 */
  function mfgDailyCard(UI, d, title) {
    const numCn = d.num_label || "分子", denCn = d.den_label || "分母";
    // 仅「能力维度」（spc_capability）的逐日来自 spc_measurement → 点行开测量记录弹窗；
    // 柏拉图维度（spc_pareto）的逐日来自 quality_defect（缺陷检验）→ 点行开测量弹窗
    // 就是口径错乱，保持行不可点。
    const spcDay = d.screen === "spc" && d.kpi === "spc_capability";
    const rows = (d.daily || []).map((x) => {
      // SPC 逐日行钩子：不带 data-kpi / data-drill-entity → 不与既有绑定链相交
      const attrs = spcDay
        ? ` role="button" tabindex="0" data-spc-records="daydetail" data-spc-day="${UI.esc(x.date)}"`
          + ` data-spc-entity="${UI.esc((d.entity && d.entity.characteristic) || "")}"`
          + ` title="点击查看 ${UI.esc(x.date)} 的全部测量记录（口径与看板一致）"`
        : "";
      return `<tr class="${spcDay ? "drill-row" : ""}"${attrs}>
      <td>${UI.esc(x.date)}</td>
      <td class="num" title="${UI.esc(numCn)}">${x.numerator === null ? "—" : UI.esc(x.numerator)}</td>
      <td class="num" title="${UI.esc(denCn)}">${x.denominator === null ? "—" : UI.esc(x.denominator)}</td>
      <td class="drill-val ${UI.esc(x.status)}">${x.value === null ? "—" : UI.esc(x.value) + UI.esc(d.unit)}</td>
    </tr>`;
    }).join("");
    const defTitle = title || `${d.label ? d.label + " · " : ""}逐日明细（${UI.esc((d.window || {}).label || "最近 7 天 · 演示窗口")}）`;
    return `<div class="section-title">${UI.esc(defTitle)}${spcDay
        ? `<span class="section-extra">点行看当日测量明细</span>` : ""}</div>
      <div class="card"><table class="std-table drill-table">
        <thead><tr><th>日期</th><th>${UI.esc(numCn)}</th><th>${UI.esc(denCn)}</th><th>${UI.esc(d.label || "值")}</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="4" class="empty-cell">暂无逐日数据</td></tr>`}</tbody></table>
        <div class="drill-def">${UI.icon("info", 12)}<span>「—」表示当日无数据（无测量 / 无检验记录），并非 0；${UI.esc(d.definition || "")}</span></div></div>`;
  }

  /** 关联原始记录卡（3/4/5 级共用）。 */
  function mfgRecordCard(UI, d, extra) {
    const cols = d.columns || [];
    const head = cols.map((c) => `<th>${UI.esc(c.label)}</th>`).join("");
    // 「数据来源」列展示翻译：demo-seed 是种子装载的内部标记，直出英文会让评审
    // 误以为是异常值；只做**显示层**翻译，不改原始记录数据（原始性不破坏）。
    const SOURCE_CN = { "demo-seed": "演示种子", "demo": "演示种子" };
    const rows = (d.records || []).map((r) => `<tr>${cols.map((c) => {
      const raw = r[c.key];
      const v = (c.key === "source" && SOURCE_CN[raw]) ? SOURCE_CN[raw] : raw;
      return `<td>${v === null || v === undefined || v === "" ? "—" : UI.esc(v)}</td>`;
    }).join("")}</tr>`).join("");
    return `<div class="section-title">关联原始记录
        <span class="section-extra">${UI.esc(extra || "最多 5 条 · 与聚合同源")}</span></div>
      <div class="card">${rows ? `<div class="drill-scroll"><table class="std-table drill-table">
        <thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`
        : `<div class="empty">${UI.icon("warn", 22)}<div style="margin-top:8px">无原始记录（离线演示数据不含明细）</div></div>`}</div>`;
  }

  /** L2：指标按维度全量分解表（序号/实体/值/目标/状态/分子÷分母/趋势/问数）。
   *  ``terminal``（不良类型下钻）为类型维度终点视图：行不再下钻实体，仅保留问数。 */
  function mfgDrillL2Body(UI, d) {
    const term = !!d.terminal;
    // 趋势列表头随所选口径：当日 1 点 / 当月 15 点 / 最近7天 7 点 / 不限窗口按数据日
    const wkey = (d.window || {}).key;
    const trendCol = wkey === "day" ? "当日" : (wkey === "month" ? "当月趋势"
      : (wkey === "all" ? "窗口趋势" : "7 日趋势"));
    const rows = (d.rows || []).map((r) => {
      const tone = MFG_TONE[r.status] || "blue";
      const ent = drillEntity(r);
      // ``masked`` = 后端已脱敏并移除了该行的下钻实体：与 terminal 同待遇，不再下钻
      // 订单履约（orders）：行单击打开「该分组订单明细」弹窗（对齐班组三率弹窗模式），
      // 3 级逐日明细入口收进弹窗内（openOrderGroupModal），行不再直接下钻。
      // 制造看板（manufacturing）：行单击同样改为弹窗（openMfgDrillModal），弹窗内展示该
      // 分组的 工段·工序·工位 + 逐日 + 关联原始记录，并保留「进入 3 级看板」入口；
      // 其余屏保持直下 3 级（品质 / SPC / 我的看板交互不变）。
      const isOrders = d.screen === "orders";
      const isMfgModal = d.screen === "manufacturing";
      const isSpcModal = d.screen === "spc";
      // SPC 弹窗必须带上**板级筛选维度**（工段 / 产品分类），否则弹窗按全厂全分类取数，
      // 与「筛选后的 L1 卡片」样本数对不上（实测 100 vs 25）→ 违反口径唯一事实源。
      // 维度在 L2 响应的**顶层 ``filter``** 里（不在行内，行里没有这两个字段）；
      // 无板级筛选时 ``filter`` 为 null / 缺省 → 兜底空串（= 全厂口径，与 L1 一致）。
      const spcFilter = (d && d.filter && typeof d.filter === "object") ? d.filter : {};
      const spcStage = spcFilter.stage || "";
      const spcPcat = spcFilter.product_category || "";
      // 不良类型下钻（terminal）行：后端无「该类型 × 实体」的更细口径 → 不再下钻实体，
      // 但点行改开「明细弹窗」（该 产线 · 型号 行的分子 ÷ 分母 · 逐日趋势），不再点了没反应。
      // ``masked`` 行仍不可点：脱敏就是脱敏，弹窗随之降级为脱敏口径的说明。
      const termRow = term && !r.masked;
      const rowAttrs = (term || r.masked)
        ? (termRow
          ? ` role="button" tabindex="0" data-p-term="${UI.esc(JSON.stringify({
              name: r.name, value: r.value, numerator: r.numerator,
              denominator: r.denominator, status: r.status, trend: r.trend || [] }))}"
            title="点击查看「${UI.esc(r.name)}」明细：分子 ÷ 分母 · 逐日趋势"`
          : ` title="${UI.esc(r.name)}：${UI.esc(r.value)}${UI.esc(d.unit)}`
            + ` · ${UI.esc(r.numerator)} ÷ ${UI.esc(r.denominator)}"`
            + (r.masked ? ` · 已按岗位脱敏，不支持下钻` : ""))
        : (isOrders
          ? ` role="button" tabindex="0" data-order-group="1"`
            + ` data-kpi="${UI.esc(d.kpi)}" data-screen="${UI.esc(d.screen)}"`
            + ` data-order-entity="${UI.esc(ent)}"`
            + ` data-order-row="${UI.esc(JSON.stringify({
                name: r.name, value: r.value, numerator: r.numerator,
                denominator: r.denominator, status: r.status }))}"`
            + ` title="点击查看「${UI.esc(r.name)}」的订单明细弹窗"`
          : (isMfgModal
            ? ` role="button" tabindex="0" data-mfg-modal="3"`
              + ` data-kpi="${UI.esc(d.kpi)}" data-screen="${UI.esc(d.screen)}"`
              + ` data-drill-entity="${UI.esc(ent)}" data-mfg-name="${UI.esc(r.name)}"`
              + ` title="点击查看「${UI.esc(r.name)}」明细弹窗（工段·工序·工位 + 逐日 + 原始记录）"`
            : (isSpcModal
              ? ` role="button" tabindex="0" data-spc-modal="capability"`
                + ` data-kpi="${UI.esc(d.kpi)}" data-screen="${UI.esc(d.screen)}"`
                + ` data-spc-entity="${UI.esc(r.name)}" data-spc-name="${UI.esc(r.name)}"`
                // 板级维度透传给弹窗钩子（mfgBind 的 [data-spc-modal] 分支读这两个）
                + ` data-spc-stage="${UI.esc(spcStage)}"`
                + ` data-spc-pcat="${UI.esc(spcPcat)}"`
                + ` title="点击查看「${UI.esc(r.name)}」能力明细弹窗（能力指数 + 逐日越限 + 原始记录）"`
              : ` role="button" tabindex="0" data-drill-entity="${UI.esc(ent)}"`
                + ` data-kpi="${UI.esc(d.kpi)}" data-screen="${UI.esc(d.screen)}"`
                + ` title="点击查看「${UI.esc(r.name)}」逐日明细"`)));
      return `<tr class="drill-row"${rowAttrs}>
        <td class="drill-no"><span class="rank-no">${UI.esc(r.rank)}</span></td>
        <td class="drill-name">${r.status === "danger" ? UI.icon("warn", 12) : ""}${UI.esc(r.name)}</td>
        <td class="drill-val ${UI.esc(r.status)}">${UI.esc(r.value)}${UI.esc(d.unit)}</td>
        <td class="drill-dim">${UI.esc(d.target)}${UI.esc(d.unit)}</td>
        <td>${UI.badge(MFG_STATUS_CN[r.status] || r.status, MFG_BADGE[r.status] || "badge-gray")}</td>
        <td class="drill-frac">${UI.esc(r.numerator)} ÷ ${UI.esc(r.denominator)}</td>
        <td class="drill-spark">${mfgSpark(r.trend, tone)}</td>
        <td class="drill-op"><button class="row-ask" data-ask="${UI.esc(d.ask || "")}"
            title="跳转协同问数：${UI.esc(d.ask || "")}">${UI.icon("chat", 11)}问数</button></td>
      </tr>`;
    }).join("");
    // 2 级看板补充数据（后端仅对声明了板级筛选的指标下发）：整体汇总 / 整体逐日 / 原始记录；
    // 离线演示数据不含这三块 → 缺省即不渲染，绝不编造。
    const s = d.summary || null;
    const hero = s ? mfgDrillHero(UI, Object.assign({}, d, {
      value: s.value, numerator: s.numerator, denominator: s.denominator,
      delta: s.delta, status: s.status,
    }), `${d.label} · 整体汇总`) : "";
    const summaryStrip = s ? `<div class="grid grid-4 std-kpis">
        <div class="std-mini"><i>覆盖${UI.esc(d.dimension)}</i><b>${UI.esc(s.items)}</b>
          <em>明细行数（全量分组）</em></div>
        <div class="std-mini"><i>达标 / 预警 / 危险</i>
          <b>${UI.esc(s.ok)} / ${UI.esc(s.warn)} / ${UI.esc(s.danger)}</b>
          <em>目标 ${UI.esc(s.target)}${UI.esc(s.unit)}</em></div>
        <div class="std-mini"><i>整体值</i>
          <b class="std-conform ${UI.esc(s.status)}">${UI.esc(s.value)}${UI.esc(s.unit)}</b>
          <em>${UI.esc(d.num_label || "分子")} ÷ ${UI.esc(d.den_label || "分母")} ${UI.esc(s.numerator)} ÷ ${UI.esc(s.denominator)}</em></div>
        <div class="std-mini"><i>最需关注</i>
          <b class="std-conform ${s.worst ? UI.esc(s.worst.status) : ""}">${
            s.worst ? UI.esc(s.worst.name) : "—"}</b>
          <em>${s.worst ? UI.esc(s.worst.value) + UI.esc(s.unit) : "无明细行"}</em></div>
      </div>` : "";
    const dailyCard = s ? mfgDailyCard(UI, d, `${UI.esc(d.label)} · 整体逐日明细（${UI.esc((d.window || {}).label || "")}）`) : "";
    const recordCard = s ? mfgRecordCard(UI, d, "筛选范围内 · 最多 20 条 · 与聚合同源") : "";
    return hero + summaryStrip + `<div class="section-title">${UI.esc(d.label)}${d.defect_type
        ? ` · ${UI.esc(d.pareto_label || "不良类型")}「${UI.esc(d.defect_type)}」` : ""} · 按${UI.esc(d.dimension)}分解
        <span class="section-extra">共 ${(d.rows || []).length} 项 · ${term
          ? "该不良类型下的明细 · 返回看板可切换类型" : (d.screen === "orders"
            ? "点行看该分组订单明细弹窗 · 行内「问数」可跳协同问数"
            : (d.screen === "manufacturing"
              ? "点行看明细弹窗（工段·工序·工位 + 逐日 + 原始记录）· 弹窗内可进入 3 级看板 · 行内「问数」可跳协同问数"
              : (d.screen === "spc"
                ? "点行看该特性的能力明细弹窗（能力指数 + 逐日越限 + 原始记录）· 弹窗内可进入 3 级看板 · 行内「问数」可跳协同问数"
                : "点行看逐日明细 · 行内「问数」可跳协同问数")))}</span></div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">${UI.icon("list", 16)}${UI.esc(d.label)} · 明细分解</div>
          <div class="card-extra">目标 ${UI.esc(d.target)}${UI.esc(d.unit)}</div>
        </div>
        ${rows ? `<div class="drill-scroll"><table class="std-table drill-table">
          <thead><tr><th>序号</th><th>${UI.esc(d.dimension)}</th><th>${UI.esc(d.label || "值")}</th><th>目标</th>
            <th>状态</th><th>${UI.esc(d.num_label || "分子")} ÷ ${UI.esc(d.den_label || "分母")}</th><th>${trendCol}</th><th>操作</th></tr></thead>
          <tbody>${rows}</tbody></table></div>`
        : `<div class="empty">${UI.icon("warn", 22)}<div style="margin-top:8px">${term
          ? "该不良类型在当前筛选范围内无明细记录" : "该指标暂无分组明细"}</div></div>`}
        <div class="drill-def">${UI.icon("info", 12)}<span>口径：${UI.esc(d.definition || "")}</span></div>
      </div>` + dailyCard + recordCard;
  }

  /** L3：单实体明细（工段·工序分解 + 工位明细 + 帕累托 + 逐日 + 原始记录）。 */
  function mfgDrillL3Body(UI, d) {
    // hero 标题必须带**指标名**（用户反馈：只显示实体名时，大数字看不出是越限率还是 Cpk）
    return mfgDrillHero(UI, d, drillHeroTitle(d, (d.entity && d.entity.name) || ""))
      + mfgStageCard(UI, d)
      + mfgStationCard(UI, d)
      + mfgParetoCard(UI, d)
      + mfgDailyCard(UI, d)
      + mfgRecordCard(UI, d, "最多 5 条 · 与聚合同源");
  }

  /** L4：单工序明细（该工序工位表 + 逐日 + 过站记录）。 */
  function mfgDrillL4Body(UI, d) {
    return mfgDrillHero(UI, d, drillHeroTitle(d, (d.parent && d.parent.name) || ""))
      + mfgStationCard(UI, d, "该工序工位明细", "点工位行下钻到逐日 + 过站记录", true)
      + mfgDailyCard(UI, d, "该工序逐日明细（最近 7 天）")
      + mfgRecordCard(UI, d, "该工序过站记录 · 最多 20 条");
  }

  /** L5：单工位明细（班次对比 + 逐日 + 过站记录）。 */
  function mfgDrillL5Body(UI, d) {
    return mfgDrillHero(UI, d, drillHeroTitle(d, (d.parent && d.parent.name) || ""))
      + mfgShiftCard(UI, d)
      + mfgDailyCard(UI, d, "该工位逐日明细（最近 7 天）")
      + mfgRecordCard(UI, d, "该工位过站记录 · 最多 20 条");
  }

  /** 下钻 hero 标题：**指标名 · 实体名**（指标名缺省时退回纯实体名，兼容旧演示数据）。 */
  function drillHeroTitle(d, entityName) {
    return (d && d.label) ? `${d.label} · ${entityName || ""}` : (entityName || "");
  }

  /**
   * 离线降级：不良类型下钻（L1 累计柏拉图点击）。
   * 演示数据没有「不良类型 × 产线·型号」交叉表，故按 TOP5 明细的不良数占比把该类型
   * 损失量分摊到各产线 · 型号（最大余数法，各行之和恒等于该类型总量），并标注为演示口径。
   */
  function mfgParetoFallback(ctx, drill, reason) {
    const q = (ctx.DEMO.DASHBOARD || {}).quality || {};
    const ranking = (q.rankings || [])[0] || {};
    const kpi = (q.kpis || []).find((k) => k.key === "defect_rate") || {};
    const hitType = (q.pareto || []).find((x) => x.name === drill.defectType);
    const baseRows = (ranking.items || []).filter((r) => (r.numerator || 0) > 0);
    if (!hitType || !baseRows.length) return null;
    const totalDefect = baseRows.reduce((a, b) => a + b.numerator, 0) || 1;
    const raw = baseRows.map((r) => (hitType.value * r.numerator) / totalDefect);
    const alloc = raw.map((v) => Math.floor(v));
    let rest = hitType.value - alloc.reduce((a, b) => a + b, 0);
    raw.map((v, i) => ({ i: i, frac: v - Math.floor(v) }))
      .sort((a, b) => b.frac - a.frac)
      .forEach((x) => { if (rest > 0) { alloc[x.i] += 1; rest -= 1; } });
    const rows = baseRows.map((r, i) => {
      const value = Math.round((1000 * alloc[i]) / (r.denominator || 1)) / 10;
      return Object.assign({}, r, {
        value: value, numerator: alloc[i], trend: [],
        status: mfgStatusOf(value, ranking.target, !!ranking.higher_is_better),
      });
    }).filter((r) => r.numerator > 0)
      .sort((a, b) => b.value - a.value)
      .map((r, i) => Object.assign(r, { rank: i + 1 }));
    return {
      screen: "quality", kpi: "defect_rate", level: 2, kind: "pareto",
      terminal: true, defect_type: drill.defectType, pareto_label: "不良类型",
      label: ranking.label || "不良率", unit: ranking.unit || "%",
      target: ranking.target || 2.0, higher_is_better: !!ranking.higher_is_better,
      dimension: "产线 · 型号", definition: kpi.definition || "", ask: kpi.ask || "",
      window: q.window, rows: rows, degraded: true,
      degraded_reason: reason || "演示数据（该类型损失量按 TOP5 不良数占比分摊）",
    };
  }

  /** 离线降级：L2 取 DEMO.DASHBOARD.drill[screen][kpi]；L3 从该 L2 行定位实体；
   *  L4/L5 由该行自带的 offline 工段/工序/工位分解裁剪而来（后端离线也能演示）。 */
  function mfgDrillFallback(ctx, drill, reason) {
    // 不良类型下钻：演示数据无该维度交叉表 → 走分摊构造
    if ((drill.level || 2) === 2 && drill.defectType) {
      return mfgParetoFallback(ctx, drill, reason);
    }
    const all = ((ctx.DEMO.DASHBOARD || {}).drill || {});
    const l2 = (all[drill.screen] || {})[drill.kpi];
    if (!l2) return null;
    if ((drill.level || 2) === 2) return l2;
    const e = drill.entity || {};
    const hit = (l2.rows || []).find((r) => {
      const re = r.entity || {};
      if (e.wo) return (r.wo || re.wo) === e.wo;
      // SPC：特性 / 不良类型维度（L2 行 entity 与 backend entity_cols 一致）
      if (e.characteristic) return (re.characteristic || r.characteristic) === e.characteristic;
      if (e.defect_type) return (re.defect_type || r.defect_type) === e.defect_type;
      if (e.customer) {
        const rc = re.customer || r.customer || "";
        const rl = re.level || r.level || "";
        return rc === e.customer && (!e.level || rl === e.level);
      }
      if (e.model) {
        const rm = re.model || r.model || "";
        const rline = re.line || r.line || "";
        return rm === e.model && (!e.line || rline === e.line);
      }
      return false;
    });
    if (!hit) return null;
    const base = Object.assign({}, l2, {
      level: 3, degraded_reason: reason || "演示数据",
      entity: Object.assign({ name: hit.name, line: hit.line || "",
                              model: hit.model || "", wo: hit.wo || "" }, hit.entity || {}),
      value: hit.value, numerator: hit.numerator, denominator: hit.denominator,
      status: hit.status, delta: Math.round((hit.value - l2.target) * 10) / 10,
      stages: hit.stages || [], stations: hit.stations || [],
      pareto: hit.pareto || [], pareto_label: hit.pareto_label || "缺陷模式",
      daily: hit.daily || [], records: hit.records || [], columns: hit.columns || [],
    });
    const lvl = drill.level || 3;
    if (lvl === 3) return base;
    const stepName = drill.step || "";
    const stepDef = (base.stages || []).map((st) => (st.steps || []).find((sp) => sp.step === stepName))
      .find((x) => x);
    if (!stepDef) return null;
    const ent = base.entity;
    const stepStations = (base.stations || []).filter((s) => s.step === stepName);
    const sum = (arr, k) => arr.reduce((a, b) => a + (b[k] || 0), 0);
    const agg = (arr, fb) => (arr.length
      ? { num: sum(arr, "num"), den: sum(arr, "den") } : { num: fb.num, den: fb.den });
    const mk = (n, d) => {
      const v = d ? Math.round(1000 * n / d) / 10 : 0;
      return Object.assign({}, base, {
        entity: ent, numerator: n, denominator: d, value: v,
        status: mfgStatusOf(v, base.target, base.higher_is_better),
      });
    };
    if (lvl === 4) {
      const a = agg(stepStations, { num: stepDef.num, den: stepDef.den });
      const p = mk(a.num, a.den);
      p.level = 4; p.stations = stepStations;
      p.shifts = [];
      p.parent = { stage: stepDef.stage, step: stepName, station: "",
                   name: [stepDef.stage, stepName].filter((x) => x).join(" · ") };
      p.step = stepDef;
      return p;
    }
    const sno = drill.station || "";
    const rows = stepStations.filter((s) => s.station_no === sno);
    if (!rows.length) return null;
    const a = agg(rows, { num: stepDef.num, den: stepDef.den });
    const p = mk(a.num, a.den);
    const byShift = {};
    rows.forEach((s) => { (byShift[s.shift] = byShift[s.shift] || []).push(s); });
    p.level = 5; p.stations = rows;
    p.shifts = Object.keys(byShift).map((sh) => {
      const g = agg(byShift[sh], { num: 0, den: 0 });
      const v = g.den ? Math.round(1000 * g.num / g.den) / 10 : 0;
      return { shift: sh, num: g.num, den: g.den, value: v,
               status: mfgStatusOf(v, base.target, base.higher_is_better) };
    });
    p.station = rows[0];
    p.parent = { stage: stepDef.stage, step: stepName, station: sno,
                 name: [stepDef.stage, stepName, sno].filter((x) => x).join(" · ") };
    p.step = stepDef;
    return p;
  }

  /** 看板下钻状态写入并重渲染（挂 screenState，重渲染不丢；制造 / 订单履约共用）。 */
  function setDrill(ctx, next) {
    ctx.state.screenState.boardDrill = next;
    ctx.refreshScreen();
  }

  /** 返回上一级：L5 → L4 → L3 → L2 → L1（看板首页）。 */
  function backDrill(ctx) {
    const st = ctx.state.screenState;
    const cur = st.boardDrill;
    if (!cur) return;
    const host = cur.host || "";   // 保留宿主（我的看板内联下钻 / 看板内下钻）
    const win = cur.window;        // 保留时间口径（当日/当月/最近7天/all），返回后仍同口径
    const lvl = cur.level || 2;
    if (lvl >= 5) {
      st.boardDrill = { level: 4, host: host, screen: cur.screen, kpi: cur.kpi,
                        entity: cur.entity, step: cur.step, window: win };
    } else if (lvl === 4) {
      st.boardDrill = { level: 3, host: host, screen: cur.screen, kpi: cur.kpi,
                        entity: cur.entity, window: win };
    } else if (lvl === 3) {
      st.boardDrill = { level: 2, host: host, screen: cur.screen, kpi: cur.kpi, window: win };
    } else {
      st.boardDrill = null;
    }
    ctx.refreshScreen();
  }

  /** 标准三率 1 级 → 2 级：点工段卡把下钻状态挂到 ``boardDrill``（level=2 + stdStage），
   *  走与其它看板下钻同一条 refreshScreen 路径——面包屑 / 返回 / 口径切换都复用同一套，
   *  不在卡片下方就地展开（二级看板是独立下钻页，含汇总 + 班组明细）。 */
  function mfgStdBind(ctx, box) {
    const cards = box.querySelectorAll("[data-std-stage]");
    if (!cards.length) return;
    const go = (name) => {
      ctx.state.screenState.boardDrill = {
        level: 2, screen: "quality", stdStage: name,
        window: ctx.state.screenState.mfgWindow || "window",
      };
      ctx.refreshScreen();
    };
    cards.forEach((card) => {
      const fn = (ev) => {
        if (ev) ev.stopPropagation();   // 不进 mfgBind 的下钻/问数分支
        go(card.dataset.stdStage);
      };
      card.addEventListener("click", fn);
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(card.dataset.stdStage); }
      });
    });
  }

  /** 标准三率 2 级看板加载：优先复用 1 级已下发的同口径数据（零网络往返）；
   *  换口径（当日/当月）或有下钻但缓存失效时按同口径补取一次，失败回落演示数据。 */
  async function loadStdDrill(ctx, drill) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    const box = document.getElementById("mfg-body");
    if (!box) return;
    const win = ["day", "month", "window"].indexOf(drill.window) >= 0
      ? drill.window : (st.mfgWindow || "window");
    let d = (st.stdPayload && st.stdPayload.key === win) ? st.stdPayload.data : null;
    if (!d) {
      if (ctx.api.online()) {
        try {
          const r = await ctx.api.qualityDashboard(win);
          if (r && !r.degraded && r.standard_three_rates) d = r;
        } catch (err) { /* 降级演示数据，不阻断 */ }
      }
      if (!d) d = (ctx.DEMO.DASHBOARD || {}).quality || null;
      st.stdPayload = d ? { key: win, data: d } : null;
    }
    const crumbEl = document.getElementById("mfg-crumb");
    if (crumbEl) {
      crumbEl.innerHTML = `<div class="mfg-crumb">
        <button class="crumb-back" data-crumb-back>${UI.icon("arrow", 12)} 返回标准三率</button>
        <span class="crumb-path"><span class="crumb-node">品质看板</span>
          <span class="crumb-sep">›</span><span class="crumb-node">标准三率 · 符合率</span>
          <span class="crumb-sep">›</span><span class="crumb-node crumb-cur">${UI.esc(drill.stdStage)}</span></span>
      </div>`;
      const back = crumbEl.querySelector("[data-crumb-back]");
      if (back) back.addEventListener("click", () => backDrill(ctx));
    }
    const stage = (((d || {}).standard_three_rates || {}).by_stage || [])
      .find((x) => x.stage === drill.stdStage);
    if (!stage) {
      box.innerHTML = `<div class="empty">${UI.icon("warn", 28)}
        <div style="margin-top:8px">该工段在当前口径下暂无审核数据</div></div>`;
      return;
    }
    box.innerHTML = stdL2Body(UI, d, stage);
    mfgBind(ctx, box, d);   // 行内「问数」等通用交互
  }

  /** 绑定看板下钻交互：data-kpi → L2；data-drill-entity → L3；
   *  data-drill-step → L4（带 data-drill-station 则 L5）；行内「问数」→ 协同问数。 */
  function mfgBind(ctx, box, payload, host) {
    const h = host || "";   // 宿主透传：我的看板内联下钻时回写 dashboard，保证返回/再下钻不串屏
    // 板级时间口径：下钻与所看档位同口径（当日/当月/最近7天）。
    // 订单履约看板与 SPC 看板各自带独立口径状态，其余看板（制造/品质/我的看板）沿用 mfgWindow。
    const st = ctx.state.screenState || {};
    const screenId = (payload && payload.screen) || "";
    const win = (screenId === "orders" ? st.ordersWindow
      : (screenId === "spc" ? st.spcWindow : st.mfgWindow)) || "window";
    box.querySelectorAll("[data-ask]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const q = btn.dataset.ask;
        if (!q) return;
        // 结构化问数上下文：从按钮所在的 KPI 卡/排行行读取口径标识，
        // 叠加板级档位与筛选——后端命中看板 KPI 时直接复用看板聚合函数
        // （口径同源，不经 LLM），未命中则静默回落自然语言链路。
        const card = btn.closest("[data-kpi]");
        const screen = (card && card.dataset.screen) || (payload && payload.screen) || "";
        const askCtx = {
          module: h === "dashboard" ? "dashboard" : "mfgboard",
          screen: screen,
          kpi: (card && card.dataset.kpi) || "",
          view: btn.dataset.askView || "kpi",
          window: win,
          // 筛选按屏取：SPC 屏用自己的 spcStage / spcProdCat（不与制造/品质混用），
          // 否则「看板显示 SMT 的数、问数却给全厂的数」= 口径不一致缺陷
          filters: (screen === "spc"
            ? { stage: st.spcStage || "", category: "", prod_cat: st.spcProdCat || "" }
            : { stage: st.mfgStage || st.mfgMStage || "",
                category: st.mfgCat || "",
                prod_cat: st.mfgProdCat || "" }),
        };
        writeAskPrefill(q, askCtx);
        ctx.navigate("chat");
      });
    });
    // 2 级看板班组行 → 班组三率明细弹窗（口径跟随当前档位）
    box.querySelectorAll("[data-std-team]").forEach((tr) => {
      tr.setAttribute("role", "button");
      tr.setAttribute("tabindex", "0");
      tr.addEventListener("click", () => {
        openStdTeamModal(ctx, tr.dataset.stdTeamStage || "", tr.dataset.stdTeam, win);
      });
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); tr.click(); }
      });
    });
    const entOf = (el) => {
      try { return JSON.parse(el.dataset.drillEntity || "{}"); } catch (e) { return {}; }
    };
    // 行内点击必须 stopPropagation：来料质量 TOP10 卡的行既带 data-drill-entity（L3），
    // 又嵌在带 data-kpi（L2）的卡片里，不拦截会冒泡触发卡片的 L2，把用户带回上一级。
    const goL2 = (el) => (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      setDrill(ctx, {
        level: 2, host: h, screen: el.dataset.screen, kpi: el.dataset.kpi, window: win,
      });
    };
    const goL3 = (el) => (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      setDrill(ctx, {
        level: 3, host: h, screen: el.dataset.screen, kpi: el.dataset.kpi,
        entity: entOf(el), window: win,
      });
    };
    const goL4L5 = (el) => (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      const station = el.dataset.drillStation || "";
      setDrill(ctx, {
        level: station ? 5 : 4, host: h, screen: el.dataset.screen, kpi: el.dataset.kpi,
        entity: entOf(el), step: el.dataset.drillStep || "", station: station,
        window: win,
      });
    };
    // L1 累计不良柏拉图 → 不良类型下钻（类型维度 L2，行不再下钻实体）。
    // 交互式柏拉图行带 data-drill-window（本块期间：月别 "month" / 日别 "day"）+
    // stage/category/prod-cat（与当前「工段/类别/产品分类」筛选同口径）；
    // 静态柏拉图行缺省走板级口径。
    const goPareto = (el) => () => setDrill(ctx, {
      level: 2, host: h, screen: el.dataset.screen, kpi: el.dataset.kpi,
      defectType: el.dataset.drillPareto || "",
      window: el.dataset.drillWindow || win,
      stage: el.dataset.drillStage || "",
      category: el.dataset.drillCategory || "",
      prodCat: el.dataset.drillProdCat || "",
    });
    const keyGo = (fn) => (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); }
    };
    // 制造看板「停机 / 报废原因」柏拉图行（不可下钻）→ 明细弹窗（本地切片，与柏拉图同源）。
    box.querySelectorAll("[data-p-reason]").forEach((el) => {
      const fn = (ev) => {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        openMfgReasonModal(ctx, el, payload);
      };
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    // 品质看板「不良类型下钻」明细行（terminal）→ 明细弹窗（分子 ÷ 分母 · 逐日趋势）。
    box.querySelectorAll("[data-p-term]").forEach((el) => {
      const fn = (ev) => {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        let row = {};
        try { row = JSON.parse(el.dataset.pTerm || "{}"); } catch (e) { row = {}; }
        openParetoTermModal(ctx, row, payload);
      };
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    box.querySelectorAll("[data-drill-pareto]").forEach((el) => {
      const fn = goPareto(el);
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    // 订单履约 2 级看板行 → 该分组订单明细弹窗（口径跟随当前档位；
    // 行缓存值一并透传，离线时降级为「只有该行比率」并明确标注）。
    // 必须先于 [data-kpi] 的 L2 绑定排除，否则点行会同时触发展开 2 级下钻。
    box.querySelectorAll("[data-order-group]").forEach((el) => {
      const fn = (ev) => {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        openOrderGroupModal(ctx, el.dataset.kpi, el.dataset.orderEntity || "{}",
          win, el.dataset.orderRow || "");
      };
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    // 制造看板 2/3/4 级行 → 明细弹窗（该分组的工段·工序·工位 + 逐日 + 关联原始记录），
    // 弹窗内保留「进入 N 级看板」入口；行不再直接整页下钻（对齐订单 / 班组弹窗模式）。
    // 同样必须先于 [data-drill-entity]/[data-drill-step] 的绑定排除，避免同一次点击触发两次。
    box.querySelectorAll("[data-mfg-modal]").forEach((el) => {
      const fn = (ev) => {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        openMfgDrillModal(ctx, {
          level: parseInt(el.dataset.mfgModal, 10) || 3,
          screen: el.dataset.screen, kpi: el.dataset.kpi,
          entity: entOf(el), step: el.dataset.drillStep || "",
          station: el.dataset.drillStation || "", win: win,
          name: el.dataset.mfgName || "",
        });
      };
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    // SPC 2 级看板行 → 该特性的能力明细弹窗（能力指数 + 逐日越限 + 原始记录，
    // 口径跟随当前档位与板级筛选；弹窗内保留「进入 3 级看板」入口）。
    // 同样必须先于 [data-kpi]/[data-drill-entity] 的绑定排除，避免同一次点击触发两次。
    box.querySelectorAll("[data-spc-modal]").forEach((el) => {
      const fn = (ev) => {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        openSpcCapabilityModal(ctx, {
          characteristic: el.dataset.spcEntity || "",
          name: el.dataset.spcName || "",
          win: win,
          stage: el.dataset.spcStage || "",
          productCategory: el.dataset.spcPcat || "",
        });
      };
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    // SPC 测量记录弹窗（越限明细 / 单日明细）：hero「越限明细」按钮与逐日表行。
    // 钩子只带 data-spc-records（**不带** data-kpi / data-drill-entity）→ 与既有
    // L2/L3/L4-L5 绑定链零交集，无需改任何排除选择器（E3 安全）。板级筛选直接读
    // spcStage/spcProdCat（板级状态即唯一事实源，L1/L2/L3 全部同源）。
    box.querySelectorAll("[data-spc-records]").forEach((el) => {
      const fn = (ev) => {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        openSpcRecordsModal(ctx, {
          win: win,
          stage: st.spcStage || "",
          productCategory: st.spcProdCat || "",
          characteristic: el.dataset.spcEntity || "",
          day: el.dataset.spcDay || "",
          overOnly: el.dataset.spcRecords === "overlimit",
        });
      };
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
    // 仅 KPI 卡走 L2；带 data-drill-entity 的行（L1 TOP5 / L2 明细行）一律直下 L3
    box.querySelectorAll("[data-kpi]:not([data-drill-entity]):not([data-drill-pareto])"
        + ":not([data-order-group]):not([data-mfg-modal]):not([data-spc-modal])")
      .forEach((el) => {
        const fn = goL2(el);
        el.addEventListener("click", fn);
        el.addEventListener("keydown", keyGo(fn));
      });
    // 工序行 / 工位行带 data-drill-step，优先绑定（避免被下面的 L3 绑定覆盖）
    box.querySelectorAll("[data-drill-step]:not([data-mfg-modal]):not([data-spc-modal])")
      .forEach((el) => {
        const fn = goL4L5(el);
        el.addEventListener("click", fn);
        el.addEventListener("keydown", keyGo(fn));
      });
    box.querySelectorAll("[data-drill-entity]:not([data-drill-step])"
        + ":not([data-mfg-modal]):not([data-spc-modal])").forEach((el) => {
      const fn = goL3(el);
      el.addEventListener("click", fn);
      el.addEventListener("keydown", keyGo(fn));
    });
  }

  /**
   * 下钻数据加载（真实优先 + 演示降级，全程不抛）：
   * 无 entity = L2（按维度全量分解）；有 entity = L3（单实体 + 工段/工序/工位）；
   * 再带 step = L4（该工序）；再带 station = L5（该工位）。
   */
  async function loadMfgDrill(ctx, drill) {
    const UI = ctx.UI;
    const cfg = boardCfgOf(drill);
    const box = document.getElementById(cfg.body);
    if (!box) return;
    const lvl = drill.level || 2;
    const entity = lvl >= 3 ? (drill.entity || {}) : null;
    const step = lvl >= 4 ? (drill.step || "") : "";
    const station = lvl >= 5 ? (drill.station || "") : "";
    const defectType = lvl === 2 ? (drill.defectType || "") : "";
    // 时间口径与所看板级一致；不良类型下钻可为 "all"（不限窗口）或本块期间（月别 month /
    // 日别 day）+ stage/category/prod_category 筛选。
    // 订单看板有独立口径状态，SPC 看板也有（spcWindow），其余看板沿用 mfgWindow。
    const ss = ctx.state.screenState || {};
    const fbWin = (drill.screen === "orders") ? ss.ordersWindow
      : ((drill.screen === "spc") ? ss.spcWindow : ss.mfgWindow);
    const windowKey = drill.window || fbWin || "window";
    // 板级筛选随下钻：只有「该下钻目标声明生效」的项才跟随板级状态（见 DRILL_FILTER_SCOPE）；
    // 其余（含不良类型下钻——筛选在柏拉图卡片内自选、行自带参数）一律以 drill 自带的值为准，
    // 绝不把板级筛选悄悄带进来。工作台「我的看板」内联下钻宿主不同屏，同样不跟随。
    const fs = drillFilterScope(drill);
    const onBoard = !drill.host;
    const stage = drill.stage || (drill.screen === "manufacturing" ? (ss.mfgMStage || "")
      : (drill.screen === "spc" ? (onBoard && fs.stage ? (ss.spcStage || "") : "")
        : (onBoard && fs.stage ? (ss.mfgStage || "") : "")));
    const category = drill.category || (onBoard && fs.category ? (ss.mfgCat || "") : "");
    const prodCat = drill.prodCat || (drill.screen === "manufacturing" ? (ss.mfgProdCat || "")
      : (drill.screen === "spc" ? (onBoard && fs.product_category ? (ss.spcProdCat || "") : "")
        : ""));
    let payload = null, source = "real", reason = "";

    if (ctx.api.online()) {
      try {
        const d = await ctx.api.dashboardDrill(drill.screen, drill.kpi, entity, step,
                                               station, defectType, windowKey,
                                               stage, category, prodCat);
        // 不良类型下钻：后端权威（空行也是有效答案），不因 0 行退回演示数据
        const okLvl = d && d.level === lvl
          && (lvl === 2 ? ((d.rows || []).length > 0 || !!d.defect_type) : !!d.entity);
        if (d && !d.degraded && okLvl) {
          payload = d;
        } else {
          reason = (d && d.degraded_reason) || "后端数据降级";
        }
      } catch (err) {
        if (err && err.kind === "permission_denied") {
          // 岗位在屏上可见、但这条下钻维度不允许（例如脱敏行）→ 如实拒绝，
          // 不退回演示明细，否则客户看到的是编造的"明细"却以为是真的。
          scopedDenied(UI, err, "该下钻明细", box, document.getElementById(cfg.flag));
          return;
        }
        reason = UI.friendlyError(err).text;
        UI.toast("下钻数据请求失败，已切换演示数据", "warn", 3600);
      }
    } else {
      reason = "后端离线 · 演示数据";
    }

    if (!payload) {
      source = "demo";
      payload = mfgDrillFallback(ctx, drill, reason);
    }
    if (!payload) {
      box.innerHTML = `<div class="empty">${UI.icon("warn", 28)}<div style="margin-top:8px">暂无明细数据</div></div>`;
      return;
    }

    const winEl = document.getElementById(cfg.win);
    if (winEl && payload.window) {
      winEl.textContent = `${payload.window.label}（${payload.window.start} ~ ${payload.window.end}）`;
    }
    const flagEl = document.getElementById(cfg.flag);
    if (flagEl) flagEl.innerHTML = source === "demo"
      ? UI.demoFlag(reason || "演示数据") : "";

    const crumbEl = document.getElementById(cfg.crumb);
    if (crumbEl) {
      crumbEl.innerHTML = mfgCrumbHtml(UI, payload, cfg.name);
      const back = crumbEl.querySelector("[data-crumb-back]");
      if (back) back.addEventListener("click", () => backDrill(ctx));
    }

    const bodyOf = {
      2: mfgDrillL2Body, 3: mfgDrillL3Body, 4: mfgDrillL4Body, 5: mfgDrillL5Body,
    }[payload.level] || mfgDrillL2Body;
    box.innerHTML = bodyOf(UI, payload);
    mfgBind(ctx, box, payload, drill.host);
  }

  function render_mfgboard(root, ctx) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    const tab = st.mfgTab === "quality" ? "quality" : "manufacturing";
    const isQ = tab === "quality";
    // 板级时间口径：当日 / 当月 / 最近7天（默认最近7天，与后端 resolve_window 同键）
    const WINDOW_KEYS = ["day", "month", "window"];
    const winKey = WINDOW_KEYS.indexOf(st.mfgWindow) >= 0 ? st.mfgWindow : "window";
    const WINDOW_CN = { day: "当日", month: "当月", window: "最近7天" };
    // 下钻状态只在同 Tab 内有效；换 Tab / 换屏复位
    let drill = st.boardDrill;
    // 仅本屏下钻有效；其它看板/工作台内联下钻残留一律复位
    if (drill && (drill.screen !== tab || drill.host === "dashboard")) {
      drill = null; st.boardDrill = null;
    }

    // 报告中心「就地生成报告」载荷：把当前看板口径 + 模块作为溯源引用归档
    const scopeCn = isQ
      ? mfgScopeCn({ stage: st.mfgStage, category: st.mfgCat })
      : mfgMfgScopeCn({ stage: st.mfgMStage, product_category: st.mfgProdCat });
    const rpBoardPayload = UI.esc(JSON.stringify({
      source_module: "kpi",
      context: {
        title: `${isQ ? "品质" : "制造"}看板快照报告 · ${WINDOW_CN[winKey]}`,
        summary: `看板口径：${WINDOW_CN[winKey]}`
          + (scopeCn ? `｜汇总范围：${scopeCn}` : "")
          + `｜模块：${isQ ? "品质" : "制造"}综合看板`,
        refs: [isQ ? "dashboard:quality" : "dashboard:manufacturing"],
      },
    }));

    // 板级筛选在 2 级及以下下钻里只保留**该模块真正生效**的项（见 drillFilterScope 说明）；
    // 全不生效时（来料固定全量口径 / 客诉快照口径 / 标准三率 2 级）整条不渲染——
    // 不给「点了没反应、点了还退回看板首页」的假控件。
    const fs = drillFilterScope(drill);
    const winGroup = fs.window
      ? `<span class="chip-row-label">统计口径</span>`
        + WINDOW_KEYS.map((k) => `<button class="chip${k === winKey ? " active" : ""}"
            data-window="${k}">${UI.icon("clock", 13)}${WINDOW_CN[k]}</button>`).join("")
        + `<span class="chip-row-note">· ${drill ? "下钻与所看口径同源" : "KPI / TOP5 均按所选口径"}</span>`
      : "";
    const scopeGroup = isQ ? mfgScopeChips(UI, st, fs) : mfgMfgScopeChips(UI, st, fs);
    const windowRow = (winGroup || scopeGroup)
      ? `<div class="chip-row mfg-windows" id="mfg-windows" style="margin-bottom:14px">
          ${winGroup}${scopeGroup}
        </div>` : "";

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("activity", 20)}制造综合看板
            ${isQ ? UI.badge("品质看板", "badge-purple") : UI.badge("制造看板", "badge-blue")}</div>
          <div class="page-desc">制造 + 品质双看板 · TOP5 突出重点（最需关注的最差 5 名）。
            <span id="mfg-window">统计窗口加载中…</span>
            <span id="mfg-flag"></span><span id="mfg-gen" class="mfg-gen"></span>
            <button class="btn btn-outline btn-sm" style="margin-left:10px" data-report-gen="${rpBoardPayload}"
              title="把当前看板口径与模块归档为报告（可继续走审定 / 派发）">${UI.icon("report", 12)}生成报告</button></div>
        </div>
        <div class="chip-row mfg-tabs" id="mfg-tabs" style="margin-bottom:14px">
          <button class="chip${isQ ? "" : " active"}" data-tab="manufacturing">${UI.icon("cpu", 14)}制造看板</button>
          <button class="chip${isQ ? " active" : ""}" data-tab="quality">${UI.icon("shield", 14)}品质看板</button>
        </div>
        ${windowRow}
        <div id="mfg-crumb"></div>
        <div id="mfg-body">
          <div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载看板数据…</div></div>
        </div>
      </div>`;

    root.querySelectorAll("#mfg-tabs [data-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (st.mfgTab === btn.dataset.tab) return;
        st.mfgTab = btn.dataset.tab;
        st.boardDrill = null;   // 切换 Tab 复位下钻层级
        ctx.refreshScreen();
      });
    });

    root.querySelectorAll("#mfg-windows [data-window]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (winKey === btn.dataset.window) return;
        st.mfgWindow = btn.dataset.window;
        // 换口径保持当前下钻层级（L2–L5）：只把下钻对象的窗口改成新口径，不退回看板首页。
        if (drill) drill.window = btn.dataset.window;
        else st.boardDrill = null;
        ctx.refreshScreen();
      });
    });

    // 品质看板板级「工段 / 类别」筛选：改的是 quality_defect 同源指标的汇总口径，
    // 后端已把这两个筛选折进下钻 WHERE → 下钻态下**保持当前层级**并按新范围就地重取，
    // 不再复位回看板首页（此前一律复位，导致「进 2 级看板点筛选就退回主界面」）。
    // 不吃这两个筛选的下钻模块（来料 / 客诉 / 标准三率 2 级）只渲染统计口径，选项根本不出现。
    root.querySelectorAll("#mfg-windows [data-stage]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if ((st.mfgStage || "") === btn.dataset.stage) return;
        st.mfgStage = btn.dataset.stage;
        if (drill) drill.stage = st.mfgStage;
        ctx.refreshScreen();
      });
    });
    root.querySelectorAll("#mfg-windows [data-cat]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if ((st.mfgCat || "") === btn.dataset.cat) return;
        st.mfgCat = btn.dataset.cat;
        if (drill) drill.category = st.mfgCat;
        ctx.refreshScreen();
      });
    });

    // 制造看板板级「产品分类 / 工段」筛选（图1 形态）：制造四个 KPI 都声明了 scope，
    // 下钻与板级筛选同口径（否则卡片说 SMT、下钻却是全厂分解）；下钻态同样就地重取不回首页。
    const pcatSel = root.querySelector("#mfg-windows [data-pcat]");
    if (pcatSel) {
      pcatSel.addEventListener("change", () => {
        if ((st.mfgProdCat || "") === pcatSel.value) return;
        st.mfgProdCat = pcatSel.value;
        if (drill) drill.prodCat = st.mfgProdCat;
        ctx.refreshScreen();
      });
    }
    root.querySelectorAll("#mfg-windows [data-mstage]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if ((st.mfgMStage || "") === btn.dataset.mstage) return;
        st.mfgMStage = btn.dataset.mstage;
        if (drill) drill.stage = st.mfgMStage;
        ctx.refreshScreen();
      });
    });

    if (drill && drill.stdStage) loadStdDrill(ctx, drill);
    else if (drill) loadMfgDrill(ctx, drill);
    else loadMfgDashboard(ctx, tab, winKey);
  }

  /**
   * 看板数据加载（真实优先 + 三路降级，全程不抛）：
   * 1) 后端在线且返回未降级数据 → 用真实数据（realFlag）；
   * 2) 后端离线 / 返回 degraded / kpis 为空 / 请求异常 → 用演示数据（demoFlag + 原因）。
   * 演示数据与接口逐字段同构，由同一聚合口径（seed_v3 明细）预先算出。
   */
  async function loadMfgDashboard(ctx, tab, windowKey) {
    const UI = ctx.UI;
    const box = document.getElementById("mfg-body");
    if (!box) return;
    const isQ = tab === "quality";
    const tabLabel = isQ ? "品质看板" : "制造综合看板";
    // 时间口径：默认最近 7 天（与后端 resolve_window 同键），非法值回落 "window"
    const win = ["day", "month", "window"].indexOf(windowKey) >= 0 ? windowKey : "window";
    // 板级筛选：品质 = 「工段 / 缺陷类别」，制造 = 「产品分类 / 工段」（两 Tab 各自独立的状态键，
    // 避免切 Tab 时把另一个口径的筛选带过来）
    const st = ctx.state.screenState || {};
    const stage = isQ ? (st.mfgStage || "") : (st.mfgMStage || "");
    const category = isQ ? (st.mfgCat || "") : "";
    const pcat = isQ ? "" : (st.mfgProdCat || "");
    let payload = null, source = "real", reason = "", scopeMeta = null;

    if (ctx.api.online()) {
      try {
        const d = isQ ? await ctx.api.qualityDashboard(win, stage, category)
                      : await ctx.api.manufacturingDashboard(win, stage, pcat);
        if (d && !d.degraded && (d.kpis || []).length) {
          payload = d;
          scopeMeta = d.scope || null;
        } else {
          reason = (d && d.degraded_reason) || "后端数据降级";
        }
      } catch (err) {
        if (err && err.kind === "permission_denied") {
          scopedDenied(UI, err, tabLabel, box,
                       document.getElementById("mfg-flag"));
          return;
        }
        reason = UI.friendlyError(err).text;
        UI.toast("看板数据请求失败，已切换演示数据", "warn", 3600);
      }
    } else {
      reason = "后端离线 · 演示数据";
    }

    if (!payload) {
      source = "demo";
      payload = (ctx.DEMO.DASHBOARD || {})[tab];
      // 演示数据是静态快照，没有工段 / 类别 / 产品分类维度的分母，无法按筛选重算 → 如实标注，
      // 不让「选了筛选、值却是全厂」看起来像真实汇总。
      if (payload && (stage || category || pcat)) {
        reason = `${reason || "演示数据"} · 离线演示数据不支持板级筛选`;
      }
    }
    if (!payload) {
      box.innerHTML = `<div class="empty">${UI.icon("warn", 28)}<div style="margin-top:8px">暂无看板数据</div></div>`;
      return;
    }

    // 头部元信息（统计窗口 / 数据来源标记 / 生成时间）
    const winEl = document.getElementById("mfg-window");
    if (winEl && payload.window) {
      winEl.textContent = `${payload.window.label}（${payload.window.start} ~ ${payload.window.end}）`;
    }
    const flagEl = document.getElementById("mfg-flag");
    if (flagEl) {
      flagEl.innerHTML = (source === "demo"
        ? UI.demoFlag(reason || "演示数据")
        : "") + maskedFlag(UI, scopeMeta && scopeMeta.masked);
    }
    const genEl = document.getElementById("mfg-gen");
    if (genEl && payload.generated_at) genEl.textContent = " · 生成于 " + payload.generated_at;

    box.innerHTML = isQ ? mfgQualityBody(UI, payload) : mfgManufacturingBody(UI, payload);
    // 主体点击 = 就地看明细（L2/L3）；卡内/行内「问数」按钮 = 跳协同问数
    mfgBind(ctx, box, payload);
    // 品质看板交互式柏拉图（日别/月别 + 期间 + 类别/工段本地筛选）—— 独立于板级口径
    mfgParetoBind(ctx, box, payload);
    // 制造看板两张「原因累计柏拉图」（停机 / 报废）：同一套双面板机件，各自独立筛选状态
    if (!isQ) {
      mfgParetoBind(ctx, box, payload, MFG_STOP_PARETO);
      mfgParetoBind(ctx, box, payload, MFG_SCRAP_PARETO);
    }
    // 标准三率两级看板（1 级工段 → 点卡下钻 2 级班组）
    if (isQ) {
      // 缓存本口径的整份载荷：下钻 2 级时直接就地切片，不再回后端取一遍
      if (payload.standard_three_rates && st) {
        st.stdPayload = { key: win, data: payload };
      }
      mfgStdBind(ctx, box);
    }
  }

  /* ============================================================
   * 屏幕七：SPC 过程能力看板（逐特性 Cpk 判定 + 越限预警 + 不良柏拉图）
   * ============================================================ */

  // 判定 verdict → 视觉 tone（与后端 _verdict_status 同口径）
  const SPC_VERDICT = { "合格": "ok", "偏低": "warn", "不合格": "danger", "不可判定": "empty" };

  // SPC 屏的三档统计口径（与后端 WINDOWS 键集同源：window/day/month）
  const SPC_WINDOW_KEYS = ["day", "month", "window"];
  const SPC_WINDOW_CN = { day: "当日", month: "当月", window: "最近7天" };

  /** L1 正文：逐特性 KPI 卡 + 过程能力明细 + 不良柏拉图 + TOP5 + 口径说明。 */
  function spcBody(UI, d) {
    const grid = (d.kpis || []).length >= 4 ? "grid grid-4" : "grid grid-3";

    const kpiHtml = `<div class="section-title">过程能力（逐特性 Cpk）
        <span class="section-extra">点卡片看该特性原始记录 · 卡内「问数」可跳协同问数</span></div>
      <div class="${grid}">${(d.kpis || []).map((k) => mfgKpiCard(UI, k, "spc", k.entity)).join("")}</div>`;

    const rankHtml = (d.rankings || []).length
      ? `<div class="section-title">重点关注 TOP5 <span class="section-extra">最需关注的最差 5 名 · 点行看实体原始记录</span></div>
      <div class="grid grid-2">${(d.rankings || []).map((r) => mfgRankCard(UI, r, "spc")).join("")}</div>` : "";

    const gapHtml = d.data_gap
      ? `<div class="card"><div class="kpi-def"><div class="kpi-def-row">${UI.icon("warn", 12)}
          <span>存在数据缺口（缺测量值 / 缺规格限）的特性，其能力指数不可判定，未做编造。</span></div></div></div>` : "";

    // L2 入口（2 级看板）：SPC 屏**唯一**承担「进入 2 级看板」职责的元素。
    // 为什么必须单独设一个（真实缺陷，2026-10-20 主理人复核定位）：
    //   mfgBind 的 L2 链是 `[data-kpi]:not([data-drill-entity])…` —— 只有「带
    //   data-kpi 且**不带** data-drill-entity」的元素才会被 L2 链捕获；而 SPC 的
    //   L1 逐特性 KPI 卡传了 `k.entity` → 卡上带 data-drill-entity → 落 L3 链，
    //   明细表行 / 柏拉图行 / TOP5 行同样都带 → **L1 上没有任何元素能被 L2 链
    //   捕获** ⇒ 2 级看板永不可达 ⇒ 2 级行的弹窗钩子写得再对也点不出来。
    //   订单 / 制造 / 品质看板的 mfgKpiCard 不传 ent，故它们的 L2 可达。
    // 硬约束：① data-kpi 必须是字面量（可被静态守卫守住）②**绝不带**
    //   data-drill-entity（带了就被 L3 链抢走）③ 必须渲染进 #spc-body 容器
    //   （mfgBind 只在 box 内 querySelectorAll）。
    const l2EntryHtml = `<div class="section-title">过程能力明细
        <span class="section-extra">Cp/Cpk 用组内 σ · Pp/Ppk 用整体 σ · 判定：≥1.33 合格 / ≥1.00 偏低 / &lt;1.00 不合格 · 规格限为演示规格限，工艺侧待给定</span>
        <button class="chip" role="button" tabindex="0"
          data-kpi="spc_capability" data-screen="spc"
          title="进入 2 级看板：按特性全量分解（子组超差率），点行看该特性的能力明细弹窗">
          ${UI.icon("layers", 13)}2 级看板 · 按特性分解</button>
        <button class="chip" role="button" tabindex="0"
          data-spc-records="overlimit"
          title="查看本口径下全部越限测量记录（超出规格限的行，按超出幅度降序）——不必进 2 级看板即可查看">
          ${UI.icon("warn", 13)}全厂越限明细</button></div>`;

    // 顺序：KPI 卡区 → 数据缺口 → 【过程能力明细 标题+2级看板入口 + 表格】→ 柏拉图 → TOP5 → notes
    // （标题与表格必须相邻 —— 入口语义是「看该表格的全量分解」，隔开 KPI 区会误导）
    return kpiHtml + gapHtml + l2EntryHtml + spcCapCard(UI, d.capabilities)
      + spcParetoCard(UI, d.pareto) + rankHtml + mfgNotes(UI, d.notes);
  }

  /** 过程能力明细表：特性 / 判定 / Cp / Cpk / Pp / Ppk / σ组内 / σ整体 / 样本数 / 子组数 / 规格限；
   *  行主体点击 = L3（该特性关联原始记录）；行内「问数」= 跳协同问数。 */
  function spcCapCard(UI, caps) {
    if (!(caps || []).length) return "";
    const cell = (v) => (v === null || v === undefined ? "—" : UI.esc(v));
    const rows = caps.map((c) => {
      const tone = SPC_VERDICT[c.verdict] || "empty";
      const spec = c.spec || {};
      const specTxt = (spec.lsl == null && spec.usl == null) ? "—"
        : `${spec.lsl == null ? "—" : spec.lsl} ~ ${spec.usl == null ? "—" : spec.usl}`;
      const ent = { characteristic: c.characteristic };
      const ask = `${c.characteristic} 的过程能力 Cpk 如何？是否需要加严检验或工程介入？`;
      return `<tr class="drill-row" role="button" tabindex="0"
              data-drill-entity="${UI.esc(JSON.stringify(ent))}" data-kpi="spc_capability" data-screen="spc"
              title="点击查看「${UI.esc(c.characteristic)}」关联原始记录">
        <td class="drill-name">${UI.esc(c.characteristic)}</td>
        <td>${c.verdict ? UI.badge(c.verdict, MFG_BADGE[tone] || "badge-gray") : "—"}</td>
        <td class="num">${cell(c.cp)}</td>
        <td class="drill-val ${tone}">${cell(c.cpk)}</td>
        <td class="num">${cell(c.pp)}</td>
        <td class="num">${cell(c.ppk)}</td>
        <td class="num">${cell(c.sigma_within)}</td>
        <td class="num">${cell(c.sigma_overall)}</td>
        <td class="num">${cell(c.n_total)}</td>
        <td class="num">${cell(c.n_subgroups)}</td>
        <td class="drill-dim">${UI.esc(specTxt)}</td>
        <td class="drill-op"><button class="row-ask" data-ask="${UI.esc(ask)}"
            title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 11)}问数</button></td>
      </tr>`;
    }).join("");
    // 标题区（含 2 级看板入口）由 spcBody 统一输出 → 此处只出表格本体
    // （避免两个 section-title 重复渲染同一个标题）。
    return `<div class="card"><div class="drill-scroll"><table class="std-table drill-table">
        <thead><tr><th>特性</th><th>判定</th><th>Cp</th><th>Cpk</th><th>Pp</th><th>Ppk</th>
          <th>σ组内</th><th>σ整体</th><th>样本数</th><th>子组数</th><th>规格限（演示）</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table></div></div>`;
  }

  /** 不良柏拉图：按不良数降序 + 占比条 + 累计占比；条目点击 = L3（该不良类型原始记录）。
   *  归并行（「其他」= 前 10 名以外的合并桶）只做展示，不带下钻入口。 */
  function spcParetoCard(UI, pareto) {
    const items = pareto || [];
    if (!items.length) return "";
    const max = Math.max.apply(null, items.map((x) => x.value || 0)) || 1;
    const rows = items.map((x, i) => {
      const ent = (x.entity && Object.keys(x.entity).length) ? x.entity : { defect_type: x.name };
      const ask = x.ask || `最近7天「${x.name}」不良 ${x.value} 件、占比 ${x.share}%，如何降低该类缺陷？`;
      const openTag = x.aggregated
        ? `<div class="pareto-row" title="其余 ${UI.esc(x.folded_count || 0)} 类合并 · 占比 ${UI.esc(x.share)}% · 不参与下钻">`
        : `<div class="pareto-row clickable" role="button" tabindex="0"
              data-drill-entity="${UI.esc(JSON.stringify(ent))}" data-kpi="spc_pareto" data-screen="spc"
              title="点击查看「${UI.esc(x.name)}」关联原始记录">`;
      return `${openTag}
        <span class="pareto-no">${i + 1}</span>
        <span class="pareto-name">${UI.esc(x.name)}</span>
        <span class="pareto-bar"><i style="width:${(((x.value || 0) / max) * 100).toFixed(1)}%"></i></span>
        <span class="pareto-val">${UI.esc(x.value)}</span>
        <span class="pareto-share">${UI.esc(x.share)}%</span>
        <span class="pareto-cum">累计 ${UI.esc(x.cum_share)}%</span>
        ${x.aggregated ? `<span class="pareto-ask"></span>`
          : `<button class="row-ask" data-ask="${UI.esc(ask)}"
          title="跳转协同问数：${UI.esc(ask)}">${UI.icon("chat", 11)}问数</button>`}
      </div>`;
    }).join("");
    return `<div class="section-title">不良柏拉图
        <span class="section-extra">按不良数降序 · 占比 = 该类型 ÷ 合计 · 累计首达 80% 为关键少数 · 前 10 名以外并入「其他」</span></div>
      <div class="card spc-pareto"><div class="pareto-list">${rows}</div></div>`;
  }

  function render_spc(root, ctx) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    // 下钻状态只在本屏有效；从其它看板进入时复位
    let drill = st.boardDrill;
    if (drill && (drill.screen !== "spc" || drill.host === "dashboard")) {
      drill = null; st.boardDrill = null;
    }

    // 板级筛选 state（SPC 专属，不与制造/品质的 mfg* 变量混用 —— 跨屏复用会让
    // 别的屏改掉本屏的筛选口径）。
    const winKey = ["day", "month", "window"].indexOf(st.spcWindow) >= 0
      ? st.spcWindow : "window";
    const pcat = st.spcProdCat || "";
    const stage = st.spcStage || "";

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("spc", 20)}SPC 过程能力看板
            ${UI.badge("Cpk / Pp / Ppk · 判定与越限预警", "badge-blue")}</div>
          <div class="page-desc">逐特性过程能力指数（双西格玛口径）· 越限预警三级下钻（特性 / 不良类型）· 不良柏拉图关键少数。
            <span id="spc-window">统计窗口加载中…</span>
            <span id="spc-flag"></span><span id="spc-gen" class="mfg-gen"></span></div>
        </div>
        <div class="chip-row mfg-windows" id="spc-windows" style="margin-bottom:14px">
          <span class="chip-row-label">统计口径</span>
          ${SPC_WINDOW_KEYS.map((k) => `<button class="chip${k === winKey ? " active" : ""}"
            data-window="${k}">${UI.icon("clock", 13)}${SPC_WINDOW_CN[k]}</button>`).join("")}
          <span class="chip-row-note">· KPI / TOP5 / 过程能力明细 / 不良柏拉图 均按所选口径</span>
          <span class="chip-row-label">产品分类</span>
          <select class="chip-select" data-pcat title="按「产品分类」范围汇总">
            <option value="">全部</option>
            ${MFG_PCAT_OPTIONS.map((p) => `<option value="${UI.esc(p)}"${p === pcat ? " selected" : ""}>${UI.esc(p)}</option>`).join("")}
          </select>
          <span class="chip-row-label">工段</span>
          <button class="chip${stage ? "" : " active"}" data-stage="" title="按全工段范围汇总">全部</button>
          ${MFG_STAGE_CHIPS.map((s) => `<button class="chip${stage === s ? " active" : ""}"
            data-stage="${UI.esc(s)}" title="按「工段 ${UI.esc(s)}」范围汇总">${UI.esc(s)}</button>`).join("")}
        </div>
        <div id="spc-crumb"></div>
        <div id="spc-body">
          <div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载看板数据…</div></div>
        </div>
      </div>`;

    // 事件绑定：改 state → 若在下钻态则同步改 drill.*（**不复位下钻层级**）
    // → ctx.refreshScreen() 同层刷新（与订单 / 制造看板同纪律）。
    root.querySelectorAll("#spc-windows [data-window]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (winKey === btn.dataset.window) return;
        st.spcWindow = btn.dataset.window;
        if (drill) drill.window = btn.dataset.window;
        else st.boardDrill = null;
        ctx.refreshScreen();
      });
    });
    const pcatSel = root.querySelector("#spc-windows [data-pcat]");
    if (pcatSel) {
      pcatSel.addEventListener("change", () => {
        if ((st.spcProdCat || "") === pcatSel.value) return;
        st.spcProdCat = pcatSel.value;
        if (drill) drill.prodCat = st.spcProdCat;
        ctx.refreshScreen();
      });
    }
    root.querySelectorAll("#spc-windows [data-stage]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if ((st.spcStage || "") === btn.dataset.stage) return;
        st.spcStage = btn.dataset.stage;
        if (drill) drill.stage = st.spcStage;
        ctx.refreshScreen();
      });
    });

    if (drill) loadMfgDrill(ctx, drill);
    else loadSpcDashboard(ctx);
  }

  /**
   * SPC 看板 L1 数据加载（真实优先 + 演示降级，全程不抛）：
   * 在线且未降级 → /api/v2/dashboard/spc（带板级筛选参数）；
   * 否则 → DEMO.SPC_DASHBOARD（与接口同构，**不支持板级筛选**，显式标注）。
   */
  async function loadSpcDashboard(ctx) {
    const UI = ctx.UI;
    const cfg = BOARD_CFG.spc;
    const box = document.getElementById(cfg.body);
    if (!box) return;
    const st = ctx.state.screenState;
    const win = ["day", "month", "window"].indexOf(st.spcWindow) >= 0
      ? st.spcWindow : "window";
    const pcat = st.spcProdCat || "";
    const stage = st.spcStage || "";
    let payload = null, source = "real", reason = "", scopeMeta = null;

    if (ctx.api.online()) {
      try {
        const d = await ctx.api.spcDashboard(win, stage, pcat);
        if (d && !d.degraded && (d.kpis || []).length) {
          payload = d;
          scopeMeta = d.scope || null;
        } else {
          reason = (d && d.degraded_reason) || "后端数据降级";
        }
      } catch (err) {
        if (err && err.kind === "permission_denied") {
          scopedDenied(UI, err, "SPC 过程能力看板", box,
                       document.getElementById(cfg.flag));
          return;
        }
        reason = UI.friendlyError(err).text;
        UI.toast("看板数据请求失败，已切换演示数据", "warn", 3600);
      }
    } else {
      reason = "后端离线 · 演示数据";
    }

    if (!payload) {
      source = "demo";
      payload = ctx.DEMO.SPC_DASHBOARD;
    }
    if (!payload) {
      box.innerHTML = `<div class="empty">${UI.icon("warn", 28)}<div style="margin-top:8px">暂无看板数据</div></div>`;
      return;
    }

    const winEl = document.getElementById(cfg.win);
    if (winEl && payload.window) {
      winEl.textContent = `${payload.window.label}（${payload.window.start} ~ ${payload.window.end}）`;
    }
    const flagEl = document.getElementById(cfg.flag);
    // 离线演示数据**不支持板级筛选**（快照是固定口径）→ 显式说明，
    // 绝不假装这是「筛选后的汇总」
    const demoNote = (source === "demo" && (win !== "window" || pcat || stage))
      ? UI.demoFlag("离线演示数据不支持板级筛选，当前显示固定口径快照")
      : "";
    if (flagEl) {
      flagEl.innerHTML = (source === "demo"
        ? UI.demoFlag(reason || "演示数据")
        : "") + demoNote + maskedFlag(UI, scopeMeta && scopeMeta.masked);
    }
    const genEl = document.getElementById(cfg.gen);
    if (genEl && payload.generated_at) genEl.textContent = " · 生成于 " + payload.generated_at;

    box.innerHTML = spcBody(UI, payload);
    mfgBind(ctx, box, payload);
  }

  /* ============================================================
   * 导出
   * ============================================================ */

  /* ============================================================
   * R8：数据口径词典弹层（看板 KPI / 问数证据 / 帮助中心共用入口）
   * ============================================================ */

  /** 按词条 id 拉取统一口径并弹层；找不到就明示，不猜。 */
  function openDictEntry(entryId) {
    const api = window.App && window.App.api;
    if (!api || typeof api._get !== "function" || !entryId) return;
    const UI = window.App.UI;
    api._get("/api/v2/metric-dict?id=" + encodeURIComponent(entryId))
      .then((resp) => {
        // `api._get` 已解信封：resp 即 data 载荷本身
        const entry = resp && resp.entry;
        if (!entry) {
          UI.toast((resp && resp.note) || "未找到该口径（不编造）");
          return;
        }
        const rows = [
          ["词条 id", entry.id],
          ["来源", entry.source_label],
          ["归属", entry.owner || "—"],
          ["涉及表", (entry.tables || []).join("、") || "—"],
          ["单位 / 目标", entry.unit
            ? `${entry.unit}${entry.target != null ? " · 目标 " + entry.target : ""}`
            : "—"],
          ["口径说明", entry.definition || "—"],
          ["计算公式", entry.formula || "—"],
          ["关键词", (entry.keywords || []).join("、") || "—"],
          ["引用方", (entry.refs || []).join(" · ") || "—"],
        ];
        const html = '<table class="help-table"><tbody>'
          + rows.map((r) => `<tr><th style="width:22%;text-align:left">${UI.esc(r[0])}</th>`
            + `<td style="white-space:pre-wrap">${UI.esc(r[1])}</td></tr>`).join("")
          + "</tbody></table>";
        if (window.SceneKit && typeof window.SceneKit.openDrill === "function") {
          window.SceneKit.openDrill({
            title: entry.name || entry.id,
            subtitle: "口径词典 · " + (entry.source_label || ""),
            html,
          });
        }
      })
      .catch(() => { /* 网络失败不打扰主流程 */ });
  }

  // 捕获阶段拦截：KPI 卡主体是「点击下钻」，口径按钮必须先于卡片处理器
  // stopPropagation，否则一次点击会同时打开下钻与口径。
  document.addEventListener("click", function (ev) {
    const el = ev.target && ev.target.closest && ev.target.closest("[data-dict-id]");
    if (!el) return;
    ev.preventDefault();
    ev.stopPropagation();
    openDictEntry(el.getAttribute("data-dict-id"));
  }, true);

  /* R11：呆滞料卡「发起退供评审」——只发起，不自动放行；创建后跳审批中心。 */
  document.addEventListener("click", function (ev) {
    const btn = ev.target && ev.target.closest && ev.target.closest("[data-ag-return]");
    if (!btn || btn.disabled) return;
    ev.preventDefault();
    ev.stopPropagation();
    const api = window.App && window.App.api;
    const UI = window.App.UI;
    if (!api || typeof api.agingReturnRequest !== "function") return;
    let parts = [];
    try { parts = JSON.parse(btn.getAttribute("data-part-nos") || "[]"); }
    catch (e) { parts = []; }
    if (!parts.length) { UI.toast("没有可发起退供的料号", "warn"); return; }
    btn.disabled = true;
    api.agingReturnRequest(parts, Number(btn.getAttribute("data-days") || 180), "")
      .then((data) => {
        // 发起人（如仓储）不一定持有 disposition 槽位（看不到该待办），
        // 因此不强行跳审批中心——只回执工单号，处置进度由治理岗位推进。
        UI.toast(`已创建退供评审工单 ${data.task_uid}（待治理岗位确认，未放行）`,
                 "ok", 5000);
      })
      .catch((err) => {
        UI.toast((err && err.message) || "退供评审创建失败", "warn", 4200);
        btn.disabled = false;
      });
  }, true);

  window.Screens = {
    render_dashboard,
    render_orders,
    render_spc,
    render_chat,
    render_alerts,
    render_reports,
    render_hitl,
    render_mfgboard,
  };
})();
