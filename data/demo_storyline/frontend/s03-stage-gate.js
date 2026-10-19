/**
 * S3 阶段门场景数据（T11 交付物 · 契约位置 data/demo_storyline/frontend/s03-stage-gate.js）
 *
 * 定位：本文件是 S3 屏「阶段门记分卡」的**场景自有派生配置**（非 T02 剧情数据副本）。
 *   - 阶段门阈值 / 门禁机械判定规则属于场景排片口径，登记在 frontend/ 下，随场景版本走；
 *   - 记分卡的四个数（良率 / CPK / 直通率 / 问题闭环数）由场景屏从 T02 剧情对象
 *     （events / sales_orders / work_orders / inspections）**Web 侧现算**，不在此文件静态固化。
 *
 * 装载说明：本文件按 registry.json 的 scene_data_dir 契约落位，但在当前 T04 装载层下
 *   浏览器不可直接取用（`/demo-ui/` 只挂载 ui 目录，frontend/ 未挂载）——详见
 *   src/mfg_agents/v2/demo/ui/scenes/s03-stage-gate.js 顶部「镜像常量」注释与 T11 备注。
 *   tests/demo/test_s03.py 校验本文件与场景屏镜像常量严格一致，避免两处漂移。
 *
 * 词表：人名取自 tools/demo_gen/wordlist.json，全部虚构。
 */
(function () {
  "use strict";

  window.SCENE_DATA = window.SCENE_DATA || {};
  window.SCENE_DATA["s03"] = {
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
      "title": "门禁准则问答（kbqa 回放）",
      "source": "faqs.jsonl · DEMO-FAQ-007（Go 的四项条件）",
      "degrade": "制度问答回放未就绪，不展示编造答案"
    }
  };
})();
