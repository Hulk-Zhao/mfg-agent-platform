/**
 * 场景组件工具箱（T05 交付物 · `window.SceneKit`）
 *
 * 定位：T06–T11 十二个场景屏共用的**最小可用组件集**。零依赖、零构建，
 * 只产出 HTML 字符串；样式在首次调用时注入一次（`<style id="scene-kit-style">`），
 * 类名统一 `sk-` 前缀，颜色一律取 app.css 的 CSS 变量——本项目是 light 主题，
 * 这里**禁止**硬编码深色底，否则夜间反馈里那种"黑块浮在白卡片上"会立刻出现。
 *
 * 三条纪律（都对应一次真实踩坑）：
 *  1. 「演示数据」徽标只在 `demoBadge()` 一处产出：T12 要做框架级强制挂载时，
 *     改这一处即可覆盖全部场景屏；散落到各屏就再也收不回来。
 *  2. `story()` 是公共故事数据的**唯一**入口：优先打 T04 装载层
 *     `GET /api/v2/demo/objects/{name}`，失败回落本地 `window.DEMO_STORY`，
 *     再失败返回 `{ok:false}` 由屏自己显示降级提示。
 *     它**永不 reject、永不把 err.message 透给用户**——装载层未交付时，
 *     页面必须说「未就绪」，而不是"Failed to fetch"。
 *  3. `openDrill()` 自己建 overlay 并自己拆监听，不往全局挂状态。
 */
(function () {
  "use strict";

  const STYLE_ID = "scene-kit-style";

  /** 当前渲染上下文（由 `use(ctx)` 绑定，供 esc/icon/badge 等转发）。 */
  let current = null;

  /** 当前仍存活的下钻弹层（同一时刻只允许一个）。 */
  let activeDrill = null;

  /** 事件类型 → 中文标签（口径见 spec-v1.md §2.10）。 */
  const EVENT_TYPE_LABELS = {
    order: "订单",
    order_status: "订单状态",
    report: "报告",
    inspection: "检验",
    alert: "预警",
    hitl: "会签",
    ecn: "ECN",
    complaint: "客诉",
  };

  /** 会签槽位状态 → 文案 + 徽标样式。 */
  const SIGNOFF_STATUS = {
    pending: ["待会签", "badge-amber"],
    approved: ["已通过", "badge-green"],
    rejected: ["已驳回", "badge-red"],
  };

  /* ============================================================
   * 一、样式（一次性注入）
   * ============================================================ */

  const CSS = `
/* 场景组件工具箱（SceneKit）：类名一律 sk- 前缀，颜色只取 app.css 变量 */
.sk-block { margin-top: 18px; }
.sk-note { font-size: 11.5px; color: var(--text-3); line-height: 1.6; margin-top: 8px; }
.sk-head-extra { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }

/* 数字卡 */
.sk-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.sk-grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.sk-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.sk-grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }

.sk-num-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  text-align: left;
  font: inherit;
  color: inherit;
}
.sk-num-card.is-clickable { cursor: pointer; transition: box-shadow .16s ease, transform .16s ease, border-color .16s ease; }
.sk-num-card.is-clickable:hover { box-shadow: var(--shadow-md); border-color: var(--border-strong); transform: translateY(-1px); }
.sk-num-card.is-clickable:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.sk-num-label { font-size: 12.5px; color: var(--text-2); display: flex; align-items: center; gap: 6px; }
.sk-num-main { display: flex; align-items: baseline; gap: 5px; }
.sk-num-value { font-size: 25px; font-weight: 700; line-height: 1.15; color: var(--text); word-break: break-all; }
.sk-num-unit { font-size: 11.5px; color: var(--text-3); }
.sk-num-hint { font-size: 11.5px; color: var(--text-3); line-height: 1.55; }
.sk-num-tip { position: absolute; top: 10px; right: 12px; font-size: 10.5px; color: var(--text-3); letter-spacing: .3px; }
.sk-tone-blue .sk-num-value { color: var(--primary); }
.sk-tone-green .sk-num-value { color: var(--ok); }
.sk-tone-amber .sk-num-value { color: var(--warn); }
.sk-tone-red .sk-num-value { color: var(--danger); }
.sk-tone-purple .sk-num-value { color: var(--purple); }
.sk-tone-gray .sk-num-value { color: var(--text-2); }

/* 事件流 */
.sk-flow-wrap { margin-top: 2px; }
.sk-flow { display: flex; gap: 0; overflow-x: auto; padding: 6px 0 10px; }
.sk-flow-item { position: relative; flex: 1 1 0; min-width: 168px; padding: 22px 14px 0 0; }
.sk-flow-item::before {
  content: ""; position: absolute; left: 2px; top: 7px;
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--text-3); box-shadow: 0 0 0 3px var(--card);
}
.sk-flow-item::after {
  content: ""; position: absolute; left: 14px; top: 11px;
  width: calc(100% - 16px); height: 2px; background: var(--border);
}
.sk-flow-item:last-child::after { display: none; }
.sk-flow-item.sk-t-order::before { background: var(--primary); }
.sk-flow-item.sk-t-order_status::before { background: var(--primary); }
.sk-flow-item.sk-t-report::before { background: var(--purple); }
.sk-flow-item.sk-t-inspection::before { background: var(--ok); }
.sk-flow-item.sk-t-alert::before { background: var(--danger); }
.sk-flow-item.sk-t-hitl::before { background: var(--warn); }
.sk-flow-item.sk-t-ecn::before { background: var(--primary-deep); }
.sk-flow-item.sk-t-complaint::before { background: var(--danger); box-shadow: 0 0 0 3px var(--danger-bg); }
.sk-flow-ts { font-size: 11.5px; color: var(--text-3); }
.sk-flow-type {
  display: inline-block; margin: 3px 0 4px; padding: 0 6px;
  font-size: 10.5px; line-height: 17px; border-radius: 4px;
  color: var(--text-2); background: #EEF1F6;
}
.sk-flow-text { font-size: 12.4px; color: var(--text); line-height: 1.6; }
.sk-flow-scene { font-size: 11px; color: var(--text-3); margin-top: 3px; }

/* 会签卡 */
.sk-signoff-body { padding: 6px 18px 12px; }
.sk-slot {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 0; border-bottom: 1px dashed var(--border);
}
.sk-slot:last-child { border-bottom: none; }
.sk-slot-role { font-size: 12.8px; font-weight: 600; color: var(--text); min-width: 66px; }
.sk-slot-user { font-size: 12.2px; color: var(--text-2); min-width: 60px; }
.sk-slot-ts { font-size: 11.5px; color: var(--text-3); }
.sk-slot-act { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.sk-readonly {
  font-size: 11.5px; color: var(--text-2);
  background: #EEF1F6; border: 1px solid var(--border);
  padding: 2px 9px; border-radius: 5px;
}
.sk-signoff-note {
  padding: 10px 18px 14px; font-size: 11.5px; color: var(--text-3);
  border-top: 1px solid var(--border); line-height: 1.6;
}

/* 降级提示（装载层未就绪时唯一的合法呈现：说明原因，不编数字） */
.sk-degrade {
  display: flex; align-items: flex-start; gap: 9px;
  padding: 12px 14px; border-radius: var(--radius-sm);
  background: var(--warn-bg); border: 1px dashed var(--warn);
  color: var(--warn); font-size: 12.4px; line-height: 1.65;
}
.sk-degrade svg { flex: none; margin-top: 2px; }
.sk-degrade b { color: var(--warn); }
.sk-degrade-sub { margin-top: 4px; font-size: 11.5px; color: var(--text-2); }

/* 下钻弹层 */
.sk-overlay {
  position: fixed; top: 0; right: 0; bottom: 0; left: 0;
  display: flex; align-items: center; justify-content: center;
  padding: 24px; background: rgba(31, 39, 51, .42); z-index: 3000;
}
.sk-drill {
  width: min(720px, 100%); max-height: 82vh;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow-pop);
}
.sk-drill-head {
  display: flex; align-items: center; gap: 9px;
  padding: 13px 18px; border-bottom: 1px solid var(--border);
}
.sk-drill-title { font-size: 14.5px; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 8px; }
.sk-drill-title svg { color: var(--primary); }
.sk-drill-close {
  margin-left: auto; display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: var(--radius-sm);
  color: var(--text-2); background: transparent;
}
.sk-drill-close:hover { background: var(--primary-light-2); color: var(--primary); }
.sk-drill-sub { padding: 10px 18px 0; font-size: 12px; color: var(--text-3); line-height: 1.6; }
.sk-drill-body { padding: 12px 18px 18px; overflow-y: auto; font-size: 12.6px; color: var(--text-2); line-height: 1.75; }
.sk-kv { display: flex; flex-direction: column; gap: 8px; }
.sk-kv-row { display: flex; gap: 10px; align-items: flex-start; }
.sk-kv-k { flex: none; width: 76px; color: var(--text-3); font-size: 12px; }
.sk-kv-v { flex: 1; color: var(--text); font-size: 12.6px; word-break: break-word; }

@media (max-width: 760px) {
  .sk-grid-2, .sk-grid-3, .sk-grid-4 { grid-template-columns: minmax(0, 1fr); }
  .sk-flow-item { min-width: 150px; }
}
`;

  /** 注入样式表（只注入一次；重复调用直接返回）。 */
  function injectStyle() {
    if (typeof document === "undefined") return;
    if (document.getElementById(STYLE_ID)) return;
    const head = document.head
      || (document.getElementsByTagName ? document.getElementsByTagName("head")[0] : null);
    if (!head) return;
    const node = document.createElement("style");
    node.id = STYLE_ID;
    node.textContent = CSS;
    head.appendChild(node);
  }

  /* ============================================================
   * 二、上下文绑定与转发
   * ============================================================ */

  /**
   * 绑定当前渲染上下文。`ctx` 缺失/不合法时不崩（只清空缓存），
   * 之后各组件走兜底实现，保证「屏幕未注册」这类边界不会变成白屏。
   *
   * @param {Object} ctx app.js 传下来的渲染上下文。
   * @returns {Object} SceneKit（便于链式调用）。
   */
  function use(ctx) {
    current = (ctx && typeof ctx === "object") ? ctx : null;
    injectStyle();
    return SceneKit;
  }

  function ui() {
    return (current && current.UI && typeof current.UI === "object") ? current.UI : null;
  }

  /** HTML 转义。优先转发 app.js 的 esc（口径统一），缺上下文时用等价兜底。 */
  function esc(value) {
    const kit = ui();
    if (kit && typeof kit.esc === "function") return kit.esc(value);
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** 内联 SVG 图标。优先转发 app.js 的 icon；无上下文时渲染一个中性信息图标。 */
  function icon(name, size, cls) {
    const kit = ui();
    const s = typeof size === "number" ? size : 16;
    const c = cls || "";
    if (kit && typeof kit.icon === "function") return kit.icon(name, s, c);
    return '<svg class="' + esc(c) + '" viewBox="0 0 24 24" width="' + s + '" height="' + s
      + '" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"'
      + ' stroke-linejoin="round" aria-hidden="true">'
      + '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>';
  }

  /** 徽标。优先转发 app.js 的 badge。 */
  function badge(text, cls, dot) {
    const kit = ui();
    if (kit && typeof kit.badge === "function") {
      return kit.badge(text, cls || "badge-gray", dot === true);
    }
    return '<span class="badge ' + esc(cls || "badge-gray") + '">'
      + (dot === true ? '<span class="badge-dot"></span>' : "") + esc(text) + "</span>";
  }

  /** 加载指示器。优先转发 app.js 的 spinner（.spinner 样式在 app.css 里）。 */
  function spinner(cls) {
    const kit = ui();
    if (kit && typeof kit.spinner === "function") return kit.spinner(cls || "");
    return '<span class="spinner ' + esc(cls || "") + '"></span>';
  }

  /**
   * 「演示数据」徽标——**全站唯一出口**。
   * 优先复用 `ctx.UI.demoFlag()`（视觉与既有 `.demo-flag` 完全一致）；
   * 无上下文时仍产出 `.demo-flag` 结构，保证不被样式漏掉。
   */
  function demoBadge(text) {
    const label = (typeof text === "string" && text.trim()) ? text : "演示数据";
    const kit = ui();
    if (kit && typeof kit.demoFlag === "function") return kit.demoFlag(label);
    return '<span class="demo-flag">' + icon("info", 11) + esc(label) + "</span>";
  }

  /* ============================================================
   * 三、页面骨架组件
   * ============================================================ */

  /**
   * 场景页头。**「演示数据」徽标强制内联在标题行**——
   * 任何场景屏只要用这个页头，天然带演示标识，不依赖各屏自觉。
   *
   * @param {Object} opts `{ icon, title, desc, extra }`
   * @returns {string} HTML
   */
  function pageHead(opts) {
    const o = opts || {};
    const headIcon = o.icon ? icon(o.icon, 20) : "";
    const desc = o.desc ? '<div class="page-desc">' + esc(o.desc) + "</div>" : "";
    const extra = o.extra ? '<div class="sk-head-extra">' + o.extra + "</div>" : "";
    return '<div class="page-head">'
      + '<div class="page-title">' + headIcon + esc(o.title || "场景屏") + demoBadge() + "</div>"
      + desc + extra
      + "</div>";
  }

  /**
   * 数字卡。
   *
   * @param {Object} card `{ label, value, unit, hint, tone, drill }`
   *   - `drill` 为真时渲染为可点击卡（`data-sk-drill` + `role="button"` + `tabindex="0"`），
   *     点击/keydown 由场景屏用 `bindDrills()` 或自行绑定，调 `openDrill()` 展示口径。
   * @returns {string} HTML
   */
  function numberCard(card) {
    const c = card || {};
    const tone = c.tone ? String(c.tone) : "gray";
    const drill = c.drill;
    const clickable = drill !== undefined && drill !== null && drill !== false && drill !== "";
    const cls = "sk-num-card sk-tone-" + esc(tone) + (clickable ? " is-clickable" : "");
    const attrs = clickable
      ? ' data-sk-drill="' + esc(String(drill)) + '" role="button" tabindex="0"'
      + ' aria-label="' + esc(String(c.label || "数字卡")) + '，点击查看口径"'
      : ' aria-label="' + esc(String(c.label || "数字卡")) + '"';
    const unit = c.unit ? '<span class="sk-num-unit">' + esc(c.unit) + "</span>" : "";
    const hint = c.hint ? '<div class="sk-num-hint">' + esc(c.hint) + "</div>" : "";
    const tip = clickable ? '<span class="sk-num-tip">口径</span>' : "";
    return '<div class="' + cls + '"' + attrs + ">"
      + tip
      + '<div class="sk-num-label">' + esc(c.label || "") + "</div>"
      + '<div class="sk-num-main"><span class="sk-num-value">'
      + esc(c.value === undefined || c.value === null ? "—" : c.value) + "</span>" + unit + "</div>"
      + hint
      + "</div>";
  }

  /** 一行 2–4 张数字卡。 */
  function numberRow(cards) {
    const list = Array.isArray(cards) ? cards : [];
    if (!list.length) return "";
    let cls = "sk-grid";
    if (list.length === 2) cls += " sk-grid-2";
    else if (list.length === 3) cls += " sk-grid-3";
    else if (list.length === 4) cls += " sk-grid-4";
    return '<div class="' + cls + '">' + list.map(function (card) {
      return numberCard(card);
    }).join("") + "</div>";
  }

  /* ============================================================
   * 四、业务组件
   * ============================================================ */

  function safeToken(value) {
    const raw = String(value === null || value === undefined ? "" : value).toLowerCase();
    const cleaned = raw.replace(/[^a-z0-9_-]/g, "");
    return cleaned || "unknown";
  }

  function eventTypeLabel(type) {
    const key = safeToken(type);
    return EVENT_TYPE_LABELS[key] || (key === "unknown" ? "事件" : key);
  }

  /**
   * 事件流条（横向时间轴）。
   *
   * @param {Array} events `[{ ts, type, text, scene }]`
   * @param {Object} [opts] `{ title, extra, note }`
   * @returns {string} HTML
   */
  function eventFlow(events, opts) {
    const list = Array.isArray(events) ? events : [];
    const o = opts || {};
    if (!list.length) return "";
    const items = list.map(function (ev) {
      const e = ev || {};
      const token = safeToken(e.type);
      return '<div class="sk-flow-item sk-t-' + esc(token) + '">'
        + '<div class="sk-flow-ts">' + esc(fmtDate(e.ts)) + "</div>"
        + '<div class="sk-flow-type">' + esc(eventTypeLabel(e.type)) + "</div>"
        + '<div class="sk-flow-text">' + esc(e.text || "") + "</div>"
        + (e.scene ? '<div class="sk-flow-scene">' + esc(e.scene) + "</div>" : "")
        + "</div>";
    }).join("");
    const title = o.title
      ? '<div class="section-title">' + esc(o.title)
      + (o.extra ? '<span class="section-extra">' + esc(o.extra) + "</span>" : "")
      + "</div>"
      : "";
    const note = o.note ? '<div class="sk-note">' + esc(o.note) + "</div>" : "";
    return '<div class="sk-flow-wrap">' + title
      + '<div class="sk-flow">' + items + "</div>" + note + "</div>";
  }

  /**
   * 会签卡。
   *
   * @param {Object} opts `{ title, slots, note, readonly }`
   *   - `slots`: `[{ role, user, status: pending|approved|rejected, ts }]`
   *   - `readonly` 省略时按当前岗位判定（`isReadonly()`）：只读访客的操作位
   *     显示「只读演示」，不出现可点按钮。
   * @returns {string} HTML
   */
  function signoffCard(opts) {
    const o = opts || {};
    const slots = Array.isArray(o.slots) ? o.slots : [];
    const readonly = (o.readonly === undefined) ? isReadonly(current) : o.readonly === true;
    const approved = slots.filter(function (s) {
      return s && String(s.status || "").toLowerCase() === "approved";
    }).length;
    const rows = slots.map(function (slot) {
      const s = slot || {};
      const key = safeToken(s.status || "pending");
      const meta = SIGNOFF_STATUS[key] || [s.status ? String(s.status) : "未设置", "badge-gray"];
      const act = readonly
        ? '<span class="sk-readonly">只读演示</span>'
        : '<button class="btn btn-sm btn-outline" type="button" data-sk-signoff="'
        + esc(String(s.role || "")) + '">会签</button>';
      return '<div class="sk-slot">'
        + '<span class="sk-slot-role">' + esc(s.role || "未指定槽位") + "</span>"
        + '<span class="sk-slot-user">' + esc(s.user || "—") + "</span>"
        + badge(meta[0], meta[1])
        + '<span class="sk-slot-ts">' + esc(fmtDate(s.ts)) + "</span>"
        + '<span class="sk-slot-act">' + act + "</span>"
        + "</div>";
    }).join("");
    const progress = slots.length ? approved + " / " + slots.length + " 已通过" : "未配置槽位";
    const note = o.note ? '<div class="sk-signoff-note">' + esc(o.note) + "</div>" : "";
    return '<div class="card">'
      + '<div class="card-head">'
      + '<div class="card-title">' + icon("approve", 15) + esc(o.title || "会签卡") + "</div>"
      + '<div class="card-extra">' + esc(progress) + "</div>"
      + "</div>"
      + '<div class="sk-signoff-body">' + (rows || '<div class="empty">未配置会签槽位</div>') + "</div>"
      + note
      + "</div>";
  }

  /* ============================================================
   * 五、下钻弹层
   * ============================================================ */

  /** 关闭当前下钻弹层并移除事件监听（幂等）。 */
  function closeDrill() {
    if (!activeDrill) return;
    const { overlay, onKeyDown } = activeDrill;
    activeDrill = null;
    if (typeof document !== "undefined" && typeof onKeyDown === "function") {
      document.removeEventListener("keydown", onKeyDown, true);
    }
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }

  /**
   * 打开下钻弹层。Esc / 点遮罩 / 关闭按钮均可关闭；关闭时移除 DOM 与监听，
   * 不留全局状态（同一时刻只保留一个弹层）。
   *
   * @param {Object} opts `{ title, subtitle, html }`
   * @returns {Function|null} 关闭函数
   */
  function openDrill(opts) {
    const o = opts || {};
    if (typeof document === "undefined" || !document.body) return null;
    closeDrill();

    const overlay = document.createElement("div");
    overlay.className = "sk-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML = '<div class="sk-drill">'
      + '<div class="sk-drill-head">'
      + '<div class="sk-drill-title">' + icon("info", 16) + esc(o.title || "口径说明") + "</div>"
      + '<button class="sk-drill-close" type="button" aria-label="关闭">' + icon("x", 15) + "</button>"
      + "</div>"
      + (o.subtitle ? '<div class="sk-drill-sub">' + esc(o.subtitle) + "</div>" : "")
      + '<div class="sk-drill-body">' + (o.html || '<div class="empty">暂无口径说明</div>') + "</div>"
      + "</div>";

    const onKeyDown = function (ev) {
      if (!ev) return;
      if (ev.key === "Escape" || ev.keyCode === 27) {
        if (typeof ev.preventDefault === "function") ev.preventDefault();
        closeDrill();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);

    const closeBtn = overlay.querySelector(".sk-drill-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function (ev) {
        if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
        closeDrill();
      });
    }
    // 点遮罩关闭：只在 target 就是遮罩本身时触发，避免点内容区误关。
    overlay.addEventListener("click", function (ev) {
      if (ev && ev.target === overlay) closeDrill();
    });

    document.body.appendChild(overlay);
    activeDrill = { overlay, onKeyDown };
    return closeDrill;
  }

  /* ============================================================
   * 六、数据与格式化
   * ============================================================ */

  /**
   * 取场景自带数据（`window.SCENE_DATA[sceneId]`，由各场景的数据文件产出）。
   * 缺失时**抛中文可读错误**——场景数据是自家文件，没有就是漏了，必须立刻发现。
   */
  function sceneData(sceneId) {
    const id = String(sceneId === null || sceneId === undefined ? "" : sceneId);
    const bag = window.SCENE_DATA;
    if (!bag || typeof bag !== "object" || !(id in bag)) {
      throw new Error("场景数据未就绪：window.SCENE_DATA[" + id
        + "] 缺失（该场景的数据文件未加载，或未按 registry.json 约定挂到 window.SCENE_DATA）");
    }
    return bag[id];
  }

  function localStoryRows(name) {
    const bag = window.DEMO_STORY;
    if (!bag || typeof bag !== "object") return null;
    const rows = bag[name];
    return Array.isArray(rows) ? rows.slice() : null;
  }

  function pickRows(payload) {
    if (!payload) return null;
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload.data)) return payload.data;
    if (payload.data && typeof payload.data === "object") {
      if (Array.isArray(payload.data.rows)) return payload.data.rows;
      if (Array.isArray(payload.data.items)) return payload.data.items;
      if (Array.isArray(payload.data.records)) return payload.data.records;
    }
    if (Array.isArray(payload.rows)) return payload.rows;
    if (Array.isArray(payload.items)) return payload.items;
    if (Array.isArray(payload.records)) return payload.records;
    return null;
  }

  /** 带超时的 JSON GET。任何失败都 reject（由调用方统一降级，不在此吞错）。 */
  function fetchJson(url, timeoutMs) {
    return new Promise(function (resolve, reject) {
      if (typeof fetch !== "function") {
        reject(new Error("当前浏览器不支持 fetch"));
        return;
      }
      let timer = null;
      const opts = { headers: { Accept: "application/json" } };
      try {
        if (typeof AbortController === "function") {
          const ctrl = new AbortController();
          opts.signal = ctrl.signal;
          timer = setTimeout(function () { ctrl.abort(); }, timeoutMs);
        }
      } catch (err) {
        // 老浏览器没有 AbortController：退化为无超时（不阻断主流程）
        timer = null;
      }
      const done = function (fn, arg) {
        if (timer) clearTimeout(timer);
        timer = null;
        fn(arg);
      };
      fetch(url, opts).then(function (resp) {
        if (!resp || !resp.ok) {
          throw new Error("HTTP " + (resp ? resp.status : 0));
        }
        return resp.json();
      }).then(function (json) {
        done(resolve, json);
      }).catch(function (err) {
        done(reject, err);
      });
    });
  }

  function storyApi() {
    if (current && current.api) return current.api;
    const app = window.App;
    if (app && app.api) return app.api;
    return null;
  }

  /**
   * 公共故事数据的**统一入口**。
   *
   * 取数顺序：`GET /api/v2/demo/objects/<objectName>`（T04 装载层 loader.py）
   * → `window.DEMO_STORY[objectName]`（离线回放）
   * → `{ ok:false, rows:[], error:"演示装载层未就绪" }`。
   *
   * 端点口径以 T04 的**真实路由**为准（`src/.../demo/api.py` 的
   * `@app.get("/api/v2/demo/objects/{name}")`，响应 `{is_demo,label,data_dir,object,rows}`，
   * **无 code 信封**）。早期按任务书文字写成 `/api/v2/demo/storyline/{name}`，
   * 实测 404 → 占位屏永远走降级分支。`tests/demo/test_t05_contract.py`
   * 的 `test_scene_kit_endpoint_matches_backend_route` 现在把这条钉死。
   *
   * **任何情况都不 reject、不抛未捕获异常、不把 err.message 透给用户**：
   * 装载层没交付时页面必须说「未就绪」，而不是把 "Failed to fetch" 甩到脸上。
   *
   * @param {string} objectName 对象名（如 `sales_orders`，取值见 registry.json 的 objects 键）。
   * @returns {Promise<{ok: boolean, rows: Array, source?: string, degraded?: boolean,
   *   error?: string, note?: string}>}
   */
  function story(objectName) {
    const name = String(objectName === null || objectName === undefined ? "" : objectName).trim();
    if (!name) {
      return Promise.resolve({ ok: false, rows: [], error: "未指定故事对象名" });
    }

    const degrade = function (note) {
      const rows = localStoryRows(name);
      if (rows) {
        return { ok: true, rows: rows, source: "local", degraded: true, note: note || "" };
      }
      return { ok: false, rows: [], error: "演示装载层未就绪", note: note || "" };
    };

    const api = storyApi();
    if (!api || typeof api.base !== "function") {
      return Promise.resolve(degrade("渲染上下文未提供后端基址"));
    }

    let online = true;
    try {
      if (typeof api.online === "function") online = api.online() !== false;
    } catch (err) {
      online = true;
    }
    if (!online) {
      return Promise.resolve(degrade("后端离线：探针未通过"));
    }

    let base = "";
    try {
      base = api.base() || "";
    } catch (err) {
      base = "";
    }
    if (!base) {
      return Promise.resolve(degrade("后端基址未知"));
    }

    // T04 真实路由：GET /api/v2/demo/objects/{name} → { is_demo, label, data_dir, object, rows }
    const url = base + "/api/v2/demo/objects/" + encodeURIComponent(name);
    return fetchJson(url, 6000).then(function (payload) {
      const rows = pickRows(payload);
      if (rows) return { ok: true, rows: rows, source: "api" };
      return degrade("接口返回结构不符合 rows 约定");
    }).catch(function () {
      return degrade("接口不可用（T04 装载层端点未就绪或无权限）");
    }).catch(function () {
      return { ok: false, rows: [], error: "演示装载层未就绪" };
    });
  }

  /** 当前岗位是否只读访客（`readonly: true`）。ctx 省略时用最近一次 `use()` 的上下文。 */
  function isReadonly(ctx) {
    const c = contractCtx(ctx);
    if (!c) return false;
    let role = null;
    try {
      if (typeof c.currentRole === "function") {
        role = c.currentRole();
      } else if (c.state && c.DEMO && Array.isArray(c.DEMO.ROLES)) {
        role = c.DEMO.ROLES.find(function (item) {
          return item && item.id === c.state.role;
        }) || null;
      }
    } catch (err) {
      role = null;
    }
    return !!(role && role.readonly === true);
  }

  /** 把 undefined 上下文解析成「当前上下文」。 */
  function contractCtx(ctx) {
    if (ctx === undefined || ctx === null) return current;
    return typeof ctx === "object" ? ctx : null;
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  /**
   * 轻量日期/时间格式化：`YYYY-MM-DD HH:MM`（spec-v1.md §6：时间轴统一到分钟）。
   * 空值 / 非法值一律返回 `—`，绝不让 `Invalid Date` 出现在界面上。
   */
  function fmtDate(value) {
    if (value === null || value === undefined || value === "") return "—";
    const raw = String(value);
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return raw;
    const hasTime = raw.indexOf("T") !== -1 || raw.indexOf(":") !== -1;
    const datePart = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    if (!hasTime) return datePart;
    return datePart + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  /**
   * 数字格式化：数量千分位整数，金额/比率按 `digits` 保留小数。
   * 空值 / 非数字返回 `—`（与全站一致：宁可显示破折号，不显示 NaN）。
   *
   * @param {number|string} value 数值
   * @param {number} [digits] 小数位；省略 = 整数（四舍五入）
   */
  function fmtNum(value, digits) {
    if (value === null || value === undefined || value === "") return "—";
    const n = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(n)) return "—";
    const d = (typeof digits === "number" && digits >= 0) ? Math.min(digits, 6) : 0;
    return n.toFixed(d).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  /* ============================================================
   * 七、可选辅助：把数字卡的下钻交互一次绑好
   * ============================================================ */

  /**
   * 给容器内所有 `[data-sk-drill]` 数字卡绑定点击 / 回车 / 空格，
   * 触发 `handler(key, cardElement)`。场景屏不必各自写一遍键盘可达性。
   *
   * @param {Element} root 容器元素
   * @param {Function} handler `(key, element) => void`
   * @returns {number} 绑定的卡片数
   */
  function bindDrills(root, handler) {
    if (!root || typeof root.querySelectorAll !== "function") return 0;
    if (typeof handler !== "function") return 0;
    const cards = root.querySelectorAll("[data-sk-drill]");
    let bound = 0;
    for (let i = 0; i < cards.length; i += 1) {
      const card = cards[i];
      if (card.dataset.skBound === "1") continue;
      card.dataset.skBound = "1";
      const fire = function (ev) {
        if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
        handler(card.dataset.skDrill, card);
      };
      card.addEventListener("click", fire);
      card.addEventListener("keydown", function (ev) {
        if (!ev) return;
        if (ev.key === "Enter" || ev.key === " " || ev.keyCode === 13 || ev.keyCode === 32) fire(ev);
      });
      bound += 1;
    }
    return bound;
  }

  /* ============================================================
   * 八、导出
   * ============================================================ */

  const SceneKit = {
    use,
    esc,
    icon,
    badge,
    spinner,
    demoBadge,
    pageHead,
    numberCard,
    numberRow,
    eventFlow,
    signoffCard,
    openDrill,
    closeDrill,
    sceneData,
    story,
    isReadonly,
    fmtDate,
    fmtNum,
    bindDrills,
  };

  window.SceneKit = SceneKit;

  // 首次加载即注入样式：即使某屏只调 fmtNum（不调 use），也不该等到首帧才闪一下样式。
  injectStyle();

  // 会签按钮的骨架兜底：场景屏未接管时，给一句诚实的提示，而不是让按钮点了没反应。
  if (typeof document !== "undefined") {
    document.addEventListener("click", function (ev) {
      const target = ev && ev.target;
      if (!target || typeof target.closest !== "function") return;
      const btn = target.closest("[data-sk-signoff]");
      if (!btn) return;
      const kit = ui();
      if (kit && typeof kit.toast === "function") {
        kit.toast("会签写操作由场景屏接入（本卡为骨架示意，非业务数据）", "warn", 3200);
      }
    }, false);
  }
})();
