/**
 * S5 场景屏 · 出货与质量追溯（T06 交付物）
 *
 * 交付物清单（对应计划书 T06 三件）：
 *  1. 本文件 `src/mfg_agents/v2/demo/ui/scenes/s05-trace.js`：自注册到
 *     `window.SceneRegistry`（**不改 app.js / 总导航**），渲染 S5 屏；
 *  2. `data/demo_storyline/frontend/s05-trace.js`：场景派生数据单一事实源
 *     （本文件内 `INLINE_SCENE_DATA` 为其同构镜像，`tests/demo/test_s05.py` 深比对）；
 *  3. `tests/demo/test_s05.py`：登记契约 + 镜像不漂移 + 图谱坐标 + 取数纪律用例。
 *
 * 承载主线（spec-v1.md §3 主线 #13–#15、§4 支线 B）：
 *  - 出货闭环：OQC 放行 → 发货确认（EV-0021 / EV-0022）→ 开票（EV-0023）；
 *  - 客诉入口（EV-0024 受理）→ 批次定位（EV-0025：批次 / 工单 / 工序 / 设备 / 当班 /
 *    同批库存）→ 8D 会签闭环（EV-0026）。
 *
 * 三条纪律（与 S3/S4 屏一致，跨窗口可对照）：
 *  1. **公共数字全部现取**：经 `SceneKit.story(objectName)`（→ T04 装载层
 *     `GET /api/v2/demo/objects/{name}`）读取，本文件**不写死任何业务数字**；
 *  2. **每张数字卡可下钻**：口径 + 数据来源 + 关联原始记录 + 处理链路，四段齐备，
 *     满足「所有数字可点开处理链路」的验收项；
 *  3. **降级不编数**：装载层未就绪时显示诚实提示，绝不显示编造数字。
 *
 * 只读访客（五访客 readonly=true）：会签位显示「只读演示」；「开始批次定位」是纯前端
 * 视图动作（不产生写请求），对访客放开，便于验收「≤5 次点击完成定位」。
 */
(function () {
  "use strict";

  const SCENE_ID = "s05";
  const STYLE_ID = "s05-scene-style";

  /** 本屏取数的故事对象（核心三件缺失即整屏降级；其余缺失就地静默降级）。 */
  const STORY_OBJECTS = ["events", "lots", "work_orders", "equipment", "inspections",
    "sales_orders", "customers"];
  const CORE_OBJECTS = ["events", "lots", "work_orders"];

  const CSS = [
    /* SVG 追溯图谱：类不生效时仍有 inline style 兜底，避免渲染成黑块 */
    "#s05-graph { display: block; width: 100%; height: auto; }",
    ".s05-node { cursor: pointer; }",
    ".s05-node-rect { fill: var(--card); stroke: var(--border-strong); stroke-width: 1.4;"
      + " transition: stroke-width .2s ease, stroke .2s ease; }",
    ".s05-node:hover .s05-node-rect { stroke-width: 2.2; }",
    ".s05-graph-wrap.is-located .s05-node-rect { stroke-width: 2.4; }",
    ".s05-node-label { font-size: 12px; fill: var(--text-3); }",
    ".s05-node-value { font-size: 13.5px; font-weight: 700; fill: var(--text); }",
    ".s05-node-hint { font-size: 10.5px; fill: var(--text-3); }",
    ".s05-t-red .s05-node-rect { stroke: var(--danger); }",
    ".s05-t-blue .s05-node-rect { stroke: var(--primary); }",
    ".s05-t-purple .s05-node-rect { stroke: var(--purple); }",
    ".s05-t-green .s05-node-rect { stroke: var(--ok); }",
    ".s05-t-gray .s05-node-rect { stroke: var(--border-strong); }",
    ".s05-edge { stroke: var(--border-strong); stroke-width: 1.6; fill: none; }",
    ".s05-edge-label { font-size: 11px; fill: var(--text-3); }",
    ".s05-arrow { fill: var(--border-strong); }",
    ".s05-graph-wrap { overflow-x: auto; padding-bottom: 4px; }",
    /* 定位动作行 */
    ".s05-locate-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 10px 0 2px; }",
    ".s05-locate-state { font-size: 12px; color: var(--text-3); }",
    ".s05-locate-state.is-done { color: var(--ok); font-weight: 600; }",
    /* 追溯链步骤 */
    ".s05-steps { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }",
    ".s05-step { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-2);"
      + " background: var(--primary-light); border: 1px solid var(--border); border-radius: 999px;"
      + " padding: 3px 10px 3px 6px; }",
    ".s05-step-no { width: 17px; height: 17px; border-radius: 50%; background: var(--primary); color: #fff;"
      + " font-size: 10.5px; font-weight: 700; display: inline-flex; align-items: center;"
      + " justify-content: center; flex: none; }",
    /* 明细小表 */
    ".s05-list { margin-top: 8px; border-top: 1px solid var(--border); }",
    ".s05-list-row { display: flex; gap: 10px; padding: 7px 2px; border-bottom: 1px solid var(--border);"
      + " font-size: 12.4px; align-items: flex-start; }",
    ".s05-list-k { flex: none; color: var(--text-3); width: 112px; }",
    ".s05-list-v { flex: 1; color: var(--text); word-break: break-word; }",
    ".s05-list-row.is-clickable { cursor: pointer; }",
    ".s05-list-row.is-clickable:hover .s05-list-v { color: var(--primary); }",
    /* 点击路径 */
    ".s05-path { margin: 4px 0 0 0; padding-left: 20px; }",
    ".s05-path li { font-size: 12.6px; color: var(--text-2); line-height: 1.9; }",
    ".s05-path li b { color: var(--text); }",
    ".s05-degrade-note { font-size: 11.5px; color: var(--text-3); margin-top: 6px; }",
    /* T18 智能体层：取证书签 / 案例卡 / 检索轨迹 / 治理卡 */
    ".s05-badges { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }",
    ".s05-badges-title { flex-basis: 100%; font-size: 12.4px; color: var(--text-2); font-weight: 600; }",
    ".s05-badges-extra { margin-left: 8px; font-weight: 400; color: var(--text-3); }",
    ".s05-badge { display: inline-flex; flex-direction: column; gap: 2px; text-align: left;"
      + " border: 1px dashed var(--border-strong); background: var(--card); border-radius: 8px;"
      + " padding: 6px 10px; cursor: pointer; font-size: 12px; color: var(--text); }",
    ".s05-badge:hover { border-color: var(--primary); color: var(--primary); }",
    ".s05-badge-label { font-weight: 600; }",
    ".s05-badge-agent { color: var(--text-2); font-size: 11.4px; }",
    ".s05-case-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }",
    ".s05-case { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: var(--card); }",
    ".s05-case-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }",
    ".s05-case-id { font-size: 11.6px; color: var(--text-3); }",
    ".s05-case-title { font-weight: 600; font-size: 13.2px; color: var(--text); }",
    ".s05-case-score { margin-left: auto; font-size: 11.6px; color: var(--text-2); }",
    ".s05-case-meta { margin-top: 4px; font-size: 12.2px; color: var(--text-2); }",
    ".s05-case-reason { margin-top: 3px; font-size: 12px; color: var(--text-3); }",
    ".s05-trace-title { margin-top: 12px; font-size: 12.4px; font-weight: 600; color: var(--text-2); }",
    ".s05-trace-steps { margin: 6px 0 0 0; padding-left: 20px; }",
    ".s05-trace-steps li { font-size: 12.4px; color: var(--text-2); line-height: 1.9; }",
    ".s05-trace-steps li b { color: var(--text); }",
    ".s05-explain { margin-top: 10px; font-size: 12.4px; color: var(--text-2); }",
    ".s05-explain summary { cursor: pointer; color: var(--text); font-weight: 600; }",
    ".s05-explain ul { margin: 6px 0 0 0; padding-left: 20px; }",
    ".s05-explain li { line-height: 1.9; }",
    ".s05-hitl { margin-top: 8px; }",
    ".s05-hitl-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }",
    ".s05-hitl-slot { font-size: 11.6px; color: var(--primary); background: var(--primary-light);"
      + " border-radius: 999px; padding: 1px 8px; }",
    ".s05-hitl-vis { margin-left: auto; font-size: 11.8px; color: var(--text-2); }",
    ".s05-hitl-meta { margin-top: 5px; font-size: 12.2px; color: var(--text-2); }",
    ".s05-hitl-ro { margin-top: 5px; font-size: 11.8px; color: var(--text-3); }",
    ".s05-d8-node text { pointer-events: none; }",
  ].join("\n");

  /**
   * 场景派生数据镜像常量——与 `data/demo_storyline/frontend/s05-trace.js` 的
   * `window.SCENE_DATA["s05"]` **逐值相等**（`tests/demo/test_s05.py` 深比对）。
   * 之所以镜像一份：本轮各场景屏统一从 `window.SCENE_DATA` 读取（契约文件已挂到
   * `/demo-story`，但屏内镜像仍是兜底与版本锚，与 T09/T11 处理一致）。
   */
  const INLINE_SCENE_DATA = {
    "scene_id": "s05",
    "spec_ref": "data/demo_storyline/spec-v1.md §3（主线 #13–#15）· §4 支线 B（客诉 8D）· §5 界面数字台账",
    "value_owner": "公共数字全部经 SceneKit.story(objectName) 从 T04 装载层读取；本文件只提供布局与口径文案",
    "canvas": {
      "w": 1040,
      "h": 320
    },
    "nodes": [
      {
        "key": "complaint",
        "label": "客诉单",
        "x": 20,
        "y": 36,
        "w": 176,
        "h": 64,
        "tone": "red",
        "icon": "alert",
        "value_from": "S5 事件 type=complaint（受理）→ payload.symptom / qty",
        "record": "events（S5 · complaint）"
      },
      {
        "key": "lot",
        "label": "批次",
        "x": 224,
        "y": 36,
        "w": 176,
        "h": 64,
        "tone": "blue",
        "icon": "box",
        "value_from": "events（stage=located）→ payload.trace.lot",
        "record": "lots"
      },
      {
        "key": "wo",
        "label": "工单",
        "x": 428,
        "y": 36,
        "w": 176,
        "h": 64,
        "tone": "blue",
        "icon": "orders",
        "value_from": "events（stage=located）→ payload.trace.wo",
        "record": "work_orders"
      },
      {
        "key": "op",
        "label": "工序",
        "x": 632,
        "y": 36,
        "w": 176,
        "h": 64,
        "tone": "blue",
        "icon": "activity",
        "value_from": "events（stage=located）→ payload.trace.op → work_orders[].ops[].op_no",
        "record": "work_orders.ops"
      },
      {
        "key": "equipment",
        "label": "设备",
        "x": 836,
        "y": 36,
        "w": 176,
        "h": 64,
        "tone": "purple",
        "icon": "cpu",
        "value_from": "events（stage=located）→ payload.trace.equipment",
        "record": "equipment"
      },
      {
        "key": "shift",
        "label": "当班",
        "x": 632,
        "y": 224,
        "w": 176,
        "h": 64,
        "tone": "gray",
        "icon": "clock",
        "value_from": "events（stage=located）→ payload.trace.shift",
        "record": "events（payload.trace.shift）"
      },
      {
        "key": "stock",
        "label": "同批库存",
        "x": 224,
        "y": 224,
        "w": 176,
        "h": 64,
        "tone": "green",
        "icon": "database",
        "value_from": "events（stage=located）→ payload.trace.stock_lots → lots（同库位）",
        "record": "lots"
      }
    ],
    "edges": [
      {
        "from": "complaint",
        "to": "lot",
        "label": "关联"
      },
      {
        "from": "lot",
        "to": "wo",
        "label": "同批产出"
      },
      {
        "from": "wo",
        "to": "op",
        "label": "执行"
      },
      {
        "from": "op",
        "to": "equipment",
        "label": "使用"
      },
      {
        "from": "op",
        "to": "shift",
        "label": "当班"
      },
      {
        "from": "lot",
        "to": "stock",
        "label": "位置"
      }
    ],
    "trace_steps": [
      {
        "no": 1,
        "key": "complaint",
        "title": "客诉受理",
        "detail": "记录症状与影响数，作为追溯入口"
      },
      {
        "no": 2,
        "key": "lot",
        "title": "批次定位",
        "detail": "按客诉关联批次命中生产批次"
      },
      {
        "no": 3,
        "key": "wo",
        "title": "工单回溯",
        "detail": "批次 → 生产工单"
      },
      {
        "no": 4,
        "key": "op",
        "title": "工序回溯",
        "detail": "工单 → 执行工序（op_no）"
      },
      {
        "no": 5,
        "key": "equipment",
        "title": "设备回溯",
        "detail": "工序 → 所用设备"
      },
      {
        "no": 6,
        "key": "shift",
        "title": "班次回溯",
        "detail": "工序 → 当班班次"
      },
      {
        "no": 7,
        "key": "stock",
        "title": "库存位置",
        "detail": "同批库存位置与数量（影响面）"
      }
    ],
    "locate_sla": {
      "minutes": 15,
      "label": "15 分钟内定位",
      "source": "spec-v1.md §4 支线 B · 事件 payload note（演示口径 15 分钟内）"
    },
    "click_path": {
      "title": "点击路径（从客诉单到定位批次）",
      "limit": 5,
      "steps": [
        {
          "no": 1,
          "action": "点击「开始批次定位」（客诉单卡内）",
          "lands": "批次 / 工单 / 工序 / 设备 / 当班 五段同时点亮"
        },
        {
          "no": 2,
          "action": "点击任一已点亮节点或定位数字卡",
          "lands": "弹层：口径 + 来源 + 关联原始记录"
        },
        {
          "no": 3,
          "action": "点击「同批库存」节点",
          "lands": "库存位置与数量（lots 对象）"
        },
        {
          "no": 4,
          "action": "点击「OQC 放行」数字卡",
          "lands": "检验批原始记录（inspections）"
        },
        {
          "no": 5,
          "action": "点击「8D 会签」槽位",
          "lands": "会签留痕与槽位（events · hitl）"
        }
      ],
      "note": "定位动作本身只需 1 次点击（客诉单 → 定位批次）；本表列满 5 步即验收上限。"
    },
    "slot_labels": {
      "pqe": "制程质量工程师",
      "cs": "客户服务",
      "rd": "研发工程",
      "pe": "工艺工程",
      "plan": "计划",
      "rd_engineering": "研发工程",
      "manufacturing": "制造运营"
    },
    "eight_d": {
      "title": "8D 时间线（客诉闭环）",
      "steps": [
        "D1 团队",
        "D2 问题描述",
        "D3 临时对策",
        "D4 根因",
        "D5 纠正措施",
        "D6 实施",
        "D7 预防",
        "D8 结案"
      ],
      "refs": {
        "complaint_event": "DEMO-EV-0024",
        "located_event": "DEMO-EV-0025",
        "signoff_event": "DEMO-EV-0026",
        "complaint_id": "DEMO-CC-001",
        "report_id": "DEMO-8D-001"
      },
      "source": "spec-v1.md §4 支线 B · events.jsonl（S5 · complaint / hitl）"
    },
    "agent_layer": {
      "replays": {
        "agent": "s05_complaint_8d",
        "cases": "s05_same_root_cause_cases"
      },
      "panel_title": "客诉 8D 智能体协同（DEMO-CC-001）",
      "d8": {
        "canvas": {
          "w": 1040,
          "h": 128
        },
        "current_stage": "D8",
        "root_cause_case": "DEMO-CASE-006",
        "note": "D1–D8 由 qa_lead 规则模板确定性合成（spec-v1 §4 支线 B）；当前 D8 待 8D 关闭会签。",
        "steps": [
          {
            "key": "D1",
            "label": "成立小组",
            "agent": "quality",
            "x": 8,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D2",
            "label": "问题描述",
            "agent": "quality",
            "x": 137,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D3",
            "label": "临时对策",
            "agent": "quality",
            "x": 266,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D4",
            "label": "根因分析",
            "agent": "rd_engineering",
            "x": 395,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D5",
            "label": "纠正措施",
            "agent": "rd_engineering",
            "x": 524,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D6",
            "label": "实施验证",
            "agent": "manufacturing",
            "x": 653,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D7",
            "label": "预防措施",
            "agent": "quality",
            "x": 782,
            "y": 8,
            "w": 120,
            "h": 54
          },
          {
            "key": "D8",
            "label": "结案会签",
            "agent": "quality",
            "x": 911,
            "y": 8,
            "w": 120,
            "h": 54
          }
        ]
      },
      "graph_badges": [
        {
          "key": "complaint",
          "domain": "quality",
          "tool": "list_scenarios",
          "branch": ""
        },
        {
          "key": "lot",
          "domain": "quality",
          "tool": "run_registered_query",
          "branch": ""
        },
        {
          "key": "wo",
          "domain": "quality",
          "tool": "run_registered_query",
          "branch": ""
        },
        {
          "key": "op",
          "domain": "quality",
          "tool": "run_registered_query",
          "branch": "pqe_process"
        },
        {
          "key": "equipment",
          "domain": "rd_engineering",
          "tool": "run_registered_query",
          "branch": "engineering"
        },
        {
          "key": "shift",
          "domain": "quality",
          "tool": "run_registered_query",
          "branch": ""
        },
        {
          "key": "stock",
          "domain": "supply_chain",
          "tool": "run_registered_query",
          "branch": "business_sales"
        }
      ],
      "cases": {
        "title": "同根因历史案例（Agentic RAG 回放）",
        "trace_title": "检索轨迹（术语归一 → 本体扩展 → 授权过滤 → 引用校验）",
        "explain_title": "为什么看到这几条 / 为什么看不到"
      },
      "hitl": {
        "title": "8D 与对客回复 · 治理工单（只读）",
        "empty": "仅展示演示种子工单（当前无可见种子）",
        "invisible": "本岗位不参与该槽位审批（按权限矩阵不可见）",
        "readonly_note": "操作只读：真实会签在治理窗口（审批确认中心）执行"
      },
      "source_note": "回放轨迹与真实链路同构（AgenticStep 六键）；来源徽标「演示回放」不可去除。"
    },
    "value_lines": [
      "任一客诉 15 分钟内定位到批次、工序、设备和库存位置",
      "一键说清：定位结论附数据来源，可逐段回指原始记录",
      "8D 全程线上流转：槽位会签留痕，责任可追"
    ]
  };

  /* ============================================================
   * 一、样式注入（只注入一次；场景自有类一律 s05- 前缀）
   * ============================================================ */

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

  /** 场景派生数据：优先 window.SCENE_DATA（数据文件），缺位时登记内联镜像。 */
  function sceneData() {
    const bag = window.SCENE_DATA;
    if (bag && bag[SCENE_ID] && typeof bag[SCENE_ID] === "object") return bag[SCENE_ID];
    if (!bag || typeof bag !== "object") window.SCENE_DATA = {};
    if (!window.SCENE_DATA[SCENE_ID]) window.SCENE_DATA[SCENE_ID] = INLINE_SCENE_DATA;
    return window.SCENE_DATA[SCENE_ID];
  }

  function hasRows(list) {
    return Array.isArray(list) && list.length > 0;
  }

  function findById(list, key, id) {
    if (!hasRows(list) || id === null || id === undefined || id === "") return null;
    for (let i = 0; i < list.length; i += 1) {
      const row = list[i];
      if (row && String(row[key]) === String(id)) return row;
    }
    return null;
  }

  /** 口径/记录键值表（值一律走 esc）。 */
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

  /** 一行小字说明（诚实降级，不用英文异常文案）。 */
  function smallNote(text) {
    return '<div class="s05-degrade-note">' + kit().esc(text) + "</div>";
  }

  function num(value, digits) {
    return kit().fmtNum(value, typeof digits === "number" ? digits : 0);
  }

  /** 来源标注（T04 装载层端点 / 本地离线回放）。 */
  function sourceLabel(name) {
    return lastSource[name] === "local"
      ? "window.DEMO_STORY（本地离线回放）"
      : "GET /api/v2/demo/objects/" + name;
  }

  /* ============================================================
   * 三、取数（唯一入口 SceneKit.story，永不 reject）
   * ============================================================ */

  let lastSource = {};

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
   * @returns {Promise<{ok: boolean, rows: Object, source: Object, missing: string[]}>}
   *   `ok` 只要求核心三件（events / lots / work_orders）到位；其余对象缺失时
   *   就地静默降级（如「客户质量概况卡」留空并给出说明），不影响整屏。
   */
  function loadAll() {
    return Promise.all(STORY_OBJECTS.map(function (name) {
      return loadStory(name);
    })).then(function (results) {
      const rows = {};
      const source = {};
      const missing = [];
      STORY_OBJECTS.forEach(function (name, i) {
        const hit = results[i];
        rows[name] = hit ? hit.rows : [];
        source[name] = hit ? hit.source : "none";
        if (!hit) missing.push(name);
      });
      const coreMissing = CORE_OBJECTS.filter(function (name) {
        return missing.indexOf(name) !== -1;
      });
      return { ok: coreMissing.length === 0, rows: rows, source: source, missing: missing };
    });
  }

  /* ============================================================
   * 四、业务派生（一律从 D.rows 计算，不写死任何业务数字）
   * ============================================================ */

  const WO_STATUS = { done: "已完工", in_progress: "在制中", released: "已下达" };
  const SHIP_STATUS = { shipped: "已发货", invoiced: "已开票", open: "待发货" };
  const OQC_RESULT = { pass: "合格放行", concession: "让步接收", fail: "不合格" };

  function s5Events(D) {
    return (D.rows.events || []).filter(function (ev) {
      return ev && ev.scene === "S5";
    }).slice().sort(function (a, b) {
      return String(a.ts) < String(b.ts) ? -1 : 1;
    });
  }

  /** 客诉受理事件（type=complaint 且载荷无 stage=located）。 */
  function complaintEvent(list) {
    return list.filter(function (ev) {
      return ev.type === "complaint" && !(ev.payload && ev.payload.stage);
    })[0] || null;
  }

  /** 批次定位事件（载荷 stage=located，trace 即定位结论）。 */
  function locatedEvent(list) {
    return list.filter(function (ev) {
      return ev.type === "complaint" && ev.payload && ev.payload.stage === "located";
    })[0] || null;
  }

  /** 8D 会签事件（type=hitl）。 */
  function signoffEvent(list) {
    return list.filter(function (ev) { return ev.type === "hitl"; })[0] || null;
  }

  function traceOf(ev) {
    return (ev && ev.payload && ev.payload.trace) || null;
  }

  function woStatusText(status) {
    return WO_STATUS[String(status)] || (status ? String(status) : "—");
  }

  function opRow(wo, opNo) {
    if (!wo || !Array.isArray(wo.ops)) return null;
    return wo.ops.filter(function (op) {
      return String(op && op.op_no) === String(opNo);
    })[0] || null;
  }

  /** 同批库存：与客诉批次同库位的批（库位串去掉括号内备注后比较）。 */
  function stockLots(D, lot) {
    if (!lot) return [];
    const wh = String(lot.location || "").split("（")[0];
    if (!wh) return [];
    return (D.rows.lots || []).filter(function (row) {
      return String(row && row.location || "").split("（")[0] === wh;
    });
  }

  /** 整屏模型：全部字段现取，缺失即 null（渲染层再决定降级文案）。 */
  function buildModel(D, S) {
    const events = s5Events(D);
    const complaint = complaintEvent(events);
    const located = locatedEvent(events);
    const trace = traceOf(located);
    const lot = trace ? findById(D.rows.lots, "lot_id", trace.lot) : null;
    const wo = trace ? findById(D.rows.work_orders, "wo_id", trace.wo) : null;
    const op = opRow(wo, trace ? trace.op : null);
    const equipment = trace ? findById(D.rows.equipment, "equipment_id", trace.equipment) : null;
    const stock = stockLots(D, lot);
    const stockQty = stock.reduce(function (sum, row) {
      const n = Number(row && row.qty);
      return sum + (Number.isFinite(n) ? n : 0);
    }, 0);
    const oqc = wo ? (D.rows.inspections || []).filter(function (row) {
      return row && row.stage === "OQC" && String(row.wo_id) === String(wo.wo_id);
    })[0] || null : null;
    const ship = events.filter(function (ev) { return ev.type === "order_status"; });
    const order = wo ? findById(D.rows.sales_orders, "order_id", wo.order_id) : null;
    const customer = order ? findById(D.rows.customers, "customer_id", order.customer_id) : null;
    const signoff = signoffEvent(events);
    return {
      events: events, complaint: complaint, located: located, trace: trace,
      lot: lot, wo: wo, op: op, equipment: equipment, stock: stock, stockQty: stockQty,
      oqc: oqc, shipEvents: ship,
      ship: ship.filter(function (ev) { return ev.payload && ev.payload.status === "shipped"; })[0] || null,
      invoice: ship.filter(function (ev) { return ev.payload && ev.payload.status === "invoiced"; })[0] || null,
      order: order, customer: customer, signoff: signoff, sla: S.locate_sla || {},
    };
  }

  /* ============================================================
   * 五、下钻（口径 + 来源 + 关联原始记录 + 处理链路）
   * ============================================================ */

  let drills = {};

  /** 字段中文名（只覆盖本屏出现的字段，未知字段原样展示，不臆造）。 */
  const FIELD_LABELS = {
    event_id: "事件号", ts: "时间", type: "事件类型", scene: "场景", object_ref: "关联对象",
    lot_id: "批次", wo_id: "工单", material_id: "物料", qty: "数量", location: "库位",
    op_no: "工序号", name: "名称", equipment_id: "设备", status: "状态",
    qty_good: "完工良品数", qty_planned: "计划数", order_id: "订单",
    stage: "检验阶段", result: "结论", sample_qty: "抽检数", defect_qty: "不良数",
    inspector: "检验员", batch_id: "检验批", customer_id: "客户", credit_grade: "信用等级",
    quality_profile: "质量档案", promise_date: "承诺交期", actual_ship_date: "实际发货日",
    unit_price: "单价", symptom: "症状", note: "说明", shift: "班次", op: "工序",
    equipment: "设备", lot: "批次", wo: "工单", stock_lots: "同批库存批次数",
  };

  /** 原始记录展开成键值对（嵌套对象/数组序列化为 JSON，保持可追溯而不美化）。 */
  function recordPairs(row) {
    if (!row) return [];
    return Object.keys(row).map(function (key) {
      const val = row[key];
      let text;
      if (val === null || val === undefined || val === "") text = "—";
      else if (typeof val === "object") text = JSON.stringify(val);
      else text = String(val);
      return [FIELD_LABELS[key] || key, text];
    });
  }

  /** 处理链路：本屏 S5 事件按时间列出（定位结论与闭环的来龙去脉）。 */
  function chainHtml(D) {
    const K = kit();
    const list = s5Events(D);
    if (!list.length) return '<div class="empty">暂无 S5 事件</div>';
    return '<div class="s05-list">' + list.map(function (ev) {
      const note = (ev.payload && ev.payload.note) || "";
      return '<div class="s05-list-row"><span class="s05-list-k">' + K.esc(K.fmtDate(ev.ts))
        + '</span><span class="s05-list-v">' + K.esc(String(ev.event_id) + " · " + String(ev.type)
        + (ev.object_ref ? " · " + String(ev.object_ref) : "") + (note ? " · " + note : ""))
        + "</span></div>";
    }).join("") + "</div>";
  }

  function drillBody(spec, row, D) {
    const pairs = [
      ["口径来源", spec.from],
      ["数据来源", sourceLabel(spec.object || "events")],
      ["口径说明", spec.note],
      ["规范出处", "data/demo_storyline/spec-v1.md §3 / §4 支线 B"],
    ];
    return '<div class="section-title">口径与来源</div>' + kv(pairs)
      + '<div class="section-title" style="margin-top:14px">关联原始记录</div>'
      + (row ? kv(recordPairs(row)) : '<div class="empty">该对象无对应原始记录行</div>')
      + '<div class="section-title" style="margin-top:14px">处理链路（S5 事件顺序）</div>'
      + chainHtml(D);
  }

  function registerDrill(key, spec, row, D) {
    drills[key] = {
      title: spec.title || "口径说明",
      subtitle: spec.subtitle || "",
      html: drillBody(spec, row, D),
    };
  }

  function buildDrills(D, S) {
    const m = buildModel(D, S);
    drills = {};
    const ev = m.located || m.complaint;

    registerDrill("complaint", {
      title: "客诉受理", object: "events",
      from: "events（scene=S5 · type=complaint，载荷无 stage）",
      note: "客诉受理事件即支线 B 的追溯入口；受理当天即可发起批次定位。",
    }, m.complaint, D);

    registerDrill("qty", {
      title: "客诉影响数", object: "events",
      from: "events（type=complaint）→ payload.qty",
      note: "客诉载荷中的影响数（件），用于判定影响范围与优先级。",
    }, m.complaint, D);

    registerDrill("sla", {
      title: "定位时限口径", object: "events",
      from: "events（stage=located）→ payload.note · " + (m.sla.source || ""),
      note: "演示口径：" + (m.sla.label || "—") + "完成批次定位；该口径由定位事件载荷自陈，非界面硬编码。",
    }, m.located, D);

    registerDrill("lot", {
      title: "批次定位", object: "lots",
      from: "events（stage=located）→ payload.trace.lot → lots.lot_id",
      note: "批次是追溯链条的锚点：由它向上回工单/工序/设备，向下看同批库存影响面。",
    }, m.lot, D);

    registerDrill("wo", {
      title: "工单回溯", object: "work_orders",
      from: "events（stage=located）→ payload.trace.wo → work_orders.wo_id",
      note: "工单承接批次产出，附带计划数 / 完工良品数与工序清单（ops）。",
    }, m.wo, D);

    registerDrill("op", {
      title: "工序回溯", object: "work_orders",
      from: "events（stage=located）→ payload.trace.op → work_orders[].ops[].op_no",
      note: "命中工序由 trace.op 指定，工序名 / 标准工时取自工单 ops 清单。",
    }, m.op, D);

    registerDrill("equipment", {
      title: "设备回溯", object: "equipment",
      from: "events（stage=located）→ payload.trace.equipment → equipment.equipment_id",
      note: "设备与命中工序的设备号一致，可用于同机台其他批次的风险扩散判断。",
    }, m.equipment, D);

    registerDrill("shift", {
      title: "班次回溯", object: "events",
      from: "events（stage=located）→ payload.trace.shift",
      note: "当班班次来自定位事件载荷，用于同班次产品的横向排查。",
    }, m.located, D);

    registerDrill("stock", {
      title: "同批库存位置", object: "lots",
      from: "events（stage=located）→ payload.trace.stock_lots + lots（同库位明细）",
      note: "同库位批次明细现取自 lots 对象；批次数与定位载荷自陈值一致。",
    }, m.lot, D);

    registerDrill("oqc", {
      title: "OQC 放行", object: "inspections",
      from: "inspections（stage=OQC · wo_id 与工单一致）",
      note: "出货前检验批的结论 / 抽检数 / 不良数，是放行判定依据。",
    }, m.oqc, D);

    registerDrill("ship", {
      title: "发货确认", object: "events",
      from: "events（type=order_status · payload.status=shipped）",
      note: "发货确认事件含实际发货日；与订单承诺交期对照即交期履约口径。",
    }, m.ship, D);

    registerDrill("invoice", {
      title: "开票", object: "events",
      from: "events（type=order_status · payload.status=invoiced）",
      note: "开票事件完成履约闭环的最后一环（发货 → 开票）。",
    }, m.invoice, D);

    registerDrill("order", {
      title: "订单与交期", object: "sales_orders",
      from: "work_orders.order_id → sales_orders.order_id",
      note: "订单行给出数量与单价、承诺交期；实际发货日以发货事件为准。",
    }, m.order, D);

    registerDrill("customer", {
      title: "客户质量概况", object: "customers",
      from: "sales_orders.customer_id → customers.customer_id",
      note: "客户档案含信用等级与近 12 个月质量档案（客诉/退货率），只读展示。",
    }, m.customer, D);

    registerDrill("signoff", {
      title: "8D 会签", object: "events",
      from: "events（type=hitl · payload.slots / result）",
      note: "会签槽位取自事件载荷 slots，经槽位词典转中文名；只读访客不出现可提交按钮。",
    }, m.signoff, D);

    registerDrill("trace-step", {
      title: "追溯链路", object: "events",
      from: "S5 事件序列（受理 → 定位 → 会签）",
      note: "链路顺序即演示讲解顺序，共 " + (S.trace_steps || []).length + " 段。",
    }, ev, D);

    return m;
  }

  function openDrillByKey(key) {
    const K = kit();
    const item = drills[key];
    if (!item) {
      if (typeof K.toast === "function") K.toast("该指标暂无可展示的口径说明", "warn", 2600);
      return;
    }
    K.openDrill({ title: item.title, subtitle: item.subtitle, html: item.html });
  }

  /* ============================================================
   * 六、渲染
   * ============================================================ */

  const TONE_COLORS = {
    red: "var(--danger,#CF222E)", blue: "var(--primary,#185FA5)",
    purple: "var(--purple,#8250DF)", green: "var(--ok,#1A7F37)",
    gray: "var(--border-strong,#D3DBE6)", amber: "var(--warn,#9A6700)",
  };

  function nodeValue(model, key) {
    const t = model.trace || {};
    if (key === "complaint") {
      return {
        value: model.complaint ? String(model.complaint.payload.symptom || "客诉受理") : "—",
        hint: model.complaint ? String(model.complaint.object_ref || "") : "无受理事件",
      };
    }
    if (key === "lot") {
      return { value: t.lot ? String(t.lot) : "—", hint: model.lot ? String(model.lot.location) : "批次对象缺失" };
    }
    if (key === "wo") {
      return {
        value: t.wo ? String(t.wo) : "—",
        hint: model.wo ? (woStatusText(model.wo.status) + " · 良品 " + num(model.wo.qty_good)) : "工单对象缺失",
      };
    }
    if (key === "op") {
      return {
        value: t.op ? String(t.op) : "—",
        hint: model.op ? String(model.op.name) : "工序清单缺失",
      };
    }
    if (key === "equipment") {
      return {
        value: t.equipment ? String(t.equipment) : "—",
        hint: model.equipment ? String(model.equipment.name) : "设备对象缺失",
      };
    }
    if (key === "shift") {
      return { value: t.shift ? String(t.shift) : "—", hint: "定位载荷 · 当班班次" };
    }
    return {
      value: Number.isFinite(Number(t.stock_lots)) ? (num(t.stock_lots) + " 批") : "—",
      hint: "同库位合计 " + num(model.stockQty) + " 件",
    };
  }

  /** 节点 SVG：坐标取自契约数据；连线端点由节点坐标计算（不写死端点）。 */
  function graphSvg(S, model) {
    const K = kit();
    const canvas = S.canvas || { w: 1040, h: 320 };
    const nodes = S.nodes || [];
    const byKey = {};
    nodes.forEach(function (n) { byKey[n.key] = n; });
    const cx = function (n) { return n.x + n.w / 2; };

    const edges = (S.edges || []).map(function (edge) {
      const a = byKey[edge.from];
      const b = byKey[edge.to];
      if (!a || !b) return "";
      const stroke = 'style="stroke:var(--border-strong,#D3DBE6);stroke-width:1.6;fill:none"';
      let d = "";
      let labelX = 0;
      let labelY = 0;
      let arrow = "";
      if (Math.abs(cx(a) - cx(b)) < 1) {
        // 纵向：下边中点 → 上边中点
        const x = cx(a);
        d = "M" + x + " " + (a.y + a.h) + " V" + (b.y - 8);
        arrow = '<path d="M' + (x - 5) + " " + (b.y - 9) + "L" + (x + 5) + " " + (b.y - 9)
          + "L" + x + " " + (b.y - 1) + 'Z" class="s05-arrow" '
          + 'style="fill:var(--border-strong,#D3DBE6)"/>';
        labelX = x + 8;
        labelY = (a.y + a.h + b.y) / 2;
      } else {
        const right = cx(a) < cx(b);
        const x1 = right ? a.x + a.w : a.x;
        const x2 = right ? b.x - 10 : b.x + b.w + 10;
        const y = cx(a) < cx(b) ? a.y + a.h / 2 : a.y + a.h / 2;
        d = "M" + x1 + " " + y + " H" + x2;
        arrow = '<path d="M' + (right ? x2 + 9 : x2 - 9) + " " + (y - 5) + "L"
          + (right ? x2 + 9 : x2 - 9) + " " + (y + 5) + "L" + (right ? x2 + 1 : x2 - 1)
          + " " + y + 'Z" class="s05-arrow" style="fill:var(--border-strong,#D3DBE6)"/>';
        labelX = (x1 + x2) / 2;
        labelY = y - 8;
      }
      return '<path class="s05-edge" d="' + d + '" ' + stroke + "/>" + arrow
        + '<text class="s05-edge-label" x="' + labelX + '" y="' + labelY + '" text-anchor="middle" '
        + 'style="fill:var(--text-3,#8B98AB)">' + K.esc(edge.label || "") + "</text>";
    }).join("");

    const shapes = nodes.map(function (n) {
      const v = nodeValue(model, n.key);
      const color = TONE_COLORS[n.tone] || TONE_COLORS.gray;
      const valueSize = String(v.value).length > 12 ? 11.5 : 13.5;
      return '<g class="s05-node s05-t-' + K.esc(n.tone || "gray") + '" data-sk-drill="'
        + K.esc(n.key) + '" role="button" tabindex="0" aria-label="'
        + K.esc(n.label + "，点击查看口径") + '">'
        + '<rect class="s05-node-rect" x="' + n.x + '" y="' + n.y + '" width="' + n.w
        + '" height="' + n.h + '" rx="10" style="fill:var(--card,#FFFFFF);stroke:' + color + '"/>'
        + '<text class="s05-node-label" x="' + cx(n) + '" y="' + (n.y + 22)
        + '" text-anchor="middle" style="fill:var(--text-3,#8B98AB);font-size:12px">'
        + K.esc(n.label) + "</text>"
        + '<text class="s05-node-value" x="' + cx(n) + '" y="' + (n.y + 41)
        + '" text-anchor="middle" style="fill:var(--text,#1F2733);font-size:' + valueSize
        + 'px;font-weight:700">' + K.esc(v.value) + "</text>"
        + '<text class="s05-node-hint" x="' + cx(n) + '" y="' + (n.y + 57)
        + '" text-anchor="middle" style="fill:var(--text-3,#8B98AB);font-size:10.5px">'
        + K.esc(Array.isArray(v.hint) ? v.hint.join(" · ") : v.hint) + "</text>"
        + "</g>";
    }).join("");

    return '<div class="s05-graph-wrap" id="s05-graph-wrap">'
      + '<svg id="s05-graph" viewBox="0 0 ' + canvas.w + " " + canvas.h
      + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="S5 批次追溯图谱">'
      + edges + shapes + "</svg></div>";
  }

  function locateRow(model) {
    const K = kit();
    const readonly = K.isReadonly();
    const doneText = model.located && model.trace
      ? "已定位：客诉 → " + String(model.trace.lot || "—") + "（" + String(model.trace.op || "—")
        + " 工序 · " + String(model.trace.equipment || "—") + "）"
      : "尚未定位（缺少 stage=located 的客诉事件）";
    return '<div class="s05-locate-row">'
      + '<button class="btn btn-primary" type="button" id="s05-locate"'
      + (model.located ? "" : " disabled")
      + '>开始批次定位</button>'
      + '<span class="s05-locate-state" id="s05-locate-state">'
      + K.esc(model.located ? (model.sla.label || "待定位") : doneText) + "</span>"
      + K.badge(readonly ? "只读访客 · 定位为纯视图动作" : "定位不写库 · 结论可回指", "badge-outline")
      + "</div>";
  }

  function stepsHtml(S, model) {
    const K = kit();
    const steps = S.trace_steps || [];
    if (!steps.length) return "";
    return '<div class="s05-steps">' + steps.map(function (step) {
      const v = model.trace ? nodeValue(model, step.key).value : "—";
      return '<span class="s05-step"><span class="s05-step-no">' + K.esc(String(step.no))
        + '</span><span data-sk-drill="trace-step">' + K.esc(step.title + "：" + v)
        + "</span></span>";
    }).join("") + "</div>";
  }

  function overviewCards(model) {
    const K = kit();
    const c = model.complaint;
    return K.numberRow([
      {
        label: "客诉受理", tone: "red", drill: "complaint",
        value: c ? String(c.object_ref || "—") : "—",
        hint: c ? ("症状：" + String(c.payload.symptom || "—")) : "无受理事件",
      },
      {
        label: "影响数", tone: "gray", drill: "qty", unit: "件",
        value: c && c.payload.qty !== undefined ? num(c.payload.qty) : "—",
        hint: "客诉载荷",
      },
      {
        label: "受理时间", tone: "gray", drill: "complaint",
        value: c ? K.fmtDate(c.ts) : "—", hint: "事件时间",
      },
      {
        label: "定位时限", tone: "blue", drill: "sla", unit: "分钟",
        value: model.sla.minutes !== undefined ? ("≤" + String(model.sla.minutes)) : "—",
        hint: model.sla.label || "—",
      },
    ]);
  }

  function traceCards(model) {
    const K = kit();
    const t = model.trace;
    const cells = [
      { key: "lot", label: "批次", tone: "blue", hint: model.lot ? String(model.lot.location) : "—" },
      { key: "wo", label: "工单", tone: "blue", hint: model.wo ? woStatusText(model.wo.status) : "—" },
      { key: "op", label: "工序", tone: "blue", hint: model.op ? String(model.op.name) : "—" },
      { key: "equipment", label: "设备", tone: "purple", hint: model.equipment ? String(model.equipment.name) : "—" },
      { key: "shift", label: "当班", tone: "gray", hint: "定位载荷" },
    ];
    return K.numberRow(cells.map(function (cell) {
      let value = "—";
      if (cell.key === "lot") value = t ? String(t.lot || "—") : "—";
      else if (cell.key === "wo") value = t ? String(t.wo || "—") : "—";
      else if (cell.key === "op") value = t ? String(t.op || "—") : "—";
      else if (cell.key === "equipment") value = t ? String(t.equipment || "—") : "—";
      else value = t ? String(t.shift || "—") : "—";
      return { label: cell.label, value: value, tone: cell.tone, hint: cell.hint, drill: cell.key };
    }));
  }

  function stockBlock(model, D) {
    const K = kit();
    const t = model.trace || {};
    const rows = model.stock || [];
    const head = K.numberRow([
      {
        label: "同批库存批次数", tone: "green", drill: "stock", unit: "批",
        value: t.stock_lots !== undefined ? num(t.stock_lots) : "—",
        hint: "定位载荷 trace.stock_lots",
      },
      {
        label: "同库位库存数量", tone: "green", drill: "stock", unit: "件",
        value: rows.length ? num(model.stockQty) : "—",
        hint: rows.length ? ("按 lots 同库位明细求和 · 共 " + String(rows.length) + " 批") : "lots 明细缺失",
      },
    ]);
    const list = rows.length ? ('<div class="s05-list">' + rows.map(function (row) {
      return '<div class="s05-list-row">'
        + '<span class="s05-list-k">' + K.esc(String(row.lot_id || "—")) + "</span>"
        + '<span class="s05-list-v">' + K.esc(String(row.location || "—") + " · 数量 "
          + num(row.qty) + " · 物料 " + String(row.material_id || "—") + " · 工单 "
          + String(row.wo_id || "—")) + "</span></div>";
    }).join("") + "</div>") : smallNote("lots 未返回同库位明细，本清单留空（静默降级，不编造数量）。");
    return head + list
      + smallNote("口径：" + sourceLabel("lots") + "；同库位判定 = 库位串去掉括号内备注后相等。");
  }

  function shipmentBlock(model) {
    const K = kit();
    const oqc = model.oqc;
    const ship = model.ship;
    const invoice = model.invoice;
    const shipDate = ship && ship.payload ? ship.payload.actual_ship_date : null;
    return K.numberRow([
      {
        label: "OQC 结论", tone: "green", drill: "oqc",
        value: oqc ? (OQC_RESULT[String(oqc.result)] || String(oqc.result || "—")) : "—",
        hint: oqc ? ("检验批 " + String(oqc.batch_id || "—")) : "inspections 缺失",
      },
      {
        label: "抽检数", tone: "gray", drill: "oqc", unit: "件",
        value: oqc ? num(oqc.sample_qty) : "—", hint: "OQC 抽样",
      },
      {
        label: "发货状态", tone: "blue", drill: "ship",
        value: ship ? (SHIP_STATUS[String(ship.payload.status)] || String(ship.payload.status || "—")) : "—",
        hint: shipDate ? ("实际发货 " + K.fmtDate(shipDate)) : "无发货事件",
      },
      {
        label: "开票状态", tone: "purple", drill: "invoice",
        value: invoice ? (SHIP_STATUS[String(invoice.payload.status)] || String(invoice.payload.status || "—")) : "—",
        hint: invoice ? K.fmtDate(invoice.ts) : "无开票事件",
      },
      {
        label: "订单数量", tone: "gray", drill: "order", unit: "件",
        value: model.order && Array.isArray(model.order.lines) && model.order.lines[0]
          ? num(model.order.lines[0].qty) : "—",
        hint: model.order ? ("承诺交期 " + K.fmtDate(model.order.promise_date)) : "订单缺失",
      },
    ]);
  }

  function customerBlock(model) {
    const K = kit();
    const cus = model.customer;
    if (!cus) {
      // 只读概况卡的静默降级：不报错、不编数，只说明留空原因。
      return '<div class="section-title">客户质量概况<span class="section-extra">只读 · 静默降级</span></div>'
        + smallNote("customers 未就绪（或订单未关联客户）：本卡留空，不显示编造数字。");
    }
    return '<div class="section-title">客户质量概况<span class="section-extra">只读 · 静默降级</span></div>'
      + kv([
        ["客户", String(cus.name || "—") + " / " + String(cus.customer_id || "—")],
        ["信用等级", cus.credit_grade || "—"],
        ["质量档案", cus.quality_profile || "—"],
        ["对客订单", model.order ? String(model.order.order_id || "—") : "—"],
      ])
      + smallNote("口径：" + sourceLabel("customers") + "，经 sales_orders.customer_id 关联。");
  }

  function signoffBlock(model, D, S) {
    const K = kit();
    const signoff = model.signoff;
    const refs = (S.eight_d && S.eight_d.refs) || {};
    if (!signoff) {
      return '<div class="section-title">8D 会签（客诉闭环）</div>'
        + smallNote("未找到 S5 的 hitl 事件，会签卡留空（静默降级）。");
    }
    const keys = Array.isArray(signoff.payload.slots) ? signoff.payload.slots : [];
    const labels = S.slot_labels || {};
    const slots = keys.map(function (key) {
      const name = labels[key];
      return {
        role: name ? (name + "（" + String(key) + "）") : String(key),
        user: "—",
        status: String(signoff.payload.result || "").indexOf("闭环") !== -1 ? "approved" : "pending",
        ts: signoff.ts,
      };
    });
    return K.signoffCard({
      title: (S.eight_d && S.eight_d.title) || "8D 会签",
      slots: slots,
      note: "槽位与结论取自 " + String(signoff.event_id || refs.signoff_event || "hitl 事件")
        + " 载荷（slots / result）；8D 报告号 " + String(refs.report_id || "—")
        + "，客诉单 " + String(refs.complaint_id || "—") + "。" + (S.eight_d && S.eight_d.source ? "口径：" + String(S.eight_d.source) : ""),
    });
  }

  function pathBlock(S) {
    const K = kit();
    const p = S.click_path || {};
    const steps = p.steps || [];
    if (!steps.length) return "";
    return '<div class="sk-block"><div class="section-title">' + K.esc(p.title || "点击路径")
      + '<span class="section-extra">验收口径：≤ ' + K.esc(String(p.limit || 5)) + " 次点击</span></div>"
      + '<ol class="s05-path">' + steps.map(function (step) {
        return "<li><b>" + K.esc(step.action || "") + "</b> → " + K.esc(step.lands || "") + "</li>";
      }).join("") + "</ol>"
      + (p.note ? smallNote(String(p.note)) : "") + "</div>";
  }

  function valueLines(S) {
    const K = kit();
    const lines = S.value_lines || [];
    if (!lines.length) return "";
    return '<div class="s05-steps">' + lines.map(function (line) {
      return '<span class="s05-step">' + K.icon("info", 12) + K.esc(line) + "</span>";
    }).join("") + "</div>";
  }

  /* ============================================================
   * 五·五、智能体层（T18）：协同面板 / 取证书签 / 同根因案例 / 8D 知识图 / 治理卡
   *
   * 纪律：全部数据经 SceneKit.replay()（永不 reject）与 ctx.api.workflowPending()
   * 现取；取不到就整块降级，不编造步骤与数字。回放包字段与真实链路同构
   * （steps 六键 = AgenticStep.to_dict()），来源徽标由 SceneKit.agentPanel 强制渲染。
   * ============================================================ */

  function agentLayerOf(S) {
    return (S && S.agent_layer && typeof S.agent_layer === "object") ? S.agent_layer : {};
  }

  function agentDegrade(text) {
    const K = kit();
    return '<div class="card"><div class="card-pad sk-degrade" data-sk-s05-agent-degrade="1">'
      + "<b>" + K.esc(text || "演示回放数据未就绪，不展示编造内容") + "</b>"
      + '<div class="sk-degrade-sub">按上线纪律：取不到回放就不渲染半块，也不显示编造数字。</div>'
      + "</div></div>";
  }

  function summaryOf(pkg, branch) {
    const bag = (pkg && pkg.node_steps) || {};
    let list = (branch && bag[branch]) || null;
    if (!list || !list.length) list = (pkg && pkg.steps) || [];
    if (!list.length) return "";
    const last = list[list.length - 1] || {};
    return String(last.summary || "");
  }

  function agentNameOf(pkg, domain) {
    const list = (pkg && pkg.agents) || [];
    for (let i = 0; i < list.length; i += 1) {
      if (list[i] && list[i].domain === domain) {
        return String(list[i].abbr || "") + " · " + String(list[i].name || "");
      }
    }
    return String(domain || "");
  }

  function graphBadgesHtml(S, pkg) {
    const K = kit();
    const layer = agentLayerOf(S);
    const badges = layer.graph_badges || [];
    if (!badges.length || !pkg) return "";
    const byKey = {};
    (S.nodes || []).forEach(function (n) { byKey[n.key] = n; });
    let html = '<div class="s05-badges" data-sk-s05-badges="1">'
      + '<div class="s05-badges-title">取证节点书签 · 谁取的证 / 用什么工具 / 得出什么'
      + '<span class="s05-badges-extra">' + K.esc(layer.source_note || "") + "</span></div>";
    badges.forEach(function (b) {
      const node = byKey[b.key];
      if (!node) return;
      html += '<button type="button" class="s05-badge" data-sk-s05-badge="' + K.esc(b.key) + '"'
        + ' data-sk-s05-agent="' + K.esc(b.domain) + '" data-sk-s05-tool="' + K.esc(b.tool) + '"'
        + ' data-sk-s05-branch="' + K.esc(b.branch || "") + '">'
        + '<span class="s05-badge-label">' + K.esc(node.label) + "</span>"
        + '<span class="s05-badge-agent">' + K.esc(agentNameOf(pkg, b.domain)) + "</span>"
        + "</button>";
    });
    return html + "</div>";
  }

  function bindGraphBadges(root, S, pkg) {
    const K = kit();
    if (!root) return;
    const buttons = root.querySelectorAll("[data-sk-s05-badge]");
    for (let i = 0; i < buttons.length; i += 1) {
      buttons[i].addEventListener("click", function () {
        const key = this.getAttribute("data-sk-s05-badge");
        const domain = this.getAttribute("data-sk-s05-agent");
        const tool = this.getAttribute("data-sk-s05-tool");
        const branch = this.getAttribute("data-sk-s05-branch");
        const node = (S.nodes || []).filter(function (n) { return n.key === key; })[0] || {};
        K.openDrill({
          title: String(node.label || key) + " · 取证书签",
          subtitle: "回放轨迹与真实链路同构（来源徽标「演示回放」）",
          html: kv([
            ["图谱节点", String(node.label || key)],
            ["取证智能体", agentNameOf(pkg, domain)],
            ["调用工具", String(tool || "")],
            ["取证结论", summaryOf(pkg, branch) || "（该节点无独立回放步骤，沿用主链结论）"],
            ["关联原始记录", String(node.record || "")],
            ["口径来源", String(node.value_from || "")],
          ]),
        });
      });
    }
  }

  function casesHtml(S, pkg) {
    const K = kit();
    const conf = agentLayerOf(S).cases || {};
    if (!pkg) return agentDegrade("同根因案例回放数据未就绪");
    const hits = pkg.hits || [];
    let html = '<div class="section-title">' + K.esc(conf.title || "同根因历史案例")
      + '<span class="section-extra">' + K.esc(pkg.query || "") + "</span></div>";
    if (!hits.length) return html + agentDegrade("本次检索无命中（不编造案例凑数）");
    html += '<div class="s05-case-list">' + hits.map(function (h) {
      return '<div class="s05-case"><div class="s05-case-head">'
        + '<span class="s05-case-id">' + K.esc(h.case_id) + "</span>"
        + '<span class="s05-case-title">' + K.esc(h.title) + "</span>"
        + '<span class="s05-case-score">相似度 ' + K.esc(String(h.score)) + "</span></div>"
        + '<div class="s05-case-meta">根因：' + K.esc(h.root_cause) + " · 部门：" + K.esc(h.dept)
        + (h.redacted ? " · 已脱敏" : "") + "</div>"
        + '<div class="s05-case-reason">' + K.esc(h.reason || "") + "</div></div>";
    }).join("") + "</div>";
    const trace = pkg.trace || [];
    if (trace.length) {
      html += '<div class="s05-trace-title">' + K.esc(conf.trace_title || "检索轨迹") + "</div>"
        + '<ol class="s05-trace-steps">' + trace.map(function (t) {
          return "<li><b>" + K.esc(t.title) + "</b> — " + K.esc(t.detail) + "</li>";
        }).join("") + "</ol>";
    }
    const explain = pkg.filter_explain || [];
    if (explain.length) {
      html += '<details class="s05-explain"><summary>'
        + K.esc(conf.explain_title || "为什么看到这几条 / 为什么看不到") + "</summary><ul>"
        + explain.map(function (line) { return "<li>" + K.esc(line) + "</li>"; }).join("")
        + "</ul></details>";
    }
    if (pkg.ontology_gain) {
      html += smallNote("本体增益：" + String(pkg.ontology_gain.before) + " → "
        + String(pkg.ontology_gain.after) + " 条 · " + String(pkg.ontology_gain.detail || ""));
    }
    return html;
  }

  function d8Svg(S) {
    const K = kit();
    const d8 = agentLayerOf(S).d8 || {};
    const canvas = d8.canvas || { w: 1040, h: 128 };
    const steps = d8.steps || [];
    const current = String(d8.current_stage || "");
    const parts = ['<svg viewBox="0 0 ' + canvas.w + " " + canvas.h
      + '" role="img" aria-label="8D 结构图" style="width:100%;height:auto;display:block">'];
    steps.forEach(function (st) {
      const active = st.key === current;
      parts.push('<g class="s05-d8-node" id="s05-d8-' + K.esc(st.key) + '"'
        + ' data-sk-kc-node="' + K.esc(st.key) + '">'
        + '<rect x="' + st.x + '" y="' + st.y + '" width="' + st.w + '" height="' + st.h + '" rx="8"'
        + ' fill="' + (active ? "var(--primary-light,#E7F0FA)" : "var(--card,#fff)") + '"'
        + ' stroke="' + (active ? "var(--primary,#185FA5)" : "var(--border-strong,#D3DBE6)") + '"'
        + ' stroke-width="' + (active ? "2.2" : "1.3") + '"></rect>'
        + '<text x="' + (st.x + st.w / 2) + '" y="' + (st.y + 22) + '" text-anchor="middle"'
        + ' font-size="12.5" font-weight="600" fill="var(--text,#1F2328)">' + K.esc(st.key) + "</text>"
        + '<text x="' + (st.x + st.w / 2) + '" y="' + (st.y + 40) + '" text-anchor="middle"'
        + ' font-size="11" fill="var(--text-2,#57606A)">' + K.esc(st.label) + "</text></g>");
    });
    parts.push("</svg>");
    return parts.join("");
  }

  function d8PanelHtml(S, pkg, D) {
    const K = kit();
    const d8 = agentLayerOf(S).d8 || {};
    const caseId = String(d8.root_cause_case || "");
    const cases = (D && D.rows && D.rows.cases) || [];
    let rootCause = "";
    for (let i = 0; i < cases.length; i += 1) {
      if (caseId && cases[i].case_id === caseId) rootCause = String(cases[i].root_cause || "");
    }
    const nodes = (d8.steps || []).map(function (st) {
      let conclusion = "";
      if (st.key === "D4") conclusion = "根因：" + (rootCause || "见同根因案例");
      else if (st.key === "D8") conclusion = "待 d8_close 会签（演示回放）";
      else conclusion = "该阶段由 " + agentNameOf(pkg, st.agent) + " 承接";
      return {
        id: st.key, label: st.label, agent: agentNameOf(pkg, st.agent),
        tool: "回放轨迹", conclusion: conclusion, refs: [],
      };
    });
    return '<div class="section-title">8D 结构图 · 知识图片卡<span class="section-extra">'
      + K.esc(d8.note || "") + "</span></div>"
      + K.knowledgeCard({
        title: "8D 闭环（D1–D8）· 当前阶段高亮",
        svg: d8Svg(S),
        nodes: nodes,
        caption: "图中节点结论与智能体协同面板同源（回放包 node_steps / 案例根因），可逐条对回。",
      });
  }

  function hitlPanelHtml(S, tasks) {
    const K = kit();
    const conf = agentLayerOf(S).hitl || {};
    if (!tasks || !tasks.length) {
      return agentDegrade(conf.empty || "仅展示演示种子工单（当前无可见种子）");
    }
    let html = '<div class="section-title">' + K.esc(conf.title || "治理工单（只读）")
      + '<span class="section-extra">按权限矩阵可见 · 零写操作</span></div>';
    tasks.forEach(function (t) {
      const vis = String(t.visibility || "");
      const visText = vis === "mine" ? "我的待办（会签席）"
        : vis === "domain" ? "域内可见（只读）" : (conf.invisible || "不可见");
      const st = t.signoff_status || {};
      const progress = String(st.progress || (String(st.done || 0) + "/" + String(st.total || 0)));
      const ctx = t.context || {};
      html += '<div class="s05-hitl card"><div class="card-pad">'
        + '<div class="s05-hitl-head"><b>' + K.esc(t.title || "") + "</b>"
        + '<span class="s05-hitl-slot">' + K.esc(ctx.slot || "") + "</span>"
        + '<span class="s05-hitl-vis">' + K.esc(visText) + "</span></div>"
        + '<div class="s05-hitl-meta">状态：' + K.esc(t.status || "") + " · 会签进度："
        + K.esc(progress) + " · 归属：" + K.esc(ctx.complaint_id || "演示种子") + "</div>"
        + '<div class="s05-hitl-ro">' + K.esc(conf.readonly_note || "只读演示") + "</div>"
        + "</div></div>";
    });
    return html;
  }

  function hydrateAgentLayer(root, ctx, D, S) {
    const K = kit();
    if (!root) return;
    const layer = agentLayerOf(S);
    const replays = layer.replays || {};
    const panelBox = root.querySelector("#s05-agent-panel");
    const badgeBox = root.querySelector("#s05-graph-badges");
    const caseBox = root.querySelector("#s05-case-panel");
    const hitlBox = root.querySelector("#s05-hitl-panel");
    const d8Box = root.querySelector("#s05-d8-panel");
    const pending = (ctx && ctx.api && typeof ctx.api.workflowPending === "function")
      ? ctx.api.workflowPending().then(function (res) {
        // app.js::_get 已解包：workflowPending() 直接返回任务数组（信封 {code,data}
        // 在 API 层拆掉了）。两种形状都认，避免「接口有数据、屏上恒为空」。
        if (Array.isArray(res)) return res;
        return (res && res.data) || [];
      })
        .catch(function () { return null; })
      : Promise.resolve(null);

    K.replay(replays.agent).then(function (res) {
      if (!panelBox || !panelBox.parentNode) return;
      if (!res || !res.ok || !res.package) {
        const why = String((res && res.error) || "回放数据未就绪");
        panelBox.innerHTML = agentDegrade("演示回放数据未就绪（" + why + "）");
        if (badgeBox) badgeBox.innerHTML = "";
        if (hitlBox) hitlBox.innerHTML = agentDegrade("治理待办与回放同源，回放未就绪时整块降级");
        if (d8Box) d8Box.innerHTML = "";
        return;
      }
      const pkg = res.package;
      pending.then(function (tasks) {
        if (!panelBox.parentNode) return;
        const seeds = (tasks || []).filter(function (t) {
          const c = (t && t.context) || {};
          // 判据用 demo_seed_id 标记（与 api._ensure_8d_seed 的幂等键同源）：
          // 不按 origin 猜（origin 是来源说明，未来新增 demo_seed 工单可能混入）。
          return c.demo_seed_id === "s5_8d_v1"
            && (c.slot === "d8_close" || c.slot === "complaint_reply");
        });
        const first = seeds[0] || null;
        const st = (first && first.signoff_status) || {};
        const hitlTask = first ? {
          title: first.title,
          slot: (first.context || {}).slot,
          status: first.status,
          visibility: first.visibility,
          progress: String(st.progress || (String(st.done || 0) + "/" + String(st.total || 0))),
          required: true,
          my_action: first.my_action,
          readonly_note: (agentLayerOf(S).hitl || {}).readonly_note || "",
        } : null;
        panelBox.innerHTML = K.agentPanel({
          title: pkg.title,
          agents: pkg.agents,
          dag: pkg.dag,
          steps: pkg.steps,
          node_steps: pkg.node_steps,
          conclusion: pkg.conclusion,
          source: "replay",
          hitl_task: hitlTask,
        });
        if (badgeBox) {
          badgeBox.innerHTML = graphBadgesHtml(S, pkg);
          bindGraphBadges(root, S, pkg);
        }
        if (d8Box) d8Box.innerHTML = d8PanelHtml(S, pkg, D);
        if (hitlBox) {
          hitlBox.innerHTML = (tasks === null)
            ? agentDegrade("治理待办读取失败，不展示编造工单")
            : hitlPanelHtml(S, seeds);
        }
      });
    });

    K.replay(replays.cases).then(function (res) {
      if (!caseBox || !caseBox.parentNode) return;
      if (!res || !res.ok || !res.package) {
        caseBox.innerHTML = casesHtml(S, null);
        return;
      }
      caseBox.innerHTML = casesHtml(S, res.package);
    });
  }

  function bodyHtml(S, D) {
    const K = kit();
    const model = buildDrills(D, S);
    const degrade = D.missing.length
      ? smallNote("以下对象未取到、已就地静默降级：" + D.missing.join(" / ")
        + "（口径：" + STORY_OBJECTS.map(sourceLabel).join("、") + "）。")
      : smallNote("取数口径：" + STORY_OBJECTS.map(sourceLabel).join("、") + "。");
    return '<div class="sk-block" id="s05-agent-panel">' + K.spinner("lg")
      + "正在读取智能体回放轨迹…</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">客诉与批次定位<span class="section-extra">'
      + K.esc("支线 B · 8D 闭环入口") + "</span></div>"
      + overviewCards(model)
      + locateRow(model)
      + graphSvg(S, model)
      + '<div id="s05-graph-badges"></div>'
      + stepsHtml(S, model)
      + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">定位结论（五段回指）</div>'
      + '<div id="s05-trace-cards">' + traceCards(model) + "</div>"
      + "</div>"
      + '<div class="sk-block" id="s05-case-panel">' + K.spinner("lg")
      + "正在读取同根因案例回放…</div>"
      + '<div class="sk-block">'
      + K.eventFlow(s5Events(D).map(function (ev) {
        return {
          ts: ev.ts, type: ev.type, scene: ev.scene,
          text: (ev.payload && ev.payload.note) || "",
        };
      }), {
        title: "S5 关键事件（出货 → 客诉 → 闭环）",
        extra: sourceLabel("events"),
        note: "事件为 T04 装载层原样读出；点击事件流上方各卡可查看关联原始记录。",
      })
      + "</div>"
      + '<div class="sk-block">' + signoffBlock(model, D, S) + "</div>"
      + '<div class="sk-block" id="s05-hitl-panel">' + K.spinner("lg")
      + "正在读取治理待办…</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">同批库存位置<span class="section-extra">影响面</span></div>'
      + stockBlock(model, D) + "</div>"
      + '<div class="sk-block">'
      + '<div class="section-title">OQC 放行与发货状态</div>'
      + shipmentBlock(model) + "</div>"
      + '<div class="sk-block" id="s05-d8-panel"></div>'
      + '<div class="sk-block">' + customerBlock(model) + "</div>"
      + pathBlock(S)
      + '<div class="sk-block">' + degrade
      + smallNote("规范出处：" + String(S.spec_ref || "") + "；本屏全部数字现取自装载层，逐项可下钻。")
      + "</div>";
  }

  /** 定位按钮：纯前端点亮（不写库），供验收「≤5 次点击完成定位」。 */
  function bindLocate(root, model) {
    const btn = root.querySelector("#s05-locate");
    if (!btn) return;
    btn.addEventListener("click", function () {
      const wrap = root.querySelector("#s05-graph-wrap");
      const cards = root.querySelector("#s05-trace-cards");
      if (wrap && wrap.classList) wrap.classList.add("is-located");
      if (cards && cards.classList) cards.classList.add("is-located");
      const state = root.querySelector("#s05-locate-state");
      if (state) {
        const t = model.trace || {};
        state.className = "s05-locate-state is-done";
        state.textContent = "已定位：" + String(t.lot || "—") + " · " + String(t.op || "—")
          + " 工序 · " + String(t.equipment || "—") + " · " + String(t.shift || "—");
      }
      btn.setAttribute("disabled", "disabled");
    });
  }

  function degradeHtml(D) {
    const K = kit();
    const missing = D && D.missing ? D.missing : CORE_OBJECTS;
    return '<div class="sk-degrade">' + K.icon("warn", 15) + "<div>"
      + "<b>演示装载层未就绪：</b>缺少 " + K.esc(missing.join(" / "))
      + "。本屏不显示编造数字。"
      + '<div class="sk-degrade-sub">请确认 '
      + K.esc("GET /api/v2/demo/objects/{name}") + " 可用（T04 装载层）；"
      + "离线时以 window.DEMO_STORY 回落。</div></div></div>";
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
    const readonly = K.isReadonly();
    root.innerHTML = '<div class="content-inner">'
      + K.pageHead({
        icon: "truck",
        title: "S5 · 出货与质量追溯",
        desc: (S.value_lines || []).join("；") + "。数字全部现取自演示装载层，逐项可下钻。",
        extra: K.badge(readonly ? "只读访客 · 不发起写操作" : "只读演示 · 定位为视图动作", "badge-outline"),
      })
      + '<div class="sk-block" id="s05-body"><div class="empty">' + K.spinner("lg")
      + "正在读取演示装载层…</div></div>"
      + '<div class="sk-block" id="s05-value-lines">' + valueLines(S) + "</div></div>";

    const body = root.querySelector("#s05-body");
    loadAll().then(function (D) {
      // 屏已切走（节点脱离文档）就不再写 DOM。
      if (!body || !body.parentNode) return;
      if (!D || !D.ok) {
        body.innerHTML = degradeHtml(D);
        return;
      }
      body.innerHTML = bodyHtml(S, D);
      K.bindDrills(body, openDrillByKey);
      bindLocate(body, buildModel(D, S));
      // T18：智能体层（回放协同面板 / 取证书签 / 同根因案例 / 8D 知识图 / 治理卡）
      hydrateAgentLayer(body, ctx, D, S);
    }).catch(function () {
      if (!body || !body.parentNode) return;
      body.innerHTML = degradeHtml(null);
    });
  }

  /* ============================================================
   * 七、登记（自注册，不改 app.js / 总导航）
   * ============================================================ */

  if (!window.SceneRegistry || typeof window.SceneRegistry.register !== "function") {
    console.error("[s05] window.SceneRegistry 缺失：scenes/registry.js 必须在本文件之前加载");
    return;
  }

  window.SceneRegistry.register({
    id: SCENE_ID,
    label: "S5 · 出货与质量追溯",
    title: "S5 · 出货与质量追溯",
    sub: "客诉 15 分钟定位批次/工序/设备 · 同批库存影响面 · 8D 会签闭环",
    icon: "truck",
    group: window.SceneRegistry.GROUP_NAME,
    order: 50,
    // spec-v1.md §4：S5 面向 总经理 / 业务 / 质量 / 制造（研发不参与出货追溯）。
    // 五访客以治理 key（sales / manufacturing / qa / gm）命中，故两套写法都列。
    roles: ["gm", "biz", "qa", "mfg", "sales", "manufacturing"],
    render: render,
  });
})();
