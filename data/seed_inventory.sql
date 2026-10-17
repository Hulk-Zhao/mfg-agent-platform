-- ============================================================
-- V3.7 库存域演示数据扩充（seed_inventory.sql）
-- 目标：为「企业综合助手」的制造数据自然语言查询（材料库存 / 成品库存 /
--       来料批次 / 库龄呆滞 / 缺料缺口 / 批次追溯）提供有东西可查的底座。
--
-- 纪律（数据红线，先读再改）：
--   1. 只新增 3 张表（material_inventory / finished_inventory / incoming_batch），
--      **不改任何旧表结构、不动任何旧表行**；
--   2. `inventory` 行数恒为 2（tests/v2/test_scenarios_v2.py 的 ATP 对账基线），
--      故成品库存富表独立承载，绝不 INSERT INTO inventory；
--   3. `material_batch` 被 S2「来料异常」场景锚定（iqc_batches_recent 窗口
--      >= 2026-10-11 且 incoming_result='fail'），故本文件**不碰该表**；
--      本表内 fail 行一律落在 2026-10-10 及以前，避免与 S2 口径混淆；
--   4. 演示红线日 2026-10-15 行不动；数据落在 2026-10-08 ~ 10-14。
--   5. 幂等：CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE（重复启动不重复插入）。
-- ============================================================

-- ---------- ① 材料库存（原材料/电子料/结构件/包材/辅料/标准件） ----------
CREATE TABLE IF NOT EXISTS material_inventory (
  id INTEGER PRIMARY KEY,
  part_no TEXT NOT NULL,
  part_name TEXT NOT NULL,
  category TEXT,
  warehouse TEXT,
  bin_location TEXT,
  batch_no TEXT,
  supplier TEXT,
  on_hand_qty INTEGER NOT NULL DEFAULT 0,
  reserved_qty INTEGER NOT NULL DEFAULT 0,
  frozen_qty INTEGER NOT NULL DEFAULT 0,
  safety_stock INTEGER NOT NULL DEFAULT 0,
  age_days INTEGER NOT NULL DEFAULT 0,
  last_move_date TEXT,
  unit_cost REAL,
  updated_at TEXT
);

INSERT OR IGNORE INTO material_inventory
  (id, part_no, part_name, category, warehouse, bin_location, batch_no, supplier,
   on_hand_qty, reserved_qty, frozen_qty, safety_stock, age_days, last_move_date, unit_cost, updated_at) VALUES
(1,  'CAP-0402-10uF',       '贴片电容10uF',    '电子料', '电子料仓', 'A-01-03', 'MAT-CAP-1008', '三星电机', 150000, 40000,     0,  50000, 12, '2026-10-14', 0.021, '2026-10-14 16:00:00'),
(2,  'CAP-0402-10uF',       '贴片电容10uF',    '电子料', '电子料仓', 'A-01-04', 'MAT-CAP-1004', '三星电机',  30000,     0,     0,  50000, 96, '2026-07-10', 0.020, '2026-07-10 10:00:00'),
(3,  'RES-0603-1k',         '贴片电阻1kΩ',     '电子料', '电子料仓', 'A-01-07', 'MAT-RES-1010', '风华高科',  40000,  5000,     0,  60000, 34, '2026-09-11', 0.008, '2026-10-12 09:00:00'),
(4,  'IC-MCU-32bit',        '主控MCU',         '电子料', '电子料仓', 'A-02-01', 'MAT-IC-1012',  '兆易创新',   5000,  4500,     0,   8000, 20, '2026-09-25', 3.850, '2026-10-12 10:20:00'),
(5,  'IC-MCU-32bit',        '主控MCU',         '电子料', '电子料仓', 'A-02-02', 'MAT-IC-1003',  '兆易创新',   3000,  1500,     0,   8000, 88, '2026-07-18', 3.800, '2026-07-18 14:00:00'),
(6,  'CAP-0805-22uF',       '贴片电容22uF',    '电子料', '电子料仓', 'A-01-05', 'MAT-CAP-1013', '三星电机',  25000,     0, 20000,  15000,  9, '2026-10-06', 0.034, '2026-10-06 09:00:00'),
(7,  'CON-TYPEC-16P',       'TYPE-C连接器',    '结构件', '结构件仓', 'B-01-02', 'MAT-CON-1014', '立讯精密',  60000, 20000,     0,  30000,  8, '2026-10-07', 0.320, '2026-10-14 09:40:00'),
(8,  'CON-TYPEC-16P',       'TYPE-C连接器',    '结构件', '结构件仓', 'B-01-05', 'MAT-CON-1025', '立讯精密',   5000,  4800,     0,  30000, 16, '2026-09-29', 0.310, '2026-10-10 11:00:00'),
(9,  'PCB-022',             'A型号PCB',        '结构件', '结构件仓', 'B-02-01', 'BATCH-A-1008', '深南电路',   3000,  2800,     0,   2500, 18, '2026-09-27', 6.500, '2026-10-12 09:00:00'),
(10, 'SOLDER-PASTE-SAC305', '无铅锡膏SAC305',  '辅料',   '辅料仓',   'C-01-01', 'MAT-SOL-1015', '千住金属',    180,    20,     0,    100,  6, '2026-10-09', 168.0, '2026-10-13 08:10:00'),
(11, 'SCREW-M8-20',         '内六角螺栓M8×20', '标准件', '标准件仓', 'D-01-01', 'MAT-SCR-1018', '东莞紧固',  50000,     0,     0,  20000,  4, '2026-10-11', 0.120, '2026-10-14 15:00:00'),
(12, 'CASE-ALU-A',          '铝合金外壳A',     '结构件', '结构件仓', 'B-03-01', 'MAT-CASE-1019','铠胜集团',   4200,  3000,     0,   2000,  5, '2026-10-10', 22.500, '2026-10-13 09:50:00'),
(13, 'CASE-ALU-A',          '铝合金外壳A',     '结构件', '结构件仓', 'B-03-04', 'MAT-CASE-1027','铠盛精密',   3000,     0,     0,   2000, 130, '2026-06-07', 22.000, '2026-06-07 16:00:00'),
(14, 'PACK-BOX-A',          '包装彩盒A',       '包材',   '包材仓',   'E-01-01', 'MAT-PKG-1020', '裕同科技',   8000,   500,     0,   5000,  7, '2026-10-08', 1.150, '2026-10-11 10:00:00'),
(15, 'PACK-BOX-A',          '包装彩盒A',       '包材',   '包材仓',   'E-01-03', 'MAT-PKG-1002', '裕同科技',   4500,     0,     0,   5000, 210, '2026-03-20', 1.100, '2026-03-20 09:00:00'),
(16, 'LCD-5.5-IPS',         '5.5寸液晶屏',     '电子料', '电子料仓', 'A-03-02', 'MAT-LCD-1016', '京东方',     2200,  1500,     0,   3000,  8, '2026-10-07', 45.000, '2026-10-10 14:00:00'),
(17, 'BAT-LI-3000',         '锂离子电池3000mAh','电子料','电子料仓', 'A-04-01', 'MAT-BAT-1017', '欣旺达',     1600,  1400,     0,   3000, 120, '2026-06-17', 12.800, '2026-06-17 11:20:00'),
(18, 'BAT-LI-3000',         '锂离子电池3000mAh','电子料','电子料仓', 'A-04-03', 'MAT-BAT-1028', '欣旺达',     1500,  1500,     0,   3000,  7, '2026-10-08', 12.600, '2026-10-08 09:00:00'),
(19, 'CONN-FPC-30P',        'FPC连接器30P',    '电子料', '电子料仓', 'A-05-01', 'MAT-FPC-1029', '立讯精密',   9000,  2000,     0,   5000, 25, '2026-09-20', 0.850, '2026-10-05 10:00:00');

-- ---------- ② 成品库存（成品库存综合；inventory 表原样不动） ----------
CREATE TABLE IF NOT EXISTS finished_inventory (
  id INTEGER PRIMARY KEY,
  product_model TEXT NOT NULL,
  warehouse TEXT NOT NULL,
  batch_no TEXT,
  on_hand_qty INTEGER NOT NULL DEFAULT 0,
  reserved_qty INTEGER NOT NULL DEFAULT 0,
  safety_stock INTEGER NOT NULL DEFAULT 0,
  age_days INTEGER NOT NULL DEFAULT 0,
  last_move_date TEXT,
  updated_at TEXT
);

INSERT OR IGNORE INTO finished_inventory
  (id, product_model, warehouse, batch_no, on_hand_qty, reserved_qty, safety_stock, age_days, last_move_date, updated_at) VALUES
(1,  'A型号', '深圳成品仓', 'BATCH-A-1015', 1200, 800, 1000,  2, '2026-10-15', '2026-10-15 16:00:00'),
(2,  'B型号', '深圳成品仓', 'BATCH-B-1014', 5000, 500, 2000,  3, '2026-10-14', '2026-10-15 16:00:00'),
(3,  'A型号', '华东成品仓', 'BATCH-A-1013',  800, 300,  500, 18, '2026-09-27', '2026-10-12 10:00:00'),
(4,  'A型号', '深圳成品仓', 'BATCH-A-1009',  600,   0, 1000, 42, '2026-09-03', '2026-09-03 10:00:00'),
(5,  'C型号', '华东成品仓', 'BATCH-C-1009', 1500, 600,  800,  6, '2026-10-09', '2026-10-14 09:00:00'),
(6,  'C型号', '保税仓',     'BATCH-C-1010',  900,   0,  400,  9, '2026-10-06', '2026-10-13 15:00:00'),
(7,  'D型号', '深圳成品仓', 'BATCH-D-1001', 3200,1200, 1500,  5, '2026-10-10', '2026-10-14 14:00:00'),
(8,  'D型号', '华东成品仓', 'BATCH-D-1002', 1100, 900,  800, 11, '2026-10-04', '2026-10-10 11:00:00'),
(9,  'E型号', '保税仓',     'BATCH-E-1001',  700, 650,  600,  7, '2026-10-08', '2026-10-13 10:00:00'),
(10, 'E型号', '深圳成品仓', 'BATCH-E-1002', 2400, 300, 1000, 14, '2026-10-01', '2026-10-11 16:00:00');

-- ---------- ③ 来料批次综合（含 IQC 结论细化：合格/不合格/待检/让步接收） ----------
CREATE TABLE IF NOT EXISTS incoming_batch (
  id INTEGER PRIMARY KEY,
  batch_no TEXT NOT NULL,
  part_no TEXT NOT NULL,
  part_name TEXT,
  supplier TEXT,
  arrival_time TEXT,
  qty INTEGER NOT NULL DEFAULT 0,
  qualified_qty INTEGER NOT NULL DEFAULT 0,
  inspect_status TEXT NOT NULL,   -- pass 合格 | fail 不合格 | pending 待检 | deviation 让步接收
  warehouse TEXT,
  bin_location TEXT,
  coa_url TEXT,
  age_days INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO incoming_batch
  (id, batch_no, part_no, part_name, supplier, arrival_time, qty, qualified_qty,
   inspect_status, warehouse, bin_location, coa_url, age_days) VALUES
(1,  'MAT-CAP-1008', 'CAP-0402-10uF',       '贴片电容10uF',    '三星电机',   '2026-10-08 08:30:00', 200000, 200000, 'pass',      '电子料仓', 'A-01-03', 'https://coa.example/MAT-CAP-1008.pdf', 7),
(2,  'MAT-RES-1010', 'RES-0603-1k',         '贴片电阻1kΩ',     '风华高科',   '2026-10-10 09:00:00',  60000,  60000, 'pass',      '电子料仓', 'A-01-07', 'https://coa.example/MAT-RES-1010.pdf', 5),
(3,  'MAT-IC-1012',  'IC-MCU-32bit',        '主控MCU',         '兆易创新',   '2026-10-12 10:15:00',  10000,  10000, 'pass',      '电子料仓', 'A-02-01', 'https://coa.example/MAT-IC-1012.pdf', 3),
(4,  'MAT-CAP-1013', 'CAP-0805-22uF',       '贴片电容22uF',    '三星电机',   '2026-10-09 08:45:00',  30000,      0, 'fail',      '电子料仓', 'A-01-05', 'https://coa.example/MAT-CAP-1013.pdf', 6),
(5,  'MAT-CON-1014', 'CON-TYPEC-16P',       'TYPE-C连接器',    '立讯精密',   '2026-10-14 09:30:00',  80000,  80000, 'pass',      '结构件仓', 'B-01-02', 'https://coa.example/MAT-CON-1014.pdf', 1),
(6,  'BATCH-A-1008', 'PCB-022',             'A型号PCB',        '深南电路',   '2026-10-12 09:00:00',   3000,   3000, 'pass',      '结构件仓', 'B-02-01', 'https://coa.example/BATCH-A-1008.pdf', 3),
(7,  'MAT-SOL-1015', 'SOLDER-PASTE-SAC305', '无铅锡膏SAC305',  '千住金属',   '2026-10-13 08:00:00',    200,    200, 'pass',      '辅料仓',   'C-01-01', 'https://coa.example/MAT-SOL-1015.pdf', 2),
(8,  'MAT-LCD-1016', 'LCD-5.5-IPS',         '5.5寸液晶屏',     '京东方',     '2026-10-10 14:00:00',   2500,   2480, 'pass',      '电子料仓', 'A-03-02', 'https://coa.example/MAT-LCD-1016.pdf', 5),
(9,  'MAT-BAT-1017', 'BAT-LI-3000',         '锂离子电池3000mAh','欣旺达',    '2026-10-09 11:20:00',   2000,      0, 'fail',      '电子料仓', 'A-04-01', 'https://coa.example/MAT-BAT-1017.pdf', 6),
(10, 'MAT-SCR-1018', 'SCREW-M8-20',         '内六角螺栓M8×20', '东莞紧固',   '2026-10-14 15:00:00', 100000, 100000, 'pass',      '标准件仓', 'D-01-01', 'https://coa.example/MAT-SCR-1018.pdf', 1),
(11, 'MAT-CASE-1019','CASE-ALU-A',          '铝合金外壳A',     '铠胜集团',   '2026-10-13 09:40:00',   5000,   4980, 'pass',      '结构件仓', 'B-03-01', 'https://coa.example/MAT-CASE-1019.pdf', 2),
(12, 'MAT-PKG-1020', 'PACK-BOX-A',          '包装彩盒A',       '裕同科技',   '2026-10-11 10:00:00',  10000,  10000, 'pass',      '包材仓',   'E-01-01', 'https://coa.example/MAT-PKG-1020.pdf', 4),
(13, 'MAT-CAP-1021', 'CAP-0402-10uF',       '贴片电容10uF',    '村田',       '2026-10-09 08:10:00',  60000,      0, 'fail',      '电子料仓', 'A-01-08', 'https://coa.example/MAT-CAP-1021.pdf', 6),
(14, 'MAT-RES-1022', 'RES-0603-1k',         '贴片电阻1kΩ',     '国巨',       '2026-10-08 09:50:00',  40000,      0, 'fail',      '电子料仓', 'A-01-09', 'https://coa.example/MAT-RES-1022.pdf', 7),
(15, 'MAT-IC-1023',  'IC-MCU-32bit',        '主控MCU',         '兆易创新',   '2026-10-10 16:30:00',   5000,   5000, 'pending',   '电子料仓', 'A-02-03', 'https://coa.example/MAT-IC-1023.pdf', 5),
(16, 'MAT-LCD-1024', 'LCD-5.5-IPS',         '5.5寸液晶屏',     '天马微电子', '2026-10-12 13:15:00',   1200,   1200, 'pending',   '电子料仓', 'A-03-04', 'https://coa.example/MAT-LCD-1024.pdf', 3),
(17, 'MAT-CON-1025', 'CON-TYPEC-16P',       'TYPE-C连接器',    '立讯精密',   '2026-10-10 11:00:00',   5000,   4800, 'deviation', '结构件仓', 'B-01-05', 'https://coa.example/MAT-CON-1025.pdf', 5),
(18, 'MAT-SOL-1026', 'SOLDER-PASTE-SAC305', '无铅锡膏SAC305',  '千住金属',   '2026-10-11 08:30:00',    100,     95, 'deviation', '辅料仓',   'C-01-02', 'https://coa.example/MAT-SOL-1026.pdf', 4),
(19, 'MAT-CASE-1027','CASE-ALU-A',          '铝合金外壳A',     '铠盛精密',   '2026-10-08 15:20:00',   3000,   3000, 'pass',      '结构件仓', 'B-03-04', 'https://coa.example/MAT-CASE-1027.pdf', 7),
(20, 'MAT-BAT-1028', 'BAT-LI-3000',         '锂离子电池3000mAh','欣旺达',    '2026-10-08 09:00:00',   1500,   1500, 'pass',      '电子料仓', 'A-04-03', 'https://coa.example/MAT-BAT-1028.pdf', 7),
(21, 'MAT-FPC-1029', 'CONN-FPC-30P',        'FPC连接器30P',    '立讯精密',   '2026-10-08 10:20:00',   9000,   8900, 'pass',      '电子料仓', 'A-05-01', 'https://coa.example/MAT-FPC-1029.pdf', 7);
