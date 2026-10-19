/**
 * S5 场景数据 · 出货与质量追溯（T06 交付物 · 数据契约）
 *
 * 定位：本文件是 T06 场景屏的**派生数据单一事实源**，导出挂到
 * `window.SCENE_DATA["s05"]`，由场景屏 `scenes/s05-trace.js` 读取。
 *
 * 三条纪律（与 S3/S4 侧同构，便于六个场景屏互相对照）：
 *  1. 只放**派生/注解**数据：SVG 图谱坐标与连线关系、追溯步骤文案、会签槽位名词典、
 *     定位时限口径。公共故事数据（事件 / 批次 / 工单 / 设备 / 检验批 / 订单 / 客户）
 *     一律经 `SceneKit.story(objectName)` 从 T04 装载层读取，本文件**不复抄任何数字快照**。
 *  2. 本文件引用的对象 id（DEMO-EV-0024 / 0025 / 0026、DEMO-LOT-002、DEMO-WO-001、
 *     DEMO-EQ-006、op_no 30、DEMO-IB-OQC-001、DEMO-CC-001、DEMO-8D-001）在
 *     `data/demo_storyline/*.jsonl` 中真实存在，由 `tests/demo/test_s05.py` 逐条回溯校验。
 *  3. 口径出处一律回指 `spec-v1.md`（§3 主线 #13–#15 · §4 支线 B · §5 界面数字台账）。
 *
 * 浏览器侧取用：本目录已由 `demo/api.py` 挂到 `/demo-story`（T10 追加），契约文件可被
 * `GET /demo-story/s05-trace.js` 直接取到；但本轮各场景屏（T06/T09/T11）统一在屏内保留
 * 一份同构镜像 `INLINE_SCENE_DATA`，本文件即该镜像的**版本上游**。两者由
 * `tests/demo/test_s05.py` 深比对（逐值相等），任一侧漂移即变红。
 *
 * 字面量形态：本文件的对象字面量是**纯 JSON**（键加引号、无注释、无尾逗号），
 * 以便 Python 侧用 json.loads 做深比对——修改时保持该形态。
 */
(function () {
  "use strict";

  window.SCENE_DATA = window.SCENE_DATA || {};

  window.SCENE_DATA["s05"] = {
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
})();
