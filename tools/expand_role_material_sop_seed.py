"""案例库补充：岗位经验 22 条 + 常见材料（IC/MOS/塑胶件）9 条 + SOP 各工位（WSP）10 条。

为什么要有这个脚本：
- 「对所有岗位补充案例」——现有 role_case 只覆盖质量/设备/工艺/采购/客服/生产/管理，
  计划、物控、仓储、财务、行政、EHS、维保、测试、NPI、IE、ME、研发管理、采购管理、
  质量总监、业务等岗位要么没有、要么只有 1 条，换岗位搜同类问题演示不出差异化。
- 「质量模块常见材料补充」——来料语料有 PCB/MLCC/连接器/电感/线材，但 IC、MOS、
  塑胶件这三类最常见的器件/结构件没有对应案例，检索这几个词命中不到。
- 「SOP 各工位补充」——锡膏印刷/贴片/回流焊/插件/波峰焊/ICT/功能测试/老化/组装/
  包装 各工位补一条可检索的工序案例。

纪律（与 tools/expand_multidomain_seed.py 同法）：
- 内容按真实职能/工艺场景写死，脚本只做**幂等拼装**（按 case_id 覆盖，不重复添加）；
- 每条引用的 defect_mode_code / root_cause_code 必须已在
  ``src/mfg_agents/v2/data/ontology/*.yaml`` 注册（本轮同步新增 IC/MOS/塑胶件
  缺陷码 + 根因码 + 对策码）；
- 职能经验案例按部门做 ACL 收敛（dept:<部门>），材料/SOP 案例全员开放（acl=[]）。

幂等：按 ``case_id`` 覆盖写；已存在的 ``case_id`` 不重复添加。
用法：.venv/Scripts/python.exe tools/expand_role_material_sop_seed.py
"""
from __future__ import annotations

import json
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "case_seed.json"


def D(dept: str) -> list:
    """职能经验案例的部门 ACL（只对该部门可见）。"""
    return [{"dim": "dept", "value": dept, "effect": "allow"}]


OPEN: list = []  # 全员开放（脱敏知识层）

#: (case_id, category, subcategory, title, summary, phenomenon, root_cause,
#:  countermeasure, defect_mode_code, root_cause_code, plant, line_code,
#:  owner_dept, source_ticket_type, source_ticket_id, author_role, acl)
ROWS = [
    # ==================== 一、岗位经验（22 条，覆盖各职能岗位）====================
    # ---------- 计划 / 物控 / 仓储 ----------
    ("RC-PLN-2026-0001", "role_case", "planning_experience",
     "计划经验：插单与产能冲突的排产优先级",
     "插单不能只看客户等级，要按瓶颈工序负荷 + 物料齐套排序，避免小单插出大停线。",
     "月末冲刺期 3 张急单同时插入，未评估瓶颈贴片工序负荷，换型频繁、整体交期反而延后。",
     "插单排序只看客户等级，未评估瓶颈工序负荷与物料齐套。",
     "插单按瓶颈负荷 + 齐套排序；急单预留换型窗口；插单决策看板留痕。",
     "DM-XD-602", "RC-XD-062", "", "", "supply_chain", "manual", "RC-2026-5001", "planning", D("supply_chain")),
    ("RC-PLN-2026-0002", "role_case", "planning_experience",
     "计划经验：换型窗口与交付节拍的对齐",
     "把换型窗口显式排进日计划，用集中换型减少节拍损失。",
     "多型号小批量混排，换型次数由日均 2 次升到 5 次，交付节拍损失 12%。",
     "换型窗口未纳入日计划，型号切换无集中安排。",
     "换型窗口显式日计划化并集中换型；换型节拍纳入看板；急单预留缓冲窗口。",
     "DM-XD-601", "RC-XD-061", "", "", "supply_chain", "manual", "RC-2026-5002", "planning", D("supply_chain")),
    ("RC-MC-2026-0001", "role_case", "planning_experience",
     "物控经验：齐套率监控与缺料预警前置",
     "齐套率按工单前置到开工前 3 天预警，缺料在排产前暴露而不是上线后停线。",
     "工单开工当日才发现某长交期料缺料，产线待料 3 小时。",
     "齐套率未前置监控，缺料在上线后才暴露。",
     "齐套率按工单前置 3 天预警；关键料缺口日清到人；缺料与排产联动。",
     "DM-SCM-401", "RC-SCM-031", "", "", "supply_chain", "manual", "RC-2026-5003", "material_control", D("supply_chain")),
    ("RC-WH-2026-0001", "role_case", "warehouse_experience",
     "仓储经验：先进先出失效致物料超期复用",
     "FIFO 靠人工标识不够，要按批次与库位绑定 + 系统强制先出，超期料自动拦截。",
     "仓库呆滞料超期 8 个月被复用，焊盘氧化造成 SMT 虚焊。",
     "FIFO 仅靠人工标识，库位未按批次绑定，超期料未拦截。",
     "批次与库位绑定并系统强制先出；超期料自动拦截与复检；呆滞月度预警。",
     "DM-SCM-405", "RC-SCM-035", "", "", "warehouse", "manual", "RC-2026-5004", "warehouse", D("warehouse")),
    ("RC-WH-2026-0002", "role_case", "warehouse_experience",
     "仓储经验：盘点差异的根因归类与闭环",
     "盘点差异按收发错、混料、损耗、记录错四类归类，才能对症整改而不是只调账。",
     "月度盘点差异率 1.5%，多年只做调账不查根因，库存账实长期不符。",
     "盘点差异只调账未归类根因，收发与记录流程缺陷未整改。",
     "差异按四类根因归类并闭环整改；高频差异点专项复盘；账实差异纳入考核。",
     "DM-SCM-405", "RC-SCM-035", "", "", "warehouse", "manual", "RC-2026-5005", "warehouse_manager", D("warehouse")),
    # ---------- 财务 ----------
    ("RC-FIN-2026-0001", "role_case", "finance_experience",
     "财务经验：质量成本（COPQ）口径统一",
     "COPQ 要统一内部失败 + 外部失败 + 鉴定 + 预防四类口径，才能横向对比并推动改善。",
     "各车间质量成本口径不一，同一次客诉在不同报表里金额差异 3 倍，无法对比。",
     "质量成本口径未统一，各单元自行归集，数据不可比。",
     "统一 COPQ 四类口径并固化模板；月度例会对比；口径变更走评审留痕。",
     "DM-XD-603", "RC-XD-062", "", "", "finance", "manual", "RC-2026-5006", "finance", D("finance")),
    ("RC-FIN-2026-0002", "role_case", "finance_experience",
     "财务经验：呆滞库存减值与处置联动",
     "减值不是终点，要联动采购/物控给出复用、改制、报废的处置路径。",
     "呆滞库存计提减值后长期挂账，未推动处置，占用资金且掩盖真实损失。",
     "减值与处置脱节，无跨部门处置责任与时限。",
     "减值联动处置路径（复用/改制/报废）；处置责任到部门并限时；月度跟踪。",
     "DM-SCM-405", "RC-SCM-035", "", "", "finance", "manual", "RC-2026-5007", "finance_manager", D("finance")),
    # ---------- 行政 / EHS ----------
    ("RC-HR-2026-0001", "role_case", "admin_experience",
     "行政经验：一线技能矩阵与多能工排班",
     "用技能矩阵支撑多能工排班，淡旺季人力弹性不靠临时借调。",
     "旺季关键工位只有 1 人会操作，请假即停线；淡季人力又闲置。",
     "无技能矩阵与多能工培养，关键工位单点依赖。",
     "建立技能矩阵与多能工培养计划；关键工位双人认证；排班按矩阵弹性调配。",
     "DM-XD-601", "RC-XD-061", "", "", "admin", "manual", "RC-2026-5008", "admin_hr", D("admin")),
    ("RC-EHS-2026-0001", "role_case", "ehs_experience",
     "EHS 经验：化学品泄漏应急处置与停线联动",
     "泄漏应急要与停线/疏散联动，先切断源再围堵，绝不能边生产边处理。",
     "清洗剂储罐渗漏，现场边生产边擦拭，泄漏扩大至相邻工位。",
     "泄漏应急无停线联动与固定处置角色，处置滞后。",
     "泄漏应急联动停线与疏散；切断源 + 围堵 + 回收固定流程；季度演练。",
     "DM-XD-601", "RC-XD-061", "", "", "ehs", "manual", "RC-2026-5009", "ehs", D("ehs")),
    # ---------- 设备 / 维保 ----------
    ("RC-MT-2026-0001", "role_case", "maintenance_experience",
     "维保经验：点检趋势异常的早期处置",
     "点检不只看合不合格，要看趋势斜率，漂移趋势即提前更换。",
     "回流炉加热管点检逐月在限内但持续上漂，未预警，最终单区报废停线。",
     "点检仅判定限内/超限，未做趋势分析，早期漂移被放过。",
     "点检数据做趋势分析；超趋势阈值提前更换；关键件建立寿命台账。",
     "DM-EQ-072", "RC-EQ-HEATER-011", "", "", "equipment", "manual", "RC-2026-5010", "maintenance_tech", D("equipment")),
    ("RC-EQM-2026-0001", "role_case", "equipment_experience",
     "设备管理经验：关键备件储备与 MTTR 改善",
     "关键备件按 MTTR 影响分级储备，缺件停机是 MTTR 的最大来源。",
     "贴片机关键传感器故障，因无备件外购等待 48 小时，MTTR 严重超标。",
     "关键备件无分级储备，故障后外购等待拉长 MTTR。",
     "关键备件按 MTTR 影响分级储备；安全库存定期复核；故障复盘入库。",
     "DM-EQ-071", "RC-EQ-NOZZLE-010", "", "", "equipment", "manual", "RC-2026-5011", "equipment_manager", D("equipment")),
    # ---------- 研发 / 工程 ----------
    ("RC-TE-2026-0001", "role_case", "test_experience",
     "测试经验：可靠性试验（HTOL）早期失效判定",
     "可靠性早期失效要用失效时间分布判定，不能只看是否通过终点。",
     "高温老化试验中 1000h 通过，但 200h 内已有零星失效，被判为偶发放行。",
     "可靠性失效判定只看终点通过，未分析早期失效分布。",
     "可靠性试验做失效时间分布分析；早期失效触发批次追溯；判据分层。",
     "DM-TST-072", "RC-TST-002", "", "", "rd_engineering", "manual", "RC-2026-5012", "test_engineer", D("rd_engineering")),
    ("RC-NPI-2026-0001", "role_case", "npi_experience",
     "NPI 经验：试产良率爬坡的阶段门禁",
     "试产良率爬坡设阶段门禁，未达门禁不放开量产，避免带病量产。",
     "新机型试产良率仅 82% 即放开量产，量产后不良持续，返工成本翻倍。",
     "试产无良率门禁，未达目标即放量，问题带到量产。",
     "试产设良率门禁；未达门禁触发设计/工艺整改；放量需评审留痕。",
     "DM-DES-083", "RC-DES-053", "", "", "rd_engineering", "manual", "RC-2026-5013", "npi_engineer", D("rd_engineering")),
    ("RC-IE-2026-0001", "role_case", "ie_experience",
     "IE 经验：线平衡与瓶颈工位改善",
     "用线平衡率定位瓶颈，优先改善瓶颈工位而不是全线加人。",
     "总装线节拍卡在某一装配工位，全线等料，产能利用率仅 75%。",
     "未做线平衡分析，瓶颈工位超载而其他工位空转。",
     "做线平衡分析并拆分瓶颈工位工序；节拍看板跟踪；阶段性复核。",
     "DM-ASSY-308", "RC-ASSY-041", "", "", "rd_engineering", "manual", "RC-2026-5014", "ie_engineer", D("rd_engineering")),
    ("RC-ME-2026-0001", "role_case", "me_experience",
     "结构经验：公差链分析与装配干涉预防",
     "装配干涉要在设计阶段做公差链分析，把累积公差算进去。",
     "整机量产后壳体与支架干涉异响，拆解为公差累积超限。",
     "设计未做公差链分析，累积公差在量产放大成干涉。",
     "设计阶段做公差链分析；关键配合面收紧公差；首批装配验证。",
     "DM-ASSY-308", "RC-ASSY-041", "", "", "rd_engineering", "manual", "RC-2026-5015", "me_engineer", D("rd_engineering")),
    ("RC-RDT-2026-0001", "role_case", "test_experience",
     "研发测试经验：固件回归用例覆盖",
     "固件变更必须跑回归，回归用例按改动影响面自动圈选，不能只测改点。",
     "固件小版本改动只测改点，遗漏下游模块回归，客户端出现功能失效。",
     "固件回归用例按改点圈选，未评估影响面，覆盖不足。",
     "回归用例按影响面圈选；变更与用例绑定；回归未过不放行。",
     "DM-DES-084", "RC-DES-054", "", "", "rd_engineering", "manual", "RC-2026-5016", "rd_test_engineer", D("rd_engineering")),
    ("RC-RDM-2026-0001", "role_case", "rd_management_experience",
     "研发管理经验：设计变更（ECN）评审门禁",
     "ECN 必须过设计-工艺-质量-采购四方面评审，禁止口头变更。",
     "研发口头变更物料未过 ECN，量产后与 BOM 不符造成批量返工。",
     "变更未走 ECN 评审门禁，口头变更流入量产。",
     "ECN 四方评审门禁；变更与 BOM/工艺同步；禁止口头变更。",
     "DM-DES-083", "RC-DES-053", "", "", "rd_engineering", "manual", "RC-2026-5017", "rd_manager", D("rd_engineering")),
    ("RC-PE-2026-0001", "role_case", "process_experience",
     "工艺经验：设备能力指数（CMK）验收",
     "新设备验收不只看能否生产，要看 CMK，能力不足的产线置入必然后患。",
     "新贴片机到厂直接投产，未验收设备能力，印刷厚度 Cpk 长期不达标。",
     "设备验收未做能力指数（CMK）验证即投产。",
     "新设备验收强制 CMK 验证；达门禁方可投产；能力报告留痕。",
     "DM-DES-083", "RC-DES-053", "", "", "rd_engineering", "manual", "RC-2026-5018", "product_engineering", D("rd_engineering")),
    # ---------- 采购 / 供应商质量 / 业务 / 质量总监 ----------
    ("RC-PROCM-2026-0001", "role_case", "sourcing_experience",
     "采购管理经验：供应商分级与年度审核",
     "供应商按绩效 + 风险分级，分级决定份额与审核频次，不搞平均主义。",
     "供应商无分级，高绩效与高风险供应商同等待遇，采购资源投向错配。",
     "供应商无分级管理与年度审核，采购策略一刀切。",
     "供应商绩效 + 风险分级；分级决定份额与审核频次；年度审核留痕。",
     "DM-SCM-402", "RC-SCM-032", "", "", "procurement", "manual", "RC-2026-5019", "procurement_manager", D("procurement")),
    ("RC-SQE-2026-0001", "role_case", "quality_experience",
     "质量经验：供应商 PPAP 与首批确认",
     "新料/变更料必须过 PPAP 与首批试产确认，文件齐套才准入。",
     "新供应商未做 PPAP 即量产，首批尺寸超差导致装配不良。",
     "供应商准入未做 PPAP 与首批确认，变更未验证。",
     "新料/变更料强制 PPAP 与首批试产确认；文件齐套准入；ECN 管控。",
     "DM-SCM-404", "RC-SCM-034", "", "", "quality", "manual", "RC-2026-5020", "sqe", D("quality")),
    ("RC-DIR-2026-0001", "role_case", "quality_experience",
     "质量经验：质量红线指标与目标设置",
     "红线指标不可交易，目标指标可协商；先分清两者再谈考核。",
     "交付压力下现场要求放宽某项外观判定，因红线与目标不分，反复扯皮。",
     "质量红线与目标指标未区分，红线被当作可协商目标。",
     "明确红线（不可交易）与目标（可协商）清单；红线变更需质量总监批准并留痕。",
     "DM-XD-602", "RC-XD-062", "", "", "quality", "manual", "RC-2026-5021", "quality_director", D("quality")),
    ("RC-BIZ-2026-0001", "role_case", "service_experience",
     "业务经验：客户交期承诺的风险评估",
     "交期承诺前先看产能与物料风险，宁可分批承诺也不做无法兑现的满口承诺。",
     "业务为拿单承诺全量两周交付，实际产能不足，延期两周被罚款。",
     "交期承诺未做产能与物料风险评估，承诺不可兑现。",
     "交期承诺前做产能/物料风险评估；高风险单分批承诺；承诺与排产联动。",
     "DM-SCM-402", "RC-SCM-032", "", "", "sales", "manual", "RC-2026-5022", "sales", D("sales")),

    # ==================== 二、常见材料（IC / MOS / 塑胶件，9 条，全员开放）====================
    ("QC-MAT-2026-0001", "quality_case", "material_defect",
     "材料：IC 引脚共面度超差致贴装虚焊",
     "IC 来料引脚共面度超差，贴装后引脚与焊盘接触不良形成虚焊；来料加严后恢复。",
     "AOI 检出 IC 引脚虚焊，剖切见部分引脚未与焊盘共面，贴合不实。",
     "IC 来料引脚共面度超差，超出贴装可容差；来料检验未覆盖共面度。",
     "IC 来料增加引脚共面度抽检并加严；超差批次退货换料；供应商 8D 整改。",
     "DM-MAT-059", "RC-MAT-COPLANAR-010", "P1", "来料-1", "quality", "iqc", "IQC-2026-1001", "sqe", OPEN),
    ("QC-MAT-2026-0002", "quality_case", "material_defect",
     "材料：IC 潮敏器件受潮致回流爆米花",
     "潮敏 IC 来料受潮，回流焊高温下内部水汽膨胀产生爆米花开裂；MSL 管控后恢复。",
     "回流后 X-Ray 检出 IC 本体分层开裂，功能测试部分失效。",
     "潮敏器件（MSL）来料受潮，拆封后未烘烤即上线，回流高温下内部水汽膨胀。",
     "建立 MSL 分级管控与拆封后烘烤时限；防潮包装与湿度卡；超期强制烘烤。",
     "DM-MAT-060", "RC-MAT-MSL-011", "P1", "来料-1", "quality", "iqc", "IQC-2026-1002", "sqe", OPEN),
    ("QC-MAT-2026-0003", "quality_case", "material_defect",
     "材料：IC 静电损伤（ESD）致功能失效",
     "IC 在搬运/上料环节遭静电损伤，成品功能间歇失效；ESD 防护升级后恢复。",
     "整机功能测试 IC 相关功能间歇失效，回查该 IC 曾在上料环节暴露于未接地工位。",
     "来料与上料环节静电防护缺失，器件遭 ESD 损伤。",
     "来料 ESD 防护与包装抽检；工位接地与离子风扇点检；敏感器件操作规范。",
     "DM-MAT-061", "RC-MAT-ESD-012", "P1", "来料-1", "quality", "8d", "8D-2026-1003", "sqe", OPEN),
    ("QC-MAT-2026-0004", "quality_case", "material_defect",
     "材料：IC 丝印型号错印致混料错装",
     "IC 丝印型号与实物不符，产线按丝印识别导致混料错装；来料标识管控后恢复。",
     "产线按丝印型号上料，成品参数不符，追溯发现该批 IC 丝印与料号不一致。",
     "供应商丝印错印且来料未按料号唯一条码校验，混料未被拦截。",
     "来料按唯一料号条码校验并抽检丝印；错印批次全退；供应商 8D 整改。",
     "DM-MAT-062", "RC-MAT-SUPPLIER-006", "P1", "来料-1", "quality", "iqc", "IQC-2026-1004", "sqe", OPEN),
    ("QC-MAT-2026-0005", "quality_case", "material_defect",
     "材料：MOS 管静电击穿致电源模块失效",
     "MOS 管在装配前遭静电击穿，电源模块上电即失效；ESD 与来料抽检后恢复。",
     "电源模块 EOL 上电即失效，解剖见 MOS 管栅极击穿。",
     "MOS 管静电敏感，装配前无 ESD 防护且来料未做击穿抽检。",
     "MOS 管来料击穿抽检；装配工位 ESD 防护与接地；敏感器件专用周转。",
     "DM-MAT-063", "RC-MAT-ESD-012", "P1", "来料-1", "quality", "8d", "8D-2026-1005", "sqe", OPEN),
    ("QC-MAT-2026-0006", "quality_case", "material_defect",
     "材料：MOS 参数漂移（阈值电压）致导通异常",
     "MOS 来料阈值电压批次漂移，同一驱动电路导通特性不一致；参数抽测后恢复。",
     "同一批整机 MOS 导通损耗偏高、局部发热，实测阈值电压较标称偏高 15%。",
     "MOS 来料阈值电压参数离散，来料未做参数抽测，批次漂移未被拦截。",
     "MOS 来料参数抽测（Vth/导通电阻）；超差批次退货；供应商参数一致性整改。",
     "DM-MAT-064", "RC-MAT-PARAM-013", "P1", "来料-1", "quality", "iqc", "IQC-2026-1006", "sqe", OPEN),
    ("QC-MAT-2026-0007", "quality_case", "material_defect",
     "材料：塑胶件缩水变形致装配间隙超差",
     "塑胶件来料缩水变形，装配后外观间隙超差；成型参数与模流优化后恢复。",
     "整机外观间隙 0.8mm 超限值 0.5mm，拆解见塑胶件局部缩水变形。",
     "塑胶件来料成型收缩不均（缩水变形），来料未做尺寸与外观抽检。",
     "塑件成型参数与模流优化；来料尺寸/外观抽检加严；超差批次退货。",
     "DM-MAT-065", "RC-MAT-WARP-014", "P1", "来料-1", "quality", "iqc", "IQC-2026-1007", "sqe", OPEN),
    ("QC-MAT-2026-0008", "quality_case", "material_defect",
     "材料：塑胶件熔接痕强度不足致卡扣开裂",
     "塑胶件熔接痕处强度不足，装配/跌落时卡扣开裂；结构与工艺优化后恢复。",
     "整机跌落测试后塑胶卡扣在熔接痕处断裂，抽检合格率仅 88%。",
     "塑胶件熔接痕强度不足，浇口位置与熔接温度不匹配。",
     "塑件熔接痕结构优化与浇口调整；来料熔接痕强度抽检；跌落验证。",
     "DM-MAT-066", "RC-MAT-WELD-015", "P1", "来料-1", "quality", "8d", "8D-2026-1008", "sqe", OPEN),
    ("QC-MAT-2026-0009", "quality_case", "material_defect",
     "材料：塑胶件阻燃不达标（安规风险）",
     "塑胶件来料阻燃等级不达标，存在安规与安全风险；阻燃验证与来料抽检后恢复。",
     "安规送检发现塑胶外壳阻燃测试不通过（余焰时间超标）。",
     "塑胶件阻燃配方不足，来料阻燃性能未验证即入库使用。",
     "塑件阻燃验证与来料抽检（阻燃/灼热丝）；不达标批次全退；供应商整改。",
     "DM-MAT-067", "RC-MAT-FR-016", "P1", "来料-1", "quality", "iqc", "IQC-2026-1009", "sqe", OPEN),

    # ==================== 三、SOP 各工位（10 条，全员开放）====================
    ("QC-WSP-2026-0001", "quality_case", "process_defect",
     "工序：锡膏印刷—钢网开孔不当致少锡虚焊",
     "钢网开孔面积不足导致锡量偏少，回流后引脚少锡虚焊；开孔优化后恢复。",
     "SPI 检出锡量偏低（体积偏差 -25%），回流后细间距引脚少锡虚焊。",
     "钢网开孔面积比设计不当，锡膏转移量不足。",
     "钢网开孔优化（面积比 ≥0.66）并做锡量验证；SPI 全检；开孔变更走评审。",
     "DM-SMT-016", "RC-TOOL-APERTURE-006", "P1", "SMT-1", "process", "process", "PD-2026-2001", "pe", OPEN),
    ("QC-WSP-2026-0002", "quality_case", "process_defect",
     "工序：贴片—贴装压力过大致元件损伤",
     "贴片机吸嘴下压压力过大，压伤元件（尤其薄型 IC/MLCC）；压力校准后恢复。",
     "AOI 检出薄型元件本体裂纹，集中在高压力贴装头。",
     "贴装压力过大，超出元件可承受范围，薄型元件被压伤。",
     "贴装压力校准至基准并锁定；薄型元件分区降压；贴装后抽检元件外观。",
     "DM-PKG-033", "RC-PARAM-MOUNT-003", "P1", "SMT-1", "process", "process", "PD-2026-2002", "pe", OPEN),
    ("QC-WSP-2026-0003", "quality_case", "process_defect",
     "工序：回流焊—器件受潮致焊点空洞",
     "器件受潮（含 PCB 与 IC）在回流时水汽逸出形成焊点空洞；烘烤管控后恢复。",
     "X-Ray 检出焊点空洞率 30%，超限值 25%，集中于受潮器件与板。",
     "器件与 PCB 受潮，回流前未烘烤，水汽逸出形成焊点空洞。",
     "受潮器件与 PCB 回流前烘烤（按 MSL）；防潮存储；空洞率定期 X-Ray 抽检。",
     "DM-SMT-022", "RC-MAT-MSL-011", "P1", "SMT-1", "process", "process", "PD-2026-2003", "pe", OPEN),
    ("QC-WSP-2026-0004", "quality_case", "process_defect",
     "工序：插件—元件反向（极性反）",
     "插件工位极性防错缺失，元件反向装入；极性防错工装与首件确认后恢复。",
     "功能测试检出电解电容极性反装，抽检发现多台同问题。",
     "插件上料无极性防错校验，靠人眼核对，反向未被拦截。",
     "插件加极性防错工装与上料扫描；首件确认极性；100% 目视复核。",
     "DM-DIP-103", "RC-PROC-SUP-003", "P1", "DIP-1", "process", "process", "PD-2026-2004", "pe", OPEN),
    ("QC-WSP-2026-0005", "quality_case", "process_defect",
     "工序：波峰焊—锡桥拉尖致连锡",
     "波峰焊参数偏移致锡桥拉尖连锡；参数锁定与预热校验后恢复。",
     "波峰焊后相邻引脚出现锡桥连锡，不良率 1.8%。",
     "波峰焊预热不足与波峰参数偏移，锡桥未及时断开形成拉尖。",
     "波峰焊参数锁定与预热复测；助焊剂喷涂验证；连锡 AOI/目检加严。",
     "DM-DIP-104", "RC-DIP-005", "P1", "DIP-1", "process", "8d", "8D-2026-2005", "me", OPEN),
    ("QC-WSP-2026-0006", "quality_case", "process_defect",
     "工序：ICT—治具探针接触不良致误测",
     "ICT 治具探针接触不良造成间歇误测，复测合格；探针点检与更换后恢复。",
     "ICT 测试间歇报开路，复测多为合格，集中在某几根探针。",
     "ICT 治具探针接触不良，接触电阻偏高致误测。",
     "ICT 探针纳入点检与更换周期；加接触自检；复测确认与治具保养台账。",
     "DM-TST-076", "RC-ASSY-050", "P1", "TEST-1", "equipment", "process", "PD-2026-2006", "te", OPEN),
    ("QC-WSP-2026-0007", "quality_case", "process_defect",
     "工序：功能测试—测试项缺失致漏检",
     "功能测试程序缺漏关键测试项，缺陷流出客户端；测试覆盖评审后补全。",
     "客户端反馈功能缺陷，回查功能测试程序缺该项测试。",
     "功能测试项缺失，测试覆盖评审不充分，未与设计验证项对齐。",
     "补全测试项并做覆盖评审；测试项与设计验证项对齐；程序变更留痕。",
     "DM-TST-070", "RC-TST-003", "P1", "TEST-3", "quality", "8d", "8D-2026-2007", "mqe", OPEN),
    ("QC-WSP-2026-0008", "quality_case", "process_defect",
     "工序：老化—老化时长不足致衰减未暴露",
     "老化时长不足导致衰减未充分暴露即放行；时长与判据校准后恢复。",
     "售后反馈老化相关衰减异常，回查产线老化时长被压缩以赶交付。",
     "老化时长不足，衰减未充分暴露即放行。",
     "老化时长与判据校准并锁定；老化前后容量内阻对比留存；超限自动拦截。",
     "DM-AGING-801", "RC-AGING-002", "P1", "AGING-1", "quality", "8d", "8D-2026-2008", "pqe", OPEN),
    ("QC-WSP-2026-0009", "quality_case", "process_defect",
     "工序：组装—紧固顺序错致螺钉漏拧",
     "组装紧固顺序未图示化且无计数校验，漏拧靠抽检发现；顺序固化后恢复。",
     "抽检发现多台整机漏拧螺钉，部分紧固顺序与工艺不符。",
     "紧固顺序未图示化，且无计数校验，漏拧靠抽检才发现。",
     "紧固顺序图示化 + 扭力枪计数防漏拧 + 首件确认顺序。",
     "DM-ASSY-302", "RC-ASSY-048", "P1", "ASSY-1", "manufacturing", "manual", "RC-2026-2009", "pe", OPEN),
    ("QC-WSP-2026-0010", "quality_case", "process_defect",
     "工序：包装—条码打印浓度不足不可读",
     "出口条码打印浓度不足不可读，出库扫码停滞；打印质量抽检后恢复。",
     "出库扫码失败率 5%，需人工录入，出货节拍下降。",
     "条码打印质量差，打印头未按期保养，浓度偏低。",
     "打印浓度纳入日点检；打印头按期保养；每批抽测条码可读性。",
     "DM-PACK-206", "RC-PACK-026", "P1", "PKG-1", "production", "process", "PD-2026-2010", "mqe", OPEN),
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
            "published_at": "2026-10-15",
            "last_verified_at": "2026-10-18",
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
                    + " 岗位经验 22 条 + 常见材料（IC/MOS/塑胶件）9 条 + SOP 各工位（WSP）10 条"
                      "由 tools/expand_role_material_sop_seed.py 追加。").strip()
    SEED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return {"added": added, "total": len(data["cases"])}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
