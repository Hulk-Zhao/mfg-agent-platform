"""制造看板体量 / 维度扩容（v2）种子生成器：产出 ``data/seed_board_mfg_volume2.sql``。

用法（仓库根目录，PowerShell）：::

    uv run python tools/gen_board_mfg_volume_seed.py

为什么要有这个脚本
------------------
演示库的制造四表（equipment_run_time / scrap_record / work_report /
test_records）只有 2026-10-01~10-15、6 线 × 6 型号（L2 共 36 组），
station_detail 只覆盖 10-08~10-15 的 4 线 3 型号（约 70% 的 L2 行进 L3 是空页），
且「整机」工段（ASSY-*）没有任何数据。本脚本把体量 / 维度一次性补齐：

- 制造四表补 **2026-09-01~09-30**（8 线 × 12 型号，每 (line,model,day) 2 行）；
- 给 ASSY-1（整机工段）补 **2026-10-01~10-15**，让「整机」筛选有真实数据；
- ``equipment_run_time`` 扩列 ``stop_reason``（8 类）+ 幂等回填老行（仅原值 NULL）；
- ``station_detail`` 覆盖全部 (line,model) × 10-01~10-15 × {availability,
  scrap_rate, test_fpy}（拆分式：分子 / 分母 / 不良数，求和能回原值）；
- ``production_output`` 补 10-01~10-15 × 8 线 × 12 型号（含 3 行明显低 OEE）。

口径 / 纪律
-----------
- 固定随机种子（``SEED``）→ 同一输入永远产出同一份 SQL（可复现、可评审）；
- **只新增行 / 只补历史**：既有 10-15 锚点行一律不动（不重建、不 Delete）；
- 幂等：四表 + production_output 用显式 id（id 段见 SQL 文件头），
  station_detail 沿用唯一索引（metric_key, group_key, record_date,
  process_step, station_no, shift）+ 自增 id；两处回填都是 ``UPDATE ... WHERE
  原值 IS NULL``，重复执行不再产生重复行；
- 原因分布刻意做头部（非均匀随机），停机原因 3 类约占 80%。
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "seed_board_mfg_volume2.sql"

#: 固定随机种子（改这个值 = 换一批数据；保持默认即"冻结"当前演示数据）
SEED = 20261021

#: 各表新行 id 起点（既有 max(id)：equipment_run_time 2870 / scrap_record 4427 /
#: work_report 4897 / test_records 6970 / production_output 107；段间留足余量）
ID_START = 10001

#: 演示「今天」（与 dashboard.DEMO_TODAY 一致）
DEMO_TODAY = date(2026, 10, 15)

#: 历史补数区间（制造四表）
SEP_FROM, SEP_TO = date(2026, 9, 1), date(2026, 9, 30)
#: ASSY-1（整机）十月补数区间（让「整机」筛选在当前板级窗口内有数据）
OCT_FROM, OCT_TO = date(2026, 10, 1), date(2026, 10, 15)
#: station_detail 覆盖区间
SD_FROM, SD_TO = date(2026, 10, 1), date(2026, 10, 15)

LINES = ["SMT-1", "SMT-2", "SMT-3", "DIP-1", "DIP-2", "PACK-1", "PACK-2",
         "ASSY-1"]
MODELS = ["A型号", "B型号", "C型号", "D型号", "E型号", "F型号",
          "G型号", "H型号", "I型号", "J型号", "K型号", "L型号"]

#: 型号 → 产品分类（4 分类各 3 型号；与 dashboard._MFG_CAT_MODELS 同源）
MODEL_CATEGORY = {
    "A型号": "消费电子", "E型号": "消费电子", "G型号": "消费电子",
    "B型号": "智能电源", "F型号": "智能电源", "H型号": "智能电源",
    "C型号": "储能电源", "I型号": "储能电源", "J型号": "储能电源",
    "D型号": "车载", "K型号": "车载", "L型号": "车载",
}
#: 型号 → 单字母（work_report / station_detail 的 wo_no 体系：WO-{字母}-{MMDD}）
MODEL_LETTER = {m: m[0] for m in MODELS}

#: 产线 → 工段（与 dashboard._MFG_STAGE_LINE_LIKE 反解一致）
LINE_STAGE_CN = {
    "SMT": "SMT段", "DIP": "DIP段", "PACK": "组装段", "ASSY": "组装段",
}

#: 产线前缀 → 3 道工序（工序名与 station_detail / 修复后的 _STEP_STAGE 同名）
LINE_STEPS = {
    "SMT": ["锡膏印刷", "贴片", "回流焊"],
    "DIP": ["插件", "波峰焊", "清洗"],
    "PACK": ["组装", "包装", "测试"],
    "ASSY": ["整机组装", "包装入库", "测试"],
}
#: 工序 → 工段（station_detail 的 process_stage 列；与 dashboard._STEP_STAGE 同源）
STEP_STAGE = {
    "锡膏印刷": "SMT段", "贴片": "SMT段", "回流焊": "SMT段",
    "插件": "DIP段", "波峰焊": "DIP段", "清洗": "DIP段",
    "组装": "组装段", "包装": "组装段", "测试": "测试段",
    "整机组装": "组装段", "包装入库": "组装段",
}

#: 工序 → (工位号, 工位名, 设备号, 缺陷模式, 节拍秒)
STEP_META = {
    "锡膏印刷": ("SPI-01", "印刷机#1", "EQ-SPI-01", "换型调机", 32),
    "贴片": ("SMT-M1", "贴片机#1", "EQ-SMT-M1", "抛料停机", 32),
    "回流焊": ("RF-01", "回流炉#1", "EQ-RF-01", "炉温异常", 32),
    "插件": ("DIP-I1", "插件工位#1", "EQ-DIP-I1", "缺料待料", 45),
    "波峰焊": ("WS-01", "波峰炉#1", "EQ-WS-01", "波峰炉保养", 45),
    "清洗": ("CL-01", "清洗机#1", "EQ-CL-01", "清洗液更换", 45),
    "组装": ("AS-01", "组装工位#1", "EQ-AS-01", "装配干涉", 55),
    "包装": ("PK-01", "包装工位#1", "EQ-PK-01", "包装破损", 40),
    "测试": ("TE-01", "测试工位#1", "EQ-TE-01", "测试失效", 60),
    "整机组装": ("ASSY-A1", "整机装配#1", "EQ-ASSY-A1", "装配干涉", 80),
    "包装入库": ("ASSY-P1", "入库包装#1", "EQ-ASSY-P1", "包装破损", 50),
}

SHIFTS = ["白班", "夜班"]
OPERATORS = ["张建国", "李海燕", "王振", "刘敏", "陈志强", "周慧", "赵鹏", "孙晓东"]

#: 停机原因词表（8 类）与目标分布（前 3 类约占 80%）
STOP_REASONS = ["设备故障", "换型调机", "缺料待料", "计划保养",
                "品质异常停线", "程序参数异常", "人员待岗", "其他"]
STOP_WEIGHTS = [45, 22, 13, 8, 5, 4, 2, 1]
#: 停机时长（分钟）区间（与原因弱相关，保证量级头部清晰）
STOP_MIN_RANGE = {
    "设备故障": (45, 95), "品质异常停线": (30, 70), "换型调机": (25, 60),
    "缺料待料": (20, 50), "计划保养": (15, 40), "程序参数异常": (12, 35),
    "人员待岗": (6, 20), "其他": (5, 15),
}

#: 报废原因词表与目标分布（新行内前 3 类 = 80%，头部明显）
SCRAP_REASONS = ["焊点不良", "锡膏偏移超差报废", "元件失效", "来料不良",
                 "外观不良", "设备异常", "装配干涉", "波峰焊连锡批废"]
SCRAP_WEIGHTS = [48, 20, 12, 8, 5, 3, 2, 2]

#: 测试类型 → (测试项, 常见失效描述)
TEST_TYPES = [
    ("功能", "电性能测试", "功能失效"),
    ("可靠性", "高低温循环", "温漂超差"),
    ("外观", "外观检查", "外观不良"),
    ("软件", "固件功能测试", "固件异常"),
]


def _s(val) -> str:
    """Python 值 → SQL 字面量（None → NULL，字符串加单引号）。"""
    if val is None:
        return "NULL"
    return f"'{val}'"


def _letter(model: str) -> str:
    return MODEL_LETTER[model]


def _line_kind(line: str) -> str:
    return line.split("-", 1)[0]


def _mmdd(d: date) -> str:
    return d.strftime("%m%d")


def _dstr(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _days(d0: date, d1: date) -> list[date]:
    out, d = [], d0
    while d <= d1:
        out.append(d)
        d += timedelta(days=1)
    return out


def _weighted(rng: random.Random, items: list[str], weights: list[int]) -> str:
    return rng.choices(items, weights=weights, k=1)[0]


class Emitter:
    """把行按批攒进 ``INSERT OR IGNORE`` 语句（每批 ≤ chunk 行）。"""

    def __init__(self, chunk: int = 400):
        self.lines: list[str] = []
        self.chunk = chunk

    def insert(self, table: str, cols: list[str], rows: list[list[str]]) -> None:
        if not rows:
            return
        col_sql = ", ".join(cols)
        header = f"INSERT OR IGNORE INTO {table} ({col_sql}) VALUES"
        for i in range(0, len(rows), self.chunk):
            batch = rows[i:i + self.chunk]
            self.lines.append(header)
            for j, r in enumerate(batch):
                tail = "," if j < len(batch) - 1 else ";"
                self.lines.append("(" + ", ".join(r) + ")" + tail)
        self.lines.append("")

    def raw(self, sql: str) -> None:
        self.lines.append(sql)
        self.lines.append("")


def build(rng: random.Random) -> tuple[str, dict]:
    em = Emitter()
    counts: dict[str, int] = {}
    ids = {k: ID_START for k in
           ("equipment", "scrap", "work", "test", "output")}

    def nid(k: str) -> int:
        v = ids[k]
        ids[k] += 1
        return v

    # ---------------- 1) 制造四表：2026-09（8 线 × 12 型号） ----------------
    eq_rows, sp_rows, wr_rows = [], [], []
    for d in _days(SEP_FROM, SEP_TO):
        for line in LINES:
            kind = _line_kind(line)
            for model in MODELS:
                for _ in range(2):
                    # equipment_run_time：稼动率
                    planned = rng.choice([420, 440, 450, 460, 480])
                    if rng.random() < 0.12:
                        stop, reason = 0, None
                    else:
                        reason = _weighted(rng, STOP_REASONS, STOP_WEIGHTS)
                        lo, hi = STOP_MIN_RANGE[reason]
                        stop = rng.randint(lo, hi)
                    eq_rows.append([str(nid("equipment")), _s(_dstr(d)),
                                    _s(line), _s(model), str(planned),
                                    str(planned - stop), str(stop), _s(reason)])
                    # scrap_record：报废率（产出量级抬高，使原因柏拉图头部更清晰）
                    output = rng.randint(300, 1100)
                    rate = rng.uniform(0.008, 0.032)
                    if rng.random() < 0.08:
                        rate = rng.uniform(0.045, 0.085)
                    scrap = max(0, round(output * rate))
                    sp_rows.append([str(nid("scrap")), _s(_dstr(d)), _s(line),
                                    _s(model), str(output), str(scrap),
                                    _s(_weighted(rng, SCRAP_REASONS, SCRAP_WEIGHTS))])
                # work_report：报工完成率（日批工单 3 道工序 → 每 (line,model,day) 3 行）
                wo = f"WO-{_letter(model)}-{_mmdd(d)}"
                for step in LINE_STEPS[kind]:
                    reported = 0 if rng.random() < 0.07 else 1
                    wr_rows.append([str(nid("work")), _s(_dstr(d)), _s(line),
                                    _s(model), _s(wo), _s(step), "1",
                                    str(reported)])

    # test_records（无 line_code：按型号 × 4 测试类型铺）
    te_rows = []
    for d in _days(SEP_FROM, SEP_TO):
        for model in MODELS:
            for ttype, titem, fail_desc in TEST_TYPES:
                for _ in range(2):
                    ok = rng.random() > 0.055
                    te_rows.append([
                        str(nid("test")), _s(model),
                        _s(f"BATCH-{_letter(model)}-{_mmdd(d)}"), _s(ttype),
                        _s(titem), _s("pass" if ok else "fail"),
                        _s(None if ok else fail_desc),
                        _s(rng.choice(OPERATORS)), _s(_dstr(d))])

    em.insert("equipment_run_time",
              ["id", "record_date", "line_code", "product_model",
               "planned_run_min", "actual_run_min", "stop_min", "stop_reason"],
              eq_rows)
    em.insert("scrap_record",
              ["id", "record_date", "line_code", "product_model",
               "output_qty", "scrap_qty", "scrap_reason"], sp_rows)
    em.insert("work_report",
              ["id", "report_date", "line_code", "product_model", "wo_no",
               "process_step", "should_report", "reported"], wr_rows)
    em.insert("test_records",
              ["id", "product_model", "batch_no", "test_type", "test_item",
               "result", "defect_desc", "tester", "test_date"], te_rows)
    counts["equipment_run_time(9月)"] = len(eq_rows)
    counts["scrap_record(9月)"] = len(sp_rows)
    counts["work_report(9月)"] = len(wr_rows)
    counts["test_records(9月)"] = len(te_rows)

    # ---------------- 2) 制造三表：ASSY-1 补 10 月（整机工段有数据） ----------------
    eq_oct, sp_oct, wr_oct = [], [], []
    for d in _days(OCT_FROM, OCT_TO):
        for model in MODELS:
            for _ in range(2):
                planned = rng.choice([420, 440, 450, 460])
                reason = _weighted(rng, STOP_REASONS, STOP_WEIGHTS)
                lo, hi = STOP_MIN_RANGE[reason]
                stop = rng.randint(lo, hi)
                eq_oct.append([str(nid("equipment")), _s(_dstr(d)), _s("ASSY-1"),
                               _s(model), str(planned), str(planned - stop),
                               str(stop), _s(reason)])
                output = rng.randint(300, 900)
                scrap = max(0, round(output * rng.uniform(0.008, 0.030)))
                sp_oct.append([str(nid("scrap")), _s(_dstr(d)), _s("ASSY-1"),
                               _s(model), str(output), str(scrap),
                               _s(_weighted(rng, SCRAP_REASONS, SCRAP_WEIGHTS))])
            # work_report：ASSY-1 日批工单 3 道工序
            for step in LINE_STEPS["ASSY"]:
                wr_oct.append([str(nid("work")), _s(_dstr(d)), _s("ASSY-1"),
                               _s(model), _s(f"WO-{_letter(model)}-{_mmdd(d)}"),
                               _s(step), "1",
                               str(0 if rng.random() < 0.05 else 1)])
    # 一条明显低完成率的 SMT 工单（让「报工完成率」L3 首行必含 SMT段）
    for step, reported in (("锡膏印刷", 1), ("贴片", 0), ("回流焊", 0)):
        wr_oct.append([str(nid("work")), _s("2026-10-09"), _s("SMT-1"),
                       _s("A型号"), _s("WO-A-1009"), _s(step), "1",
                       str(reported)])

    em.insert("equipment_run_time",
              ["id", "record_date", "line_code", "product_model",
               "planned_run_min", "actual_run_min", "stop_min", "stop_reason"],
              eq_oct)
    em.insert("scrap_record",
              ["id", "record_date", "line_code", "product_model",
               "output_qty", "scrap_qty", "scrap_reason"], sp_oct)
    em.insert("work_report",
              ["id", "report_date", "line_code", "product_model", "wo_no",
               "process_step", "should_report", "reported"], wr_oct)
    counts["equipment_run_time(10月ASSY)"] = len(eq_oct)
    counts["scrap_record(10月ASSY)"] = len(sp_oct)
    counts["work_report(10月ASSY+低报工)"] = len(wr_oct)

    # ---------------- 3) production_output：10-01~10-15 × 8 线 × 12 型号 ----------------
    po_rows = []
    low_ids = {("PACK-1", "G型号", "2026-10-06"),
               ("PACK-2", "L型号", "2026-10-11"),
               ("ASSY-1", "J型号", "2026-10-14")}
    # 既有锚点已存在 (SMT-1, A型号, 2026-10-15) 两行（OEE 暴跌场景），此组合**不重复新增**
    # ——注册查询 mfg_oee（当日 SMT-1·A型号）的行数断言依赖恰好 2 行。
    skip_combo = ("SMT-1", "A型号", "2026-10-15")
    for d in _days(OCT_FROM, OCT_TO):
        for line in LINES:
            for model in MODELS:
                if (line, model, _dstr(d)) == skip_combo:
                    continue
                planned = rng.randint(800, 1600)
                if (line, model, _dstr(d)) in low_ids:
                    oee = rng.randint(28, 45)
                    eff = rng.uniform(0.45, 0.65)
                    stop_dur = rng.randint(150, 260)
                elif line == "SMT-1":
                    # 保持（既有）唯一一行 SMT-1 低 OEE 锚点不被新增行破坏
                    oee = rng.randint(70, 90)
                    eff = rng.uniform(0.85, 1.0)
                    stop_dur = rng.randint(0, 90)
                else:
                    oee = rng.randint(58, 92)
                    eff = rng.uniform(0.80, 1.0)
                    stop_dur = rng.randint(0, 130)
                po_rows.append([str(nid("output")), _s(_dstr(d)), _s(line),
                                _s(model), str(planned),
                                str(int(planned * eff)), f"{oee:.2f}",
                                str(stop_dur)])
    em.insert("production_output",
              ["id", "record_date", "line_code", "product_model", "planned_qty",
               "actual_qty", "oee", "stop_duration_min"], po_rows)
    counts["production_output(10月)"] = len(po_rows)

    # ---------------- 4) station_detail：全 (line,model) × 15 天 × 3 指标 ----------------
    # 拆分式：availability num=实际 / den=计划；scrap_rate num=报废 / den=产出；
    # test_fpy num=通过 / den=测试数；defect_value = 损失量（求和可回原值）。
    sd_rows = []
    for d in _days(SD_FROM, SD_TO):
        for line in LINES:
            kind = _line_kind(line)
            steps = LINE_STEPS[kind]
            for model in MODELS:
                letter = _letter(model)
                wo = f"WO-{letter}-{_mmdd(d)}"
                for metric in ("availability", "scrap_rate", "test_fpy"):
                    for step in steps:
                        s_no, s_name, eq_no, dmode, cyc = STEP_META[step]
                        stage = STEP_STAGE[step]
                        for shift in SHIFTS:
                            op = rng.choice(OPERATORS)
                            if metric == "availability":
                                den = rng.randint(60, 110)
                                if rng.random() < 0.15:
                                    loss = rng.randint(int(den * 0.18), int(den * 0.32))
                                else:
                                    loss = rng.randint(1, int(den * 0.12))
                                num = den - loss
                                dval = loss
                            elif metric == "scrap_rate":
                                den = rng.randint(90, 320)
                                bad = rng.random() < 0.12
                                num = rng.randint(6, 20) if bad else rng.randint(0, 7)
                                num = min(num, den)
                                dval = num
                            else:  # test_fpy
                                den = rng.randint(24, 68)
                                fails = rng.randint(2, 6) if rng.random() < 0.10 \
                                    else rng.randint(0, 2)
                                fails = min(fails, den)
                                num = den - fails
                                dval = fails
                            sd_rows.append([
                                _s(metric), _s(f"{line}|{model}"), _s(_dstr(d)),
                                _s(line), _s(model), _s(wo), _s(stage), _s(step),
                                _s(s_no), _s(s_name), _s(eq_no), _s(shift), _s(op),
                                str(cyc), _s(dmode), str(num), str(den), str(dval)])
    SD_COLS = ["metric_key", "group_key", "record_date", "line_code",
               "product_model", "wo_no", "process_stage", "process_step",
               "station_no", "station_name", "equipment_no", "shift", "operator",
               "cycle_time_s", "defect_mode", "num_value", "den_value",
               "defect_value"]
    em.insert("station_detail", SD_COLS, sd_rows)
    counts["station_detail"] = len(sd_rows)

    # ---------------- 5) 幂等回填 ----------------
    # (a) stop_reason：老行（原值 NULL 且 stop_min>0）按 id 桶位确定性回填（8 类，头部同分布）
    em.raw(
        "UPDATE equipment_run_time SET stop_reason = CASE\n"
        "  WHEN id % 100 < 45 THEN '设备故障'\n"
        "  WHEN id % 100 < 67 THEN '换型调机'\n"
        "  WHEN id % 100 < 80 THEN '缺料待料'\n"
        "  WHEN id % 100 < 88 THEN '计划保养'\n"
        "  WHEN id % 100 < 93 THEN '品质异常停线'\n"
        "  WHEN id % 100 < 97 THEN '程序参数异常'\n"
        "  WHEN id % 100 < 99 THEN '人员待岗'\n"
        "  ELSE '其他' END\n"
        "WHERE stop_reason IS NULL AND stop_min > 0;")
    # (b) station_detail.wo_no：老行（原值 NULL）回填为 WO-{型号字母}-{MMDD}
    em.raw(
        "UPDATE station_detail SET wo_no =\n"
        "  'WO-' || substr(product_model, 1, 1) || '-' || substr(record_date, 6, 2)\n"
        "  || substr(record_date, 9, 2)\n"
        "WHERE wo_no IS NULL;")

    return "\n".join(em.lines), counts


def main() -> None:
    rng = random.Random(SEED)
    body, counts = build(rng)
    header = (
        "-- ============================================================\n"
        "-- 制造看板体量/维度扩容（v2）：由 tools/gen_board_mfg_volume_seed.py 生成，请勿手改。\n"
        f"-- 随机种子 {SEED}（固定 → 可复现）；用法：uv run python tools/gen_board_mfg_volume_seed.py\n"
        "-- 幂等：INSERT OR IGNORE + 显式 id（段首 10001，各表既有 max(id) < 5000）；\n"
        "--       station_detail 沿用唯一索引；回填均为 UPDATE ... WHERE 原值 IS NULL。\n"
        "-- 前置：equipment_run_time.stop_reason 列由 orchestrator._apply_seed 的 PRAGMA\n"
        "--       守卫 ALTER 补齐（独立执行本文件前请先补列）。\n"
        "-- 红线：只新增行/补历史，既有 2026-10-15 锚点行一律不动。\n"
        "-- ============================================================\n\n"
        "-- 新增行数：\n")
    for k, v in counts.items():
        header += f"--   {k}: {v}\n"
    header += "\n"
    text = header + body
    OUT.write_text(text, encoding="utf-8")
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"wrote {OUT}  ({size_mb:.2f} MB)")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
