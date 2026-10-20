"""本地SQLite数据库初始化：建表 + 插入5个场景的模拟数据。
用法：uv run python -m mfg_agents.common.init_db
"""
import os
from sqlalchemy import create_engine, text
from .config import settings


SCHEMA = """
DROP TABLE IF EXISTS production_output;
CREATE TABLE production_output (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  record_date TEXT,
  line_code TEXT,
  product_model TEXT,
  planned_qty INTEGER,
  actual_qty INTEGER,
  oee REAL,
  stop_duration_min INTEGER
);

DROP TABLE IF EXISTS line_stop_event;
CREATE TABLE line_stop_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  line_code TEXT,
  product_model TEXT,
  start_time TEXT,
  end_time TEXT,
  stop_code TEXT,
  root_cause TEXT,
  status TEXT
);

DROP TABLE IF EXISTS quality_defect;
CREATE TABLE quality_defect (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_no TEXT,
  product_model TEXT,
  line_code TEXT,
  defect_type TEXT,
  defect_qty INTEGER,
  total_qty INTEGER,
  inspect_time TEXT,
  process_stage TEXT,
  defect_category TEXT,
  product_category TEXT
);

DROP TABLE IF EXISTS material_batch;
CREATE TABLE material_batch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_no TEXT,
  part_no TEXT,
  part_name TEXT,
  supplier TEXT,
  incoming_result TEXT,
  coa_url TEXT,
  arrival_time TEXT
);

DROP TABLE IF EXISTS equipment_log;
CREATE TABLE equipment_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  equipment_id TEXT,
  line_code TEXT,
  log_time TEXT,
  alarm_code TEXT,
  param_name TEXT,
  param_value TEXT,
  is_abnormal INTEGER
);

DROP TABLE IF EXISTS inventory;
CREATE TABLE inventory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_model TEXT,
  warehouse TEXT,
  on_hand_qty INTEGER,
  reserved_qty INTEGER
);

DROP TABLE IF EXISTS shipment;
CREATE TABLE shipment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT,
  product_model TEXT,
  qty INTEGER,
  ship_status TEXT,
  eta TEXT
);

DROP TABLE IF EXISTS customer_complaint;
CREATE TABLE customer_complaint (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  complaint_no TEXT,
  customer TEXT,
  product_model TEXT,
  batch_no TEXT,
  defect_desc TEXT,
  status TEXT
);
"""

SEED_DATA = [
    # production_output
    "INSERT INTO production_output VALUES (1,'2026-10-15','SMT-1','A型号',5000,5000,86.2,0)",
    "INSERT INTO production_output VALUES (2,'2026-10-15','SMT-1','A型号',5000,1800,32.1,150)",
    "INSERT INTO production_output VALUES (3,'2026-10-15','DIP-2','B型号',3000,2950,84.5,10)",
    # line_stop_event
    "INSERT INTO line_stop_event VALUES (1,'SMT-1','A型号','2026-10-15 14:30:00',NULL,'EQUIPMENT','待分析','ongoing')",
    # quality_defect（末三列 process_stage / defect_category / product_category：
    # 既有行分属 SMT/DIP 旧线体，无工段/类别/产品分类标注，置 NULL；
    # 电池域四段数据由 data/seed_quality_pareto.sql（工段/类别）与
    # data/seed_quality_pareto_pcat.sql（产品分类）装载）
    "INSERT INTO quality_defect VALUES (1,'BATCH-A-1015','A型号','SMT-1','锡膏偏移',32,500,'2026-10-15 14:35:00',NULL,NULL,NULL)",
    "INSERT INTO quality_defect VALUES (2,'BATCH-A-1015','A型号','SMT-1','锡膏偏移',45,500,'2026-10-15 15:10:00',NULL,NULL,NULL)",
    "INSERT INTO quality_defect VALUES (3,'BATCH-A-1015','A型号','SMT-1','连锡',8,500,'2026-10-15 15:40:00',NULL,NULL,NULL)",
    "INSERT INTO quality_defect VALUES (4,'BATCH-B-1014','B型号','DIP-2','虚焊',3,300,'2026-10-15 10:00:00',NULL,NULL,NULL)",
    # S3 客诉批次 BATCH-A-1008 的过程不良（8D D4）；line_code=SMT-2，
    # 不影响 line_stop 的 SMT-1/A型号不良分布聚合（回归红线）
    "INSERT INTO quality_defect VALUES (5,'BATCH-A-1008','A型号','SMT-2','贴片偏移',46,2000,'2026-10-12 10:00:00',NULL,NULL,NULL)",
    # material_batch
    "INSERT INTO material_batch VALUES (1,'MAT-SP-1015','SP-001','无铅锡膏SAC305','千住金属','pass','http://coa.example.com/sp1015.pdf','2026-10-15 08:00:00')",
    "INSERT INTO material_batch VALUES (2,'MAT-PCB-1013','PCB-022','A型号PCB','深南电路','pass','http://coa.example.com/pcb1013.pdf','2026-10-13 09:00:00')",
    "INSERT INTO material_batch VALUES (3,'MAT-IC-1012','IC-100','主控芯片','STMicroelectronics','pass','http://coa.example.com/ic1012.pdf','2026-10-12 11:00:00')",
    # 数据红线（§0.2）：新增批次 arrival_time **必须避开 '2026-10-15'**，
    # 否则 supply_batches_today（line_stop"无来料异常"断言）会被破坏。
    "INSERT INTO material_batch VALUES (4,'MAT-SP-1014','SP-001','无铅锡膏SAC305','千住金属','fail','http://coa.example.com/sp1014.pdf','2026-10-14 08:00:00')",
    "INSERT INTO material_batch VALUES (5,'MAT-IC-1011','IC-100','主控芯片','STMicroelectronics','fail','http://coa.example.com/ic1011.pdf','2026-10-11 11:00:00')",
    "INSERT INTO material_batch VALUES (6,'BATCH-A-1008','PCB-022','A型号PCB','深南电路','pass','http://coa.example.com/pcb1008.pdf','2026-10-12 09:00:00')",
    # equipment_log
    "INSERT INTO equipment_log VALUES (1,'PRT-01','SMT-1','2026-10-15 14:28:00','E-201','刮刀压力','0.8MPa（基准0.5）',1)",
    "INSERT INTO equipment_log VALUES (2,'PRT-01','SMT-1','2026-10-15 14:29:00','E-205','钢网清洁周期','15min（基准4h）',1)",
    "INSERT INTO equipment_log VALUES (3,'OVN-02','SMT-1','2026-10-15 14:31:00','E-301','回流温区3','245℃（基准235±5）',1)",
    "INSERT INTO equipment_log VALUES (4,'PRT-01','SMT-1','2026-10-15 09:00:00','I-001','开机自检','pass',0)",
    # inventory
    "INSERT INTO inventory VALUES (1,'A型号','深圳成品仓',1200,800)",
    "INSERT INTO inventory VALUES (2,'B型号','深圳成品仓',5000,500)",
    # shipment
    "INSERT INTO shipment VALUES (1,'SO-20261015-01','A型号',2000,'待发货','2026-10-17 10:00:00')",
    "INSERT INTO shipment VALUES (2,'SO-20261014-02','B型号',1000,'在途','2026-10-16 18:00:00')",
    # S4 追加一单逾期风险订单（不改既有 2 单的 id 顺序）
    "INSERT INTO shipment VALUES (3,'SO-20261010-03','A型号',1500,'待发货','2026-10-14 10:00:00')",
    # customer_complaint
    "INSERT INTO customer_complaint VALUES (1,'CS-20261012-01','华为终端','A型号','BATCH-A-1008','贴片元件偏移导致功能失效，不良率约2.3%','open')",
]


def init_db():
    # 确保data目录存在
    os.makedirs(os.path.join(os.getcwd(), "data"), exist_ok=True)
    engine = create_engine(settings.DB_URL)
    with engine.begin() as conn:
        # SQLite cursor不能一次跑多语句，按;分割逐条执行
        for stmt in SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        for sql in SEED_DATA:
            conn.execute(text(sql))
    print(f"[init_db] 数据库初始化完成: {settings.DB_URL}")
    print(f"[init_db] 插入 {len(SEED_DATA)} 条模拟数据")


if __name__ == "__main__":
    init_db()
