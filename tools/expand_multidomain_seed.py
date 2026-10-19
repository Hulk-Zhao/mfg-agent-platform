"""多模块/多职能域案例扩充：采购供应链 / 客服售后 / 生产 / 管理 / 跨部门 / 设计 / 供应商 共 21 条。

为什么要有这个脚本：
- 现状案例只长在"制造案例检索"一个模块，且集中在 SMT 段；采购/客服/生产/管理/跨部门等
  职能域几乎空白，导致"换个岗位搜同类问题"演示不出差异化，也演示不了跨部门案例。
- 与 ``tools/expand_segment_seed.py`` 同法：内容按真实职能场景写死，脚本只做**幂等拼装**。
- 每条引用的本体码必须已在 ``src/mfg_agents/v2/data/ontology/*.yaml`` 注册，否则会重演
  "语料涨了、本体没涨"。

幂等：按 ``case_id`` 覆盖写；已存在的 ``case_id`` 不重复添加。
用法：.venv/Scripts/python.exe tools/expand_multidomain_seed.py
"""
from __future__ import annotations

import json
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "case_seed.json"

D_PROC = [{"dim": "dept", "value": "procurement", "effect": "allow"}]
D_SVC = [{"dim": "dept", "value": "customer_service", "effect": "allow"}]
D_MFG = [{"dim": "dept", "value": "manufacturing", "effect": "allow"}]
D_MGT = [{"dim": "dept", "value": "management", "effect": "allow"}]
OPEN = []  # 全员开放（脱敏知识层）

#: (case_id, category, subcategory, title, summary, phenomenon, root_cause,
#:  countermeasure, defect_mode_code, root_cause_code, plant, line_code,
#:  owner_dept, source_ticket_type, source_ticket_id, author_role, acl)
ROWS = [
    # ---------- 采购 / 供应链 职能经验（限 dept:procurement） ----------
    ("RC-SCM-2026-0001", "role_case", "sourcing_experience",
     "采购经验：安全库存失守致缺料停线的复盘",
     "把安全库存从“固定值”改为按需求波动动态重算，避免长交期料缺料停线。",
     "长交期进口料安全库存按历史均值设置，需求上浮后两周内耗尽，产线停线 6 小时。",
     "安全库存静态设置，未随需求波动与供应商交期变化动态调整。",
     "安全库存按需求波动与交期动态重算；关键料设双源；缺口日清到人。",
     "DM-SCM-401", "RC-SCM-031", "P1", "", "procurement", "manual", "RC-2026-1001", "procurement", D_PROC),
    ("RC-SCM-2026-0002", "role_case", "sourcing_experience",
     "采购经验：供应商交期延迟的预警与双周评",
     "以产能与交期双周评替代“出事才催”，把交期风险提前发现。",
     "某供应商连续两批延期 5-8 天，靠催料才发现，已影响两条线排产。",
     "供应商产能与交期无例行评估，风险被动发现。",
     "建立产能与交期双周评；风险供应商提前备货或启动二供。",
     "DM-SCM-402", "RC-SCM-032", "P1", "", "procurement", "manual", "RC-2026-1002", "supply_chain_manager", D_PROC),
    ("RC-SCM-2026-0003", "role_case", "sourcing_experience",
     "采购经验：特采让步接收的评审要点",
     "特采不是“签字放行”，必须同时有替代料验证与风险背书。",
     "某批来料尺寸超差走特采放行，未做替代料验证，后道装配不良 3%。",
     "特采评审只走签字，未评估替代料与后道风险。",
     "特采评审强制替代料验证 + 后道风险评估；高风险特采需质量会签。",
     "DM-SCM-403", "RC-SCM-033", "P1", "", "procurement", "manual", "RC-2026-1003", "procurement_manager", D_PROC),

    # ---------- 客服 / 售后 职能经验（限 dept:customer_service） ----------
    ("RC-SVC-2026-0001", "role_case", "service_experience",
     "客服经验：客诉升级口径不一导致的对客不一致",
     "统一客诉升级话术与责任人，避免“上次不是这么说的”。",
     "同一客诉两次回复口径不一致，客户质疑内部管理，投诉升级。",
     "客诉升级无统一口径与责任人，不同岗位各自答复。",
     "客诉升级统一口径与责任人；对外答复需经质量确认；口径模板受控。",
     "DM-SVC-501", "RC-SVC-036", "P1", "", "customer_service", "manual", "RC-2026-2001", "customer_service", D_SVC),
    ("RC-SVC-2026-0002", "role_case", "service_experience",
     "客服经验：售后返修率偏高的根因回溯方法",
     "把返修数据按失效模式聚合，才能找到真根因而不是看总量。",
     "售后返修率由 1.2% 升至 2.8%，按总量排查无果。",
     "返修数据未按失效模式聚合，缺根因回溯。",
     "返修数据按失效模式聚合回溯；高频模式触发 8D；闭环回报客服。",
     "DM-SVC-502", "RC-SVC-038", "P1", "", "customer_service", "manual", "RC-2026-2002", "customer_service", D_SVC),
    ("RC-SVC-2026-0003", "role_case", "service_experience",
     "客服经验：售后备件安全库存的设定",
     "按返修时效承诺反推备件安全库存，避免“等件停机”。",
     "某型号备件缺货，售后返修周期由 3 天拉长到 12 天。",
     "售后备件无安全库存，按订单临时采购。",
     "按返修时效反推备件安全库存；关键件设最低储备并月度复核。",
     "DM-SVC-503", "RC-SVC-037", "P1", "", "customer_service", "manual", "RC-2026-2003", "sales", D_SVC),

    # ---------- 生产 职能经验（限 dept:manufacturing） ----------
    ("RC-MFG-2026-0001", "role_case", "production_experience",
     "生产经验：插件段人为漏插的防错手法",
     "用“扫描 + 清单核对 + 首件”三件套，把漏插挡在工位内。",
     "插件段漏插一颗滤波电容流入整机测试，返工 120 台。",
     "上料环节无防错校验，靠人眼核对，错漏无法拦截。",
     "上料扫描防错 + 元件清单核对 + 首件确认三件套；100% 目视复核。",
     "DM-DIP-102", "RC-PROC-SUP-003", "P1", "DIP-2", "manufacturing", "manual", "RC-2026-3001", "manufacturing", D_MFG),
    ("RC-MFG-2026-0002", "role_case", "production_experience",
     "生产经验：换型清线与首件确认的节拍管理",
     "换型超时多因清线不彻底，把清线清单化即收敛节拍。",
     "换型平均耗时 55 分钟，超目标 20 分钟，多次因残留旧料返工。",
     "换型清线无清单，残留旧型号物料导致混型返工。",
     "清线清单化 + 首件核对 + 换型节拍看板；混型零容忍。",
     "DM-PACK-203", "RC-PACK-030", "P1", "PKG-1", "manufacturing", "manual", "RC-2026-3002", "manufacturing", D_MFG),
    ("RC-MFG-2026-0003", "role_case", "production_experience",
     "生产经验：装配紧固顺序防漏拧的一线手法",
     "扭矩枪加计数 + 顺序图示，一线自查即可防漏拧。",
     "抽检发现 4 台整机漏拧螺钉，2 台紧固顺序与工艺不符。",
     "紧固顺序未图示化，且无计数校验，漏拧靠抽检才发现。",
     "顺序图示化 + 扭力枪计数防漏拧 + 首件确认顺序。",
     "DM-ASSY-302", "RC-ASSY-048", "P1", "ASSY-2", "manufacturing", "manual", "RC-2026-3003", "manufacturing", D_MFG),

    # ---------- 管理 / 项目 职能经验（限 dept:management） ----------
    ("RC-MGT-2026-0001", "role_case", "management_experience",
     "管理经验：跨部门资源冲突的协调机制",
     "跨部门冲突用“接口责任人 + 升级时限”机制化，不靠人情协调。",
     "新品导入期研发与生产争抢试制资源，节点延期两周。",
     "跨部门接口职责不清，冲突无升级时限，靠临时协调。",
     "明确接口责任人与升级时限；争议 24 小时内升级到项目办。",
     "DM-XD-601", "RC-XD-061", "P1", "", "management", "manual", "RC-2026-4001", "project_manager", D_MGT),
    ("RC-MGT-2026-0002", "role_case", "management_experience",
     "管理经验：质量与交付冲突时的决策原则",
     "质量红线不可交易，交付风险必须显式决策留痕。",
     "交付冲刺期产线要求放宽外观判定，质量拒绝，双方僵持。",
     "质量与交付冲突无既定决策原则，现场各自为政。",
     "确立“质量红线不可交易 + 交付风险显式决策留痕”原则；高风险需总经理决策。",
     "DM-XD-602", "RC-XD-062", "P1", "", "management", "manual", "RC-2026-4002", "gm", D_MGT),

    # ---------- 跨部门协同案例（全员脱敏） ----------
    ("XD-2026-0001", "cross_dept_case", "cross_dept",
     "跨部门：SMT 停线应急的多部门协同复盘",
     "停线应急靠“谁先发现谁拉群”不可靠，需固定应急角色与联动看板。",
     "SMT-1 线突发设备故障停线 4 小时，生产/设备/质量三方响应错位。",
     "停线应急无固定角色与联动机制，信息传递靠临时拉群。",
     "建立停线应急联动看板与固定角色；响应时限纳入考核。",
     "DM-XD-601", "RC-XD-061", "P1", "SMT-1", "quality", "8d", "XD-2026-0001", "qa_manager", OPEN),
    ("XD-2026-0002", "cross_dept_case", "cross_dept",
     "跨部门：缺料联动不及时致停线",
     "缺料信息只在采购侧流转，生产看不到缺口，联动滞后。",
     "某料缺料预警发出 3 天后才到生产计划，导致停线 8 小时。",
     "缺料缺口信息未在采购/生产/计划间日清联动。",
     "缺料缺口日清联动到生产与计划；缺料预警与排产联动。",
     "DM-XD-602", "RC-XD-062", "P1", "", "quality", "8d", "XD-2026-0002", "pqe", OPEN),
    ("XD-2026-0003", "cross_dept_case", "cross_dept",
     "跨部门：客诉与供应整改联动断点",
     "客诉指向来料，但客服与供应商整改之间没有闭环接口。",
     "客户投诉焊盘氧化，客服结案后供应商 8D 未跟进，同类问题复现。",
     "客诉与供应商整改之间无联动接口，整改闭环断裂。",
     "客诉指向来料时自动触发供应商整改链路；整改结果回流程客服。",
     "DM-XD-603", "RC-XD-062", "P1", "", "quality", "8d", "XD-2026-0003", "sqe", OPEN),
    ("XD-2026-0004", "cross_dept_case", "cross_dept",
     "跨部门：特采 + 质量 + 生产的三方联动决策",
     "特采影响后道，必须三方会签而非采购单方签字。",
     "来料特采放行未通知生产与质量，后道装配不良 3%，责任不清。",
     "特采决策未联动生产与质量，后道风险无背书。",
     "特采三方（采购/质量/生产）会签；高风险特采需会签留痕。",
     "DM-SCM-403", "RC-SCM-033", "P1", "", "quality", "8d", "XD-2026-0004", "quality_director", OPEN),

    # ---------- 设计缺陷案例（全员脱敏） ----------
    ("QC-DES-2026-0501", "quality_case", "design_defect",
     "设计：电源模块设计余量不足致高温降额失效",
     "电源模块降额余量不足，高温环境输出跌落，量产高不良。",
     "高温 60℃ 老化时电源模块输出跌落 8%，批量失效。",
     "设计余量不足，未按最高工作温度留足降额。",
     "重新评估余量并加大降额；高温验证纳入设计评审门禁。",
     "DM-DES-081", "RC-DES-051", "P1", "", "rd_engineering", "8d", "DES-2026-0501", "rd_engineering", OPEN),
    ("QC-DES-2026-0502", "quality_case", "design_defect",
     "设计：器件降额不足致长期可靠性失效",
     "电容降额不足，长期高温下容量衰减加速，售后返修升高。",
     "售后返修分析显示电容容量衰减，实测长期工作温度超出降额裕度。",
     "器件降额设计不足，未按可靠性曲线校核。",
     "关键器件按可靠性曲线校核降额；设计评审强制降额清单。",
     "DM-DES-082", "RC-DES-052", "P1", "", "rd_engineering", "8d", "DES-2026-0502", "rd_engineering", OPEN),
    ("QC-DES-2026-0503", "quality_case", "design_defect",
     "设计：DFM 评审缺失致量产可制造性差",
     "设计未做 DFM 评审，量产出现钢网与器件间距冲突，良率低。",
     "量产导入后印刷连锡高发，根因为器件间距与钢网冲突。",
     "设计阶段缺 DFM 评审，可制造性问题流到量产。",
     "DFM 评审纳入设计门禁；关键工艺与制造工程师会签。",
     "DM-DES-083", "RC-DES-053", "P1", "", "rd_engineering", "8d", "DES-2026-0503", "product_engineering", OPEN),

    # ---------- 供应商质量案例（全员脱敏） ----------
    ("QC-SQ-2026-0008", "quality_case", "supplier_quality",
     "供应商：二供切换未验证致批次波动",
     "二供未做等价验证即量产，批次波动引发制程不良。",
     "切换二供后该料批次波动，SMT 虚焊比例由 0.4% 升至 2.4%。",
     "二供切换未做等价验证与首批试产确认。",
     "二供切换强制等价验证与首批试产；ECN 管控变更。",
     "DM-SCM-404", "RC-SCM-034", "P1", "", "quality", "iqc", "IQC-2026-0008", "sqe", OPEN),
    ("QC-SQ-2026-0009", "quality_case", "supplier_quality",
     "供应商：库存呆滞超期料的处置",
     "超期料未定期复检，复用时性能漂移引发不良。",
     "仓库呆滞料超期 8 个月被复用，焊盘氧化导致虚焊。",
     "库存周转监控缺失，超期料未复检即复用。",
     "超期料定期复检与呆滞预警；复用前性能复检。",
     "DM-SCM-405", "RC-SCM-035", "P1", "", "quality", "iqc", "IQC-2026-0009", "sqe", OPEN),
    ("QC-SQ-2026-0010", "quality_case", "supplier_quality",
     "供应商：特采让步接收后批量隐患",
     "特采放行的批次未隔离标识，与合格料混用产生批量隐患。",
     "特采批未单独标识，与合格料混用，后道不良扩散。",
     "特采批未隔离标识与追溯，混用后无法拦截。",
     "特采批强制隔离标识与批次追溯；优先消耗且全程留痕。",
     "DM-SCM-403", "RC-SCM-033", "P1", "", "quality", "8d", "8D-2026-0010", "sqe", OPEN),
]

FIELDS = ("case_id", "category", "subcategory", "title", "summary", "phenomenon",
          "root_cause_text", "countermeasure_text", "defect_mode_code",
          "root_cause_code", "plant", "line_code", "owner_dept",
          "source_ticket_type", "source_ticket_id")


def build() -> dict:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    existing = {str(c.get("case_id")) for c in data["cases"]}
    added = 0
    for row in ROWS:
        record = dict(zip(FIELDS, row[:len(FIELDS)]))
        author_role = row[len(FIELDS)]
        acl = row[len(FIELDS) + 1]
        if record["case_id"] in existing:
            continue
        record.update({
            "case_code": record["case_id"],
            "cls_rank": 0,
            "status": "active",
            "content_version": 1,
            "is_export_controlled": 0,
            "published_at": "2026-09-20",
            "last_verified_at": "2026-10-10",
            "verified": 1,
            "citation_count": 0,
            "author_role": author_role,
            "acl": acl,
            "evidence": [],
        })
        data["cases"].append(record)
        existing.add(record["case_id"])
        added += 1
    data["note"] = (data.get("note", "")
                    + " 多职能域案例（采购供应链/客服售后/生产/管理/跨部门/设计/供应商）"
                      "由 tools/expand_multidomain_seed.py 追加。").strip()
    SEED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"added": added, "total": len(data["cases"])}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
