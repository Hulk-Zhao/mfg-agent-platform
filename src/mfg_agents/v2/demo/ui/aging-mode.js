/* ============================================================
 * 适龄化显示模式（自包含 · 增量脚本 · 不侵入 app.js）
 *  - 三档预设：标准 / 清晰 / 大字（一次点击原子写 data-font + data-density + data-theme）
 *    **出厂默认 = 清晰档**（字号 +12% · 明亮主题），首次打开 / 登录即为此档
 *  - 高级设置：字号 · 间距 · 明暗 三档单选
 *  - 持久化：localStorage（mfg-font-scale / mfg-density / mfg-display-preset）
 *           「仅本次生效」时改写 sessionStorage，会话结束即还原
 *  - 兼容既有 theme-switcher.js：主题仍写 mfg-theme，不改写其语义；
 *    切换后广播 mfg:displaychange，并用 MutationObserver 反向监听 <html data-theme>
 *  - 快捷键：Alt+A 打开面板 · Ctrl+Z 撤销上一次档位 · Alt+0 恢复默认 · Esc 关闭
 * ============================================================ */
(function () {
  "use strict";

  var FONT_KEY = "mfg-font-scale";
  var DENS_KEY = "mfg-density";
  var PRESET_KEY = "mfg-display-preset";
  var THEME_KEY = "mfg-theme";            /* 既有键，语义保持不变 */
  var ONBOARD_KEY = "mfg-display-onboarded";
  var SESSION_KEY = "mfg-display-session-only";

  var FONTS = ["standard", "medium", "large"];
  var DENSITIES = ["compact", "standard", "loose"];
  var PREVIEW = "SMT-1 线 OEE 78.3% · 待处理 3 条";

  /* theme: null 表示「沿用当前主题」，不覆盖用户已选的明暗 */
  var PRESETS = [
    { id: "standard", name: "标准", meta: "默认字号 · 默认间距", font: "standard", density: "standard", theme: null },
    { id: "clear", name: "清晰", meta: "字号 +20% · 亮色主题", font: "medium", density: "standard", theme: "light" },
    { id: "large", name: "大字", meta: "字号 +35% · 宽松间距 · 亮色主题", font: "large", density: "loose", theme: "light" }
  ];

  var DEFAULTS = { font: "medium", density: "standard" };
  /* 出厂默认主题：明亮。首次打开 / 登录（localStorage 无偏好）即为此值，
     与 theme-switcher.getTheme() 和 index.html 内联首绘脚本保持同一口径。 */
  var DEFAULT_THEME = "light";
  var state = { font: DEFAULTS.font, density: DEFAULTS.density, theme: DEFAULT_THEME };
  var sessionOnly = false;
  var lastSnapshot = null;   /* Ctrl+Z 撤销目标 */
  var undo = { node: null, timer: 0 };

  var root = document.documentElement;

  /* ---------- 存储（sessionOnly 时走 sessionStorage，退出即还原） ---------- */
  function readPref(key) {
    try {
      var v = sessionStorage.getItem(key);
      if (v === null || v === undefined) v = localStorage.getItem(key);
      return v;
    } catch (err) { return null; }
  }
  function writePref(key, val) {
    try {
      if (sessionOnly) { sessionStorage.setItem(key, val); localStorage.removeItem(key); }
      else { localStorage.setItem(key, val); sessionStorage.removeItem(key); }
    } catch (err) { /* 隐私模式下忽略 */ }
  }

  /* ---------- 首绘前同步生效：本段在脚本执行期运行，不等待 DOMContentLoaded ---------- */
  function readState() {
    var f = readPref(FONT_KEY);
    state.font = FONTS.indexOf(f) >= 0 ? f : DEFAULTS.font;
    var d = readPref(DENS_KEY);
    state.density = DENSITIES.indexOf(d) >= 0 ? d : DEFAULTS.density;
    /* 主题：优先 mfg-theme（既有键）；缺失时回落到 theme-switcher 已写到 <html> 的值。
       默认明亮（light）——与 theme-switcher.getTheme() / index.html 内联首绘脚本同口径。 */
    var t = readPref(THEME_KEY);
    if (t !== "light" && t !== "dark") t = root.getAttribute("data-theme");
    state.theme = t === "dark" ? "dark" : "light";
    try { sessionOnly = sessionStorage.getItem(SESSION_KEY) === "1"; } catch (err) { sessionOnly = false; }
  }

  function apply() {
    root.setAttribute("data-font", state.font);
    root.setAttribute("data-density", state.density);
    root.setAttribute("data-theme", state.theme);
  }

  readState();
  apply();

  /* ---------- 工具 ---------- */
  function presetOf(font, density, theme) {
    for (var i = 0; i < PRESETS.length; i++) {
      var p = PRESETS[i];
      if (p.font === font && p.density === density && (p.theme === null || p.theme === theme)) return p.id;
    }
    return "";
  }
  function snapshot() {
    return { font: state.font, density: state.density, theme: state.theme };
  }
  function commit(next, presetId) {
    lastSnapshot = snapshot();
    state.font = next.font; state.density = next.density; state.theme = next.theme;
    writePref(FONT_KEY, state.font);
    writePref(DENS_KEY, state.density);
    writePref(THEME_KEY, state.theme);
    if (presetId) writePref(PRESET_KEY, presetId);
    else try { sessionStorage.removeItem(PRESET_KEY); localStorage.removeItem(PRESET_KEY); } catch (err) { /* ignore */ }
    apply();
    try { window.dispatchEvent(new Event("mfg:displaychange")); } catch (err) { /* ignore */ }
    syncUI();
    showUndoToast(presetOf(state.font, state.density, state.theme));
  }

  /** 恢复出厂默认：清晰字号 + 标准间距 + 明亮主题（= 「清晰」档）。 */
  function resetToDefault() {
    commit({ font: DEFAULTS.font, density: DEFAULTS.density, theme: DEFAULT_THEME }, "clear");
  }

  /* ---------- 撤销提示条（自渲染：既有 toast() 不支持按钮且 .toast-root 是 pointer-events:none） ---------- */
  function ensureToast() {
    if (undo.node && undo.node.isConnected) return undo.node;
    var node = document.createElement("div");
    node.className = "am-undo-toast";
    node.setAttribute("role", "status");
    node.hidden = true;
    var text = document.createElement("span");
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "am-undo-btn";
    btn.textContent = "撤销";
    btn.addEventListener("click", function () { doUndo(); });
    node.appendChild(text);
    node.appendChild(btn);
    document.body.appendChild(node);
    undo.node = node;
    return node;
  }
  function presetName(id) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[i].name;
    return "";
  }
  function showUndoToast(presetId) {
    var node = ensureToast();
    node.firstChild.textContent = "已切换为「" + (presetName(presetId) || "自定义") + "」显示";
    node.hidden = false;
    if (undo.timer) clearTimeout(undo.timer);
    /* 撤销窗口：标准档 8s，大字档 12s。
       5s 在「大字档 + 老视」下不足以完成「看见提示 → 移动鼠标 → 点中按钮」这一串动作。 */
    var isLarge = document.documentElement.getAttribute("data-font") === "large";
    undo.timer = setTimeout(function () { node.hidden = true; }, isLarge ? 12000 : 8000);
  }
  function hideUndoToast() {
    if (undo.timer) clearTimeout(undo.timer);
    if (undo.node) undo.node.hidden = true;
  }
  function doUndo() {
    if (!lastSnapshot) { hideUndoToast(); return; }
    var prev = lastSnapshot;
    lastSnapshot = null;
    state.font = prev.font; state.density = prev.density; state.theme = prev.theme;
    writePref(FONT_KEY, state.font);
    writePref(DENS_KEY, state.density);
    writePref(THEME_KEY, state.theme);
    apply();
    try { window.dispatchEvent(new Event("mfg:displaychange")); } catch (err) { /* ignore */ }
    syncUI();
    hideUndoToast();
  }

  /* ---------- 面板 ---------- */
  var panel = null;
  var trigger = null;

  function buildPanel() {
    var el = document.createElement("div");
    el.className = "am-panel";
    el.id = "am-display-panel";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "显示设置");
    el.hidden = true;

    var presetHtml = PRESETS.map(function (p) {
      return '<button type="button" class="am-preset" data-preset="' + p.id + '">'
        + '<span class="am-preset-row"><span class="am-preset-name">' + p.name + '</span>'
        + '<span class="am-preset-meta">' + p.meta + '</span>'
        + '<span class="am-preset-flag" data-flag hidden>✓ 当前使用</span></span>'
        + '<span class="am-preset-preview">' + PREVIEW + '</span>'
        + '</button>';
    }).join("");

    function seg(name, opts, labels) {
      return '<div class="am-field" data-field="' + name + '">'
        + '<div class="am-field-label">' + labels.title + '</div>'
        + '<div class="am-seg">' + opts.map(function (o, i) {
          return '<button type="button" data-value="' + o + '" aria-pressed="false">' + labels.items[i] + '</button>';
        }).join("") + '</div></div>';
    }

    el.innerHTML =
      '<div class="am-panel-head"><span class="am-panel-title">显示设置</span>'
      + '<button type="button" class="am-close" id="am-close" aria-label="关闭">✕</button></div>'
      + presetHtml
      + '<details class="am-adv"><summary>高级设置</summary>'
      + seg("font", FONTS, { title: "字号", items: ["标准", "偏大", "特大"] })
      + seg("density", DENSITIES, { title: "间距", items: ["紧凑", "标准", "宽松"] })
      + seg("theme", ["dark", "light"], { title: "明暗", items: ["深色", "亮色"] })
      + '</details>'
      + '<div class="am-panel-foot">'
      + '<label class="am-check"><input type="checkbox" id="am-session"> 仅本次生效</label>'
      + '<button type="button" class="am-reset" id="am-reset">恢复默认显示</button>'
      + '</div>';

    el.addEventListener("click", function (ev) {
      var t = ev.target;
      var presetBtn = t.closest ? t.closest(".am-preset") : null;
      if (presetBtn) {
        var p = null;
        PRESETS.forEach(function (item) { if (item.id === presetBtn.getAttribute("data-preset")) p = item; });
        if (p) commit({ font: p.font, density: p.density, theme: p.theme || state.theme }, p.id);
        return;
      }
      var segBtn = t.closest ? t.closest(".am-seg button") : null;
      if (segBtn) {
        var field = segBtn.parentNode.parentNode.getAttribute("data-field");
        var value = segBtn.getAttribute("data-value");
        if (field === "font") commit({ font: value, density: state.density, theme: state.theme }, null);
        else if (field === "density") commit({ font: state.font, density: value, theme: state.theme }, null);
        else commit({ font: state.font, density: state.density, theme: value }, null);
        return;
      }
      if (t.id === "am-close") { closePanel(); return; }
      if (t.id === "am-reset") { resetToDefault(); return; }
    });

    el.querySelector("#am-session").addEventListener("change", function (ev) {
      sessionOnly = !!ev.target.checked;
      try {
        if (sessionOnly) sessionStorage.setItem(SESSION_KEY, "1");
        else sessionStorage.removeItem(SESSION_KEY);
      } catch (err) { /* ignore */ }
      /* 立刻按新作用域重写一遍，保证「仅本次生效」勾选后当前偏好不落到 localStorage */
      writePref(FONT_KEY, state.font);
      writePref(DENS_KEY, state.density);
      writePref(THEME_KEY, state.theme);
    });

    document.body.appendChild(el);
    panel = el;
    return el;
  }

  function placePanel() {
    if (!panel || !trigger) return;
    var r = trigger.getBoundingClientRect();
    var w = panel.offsetWidth || 320;
    var left = r.right - w;
    if (left < 12) left = 12;
    if (left + w > window.innerWidth - 12) left = Math.max(12, window.innerWidth - 12 - w);
    panel.style.left = left + "px";
    panel.style.top = (r.bottom + 8) + "px";
  }

  function openPanel() {
    if (!panel) buildPanel();
    panel.hidden = false;
    placePanel();
    syncUI();
    if (trigger) trigger.setAttribute("aria-expanded", "true");
  }
  function closePanel() {
    if (!panel) return;
    panel.hidden = true;
    if (trigger) trigger.setAttribute("aria-expanded", "false");
  }
  function panelOpen() { return !!panel && !panel.hidden; }

  function syncUI() {
    if (!panel) return;
    var current = presetOf(state.font, state.density, state.theme);
    var flags = panel.querySelectorAll(".am-preset");
    for (var i = 0; i < flags.length; i++) {
      var on = flags[i].getAttribute("data-preset") === current;
      flags[i].classList.toggle("current", on);
      var flag = flags[i].querySelector(".am-preset-flag");
      if (flag) flag.hidden = !on;
    }
    var map = { font: state.font, density: state.density, theme: state.theme };
    Object.keys(map).forEach(function (field) {
      var box = panel.querySelector('.am-field[data-field="' + field + '"]');
      if (!box) return;
      var btns = box.querySelectorAll("button");
      for (var j = 0; j < btns.length; j++) {
        btns[j].setAttribute("aria-pressed", String(btns[j].getAttribute("data-value") === map[field]));
      }
    });
    var chk = panel.querySelector("#am-session");
    if (chk) chk.checked = sessionOnly;
  }

  /* ---------- 顶栏 / 登录页入口 ----------
     trigger 记录「最近一次点开的按钮」，面板定位与 aria 状态都跟随它：
     顶栏按钮（进入系统后）与登录页浮动 Aa（#login-display-controls 内）共用一套面板。 */
  function makeAaBtn(id) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = id;
    btn.className = "icon-btn am-btn";
    btn.textContent = "Aa";
    btn.title = "显示设置（字号 · 主题 · 间距）";
    btn.setAttribute("aria-label", btn.title);
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      trigger = btn;
      if (panelOpen()) closePanel(); else openPanel();
    });
    return btn;
  }

  function mountTrigger() {
    var right = document.querySelector(".topbar-right");
    if (!right || document.getElementById("am-display-btn")) return;
    var btn = makeAaBtn("am-display-btn");
    var refresh = document.getElementById("btn-refresh");
    if (refresh && refresh.parentNode === right) right.insertBefore(btn, refresh);
    else right.appendChild(btn);
    trigger = btn;
  }

  /** 登录页字号入口：追加进 theme-switcher.js 创建的 #login-display-controls 容器。 */
  function mountLoginTrigger() {
    var box = document.getElementById("login-display-controls");
    if (!box || document.getElementById("am-login-btn")) return;
    box.appendChild(makeAaBtn("am-login-btn"));
  }

  /* ---------- 侧栏分组语义：把组名写进 ≤960px 浮出标签 ---------- */
  function tagNavGroups() {
    var nav = document.getElementById("main-nav");
    if (!nav) return;
    var group = "";
    var kids = nav.children;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k.classList.contains("nav-group-label")) { group = k.textContent; continue; }
      if (!k.classList.contains("nav-item")) continue;
      var span = k.querySelector("span");
      if (span && group) span.setAttribute("data-group", group);
    }
  }

  /* ---------- ≤960px 图标轨：挂在 body 上的浮出标签（不被 .nav 的 overflow 裁切） ---------- */
  function railCollapsed() {
    var nav = document.getElementById("main-nav");
    return !!nav && nav.getBoundingClientRect().width <= 80;
  }
  function mountRailTip() {
    if (document.getElementById("am-rail-tip")) return;
    var tip = document.createElement("div");
    tip.className = "am-rail-tip";
    tip.id = "am-rail-tip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);

    function show(item) {
      var span = item.querySelector("span");
      var label = span ? span.textContent : (item.getAttribute("title") || "");
      var group = span ? span.getAttribute("data-group") : "";
      var labelEl = document.createElement("span");
      labelEl.className = "am-rail-tip-label";
      labelEl.textContent = label;
      tip.innerHTML = "";
      tip.appendChild(labelEl);
      if (group) {
        var g = document.createElement("span");
        g.className = "am-rail-tip-group";
        g.textContent = group;
        tip.appendChild(g);
      }
      tip.hidden = false;
      var r = item.getBoundingClientRect();
      var left = r.right + 10;
      if (left + tip.offsetWidth > window.innerWidth - 8) left = window.innerWidth - 8 - tip.offsetWidth;
      tip.style.left = left + "px";
      tip.style.top = Math.round(r.top + r.height / 2) + "px";
      tip.style.transform = "translateY(-50%)";
    }
    function hide() { tip.hidden = true; }

    var nav = document.getElementById("main-nav");
    if (!nav) return;
    nav.addEventListener("mouseover", function (ev) {
      var item = ev.target.closest ? ev.target.closest(".nav-item") : null;
      if (item && railCollapsed()) show(item); else hide();
    });
    nav.addEventListener("mouseleave", hide);
    nav.addEventListener("focusin", function (ev) {
      var item = ev.target.closest ? ev.target.closest(".nav-item") : null;
      if (item && railCollapsed()) show(item);
    });
    nav.addEventListener("focusout", hide);
    window.addEventListener("resize", hide);
  }

  /* ---------- 首次引导（登录页第 2 步）：症状化三选一，不问年龄、不收集个人信息 ---------- */
  function mountOnboard() {
    if (readPref(ONBOARD_KEY) === "1") return;
    var panelHost = document.querySelector(".login-panel");
    if (!panelHost || document.getElementById("am-onboard")) return;
    var foot = panelHost.querySelector(".login-panel-foot");

    var box = document.createElement("div");
    box.className = "am-onboard";
    box.id = "am-onboard";
    /* 三档与出厂默认（清晰 / medium）对齐：中间档才是「现在的字」，避免选「刚好」反而变小 */
    var opts = [
      { font: "standard", label: "字有点大，想一屏多看些内容", prev: PREVIEW },
      { font: "medium", label: "现在的字看着刚好", prev: PREVIEW },
      { font: "large", label: "再大一点，看久了不费眼", prev: PREVIEW }
    ];
    box.innerHTML = '<div class="am-onboard-title">选择你看着舒服的字号</div>'
      + '<div class="am-onboard-opts">'
      + opts.map(function (o) {
        return '<button type="button" class="am-onboard-opt" data-font="' + o.font + '">'
          + '<span class="am-onboard-label">' + o.label + '</span>'
          + '<span class="am-onboard-prev">' + o.prev + '</span></button>';
      }).join("")
      + '</div>'
      + '<button type="button" class="am-onboard-skip" id="am-onboard-skip">先按默认进入，以后再调</button>';

    box.addEventListener("click", function (ev) {
      var t = ev.target;
      var opt = t.closest ? t.closest(".am-onboard-opt") : null;
      if (opt) {
        writePref(FONT_KEY, opt.getAttribute("data-font"));
        writePref(ONBOARD_KEY, "1");
        readState(); apply();
        try { window.dispatchEvent(new Event("mfg:displaychange")); } catch (err) { /* ignore */ }
        box.remove();
        return;
      }
      if (t.id === "am-onboard-skip") {
        writePref(ONBOARD_KEY, "1");
        box.remove();
      }
    });

    if (foot && foot.parentNode === panelHost) panelHost.insertBefore(box, foot);
    else panelHost.appendChild(box);
  }

  /* ---------- 快捷键 / 全局交互 ---------- */
  function bindGlobal() {
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && panelOpen()) { closePanel(); return; }
      /* Ctrl+Z：撤销上一次档位 */
      if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && (ev.key === "z" || ev.key === "Z")) {
        if (lastSnapshot) { ev.preventDefault(); doUndo(); }
        return;
      }
      if (ev.altKey && (ev.key === "a" || ev.key === "A")) { ev.preventDefault(); openPanel(); return; }
      if (ev.altKey && (ev.key === "0" || ev.key === ")" || ev.code === "Digit0")) {
        ev.preventDefault();
        resetToDefault();
      }
    });

    document.addEventListener("click", function (ev) {
      if (!panelOpen()) return;
      if (panel && panel.contains(ev.target)) return;
      if (trigger && trigger.contains(ev.target)) return;
      closePanel();
    });

    window.addEventListener("resize", function () { if (panelOpen()) placePanel(); });
    window.addEventListener("scroll", function () { if (panelOpen()) placePanel(); }, true);
  }

  /* ---------- 反向监听既有主题切换器 ---------- */
  function watchTheme() {
    if (typeof MutationObserver !== "function") return;
    var obs = new MutationObserver(function (list) {
      var hit = false;
      list.forEach(function (m) { if (m.attributeName === "data-theme") hit = true; });
      if (!hit) return;
      var t = root.getAttribute("data-theme");
      if (t === state.theme) return;          /* 自己写的，忽略 */
      state.theme = t === "dark" ? "dark" : "light";
      syncUI();
    });
    obs.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  }

  /* ---------- 启动 ---------- */
  function boot() {
    try {
      mountTrigger();
      mountLoginTrigger();
      buildPanel();
      bindGlobal();
      watchTheme();
      tagNavGroups();
      mountRailTip();
      mountOnboard();
      syncUI();

      /* 侧栏由 app.js 反复重渲染（切岗位 / 切屏），重渲染后补回分组标记 */
      var nav = document.getElementById("main-nav");
      if (nav && typeof MutationObserver === "function") {
        new MutationObserver(tagNavGroups).observe(nav, { childList: true });
      }
      /* 登录面板由 app.js renderLogin() 重建，重建后补回首次引导 */
      var loginRoot = document.getElementById("login-root");
      if (loginRoot && typeof MutationObserver === "function") {
        new MutationObserver(function () { try { mountOnboard(); } catch (err) { /* ignore */ } })
          .observe(loginRoot, { childList: true });
      }
    } catch (err) {
      console.warn("[aging-mode] 初始化失败：", err);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
