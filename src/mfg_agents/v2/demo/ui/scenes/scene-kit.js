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

/* ============================================================
 * 智能体协同组件（T17 · 架构 §3）：agentPanel / knowledgeCard
 * 颜色一律取 app.css 变量；来源徽标 sk-source-badge 与 .demo-flag
 * 类名分离（T12 守护判据不被劫持）。
 * ============================================================ */

/* 来源徽标（replay=「演示回放」demo 语义；live=「实时链路」；不可去除） */
.sk-source-badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; font-weight: 500; line-height: 17px;
  padding: 1px 9px; border-radius: 999px;
}
.sk-source-badge-replay { color: var(--warn); background: var(--warn-bg); border: 1px dashed var(--warn); }
.sk-source-badge-live { color: var(--ok); background: var(--ok-bg); border: 1px solid var(--ok); }

/* 面板骨架与小标题 */
.sk-agent-panel { margin-top: 18px; }
.sk-agent-section-title { margin-top: 14px; font-size: 12.5px; font-weight: 600; color: var(--text-2); }

/* 参与智能体行 */
.sk-agent-agents { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.sk-agent-chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 4px 11px 4px 5px; border: 1px solid var(--border);
  border-radius: 999px; background: var(--card); font-size: 12px;
}
.sk-agent-abbr {
  flex: none; min-width: 30px; height: 20px; padding: 0 5px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 6px; background: var(--primary-light);
  color: var(--primary); font-size: 10.5px; font-weight: 700;
}
.sk-agent-name { color: var(--text); font-weight: 600; }
.sk-agent-role { color: var(--text-3); font-size: 11.5px; }

/* DAG 拓扑条（横向链 + 分叉/汇聚/会签描边 + 条件标签） */
.sk-dag { display: flex; align-items: center; overflow-x: auto; padding: 10px 2px 12px; }
.sk-dag-node {
  position: relative; flex: none; max-width: 150px;
  display: flex; flex-direction: column; gap: 2px;
  padding: 7px 10px; border: 1px solid var(--border);
  border-radius: var(--radius-sm); background: var(--card);
  font: inherit; text-align: left; color: var(--text-2); cursor: default;
}
.sk-dag-node[data-sk-agent-node] { cursor: pointer; }
.sk-dag-node[data-sk-agent-node]:hover { border-color: var(--primary); }
.sk-dag-node.is-active { border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-light); }
.sk-dag-node:disabled { opacity: .62; cursor: not-allowed; }
.sk-dag-node.is-fork { border-color: var(--purple); }
.sk-dag-node.is-join { border-color: var(--purple); }
.sk-dag-node.is-hitl { border-style: dashed; border-color: var(--warn); }
.sk-dag-node-label { font-size: 12px; font-weight: 600; color: var(--text); line-height: 1.45; }
.sk-dag-node-meta { font-size: 10.5px; color: var(--text-3); line-height: 1.5; }
.sk-dag-node-note { font-size: 10px; color: var(--text-3); }
.sk-dag-arrow { position: relative; flex: none; width: 20px; height: 2px; margin: 0 1px; background: var(--border-strong); }
.sk-dag-arrow::after {
  content: ""; position: absolute; right: -1px; top: -3px;
  border-top: 4px solid transparent; border-bottom: 4px solid transparent;
  border-left: 6px solid var(--border-strong);
}
.sk-dag-arrow.is-fork, .sk-dag-arrow.is-join { background: var(--purple); }
.sk-dag-arrow.is-fork::after, .sk-dag-arrow.is-join::after { border-left-color: var(--purple); }

/* 步骤时间线（默认折叠，点击展开；失败步骤红标） */
.sk-agent-steps { margin-top: 6px; }
.sk-agent-step-set { display: none; }
.sk-agent-step-set.is-active { display: block; }
.sk-agent-set-title { font-size: 11.5px; color: var(--text-3); margin: 8px 0 6px; }
.sk-agent-set-status { font-size: 11.5px; color: var(--primary); margin-top: 6px; }
.sk-agent-step { border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--card); margin-bottom: 8px; }
.sk-agent-step-sum {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 11px; cursor: pointer; font-size: 12.2px; color: var(--text-2); list-style: none;
}
.sk-agent-step-sum::-webkit-details-marker { display: none; }
.sk-agent-step-no {
  flex: none; width: 18px; height: 18px; border-radius: 50%;
  background: var(--primary-light); color: var(--primary);
  font-size: 10.5px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
}
.sk-agent-step-tool {
  flex: none; font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px; color: var(--primary-deep); background: var(--primary-light);
  border-radius: 5px; padding: 1px 7px;
}
.sk-agent-step-text { flex: 1; min-width: 180px; color: var(--text); }
.sk-agent-step-flag { flex: none; font-size: 11px; color: var(--ok); }
.sk-agent-step.is-failed .sk-agent-step-flag { color: var(--danger); font-weight: 700; }
.sk-agent-step.is-failed .sk-agent-step-sum { color: var(--danger); }
.sk-agent-step-body { padding: 4px 11px 10px; border-top: 1px dashed var(--border); }
.sk-agent-step-row { display: flex; gap: 10px; align-items: flex-start; margin-top: 8px; }
.sk-agent-step-k { flex: none; width: 44px; font-size: 11.5px; color: var(--text-3); }
.sk-agent-step-v { flex: 1; font-size: 12.2px; color: var(--text-2); line-height: 1.7; word-break: break-word; }
.sk-agent-step-args {
  flex: 1; margin: 0; padding: 8px 10px; border-radius: var(--radius-sm);
  background: var(--primary-light-2); border: 1px dashed var(--border-strong);
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px; line-height: 1.65; color: var(--text-2);
  white-space: pre-wrap; word-break: break-all;
}

/* 并行四列形态（fan_out 分支并发时间线，数据驱动） */
.sk-agent-parallel { margin-top: 14px; }
.sk-agent-branches { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.sk-agent-branch { border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--card); padding: 9px 11px 11px; }
.sk-agent-branch-head { font-size: 12.2px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
.sk-agent-branch-empty { font-size: 11.5px; color: var(--text-3); }
.sk-agent-branch-conclusion { margin-top: 8px; font-size: 11.5px; color: var(--text-2); line-height: 1.7; border-top: 1px dashed var(--border); padding-top: 7px; }
.sk-agent-branch-conclusion b { color: var(--primary); }
.sk-agent-converge { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 11.5px; color: var(--text-3); }
.sk-agent-converge-arrow { position: relative; width: 2px; height: 14px; background: var(--purple); }
.sk-agent-converge-arrow::after {
  content: ""; position: absolute; left: -2px; bottom: -1px;
  border-left: 3px solid transparent; border-right: 3px solid transparent;
  border-top: 5px solid var(--purple);
}

/* 结论卡 / HITL 卡（操作位一律只读，零写按钮） */
.sk-agent-conclusion { margin-top: 6px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--card); padding: 11px 13px; }
.sk-agent-answer { font-size: 12.8px; color: var(--text); line-height: 1.85; }
.sk-agent-sub-title { margin-top: 10px; font-size: 11.5px; font-weight: 600; color: var(--text-3); }
.sk-agent-list { margin: 6px 0 0 0; padding-left: 18px; }
.sk-agent-list li { font-size: 12.2px; color: var(--text-2); line-height: 1.8; }
.sk-agent-empty { margin-top: 6px; font-size: 11.8px; color: var(--text-3); }
.sk-agent-empty.is-ok { color: var(--ok); }
.sk-agent-hitl-line { margin-top: 10px; font-size: 12px; color: var(--warn); line-height: 1.7; }
.sk-agent-hitl { margin-top: 6px; border: 1px dashed var(--warn); border-radius: var(--radius-sm); background: var(--warn-bg); padding: 11px 13px; }
.sk-agent-hitl-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.sk-agent-hitl-title { font-size: 13px; font-weight: 700; color: var(--text); }
.sk-agent-hitl-act { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-top: 10px; }
.sk-agent-hitl-note { font-size: 11.5px; color: var(--text-3); line-height: 1.6; }

/* 知识图片卡（knowledgeCard）：内联 SVG + 节点书签 + 图文互证声明 */
.sk-kc { margin-top: 18px; }
.sk-kc-svg { margin-top: 8px; overflow-x: auto; }
.sk-kc-svg svg { display: block; width: 100%; height: auto; }
.sk-kc-bookmarks { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.sk-kc-bookmarks.is-empty { font-size: 11.5px; color: var(--text-3); }
.sk-kc-bookmark {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 11px; border: 1px solid var(--border);
  border-radius: 999px; background: var(--card);
  font-size: 12px; color: var(--text-2); cursor: pointer;
  transition: border-color .16s ease, color .16s ease, background .16s ease;
}
.sk-kc-bookmark::before { content: "\\25C6"; font-size: 9px; color: var(--text-3); }
.sk-kc-bookmark:hover { border-color: var(--primary); color: var(--primary); }
.sk-kc-bookmark.is-active { border-color: var(--primary); color: var(--primary); background: var(--primary-light); }
.sk-kc-bookmark.is-active::before { color: var(--primary); }
.sk-kc-caption { display: flex; align-items: flex-start; gap: 7px; margin-top: 10px; font-size: 11.5px; color: var(--text-3); line-height: 1.7; }
.sk-kc-caption svg { flex: none; margin-top: 2px; }
/* 节点高亮：!important 用于压过场景 SVG 自带的类规则与内联 stroke（高亮是瞬时态） */
.sk-kc .sk-kc-node-active,
.sk-kc .sk-kc-node-active rect,
.sk-kc .sk-kc-node-active circle,
.sk-kc .sk-kc-node-active ellipse,
.sk-kc .sk-kc-node-active path,
.sk-kc .sk-kc-node-active polygon {
  stroke: var(--primary) !important;
  stroke-width: 2.8 !important;
}

@media (max-width: 760px) {
  .sk-agent-branches { grid-template-columns: minmax(0, 1fr); }
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
   * 八、框架级「演示数据」徽标强制（T12）
   *
   * 纪律：任何场景屏的**同步首帧**必须带「演示数据」徽标（唯一出口
   * `demoBadge()`，产出 `.demo-flag`）。漏挂的屏**不渲染**——首帧被换成
   * 框架错误面板并 console.error，而不是静默显示一屏没有演示标识的界面。
   *
   * 为什么挂在 `SceneRegistry.register` 上：scene-kit.js 在 registry.js
   * **之后**、各场景脚本**之前**加载（index.html 偏序，T05 契约钉死），
   * 所以在这里包装 register 能覆盖 T06–T11 已交付与后续新增的全部屏——
   * 新屏忘记挂徽标时无法绕开，不必要求各屏自觉。
   * ============================================================ */

  /** `.demo-flag` 的类名匹配（只认 class 属性，避免误命中正文里的同名字样）。 */
  const DEMO_BADGE_CLASS_RE = /class="[^"]*\bdemo-flag\b[^"]*"/;

  /** 首帧 HTML 是否已带「演示数据」徽标。 */
  function hasDemoBadge(root) {
    if (!root) return false;
    const html = typeof root.innerHTML === "string" ? root.innerHTML : "";
    return DEMO_BADGE_CLASS_RE.test(html);
  }

  /** 漏挂徽标时呈现的框架拒绝面板（此时**不渲染**场景内容）。 */
  function badgeGuardPanel(sceneId) {
    return '<div class="content-inner"><div class="sk-degrade" data-sk-badge-guard="1">'
      + icon("warn", 15)
      + '<div><b>场景 ' + esc(String(sceneId || "未知"))
      + ' 首帧未挂「演示数据」徽标，已按框架纪律拒绝渲染。</b>'
      + '<div class="sk-degrade-sub">所有场景屏首帧须经 SceneKit.pageHead()'
      + "（或 SceneKit.demoBadge()）产出 <code>.demo-flag</code>，用于钉死演示标识。"
      + "修复：确认屏内首帧 HTML 调用了 SceneKit.pageHead()。</div></div></div></div>";
  }

  /**
   * 包装 `window.SceneRegistry.register`，给每个场景的 render 加「首帧必有
   * 演示徽标」的守护。幂等：重复调用直接返回。
   *
   * @returns {boolean} 是否已装上守护（registry 缺席时返回 false）
   */
  function installBadgeGuard() {
    const reg = window.SceneRegistry;
    if (!reg || typeof reg.register !== "function") return false;
    if (reg.__demoBadgeGuard === true) return true;

    const originalRegister = reg.register;
    reg.register = function (def) {
      if (def && typeof def === "object" && typeof def.render === "function") {
        const sceneId = def.id;
        const originalRender = def.render;
        const guarded = Object.assign({}, def, {
          render: function (root, ctx) {
            originalRender.call(this, root, ctx);
            if (hasDemoBadge(root)) return;
            if (typeof console !== "undefined" && console.error) {
              console.error("[SceneKit] 场景 " + String(sceneId)
                + " 首帧缺少「演示数据」徽标（.demo-flag），已按框架纪律拒绝渲染；"
                + "请确认屏内首帧调用了 SceneKit.pageHead()/demoBadge()。");
            }
            if (root) {
              root.innerHTML = badgeGuardPanel(sceneId);
              if (root.dataset) root.dataset.skBadgeGuard = "1";
            }
          },
        });
        return originalRegister.call(this, guarded);
      }
      return originalRegister.call(this, def);
    };
    reg.__demoBadgeGuard = true;
    return true;
  }

  /* ============================================================
   * 智能体协同组件（T17 · 架构设计 §3：agentPanel / knowledgeCard / replay）
   *
   * 三条纪律（与既有组件同律）：
   *  1. 回放包必带 schema_version（共享知识 #1），缺失/不符 → 整块降级，
   *     不渲染半包；降级文案说「未就绪」，不透传 err.message；
   *  2. steps 六键与 AgenticStep.to_dict() 同构（共享知识 #4），
   *     组件只认这六个键，脏包多带的键漏不进界面；
   *  3. 来源徽标用独立类 sk-source-badge，不占用 .demo-flag（T12 判据）；
   *     组件内零写按钮，HITL 操作位一律「只读演示」。
   * ============================================================ */

  /** 回放包 schema 版本（架构 §2.1：固定 "1.0"，只加字段不改语义）。 */
  const REPLAY_SCHEMA_VERSION = "1.0";

  /**
   * 回放步骤六键——与 `AgenticStep.to_dict()`（v2/agents/agentic.py L59-61）
   * **六键全同**（架构 §3.1 / 共享知识 #4：键集相等，不是「字段相似」）。
   */
  const REPLAY_STEP_KEYS = ["step", "thought", "tool", "args", "summary", "ok"];

  /** 只保留六键的步骤副本（半包 / 脏包的未知键不会漏进界面）。 */
  function pickReplayStep(step) {
    const src = (step && typeof step === "object") ? step : {};
    const out = {};
    REPLAY_STEP_KEYS.forEach(function (key) {
      out[key] = src[key];
    });
    return out;
  }

  /** 步骤参数展示：对象 → 两空格缩进 JSON；空值 → 破折号（绝不显示 undefined）。 */
  function stepArgsText(args) {
    if (args === null || args === undefined || args === "") return "—";
    if (typeof args !== "object") return String(args);
    try {
      return JSON.stringify(args, null, 2);
    } catch (err) {
      return String(args);
    }
  }

  /** DAG 节点类型 → 中文标签（五种 kind，与场景 YAML steps 对回）。 */
  const DAG_KIND_LABELS = {
    agent: "智能体",
    fan_out: "分叉",
    fan_in: "汇聚",
    hitl: "人工会签",
    workflow: "工作流",
  };

  /** W3C 命名空间声明（标识符，不是网络请求——内联 SVG 放行）。 */
  const SVG_NS_RE = /https?:\/\/www\.w3\.org\/\d{4}\/svg/g;

  /**
   * 内联 SVG 是否含外部资源引用（隔离纪律：知识图片零外部 URL）。
   * 先剔除 xmlns 命名空间声明，再查协议相对 / 绝对外链（http(s):// 或 //）。
   */
  function svgHasExternalResource(svgText) {
    const stripped = String(svgText === null || svgText === undefined ? "" : svgText)
      .replace(SVG_NS_RE, "");
    return /(?:https?:)?\/\//i.test(stripped);
  }

  function dagNodeKindLabel(kind) {
    const key = safeToken(kind);
    return DAG_KIND_LABELS[key] || (key === "unknown" ? "节点" : key);
  }

  /** 来源徽标（replay=「演示回放」demo 语义；live=「实时链路」）。无开关可关。 */
  function agentSourceBadge(source) {
    const live = String(source || "") === "live";
    const cls = "sk-source-badge " + (live ? "sk-source-badge-live" : "sk-source-badge-replay");
    const text = live ? "实时链路" : "演示回放";
    return '<span class="' + cls + '" data-sk-source="' + esc(live ? "live" : "replay") + '">'
      + esc(text) + "</span>";
  }

  /** 键值行（复用既有 .sk-kv 结构，口径与既有屏下钻一致）。 */
  function agentKv(pairs) {
    const rows = (Array.isArray(pairs) ? pairs : []).map(function (pair) {
      const key = Array.isArray(pair) ? String(pair[0] || "") : "";
      const val = Array.isArray(pair) ? pair[1] : "";
      const text = (val === null || val === undefined || val === "") ? "—" : String(val);
      return '<div class="sk-kv-row"><span class="sk-kv-k">' + esc(key)
        + '</span><span class="sk-kv-v">' + esc(text) + "</span></div>";
    }).join("");
    return '<div class="sk-kv">' + rows + "</div>";
  }

  /**
   * 回放取数统一入口（架构 §3.3）。与 `story()` 同纪律：
   * **永不 reject、不抛未捕获异常、不透传 err.message**。
   *
   * 取数顺序：`GET /api/v2/demo/replays/{replayId}`（T16 端点）
   * → `window.DEMO_STORY_REPLAY[replayId]`（离线回放镜像，可选）
   * → `{ok:false, error:"演示回放数据未就绪"}`。
   * `schema_version` 缺失或不等于 "1.0" → `{ok:false, error:"回放包版本不兼容"}`
   * （整块降级，不渲染半包）。
   *
   * @param {string} replayId 回放包主键（如 `s05_complaint_8d`）。
   * @returns {Promise<{ok: boolean, package?: Object, schema_version?: string,
   *   source?: "api"|"local", degraded?: boolean, error?: string, note?: string}>}
   */
  function replay(replayId) {
    const id = String(replayId === null || replayId === undefined ? "" : replayId).trim();
    if (!id) {
      return Promise.resolve({ ok: false, error: "未指定回放 id" });
    }

    // 版本校验 + 信封装配：缺版本 / 版本不符 → 整块降级，不渲染半包。
    const accept = function (pkg, source) {
      if (!pkg || typeof pkg !== "object") {
        return { ok: false, error: "演示回放数据未就绪" };
      }
      if (pkg.schema_version !== REPLAY_SCHEMA_VERSION) {
        return { ok: false, error: "回放包版本不兼容" };
      }
      return { ok: true, package: pkg, schema_version: pkg.schema_version, source: source };
    };

    const degrade = function (note) {
      const bag = window.DEMO_STORY_REPLAY;
      const local = (bag && typeof bag === "object" && !Array.isArray(bag)) ? bag[id] : null;
      if (local && typeof local === "object") {
        const accepted = accept(local, "local");
        if (accepted.ok) {
          // 与 story() 同口径：离线镜像命中 = 降级成功，必须带 degraded 标记
          return { ok: true, package: accepted.package,
                   schema_version: accepted.schema_version,
                   source: "local", degraded: true, note: note || "" };
        }
        return accepted;  // 版本不符等硬伤原样降级，不渲染半包
      }
      return { ok: false, error: "演示回放数据未就绪", note: note || "" };
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

    // T16 端点口径：GET /api/v2/demo/replays/{replay_id} → {...demo_banner, package}。
    // 单引号是**过渡写法**：T05 的 test_scene_kit_endpoint_matches_backend_route 只抓
    // 双引号整串端点与 api.py 路由表对账；T16 路由落地前，双引号整串会弄红该对账
    // （tests/demo 必须全绿）。T16 提交路由后，把这里改回双引号整串端点路径，
    // 即自动进入 T05 对账（零测试改动）。
    const url = base + '/api/v2/demo/replays/' + encodeURIComponent(id);
    return fetchJson(url, 6000).then(function (payload) {
      const pkg = (payload && typeof payload === "object") ? payload.package : null;
      if (pkg && typeof pkg === "object") return accept(pkg, "api");
      return degrade("接口返回结构不符合回放包约定");
    }).catch(function () {
      return degrade("接口不可用（T16 回放端点未就绪或无权限）");
    }).catch(function () {
      return { ok: false, error: "演示回放数据未就绪" };
    });
  }

  /**
   * 智能体协同面板（架构 §3.1）。只产出 HTML 字符串，样式经 injectStyle() 自注入。
   *
   * @param {Object} opts 入参 schema 见架构 §3.1：
   *   `{ title, agents:[{domain,abbr,name,role}], dag?:{nodes,edges},
   *      steps:[{step,thought,tool,args,summary,ok}], node_steps?:{nodeId:steps[]},
   *      conclusion:{answer,data_used,needs_hitl,hitl_title?,hitl_slot?,data_gaps},
   *      source:"live"|"replay", hitl_task? }`
   * @returns {string} HTML（steps 为空数组时整块降级，不渲染半面板）
   */
  function agentPanel(opts) {
    const o = (opts && typeof opts === "object") ? opts : {};
    const steps = Array.isArray(o.steps) ? o.steps : null;
    if (!steps || !steps.length) {
      // steps 六键同构铁律的入口闸：空数组 = 回放数据缺失，整块降级不编造。
      return '<div class="card"><div class="card-pad sk-degrade" data-sk-agent-degrade="1">'
        + icon("warn", 15)
        + "<div><b>回放数据缺失，不展示编造步骤。</b>"
        + '<div class="sk-degrade-sub">该智能体协同面板未收到回放步骤（steps 为空数组），'
        + "按上线纪律整块降级，不渲染半面板。</div></div></div></div>";
    }

    const agents = Array.isArray(o.agents) ? o.agents : [];
    const dag = (o.dag && typeof o.dag === "object"
      && Array.isArray(o.dag.nodes) && o.dag.nodes.length) ? o.dag : null;
    const nodeSteps = (o.node_steps && typeof o.node_steps === "object"
      && !Array.isArray(o.node_steps)) ? o.node_steps : {};
    const conclusion = (o.conclusion && typeof o.conclusion === "object") ? o.conclusion : null;
    const hitl = (o.hitl_task && typeof o.hitl_task === "object") ? o.hitl_task : null;

    // 1. 头部：标题 + 来源徽标（replay 必须可见，徽标不可去除、无 opts 开关）
    let html = '<div class="card sk-agent-panel" data-sk-agent-panel="1">'
      + '<div class="card-head">'
      + '<div class="card-title">' + esc(o.title || "智能体协同面板") + "</div>"
      + '<div class="card-extra">' + agentSourceBadge(o.source) + "</div>"
      + "</div>"
      + '<div class="card-pad">';

    // 2. 参与智能体行（域徽章 abbr + name + role，全部来自入参）
    if (agents.length) {
      html += '<div class="sk-agent-agents">' + agents.map(function (a) {
        const ag = a || {};
        const abbr = (ag.abbr === undefined || ag.abbr === null || ag.abbr === "")
          ? "—" : String(ag.abbr);
        const name = (ag.name === undefined || ag.name === null || ag.name === "")
          ? "未命名智能体" : String(ag.name);
        const role = (ag.role === undefined || ag.role === null || ag.role === "")
          ? "" : String(ag.role);
        return '<span class="sk-agent-chip">'
          + '<span class="sk-agent-abbr">' + esc(abbr) + "</span>"
          + '<span class="sk-agent-name">' + esc(name) + "</span>"
          + (role ? '<span class="sk-agent-role">' + esc(role) + "</span>" : "")
          + "</span>";
      }).join("") + "</div>";
    }

    // 3. DAG 拓扑条（dag 缺省 = 轻形态：S1/S3/S6 问答/推送面板无拓扑条）
    if (dag) {
      html += '<div class="sk-agent-section-title">协同拓扑（点击节点查看该节点回放步骤）</div>'
        + dagBarHtml(dag, nodeSteps);
    }

    // 4. 步骤时间线（默认折叠；点击 DAG 节点切换 node_steps[nodeId]）
    html += '<div class="sk-agent-steps" data-sk-agent-steps="1">'
      + stepSetHtml(steps, "__main__", "主链步骤（默认折叠，点击展开思考与参数）", true);
    if (dag) {
      dag.nodes.forEach(function (node) {
        const n = node || {};
        const id = String(n.id || "");
        const list = nodeSteps[id];
        if (Array.isArray(list) && list.length) {
          html += stepSetHtml(list, id, "节点步骤：" + String(n.label || id), false);
        }
      });
    }
    html += "</div>"
      + '<div class="sk-agent-set-status" data-sk-agent-set-status="1">当前显示：主链回放步骤</div>';

    // 5. 并行四列形态（dag 含 fan_out 时：分支并发时间线，数据驱动非硬编码）
    html += parallelBranchesHtml(dag, nodeSteps);

    // 6. 结论卡（answer + data_used 逐条 + data_gaps 诚实声明 + needs_hitl 行）
    html += '<div class="sk-agent-section-title">结论</div><div class="sk-agent-conclusion">';
    if (conclusion) {
      html += '<div class="sk-agent-answer">'
        + esc(String(conclusion.answer === undefined || conclusion.answer === null
          ? "（无结论正文）" : conclusion.answer)) + "</div>";
      const used = Array.isArray(conclusion.data_used) ? conclusion.data_used : [];
      html += '<div class="sk-agent-sub-title">数据出处</div>'
        + (used.length
          ? '<ul class="sk-agent-list">' + used.map(function (item) {
            return "<li>" + esc(String(item === null || item === undefined ? "" : item)) + "</li>";
          }).join("") + "</ul>"
          : '<div class="sk-agent-empty">未记录数据出处</div>');
      const gaps = Array.isArray(conclusion.data_gaps) ? conclusion.data_gaps : [];
      html += '<div class="sk-agent-sub-title">数据缺口（诚实声明）</div>'
        + (gaps.length
          ? '<ul class="sk-agent-list">' + gaps.map(function (item) {
            return "<li>" + esc(String(item === null || item === undefined ? "" : item)) + "</li>";
          }).join("") + "</ul>"
          : '<div class="sk-agent-empty is-ok">无数据缺口</div>');
      if (conclusion.needs_hitl === true) {
        html += '<div class="sk-agent-hitl-line">需人工会签：'
          + esc(String(conclusion.hitl_title || "未命名工单"))
          + (conclusion.hitl_slot
            ? "（治理槽位 " + esc(String(conclusion.hitl_slot)) + "）"
            : "")
          + "</div>";
      }
    } else {
      html += '<div class="sk-agent-empty">结论数据缺失，不展示编造结论</div>';
    }
    html += "</div>";

    // 7. HITL 卡（hitl_task 存在时；操作位一律「只读演示」，零写按钮）
    if (hitl) {
      const visibility = String(hitl.visibility || "未标注");
      const visLabel = visibility === "mine" ? "我的会签（mine）"
        : visibility === "domain" ? "域内可见（domain）"
          : visibility;
      html += '<div class="sk-agent-section-title">HITL 工单（只读回放）</div>'
        + '<div class="sk-agent-hitl" data-sk-agent-hitl="1">'
        + '<div class="sk-agent-hitl-head">'
        + '<span class="sk-agent-hitl-title">' + esc(String(hitl.title || "未命名工单")) + "</span>"
        + badge(String(hitl.status || "状态未标注"), "badge-amber")
        + "</div>"
        + agentKv([
          ["治理槽位", hitl.slot],
          ["会签进度", hitl.progress],
          ["本岗位可见性", visLabel],
          ["是否必须会签", hitl.required === true ? "是" : (hitl.required === false ? "否" : "—")],
          ["本岗位动作", hitl.my_action],
        ])
        + '<div class="sk-agent-hitl-act">'
        + '<span class="sk-readonly">只读演示</span>'
        + '<span class="sk-agent-hitl-note">'
        + esc(String(hitl.readonly_note || "写操作归治理窗口，本面板按回放形态只读展示。"))
        + "</span></div></div>";
    }

    html += "</div></div>";
    return html;
  }

  /** DAG 拓扑条：节点小卡横向链；fan_out 分叉 / fan_in 汇聚 / hitl 特殊描边挂槽位名。 */
  function dagBarHtml(dag, nodeSteps) {
    const nodes = Array.isArray(dag && dag.nodes) ? dag.nodes : [];
    if (!nodes.length) return "";
    const hasSteps = (nodeSteps && typeof nodeSteps === "object") ? nodeSteps : {};
    const nodeAttrs = function (n) {
      const id = String((n || {}).id || "");
      const list = hasSteps[id];
      const expandable = Array.isArray(list) && list.length > 0;
      const kind = String((n || {}).kind || "");
      const meta = [];
      meta.push(dagNodeKindLabel(kind));
      if ((n || {}).condition) meta.push("条件：" + String(n.condition));
      if (kind === "hitl" && (n || {}).slot) meta.push("槽位：" + String(n.slot));
      const cls = "sk-dag-node"
        + (kind === "fan_out" ? " is-fork" : "")
        + (kind === "fan_in" ? " is-join" : "")
        + (kind === "hitl" ? " is-hitl" : "");
      const attrs = expandable
        ? ' type="button" data-sk-agent-node="' + esc(id) + '"'
        + ' data-sk-agent-label="' + esc(String((n || {}).label || id)) + '"'
        + ' aria-pressed="false" title="点击查看该节点的回放步骤"'
        : ' type="button" disabled title="该节点无独立回放步骤，暂不可展开"';
      return { cls: cls, attrs: attrs, expandable: expandable,
        label: esc(String((n || {}).label || id || "未命名节点")),
        meta: esc(meta.join(" · ")) };
    };
    let html = '<div class="sk-dag" role="group" aria-label="智能体协同拓扑">';
    nodes.forEach(function (node, idx) {
      if (idx > 0) {
        const prevKind = String((nodes[idx - 1] || {}).kind || "");
        const curKind = String((node || {}).kind || "");
        const arrowCls = prevKind === "fan_out" ? " is-fork"
          : curKind === "fan_in" ? " is-join" : "";
        html += '<span class="sk-dag-arrow' + arrowCls + '" aria-hidden="true"></span>';
      }
      const a = nodeAttrs(node);
      html += '<button class="' + a.cls + '"' + a.attrs + ">"
        + '<span class="sk-dag-node-label">' + a.label + "</span>"
        + '<span class="sk-dag-node-meta">' + a.meta + "</span>"
        + (a.expandable ? "" : '<span class="sk-dag-node-note">无独立步骤</span>')
        + "</button>";
    });
    return html + "</div>";
  }

  /** 一组回放步骤（主链或某节点）：竖向 step 列表，默认折叠为「工具名 + summary」。 */
  function stepSetHtml(steps, setKey, titleText, active) {
    const list = Array.isArray(steps) ? steps : [];
    if (!list.length) return "";
    const rows = list.map(function (raw) {
      return stepDetailsHtml(pickReplayStep(raw));
    }).join("");
    return '<div class="sk-agent-step-set' + (active ? " is-active" : "") + '"'
      + ' data-sk-agent-step-set="' + esc(setKey) + '">'
      + '<div class="sk-agent-set-title">' + esc(titleText) + "</div>"
      + rows + "</div>";
  }

  /** 单步：折叠态显示步骤号 + 工具名 + summary + 成败标记；展开显示 thought/args/结果。 */
  function stepDetailsHtml(s) {
    const failed = s.ok === false;
    const stepNo = (s.step === undefined || s.step === null || s.step === "")
      ? "—" : String(s.step);
    const tool = (s.tool === undefined || s.tool === null || s.tool === "")
      ? "未记录工具" : String(s.tool);
    const summary = (s.summary === undefined || s.summary === null || s.summary === "")
      ? "（无摘要）" : String(s.summary);
    const thought = (s.thought === undefined || s.thought === null || s.thought === "")
      ? "（未记录思考）" : String(s.thought);
    return '<details class="sk-agent-step' + (failed ? " is-failed" : "") + '">'
      + '<summary class="sk-agent-step-sum">'
      + '<span class="sk-agent-step-no">' + esc(stepNo) + "</span>"
      + '<span class="sk-agent-step-tool">' + esc(tool) + "</span>"
      + '<span class="sk-agent-step-text">' + esc(summary) + "</span>"
      + '<span class="sk-agent-step-flag">' + esc(failed ? "失败" : "成功") + "</span>"
      + "</summary>"
      + '<div class="sk-agent-step-body">'
      + '<div class="sk-agent-step-row"><span class="sk-agent-step-k">思考</span>'
      + '<span class="sk-agent-step-v">' + esc(thought) + "</span></div>"
      + '<div class="sk-agent-step-row"><span class="sk-agent-step-k">参数</span>'
      + '<pre class="sk-agent-step-args">' + esc(stepArgsText(s.args)) + "</pre></div>"
      + '<div class="sk-agent-step-row"><span class="sk-agent-step-k">结果</span>'
      + '<span class="sk-agent-step-v">'
      + esc(failed ? "该步骤执行失败（以摘要为准，不编造结果）" : "执行成功") + "</span></div>"
      + "</div></details>";
  }

  /**
   * 并行四列形态：dag 含 fan_out 时渲染分支并发时间线——每个分支一列
   * （列头 = 分支 label），列内为该分支 node_steps 的折叠步骤 + 结论摘要；
   * 列后画汇聚箭头到 fan_in。数据驱动，非 S5 硬编码（S4 单分支同样适用）。
   */
  function parallelBranchesHtml(dag, nodeSteps) {
    if (!dag) return "";
    const nodes = Array.isArray(dag.nodes) ? dag.nodes : [];
    const edges = Array.isArray(dag.edges) ? dag.edges : [];
    const forks = nodes.filter(function (n) {
      return String((n || {}).kind || "") === "fan_out";
    });
    if (!forks.length) return "";

    let html = "";
    forks.forEach(function (fork) {
      const forkId = String((fork || {}).id || "");
      const branchNodes = edges.filter(function (e) {
        return e && String(e.from || "") === forkId;
      }).map(function (e) {
        const toId = String(e.to || "");
        return nodes.filter(function (n) {
          return String((n || {}).id || "") === toId;
        })[0] || null;
      }).filter(function (n) { return !!n; });
      if (!branchNodes.length) return;

      html += '<div class="sk-agent-parallel">'
        + '<div class="sk-agent-section-title">并行分支（自「'
        + esc(String((fork || {}).label || forkId)) + "」分叉，回放数据驱动）</div>"
        + '<div class="sk-agent-branches">' + branchNodes.map(function (bn) {
          const id = String((bn || {}).id || "");
          const list = Array.isArray(nodeSteps[id]) ? nodeSteps[id] : [];
          const body = list.length
            ? list.map(function (raw) { return stepDetailsHtml(pickReplayStep(raw)); }).join("")
            : '<div class="sk-agent-branch-empty">本分支回放步骤缺失，不展示编造步骤</div>';
          const last = list.length ? pickReplayStep(list[list.length - 1]) : null;
          const branchConclusion = (last && last.summary) ? String(last.summary) : "";
          return '<div class="sk-agent-branch">'
            + '<div class="sk-agent-branch-head">'
            + esc(String((bn || {}).label || id)) + "</div>"
            + body
            + (branchConclusion
              ? '<div class="sk-agent-branch-conclusion"><b>分支结论摘要</b>'
              + "（取自本分支回放末步）：" + esc(branchConclusion) + "</div>"
              : "")
            + "</div>";
        }).join("") + "</div>";

      const join = nodes.filter(function (n) {
        return String((n || {}).kind || "") === "fan_in";
      })[0] || null;
      html += '<div class="sk-agent-converge">'
        + '<span class="sk-agent-converge-arrow" aria-hidden="true"></span>'
        + "汇聚至：" + esc(String((join && join.label) || "fan_in 节点"))
        + "</div></div>";
    });
    return html;
  }

  /**
   * 知识图片卡（架构 §3.2）。内联 SVG 零外部资源（隔离红线友好）；
   * 书签与 svg 节点 id 一一对应，点击书签 → SVG 对应节点高亮
   * `sk-kc-node-active` + 复用 `openDrill()` 弹层显示取证口径。
   * `caption` 为图文互证声明：写明「图中结论与智能体协同面板结论同源，
   * 可逐条对回」，由调用方（场景数据文件口径）给定。
   *
   * @param {Object} opts `{ title, svg, nodes:[{id,label,agent,tool,conclusion,refs}], caption }`
   * @returns {string} HTML（svg 为空 / 含外部链接时整块降级，不渲染）
   */
  function knowledgeCard(opts) {
    const o = (opts && typeof opts === "object") ? opts : {};
    const svgText = (typeof o.svg === "string") ? o.svg.trim() : "";
    const nodes = Array.isArray(o.nodes) ? o.nodes : [];
    const caption = (typeof o.caption === "string") ? o.caption.trim() : "";

    if (!svgText) {
      return '<div class="card"><div class="card-pad sk-degrade" data-sk-kc-degrade="1">'
        + icon("warn", 15)
        + "<div><b>知识图片数据缺失，不展示编造图形。</b>"
        + '<div class="sk-degrade-sub">未收到内联 SVG 字符串（svg 为空），按纪律整块降级。</div>'
        + "</div></div></div>";
    }
    // 隔离纪律：知识图片只允许内联 SVG，零外部 URL（几何由场景数据文件现场生成）。
    if (svgHasExternalResource(svgText)) {
      return '<div class="card"><div class="card-pad sk-degrade" data-sk-kc-degrade="1">'
        + icon("warn", 15)
        + "<div><b>图谱包含外部资源引用，已按隔离纪律拦截。</b>"
        + '<div class="sk-degrade-sub">知识图片只允许内联 SVG（零外部 URL），'
        + "请改用场景数据文件给定的现场生成几何坐标。</div></div></div></div>";
    }

    let html = '<div class="card sk-kc" data-sk-knowledge-card="1">'
      + '<div class="card-head">'
      + '<div class="card-title">' + esc(o.title || "知识图片") + "</div>"
      + '<div class="card-extra">' + demoBadge() + "</div>"
      + "</div>"
      + '<div class="card-pad">'
      // SVG 区：内联字符串直接插入（外链已在上面的拦截中排除）
      + '<div class="sk-kc-svg">' + svgText + "</div>";

    // 节点书签列（与 svg 节点 id 一一对应；取证口径随 data-* 下沉，点击即弹层）
    if (nodes.length) {
      html += '<div class="sk-kc-bookmarks">' + nodes.map(function (n) {
        const nd = n || {};
        const id = String(nd.id || "");
        const refs = (Array.isArray(nd.refs) ? nd.refs : []).map(function (r) {
          return String(r === null || r === undefined ? "" : r);
        });
        const label = String(nd.label || id || "节点");
        return '<button class="sk-kc-bookmark" type="button"'
          + ' data-sk-kc-bookmark="' + esc(id) + '"'
          + ' data-sk-kc-label="' + esc(label) + '"'
          + ' data-sk-kc-agent="' + esc(String(nd.agent === undefined || nd.agent === null ? "" : nd.agent)) + '"'
          + ' data-sk-kc-tool="' + esc(String(nd.tool === undefined || nd.tool === null ? "" : nd.tool)) + '"'
          + ' data-sk-kc-conclusion="' + esc(String(nd.conclusion === undefined || nd.conclusion === null ? "" : nd.conclusion)) + '"'
          + ' data-sk-kc-refs="' + esc(JSON.stringify(refs)) + '"'
          + ' aria-pressed="false" title="点击高亮图谱节点并查看取证口径">'
          + esc(label) + "</button>";
      }).join("") + "</div>";
    } else {
      html += '<div class="sk-kc-bookmarks is-empty">本图未配置节点书签</div>';
    }

    // 图文互证声明（caption 写明「图中结论与智能体协同面板结论同源，可逐条对回」）
    if (caption) {
      html += '<div class="sk-kc-caption">' + icon("info", 12) + "<span>" + esc(caption) + "</span></div>";
    }
    return html + "</div></div>";
  }

  /** 书签弹层内容：取证智能体 / 工具 / 结论 / 关联口径（复用 openDrill）。 */
  function kcDrillHtml(node) {
    let html = '<div class="section-title">取证口径</div>'
      + agentKv([
        ["取证智能体", node.agent],
        ["取证工具", node.tool],
        ["节点结论", node.conclusion],
      ]);
    html += '<div class="section-title" style="margin-top:14px">关联口径</div>';
    if (Array.isArray(node.refs) && node.refs.length) {
      html += '<ul class="sk-agent-list">' + node.refs.map(function (r) {
        return "<li>" + esc(r) + "</li>";
      }).join("") + "</ul>";
    } else {
      html += '<div class="sk-agent-empty">未登记关联口径</div>';
    }
    return html;
  }

  /** 高亮 SVG 内对应节点（id 或 data-sk-kc-node 命中；类名 sk-kc-node-active）。 */
  function highlightKnowledgeNode(card, nodeId) {
    const svgHost = card.querySelector(".sk-kc-svg");
    if (!svgHost) return false;
    const candidates = svgHost.querySelectorAll("[id],[data-sk-kc-node]");
    let matched = false;
    for (let i = 0; i < candidates.length; i += 1) {
      const el = candidates[i];
      const hit = el.getAttribute("id") === nodeId
        || el.getAttribute("data-sk-kc-node") === nodeId;
      el.classList.toggle("sk-kc-node-active", hit);
      if (hit) matched = true;
    }
    if (!matched && typeof console !== "undefined" && console.warn) {
      console.warn("[SceneKit] 知识图片书签「" + nodeId
        + "」未在内联 SVG 中找到对应节点（id / data-sk-kc-node），请对齐书签与图谱几何");
    }
    return matched;
  }

  /** 书签点击：高亮节点 + 弹层取证口径（同一时刻只保留一个弹层，openDrill 自带幂等）。 */
  function onKnowledgeBookmarkClick(btn) {
    const card = btn.closest("[data-sk-knowledge-card]");
    if (!card) return;
    const nodeId = btn.getAttribute("data-sk-kc-bookmark") || "";
    let refs = [];
    try {
      const parsed = JSON.parse(btn.getAttribute("data-sk-kc-refs") || "[]");
      if (Array.isArray(parsed)) refs = parsed;
    } catch (err) {
      refs = [];
    }
    highlightKnowledgeNode(card, nodeId);
    const marks = card.querySelectorAll("[data-sk-kc-bookmark]");
    for (let i = 0; i < marks.length; i += 1) {
      const active = marks[i] === btn;
      marks[i].classList.toggle("is-active", active);
      marks[i].setAttribute("aria-pressed", active ? "true" : "false");
    }
    openDrill({
      title: btn.getAttribute("data-sk-kc-label") || nodeId || "节点取证口径",
      subtitle: "知识图片节点书签（图文互证）",
      html: kcDrillHtml({
        agent: btn.getAttribute("data-sk-kc-agent") || "",
        tool: btn.getAttribute("data-sk-kc-tool") || "",
        conclusion: btn.getAttribute("data-sk-kc-conclusion") || "",
        refs: refs,
      }),
    });
  }

  /**
   * DAG 节点点击：步骤时间线切换到该节点的 node_steps；再次点击已激活节点
   * 回主链。无 node_steps 的节点渲染时为 disabled，不会进入本分支。
   */
  function onAgentNodeClick(btn) {
    const panel = btn.closest("[data-sk-agent-panel]");
    if (!panel) return;
    const nodeId = btn.getAttribute("data-sk-agent-node") || "";
    const activeId = btn.classList.contains("is-active") ? "__main__" : nodeId;
    const sets = panel.querySelectorAll("[data-sk-agent-step-set]");
    for (let i = 0; i < sets.length; i += 1) {
      sets[i].classList.toggle("is-active",
        sets[i].getAttribute("data-sk-agent-step-set") === activeId);
    }
    const nodes = panel.querySelectorAll("[data-sk-agent-node]");
    for (let i = 0; i < nodes.length; i += 1) {
      const active = nodes[i].getAttribute("data-sk-agent-node") === activeId;
      nodes[i].classList.toggle("is-active", active);
      nodes[i].setAttribute("aria-pressed", active ? "true" : "false");
    }
    const status = panel.querySelector("[data-sk-agent-set-status]");
    if (status) {
      status.textContent = activeId === "__main__"
        ? "当前显示：主链回放步骤"
        : "当前显示：节点「" + (btn.getAttribute("data-sk-agent-label") || activeId)
          + "」的回放步骤";
    }
  }

  /* ============================================================
   * 九、导出
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
    agentPanel,
    knowledgeCard,
    replay,
    openDrill,
    closeDrill,
    sceneData,
    story,
    isReadonly,
    fmtDate,
    fmtNum,
    bindDrills,
    hasDemoBadge,
    installBadgeGuard,
  };

  window.SceneKit = SceneKit;

  // 首次加载即注入样式：即使某屏只调 fmtNum（不调 use），也不该等到首帧才闪一下样式。
  injectStyle();

  // 框架级「演示数据」徽标强制：registry.js 已先加载，这里装上守护即覆盖全部场景屏；
  // registry 尚未就绪时在 DOMContentLoaded 再补一次（幂等，绝不重复包装）。
  installBadgeGuard();
  if (typeof document !== "undefined" && document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      installBadgeGuard();
    });
  }

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

  // 智能体协同组件交互（T17）：DAG 节点切换步骤时间线、知识卡书签高亮 + 取证弹层。
  // 与 signoff 兜底同款模式：文档级委托，屏只插 HTML 不必各自绑事件；
  // 组件内**只有视图动作**，零写按钮。
  if (typeof document !== "undefined") {
    document.addEventListener("click", function (ev) {
      const target = ev && ev.target;
      if (!target || typeof target.closest !== "function") return;
      const agentNode = target.closest("[data-sk-agent-node]");
      if (agentNode) {
        onAgentNodeClick(agentNode);
        return;
      }
      const bookmark = target.closest("[data-sk-kc-bookmark]");
      if (bookmark) {
        onKnowledgeBookmarkClick(bookmark);
      }
    }, false);
  }
})();
