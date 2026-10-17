-- ============================================================
-- V3 演示数据扩充（seed_v3.sql，幂等：INSERT OR IGNORE，id 100+ 段）
-- 纪律（数据红线，tests/v2/test_scenarios_v2.py 守护）：
--   1. 不碰"今天"（2026-10-15）的任何行 —— line_stop 场景的今日断言依赖
--   2. 不碰 inventory（行数=2 是 ATP 对账基线）
--   3. shipment 只加 C 型号（inventory 无 C 型号，JOIN 后不出现在 ATP）
--   4. 不改 BATCH-A-1008 / SMT-1 进行中停线 / SMT-1 设备告警 等场景锚点
--   5. 兼容实际库结构（V1 旧 DDL：无 created_at/updated_at 列）
-- 全部新增数据落在 2026-10-08 ~ 10-14（历史），让"上周/最近"类自由提问有数据可查
-- ============================================================

-- 客诉 +4（历史：小米/OPPO/大疆/美的）
INSERT OR IGNORE INTO customer_complaint (id, complaint_no, customer, product_model, batch_no, defect_desc, status) VALUES
(101, 'CMP-20261008-01', '小米', 'B型号', 'BATCH-B-1006', '充电接口接触不良，抽样不良率1.8%，要求8D回复', 'closed'),
(102, 'CMP-20261010-02', 'OPPO', 'A型号', 'BATCH-A-1011', '外观面划伤，来料检验漏检，客户产线上线发现', 'processing'),
(103, 'CMP-20261012-03', '大疆', 'C型号', 'BATCH-C-1009', '固件版本与订单不一致，客户产线升级工时损失', 'closed'),
(104, 'CMP-20261014-04', '美的', 'B型号', 'BATCH-B-1013', '包装箱批次号标签错印，仓库发货前拦截', 'processing');

-- 生产产出 +7（A/B/C 型号 × 三条线的历史产出）
INSERT OR IGNORE INTO production_output (id, record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min) VALUES
(101, '2026-10-13', 'SMT-1', 'A型号', 2000, 1980, 88.5, 0),
(102, '2026-10-13', 'SMT-2', 'B型号', 1500, 1320, 74.2, 95),
(103, '2026-10-13', 'DIP-1', 'C型号', 900, 905, 91.0, 0),
(104, '2026-10-14', 'SMT-1', 'A型号', 2000, 1960, 86.8, 20),
(105, '2026-10-14', 'SMT-2', 'B型号', 1500, 1450, 83.5, 30),
(106, '2026-10-14', 'DIP-1', 'C型号', 900, 895, 90.2, 0),
(107, '2026-10-12', 'SMT-1', 'A型号', 2000, 1900, 82.0, 65);

-- 质量不良 +5（立碑/划伤/虚焊/少锡/元件偏移）
INSERT OR IGNORE INTO quality_defect (id, batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
(101, 'BATCH-B-1006', 'B型号', 'SMT-2', '元件立碑', 28, 1500, '2026-10-08 11:00:00'),
(102, 'BATCH-A-1011', 'A型号', 'SMT-1', '外观划伤', 55, 2000, '2026-10-10 15:30:00'),
(103, 'BATCH-C-1009', 'C型号', 'DIP-1', '焊点虚焊', 17, 900, '2026-10-12 09:10:00'),
(104, 'BATCH-B-1013', 'B型号', 'SMT-2', '锡膏少锡', 34, 1500, '2026-10-14 10:20:00'),
(105, 'BATCH-A-1012', 'A型号', 'SMT-1', '元件偏移', 12, 2000, '2026-10-14 14:45:00');

-- 来料批次 +5（含 1 行 10-09 fail：三星电机电容容值偏移；10-09 在 S2 今日判定窗口外）
INSERT OR IGNORE INTO material_batch (id, batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
(101, 'MAT-CAP-1008', 'CAP-0402-10uF', '贴片电容10uF', '三星电机', 'pass', 'https://coa.example/MAT-CAP-1008.pdf', '2026-10-08 08:30:00'),
(102, 'MAT-RES-1010', 'RES-0603-1k', '贴片电阻1kΩ', '风华高科', 'pass', 'https://coa.example/MAT-RES-1010.pdf', '2026-10-10 09:00:00'),
(103, 'MAT-IC-1012', 'IC-MCU-32bit', '主控MCU', '兆易创新', 'pass', 'https://coa.example/MAT-IC-1012.pdf', '2026-10-12 10:15:00'),
(104, 'MAT-CAP-1013', 'CAP-0805-22uF', '贴片电容22uF', '三星电机', 'fail', 'https://coa.example/MAT-CAP-1013.pdf', '2026-10-09 08:45:00'),
(105, 'MAT-CON-1014', 'CON-TYPEC-16P', 'TYPE-C连接器', '立讯精密', 'pass', 'https://coa.example/MAT-CON-1014.pdf', '2026-10-14 09:30:00');

-- 设备日志 +4（SMT-2 / OVN-01 历史告警；SMT-1 的 3 条进行中告警不动）
INSERT OR IGNORE INTO equipment_log (id, equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
(101, 'PRT-02', 'SMT-2', '2026-10-13 10:12:00', 'E203', '吸嘴真空度', '38kPa（基准≥55）', 1),
(102, 'OVN-01', 'DIP-1', '2026-10-13 15:40:00', 'W110', '温区2实际温度', '238℃（基准245±5）', 1),
(103, 'PRT-02', 'SMT-2', '2026-10-14 09:05:00', 'I010', '贴装速度', '正常', 0),
(104, 'AOI-01', 'SMT-1', '2026-10-14 16:20:00', 'I011', '检测程序版本', 'V2.31', 0);

-- 停线事件 +1（SMT-2 历史停线已恢复；ongoing 过滤保证不影响今日停线断言）
INSERT OR IGNORE INTO line_stop_event (id, line_code, product_model, start_time, end_time, stop_code, root_cause, status) VALUES
(101, 'SMT-2', 'B型号', '2026-10-13 08:55:00', '2026-10-13 10:30:00', 'STOP-EQUIP', '贴片机吸嘴堵塞（真空度不足），更换吸嘴后恢复', 'resolved');

-- 发货 +1（仅 C 型号：inventory 无 C 型号，ATP JOIN 不受影响）
INSERT OR IGNORE INTO shipment (id, order_no, product_model, qty, ship_status, eta) VALUES
(101, 'SO-C-2026-1012', 'C型号', 500, 'shipped', '2026-10-13 00:00:00');

-- ============================================================
-- V3.2 财务/行政知识库（企业通用制度与流程知识整理）
-- 鉴权（PM 裁定）：finance_policy 仅 finance/gm/coordinator 可查（高管限定）；
--                admin_policy 全员可读（行政制度公开）。
-- ============================================================

CREATE TABLE IF NOT EXISTS finance_policy (
  id INT PRIMARY KEY,
  policy_no VARCHAR(30),
  category VARCHAR(30),
  title VARCHAR(100),
  content TEXT,
  keywords VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS admin_policy (
  id INT PRIMARY KEY,
  policy_no VARCHAR(30),
  category VARCHAR(30),
  title VARCHAR(100),
  content TEXT,
  keywords VARCHAR(200)
);

INSERT OR IGNORE INTO finance_policy (id, policy_no, category, title, content, keywords) VALUES
(101, 'FIN-ADV-01', '借支', '借支申请流程', '出差/采购需提前借款，在 OA 提交《借款单》，注明金额、用途、预计归还时间。借支范围：差旅住宿押金、交通费、采购定金等，个人消费不得借支。单笔借支≤5000元由部门负责人审批，>5000元需部门总监+财务经理审批。', '借支,借款,备用金,审批'),
(102, 'FIN-ADV-02', '借支', '借支额度', '普通员工单次借支上限1万元，部门经理2万元。同一借款未核销前，新增借支须说明原借款去向。', '借支,额度,上限'),
(103, 'FIN-ADV-03', '借支', '借支发放与核销', '审批通过后财务1-2个工作日打款至本人工资卡。借支须在出差/采购结束后10个工作日内核销，核销凭发票/收据在OA关联报销单冲抵；未及时核销次月起从工资中扣回并纳入个人信用记录。', '借支,核销,发放,归还'),
(104, 'FIN-ADV-04', '借支', '备用金管理', '部门日常小额支出（≤2000元/次）可设备用金，由部门指定专人保管。备用金每月核对一次账目，凭票据报销补足。备用金不得用于私人用途，离职须全额归还。', '备用金,小额支出'),
(105, 'FIN-EXP-01', '报销', '差旅报销流程', '出差前须在OA提交《出差申请单》，注明目的地、事由、预计费用。未经审批的出差费用不予报销。报销单须在返回后5个工作日内提交。', '报销,差旅,出差申请'),
(106, 'FIN-EXP-02', '报销', '住宿费标准', '一线城市（北上广深）≤500元/晚，新一线城市（杭州、成都、武汉等）≤450元/晚，其他城市≤400元/晚。超标准部分须事前特批，否则自理。', '住宿,标准,差旅,报销'),
(107, 'FIN-EXP-03', '报销', '伙食补助与市内交通', '伙食补助：出差期间100元/天，不需发票。市内交通：凭票实报实销，每天上限80元。', '伙食,补助,交通,补贴'),
(108, 'FIN-TRV-01', '差旅', '交通标准', '飞机：经济舱（出差6小时以上可申请公务舱）。高铁：二等座（总经理以上一等座）。火车：硬卧（卧铺过夜可软卧）。自驾：按公里补贴1.5元/公里，须报销油费发票。', '交通,飞机,高铁,自驾,标准'),
(109, 'FIN-INV-01', '发票', '发票要求', '报销发票须为增值税发票原件，抬头、税号与公司信息一致；电子发票需查验真伪并截图附上；连号发票须说明情况；发票开具日期超过90天需财务经理特批。', '发票,报销,要求,增值税'),
(110, 'FIN-INV-02', '发票', '公司开票信息', '发票抬头：XX科技有限公司；税号：91110000XXXXXXXXXX；地址电话：北京市海淀区XX路XX号 010-XXXXXXXX；开户行：中国建设银行北京中关村支行。', '开票,抬头,税号,发票信息'),
(111, 'FIN-INV-03', '发票', '发票类型', '增值税专用发票：可抵扣项目（采购、服务）使用，须提供对方一般纳税人资质证明。增值税普通发票：日常费用报销使用。个人抬头发票原则上不予报销。', '发票类型,专票,普票,抵扣'),
(112, 'FIN-EXP-04', '报销', '报销时效', '当月费用当月报销，最迟不超过次月10日；跨年度费用原则上不予报销（12月费用可延至次年1月10日）。报销审批流：部门负责人→财务审核→财务经理→（>1万元）总经理。', '报销,时效,审批流,期限');

INSERT OR IGNORE INTO admin_policy (id, policy_no, category, title, content, keywords) VALUES
(101, 'ADM-MEET-01', '会议', '会议室预订', 'OA系统预订，提前1周开放。预订后15分钟未到自动释放。会议超过2小时须注明原因。', '会议室,预订,会议'),
(102, 'ADM-MEET-02', '会议', '会议室设备', '各会议室配备投影/电视、白板、视频会议终端；设备故障联系行政部内线报修。', '会议室,设备,投影'),
(103, 'ADM-CATE-01', '餐饮', '食堂餐饮', '早餐7:30-9:00（5元），午餐11:30-13:00（15元），晚餐17:30-19:00（10元）。凭工牌刷卡，费用从工资代扣。每层茶水间提供免费咖啡、茶包、矿泉水；咖啡机故障联系行政部。', '食堂,餐饮,午餐,早餐'),
(104, 'ADM-LOGI-01', '交通', '班车', '覆盖5条主要线路，时刻表见行政公告栏（OA-行政服务可查电子版）。', '班车,通勤,交通'),
(105, 'ADM-ACT-01', '活动', '员工文体活动', '公司每年举办2次全员运动会（春季/秋季），全员可报名。每季度组织部门团建，人均标准200元，凭发票报销。年中/年会设置抽奖与优秀员工表彰。', '活动,运动会,团建,年会'),
(106, 'ADM-ACT-02', '活动', '兴趣社团', '现有社团：篮球、足球、羽毛球、桌游、摄影、读书、跑步7个。每周固定时间段使用指定场地，社团活动室在B1层。加入社团在OA应用「社团中心」报名，新社团满10人可申请成立。', '社团,兴趣,活动');

-- ============================================================
-- V3.3 项目里程碑 + 测试记录（支撑 NPI/项目经理/测试智能体）
-- 鉴权：project_milestone 全员可读（项目信息透明）；test_records 测试/质量/研发/PM 可读
-- ============================================================

CREATE TABLE IF NOT EXISTS project_milestone (
  id INT PRIMARY KEY,
  product_model VARCHAR(50),
  phase VARCHAR(20),
  milestone VARCHAR(60),
  status VARCHAR(20),
  owner VARCHAR(40),
  planned_end DATE,
  actual_end DATE,
  risk_note VARCHAR(200)
);
CREATE TABLE IF NOT EXISTS test_records (
  id INT PRIMARY KEY,
  product_model VARCHAR(50),
  batch_no VARCHAR(30),
  test_type VARCHAR(40),
  test_item VARCHAR(80),
  result VARCHAR(20),
  defect_desc VARCHAR(200),
  tester VARCHAR(40),
  test_date DATE
);

INSERT OR IGNORE INTO project_milestone (id, product_model, phase, milestone, status, owner, planned_end, actual_end, risk_note) VALUES
(101, 'A型号', 'MP', '量产爬坡', 'done', 'NPI · 陈工', '2026-10-10', '2026-10-09', ''),
(102, 'B型号', 'PVT', '小批量试产验证', 'in_progress', 'NPI · 刘工', '2026-10-20', NULL, '结构公差问题已闭环，待复测'),
(103, 'B型号', 'PVT', '可靠性测试（跌落/高低温）', 'in_progress', 'TE · 张工', '2026-10-22', NULL, '高低温第一轮通过'),
(104, 'B型号', 'DVT', '设计验证测试', 'done', 'TE · 张工', '2026-09-28', '2026-09-26', ''),
(105, 'C型号', 'EVT', '工程验证', 'in_progress', 'NPI · 刘工', '2026-11-05', NULL, '主板供应商样品延迟 1 周'),
(106, 'C型号', 'EVT', 'DFM 可制造性评审', 'pending', 'IE · 王工', '2026-11-10', NULL, '');

INSERT OR IGNORE INTO test_records (id, product_model, batch_no, test_type, test_item, result, defect_desc, tester, test_date) VALUES
(101, 'B型号', 'BATCH-B-1015', '可靠性', '高低温循环 -20℃~60℃', 'pass', '', '张工', '2026-10-14'),
(102, 'B型号', 'BATCH-B-1015', '可靠性', '跌落 1.0m 六面', 'fail', '第 4 面跌落后电池仓间隙超差 0.3mm，判定卡扣强度不足', '张工', '2026-10-13'),
(103, 'B型号', 'BATCH-B-1015', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-12'),
(104, 'C型号', 'BATCH-C-1009', '软件', '固件 V1.0 回归测试', 'fail', '蓝牙配对成功率 92%（目标≥99%），Bug #101 遗留', '研发测试 · 吴工', '2026-10-12'),
(105, 'A型号', 'BATCH-A-1011', '可靠性', '跌落 1.0m 六面', 'pass', '', '张工', '2026-10-11');

-- ============================================================
-- V3.4 制造综合看板（全员可见）演示数据
-- 支撑「制造看板」（稼动率 / 报废率 / 报工完成率 / 测试直通率）
-- 与「品质看板」（不良率 / 直通率 FPY / 标准三率）的真实聚合计算。
-- 纪律（数据红线，改动前逐条核对）：
--   1. 新增行全部落在 10-08 ~ 10-14 窗口（避开 10-15 红线日）；
--   2. 不碰 production_output / inventory（基线 2 行）/ material_batch 10-15；
--   3. 不新增 SMT-1 异常设备行；
--   4. quality_defect 新增行不得含 line_code='SMT-1' AND product_model='A型号'
--      （quality 智能体无日期过滤，test_v1_regression / test_prompts_golden 断言
--       该组合主导不良为"锡膏偏移"、汇总含 77）；故新行全部落在其它线/型号。
-- 数值与公式严格自洽（见 docs/架构与前端设计-制造综合看板.md §9）。
-- ============================================================

CREATE TABLE IF NOT EXISTS equipment_run_time (
  id INT PRIMARY KEY,
  record_date DATE,
  line_code VARCHAR(20),
  product_model VARCHAR(50),
  planned_run_min INT,
  actual_run_min INT,
  stop_min INT
);
CREATE TABLE IF NOT EXISTS scrap_record (
  id INT PRIMARY KEY,
  record_date DATE,
  line_code VARCHAR(20),
  product_model VARCHAR(50),
  output_qty INT,
  scrap_qty INT,
  scrap_reason VARCHAR(80)
);
CREATE TABLE IF NOT EXISTS work_report (
  id INT PRIMARY KEY,
  report_date DATE,
  line_code VARCHAR(20),
  product_model VARCHAR(50),
  wo_no VARCHAR(30),
  process_step VARCHAR(30),
  should_report INT,
  reported INT
);
CREATE TABLE IF NOT EXISTS std_standard_audit (
  id INT PRIMARY KEY,
  audit_date DATE,
  product_model VARCHAR(50),
  process_step VARCHAR(30),
  standard_no VARCHAR(30),
  doc_present INT,
  doc_complete INT,
  audit_total INT,
  audit_conform INT
);

-- 设备运行（稼动率 = 实际运行分钟 / 计划运行分钟）
-- 分组汇总：整体 3060/3700 ≈ 82.7%；
-- DIP-2/B 385/700=55.0%（异常，danger）、SMT-2/B 448/600≈74.7%、
-- SMT-1/A 1317/1440≈91.5%、DIP-1/A 440/480≈91.7%、DIP-1/C 470/480≈97.9%
INSERT OR IGNORE INTO equipment_run_time (id, record_date, line_code, product_model, planned_run_min, actual_run_min, stop_min) VALUES
(101, '2026-10-08', 'SMT-1', 'A型号', 720, 660, 60),
(102, '2026-10-09', 'SMT-2', 'B型号', 300, 224, 76),
(103, '2026-10-10', 'DIP-1', 'A型号', 480, 440, 40),
(104, '2026-10-11', 'DIP-2', 'B型号', 700, 385, 315),
(105, '2026-10-12', 'SMT-1', 'A型号', 720, 657, 63),
(106, '2026-10-13', 'SMT-2', 'B型号', 300, 224, 76),
(107, '2026-10-14', 'DIP-1', 'C型号', 480, 470, 10);

-- 报废（报废率 = 报废数 / 总产出）
-- 分组：整体 108/10800 = 1.0%；
-- DIP-2/B 45/1000=4.50%（异常，danger）、SMT-2/C 15/500=3.00%、
-- SMT-1/A 20/2000=1.00%、DIP-1/C 10/1500≈0.67%、SMT-2/B 10/2000=0.50%、
-- DIP-1/A 8/3800≈0.21%（最优，不入 TOP5）
INSERT OR IGNORE INTO scrap_record (id, record_date, line_code, product_model, output_qty, scrap_qty, scrap_reason) VALUES
(101, '2026-10-08', 'DIP-2', 'B型号', 1000, 45, '波峰焊连锡批废'),
(102, '2026-10-09', 'SMT-2', 'C型号', 500, 15, '元件立碑不可返修'),
(103, '2026-10-10', 'SMT-1', 'A型号', 2000, 20, '锡膏偏移超差报废'),
(104, '2026-10-11', 'DIP-1', 'C型号', 1500, 10, '虚焊返修后仍报废'),
(105, '2026-10-13', 'SMT-2', 'B型号', 2000, 10, '外观划伤判废'),
(106, '2026-10-14', 'DIP-1', 'A型号', 3800, 8, '插件脚变形判废');

-- 报工（报工完成率 = 已报工工序数 / 应报工工序数）
-- 按工单汇总：整体 18/22 ≈ 81.8%；
-- WO-B-1014 3/5=60.0%、WO-C-1011 2/3≈66.7%、WO-B-1013 4/5=80.0%、
-- WO-A-1014 4/4=100%、WO-C-1013 5/5=100%
INSERT OR IGNORE INTO work_report (id, report_date, line_code, product_model, wo_no, process_step, should_report, reported) VALUES
(101, '2026-10-08', 'DIP-2', 'B型号', 'WO-B-1014', '插件', 1, 1),
(102, '2026-10-09', 'DIP-2', 'B型号', 'WO-B-1014', '波峰焊', 1, 1),
(103, '2026-10-10', 'DIP-2', 'B型号', 'WO-B-1014', '测试', 1, 1),
(104, '2026-10-11', 'DIP-2', 'B型号', 'WO-B-1014', '包装', 1, 0),
(105, '2026-10-12', 'DIP-2', 'B型号', 'WO-B-1014', '返修', 1, 0),
(106, '2026-10-09', 'SMT-2', 'C型号', 'WO-C-1011', '贴片', 1, 1),
(107, '2026-10-10', 'SMT-2', 'C型号', 'WO-C-1011', '波峰焊', 1, 1),
(108, '2026-10-11', 'SMT-2', 'C型号', 'WO-C-1011', '测试', 1, 0),
(109, '2026-10-08', 'DIP-2', 'B型号', 'WO-B-1013', '插件', 1, 1),
(110, '2026-10-09', 'DIP-2', 'B型号', 'WO-B-1013', '波峰焊', 1, 1),
(111, '2026-10-10', 'DIP-2', 'B型号', 'WO-B-1013', '测试', 1, 1),
(112, '2026-10-12', 'DIP-2', 'B型号', 'WO-B-1013', '包装', 1, 1),
(113, '2026-10-13', 'DIP-2', 'B型号', 'WO-B-1013', '返修', 1, 0),
(114, '2026-10-08', 'SMT-1', 'A型号', 'WO-A-1014', '贴片', 1, 1),
(115, '2026-10-09', 'SMT-1', 'A型号', 'WO-A-1014', '回流焊', 1, 1),
(116, '2026-10-10', 'SMT-1', 'A型号', 'WO-A-1014', '测试', 1, 1),
(117, '2026-10-11', 'SMT-1', 'A型号', 'WO-A-1014', '包装', 1, 1),
(118, '2026-10-09', 'DIP-1', 'C型号', 'WO-C-1013', '插件', 1, 1),
(119, '2026-10-10', 'DIP-1', 'C型号', 'WO-C-1013', '波峰焊', 1, 1),
(120, '2026-10-11', 'DIP-1', 'C型号', 'WO-C-1013', '测试', 1, 1),
(121, '2026-10-12', 'DIP-1', 'C型号', 'WO-C-1013', '包装', 1, 1),
(122, '2026-10-13', 'DIP-1', 'C型号', 'WO-C-1013', '老化', 1, 1);

-- 标准三率审核（完整率 = 有标准文件工序 / 总工序；
--             完善率 = 标准完善工序 / 有标准文件工序；
--             遵守率 = 实际遵守项 / 应审核项；符合率 = 三者乘积）
-- 整体：完整率 8/10=80.0%、完善率 6/8=75.0%、遵守率 162/200=81.0%、符合率≈48.6%（异常）
-- 分型号：A 100%/66.7%/90.0%→60.0%；B 66.7%/50.0%/73.3%→24.4%；C 75.0%/100%/80.0%→60.0%
INSERT OR IGNORE INTO std_standard_audit (id, audit_date, product_model, process_step, standard_no, doc_present, doc_complete, audit_total, audit_conform) VALUES
(101, '2026-10-08', 'A型号', '贴片', 'STD-A-01', 1, 1, 20, 18),
(102, '2026-10-09', 'A型号', '插件', 'STD-A-02', 1, 1, 20, 18),
(103, '2026-10-10', 'A型号', '测试', 'STD-A-03', 1, 0, 20, 18),
(104, '2026-10-08', 'B型号', '贴片', 'STD-B-01', 1, 1, 20, 15),
(105, '2026-10-11', 'B型号', '波峰焊', 'STD-B-02', 1, 0, 20, 15),
(106, '2026-10-12', 'B型号', '测试', 'STD-B-03', 0, 0, 20, 14),
(107, '2026-10-09', 'C型号', '贴片', 'STD-C-01', 1, 1, 20, 16),
(108, '2026-10-11', 'C型号', '插件', 'STD-C-02', 1, 1, 20, 16),
(109, '2026-10-13', 'C型号', '波峰焊', 'STD-C-03', 1, 1, 20, 16),
(110, '2026-10-14', 'C型号', '包装', 'STD-C-04', 0, 0, 20, 16);

-- 质量不良 +3（落 10-08~10-14 窗口；全部避开 SMT-1/A型号 —— 红线）
INSERT OR IGNORE INTO quality_defect (id, batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
(106, 'BATCH-B-1007', 'B型号', 'DIP-2', '焊点拉尖', 42, 1200, '2026-10-09 10:00:00'),
(107, 'BATCH-C-1010', 'C型号', 'SMT-2', '元件立碑', 26, 900, '2026-10-11 14:00:00'),
(108, 'BATCH-A-1013', 'A型号', 'DIP-1', '虚焊', 9, 800, '2026-10-13 09:30:00');

-- 测试记录 +17（组内均为 pass，把"测试直通率"拉回 ~90% 合理区间）
-- 分组：A 3/3=100%、B 12/13≈92.3%、C 5/6≈83.3%；整体 20/22≈90.9%
INSERT OR IGNORE INTO test_records (id, product_model, batch_no, test_type, test_item, result, defect_desc, tester, test_date) VALUES
(106, 'A型号', 'BATCH-A-1010', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-10'),
(107, 'A型号', 'BATCH-A-1012', '可靠性', '高低温循环 -20℃~60℃', 'pass', '', '张工', '2026-10-13'),
(108, 'B型号', 'BATCH-B-1010', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-08'),
(109, 'B型号', 'BATCH-B-1010', '可靠性', '跌落 1.0m 六面', 'pass', '', '张工', '2026-10-08'),
(110, 'B型号', 'BATCH-B-1011', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-09'),
(111, 'B型号', 'BATCH-B-1011', '软件', '固件 V1.0 回归测试', 'pass', '', '研发测试 · 吴工', '2026-10-09'),
(112, 'B型号', 'BATCH-B-1012', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-10'),
(113, 'B型号', 'BATCH-B-1012', '可靠性', '高低温循环 -20℃~60℃', 'pass', '', '张工', '2026-10-10'),
(114, 'B型号', 'BATCH-B-1013', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-11'),
(115, 'B型号', 'BATCH-B-1013', '软件', '固件 V1.0 回归测试', 'pass', '', '研发测试 · 吴工', '2026-10-11'),
(116, 'B型号', 'BATCH-B-1014', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-13'),
(117, 'B型号', 'BATCH-B-1014', '可靠性', '跌落 1.0m 六面', 'pass', '', '张工', '2026-10-14'),
(118, 'C型号', 'BATCH-C-1010', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-08'),
(119, 'C型号', 'BATCH-C-1010', '软件', '固件 V1.0 回归测试', 'pass', '', '研发测试 · 吴工', '2026-10-09'),
(120, 'C型号', 'BATCH-C-1011', '功能', '整机功能全测', 'pass', '', '李工', '2026-10-10'),
(121, 'C型号', 'BATCH-C-1011', '可靠性', '高低温循环 -20℃~60℃', 'pass', '', '张工', '2026-10-11'),
(122, 'C型号', 'BATCH-C-1012', '软件', '固件 V1.0 回归测试', 'pass', '', '研发测试 · 吴工', '2026-10-13');
