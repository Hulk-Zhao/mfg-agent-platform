#!/usr/bin/env python3
"""演示数据生成器（T02）。

按 data/demo_storyline/spec-v1.md 与 profile_electronics.json 生成合成演示数据集。
固定随机种子：两次运行产出字节级一致。

用法：
    python tools/demo_gen/gen.py [--profile tools/demo_gen/profile_electronics.json]
                                  [--out data/demo_storyline]
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20261019
ROOT = Path(__file__).resolve().parents[2]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def day(base: datetime, offset: int) -> datetime:
    return base + timedelta(days=offset)


def build(profile: dict, wordlist: dict) -> dict[str, list[dict]]:
    rng = random.Random(SEED)
    base = datetime.strptime(profile["base_date"], "%Y-%m-%d").replace(hour=9)
    mo = profile["main_order"]
    ops = profile["ops"]
    spc = profile["spc"]

    events: list[dict] = []
    seq = [0]

    def ev(ts: datetime, etype: str, ref: str, scene: str, **payload) -> None:
        seq[0] += 1
        events.append({
            "event_id": f"DEMO-EV-{seq[0]:04d}",
            "ts": iso(ts),
            "type": etype,
            "object_ref": ref,
            "scene": scene,
            "payload": payload,
        })

    # ---------- 主数据 ----------
    customers = [{
        "customer_id": mo["customer_id"], "name": mo["customer_name"],
        "quality_profile": "近 12 个月客诉 3 起，退货率 0.4%", "credit_grade": "A",
    }]
    materials = [
        {"material_id": mo["material_id"], "name": mo["material_name"], "kind": "finished", "std_cost": 52.0, "supplier_id": "DEMO-SUP-001"},
        {"material_id": "DEMO-MAT-2201", "name": "电源芯片 U2201", "kind": "raw", "std_cost": 6.8, "supplier_id": "DEMO-SUP-002"},
        {"material_id": "DEMO-MAT-0402", "name": "贴片电阻 0402", "kind": "raw", "std_cost": 0.012, "supplier_id": "DEMO-SUP-002"},
        {"material_id": "DEMO-MAT-470U", "name": "铝电解电容 470uF", "kind": "raw", "std_cost": 0.35, "supplier_id": "DEMO-SUP-003"},
        {"material_id": "DEMO-MAT-JST4", "name": "连接器 JST-4P", "kind": "raw", "std_cost": 0.9, "supplier_id": "DEMO-SUP-003"},
    ]
    boms = [
        {"bom_id": "DEMO-BOM-E5001", "material_id": mo["material_id"], "version": "v1.0", "kind": "E",
         "lines": [
             {"material_id": "DEMO-MAT-2201", "qty": 1, "ref_des": "U1"},
             {"material_id": "DEMO-MAT-0402", "qty": 24, "ref_des": "R1-R24"},
             {"material_id": "DEMO-MAT-470U", "qty": 4, "ref_des": "C1-C4"},
             {"material_id": "DEMO-MAT-JST4", "qty": 1, "ref_des": "J1"},
         ]},
        {"bom_id": "DEMO-BOM-M5001", "material_id": mo["material_id"], "version": "v1.0", "kind": "M", "lines": [
            {"material_id": "DEMO-MAT-2201", "qty": 1, "ref_des": "U1"},
            {"material_id": "DEMO-MAT-0402", "qty": 24, "ref_des": "R1-R24"},
            {"material_id": "DEMO-MAT-470U", "qty": 4, "ref_des": "C1-C4"},
            {"material_id": "DEMO-MAT-JST4", "qty": 1, "ref_des": "J1"},
        ]},
        {"bom_id": "DEMO-BOM-M5001", "material_id": mo["material_id"], "version": "v1.1", "kind": "M", "lines": [
            {"material_id": "DEMO-MAT-2202", "qty": 1, "ref_des": "U1"},
            {"material_id": "DEMO-MAT-0402", "qty": 24, "ref_des": "R1-R24"},
            {"material_id": "DEMO-MAT-470U", "qty": 4, "ref_des": "C1-C4"},
            {"material_id": "DEMO-MAT-JST4", "qty": 1, "ref_des": "J1"},
        ]},
    ]
    # ECN 换料后的新物料
    materials.append({"material_id": "DEMO-MAT-2202", "name": "电源芯片 U2202（替代）", "kind": "raw", "std_cost": 7.1, "supplier_id": "DEMO-SUP-004"})

    suppliers = [
        {"supplier_id": f"DEMO-SUP-{i:03d}", "name": n, "otd_rate": round(rng.uniform(0.82, 0.98), 4)}
        for i, n in enumerate(wordlist["suppliers"], start=1)
    ]
    equipment = [
        {"equipment_id": f"DEMO-EQ-{i:03d}", "name": n, "status": "running"}
        for i, n in enumerate(wordlist["equipment"], start=1)
    ]

    # ---------- 主线订单：接单 S1 ----------
    promise = day(base, mo["promise_offset_days"])
    sales_orders = [{
        "order_id": mo["order_id"], "customer_id": mo["customer_id"], "status": "approved",
        "lines": [{"material_id": mo["material_id"], "qty": mo["qty"], "unit_price": mo["unit_price"],
                   "promise_date": promise.strftime("%Y-%m-%d")}],
        "promise_date": promise.strftime("%Y-%m-%d"), "actual_ship_date": None,
    }]
    mat_cost = round(mo["qty"] * 52.0 * 0.62, 2)
    labor_cost = round(mo["qty"] * (sum(o["std_hours"] for o in ops)) * 45.0, 2)
    tool_amort = round(48000.0 / mo["qty"], 2)
    low = round(mat_cost + labor_cost + tool_amort, 2)
    quotes = [{
        "quote_id": "DEMO-QT-001", "order_id": mo["order_id"],
        "material_cost": mat_cost, "labor_cost": labor_cost, "tool_amort": tool_amort,
        "price_low": low, "price_high": round(low * 1.08, 2),
    }]
    ev(day(base, 0), "order_status", mo["order_id"], "S1", status="reviewing",
       atp_on_hand=1200, atp_wip=3000, atp_safety=500, note="ATP 初评三来源")
    ev(day(base, 1), "order_status", mo["order_id"], "S1", status="reviewing",
       quote_id="DEMO-QT-001", price_low=low, price_high=round(low * 1.08, 2), note="报价区间产出")
    ev(day(base, 2), "hitl", mo["order_id"], "S1", slots=["biz", "rd", "pln"], result="approved", note="订单评审一次会签")

    # ---------- 立项 S2 ----------
    ev(day(base, 4), "order_status", mo["order_id"], "S2", status="in_production", note="立项材料汇总完成")
    ev(day(base, 5), "hitl", "DEMO-PRJ-001", "S2", slots=["rd", "pe"], result="approved", note="立项会签放行")

    # ---------- 制样 S3 ----------
    stages = [
        ("EVT", 12, 1.42, 0.93, "Go"),
        ("DVT", 24, 1.36, 0.955, "Go"),
        ("PVT", 36, 1.34, 0.982, "Go"),
    ]
    for name, d, cpk, fpy, verdict in stages:
        ev(day(base, d), "inspection", f"DEMO-STG-{name}", "S3", stage=name, cpk=cpk, fpy=fpy, verdict=verdict)
        ev(day(base, d), "hitl", f"DEMO-STG-{name}", "S3", slots=["rd", "pqe", "pln"], result=verdict, note=f"{name} 阶段门会签")

    # ---------- 批量 S4 ----------
    wos = [
        {"wo_id": "DEMO-WO-001", "order_id": mo["order_id"], "material_id": mo["material_id"],
         "qty_planned": 2000, "qty_good": 1976, "status": "done", "ops": ops},
        {"wo_id": "DEMO-WO-002", "order_id": mo["order_id"], "material_id": mo["material_id"],
         "qty_planned": 1500, "qty_good": 1481, "status": "in_progress", "ops": ops},
        {"wo_id": "DEMO-WO-003", "order_id": mo["order_id"], "material_id": mo["material_id"],
         "qty_planned": 1500, "qty_good": 0, "status": "released", "ops": ops},
    ]
    ev(day(base, 40), "report", "DEMO-WO-001", "S4", note="WO-001 三道工序报工完成", fpy=0.988)
    # SPC 判异 1
    ev(day(base, 44), "alert", "DEMO-EQ-002", "S4", kind="spc", param="炉温", value=258.4,
       target=spc["target"], tolerance=spc["tolerance"], note="SPC 判异：SMT 炉温偏离")
    ev(day(base, 44), "hitl", "DEMO-ALERT-001", "S4", slots=["pqe", "pe"], result="处置中", note="判异处置会签发起")
    ev(day(base, 45), "alert", "DEMO-ALERT-001", "S4", kind="closed", note="判异处置闭环")

    # ---------- 支线 A：ECN S4 ----------
    ev(day(base, 47), "ecn", "DEMO-ECN-001", "S4", reason="U2201 停产风险换料",
       impact_wos=["DEMO-WO-002", "DEMO-WO-003"], impact_stock_lots=1, impact_transit_lots=1,
       impact_open_lines=1, note="ECN 影响面四象限产出")
    ev(day(base, 48), "hitl", "DEMO-ECN-001", "S4", slots=["rd", "pe", "pqe"], result="approved", note="ECN 切换会签")
    ev(day(base, 49), "ecn", "DEMO-ECN-001", "S4", note="切换执行，MBOM v1.1 生效")
    # SPC 判异 2
    ev(day(base, 52), "alert", "DEMO-EQ-006", "S4", kind="spc", param="ICT 误判率", value=0.031,
       target=0.01, tolerance=0.005, note="SPC 判异：ICT 误判率异常")
    ev(day(base, 53), "alert", "DEMO-ALERT-002", "S4", kind="closed", note="判异处置闭环")

    # ---------- 出货 S5 ----------
    ev(day(base, 60), "inspection", "DEMO-IB-OQC-001", "S5", stage="OQC", sample_qty=80, defect_qty=1, result="pass", note="OQC 放行")
    ev(day(base, 60), "order_status", mo["order_id"], "S5", status="shipped",
       actual_ship_date=day(base, 60).strftime("%Y-%m-%d"), note="发货确认")
    ev(day(base, 62), "order_status", mo["order_id"], "S5", status="invoiced", note="开票")

    # ---------- 支线 B：客诉 8D S5 ----------
    ev(day(base, 80), "complaint", "DEMO-CC-001", "S5", qty=12, symptom="烧录异常",
       lot_id="DEMO-LOT-002", note="客诉受理")
    ev(day(base, 80), "complaint", "DEMO-CC-001", "S5", stage="located",
       trace={"lot": "DEMO-LOT-002", "wo": "DEMO-WO-001", "op": "30", "equipment": "DEMO-EQ-006",
              "shift": "夜班", "stock_lots": 2}, note="批次定位完成（演示口径 15 分钟内）")
    ev(day(base, 81), "hitl", "DEMO-8D-001", "S5", slots=["pqe", "cs"], result="8D 闭环", note="8D 会签")

    # ---------- 采购 S6 ----------
    purchase_orders = [
        {"po_id": "DEMO-PO-001", "supplier_id": "DEMO-SUP-002", "material_id": "DEMO-MAT-2201",
         "qty": 6000, "status": "confirmed", "promise_date": day(base, 39).strftime("%Y-%m-%d"),
         "actual_date": day(base, 40).strftime("%Y-%m-%d")},
        {"po_id": "DEMO-PO-002", "supplier_id": "DEMO-SUP-004", "material_id": "DEMO-MAT-2202",
         "qty": 2000, "status": "delayed", "promise_date": day(base, 50).strftime("%Y-%m-%d"),
         "actual_date": None},
    ]
    ev(day(base, 38), "alert", "DEMO-PO-002", "S6", kind="shortage", material="DEMO-MAT-2202",
       gap_qty=2000, note="缺料预警：料期 vs 工排计划比对触发")
    ev(day(base, 38), "order_status", "DEMO-PO-002", "S6", note="催料任务自动生成，挂采购员待办")
    ev(day(base, 40), "alert", "DEMO-PO-002", "S6", kind="escalated", note="延期升级至采购主管")

    # ---------- 背景订单与设备的间隙事件（让时间轴是活的系统，而不是只有一条单） ----------
    for i in range(1, profile["background_orders"] + 1):
        oid = f"DEMO-SO-{100 + i}"
        ev(day(base, 20 + i), "report", f"DEMO-WO-{100 + i}", "S4", note="背景订单报工")
        ev(day(base, 22 + i), "inspection", f"DEMO-IB-BG-{i:03d}", "S4", stage="IPQC",
           sample_qty=50, defect_qty=2, result="pass", note="背景订单巡检")
    for eq in equipment[:4]:
        ev(day(base, 30), "report", eq["equipment_id"], "S4", kind="daily_check",
           note=f"{eq['name']} 日常点检完成")
    for i in range(6):
        ev(day(base, 25 + i), "hitl", f"DEMO-BG-{i:03d}", "S4",
           slots=["pqe"], result="closed", note="背景工单会签关闭")

    # ---------- 背景数据 ----------
    for i in range(1, profile["background_orders"] + 1):
        oid = f"DEMO-SO-{100 + i}"
        sales_orders.append({
            "order_id": oid, "customer_id": "DEMO-CUS-001", "status": "in_production",
            "lines": [{"material_id": mo["material_id"], "qty": 800 * i, "unit_price": 85.0,
                       "promise_date": day(base, 70 + i).strftime("%Y-%m-%d")}],
            "promise_date": day(base, 70 + i).strftime("%Y-%m-%d"), "actual_ship_date": None,
        })
        wos.append({"wo_id": f"DEMO-WO-{100 + i}", "order_id": oid, "material_id": mo["material_id"],
                    "qty_planned": 800 * i, "qty_good": int(800 * i * 0.97), "status": "in_progress", "ops": ops})

    lots = [
        {"lot_id": "DEMO-LOT-001", "material_id": mo["material_id"], "wo_id": "DEMO-WO-001", "op_no": "10",
         "qty": 1976, "location": "成品库-A03"},
        {"lot_id": "DEMO-LOT-002", "material_id": mo["material_id"], "wo_id": "DEMO-WO-001", "op_no": "30",
         "qty": 1976, "location": "成品库-A03（客诉同批）"},
        {"lot_id": "DEMO-LOT-003", "material_id": "DEMO-MAT-2201", "wo_id": "-", "op_no": "-",
         "qty": 1400, "location": "原料仓-B01"},
        {"lot_id": "DEMO-LOT-004", "material_id": "DEMO-MAT-2202", "wo_id": "-", "op_no": "-",
         "qty": 600, "location": "在途（PO-002）"},
    ]
    inspections = [
        {"batch_id": "DEMO-IB-IQC-001", "wo_id": "-", "stage": "IQC", "sample_qty": 50, "defect_qty": 1, "result": "pass", "inspector": "刘强", "ts": iso(day(base, 39))},
        {"batch_id": "DEMO-IB-IPQC-001", "wo_id": "DEMO-WO-001", "stage": "IPQC", "sample_qty": 100, "defect_qty": 3, "result": "pass", "inspector": "王倩", "ts": iso(day(base, 41))},
        {"batch_id": "DEMO-IB-OQC-001", "wo_id": "DEMO-WO-001", "stage": "OQC", "sample_qty": 80, "defect_qty": 1, "result": "pass", "inspector": "王倩", "ts": iso(day(base, 60))},
        {"batch_id": "DEMO-IB-IPQC-002", "wo_id": "DEMO-WO-002", "stage": "IPQC", "sample_qty": 100, "defect_qty": 6, "result": "concession", "inspector": "王倩", "ts": iso(day(base, 55))},
    ]
    spc_series = []
    for i in range(spc["days"]):
        ts = day(base, -spc["days"] + i + 1)
        value = spc["target"] + (rng.random() - 0.5) * spc["tolerance"]
        flag = "in"
        if i in (11, 25):
            value = spc["target"] + spc["tolerance"] * 1.35
            flag = "out"
        spc_series.append({"ts": iso(ts), "equipment_id": "DEMO-EQ-002", "param": "炉温",
                           "value": round(value, 2), "flag": flag})

    faqs = [
        {"faq_id": f"DEMO-FAQ-{i:03d}", "question": q, "answer": a}
        for i, (q, a) in enumerate([
            ("来料异常怎么发起处置？", "在预警中心选择来料异常预警，点「发起处置」，系统按槽位生成会签单。"),
            ("SPC 判异后多久必须处置？", "判异发生后 2 小时内由 PQE 发起处置会签，超时升级。"),
            ("客诉 8D 多久闭环？", "受理后 5 个工作日内提交 D4，10 个工作日内闭环。"),
            ("特采需要谁批？", "SQE 发起，质量经理终批，影响订单交期的加会计划。"),
            ("停线复线怎么决策？", "制造经理与 EQ 共同确认设备状态后，由制造经理发起复线会签。"),
            ("ECN 影响面谁出？", "系统自动产出四象限清单，PE 确认后交研发会签。"),
            ("阶段门 Go 的条件？", "良率、CPK、直通率、问题闭环数四项记分卡全部达标且无未决高风险。"),
            ("催料任务谁来跟进？", "缺料预警自动生成催料任务挂采购员待办，超 48 小时升级采购主管。"),
            ("报表数字能导出吗？", "支持导出，导出文件带水印与操作留痕。"),
            ("访客能看哪些数据？", "访客角色只读演示数据，无任何写操作权限。"),
        ], start=1)
    ]
    cases = [
        {"case_id": f"DEMO-CASE-{i:03d}", "title": t, "root_cause": r, "dept": d, "redacted": True}
        for i, (t, r, d) in enumerate([
            ("炉温波动导致虚焊", "回流焊炉温区漂移，未按点检周期校准", "制造"),
            ("电容容舞引发啸叫", "供应商来料批次容舞超差", "质量"),
            ("连接器插拔力不足", "模具磨损未及时更换", "质量"),
            ("ICT 误判批量停机", "测试程序版本与 MBOM 不一致", "研发"),
            ("贴片偏移批量不良", "钢网张力衰减未点检", "制造"),
            ("烧录异常客诉", "烧录站静电接地失效", "制造"),
            ("波峰焊锡量不足", "助焊剂喷涂压力偏低", "制造"),
            ("上料错贴片", "料站与 MBOM 映射表未更新", "制造"),
            ("AOI 漏检批次流出", "检测阈值设置过宽", "质量"),
            ("包装受潮客诉", "干燥剂投放量与包装环境不匹配", "制造"),
        ] * 2, start=1)
    ]

    return {
        "customers": customers, "materials": materials, "boms": boms, "suppliers": suppliers,
        "equipment": equipment, "sales_orders": sales_orders, "quotes": quotes,
        "work_orders": wos, "inspections": inspections, "lots": lots,
        "purchase_orders": purchase_orders, "spc_series": spc_series,
        "events": events, "faqs": faqs, "cases": cases,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=str(ROOT / "tools/demo_gen/profile_electronics.json"))
    ap.add_argument("--out", default=str(ROOT / "data/demo_storyline"))
    args = ap.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    wordlist = json.loads((ROOT / "tools/demo_gen/wordlist.json").read_text(encoding="utf-8"))
    data = build(profile, wordlist)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jsonl"):
        old.unlink()
    report = []
    for name, rows in data.items():
        path = out / f"{name}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        report.append(f"{name}: {len(rows)}")
    (out / "gen-report.txt").write_text(
        "演示数据生成报告（固定种子，可重跑复现）\n" + "\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
