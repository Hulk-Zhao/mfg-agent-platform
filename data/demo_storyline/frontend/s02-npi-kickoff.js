/**
 * S2 立项 · 场景数据（T10 交付物，**权威副本**）
 *
 * 数据契约：`data/demo_storyline/frontend/registry.json` → scenes[s02].data。
 * 本文件只放**声明式**内容：
 *   - refs       主线对象引用（"看谁"）；
 *   - rules      判定阈值与识别标记（不许把魔法数字藏在屏文件里）；
 *   - cards      五张立项材料卡的展示元信息（label / tone / unit / hint）；
 *   - caseView   案例检索的字段映射与脱敏声明；
 *   - signoff    立项会签槽位中文名与留痕说明；
 *   - sourceNote 口径来源声明。
 * **一切业务数值都不在这里**——数值一律由屏文件从 T04 装载层
 * （`GET /api/v2/demo/objects/<对象>`）现算，避免"界面数字有两份来源"。
 *
 * 挂载点：`window.SCENE_DATA["s02"]`（SceneKit.sceneData("s02") 的唯一读取入口）。
 *
 * 块标记 `// >>> SCENE_DATA` … `// <<< SCENE_DATA` 供 tests/demo/test_s02.py 提取，
 * 并与屏文件内的 INLINE_SCENE_DATA 兜底镜像逐值深比对（两处漂移即测试变红）。
 * 块内必须是**纯 JSON**（不得出现注释、尾逗号、函数），否则测试提取会失败。
 */
(function () {
  "use strict";

  if (!window.SCENE_DATA || typeof window.SCENE_DATA !== "object") {
    window.SCENE_DATA = {};
  }

  // >>> SCENE_DATA
  window.SCENE_DATA["s02"] = {
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
      "title": "立项案例检索智能体（检索过程回放）",
      "fallback": "回放未就绪时回落关键词检索（现有关键词版保留为降级路径）",
      "degrade": "检索智能体回放未就绪，已回落关键词检索"
    }
  };
  // <<< SCENE_DATA
})();
