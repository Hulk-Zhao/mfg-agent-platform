/**
 * S00 · 契约自检屏（T05 交付物）
 *
 * 作用：把「屏契约 / 数据契约 / 组件契约」和「五访客角色」串成一条可验收的链路，
 * 给 T06–T11 十二个场景屏当施工样板。它**不承载业务结论**：
 *  - 自检数字卡（契约对象数 / 已登记场景数 / 访客角色数）全部运行时实算，可核对；
 *  - 订单装载区走 `SceneKit.story('sales_orders')`，装载层没交付时显示明确降级条，
 *    绝不显示编造数字；
 *  - 事件流与会签卡明确标注「示意结构，非业务数据」。
 */
(function () {
  "use strict";

  const SCENE_ID = "s00";

  /**
   * 数据契约登记的故事对象（与 `data/demo_storyline/frontend/registry.json`
   * 的 `objects` 键严格一一对应，与 `data/demo_storyline/*.jsonl` 文件名去扩展名一致）。
   *
   * 为什么在这里镜像一份：浏览器只能访问 `/demo-ui` 静态目录，
   * 读不到仓库内的 registry.json。这份镜像不靠人肉维护——
   * `tests/demo/test_t05_contract.py` 会逐条比对 registry.json 的 objects 键与磁盘
   * 上的 jsonl 文件，任一处漂移都会让测试变红。
   */
  const STORY_OBJECTS = [
    "agent_replays", "boms", "cases", "customers", "equipment", "events", "faqs", "inspections",
    "lots", "materials", "purchase_orders", "quotes", "rag_replays", "sales_orders",
    "spc_series", "suppliers", "work_orders",
  ];

  /** 当前屏的口径字典（键 = 数字卡的 data-sk-drill，值 = 返回弹层参数）。 */
  let drills = {};

  /* ============================================================
   * 小工具
   * ============================================================ */

  /** 把 [[键, 值], ...] 渲染成口径表。值走 esc（可能是外部数据）。 */
  function kv(pairs) {
    const K = window.SceneKit;
    const rows = (pairs || []).map(function (pair) {
      const k = pair[0];
      const v = pair[1];
      return '<div class="sk-kv-row"><span class="sk-kv-k">' + K.esc(k)
        + '</span><span class="sk-kv-v">' + K.esc(v) + "</span></div>";
    }).join("");
    return '<div class="sk-kv">' + rows + "</div>";
  }

  function readonlyBadge(readonly) {
    const K = window.SceneKit;
    return readonly
      ? K.badge("只读访客 · 不发起写操作", "badge-gray")
      : K.badge("可写岗位 · 本屏仍为骨架只读", "badge-blue");
  }

  /* ============================================================
   * 订单装载（真实装载层数据 / 明确降级）
   *
   * 口径纪律：这一区展示的是 **sales_orders 全量**（主线 DEMO-SO-001 + 背景在途单），
   * 不是「主线订单」。早期标题写「主线订单」却又列出 3 行 / 7,400 片，
   * 与主线 5,000 片字面矛盾——评审会当场抓住。所以：
   *   1. 小标题一律叫「订单装载」，并标出总行数构成；
   *   2. 任何"主线"数字都按 order_id === MAIN_ORDER_ID 定位后**从 rows 算**，
   *      找不到就退回首行并在 hint 里标注「首行」，绝不硬编码 5000 / 7400。
   * ============================================================ */

  /** 主线订单号（spec-v1.md §1 / §3：主线 SO-DEMO-001，数据包内为 DEMO-SO-001）。 */
  const MAIN_ORDER_ID = "DEMO-SO-001";

  function sumQty(rows) {
    let total = 0;
    (rows || []).forEach(function (row) {
      const lines = row && Array.isArray(row.lines) ? row.lines : [];
      lines.forEach(function (line) {
        const qty = Number(line && line.qty);
        if (Number.isFinite(qty)) total += qty;
      });
    });
    return total;
  }

  function degradeHtml(res) {
    const K = window.SceneKit;
    const reason = (res && res.error) ? String(res.error) : "演示装载层未就绪";
    const note = (res && res.note) ? String(res.note) : "接口未就绪";
    return '<div class="section-title">订单装载<span class="section-extra">sales_orders</span></div>'
      + '<div class="sk-degrade">' + K.icon("warn", 15)
      + "<div><b>演示装载层未就绪（T04 接口不可用）：本屏不显示编造数字。</b>"
      + '<div class="sk-degrade-sub">当前状态：' + K.esc(reason) + "；" + K.esc(note)
      + "。恢复条件：GET /api/v2/demo/objects/sales_orders 可用，"
      + "或 window.DEMO_STORY.sales_orders 就绪。</div>"
      + "</div></div>";
  }

  function storyHtml(res) {
    const K = window.SceneKit;
    const rows = (res && Array.isArray(res.rows)) ? res.rows : [];
    const first = rows[0] || {};
    const fromApi = res && res.source === "api";
    const sourceText = fromApi
      ? "来源：GET /api/v2/demo/objects/sales_orders"
      : "来源：window.DEMO_STORY 本地回落（接口不可用）";

    // 主线行按 order_id 定位（不假定它一定在首行），定位不到才退回首行。
    const mainRows = rows.filter(function (row) {
      return row && String(row.order_id) === MAIN_ORDER_ID;
    });
    const mainRow = mainRows[0] || null;
    const otherCount = rows.length - mainRows.length;
    const useMain = mainRow !== null;
    const promiseRow = useMain ? mainRow : first;
    const promise = promiseRow.promise_date ? K.fmtDate(promiseRow.promise_date) : "—";
    const totalQty = sumQty(rows);
    const mainQty = sumQty(mainRows);
    const promiseSourceText = useMain ? MAIN_ORDER_ID + " 主线行" : "首行（未匹配到主线单）";

    // 口径随卡片走：点哪张卡，看到的就是这张卡怎么来的。
    drills["story-rows"] = function () {
      return {
        title: "订单行数",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", String(rows.length) + " 行"],
          ["口径", "sales_orders 对象返回的行数（1 行 = 1 张销售订单，含主线与背景在途单）"],
          ["构成", "主线订单（" + MAIN_ORDER_ID + "）" + (useMain ? "1 行" : "未匹配")
            + "；其余行数 " + otherCount + " 行"],
          ["来源", fromApi ? "GET /api/v2/demo/objects/sales_orders" : "window.DEMO_STORY.sales_orders"],
          ["主键", "order_id（registry.json → objects.sales_orders.key）"],
          ["首行订单", first.order_id ? String(first.order_id) : "—"],
          ["契约", "data/demo_storyline/frontend/registry.json → objects.sales_orders"],
        ]),
      };
    };
    drills["story-qty"] = function () {
      return {
        title: "订购总量",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", K.fmtNum(totalQty) + " 片"],
          ["算法", "Σ sales_orders[].lines[].qty（逐行逐行项累加，spec-v1.md §2.4）"],
          ["覆盖范围", "全量 " + rows.length + " 张订单（含主线与背景在途单）"],
          ["主线分量", useMain
            ? MAIN_ORDER_ID + " = " + K.fmtNum(mainQty) + " 片（单独按 order_id 求和，不与其他单混算）"
            : "未匹配到 " + MAIN_ORDER_ID + "，不单列"],
          ["来源", fromApi ? "GET /api/v2/demo/objects/sales_orders" : "window.DEMO_STORY.sales_orders"],
          ["备注", "只累加可解析为数字的 qty；缺字段直接跳过，不做估算"],
        ]),
      };
    };
    drills["story-promise"] = function () {
      return {
        title: "主线承诺交期",
        subtitle: "口径 · 来源 · 算法",
        html: kv([
          ["取值", promise],
          ["定位方式", "按 order_id === " + MAIN_ORDER_ID + " 取主线行"
            + (useMain ? "" : "（未匹配到，已回退首行并在卡面标注「首行」）")],
          ["口径", "sales_orders.promise_date（对客承诺交期，spec-v1.md §2.4）"],
          ["算法", "只取主线行的 promise_date，不做跨订单聚合（其他在途单的交期不参与）"],
          ["来源", fromApi ? "GET /api/v2/demo/objects/sales_orders" : "window.DEMO_STORY.sales_orders"],
          ["OTD 判据", "actual_ship_date ≤ promise_date（spec-v1.md §5）"],
        ]),
      };
    };

    const qtyHint = "口径：Σ lines[].qty，全量 " + rows.length + " 张订单"
      + (useMain ? "；主线 " + MAIN_ORDER_ID + " 为 " + K.fmtNum(mainQty) + " 片" : "");
    const cards = [
      {
        label: "订单行数", value: rows.length, unit: "行",
        hint: "口径：objects 接口返回行数（含主线与背景单），主键 order_id",
        tone: "blue", drill: "story-rows",
      },
      {
        label: "订购总量", value: K.fmtNum(totalQty), unit: "片",
        hint: qtyHint, tone: "green", drill: "story-qty",
      },
      {
        label: "主线承诺交期", value: promise, unit: "",
        hint: "口径：promise_date（" + promiseSourceText + "）",
        tone: "amber", drill: "story-promise",
      },
    ];

    const degradedNote = res && res.degraded
      ? '<div class="sk-degrade" style="margin-top:12px">' + K.icon("warn", 15)
      + "<div><b>当前取自本地回放数据（接口不可用）。</b>"
      + '<div class="sk-degrade-sub">' + K.esc(res.note || "演示装载层接口未就绪") + "</div></div></div>"
      : "";

    const scopeText = sourceText + " · sales_orders 全量 " + rows.length
      + " 行（含主线 + 背景在途单）";
    return '<div class="section-title">订单装载<span class="section-extra">' + K.esc(scopeText)
      + "</span></div>" + K.numberRow(cards) + degradedNote;
  }

  function renderStory(box) {
    const K = window.SceneKit;
    if (!box || !K) return;
    box.innerHTML = '<div class="section-title">订单装载<span class="section-extra">正在读取…</span></div>'
      + '<div class="empty">' + K.spinner("lg") + "</div>";

    K.story("sales_orders").then(function (res) {
      // 屏已切走（节点脱离文档）就不再写 DOM，避免"写进已卸载的节点"。
      if (!box.parentNode) return;
      const ok = res && res.ok === true && Array.isArray(res.rows) && res.rows.length > 0;
      box.innerHTML = ok ? storyHtml(res) : degradeHtml(res);
      K.bindDrills(box, openDrillByKey);
    }).catch(function () {
      if (!box.parentNode) return;
      box.innerHTML = degradeHtml({ error: "演示装载层未就绪", note: "取数过程异常已被组件层兜住" });
    });
  }

  /* ============================================================
   * 下钻
   * ============================================================ */

  function openDrillByKey(key) {
    const K = window.SceneKit;
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

    const readonly = K.isReadonly(ctx);
    const registry = window.SceneRegistry;
    const scenes = (registry && typeof registry.list === "function") ? registry.list() : [];
    const demo = window.DEMO || {};
    const roles = Array.isArray(demo.ROLES) ? demo.ROLES : [];
    const visitors = roles.filter(function (role) { return role && role.readonly === true; });

    // ---- 口径字典（每张数字卡都能点开看到"这个数怎么来的"）----
    drills = {};
    drills["contract-objects"] = function () {
      return {
        title: "数据契约对象条数",
        subtitle: "口径 · 来源 · 校验方式",
        html: kv([
          ["取值", String(STORY_OBJECTS.length) + " 个对象"],
          ["口径", "场景数据契约中登记的故事对象个数"],
          ["来源", "data/demo_storyline/frontend/registry.json → objects 键"],
          ["校验", "tests/demo/test_t05_contract.py 逐条比对 objects 键与 data/demo_storyline/*.jsonl 文件名"],
          ["本屏取值", "常量镜像 STORY_OBJECTS 的长度（浏览器读不到仓库内 JSON）"],
          ["对象清单", STORY_OBJECTS.join("、")],
        ]),
      };
    };
    drills["registered-scenes"] = function () {
      const ids = scenes.map(function (def) { return def.id; });
      return {
        title: "已登记场景数",
        subtitle: "口径 · 来源 · 校验方式",
        html: kv([
          ["取值", String(scenes.length) + " 个场景屏"],
          ["口径", "SceneRegistry.list() 运行时已登记的场景屏个数"],
          ["来源", "window.SceneRegistry（src/.../ui/scenes/registry.js）"],
          ["已登记 id", ids.length ? ids.join("、") : "（无）"],
          ["增量规则", "T06–T11 各窗口新建 scenes/sNN-*.js 并调用 register()，本数随之递增"],
        ]),
      };
    };
    drills["visitor-roles"] = function () {
      return {
        title: "访客角色数",
        subtitle: "口径 · 来源 · 校验方式",
        html: kv([
          ["取值", String(visitors.length) + " 个只读访客"],
          ["口径", "DEMO.ROLES 中 readonly === true 的角色个数"],
          ["来源", "window.DEMO.ROLES（由 registry.js 的 registerVisitorRoles() 注入）"],
          ["角色 id", visitors.length ? visitors.map(function (r) { return r.id; }).join("、") : "（无）"],
          ["映射", "每个访客的 roles 必须落在 32 角色治理目录：gm / sales / rd_engineering / qa / manufacturing"],
          ["原因", "后端 /api/v2/auth/login 只认治理角色 key，不认前端 id（如 gm-demo）"],
        ]),
      };
    };

    // ---- 事件流骨架示例（明确标注非业务数据）----
    const demoEvents = [
      { ts: "2026-10-19T09:00:00", type: "order", text: "骨架示例：接单评审事件位（字段 ts / type / text / scene）", scene: "S1" },
      { ts: "2026-10-19T10:30:00", type: "report", text: "骨架示例：报告产出事件位（真实报告由 T04 装载层提供）", scene: "S2" },
      { ts: "2026-10-19T14:10:00", type: "inspection", text: "骨架示例：检验批事件位（检验批对象见 registry.json → inspections）", scene: "S4" },
      { ts: "2026-10-19T16:40:00", type: "hitl", text: "骨架示例：会签节点事件位（槽位口径见 spec-v1.md §5）", scene: "S5" },
    ];

    const signoffSlots = [
      { role: "申请人", user: "示意用户", status: "approved", ts: "2026-10-19T09:05:00" },
      { role: "技术", user: "示意用户", status: "pending", ts: "" },
      { role: "计划", user: "示意用户", status: "pending", ts: "" },
    ];

    const cards = [
      {
        label: "数据契约对象", value: STORY_OBJECTS.length, unit: "个",
        hint: "registry.json → objects 键数量", tone: "blue", drill: "contract-objects",
      },
      {
        label: "已登记场景", value: scenes.length, unit: "个",
        hint: "SceneRegistry.list() 运行时实算", tone: "green", drill: "registered-scenes",
      },
      {
        label: "访客角色", value: visitors.length, unit: "个",
        hint: "DEMO.ROLES 中 readonly === true", tone: "purple", drill: "visitor-roles",
      },
    ];

    const html = '<div id="s00-root">'
      + K.pageHead({
        icon: "layers",
        title: "契约自检屏",
        desc: "契约自检屏：验证屏 / 数据 / 组件三契约与五访客角色。"
          + "本屏为 T05 验收样本，T06–T11 场景屏按同一份契约施工。",
        extra: K.badge("T05 契约样本", "badge-blue")
          + K.badge("骨架屏不含业务结论", "badge-gray")
          + readonlyBadge(readonly),
      })
      + '<div class="sk-block">'
      + '<div class="section-title">契约自检（运行时实算，可逐项核对）'
      + '<span class="section-extra">点击任一数字卡查看口径与来源</span></div>'
      + K.numberRow(cards)
      + "</div>"
      + '<div class="sk-block" data-sk-slot="story"></div>'
      + '<div class="sk-block">'
      + K.eventFlow(demoEvents, {
        title: "事件流骨架示例",
        extra: "示意结构，非业务数据",
        note: "示意结构，非业务数据：字段口径取自 spec-v1.md §2.10（ts / type / text / scene）；"
          + "真实事件由 T04 装载层的 events 对象提供。",
      })
      + "</div>"
      + '<div class="sk-block">'
      + K.signoffCard({
        title: "会签卡骨架示例（三槽位：申请人 / 技术 / 计划）",
        slots: signoffSlots,
        readonly: readonly,
        note: "示意结构，非业务数据：只验证槽位渲染、状态标签与只读模式；"
          + "真实会签写接口由场景屏接入（HITL 槽位口径见 spec-v1.md §5）。",
      })
      + "</div>"
      + '<div class="sk-note">本屏只承载契约验收：数字均可回溯（运行时实算或 loader 接口），'
      + "拿不到数据时显示降级提示，不使用编造数字。</div>"
      + "</div>";

    root.innerHTML = '<div class="content-inner">' + html + "</div>";

    const content = root.querySelector("#s00-root");
    if (content) K.bindDrills(content, openDrillByKey);
    const storyBox = root.querySelector('[data-sk-slot="story"]');
    renderStory(storyBox);
  }

  /* ============================================================
   * 登记
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s00] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: "契约自检屏",
    title: "契约自检屏",
    sub: "屏契约 / 数据契约 / 组件契约 · 三契约验收样本",
    icon: "layers",
    group: window.SceneRegistry.GROUP_NAME,
    order: 0,
    roles: window.SceneRegistry.BUSINESS_ROLES,
    render: render,
  });
})();
