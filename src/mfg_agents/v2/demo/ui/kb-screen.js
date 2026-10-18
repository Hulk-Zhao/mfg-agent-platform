/**
 * 企业咨询助手（kbqa）· 复刻原项目 Streamlit 微信式聊天 UI
 *
 * 数据面（方案 B：向量化迁移，独立轻量 RAG 通道）：
 *   POST /api/v2/kbqa       检索制度片段 → LLM 生成带出处回答
 *   GET  /api/v2/kbqa/meta  支持类别面板 + 知识库块数
 * 样式：app.css 内 `.kbqa-` 前缀命名空间，单一 light 主题
 *       （配色逐项取自原项目 webui.py 的 THEMES）。
 *
 * 注册方式：`window.Screens.render_kbqa`（app.js 的 renderScreen 自动按
 * `render_<screenId>` 查找，无需改 screens.js）。
 */
(function () {
  "use strict";

  // 原项目 SVG 图标（LOGO / BOT / USER 逐字保留）
  const LOGO_ICON = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none">'
    + '<path d="M4 6.8C4 5.2 5.3 4 6.9 4h10.2c1.6 0 2.9 1.2 2.9 2.8v6.4c0 1.6-1.3 2.8-2.9 2.8H13l-3.6 3.2c-.5.4-1.3.1-1.3-.6V16H6.9C5.3 16 4 14.8 4 13.2V6.8Z" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>'
    + '<circle cx="9" cy="10" r="1" fill="#fff"/><circle cx="12.5" cy="10" r="1" fill="#fff"/><circle cx="16" cy="10" r="1" fill="#fff"/></svg>';
  const BOT_ICON = '<svg width="21" height="21" viewBox="0 0 24 24" fill="none">'
    + '<rect x="4.5" y="7.5" width="15" height="12" rx="3.5" stroke="#fff" stroke-width="1.6"/>'
    + '<path d="M12 7.5V4.5" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>'
    + '<circle cx="12" cy="3.6" r="1.1" fill="#fff"/>'
    + '<circle cx="9.2" cy="12.4" r="1.15" fill="#fff"/><circle cx="14.8" cy="12.4" r="1.15" fill="#fff"/>'
    + '<path d="M9.6 16h4.8" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>'
    + '<path d="M2.8 11.5v3.6M21.2 11.5v3.6" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const USER_ICON = '<svg width="21" height="21" viewBox="0 0 24 24" fill="none">'
    + '<circle cx="12" cy="8.2" r="3.4" stroke="#fff" stroke-width="1.6"/>'
    + '<path d="M5.2 19.2c.9-3.2 3.6-4.9 6.8-4.9s5.9 1.7 6.8 4.9" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const SEND_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>';

  // 原项目侧边栏分类图标：emoji + 底色
  const CAT_ICON = {
    policy_attendance: ["🕐", "#dbeafe"], it_vpn: ["💻", "#e0e7ff"],
    finance_expense: ["💰", "#fef3c7"], hr_onboarding: ["👥", "#fce7f3"],
    admin_logistics: ["🍱", "#ffedd5"], engineering_eq: ["🔧", "#e2e8f0"],
    legal_contract: ["📜", "#ede9fe"], sales_marketing: ["📈", "#dcfce7"],
    quality_qms: ["✅", "#cffafe"], production_mes: ["🏭", "#fde68a"],
    scm_purchase: ["🚚", "#d9f99d"], equipment_eam: ["⚙️", "#e0e7ff"],
    ehs_safety: ["🦺", "#fee2e2"], npi_rd: ["🧪", "#f5d0fe"],
    warehouse_wms: ["📦", "#fef9c3"], service_after_sales: ["🎧", "#c7d2fe"],
  };

  // 元数据（支持类别 / 块数）仅拉一次，跨重渲染复用
  let metaCache = null;

  function nowHM() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function initialState() {
    return {
      messages: [{
        role: "bot",
        content: "您好！我是企业制度问答助手，请问有什么可以帮您？",
        tags: null, time: nowHM(),
      }],
      sending: false,
    };
  }

  /** channel → 角标文案（统一答案契约，ADR-001 §5 + DESIGN-2 direct）。 */
  const CH_LABEL = { faq: "制度直答", rag: "知识库检索", miss: "未找到", rag_unavailable: "知识库不可用", direct: "助手直答" };

  /** bot 消息的契约角标行：命中通道 + 耗时 + 点赞反馈（ADR-001 晋升管线入口）。
   *
   *  esc 必须由调用方传入——本函数在 render 作用域外定义，拿不到里面的
   *  `const esc = UI.esc`（2026-10-18 浏览器实测抓到的存量 ReferenceError：
   *  迭代一加角标时漏传参，任何带 channel 的回答都会让 paint 崩掉）。 */
  function messageMetaHtml(m, esc, idx) {
    if (m.role !== "bot" || !m.channel) return "";
    const cls = m.channel === "rag_unavailable" ? "warn" : (m.channel === "faq" ? "faq" : (m.channel === "direct" ? "direct" : "rag"));
    const lat = (typeof m.latency === "number") ? `<span>${m.latency}ms</span>` : "";
    const like = `<button class="kbqa-like" data-idx="${idx}" title="答得好？点赞后进入 FAQ 晋升待审队列">👍 有帮助</button>`;
    return `<div class="kbqa-meta"><span class="kbqa-badge kbqa-badge-${cls}">${esc(CH_LABEL[m.channel] || m.channel)}</span>${lat}${like}</div>`;
  }

  /** 单条消息 → 微信式气泡 HTML（bot 左 / user 右，含时间分隔与标签）。 */
  function messageHtml(m, esc, idx) {
    const time = m.time ? `<div class="kbqa-time">${esc(m.time)}</div>` : "";
    if (m.role === "user") {
      return time
        + '<div class="kbqa-row kbqa-user"><div class="kbqa-wrap">'
        + `<div class="kbqa-bubble">${esc(m.content)}</div></div>`
        + `<div class="kbqa-avatar kbqa-av-user">${USER_ICON}</div></div>`;
    }
    const tags = (m.tags && m.tags.length)
      ? '<div class="kbqa-tags">' + m.tags.map(
        (t) => `<span class="kbqa-tag ${esc(t.cls || "")}">${esc(t.text)}</span>`).join("") + "</div>"
      : "";
    const pending = m.pending ? " kbqa-pending" : "";
    // 失败气泡（超时/离线/接口错误）带重试入口：点一下原样重发，
    // 不用用户手打一遍问题（事故复盘中"锁死后无法自救"的体验缺口）。
    const retryBtn = m.retry
      ? `<button class="kbqa-retry" data-q="${esc(m.retry)}">↻ 重试</button>` : "";
    return time
      + `<div class="kbqa-row kbqa-bot"><div class="kbqa-avatar kbqa-av-bot">${BOT_ICON}</div>`
      + `<div class="kbqa-wrap"><div class="kbqa-bubble${pending}">${esc(m.content)}</div>${retryBtn}${tags}${messageMetaHtml(m, esc, idx)}</div></div>`;
  }

  function catsHtml(esc) {
    if (!metaCache) return '<div class="kbqa-cat-loading">类别加载中…</div>';
    return metaCache.depts.map((d) => {
      const [ico, bg] = CAT_ICON[d.key] || ["💬", "#f3e8ff"];
      return '<div class="kbqa-cat-item">'
        + `<div class="kbqa-cat-ico" style="background:${bg}">${ico}</div>`
        + `<div><div class="kbqa-cat-name">${esc(d.name)}</div>`
        + `<div class="kbqa-cat-desc">${esc(d.desc)}</div></div></div>`;
    }).join("");
  }

  function render(root, ctx) {
    const { UI, api, state } = ctx;
    const esc = UI.esc;
    state.screenState = state.screenState || {};
    const S = state.screenState.kbqa || (state.screenState.kbqa = initialState());

    root.innerHTML = `
      <div class="kbqa-root" id="kbqa-root">
        <aside class="kbqa-side">
          <div class="kbqa-arch">
            <b>🖥️ 系统配置</b><br>
            🔢 Embedding · BGE-M3（dense+sparse）<br>
            📚 知识库 · Milvus · enterprise_faq<br>
            🦙 LLM · deepseek-flash<br>
            🗂️ 知识块 · <span id="kbqa-count">…</span>
          </div>
          <div class="kbqa-divider"></div>
          <div class="kbqa-sec-title">📂 支持类别</div>
          <div id="kbqa-cats">${catsHtml(esc)}</div>
        </aside>
        <section class="kbqa-main">
          <div class="kbqa-header">
            <div class="kbqa-hcenter">
              <div class="kbqa-logo">${LOGO_ICON}</div>
              <div class="kbqa-brand-title">企业制度问答</div>
            </div>
            <div class="kbqa-online">在线</div>
          </div>
          <div class="kbqa-chat" id="kbqa-chat"></div>
          <div class="kbqa-inputbar">
            <input id="kbqa-input" class="kbqa-input" autocomplete="off"
              placeholder="请输入您的问题…（如：年假怎么规定 / 报销流程 / VPN 怎么连）"/>
            <button id="kbqa-send" class="kbqa-send" title="发送">${SEND_ICON}</button>
          </div>
        </section>
      </div>`;

    const chat = root.querySelector("#kbqa-chat");
    const input = root.querySelector("#kbqa-input");
    const paint = () => {
      chat.innerHTML = S.messages.map((m, i) => messageHtml(m, esc, i)).join("");
      chat.scrollTop = chat.scrollHeight;
    };
    paint();

    // 点赞 + 重试（事件委托：paint 会整体重渲染，监听器挂在容器上）
    chat.addEventListener("click", (e) => {
      const rbtn = e.target.closest(".kbqa-retry");
      if (rbtn && !rbtn.dataset.done) {
        rbtn.dataset.done = "1";
        send(rbtn.dataset.q || "");
        return;
      }
      const btn = e.target.closest(".kbqa-like");
      if (!btn || btn.dataset.done) return;
      const m = S.messages[Number(btn.dataset.idx)];
      if (!m || m.role !== "bot") return;
      btn.dataset.done = "1";
      api.kbqaFeedback(m.query, 5, { channel: m.channel || "" })
        .then(() => {
          btn.textContent = "已反馈 ✓";
          btn.classList.add("kbqa-like-done");
        })
        .catch(() => {
          btn.dataset.done = "";
          UI.toast("反馈发送失败，请稍后重试", "err");
        });
    });

    const send = async (arg) => {
      // arg 只接受字符串（重试查询）；addEventListener("click", send) 会传入
      // Event 对象，typeof 判断挡掉——2026-10-18 加了 retryQuery 参数后
      // 漏修这行，点击发送直接 TypeError（pageerror 实测复现）。
      const retryQuery = typeof arg === "string" ? arg : "";
      const q = (retryQuery || input.value || "").trim();
      if (!q) return;
      if (S.sending) { UI.toast("上一条还在响应中，请稍候", "warn"); return; }
      input.value = "";
      S.sending = true;
      S.messages.push({ role: "user", content: q, tags: null, time: nowHM() });
      S.messages.push({ role: "bot", content: "思考中…", tags: null, time: nowHM(),
                        pending: true, pendingSince: Date.now() });
      paint();

      // 等待秒数可见：pending 期间每秒刷新气泡文案——用户能区分"在动"
      // 与"死了"（2026-10-18 事故：无超时的 fetch 挂 6 分钟，气泡静止转圈）。
      const ticker = setInterval(() => {
        const p = S.messages.find((m) => m.pending);
        if (!p) { clearInterval(ticker); return; }
        const secs = Math.round((Date.now() - p.pendingSince) / 1000);
        p.content = secs < 1 ? "思考中…" : `思考中… ${secs}s`;
        paint();
      }, 1000);

      const replaceLastBot = (msg) => {
        clearInterval(ticker);
        const i = S.messages.map((m) => m.role).lastIndexOf("bot");
        if (i >= 0) S.messages[i] = msg;
      };
      try {
        if (!api.online()) throw new Error("后端离线：企业制度问答需要连接后端知识库");
        const data = await api.kbqaQuery(q);
        replaceLastBot({
          role: "bot", content: data.answer, tags: data.tags, time: nowHM(),
          channel: data.channel, latency: data.latency_ms, query: q,
        });
      } catch (err) {
        // AbortError = _post 的 75s 超时预算打满（后端最慢路径 60s+15s 余量）
        const timedOut = !!(err && err.name === "AbortError");
        const msg = timedOut
          ? "⚠️ 响应超时（等待 75s 后端无响应）。知识库服务可能正在冷启动或网络波动，"
            + "请重试；持续超时时制度问题可先咨询对口部门。"
          : `⚠️ ${(err && err.message) || err}`;
        replaceLastBot({
          role: "bot", content: msg, tags: null, time: nowHM(), retry: q, query: q,
        });
        UI.toast(timedOut ? "问答超时，请重试" : `问答失败：${(err && err.message) || err}`, "err");
      } finally {
        clearInterval(ticker);
        S.sending = false;
        paint();
      }
    };

    root.querySelector("#kbqa-send").addEventListener("click", () => send());
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.focus();

    // 元数据（支持类别 / 知识块数）：仅首次拉取
    if (!metaCache && api.online()) {
      api.kbqaMeta().then((meta) => {
        metaCache = meta;
        const cats = root.querySelector("#kbqa-cats");
        const count = root.querySelector("#kbqa-count");
        if (cats) cats.innerHTML = catsHtml(esc);
        if (count) count.textContent = String(meta.count);
      }).catch(() => { /* 元数据失败不阻塞问答 */ });
    } else if (metaCache) {
      const count = root.querySelector("#kbqa-count");
      if (count) count.textContent = String(metaCache.count);
    }
  }

  window.Screens = window.Screens || {};
  window.Screens.render_kbqa = render;
})();
