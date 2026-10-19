/**
 * S4 · 批量监控与 ECN 场景屏（T09 交付物）
 *
 * 交付物清单（对应计划书 T09 三件）：
 *  1. 本文件 `src/mfg_agents/v2/demo/ui/scenes/s04-batch-ecn.js`：自注册到
 *     `window.SceneRegistry`（**不改 app.js / 总导航**），渲染 S4 屏；
 *  2. `data/demo_storyline/frontend/s04-batch-ecn.js`：场景派生数据单一事实源
 *     （本文件内 `INLINE_SCENE_DATA` 为其镜像常量）；
 *  3. `tests/demo/test_s04.py`：数据契约 + 注册契约用例。
 *
 * 承载主线（spec-v1.md §3 节点 #8–#12、§4 支线 A）：
 *  - SPC 批量监控：30 天炉温序列、2 个判异点（EV-0013 / EV-0019），判异即时
 *    推送处置会签（拉群 → 取证 → 方案 → 会签 → 闭环），复用真实 SPC 智能体链路口径；
 *  - 支线 A：ECN-DEMO-001（U2201 停产风险换料）影响面四象限
 *    （在制工单 / 库存批次 / 在途批次 / 未发订单行）逐行可下钻，
 *    切换会签与 MBOM v1.1 生效沿主线 60% 进度点对齐。
 *
 * 三条纪律：
 *  1. **只读不抄**：公共故事数据一律经 `SceneKit.story('<object>')`（T05 契约，
 *     打 T04 装载层 `GET /api/v2/demo/objects/{name}`，失败回落 `window.DEMO_STORY`）
 *     现取现算，本文件不写任何数字快照；
 *  2. **不编数字**：装载层未就绪时显示降级提示（`smallDegrade` / `degradeHtml`），
 *     绝不出现编造的曲线、事件或影响面数据；拿不到的行只标注引用来源；
 *  3. **口径可下钻**：每个数字、每行影响面明细都可点到口径与原始记录出处
 *     （`data-sk-drill` + `SceneKit.openDrill`），判异处置会签经「处置会签」按钮
 *     一步可达（≤3 步验收）。
 *
 * 数据出处见上游 `data/demo_storyline/frontend/s04-batch-ecn.js` 头注释。
 */
(function () {
  "use strict";

  const SCENE_ID = "s04";

  /** 本屏消费的故事对象（键与 registry.json → objects 一致）。 */
  const STORY_OBJECTS = [
    "spc_series", "events", "work_orders", "lots", "boms",
    "sales_orders", "materials", "equipment",
  ];

  /**
   * 场景派生数据镜像常量——与 data/demo_storyline/frontend/s04-batch-ecn.js
   * 中 `window.SCENE_DATA["s04"]` **严格一致**（逐值深比对，由
   * tests/demo/test_s04.py 守护，两处漂移即变红）。
   * 之所以在此镜像一份：`/demo-story/{file}` 挂载点已由 T10 落地（数据文件可经 HTTP
   * 取到），但页面当前不会自动注入场景数据脚本，屏内内联镜像作为兜底；与
   * S2（T10）、S3（T11）同法——契约文件始终是唯一权威副本。
   */
  const INLINE_SCENE_DATA = {
    "scene": "s04",
    "title": "S4 · 批量监控与 ECN",
    "sub": "30 天 SPC 判异处置会签 · 支线 A 换料影响面四象限",
    "spec_ref": "spec-v1.md §3 节点 #8–#12 · §4 五角色视图（gm/rd/qa/mfg）· §5 界面数字台账",
    "object_source": ["spc_series", "events", "work_orders", "lots", "boms", "sales_orders", "materials", "equipment"],
    "value_anchors": [
      "异常不过夜：判异自动拉群取证，处置方案带着成本影响送到会签人。",
      "变更影响面从几天缩短到小时级，该停的没停、该切的没切不再靠 Excel。"
    ],
    "spc_limits": {
      "equipment_id": "DEMO-EQ-002",
      "param": "炉温",
      "unit": "℃",
      "digits": 1,
      "target": 245.0,
      "tolerance": 6.0,
      "source": "DEMO-EV-0013 payload（target / tolerance）；序列点取自 spc_series.jsonl（30 点）"
    },
    "disposition_chain": ["拉群", "取证", "方案", "会签", "闭环"],
    "disposition_status_labels": { "done": "已完成", "doing": "进行中", "absent": "未留痕" },
    "alerts": [
      {
        "alert_id": "DEMO-ALERT-001",
        "equipment_id": "DEMO-EQ-002",
        "param": "炉温",
        "unit": "℃",
        "digits": 1,
        "value": 258.4,
        "target": 245.0,
        "tolerance": 6.0,
        "ts": "2026-12-02T09:00:00",
        "closure_ts": "2026-12-03T09:00:00",
        "event_refs": ["DEMO-EV-0013", "DEMO-EV-0014", "DEMO-EV-0015"],
        "steps": ["done", "absent", "absent", "doing", "done"],
        "step_evidence": {
          "拉群": "DEMO-EV-0013（alert / kind=spc，判异推送即拉起处置群，演示口径）",
          "会签": "DEMO-EV-0014（hitl，槽位 pqe / pe，结果处置中）",
          "闭环": "DEMO-EV-0015（alert / kind=closed，判异处置闭环）"
        },
        "signoff_slots": ["pqe", "pe"],
        "signoff_result": "处置中",
        "signoff_ts": "2026-12-02T09:00:00",
        "note": "SMT 炉温偏离：判异即时推送处置会签，次日闭环。"
      },
      {
        "alert_id": "DEMO-ALERT-002",
        "equipment_id": "DEMO-EQ-006",
        "param": "ICT 误判率",
        "unit": "比率",
        "digits": 4,
        "value": 0.031,
        "target": 0.01,
        "tolerance": 0.005,
        "ts": "2026-12-10T09:00:00",
        "closure_ts": "2026-12-11T09:00:00",
        "event_refs": ["DEMO-EV-0019", "DEMO-EV-0020"],
        "steps": ["done", "absent", "absent", "absent", "done"],
        "step_evidence": {
          "拉群": "DEMO-EV-0019（alert / kind=spc，判异推送，演示口径）",
          "闭环": "DEMO-EV-0020（alert / kind=closed，判异处置闭环）"
        },
        "signoff_slots": [],
        "signoff_result": "",
        "signoff_ts": "",
        "equipment_note": "口径差异（原样呈现，不擅自改绑）：事件 object_ref = DEMO-EQ-006（equipment.jsonl 记为「功能测试台」），而 payload.param 写「ICT 误判率」（DEMO-EQ-005 才是「ICT 测试仪」）。演示包未给精确的 (设备, 参数) 绑定。",
        "note": "ICT 误判率异常：处置闭环。演示数据未为该判异生成独立会签事件，会签槽位留空。"
      }
    ],
    "ecn": {
      "ecn_id": "DEMO-ECN-001",
      "order_id": "DEMO-SO-001",
      "reason": "U2201 停产风险换料",
      "old_material": "DEMO-MAT-2201",
      "new_material": "DEMO-MAT-2202",
      "bom_id": "DEMO-BOM-M5001",
      "old_bom_version": "v1.0",
      "new_bom_version": "v1.1",
      "triggered_at": "2026-12-05T09:00:00",
      "signoff_ts": "2026-12-06T09:00:00",
      "effective_ts": "2026-12-07T09:00:00",
      "event_refs": ["DEMO-EV-0016", "DEMO-EV-0017", "DEMO-EV-0018"],
      "signoff_slots": ["rd", "pe", "pqe"],
      "signoff_result": "approved",
      "quadrant_titles": {
        "wos": "在制工单",
        "stock_lots": "库存批次",
        "transit_lots": "在途批次",
        "open_lines": "未发订单行"
      },
      "impact": {
        "wos": ["DEMO-WO-002", "DEMO-WO-003"],
        "stock_lots": ["DEMO-LOT-003"],
        "transit_lots": ["DEMO-LOT-004"],
        "open_lines": [{ "order_id": "DEMO-SO-001", "material_id": "DEMO-MAT-5001", "qty": 5000 }]
      },
      "impact_counts": { "wos": 2, "stock_lots": 1, "transit_lots": 1, "open_lines": 1 },
      "impact_payload_note": "DEMO-EV-0016 payload 中 impact_wos 直接给工单清单（DEMO-WO-002 / DEMO-WO-003）；impact_stock_lots / impact_transit_lots / impact_open_lines 给的是数量（各 1），对应清单由本屏按 lots（持旧料 DEMO-MAT-2201 的库存批 / 在途新料批）与 sales_orders（未发订单行）现算归集，并与 payload 数量逐项对账（tests/demo/test_s04.py）。",
      "impact_sources": {
        "wos": "DEMO-EV-0016 payload.impact_wos（事件直给：DEMO-WO-002 / DEMO-WO-003）",
        "stock_lots": "lots 中唯一持有旧料 DEMO-MAT-2201 的库存批（DEMO-LOT-003，原料仓-B01，qty 1400）",
        "transit_lots": "lots 中唯一在途批（DEMO-LOT-004，location 以「在途」开头，且为新料 DEMO-MAT-2202）",
        "open_lines": "主线订单 DEMO-SO-001 的未发订单行（sales_orders.lines：DEMO-MAT-5001 × 5000）"
      },
      "progress": {
        "spec_point": 0.6,
        "spec_label": "60%",
        "spec_ref": "spec-v1.md §1 支线 A「批量进行到 60% 时」· §3 节点 #10–#11",
        "note": "演示包不含逐时进度序列：ECN 触发时点按 spec 的 60% 进度点标定；屏上同时给出按报工数据的重算值（主线工单 qty_good 合计 ÷ qty_planned 合计），不把差异抹平成 60%。"
      }
    },
    "signoff_slot_labels": {
      "rd": "研发(RD)",
      "pe": "工艺工程(PE)",
      "pqe": "品质工程(PQE)"
    },
    "signoff_users": {
      "rd": "陈晨",
      "pe": "郑昊",
      "pqe": "赵拓"
    }
  };

  const STYLE_ID = "s04-style";

  /** 当前屏的口径字典（键 = data-sk-drill，值 = 返回 openDrill 入参的函数）。 */
  let drills = {};

  /** 最近一次取数的来源（用于口径文案标注）。 */
  let lastSource = {};

  /* ============================================================
   * 一、样式（一次性注入，颜色只取 app.css 变量）
   * ============================================================ */

  const CSS = `
.s04-row { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px;
  border-bottom: 1px dashed var(--border); font-size: 12.6px; color: var(--text); }
.s04-row:last-child { border-bottom: none; }
.s04-row.is-clickable { cursor: pointer; border-radius: var(--radius-sm); transition: background .15s ease; }
.s04-row.is-clickable:hover { background: var(--primary-light-2); }
.s04-row.is-clickable:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.s04-row-id { flex: none; width: 134px; font-weight: 600; color: var(--text); word-break: break-all; }
.s04-row-main { flex: 1; min-width: 0; }
.s04-row-sub { font-size: 11.5px; color: var(--text-3); margin-top: 2px; line-height: 1.55; }
.s04-row-tip { flex: none; font-size: 10.5px; color: var(--text-3); padding-top: 2px; }
.s04-quad { display: grid; gap: 14px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.s04-chain { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; }
.s04-step { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
  border-radius: 6px; font-size: 11.5px; border: 1px solid var(--border);
  background: var(--card); color: var(--text-2); }
.s04-step.is-done { border-color: var(--ok); color: var(--ok); background: var(--ok-bg); }
.s04-step.is-doing { border-color: var(--warn); color: var(--warn); background: var(--warn-bg); }
.s04-arrow { color: var(--text-3); font-size: 12px; }
.s04-evidence { font-size: 11.5px; color: var(--text-3); line-height: 1.65; margin-top: 6px; }
.s04-anchor { font-size: 11.5px; color: var(--text-2); line-height: 1.7; }
.s04-anchor b { color: var(--text); }
@media (max-width: 760px) {
  .s04-quad { grid-template-columns: minmax(0, 1fr); }
  .s04-row-id { width: 110px; }
}
`;

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
   * 二、小工具
   * ============================================================ */

  function kit() {
    return window.SceneKit;
  }

  /** 口径表：[[键, 值], ...]（值一律走 esc）。 */
  function kv(pairs) {
    const K = kit();
    const rows = (pairs || []).map(function (pair) {
      const key = Array.isArray(pair) ? pair[0] : "";
      const val = Array.isArray(pair) ? pair[1] : "";
      const text = (val === null || val === undefined || val === "") ? "—" : String(val);
      return '<div class="sk-kv-row"><span class="sk-kv-k">' + K.esc(key)
        + '</span><span class="sk-kv-v">' + K.esc(text) + "</span></div>";
    }).join("");
    return '<div class="sk-kv">' + rows + "</div>";
  }

  function rowsOf(D, name) {
    if (!D || !D.rows || !Array.isArray(D.rows[name])) return null;
    return D.rows[name];
  }

  function hasRows(list) {
    return Array.isArray(list) && list.length > 0;
  }

  function findById(list, key, id) {
    if (!hasRows(list)) return null;
    for (let i = 0; i < list.length; i += 1) {
      const row = list[i];
      if (row && String(row[key]) === String(id)) return row;
    }
    return null;
  }

  /** 数值按 `digits` 位小数格式化（口径：spec-v1.md §6 数字口径）。 */
  function num(value, digits) {
    const K = kit();
    return K.fmtNum(value, typeof digits === "number" ? digits : 0);
  }

  function materialLabel(materials, id) {
    const row = findById(materials, "material_id", id);
    const name = row && row.name ? String(row.name) : "";
    return name ? (name + " / " + String(id)) : String(id || "—");
  }

  function equipmentLabel(equipment, id) {
    const row = findById(equipment, "equipment_id", id);
    const name = row && row.name ? String(row.name) : "";
    return name ? (name + " / " + String(id)) : String(id || "—");
  }

  /** 工单状态 → 中文（口径与 S4 批量阶段一致）。 */
  const WO_STATUS = { done: "已完工", in_progress: "在制中", released: "已下达" };

  /** 来源标注（T04 装载层端点 / 本地离线回放）。 */
  function sourceLabel(name) {
    return lastSource[name] === "local"
      ? "本地离线回放 window.DEMO_STORY"
      : "GET /api/v2/demo/objects/" + name;
  }

  /** 场景派生数据：优先 window.SCENE_DATA（数据文件已加载时），缺位时登记内联镜像。 */
  function sceneData() {
    const bag = window.SCENE_DATA;
    if (bag && bag[SCENE_ID] && typeof bag[SCENE_ID] === "object") return bag[SCENE_ID];
    if (!bag || typeof bag !== "object") window.SCENE_DATA = {};
    window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA;
    return INLINE_SCENE_DATA;
  }

  function smallDegrade(text) {
    const K = kit();
    return '<div class="sk-degrade">' + K.icon("warn", 15) + "<div>" + K.esc(text) + "</div></div>";
  }

  /* ============================================================
   * 三、取数（唯一入口 SceneKit.story，永不 reject）
   * ============================================================ */

  function loadStory(name) {
    const K = kit();
    return K.story(name).then(function (res) {
      if (res && res.ok === true && hasRows(res.rows)) {
        return { rows: res.rows, source: res.source === "local" ? "local" : "api" };
      }
      return null;
    }).catch(function () { return null; });
  }

  /**
   * 并行取本屏全部对象。
   * @returns {Promise<{ok, rows, source, missing, localCount}>}
   */
  function loadAll() {
    return Promise.all(STORY_OBJECTS.map(function (name) {
      return loadStory(name);
    })).then(function (results) {
      const rows = {};
      const source = {};
      const missing = [];
      STORY_OBJECTS.forEach(function (name, i) {
        const r = results[i];
        if (r && hasRows(r.rows)) {
          rows[name] = r.rows;
          source[name] = r.source;
        } else {
          missing.push(name);
        }
      });
      lastSource = source;
      return {
        ok: Object.keys(rows).length > 0,
        rows: rows,
        source: source,
        missing: missing,
        localCount: Object.keys(source).filter(function (k) { return source[k] === "local"; }).length,
      };
    });
  }

  /* ============================================================
   * 四、SPC 控制图（SVG，坐标逐点计算）
   * ============================================================ */

  function spcChart(series, limits) {
    if (!hasRows(series)) return "";
    const sorted = series.slice().sort(function (a, b) {
      return String(a && a.ts).localeCompare(String(b && b.ts));
    });
    const pts = [];
    sorted.forEach(function (row, i) {
      const v = Number(row && row.value);
      if (Number.isFinite(v)) {
        pts.push({ i: i, v: v, out: String(row.flag) === "out", ts: row.ts });
      }
    });
    if (!pts.length) return "";

    const digits = typeof limits.digits === "number" ? limits.digits : 1;
    const target = Number(limits.target);
    const tol = Number(limits.tolerance);
    const lsl = target - tol;
    const usl = target + tol;

    const values = pts.map(function (p) { return p.v; });
    const lo = Math.min.apply(null, values.concat([lsl])) - tol * 0.2;
    const hi = Math.max.apply(null, values.concat([usl])) + tol * 0.2;
    const span = (hi - lo) || 1;

    const W = 780, H = 274, padL = 62, padR = 20, padT = 20, padB = 42;
    const iw = W - padL - padR;
    const ih = H - padT - padB;
    const n = sorted.length;

    function px(i) { return padL + (n <= 1 ? iw / 2 : iw * i / (n - 1)); }
    function py(v) { return padT + ih * (1 - (v - lo) / span); }

    const parts = [];
    parts.push('<rect x="' + padL + '" y="' + padT + '" width="' + iw + '" height="' + ih
      + '" fill="#FFFFFF" stroke="#E4E9F1" stroke-width="1" rx="4"/>');

    [
      [usl, "#CF222E", "USL " + num(usl, digits)],
      [target, "#1A7F37", "CL " + num(target, digits)],
      [lsl, "#CF222E", "LSL " + num(lsl, digits)]
    ].forEach(function (ref) {
      const y = py(ref[0]).toFixed(1);
      parts.push('<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y
        + '" stroke="' + ref[1] + '" stroke-width="1.1" stroke-dasharray="5 4" opacity="0.72"/>');
      parts.push('<text x="' + (padL - 9) + '" y="' + (Number(y) + 3.6).toFixed(1)
        + '" text-anchor="end" font-size="10.5" fill="' + ref[1] + '">' + ref[2] + "</text>");
    });

    parts.push('<polyline points="' + pts.map(function (p) {
      return px(p.i).toFixed(1) + "," + py(p.v).toFixed(1);
    }).join(" ") + '" fill="none" stroke="#185FA5" stroke-width="1.6" stroke-linejoin="round"/>');

    pts.forEach(function (p) {
      const cx = px(p.i).toFixed(1);
      const cy = py(p.v).toFixed(1);
      if (p.out) {
        parts.push('<circle cx="' + cx + '" cy="' + cy
          + '" r="4.6" fill="#CF222E" stroke="#FFFFFF" stroke-width="1.4"/>');
        parts.push('<text x="' + cx + '" y="' + (Number(cy) - 9).toFixed(1)
          + '" text-anchor="middle" font-size="10" fill="#CF222E">' + num(p.v, digits) + "</text>");
      } else {
        parts.push('<circle cx="' + cx + '" cy="' + cy + '" r="2.6" fill="#185FA5"/>');
      }
    });

    const marks = n <= 1 ? [0] : (n === 2 ? [0, 1] : [0, Math.floor((n - 1) / 2), n - 1]);
    marks.forEach(function (i) {
      const row = sorted[i];
      const label = row && row.ts ? String(row.ts).slice(5, 10) : "";
      parts.push('<text x="' + px(i).toFixed(1) + '" y="' + (H - padB + 19)
        + '" text-anchor="middle" font-size="10.5" fill="#8B98AB">' + label + "</text>");
    });

    return '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;height:auto;display:block"'
      + ' role="img" aria-label="' + kit().esc(limits.param + " SPC 控制图（30 天，含判异点）") + '">'
      + parts.join("") + "</svg>";
  }

  /* ============================================================
   * 五、区块构建
   * ============================================================ */

  /** 话术锚点（spec-v1.md §3.0 / §4 支线 A 演示叙事口径）。 */
  function anchorSection(S) {
    const anchors = Array.isArray(S.value_anchors) ? S.value_anchors : [];
    if (!anchors.length) return "";
    return '<div class="sk-block">'
      + '<div class="section-title">本屏演示锚点<span class="section-extra">话术口径（spec-v1.md §4）</span></div>'
      + '<div class="card"><div style="padding:12px 16px" class="s04-anchor">'
      + anchors.map(function (a) { return "<div>· " + kit().esc(a) + "</div>"; }).join("")
      + "</div></div></div>";
  }

  function kpiSection(S, D) {
    const K = kit();
    const spc = rowsOf(D, "spc_series");
    const wos = rowsOf(D, "work_orders");
    const ecn = S.ecn;
    const impact = ecn.impact;
    const impactTotal = impact.wos.length + impact.stock_lots.length
      + impact.transit_lots.length + impact.open_lines.length;

    const spcCount = hasRows(spc) ? spc.length : null;
    const outRows = hasRows(spc) ? spc.filter(function (r) { return String(r.flag) === "out"; }) : null;
    const outCount = outRows ? outRows.length : null;
    const inOrder = hasRows(wos) ? wos.filter(function (r) {
      return r && String(r.order_id) === String(ecn.order_id);
    }) : null;
    const wipWos = inOrder ? inOrder.filter(function (r) {
      return ["in_progress", "released"].indexOf(String(r.status)) !== -1;
    }) : null;

    drills["kpi-spc"] = function () {
      return {
        title: "SPC 序列点数",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", spcCount === null ? "数据缺失（spc_series 未取到）" : spcCount + " 点"],
          ["口径", "spc_series.jsonl 行数：每行 = 一台设备 + 一个参数 + 一个时间戳"],
          ["来源", sourceLabel("spc_series")],
          ["序列", S.spc_limits.equipment_id + " · " + S.spc_limits.param + "（" + S.spc_limits.unit + "）"],
          ["数据落位", D.source && D.source.spc_series === "local" ? "离线回放包" : "T04 装载层对象端点"],
          ["校验", "tests/demo/test_s04.py：判异点数 = flag == out 的行数"],
        ]),
      };
    };
    drills["kpi-out"] = function () {
      return {
        title: "判异点数",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", outCount === null ? "数据缺失" : outCount + " 个"],
          ["算法", "spc_series[] 中 flag === out 的行数（spec-v1.md §3 节点 #9 / #12：序列含 2 个判异点）"],
          ["判异点", outRows && outRows.length
            ? outRows.map(function (r) {
              return String(r.ts).slice(5, 10) + " = " + num(r.value, Number(S.spc_limits.digits));
            }).join("；")
            : "（无）"],
          ["处置事件", "2 次（DEMO-EV-0013 炉温 / DEMO-EV-0019 ICT 误判率），见下方「判异处置」区"],
          ["两组数据源", "序列判异点（2026-09-20 ~ 2026-10-19）与处置事件（批量阶段 2026-12-02 / 12-10）"
            + "在演示包中是两组独立数据，本屏只按各自口径展示，不强行对齐"],
          ["来源", sourceLabel("spc_series")],
        ]),
      };
    };
    drills["kpi-wip"] = function () {
      return {
        title: "在制工单数",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", inOrder === null ? "数据缺失" : wipWos.length + " 张"],
          ["算法", "work_orders[] 中 order_id = " + ecn.order_id + " 且 status ∈ {in_progress, released} 的行数"],
          ["命中工单", wipWos && wipWos.length
            ? wipWos.map(function (r) { return String(r.wo_id) + "（" + String(r.status) + "）"; }).join("、")
            : "（无）"],
          ["来源", sourceLabel("work_orders")],
          ["联动", "与「支线 A」四象限的「在制工单」格同源同口径"],
        ]),
      };
    };
    drills["kpi-ecn"] = function () {
      return {
        title: "ECN 影响面合计",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", impactTotal + " 项"],
          ["算法", "在制工单 " + impact.wos.length + " + 库存批次 " + impact.stock_lots.length
            + " + 在途批次 " + impact.transit_lots.length + " + 未发订单行 " + impact.open_lines.length],
          ["出处", "spec-v1.md §5「ECN 影响面 = 在制工单数 + 库存批次 + 在途批 + 未发订单行」"],
          ["事件快照", (S.ecn && S.ecn.impact_payload_note) ? S.ecn.impact_payload_note : ""],
          ["口径监督", "tests/demo/test_s04.py 断言：" + JSON.stringify(ecn.impact_counts)
            + " 与事件 payload 计数、四象限清单行数三者一致"],
        ]),
      };
    };

    const cards = [
      {
        label: "SPC 序列点数", value: spcCount === null ? "—" : K.fmtNum(spcCount), unit: "点",
        hint: "口径：spc_series 行数（30 天序列）", tone: "blue", drill: "kpi-spc",
      },
      {
        label: "判异点数", value: outCount === null ? "—" : K.fmtNum(outCount), unit: "个",
        hint: "口径：flag === out 的行数", tone: "red", drill: "kpi-out",
      },
      {
        label: "在制工单数", value: inOrder === null ? "—" : K.fmtNum(wipWos.length), unit: "张",
        hint: "口径：主线订单 status ∈ 在制/已下达", tone: "amber", drill: "kpi-wip",
      },
      {
        label: "ECN 影响面合计", value: K.fmtNum(impactTotal), unit: "项",
        hint: "口径：四象限数量之和", tone: "purple", drill: "kpi-ecn",
      },
    ];
    return K.numberRow(cards);
  }

  function spcSection(S, D) {
    const K = kit();
    const series = rowsOf(D, "spc_series");
    const limits = S.spc_limits;
    if (!hasRows(series)) {
      return '<div class="sk-block"><div class="section-title">SPC 批量监控（30 天）</div>'
        + smallDegrade("spc_series 未取到：需要装载层提供 30 天炉温序列。本屏不显示编造曲线。")
        + "</div>";
    }
    const outs = series.filter(function (r) { return String(r.flag) === "out"; });
    const seriesTs = series.map(function (r) { return String(r && r.ts).slice(0, 10); }).sort();

    drills["spc-chart"] = function () {
      return {
        title: "炉温 SPC 控制图口径",
        subtitle: "控制限 · 判异 · 数据来源",
        html: kv([
          ["设备 / 参数", limits.equipment_id + " · " + limits.param + "（" + limits.unit + "）"],
          ["控制限", "CL = " + num(limits.target, Number(limits.digits))
            + "，LSL = " + num(Number(limits.target) - Number(limits.tolerance), Number(limits.digits))
            + "，USL = " + num(Number(limits.target) + Number(limits.tolerance), Number(limits.digits))],
          ["算法", "控制限 = target ± tolerance（target / tolerance 取自 DEMO-EV-0013 payload）"],
          ["判异", "序列中 flag === out 的点标红放大（本屏共 " + outs.length + " 点："
            + (outs.length ? outs.map(function (r) {
              return String(r.ts).slice(0, 10) + " = " + num(r.value, Number(limits.digits));
            }).join("、") : "无") + "）"],
          ["序列范围", seriesTs[0] + " ~ " + seriesTs[seriesTs.length - 1]
            + "（每日 09:00 一点，共 " + series.length + " 点）"],
          ["两组数据源", "序列（含上述判异点）与「判异处置事件」（DEMO-EV-0013 / 0019，批量阶段）"
            + "在演示包中是两组独立数据源，本屏不把二者强行对齐"],
          ["来源", sourceLabel("spc_series")],
        ]),
      };
    };

    return '<div class="sk-block">'
      + '<div class="section-title">SPC 批量监控（30 天 · ' + K.esc(limits.param) + "）"
      + '<span class="section-extra">判异 ' + outs.length + " 点 · " + K.esc(limits.equipment_id) + "</span></div>"
      + '<div class="card">'
      + '<div class="card-head"><div class="card-title">' + K.icon("activity", 15)
      + "SPC 控制图（判异点标红）</div>"
      + '<div class="card-extra"><button class="btn btn-sm btn-outline" type="button"'
      + ' data-sk-drill="spc-chart">口径</button></div></div>'
      + '<div style="padding:10px 14px 6px">' + spcChart(series, limits) + "</div>"
      + "</div>"
      + '<div class="sk-note">序列判异点：' + (outs.length
        ? outs.map(function (r) {
          return String(r.ts).slice(0, 10) + "（" + num(r.value, Number(limits.digits)) + "）";
        }).join("、")
        : "无")
      + "。判异处置事件（DEMO-EV-0013 / 0019）发生在批量阶段，与序列为演示包中两组独立数据源，"
      + "本屏分别按各自口径展示（口径：spec-v1.md §3 节点 #9 / #12）。</div>"
      + "</div>";
  }

  function chainHtml(S, alert) {
    const K = kit();
    const labels = Array.isArray(S.disposition_chain) ? S.disposition_chain : [];
    const statusMap = S.disposition_status_labels || {};
    const steps = Array.isArray(alert.steps) ? alert.steps : [];
    const parts = [];
    labels.forEach(function (label, i) {
      const status = String(steps[i] || "absent");
      const cls = "s04-step" + (status === "done" ? " is-done" : (status === "doing" ? " is-doing" : ""));
      parts.push('<span class="' + cls + '">' + K.esc(label) + " · "
        + K.esc(statusMap[status] || status) + "</span>");
      if (i < labels.length - 1) parts.push('<span class="s04-arrow">→</span>');
    });
    return '<div class="s04-chain">' + parts.join("") + "</div>";
  }

  function evidenceHtml(alert) {
    const K = kit();
    const ev = alert.step_evidence || {};
    const keys = Object.keys(ev);
    if (!keys.length) return "";
    return '<div class="s04-evidence">留痕：' + keys.map(function (k) {
      return "<b>" + K.esc(k) + "</b> ← " + K.esc(ev[k]);
    }).join("；") + "。未出现事件的步骤标「未留痕」，不推断为已完成。</div>";
  }

  function alertSlots(S, alert) {
    const labels = S.signoff_slot_labels || {};
    const users = S.signoff_users || {};
    const slots = Array.isArray(alert.signoff_slots) ? alert.signoff_slots : [];
    const result = String(alert.signoff_result || "");
    const approvedAll = result === "approved";
    const inProgress = result === "处置中";
    return slots.map(function (code, i) {
      let status = "pending";
      if (approvedAll) status = "approved";
      else if (inProgress && i === 0) status = "approved";
      return {
        role: labels[code] || code,
        user: users[code] || "—",
        status: status,
        ts: status === "approved" ? alert.signoff_ts : "",
      };
    });
  }

  function alertSection(S, D) {
    const K = kit();
    const equipment = rowsOf(D, "equipment");
    const alerts = Array.isArray(S.alerts) ? S.alerts : [];
    if (!alerts.length) return "";

    const blocks = alerts.map(function (a) {
      const digits = typeof a.digits === "number" ? a.digits : 1;
      const slots = Array.isArray(a.signoff_slots) ? a.signoff_slots : [];
      const statusText = a.signoff_result ? String(a.signoff_result) : "未设会签";
      const drillKey = "alert-signoff-" + a.alert_id;

      drills[drillKey] = function () {
        const pairs = [
          ["判异工单", a.alert_id],
          ["设备 / 参数", equipmentLabel(equipment, a.equipment_id) + " · " + a.param],
          ["实测 / 目标 / 允差", num(a.value, digits) + " / " + num(a.target, digits)
            + " / ±" + num(a.tolerance, digits)],
          ["判异时间", K.fmtDate(a.ts)],
          ["闭环时间", K.fmtDate(a.closure_ts)],
          ["处置链", (Array.isArray(S.disposition_chain) ? S.disposition_chain : []).join(" → ")],
          ["会签结论", statusText],
          ["留痕事件", (a.event_refs || []).join("、")],
          ["写操作", "本屏为只读演示：会签写操作由真实 HITL 链路承接，本卡不落库"],
        ];
        if ((a.event_refs || []).length && hasRows(rowsOf(D, "events"))) {
          const related = rowsOf(D, "events").filter(function (e) {
            return e && (a.event_refs || []).indexOf(String(e.event_id)) !== -1;
          });
          if (related.length) {
            pairs.push(["事件明细", related.map(function (e) {
              return String(e.event_id) + " " + String(e.type) + " @ " + K.fmtDate(e.ts);
            }).join("；")]);
          }
        }
        if (a.equipment_note) pairs.push(["设备-参数口径差异", a.equipment_note]);
        return {
          title: "判异处置会签 · " + a.alert_id,
          subtitle: "口径 · 留痕 · 会签槽位（≤3 步可达）",
          html: kv(pairs)
            + '<div style="margin-top:12px">'
            + (slots.length
              ? K.signoffCard({
                title: "会签槽位（" + a.alert_id + "）",
                slots: alertSlots(S, a),
                readonly: true,
                note: "会签人名为虚构演示人名（tools/demo_gen/wordlist.json）；写操作不在本屏落库。",
              })
              : smallDegrade("该判异在演示数据中无独立会签事件（DEMO-EV-0019/0020 仅含判异与闭环），槽位留空，不补造。"))
            + "</div>",
        };
      };

      return '<div class="card" style="margin-top:12px">'
        + '<div class="card-head"><div class="card-title">' + K.icon("alert", 15)
        + K.esc(a.param + " 判异 · " + equipmentLabel(equipment, a.equipment_id)) + "</div>"
        + '<div class="card-extra">' + K.badge(statusText, statusText === "approved" ? "badge-green" : (statusText === "处置中" ? "badge-amber" : "badge-gray"))
        + '<button class="btn btn-sm btn-outline" type="button" data-sk-drill="' + K.esc(drillKey)
        + '">处置会签</button></div></div>'
        + '<div style="padding:10px 16px 14px">'
        + '<div class="sk-num-hint">实测 ' + K.esc(num(a.value, digits)) + " " + K.esc(a.unit)
        + "，目标 " + K.esc(num(a.target, digits)) + "，允差 ±" + K.esc(num(a.tolerance, digits))
        + "；判异 " + K.esc(K.fmtDate(a.ts)) + "，闭环 " + K.esc(K.fmtDate(a.closure_ts)) + "。"
        + K.esc(a.note) + "</div>"
        + '<div style="margin:10px 0 2px">' + chainHtml(S, a) + "</div>"
        + evidenceHtml(a)
        + (a.equipment_note
          ? '<div class="s04-evidence">' + K.esc(a.equipment_note) + "</div>"
          : "")
        + "</div></div>";
    }).join("");

    return '<div class="sk-block">'
      + '<div class="section-title">判异处置（拉群 → 取证 → 方案 → 会签 → 闭环）'
      + '<span class="section-extra">判异 ' + alerts.length + " 次 · 点击「处置会签」查看会签槽位</span></div>"
      + blocks + "</div>";
  }

  function quadrantRows(S, D, key) {
    const K = kit();
    const ecn = S.ecn;
    const wos = rowsOf(D, "work_orders");
    const lots = rowsOf(D, "lots");
    const orders = rowsOf(D, "sales_orders");
    const materials = rowsOf(D, "materials");
    const ids = (ecn.impact && Array.isArray(ecn.impact[key])) ? ecn.impact[key] : [];
    const rows = [];

    if (key === "wos") {
      ids.forEach(function (id) {
        const row = findById(wos, "wo_id", id);
        const status = row ? String(row.status) : "";
        rows.push({
          id: id, drill: "ecn-wos-" + id,
          main: row ? (WO_STATUS[status] || status) : "装载层未返回该工单",
          sub: row
            ? "物料 " + materialLabel(materials, row.material_id)
              + " · 直通 " + num(row.qty_good) + " / 计划 " + num(row.qty_planned)
            : "引用来源：DEMO-EV-0016 payload.impact_wos（不补造明细）",
          detail: row,
        });
      });
    } else if (key === "stock_lots" || key === "transit_lots") {
      ids.forEach(function (id) {
        const row = findById(lots, "lot_id", id);
        rows.push({
          id: id, drill: "ecn-lot-" + id,
          main: row ? "库位 " + String(row.location) : "装载层未返回该批次",
          sub: row
            ? "物料 " + materialLabel(materials, row.material_id)
              + " · 数量 " + num(row.qty)
              + (row.wo_id && String(row.wo_id) !== "-" ? " · 来源工单 " + String(row.wo_id) : "")
            : "引用来源：" + ecn.impact_sources[key],
          detail: row,
        });
      });
    } else {
      ids.forEach(function (line) {
        const order = findById(orders, "order_id", line && line.order_id);
        rows.push({
          id: line && line.order_id, drill: "ecn-line-" + (line && line.order_id),
          main: order ? "订单状态 " + String(order.status) : "装载层未返回该订单",
          sub: "未发订单行 · 物料 " + materialLabel(materials, line && line.material_id)
            + " · 数量 " + num(line && line.qty)
            + "（" + ecn.impact_sources.open_lines + "）",
          detail: order,
        });
      });
    }

    return rows.map(function (r) {
      drills[r.drill] = function () {
        const pairs = [
          ["对象", r.id],
          ["象限", ecn.quadrant_titles[key]],
          ["推算出处", ecn.impact_sources[key]],
        ];
        if (r.detail) {
          Object.keys(r.detail).forEach(function (k) {
            const v = r.detail[k];
            let text;
            if (v === null || v === undefined) text = "—";
            else if (typeof v === "object") text = JSON.stringify(v);
            else text = String(v);
            pairs.push([k, text]);
          });
          pairs.push(["原始记录来源", sourceLabel(
            key === "wos" ? "work_orders" : (key === "open_lines" ? "sales_orders" : "lots"))]);
        } else {
          pairs.push(["明细状态", "装载层未返回该对象：" + r.sub]);
        }
        return {
          title: "影响面明细 · " + r.id,
          subtitle: "象限 · 推算出处 · 原始记录逐字段回指",
          html: kv(pairs),
        };
      };

      return '<div class="s04-row is-clickable" data-sk-drill="' + K.esc(r.drill)
        + '" role="button" tabindex="0" aria-label="' + K.esc(String(r.id) + " 影响面明细，点击查看原始记录") + '">'
        + '<span class="s04-row-id">' + K.esc(r.id) + "</span>"
        + '<span class="s04-row-main"><span>' + K.esc(r.main) + "</span>"
        + '<div class="s04-row-sub">' + K.esc(r.sub) + "</div></span>"
        + '<span class="s04-row-tip">明细</span></div>';
    }).join("") || '<div class="empty">该象限无影响对象</div>';
  }

  function progressHtml(S, D) {
    const K = kit();
    const wos = rowsOf(D, "work_orders");
    const ecn = S.ecn;
    const specLabel = (ecn.progress && ecn.progress.spec_label) ? ecn.progress.spec_label : "60%";
    let recompute = "数据缺失：work_orders 未取到，无法重算进度";
    let planned = 0;
    let good = 0;
    if (hasRows(wos)) {
      const inOrder = wos.filter(function (r) {
        return r && String(r.order_id) === String(ecn.order_id);
      });
      inOrder.forEach(function (r) {
        planned += Number(r.qty_planned) || 0;
        good += Number(r.qty_good) || 0;
      });
      if (planned > 0) {
        recompute = "已报工 " + num(good) + " / 计划 " + num(planned)
          + " = " + (good * 100 / planned).toFixed(1) + "%";
      }
    }

    drills["ecn-progress"] = function () {
      return {
        title: "ECN 触发时点 · " + specLabel + " 进度点",
        subtitle: "口径 · 重算 · 差异说明",
        html: kv([
          ["台账口径", specLabel + " 进度点（" + (ecn.progress ? ecn.progress.spec_ref : "") + "）"],
          ["触发时间", K.fmtDate(ecn.triggered_at)],
          ["触发时工单态", hasRows(wos)
            ? wos.filter(function (r) {
              return r && String(r.order_id) === String(ecn.order_id);
            }).map(function (r) {
              return String(r.wo_id) + " " + String(r.status);
            }).join("、")
            : "数据缺失"],
          ["可见数据重算", recompute + "（主线工单 qty_good 合计 ÷ qty_planned 合计）"],
          ["差异说明", (ecn.progress ? ecn.progress.note : "")],
          ["来源", sourceLabel("work_orders")],
        ]),
      };
    };

    return '<div class="sk-num-hint" style="margin:2px 0 10px">触发时点按 spec 的 '
      + K.esc(specLabel) + " 进度点标定（" + K.esc(K.fmtDate(ecn.triggered_at))
      + "）；可见报工重算 " + K.esc(recompute)
      + '<button class="btn btn-sm btn-outline" type="button" style="margin-left:8px"'
      + ' data-sk-drill="ecn-progress">进度口径</button></div>';
  }

  function ecnSection(S, D) {
    const K = kit();
    const ecn = S.ecn;
    const materials = rowsOf(D, "materials");
    const events = rowsOf(D, "events");
    const quadKeys = ["wos", "stock_lots", "transit_lots", "open_lines"];

    const quadCards = quadKeys.map(function (key) {
      const ids = (ecn.impact && Array.isArray(ecn.impact[key])) ? ecn.impact[key] : [];
      return '<div class="card">'
        + '<div class="card-head"><div class="card-title">' + K.icon("box", 15)
        + K.esc(ecn.quadrant_titles[key]) + "</div>"
        + '<div class="card-extra">' + ids.length + " 项</div></div>"
        + '<div style="padding:2px 8px 8px">' + quadrantRows(S, D, key) + "</div>"
        + "</div>";
    }).join("");

    drills["ecn-signoff"] = function () {
      return {
        title: "ECN 切换会签 · " + ecn.ecn_id,
        subtitle: "口径 · 影响面 · 生效留痕",
        html: kv([
          ["ECN", ecn.ecn_id + "（" + ecn.reason + "）"],
          ["主线订单", ecn.order_id],
          ["换料", materialLabel(materials, ecn.old_material) + " → " + materialLabel(materials, ecn.new_material)],
          ["BOM 版本", ecn.bom_id + " " + ecn.old_bom_version + " → " + ecn.new_bom_version],
          ["触发 / 会签 / 生效", K.fmtDate(ecn.triggered_at) + " / " + K.fmtDate(ecn.signoff_ts)
            + " / " + K.fmtDate(ecn.effective_ts)],
          ["会签槽位与结论", (ecn.signoff_slots || []).join("、") + " → " + ecn.signoff_result],
          ["留痕事件", (ecn.event_refs || []).join("、")],
          ["写操作", "本屏为只读演示：切换指令与验证结论由真实 HITL 链路承接，不在本屏落库"],
        ]),
      };
    };

    const ecnEvents = hasRows(events) ? events.filter(function (e) {
      return e && String(e.object_ref) === String(ecn.ecn_id);
    }) : null;

    return '<div class="sk-block">'
      + '<div class="section-title">支线 A · ECN 影响面四象限'
      + '<span class="section-extra">' + K.esc(ecn.ecn_id + " · " + ecn.reason)
      + " · 点击任一行看原始记录</span></div>"
      + '<div class="sk-num-hint" style="margin-bottom:10px">'
      + K.esc(materialLabel(materials, ecn.old_material)) + " → "
      + K.esc(materialLabel(materials, ecn.new_material)) + "；BOM "
      + K.esc(ecn.bom_id + " " + ecn.old_bom_version + " → " + ecn.new_bom_version)
      + "；会签 " + K.esc(K.fmtDate(ecn.signoff_ts)) + "，生效 " + K.esc(K.fmtDate(ecn.effective_ts)) + "。"
      + '<button class="btn btn-sm btn-outline" type="button" style="margin-left:8px"'
      + ' data-sk-drill="ecn-signoff">切换会签口径</button></div>'
      + progressHtml(S, D)
      + '<div class="s04-quad">' + quadCards + "</div>"
      + '<div style="margin-top:14px">'
      + K.signoffCard({
        title: "ECN 切换会签（" + ecn.ecn_id + "）",
        slots: (ecn.signoff_slots || []).map(function (code) {
          const labels = S.signoff_slot_labels || {};
          const users = S.signoff_users || {};
          return {
            role: labels[code] || code,
            user: users[code] || "—",
            status: String(ecn.signoff_result) === "approved" ? "approved" : "pending",
            ts: ecn.signoff_ts,
          };
        }),
        readonly: true,
        note: "结论 " + ecn.signoff_result + "（" + K.fmtDate(ecn.signoff_ts) + "），"
          + "切换于 " + K.fmtDate(ecn.effective_ts) + " 生效（" + ecn.new_bom_version + "）。"
          + "写操作由真实 HITL 链路承接，本屏为只读演示留痕。",
      })
      + "</div>"
      + (hasRows(ecnEvents)
        ? K.eventFlow(ecnEvents.slice().sort(function (a, b) {
          return String(a.ts).localeCompare(String(b.ts));
        }).map(function (e) {
          return {
            ts: e.ts, type: e.type,
            text: (e.payload && e.payload.note) ? String(e.payload.note) : String(e.type),
            scene: "S4 · ECN",
          };
        }), {
          title: "ECN 事件时间线", extra: ecnEvents.length + " 个事件",
          note: "ECN 时间线与主线进度点对齐：批量 5,000 片分 3 张工单（2,000 / 1,500 / 1,500），"
            + "ECN 于第二张工单在制、批量阶段触发换料评审，次日会签，第 3 日 MBOM "
            + ecn.new_bom_version + " 生效。",
        })
        : smallDegrade("events 未取到：ECN 事件时间线暂不可用（不编造事件）。"))
      + "</div>";
  }

  /** 背景事件判据：背景工单 / 背景检验批 / 设备日点检 —— 不属 S4 主线与支线 A。 */
  function isBackgroundEvent(e) {
    if (!e) return true;
    const ref = String(e.object_ref || "");
    if (/^DEMO-BG/.test(ref)) return true;
    if (/^DEMO-WO-10\d/.test(ref)) return true;
    if (e.payload && e.payload.kind === "daily_check") return true;
    return false;
  }

  function eventSection(D) {
    const K = kit();
    const events = rowsOf(D, "events");
    if (!hasRows(events)) {
      return '<div class="sk-block">' + smallDegrade("events 未取到：S4 事件流暂不可用（不编造事件）。") + "</div>";
    }
    const s4 = events.filter(function (e) { return e && String(e.scene) === "S4"; });
    const main = s4.filter(function (e) { return !isBackgroundEvent(e); })
      .sort(function (a, b) { return String(a.ts).localeCompare(String(b.ts)); });
    if (!main.length) {
      return '<div class="sk-block">' + smallDegrade("events 中无 S4 主线事件（不编造事件）。") + "</div>";
    }
    return '<div class="sk-block">'
      + K.eventFlow(main.map(function (e) {
        return {
          ts: e.ts, type: e.type,
          text: (e.payload && e.payload.note) ? String(e.payload.note) : String(e.type),
          scene: e.object_ref,
        };
      }), {
        title: "S4 主线 / 支线事件流", extra: main.length + " 个事件",
        note: "取 events.jsonl 中 scene=S4 的主线/支线事件（报工、判异、会签、ECN），按时间升序；"
          + "背景工单、背景检验批与设备点检事件 " + (s4.length - main.length) + " 条已折叠（非本屏叙事对象）。",
      })
      + "</div>";
  }

  /* ============================================================
   * 六、渲染
   * ============================================================ */

  function bodyHtml(S, D) {
    const K = kit();
    const sourceBadge = D.missing && D.missing.length
      ? K.badge("部分对象缺失：" + D.missing.join("、"), "badge-amber")
      : K.badge(D.localCount > 0 ? "数据源：离线回放包 + 装载层" : "数据源：T04 装载层对象端点", "badge-gray");
    return sourceBadge
      + K.badge("口径：spec-v1.md §5 界面数字台账", "badge-gray")
      + anchorSection(S)
      + '<div class="sk-block">'
      + '<div class="section-title">批量监控关键数<span class="section-extra">点击数字卡看口径</span></div>'
      + kpiSection(S, D)
      + "</div>"
      + spcSection(S, D)
      + alertSection(S, D, K.isReadonly())
      + ecnSection(S, D, K.isReadonly())
      + eventSection(D)
      + '<div class="sk-note">本屏全部数字取自演示装载层（口径见 spec-v1.md），'
      + "取不到即显示降级提示，不编造数字；ECN 影响面四象限逐行可下钻到原始记录。</div>";
  }

  function degradeHtml(D) {
    const K = kit();
    const missing = (D && D.missing && D.missing.length) ? D.missing.join("、") : "全部对象";
    return '<div class="sk-degrade">' + K.icon("warn", 15)
      + "<div><b>演示装载层未就绪：本屏不显示编造数字。</b>"
      + '<div class="sk-degrade-sub">缺失对象：' + K.esc(missing)
      + "。恢复条件：`GET /api/v2/demo/objects/{name}` 可用（T04 装载层），"
      + "或 `window.DEMO_STORY` 离线回放包就绪。</div></div></div>";
  }

  function openDrillByKey(key) {
    const K = kit();
    const maker = drills[key];
    if (typeof maker === "function") {
      K.openDrill(maker());
      return;
    }
    K.openDrill({
      title: "口径说明",
      subtitle: "该入口未登记口径",
      html: kv([["口径键", String(key)], ["处理", "未在场景屏 drills 字典登记，请检查场景屏实现"]]),
    });
  }

  function render(root, ctx) {
    const K = kit();
    if (!root) return;
    if (!K) {
      root.innerHTML = '<div class="content-inner"><div class="empty">'
        + "组件契约未加载：scene-kit.js 必须在场景脚本之前引入（window.SceneKit 缺失）"
        + "</div></div>";
      return;
    }
    K.use(ctx);
    injectStyle();
    drills = {};

    const S = sceneData();
    root.innerHTML = '<div class="content-inner">'
      + K.pageHead({
        icon: "activity",
        title: S.title || "S4 · 批量监控与 ECN",
        desc: (S.sub || "") + "。数字全部现取自演示装载层，逐项可下钻；判异处置会签一步可达。",
      })
      + '<div class="sk-block" id="s04-body"><div class="empty">' + K.spinner("lg")
      + "正在读取演示装载层…</div></div></div>";

    const body = root.querySelector("#s04-body");
    loadAll().then(function (D) {
      // 屏已切走（节点脱离文档）就不再写 DOM，避免写进已卸载的节点。
      if (!body || !body.parentNode) return;
      if (!D || !D.ok) {
        body.innerHTML = degradeHtml(D);
        return;
      }
      body.innerHTML = bodyHtml(S, D);
      K.bindDrills(body, openDrillByKey);
    }).catch(function () {
      if (!body || !body.parentNode) return;
      body.innerHTML = degradeHtml(null);
    });
  }

  /* ============================================================
   * 七、登记（自注册，不改 app.js / 总导航）
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s04] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: "S4 · 批量监控与 ECN",
    title: "S4 · 批量监控与 ECN",
    sub: "SPC 判异处置会签 · 支线 A 换料影响面四象限",
    icon: "activity",
    group: window.SceneRegistry.GROUP_NAME,
    order: 40,
    // spec-v1.md §4：S4 面向 总经理 / 研发 / 质量 / 制造。
    // 业务（biz / sales）只看 S1/S5；研发与制造访客分别以治理 key
    // rd_engineering / manufacturing 命中，故两套写法都要列。
    roles: ["gm", "rd", "qa", "mfg", "rd_engineering", "manufacturing"],
    render: render,
  });
})();
