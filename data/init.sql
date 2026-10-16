-- ============================================================
-- mfg_agent 库初始化：模拟 MES / QMS / SCM / EAM / WMS / TMS
-- 覆盖5个演示场景的数据
-- ============================================================
CREATE DATABASE IF NOT EXISTS mfg_agent DEFAULT CHARSET utf8mb4;
USE mfg_agent;

-- 1. MES：生产产出与OEE
DROP TABLE IF EXISTS production_output;
CREATE TABLE production_output (
  id INT PRIMARY KEY AUTO_INCREMENT,
  record_date DATE,
  line_code VARCHAR(20),
  product_model VARCHAR(50),
  planned_qty INT,
  actual_qty INT,
  oee DECIMAL(5,2),
  stop_duration_min INT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 停线事件
DROP TABLE IF EXISTS line_stop_event;
CREATE TABLE line_stop_event (
  id INT PRIMARY KEY AUTO_INCREMENT,
  line_code VARCHAR(20),
  product_model VARCHAR(50),
  start_time DATETIME,
  end_time DATETIME NULL,
  stop_code VARCHAR(30),
  root_cause VARCHAR(200),
  status VARCHAR(20)
);

-- 3. QMS：品质不良
DROP TABLE IF EXISTS quality_defect;
CREATE TABLE quality_defect (
  id INT PRIMARY KEY AUTO_INCREMENT,
  batch_no VARCHAR(30),
  product_model VARCHAR(50),
  line_code VARCHAR(20),
  defect_type VARCHAR(50),
  defect_qty INT,
  total_qty INT,
  inspect_time DATETIME
);

-- 4. SCM：来料批次
DROP TABLE IF EXISTS material_batch;
CREATE TABLE material_batch (
  id INT PRIMARY KEY AUTO_INCREMENT,
  batch_no VARCHAR(30),
  part_no VARCHAR(50),
  part_name VARCHAR(100),
  supplier VARCHAR(50),
  incoming_result VARCHAR(20),
  coa_url VARCHAR(200),
  arrival_time DATETIME
);

-- 5. EAM：设备日志
DROP TABLE IF EXISTS equipment_log;
CREATE TABLE equipment_log (
  id INT PRIMARY KEY AUTO_INCREMENT,
  equipment_id VARCHAR(30),
  line_code VARCHAR(20),
  log_time DATETIME,
  alarm_code VARCHAR(20),
  param_name VARCHAR(50),
  param_value VARCHAR(100),
  is_abnormal TINYINT
);

-- 6. WMS：库存
DROP TABLE IF EXISTS inventory;
CREATE TABLE inventory (
  id INT PRIMARY KEY AUTO_INCREMENT,
  product_model VARCHAR(50),
  warehouse VARCHAR(30),
  on_hand_qty INT,
  reserved_qty INT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. TMS：发货/在途
DROP TABLE IF EXISTS shipment;
CREATE TABLE shipment (
  id INT PRIMARY KEY AUTO_INCREMENT,
  order_no VARCHAR(30),
  product_model VARCHAR(50),
  qty INT,
  ship_status VARCHAR(20),
  eta DATETIME,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. 客户投诉
DROP TABLE IF EXISTS customer_complaint;
CREATE TABLE customer_complaint (
  id INT PRIMARY KEY AUTO_INCREMENT,
  complaint_no VARCHAR(30),
  customer VARCHAR(50),
  product_model VARCHAR(50),
  batch_no VARCHAR(30),
  defect_desc VARCHAR(500),
  status VARCHAR(20),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 模拟数据（以2026-10-15为"今天"）
-- ============================================================

-- 场景1：A型号 SMT-1线 14:30停线，OEE暴跌
INSERT INTO production_output VALUES
 (1,'2026-10-15','SMT-1','A型号',5000,5000,86.20,0,'2026-10-15 14:00:00'),
 (2,'2026-10-15','SMT-1','A型号',5000,1800,32.10,150,'2026-10-15 16:30:00'),
 (3,'2026-10-15','DIP-2','B型号',3000,2950,84.50,10,'2026-10-15 16:30:00');

INSERT INTO line_stop_event VALUES
 (1,'SMT-1','A型号','2026-10-15 14:30:00',NULL,'EQUIPMENT','待分析','ongoing');

-- 品质：锡膏印刷偏移集中不良
INSERT INTO quality_defect VALUES
 (1,'BATCH-A-1015','A型号','SMT-1','锡膏偏移',32,500,'2026-10-15 14:35:00'),
 (2,'BATCH-A-1015','A型号','SMT-1','锡膏偏移',45,500,'2026-10-15 15:10:00'),
 (3,'BATCH-A-1015','A型号','SMT-1','连锡',8,500,'2026-10-15 15:40:00'),
 (4,'BATCH-B-1014','B型号','DIP-2','虚焊',3,300,'2026-10-15 10:00:00');

-- 供应链：当日锡膏批次COA正常
INSERT INTO material_batch VALUES
 (1,'MAT-SP-1015','SP-001','无铅锡膏SAC305','千住金属','pass','http://coa.example.com/sp1015.pdf','2026-10-15 08:00:00'),
 (2,'MAT-PCB-1013','PCB-022','A型号PCB','深南电路','pass','http://coa.example.com/pcb1013.pdf','2026-10-13 09:00:00'),
 (3,'MAT-IC-1012','IC-100','主控芯片','STMicroelectronics','pass','http://coa.example.com/ic1012.pdf','2026-10-12 11:00:00');

-- 设备：印刷机参数异常
INSERT INTO equipment_log VALUES
 (1,'PRT-01','SMT-1','2026-10-15 14:28:00','E-201','刮刀压力','0.8MPa（基准0.5）',1),
 (2,'PRT-01','SMT-1','2026-10-15 14:29:00','E-205','钢网清洁周期','15min（基准4h）',1),
 (3,'OVN-02','SMT-1','2026-10-15 14:31:00','E-301','回流温区3','245℃（基准235±5）',1),
 (4,'PRT-01','SMT-1','2026-10-15 09:00:00','I-001','开机自检','pass',0);

-- 场景4：库存与发货
INSERT INTO inventory VALUES
 (1,'A型号','深圳成品仓',1200,800,'2026-10-15 16:00:00'),
 (2,'B型号','深圳成品仓',5000,500,'2026-10-15 16:00:00');

INSERT INTO shipment VALUES
 (1,'SO-20261015-01','A型号',2000,'待发货','2026-10-17 10:00:00','2026-10-15 16:00:00'),
 (2,'SO-20261014-02','B型号',1000,'在途','2026-10-16 18:00:00','2026-10-15 12:00:00');

-- 场景3：客户投诉
INSERT INTO customer_complaint VALUES
 (1,'CS-20261012-01','华为终端','A型号','BATCH-A-1008','贴片元件偏移导致功能失效，不良率约2.3%','open','2026-10-12 09:00:00');
