/**
 * S1 接单评审 · 场景数据（口径字典）· T07 交付物
 *
 * 定位：`window.SCENE_DATA["s01"]` —— 本屏的**权威副本**。它只装「怎么算、怎么标、
 * 出处在哪」这类**已声明口径**，**不装任何业务数字**：订单量、ATP 三来源、报价三分量、
 * 会签槽位全部在运行时经 `SceneKit.story()` 从 T04 装载层（故事数据集）取，
 * 屏内不落一个散数（T07 验收纪律）。
 *
 * 双重副本的原因（与 S3/S4 屏同一处理）：
 *  - 浏览器只能访问 `/demo-ui` 静态目录，读不到仓库内的 registry.json；
 *  - `/demo-story` 挂载点（registry.json → api.sceneAsset）虽已在 api.py 落地，
 *    但 index.html 尚未引入本文件，故 `window.SCENE_DATA["s01"]` 运行时可能缺位；
 *  - 因此 `scenes/s01-intake.js` 内联一份**同构镜像** `INLINE_SCENE_DATA`，缺位时顶上，
 *    并在 `tests/demo/test_s01.py` 里与本文件逐值深比对——任一侧漂移即变红。
 *  两处 JSON 块保持逐字符一致，便于机器比对与人工复核。
 */

(function () {
  "use strict";

  const SCENE_DATA_OBJECT = {
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
      "title": "接单制度问答（kbqa 回放）",
      "source": "faqs.jsonl · DEMO-FAQ-011（L1 命中直答，channel=faq）",
      "degrade": "制度问答回放未就绪，不展示编造答案"
    }
  };

  window.SCENE_DATA = window.SCENE_DATA || {};
  window.SCENE_DATA["s01"] = SCENE_DATA_OBJECT;
})();
