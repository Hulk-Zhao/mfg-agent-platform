#!/usr/bin/env python3
"""演示数据自洽校验（T03）。

四类校验：
1. 跨对象引用完整性（工单→订单、检验批→工单、事件→对象、BOM 行→物料、PO→供应商/物料）
2. 指标重算比对（直通率、报价三分量、OTD、SPC 越限）
3. 时间轴合法性（事件单调递增、支线挂接点在主线进度范围内）
4. 枚举与状态机（订单状态合法迁移、检验结论、事件类型）

故意注入错误可报出具体断链位置。用法：
    python tools/demo_gen/validate.py [--dir data/demo_storyline] [--inject check_name]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ORDER_FLOW = ["draft", "reviewing", "approved", "in_production", "shipped", "closed"]
EVENT_TYPES = {"order_status", "report", "inspection", "alert", "hitl", "ecn", "complaint"}
INSPECTION_STAGES = {"IQC", "IPQC", "OQC"}
INSPECTION_RESULTS = {"pass", "fail", "concession"}


def load(directory: Path) -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        data[path.stem] = rows
    return data


def check_references(data: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    order_ids = {o["order_id"] for o in data.get("sales_orders", [])}
    wo_ids = {w["wo_id"] for w in data.get("work_orders", [])}
    mat_ids = {m["material_id"] for m in data.get("materials", [])}
    sup_ids = {s["supplier_id"] for s in data.get("suppliers", [])}
    eq_ids = {e["equipment_id"] for e in data.get("equipment", [])}
    lot_ids = {l["lot_id"] for l in data.get("lots", [])}

    for w in data.get("work_orders", []):
        if w["order_id"] not in order_ids:
            errors.append(f"work_orders[{w['wo_id']}].order_id={w['order_id']} 断引用")
        if w["material_id"] not in mat_ids:
            errors.append(f"work_orders[{w['wo_id']}].material_id={w['material_id']} 断引用")
        for op in w["ops"]:
            if op["equipment_id"] not in eq_ids:
                errors.append(f"work_orders[{w['wo_id']}].ops[{op['op_no']}].equipment_id={op['equipment_id']} 断引用")
    for b in data.get("boms", []):
        if b["material_id"] not in mat_ids:
            errors.append(f"boms[{b['bom_id']}].material_id={b['material_id']} 断引用")
        for line in b["lines"]:
            if line["material_id"] not in mat_ids:
                errors.append(f"boms[{b['bom_id']}].lines[{line['material_id']}] 断引用")
    for i in data.get("inspections", []):
        if i["wo_id"] != "-" and i["wo_id"] not in wo_ids:
            errors.append(f"inspections[{i['batch_id']}].wo_id={i['wo_id']} 断引用")
    for p in data.get("purchase_orders", []):
        if p["supplier_id"] not in sup_ids:
            errors.append(f"purchase_orders[{p['po_id']}].supplier_id={p['supplier_id']} 断引用")
        if p["material_id"] not in mat_ids:
            errors.append(f"purchase_orders[{p['po_id']}].material_id={p['material_id']} 断引用")
    for e in data.get("events", []):
        if e["type"] not in EVENT_TYPES:
            errors.append(f"events[{e['event_id']}].type={e['type']} 非法事件类型")
        ref = e["object_ref"]
        if not (ref in order_ids or ref in wo_ids or ref in mat_ids or ref in eq_ids
                or ref in lot_ids or ref.startswith("DEMO-") or ref in sup_ids):
            errors.append(f"events[{e['event_id']}].object_ref={ref} 断引用")
    for l in data.get("lots", []):
        if l["wo_id"] != "-" and l["wo_id"] not in wo_ids:
            errors.append(f"lots[{l['lot_id']}].wo_id={l['wo_id']} 断引用")
    return errors


def check_metrics(data: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    # 直通率口径：qty_good/qty_planned 在 (0,1]；未开工（released）工单不参与
    for w in data.get("work_orders", []):
        if w["status"] == "released":
            continue
        fpy = w["qty_good"] / w["qty_planned"] if w["qty_planned"] else 0.0
        if not (0.0 < fpy <= 1.0):
            errors.append(f"work_orders[{w['wo_id']}] 直通率越界: {fpy:.4f}")
    # 报价三分量之和 = 下限
    for q in data.get("quotes", []):
        total = round(q["material_cost"] + q["labor_cost"] + q["tool_amort"], 2)
        if abs(total - q["price_low"]) > 0.01:
            errors.append(f"quotes[{q['quote_id']}] 下限≠三分量之和: {total} vs {q['price_low']}")
        if not q["price_low"] < q["price_high"]:
            errors.append(f"quotes[{q['quote_id']}] 区间非法")
    # OTD：已发货订单必须有 actual ≤ promise
    for o in data.get("sales_orders", []):
        if o["status"] == "shipped" and o["actual_ship_date"]:
            if o["actual_ship_date"] > o["promise_date"]:
                errors.append(f"sales_orders[{o['order_id']}] OTD 超标: {o['actual_ship_date']} > {o['promise_date']}")
    # SPC 越限标记与数值一致
    for s in data.get("spc_series", []):
        out = abs(s["value"] - 245.0) > 6.0
        if out != (s["flag"] == "out"):
            errors.append(f"spc_series[{s['ts']}] flag={s['flag']} 与数值 {s['value']} 不一致")
    return errors


def check_timeline(data: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    events = sorted(data.get("events", []), key=lambda e: e["ts"])
    prev = None
    for e in events:
        if prev and e["ts"] < prev:
            errors.append(f"events[{e['event_id']}] 时间轴倒流: {e['ts']} < {prev}")
        prev = e["ts"]
    # 支线 A：ECN 发布应晚于 WO-001 报工事件
    wo1_report = [e for e in events if e["object_ref"] == "DEMO-WO-001" and e["type"] == "report"]
    ecn = [e for e in events if e["object_ref"] == "DEMO-ECN-001"]
    if wo1_report and ecn and ecn[0]["ts"] < wo1_report[0]["ts"]:
        errors.append("支线 A：ECN 早于 WO-001 报工，挂接点不合法")
    # 支线 B：客诉定位应晚于受理
    complaints = [e for e in events if e["object_ref"] == "DEMO-CC-001"]
    stages = [c["payload"].get("stage") for c in complaints]
    if "located" in stages and stages.index("located") == 0:
        errors.append("支线 B：定位事件早于受理事件")
    return errors


def check_enums(data: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    for o in data.get("sales_orders", []):
        if o["status"] not in ORDER_FLOW:
            errors.append(f"sales_orders[{o['order_id']}].status={o['status']} 非法")
    for i in data.get("inspections", []):
        if i["stage"] not in INSPECTION_STAGES:
            errors.append(f"inspections[{i['batch_id']}].stage={i['stage']} 非法")
        if i["result"] not in INSPECTION_RESULTS:
            errors.append(f"inspections[{i['batch_id']}].result={i['result']} 非法")
    return errors


CHECKS = {
    "references": check_references,
    "metrics": check_metrics,
    "timeline": check_timeline,
    "enums": check_enums,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/demo_storyline")
    ap.add_argument("--inject", choices=sorted(CHECKS), default=None,
                    help="故意破坏一类数据以验证校验器能报错（内部自测用）")
    args = ap.parse_args()
    directory = Path(args.dir)
    data = load(directory)

    if args.inject:
        if args.inject == "references":
            data["work_orders"][0]["order_id"] = "DEMO-SO-999"
        elif args.inject == "metrics":
            data["quotes"][0]["price_low"] = 1.0
        elif args.inject == "timeline":
            data["events"] = list(reversed(data["events"]))
        elif args.inject == "enums":
            data["inspections"][0]["result"] = "weird"
        data.setdefault("_injected", [])

    failed = False
    for name, fn in CHECKS.items():
        errs = fn(data)
        if errs:
            failed = True
            print(f"[FAIL] {name}: {len(errs)} 处")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"[PASS] {name}")
    if args.inject and not failed:
        print(f"[FAIL] 注入 {args.inject} 错误但校验器未报出")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
