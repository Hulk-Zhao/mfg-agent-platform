/**
 * S6 采购执行场景数据（T08 交付物 · 契约位置 data/demo_storyline/frontend/s06-procurement.js）
 *
 * 定位：本文件是 S6 屏「采购执行」的**场景自有派生配置**（非 T02 剧情数据副本）。
 *   - 缺料预警口径 / 工排需料日 / 升级链阈值 / 指派岗位-人名属于场景排片口径，随场景版本走；
 *   - PO 清单、供应商、缺口量、留痕时间轴全部由场景屏从 T02 剧情对象
 *     （purchase_orders / suppliers / materials / work_orders / sales_orders / events）
 *     **Web 侧现算**，不在此文件静态固化。
 *
 * 装载说明：本文件按 registry.json 的 scene_data_dir 契约落位；`/demo-story/{file}`
 *   挂载点已由 T10 落地，但 `index.html` 未引入 frontend/sNN-*.js 的 script 标签，
 *   浏览器运行时取不到本文件——屏内因此内联一份同构兜底 `INLINE_SCENE_DATA`，
 *   并由 tests/demo/test_s06.py 逐值深比对两者，任一侧改动即变红。
 *
 * 词表：人名取自 tools/demo_gen/wordlist.json，全部虚构。
 */
(function () {
  "use strict";

  window.SCENE_DATA = window.SCENE_DATA || {};
  window.SCENE_DATA["s06"] = {
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
      { "level": 1, "owner_role": "采购员", "threshold_days": 0 },
      { "level": 2, "owner_role": "采购主管", "threshold_days": 2 },
      { "level": 3, "owner_role": "供应链经理", "threshold_days": 5 }
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
    "object_source": ["purchase_orders", "suppliers", "materials", "work_orders", "sales_orders", "events"]
  };
})();
