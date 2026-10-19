/**
 * S6 · 采购执行屏（T08 交付物）
 *
 * 场景：`spec-v1.md` 第 3 节第 15 行——PO 下发 → 供应商交期确认 → 缺料预警
 * （料期 vs 工排计划比对）→ 自动生成催料任务进采购员待办 → 延期升级链 → 催料留痕表。
 *
 * 本屏三条纪律：
 *  1. **预警到待办全自动、零人工步骤是验收项**：链路时间戳全部取自 events 对象
 *     （`DEMO-EV-0027` 缺料预警 → `DEMO-EV-0028` 催料任务），本屏现算两者间隔并
 *     在界面上写明；间隔不为 0 就说明链路里混进了人工环节，会当场暴露。
 *  2. **缺口是机械判定**：口径（未交付 + 料期晚于工排需料日）与升级阈值来自
 *     `data/demo_storyline/frontend/s06-procurement.js`（本文件顶部 `INLINE_SCENE_DATA`
 *     为其同构镜像，`tests/demo/test_s06.py` 逐值深比对），不由文案临时拍脑袋。
 *  3. **取数走 `SceneKit.story()`（T05 契约入口，实指 `/api/v2/demo/objects/{name}`），
 *     并对同一端点保留一条屏内直连兜底**（防在线探针误判离线时静默降级）；
 *     两者都拿不到才显示降级条，绝不编数字。
 *
 * 词表：人名取自 `tools/demo_gen/wordlist.json`，全部虚构。
 */
(function () {
  "use strict";

  const SCENE_ID = "s06";

  /**
   * 场景派生配置——与 `data/demo_storyline/frontend/s06-procurement.js` 的
   * `window.SCENE_DATA["s06"]` **同构镜像**（test_s06.py 逐值深比对，两处漂移即变红）。
   *
   * 阈值出处：缺料预警口径与延期升级链见 `spec-v1.md` §3 第 15 行（S6）；
   * 工排需料日按主线工单排产倒推（DEMO-WO-002 / DEMO-WO-003）。
   */
  const INLINE_SCENE_DATA = {
    "scene": "s06",
    "title": "S6 · 采购执行",
    "sub": "PO 下发 → 供应商交期确认 → 缺料预警 → 自动催料待办 → 延期升级 → 催料留痕",
    "shortage_rule": "缺料预警口径：PO 未交付（actual_date 为空）且料期（promise_date）晚于工排需料日（plan_need）→ 缺口成立；缺口量取该 PO 未交付下单量",
    "plan_need": {
      "DEMO-MAT-2201": "2026-11-20",
      "DEMO-MAT-2202": "2026-12-01"
    },
    "escalation_rule": "预警持续天数 = 升级留痕时间 − 缺料预警时间；0–1 天由采购员催料，≥2 天升级采购主管，≥5 天升级供应链经理；升级链每一级可追到人",
    "escalation_levels": [
      {
        "level": 1,
        "owner_role": "采购员",
        "threshold_days": 0
      },
      {
        "level": 2,
        "owner_role": "采购主管",
        "threshold_days": 2
      },
      {
        "level": 3,
        "owner_role": "供应链经理",
        "threshold_days": 5
      }
    ],
    "short_ship_rule": "实收量 < PO 下单量 → 短装，按短装量触发二次催料；当前演示数据集未建模实收量字段，按「无短装留痕」呈现，不编造短装数量",
    "assignees": {
      "采购员": "孙毅",
      "采购主管": "吴悦",
      "供应链经理": "郑昊"
    },
    "threshold_sources": {
      "shortage_rule": "spec-v1.md §3 第 15 行（S6：PO 下发 / 交期确认 / 缺料预警（料期 vs 工排计划比对）/ 催料任务）",
      "escalation_rule": "spec-v1.md §3 第 15 行（S6）延期升级链；演示载荷 DEMO-EV-0029 记载升级至采购主管",
      "plan_need": "工排计划需料日：机加/装配按主线工单排产倒推（DEMO-WO-002 / DEMO-WO-003）"
    },
    "object_source": [
      "purchase_orders",
      "suppliers",
      "materials",
      "work_orders",
      "sales_orders",
      "events"
    ],
    "agent_layer": {
      "replays": {
        "agent": "s06_shortage_procurement",
        "cases": "s06_supplier_cases",
        "faq": "s06_urge_faq"
      },
      "title": "采购智能体（缺口判定 → 供应商案例 → 催料制度）",
      "source": "purchase_orders / cases / faqs（回放与 T08 缺口机械口径同源）",
      "degrade": "采购智能体回放未就绪，不展示编造结论"
    }
  };

  /** 取场景配置：优先已加载的 `window.SCENE_DATA['s06']`，缺位时登记内联镜像。 */
  function sceneData() {
    const bag = window.SCENE_DATA;
    if (!bag || typeof bag !== "object") window.SCENE_DATA = {};
    if (!window.SCENE_DATA[SCENE_ID]) window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA;
    return window.SCENE_DATA[SCENE_ID];
  }

  const CFG = sceneData();
  const PLAN_NEED = CFG.plan_need;
  const ESC_LEVELS = CFG.escalation_levels;
  const ESC_RULE = CFG.escalation_rule;
  const SHORTAGE_RULE = CFG.shortage_rule;
  const SHORT_SHIP_RULE = CFG.short_ship_rule;
  const ASSIGNEES = CFG.assignees;

  /** 本屏取数的剧情对象（registry.json 里同键登记，供契约测试核对）。 */
  const OBJECTS = ["purchase_orders", "suppliers", "materials", "work_orders", "sales_orders", "events"];

  /** 供应商的「交期确认」状态字典（spec-v1.md §3 采购口径）。 */
  const PO_STATUS = {
    confirmed: ["交期已确认", "badge-green"],
    delayed: ["延期", "badge-red"],
    shipped: ["已发货", "badge-blue"],
    closed: ["已关闭", "badge-gray"],
  };

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

  /** 坐标逐对核对：日期差（b − a，单位天）；任一不可解析返回 null。 */
  function daysBetween(a, b) {
    const t1 = Date.parse(String(a));
    const t2 = Date.parse(String(b));
    if (!Number.isFinite(t1) || !Number.isFinite(t2)) return null;
    return Math.round((t2 - t1) / 86400000);
  }

  /** 分钟差（b − a）；同秒 = 0，用于证明「预警 → 待办」之间没有人工停留。 */
  function minutesBetween(a, b) {
    const t1 = Date.parse(String(a));
    const t2 = Date.parse(String(b));
    if (!Number.isFinite(t1) || !Number.isFinite(t2)) return null;
    return Math.round((t2 - t1) / 60000);
  }

  function poStatusBadge(status) {
    const K = window.SceneKit;
    const meta = PO_STATUS[String(status)] || [String(status || "未确认"), "badge-gray"];
    return K.badge(meta[0], meta[1]);
  }

  function readonlyBadge(readonly) {
    const K = window.SceneKit;
    return readonly
      ? K.badge("只读访客 · 采购执行链路只读", "badge-gray")
      : K.badge("可写岗位 · 本屏只读（催料写操作在治理窗口）", "badge-blue");
  }

  function nameOf(role) {
    return ASSIGNEES[role] || "—";
  }

  /* ============================================================
   * 取数：Contract 入口 + 端点兜底
   * ============================================================ */

  /**
   * 直连兜底：`GET <base>/api/v2/demo/objects/{name}`（与 `SceneKit.story()` 同端点）。
   *
   * 为什么需要：`SceneKit.story()` 在 `api.online()` 探针判离线、或响应结构不合 rows
   * 约定时会**静默降级**（走 `window.DEMO_STORY` 或返回 ok:false）。本屏不依赖探针结论，
   * 对同一端点保留一条直连兜底；`story()` 正常时本函数不会被调用（零额外请求）。
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
   * 派生：缺口判定 / 自动催料链路 / 升级链
   * ============================================================ */

  function indexBy(rows, key) {
    const map = {};
    (rows || []).forEach(function (row) {
      if (row && row[key] !== undefined && row[key] !== null) map[row[key]] = row;
    });
    return map;
  }

  /**
   * 单张 PO 的缺口判定（机械口径，见 INLINE_SCENE_DATA.shortage_rule）。
   *
   * 三个输入：是否已交付（actual_date 非空）、料期（promise_date）、工排需料日（plan_need）。
   * 「已交付」不算缺口——即使曾经晚于需料日，货到了就闭环（DEMO-PO-001 即此类）。
   */
  function gapOf(po) {
    const need = PLAN_NEED[po.material_id] || null;
    const delivered = !!po.actual_date;
    const lateDays = (po.promise_date && need) ? daysBetween(need, po.promise_date) : null;
    const gap = !delivered && lateDays !== null && lateDays > 0;
    return {
      need_date: need,
      delivered: delivered,
      late_days: lateDays,
      gap: gap,
      gap_qty: gap ? Number(po.qty) : 0,
    };
  }

  /** 按预警持续天数取升级档位（机械判定；找不到档位返回 null）。 */
  function levelByAging(agingDays) {
    let hit = null;
    ESC_LEVELS.forEach(function (lv) {
      if (agingDays !== null && agingDays >= lv.threshold_days) hit = lv;
    });
    return hit;
  }

  function derive(data) {
    const pos = data.purchase_orders || [];
    const supById = indexBy(data.suppliers, "supplier_id");
    const matById = indexBy(data.materials, "material_id");
    const events = (data.events || []).slice().sort(function (a, b) {
      return String(a && a.ts) < String(b && b.ts) ? -1 : 1;
    });
    const s6 = events.filter(function (e) { return e && e.scene === "S6"; });

    const shortages = s6.filter(function (e) {
      return e.type === "alert" && (e.payload || {}).kind === "shortage";
    });
    const escalationEvents = s6.filter(function (e) {
      return e.type === "alert" && (e.payload || {}).kind === "escalated";
    });
    const todoEvents = s6.filter(function (e) { return e.type === "order_status"; });

    const orders = pos.map(function (po) {
      const gap = gapOf(po);
      return {
        raw: po,
        material: matById[po.material_id] || null,
        supplier: supById[po.supplier_id] || null,
        confirmed: po.status === "confirmed",
        gap_info: gap,
      };
    });

    // 自动链路：以催料待办事件为锚，回溯同对象的缺料预警，现算两者间隔（分钟）。
    const chain = todoEvents.map(function (todo) {
      const alert = shortages.filter(function (s) {
        return s.object_ref === todo.object_ref && String(s.ts) <= String(todo.ts);
      }).pop() || null;
      return {
        object_ref: todo.object_ref,
        alert_ts: alert ? alert.ts : null,
        alert_id: alert ? alert.event_id : null,
        alert_payload: alert ? (alert.payload || {}) : {},
        todo_ts: todo.ts,
        todo_id: todo.event_id,
        todo_note: (todo.payload || {}).note || "",
        latency_min: alert ? minutesBetween(alert.ts, todo.ts) : null,
        owner_role: ESC_LEVELS[0] ? ESC_LEVELS[0].owner_role : "采购员",
        order: orders.find(function (o) { return o.raw.po_id === todo.object_ref; }) || null,
      };
    });

    // 升级链：以升级留痕时间反推预警持续天数，再机械取档；未触发的档位显式标「未触发」。
    const escalations = escalationEvents.map(function (esc) {
      const alert = shortages.filter(function (s) {
        return s.object_ref === esc.object_ref && String(s.ts) <= String(esc.ts);
      }).pop() || null;
      const aging = alert ? daysBetween(alert.ts, esc.ts) : null;
      const reach = levelByAging(aging);
      return {
        object_ref: esc.object_ref,
        ts: esc.ts,
        event_id: esc.event_id,
        note: (esc.payload || {}).note || "",
        alert_ts: alert ? alert.ts : null,
        aging_days: aging,
        reached_level: reach ? reach.level : null,
        reached_role: reach ? reach.owner_role : null,
        reached_name: reach ? nameOf(reach.owner_role) : "—",
        levels: ESC_LEVELS.map(function (lv) {
          const on = aging !== null && aging >= lv.threshold_days;
          return {
            level: lv.level,
            owner_role: lv.owner_role,
            owner_name: nameOf(lv.owner_role),
            threshold_days: lv.threshold_days,
            reached: on,
            ts: on ? esc.ts : null,
          };
        }),
      };
    });

    const counts = {
      po: orders.length,
      confirmed: orders.filter(function (o) { return o.confirmed; }).length,
      shortage: shortages.length,
      todo: todoEvents.length,
      escalated: escalationEvents.length,
    };

    return {
      orders: orders, shortages: shortages, todos: todoEvents,
      chain: chain, escalations: escalations, counts: counts, s6: s6, events: events,
    };
  }

  /* ============================================================
   * 渲染片段
   * ============================================================ */

  function chainHtml(d) {
    const K = window.SceneKit;
    if (!d.chain.length) {
      return '<div class="card"><div class="card-head"><div class="card-title">'
        + K.icon("zap", 15) + "预警 → 待办自动链路</div></div>"
        + '<div class="card-body" style="padding:12px 16px 16px">'
        + '<div class="empty">本数据集无自动催料链路留痕（S6 事件里没有催料任务事件）</div></div></div>';
    }
    const rows = d.chain.map(function (c) {
      const latencyOk = c.latency_min === 0;
      const steps = [
        ["① 缺料预警", c.alert_id || "—", K.fmtDate(c.alert_ts || "")],
        ["② 催料任务自动生成", c.todo_id || "—", K.fmtDate(c.todo_ts || "")],
        ["③ 挂采购员待办", nameOf(c.owner_role) + "（" + c.owner_role + "）", "自动指派"],
      ];
      return '<div style="padding:12px 0;border-bottom:1px dashed var(--border)">'
        + '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">'
        + '<span style="font-size:12.8px;font-weight:700;color:var(--text)">'
        + K.esc(c.object_ref) + "</span>"
        + K.badge("间隔 " + K.fmtNum(c.latency_min) + " 分钟", latencyOk ? "badge-green" : "badge-red")
        + K.badge("人工步骤 0 步", latencyOk ? "badge-green" : "badge-amber")
        + '<button class="btn btn-sm btn-outline" type="button" data-sk-drill="chain-auto">链路口径</button>'
        + "</div>"
        + steps.map(function (s, i) {
          return '<div style="display:flex;gap:8px;align-items:baseline;font-size:12.4px;'
            + 'color:var(--text-2);line-height:1.8">'
            + '<span style="min-width:132px;color:var(--text-3)">' + K.esc(s[0]) + "</span>"
            + '<span style="color:var(--text)">' + K.esc(s[1]) + "</span>"
            + '<span style="color:var(--text-3)">' + K.esc(s[2]) + "</span>"
            + (i < steps.length - 1 ? '<span style="color:var(--text-3)">→</span>' : "")
            + "</div>";
        }).join("")
        + '<div class="sk-num-hint" style="margin-top:4px">任务说明：' + K.esc(c.todo_note)
        + "（缺口量 " + K.fmtNum(c.order ? c.order.gap_info.gap_qty : 0) + " 片）</div>"
        + "</div>";
    }).join("");

    const half = d.chain[0].latency_min;
    return '<div class="card">'
      + '<div class="card-head"><div class="card-title">' + K.icon("zap", 15) + "预警 → 待办自动链路"
      + '</div><div class="card-extra">'
      + (half === 0 ? "全自动 · 零人工步骤" : "存在人工停留 · 需核查") + "</div></div>"
      + '<div class="card-body" style="padding:8px 16px 14px">' + rows
      + '<div class="sk-note">口径：链路时间戳全部取自 events（scene=S6）；间隔 = 待办事件时间 − 预警事件时间。'
      + "验收要求「预警到待办全自动无人工步骤」——界面显示的间隔必须为 0 分钟，否则说明链路里混进了人工环节。"
      + "</div></div></div>";
  }

  function poTableHtml(d) {
    const K = window.SceneKit;
    const th = function (t) {
      return '<th style="text-align:left;padding:9px 12px;font-size:12px;color:var(--text-2);'
        + 'font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap">' + K.esc(t) + "</th>";
    };
    const heads = ["PO", "物料", "供应商（OTD）", "下单量", "料期", "实交", "状态", "缺口判定"]
      .map(th).join("");
    const rows = d.orders.map(function (o) {
      const po = o.raw;
      const g = o.gap_info;
      const supplier = o.supplier
        ? o.supplier.name + "（OTD " + K.fmtNum(Number(o.supplier.otd_rate) * 100, 1) + "%）"
        : (po.supplier_id || "—");
      const material = o.material
        ? o.material.name + "（" + po.material_id + "）" : (po.material_id || "—");
      const gapTone = g.gap ? "sk-tone-red" : "sk-tone-green";
      const gapText = g.gap
        ? "缺口成立 · " + K.fmtNum(g.gap_qty) + " 片（料期晚于需料日 " + K.fmtNum(g.late_days) + " 天）"
        : (g.delivered ? "无缺口（已交付，曾晚于需料日 "
          + (isNum(g.late_days) ? K.fmtNum(g.late_days) : "—") + " 天）"
          : "无缺口（料期不晚于需料日）");
      return '<tr>'
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + K.esc(po.po_id) + '</td>'
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border)">' + K.esc(material) + "</td>"
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border)">' + K.esc(supplier) + "</td>"
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap">'
        + K.fmtNum(Number(po.qty)) + "</td>"
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + K.esc(K.fmtDate(po.promise_date)) + " / 需料 " + K.esc(K.fmtDate(g.need_date)) + "</td>"
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + K.esc(po.actual_date ? K.fmtDate(po.actual_date) : "未交付") + "</td>"
        + '<td style="padding:10px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + poStatusBadge(po.status) + "</td>"
        + '<td class="' + gapTone + '" data-sk-drill="po-' + K.esc(po.po_id) + '" role="button" tabindex="0" '
        + 'style="padding:10px 12px;border-bottom:1px solid var(--border);cursor:pointer;font-size:12.2px">'
        + K.esc(gapText) + "</td>"
        + "</tr>";
    }).join("");
    return '<div style="overflow-x:auto;padding-top:10px">'
      + '<table style="border-collapse:collapse;width:100%;min-width:900px">'
      + "<thead><tr>" + heads + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  function todoHtml(d) {
    const K = window.SceneKit;
    if (!d.chain.length) {
      return '<div class="empty">采购员待办：本数据集无催料任务</div>';
    }
    return d.chain.map(function (c) {
      const qty = c.order ? c.order.gap_info.gap_qty : 0;
      const mat = c.order && c.order.material ? c.order.material.name : "—";
      return '<div class="card" style="margin-top:10px">'
        + '<div class="card-head">'
        + '<div class="card-title">' + K.icon("list", 15) + "催料任务 · " + K.esc(c.object_ref) + "</div>"
        + '<div class="card-extra">' + K.badge("系统自动生成", "badge-blue")
        + K.badge("需人工确认：否", "badge-green") + "</div></div>"
        + '<div class="card-body" style="padding:10px 16px 14px">'
        + kv([
          ["任务", "催料 " + c.object_ref + "（" + mat + " · 缺口 " + K.fmtNum(qty) + " 片）"],
          ["指派", nameOf(c.owner_role) + "（" + c.owner_role + "）"],
          ["来源", "缺料预警 " + (c.alert_id || "—") + " → " + (c.todo_id || "—") + "（scene=S6）"],
          ["生成时间", K.fmtDate(c.todo_ts || "") + " · 与预警间隔 " + K.fmtNum(c.latency_min) + " 分钟"],
          ["状态", c.latency_min === 0 ? "已挂采购员待办（全自动，零人工步骤）" : "链路存在人工停留，需核查"],
        ])
        + '<div class="sk-note">待办不提供写按钮：真实催料任务写操作在治理窗口（待办中心 / HITL）执行；'
        + "本屏只回放自动生成结果，避免出现绕过治理的旁路写入。</div>"
        + "</div></div>";
    }).join("");
  }

  function escalationHtml(d) {
    const K = window.SceneKit;
    if (!d.escalations.length) {
      return '<div class="empty">升级链：本数据集无延期升级留痕</div>';
    }
    return d.escalations.map(function (esc) {
      const rows = esc.levels.map(function (lv) {
        const tone = lv.reached ? "sk-tone-red" : "sk-tone-gray";
        return '<div class="sk-slot">'
          + '<span class="sk-slot-role">L' + K.esc(lv.level) + " " + K.esc(lv.owner_role) + "</span>"
          + '<span class="sk-slot-user">' + K.esc(lv.owner_name) + "</span>"
          + K.badge(lv.reached ? "已触达" : "未触发",
            lv.reached ? "badge-red" : "badge-gray")
          + '<span class="sk-slot-ts">阈值 ≥ ' + K.fmtNum(lv.threshold_days) + " 天</span>"
          + '<span class="sk-slot-act"><button class="btn btn-sm btn-outline" type="button" '
          + 'data-sk-drill="esc-' + K.esc(lv.level) + '">追人</button></span>'
          + '<span class="' + tone + '" style="display:none"></span>'
          + "</div>";
      }).join("");
      return '<div class="card">'
        + '<div class="card-head">'
        + '<div class="card-title">' + K.icon("approve", 15) + "延期升级链 · " + K.esc(esc.object_ref) + "</div>"
        + '<div class="card-extra">' + K.badge("当前档位 L" + K.esc(esc.reached_level || "-")
          + " " + K.esc(esc.reached_role || "—"), "badge-red") + "</div></div>"
        + '<div class="sk-signoff-body">' + rows + "</div>"
        + '<div class="sk-signoff-note">预警持续天数 ' + K.fmtNum(esc.aging_days) + " 天（"
        + K.esc(K.fmtDate(esc.alert_ts || "")) + " 预警 → " + K.esc(K.fmtDate(esc.ts)) + " 升级）。"
        + "当前档位：" + K.esc(esc.reached_role || "—") + " / " + K.esc(esc.reached_name)
        + "。规则：" + K.esc(ESC_RULE) + " 留痕：" + K.esc(esc.note || "—") + "</div></div>";
    }).join("");
  }

  function ledgerHtml(d) {
    const K = window.SceneKit;
    if (!d.s6.length) return '<div class="empty">催料留痕表：无 S6 事件</div>';
    const rows = d.s6.map(function (e) {
      const p = e.payload || {};
      const kind = e.type + (p.kind ? " / " + p.kind : "");
      return "<tr>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + K.esc(K.fmtDate(e.ts)) + "</td>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + K.esc(e.object_ref) + "</td>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap">'
        + K.esc(kind) + "</td>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">' + K.esc(p.note || "—") + "</td>"
        + "</tr>";
    }).join("");
    const th = function (t) {
      return '<th style="text-align:left;padding:9px 12px;font-size:12px;color:var(--text-2);'
        + 'font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap">' + K.esc(t) + "</th>";
    };
    return '<div style="overflow-x:auto;padding-top:10px">'
      + '<table style="border-collapse:collapse;width:100%;min-width:760px">'
      + "<thead><tr>" + ["时间", "对象", "动作", "说明"].map(th).join("") + "</tr></thead>"
      + "<tbody>" + rows + "</tbody></table></div>";
  }

  function ruleHtml() {
    const K = window.SceneKit;
    const rows = ESC_LEVELS.map(function (lv) {
      return "<tr>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border);font-weight:600">L'
        + K.esc(lv.level) + "</td>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">'
        + K.esc(lv.owner_role) + "</td>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">'
        + K.esc(nameOf(lv.owner_role)) + "</td>"
        + '<td style="padding:8px 12px;border-bottom:1px solid var(--border)">≥ '
        + K.fmtNum(lv.threshold_days) + " 天</td></tr>";
    }).join("");
    const th = function (t) {
      return '<th style="text-align:left;padding:8px 12px;font-size:12px;color:var(--text-2);'
        + 'font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap">' + K.esc(t) + "</th>";
    };
    return '<div class="card">'
      + '<div class="card-head"><div class="card-title">' + K.icon("shield", 15) + "缺口判定与升级阈值"
      + '</div><div class="card-extra">' + K.badge("机械判定 · 逐条可复核", "badge-blue") + "</div></div>"
      + '<div class="card-body" style="padding:12px 16px 16px">'
      + '<div style="font-size:12.6px;color:var(--text);line-height:1.7">' + K.esc(SHORTAGE_RULE) + "</div>"
      + '<div style="font-size:12.6px;color:var(--text);line-height:1.7;margin-top:6px">'
      + K.esc(ESC_RULE) + "</div>"
      + '<div style="overflow-x:auto;margin-top:10px">'
      + '<table style="border-collapse:collapse;width:100%;min-width:520px">'
      + "<thead><tr>" + ["档位", "岗位", "责任人", "预警持续阈值"].map(th).join("") + "</tr></thead>"
      + "<tbody>" + rows + "</tbody></table></div>"
      + '<div class="sk-note">短装口径：' + K.esc(SHORT_SHIP_RULE) + "</div>"
      + "</div></div>";
  }

  function degradeHtml(state) {
    const K = window.SceneKit;
    const missing = Object.keys(state.loaded).filter(function (k) {
      return state.loaded[k] !== true;
    });
    return '<div class="sk-degrade">' + K.icon("warn", 15)
      + "<div><b>演示装载层未就绪：本屏不显示编造数字。</b>"
      + '<div class="sk-degrade-sub">缺失对象：' + K.esc(missing.join("、") || "（未知）")
      + "。恢复条件：GET /api/v2/demo/objects/{object}（T04 当前实现）可用一行数据。"
      + "</div></div></div>";
  }

  function bodyHtml(d, readonly, state) {
    const K = window.SceneKit;
    if (state.loaded.purchase_orders !== true || !d.orders.length) {
      return degradeHtml(state);
    }
    const shortest = d.chain.reduce(function (acc, c) {
      if (c.latency_min === null) return acc;
      return acc === null ? c.latency_min : Math.min(acc, c.latency_min);
    }, null);
    const cards = [
      {
        label: "采购订单", value: d.counts.po, unit: "笔",
        hint: "口径：purchase_orders 全部单据（演示故事 2 笔）", tone: "blue", drill: "ov-po",
      },
      {
        label: "交期已确认", value: d.counts.confirmed + " / " + d.counts.po, unit: "",
        hint: "供应商回签状态 = confirmed 的笔数（delayed 记延期）",
        tone: d.counts.confirmed === d.counts.po ? "green" : "amber", drill: "ov-confirmed",
      },
      {
        label: "缺料预警", value: d.counts.shortage, unit: "项",
        hint: "料期 vs 工排需料日比对触发的缺口数",
        tone: d.counts.shortage === 0 ? "green" : "red", drill: "ov-shortage",
      },
      {
        label: "自动催料待办", value: d.counts.todo, unit: "条",
        hint: "预警 → 待办间隔 " + (shortest === null ? "—" : K.fmtNum(shortest)) + " 分钟（0 = 全自动）",
        tone: shortest === 0 ? "green" : "amber", drill: "ov-todo",
      },
    ];
    return '<div class="sk-block">'
      + '<div class="section-title">采购执行概览<span class="section-extra">'
      + "四个数均 Web 侧现算 · 点击查看口径与来源</span></div>"
      + K.numberRow(cards)
      + "</div>"
      + '<div class="sk-block">' + chainHtml(d) + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">采购订单与交期确认<span class="section-extra">'
      + "料期（promise_date） vs 工排需料日（plan_need）逐笔比对</span></div>"
      + poTableHtml(d)
      + '<div class="sk-note">缺口判定为机械口径：未交付且料期晚于工排需料日才算缺口；已交付一律闭环，'
      + "不为「曾经迟过」保留缺口标记。点击「缺口判定」格可查看该笔 PO 的完整口径、来源与算法。</div>"
      + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">采购员待办（催料任务）<span class="section-extra">'
      + "来源：events · scene=S6 · type=order_status</span></div>"
      + todoHtml(d)
      + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">延期升级链<span class="section-extra">'
      + "升级档位按预警持续天数机械判定 · 每级可追到人</span></div>"
      + escalationHtml(d)
      + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">催料记录留痕表<span class="section-extra">'
      + "events · scene=S6 全量 " + d.s6.length + " 条</span></div>"
      + ledgerHtml(d)
      + "</div>"
      + '<div class="sk-block">' + ruleHtml() + "</div>"
      + '<div class="sk-block">'
      + K.eventFlow(d.s6.map(function (e) {
        const p = e.payload || {};
        return {
          ts: e.ts, type: e.type, scene: e.scene,
          text: (e.type + (p.kind ? " / " + p.kind : "")) + " · " + (p.note || e.object_ref),
        };
      }), {
        title: "S6 事件流（按时间升序）",
        extra: "来源：events 对象 · scene=S6",
        note: "共 " + d.s6.length + " 条 S6 事件（预警 " + d.shortages.length + " 条 / 待办 "
          + d.todos.length + " 条 / 升级 " + d.escalations.length + " 条）。"
          + (readonly ? "当前岗位为只读访客。" : "")
          + "事件字段口径见 spec-v1.md §2.10。",
      })
      + '<div class="sk-block" id="s06-agent"></div>'
      + "</div>";
  }

  /* ---------- T19：采购智能体（缺口 → 供应商案例 → 催料制度，只读回放） ---------- */
  function hydrateAgentBlock(box) {
    const K = window.SceneKit;
    const el = box ? box.querySelector("#s06-agent") : null;
    const a = (CFG && CFG.agent_layer) || null;
    if (!el || !a || !K || !a.replays) return;
    const degrade = function (text) {
      el.innerHTML = '<div class="sk-degrade" data-sk-s06-agent-degrade="1">'
        + K.esc(text || a.degrade || "采购智能体回放未就绪，不展示编造结论") + "</div>";
    };
    K.replay(a.replays.agent).then(function (res) {
      if (!el.parentNode) return;
      if (!res || !res.ok || !res.package) { degrade(); return; }
      const pkg = res.package;
      let html = '<div class="section-title">' + K.esc(a.title)
        + '<span class="section-extra">' + K.esc(a.source || "") + "</span></div>"
        + K.agentPanel({
          title: pkg.title, agents: pkg.agents, dag: pkg.dag, steps: pkg.steps,
          node_steps: pkg.node_steps, conclusion: pkg.conclusion, source: "replay",
        });
      el.innerHTML = html;
      K.replay(a.replays.cases).then(function (cres) {
        if (!el.parentNode || !cres || !cres.ok || !cres.package) return;
        const cp = cres.package;
        const hits = (cp.hits || []).map(function (h) {
          return '<div class="s06-case"><b>' + K.esc(h.case_id) + " · " + K.esc(h.title)
            + "</b><div>根因：" + K.esc(h.root_cause) + " · 部门：" + K.esc(h.dept)
            + (h.redacted ? " · 已脱敏" : "") + "</div></div>";
        }).join("");
        if (hits) {
          el.innerHTML = html + '<div class="section-title">供应商来料案例（Agentic RAG 回放）</div>'
            + '<div class="s06-cases">' + hits + "</div>";
        }
        K.replay(a.replays.faq).then(function (fres) {
          if (!el.parentNode || !fres || !fres.ok || !fres.package) return;
          const fp = fres.package;
          el.innerHTML = el.innerHTML + '<div class="section-title">催料制度问答（kbqa 回放）</div>'
            + K.agentPanel({
              title: fp.title, agents: fp.agents, steps: fp.steps,
              conclusion: fp.conclusion, source: "replay",
            });
        });
      });
    });
  }

  /* ============================================================
   * 下钻口径字典
   * ============================================================ */

  function registerDrills(d, state) {
    const K = window.SceneKit;
    const sourceOf = function (name) {
      return state.loaded[name] === true
        ? "GET /api/v2/demo/objects/" + name
        : "（未取到：" + name + " 装载层未就绪）";
    };

    drills = {};

    drills["ov-po"] = function () {
      return {
        title: "采购订单笔数", subtitle: "口径 · 来源",
        html: kv([
          ["取值", String(d.counts.po) + " 笔"],
          ["口径", "purchase_orders 全量单据（spec-v1.md §3 第 15 行 S6）"],
          ["来源", sourceOf("purchase_orders")],
          ["单据清单", d.orders.map(function (o) {
            return o.raw.po_id + "（" + o.raw.status + "）";
          }).join("、") || "（无）"],
        ]),
      };
    };

    drills["ov-confirmed"] = function () {
      return {
        title: "交期已确认", subtitle: "口径 · 来源",
        html: kv([
          ["取值", d.counts.confirmed + " / " + d.counts.po + " 笔"],
          ["口径", "status = confirmed 记为已确认；delayed 记为延期；其余按字典映射"],
          ["来源", sourceOf("purchase_orders") + "；供应商档案 " + sourceOf("suppliers")],
          ["逐笔状态", d.orders.map(function (o) {
            const meta = PO_STATUS[String(o.raw.status)] || [String(o.raw.status), "badge-gray"];
            return o.raw.po_id + "：" + meta[0];
          }).join("；") || "（无）"],
        ]),
      };
    };

    drills["ov-shortage"] = function () {
      return {
        title: "缺料预警", subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", String(d.counts.shortage) + " 项"],
          ["口径", SHORTAGE_RULE],
          ["算法", "逐笔 PO：未交付 且 料期（promise_date）> 工排需料日（plan_need[material_id]）→ 缺口成立"],
          ["来源", sourceOf("purchase_orders") + "；预警事件 " + sourceOf("events") + "（scene=S6 / type=alert / kind=shortage）"],
          ["命中", d.orders.filter(function (o) { return o.gap_info.gap; }).map(function (o) {
            return o.raw.po_id + "（缺口 " + K.fmtNum(o.gap_info.gap_qty) + " 片 · 迟 "
              + K.fmtNum(o.gap_info.late_days) + " 天）";
          }).join("、") || "（无）"],
        ]),
      };
    };

    drills["ov-todo"] = function () {
      return {
        title: "自动催料待办", subtitle: "口径 · 来源 · 自动化证据",
        html: kv([
          ["取值", String(d.counts.todo) + " 条"],
          ["口径", "events 中 scene=S6 / type=order_status 的催料任务事件（自动生成，非人工建单）"],
          ["来源", sourceOf("events")],
          ["自动化证据", d.chain.map(function (c) {
            return c.object_ref + "：预警 " + (c.alert_id || "—") + " → 待办 " + (c.todo_id || "—")
              + "，间隔 " + K.fmtNum(c.latency_min) + " 分钟";
          }).join("；") || "（无）"],
        ]),
      };
    };

    d.orders.forEach(function (o) {
      drills["po-" + o.raw.po_id] = function () {
        const po = o.raw;
        const g = o.gap_info;
        return {
          title: "缺口判定 · " + po.po_id, subtitle: "口径 · 来源 · 算法",
          html: kv([
            ["结论", g.gap ? "缺口成立" : "无缺口"],
            ["下单量", K.fmtNum(Number(po.qty)) + " 片（缺口量 " + K.fmtNum(g.gap_qty) + " 片）"],
            ["料期", K.fmtDate(po.promise_date) + "（供应商承诺）"],
            ["工排需料日", K.fmtDate(g.need_date) + "（plan_need 场景配置）"],
            ["实交", po.actual_date ? K.fmtDate(po.actual_date) : "未交付"],
            ["迟于需料日", isNum(g.late_days) ? K.fmtNum(g.late_days) + " 天" : "—"],
            ["算法", "缺口 =（未交付）且（料期 > 需料日）；已交付一律闭环"],
            ["来源", sourceOf("purchase_orders") + "；物料 " + sourceOf("materials")
              + "；供应商 " + sourceOf("suppliers")],
            ["供应商", o.supplier ? o.supplier.name + "（OTD "
              + K.fmtNum(Number(o.supplier.otd_rate) * 100, 1) + "%）" : (po.supplier_id || "—")],
          ]),
        };
      };
    });

    drills["chain-auto"] = function () {
      const c = d.chain[0] || {};
      return {
        title: "预警 → 待办自动链路", subtitle: "口径 · 自动化证据",
        html: kv([
          ["对象", String(c.object_ref || "—")],
          ["缺料预警", (c.alert_id || "—") + "　" + K.fmtDate(c.alert_ts || "")],
          ["催料任务", (c.todo_id || "—") + "　" + K.fmtDate(c.todo_ts || "")],
          ["间隔", K.fmtNum(c.latency_min) + " 分钟（0 = 全自动无人工步骤）"],
          ["口径", "间隔 = 待办事件 ts − 预警事件 ts；两个事件同属 scene=S6、同一 object_ref"],
          ["来源", sourceOf("events")],
          ["验收口径", "«预警到待办全自动无人工步骤»——间隔不为 0 即视为链路混入人工环节"],
        ]),
      };
    };

    ESC_LEVELS.forEach(function (lv) {
      drills["esc-" + lv.level] = function () {
        const esc = d.escalations[0] || {};
        const row = (esc.levels || []).filter(function (x) { return x.level === lv.level; })[0];
        return {
          title: "升级档位 L" + lv.level + " · " + lv.owner_role, subtitle: "可追到人 · 口径",
          html: kv([
            ["岗位", lv.owner_role],
            ["责任人", nameOf(lv.owner_role) + "（虚构人名，取自 tools/demo_gen/wordlist.json）"],
            ["阈值", "预警持续 ≥ " + K.fmtNum(lv.threshold_days) + " 天"],
            ["状态", row && row.reached ? "已触达" : "未触发"],
            ["当前预警持续", K.fmtNum(esc.aging_days) + " 天（" + K.fmtDate(esc.alert_ts || "")
              + " 预警 → " + K.fmtDate(esc.ts || "") + " 升级留痕）"],
            ["规则", ESC_RULE],
            ["来源", sourceOf("events") + "（scene=S6 / type=alert / kind=escalated）"],
          ]),
        };
      };
    });

    drills["ledger"] = function () {
      return {
        title: "催料记录留痕表", subtitle: "口径 · 来源",
        html: kv([
          ["口径", "events 中 scene=S6 的全部事件，按时间升序（预警 / 待办 / 升级三类）"],
          ["来源", sourceOf("events")],
          ["条数", String(d.s6.length) + " 条"],
          ["时间范围", (d.s6.length ? K.fmtDate(d.s6[0].ts) + " → " + K.fmtDate(d.s6[d.s6.length - 1].ts) : "—")],
        ]),
      };
    };

    drills["rule-shortship"] = function () {
      return {
        title: "短装判定", subtitle: "口径 · 数据缺口",
        html: kv([
          ["口径", SHORT_SHIP_RULE],
          ["结论", "本数据集无短装留痕（未建模实收量字段），不编造短装数量"],
          ["来源", sourceOf("purchase_orders")],
        ]),
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

    root.innerHTML = '<div class="content-inner"><div id="s06-root">'
      + K.pageHead({
        icon: "truck",
        title: "S6 · 采购执行",
        desc: "采购执行链路：PO 下发 → 供应商交期确认 → 缺料预警（料期 vs 工排需料日比对）"
          + "→ 自动生成催料任务进采购员待办 → 延期升级链 → 催料留痕表。预警到待办零人工步骤。",
        extra: K.badge("T08 验收屏", "badge-blue")
          + K.badge("缺口与升级档位机械判定", "badge-gray")
          + readonlyBadge(readonly),
      })
      + '<div class="sk-block" data-sk-slot="body">'
      + '<div class="empty">' + K.spinner("lg")
      + '<div style="margin-top:10px">正在核对装载层与现算采购链路…</div></div>'
      + "</div>"
      + '<div class="sk-note">数据纪律：缺口量、交期确认率、预警与待办的间隔全部 Web 侧现算或取事件载荷，'
      + "不使用静态快照；取不到对象时显示降级条，不编数字。</div>"
      + "</div></div>";

    const bodyBox = root.querySelector('[data-sk-slot="body"]');

    Promise.all(OBJECTS.map(function (name) {
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

      registerDrills(d, state);
      bodyBox.innerHTML = bodyHtml(d, readonly, state);
      K.bindDrills(bodyBox, openDrillByKey);
      hydrateAgentBlock(bodyBox);
    }).catch(function () {
      if (!bodyBox || !bodyBox.parentNode) return;
      state.loaded.purchase_orders = false;
      bodyBox.innerHTML = degradeHtml(state);
    });
  }

  /* ============================================================
   * 登记
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s06] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: "S6 · 采购执行",
    title: "S6 · 采购执行",
    sub: "交期确认 · 缺料预警 · 自动催料待办 · 延期升级",
    icon: "truck",
    group: window.SceneRegistry.GROUP_NAME,
    order: 60,
    roles: window.SceneRegistry.BUSINESS_ROLES,
    render: render,
  });
})();
