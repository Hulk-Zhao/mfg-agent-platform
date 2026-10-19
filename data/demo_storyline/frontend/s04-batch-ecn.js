/**
 * S4 批量监控与 ECN 场景数据（T09 交付物 · 契约位置 data/demo_storyline/frontend/s04-batch-ecn.js）
 *
 * 定位：本文件是 S4 屏的**场景自有派生数据**单一事实源，不是 T02 剧情数据的副本。
 *   - 公共故事数据（spc_series / events / work_orders / lots / boms / sales_orders /
 *     materials / equipment）一律经 `SceneKit.story('<object>')` 从 T04 装载层读取，
 *     本文件不复抄任何数字快照；
 *   - 本文件只放场景排片口径：SPC 控制限口径、判异处置链、ECN 影响面四象限的
 *     对象归集与推算出处、会签槽位与人名。
 *
 * 装载说明：本文件按 registry.json 的 scene_data_dir 契约落位，可经 `/demo-story/{file}`
 *   挂载点（T10 落地）由 HTTP 取到；但演示页面当前不自动注入场景数据脚本，屏内因此
 *   内联一份同构镜像兜底（见 `src/mfg_agents/v2/demo/ui/scenes/s04-batch-ecn.js`）。
 *   `tests/demo/test_s04.py` 校验本文件与场景屏镜像常量严格一致，避免两处漂移。
 *
 * 数据出处（逐条可回溯 T02 数据包）：
 *   - SPC 控制限口径：events 中 DEMO-EV-0013 payload（target=245.0 / tolerance=6.0）；
 *   - 判异处置链与留痕事件：DEMO-EV-0013/0014/0015（判异 1）、DEMO-EV-0019/0020（判异 2）；
 *   - 判异会签槽位：DEMO-EV-0014 payload.slots=[pqe, pe]、result=处置中；
 *   - ECN 影响面：DEMO-EV-0016 payload（impact_wos / impact_stock_lots /
 *     impact_transit_lots / impact_open_lines）；
 *   - ECN 会签与生效：DEMO-EV-0017（hitl / approved）、DEMO-EV-0018（切换执行，MBOM v1.1 生效）；
 *   - 60% 进度点：spec-v1.md §1 支线 A「批量进行到 60% 时」与 §3 节点 #10–#11。
 *
 * 词表：人名取自 tools/demo_gen/wordlist.json，全部虚构。
 */
(function () {
  "use strict";

  window.SCENE_DATA = window.SCENE_DATA || {};
  window.SCENE_DATA["s04"] = {
    "scene": "s04",
    "title": "S4 · 批量监控与 ECN",
    "sub": "30 天 SPC 判异处置会签 · 支线 A 换料影响面四象限",
    "spec_ref": "spec-v1.md §3 节点 #8–#12 · §4 五角色视图（gm/rd/qa/mfg）· §5 界面数字台账",
    "object_source": [
      "spc_series",
      "events",
      "work_orders",
      "lots",
      "boms",
      "sales_orders",
      "materials",
      "equipment"
    ],
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
    "disposition_chain": [
      "拉群",
      "取证",
      "方案",
      "会签",
      "闭环"
    ],
    "disposition_status_labels": {
      "done": "已完成",
      "doing": "进行中",
      "absent": "未留痕"
    },
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
        "event_refs": [
          "DEMO-EV-0013",
          "DEMO-EV-0014",
          "DEMO-EV-0015"
        ],
        "steps": [
          "done",
          "absent",
          "absent",
          "doing",
          "done"
        ],
        "step_evidence": {
          "拉群": "DEMO-EV-0013（alert / kind=spc，判异推送即拉起处置群，演示口径）",
          "会签": "DEMO-EV-0014（hitl，槽位 pqe / pe，结果处置中）",
          "闭环": "DEMO-EV-0015（alert / kind=closed，判异处置闭环）"
        },
        "signoff_slots": [
          "pqe",
          "pe"
        ],
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
        "event_refs": [
          "DEMO-EV-0019",
          "DEMO-EV-0020"
        ],
        "steps": [
          "done",
          "absent",
          "absent",
          "absent",
          "done"
        ],
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
      "event_refs": [
        "DEMO-EV-0016",
        "DEMO-EV-0017",
        "DEMO-EV-0018"
      ],
      "signoff_slots": [
        "rd",
        "pe",
        "pqe"
      ],
      "signoff_result": "approved",
      "quadrant_titles": {
        "wos": "在制工单",
        "stock_lots": "库存批次",
        "transit_lots": "在途批次",
        "open_lines": "未发订单行"
      },
      "impact": {
        "wos": [
          "DEMO-WO-002",
          "DEMO-WO-003"
        ],
        "stock_lots": [
          "DEMO-LOT-003"
        ],
        "transit_lots": [
          "DEMO-LOT-004"
        ],
        "open_lines": [
          {
            "order_id": "DEMO-SO-001",
            "material_id": "DEMO-MAT-5001",
            "qty": 5000
          }
        ]
      },
      "impact_counts": {
        "wos": 2,
        "stock_lots": 1,
        "transit_lots": 1,
        "open_lines": 1
      },
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
    },
    "agent_layer": {
      "replays": {
        "agent": "s04_spc_anomaly",
        "cases": "s04_spc_similar_cases"
      },
      "title": "SPC 判异智能体（判异 → 取证 → 相似不良）",
      "source": "SPC 序列 / 案例库（控制限口径回指判异事件载荷）",
      "degrade": "判异智能体回放未就绪，不展示编造结论"
    }
  };
})();
