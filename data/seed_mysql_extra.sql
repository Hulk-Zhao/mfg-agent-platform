-- ============================================================
-- 演示数据集扩充（MySQL 旁路；V2 主链路仍读 SQLite）
--
-- 由 data/init.sql 建立的表结构之上追加"演示可信"量级的历史数据：
--   生产产出 / 停线事件 / 品质不良 / 来料批次 / 发货单 / 设备日志 / 库存
--
-- 红线：新增行不改变 queries.yaml 已注册查询对演示日 2026-10-15 的结果集
--   （mfg_oee / mfg_stop_ongoing / quality_defect_dist / supply_batches_today /
--     iqc_batches_recent / equip_alarms / shipment_status 口径不变，详见生成器注释）
--
-- 应用方式（既有容器，只执行一次）：
--   docker exec -i mfg_mysql mysql -uroot -pdemo123 mfg_agent < data/seed_mysql_extra.sql
-- 全新初始化：docker-compose.infra.yml 已将其挂载到 initdb 目录（init.sql 之后自动执行）。
--
-- 注意：本脚本为一次性追加，重复执行会重复插入（如需重建请先 docker compose down -v）。
-- ============================================================
USE mfg_agent;


-- ---------- production_output：追加 60 行 ----------
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-08-20', 'SMT-2', 'C型号', 2000, 2000, 70.0, 0, '2026-08-20 16:30:00'),
 ('2026-08-24', 'DIP-1', 'C型号', 2500, 2463, 71.3, 7, '2026-08-24 16:30:00'),
 ('2026-08-28', 'DIP-2', 'C型号', 3000, 2926, 72.6, 14, '2026-08-28 16:30:00'),
 ('2026-09-02', 'ASSY-1', 'C型号', 3500, 3389, 73.9, 21, '2026-09-02 16:30:00'),
 ('2026-09-06', 'TEST-1', 'C型号', 4000, 3852, 75.2, 28, '2026-09-06 16:30:00'),
 ('2026-09-10', 'SMT-2', 'D型号', 4500, 4315, 76.5, 35, '2026-09-10 16:30:00'),
 ('2026-09-14', 'DIP-1', 'D型号', 2000, 1778, 77.8, 42, '2026-09-14 16:30:00'),
 ('2026-09-18', 'DIP-2', 'D型号', 2500, 2241, 79.1, 49, '2026-09-18 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-09-22', 'ASSY-1', 'D型号', 3000, 2704, 80.4, 56, '2026-09-22 16:30:00'),
 ('2026-09-26', 'TEST-1', 'D型号', 3500, 3167, 81.7, 3, '2026-09-26 16:30:00'),
 ('2026-09-30', 'SMT-2', 'E型号', 4000, 3630, 83.0, 10, '2026-09-30 16:30:00'),
 ('2026-10-02', 'DIP-1', 'E型号', 4500, 4493, 84.3, 17, '2026-10-02 16:30:00'),
 ('2026-10-05', 'DIP-2', 'E型号', 2000, 1956, 85.6, 24, '2026-10-05 16:30:00'),
 ('2026-10-08', 'ASSY-1', 'E型号', 2500, 2419, 86.9, 31, '2026-10-08 16:30:00'),
 ('2026-08-20', 'TEST-1', 'E型号', 3000, 2882, 88.2, 38, '2026-08-20 16:30:00'),
 ('2026-08-24', 'SMT-2', 'C型号', 3500, 3345, 89.5, 45, '2026-08-24 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-08-28', 'DIP-1', 'C型号', 4000, 3808, 90.8, 52, '2026-08-28 16:30:00'),
 ('2026-09-02', 'DIP-2', 'C型号', 4500, 4271, 92.1, 59, '2026-09-02 16:30:00'),
 ('2026-09-06', 'ASSY-1', 'C型号', 2000, 1734, 93.4, 6, '2026-09-06 16:30:00'),
 ('2026-09-10', 'TEST-1', 'C型号', 2500, 2197, 94.7, 13, '2026-09-10 16:30:00'),
 ('2026-09-14', 'SMT-2', 'D型号', 3000, 2660, 71.0, 20, '2026-09-14 16:30:00'),
 ('2026-09-18', 'DIP-1', 'D型号', 3500, 3123, 72.3, 27, '2026-09-18 16:30:00'),
 ('2026-09-22', 'DIP-2', 'D型号', 4000, 3986, 73.6, 34, '2026-09-22 16:30:00'),
 ('2026-09-26', 'ASSY-1', 'D型号', 4500, 4449, 74.9, 41, '2026-09-26 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-09-30', 'TEST-1', 'D型号', 2000, 1912, 76.2, 48, '2026-09-30 16:30:00'),
 ('2026-10-02', 'SMT-2', 'E型号', 2500, 2375, 77.5, 55, '2026-10-02 16:30:00'),
 ('2026-10-05', 'DIP-1', 'E型号', 3000, 2838, 78.8, 2, '2026-10-05 16:30:00'),
 ('2026-10-08', 'DIP-2', 'E型号', 3500, 3301, 80.1, 9, '2026-10-08 16:30:00'),
 ('2026-08-20', 'ASSY-1', 'E型号', 4000, 3764, 81.4, 16, '2026-08-20 16:30:00'),
 ('2026-08-24', 'TEST-1', 'E型号', 4500, 4227, 82.7, 23, '2026-08-24 16:30:00'),
 ('2026-08-28', 'SMT-2', 'C型号', 2000, 1690, 84.0, 30, '2026-08-28 16:30:00'),
 ('2026-09-02', 'DIP-1', 'C型号', 2500, 2153, 85.3, 37, '2026-09-02 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-09-06', 'DIP-2', 'C型号', 3000, 2616, 86.6, 44, '2026-09-06 16:30:00'),
 ('2026-09-10', 'ASSY-1', 'C型号', 3500, 3479, 87.9, 51, '2026-09-10 16:30:00'),
 ('2026-09-14', 'TEST-1', 'C型号', 4000, 3942, 89.2, 58, '2026-09-14 16:30:00'),
 ('2026-09-18', 'SMT-2', 'D型号', 4500, 4405, 90.5, 5, '2026-09-18 16:30:00'),
 ('2026-09-22', 'DIP-1', 'D型号', 2000, 1868, 91.8, 12, '2026-09-22 16:30:00'),
 ('2026-09-26', 'DIP-2', 'D型号', 2500, 2331, 93.1, 19, '2026-09-26 16:30:00'),
 ('2026-09-30', 'ASSY-1', 'D型号', 3000, 2794, 94.4, 26, '2026-09-30 16:30:00'),
 ('2026-10-02', 'TEST-1', 'D型号', 3500, 3257, 70.7, 33, '2026-10-02 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-10-05', 'SMT-2', 'E型号', 4000, 3720, 72.0, 40, '2026-10-05 16:30:00'),
 ('2026-10-08', 'DIP-1', 'E型号', 4500, 4183, 73.3, 47, '2026-10-08 16:30:00'),
 ('2026-08-20', 'DIP-2', 'E型号', 2000, 1646, 74.6, 54, '2026-08-20 16:30:00'),
 ('2026-08-24', 'ASSY-1', 'E型号', 2500, 2109, 75.9, 1, '2026-08-24 16:30:00'),
 ('2026-08-28', 'TEST-1', 'E型号', 3000, 2972, 77.2, 8, '2026-08-28 16:30:00'),
 ('2026-09-02', 'SMT-2', 'C型号', 3500, 3435, 78.5, 15, '2026-09-02 16:30:00'),
 ('2026-09-06', 'DIP-1', 'C型号', 4000, 3898, 79.8, 22, '2026-09-06 16:30:00'),
 ('2026-09-10', 'DIP-2', 'C型号', 4500, 4361, 81.1, 29, '2026-09-10 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-09-14', 'ASSY-1', 'C型号', 2000, 1824, 82.4, 36, '2026-09-14 16:30:00'),
 ('2026-09-18', 'TEST-1', 'C型号', 2500, 2287, 83.7, 43, '2026-09-18 16:30:00'),
 ('2026-09-22', 'SMT-2', 'D型号', 3000, 2750, 85.0, 50, '2026-09-22 16:30:00'),
 ('2026-09-26', 'DIP-1', 'D型号', 3500, 3213, 86.3, 57, '2026-09-26 16:30:00'),
 ('2026-09-30', 'DIP-2', 'D型号', 4000, 3676, 87.6, 4, '2026-09-30 16:30:00'),
 ('2026-10-02', 'ASSY-1', 'D型号', 4500, 4139, 88.9, 11, '2026-10-02 16:30:00'),
 ('2026-10-05', 'TEST-1', 'D型号', 2000, 1602, 90.2, 18, '2026-10-05 16:30:00'),
 ('2026-10-08', 'SMT-2', 'E型号', 2500, 2465, 91.5, 25, '2026-10-08 16:30:00');
INSERT INTO production_output (record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min, created_at) VALUES
 ('2026-08-20', 'DIP-1', 'E型号', 3000, 2928, 92.8, 32, '2026-08-20 16:30:00'),
 ('2026-08-24', 'DIP-2', 'E型号', 3500, 3391, 94.1, 39, '2026-08-24 16:30:00'),
 ('2026-08-28', 'ASSY-1', 'E型号', 4000, 3854, 70.4, 46, '2026-08-28 16:30:00'),
 ('2026-09-02', 'TEST-1', 'E型号', 4500, 4317, 71.7, 53, '2026-09-02 16:30:00');

-- ---------- line_stop_event：追加 40 行 ----------
INSERT INTO line_stop_event (line_code, product_model, start_time, end_time, stop_code, root_cause, status) VALUES
 ('DIP-1', 'E型号', '2026-08-20 08:00:00', '2026-08-20 08:00:00', 'EQ', '设备故障（已闭环，时长20min）', 'closed'),
 ('DIP-2', 'C型号', '2026-08-24 09:01:00', '2026-08-24 09:03:00', 'MAT', '来料异常（已闭环，时长31min）', 'closed'),
 ('ASSY-1', 'D型号', '2026-08-28 10:02:00', '2026-08-28 10:06:00', 'PRC', '工艺参数漂移（已闭环，时长42min）', 'closed'),
 ('TEST-1', 'E型号', '2026-09-02 11:03:00', '2026-09-02 11:09:00', 'EQ', '换产清线（已闭环，时长53min）', 'closed'),
 ('SMT-2', 'C型号', '2026-09-06 12:04:00', '2026-09-06 13:12:00', 'MAT', '计划停机（已闭环，时长64min）', 'closed'),
 ('DIP-1', 'D型号', '2026-09-10 13:05:00', '2026-09-10 14:15:00', 'PRC', '品质拦截（已闭环，时长75min）', 'closed'),
 ('DIP-2', 'E型号', '2026-09-14 14:06:00', '2026-09-14 15:18:00', 'EQ', '动力供应（已闭环，时长86min）', 'closed'),
 ('ASSY-1', 'C型号', '2026-09-18 15:07:00', '2026-09-18 16:21:00', 'MAT', '设备故障（已闭环，时长97min）', 'closed');
INSERT INTO line_stop_event (line_code, product_model, start_time, end_time, stop_code, root_cause, status) VALUES
 ('TEST-1', 'D型号', '2026-09-22 08:08:00', '2026-09-22 09:24:00', 'PRC', '来料异常（已闭环，时长108min）', 'closed'),
 ('SMT-2', 'E型号', '2026-09-26 09:09:00', '2026-09-26 10:27:00', 'EQ', '工艺参数漂移（已闭环，时长119min）', 'closed'),
 ('DIP-1', 'C型号', '2026-09-30 10:10:00', '2026-09-30 12:30:00', 'MAT', '换产清线（已闭环，时长130min）', 'closed'),
 ('DIP-2', 'D型号', '2026-10-02 11:11:00', '2026-10-02 13:33:00', 'PRC', '计划停机（已闭环，时长141min）', 'closed'),
 ('ASSY-1', 'E型号', '2026-10-05 12:12:00', '2026-10-05 14:36:00', 'EQ', '品质拦截（已闭环，时长152min）', 'closed'),
 ('TEST-1', 'C型号', '2026-10-08 13:13:00', '2026-10-08 15:39:00', 'MAT', '动力供应（已闭环，时长163min）', 'closed'),
 ('SMT-2', 'D型号', '2026-08-20 14:14:00', '2026-08-20 16:42:00', 'PRC', '设备故障（已闭环，时长174min）', 'closed'),
 ('DIP-1', 'E型号', '2026-08-24 15:15:00', '2026-08-24 18:45:00', 'EQ', '来料异常（已闭环，时长185min）', 'closed');
INSERT INTO line_stop_event (line_code, product_model, start_time, end_time, stop_code, root_cause, status) VALUES
 ('DIP-2', 'C型号', '2026-08-28 08:16:00', '2026-08-28 11:48:00', 'MAT', '工艺参数漂移（已闭环，时长196min）', 'closed'),
 ('ASSY-1', 'D型号', '2026-09-02 09:17:00', '2026-09-02 09:51:00', 'PRC', '换产清线（已闭环，时长27min）', 'closed'),
 ('TEST-1', 'E型号', '2026-09-06 10:18:00', '2026-09-06 10:54:00', 'EQ', '计划停机（已闭环，时长38min）', 'closed'),
 ('SMT-2', 'C型号', '2026-09-10 11:19:00', '2026-09-10 11:57:00', 'MAT', '品质拦截（已闭环，时长49min）', 'closed'),
 ('DIP-1', 'D型号', '2026-09-14 12:20:00', '2026-09-14 13:00:00', 'PRC', '动力供应（已闭环，时长60min）', 'closed'),
 ('DIP-2', 'E型号', '2026-09-18 13:21:00', '2026-09-18 14:03:00', 'EQ', '设备故障（已闭环，时长71min）', 'closed'),
 ('ASSY-1', 'C型号', '2026-09-22 14:22:00', '2026-09-22 15:06:00', 'MAT', '来料异常（已闭环，时长82min）', 'closed'),
 ('TEST-1', 'D型号', '2026-09-26 15:23:00', '2026-09-26 16:09:00', 'PRC', '工艺参数漂移（已闭环，时长93min）', 'closed');
INSERT INTO line_stop_event (line_code, product_model, start_time, end_time, stop_code, root_cause, status) VALUES
 ('SMT-2', 'E型号', '2026-09-30 08:24:00', '2026-09-30 09:12:00', 'EQ', '换产清线（已闭环，时长104min）', 'closed'),
 ('DIP-1', 'C型号', '2026-10-02 09:25:00', '2026-10-02 10:15:00', 'MAT', '计划停机（已闭环，时长115min）', 'closed'),
 ('DIP-2', 'D型号', '2026-10-05 10:26:00', '2026-10-05 12:18:00', 'PRC', '品质拦截（已闭环，时长126min）', 'closed'),
 ('ASSY-1', 'E型号', '2026-10-08 11:27:00', '2026-10-08 13:21:00', 'EQ', '动力供应（已闭环，时长137min）', 'closed'),
 ('TEST-1', 'C型号', '2026-08-20 12:28:00', '2026-08-20 14:24:00', 'MAT', '设备故障（已闭环，时长148min）', 'closed'),
 ('SMT-2', 'D型号', '2026-08-24 13:29:00', '2026-08-24 15:27:00', 'PRC', '来料异常（已闭环，时长159min）', 'closed'),
 ('DIP-1', 'E型号', '2026-08-28 14:30:00', '2026-08-28 16:30:00', 'EQ', '工艺参数漂移（已闭环，时长170min）', 'closed'),
 ('DIP-2', 'C型号', '2026-09-02 15:31:00', '2026-09-02 18:33:00', 'MAT', '换产清线（已闭环，时长181min）', 'closed');
INSERT INTO line_stop_event (line_code, product_model, start_time, end_time, stop_code, root_cause, status) VALUES
 ('ASSY-1', 'D型号', '2026-09-06 08:32:00', '2026-09-06 11:36:00', 'PRC', '计划停机（已闭环，时长192min）', 'closed'),
 ('TEST-1', 'E型号', '2026-09-10 09:33:00', '2026-09-10 09:39:00', 'EQ', '品质拦截（已闭环，时长23min）', 'closed'),
 ('SMT-2', 'C型号', '2026-09-14 10:34:00', '2026-09-14 10:42:00', 'MAT', '动力供应（已闭环，时长34min）', 'closed'),
 ('DIP-1', 'D型号', '2026-09-18 11:35:00', '2026-09-18 11:45:00', 'PRC', '设备故障（已闭环，时长45min）', 'closed'),
 ('DIP-2', 'E型号', '2026-09-22 12:36:00', '2026-09-22 12:48:00', 'EQ', '来料异常（已闭环，时长56min）', 'closed'),
 ('ASSY-1', 'C型号', '2026-09-26 13:37:00', '2026-09-26 14:51:00', 'MAT', '工艺参数漂移（已闭环，时长67min）', 'closed'),
 ('TEST-1', 'D型号', '2026-09-30 14:38:00', '2026-09-30 15:54:00', 'PRC', '换产清线（已闭环，时长78min）', 'closed'),
 ('SMT-2', 'E型号', '2026-10-02 15:39:00', '2026-10-02 16:57:00', 'EQ', '计划停机（已闭环，时长89min）', 'closed');

-- ---------- quality_defect：追加 210 行 ----------
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0820', 'C型号', 'SMT-2', '锡膏偏移', 12, 200, '2026-08-20 09:00:00'),
 ('BATCH-C-0824', 'C型号', 'DIP-1', '锡膏偏移', 23, 300, '2026-08-24 10:07:00'),
 ('BATCH-C-0828', 'C型号', 'DIP-2', '锡膏偏移', 37, 400, '2026-08-28 11:14:00'),
 ('BATCH-C-0902', 'C型号', 'ASSY-1', '锡膏偏移', 55, 500, '2026-09-02 12:21:00'),
 ('BATCH-C-0906', 'C型号', 'TEST-1', '锡膏偏移', 76, 600, '2026-09-06 13:28:00'),
 ('BATCH-D-0910', 'D型号', 'SMT-2', '锡膏偏移', 101, 700, '2026-09-10 14:35:00'),
 ('BATCH-D-0914', 'D型号', 'DIP-1', '锡膏偏移', 32, 200, '2026-09-14 15:42:00'),
 ('BATCH-D-0918', 'D型号', 'DIP-2', '锡膏偏移', 53, 300, '2026-09-18 16:49:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0922', 'D型号', 'ASSY-1', '锡膏偏移', 78, 400, '2026-09-22 17:56:00'),
 ('BATCH-D-0926', 'D型号', 'TEST-1', '锡膏偏移', 106, 500, '2026-09-26 09:03:00'),
 ('BATCH-E-0930', 'E型号', 'SMT-2', '锡膏偏移', 138, 600, '2026-09-30 10:10:00'),
 ('BATCH-E-1002', 'E型号', 'DIP-1', '锡膏偏移', 172, 700, '2026-10-02 11:17:00'),
 ('BATCH-E-1005', 'E型号', 'DIP-2', '锡膏偏移', 52, 200, '2026-10-05 12:24:00'),
 ('BATCH-E-1008', 'E型号', 'ASSY-1', '锡膏偏移', 84, 300, '2026-10-08 13:31:00'),
 ('BATCH-E-0820', 'E型号', 'TEST-1', '锡膏偏移', 119, 400, '2026-08-20 14:38:00'),
 ('BATCH-C-0824', 'C型号', 'SMT-2', '连锡', 157, 500, '2026-08-24 15:45:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0828', 'C型号', 'DIP-1', '连锡', 199, 600, '2026-08-28 16:52:00'),
 ('BATCH-C-0902', 'C型号', 'DIP-2', '连锡', 244, 700, '2026-09-02 17:59:00'),
 ('BATCH-C-0906', 'C型号', 'ASSY-1', '连锡', 73, 200, '2026-09-06 09:06:00'),
 ('BATCH-C-0910', 'C型号', 'TEST-1', '连锡', 114, 300, '2026-09-10 10:13:00'),
 ('BATCH-D-0914', 'D型号', 'SMT-2', '连锡', 160, 400, '2026-09-14 11:20:00'),
 ('BATCH-D-0918', 'D型号', 'DIP-1', '连锡', 208, 500, '2026-09-18 12:27:00'),
 ('BATCH-D-0922', 'D型号', 'DIP-2', '连锡', 260, 600, '2026-09-22 13:34:00'),
 ('BATCH-D-0926', 'D型号', 'ASSY-1', '连锡', 315, 700, '2026-09-26 14:41:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0930', 'D型号', 'TEST-1', '连锡', 93, 200, '2026-09-30 15:48:00'),
 ('BATCH-E-1002', 'E型号', 'SMT-2', '连锡', 145, 300, '2026-10-02 16:55:00'),
 ('BATCH-E-1005', 'E型号', 'DIP-1', '连锡', 200, 400, '2026-10-05 17:02:00'),
 ('BATCH-E-1008', 'E型号', 'DIP-2', '连锡', 259, 500, '2026-10-08 09:09:00'),
 ('BATCH-E-0820', 'E型号', 'ASSY-1', '连锡', 321, 600, '2026-08-20 10:16:00'),
 ('BATCH-E-0824', 'E型号', 'TEST-1', '连锡', 387, 700, '2026-08-24 11:23:00'),
 ('BATCH-C-0828', 'C型号', 'SMT-2', '虚焊', 114, 200, '2026-08-28 12:30:00'),
 ('BATCH-C-0902', 'C型号', 'DIP-1', '虚焊', 176, 300, '2026-09-02 13:37:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0906', 'C型号', 'DIP-2', '虚焊', 241, 400, '2026-09-06 14:44:00'),
 ('BATCH-C-0910', 'C型号', 'ASSY-1', '虚焊', 310, 500, '2026-09-10 15:51:00'),
 ('BATCH-C-0914', 'C型号', 'TEST-1', '虚焊', 382, 600, '2026-09-14 16:58:00'),
 ('BATCH-D-0918', 'D型号', 'SMT-2', '虚焊', 458, 700, '2026-09-18 17:05:00'),
 ('BATCH-D-0922', 'D型号', 'DIP-1', '虚焊', 134, 200, '2026-09-22 09:12:00'),
 ('BATCH-D-0926', 'D型号', 'DIP-2', '虚焊', 206, 300, '2026-09-26 10:19:00'),
 ('BATCH-D-0930', 'D型号', 'ASSY-1', '虚焊', 282, 400, '2026-09-30 11:26:00'),
 ('BATCH-D-1002', 'D型号', 'TEST-1', '虚焊', 361, 500, '2026-10-02 12:33:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-1005', 'E型号', 'SMT-2', '虚焊', 444, 600, '2026-10-05 13:40:00'),
 ('BATCH-E-1008', 'E型号', 'DIP-1', '虚焊', 529, 700, '2026-10-08 14:47:00'),
 ('BATCH-E-0820', 'E型号', 'DIP-2', '虚焊', 154, 200, '2026-08-20 15:54:00'),
 ('BATCH-E-0824', 'E型号', 'ASSY-1', '虚焊', 237, 300, '2026-08-24 16:01:00'),
 ('BATCH-E-0828', 'E型号', 'TEST-1', '虚焊', 323, 400, '2026-08-28 17:08:00'),
 ('BATCH-C-0902', 'C型号', 'SMT-2', '立碑', 412, 500, '2026-09-02 09:15:00'),
 ('BATCH-C-0906', 'C型号', 'DIP-1', '立碑', 505, 600, '2026-09-06 10:22:00'),
 ('BATCH-C-0910', 'C型号', 'DIP-2', '立碑', 601, 700, '2026-09-10 11:29:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0914', 'C型号', 'ASSY-1', '立碑', 175, 200, '2026-09-14 12:36:00'),
 ('BATCH-C-0918', 'C型号', 'TEST-1', '立碑', 267, 300, '2026-09-18 13:43:00'),
 ('BATCH-D-0922', 'D型号', 'SMT-2', '立碑', 364, 400, '2026-09-22 14:50:00'),
 ('BATCH-D-0926', 'D型号', 'DIP-1', '立碑', 463, 500, '2026-09-26 15:57:00'),
 ('BATCH-D-0930', 'D型号', 'DIP-2', '立碑', 566, 600, '2026-09-30 16:04:00'),
 ('BATCH-D-1002', 'D型号', 'ASSY-1', '立碑', 672, 700, '2026-10-02 17:11:00'),
 ('BATCH-D-1005', 'D型号', 'TEST-1', '立碑', 195, 200, '2026-10-05 09:18:00'),
 ('BATCH-E-1008', 'E型号', 'SMT-2', '立碑', 298, 300, '2026-10-08 10:25:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0820', 'E型号', 'DIP-1', '立碑', 404, 400, '2026-08-20 11:32:00'),
 ('BATCH-E-0824', 'E型号', 'DIP-2', '立碑', 514, 500, '2026-08-24 12:39:00'),
 ('BATCH-E-0828', 'E型号', 'ASSY-1', '立碑', 627, 600, '2026-08-28 13:46:00'),
 ('BATCH-E-0902', 'E型号', 'TEST-1', '立碑', 744, 700, '2026-09-02 14:53:00'),
 ('BATCH-C-0906', 'C型号', 'SMT-2', '缺件', 216, 200, '2026-09-06 15:00:00'),
 ('BATCH-C-0910', 'C型号', 'DIP-1', '缺件', 329, 300, '2026-09-10 16:07:00'),
 ('BATCH-C-0914', 'C型号', 'DIP-2', '缺件', 445, 400, '2026-09-14 17:14:00'),
 ('BATCH-C-0918', 'C型号', 'ASSY-1', '缺件', 565, 500, '2026-09-18 09:21:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0922', 'C型号', 'TEST-1', '缺件', 688, 600, '2026-09-22 10:28:00'),
 ('BATCH-D-0926', 'D型号', 'SMT-2', '缺件', 815, 700, '2026-09-26 11:35:00'),
 ('BATCH-D-0930', 'D型号', 'DIP-1', '缺件', 236, 200, '2026-09-30 12:42:00'),
 ('BATCH-D-1002', 'D型号', 'DIP-2', '缺件', 359, 300, '2026-10-02 13:49:00'),
 ('BATCH-D-1005', 'D型号', 'ASSY-1', '缺件', 486, 400, '2026-10-05 14:56:00'),
 ('BATCH-D-1008', 'D型号', 'TEST-1', '缺件', 616, 500, '2026-10-08 15:03:00'),
 ('BATCH-E-0820', 'E型号', 'SMT-2', '缺件', 750, 600, '2026-08-20 16:10:00'),
 ('BATCH-E-0824', 'E型号', 'DIP-1', '缺件', 886, 700, '2026-08-24 17:17:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0828', 'E型号', 'DIP-2', '缺件', 256, 200, '2026-08-28 09:24:00'),
 ('BATCH-E-0902', 'E型号', 'ASSY-1', '缺件', 390, 300, '2026-09-02 10:31:00'),
 ('BATCH-E-0906', 'E型号', 'TEST-1', '缺件', 527, 400, '2026-09-06 11:38:00'),
 ('BATCH-C-0910', 'C型号', 'SMT-2', '元件偏移', 667, 500, '2026-09-10 12:45:00'),
 ('BATCH-C-0914', 'C型号', 'DIP-1', '元件偏移', 811, 600, '2026-09-14 13:52:00'),
 ('BATCH-C-0918', 'C型号', 'DIP-2', '元件偏移', 958, 700, '2026-09-18 14:59:00'),
 ('BATCH-C-0922', 'C型号', 'ASSY-1', '元件偏移', 277, 200, '2026-09-22 15:06:00'),
 ('BATCH-C-0926', 'C型号', 'TEST-1', '元件偏移', 420, 300, '2026-09-26 16:13:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0930', 'D型号', 'SMT-2', '元件偏移', 568, 400, '2026-09-30 17:20:00'),
 ('BATCH-D-1002', 'D型号', 'DIP-1', '元件偏移', 718, 500, '2026-10-02 09:27:00'),
 ('BATCH-D-1005', 'D型号', 'DIP-2', '元件偏移', 872, 600, '2026-10-05 10:34:00'),
 ('BATCH-D-1008', 'D型号', 'ASSY-1', '元件偏移', 49, 700, '2026-10-08 11:41:00'),
 ('BATCH-D-0820', 'D型号', 'TEST-1', '元件偏移', 17, 200, '2026-08-20 12:48:00'),
 ('BATCH-E-0824', 'E型号', 'SMT-2', '元件偏移', 31, 300, '2026-08-24 13:55:00'),
 ('BATCH-E-0828', 'E型号', 'DIP-1', '元件偏移', 48, 400, '2026-08-28 14:02:00'),
 ('BATCH-E-0902', 'E型号', 'DIP-2', '元件偏移', 69, 500, '2026-09-02 15:09:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0906', 'E型号', 'ASSY-1', '元件偏移', 93, 600, '2026-09-06 16:16:00'),
 ('BATCH-E-0910', 'E型号', 'TEST-1', '元件偏移', 121, 700, '2026-09-10 17:23:00'),
 ('BATCH-C-0914', 'C型号', 'SMT-2', '空洞', 38, 200, '2026-09-14 09:30:00'),
 ('BATCH-C-0918', 'C型号', 'DIP-1', '空洞', 62, 300, '2026-09-18 10:37:00'),
 ('BATCH-C-0922', 'C型号', 'DIP-2', '空洞', 89, 400, '2026-09-22 11:44:00'),
 ('BATCH-C-0926', 'C型号', 'ASSY-1', '空洞', 120, 500, '2026-09-26 12:51:00'),
 ('BATCH-C-0930', 'C型号', 'TEST-1', '空洞', 154, 600, '2026-09-30 13:58:00'),
 ('BATCH-D-1002', 'D型号', 'SMT-2', '空洞', 192, 700, '2026-10-02 14:05:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-1005', 'D型号', 'DIP-1', '空洞', 58, 200, '2026-10-05 15:12:00'),
 ('BATCH-D-1008', 'D型号', 'DIP-2', '空洞', 92, 300, '2026-10-08 16:19:00'),
 ('BATCH-D-0820', 'D型号', 'ASSY-1', '空洞', 130, 400, '2026-08-20 17:26:00'),
 ('BATCH-D-0824', 'D型号', 'TEST-1', '空洞', 171, 500, '2026-08-24 09:33:00'),
 ('BATCH-E-0828', 'E型号', 'SMT-2', '空洞', 216, 600, '2026-08-28 10:40:00'),
 ('BATCH-E-0902', 'E型号', 'DIP-1', '空洞', 263, 700, '2026-09-02 11:47:00'),
 ('BATCH-E-0906', 'E型号', 'DIP-2', '空洞', 78, 200, '2026-09-06 12:54:00'),
 ('BATCH-E-0910', 'E型号', 'ASSY-1', '空洞', 123, 300, '2026-09-10 13:01:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0914', 'E型号', 'TEST-1', '空洞', 171, 400, '2026-09-14 14:08:00'),
 ('BATCH-C-0918', 'C型号', 'SMT-2', '焊球不良', 222, 500, '2026-09-18 15:15:00'),
 ('BATCH-C-0922', 'C型号', 'DIP-1', '焊球不良', 277, 600, '2026-09-22 16:22:00'),
 ('BATCH-C-0926', 'C型号', 'DIP-2', '焊球不良', 335, 700, '2026-09-26 17:29:00'),
 ('BATCH-C-0930', 'C型号', 'ASSY-1', '焊球不良', 99, 200, '2026-09-30 09:36:00'),
 ('BATCH-C-1002', 'C型号', 'TEST-1', '焊球不良', 153, 300, '2026-10-02 10:43:00'),
 ('BATCH-D-1005', 'D型号', 'SMT-2', '焊球不良', 212, 400, '2026-10-05 11:50:00'),
 ('BATCH-D-1008', 'D型号', 'DIP-1', '焊球不良', 273, 500, '2026-10-08 12:57:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0820', 'D型号', 'DIP-2', '焊球不良', 338, 600, '2026-08-20 13:04:00'),
 ('BATCH-D-0824', 'D型号', 'ASSY-1', '焊球不良', 406, 700, '2026-08-24 14:11:00'),
 ('BATCH-D-0828', 'D型号', 'TEST-1', '焊球不良', 119, 200, '2026-08-28 15:18:00'),
 ('BATCH-E-0902', 'E型号', 'SMT-2', '焊球不良', 184, 300, '2026-09-02 16:25:00'),
 ('BATCH-E-0906', 'E型号', 'DIP-1', '焊球不良', 252, 400, '2026-09-06 17:32:00'),
 ('BATCH-E-0910', 'E型号', 'DIP-2', '焊球不良', 324, 500, '2026-09-10 09:39:00'),
 ('BATCH-E-0914', 'E型号', 'ASSY-1', '焊球不良', 399, 600, '2026-09-14 10:46:00'),
 ('BATCH-E-0918', 'E型号', 'TEST-1', '焊球不良', 478, 700, '2026-09-18 11:53:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0922', 'C型号', 'SMT-2', '引脚氧化', 140, 200, '2026-09-22 12:00:00'),
 ('BATCH-C-0926', 'C型号', 'DIP-1', '引脚氧化', 215, 300, '2026-09-26 13:07:00'),
 ('BATCH-C-0930', 'C型号', 'DIP-2', '引脚氧化', 293, 400, '2026-09-30 14:14:00'),
 ('BATCH-C-1002', 'C型号', 'ASSY-1', '引脚氧化', 375, 500, '2026-10-02 15:21:00'),
 ('BATCH-C-1005', 'C型号', 'TEST-1', '引脚氧化', 460, 600, '2026-10-05 16:28:00'),
 ('BATCH-D-1008', 'D型号', 'SMT-2', '引脚氧化', 549, 700, '2026-10-08 17:35:00'),
 ('BATCH-D-0820', 'D型号', 'DIP-1', '引脚氧化', 160, 200, '2026-08-20 09:42:00'),
 ('BATCH-D-0824', 'D型号', 'DIP-2', '引脚氧化', 245, 300, '2026-08-24 10:49:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0828', 'D型号', 'ASSY-1', '引脚氧化', 334, 400, '2026-08-28 11:56:00'),
 ('BATCH-D-0902', 'D型号', 'TEST-1', '引脚氧化', 426, 500, '2026-09-02 12:03:00'),
 ('BATCH-E-0906', 'E型号', 'SMT-2', '引脚氧化', 522, 600, '2026-09-06 13:10:00'),
 ('BATCH-E-0910', 'E型号', 'DIP-1', '引脚氧化', 620, 700, '2026-09-10 14:17:00'),
 ('BATCH-E-0914', 'E型号', 'DIP-2', '引脚氧化', 180, 200, '2026-09-14 15:24:00'),
 ('BATCH-E-0918', 'E型号', 'ASSY-1', '引脚氧化', 276, 300, '2026-09-18 16:31:00'),
 ('BATCH-E-0922', 'E型号', 'TEST-1', '引脚氧化', 375, 400, '2026-09-22 17:38:00'),
 ('BATCH-C-0926', 'C型号', 'SMT-2', '抛料', 477, 500, '2026-09-26 09:45:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0930', 'C型号', 'DIP-1', '抛料', 583, 600, '2026-09-30 10:52:00'),
 ('BATCH-C-1002', 'C型号', 'DIP-2', '抛料', 692, 700, '2026-10-02 11:59:00'),
 ('BATCH-C-1005', 'C型号', 'ASSY-1', '抛料', 201, 200, '2026-10-05 12:06:00'),
 ('BATCH-C-1008', 'C型号', 'TEST-1', '抛料', 306, 300, '2026-10-08 13:13:00'),
 ('BATCH-D-0820', 'D型号', 'SMT-2', '抛料', 416, 400, '2026-08-20 14:20:00'),
 ('BATCH-D-0824', 'D型号', 'DIP-1', '抛料', 528, 500, '2026-08-24 15:27:00'),
 ('BATCH-D-0828', 'D型号', 'DIP-2', '抛料', 644, 600, '2026-08-28 16:34:00'),
 ('BATCH-D-0902', 'D型号', 'ASSY-1', '抛料', 763, 700, '2026-09-02 17:41:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0906', 'D型号', 'TEST-1', '抛料', 221, 200, '2026-09-06 09:48:00'),
 ('BATCH-E-0910', 'E型号', 'SMT-2', '抛料', 337, 300, '2026-09-10 10:55:00'),
 ('BATCH-E-0914', 'E型号', 'DIP-1', '抛料', 456, 400, '2026-09-14 11:02:00'),
 ('BATCH-E-0918', 'E型号', 'DIP-2', '抛料', 579, 500, '2026-09-18 12:09:00'),
 ('BATCH-E-0922', 'E型号', 'ASSY-1', '抛料', 705, 600, '2026-09-22 13:16:00'),
 ('BATCH-E-0926', 'E型号', 'TEST-1', '抛料', 835, 700, '2026-09-26 14:23:00'),
 ('BATCH-C-0930', 'C型号', 'SMT-2', '锡膏偏移', 242, 200, '2026-09-30 15:30:00'),
 ('BATCH-C-1002', 'C型号', 'DIP-1', '锡膏偏移', 368, 300, '2026-10-02 16:37:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-1005', 'C型号', 'DIP-2', '锡膏偏移', 497, 400, '2026-10-05 17:44:00'),
 ('BATCH-C-1008', 'C型号', 'ASSY-1', '锡膏偏移', 630, 500, '2026-10-08 09:51:00'),
 ('BATCH-C-0820', 'C型号', 'TEST-1', '锡膏偏移', 766, 600, '2026-08-20 10:58:00'),
 ('BATCH-D-0824', 'D型号', 'SMT-2', '锡膏偏移', 906, 700, '2026-08-24 11:05:00'),
 ('BATCH-D-0828', 'D型号', 'DIP-1', '锡膏偏移', 262, 200, '2026-08-28 12:12:00'),
 ('BATCH-D-0902', 'D型号', 'DIP-2', '锡膏偏移', 398, 300, '2026-09-02 13:19:00'),
 ('BATCH-D-0906', 'D型号', 'ASSY-1', '锡膏偏移', 538, 400, '2026-09-06 14:26:00'),
 ('BATCH-D-0910', 'D型号', 'TEST-1', '锡膏偏移', 681, 500, '2026-09-10 15:33:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0914', 'E型号', 'SMT-2', '锡膏偏移', 828, 600, '2026-09-14 16:40:00'),
 ('BATCH-E-0918', 'E型号', 'DIP-1', '锡膏偏移', 977, 700, '2026-09-18 17:47:00'),
 ('BATCH-E-0922', 'E型号', 'DIP-2', '锡膏偏移', 282, 200, '2026-09-22 09:54:00'),
 ('BATCH-E-0926', 'E型号', 'ASSY-1', '锡膏偏移', 429, 300, '2026-09-26 10:01:00'),
 ('BATCH-E-0930', 'E型号', 'TEST-1', '锡膏偏移', 579, 400, '2026-09-30 11:08:00'),
 ('BATCH-C-1002', 'C型号', 'SMT-2', '连锡', 32, 500, '2026-10-02 12:15:00'),
 ('BATCH-C-1005', 'C型号', 'DIP-1', '连锡', 49, 600, '2026-10-05 13:22:00'),
 ('BATCH-C-1008', 'C型号', 'DIP-2', '连锡', 69, 700, '2026-10-08 14:29:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0820', 'C型号', 'ASSY-1', '连锡', 23, 200, '2026-08-20 15:36:00'),
 ('BATCH-C-0824', 'C型号', 'TEST-1', '连锡', 39, 300, '2026-08-24 16:43:00'),
 ('BATCH-D-0828', 'D型号', 'SMT-2', '连锡', 60, 400, '2026-08-28 17:50:00'),
 ('BATCH-D-0902', 'D型号', 'DIP-1', '连锡', 83, 500, '2026-09-02 09:57:00'),
 ('BATCH-D-0906', 'D型号', 'DIP-2', '连锡', 110, 600, '2026-09-06 10:04:00'),
 ('BATCH-D-0910', 'D型号', 'ASSY-1', '连锡', 140, 700, '2026-09-10 11:11:00'),
 ('BATCH-D-0914', 'D型号', 'TEST-1', '连锡', 43, 200, '2026-09-14 12:18:00'),
 ('BATCH-E-0918', 'E型号', 'SMT-2', '连锡', 70, 300, '2026-09-18 13:25:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0922', 'E型号', 'DIP-1', '连锡', 100, 400, '2026-09-22 14:32:00'),
 ('BATCH-E-0926', 'E型号', 'DIP-2', '连锡', 134, 500, '2026-09-26 15:39:00'),
 ('BATCH-E-0930', 'E型号', 'ASSY-1', '连锡', 171, 600, '2026-09-30 16:46:00'),
 ('BATCH-E-1002', 'E型号', 'TEST-1', '连锡', 212, 700, '2026-10-02 17:53:00'),
 ('BATCH-C-1005', 'C型号', 'SMT-2', '虚焊', 64, 200, '2026-10-05 09:00:00'),
 ('BATCH-C-1008', 'C型号', 'DIP-1', '虚焊', 101, 300, '2026-10-08 10:07:00'),
 ('BATCH-C-0820', 'C型号', 'DIP-2', '虚焊', 141, 400, '2026-08-20 11:14:00'),
 ('BATCH-C-0824', 'C型号', 'ASSY-1', '虚焊', 185, 500, '2026-08-24 12:21:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-C-0828', 'C型号', 'TEST-1', '虚焊', 232, 600, '2026-08-28 13:28:00'),
 ('BATCH-D-0902', 'D型号', 'SMT-2', '虚焊', 283, 700, '2026-09-02 14:35:00'),
 ('BATCH-D-0906', 'D型号', 'DIP-1', '虚焊', 84, 200, '2026-09-06 15:42:00'),
 ('BATCH-D-0910', 'D型号', 'DIP-2', '虚焊', 131, 300, '2026-09-10 16:49:00'),
 ('BATCH-D-0914', 'D型号', 'ASSY-1', '虚焊', 182, 400, '2026-09-14 17:56:00'),
 ('BATCH-D-0918', 'D型号', 'TEST-1', '虚焊', 236, 500, '2026-09-18 09:03:00'),
 ('BATCH-E-0922', 'E型号', 'SMT-2', '虚焊', 294, 600, '2026-09-22 10:10:00'),
 ('BATCH-E-0926', 'E型号', 'DIP-1', '虚焊', 354, 700, '2026-09-26 11:17:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-0930', 'E型号', 'DIP-2', '虚焊', 104, 200, '2026-09-30 12:24:00'),
 ('BATCH-E-1002', 'E型号', 'ASSY-1', '虚焊', 162, 300, '2026-10-02 13:31:00'),
 ('BATCH-E-1005', 'E型号', 'TEST-1', '虚焊', 223, 400, '2026-10-05 14:38:00'),
 ('BATCH-C-1008', 'C型号', 'SMT-2', '立碑', 287, 500, '2026-10-08 15:45:00'),
 ('BATCH-C-0820', 'C型号', 'DIP-1', '立碑', 355, 600, '2026-08-20 16:52:00'),
 ('BATCH-C-0824', 'C型号', 'DIP-2', '立碑', 426, 700, '2026-08-24 17:59:00'),
 ('BATCH-C-0828', 'C型号', 'ASSY-1', '立碑', 125, 200, '2026-08-28 09:06:00'),
 ('BATCH-C-0902', 'C型号', 'TEST-1', '立碑', 192, 300, '2026-09-02 10:13:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-D-0906', 'D型号', 'SMT-2', '立碑', 264, 400, '2026-09-06 11:20:00'),
 ('BATCH-D-0910', 'D型号', 'DIP-1', '立碑', 338, 500, '2026-09-10 12:27:00'),
 ('BATCH-D-0914', 'D型号', 'DIP-2', '立碑', 416, 600, '2026-09-14 13:34:00'),
 ('BATCH-D-0918', 'D型号', 'ASSY-1', '立碑', 497, 700, '2026-09-18 14:41:00'),
 ('BATCH-D-0922', 'D型号', 'TEST-1', '立碑', 145, 200, '2026-09-22 15:48:00'),
 ('BATCH-E-0926', 'E型号', 'SMT-2', '立碑', 223, 300, '2026-09-26 16:55:00'),
 ('BATCH-E-0930', 'E型号', 'DIP-1', '立碑', 304, 400, '2026-09-30 17:02:00'),
 ('BATCH-E-1002', 'E型号', 'DIP-2', '立碑', 389, 500, '2026-10-02 09:09:00');
INSERT INTO quality_defect (batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time) VALUES
 ('BATCH-E-1005', 'E型号', 'ASSY-1', '立碑', 477, 600, '2026-10-05 10:16:00'),
 ('BATCH-E-1008', 'E型号', 'TEST-1', '立碑', 569, 700, '2026-10-08 11:23:00');

-- ---------- material_batch：追加 66 行 ----------
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-0820', 'SP-001', '无铅锡膏SAC305', '千住金属', 'fail', 'http://coa.example.com/mat-sp-0820.pdf', '2026-08-20 08:00:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 09:11:00'),
 ('MAT-IC-0914', 'IC-100', '主控芯片', 'STMicroelectronics', 'pass', 'http://coa.example.com/mat-ic-0914.pdf', '2026-09-14 10:22:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 11:33:00'),
 ('MAT-CON-1005', 'CON-015', '连接器', '立讯精密', 'pass', 'http://coa.example.com/mat-con-1005.pdf', '2026-10-05 12:44:00'),
 ('MAT-RES-0824', 'RES-003', '贴片电阻', '国巨电子', 'fail', 'http://coa.example.com/mat-res-0824.pdf', '2026-08-24 13:55:00'),
 ('MAT-LED-0910', 'LED-021', '背光LED', '京东方', 'pass', 'http://coa.example.com/mat-led-0910.pdf', '2026-09-10 08:06:00'),
 ('MAT-FPC-1012', 'FPC-009', '柔性排线', '鹏鼎控股', 'pass', 'http://coa.example.com/mat-fpc-1012.pdf', '2026-10-12 09:17:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-1002', 'SP-001', '无铅锡膏SAC305', '千住金属', 'pass', 'http://coa.example.com/mat-sp-1002.pdf', '2026-10-02 10:28:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 11:39:00'),
 ('MAT-IC-0828', 'IC-100', '主控芯片', 'STMicroelectronics', 'fail', 'http://coa.example.com/mat-ic-0828.pdf', '2026-08-28 12:50:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 13:01:00'),
 ('MAT-CON-0930', 'CON-015', '连接器', '立讯精密', 'pass', 'http://coa.example.com/mat-con-0930.pdf', '2026-09-30 08:12:00'),
 ('MAT-RES-1012', 'RES-003', '贴片电阻', '国巨电子', 'pass', 'http://coa.example.com/mat-res-1012.pdf', '2026-10-12 09:23:00'),
 ('MAT-LED-0902', 'LED-021', '背光LED', '京东方', 'pass', 'http://coa.example.com/mat-led-0902.pdf', '2026-09-02 10:34:00'),
 ('MAT-FPC-0902', 'FPC-009', '柔性排线', '鹏鼎控股', 'fail', 'http://coa.example.com/mat-fpc-0902.pdf', '2026-09-02 11:45:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-0926', 'SP-001', '无铅锡膏SAC305', '千住金属', 'pass', 'http://coa.example.com/mat-sp-0926.pdf', '2026-09-26 12:56:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 13:07:00'),
 ('MAT-IC-0828', 'IC-100', '主控芯片', 'STMicroelectronics', 'pass', 'http://coa.example.com/mat-ic-0828.pdf', '2026-08-28 08:18:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 09:29:00'),
 ('MAT-CON-0906', 'CON-015', '连接器', '立讯精密', 'fail', 'http://coa.example.com/mat-con-0906.pdf', '2026-09-06 10:40:00'),
 ('MAT-RES-1012', 'RES-003', '贴片电阻', '国巨电子', 'pass', 'http://coa.example.com/mat-res-1012.pdf', '2026-10-12 11:51:00'),
 ('MAT-LED-0824', 'LED-021', '背光LED', '京东方', 'pass', 'http://coa.example.com/mat-led-0824.pdf', '2026-08-24 12:02:00'),
 ('MAT-FPC-1012', 'FPC-009', '柔性排线', '鹏鼎控股', 'pass', 'http://coa.example.com/mat-fpc-1012.pdf', '2026-10-12 13:13:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-0918', 'SP-001', '无铅锡膏SAC305', '千住金属', 'pass', 'http://coa.example.com/mat-sp-0918.pdf', '2026-09-18 08:24:00'),
 ('MAT-PCB-0910', 'PCB-022', 'PCB板', '深南电路', 'fail', 'http://coa.example.com/mat-pcb-0910.pdf', '2026-09-10 09:35:00'),
 ('MAT-IC-0820', 'IC-100', '主控芯片', 'STMicroelectronics', 'pass', 'http://coa.example.com/mat-ic-0820.pdf', '2026-08-20 10:46:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 11:57:00'),
 ('MAT-CON-0914', 'CON-015', '连接器', '立讯精密', 'pass', 'http://coa.example.com/mat-con-0914.pdf', '2026-09-14 12:08:00'),
 ('MAT-RES-1012', 'RES-003', '贴片电阻', '国巨电子', 'pass', 'http://coa.example.com/mat-res-1012.pdf', '2026-10-12 13:19:00'),
 ('MAT-LED-0914', 'LED-021', '背光LED', '京东方', 'fail', 'http://coa.example.com/mat-led-0914.pdf', '2026-09-14 08:30:00'),
 ('MAT-FPC-1012', 'FPC-009', '柔性排线', '鹏鼎控股', 'pass', 'http://coa.example.com/mat-fpc-1012.pdf', '2026-10-12 09:41:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-0910', 'SP-001', '无铅锡膏SAC305', '千住金属', 'pass', 'http://coa.example.com/mat-sp-0910.pdf', '2026-09-10 10:52:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 11:03:00'),
 ('MAT-IC-1002', 'IC-100', '主控芯片', 'STMicroelectronics', 'pass', 'http://coa.example.com/mat-ic-1002.pdf', '2026-10-02 12:14:00'),
 ('MAT-CAP-0918', 'CAP-007', '贴片电容', '村田制作所', 'fail', 'http://coa.example.com/mat-cap-0918.pdf', '2026-09-18 13:25:00'),
 ('MAT-CON-0906', 'CON-015', '连接器', '立讯精密', 'pass', 'http://coa.example.com/mat-con-0906.pdf', '2026-09-06 08:36:00'),
 ('MAT-RES-1012', 'RES-003', '贴片电阻', '国巨电子', 'pass', 'http://coa.example.com/mat-res-1012.pdf', '2026-10-12 09:47:00'),
 ('MAT-LED-0930', 'LED-021', '背光LED', '京东方', 'pass', 'http://coa.example.com/mat-led-0930.pdf', '2026-09-30 10:58:00'),
 ('MAT-FPC-1012', 'FPC-009', '柔性排线', '鹏鼎控股', 'pass', 'http://coa.example.com/mat-fpc-1012.pdf', '2026-10-12 11:09:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-0922', 'SP-001', '无铅锡膏SAC305', '千住金属', 'fail', 'http://coa.example.com/mat-sp-0922.pdf', '2026-09-22 12:20:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 13:31:00'),
 ('MAT-IC-0926', 'IC-100', '主控芯片', 'STMicroelectronics', 'pass', 'http://coa.example.com/mat-ic-0926.pdf', '2026-09-26 08:42:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 09:53:00'),
 ('MAT-CON-0828', 'CON-015', '连接器', '立讯精密', 'pass', 'http://coa.example.com/mat-con-0828.pdf', '2026-08-28 10:04:00'),
 ('MAT-RES-0926', 'RES-003', '贴片电阻', '国巨电子', 'fail', 'http://coa.example.com/mat-res-0926.pdf', '2026-09-26 11:15:00'),
 ('MAT-LED-0922', 'LED-021', '背光LED', '京东方', 'pass', 'http://coa.example.com/mat-led-0922.pdf', '2026-09-22 12:26:00'),
 ('MAT-FPC-1012', 'FPC-009', '柔性排线', '鹏鼎控股', 'pass', 'http://coa.example.com/mat-fpc-1012.pdf', '2026-10-12 13:37:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-0824', 'SP-001', '无铅锡膏SAC305', '千住金属', 'pass', 'http://coa.example.com/mat-sp-0824.pdf', '2026-08-24 08:48:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 09:59:00'),
 ('MAT-IC-0930', 'IC-100', '主控芯片', 'STMicroelectronics', 'fail', 'http://coa.example.com/mat-ic-0930.pdf', '2026-09-30 10:10:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 11:21:00'),
 ('MAT-CON-0820', 'CON-015', '连接器', '立讯精密', 'pass', 'http://coa.example.com/mat-con-0820.pdf', '2026-08-20 12:32:00'),
 ('MAT-RES-1012', 'RES-003', '贴片电阻', '国巨电子', 'pass', 'http://coa.example.com/mat-res-1012.pdf', '2026-10-12 13:43:00'),
 ('MAT-LED-0914', 'LED-021', '背光LED', '京东方', 'pass', 'http://coa.example.com/mat-led-0914.pdf', '2026-09-14 08:54:00'),
 ('MAT-FPC-1002', 'FPC-009', '柔性排线', '鹏鼎控股', 'fail', 'http://coa.example.com/mat-fpc-1002.pdf', '2026-10-02 09:05:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('MAT-SP-1005', 'SP-001', '无铅锡膏SAC305', '千住金属', 'pass', 'http://coa.example.com/mat-sp-1005.pdf', '2026-10-05 10:16:00'),
 ('MAT-PCB-1012', 'PCB-022', 'PCB板', '深南电路', 'pass', 'http://coa.example.com/mat-pcb-1012.pdf', '2026-10-12 11:27:00'),
 ('MAT-IC-0910', 'IC-100', '主控芯片', 'STMicroelectronics', 'pass', 'http://coa.example.com/mat-ic-0910.pdf', '2026-09-10 12:38:00'),
 ('MAT-CAP-1012', 'CAP-007', '贴片电容', '村田制作所', 'pass', 'http://coa.example.com/mat-cap-1012.pdf', '2026-10-12 13:49:00'),
 ('BATCH-C-0820', 'PCB-022', 'A型号PCB', '深南电路', 'pass', 'http://coa.example.com/trace0.pdf', '2026-08-20 09:00:00'),
 ('BATCH-C-0824', 'PCB-022', 'A型号PCB', '深南电路', 'pass', 'http://coa.example.com/trace1.pdf', '2026-08-24 09:00:00'),
 ('BATCH-C-0828', 'PCB-022', 'A型号PCB', '深南电路', 'pass', 'http://coa.example.com/trace2.pdf', '2026-08-28 09:00:00'),
 ('BATCH-C-0902', 'PCB-022', 'A型号PCB', '深南电路', 'pass', 'http://coa.example.com/trace3.pdf', '2026-09-02 09:00:00');
INSERT INTO material_batch (batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time) VALUES
 ('BATCH-C-0906', 'PCB-022', 'A型号PCB', '深南电路', 'pass', 'http://coa.example.com/trace4.pdf', '2026-09-06 09:00:00'),
 ('BATCH-C-0910', 'PCB-022', 'A型号PCB', '深南电路', 'pass', 'http://coa.example.com/trace5.pdf', '2026-09-10 09:00:00');

-- ---------- shipment：追加 50 行 ----------
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20260820-10', 'C型号', 300, '待发货', '2026-08-24 10:00:00', '2026-08-20 16:00:00'),
 ('SO-20260824-11', 'D型号', 550, '在途', '2026-08-28 11:00:00', '2026-08-24 16:00:00'),
 ('SO-20260828-12', 'E型号', 800, '已签收', '2026-09-02 12:00:00', '2026-08-28 16:00:00'),
 ('SO-20260902-13', 'C型号', 1050, '部分发货', '2026-09-06 13:00:00', '2026-09-02 16:00:00'),
 ('SO-20260906-14', 'D型号', 1300, '待发货', '2026-09-10 14:00:00', '2026-09-06 16:00:00'),
 ('SO-20260910-15', 'E型号', 1550, '在途', '2026-09-14 15:00:00', '2026-09-10 16:00:00'),
 ('SO-20260914-16', 'C型号', 1800, '已签收', '2026-09-18 16:00:00', '2026-09-14 16:00:00'),
 ('SO-20260918-17', 'D型号', 2050, '部分发货', '2026-09-22 17:00:00', '2026-09-18 16:00:00');
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20260922-18', 'E型号', 300, '待发货', '2026-09-26 10:00:00', '2026-09-22 16:00:00'),
 ('SO-20260926-19', 'C型号', 550, '在途', '2026-09-30 11:00:00', '2026-09-26 16:00:00'),
 ('SO-20260930-20', 'D型号', 800, '已签收', '2026-10-02 12:00:00', '2026-09-30 16:00:00'),
 ('SO-20261002-21', 'E型号', 1050, '部分发货', '2026-10-05 13:00:00', '2026-10-02 16:00:00'),
 ('SO-20261005-22', 'C型号', 1300, '待发货', '2026-10-08 14:00:00', '2026-10-05 16:00:00'),
 ('SO-20261008-23', 'D型号', 1550, '在途', '2026-10-10 15:00:00', '2026-10-08 16:00:00'),
 ('SO-20260820-24', 'E型号', 1800, '已签收', '2026-08-24 16:00:00', '2026-08-20 16:00:00'),
 ('SO-20260824-25', 'C型号', 2050, '部分发货', '2026-08-28 17:00:00', '2026-08-24 16:00:00');
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20260828-26', 'D型号', 300, '待发货', '2026-09-02 10:00:00', '2026-08-28 16:00:00'),
 ('SO-20260902-27', 'E型号', 550, '在途', '2026-09-06 11:00:00', '2026-09-02 16:00:00'),
 ('SO-20260906-28', 'C型号', 800, '已签收', '2026-09-10 12:00:00', '2026-09-06 16:00:00'),
 ('SO-20260910-29', 'D型号', 1050, '部分发货', '2026-09-14 13:00:00', '2026-09-10 16:00:00'),
 ('SO-20260914-30', 'E型号', 1300, '待发货', '2026-09-18 14:00:00', '2026-09-14 16:00:00'),
 ('SO-20260918-31', 'C型号', 1550, '在途', '2026-09-22 15:00:00', '2026-09-18 16:00:00'),
 ('SO-20260922-32', 'D型号', 1800, '已签收', '2026-09-26 16:00:00', '2026-09-22 16:00:00'),
 ('SO-20260926-33', 'E型号', 2050, '部分发货', '2026-09-30 17:00:00', '2026-09-26 16:00:00');
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20260930-34', 'C型号', 300, '待发货', '2026-10-02 10:00:00', '2026-09-30 16:00:00'),
 ('SO-20261002-35', 'D型号', 550, '在途', '2026-10-05 11:00:00', '2026-10-02 16:00:00'),
 ('SO-20261005-36', 'E型号', 800, '已签收', '2026-10-08 12:00:00', '2026-10-05 16:00:00'),
 ('SO-20261008-37', 'C型号', 1050, '部分发货', '2026-10-10 13:00:00', '2026-10-08 16:00:00'),
 ('SO-20260820-38', 'D型号', 1300, '待发货', '2026-08-24 14:00:00', '2026-08-20 16:00:00'),
 ('SO-20260824-39', 'E型号', 1550, '在途', '2026-08-28 15:00:00', '2026-08-24 16:00:00'),
 ('SO-20260828-40', 'C型号', 1800, '已签收', '2026-09-02 16:00:00', '2026-08-28 16:00:00'),
 ('SO-20260902-41', 'D型号', 2050, '部分发货', '2026-09-06 17:00:00', '2026-09-02 16:00:00');
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20260906-42', 'E型号', 300, '待发货', '2026-09-10 10:00:00', '2026-09-06 16:00:00'),
 ('SO-20260910-43', 'C型号', 550, '在途', '2026-09-14 11:00:00', '2026-09-10 16:00:00'),
 ('SO-20260914-44', 'D型号', 800, '已签收', '2026-09-18 12:00:00', '2026-09-14 16:00:00'),
 ('SO-20260918-45', 'E型号', 1050, '部分发货', '2026-09-22 13:00:00', '2026-09-18 16:00:00'),
 ('SO-20260922-46', 'C型号', 1300, '待发货', '2026-09-26 14:00:00', '2026-09-22 16:00:00'),
 ('SO-20260926-47', 'D型号', 1550, '在途', '2026-09-30 15:00:00', '2026-09-26 16:00:00'),
 ('SO-20260930-48', 'E型号', 1800, '已签收', '2026-10-02 16:00:00', '2026-09-30 16:00:00'),
 ('SO-20261002-49', 'C型号', 2050, '部分发货', '2026-10-05 17:00:00', '2026-10-02 16:00:00');
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20261005-50', 'D型号', 300, '待发货', '2026-10-08 10:00:00', '2026-10-05 16:00:00'),
 ('SO-20261008-51', 'E型号', 550, '在途', '2026-10-10 11:00:00', '2026-10-08 16:00:00'),
 ('SO-20260820-52', 'C型号', 800, '已签收', '2026-08-24 12:00:00', '2026-08-20 16:00:00'),
 ('SO-20260824-53', 'D型号', 1050, '部分发货', '2026-08-28 13:00:00', '2026-08-24 16:00:00'),
 ('SO-20260828-54', 'E型号', 1300, '待发货', '2026-09-02 14:00:00', '2026-08-28 16:00:00'),
 ('SO-20260902-55', 'C型号', 1550, '在途', '2026-09-06 15:00:00', '2026-09-02 16:00:00'),
 ('SO-20260906-56', 'D型号', 1800, '已签收', '2026-09-10 16:00:00', '2026-09-06 16:00:00'),
 ('SO-20260910-57', 'E型号', 2050, '部分发货', '2026-09-14 17:00:00', '2026-09-10 16:00:00');
INSERT INTO shipment (order_no, product_model, qty, ship_status, eta, updated_at) VALUES
 ('SO-20260914-58', 'C型号', 300, '待发货', '2026-09-18 10:00:00', '2026-09-14 16:00:00'),
 ('SO-20260918-59', 'D型号', 550, '在途', '2026-09-22 11:00:00', '2026-09-18 16:00:00');

-- ---------- equipment_log：追加 80 行 ----------
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-08-20 07:00:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'ASSY-1', '2026-08-24 08:13:00', 'E-215', '贴装压力', '1.20N', 1),
 ('OVN-02', 'TEST-1', '2026-08-28 09:26:00', 'E-230', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'SMT-2', '2026-09-02 10:39:00', 'I-001', '检出率', '99.2%', 0),
 ('CLN-01', 'SMT-1', '2026-09-06 11:52:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'DIP-2', '2026-09-10 12:05:00', 'E-275', '测试针压', '120g', 1),
 ('WAV-01', 'ASSY-1', '2026-09-14 13:18:00', 'I-001', '锡炉温度', '255℃', 0),
 ('XRY-01', 'TEST-1', '2026-09-18 14:31:00', 'E-200', '管电压', '100kV', 1);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-09-22 15:44:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'DIP-1', '2026-09-26 16:57:00', 'I-001', '贴装压力', '1.20N', 0),
 ('OVN-02', 'DIP-2', '2026-09-30 17:10:00', 'E-245', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'ASSY-1', '2026-10-02 18:23:00', 'E-260', '检出率', '99.2%', 1),
 ('CLN-01', 'SMT-1', '2026-10-05 07:36:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'SMT-2', '2026-10-08 08:49:00', 'E-290', '测试针压', '120g', 1),
 ('WAV-01', 'DIP-1', '2026-08-20 09:02:00', 'E-200', '锡炉温度', '255℃（超基准）', 1),
 ('XRY-01', 'DIP-2', '2026-08-24 10:15:00', 'I-001', '管电压', '100kV', 0);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-08-28 11:28:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'TEST-1', '2026-09-02 12:41:00', 'E-245', '贴装压力', '1.20N', 1),
 ('OVN-02', 'SMT-2', '2026-09-06 13:54:00', 'I-001', '温区3温度', '235℃', 0),
 ('AOI-01', 'DIP-1', '2026-09-10 14:07:00', 'E-275', '检出率', '99.2%', 1),
 ('CLN-01', 'SMT-1', '2026-09-14 15:20:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'ASSY-1', '2026-09-18 16:33:00', 'I-001', '测试针压', '120g', 0),
 ('WAV-01', 'TEST-1', '2026-09-22 17:46:00', 'E-215', '锡炉温度', '255℃（超基准）', 1),
 ('XRY-01', 'SMT-2', '2026-09-26 18:59:00', 'E-230', '管电压', '100kV', 1);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-09-30 07:12:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'DIP-2', '2026-10-02 08:25:00', 'E-260', '贴装压力', '1.20N', 1),
 ('OVN-02', 'ASSY-1', '2026-10-05 09:38:00', 'E-275', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'TEST-1', '2026-10-08 10:51:00', 'I-001', '检出率', '99.2%', 0),
 ('CLN-01', 'SMT-1', '2026-08-20 11:04:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'DIP-1', '2026-08-24 12:17:00', 'E-215', '测试针压', '120g', 1),
 ('WAV-01', 'DIP-2', '2026-08-28 13:30:00', 'I-001', '锡炉温度', '255℃', 0),
 ('XRY-01', 'ASSY-1', '2026-09-02 14:43:00', 'E-245', '管电压', '100kV', 1);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-09-06 15:56:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'SMT-2', '2026-09-10 16:09:00', 'I-001', '贴装压力', '1.20N', 0),
 ('OVN-02', 'DIP-1', '2026-09-14 17:22:00', 'E-290', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'DIP-2', '2026-09-18 18:35:00', 'E-200', '检出率', '99.2%', 1),
 ('CLN-01', 'SMT-1', '2026-09-22 07:48:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'TEST-1', '2026-09-26 08:01:00', 'E-230', '测试针压', '120g', 1),
 ('WAV-01', 'SMT-2', '2026-09-30 09:14:00', 'E-245', '锡炉温度', '255℃（超基准）', 1),
 ('XRY-01', 'DIP-1', '2026-10-02 10:27:00', 'I-001', '管电压', '100kV', 0);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-10-05 11:40:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'ASSY-1', '2026-10-08 12:53:00', 'E-290', '贴装压力', '1.20N', 1),
 ('OVN-02', 'TEST-1', '2026-08-20 13:06:00', 'I-001', '温区3温度', '235℃', 0),
 ('AOI-01', 'SMT-2', '2026-08-24 14:19:00', 'E-215', '检出率', '99.2%', 1),
 ('CLN-01', 'SMT-1', '2026-08-28 15:32:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'DIP-2', '2026-09-02 16:45:00', 'I-001', '测试针压', '120g', 0),
 ('WAV-01', 'ASSY-1', '2026-09-06 17:58:00', 'E-260', '锡炉温度', '255℃（超基准）', 1),
 ('XRY-01', 'TEST-1', '2026-09-10 18:11:00', 'E-275', '管电压', '100kV', 1);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-09-14 07:24:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'DIP-1', '2026-09-18 08:37:00', 'E-200', '贴装压力', '1.20N', 1),
 ('OVN-02', 'DIP-2', '2026-09-22 09:50:00', 'E-215', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'ASSY-1', '2026-09-26 10:03:00', 'I-001', '检出率', '99.2%', 0),
 ('CLN-01', 'SMT-1', '2026-09-30 11:16:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'SMT-2', '2026-10-02 12:29:00', 'E-260', '测试针压', '120g', 1),
 ('WAV-01', 'DIP-1', '2026-10-05 13:42:00', 'I-001', '锡炉温度', '255℃', 0),
 ('XRY-01', 'DIP-2', '2026-10-08 14:55:00', 'E-290', '管电压', '100kV', 1);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-08-20 15:08:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'TEST-1', '2026-08-24 16:21:00', 'I-001', '贴装压力', '1.20N', 0),
 ('OVN-02', 'SMT-2', '2026-08-28 17:34:00', 'E-230', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'DIP-1', '2026-09-02 18:47:00', 'E-245', '检出率', '99.2%', 1),
 ('CLN-01', 'SMT-1', '2026-09-06 07:00:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'ASSY-1', '2026-09-10 08:13:00', 'E-275', '测试针压', '120g', 1),
 ('WAV-01', 'TEST-1', '2026-09-14 09:26:00', 'E-290', '锡炉温度', '255℃（超基准）', 1),
 ('XRY-01', 'SMT-2', '2026-09-18 10:39:00', 'I-001', '管电压', '100kV', 0);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-09-22 11:52:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'DIP-2', '2026-09-26 12:05:00', 'E-230', '贴装压力', '1.20N', 1),
 ('OVN-02', 'ASSY-1', '2026-09-30 13:18:00', 'I-001', '温区3温度', '235℃', 0),
 ('AOI-01', 'TEST-1', '2026-10-02 14:31:00', 'E-260', '检出率', '99.2%', 1),
 ('CLN-01', 'SMT-1', '2026-10-05 15:44:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'DIP-1', '2026-10-08 16:57:00', 'I-001', '测试针压', '120g', 0),
 ('WAV-01', 'DIP-2', '2026-08-20 17:10:00', 'E-200', '锡炉温度', '255℃（超基准）', 1),
 ('XRY-01', 'ASSY-1', '2026-08-24 18:23:00', 'E-215', '管电压', '100kV', 1);
INSERT INTO equipment_log (equipment_id, line_code, log_time, alarm_code, param_name, param_value, is_abnormal) VALUES
 ('PRT-01', 'SMT-1', '2026-08-28 07:36:00', 'I-001', '刮刀压力', '0.50MPa', 0),
 ('MNT-02', 'SMT-2', '2026-09-02 08:49:00', 'E-245', '贴装压力', '1.20N', 1),
 ('OVN-02', 'DIP-1', '2026-09-06 09:02:00', 'E-260', '温区3温度', '235℃（超基准）', 1),
 ('AOI-01', 'DIP-2', '2026-09-10 10:15:00', 'I-001', '检出率', '99.2%', 0),
 ('CLN-01', 'SMT-1', '2026-09-14 11:28:00', 'I-001', '清洗液浓度', '8.0%', 0),
 ('TST-01', 'TEST-1', '2026-09-18 12:41:00', 'E-200', '测试针压', '120g', 1),
 ('WAV-01', 'SMT-2', '2026-09-22 13:54:00', 'I-001', '锡炉温度', '255℃', 0),
 ('XRY-01', 'DIP-1', '2026-09-26 14:07:00', 'E-230', '管电压', '100kV', 1);

-- ---------- inventory：追加 6 行 ----------
INSERT INTO inventory (product_model, warehouse, on_hand_qty, reserved_qty, updated_at) VALUES
 ('C型号', '深圳成品仓', 800, 200, '2026-10-15 16:00:00'),
 ('C型号', '苏州成品仓', 500, 120, '2026-10-15 16:00:00'),
 ('D型号', '深圳成品仓', 1100, 290, '2026-10-15 16:00:00'),
 ('D型号', '苏州成品仓', 700, 180, '2026-10-15 16:00:00'),
 ('E型号', '深圳成品仓', 1400, 380, '2026-10-15 16:00:00'),
 ('E型号', '苏州成品仓', 900, 240, '2026-10-15 16:00:00');

-- 完成。
