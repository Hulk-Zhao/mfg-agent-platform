/* ============================================================
 * 个性化主题切换器（自包含 · 增量脚本 · 不侵入 app.js）
 *  - 明暗主题切换：html[data-theme] = dark(默认) / light
 *  - 主题色四选一：html[data-accent] = blue(默认) / cyan / violet / orange
 *  - 偏好持久化：localStorage（mfg-theme / mfg-accent）
 *  - 挂载点：顶栏右侧，注入两个 icon-btn（太阳/月亮 + 主题色圆点）
 * ============================================================ */
(function () {
  "use strict";
  var THEME_KEY = "mfg-theme";
  var ACCENT_KEY = "mfg-accent";
  var ACCENTS = ["blue", "cyan", "violet", "orange"];
  var ACCENT_DOT = { blue: "#3B8AE8", cyan: "#2AC3DE", violet: "#8B7CF6", orange: "#F2762E" };

  var SUN_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5 5l1.7 1.7M17.3 17.3L19 19M19 5l-1.7 1.7M6.7 17.3L5 19"/></svg>';
  var MOON_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 14.2A8.6 8.6 0 0 1 9.8 3.4a8.6 8.6 0 1 0 10.8 10.8z"/></svg>';

  function getTheme() { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; }
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

  function makeBtn(id, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "icon-btn";
    b.id = id;
    b.title = title;
    b.setAttribute("aria-label", title);
    return b;
  }

  function boot() {
    var right = document.querySelector(".topbar-right");
    if (!right || document.getElementById("btn-theme")) return;
    var refresh = document.getElementById("btn-refresh");

    var themeBtn = makeBtn("btn-theme", "切换明暗主题");
    var accentBtn = makeBtn("btn-accent", "切换主题色");

    function paint() {
      themeBtn.innerHTML = getTheme() === "light" ? MOON_SVG : SUN_SVG;
      var dot = ACCENT_DOT[getAccent()];
      accentBtn.innerHTML = '<span style="display:block;width:13px;height:13px;border-radius:50%;background:' +
        dot + ';box-shadow:0 0 0 2px rgba(127,158,205,.35)"></span>';
    }

    themeBtn.addEventListener("click", function () {
      localStorage.setItem(THEME_KEY, getTheme() === "light" ? "dark" : "light");
      apply(); paint();
    });

    accentBtn.addEventListener("click", function () {
      var next = ACCENTS[(ACCENTS.indexOf(getAccent()) + 1) % ACCENTS.length];
      localStorage.setItem(ACCENT_KEY, next);
      apply(); paint();
    });

    if (refresh && refresh.parentNode === right) right.insertBefore(themeBtn, refresh);
    else right.appendChild(themeBtn);
    right.insertBefore(accentBtn, themeBtn);
    paint();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
