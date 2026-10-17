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

  async function hitlAction(ctx, taskUid, action) {
    const st = ctx.state.screenState;
    const map = processedMap(ctx);
    if (map[taskUid]) return;
    const role = ctx.currentRole();

    // 动作校验：有可选动作时必须先选定；需填依据的动作必须写理由
    const acts = normalizeActions(_hitlActions[taskUid] || []);
    const govStatus = signoffOf(taskUid, acts);
    const sel = hitlSelMap(ctx)[taskUid]
      || (govStatus.action_key ? { key: govStatus.action_key, label: govStatus.selected_action } : null);
    const commentEl = document.getElementById(`act-comment-${taskUid}`)
      || document.getElementById("hitl-comment");
    const commentText = commentEl && commentEl.value ? commentEl.value.trim() : "";
    if (action === "approve") {
      if (acts.length && !sel) {
        ctx.UI.toast("请先选择一个处置动作（按你的岗位权限）", "warn", 3200);
        return;
      }
      if (sel && sel.require_comment && !commentText) {
        ctx.UI.toast("该动作需填写决策依据（将写入工单留痕）", "warn", 3400);
        return;
      }
      // 前端门控（后端仍二次校验）：会签未齐 / 无 approve 权限时直接提示
      const gate = approveGate(ctx, taskUid, acts);
      if (!gate.ok) {
        ctx.UI.toast(gate.reason, "warn", 4600);
        return;
      }
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
        ? await ctx.api.workflowApprove(taskUid, role.user, comment, false, decision,
          govRoles(ctx), role.dept, role.uid)
        : await ctx.api.workflowReject(taskUid, role.user, comment, decision,
          govRoles(ctx), role.dept, role.uid);
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
      ctx.UI.toast(`工单 ${taskUid} 已${action === "approve" ? "同意" : "驳回"} → ${task.status}`, "ok", 4000);
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
      + `<span>${label} · 工单状态 <b>${ctx.UI.esc(info.status)}</b> · 处理人 ${ctx.UI.esc(info.actor)} · ${ctx.UI.esc(time)}`
      + `${info.demo ? "（演示模式，未真实调用后端）" : ""}</span></div>`;
  }

  /* ============================================================
   * 屏幕一：工作台
   * ============================================================ */

  function render_dashboard(root, ctx) {
    const UI = ctx.UI, DEMO = ctx.DEMO;
    const role = ctx.currentRole();
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

        <div class="briefing" id="briefing-bar">
          <div class="briefing-head">${UI.icon("sparkle", 14)}今日需要你关注
            <span class="section-extra">事找人：按岗位聚合（点击直达）</span></div>
          <div class="briefing-items" id="briefing-items">
            <span class="empty-inline">${UI.spinner("sm")} 正在聚合…</span>
          </div>
        </div>

        <div class="section-title">我的看板 <span class="section-extra">统计范围：${UI.esc(role.name)}视角 · 点卡片直接问数下钻</span></div>
        <div class="grid grid-4">
          ${(DEMO.ROLE_KPIS[role.id] || []).map((k) => `
          <button class="card stat-card stat-clickable" data-ask="${UI.esc(k.ask || "")}"
                  title="点击下钻：${UI.esc(k.ask || "")}">
            <div class="stat-icon ${k.tone}">${UI.icon(k.icon, 19)}</div>
            <div><div class="stat-num">${UI.esc(k.num)}</div><div class="stat-label">${UI.esc(k.label)}</div>
            <div class="stat-trend ${k.up ? "up" : ""}">${UI.icon(k.up ? "warn" : "clock", 12)} ${UI.esc(k.trend)}</div>
            <div class="stat-ask">${UI.icon("chat", 11)} ${UI.esc(k.ask || "")}</div></div>
          </button>`).join("")}
          <div class="card stat-card">
            <div class="stat-icon blue">${UI.icon("approve", 19)}</div>
            <div><div class="stat-num" id="dash-hitl-count">—</div><div class="stat-label">待人工确认</div>
            <div class="stat-trend">${UI.icon("clock", 12)} 停线工单 SLA 2h</div></div>
          </div>
        </div>

        <div class="section-title">待办中心 <span class="section-extra" id="todo-source">待确认事项 · 加载中…</span></div>
        <div class="card">
          <div class="card-head">
            <div class="card-title">${UI.icon("approve", 16)}人工在环待确认</div>
            <div class="card-extra">
              <button class="link-btn" data-goto="hitl">前往审批中心 ${UI.icon("arrow", 12)}</button>
            </div>
          </div>
          <div id="todo-list"><div class="empty">${UI.spinner("lg")}<div style="margin-top:10px">正在加载待办…</div></div></div>
        </div>

        <div class="section-title">快捷入口</div>
        <div class="grid grid-3">
          <button class="quick-entry" data-goto="chat">
            <span class="qe-ico" style="background:var(--primary-light);color:var(--primary)">${UI.icon("chat", 20)}</span>
            协同问数<span style="font-size:11.5px;color:var(--text-3);font-weight:400">对话式跨部门协同分析</span>
          </button>
          <button class="quick-entry" data-goto="orders">
            <span class="qe-ico" style="background:var(--warn-bg);color:var(--warn)">${UI.icon("orders", 20)}</span>
            订单履约看板<span style="font-size:11.5px;color:var(--text-3);font-weight:400">订单管道 · 风险订单 · 部门协同</span>
          </button>
          <button class="quick-entry" data-goto="hitl">
            <span class="qe-ico" style="background:var(--ok-bg);color:var(--ok)">${UI.icon("approve", 20)}</span>
            审批确认中心<span style="font-size:11.5px;color:var(--text-3);font-weight:400">复线决策 / 对客 ETA 审批</span>
          </button>
        </div>
      </div>`;

    root.querySelectorAll("[data-role]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.setRole(btn.dataset.role));
    });
    root.querySelectorAll("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.navigate(btn.dataset.goto));
    });

    // KPI 卡下钻问数（"看到异常→问出结论"一步完成）
    root.querySelectorAll("[data-ask]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const q = btn.dataset.ask;
        if (!q) return;
        try { sessionStorage.setItem("chat_prefill", q); } catch (e) { /* 隐私模式忽略 */ }
        ctx.navigate("chat");
      });
    });

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
        const resp = await ctx.api.workflowPending(role.roles || []);
        const all = resp.data || [];
        const mine = all.filter((t) => t.my_action);
        // to_dict 不含 sla_status（property），按 sla_due_at 自行计算超时
        const overdue = all.filter((t) =>
          t.sla_due_at && new Date(t.sla_due_at) < new Date()
          && !["done", "closed"].includes(t.status));
        if (mine.length) {
          const byAct = {};
          mine.forEach((t) => { byAct[t.my_action] = (byAct[t.my_action] || 0) + 1; });
          const label = { select_action: "待我发起", cosign: "待我会签", approve: "待我终批" };
          items.push(`<button class="briefing-item hot" data-goto="hitl">${UI.icon("approve", 13)}
            <b>${mine.length} 项待办</b>：${Object.entries(byAct).map(([k, v]) => `${label[k] || k} ${v}`).join(" · ")}</button>`);
        }
        if (overdue.length) {
          items.push(`<button class="briefing-item danger" data-goto="hitl">${UI.icon("warn", 13)}
            <b>${overdue.length} 张工单超时</b>：SLA 已过，需关注升级</button>`);
        }
      }
    } catch (e) { /* 离线降级：下方演示数据兜底 */ }
    const sum = ctx.DEMO.ALERT_SUMMARY[role.id] || ctx.DEMO.ALERT_SUMMARY.gm;
    if (sum.spc) items.push(`<button class="briefing-item" data-goto="alerts">${UI.icon("activity", 13)}
      <b>SPC 越限 ${sum.spc} 项</b>：锡膏印刷 CPK 0.89 &lt; 1.33</button>`);
    if (sum.shortage) items.push(`<button class="briefing-item" data-goto="alerts">${UI.icon("box", 13)}
      <b>缺料预警 ${sum.shortage} 项</b>：锡膏 M7020 到货延迟 36h</button>`);
    if (sum.order) items.push(`<button class="briefing-item" data-goto="orders">${UI.icon("orders", 13)}
      <b>订单履约风险 ${sum.order} 单</b>：华为终端 S 级订单临期</button>`);
    if (!items.length) {
      box.innerHTML = `<span class="empty-inline">${UI.icon("check", 13)} 当前无待办与预警，一切正常</span>`;
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
      const raw = await ctx.api.workflowPending();
      if (ctx.state.screen !== "dashboard") return;
      if (source) source.innerHTML = `待确认事项 · ${UI.realFlag("WorkflowEngine 待办队列")}`;
      if (count) count.textContent = String(raw.length);
      const tasks = mergeProcessedTasks(ctx, raw);
      if (!tasks.length) {
        list.innerHTML = `<div class="empty">${UI.icon("check", 30)}<div style="margin-top:8px">当前无 待确认事项——所有关键决策均已处理</div></div>`;
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
        <button class="btn btn-outline btn-sm" data-goto="hitl">详情</button>
      </div>
    </div>`;
  }

  function realTodoHtml(ctx, t) {
    const UI = ctx.UI;
    const due = slaDueIso(t);
    const total = slaTotalMs(t);
    const approvers = (t.assignees || []).join("、") || "—";
    const processed = processedMap(ctx)[t.task_uid];
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
        <div style="margin-top:8px;max-width:280px">${slaHtml(due, total)}</div>
        ${hitlProcessedBanner(ctx, t.task_uid)}
      </div>
      <div class="todo-actions">
        ${processed ? "" : `
        <button class="btn btn-primary btn-sm" data-approve="${UI.esc(t.task_uid)}">同意</button>
        <button class="btn btn-danger btn-sm" data-reject="${UI.esc(t.task_uid)}">驳回</button>`}
        <button class="btn btn-outline btn-sm" data-goto="hitl">详情</button>
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
    list.querySelectorAll("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => ctx.navigate(btn.dataset.goto));
    });
  }

  /* ============================================================
   * 屏幕二：订单履约看板
   * ============================================================ */

  function render_orders(root, ctx) {
    const UI = ctx.UI, DEMO = ctx.DEMO;
    const O = DEMO.ORDERS;
    const total = O.pipeline.reduce((s, p) => s + p.count, 0);

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("orders", 20)}订单履约看板</div>
          <div class="page-desc">订单全链路状态管道、风险订单跟踪与 16 部门（5 域组）协同态势。${UI.demoFlag()}</div>
        </div>

        <div class="card">
          <div class="card-head">
            <div class="card-title">${UI.icon("activity", 16)}订单状态管道</div>
            <div class="card-extra">在单合计 <b style="color:var(--text)">${total}</b> 单 ${UI.demoFlag("演示数据")}</div>
          </div>
          <div class="pipeline">
            ${O.pipeline.map((p, i) => `
              <div class="pipe-stage">
                <div class="pipe-stage-name">${UI.esc(p.stage)}</div>
                <div class="pipe-stage-num">${p.count}</div>
                <div class="pipe-stage-bar"><div class="progress-bar" style="width:${Math.round((p.count / total) * 100)}%"></div></div>
                <div class="pipe-stage-pct">占比 ${Math.round((p.count / total) * 100)}%</div>
              </div>
              ${i < O.pipeline.length - 1 ? `<div class="pipe-arrow">${UI.icon("chevron", 14)}</div>` : ""}
            `).join("")}
          </div>
        </div>

        <div class="section-title">风险订单 <span class="section-extra">延期风险 / 缺料关联 / 客户等级</span></div>
        <div class="card">
          ${O.risks.map((r) => {
            const levelCls = r.level === "S" ? "badge-red" : r.level === "A" ? "badge-amber" : "badge-gray";
            const tagList = (r.tagsList || r.tags || []);
            return `<div class="risk-order">
              <div class="ro-head">
                <span class="ro-no">${UI.esc(r.no)}</span>
                ${UI.badge(r.customer, "badge-blue")}
                ${UI.badge("客户等级 " + r.level, levelCls)}
                ${UI.badge("延期 " + r.delay_days + " 天", r.delay_days >= 3 ? "badge-red" : "badge-amber")}
                ${tagList.map((t) => UI.badge(t, "badge-outline")).join("")}
              </div>
              <div class="ro-body">
                <span>产品 <b>${UI.esc(r.product)}</b></span>
                <span>缺料关联 <b>${UI.esc(r.shortage)}</b></span>
              </div>
              <div class="ro-foot">
                <span style="font-size:11.5px;color:var(--text-3)">履约进度</span>
                <div class="progress"><div class="progress-bar ${r.progress < 50 ? "warn" : ""}" style="width:${r.progress}%"></div></div>
                <span style="font-size:11.5px;color:var(--text-3);font-variant-numeric:tabular-nums">${r.progress}%</span>
              </div>
            </div>`;
          }).join("")}
        </div>

        <div class="section-title">部门协同状态 <span class="section-extra">16 部门 → 5 域组 · 今日待办 / 已闭环 / 超时 ${UI.demoFlag()}</span></div>
        <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(230px,1fr))">
          ${O.domains.map((d) => {
            const todo = d.depts.reduce((s, x) => s + x.todo, 0);
            const done = d.depts.reduce((s, x) => s + x.done, 0);
            const over = d.depts.reduce((s, x) => s + x.over, 0);
            return `<div class="card dept-domain">
              <div class="dept-domain-head">
                <span class="stat-icon ${d.name === "制造域" ? "blue" : d.name === "品质域" ? "purple" : d.name === "供应链域" ? "green" : d.name === "设备域" ? "amber" : "red"}" style="width:30px;height:30px;border-radius:8px">${UI.icon(d.icon, 15)}</span>
                <span class="dept-domain-name">${UI.esc(d.name)}</span>
                <span class="dept-domain-count">${d.depts.length} 部门</span>
              </div>
              <table class="dept-table">
                <thead><tr><th>部门</th><th style="text-align:right">待办</th><th style="text-align:right">闭环</th><th style="text-align:right">超时</th></tr></thead>
                <tbody>
                  ${d.depts.map((x) => `<tr>
                    <td>${UI.esc(x.name)}</td>
                    <td class="num">${x.todo}</td>
                    <td class="num" style="color:var(--ok)">${x.done}</td>
                    <td class="num ${x.over > 0 ? "over" : ""}">${x.over}</td>
                  </tr>`).join("")}
                  <tr><td style="font-weight:600;color:var(--text)">小计</td>
                    <td class="num" style="font-weight:600">${todo}</td>
                    <td class="num" style="font-weight:600;color:var(--ok)">${done}</td>
                    <td class="num ${over > 0 ? "over" : ""} style="font-weight:600">${over}</td></tr>
                </tbody>
              </table>
            </div>`;
          }).join("")}
        </div>
      </div>`;
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
  ]);
  const QUICK_QUESTIONS = [
    "A型号在SMT-1停线，何时复线？",
    "今天锡膏批次有问题",
    "华为投诉BATCH-A-1008",
    "A型号能发多少货",
    "SMT-1设备参数异常吗",
  ];
  // 节点展示名（YAML label 未定义时的兜底；事件通常已带 label）
  const NODE_LABELS = {
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
  };

  function chatStore(ctx) {
    const st = ctx.state.screenState;
    if (!st.chat) st.chat = { messages: [], seq: 0 };
    return st.chat;
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

  function quickCommentChipsHtml(taskUid, slot) {
    const list = QUICK_COMMENTS[slot] || [];
    if (!list.length) return "";
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
      <div class="content-inner" style="max-width:1040px">
        <div class="chat-screen-head">
          <div class="page-title">${UI.icon("chat", 19)}协同问数</div>
          ${online
            ? UI.realFlag("真实链路 · 后端在线")
            : `<span class="offline-banner" style="padding:5px 11px">${UI.icon("warn", 13)}后端离线——当前为演示模式：对话使用预置演示结果回放（已标注演示）</span>`}
          <div class="head-extra">
            <span style="font-size:12px;color:var(--text-3)">问数角色</span>
            ${UI.badge(ctx.currentRole().name, "badge-blue")}
            <button class="btn btn-outline btn-sm" id="chat-clear">${UI.icon("refresh", 12)}清空对话</button>
          </div>
        </div>
        <div class="chat-wrap">
          <div class="chat-scroll" id="chat-scroll"></div>
          <div class="chat-inputbar">
            <div class="chat-chips" id="chat-chips">
              ${QUICK_QUESTIONS.map((q) => `<button class="chip" data-q="${UI.esc(q)}">${UI.esc(q)}</button>`).join("")}
            </div>
            <div class="chat-history" id="chat-history">
              <span class="chat-history-label">${UI.icon("clock", 12)}最近问过（点击重问）</span>
              <span id="chat-history-items"></span>
            </div>
            <div class="chat-input-row">
              <textarea class="chat-input" id="chat-input" rows="1" placeholder="请输入制造业协同问题，例如：A型号在SMT-1停线，何时复线？"></textarea>
              <button class="chat-send" id="chat-send" title="发送">${UI.icon("send", 17)}</button>
            </div>
            <div class="chat-hint">问数链路：两级路由 → 场景 DAG（三域并行取证 → 根因分析 → 工作流工单 → 人工确认 → 报告）· 全部结论数据可追溯</div>
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
        histBox.innerHTML = `<span style="color:var(--text-3);font-size:11.5px">暂无（问完自动记录）</span>`;
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
      renderChatMessages(ctx, scroll);
      ctx.UI.toast("对话已清空", "info", 1500);
    });

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

    // 其他屏下钻带来的预填问题（KPI 卡/全局搜索）：自动发送一次
    let prefill = null;
    try { prefill = sessionStorage.getItem("chat_prefill"); } catch (e) { /* 忽略 */ }
    if (prefill) {
      try { sessionStorage.removeItem("chat_prefill"); } catch (e) { /* 忽略 */ }
      sendChatQueryRouter(ctx, scroll, prefill);
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
    return { user_id: "u-" + r.id, dept: r.dept, roles: r.roles.slice() };
  }

  function nearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  }

  function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
  }

  function renderChatMessages(ctx, scroll) {
    const chat = chatStore(ctx);
    if (!chat.messages.length) {
      scroll.innerHTML = `<div class="empty" style="margin:auto">${ctx.UI.icon("chat", 34)}<div style="margin-top:10px;font-size:13px">向智能总控提问，或点击上方快捷问题开始</div></div>`;
      return;
    }
    scroll.innerHTML = chat.messages.map((m) => renderChatMessage(ctx, m)).join("");
    bindChatActions(ctx, scroll);
    bindQuickComments(scroll);
    scrollToBottom(scroll);
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
    else if (m.kind === "fallback") body = fallbackCardHtml(ctx, m.result, { demo: !!m.demo });
    else if (m.kind === "hitl") body = hitlChatCardHtml(ctx, m);
    else if (m.kind === "error") body = `<div class="result-card"><div class="result-head"><div class="result-title">${UI.icon("warn", 15)}执行异常</div></div><div class="result-body"><div class="offline-banner">${UI.icon("warn", 14)}<span>${UI.esc(m.text)}</span></div></div></div>`;
    return `<div class="msg" data-msg-id="${m.id}">${avatar}<div class="msg-panel">${body}</div></div>`;
  }

  function bindChatActions(ctx, scroll) {
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

  async function sendChatQuery(ctx, scroll, text) {
    const UI = ctx.UI;
    const chat = chatStore(ctx);
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
      result = await ctx.api.query(text, requestId, userCtxFor(ctx));
    } catch (err) {
      chat.messages.push({ kind: "error", id: "m" + ++chat.seq, text: `问数请求失败：${err && err.message ? err.message : String(err)}。请检查后端服务后重试。` });
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
    chat.messages.push(resultCardMessage(ctx, result));
    (result.hitl || []).forEach((h) => {
      chat.messages.push({
        kind: "hitl", id: "m" + ++chat.seq, taskUid: h.task_uid || "",
        prompt: h.prompt || "", approvers: h.approver_roles || [],
        actions: (h.actions || []).map((a) => (typeof a === "string" ? { key: a, label: a } : a)),
        slot: h.slot || h.kind || "",
        governance: h.governance || null,
        signoffStatus: h.signoff_status || null,
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
        text: "后端离线：该问题未收录离线回放（离线回放覆盖 5 个快捷问题）。请启用在线模式（起后端服务）运行真实链路，或点击上方 5 个快捷问题之一。",
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
      chat.messages.push({
        kind: "hitl", id: "m" + ++chat.seq, taskUid: h.task_uid || "",
        prompt: h.prompt || "", approvers: h.approver_roles || [],
        actions: (h.actions || []).map((a) => (typeof a === "string" ? { key: a, label: a } : a)),
        runId: demo.result.run_id, demo: true,
      });
    });
    if (ctx.state.screen === "chat") renderChatMessages(ctx, scroll);
    if (sendBtn) sendBtn.disabled = false;
    if (input) { input.disabled = false; input.focus(); }
  }

  /** 统一发送入口：在线走真实链路，离线走演示回放 */
  function sendChatQueryRouter(ctx, scroll, text) {
    if (ctx.api.online()) return sendChatQuery(ctx, scroll, text);
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
    let stateHtml;
    if (flow.status === "done") stateHtml = `${UI.icon("check", 12)} 已完成 · ${flow.order.length} 节点`;
    else if (flow.status === "error") stateHtml = `${UI.icon("warn", 12)} 执行异常`;
    else stateHtml = `${UI.spinner()} 执行中（${running} 个节点运行中）`;
    return `<div class="flow-panel">
      <div class="flow-head">${UI.icon("sparkle", 14)} 执行过程<span class="flow-state">${stateHtml}</span></div>
      <div class="flow-body">${flowBodyHtml(ctx, flow)}</div>
    </div>`;
  }

  function flowBodyHtml(ctx, flow) {
    let html = "";
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
    if (!html) html = `<div style="font-size:12.5px;color:var(--text-3)">等待节点启动…</div>`;
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
    if (result && (result.fallback === "manual_ticket" || !result.scenario_key)) {
      return { kind: "fallback", id: "m" + ++chat.seq, result, demo: !!demo };
    }
    return { kind: "result", id: "m" + ++chat.seq, result, demo: !!demo };
  }

  function resultCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const route = result.route || {};
    const brief = result.internal_brief || {};
    const degraded = !!result.degraded;
    const faText = brief["风险评级"] || "";
    const ratingMatch = /评级\s*([A-D])/.exec(faText);
    const etaMatch = /预计\s*(\d+(?:\.\d+)?)\s*h/i.exec(faText);
    // data_gap：无数据源的项（不编造）——前端必须显式渲染（架构纪律）
    const gapNotes = Array.from(new Set(
      (result.provenance || []).filter((p) => p.channel === "data_gap")
        .map((p) => p.note).filter(Boolean)));

    const briefItems = Object.keys(brief).map((k) => {
      let v = UI.esc(brief[k] === true ? "True" : brief[k] === false ? "False" : brief[k] || "—");
      let extra = "";
      if (k === "风险评级" && ratingMatch) {
        extra = ` <span class="rating-pill rating-${ratingMatch[1]}">${ratingMatch[1]}</span>`;
      }
      return `<div class="brief-item"><span class="k">${UI.esc(k)}</span><span class="v">${v}${extra}</span></div>`;
    }).join("");

    const tickets = (result.workflow_tasks || []).map((w) => {
      const t = w.ticket || {};
      return `<div class="brief-item" style="grid-column:1/-1"><span class="k">工单</span><span class="v">`
        + `<b>${UI.esc(t.task_uid || "-")}</b> [${UI.esc(t.status || "-")}] ${UI.esc(t.title || "")}`
        + ` | 受理：${UI.esc((t.assignees || []).join("、"))}`
        + `${t.sla_due_at ? " | SLA " + UI.fmtDateTime(t.sla_due_at) : ""}`
        + `</span></div>`;
    }).join("");

    return `<div class="result-card">
      <div class="result-head">
        <div class="result-title">${UI.icon("file", 15)}分析简报 · ${UI.esc(result.scenario_key || "未知场景")}</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : UI.realFlag("真实链路")}
          ${UI.badge("路由 " + (route.level || "-"), route.level === "L0" ? "badge-blue" : "badge-amber")}
          ${UI.badge("匹配度 " + (route.confidence != null ? route.confidence : "-"), "badge-outline")}
          ${degraded ? UI.badge("受限模式", "badge-amber") : UI.badge("完整模式", "badge-green")}
          ${UI.badge(result.run_id || "", "badge-gray")}
        </div>
      </div>
      <div class="result-body">
        ${degraded ? (() => {
          const reasons = result.degraded_reasons || [];
          const svcDown = reasons.some((r) => /服务|模型|分析/.test(r));
          const dataGap = reasons.some((r) => /数据缺口|数据不完整|缺少数据源/.test(r));
          const mode = svcDown ? "分析服务未就绪，部分结论采用模板化输出"
            : dataGap ? "部分环节缺少数据源，相关结论已标注缺口"
              : "部分环节采用受限输出";
          return `<div class="degrade-banner" style="margin-bottom:12px">${UI.icon("warn", 14)}<span><b>受限模式：${UI.esc(mode)}</b>${reasons.length ? "（" + UI.esc(reasons.join("；")) + "）" : ""}。全部结论仍可追溯数据来源，可人工复核后放行。</span></div>`;
        })() : ""}
        ${gapNotes.length ? `<div class="offline-banner" style="margin-bottom:12px">${UI.icon("info", 14)}<span><b>数据缺口（data_gap）：该数据当前无来源</b>，平台不编造数据，相关项已转人工/现场确认 —— ${UI.esc(gapNotes.join("；"))}</span></div>` : ""}
        <div class="brief-grid">${briefItems}${tickets}</div>
        ${etaMatch ? `<div style="margin-top:10px;font-size:12.5px;color:var(--text-2)">${UI.icon("clock", 13)} 预计复线时间：<b style="color:var(--text)">${UI.esc(etaMatch[1])} 小时</b> · 总耗时 ${UI.esc(String(result.elapsed_ms || "-"))}ms</div>` : ""}
        ${provenanceHtml(ctx, result)}
      </div>
    </div>`;
  }

  function fallbackCardHtml(ctx, result, opts) {
    const UI = ctx.UI;
    const demo = opts && opts.demo;
    const route = result.route || {};
    const items = [
      ["路由等级", route.level || "-"],
      ["匹配场景", route.scenario_key || "未匹配（超出已覆盖范围）"],
      ["匹配度", route.confidence != null ? String(route.confidence) : "-"],
      ["低置信标记", route.low_confidence ? "是（建议人工复核）" : "否"],
      ["路由依据", route.reason || "-"],
      ["抽取实体", JSON.stringify(route.entities || {})],
    ].map(([k, v]) => `<div class="brief-item"><span class="k">${UI.esc(k)}</span><span class="v">${UI.esc(v)}</span></div>`).join("");
    return `<div class="result-card">
      <div class="result-head">
        <div class="result-title">${UI.icon("shield", 15)}人工兜底工单</div>
        <div class="badges">
          ${demo ? UI.demoFlag("演示回放") : UI.realFlag("真实链路")}
          ${UI.badge("转人工", "badge-amber")}
          ${UI.badge(result.run_id || "", "badge-gray")}
        </div>
      </div>
      <div class="result-body">
        <div class="offline-banner" style="margin-bottom:12px">${UI.icon("info", 14)}<span><b>当前问题超出平台已覆盖范围（或路由匹配度低），已转人工兜底工单处理。</b>人工兜底是平台设计的一部分——确定性流程之外的问题不臆造结论。可改问停线场景相关问题，例如「A型号在SMT-1停线，何时复线？」。</span></div>
        <div class="brief-grid">${items}</div>
        ${result.report ? `<div style="margin-top:10px;font-size:12.8px;color:var(--text-2);line-height:1.8">${UI.icon("file", 13)} ${UI.esc(result.report)}</div>` : ""}
      </div>
    </div>`;
  }

  function provenanceHtml(ctx, result) {
    const UI = ctx.UI;
    const all = result.provenance || [];
    const sqlItems = all.filter((p) => p.sql);
    const otherItems = all.filter((p) => !p.sql && p.channel !== "node_timing");
    const timings = all.filter((p) => p.channel === "node_timing");

    const sqlHtml = sqlItems.map((p) => `
      <div class="prov-item">
        <div class="prov-item-head">
          ${UI.badge(p.domain || "-", "badge-blue")}
          <span class="prov-item-name">${UI.esc(p.query_name || p.metric_id || "-")}</span>
          ${UI.badge(p.channel || "-", "badge-outline")}
          ${p.masked ? `<span class="mask-flag">${UI.icon("eyeoff", 12)}敏感字段已按角色脱敏</span>` : ""}
        </div>
        <div class="sql-block">${UI.esc(p.sql)}</div>
        <div class="prov-item-meta">
          <span>命中 <b>${UI.esc(String(p.row_count != null ? p.row_count : 0))}</b> 行</span>
          <span>表：<b>${UI.esc((p.tables || []).join(", "))}</b></span>
          <span>脱敏：<b>${p.masked ? "是" : "否"}</b></span>
        </div>
      </div>`).join("");

    const otherHtml = otherItems.map((p) => {
      const label = p.channel === "rag" ? `RAG 检索：${UI.esc(p.query || "")}（命中 ${UI.esc(String(p.case_count != null ? p.case_count : 0))} 条历史案例）`
        : p.channel === "workflow" ? `工作流工单：<b>${UI.esc(p.task_uid || "")}</b>（${UI.esc((p.assignees || []).join("、"))}）`
        : p.channel === "hitl" ? `人工确认 待确认：<b>${UI.esc(p.task_uid || "")}</b>（${UI.esc((p.approver_roles || []).join("、"))}）`
        : p.channel === "report" ? `报告生成：degraded=${p.degraded ? "是" : "否"}，禁词表兜底 ${UI.esc(String((p.masked_words || []).length))} 词`
        : p.channel === "data_gap" ? `数据缺口（无来源，不编造）：${UI.esc(p.note || "")}`
        : p.channel === "compute" ? `代码计算：${UI.esc(p.formula || "")}（来源：${UI.esc(p.source || "")}）`
        : p.channel === "template" ? `规则模板：${UI.esc(p.note || "模板化结论")}`
        : UI.esc(JSON.stringify(p));
      return `<div class="prov-item"><div class="prov-item-head">${UI.badge(p.domain || p.channel || "-", "badge-purple")}<span style="font-size:12.3px;color:var(--text-2)">${label}</span></div></div>`;
    }).join("");

    const timingHtml = timings.length
      ? `<div class="prov-item"><div class="prov-item-head">${UI.badge("节点耗时", "badge-gray")}<span style="font-size:12px;color:var(--text-3)">${timings.length} 个节点</span></div>
         <div class="chip-row" style="margin-top:7px">${timings.map((t) => `<span class="badge ${t.status === "ok" ? "badge-green" : "badge-amber"}">${UI.esc(t.node || "-")} ${UI.esc(String(t.elapsed_ms != null ? Math.round(t.elapsed_ms) + "ms" : "-"))}</span>`).join("")}</div></div>`
      : "";

    if (!sqlItems.length && !otherItems.length && !timings.length) return "";
    return `<details class="prov">
      <summary>${UI.icon("database", 14)}数据溯源（SQL 原文 · 命中行 · 脱敏标记 · 节点耗时）<span class="prov-count">${sqlItems.length + otherItems.length + timings.length} 条</span></summary>
      <div class="prov-body">${sqlHtml}${otherHtml}${timingHtml}</div>
    </details>`;
  }

  /* ---------- 人工确认 待确认卡片（对话流内） ---------- */

  // 演示岗位（前端登录身份）→ 治理角色 key（与 data/permissions.yaml 的 roles 对齐）。
  // 后端以 user_ctx.roles 为权威，前端此映射只用于"预先灰显/提示"，不承担安全职责。
  const ROLE_GOV = {
    gm: ["gm"],
    mfg: ["manufacturing"],
    sqe: ["sqe"],
    pqe: ["pqe"],
    qa: ["qa"],
    mc: ["material_control"],
    biz: ["sales"],
    proc: ["procurement"],
    wh: ["warehouse"],
    rd: ["rd_engineering"],
    // G6 会签独立性铁律：一个岗位只允许对应**一个**治理角色。
    // 同一身份持多个会签角色 = 一个人可签多票 = 会签造假入口。
    // 演示三方会签的正确方式：换身份登录（sqe/pqe/qa 已拆分为独立岗位）。
  };

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
  };

  const ROLE_LABEL = {
    gm: "总经理", quality_director: "质量总监", qa_manager: "QA经理",
    qa: "QA", sqe: "SQE", pqe: "PQE", procurement: "采购", planning: "计划",
    material_control: "物控", manufacturing: "制造", equipment: "设备工程",
    sales: "业务", warehouse: "仓库", rd_engineering: "工程", finance: "财务",
  };

  /** 中文写法/别名 → 治理角色 key（未识别返回 null，与后端一样 fail-closed） */
  function roleKey(name) {
    const k = String(name == null ? "" : name).trim().toLowerCase();
    return ROLE_KEY_BY_NAME[k] || null;
  }

  function roleKeysOf(names) {
    return (names || []).map(roleKey).filter(Boolean);
  }

  /** 当前登录身份对应的治理角色 key 列表 */
  function govRoles(ctx) {
    const r = ctx.currentRole();
    return ROLE_GOV[r.id] || [];
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
      const task = await ctx.api.workflowCosign(taskUid, role.user,
        govRoles(ctx), role.dept, comment, "", role.uid);
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
        role.user, govRoles(ctx), role.dept, comment, role.uid);
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
    return `<div class="hitl-card" data-hitl-task="${UI.esc(m.taskUid)}">
      <div class="hitl-card-head">${UI.icon("approve", 15)}待人工确认（不自动放行）</div>
      <div class="hitl-card-prompt">${UI.esc(m.prompt)}</div>
      <div class="hitl-card-meta">
        <span>审批人：<b>${UI.esc((m.approvers || []).join("、"))}</b></span>
        <span>工单：<b>${UI.esc(m.taskUid || "-")}</b></span>
        ${m.runId ? `<span>run_id：<b>${UI.esc(m.runId)}</b></span>` : ""}
      </div>
      ${acts.length ? `<div class="hitl-card-meta">可选动作（按岗位权限，灰显需对应岗位发起）：</div>
        <div class="act-chips">${actionChipsHtml(ctx, m.taskUid, m.actions)}</div>
        ${actionDetailHtml(ctx, m.taskUid, m.actions)}` : ""}
      ${signoffPanelHtml(ctx, m.taskUid, m.actions)}
      ${processed ? "" : `${quickCommentChipsHtml(m.taskUid, m.slot || _hitlSlot[taskUid] || "")}
      <input class="input act-comment" id="act-comment-${UI.esc(m.taskUid)}" placeholder="决策意见（可选，将写入工单留痕）" />
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
      return { ok: false, reason: "请先选定处置动作（select_action）" };
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
   * ============================================================ */

  function render_alerts(root, ctx) {
    const UI = ctx.UI, DEMO = ctx.DEMO;
    const st = ctx.state.screenState;
    if (!st.alertFilter) st.alertFilter = "全部";
    const types = ["全部", "SPC越限", "缺料", "订单临期", "设备告警"];
    const typeCls = { "SPC越限": "badge-purple", "缺料": "badge-amber", "订单临期": "badge-red", "设备告警": "badge-blue" };
    const statusCls = { "待处置": "badge-red", "处置中": "badge-blue", "已闭环": "badge-green" };
    const list = st.alertFilter === "全部" ? DEMO.ALERTS : DEMO.ALERTS.filter((a) => a.type === st.alertFilter);

    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("alert", 20)}预警中心</div>
          <div class="page-desc">SPC 越限 / 缺料 / 订单临期 / 设备告警四类预警，每条标注联动部门（体现多域协同设计）。${UI.demoFlag()}</div>
        </div>
        <div class="chip-row" style="margin-bottom:14px" id="alert-filters">
          ${types.map((t) => `<button class="chip${st.alertFilter === t ? " active" : ""}" data-filter="${UI.esc(t)}">${UI.esc(t)}${t === "全部" ? ` (${DEMO.ALERTS.length})` : ` (${DEMO.ALERTS.filter((a) => a.type === t).length})`}</button>`).join("")}
        </div>
        <div class="card">
          ${list.length ? list.map((a) => `
            <div class="alert-item">
              <div class="alert-level ${a.level}">${a.level}</div>
              <div class="alert-main">
                <div class="alert-title">${UI.esc(a.title)} ${UI.badge(a.type, typeCls[a.type] || "badge-gray")}</div>
                <div class="alert-detail">${UI.esc(a.detail)}</div>
                <div class="alert-tags">
                  <span class="tag-label">联动部门：</span>
                  ${a.tags.map((t) => UI.badge(t, "badge-outline")).join("")}
                </div>
                <div class="alert-foot"><span>编号 ${UI.esc(a.id)}</span><span>责任人 ${UI.esc(a.owner)}</span><span>触发时间 ${UI.esc(a.time)}</span></div>
              </div>
              <div class="alert-status">${UI.badge(a.status, statusCls[a.status] || "badge-gray", true)}</div>
            </div>`).join("") : `<div class="empty">该类型暂无预警</div>`}
        </div>
      </div>`;

    root.querySelectorAll("[data-filter]").forEach((btn) => {
      btn.addEventListener("click", () => { ctx.state.screenState.alertFilter = btn.dataset.filter; ctx.refreshScreen(); });
    });
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

        <div class="report-cols">
          <div class="report-doc">
            <div class="report-doc-head internal">
              ${UI.icon("file", 16)}
              <div><div class="report-doc-title">${UI.esc(R.internal.title)}</div>
              <div style="font-size:11.5px;color:var(--text-3)">run_id ${UI.esc(R.internal.run_id)} · ${UI.esc(R.internal.route.scenario)}</div></div>
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
              <div style="font-size:11.5px;color:var(--text-3)">字段级脱敏 + 禁词表兜底（node_registry.FORBIDDEN_WORDS）</div></div>
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
  }

  /* ============================================================
   * 屏幕六：审批确认中心
   * ============================================================ */

  function render_hitl(root, ctx) {
    const UI = ctx.UI;
    root.innerHTML = `
      <div class="content-inner">
        <div class="page-head">
          <div class="page-title">${UI.icon("approve", 20)}审批确认中心</div>
          <div class="page-desc">关键决策（复线、对客交期、退换货、8D 关闭等）须由对应岗位人工确认，绝不自动放行。${UI.realFlag("WorkflowEngine 待办队列")}</div>
        </div>
        <div class="hitl-layout">
          <div class="card">
            <div class="card-head">
              <div class="card-title">${UI.icon("list", 16)}待审批列表</div>
              <div class="card-extra" id="hitl-source">加载中…</div>
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

    if (ctx.api.online()) {
      try {
        // 「审批即待办」：只拉当前岗位可动作的工单（后端按 my_action 过滤）
        const raw = await ctx.api.workflowPending(govRoles(ctx));
        tasks = mergeProcessedTasks(ctx, raw);
        tasks.forEach((t) => { if (t.task_uid && t.governance) _hitlGov[t.task_uid] = t; });
        if (!tasks.length) {
          // 兜底：当前岗位无待办时，只读展示队列中「待其他岗位处理」的工单，
          // 避免审批中心出现"看起来没活"的误导（可动作工单仍只有我的待办）。
          const all = await ctx.api.workflowPending();
          const others = mergeProcessedTasks(ctx, all)
            .filter((t) => t.task_uid && !processedMap(ctx)[t.task_uid]);
          if (others.length) {
            tasks = others.map((t) => ({ ...t, _readonly: true }));
            readonlyMode = true;
          }
        }
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
        : readonlyMode
          ? UI.badge("当前岗位无待办 · 只读展示他人待办", "badge-amber")
          : UI.realFlag("我的待办（" + roleLabels(govRoles(ctx)).join("/") + "）");
    }

    if (!tasks.length) {
      listEl.innerHTML = `<div class="empty">${UI.icon("check", 30)}<div style="margin-top:8px">当前无待审批工单</div>
        <div style="font-size:11.5px;margin-top:6px">可在「协同问数」发起停线问数，人工确认 节点将生成真实待办</div></div>`;
      document.getElementById("hitl-detail").innerHTML = "";
      return;
    }

    const st = ctx.state.screenState;
    if (!st.hitlSelected || !tasks.some((t) => t.task_uid === st.hitlSelected)) {
      st.hitlSelected = tasks[0].task_uid;
    }
    listEl.innerHTML = tasks.map((t) => hitlListItemHtml(ctx, t)).join("");
    listEl.querySelectorAll("[data-select]").forEach((el) => {
      el.addEventListener("click", () => {
        ctx.state.screenState.hitlSelected = el.dataset.select;
        listEl.querySelectorAll(".hitl-list-item").forEach((x) => x.classList.remove("selected"));
        el.classList.add("selected");
        const task = tasks.find((x) => x.task_uid === el.dataset.select);
        renderHitlDetail(ctx, task);
      });
    });
    const current = tasks.find((t) => t.task_uid === st.hitlSelected) || tasks[0];
    renderHitlDetail(ctx, current);
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

  function hitlListItemHtml(ctx, t) {
    const UI = ctx.UI;
    const st = ctx.state.screenState;
    const processed = processedMap(ctx)[t.task_uid];
    const due = slaDueIso(t);
    const total = slaTotalMs(t);
    const selected = st.hitlSelected === t.task_uid;
    const signoff = signoffOf(t.task_uid, (t.context && t.context.actions) || []);
    const progress = signoff.total
      ? `<span>会签 ${UI.esc(signoff.progress)}${signoff.satisfied ? " ✓" : ""}</span>` : "";
    return `<div class="hitl-list-item${selected ? " selected" : ""}${processed ? " processed" : ""}" data-select="${UI.esc(t.task_uid)}">
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
  };

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
      ["工单类型", kindLabel + "（" + (t.wf_id || "-") + "）"],
      ["治理槽位", slot || "未纳管（老工单）"],
      ["已选动作", signoffOf(t.task_uid, c.actions).selected_action || "-"],
      ["我的待办", MY_ACTION_LABEL[t.my_action] || "只看不办"],
      ["run_id", c.run_id || "-"],
      ["线体 / 型号", (c.line_code || "-") + " / " + (c.product_model || "-")],
      ["风险评级", c.fa_rating || "-"],
      ["预估复线", c.eta_hours != null ? c.eta_hours + " 小时" : "-"],
      ["受理人", (t.assignees || []).join("、") || "-"],
      ["创建时间", t.created_at ? UI.fmtDateTime(t.created_at) : "-"],
      ["SLA 截止", UI.fmtDateTime(due)],
    ].map(([k, v]) => `<div class="kv"><span class="kv-key">${UI.esc(k)}</span><span class="kv-val">${UI.esc(v)}</span></div>`).join("");

    const history = (t.history || []).slice().reverse().map((h) => `
      <div class="history-item">
        <span class="h-action">${UI.esc(h.action || "-")}</span>
        <span>${UI.esc(h.from || "?")} → ${UI.esc(h.to || "?")}</span>
        <span class="h-ts">${UI.esc(h.ts ? UI.fmtDateTime(h.ts) : "")}</span>
        <span style="color:var(--text-3)">${UI.esc(h.actor || "")}${h.comment ? " · " + UI.esc(h.comment) : ""}</span>
      </div>`).join("");

    box.innerHTML = `
      <div class="card card-pad">
        <div class="hitl-detail-head">
          <div class="hitl-detail-title">${UI.esc(t.hitl_prompt || t.title)}</div>
          <div style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap">
            ${processed ? UI.badge("已处理 · " + processed.status, "badge-green") : UI.badge(t.status || "waiting_hitl", "badge-red", true)}
            ${UI.badge(t.priority || "P1", "badge-amber")}
            ${myActionBadge(ctx, t)}
            ${t.demo ? UI.demoFlag("演示数据") : UI.realFlag("真实工单")}
          </div>
        </div>
        <div style="margin-top:12px;font-size:12.5px;color:var(--text-2);line-height:1.75">
          ${UI.icon("info", 13)} 关键决策必须人工确认后放行（规则：分析服务只出建议，不参与审批与过账）。多人会签的动作需全部会签人签署后才可终批，岗位权限由后端 WorkflowEngine 校验。
        </div>
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

        ${processed ? hitlProcessedBanner(ctx, t.task_uid) : (t._readonly
          ? `<div class="gate-hint">${UI.icon("info", 13)} 当前岗位对该工单无待办动作（只读视图）：审批/会签由对应岗位执行，安全校验在后端。</div>`
          : `
        <div class="hitl-actions-bar">
          ${quickCommentChipsHtml(t.task_uid, (t.governance || {}).slot || "")}
          <input class="input hitl-comment" id="hitl-comment" placeholder="决策意见（可选，将写入工单 history）" />
          <button class="btn btn-success" id="hitl-approve"
            ${gate.ok ? "" : `disabled title="${UI.esc(gate.reason)}"`}>${UI.icon("check", 14)}同意</button>
          <button class="btn btn-danger" id="hitl-reject">${UI.icon("x", 14)}驳回</button>
        </div>
        ${gate.ok ? "" : `<div class="gate-hint">${UI.icon("info", 13)} ${UI.esc(gate.reason)}</div>`}`)}
      </div>`;

    wireActionChips(document, ctx, t.task_uid);
    bindQuickComments(document);
    wireCosign(document, ctx, t.task_uid, (c.actions || []));
    const approveBtn = document.getElementById("hitl-approve");
    const rejectBtn = document.getElementById("hitl-reject");
    if (approveBtn) approveBtn.addEventListener("click", () => hitlAction(ctx, t.task_uid, "approve"));
    if (rejectBtn) rejectBtn.addEventListener("click", () => hitlAction(ctx, t.task_uid, "reject"));
  }

  /* ============================================================
   * 导出
   * ============================================================ */

  window.Screens = {
    render_dashboard,
    render_orders,
    render_chat,
    render_alerts,
    render_reports,
    render_hitl,
  };
})();
