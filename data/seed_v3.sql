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
