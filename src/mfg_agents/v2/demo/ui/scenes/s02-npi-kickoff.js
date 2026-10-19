/**
 * S2 · 立项场景屏（T10 交付物）
 *
 * 契约（见 data/demo_storyline/frontend/registry.json → scenes[s02]）：
 *  - 屏契约：导出 `render(root, ctx)`，在 `window.SceneRegistry.register()` 登记；
 *  - 数据契约：展示元信息读 `SceneKit.sceneData("s02")`（权威副本 data/demo_storyline/frontend/s02-npi-kickoff.js，
 *    本文件内联同构兜底镜像，两者由 tests/demo/test_s02.py 深比对守护）；
 *    全部**数值**由 T04 装载层现算，本文件不写任何业务散数；
 *  - 组件契约：徽标 / 数字卡 / 事件流 / 会签卡 / 下钻一律走 SceneKit，保证六屏风格一致。
 *
 * 三块内容（对应计划 T10「做什么」）：
 *  1. 立项材料自动汇总：订单输入 / EBOM·MBOM / DFM 预审 / 长周期料 / 阶段门计划 五卡，张张可下钻；
 *  2. 相似历史案例检索：装载层 20 条合成案例，关键词检索 + 点开看脱敏原文；
 *  3. 立项会签：槽位与结果回放装载层事件 DEMO-EV-0005 的留痕。
 *
 * 取数说明：一律走组件契约 `SceneKit.story('<object>')`（内部打 T04 装载层
 * `GET /api/v2/demo/objects/{name}`，不可用时回落 `window.DEMO_STORY` 并标注降级）。
 * 本屏不自写端点直连——T05 契约细则第 2 条明令下游窗口不许再各写一套
 * （`/api/v2/demo/storyline/*` 是早期误写，真实路由只有 `/objects/*`）。
 * 场景展示元信息走本屏自身的 `frontend/s02-npi-kickoff.js`（经 SCENE_DATA 读取）。
 */
(function () {
  "use strict";

  const SCENE_ID = "s02";

  /** 本屏需要的装载层对象（与 registry.json → scenes[s02].objects 一致）。 */
  const OBJECTS = [
    "sales_orders", "customers", "boms", "materials", "suppliers",
    "purchase_orders", "events", "cases",
  ];

  /** 取数超时与端点由 SceneKit.story() 统一承载，本屏不自建 fetch 通路。 */

  /** 当前屏的口径字典（键 = 数字卡的 data-sk-drill）。 */
  let drills = {};

  function kit() { return window.SceneKit; }

  /* ============================================================
   * 零、本屏专属样式（只注入一次；类名继续用 sk- 前缀，颜色取 app.css 变量）
   *
   * 为什么不放进 scene-kit：组件契约要求六屏风格一致，公共件归 T05/T12 维护；
   * 表格与检索条是 S2 自己的排版件，随屏走，避免改动别人文件。
   * ============================================================ */

  const STYLE_ID = "s02-style";

  const CSS = `
.sk-table { width: 100%; border-collapse: collapse; font-size: 12.2px; }
.sk-table th {
  text-align: left; padding: 6px 8px; color: var(--text-3); font-weight: 600;
  border-bottom: 1px solid var(--border); white-space: nowrap;
}
.sk-table td {
  padding: 7px 8px; color: var(--text); border-bottom: 1px dashed var(--border);
  word-break: break-all;
}
.sk-table tbody tr:last-child td { border-bottom: none; }
.sk-search {
  display: flex; align-items: center; gap: 8px;
  margin: 4px 0 10px; padding: 6px 10px;
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-sm);
}
.sk-search-icon { display: inline-flex; color: var(--text-3); flex: none; }
.sk-search-input {
  flex: 1; min-width: 0; border: none; outline: none; background: transparent;
  font: inherit; font-size: 12.6px; color: var(--text);
}
.sk-case-hint { font-size: 11.5px; color: var(--text-3); margin-bottom: 6px; }
.sk-case-list { display: flex; flex-direction: column; }
.sk-case-row {
  display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
  padding: 9px 6px; border: none; border-bottom: 1px dashed var(--border);
  background: transparent; font: inherit; color: inherit; cursor: pointer;
}
.sk-case-row:hover { background: var(--primary-light-2); }
.sk-case-row:last-child { border-bottom: none; }
.sk-case-main { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.sk-case-title { font-size: 12.8px; color: var(--text); font-weight: 600; }
.sk-case-sub { font-size: 11.5px; color: var(--text-3); }
.sk-case-score { flex: none; font-size: 11.5px; color: var(--text-2); }
.sk-case-more { flex: none; display: inline-flex; color: var(--text-3); }
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
   * 一、装载层取数（走组件契约 SceneKit.story，不自写端点）
   *
   * T05 契约细则第 2 条：公共故事数据一律 `SceneKit.story('<object>')`
   * （接口 → `DEMO_STORY` 回落 → 明确降级，永不 reject、不编数字），
   * 下游窗口不许再各自手写 `/api/v2/demo/...` 直连。本屏照此执行。
   * ============================================================ */

  /**
   * 并发取全部对象；返回 `{data: {对象名: 行数组}, missing: [对象名], degraded: [对象名]}`。
   *
   * - `missing`：对象没取到（接口与本地回放都不可用）→ 相关卡片显示破折号；
   * - `degraded`：接口不可用但本地回放顶上了 → 页面上明确标注取自本地回放。
   */
  function loadObjects() {
    const K = kit();
    return Promise.all(OBJECTS.map(function (name) {
      return K.story(name).then(function (res) {
        return { name: name, res: res };
      }).catch(function () {
        // story() 契约上永不 reject；这里再兜一层，避免单点异常拖垮整屏。
        return { name: name, res: { ok: false, rows: [], error: "装载层取数异常" } };
      });
    })).then(function (list) {
      const data = {};
      const missing = [];
      const degraded = [];
      list.forEach(function (item) {
        const res = item.res || {};
        data[item.name] = Array.isArray(res.rows) ? res.rows : [];
        if (res.ok !== true) missing.push(item.name);
        else if (res.degraded === true) degraded.push(item.name);
      });
      return { data: data, missing: missing, degraded: degraded };
    }).catch(function () {
      const data = {};
      OBJECTS.forEach(function (name) { data[name] = []; });
      return { data: data, missing: OBJECTS.slice(), degraded: [] };
    });
  }

  /* ============================================================
   * 二、现算派生模型（数字全部来自装载层行，无手写散数）
   * ============================================================ */

  /** 名称 → 品类前缀：去掉「（替代）」标记与最后一个空格后的型号段。 */
  function categoryOf(name, token) {
    return String(name || "").split(token).join("").trim().split(" ")[0];
  }

  function buildModel(data, meta) {
    const rules = meta.rules || {};
    const orders = data.sales_orders || [];
    const boms = data.boms || [];
    const materials = data.materials || [];
    const suppliers = data.suppliers || [];
    const pos = data.purchase_orders || [];
    const events = data.events || [];
    const cases = data.cases || [];

    const mainOrder = orders.filter(function (o) {
      return o && o.order_id === meta.refs.orderId;
    })[0] || orders[0] || {};
    const mainLine = (Array.isArray(mainOrder.lines) ? mainOrder.lines : [])[0] || {};
    const customer = (data.customers || []).filter(function (c) {
      return c && c.customer_id === mainOrder.customer_id;
    })[0] || {};

    const ebom = boms.filter(function (b) { return b && b.kind === "E"; })[0] || {};
    const ebomLines = Array.isArray(ebom.lines) ? ebom.lines : [];
    const mboms = boms.filter(function (b) { return b && b.kind === "M"; });
    const mbomVersions = [];
    mboms.forEach(function (b) {
      if (b.version && mbomVersions.indexOf(b.version) === -1) mbomVersions.push(b.version);
    });
    mbomVersions.sort();

    const materialById = {};
    materials.forEach(function (m) { if (m && m.material_id) materialById[m.material_id] = m; });
    const supplierName = {};
    const supplierOtd = {};
    suppliers.forEach(function (s) {
      if (!s || !s.supplier_id) return;
      supplierName[s.supplier_id] = s.name || s.supplier_id;
      const otd = Number(s.otd_rate);
      supplierOtd[s.supplier_id] = Number.isFinite(otd) ? otd : null;
    });

    const token = rules.substituteNameToken || "（替代）";
    const warnLow = Number(rules.supplierOtdWarnLow);
    const otdThreshold = Number.isFinite(warnLow) ? warnLow : 0.92;

    // 替代料品类集合：materials 中带「（替代）」标记的料件，按其品类前缀登记。
    const substituteCategories = {};
    materials.forEach(function (m) {
      if (!m || !m.material_id) return;
      if (String(m.name || "").indexOf(token) === -1) return;
      substituteCategories[categoryOf(m.name, token)] = m.material_id;
    });

    // EBOM 子件逐行判定：单源（默认供应商历史 OTD 低于阈值）+ 是否已出现替代料。
    const ebomRows = ebomLines.map(function (line) {
      const mid = line.material_id;
      const mat = materialById[mid] || {};
      const supId = mat.supplier_id || "—";
      const otd = supId !== "—" ? supplierOtd[supId] : null;
      const singleSource = supId !== "—" && supplierName[supId] !== undefined;
      const riskyOtd = otd !== null && otd < otdThreshold;
      const substituteId = substituteCategories[categoryOf(mat.name, token)] || "";
      const hasSubstitute = substituteId !== "" && substituteId !== mid;
      return {
        material_id: mid,
        name: mat.name || "—",
        qty: line.qty,
        ref_des: line.ref_des || "—",
        supplier_id: supId,
        supplier_name: supplierName[supId] || "—",
        otd: otd,
        singleSource: singleSource,
        riskyOtd: riskyOtd,
        hasSubstitute: hasSubstitute,
        substitute_id: hasSubstitute ? substituteId : "",
      };
    });

    const dfmRisky = ebomRows.filter(function (r) { return r.riskyOtd || r.hasSubstitute; });
    const singleSourceCount = ebomRows.filter(function (r) { return r.singleSource; }).length;

    // 长周期料：在途采购单（status 非 confirmed 完结核销口径 → 以 delayed / confirmed 区分）。
    const openPos = pos.filter(function (p) {
      return p && String(p.status || "") !== "closed";
    });
    const delayedPos = openPos.filter(function (p) { return String(p.status || "") === "delayed"; });
    const shortageEvent = events.filter(function (e) {
      return e && e.payload && e.payload.kind === "shortage";
    })[0] || null;

    // 阶段门计划：装载层 scene=S3 阶段门事件（EVT / DVT / PVT）。
    const stageEvents = events.filter(function (e) {
      return e && e.scene === "S3" && e.payload && e.payload.stage;
    });

    // 立项会签留痕：scene=S2 的 hitl 事件。
    const signoffEvent = events.filter(function (e) {
      return e && e.scene === meta.refs.eventScene && e.type === "hitl";
    })[0] || null;
    const slots = [];
    if (signoffEvent && signoffEvent.payload) {
      const payloadSlots = Array.isArray(signoffEvent.payload.slots) ? signoffEvent.payload.slots : [];
      const rawResult = String(signoffEvent.payload.result || "");
      const status = (rawResult === "approved" || rawResult === "Go") ? "approved"
        : (rawResult === "rejected" ? "rejected" : "pending");
      payloadSlots.forEach(function (key) {
        slots.push({
          role: (meta.signoff.slotLabels || {})[key] || key,
          user: "—",
          status: status,
          ts: signoffEvent.ts,
        });
      });
    }

    return {
      mainOrder: mainOrder,
      mainLine: mainLine,
      customer: customer,
      ebom: ebom,
      ebomRows: ebomRows,
      mboms: mboms,
      mbomVersions: mbomVersions,
      dfmRisky: dfmRisky,
      singleSourceCount: singleSourceCount,
      openPos: openPos,
      delayedPos: delayedPos,
      shortageEvent: shortageEvent,
      stageEvents: stageEvents,
      s2Events: events.filter(function (e) {
        return e && e.scene === meta.refs.eventScene;
      }).sort(function (a, b) { return String(a.ts) < String(b.ts) ? -1 : 1; }),
      signoffEvent: signoffEvent,
      slots: slots,
      cases: cases.slice(),
      otdThreshold: otdThreshold,
    };
  }

  /* ============================================================
   * 三、渲染：口径表 / 降级条
   * ============================================================ */

  function kv(pairs) {
    const K = kit();
    const rows = (pairs || []).map(function (pair) {
      return '<div class="sk-kv-row"><span class="sk-kv-k">' + K.esc(pair[0])
        + '</span><span class="sk-kv-v">' + K.esc(pair[1]) + "</div>";
    }).join("");
    return '<div class="sk-kv">' + rows + "</div>";
  }

  /** 逐行表格（下钻弹层里的明细表）：cols = [{key,label}]，rows = 对象数组。 */
  function table(cols, rows) {
    const K = kit();
    const head = cols.map(function (c) { return '<th>' + K.esc(c.label) + "</th>"; }).join("");
    const body = (rows || []).map(function (row) {
      const tds = cols.map(function (c) {
        return '<td>' + K.esc(row[c.key] === undefined || row[c.key] === null ? "—" : row[c.key]) + "</td>";
      }).join("");
      return "<tr>" + tds + "</tr>";
    }).join("");
    return '<table class="sk-table"><thead><tr>' + head + "</tr></thead><tbody>"
      + (body || '<tr><td colspan="' + cols.length + '">无明细行</td></tr>')
      + "</tbody></table>";
  }

  /** 事件摘要文案：payload.note 优先，其次 status / result，最后键值罗列。 */
  function eventText(ev) {
    const payload = (ev && ev.payload) || {};
    if (payload.note) return String(payload.note);
    if (payload.status) return String(payload.status);
    if (payload.result) return String(payload.result);
    const keys = Object.keys(payload);
    if (!keys.length) return "";
    return keys.slice(0, 4).map(function (key) {
      return key + "=" + payload[key];
    }).join(" · ");
  }

  function degradeBar(missing, degraded) {
    const K = kit();
    if (!missing.length && !degraded.length) return "";
    const parts = [];
    if (missing.length) {
      parts.push("<b>装载层未就绪：</b>" + K.esc(missing.join("、")) + " 未取到，"
        + "相关卡片显示破折号而不显示编造数字。");
    }
    if (degraded.length) {
      parts.push("<b>当前取自本地回放：</b>" + K.esc(degraded.join("、"))
        + "（接口不可用时回落 window.DEMO_STORY）。");
    }
    return '<div class="sk-degrade">' + K.icon("warn", 15)
      + "<div>" + parts.join('<div class="sk-degrade-sub">') + "</div></div>";
  }

  /* ============================================================
   * 四、五张立项材料卡 + 口径字典
   * ============================================================ */

  function buildCards(model, meta, ctxInfo) {
    const K = kit();
    const cards = [];
    const cardMeta = {};
    (meta.cards || []).forEach(function (c) { cardMeta[c.key] = c; });

    const push = function (key, value, unit, hint) {
      const m = cardMeta[key] || {};
      cards.push({
        label: m.label || key,
        value: value,
        unit: unit,
        hint: hint,
        tone: m.tone || "gray",
        drill: key,
      });
    };

    const orderQty = Number(model.mainLine.qty);
    push("order-input", Number.isFinite(orderQty) ? K.fmtNum(orderQty) : "—",
      cardMeta["order-input"] ? cardMeta["order-input"].unit : "片",
      "承诺交期 " + K.fmtDate(model.mainOrder.promise_date)
        + " · 客户 " + (model.customer.name || "—"));

    push("ebom", K.fmtNum(model.ebomRows.length), "项",
      "EBOM " + (model.ebom.version || "—") + " · MBOM 版本 "
        + (model.mbomVersions.length ? model.mbomVersions.join(" / ") : "—"));

    push("dfm", K.fmtNum(model.dfmRisky.length), "项",
      "单源料件 " + K.fmtNum(model.singleSourceCount) + " 项 · 命中风险规则 "
        + K.fmtNum(model.dfmRisky.length) + " 项");

    push("long-lead", K.fmtNum(model.openPos.length), "张 PO",
      "延期 " + K.fmtNum(model.delayedPos.length) + " 张"
        + (model.shortageEvent ? " · 缺口 " + K.fmtNum(model.shortageEvent.payload.gap_qty) + " 片" : ""));

    push("stage-gate-plan", K.fmtNum(model.stageEvents.length), "门",
      model.stageEvents.length
        ? model.stageEvents.map(function (e) { return e.payload.stage; }).join(" / ")
        : "无阶段门事件");

    /* ---- 口径字典：每张卡点开都能看到"这个数怎么来的" ---- */
    drills = {};

    drills["order-input"] = function () {
      const line = model.mainLine || {};
      const cust = model.customer || {};
      return {
        title: "订单输入",
        subtitle: "口径 · 来源 · 下游引用",
        html: kv([
          ["取值", K.fmtNum(line.qty) + " 片"],
          ["口径", "sales_orders 主线订单首个订单行的 qty（spec-v1.md §2.4）"],
          ["来源", "GET /api/v2/demo/objects/sales_orders（" + ctxInfo.sourceText + "）"],
          ["订单号", model.mainOrder.order_id || "—"],
          ["订单状态", model.mainOrder.status || "—"],
          ["单价", line.unit_price === undefined ? "—" : K.fmtNum(line.unit_price, 2) + " 元/片"],
          ["承诺交期", K.fmtDate(model.mainOrder.promise_date)],
          ["客户", (cust.name || "—") + " · 信用等级 " + (cust.credit_grade || "—")],
          ["客户质量档案", cust.quality_profile || "—"],
          ["下游引用", "S1 接单评审（报价区间）、S3 阶段门、S5 出货追溯同源"],
        ]),
      };
    };

    drills["ebom"] = function () {
      const diffRows = [];
      const v10 = model.mboms.filter(function (b) { return b.version === "v1.0"; })[0];
      const latest = model.mboms.length ? model.mboms[model.mboms.length - 1] : null;
      if (v10 && latest && v10 !== latest) {
        const before = {};
        (v10.lines || []).forEach(function (l) { before[l.ref_des] = l.material_id; });
        (latest.lines || []).forEach(function (l) {
          if (before[l.ref_des] !== l.material_id) {
            diffRows.push({
              ref_des: l.ref_des || "—",
              before: before[l.ref_des] || "—",
              after: l.material_id,
              version: latest.version,
            });
          }
        });
      }
      return {
        title: "EBOM / MBOM 子件",
        subtitle: "口径 · 来源 · 两版差异",
        html: kv([
          ["取值", K.fmtNum(model.ebomRows.length) + " 项"],
          ["口径", "boms 中 kind=E 的子件行数（spec-v1.md §2.3）"],
          ["来源", "GET /api/v2/demo/objects/boms（" + ctxInfo.sourceText + "）"],
          ["EBOM 版本", model.ebom.version || "—"],
          ["EBOM 归属物料", model.ebom.material_id || "—"],
          ["MBOM 版本", model.mbomVersions.length ? model.mbomVersions.join(" / ") : "—"],
          ["MBOM 版本数", K.fmtNum(model.mbomVersions.length) + " 版（ECN 后新增 v1.1）"],
        ])
          + '<div class="section-title" style="margin-top:14px">EBOM 子件明细'
          + '<span class="section-extra">' + K.esc(ctxInfo.sourceText) + "</span></div>"
          + table(
            [{ key: "material_id", label: "料号" }, { key: "name", label: "料名" },
              { key: "qty", label: "用量" }, { key: "ref_des", label: "位号" }],
            model.ebomRows
          )
          + '<div class="section-title" style="margin-top:14px">MBOM 版本差异（v1.0 → '
          + K.esc(latest && latest.version ? latest.version : "—") + "）"
          + '<span class="section-extra">改动行</span></div>'
          + table(
            [{ key: "ref_des", label: "位号" }, { key: "before", label: "改动前料号" },
              { key: "after", label: "改动后料号" }, { key: "version", label: "生效版本" }],
            diffRows
          ),
      };
    };

    drills["dfm"] = function () {
      const ruleText = "① 默认供应商历史 OTD < " + model.otdThreshold
        + "（交期高风险）；② EBOM 子件已存在同品类「（替代）」料件（工程替代风险）。命中任一即计为风险项。";
      return {
        title: "DFM 预审风险项",
        subtitle: "口径 · 规则 · 明细",
        html: kv([
          ["取值", K.fmtNum(model.dfmRisky.length) + " 项"],
          ["口径", ruleText],
          ["来源", "GET /api/v2/demo/objects/materials、/suppliers、/boms（" + ctxInfo.sourceText + "）"],
          ["单源提示", K.fmtNum(model.singleSourceCount) + " / " + K.fmtNum(model.ebomRows.length)
            + " 项 EBOM 子件在 materials 中只登记 1 个默认供应商"],
          ["阈值来源", "data/demo_storyline/frontend/s02-npi-kickoff.js → rules.supplierOtdWarnLow"],
          ["下游引用", "S4 支线 A ECN 换料（DEMO-ECN-001）的输入"],
        ])
          + '<div class="section-title" style="margin-top:14px">EBOM 子件预审明细'
          + '<span class="section-extra">按规则逐行判定</span></div>'
          + table(
            [{ key: "material_id", label: "料号" }, { key: "name", label: "料名" },
              { key: "supplier_name", label: "默认供应商" }, { key: "otd_display", label: "供应商 OTD" },
              { key: "risk_display", label: "命中风险" }, { key: "substitute_id", label: "同类替代料" }],
            model.ebomRows.map(function (r) {
              const risks = [];
              if (r.riskyOtd) risks.push("交期高风险");
              if (r.hasSubstitute) risks.push("已有替代料");
              return {
                material_id: r.material_id,
                name: r.name,
                supplier_name: r.supplier_name,
                otd_display: r.otd === null ? "—" : K.fmtNum(r.otd, 4),
                risk_display: risks.length ? risks.join("、") : "未命中",
                substitute_id: r.substitute_id || "—",
              };
            })
          ),
      };
    };

    drills["long-lead"] = function () {
      const rows = model.openPos.map(function (p) {
        const supplier = (ctxInfo.supplierName || {})[p.supplier_id] || p.supplier_id || "—";
        return {
          po_id: p.po_id || "—",
          material_id: p.material_id || "—",
          supplier: supplier,
          qty: p.qty,
          promise_date: K.fmtDate(p.promise_date),
          status: p.status || "—",
        };
      });
      return {
        title: "长周期料（在途 / 延期）",
        subtitle: "口径 · 来源 · 采购单明细",
        html: kv([
          ["取值", K.fmtNum(model.openPos.length) + " 张在途 PO"],
          ["口径", "purchase_orders 中 status ≠ closed 的采购单张数（spec-v1.md 采购对象口径）"],
          ["延期", K.fmtNum(model.delayedPos.length) + " 张（status = delayed）"],
          ["缺口预警", model.shortageEvent
            ? "物料 " + (model.shortageEvent.payload.material || "—") + " · 缺口 "
              + K.fmtNum(model.shortageEvent.payload.gap_qty) + " 片 · 触发于 "
              + K.fmtDate(model.shortageEvent.ts)
            : "无缺料预警事件"],
          ["来源", "GET /api/v2/demo/objects/purchase_orders、/events（" + ctxInfo.sourceText + "）"],
          ["下游引用", "S6 采购执行（PO 交期确认 / 催料任务 / 升级链）"],
        ])
          + '<div class="section-title" style="margin-top:14px">在途采购单明细'
          + '<span class="section-extra">承诺交期与状态</span></div>'
          + table(
            [{ key: "po_id", label: "采购单" }, { key: "material_id", label: "料号" },
              { key: "supplier", label: "供应商" }, { key: "qty", label: "数量" },
              { key: "promise_date", label: "承诺交期" }, { key: "status", label: "状态" }],
            rows
          ),
      };
    };

    drills["stage-gate-plan"] = function () {
      const rows = model.stageEvents.map(function (e) {
        const slotKeys = (e.payload && Array.isArray(e.payload.slots)) ? e.payload.slots : [];
        return {
          stage: e.payload.stage || "—",
          ts: K.fmtDate(e.ts),
          verdict: e.payload.verdict || e.payload.result || "—",
          slots: slotKeys.map(function (k) {
            return (meta.signoff.slotLabels || {})[k] || k;
          }).join("、") || "—",
        };
      });
      return {
        title: "阶段门计划",
        subtitle: "口径 · 来源 · 三门明细",
        html: kv([
          ["取值", K.fmtNum(model.stageEvents.length) + " 道阶段门"],
          ["口径", "events 中 scene=S3 且 payload.stage 存在的事件数（EVT / DVT / PVT）"],
          ["来源", "GET /api/v2/demo/objects/events（" + ctxInfo.sourceText + "）"],
          ["驱动关系", "阶段门时间点即 S3 记分卡与门禁结论的挂接点"],
          ["下游引用", "S3 阶段门会签（良率 / CPK / 直通率 / 问题闭环数四维记分卡）"],
        ])
          + '<div class="section-title" style="margin-top:14px">阶段门明细'
          + '<span class="section-extra">时间 · 结论 · 会签槽位</span></div>'
          + table(
            [{ key: "stage", label: "阶段门" }, { key: "ts", label: "计划时间" },
              { key: "verdict", label: "结论" }, { key: "slots", label: "会签槽位" }],
            rows
          ),
      };
    };

    return cards;
  }

  /* ============================================================
   * 五、案例检索（20 条合成案例：关键词检索 + 脱敏原文）
   * ============================================================ */

  function caseScore(row, tokens, fields) {
    if (!tokens.length) return 0;
    let hits = 0;
    tokens.forEach(function (t) {
      fields.forEach(function (f) {
        const value = String(row[f] === undefined || row[f] === null ? "" : row[f]).toLowerCase();
        if (value.indexOf(t) !== -1) hits += 1;
      });
    });
    return hits;
  }

  /* ---------- T19：立项案例检索智能体（Agentic RAG 回放，关键词版保留为降级） ---------- */
  function hydrateAgentBlock(box, meta) {
    const K = window.SceneKit;
    const el = box ? box.querySelector('[data-sk-slot="agent"]') : null;
    const a = (meta && meta.agent_layer) || null;
    if (!el || !a || !K || !a.replays) return;
    K.replay(a.replays.agent).then(function (res) {
      if (!el.parentNode) return;
      if (!res || !res.ok || !res.package) {
        el.innerHTML = '<div class="sk-degrade" data-sk-s02-agent-degrade="1">'
          + K.esc(a.degrade || "检索智能体回放未就绪，已回落关键词检索") + "</div>"
          + '<div class="sk-note">' + K.esc(a.fallback || "") + "</div>";
        return;
      }
      const pkg = res.package;
      const head = '<div class="section-title">' + K.esc(a.title) + "</div>"
        + K.agentPanel({
          title: pkg.title, agents: pkg.agents, dag: pkg.dag, steps: pkg.steps,
          node_steps: pkg.node_steps, conclusion: pkg.conclusion, source: "replay",
        });
      el.innerHTML = head;
      K.replay(a.replays.cases).then(function (cres) {
        if (!el.parentNode || !cres || !cres.ok || !cres.package) return;
        const cp = cres.package;
        const trace = (cp.trace || []).map(function (t) {
          return "<li><b>" + K.esc(t.title) + "</b> — " + K.esc(t.detail) + "</li>";
        }).join("");
        const hits = (cp.hits || []).map(function (h) {
          return '<div class="sk-case-list" style="margin-top:6px"><b>'
            + K.esc(h.case_id) + " · " + K.esc(h.title) + "</b><div>根因："
            + K.esc(h.root_cause) + " · 相似度 " + K.esc(String(h.score)) + "</div></div>";
        }).join("");
        el.innerHTML = head
          + (trace ? '<div class="section-title">检索轨迹（归一 → 扩展 → 过滤 → 引用）</div>'
            + '<ol style="margin:8px 0 0 18px;font-size:12.4px;line-height:1.9;'
            + 'color:var(--text-2,#57606A)">' + trace + "</ol>" : "")
          + (hits ? '<div class="section-title">命中案例（回放）</div>' + hits : "")
          + '<div class="sk-note">回放与真实链路同构（trace 五步 / filter_explain）；'
          + "下方关键词检索保留为降级路径。</div>";
      });
    });
  }

  function renderCaseList(box, cases, meta, keyword) {
    const K = kit();
    const view = meta.caseView || {};
    const fields = view.matchedFields || ["title", "root_cause", "dept"];
    const tokens = String(keyword || "").toLowerCase().split(/[\s,，、;；]+/).filter(function (t) {
      return t.length > 0;
    });
    const limit = Number(meta.rules.caseListLimit) || 20;

    const scored = cases.map(function (row) {
      return { row: row, score: caseScore(row, tokens, fields) };
    }).filter(function (item) {
      return tokens.length === 0 || item.score > 0;
    }).sort(function (a, b) {
      return b.score - a.score;
    }).slice(0, limit);

    const hint = tokens.length
      ? "命中 " + scored.length + " / " + cases.length + " 条（按关键词命中维度数降序）"
      : "共 " + Math.min(cases.length, limit) + " 条合成案例（未输入关键词，按装载层顺序列出）";

    const rows = scored.map(function (item) {
      const row = item.row || {};
      const title = row.title || "未命名案例";
      const scoreText = tokens.length ? (view.scoreLabel || "关键词命中") + " " + item.score + " 项" : "—";
      return '<button class="sk-case-row" type="button" data-sk-case="' + K.esc(row.case_id || "") + '">'
        + '<span class="sk-case-main"><span class="sk-case-title">' + K.esc(title) + "</span>"
        + '<span class="sk-case-sub">' + K.esc(row.case_id || "—")
        + " · " + K.esc(row.dept || "未归类") + "</span></span>"
        + '<span class="sk-case-score">' + K.esc(scoreText) + "</span>"
        + '<span class="sk-case-more">' + K.icon("chevron", 14) + "</span>"
        + "</button>";
    }).join("");

    box.innerHTML = '<div class="sk-case-hint">' + K.esc(hint) + "</div>"
      + (rows || '<div class="empty">没有命中该关键词的案例；换一个缺陷症状或根因词试试</div>');

    const buttons = box.querySelectorAll("[data-sk-case]");
    for (let i = 0; i < buttons.length; i += 1) {
      buttons[i].addEventListener("click", function (ev) {
        if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
        const id = this.dataset.skCase;
        const row = cases.filter(function (c) { return c && c.case_id === id; })[0];
        if (!row) {
          K.openDrill({ title: "案例不存在", subtitle: "装载层无此案例编号", html: kv([["编号", id]]) });
          return;
        }
        const pairs = (view.fields || []).map(function (f) {
          return [f.label, row[f.key] === undefined || row[f.key] === null ? "—" : row[f.key]];
        });
        K.openDrill({
          title: row.title || "案例原文",
          subtitle: "脱敏原文（案例编号 " + (row.case_id || "—") + "）",
          html: kv(pairs)
            + '<div class="sk-note" style="margin-top:12px">' + K.esc(view.redactionNote || "") + "</div>",
        });
      });
    }
  }

  /* ============================================================
   * 六、场景配置（含兜底镜像）与渲染入口
   * ============================================================ */

  /**
   * 场景配置（展示元信息）取用顺序：
   *  1. `window.SCENE_DATA["s02"]`（契约权威副本 data/demo_storyline/frontend/s02-npi-kickoff.js）；
   *  2. 缺位时本文件内联兜底镜像 INLINE_SCENE_DATA（与权威副本逐值同构，
   *     tests/demo/test_s02.py 深比对守护；registry.json 的 `/demo-story/{file}` 挂载点
   *     已在 api.py 落地，但 index.html 未引入该脚本，故兜底仍是主路径）。
   * 两处内容必须一致——改任一处都要同步另一处并跑测试。
   */
  // >>> INLINE_SCENE_DATA
  const INLINE_SCENE_DATA = {
    "sceneId": "s02",
    "refs": {
      "orderId": "DEMO-SO-001",
      "eventScene": "S2"
    },
    "rules": {
      "supplierOtdWarnLow": 0.92,
      "substituteNameToken": "（替代）",
      "caseListLimit": 20
    },
    "cards": [
      {
        "key": "order-input",
        "label": "订单输入",
        "tone": "blue",
        "unit": "片",
        "hint": "口径：sales_orders 主线订单行 qty（承诺交期 promise_date）"
      },
      {
        "key": "ebom",
        "label": "EBOM / MBOM",
        "tone": "purple",
        "unit": "项",
        "hint": "口径：boms 中 kind=E 的子件行数（MBOM 两版差异单独列示）"
      },
      {
        "key": "dfm",
        "label": "DFM 预审 · 风险项",
        "tone": "amber",
        "unit": "项",
        "hint": "口径：命中「供应商 OTD 低于阈值」或「已存在同品类替代料」的 EBOM 子件项数"
      },
      {
        "key": "long-lead",
        "label": "长周期料（在途 / 延期）",
        "tone": "red",
        "unit": "张 PO",
        "hint": "口径：purchase_orders 在途张数（status=delayed 计为延期）"
      },
      {
        "key": "stage-gate-plan",
        "label": "阶段门计划",
        "tone": "green",
        "unit": "门",
        "hint": "口径：events 中 scene=S3 的阶段门事件数（EVT / DVT / PVT）"
      }
    ],
    "caseView": {
      "keywordPlaceholder": "输入缺陷模式 / 症状 / 根因关键词（如 炉温、来料、模具）",
      "matchedFields": [
        "title",
        "root_cause",
        "dept"
      ],
      "fields": [
        {
          "key": "case_id",
          "label": "案例编号"
        },
        {
          "key": "dept",
          "label": "所属部门"
        },
        {
          "key": "title",
          "label": "案例标题"
        },
        {
          "key": "root_cause",
          "label": "根因（脱敏）"
        }
      ],
      "scoreLabel": "关键词命中",
      "redactionNote": "本文为脱敏知识层原文：已隐去企业名、人名、真实批次号；证据层引用（原文附件 / 检测报告）需另行申请授权。"
    },
    "signoff": {
      "title": "立项会签（研发 / 工艺 双槽位）",
      "slotLabels": {
        "rd": "研发",
        "pe": "工艺",
        "pqe": "制程质量",
        "pln": "计划"
      },
      "note": "留痕来源：装载层事件 DEMO-EV-0005（type=hitl，payload.slots=[rd,pe]，result=approved）。演示实例只读回放该留痕；写路径由治理层（HITL）承载，不在本演示屏范围内。"
    },
    "sourceNote": "本屏全部数字由 T04 装载层（GET /api/v2/demo/objects/<对象>）现算，无手写散数；装载层不可用时显示降级提示，不显示编造数字。",
    "agent_layer": {
      "replays": {
        "agent": "s02_case_rag",
        "cases": "s02_case_search"
      },
      "title": "立项案例检索智能体（Agentic RAG 回放）",
      "fallback": "回放未就绪时回落关键词检索（现有关键词版保留为降级路径）",
      "degrade": "检索智能体回放未就绪，已回落关键词检索"
    }
  };
  // <<< INLINE_SCENE_DATA

  function sceneData() {
    const bag = window.SCENE_DATA;
    if (!bag || typeof bag !== "object") window.SCENE_DATA = {};
    if (!window.SCENE_DATA[SCENE_ID]) window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA;
    return window.SCENE_DATA[SCENE_ID];
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

    const meta = sceneData();

    const readonly = K.isReadonly(ctx);
    root.innerHTML = '<div class="content-inner"><div id="s02-root">'
      + K.pageHead({
        icon: "file",
        title: "S2 · 立项",
        desc: "立项材料自动汇总（订单输入 / EBOM·MBOM / DFM 预审 / 长周期料 / 阶段门计划）"
          + "· 相似历史案例检索 · 立项会签留痕。",
        extra: K.badge("T10 · S2 立项", "badge-blue")
          + (readonly ? K.badge("只读访客 · 不发起写操作", "badge-gray")
            : K.badge("可写岗位 · 写路径见口径", "badge-blue")),
      })
      + '<div class="sk-block" data-sk-slot="cards"><div class="empty">'
      + (K.spinner ? K.spinner("lg") : "") + "正在读取装载层…</div></div>"
      + '<div class="sk-block" data-sk-slot="flow"></div>'
      + '<div class="sk-block" data-sk-slot="agent"></div>'
      + '<div class="sk-block" data-sk-slot="cases"></div>'
      + '<div class="sk-block" data-sk-slot="signoff"></div>'
      + '<div class="sk-note">' + K.esc(meta.sourceNote || "") + "</div>"
      + "</div></div>";

    const box = root.querySelector("#s02-root");
    if (!box) return;

    const cardsBox = box.querySelector('[data-sk-slot="cards"]');
    const flowBox = box.querySelector('[data-sk-slot="flow"]');
    const casesBox = box.querySelector('[data-sk-slot="cases"]');
    const signoffBox = box.querySelector('[data-sk-slot="signoff"]');

    loadObjects().then(function (loaded) {
      // 屏已切走就不再写 DOM。
      if (!box.parentNode) return;
      const model = buildModel(loaded.data, meta);
      const ctxInfo = {
        sourceText: loaded.missing.length || loaded.degraded.length
          ? "部分对象降级/缺失：" + loaded.missing.concat(loaded.degraded).join("、")
          : "SceneKit.story() → T04 装载层",
        supplierName: {},
      };
      (loaded.data.suppliers || []).forEach(function (s) {
        if (s && s.supplier_id) ctxInfo.supplierName[s.supplier_id] = s.name || s.supplier_id;
      });

      const cards = buildCards(model, meta, ctxInfo);
      cardsBox.innerHTML = '<div class="section-title">立项材料自动汇总'
        + '<span class="section-extra">点击任一卡片查看口径与明细</span></div>'
        + K.numberRow(cards)
        + degradeBar(loaded.missing, loaded.degraded);
      K.bindDrills(cardsBox, openDrillByKey);

      flowBox.innerHTML = model.s2Events.length
        ? K.eventFlow(model.s2Events.map(function (ev) {
          return { ts: ev.ts, type: ev.type, text: eventText(ev), scene: ev.scene };
        }), {
          title: "立项阶段事件流",
          extra: "scene = S2 · 装载层 events",
          note: "事件字段口径见 spec-v1.md §2.10（ts / type / object_ref / payload / scene）；"
            + "本屏只回放，不新增事件。",
        })
        : '<div class="sk-degrade">' + K.icon("warn", 15)
          + '<div><b>没有取到 scene=S2 的事件。</b><div class="sk-degrade-sub">'
          + "检查 GET /api/v2/demo/objects/events 是否可用。</div></div></div>";

      // 案例检索
      casesBox.innerHTML = '<div class="section-title">相似历史案例检索'
        + '<span class="section-extra">装载层 cases · ' + K.fmtNum(model.cases.length) + " 条合成案例</span></div>"
        + '<div class="sk-search">'
        + '<span class="sk-search-icon">' + K.icon("search", 15) + "</span>"
        + '<input class="sk-search-input" type="search" id="s02-case-input" '
        + 'placeholder="' + K.esc((meta.caseView || {}).keywordPlaceholder || "输入关键词") + '" />'
        + '<button class="btn btn-sm" type="button" id="s02-case-go">检索</button>'
        + "</div>"
        + '<div class="sk-case-list" data-sk-slot="case-list"></div>';

      const listBox = casesBox.querySelector('[data-sk-slot="case-list"]');
      const input = casesBox.querySelector("#s02-case-input");
      const go = casesBox.querySelector("#s02-case-go");
      const runSearch = function () {
        renderCaseList(listBox, model.cases, meta, input ? input.value : "");
      };
      if (go) go.addEventListener("click", runSearch);
      if (input) {
        input.addEventListener("keydown", function (ev) {
          if (ev && (ev.key === "Enter" || ev.keyCode === 13)) {
            if (typeof ev.preventDefault === "function") ev.preventDefault();
            runSearch();
          }
        });
      }
      runSearch();

      // T19：检索智能体（Agentic RAG 回放）——回放未就绪时保留上面的关键词版为降级路径
      hydrateAgentBlock(box, meta);

      // 会签留痕
      const signoffMeta = meta.signoff || {};
      signoffBox.innerHTML = K.signoffCard({
        title: signoffMeta.title || "立项会签",
        slots: model.slots,
        readonly: readonly,
        note: (signoffMeta.note || "")
          + (model.signoffEvent ? "（事件 " + model.signoffEvent.event_id + " · "
            + K.fmtDate(model.signoffEvent.ts) + "）" : ""),
      });
      const signBtn = signoffBox.querySelector("[data-sk-signoff]");
      if (signBtn && !readonly) {
        signBtn.addEventListener("click", function (ev) {
          if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
          if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
          K.openDrill({
            title: "立项会签写路径",
            subtitle: "演示实例的写操作边界",
            html: kv([
              ["读路径", "本屏会签槽位与结论回放装载层事件 DEMO-EV-0005（只读）"],
              ["写路径", "真实会签走 HITL 治理层（工单 + 槽位 + 审计留痕），本演示屏不直连写接口"],
              ["原因", "演示实例独立库、只读访客为主；写操作会污染治理留痕"],
              ["下一步", "需要演示写操作时，由治理层提供演示工单端点后在本卡接入"],
            ]),
          });
        });
      }
    }).catch(function () {
      if (!box.parentNode) return;
      cardsBox.innerHTML = degradeBar(OBJECTS.slice(), []);
    });
  }

  /* ============================================================
   * 七、下钻分派
   * ============================================================ */

  function openDrillByKey(key) {
    const K = kit();
    const maker = drills[key];
    if (typeof maker === "function") {
      K.openDrill(maker());
      return;
    }
    K.openDrill({
      title: "口径说明",
      subtitle: "该数字卡未登记口径",
      html: kv([["键", String(key)], ["处理", "未登记的口径键，请检查场景屏的 drills 字典"]]),
    });
  }

  /* ============================================================
   * 八、登记
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s02] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: "S2 · 立项",
    title: "S2 · 立项",
    sub: "立项材料自动汇总 · 相似案例检索 · 立项会签",
    icon: "file",
    group: window.SceneRegistry.GROUP_NAME,
    order: 20,
    roles: window.SceneRegistry.BUSINESS_ROLES,
    render: render,
  });
})();
