"""电池/整机五段案例扩充：电芯组包 / 测试 / 老化 / 整机组装 / 包装 共 30 条（每段 6 条）。

为什么要有这个脚本：
- 产线已扩到"电芯组包 → 测试 → 老化 → 整机组装 → 包装"多段，但案例库此前没有
  电芯组包与老化两段的案例，测试/整机/包装虽有也偏薄，跨段追溯演示不出来。
- 与 ``tools/expand_segment_seed.py`` / ``tools/expand_multidomain_seed.py`` 同法：
  内容按真实工艺写死，脚本只做**幂等拼装**，不生成占位文案。
- 每条引用的本体码必须已在 ``src/mfg_agents/v2/data/ontology/*.yaml`` 注册
  （本次同步新增 电芯组包/老化/测试 三域 + 整机/包装补充码）。

幂等：按 ``case_id`` 覆盖写；已存在的 ``case_id`` 不重复添加。
用法：.venv/Scripts/python.exe tools/expand_battery_segment_seed.py
"""
from __future__ import annotations

import json
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "case_seed.json"

#: (case_id, category, subcategory, title, summary, phenomenon, root_cause,
#:  countermeasure, defect_mode_code, root_cause_code, plant, line_code,
#:  owner_dept, source_ticket_type, source_ticket_id, author_role)
ROWS = [
    # ================= 电芯组包段 · 6 条 =================
    ("QC-CELL-2026-0601", "quality_case", "process_defect",
     "电芯组包：配组容量偏差超标致整组一致性差",
     "配组时容量分档过宽，模组内电芯容量离散大，充放电一致性差；分容重排后恢复。",
     "模组充放电测试一致性偏差 6%，整组可用容量低于标称 4%。",
     "配组筛选不当，容量分档区间过宽，同组电芯容量离散大。",
     "按分容结果细化分档并重排配组；同组容量极差 ≤2%；配组数据入追溯。",
     "DM-CELL-701", "RC-CELL-001", "P1", "CELL-1", "process", "process", "PD-2026-0601", "pe"),
    ("QC-CELL-2026-0602", "quality_case", "process_defect",
     "电芯组包：内阻不一致致充放电不均",
     "组包未按内阻分档，同组内阻离散导致充放电不均与局部温升；内阻分档后恢复。",
     "模组充电时局部电芯温升偏高 8℃，内阻离散度 12%。",
     "内阻未纳入配组分档，同组内阻离散大。",
     "按内阻分档配组；同组内阻极差 ≤5%；配组前复测内阻入档。",
     "DM-CELL-702", "RC-CELL-002", "P1", "CELL-1", "process", "process", "PD-2026-0602", "pe"),
    ("QC-CELL-2026-0603", "quality_case", "process_defect",
     "电芯组包：极耳焊接虚焊致模组内阻偏高",
     "极耳焊接能量不足产生虚焊，模组内阻偏高、DOA 上升；参数锁定与拉力抽检后恢复。",
     "模组内阻较基准高 15%，抽样解剖见极耳焊点未完全熔合。",
     "极耳焊接能量不足，参数未锁定，焊点熔合不充分。",
     "焊接参数锁定并做极耳拉力抽检（每班 5 件）；焊接能量监控。",
     "DM-CELL-703", "RC-CELL-003", "P1", "CELL-1", "process", "8d", "8D-2026-0603", "pe"),
    ("QC-CELL-2026-0604", "quality_case", "process_defect",
     "电芯组包：BMS 采线接错致电压采集异常",
     "采线接错导致 BMS 电压采集异常、报警误触发；采线防错与首件校验后恢复。",
     "BMS 报单体电压采集异常 3 处，实测采线顺序与图纸不符。",
     "采线环节无防错校验，接错未被拦截。",
     "采线加防错标识与首件校验；采线顺序图固化；通电前做采集自检。",
     "DM-CELL-704", "RC-CELL-004", "P1", "CELL-2", "process", "8d", "8D-2026-0604", "pe"),
    ("QC-CELL-2026-0605", "quality_case", "process_defect",
     "电芯组包：极耳折弯挤压致绝缘风险",
     "装配工装限位不当挤压极耳，绝缘间距不足存在短路风险；工装优化后恢复。",
     "解剖见极耳折弯处绝缘膜破损，耐压测试临界。",
     "装配工装挤压极耳，限位设计不当。",
     "优化工装限位并加软质护具；极耳外观 100% 目检；耐压复测。",
     "DM-CELL-705", "RC-CELL-005", "P1", "CELL-2", "process", "8d", "8D-2026-0605", "pe"),
    ("QC-CELL-2026-0606", "quality_case", "process_defect",
     "电芯组包：模组绝缘耐压不足",
     "模组绝缘处理不到位，耐压测试不合格；绝缘工艺固化与复测后恢复。",
     "模组耐压测试击穿，绝缘电阻低于限值 1 个数量级。",
     "模组绝缘工艺不良，绝缘膜贴装不到位。",
     "绝缘工艺固化（贴装+固化时间）；模组 100% 耐压复测；工艺参数入受控。",
     "DM-CELL-706", "RC-CELL-006", "P1", "CELL-2", "quality", "8d", "8D-2026-0606", "pqe"),

    # ================= 测试段 · 6 条 =================
    ("QC-TST-2026-0701", "quality_case", "process_defect",
     "测试：EOL 测试程序参数错误致批量误判",
     "测试程序参数设置错误导致良品被判不良，复测率飙升；程序版本受控后恢复。",
     "EOL 测试误判率 5%，良品被判不良，大量复测。",
     "测试程序参数未随产品变更更新，程序版本失管。",
     "测试程序版本受控并与产品版本绑定；变更走评审；首件确认测试结果。",
     "DM-TST-071", "RC-TST-001", "P1", "TEST-3", "quality", "process", "PD-2026-0701", "mqe"),
    ("QC-TST-2026-0702", "quality_case", "process_defect",
     "测试：测试阈值过紧致误判率高",
     "阈值未随器件批次差异校准，边界波动被判不良；判据分层校准后恢复。",
     "测试误判率 4%，边界器件大量复测。",
     "测试判据未随批次校准，阈值过紧。",
     "判据分层校准（内控/客控）；按批次复校阈值；复测确认机制。",
     "DM-TST-072", "RC-TST-002", "P1", "TEST-3", "quality", "process", "PD-2026-0702", "mqe"),
    ("QC-TST-2026-0703", "quality_case", "process_defect",
     "测试：绝缘耐压失效漏放",
     "耐压测试参数不当导致临界品放行，售后出现绝缘失效；参数验证与复测后恢复。",
     "售后反馈 2 起绝缘失效，回查耐压测试为临界放行。",
     "耐压测试参数设置不当，临界品被判合格。",
     "耐压参数验证并加严临界判定；定期做标准件校验；复测确认。",
     "DM-TST-075", "RC-TST-002", "P1", "TEST-3", "quality", "8d", "8D-2026-0703", "pqe"),
    ("QC-TST-2026-0704", "quality_case", "process_defect",
     "测试：ATE 针床接触不良致误测",
     "ATE 针床探针接触不良造成间歇误测，复测后判为夹具问题；针床点检后恢复。",
     "ATE 测试间歇报通信/开路，复测多为合格。",
     "ATE 针床探针接触不良，接触电阻偏高致误测。",
     "针床探针纳入点检与更换周期；加接触自检；复测确认机制。",
     "DM-TST-076", "RC-TST-001", "P1", "TEST-3", "equipment", "process", "PD-2026-0704", "pe"),
    ("QC-TST-2026-0705", "quality_case", "process_defect",
     "测试：测试项缺失致缺陷漏检",
     "测试程序缺漏关键测试项，缺陷流出到客户端；测试覆盖评审后补全。",
     "客户端反馈功能缺陷，回查测试程序缺该项测试。",
     "测试项缺失，测试覆盖评审不充分。",
     "补全测试项并做覆盖评审；测试项与设计验证项对齐；变更留痕。",
     "DM-TST-070", "RC-TST-003", "P1", "TEST-3", "quality", "8d", "8D-2026-0705", "mqe"),
    ("QC-TST-2026-0706", "quality_case", "process_defect",
     "测试：测试数据未追溯致复核困难",
     "测试数据未按件留存，异常复核时无法还原当时测试曲线；数据留存后恢复。",
     "客户要求提供测试记录，产线无法按件还原测试数据。",
     "测试数据未按件留存，仅在设备本地短期保存。",
     "测试数据按件留存并入追溯系统；保存期与追溯要求对齐；异常件自动留档。",
     "DM-TST-074", "RC-TST-002", "P1", "TEST-3", "quality", "manual", "RC-2026-0706", "mqe"),

    # ================= 老化段 · 6 条 =================
    ("QC-AGING-2026-0801", "quality_case", "process_defect",
     "老化：老化后容量衰减超标",
     "老化时长不足导致衰减未充分暴露，老化后容量衰减超标；时长与判据校准后恢复。",
     "老化后抽样容量衰减 7% 超限值 3%，判定为衰减超标。",
     "老化时长不足，衰减未充分暴露即放行。",
     "老化时长与判据校准；老化前后容量对比留存；超限自动拦截。",
     "DM-AGING-801", "RC-AGING-002", "P1", "AGING-1", "quality", "8d", "8D-2026-0801", "pqe"),
    ("QC-AGING-2026-0802", "quality_case", "process_defect",
     "老化：老化后内阻升高",
     "老化后内阻升高超限，反映电芯一致性或焊接问题；老化后复核后恢复。",
     "老化后内阻较老化前升高 12%，超限值 8%。",
     "老化时长不足且未做老化前后内阻对比。",
     "老化前后内阻对比复核；超限拦截并追溯至组包工段。",
     "DM-AGING-802", "RC-AGING-002", "P1", "AGING-1", "quality", "8d", "8D-2026-0802", "pqe"),
    ("QC-AGING-2026-0803", "quality_case", "process_defect",
     "老化：老化房温度不均致老化不充分",
     "老化房风道布局不合理，边角区域温度偏低，老化不充分漏检；风道调整后恢复。",
     "老化后边角区域产品衰减异常低（未充分老化），漏检率上升。",
     "老化房温度分布不均，局部温区偏低 8℃。",
     "调整风道与风机；多点温度验证；老化曲线定期验证。",
     "DM-AGING-803", "RC-AGING-001", "P1", "AGING-1", "engineering", "process", "PD-2026-0803", "pe"),
    ("QC-AGING-2026-0804", "quality_case", "process_defect",
     "老化：老化鼓包漏液",
     "老化过程出现电芯鼓包漏液，反映电芯来料或老化条件异常；老化后复核后恢复。",
     "老化后抽检发现 3 只电芯鼓包、1 只漏液。",
     "老化判据不合理，鼓包早期特征未被拦截；电芯来料一致性波动。",
     "老化后 100% 外观复核（鼓包/漏液）；来料一致性加严；异常批次追溯。",
     "DM-AGING-804", "RC-AGING-003", "P1", "AGING-2", "quality", "8d", "8D-2026-0804", "pqe"),
    ("QC-AGING-2026-0805", "quality_case", "process_defect",
     "老化：老化夹具接触不良致误判",
     "老化夹具接触不良造成间歇误判，复测合格；夹具点检与接触改善后恢复。",
     "老化测试中 3% 产品报异常，复测后判为夹具接触不良。",
     "老化夹具接触不良，接触电阻波动致误判。",
     "老化夹具点检与接触改善；加接触自检；异常自动复测。",
     "DM-AGING-802", "RC-AGING-003", "P1", "AGING-2", "equipment", "process", "PD-2026-0805", "pe"),
    ("QC-AGING-2026-0806", "quality_case", "process_defect",
     "老化：老化判据不合理致放行过松",
     "老化放行判据宽松，临界衰减品被放行；判据校准后恢复。",
     "售后反馈老化相关衰减异常，回查老化判据阈值过松。",
     "老化判据不合理，放行标准过松。",
     "老化判据按实测分布校准；临界品加严；判据变更走评审留痕。",
     "DM-AGING-801", "RC-AGING-003", "P1", "AGING-2", "quality", "manual", "RC-2026-0806", "mqe"),

    # ================= 整机组装段 · 6 条 =================
    ("QC-ASSY-2026-0901", "quality_case", "process_defect",
     "整机：线束接错致功能异常",
     "整机组装线束插接错误导致功能异常，作业指导未明确走向；固化后恢复。",
     "整机功能测试报异常，排查为线束插接错误。",
     "作业指导未明确线束走向与插接位，且无防错。",
     "线束走向与插接位图示化；加防错标识；通电前做通路自检。",
     "DM-ASSY-307", "RC-PROC-SOP-004", "P1", "ASSY-3", "production", "8d", "8D-2026-0901", "mqe"),
    ("QC-ASSY-2026-0902", "quality_case", "process_defect",
     "整机：结构件装配错位致外观间隙超差",
     "结构件装配错位造成外观间隙超差，公差累积放大偏差；治具修正后恢复。",
     "整机外观间隙 0.9mm 超限值 0.5mm，装配错位明显。",
     "装配公差累积超限，定位治具磨损放大偏差。",
     "做公差链分析并修正定位治具；加装配后间隙 100% 检测。",
     "DM-ASSY-308", "RC-ASSY-041", "P1", "ASSY-3", "production", "process", "PD-2026-0902", "pe"),
    ("QC-ASSY-2026-0903", "quality_case", "process_defect",
     "整机：螺钉滑牙（装配扭矩失控）",
     "扭力工具衰减造成螺钉滑牙，装配强度不足；扭力工具校准后恢复。",
     "螺钉滑牙不良率 0.28%，实测扭力低于设定 12%。",
     "扭力工具衰减未按期校准，扭矩失控。",
     "扭力工具月度校准并留曲线；每班抽测；偏差超限停机送修。",
     "DM-ASSY-302", "RC-ASSY-042", "P1", "ASSY-3", "production", "8d", "8D-2026-0903", "mqe"),
    ("QC-ASSY-2026-0904", "quality_case", "process_defect",
     "整机：接地电阻超标",
     "接地连接点工艺不到位，整机接地电阻超标；接地工艺固化后恢复。",
     "整机接地电阻 0.17Ω 高于限值 0.1Ω。",
     "接地工艺缺陷，连接点接触电阻偏高。",
     "接地工艺固化（去漆+力矩+涂层）；整机 100% 接地复测。",
     "DM-ASSY-303", "RC-ASSY-043", "P1", "ASSY-3", "quality", "8d", "8D-2026-0904", "pqe"),
    ("QC-ASSY-2026-0905", "quality_case", "process_defect",
     "整机：整机异响（装配干涉）",
     "整机装配后通电出现持续异响，结构件干涉；治具修正后恢复。",
     "整机通电后持续异响，拆解见内部支架与壳体干涉。",
     "装配公差累积导致结构件干涉异响。",
     "公差链分析并修正定位治具；装配后异响 100% 检测。",
     "DM-ASSY-301", "RC-ASSY-041", "P1", "ASSY-3", "production", "8d", "8D-2026-0905", "pe"),
    ("QC-ASSY-2026-0906", "quality_case", "process_defect",
     "整机：外观划伤",
     "整机转运防护不足导致外观划伤；防护升级后恢复。",
     "整机外观划伤不良率 1.9%，集中在转运环节。",
     "搬运防护不足，整机与工装直接接触磨损。",
     "转运工装加软质防护；外观划伤 100% 目检；抽检升级。",
     "DM-ASSY-304", "RC-ASSY-044", "P1", "ASSY-3", "production", "process", "PD-2026-0906", "mqe"),

    # ================= 包装段 · 6 条 =================
    ("QC-PKG-2026-1001", "quality_case", "process_defect",
     "包装：包装标识与实物不符",
     "出口包装唛头标识与实物型号不符，仓库发货前拦截；模板受控后恢复。",
     "出口批次唛头型号与实物不符，发货前复核拦截 120 箱。",
     "标签模板与产品版本未绑定，误用旧模板。",
     "标签模板与产品版本绑定受控；出口批次首件核对；复核留痕。",
     "DM-PACK-207", "RC-PACK-021", "P1", "PKG-2", "production", "process", "PD-2026-1001", "mqe"),
    ("QC-PKG-2026-1002", "quality_case", "process_defect",
     "包装：出货文件缺失",
     "随货出货文件（合格证/装箱单）缺失，客户清关受阻；清单校验后恢复。",
     "客户清关反馈缺少装箱单与合格证，延误清关 2 天。",
     "附件清单版本不一致，出货文件未随 BOM 变更同步。",
     "出货文件清单与 BOM 同步受控；装箱后文件核对并入称重复核环节。",
     "DM-PACK-208", "RC-PACK-022", "P1", "PKG-2", "production", "8d", "8D-2026-1002", "mqe"),
    ("QC-PKG-2026-1003", "quality_case", "process_defect",
     "包装：栈板与缓冲不匹配致运输磕碰",
     "栈板尺寸与缓冲方案不匹配，运输途中产品磕碰；方案评审后恢复。",
     "客户开箱反馈边角磕碰划伤率 2.8%。",
     "缓冲设计不足且栈板尺寸不符，运输振动下位移磕碰。",
     "缓冲与栈板方案联合评审并做跌落验证；按方案定量填充与固定。",
     "DM-PACK-204", "RC-PACK-028", "P1", "PKG-2", "process", "8d", "8D-2026-1003", "pe"),
    ("QC-PKG-2026-1004", "quality_case", "process_defect",
     "包装：条码不可读致出货停滞",
     "出口条码打印浓度不足不可读，出库扫码停滞；打印质量抽检后恢复。",
     "出库扫码失败率 5%，需人工录入，出货节拍下降。",
     "条码打印质量差，打印头未按期保养。",
     "打印浓度纳入日点检；打印头按期保养；每批抽测条码可读性。",
     "DM-PACK-206", "RC-PACK-026", "P1", "PKG-2", "production", "process", "PD-2026-1004", "mqe"),
    ("QC-PKG-2026-1005", "quality_case", "process_defect",
     "包装：混装致客户错发",
     "换型防错缺失导致 A/B 型号混装出货，客户错发投诉；防错后恢复。",
     "客户收货发现同箱混装 A/B 两型号，投诉并要求补发。",
     "型号切换防错缺失，换型后未清线。",
     "换型清线与首件核对；装箱前扫码比对型号；混装防错点检。",
     "DM-PACK-203", "RC-PACK-030", "P1", "PKG-2", "production", "8d", "8D-2026-1005", "mqe"),
    ("QC-PKG-2026-1006", "quality_case", "process_defect",
     "包装：静电袋密封不良致器件受潮",
     "静电袋封边密封不良，长途运输后器件受潮；封口参数管控后恢复。",
     "客户反馈开箱器件受潮，湿度卡变色，封边开裂。",
     "静电袋密封不良，封口参数未管控。",
     "封口参数管控并每班验证；增加密封测试与湿度卡；抽检升级。",
     "DM-PACK-205", "RC-PACK-029", "P1", "PKG-2", "process", "8d", "8D-2026-1006", "sqe"),
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
        if record["case_id"] in existing:
            continue
        record.update({
            "case_code": record["case_id"],
            "cls_rank": 0,
            "status": "active",
            "content_version": 1,
            "is_export_controlled": 0,
            "published_at": "2026-09-25",
            "last_verified_at": "2026-10-12",
            "verified": 1,
            "citation_count": 0,
            "author_role": author_role,
            "acl": [],
            "evidence": [],
        })
        data["cases"].append(record)
        existing.add(record["case_id"])
        added += 1
    data["note"] = (data.get("note", "")
                    + " 电池/整机五段案例（电芯组包/测试/老化/整机组装/包装）"
                      "由 tools/expand_battery_segment_seed.py 追加。").strip()
    SEED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"added": added, "total": len(data["cases"])}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
