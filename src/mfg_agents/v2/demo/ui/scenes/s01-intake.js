/**
 * S1 · 接单评审（T07 交付物）
 *
 * 【认领备注】本节点由 T07 窗口开发（见 docs/制造生态链-demo-AIGC开发顺序计划-v1.0.md
 * T07 处的「特殊备注」）：其他窗口请勿重复实现 S1。
 *
 * 屏内纪律（与并行窗口的接缝约定）：
 *  1. **数字全部现算**：订单量、ATP 三来源、报价三分量、会签槽位一律运行时经
 *     `SceneKit.story()` 从 T04 装载层（data/demo_storyline/*.jsonl）取，屏内不落散数；
 *     每张数字卡都必须点得开「口径 · 出处 · 现算」。
 *  2. **取不到数不编数**：装载层未就绪时给明确降级提示（文案「演示装载层未就绪」），
 *     而不是回落到手写常量。
 *  3. **会签双轨留痕**（T15 起）：
 *     (a) **平台真实治理工单**：后端幂等预置 order_review 工单（wf_id=hitl_order_review），
 *         本屏经 `/api/v2/workflow/pending` 按当前岗位令牌拉取——不在治理域就取不到
 *         （后端按槽位可见性裁决），三槽位状态一律取自工单真实 signoff_status。
 *     (b) **事件流留痕**：events.jsonl 的 hitl 事件（payload.slots + result）并列保留，
 *         事件未记录会签人姓名，故姓名列留空并在口径里写清。
 *     会签 / 终批等写操作全部留在治理窗口；本屏只读，不出现写按钮。
 *  4. 不碰 app.js：本屏只经 `SceneRegistry.register()` 登记（T05 契约）。
 *  5. 口径字典（标签 / 公式 / 槽位映射）在 `data/demo_storyline/frontend/s01-intake.js`，
 *     本文件内联同构镜像，缺位时顶上；两侧由 tests/demo/test_s01.py 逐值深比对。
 */
(function () {
  "use strict";

  const SCENE_ID = "s01";

  /** 与 data/demo_storyline/frontend/s01-intake.js 的 SCENE_DATA_OBJECT 逐字符同构。 */
  const INLINE_SCENE_DATA = {
    "sceneId": "s01",
    "label": "S1 · 接单评审",
    "title": "S1 · 接单评审",
    "sub": "ATP 初评三来源 · 报价区间三分量 · 订单评审一次会签",
    "icon": "orders",
    "group": "生态链场景",
    "order": 10,
    "objects": [
      "sales_orders",
      "quotes",
      "customers",
      "materials",
      "boms",
      "work_orders",
      "purchase_orders",
      "lots",
      "events"
    ],
    "statusLabels": {
      "draft": "草稿",
      "reviewing": "评审中",
      "approved": "已评审通过",
      "in_production": "生产中",
      "shipped": "已发货",
      "closed": "已关闭"
    },
    "atp": {
      "eventMarker": "atp_on_hand",
      "formula": "在库可用 + 在制（产能） − 安全库存",
      "formulaNote": "口径来自 spec-v1.md §5「ATP 可用量」；数据集 ATP 快照字段为 atp_on_hand / atp_wip / atp_safety（payload.note 标注「ATP 初评三来源」），其中 atp_wip 字段语义为在制可交量，故本屏按「在库 + 在制 − 安全」现算。",
      "sources": [
        {
          "key": "on_hand",
          "field": "atp_on_hand",
          "label": "在库可用",
          "unit": "片",
          "tone": "blue",
          "detailObject": "lots"
        },
        {
          "key": "wip",
          "field": "atp_wip",
          "label": "在制（产能）",
          "unit": "片",
          "tone": "purple",
          "detailObject": "work_orders"
        },
        {
          "key": "safety",
          "field": "atp_safety",
          "label": "安全库存（扣减）",
          "unit": "片",
          "tone": "gray",
          "detailObject": "events"
        }
      ],
      "totalLabel": "ATP 可用（现算）",
      "totalUnit": "片",
      "totalTone": "green"
    },
    "quote": {
      "eventMarker": "quote_id",
      "parts": [
        {
          "key": "material",
          "field": "material_cost",
          "label": "材料成本",
          "formula": "BOM 用量 × 最新采购价（spec-v1.md §2.5）",
          "recompute": "bom"
        },
        {
          "key": "labor",
          "field": "labor_cost",
          "label": "工时成本",
          "formula": "Σ 工序标准工时 × 订单量 × 费率（spec-v1.md §2.5）",
          "recompute": "ops"
        },
        {
          "key": "tool",
          "field": "tool_amort",
          "label": "模具摊销",
          "formula": "模具费 ÷ 订单量（spec-v1.md §2.5）",
          "recompute": "tool"
        }
      ],
      "lowField": "price_low",
      "highField": "price_high",
      "bufferRate": 0.08,
      "lowFormula": "报价下限 = material_cost + labor_cost + tool_amort",
      "highFormula": "报价上限 = 报价下限 × (1 + 毛利缓冲率)"
    },
    "signoff": {
      "slotOrder": [
        "biz",
        "rd",
        "pln"
      ],
      "slotLabels": {
        "biz": "接单人 · 业务",
        "rd": "技术 · 研发",
        "pln": "计划"
      },
      "slotByRole": {
        "biz-demo": "biz",
        "sales": "biz",
        "rd-demo": "rd",
        "rd_engineering": "rd",
        "mfg-demo": "pln",
        "manufacturing": "pln"
      },
      "resultToStatus": {
        "approved": "approved",
        "Go": "approved",
        "rejected": "rejected",
        "No-Go": "rejected"
      },
      "viewLabels": {
        "applicant": "申请人视角（接单人）",
        "cosigner": "会签人视角",
        "observer": "观察者视角（只读）",
        "outsider": "治理域外岗位（工单不可见）"
      },
      "visibilityNote": "待办可见性纪律：申请人只看到自己发起的会签进度，会签人只看到自己槽位应确认的事项；本屏按当前岗位只读演示该视角差异，不发起写操作。",
      "slotByGovernanceRole": {
        "sales": "biz",
        "rd_engineering": "rd",
        "planning": "pln"
      },
      "workOrder": {
        "wfId": "hitl_order_review",
        "slots": [
          "biz",
          "rd",
          "pln"
        ],
        "blockTitle": "订单评审 · 真实治理工单（order_review）",
        "outsiderNote": "当前岗位不持有 order_review 槽位权限：后端按令牌角色裁决为不可见，队列不返回该工单、详情取不到（404）。本屏不拉全量队列兜底——不借别人的眼睛看。",
        "readonlyNote": "工单为平台真实 HITL 留痕：三槽位会签状态取自工单 signoff_status（会签齐备前不得终批）；会签 / 终批等写操作在「人工确认」治理窗口执行，本演示屏保持只读、不出现写按钮。"
      }
    },
    "customer": {
      "profileField": "quality_profile",
      "gradeField": "credit_grade"
    },
    "timelineTitle": "S1 接单评审事件流",
    "agent_layer": {
      "replay": "s01_intake_faq",
      "kind": "kbqa",
      "title": "接单制度问答（制度库直答）",
      "source": "制度库 FAQ · DEMO-FAQ-011（命中即直答，不调生成模型）",
      "degrade": "制度问答回放未就绪，不展示编造答案"
    }
  };

  /** 权威副本优先，缺位时用内联镜像（浏览器读不到仓库内的 registry.json）。 */
  function sceneData() {
    const ext = (window.SCENE_DATA || {})[SCENE_ID];
    return (ext && typeof ext === "object") ? ext : INLINE_SCENE_DATA;
  }

  /** 口径字典（渲染时绑定）；键 = 数字卡的 data-sk-drill。 */
  let CFG = INLINE_SCENE_DATA;
  let drills = {};

  /* ============================================================
   * 小工具
   * ============================================================ */

  function K() {
    return window.SceneKit;
  }

  function kv(pairs) {
    const kit = K();
    const rows = (pairs || []).map(function (pair) {
      return '<div class="sk-kv-row"><span class="sk-kv-k">' + kit.esc(pair[0])
        + '</span><span class="sk-kv-v">' + kit.esc(pair[1]) + "</span></div>";
    }).join("");
    return '<div class="sk-kv">' + rows + "</div>";
  }

  function tableHtml(heads, rows, emptyText) {
    const kit = K();
    const head = "<tr>" + (heads || []).map(function (h) {
      return "<th>" + kit.esc(h) + "</th>";
    }).join("") + "</tr>";
    const body = (rows && rows.length)
      ? rows.map(function (cells) {
        return "<tr>" + cells.map(function (cell) {
          return "<td>" + kit.esc(cell) + "</td>";
        }).join("") + "</tr>";
      }).join("")
      : '<tr><td colspan="' + String((heads || []).length || 1) + '" class="empty-cell">'
        + kit.esc(emptyText || "无明细行") + "</td></tr>";
    return '<div class="drill-scroll"><table class="std-table drill-table"><thead>' + head
      + "</thead><tbody>" + body + "</tbody></table></div>";
  }

  function detail(title, heads, rows, emptyText) {
    const kit = K();
    return '<div class="section-title">' + kit.esc(title)
      + '<span class="section-extra">明细仅列示，不与快照加总</span></div>'
      + tableHtml(heads, rows, emptyText);
  }

  function note(text) {
    const kit = K();
    return text ? '<div class="sk-note">' + kit.esc(text) + "</div>" : "";
  }

  function numText(value, digits) {
    const n = Number(value);
    return Number.isFinite(n) ? K().fmtNum(n, digits) : "—";
  }

  function dateText(value) {
    return (value === null || value === undefined || value === "") ? "—" : K().fmtDate(value);
  }

  /** 故事对象行（未就绪 / 空数组都返回 []，调用方据此决定降级文案）。 */
  function rowsOf(ds, name) {
    const res = ds[name];
    return (res && res.ok === true && Array.isArray(res.rows)) ? res.rows : [];
  }

  function okOf(ds, name) {
    const res = ds[name];
    return !!(res && res.ok === true);
  }

  function firstOf(list) {
    return (Array.isArray(list) && list.length) ? list[0] : null;
  }

  function byField(list, field, value) {
    return (list || []).filter(function (row) {
      return row && String(row[field]) === String(value);
    });
  }

  function sumBy(list, field) {
    let total = 0;
    (list || []).forEach(function (row) {
      const n = Number(row && row[field]);
      if (Number.isFinite(n)) total += n;
    });
    return total;
  }

  /** 事件出处（口径表前几行）：事件号 / 时间 / 类型 / 场景 / 对象 + payload 逐字段。 */
  function evPairs(ev) {
    if (!ev) return [["出处", "S1 事件缺失：本卡口径无法回溯，不显示数"]];
    const payload = ev.payload || {};
    const head = [
      ["出处事件", String(ev.event_id || "—")],
      ["事件时间", dateText(ev.ts)],
      ["事件类型", String(ev.type || "—")],
      ["场景", String(ev.scene || "—")],
      ["关联对象", String(ev.object_ref || "—")],
    ];
    Object.keys(payload).forEach(function (key) {
      const value = payload[key];
      head.push(["payload." + key, (value && typeof value === "object")
        ? JSON.stringify(value) : String(value)]);
    });
    return head;
  }

  function roleOf(ctx) {
    if (!ctx || typeof ctx !== "object") return null;
    try {
      if (typeof ctx.currentRole === "function") return ctx.currentRole();
    } catch (err) {
      return null;
    }
    return null;
  }

  function mySlotOf(ctx, cfg) {
    const role = roleOf(ctx);
    if (!role) return "";
    const map = cfg.signoff.slotByRole || {};
    if (role.id && map[role.id]) return map[role.id];
    let hit = "";
    (Array.isArray(role.roles) ? role.roles : []).forEach(function (key) {
      if (!hit && map[key]) hit = map[key];
    });
    return hit;
  }

  function viewKindOf(slot) {
    if (slot === "biz") return "applicant";
    if (slot) return "cosigner";
    return "observer";
  }

  function visibilityText(viewKind, slot, labels) {
    if (viewKind === "applicant") {
      return "申请人（" + String(labels.biz || "接单人") + "）：看到自己发起的会签进度与三槽位状态，不代替会签人确认。";
    }
    if (viewKind === "cosigner") {
      return "会签人（" + String(labels[slot] || "—") + "）：只看到自己槽位应确认的事项与留痕，不越槽位操作。";
    }
    return "观察者：只读查看三槽位进度与留痕，不参与会签、不发起写操作。";
  }

  function viewPointText(viewKind, labels) {
    if (viewKind === "applicant") {
      return "你是发起人：三槽位中「" + String(labels.rd || "技术") + "」「"
        + String(labels.pln || "计划") + "」为会签人，其确认后接单评审才闭环。";
    }
    if (viewKind === "cosigner") {
      return "你是会签人：仅应确认你槽位相关事项；本单发起人为「"
        + String(labels.biz || "接单人") + "」。";
    }
    if (viewKind === "outsider") {
      return "你的岗位不持有 order_review 槽位权限：该工单对你不可见，队列里也没有它。";
    }
    return "你是观察者：三槽位进度与留痕对你可见，但你不参与会签、不发起写操作。";
  }

  /* ============================================================
   * 真实治理工单（T15 / order_review）：按岗位可见性驱动视角与槽位
   * ============================================================ */

  /** 工单可见性 → 中文口径（与后端 workflow_scope 的 mine / domain / hidden 对齐）。 */
  const VIS_TEXT = {
    mine: "当前阶段我可动作",
    domain: "治理域内 · 暂不轮到我（只读）",
  };

  /** order_review 工单配置（口径字典声明，缺位时给最小默认值）。 */
  function workOrderCfg() {
    const s = (CFG && CFG.signoff) || {};
    return s.workOrder || {};
  }

  /** 当前岗位的治理角色 key 列表（令牌 roles 为权威来源；判定可见性的唯一输入）。 */
  function govRolesOf(ctx) {
    const role = roleOf(ctx);
    return (role && Array.isArray(role.roles)) ? role.roles.map(String) : [];
  }

  /**
   * 拉取 order_review 真实工单（`GET /api/v2/workflow/pending`，按令牌角色）。
   *
   * 后端 fail-closed：无令牌 401、越权扩宽 403、无治理职责返回空队列。
   * 本函数**永不 reject**（与 `SceneKit.story()` 同一纪律），返回：
   *   `{ok:true, task}` 可见 / `{ok:true, task:null}` 治理域外 / `{ok:false, reason}` 降级。
   */
  function loadWorkOrder(ctx) {
    const api = ctx && ctx.api;
    if (!api || typeof api.workflowPending !== "function") {
      return Promise.resolve({ ok: false, reason: "no-api" });
    }
    if (typeof api.online === "function" && api.online() === false) {
      return Promise.resolve({ ok: false, reason: "offline" });
    }
    const wfId = String(workOrderCfg().wfId || "");
    return api.workflowPending(govRolesOf(ctx)).then(function (list) {
      const items = Array.isArray(list) ? list : [];
      const task = items.filter(function (t) {
        return t && String(t.wf_id) === wfId;
      })[0] || null;
      return { ok: true, task: task, scope: (list && list.scope) || {} };
    }).catch(function (err) {
      return {
        ok: false, reason: "error",
        status: (err && err.status) || 0,
        message: String((err && err.message) || err),
      };
    });
  }

  /** 由真实工单判定「本岗位 ↔ 工单」的关系（视角 + 我的槽位）。 */
  function relationOf(wo, ctx, cfg) {
    const map = cfg.signoff.slotByGovernanceRole || {};
    const task = wo && wo.task;
    const roleKeys = govRolesOf(ctx);
    if (!task) return { kind: "outsider", slot: "", task: null };
    const gov = task.governance || {};
    const status = task.signoff_status || {};
    const cosignKeys = (status.required || gov.required_signoffs || []).map(function (r) {
      return String((r || {}).role || "");
    });
    const proposer = gov.proposed_by || {};
    let kind = "observer";
    if (roleKeys.some(function (k) { return cosignKeys.indexOf(k) !== -1; })) {
      kind = "cosigner";
    } else if (proposer.role && roleKeys.indexOf(String(proposer.role)) !== -1) {
      kind = "applicant";
    }
    let slot = "";
    if (kind === "cosigner") {
      // 会签人取自己命中的槽位（排除发起槽位 biz）
      slot = roleKeys.map(function (k) { return map[k]; })
        .filter(function (s) { return s && s !== "biz"; })[0] || "";
    } else if (kind === "applicant") {
      slot = map[String(proposer.role)] || "biz";
    }
    return { kind: kind, slot: slot, task: task, visibility: String(task.visibility || "") };
  }

  /** 从真实工单派生三槽位行（发起槽位 + 已选动作的会签槽位）。 */
  function slotsFromTask(task, cfg) {
    const labels = cfg.signoff.slotLabels || {};
    const map = cfg.signoff.slotByGovernanceRole || {};
    const gov = task.governance || {};
    const status = task.signoff_status || {};
    const required = status.required || gov.required_signoffs || [];
    const signoffs = gov.signoffs || [];
    const proposer = gov.proposed_by || {};
    const rows = [{
      role: String(labels[map[String(proposer.role || "")] || "biz"] || "接单人 · 业务")
        + "（发起 · " + String(proposer.role_name || "—") + "）",
      user: String(proposer.actor || "—"),
      status: proposer.actor ? "approved" : "pending",
      ts: proposer.ts || "",
    }];
    required.forEach(function (item) {
      const key = String((item || {}).role || "");
      const hit = signoffs.filter(function (s) {
        return s && String(s.role) === key;
      })[0] || null;
      rows.push({
        role: String(labels[map[key]] || (item || {}).role_name || key || "—"),
        user: hit ? String(hit.actor || "—") : "—",
        status: hit ? "approved" : "pending",
        ts: hit ? String(hit.ts || "") : "",
      });
    });
    return rows;
  }

  /* ============================================================
   * 取数：并行读 8 个故事对象（T04 装载层）
   * ============================================================ */

  function loadAll() {
    const kit = K();
    const names = Array.isArray(CFG.objects) ? CFG.objects : [];
    return Promise.all(names.map(function (name) {
      return kit.story(name).then(function (res) {
        return [name, res];
      }).catch(function (err) {
        return [name, {
          ok: false,
          rows: [],
          error: "演示装载层未就绪",
          note: String((err && err.message) || err),
        }];
      });
    })).then(function (pairs) {
      const ds = {};
      pairs.forEach(function (pair) {
        ds[pair[0]] = pair[1];
      });
      return ds;
    });
  }

  /* ============================================================
   * 派生：从事件流反查主线订单，再按订单聚合各对象
   * ============================================================ */

  function derive(ds) {
    const events = rowsOf(ds, "events");
    const s1Events = events.filter(function (ev) {
      return ev && String(ev.scene || "").toUpperCase() === "S1";
    });

    // 三处出处都在 S1 事件里：ATP 快照 / 报价单号 / 会签槽位结果（不硬编码订单号）
    const atpEv = s1Events.filter(function (ev) {
      return ev.payload && ev.payload[CFG.atp.eventMarker] !== undefined;
    })[0] || null;
    const quoteEv = s1Events.filter(function (ev) {
      return ev.payload && ev.payload[CFG.quote.eventMarker] !== undefined;
    })[0] || null;
    const signEv = s1Events.filter(function (ev) {
      return ev && String(ev.type) === "hitl";
    })[0] || null;

    const orderId = String((atpEv && atpEv.object_ref) || (signEv && signEv.object_ref)
      || (firstOf(s1Events) || {}).object_ref || "");

    const orders = rowsOf(ds, "sales_orders");
    const order = firstOf(byField(orders, "order_id", orderId));
    const quote = firstOf(byField(rowsOf(ds, "quotes"), "order_id", orderId));
    const line = firstOf(order && order.lines);
    const material = line
      ? firstOf(byField(rowsOf(ds, "materials"), "material_id", line.material_id)) : null;
    const customer = order
      ? firstOf(byField(rowsOf(ds, "customers"), "customer_id", order.customer_id)) : null;

    const bomRows = material
      ? byField(rowsOf(ds, "boms"), "material_id", material.material_id) : [];
    const boms = rowsOf(ds, "boms");
    const workOrders = order
      ? byField(rowsOf(ds, "work_orders"), "order_id", order.order_id) : [];

    // 料期 / 在途的取数范围＝本订单 BOM 用料（成品 + 各组成件）。
    // 只按成品物料号过滤会漏掉在途原料（如成品 MAT-5001 之下的 MAT-2202 在途批），
    // 料期初评正是在盯这些组成件的到料风险。
    const scopeMaterialIds = {};
    if (material && material.material_id) scopeMaterialIds[String(material.material_id)] = true;
    bomRows.forEach(function (bom) {
      (bom && Array.isArray(bom.lines) ? bom.lines : []).forEach(function (bomLine) {
        if (bomLine && bomLine.material_id) scopeMaterialIds[String(bomLine.material_id)] = true;
      });
    });
    const scopeIds = Object.keys(scopeMaterialIds);
    const allLots = rowsOf(ds, "lots");
    const scopedLots = allLots.filter(function (lot) {
      return lot && scopeMaterialIds[String(lot.material_id)];
    });
    const lots = scopedLots.length ? scopedLots : (material ? byField(allLots, "material_id",
      material.material_id) : []);

    const allPos = rowsOf(ds, "purchase_orders");
    const scopedPos = allPos.filter(function (po) {
      return po && scopeMaterialIds[String(po.material_id)];
    });
    const pos = scopedPos.length ? scopedPos : allPos;

    return {
      ds: ds,
      events: events,
      s1Events: s1Events,
      atpEv: atpEv,
      quoteEv: quoteEv,
      signEv: signEv,
      orderId: orderId,
      order: order,
      quote: quote,
      line: line,
      material: material,
      customer: customer,
      bomRows: bomRows,
      boms: boms,
      workOrders: workOrders,
      lots: lots,
      scopeIds: scopeIds,
      pos: pos,
      posScoped: scopedPos.length > 0,
      customerOrders: customer
        ? byField(rowsOf(ds, "sales_orders"), "customer_id", customer.customer_id) : [],
    };
  }

  /* ============================================================
   * 渲染主体
   * ============================================================ */

  function sectionTitle(title, extra) {
    const kit = K();
    return '<div class="section-title">' + kit.esc(title)
      + (extra ? '<span class="section-extra">' + kit.esc(extra) + "</span>" : "")
      + "</div>";
  }

  function degradeHtml(ds, failed) {
    const kit = K();
    const names = failed.map(function (name) {
      const res = ds[name] || {};
      return name + "（" + String(res.error || "未就绪") + "）";
    }).join("、");
    return '<div class="sk-degrade">' + kit.icon("warn", 15)
      + "<div><b>演示装载层未就绪：本屏不显示编造数字。</b>"
      + '<div class="sk-degrade-sub">当前状态：' + kit.esc(names)
      + "。恢复条件：GET /api/v2/demo/objects/{name} 可用（对象名："
      + kit.esc((CFG.objects || []).join("、")) + "）。</div></div></div>";
  }

  function buildHtml(d, ctx, readonly, wo) {
    const kit = K();
    const cfg = CFG;
    const labels = cfg.signoff.slotLabels || {};
    const role = roleOf(ctx) || {};
    // 视角以「真实工单可见性」为准（T15）；工单取不到时才回落事件流的槽位映射
    const woRel = relationOf(wo, ctx, cfg);
    const live = !!(wo && wo.ok === true && woRel.task);
    const legacySlot = mySlotOf(ctx, cfg);
    const viewKind = live ? woRel.kind : (wo && wo.ok === true ? "outsider"
      : viewKindOf(legacySlot));
    const mySlot = live ? woRel.slot : legacySlot;
    const task = woRel.task || null;

    if (!d.order || !d.atpEv) {
      return '<div class="sk-block">'
        + degradeHtml(d.ds, ["sales_orders", "events"])
        + note("主线订单或 S1 事件缺失：ATP 初评 / 报价 / 会签三处的出处都在 S1 事件流里，"
          + "取不到事件就不给任何数字（避免拍到屏幕上的是编造值）。")
        + "</div>";
    }

    const supportFailed = (cfg.objects || []).filter(function (name) {
      return !okOf(d.ds, name);
    });
    const supportWarn = supportFailed.length
      ? '<div class="sk-degrade">' + kit.icon("warn", 15)
        + "<div><b>部分支撑对象未就绪（相关明细表会显示空行）。</b>"
        + '<div class="sk-degrade-sub">未就绪对象：' + kit.esc(supportFailed.join("、")) + "</div></div></div>"
      : "";

    /* ---------- 现算值（全部由故事数据派生，屏内无散数） ---------- */
    const orderQty = sumBy(d.order.lines, "qty");
    const atpSourceCards = (cfg.atp.sources || []).map(function (src) {
      return {
        key: src.key,
        label: src.label,
        value: (d.atpEv.payload && d.atpEv.payload[src.field] !== undefined)
          ? d.atpEv.payload[src.field] : "",
        unit: src.unit,
        tone: src.tone,
      };
    });
    const onHand = Number(d.atpEv.payload[cfg.atp.sources[0].field]);
    const wip = Number(d.atpEv.payload[cfg.atp.sources[1].field]);
    const safety = Number(d.atpEv.payload[cfg.atp.sources[2].field]);
    const atpTotal = onHand + wip - safety;

    const quoteParts = (cfg.quote.parts || []).map(function (part) {
      return {
        key: part.key,
        label: part.label,
        value: d.quote ? d.quote[part.field] : "",
        formula: part.formula,
        recompute: part.recompute,
      };
    });
    const partsSum = quoteParts.reduce(function (acc, part) {
      const n = Number(part.value);
      return acc + (Number.isFinite(n) ? n : 0);
    }, 0);
    const quoteLow = d.quote ? Number(d.quote[cfg.quote.lowField]) : NaN;
    const quoteHigh = d.quote ? Number(d.quote[cfg.quote.highField]) : NaN;
    const buffer = (Number.isFinite(quoteHigh) && Number.isFinite(quoteLow) && quoteLow > 0)
      ? (quoteHigh / quoteLow - 1) : NaN;
    const highCalc = Number.isFinite(quoteLow) ? quoteLow * (1 + cfg.quote.bufferRate) : NaN;
    const opsHoursPerPiece = d.workOrders.reduce(function (acc, wo) {
      const ops = Array.isArray(wo && wo.ops) ? wo.ops : [];
      return acc + sumBy(ops, "std_hours");
    }, 0) / (d.workOrders.length || 1);
    const laborHours = opsHoursPerPiece * orderQty;
    const laborRate = (Number.isFinite(Number(laborHours)) && laborHours > 0 && d.quote)
      ? Number(d.quote[cfg.quote.parts[1].field]) / laborHours : NaN;
    const toolFee = d.quote ? Number(d.quote[cfg.quote.parts[2].field]) * orderQty : NaN;

    const rawSlots = (d.signEv && d.signEv.payload && Array.isArray(d.signEv.payload.slots)
      && d.signEv.payload.slots.length)
      ? d.signEv.payload.slots.map(String) : cfg.signoff.slotOrder.slice();
    const slotStatus = d.signEv
      ? (cfg.signoff.resultToStatus[String((d.signEv.payload || {}).result)] || "pending")
      : "pending";
    const slots = rawSlots.map(function (key) {
      const label = String(labels[key] || key);
      return {
        role: label + (key === mySlot ? "（你所在槽位）" : ""),
        user: "—",
        status: slotStatus,
        ts: d.signEv ? d.signEv.ts : "",
      };
    });
    const approvedCount = slots.filter(function (s) {
      return String(s.status) === "approved";
    }).length;

    const latestPo = d.pos.reduce(function (acc, po) {
      const day = po && po.promise_date ? String(po.promise_date) : "";
      return (day && day > acc) ? day : acc;
    }, "");
    const delayedPos = d.pos.filter(function (po) {
      return po && String(po.status) === "delayed";
    }).length;
    const transitLots = d.lots.filter(function (lot) {
      return lot && String(lot.location || "").indexOf("在途") !== -1;
    });
    const transitQty = sumBy(transitLots, "qty");
    const promiseDay = d.order.promise_date ? String(d.order.promise_date) : "";
    const poAhead = (latestPo && promiseDay) ? (latestPo <= promiseDay) : null;

    const custOrders = d.customerOrders;
    const inProduction = custOrders.filter(function (o) {
      return o && String(o.status) === "in_production";
    }).length;
    const profileText = d.customer ? String(d.customer[cfg.customer.profileField] || "—") : "—";

    /* ---------- 真实治理工单派生（T15 / order_review） ---------- */
    const woc = cfg.signoff.workOrder || {};
    const liveSlots = task ? slotsFromTask(task, cfg) : [];
    const signoffProgress = task
      ? String((task.signoff_status || {}).progress || "—") : "—";
    const missingCount = task
      ? ((task.signoff_status || {}).missing || []).length : 0;

    /* ---------- 口径字典：每张数字卡一个 drill ---------- */
    drills = {};

    drills["order-qty"] = function () {
      return {
        title: "订单量",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", numText(orderQty, 0) + " 片"],
          ["算法", "Σ sales_orders[].lines[].qty（spec-v1.md §2.4）"],
          ["出处", "GET /api/v2/demo/objects/sales_orders → " + String(d.order.order_id)],
          ["主线订单认定", "S1 事件流 payload 的 object_ref 反查（不硬编码订单号）"],
        ]) + detail("订单行明细", ["物料", "数量", "单价"], (d.order.lines || []).map(function (l) {
          return [String(l.material_id), numText(l.qty, 0), numText(l.unit_price, 2)];
        })),
      };
    };
    drills["order-promise"] = function () {
      return {
        title: "对客承诺交期",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", dateText(d.order.promise_date)],
          ["口径", "sales_orders.promise_date（对客承诺交期，spec-v1.md §2.4）"],
          ["OTD 判据", "actual_ship_date ≤ promise_date（spec-v1.md §5）"],
          ["出处", "GET /api/v2/demo/objects/sales_orders → " + String(d.order.order_id)],
          ["备注", "本屏不对承诺是否可满足下结论：ATP / 料期两块列出各自来源，供评审判断"],
        ]),
      };
    };
    drills["order-status"] = function () {
      return {
        title: "订单评审状态",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", String(cfg.statusLabels[String(d.order.status)] || d.order.status || "—")],
          ["原始值", String(d.order.status || "—")],
          ["口径", "sales_orders.status 枚举（spec-v1.md §2.4），中文标签见本屏口径字典"],
          ["出处", "GET /api/v2/demo/objects/sales_orders → " + String(d.order.order_id)],
          ["佐证", d.signEv ? "会签事件 " + String(d.signEv.event_id) + "（" + dateText(d.signEv.ts) + "）" : "会签事件未就绪"],
        ]),
      };
    };
    drills["customer-name"] = function () {
      return {
        title: "客户",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", d.customer ? String(d.customer.name || "—") : "—"],
          ["客户编号", d.customer ? String(d.customer.customer_id || "—") : "—"],
          ["关联方式", "sales_orders.customer_id → customers.customer_id（spec-v1.md §2.2）"],
          ["出处", "GET /api/v2/demo/objects/customers"],
          ["该客户订单", String(custOrders.length) + " 张（现算：按 customer_id 过滤）"],
        ]),
      };
    };

    (cfg.atp.sources || []).forEach(function (src) {
      drills["atp-" + src.key] = function () {
        const payloadValue = d.atpEv.payload ? d.atpEv.payload[src.field] : "";
        const rowsForDetail = [];
        if (src.detailObject === "work_orders") {
          d.workOrders.forEach(function (wo) {
            rowsForDetail.push([String(wo.wo_id), String(wo.status),
              numText(wo.qty_planned, 0), numText(wo.qty_good, 0)]);
          });
        } else if (src.detailObject === "lots") {
          d.lots.forEach(function (lot) {
            rowsForDetail.push([String(lot.lot_id), String(lot.material_id),
              String(lot.location), numText(lot.qty, 0)]);
          });
        }
        const heads = src.detailObject === "work_orders"
          ? ["工单", "状态", "计划量", "良品量"] : ["批次", "物料", "库位", "数量"];
        return {
          title: src.label,
          subtitle: "口径 · 出处 · 现算",
          html: kv(evPairs(d.atpEv).concat([
            ["本卡字段", "payload." + src.field],
            ["取值", payloadValue === "" ? "—" : numText(payloadValue, 0) + " " + String(src.unit)],
            ["口径", String(cfg.atp.formula) + "（三来源之一，spec-v1.md §5）"],
            ["口径备注", String(cfg.atp.formulaNote)],
          ])) + (rowsForDetail.length
            ? detail("关联明细（" + String(src.detailObject) + "）", heads, rowsForDetail)
            : note("关联明细对象 " + String(src.detailObject || "—") + " 未就绪或该订单无行")),
        };
      };
    });
    drills["atp-total"] = function () {
      return {
        title: String(cfg.atp.totalLabel),
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", numText(atpTotal, 0) + " " + String(cfg.atp.totalUnit)],
          ["现算式", numText(onHand, 0) + " + " + numText(wip, 0) + " − " + numText(safety, 0)
            + " = " + numText(atpTotal, 0)],
          ["口径", String(cfg.atp.formula) + "（spec-v1.md §5「ATP 可用量」）"],
          ["出处", "events → " + String(d.atpEv.event_id) + " · payload（"
            + dateText(d.atpEv.ts) + "）"],
          ["订单量对账", "订单量 " + numText(orderQty, 0) + " 片，ATP 可用 "
            + numText(atpTotal, 0) + " 片（现算差值 " + numText(atpTotal - orderQty, 0) + " 片）"],
          ["备注", "ATP 与订单量的差值是事实陈述，初评结论（是否接单）属于评审意见，本屏不代填"],
        ]),
      };
    };

    drills["po-latest"] = function () {
      return {
        title: "最晚采购承诺到料日",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", dateText(latestPo)],
          ["算法", "max purchase_orders[].promise_date（本屏范围内）"],
          ["范围", d.posScoped
            ? "本订单 BOM 用料命中 " + String(d.pos.length) + " 张采购单"
            : "BOM 用料未命中采购单，退回全部 " + String(d.pos.length) + " 张（已在屏上标注）"],
          ["出处", "GET /api/v2/demo/objects/purchase_orders（spec-v1.md §2.7）"],
          ["对客交期比对", poAhead === null ? "—"
            : (dateText(latestPo) + (poAhead ? " ≤ " : " > ") + dateText(promiseDay)
              + "（" + (poAhead ? "料期早于交期" : "料期晚于交期") + "，现算）")],
        ]) + detail("采购在途明细", ["采购单", "物料", "供应商", "承诺到料", "状态"],
          d.pos.map(function (po) {
            return [String(po.po_id), String(po.material_id), String(po.supplier_id),
              dateText(po.promise_date), String(po.status)];
          }), "purchase_orders 未就绪或本订单 BOM 无对应采购单"),
      };
    };
    drills["po-delayed"] = function () {
      return {
        title: "延期采购单数",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", String(delayedPos) + " 张"],
          ["算法", "count(purchase_orders[].status === \"delayed\")"],
          ["范围", "同上「料期」范围：" + String(d.pos.length) + " 张采购单"],
          ["出处", "GET /api/v2/demo/objects/purchase_orders"],
        ]) + detail("延期明细", ["采购单", "承诺到料", "实际到货", "状态"],
          d.pos.filter(function (po) {
            return po && String(po.status) === "delayed";
          }).map(function (po) {
            return [String(po.po_id), dateText(po.promise_date),
              dateText(po.actual_date), String(po.status)];
          }), "无延期采购单"),
      };
    };
    drills["lot-transit"] = function () {
      return {
        title: "在途批次",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", String(transitLots.length) + " 批 / " + numText(transitQty, 0) + " 片"],
          ["算法", "lots 中 location 含「在途」的行数与其 qty 之和"],
          ["取数范围", "本订单 BOM 用料（成品 + 各组成件）相关批次，共 "
            + String(d.lots.length) + " 批 / 物料 " + String(d.scopeIds.join("、"))],
          ["出处", "GET /api/v2/demo/objects/lots（spec-v1.md §2.6）"],
          ["备注", "在途批次不并入 ATP 快照（ATP 三来源为在库 / 在制 / 安全库存），"
            + "与 ATP 可用量不同轴"],
        ]) + detail("在途明细", ["批次", "物料", "库位", "数量"],
          transitLots.map(function (lot) {
            return [String(lot.lot_id), String(lot.material_id), String(lot.location),
              numText(lot.qty, 0)];
          }), "无在途批次"),
      };
    };

    (cfg.quote.parts || []).forEach(function (part) {
      drills["quote-" + part.key] = function () {
        const value = d.quote ? d.quote[part.field] : "";
        const extra = [
          ["本卡字段", "quotes." + part.field],
          ["取值", value === "" || value === undefined ? "—" : numText(value, 2) + " 元"],
          ["口径", String(part.formula)],
        ];
        if (part.recompute === "ops") {
          extra.push(["现算", "Σ 工序标准工时 × 订单量 = " + numText(opsHoursPerPiece, 3)
            + " h/件 × " + numText(orderQty, 0) + " 件 = " + numText(laborHours, 2) + " h"]);
          extra.push(["反算费率", "labor_cost ÷ 总工时 = " + numText(laborRate, 2) + " 元/小时"]);
        } else if (part.recompute === "tool") {
          extra.push(["现算", "模具摊销 × 订单量 = " + numText(toolFee, 2) + " 元（反算模具费口径）"]);
        } else {
          extra.push(["现算", "本屏不做重算：BOM 只给用量，最新采购价不在本数据集内，"
            + "不以 materials.std_cost 代替（避免拿另一个口径冒充本值）"]);
        }
        if (!d.quote) {
          return {
            title: part.label,
            subtitle: "口径 · 出处 · 现算",
            html: kv([["状态", "quotes 未就绪或该订单无报价行：本卡不显示数"]]),
          };
        }
        const rowsForDetail = part.recompute === "bom"
          ? (d.bomRows.length ? d.bomRows.map(function (bom) {
            return [String(bom.bom_id), String(bom.kind),
              String((bom.lines || []).length) + " 行", String(bom.version)];
          }) : [])
          : d.workOrders.reduce(function (acc, wo) {
            (Array.isArray(wo.ops) ? wo.ops : []).forEach(function (op) {
              acc.push([String(wo.wo_id), String(op.op_no), String(op.name || op.equipment_id),
                numText(op.std_hours, 3)]);
            });
            return acc;
          }, []);
        const heads = part.recompute === "bom"
          ? ["BOM", "类型", "行数", "版本"] : ["工单", "工序", "工位", "标准工时(h)"];
        return {
          title: part.label,
          subtitle: "口径 · 出处 · 现算",
          html: kv(evPairs(d.quoteEv).concat(extra))
            + (rowsForDetail.length ? detail("关联明细", heads, rowsForDetail)
              : note("关联明细对象未就绪或该订单无行")),
        };
      };
    });
    drills["quote-low"] = function () {
      return {
        title: "报价下限",
        subtitle: "口径 · 出处 · 现算",
        html: kv(evPairs(d.quoteEv).concat([
          ["本卡字段", "quotes." + String(cfg.quote.lowField)],
          ["取值", numText(quoteLow, 2) + " 元"],
          ["口径", String(cfg.quote.lowFormula)],
          ["现算校验", numText(partsSum, 2) + "（三分量之和）− " + numText(quoteLow, 2)
            + " = " + numText(partsSum - quoteLow, 2) + " 元差额"],
          ["出处", "GET /api/v2/demo/objects/quotes → " + String((d.quote || {}).quote_id || "—")],
        ])),
      };
    };
    drills["quote-high"] = function () {
      return {
        title: "报价上限",
        subtitle: "口径 · 出处 · 现算",
        html: kv(evPairs(d.quoteEv).concat([
          ["本卡字段", "quotes." + String(cfg.quote.highField)],
          ["取值", numText(quoteHigh, 2) + " 元"],
          ["口径", String(cfg.quote.highFormula)],
          ["毛利缓冲率", numText((cfg.quote.bufferRate || 0) * 100, 2) + "%（口径字典声明值）"],
          ["现算校验", numText(highCalc, 2) + "（下限 × (1+缓冲) 四舍五入到分）"],
        ])),
      };
    };
    drills["quote-buffer"] = function () {
      return {
        title: "上限相对下限的毛利缓冲",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", Number.isFinite(buffer) ? ("+" + numText(buffer * 100, 2) + "%") : "—"],
          ["算法", "price_high ÷ price_low − 1（现算）"],
          ["现式", numText(quoteHigh, 2) + " ÷ " + numText(quoteLow, 2) + " − 1 = "
            + (Number.isFinite(buffer) ? numText(buffer, 4) : "—")],
          ["口径依据", "spec-v1.md §2.5 报价区间（下限 = 成本三分量之和；上限含毛利缓冲）"],
          ["出处", "quotes." + String(cfg.quote.lowField) + " / quotes."
            + String(cfg.quote.highField)],
        ]),
      };
    };

    drills["signoff-slots"] = function () {
      return {
        title: "会签槽位",
        subtitle: "口径 · 出处 · 现算",
        html: kv(evPairs(d.signEv).concat([
          ["槽位顺序", rawSlots.join(" → ")],
          ["槽位标签", rawSlots.map(function (key) {
            return String(key) + "=" + String(labels[key] || key);
          }).join("；")],
          ["进度", String(approvedCount) + " / " + String(slots.length) + " 已通过"],
          ["你的槽位", mySlot ? String(labels[mySlot] || mySlot) + "（" + mySlot + "）" : "无（观察者）"],
        ])) + detail("留痕明细", ["槽位", "状态", "时间"],
          slots.map(function (slot) {
            return [slot.role, slot.status, dateText(slot.ts)];
          })),
      };
    };
    drills["signoff-trace"] = function () {
      return {
        title: "会签留痕（事件流回放）",
        subtitle: "口径 · 出处 · 现算",
        html: kv(evPairs(d.signEv).concat([
          ["留痕形态", "events.jsonl 的 hitl 事件（payload.slots + payload.result + ts）"],
          ["与真实工单的关系", "平台已登记 order_review 治理工单（wf_id="
            + String(woc.wfId || "—") + "，T15）；上方工单卡取自该工单的真实 "
            + "signoff_status，本卡为事件流回放，两者并列互证"],
          ["姓名列", "事件未记录会签人姓名，本卡槽位 user 列留空（不虚构姓名）；"
            + "真实工单的会签人姓名见上方工单卡"],
          ["口径依据", "spec-v1.md §5 会签槽位口径"],
        ])),
      };
    };

    drills["workorder-live"] = function () {
      return {
        title: "订单评审治理工单（order_review）",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["工单号", task ? String(task.task_uid || "—") : "不可见（本岗位不在治理域）"],
          ["流程 id", String(woc.wfId || "—")],
          ["状态", task ? String(task.status || "—") : "—"],
          ["三槽位", (cfg.signoff.slotOrder || []).map(function (k) {
            return String(labels[k] || k);
          }).join(" / ")],
          ["出处", "GET /api/v2/workflow/pending（按 Authorization 令牌裁决，"
            + "按 wf_id=" + String(woc.wfId || "—") + " 过滤命中）"],
          ["可见性判据", "permissions.yaml 的「角色 × 槽位」矩阵：本岗位治理角色 "
            + (govRolesOf(ctx).join("、") || "—") + " → "
            + (task ? "持有 order_review 槽位权限（可见）" : "未持有（不可见）")],
          ["纪律", "会签 / 终批等写操作在「人工确认」治理窗口执行；本屏只读、无写按钮"],
        ]),
      };
    };
    drills["workorder-signoff"] = function () {
      return {
        title: "会签进度（工单真实留痕）",
        subtitle: "口径 · 出处 · 现算",
        html: (task
          ? kv([
            ["进度", signoffProgress + "（缺 " + String(missingCount) + " 个会签人）"],
            ["会签齐备", ((task.signoff_status || {}).satisfied)
              ? "是" : "否（会签齐备前不得终批）"],
            ["已选动作", String((task.signoff_status || {}).selected_action || "—")],
            ["寻源", "工单 " + String(task.task_uid || "—") + " · "
              + "GET /api/v2/workflow/pending"],
          ]) + detail("三槽位状态（发起 + 会签）", ["槽位", "留痕人", "状态"],
            liveSlots.map(function (s) {
              return [String(s.role), String(s.user), String(s.status)];
            }))
          : note("工单对本岗位不可见：会签进度只能来自工单，不可见即不展示（不臆造进度）。")),
      };
    };
    drills["workorder-visibility"] = function () {
      return {
        title: "本岗位对待办工单的可见性",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["当前岗位", String(role.name || "—") + " · " + String(role.user || "—")],
          ["治理角色", govRolesOf(ctx).join("、") || "—"],
          ["可见性", task
            ? String(task.visibility || "—") + "（"
              + String(VIS_TEXT[String(task.visibility || "")] || "—") + "）"
            : "不可见（hidden）"],
          ["我能不能动作", (task && task.my_action) ? String(task.my_action) : "否（只看不办）"],
          ["视角", String(cfg.signoff.viewLabels[viewKind] || "—")],
          ["判定链路", "登录令牌 → roles → /api/v2/workflow/pending → "
            + "workflow_scope.classify（槽位 ∈ 我持权限的槽位？）"],
          ["纪律", String(cfg.signoff.visibilityNote)],
        ]),
      };
    };
    drills["signoff-view"] = function () {
      return {
        title: "本视角可见内容",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["当前岗位", String((role.name || "—")) + " · " + String((role.user || "—"))],
          ["岗位 id", String(role.id || "—")],
          ["治理角色", (Array.isArray(role.roles) && role.roles.length) ? role.roles.join("、") : "—"],
          ["视角", String(cfg.signoff.viewLabels[viewKind] || "—")],
          ["你所在槽位", mySlot ? String(labels[mySlot] || mySlot) : "无（观察者）"],
          ["可见范围", visibilityText(viewKind, mySlot, labels)],
          ["只读", readonly ? "是（本屏不发起写操作）" : "否"],
          ["纪律依据", String(cfg.signoff.visibilityNote)],
        ]),
      };
    };

    drills["customer-credit"] = function () {
      return {
        title: "客户信用等级",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", d.customer ? String(d.customer[cfg.customer.gradeField] || "—") : "—"],
          ["口径", "customers." + String(cfg.customer.gradeField) + "（spec-v1.md §2.2）"],
          ["出处", "GET /api/v2/demo/objects/customers → "
            + String((d.customer || {}).customer_id || "—")],
          ["关联方式", "sales_orders.customer_id → customers.customer_id"],
        ]),
      };
    };
    drills["customer-orders"] = function () {
      return {
        title: "该客户订单数",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["取值", String(custOrders.length) + " 张 / 在产 " + String(inProduction) + " 张"],
          ["算法", "按 customer_id 过滤 sales_orders；在产 = status === \"in_production\""],
          ["出处", "GET /api/v2/demo/objects/sales_orders"],
          ["备注", "这是" + String(((d.customer || {}).name) || "该客户")
            + "历史下单事实的现算，不含预测"],
        ]) + detail("订单明细", ["订单", "状态", "承诺交期", "订购量"],
          custOrders.map(function (o) {
            return [String(o.order_id), String(cfg.statusLabels[String(o.status)] || o.status),
              dateText(o.promise_date), numText(sumBy(o.lines, "qty"), 0)];
          })),
      };
    };
    drills["customer-profile"] = function () {
      return {
        title: "客户历史质量档案",
        subtitle: "口径 · 出处 · 现算",
        html: kv([
          ["字段", "customers." + String(cfg.customer.profileField)],
          ["原文", profileText],
          ["口径", "档案为 T02 生成的故事数据集字段原文，本屏只做展示与出处标注，不改写、不解析成结论"],
          ["出处", "GET /api/v2/demo/objects/customers → "
            + String((d.customer || {}).customer_id || "—")],
          ["关联", "该客户 12 个月内订单 " + String(custOrders.length) + " 张，在产 "
            + String(inProduction) + " 张（customer-orders 卡现算）"],
        ]),
      };
    };

    /* ---------- 页面骨架 ---------- */
    const viewBadge = kit.badge(String(cfg.signoff.viewLabels[viewKind] || "—"), "badge-blue");
    const readonlyBadge = readonly
      ? kit.badge("只读访客 · 不发起写操作", "badge-gray")
      : kit.badge("可写岗位 · 本屏仍只读演示", "badge-blue");

    const head = kit.pageHead({
      icon: cfg.icon,
      title: cfg.title,
      desc: "接单评审初评：ATP 三来源（在库 / 在制 / 安全库存）、料期与在途、"
        + "报价区间与成本三分量、订单评审一次会签（三槽位）。每张数字卡都可点开"
        + "「口径 · 出处 · 现算」。",
      extra: viewBadge + readonlyBadge + kit.badge("T07 场景屏", "badge-blue"),
    });

    /* ---------- 一、订单与客户 ---------- */
    const block1 = '<div class="sk-block">'
      + sectionTitle("订单与客户", "来源：sales_orders / customers")
      + kit.numberRow([
        {
          label: "订单量", value: numText(orderQty, 0), unit: "片",
          hint: "口径：Σ lines[].qty（spec-v1.md §2.4）", tone: "blue", drill: "order-qty",
        },
        {
          label: "对客交期", value: dateText(d.order.promise_date), unit: "",
          hint: "口径：promise_date（OTD 判据见 §5）", tone: "amber", drill: "order-promise",
        },
        {
          label: "订单评审状态",
          value: String(cfg.statusLabels[String(d.order.status)] || d.order.status || "—"),
          unit: "",
          hint: "口径：status 枚举（原始值 approval 见下钻）", tone: "green", drill: "order-status",
        },
        {
          label: "客户", value: d.customer ? String(d.customer.name || "—") : "—", unit: "",
          hint: "口径：customer_id 关联（spec-v1.md §2.2）", tone: "gray", drill: "customer-name",
        },
      ]) + "</div>";

    /* ---------- 二、ATP 初评三来源 ---------- */
    const atpCards = atpSourceCards.map(function (card) {
      return {
        label: card.label,
        value: card.value === "" ? "—" : numText(card.value, 0),
        unit: card.unit,
        hint: "口径：payload." + String(card.key === "on_hand" ? "atp_on_hand"
          : card.key === "wip" ? "atp_wip" : "atp_safety") + "（三来源之一）",
        tone: card.tone,
        drill: "atp-" + card.key,
      };
    });
    atpCards.push({
      label: cfg.atp.totalLabel, value: numText(atpTotal, 0), unit: cfg.atp.totalUnit,
      hint: "现算：" + cfg.atp.formula, tone: cfg.atp.totalTone, drill: "atp-total",
    });
    const block2 = '<div class="sk-block">'
      + sectionTitle("ATP 初评三来源（在库 / 在制 / 安全库存）",
        "出处：events → " + String(d.atpEv.event_id))
      + kit.numberRow(atpCards)
      + note("现算口径：" + cfg.atp.formula + "。"
        + "在库、在制明细分别下钻到 lots 与 work_orders（明细仅列示，不与快照加总）。")
      + "</div>";

    /* ---------- 三、料期与在途 ---------- */
    const block3 = '<div class="sk-block">'
      + sectionTitle("料期初评（采购承诺 vs 对客交期）",
        "范围：本订单 BOM 用料对应采购单")
      + kit.numberRow([
        {
          label: "最晚采购承诺到料", value: dateText(latestPo), unit: "",
          hint: poAhead === null ? "口径：max promise_date（本屏范围）"
            : "现算：" + (poAhead ? "早于" : "晚于") + "对客交期 " + dateText(promiseDay),
          tone: poAhead === false ? "amber" : "blue", drill: "po-latest",
        },
        {
          label: "延期采购单", value: String(delayedPos), unit: "张",
          hint: "口径：count(status === delayed)", tone: delayedPos ? "amber" : "green",
          drill: "po-delayed",
        },
        {
          label: "在途批次", value: numText(transitQty, 0), unit: "片",
          hint: "口径：lots.location 含「在途」（" + String(transitLots.length) + " 批）",
          tone: "purple", drill: "lot-transit",
        },
      ]) + "</div>";

    /* ---------- 四、报价区间与三分量 ---------- */
    const block4 = '<div class="sk-block">'
      + sectionTitle("报价区间与成本三分量",
        "出处：quotes → " + String((d.quote || {}).quote_id || "—")
        + (d.quoteEv ? " / events → " + String(d.quoteEv.event_id) : ""))
      + kit.numberRow(quoteParts.map(function (part, index) {
        return {
          label: part.label,
          value: part.value === "" || part.value === undefined ? "—" : numText(part.value, 2),
          unit: "元",
          hint: "口径：" + String(part.formula),
          tone: index === 1 ? "purple" : index === 2 ? "gray" : "blue",
          drill: "quote-" + part.key,
        };
      }))
      + kit.numberRow([
        {
          label: "报价下限", value: numText(quoteLow, 2), unit: "元",
          hint: "现算校验：三分量之和 " + numText(partsSum, 2) + " 元", tone: "green",
          drill: "quote-low",
        },
        {
          label: "报价上限", value: numText(quoteHigh, 2), unit: "元",
          hint: "口径：下限 × (1 + 毛利缓冲）", tone: "green", drill: "quote-high",
        },
        {
          label: "毛利缓冲",
          value: Number.isFinite(buffer) ? "+" + numText(buffer * 100, 2) + "%" : "—",
          unit: "",
          hint: "现算：price_high ÷ price_low − 1", tone: "amber", drill: "quote-buffer",
        },
      ])
      + note("三分量之和 = " + numText(partsSum, 2) + " 元，与报价下限 "
        + numText(quoteLow, 2) + " 元现算差额 " + numText(partsSum - quoteLow, 2)
        + " 元；上限按下限 × (1 + "
        + numText((cfg.quote.bufferRate || 0) * 100, 2) + "%) 现算为 "
        + numText(highCalc, 2) + " 元（四舍五入到分）。")
      + "</div>";

    /* ---------- 五、订单评审 · 真实治理工单（T15 order_review） ---------- */
    const liveStatus = !wo || wo.ok !== true
      ? note("治理工单未就绪（"
        + (wo && wo.reason === "offline" ? "后端离线探针未通过"
          : wo && wo.reason === "no-api" ? "渲染上下文未提供 api"
            : "接口不可用或返回 4xx") + "）。本卡不臆造工单：会签状态以事件流回放为准（见下卡）。")
      : (!task
        ? note(String(woc.outsiderNote || "本岗位不在治理域：工单不可见。"))
          + kv([
            ["本岗位治理角色", govRolesOf(ctx).join("、") || "—"],
            ["判定依据", "permissions.yaml 的「角色 × 槽位」矩阵；"
              + "后端按令牌角色裁决，无该槽位权限即不返回（fail-closed）"],
            ["后端结果", "队列中无 wf_id=" + String(woc.wfId || "—")
              + " 的工单；即使拿到工单号，详情接口也返回 404（不把 ID 空间变成可探测信息）"],
            ["纪律", "前端不另拉全量队列兜底——不借别人的眼睛看"],
          ])
        : kv([
          ["工单号", String(task.task_uid || "—")],
          ["流程 id", String(woc.wfId || "—")],
          ["状态", String(task.status || "—")],
          ["已选动作", String((task.signoff_status || {}).selected_action || "—")],
          ["会签进度", signoffProgress + "（缺 " + String(missingCount) + " 个会签人）"],
          ["本岗位可见性", String(task.visibility || "—") + "（"
            + String(VIS_TEXT[String(task.visibility || "")] || "—") + "）"],
          ["我能不能动作", (task && task.my_action) ? String(task.my_action) : "否（只看不办）"],
          ["SLA 到期", dateText(task.sla_due_at)],
          ["留痕纪律", String(woc.readonlyNote || "")],
        ])
        + kit.signoffCard({
          title: String(woc.blockTitle || "订单评审会签（真实工单）"),
          slots: liveSlots,
          readonly: true,
          note: String(woc.readonlyNote || ""),
        }));

    const blockLive = '<div class="sk-block">'
      + sectionTitle(String(woc.blockTitle || "订单评审 · 真实治理工单"),
        wo && wo.ok === true
          ? "留痕：GET /api/v2/workflow/pending（wf_id=" + String(woc.wfId || "—") + "）"
          : "留痕：治理工单未就绪")
      + kit.numberRow([
        {
          label: "治理工单", value: task ? "1" : "0", unit: "条",
          hint: "口径：GET /api/v2/workflow/pending 按 wf_id 过滤（T15）",
          tone: task ? "green" : "gray", drill: "workorder-live",
        },
        {
          label: "会签进度", value: signoffProgress, unit: "",
          hint: "口径：工单 signoff_status（会签齐备前不得终批）",
          tone: (task && (task.signoff_status || {}).satisfied) ? "green" : "amber",
          drill: "workorder-signoff",
        },
        {
          label: "本岗位可见性",
          value: task ? String(task.visibility || "—") : "不可见", unit: "",
          hint: "口径：permissions.yaml 槽位 × 角色矩阵（后端裁决）", tone: "purple",
          drill: "workorder-visibility",
        },
        {
          label: "视角", value: String(cfg.signoff.viewLabels[viewKind] || "—"), unit: "",
          hint: "口径：由工单 proposed_by / required_signoffs 与本人角色比对得出",
          tone: "blue", drill: "workorder-visibility",
        },
      ])
      + liveStatus
      + '<div class="sk-note">本屏只读：会签与终批等写操作一律在「人工确认」治理窗口执行，'
      + "此处不出现写按钮（演示身份为只读访客）。</div>"
      + "</div>";

    /* ---------- 六、订单评审会签（事件流回放） ---------- */
    const block5 = '<div class="sk-block">'
      + sectionTitle("订单评审 · 事件流回放（三槽位）",
        d.signEv ? "留痕：events → " + String(d.signEv.event_id) : "留痕：S1 事件缺失")
      + kit.signoffCard({
        title: "接单评审会签（槽位：接单人 / 技术 / 计划）· 事件流回放",
        slots: slots,
        readonly: readonly,
        note: "留痕取自 events.jsonl 的 hitl 事件（槽位 payload.slots="
          + rawSlots.join("/") + "，payload.result="
          + String((d.signEv && d.signEv.payload && d.signEv.payload.result) || "—")
          + "，时间 " + dateText(d.signEv ? d.signEv.ts : "") + "）。"
          + "事件未记录会签人姓名，故姓名列留空；"
          + "平台已登记 order_review 治理工单（wf_id=" + String(woc.wfId || "—")
          + "），真实会签人与进度见上方工单卡；演示身份只读，本屏不发起写操作。",
      })
      + kit.numberRow([
        {
          label: "会签槽位", value: String(slots.length), unit: "个",
          hint: "口径：payload.slots（spec-v1.md §5）", tone: "blue", drill: "signoff-slots",
        },
        {
          label: "已通过槽位", value: String(approvedCount), unit: "个",
          hint: "口径：payload.result → 状态映射（本屏口径字典）",
          tone: approvedCount === slots.length && slots.length ? "green" : "amber",
          drill: "signoff-slots",
        },
        {
          label: "留痕事件", value: d.signEv ? "1" : "0", unit: "条",
          hint: "口径：count(events.type === hitl && scene === S1)", tone: "purple",
          drill: "signoff-trace",
        },
      ])
      + '<div class="card"><div class="card-head"><div class="card-title">'
      + kit.icon("approve", 15) + kit.esc("本视角要点 · " + String(cfg.signoff.viewLabels[viewKind] || "—"))
      + '</div><div class="card-extra">' + kit.esc("当前岗位 "
        + String(role.name || "—") + " · " + String(role.user || "—")) + "</div></div>"
      + kv([
        ["视角", String(cfg.signoff.viewLabels[viewKind] || "—")],
        ["你所在槽位", mySlot ? String(labels[mySlot] || mySlot) : "无（观察者）"],
        ["可见范围", visibilityText(viewKind, mySlot, labels)],
        ["本视角要点", viewPointText(viewKind, labels)],
        ["槽位状态来源", d.signEv ? String(d.signEv.event_id) + " · payload.result="
          + String((d.signEv.payload || {}).result || "—") : "S1 会签事件缺失"],
      ])
      + '<div class="sk-note">' + kit.esc(String(cfg.signoff.visibilityNote)) + "</div></div>"
      + "</div>";

    /* ---------- 六、客户历史质量档案 ---------- */
    const block6 = '<div class="sk-block">'
      + sectionTitle("客户历史质量档案", "来源：customers → "
        + String((d.customer || {}).customer_id || "—"))
      + kit.numberRow([
        {
          label: "客户信用等级",
          value: d.customer ? String(d.customer[cfg.customer.gradeField] || "—") : "—", unit: "",
          hint: "口径：customers." + String(cfg.customer.gradeField), tone: "blue",
          drill: "customer-credit",
        },
        {
          label: "该客户订单", value: String(custOrders.length), unit: "张",
          hint: "现算：按 customer_id 过滤 sales_orders", tone: "gray", drill: "customer-orders",
        },
        {
          label: "其中在产", value: String(inProduction), unit: "张",
          hint: "现算：status === in_production", tone: "purple", drill: "customer-orders",
        },
      ])
      + '<div class="card"><div class="card-head"><div class="card-title">'
      + kit.icon("shield", 15) + kit.esc("质量档案原文（customers." + cfg.customer.profileField + "）")
      + '</div><div class="card-extra">' + kit.esc(String((d.customer || {}).name || "—"))
      + "</div></div>" + note(profileText)
      + '<div class="sk-note">档案为故事数据集字段原文，本屏只展示与标注出处，不解析成结论。'
      + "点击下方数字卡可见字段口径。</div></div>"
      + "</div>";

    /* ---------- 七、事件流 ---------- */
    const flow = d.s1Events.map(function (ev) {
      const payload = ev.payload || {};
      return {
        ts: ev.ts,
        type: ev.type,
        text: String(payload.note || payload.result || ev.event_id || ""),
        scene: ev.scene,
      };
    }).sort(function (a, b) {
      return String(a.ts) < String(b.ts) ? -1 : 1;
    });
    const block7 = flow.length
      ? '<div class="sk-block">' + kit.eventFlow(flow, {
        title: cfg.timelineTitle,
        extra: "来源：events.jsonl（scene=S1）",
        note: "事件文本取自 payload.note / payload.result 原文；"
          + "本屏所有「出处」列均指向这三条事件中的一条。",
      }) + "</div>"
      : '<div class="sk-block">' + note("S1 事件流未就绪：本屏的 ATP / 报价 / 会签出处都在事件流里。")
        + "</div>";

    /* ---------- 页脚纪律 ---------- */
    const footer = '<div class="sk-note">屏内纪律：所有数字均运行时现算、出处指向 events.jsonl 或 '
      + "装载层对象；取不到数时显示降级提示而不显示编造值；口径与公式见各数字卡下钻。"
      + "会签留痕双轨并列：order_review 平台治理工单（按岗位可见性裁决）与 events.jsonl 事件流回放；"
      + "演示身份只读，写操作在治理窗口执行。</div>";

    return '<div id="s01-root">' + head
      + (supportWarn ? '<div class="sk-block">' + supportWarn + "</div>" : "")
      + block1 + block2 + block3 + block4 + blockLive + block5 + block6 + block7
      + '<div class="sk-block" id="s01-agent" data-channel="faq"></div>' + footer
      + "</div>";
  }

  /* ---------- T19：接单制度问答（制度库直答，轻形态，不编造答案） ---------- */
  function hydrateAgentBlock(box, cfg) {
    const kit = K();
    const el = box ? box.querySelector("#s01-agent") : null;
    const a = (cfg && cfg.agent_layer) || null;
    if (!el || !a || !kit) return;
    kit.replay(a.replay).then(function (res) {
      if (!el.parentNode) return;
      if (!res || !res.ok || !res.package) {
        el.innerHTML = '<div class="sk-degrade" data-sk-s01-agent-degrade="1">'
          + kit.esc(a.degrade || "制度问答回放未就绪，不展示编造答案") + "</div>";
        return;
      }
      const pkg = res.package;
      el.innerHTML = '<div class="section-title">' + kit.esc(a.title)
        + '<span class="section-extra">' + kit.esc(a.source || "") + "</span></div>"
        + kit.agentPanel({
          title: pkg.title, agents: pkg.agents, steps: pkg.steps,
          conclusion: pkg.conclusion, source: "replay",
        })
        + '<div class="sk-note">来源徽标「演示回放」不可去除；答案取自制度库原文'
        + "（命中即直答），未命中时照实说「未收录该问题」。</div>";
    });
  }

  /* ============================================================
   * 下钻分派
   * ============================================================ */

  function openDrillByKey(key) {
    const kit = K();
    const maker = drills[key];
    if (typeof maker === "function") {
      kit.openDrill(maker());
      return;
    }
    kit.openDrill({
      title: "口径说明",
      subtitle: "该数字卡未登记口径",
      html: kv([["键", String(key)],
        ["处理", "未登记的口径键：请检查本屏 drills 字典（T07 交付物）"]]),
    });
  }

  /* ============================================================
   * 渲染入口
   * ============================================================ */

  function render(root, ctx) {
    const kit = K();
    if (!root) return;
    if (!kit) {
      root.innerHTML = '<div class="content-inner"><div class="empty">'
        + "组件契约未加载：scene-kit.js 必须在场景脚本之前引入（window.SceneKit 缺失）"
        + "</div></div>";
      return;
    }
    kit.use(ctx);
    CFG = sceneData();
    drills = {};

    root.innerHTML = '<div class="content-inner">'
      + kit.pageHead({
        icon: CFG.icon,
        title: CFG.title,
        desc: "正在读取演示装载层（" + String((CFG.objects || []).length) + " 个故事对象）…",
      })
      + '<div class="sk-block"><div class="empty">' + kit.spinner("lg") + "</div></div>"
      + "</div>";

    const box = root.querySelector("#s01-root") || root.querySelector(".content-inner");

    // 装载层与治理工单并行取（loadWorkOrder 永不 reject，故 Promise.all 不会被它拉挂）
    Promise.all([loadAll(), loadWorkOrder(ctx)]).then(function (both) {
      const ds = both[0];
      const wo = both[1];
      // 屏已切走就不写 DOM，避免写进已卸载的节点
      if (!box || !box.parentNode) return;
      const essential = ["sales_orders", "quotes", "customers", "events"];
      const missingEssential = essential.filter(function (name) {
        return !okOf(ds, name);
      });
      const readonly = kit.isReadonly(ctx);

      if (missingEssential.length) {
        box.innerHTML = '<div id="s01-root">'
          + kit.pageHead({
            icon: CFG.icon, title: CFG.title,
            desc: "接单评审初评：所需故事对象未就绪，本屏不显示编造数字。",
            extra: kit.badge("演示装载层未就绪", "badge-amber"),
          })
          + '<div class="sk-block">' + degradeHtml(ds, missingEssential)
          + note("恢复条件：GET /api/v2/demo/objects/{name} 可用；"
            + "对象名见本屏口径字典 objects（T04 装载层）。") + "</div></div>";
        return;
      }

      const derived = derive(ds);
      box.innerHTML = buildHtml(derived, ctx, readonly, wo);
      kit.bindDrills(box, openDrillByKey);
      hydrateAgentBlock(box, CFG);
    }).catch(function (err) {
      if (!box || !box.parentNode) return;
      box.innerHTML = '<div id="s01-root">' + degradeHtml({}, ["events"])
        + note("取数过程异常已被场景屏兜住：" + String((err && err.message) || err)) + "</div>";
    });
  }

  /* ============================================================
   * 登记（不碰 app.js）
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s01] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: INLINE_SCENE_DATA.label,
    title: INLINE_SCENE_DATA.title,
    sub: INLINE_SCENE_DATA.sub,
    icon: INLINE_SCENE_DATA.icon,
    group: window.SceneRegistry.GROUP_NAME,
    order: INLINE_SCENE_DATA.order,
    roles: window.SceneRegistry.BUSINESS_ROLES,
    render: render,
  });
})();
