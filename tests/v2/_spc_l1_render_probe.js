/**
 * SPC L1 渲染探针：把 screens.js 里的 `spcBody` 单独跑起来，输出**真实渲染后的 HTML**。
 *
 * 用途（QA 回归守卫）：
 * 「SPC 2 级看板入口可达性」不能靠正则猜源码 —— `mfgKpiCard` 的 `data-kpi` 是
 * 动态拼的（`${UI.esc(kpi.key)}`），源码里根本没有字面量 "spc_capability"。
 * 所以这里**真渲染一次**，把 HTML 吐给 pytest 去断言「存在一个能被 L2 链捕获的元素」。
 *
 * 用法：node spc_l1_render_probe.js <payload.json> <out.html>
 */
"use strict";
const fs = require("fs");
const path = require("path");

const SCREENS = path.join(__dirname, "..", "..", "src", "mfg_agents", "v2", "demo", "ui", "screens.js");
const payloadPath = process.argv[2];
const outPath = process.argv[3];

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

/* ---- 最小 UI 替身：只提供 screens.js 的 spcBody 路径用到的 API ---- */
const UI = {
  esc: (v) => String(v === null || v === undefined ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;"),
  icon: (name, size) => `<i class="ic ic-${name}" style="width:${size || 14}px"></i>`,
  badge: (text, cls) => `<span class="${cls || "badge"}">${UI.esc(text)}</span>`,
  demoFlag: (text) => `<span class="demo-flag">${UI.esc(text)}</span>`,
};

/* ---- 把 screens.js 里 spcBody 依赖的函数抠出来，单独求值 ---- */
const src = fs.readFileSync(SCREENS, "utf8");

/** 抠出某个顶层函数（含其前置的同级辅助常量）——按函数名定位 + 花括号配平。 */
function extractFn(name) {
  const re = new RegExp("function\\s+" + name + "\\s*\\(");
  const m = re.exec(src);
  if (!m) throw new Error("screens.js 里找不到函数 " + name);
  const start = m.index;
  const openAt = src.indexOf("{", m.index + m[0].length - 1);
  let depth = 0;
  let i = openAt;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") { depth--; if (depth === 0) { i++; break; } }
  }
  return src.slice(start, i);
}

/* spcBody 依赖：mfgKpiCard / mfgSpark / spcCapCard / spcParetoCard /
 * mfgRankCard / mfgNotes / drillEntity / MFG_TONE / MFG_KPI_ICON / MFG_BADGE / SPC_VERDICT */
const pieces = [
  "const MFG_TONE = {ok:'ok',warn:'warn',danger:'danger',empty:'gray'};",
  "const MFG_BADGE = {ok:'badge-ok',warn:'badge-warn',danger:'badge-danger',empty:'badge-gray'};",
  "const MFG_KPI_ICON = {};",
  "const SPC_VERDICT = {合格:'ok',偏低:'warn',不合格:'danger',不可判定:'empty'};",
  extractFn("drillEntity"),
  extractFn("mfgSpark"),
  extractFn("mfgKpiCard"),
  extractFn("mfgRankCard"),
  extractFn("mfgNotes"),
  extractFn("spcCapCard"),
  extractFn("spcParetoCard"),
  extractFn("spcBody"),
];

const runner = pieces.join("\n\n") + `
;globalThis.__spcBody = spcBody;
;globalThis.__UI = UI;
`;

const vm = require("vm");
const sandbox = { UI, console, JSON, String, Object, Array, Number, Math, Date };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(runner, sandbox, { filename: "spc_l1_probe.js" });

const html = sandbox.__spcBody(UI, payload);
fs.writeFileSync(outPath, html, "utf8");
process.stdout.write(String(html.length) + "\n");
