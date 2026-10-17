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
-- V3.2 财务/行政知识库（来源：E:/tools/assistant/data/knowledge_base 复制提炼）
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
