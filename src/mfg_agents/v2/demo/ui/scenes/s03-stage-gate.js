/**
 * S3 · 阶段门会签屏（T11 交付物）
 *
 * 场景：`spec-v1.md` 第 3 节「阶段门（EVT / DVT / PVT）」——试产良率爬坡，
 * 每个阶段门用四条口径给出「Go / 有条件 Go / No-Go」结论，并留下会签痕。
 *
 * 本屏三条纪律：
 *  1. **记分卡的四个数全部 Web 侧现算**（良率 / CPK 取自事件载荷，直通率按工单级
 *     重新累加，问题闭环数按事件时间轴现差），不读任何静态快照——T03 重算值一致
 *     是验收项，所以这里必须真的重算。
 *  2. **门禁结论是机械判定**：四维阈值来自 `data/demo_storyline/frontend/s03-stage-gate.js`
 *     （本文件顶部 `INLINE_SCENE_DATA` 为其镜像，`tests/demo/test_s03.py` 深比对两者一致），
 *     判定规则取自 `verdict_rule`，不由文案临时拍脑袋。
 *  3. **取数走 `SceneKit.story()`（T05 契约入口，实指 `/api/v2/demo/objects/{name}`），
 *     并对同一端点保留一条屏内直连兜底**（防在线探针误判离线 / 结构不符时静默降级，
 *     详见下方 `loadObject()` 注释）；两者都拿不到才显示降级条。
 *
 * 词表：人名取自 `tools/demo_gen/wordlist.json`，全部虚构。
 */
(function () {
  "use strict";

  const SCENE_ID = "s03";

  /**
   * 场景派生配置——与 `data/demo_storyline/frontend/s03-stage-gate.js` 的
   * `window.SCENE_DATA["s03"]` **同构镜像**（test_s03.py 逐值深比对，两处漂移即变红）。
   *
   * 为什么要在屏内内联一份：`/demo-story/{file}` 挂载点已由 T10 落地，
   * 但 `index.html` 未引入 `frontend/sNN-*.js` 的 script 标签，浏览器实际取不到该文件。
   * T09（S4 屏）已验证同一处理：内联同构兜底并在缺位时登记
   * `window.SCENE_DATA['s03']`，契约文件保持为唯一权威副本。
   *
   * 阈值出处：良率下限 = 操作细则第八条（PVT 试产良率一般不低于 95%）+ NPI 试产良率
   * 爬坡经验（EVT/DVT 逐级抬升）；CPK 下限 = spec-v1.md §3（DVT：CPK≥1.33）；
   * 直通率下限 = spec-v1.md §3（PVT：直通率≥98%）。
   */
  const INLINE_SCENE_DATA = {
    "scene": "s03",
    "title": "S3 · 阶段门会签",
    "sub": "EVT / DVT / PVT 记分卡 · 门禁机械判定 · 阶段门会签",
    "stage_order": [
      "EVT",
      "DVT",
      "PVT"
    ],
    "gate": {
      "yield_min": {
        "EVT": 0.9,
        "DVT": 0.93,
        "PVT": 0.95
      },
      "cpk_min": 1.33,
      "fpy_min": {
        "EVT": 0.95,
        "DVT": 0.96,
        "PVT": 0.98
      }
    },
    "verdict_rule": "四维（良率 / CPK / 直通率 / 问题闭环数）全部达标 → Go；恰 1 项不达标且无未决高风险 → 有条件 Go；其余 → No-Go",
    "threshold_sources": {
      "yield_min": "操作细则第八条（PVT 试产良率一般不低于 95%）+ NPI 试产良率爬坡经验（EVT/DVT 逐级抬升）",
      "cpk_min": "spec-v1.md §3（DVT：CPK≥1.33）",
      "fpy_min": "spec-v1.md §3（PVT：直通率≥98%）",
      "closed_min": "阶段门放行前置：无未决高风险 / 无未决问题"
    },
    "object_source": [
      "events",
      "sales_orders",
      "work_orders",
      "inspections"
    ],
    "signoff_slot_labels": {
      "rd": "研发",
      "pqe": "品质工程(PQE)",
      "pln": "计划"
    },
    "signoff_users": {
      "rd": "陈晨",
      "pqe": "赵拓",
      "pln": "周航"
    },
    "agent_layer": {
      "replay": "s03_gate_faq",
      "kind": "kbqa",
      "title": "门禁准则问答（制度库直答）",
      "source": "制度库 FAQ · DEMO-FAQ-007（Go 的四项条件）",
      "degrade": "制度问答回放未就绪，不展示编造答案"
    }
  };

  /** 取场景配置：优先已加载的 `window.SCENE_DATA['s03']`，缺位时登记内联镜像。 */
  function sceneData() {
    const bag = window.SCENE_DATA;
    if (!bag || typeof bag !== "object") window.SCENE_DATA = {};
    if (!window.SCENE_DATA[SCENE_ID]) window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA;
    return window.SCENE_DATA[SCENE_ID];
  }

  const CFG = sceneData();
  const STAGE_ORDER = CFG.stage_order;
  const GATE = CFG.gate;
  const VERDICT_RULE = CFG.verdict_rule;
  const SLOT_LABELS = CFG.signoff_slot_labels;
  const SLOT_USERS = CFG.signoff_users;

  /** 当前屏的口径字典（键 = 下钻 `data-sk-drill`，值 = 返回弹层参数）。 */
  let drills = {};

  /** 渲染上下文（`loadObject` 兜底直连时要用它的后端基址）。 */
  let ctxRef = null;

  /* ============================================================
   * 小工具
   * ============================================================ */

  function kv(pairs) {
    const K = window.SceneKit;
    const rows = (pairs || []).map(function (pair) {
      return '<div class="sk-kv-row"><span class="sk-kv-k">' + K.esc(pair[0])
        + '</span><span class="sk-kv-v">' + K.esc(pair[1]) + "</span></div>";
    }).join("");
    return '<div class="sk-kv">' + rows + "</div>";
  }

  function isNum(v) {
    return typeof v === "number" && Number.isFinite(v);
  }

  function pct(v, digits) {
    const K = window.SceneKit;
    if (!isNum(v)) return "—";
    return K.fmtNum(v * 100, digits === undefined ? 1 : digits) + "%";
  }

  function thresholdText(stage, kind) {
    if (kind === "yield") return "阈值 ≥ " + pct(GATE.yield_min[stage]);
    if (kind === "fpy") return "阈值 ≥ " + pct(GATE.fpy_min[stage]);
    return "阈值 ≥ " + window.SceneKit.fmtNum(GATE.cpk_min, 2);
  }

  function verdictBadge(verdict) {
    const K = window.SceneKit;
    const v = String(verdict || "");
    if (v === "Go") return K.badge("Go", "badge-green");
    if (v === "有条件 Go") return K.badge("有条件 Go", "badge-amber");
    if (v === "No-Go") return K.badge("No-Go", "badge-red");
    return K.badge(v || "未判定", "badge-gray");
  }

  function readonlyBadge(readonly) {
    const K = window.SceneKit;
    return readonly
      ? K.badge("只读访客 · 记分卡与门禁结论只读", "badge-gray")
      : K.badge("可写岗位 · 本屏只读（会签写操作在治理窗口）", "badge-blue");
  }

  /* ============================================================
   * 取数：Contract 入口 + 端点兜底
   * ============================================================ */

  /**
   * 直连兜底：`GET <base>/api/v2/demo/objects/{name}`（与 `SceneKit.story()` 同端点）。
   *
   * 为什么需要：`SceneKit.story()` 在 `api.online()` 探针判离线、或响应结构不合 rows
   * 约定时会**静默降级**（返回 ok:false 或走 `window.DEMO_STORY`）。本屏不依赖探针结论，
   * 对同一端点保留一条直连兜底；`story()` 正常时本函数不会被调用（零额外请求）。
   * 注：早期 `story()` 误打 `/api/v2/demo/storyline/{name}`（实测 404），该问题已由 T05
   * 修正为 `/objects/{name}`（见现网 `scene-kit.js`），本屏兜底随之退居备用。
   */
  function directFetch(name) {
    const api = (ctxRef && ctxRef.api) || (window.App && window.App.api) || null;
    if (!api || typeof api.base !== "function") return Promise.resolve(null);
    let base = "";
    try { base = api.base() || ""; } catch (err) { base = ""; }
    if (!base || typeof fetch !== "function") return Promise.resolve(null);
    const url = base + "/api/v2/demo/objects/" + encodeURIComponent(name);
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp || !resp.ok) throw new Error("HTTP " + (resp ? resp.status : 0));
        return resp.json();
      })
      .then(function (payload) {
        const rows = payload && Array.isArray(payload.rows) ? payload.rows : null;
        if (!rows) return null;
        return { ok: true, rows: rows, source: "api", note: "直连 /api/v2/demo/objects/" + name };
      })
      .catch(function () { return null; });
  }

  function loadObject(name) {
    const K = window.SceneKit;
    return K.story(name).then(function (res) {
      if (res && res.ok === true && Array.isArray(res.rows)) return res;
      return directFetch(name).then(function (alt) {
        return alt || (res || { ok: false, rows: [], error: "演示装载层未就绪" });
      });
    }).catch(function () {
      return { ok: false, rows: [], error: "演示装载层未就绪" };
    });
  }

  /* ============================================================
   * 派生：记分卡四维 + 门禁机械判定
   * ============================================================ */

  /** 主线订单：取销售订单中行项总量最大的一张（演示故事有且只有一张主单）。 */
  function mainOrderOf(orders) {
    let best = null;
    let bestQty = -1;
    (orders || []).forEach(function (o) {
      const qty = (Array.isArray(o && o.lines) ? o.lines : []).reduce(function (acc, line) {
        const q = Number(line && line.qty);
        return acc + (Number.isFinite(q) ? q : 0);
      }, 0);
      if (qty > bestQty) { bestQty = qty; best = o; }
    });
    return best;
  }

  /**
   * 事件时间轴上的「问题闭环数 / 未决问题数」——按事件时间戳现差，不做预测。
   *
   * 口径：问题 = `alert`（非 closed）+ `complaint`；闭环 = `alert(kind=closed)`
   * + `hitl(result ∈ {closed, 8D 闭环})`。截至某阶段 = 只统计 `ts ≤ 阶段时间` 的事件，
   * 之后发生的问题不属于该阶段门的判定依据。
   */
  function issuesAt(events, ts) {
    let opened = 0;
    let closed = 0;
    (events || []).forEach(function (e) {
      if (!e || String(e.ts) > String(ts)) return;
      const p = e.payload || {};
      if (e.type === "alert" && p.kind !== "closed") opened += 1;
      if (e.type === "complaint") opened += 1;
      if (e.type === "alert" && p.kind === "closed") closed += 1;
      if (e.type === "hitl" && (p.result === "closed" || p.result === "8D 闭环")) closed += 1;
    });
    return { opened: opened, closed: closed, open: Math.max(0, opened - closed) };
  }

  /**
   * 机械门禁判定。返回 `{ checks, fails, verdict }`：
   * `checks` 逐条给出取值 / 阈值 / 是否达标（记分卡表格与下钻弹层共用同一份数据）。
   */
  function gateOf(stage, stageMetrics, fpyOrder, issues) {
    const m = stageMetrics[stage] || {};
    const closedPass = issues.open === 0;
    const checks = [
      {
        key: "yield", label: "良率", value: m.fpy, min: GATE.yield_min[stage],
        pass: isNum(m.fpy) && m.fpy >= GATE.yield_min[stage],
        text: pct(m.fpy), hint: thresholdText(stage, "yield"),
      },
      {
        key: "cpk", label: "CPK", value: m.cpk, min: GATE.cpk_min,
        pass: isNum(m.cpk) && m.cpk >= GATE.cpk_min,
        text: isNum(m.cpk) ? window.SceneKit.fmtNum(m.cpk, 2) : "—",
        hint: thresholdText(stage, "cpk"),
      },
      {
        key: "fpy", label: "直通率", value: fpyOrder, min: GATE.fpy_min[stage],
        pass: isNum(fpyOrder) && fpyOrder >= GATE.fpy_min[stage],
        text: pct(fpyOrder, 2), hint: thresholdText(stage, "fpy") + "（工单级口径）",
      },
      {
        key: "closed", label: "问题闭环数", value: issues.closed, min: null,
        pass: closedPass, text: window.SceneKit.fmtNum(issues.closed) + " 项",
        hint: closedPass ? "已闭环 " + issues.closed + " · 未决 0（放行前置：无未决问题）"
          : "已闭环 " + issues.closed + " · 未决 " + issues.open + "（存在未决问题）",
      },
    ];
    const fails = checks.filter(function (c) { return !c.pass; }).length;
    // 「未决高风险」在演示数据里没有独立标注位，恒为 false——规则保留该分支便于治理窗口接管。
    const hasOpenHighRisk = false;
    let verdict = "No-Go";
    if (fails === 0) verdict = "Go";
    else if (fails === 1 && !hasOpenHighRisk) verdict = "有条件 Go";
    return { checks: checks, fails: fails, verdict: verdict };
  }

  function derive(data) {
    const events = data.events || [];
    const s3 = events.filter(function (e) { return e && e.scene === "S3"; });

    const metrics = {};
    const signoffs = {};
    STAGE_ORDER.forEach(function (n) { metrics[n] = {}; signoffs[n] = null; });
    s3.forEach(function (e) {
      const p = e.payload || {};
      const n = p.stage;
      if (STAGE_ORDER.indexOf(n) < 0) return;
      if (e.type === "inspection") {
        metrics[n] = { cpk: Number(p.cpk), fpy: Number(p.fpy), verdict: p.verdict || "", ts: e.ts, event_id: e.event_id };
      }
      if (e.type === "hitl") {
        signoffs[n] = { slots: Array.isArray(p.slots) ? p.slots : [], result: p.result || "", ts: e.ts, note: p.note || "" };
      }
    });

    // 直通率（工单级）：Σ qty_good / Σ qty_planned，仅统计主线订单下已开工的工单
    // （status !== released），与 spec-v1.md §2.6 / §5 工单级口径一致。
    const mainOrder = mainOrderOf(data.sales_orders);
    const orderId = mainOrder ? mainOrder.order_id : null;
    const wos = (data.work_orders || []).filter(function (w) {
      return w && orderId && w.order_id === orderId && w.status !== "released";
    });
    let good = 0;
    let plan = 0;
    wos.forEach(function (w) {
      const g = Number(w.qty_good);
      const p = Number(w.qty_planned);
      if (Number.isFinite(g)) good += g;
      if (Number.isFinite(p)) plan += p;
    });
    const fpyOrder = plan > 0 ? good / plan : null;

    // 主线相关检验批：用于「可下钻到检验批」，取主线订单下工单命中的批次 + IQC 来料批。
    const woIds = wos.map(function (w) { return w.wo_id; });
    const batches = (data.inspections || []).filter(function (ib) {
      return ib && (ib.wo_id === "-" || woIds.indexOf(ib.wo_id) >= 0);
    });

    const gates = {};
    STAGE_ORDER.forEach(function (n) {
      const issues = metrics[n] && metrics[n].ts ? issuesAt(events, metrics[n].ts) : { opened: 0, closed: 0, open: 0 };
      gates[n] = gateOf(n, metrics, fpyOrder, issues);
      gates[n].issues = issues;
    });

    return {
      s3: s3, metrics: metrics, signoffs: signoffs, mainOrder: mainOrder,
      wos: wos, fpyOrder: fpyOrder, batches: batches, gates: gates,
      events: events,
    };
  }

  /* ============================================================
   * 渲染片段
   * ============================================================ */

  function cellHtml(cell, key) {
    const K = window.SceneKit;
    const tone = cell.pass ? "sk-tone-green" : "sk-tone-red";
    return '<td class="' + tone + '" data-sk-drill="' + K.esc(key)
      + '" role="button" tabindex="0" style="padding:10px 12px;'
      + 'border-bottom:1px solid var(--border);cursor:pointer;vertical-align:top">'
      + '<div style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">'
      + '<span class="sk-num-value" style="font-size: calc(17px * var(--font-scale))">' + K.esc(cell.text) + "</span>"
      + K.badge(cell.pass ? "达标" : "不达标", cell.pass ? "badge-green" : "badge-red")
      + "</div>"
      + '<div class="sk-num-hint">' + K.esc(cell.hint) + "</div></td>";
  }

  function matrixHtml(d) {
    const K = window.SceneKit;
    const headCells = ["阶段门", "良率", "CPK", "直通率（工单级）", "问题闭环数", "门禁结论"]
      .map(function (t) {
        return '<th style="text-align:left;padding:9px 12px;font-size: calc(12px * var(--font-scale));color:var(--text-2);'
          + 'font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap">' + K.esc(t) + "</th>";
      }).join("");

    const rows = STAGE_ORDER.map(function (n) {
      const g = d.gates[n];
      const m = d.metrics[n] || {};
      const byKey = {};
      g.checks.forEach(function (c) { byKey[c.key] = c; });
      const head = '<td style="padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top">'
        + '<div style="font-size: calc(13px * var(--font-scale));font-weight:700;color:var(--text)">' + K.esc(n) + " 阶段门</div>"
        + '<div class="sk-num-hint">' + K.esc(K.fmtDate(m.ts || "")) + "</div></td>";
      const verdictCell = '<td data-sk-drill="' + K.esc("verdict-" + n)
        + '" role="button" tabindex="0" style="padding:10px 12px;border-bottom:1px solid var(--border);'
        + 'cursor:pointer;vertical-align:top">' + verdictBadge(g.verdict)
        + '<div class="sk-num-hint">' + K.esc(g.fails === 0 ? "四维全部达标" : "不达标 " + g.fails + " 项") + "</div></td>";
      return "<tr>"
        + head
        + cellHtml(byKey.yield, "yield-" + n)
        + cellHtml(byKey.cpk, "cpk-" + n)
        + cellHtml(byKey.fpy, "fpy-" + n)
        + cellHtml(byKey.closed, "closed-" + n)
        + verdictCell
        + "</tr>";
    }).join("");

    return '<div style="overflow-x:auto;padding-top:10px">'
      + '<table style="border-collapse:collapse;width:100%;min-width:760px">'
      + "<thead><tr>" + headCells + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  function ruleHtml() {
    const K = window.SceneKit;
    const rows = STAGE_ORDER.map(function (n) {
      return "<tr><td style=\"padding:8px 12px;border-bottom:1px solid var(--border);font-weight:600\">"
        + K.esc(n) + '</td><td style="padding:8px 12px;border-bottom:1px solid var(--border)">'
        + K.esc("≥ " + pct(GATE.yield_min[n])) + '</td>'
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">'
        + K.esc("≥ " + K.fmtNum(GATE.cpk_min, 2)) + '</td>'
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">'
        + K.esc("≥ " + pct(GATE.fpy_min[n])) + '</td>'
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">'
        + K.esc("未决 0") + "</td></tr>";
    }).join("");
    return '<div class="card">'
      + '<div class="card-head"><div class="card-title">' + K.icon("shield", 15) + "门禁机械判定规则"
      + '</div><div class="card-extra">' + K.badge("非人工评审 · 逐格可复核", "badge-blue") + "</div></div>"
      + '<div class="card-body" style="padding:12px 16px 16px">'
      + '<div style="font-size: calc(12.6px * var(--font-scale));color:var(--text);line-height:1.7">' + K.esc(VERDICT_RULE) + "</div>"
      + '<div style="overflow-x:auto;margin-top:10px">'
      + '<table style="border-collapse:collapse;width:100%;min-width:520px">'
      + '<thead><tr>'
      + ["阶段门", "良率下限", "CPK 下限", "直通率下限", "问题"]
        .map(function (t) {
          return '<th style="text-align:left;padding:8px 12px;font-size: calc(12px * var(--font-scale));color:var(--text-2);'
            + 'font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap">' + K.esc(t) + "</th>";
        }).join("")
      + "</tr></thead><tbody>" + rows + "</tbody></table></div>"
      + '<div class="sk-note">阈值来源：良率下限 = 操作细则第八条（PVT 试产良率一般不低于 95%）+ NPI 试产良率爬坡经验；'
      + "CPK 下限 = spec-v1.md §3（DVT：CPK≥1.33）；直通率下限 = spec-v1.md §3（PVT：直通率≥98%）。"
      + "「未决高风险」分支在演示数据中无独立标注位，恒为无风险，交由治理窗口接管。</div>"
      + "</div></div>";
  }

  function signoffHtml(d, readonly) {
    const K = window.SceneKit;
    return STAGE_ORDER.map(function (n) {
      const sf = d.signoffs[n];
      if (!sf) return "";
      const slots = (sf.slots || []).map(function (key) {
        return {
          role: SLOT_LABELS[key] || String(key),
          user: SLOT_USERS[key] || "—",
          status: sf.result === "Go" ? "approved" : (sf.result === "No-Go" ? "rejected" : "pending"),
          ts: sf.ts,
        };
      });
      return '<div class="sk-block">' + K.signoffCard({
        title: n + " 阶段门会签（" + (sf.note || "") + "）",
        slots: slots,
        // 本屏不发生写操作：真实门禁会签写接口尚未接入治理链路，
        // 这里统一置只读，避免出现绕过治理的旁路写操作。
        readonly: true,
        note: "会签留痕来源：events 对象中 scene=S3 / type=hitl 的事件（槽位 rd / pqe / pln，结果 "
          + (sf.result || "—") + "）。本屏不提供写按钮：真实门禁会签写操作应在治理窗口"
          + "（待办中心 / HITL）执行；写链路未接入前保持只读，避免无治理的旁路写入。"
          + (readonly ? "（当前岗位为只读访客）" : ""),
      }) + "</div>";
    }).join("");
  }

  function flowEvents(d) {
    return d.s3.map(function (e) {
      const p = e.payload || {};
      let text = "";
      if (e.type === "inspection") {
        text = (p.stage || "?") + " 阶段门：CPK " + window.SceneKit.fmtNum(p.cpk, 2)
          + " · 良率 " + pct(Number(p.fpy)) + " · T02 判定 " + (p.verdict || "—");
      } else if (e.type === "hitl") {
        text = (p.stage || "?") + " 阶段门会签：结果 " + (p.result || "—")
          + "（槽位 " + (Array.isArray(p.slots) ? p.slots.join(" / ") : "—") + "）";
      } else {
        text = (p.note || "") || (e.type || "事件");
      }
      return { ts: e.ts, type: e.type, text: text, scene: e.scene };
    });
  }

  function degradeHtml(state) {
    const K = window.SceneKit;
    const missing = Object.keys(state.loaded).filter(function (k) {
      return state.loaded[k] !== true;
    });
    return '<div class="sk-degrade">' + K.icon("warn", 15)
      + "<div><b>演示装载层未就绪：本屏不显示编造数字。</b>"
      + '<div class="sk-degrade-sub">缺失对象：' + K.esc(missing.join("、") || "（未知）")
      + "。恢复条件：GET /api/v2/demo/storyline/{object}"
      + "（T05 契约端点）或 GET /api/v2/demo/objects/{object}（T04 当前实现）可用一行数据。</div>"
      + "</div></div>";
  }

  function bodyHtml(d, readonly, state) {
    const K = window.SceneKit;
    if (state.loaded.events !== true || !d.s3.length) {
      return degradeHtml(state);
    }
    const passed = STAGE_ORDER.filter(function (n) { return d.gates[n].verdict === "Go"; }).length;
    const openIssues = STAGE_ORDER.reduce(function (acc, n) {
      return Math.max(acc, d.gates[n].issues.open);
    }, 0);

    const cards = [
      {
        label: "阶段门数", value: STAGE_ORDER.length, unit: "道",
        hint: "口径：EVT / DVT / PVT 三道试产门（spec-v1.md §3）", tone: "blue", drill: "ov-stages",
      },
      {
        label: "主线直通率", value: pct(d.fpyOrder, 2), unit: "",
        hint: "工单级：Σ qty_good / Σ qty_planned（不可用则显示 —）",
        tone: isNum(d.fpyOrder) && d.fpyOrder >= GATE.fpy_min.PVT ? "green" : "red", drill: "ov-fpy",
      },
      {
        label: "判定 Go 的门数", value: passed + " / " + STAGE_ORDER.length, unit: "",
        hint: "机械判定：四维全达标才算 Go", tone: passed === STAGE_ORDER.length ? "green" : "amber",
        drill: "ov-pass",
      },
      {
        label: "未决问题", value: openIssues, unit: "项",
        hint: "截至各阶段：问题事件 − 闭环事件（放行前置）",
        tone: openIssues === 0 ? "green" : "red", drill: "ov-open",
      },
    ];

    return '<div class="sk-block">'
      + '<div class="section-title">阶段门概览<span class="section-extra">'
      + "四个数均 Web 侧现算 · 点击查看口径与来源</span></div>"
      + K.numberRow(cards)
      + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">阶段门记分卡（EVT / DVT / PVT）'
      + '<span class="section-extra" style="display:inline-flex;align-items:center;gap:8px">'
      + "良率 / CPK 取阶段事件载荷 · 直通率工单级重算 · 问题闭环按时间轴现差"
      + '<button class="btn btn-sm btn-outline" type="button" data-sk-drill="batches">检验批口径（'
      + d.batches.length + ' 批）</button></span></div>'
      + matrixHtml(d)
      + '<div class="sk-note">每格可点开：给出取值、阈值、口径、来源对象与算法，并附可下拉的检验批'
      + "（批号 / 抽检数 / 缺陷数）。四个数与 T03 重算值一致——本屏不做二次取整或美化。</div>"
      + "</div>"
      + '<div class="sk-block">' + ruleHtml() + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">阶段门会签流<span class="section-extra">'
      + "留痕来自 events（scene=S3 / type=hitl）</span></div>"
      + signoffHtml(d, readonly)
      + "</div>"
      + '<div class="sk-block">'
      + K.eventFlow(flowEvents(d), {
        title: "S3 事件流（按时间升序）",
        extra: "来源：events 对象 · scene=S3",
        note: "共 " + d.s3.length + " 条 S3 事件（检验 " 
          + d.s3.filter(function (e) { return e.type === "inspection"; }).length + " 条 / 会签 "
          + d.s3.filter(function (e) { return e.type === "hitl"; }).length + " 条）。"
          + "事件字段口径见 spec-v1.md §2.10。",
      })
      + '<div class="sk-block" id="s03-agent" data-channel="faq"></div>'
      + "</div>";
  }

  /* ---------- T19：门禁准则问答（制度库直答，轻形态） ---------- */
  function hydrateAgentBlock(box) {
    const K = window.SceneKit;
    const el = box ? box.querySelector("#s03-agent") : null;
    const a = (CFG && CFG.agent_layer) || null;
    if (!el || !a || !K) return;
    K.replay(a.replay).then(function (res) {
      if (!el.parentNode) return;
      if (!res || !res.ok || !res.package) {
        el.innerHTML = '<div class="sk-degrade" data-sk-s03-agent-degrade="1">'
          + K.esc(a.degrade || "制度问答回放未就绪，不展示编造答案") + "</div>";
        return;
      }
      const pkg = res.package;
      el.innerHTML = '<div class="section-title">' + K.esc(a.title)
        + '<span class="section-extra">' + K.esc(a.source || "") + "</span></div>"
        + K.agentPanel({
          title: pkg.title, agents: pkg.agents, steps: pkg.steps,
          conclusion: pkg.conclusion, source: "replay",
        })
        + '<div class="sk-note">来源徽标「演示回放」不可去除；门禁阈值口径见记分卡，'
        + "制度出处见 FAQ 编号（命中即直答）。</div>";
    });
  }

  /* ============================================================
   * 下钻口径字典
   * ============================================================ */

  function batchPairs(d) {
    if (!d.batches.length) return [["检验批", "（无主线相关检验批）"]];
    return d.batches.slice(0, 6).map(function (ib) {
      const s = Number(ib.sample_qty);
      const def = Number(ib.defect_qty);
      const rate = Number.isFinite(s) && s > 0 && Number.isFinite(def) ? 1 - def / s : null;
      return [ib.batch_id, (ib.stage || "—") + " · 抽检 " + window.SceneKit.fmtNum(s)
        + " · 缺陷 " + window.SceneKit.fmtNum(def) + " · 批直通 " + pct(rate, 2)
        + " · 检验员 " + (ib.inspector || "—")];
    });
  }

  function registerDrills(d, state) {
    const K = window.SceneKit;
    const sourceOf = function (name) {
      return state.loaded[name] === true
        ? "GET /api/v2/demo/storyline/" + name + " 或 /api/v2/demo/objects/" + name
        : "（未取到：" + name + " 装载层未就绪）";
    };

    drills = {};

    drills["ov-stages"] = function () {
      return {
        title: "阶段门数", subtitle: "口径 · 来源",
        html: kv([
          ["取值", String(STAGE_ORDER.length) + " 道"],
          ["口径", "试产阶段门 EVT / DVT / PVT（spec-v1.md §3）"],
          ["来源", "events 对象中 scene=S3 且 type=inspection 的事件（按 payload.stage 去重）"],
          ["阶段清单", STAGE_ORDER.join("、") + "（阈值镜像 data/demo_storyline/frontend/s03-stage-gate.js）"],
        ]),
      };
    };

    drills["ov-fpy"] = function () {
      return {
        title: "主线直通率（工单级）", subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", pct(d.fpyOrder, 2)],
          ["口径", "spec-v1.md §2.6：直通率 = 良品数 / 计划投产数（工单级）"],
          ["算法", "Σ work_orders[].qty_good ÷ Σ work_orders[].qty_planned（仅主线订单、status ≠ released）"],
          ["来源", sourceOf("work_orders") + "；主线订单取自 " + sourceOf("sales_orders")],
          ["分子 / 分母", K.fmtNum(d.sums ? d.sums.good : 0) + " ÷ " + K.fmtNum(d.sums ? d.sums.plan : 0)],
          ["纳入工单", d.wos.length ? d.wos.map(function (w) {
            return w.wo_id + "（" + w.status + "）";
          }).join("、") : "（无）"],
        ]),
      };
    };

    drills["ov-pass"] = function () {
      return {
        title: "判定 Go 的门数", subtitle: "口径 · 规则 · 逐门结论",
        html: kv(STAGE_ORDER.map(function (n) {
          const g = d.gates[n];
          return [n, g.verdict + "（不达标 " + g.fails + " 项）"];
        }).concat([["规则", VERDICT_RULE]])),
      };
    };

    drills["ov-open"] = function () {
      return {
        title: "未决问题", subtitle: "口径 · 算法",
        html: kv([
          ["取值", String(STAGE_ORDER.reduce(function (acc, n) {
            return Math.max(acc, d.gates[n].issues.open);
          }, 0)) + " 项（取各阶段最大值）"],
          ["口径", "问题 = alert（非 closed）+ complaint；闭环 = alert(kind=closed) + hitl(result ∈ closed / 8D 闭环)"],
          ["算法", "只统计 ts ≤ 该阶段门时间的事件（之后发生的问题不计入该阶段判定）"],
          ["来源", sourceOf("events")],
          ["各阶段", STAGE_ORDER.map(function (n) {
            const s = d.gates[n].issues;
            return n + "：发生 " + s.opened + " · 闭环 " + s.closed + " · 未决 " + s.open;
          }).join("；")],
        ]),
      };
    };

    STAGE_ORDER.forEach(function (n) {
      const g = d.gates[n];
      const m = d.metrics[n] || {};
      const sf = d.signoffs[n] || {};
      const byKey = {};
      g.checks.forEach(function (c) { byKey[c.key] = c; });

      drills["yield-" + n] = function () {
        return {
          title: n + " 阶段门 · 良率（试产良率）", subtitle: "口径 · 来源 · 算法 · 检验批",
          html: kv([
            ["取值", byKey.yield.text + "（" + (byKey.yield.pass ? "达标" : "不达标") + "）"],
            ["阈值", thresholdText(n, "yield")],
            ["口径", "试产良率 = 阶段试产良品数 / 投入数，按阶段门口径取事件载荷 fpy"],
            ["来源", "events 事件 " + (m.event_id || "—") + "（scene=S3 / type=inspection / stage=" + n + "）"],
            ["阶段时间", K.fmtDate(m.ts || "")],
          ].concat(batchPairs(d))),
        };
      };

      drills["cpk-" + n] = function () {
        return {
          title: n + " 阶段门 · CPK", subtitle: "口径 · 来源 · 算法 · 检验批",
          html: kv([
            ["取值", byKey.cpk.text + "（" + (byKey.cpk.pass ? "达标" : "不达标") + "）"],
            ["阈值", thresholdText(n, "cpk")],
            ["口径", "双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R，R̄/d2）；Pp/Ppk 用整体 σ（ddof=1），严格保证 Pp ≠ Cp"],
            ["来源", "events 事件 " + (m.event_id || "—") + " 载荷 cpk；过程数据见 spc_series 对象"],
            ["阶段时间", K.fmtDate(m.ts || "")],
          ].concat(batchPairs(d))),
        };
      };

      drills["fpy-" + n] = function () {
        return {
          title: n + " 阶段门 · 直通率（工单级）", subtitle: "口径 · 来源 · 算法 · 检验批",
          html: kv([
            ["取值", byKey.fpy.text + "（" + (byKey.fpy.pass ? "达标" : "不达标") + "）"],
            ["阈值", thresholdText(n, "fpy")],
            ["口径", "工单级口径，与阶段无关：主线订单整体的良品数 / 计划投产数"],
            ["算法", "Σ work_orders[].qty_good ÷ Σ work_orders[].qty_planned（status ≠ released）"],
            ["来源", sourceOf("work_orders")],
          ].concat(batchPairs(d))),
        };
      };

      drills["closed-" + n] = function () {
        const s = g.issues;
        return {
          title: n + " 阶段门 · 问题闭环数", subtitle: "口径 · 来源 · 算法",
          html: kv([
            ["取值", byKey.closed.text + "（" + (byKey.closed.pass ? "达标" : "不达标") + "）"],
            ["阈值", "放行前置：未决问题数 = 0"],
            ["口径", "截至阶段时间已闭环的问题事件数；未决 = 发生 − 闭环"],
            ["算法", "只统计 ts ≤ " + K.fmtDate(m.ts || "") + " 的事件"],
            ["结果", "发生 " + s.opened + " · 闭环 " + s.closed + " · 未决 " + s.open],
            ["来源", sourceOf("events")],
          ]),
        };
      };

      drills["verdict-" + n] = function () {
        return {
          title: n + " 阶段门 · 门禁结论", subtitle: "机械判定 · 逐维取值",
          html: kv(g.checks.map(function (c) {
            return [c.label, c.text + "　" + (c.pass ? "达标" : "不达标") + "　（" + c.hint + "）"];
          }).concat([
            ["不达标项数", String(g.fails)],
            ["规则", VERDICT_RULE],
            ["结论", g.verdict],
            ["T02 载荷判定", (m.verdict || "—") + "（与机械判定一致是 T11 验收项）"],
            ["会签留痕", (sf.slots || []).map(function (k) { return SLOT_LABELS[k] || k; }).join(" / ")
              + " · 结果 " + (sf.result || "—")],
          ])),
        };
      };
    });

    drills["batches"] = function () {
      return {
        title: "主线相关检验批", subtitle: "检验批口径 · 来源",
        html: kv([
          ["口径", "检验批直通率 = 1 − defect_qty / sample_qty（批级，与工单级直通率不同口径）"],
          ["来源", sourceOf("inspections")],
          ["纳入规则", "wo_id 命中主线工单，或 IQC 来料批（wo_id = -）"],
        ].concat(batchPairs(d))),
      };
    };
  }

  function openDrillByKey(key) {
    const K = window.SceneKit;
    const maker = drills[key];
    if (typeof maker === "function") {
      K.openDrill(maker());
      return;
    }
    K.openDrill({
      title: "口径说明", subtitle: "该格未登记口径",
      html: kv([["键", String(key)], ["处理", "未登记的口径键，请检查场景屏的 drills 字典"]]),
    });
  }

  /* ============================================================
   * 渲染
   * ============================================================ */

  function render(root, ctx) {
    const K = window.SceneKit;
    if (!root) return;
    if (!K) {
      root.innerHTML = '<div class="content-inner"><div class="empty">'
        + "组件契约未加载：scene-kit.js 必须在场景脚本之前引入（window.SceneKit 缺失）"
        + "</div></div>";
      return;
    }
    K.use(ctx);
    ctxRef = ctx || null;

    const readonly = K.isReadonly(ctx);
    const state = { loaded: {}, sources: {} };

    root.innerHTML = '<div class="content-inner"><div id="s03-root">'
      + K.pageHead({
        icon: "flask",
        title: "S3 · 阶段门会签（EVT / DVT / PVT）",
        desc: "试产阶段门记分卡：良率 / CPK / 直通率 / 问题闭环数四维现算，门禁结论机械判定，"
          + "并保留阶段门会签留痕。四个数均可下钻到口径、算法与检验批。",
        extra: K.badge("T11 验收屏", "badge-blue")
          + K.badge("门禁机械判定 · 逐格可复核", "badge-gray")
          + readonlyBadge(readonly),
      })
      + '<div class="sk-block" data-sk-slot="body">'
      + '<div class="empty">' + K.spinner("lg") + "<div style=\"margin-top:10px\">正在核对装载层与重算记分卡…</div></div>"
      + "</div>"
      + '<div class="sk-note">数据纪律：四维取值全部 Web 侧现算或取事件载荷，不使用静态快照；'
      + "取不到对象时显示降级条，不编数字。</div>"
      + "</div></div>";

    const bodyBox = root.querySelector('[data-sk-slot="body"]');
    const names = ["events", "sales_orders", "work_orders", "inspections"];

    Promise.all(names.map(function (name) {
      return loadObject(name).then(function (res) {
        const ok = res && res.ok === true && Array.isArray(res.rows);
        state.loaded[name] = ok;
        state.sources[name] = ok ? (res.source || "api") : (res && res.error ? res.error : "未就绪");
        return { name: name, rows: ok ? res.rows : [] };
      });
    })).then(function (results) {
      if (!bodyBox || !bodyBox.parentNode) return; // 屏已切走：不再写 DOM
      const data = {};
      results.forEach(function (r) { data[r.name] = r.rows; });
      const d = derive(data);

      // 直通率分子分母补进 d，供口径弹层展示（derive 里已在 gateOf 之外单独算过）
      const good = d.wos.reduce(function (acc, w) {
        const v = Number(w.qty_good); return acc + (Number.isFinite(v) ? v : 0);
      }, 0);
      const plan = d.wos.reduce(function (acc, w) {
        const v = Number(w.qty_planned); return acc + (Number.isFinite(v) ? v : 0);
      }, 0);
      d.sums = { good: good, plan: plan };

      registerDrills(d, state);
      bodyBox.innerHTML = bodyHtml(d, readonly, state);
      K.bindDrills(bodyBox, openDrillByKey);
      hydrateAgentBlock(bodyBox);
    }).catch(function () {
      if (!bodyBox || !bodyBox.parentNode) return;
      state.loaded.events = false;
      bodyBox.innerHTML = degradeHtml(state);
    });
  }

  /* ============================================================
   * 登记
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s03] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: "S3 · 阶段门会签",
    title: "S3 · 阶段门会签（EVT / DVT / PVT）",
    sub: "阶段门记分卡 · 门禁机械判定 · 会签留痕",
    icon: "flask",
    group: window.SceneRegistry.GROUP_NAME,
    order: 30,
    roles: window.SceneRegistry.BUSINESS_ROLES,
    render: render,
  });
})();
