/* ============================================================
 * 个性化主题切换器（自包含 · 增量脚本 · 不侵入 app.js）
 *  - 明暗主题切换：html[data-theme] = light(默认) / dark
 *    —— 首次打开与登录默认「明亮」；只有 localStorage 明确存过 dark 才回落深色
 *  - 主题色四选一：html[data-accent] = blue(默认) / cyan / violet / orange
 *  - 偏好持久化：localStorage（mfg-theme / mfg-accent）
 *  - 挂载点两处：
 *      ① 顶栏 .topbar-right（进入系统后）
 *      ② 登录页浮动控件 #login-display-controls（右上角，#login-root 可见时才显示；
 *         纯 CSS 兄弟选择器控制显隐，登录 / 登出零 JS 联动）
 *    两组按钮共用同一套切换函数，refreshButtons() 同步所有实例的图标。
 * ============================================================ */
(function () {
  "use strict";
  var THEME_KEY = "mfg-theme";
  var ACCENT_KEY = "mfg-accent";
  var ACCENTS = ["blue", "cyan", "violet", "orange"];
  var ACCENT_DOT = { blue: "#3B8AE8", cyan: "#2AC3DE", violet: "#8B7CF6", orange: "#F2762E" };

  var SUN_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5 5l1.7 1.7M17.3 17.3L19 19M19 5l-1.7 1.7M6.7 17.3L5 19"/></svg>';
  var MOON_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 14.2A8.6 8.6 0 0 1 9.8 3.4a8.6 8.6 0 1 0 10.8 10.8z"/></svg>';

  /* 默认明亮：未存过偏好（新用户 / 首次打开 / 登录前）一律 light，深色需用户显式选择。
     index.html <head> 内联首绘脚本与本函数共用同一判据（mfg-theme === "dark"），改动需同步。 */
  function getTheme() { return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"; }
  function getAccent() {
    var a = localStorage.getItem(ACCENT_KEY);
    return ACCENTS.indexOf(a) >= 0 ? a : "blue";
  }

  /** 立即生效：在首次绘制前把属性写到 <html> 上，避免闪跳。 */
  function apply() {
    var root = document.documentElement;
    root.setAttribute("data-theme", getTheme());
    if (getAccent() !== "blue") root.setAttribute("data-accent", getAccent());
    else root.removeAttribute("data-accent");
  }
  apply();

  /* ---------- 切换动作（多实例共用） ---------- */
  function toggleTheme() {
    localStorage.setItem(THEME_KEY, getTheme() === "light" ? "dark" : "light");
    apply();
    refreshButtons();
  }
  function cycleAccent() {
    var next = ACCENTS[(ACCENTS.indexOf(getAccent()) + 1) % ACCENTS.length];
    localStorage.setItem(ACCENT_KEY, next);
    apply();
    refreshButtons();
  }

  function themeGlyph() { return getTheme() === "light" ? MOON_SVG : SUN_SVG; }
  function accentGlyph() {
    var dot = ACCENT_DOT[getAccent()];
    return '<span style="display:block;width:13px;height:13px;border-radius:50%;background:' +
      dot + ';box-shadow:0 0 0 2px rgba(127,158,205,.35)"></span>';
  }

  /** 同步所有实例（顶栏 + 登录页浮动）的图标。 */
  function refreshButtons() {
    var themes = document.querySelectorAll("[data-ts-theme]");
    var i;
    for (i = 0; i < themes.length; i++) themes[i].innerHTML = themeGlyph();
    var accents = document.querySelectorAll("[data-ts-accent]");
    for (i = 0; i < accents.length; i++) accents[i].innerHTML = accentGlyph();
  }

  function makeTsBtn(kind, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "icon-btn";
    b.title = title;
    b.setAttribute("aria-label", title);
    if (kind === "theme") {
      b.setAttribute("data-ts-theme", "");
      b.addEventListener("click", toggleTheme);
    } else {
      b.setAttribute("data-ts-accent", "");
      b.addEventListener("click", cycleAccent);
    }
    return b;
  }

  /* ---------- 登录页浮动控件（右上角，字号入口由 aging-mode.js 追加进同一容器） ---------- */
  function ensureLoginStyle() {
    if (document.getElementById("ts-login-style")) return;
    var st = document.createElement("style");
    st.id = "ts-login-style";
    st.textContent =
      "#login-display-controls{position:fixed;top:14px;right:18px;z-index:260;display:none;gap:8px;align-items:center}"
      + "#login-root:not([hidden]) ~ #login-display-controls{display:inline-flex}"
      + "#login-display-controls button{display:inline-flex;align-items:center;justify-content:center;"
      + "width:36px;height:36px;border-radius:10px;border:1px solid var(--border-strong,#D4DCE8);"
      + "background:var(--card,#fff);color:var(--text,#1B2434);cursor:pointer;"
      + "box-shadow:0 2px 10px rgba(16,24,40,.12);transition:border-color .15s ease,color .15s ease}"
      + "#login-display-controls button:hover{border-color:var(--primary);color:var(--primary)}";
    document.head.appendChild(st);
  }

  function mountLoginControls() {
    ensureLoginStyle();
    if (document.getElementById("login-display-controls")) return;
    var host = document.getElementById("login-root");
    if (!host) return;
    var box = document.createElement("div");
    box.id = "login-display-controls";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "主题与字号设置");
    box.appendChild(makeTsBtn("accent", "切换主题色"));
    box.appendChild(makeTsBtn("theme", "切换明暗主题"));
    /* 必须挂在 body 下且位于 #login-root 之后（后续兄弟），CSS「~」选择器才生效 */
    document.body.appendChild(box);
  }

  function boot() {
    var right = document.querySelector(".topbar-right");
    if (right && !document.getElementById("btn-theme")) {
      var refresh = document.getElementById("btn-refresh");
      var themeBtn = makeTsBtn("theme", "切换明暗主题");
      themeBtn.id = "btn-theme";
      var accentBtn = makeTsBtn("accent", "切换主题色");
      accentBtn.id = "btn-accent";
      if (refresh && refresh.parentNode === right) right.insertBefore(themeBtn, refresh);
      else right.appendChild(themeBtn);
      right.insertBefore(accentBtn, themeBtn);
    }
    mountLoginControls();
    refreshButtons();
  }

  /* aging-mode 面板改主题时直接写 <html> 属性并广播事件；
     这里反向监听属性，保证本脚本的太阳/月亮与色点图标不滞后。 */
  if (typeof MutationObserver === "function") {
    new MutationObserver(function () { refreshButtons(); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-accent"] });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
