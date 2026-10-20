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

# ---------- 回放包（T16）----------

#: 回放包 schema 版本（架构 §2.1：固定 "1.0"，只加字段不改语义；
#: SceneKit.replay() 与 loader/api 同口径校验，缺失/不符 → 整块降级）
REPLAY_SCHEMA_VERSION = "1.0"

#: 智能体域 → abbr/name 镜像。**抄自** mfg_agents.v2.agents.agentic
#: 的 DOMAIN_AGENT_CONFIGS（生成器保持零依赖，漂移由
#: tests/demo/test_replay_pipeline.py 断言与真实配置相等来兜底）。
AGENT_CONFIGS_MIRROR: dict[str, dict[str, str]] = {
    "quality": {"abbr": "QA", "name": "质量域智能体"},
    "manufacturing": {"abbr": "MFG", "name": "制造运营智能体"},
    "rd_engineering": {"abbr": "RD", "name": "研发工程智能体"},
    "procurement": {"abbr": "PROC", "name": "采购智能体"},
    "supply_chain": {"abbr": "SC", "name": "供应链智能体"},
}

#: 智能体域 → 一句话职责摘要（架构 §2.2：固定映射，不搬完整 prompt）。
ROLE_SUMMARY: dict[str, str] = {
    "quality": "客诉 8D 牵头与质量判定",
    "manufacturing": "批量过程监控与 SPC 判异处置",
    "rd_engineering": "工程设计与历史案例检索",
    "procurement": "缺口判定与供应商案例推送",
    "supply_chain": "供应链计划与催料协同",
}

#: 回放步骤六键（与 AgenticStep.to_dict() 六键全同，架构 §2.1 / 共享知识 #4）。
REPLAY_STEP_KEYS = ("step", "thought", "tool", "args", "summary", "ok")


def _agent_of(domain: str) -> dict[str, str]:
    """ agents[] 一行：domain/abbr/name 命中 DOMAIN_AGENT_CONFIGS 镜像，role 取固定摘要。"""
    cfg = AGENT_CONFIGS_MIRROR[domain]  # KeyError = 域配置漂移，生成器立刻失败
    return {"domain": domain, "abbr": cfg["abbr"], "name": cfg["name"],
            "role": ROLE_SUMMARY[domain]}


def _step(no: int, thought: str, tool: str, args: dict, summary: str,
          ok: bool = True) -> dict:
    """一步回放（六键全同 AgenticStep.to_dict()；tool 取自 AGENT_TOOLS_META）。"""
    return {"step": no, "thought": thought, "tool": tool, "args": args,
            "summary": summary, "ok": ok}


def _envelope(replay_id: str, scene: str, kind: str, title: str,
              agents: list[dict], steps: list[dict], conclusion: dict,
              **extra) -> dict:
    """回放包共用信封（架构 §2.1：所有包必带八键，kind 决定扩展字段）。"""
    pkg = {
        "replay_id": replay_id,
        "schema_version": REPLAY_SCHEMA_VERSION,
        "scene": scene,
        "kind": kind,
        "title": title,
        "agents": agents,
        "steps": steps,
        "conclusion": conclusion,
        "source": "replay",  # P1-1 实时形态才为 "live"，P0 不产
    }
    pkg.update(extra)
    return pkg


def _find_event(events: list[dict], predicate) -> dict:
    """按内容谓词找锚点事件（内容派生纪律：回放包不手抄第二份事件号）。

    找不到即抛错——锚点事件缺失说明上游数据被改，回放包必须立刻失败，
    而不是静默产出一个断引用包。
    """
    for event in events:
        if predicate(event):
            return event
    raise KeyError("回放包锚点事件缺失（events.jsonl 被改？请同步回放构建谓词）")


def _row_by_id(rows: list[dict], key: str, value: str) -> dict:
    """按主键取一行（案例标题/根因、FAQ 问答对都从这里派生，不手抄）。"""
    for row in rows:
        if row.get(key) == value:
            return row
    raise KeyError(f"回放包引用不存在的 {key}={value}")


def _stock_lots(lots: list[dict], lot: dict) -> list[dict]:
    """同批库存：与锚点批次同库位的批（口径同 S5 屏：库位串去掉括号备注后比较）。"""
    where = str(lot.get("location") or "").split("（")[0]
    return [row for row in lots
            if str(row.get("location") or "").split("（")[0] == where]


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
            # T16（U2 已裁决·确认新增）：现有 10 条无接单评审制度条目，US-6 无法对回；
            # 内容与 T15 已落地的 order_review 治理事实（propose=业务 / cosign=工程+计划 /
            # approve=业务）逐字对得上，数据自洽有出处。
            ("接单评审会签怎么发起？", "订单过 ATP 初评与报价区间后，由业务在接单评审场景发起一次会签，工程与计划会签后业务终批。"),
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

    data = {
        "customers": customers, "materials": materials, "boms": boms, "suppliers": suppliers,
        "equipment": equipment, "sales_orders": sales_orders, "quotes": quotes,
        "work_orders": wos, "inspections": inspections, "lots": lots,
        "purchase_orders": purchase_orders, "spc_series": spc_series,
        "events": events, "faqs": faqs, "cases": cases,
    }
    # T16：回放包（4 智能体轨迹 + 7 RAG 检索）——全部从上面已生成的行派生，
    # 不手抄第二份；固定种子下重跑字节级一致（架构 §2.2 内容派生纪律）。
    data["agent_replays"] = _build_agent_replays(
        events, cases, lots, inspections, equipment, spc)
    data["rag_replays"] = _build_rag_replays(events, cases, faqs)
    return data


def _build_agent_replays(events: list[dict], cases: list[dict], lots: list[dict],
                         inspections: list[dict], equipment: list[dict],
                         spc: dict) -> list[dict]:
    """4 个智能体协同回放包（架构 §2.1 清单；数字全部从已生成行派生）。"""
    # ---- S5 客诉 8D（customer_complaint_v2 全 DAG）----
    complaint = _find_event(
        events, lambda e: e["object_ref"] == "DEMO-CC-001" and e["type"] == "complaint"
        and not (e.get("payload") or {}).get("stage"))
    located = _find_event(
        events, lambda e: e["object_ref"] == "DEMO-CC-001" and e["type"] == "complaint"
        and (e.get("payload") or {}).get("stage") == "located")
    cp, lp = complaint["payload"], located["payload"]
    trace = lp["trace"]
    lot = _row_by_id(lots, "lot_id", trace["lot"])
    stock = _stock_lots(lots, lot)
    stock_qty = sum(int(row["qty"]) for row in stock)
    root_cause = _row_by_id(cases, "case_id", "DEMO-CASE-006")["root_cause"]
    ipqc = next(i for i in inspections
                if i["wo_id"] == trace["wo"] and i["stage"] == "IPQC")
    iqc = next(i for i in inspections if i["stage"] == "IQC")
    eq = _row_by_id(equipment, "equipment_id", trace["equipment"])

    s05_nodes = [
        {"id": "intake", "label": "业务Agent · 意图识别", "agent": "business", "kind": "agent"},
        {"id": "qa_lead", "label": "QA · 8D 牵头", "agent": "qa_lead", "kind": "agent"},
        {"id": "fanout", "label": "四域并行取证", "agent": "", "kind": "fan_out",
         "condition": "complaint_found"},
        {"id": "pqe_process", "label": "PQE · 过程分析", "agent": "pqe_process",
         "kind": "agent", "branch": "过程分析"},
        {"id": "sqe_supplier", "label": "SQE · 供应商侧", "agent": "sqe_supplier",
         "kind": "agent", "branch": "供应商侧"},
        {"id": "engineering", "label": "工程 · 设计根因", "agent": "engineering",
         "kind": "agent", "branch": "设计根因"},
        {"id": "business_sales", "label": "业务 · 对客口径", "agent": "business_sales",
         "kind": "agent", "branch": "对客口径"},
        {"id": "fanin", "label": "四域汇聚", "agent": "", "kind": "fan_in"},
        {"id": "dispatch_site", "label": "现场分派 · 责任工段", "agent": "site_dispatch",
         "kind": "agent"},
        {"id": "capa_flow", "label": "客诉 8D 整改工单", "agent": "", "kind": "workflow"},
        {"id": "hitl_d8_close", "label": "8D 报告关闭确认", "agent": "hitl_d8_close",
         "kind": "hitl", "slot": "d8_close"},
        {"id": "hitl_complaint_reply", "label": "对客回复确认（脱敏后）",
         "agent": "hitl_complaint_reply", "kind": "hitl", "slot": "complaint_reply"},
        {"id": "report_complaint", "label": "报告Agent · 8D/对客报告",
         "agent": "report_complaint", "kind": "agent"},
    ]
    s05_edges = [
        {"from": "intake", "to": "qa_lead"}, {"from": "qa_lead", "to": "fanout"},
        {"from": "fanout", "to": "pqe_process"}, {"from": "fanout", "to": "sqe_supplier"},
        {"from": "fanout", "to": "engineering"}, {"from": "fanout", "to": "business_sales"},
        {"from": "pqe_process", "to": "fanin"}, {"from": "sqe_supplier", "to": "fanin"},
        {"from": "engineering", "to": "fanin"}, {"from": "business_sales", "to": "fanin"},
        {"from": "fanin", "to": "dispatch_site"},
        {"from": "dispatch_site", "to": "capa_flow"},
        {"from": "capa_flow", "to": "hitl_d8_close"},
        {"from": "hitl_d8_close", "to": "hitl_complaint_reply"},
        {"from": "hitl_complaint_reply", "to": "report_complaint"},
    ]
    s05 = _envelope(
        "s05_complaint_8d", "S5", "agent",
        f"客诉 8D 多智能体协同（{complaint['object_ref']}）",
        [_agent_of("quality"), _agent_of("rd_engineering"), _agent_of("supply_chain")],
        [
            _step(1, "客诉事件进入，先确认投诉事实与影响面，再匹配协同场景",
                  "list_scenarios", {"keywords": ["客诉", "8D"], "scene": "S5"},
                  f"客诉 {complaint['object_ref']}：{cp['qty']} 片{cp['symptom']}，"
                  "命中协同场景 customer_complaint_v2"),
            _step(2, "按批次回溯工单 / 工序 / 设备 / 当班，确认定位结论",
                  "run_registered_query",
                  {"query_name": "lot_trace", "lot_id": trace["lot"]},
                  f"定位结论：批次 {trace['lot']} → 工单 {trace['wo']} · "
                  f"工序 {trace['op']} · 设备 {trace['equipment']} · {trace['shift']}"),
            _step(3, "定位后合成 8D 骨架，并评估同批库存影响面",
                  "run_registered_query",
                  {"query_name": "stock_impact", "lot_id": trace["lot"]},
                  f"同批库存 {len(stock)} 批 {stock_qty:,} 件，8D 骨架（D1–D8）已合成，"
                  "待四域并行取证"),
        ],
        {
            "answer": (f"四域并行取证收敛：{cp['symptom']}根因（{root_cause}）定位到批次 "
                       f"{trace['lot']}（工单 {trace['wo']} · 工序 {trace['op']} · "
                       f"设备 {trace['equipment']} · {trace['shift']}），同批库存 "
                       f"{len(stock)} 批 {stock_qty:,} 件待围堵；8D 整改工单（SLA 72h）"
                       "与双 HITL（d8_close / complaint_reply）按槽位流转。"),
            "data_used": [
                f"{complaint['object_ref']}（events · complaint 受理）",
                f"{trace['lot']}（lots）", f"{trace['wo']}（work_orders）",
                f"{trace['equipment']}（equipment）",
                f"{located['event_id']}（events · stage=located）",
                "DEMO-CASE-006（cases · 同根因）",
            ],
            "needs_hitl": True, "hitl_title": "8D 报告关闭确认",
            "hitl_slot": "d8_close", "data_gaps": [],
        },
        scenario_key="customer_complaint_v2",
        trigger={"event_id": complaint["event_id"], "type": "complaint",
                 "object_ref": complaint["object_ref"],
                 "summary": f"客诉受理：{cp['qty']} 片{cp['symptom']}"},
        dag={"nodes": s05_nodes, "edges": s05_edges},
        node_steps={
            "intake": [_step(1, "识别客诉意图并匹配 8D 闭环协同场景",
                             "list_scenarios", {"keywords": ["客诉", "8D"]},
                             "进入 customer_complaint_v2：QA 牵头、四域并行")],
            "pqe_process": [_step(1, "过程侧取工序检验记录，确认不良集中在烧录后测试",
                                  "run_registered_query",
                                  {"query_name": "inspection_by_wo",
                                   "wo_id": trace["wo"], "stage": "IPQC"},
                                  f"工序 {trace['op']} IPQC 抽检 {ipqc['sample_qty']}、"
                                  f"不良 {ipqc['defect_qty']}（检验批 {ipqc['batch_id']}）")],
            "sqe_supplier": [_step(1, "供应商侧取来料检验，确认物料批次无系统性异常",
                                   "run_registered_query",
                                   {"query_name": "inspection_iqc"},
                                   f"IQC 抽检 {iqc['sample_qty']}、不良 "
                                   f"{iqc['defect_qty']}（检验批 {iqc['batch_id']}），"
                                   "来料批次合格")],
            "engineering": [_step(1, "工程侧核对工序设备与接地要求，定位设计根因",
                                  "run_registered_query",
                                  {"query_name": "equipment_status",
                                   "equipment_id": trace["equipment"]},
                                  f"工序 {trace['op']} 使用设备 {trace['equipment']}"
                                  f"（{eq['name']}，状态 {eq['status']}）；"
                                  f"根因口径：{root_cause}")],
            "business_sales": [_step(1, "业务侧整理对客口径：影响面与围堵进度",
                                     "run_registered_query",
                                     {"query_name": "order_by_wo",
                                      "wo_id": trace["wo"]},
                                     f"工单 {trace['wo']} 归属主线订单；对客口径："
                                     f"{cp['qty']} 片{cp['symptom']}，围堵中")],
            "capa_flow": [_step(1, "8D 整改工单按 capa_flow 建立，SLA 对齐工作流节点",
                                "create_hitl_task",
                                {"slot": "d8_close", "title": "客诉 8D 整改工单"},
                                "8D 整改工单已建（SLA 72h，对齐 capa_flow 节点）")],
            "report_complaint": [_step(1, "合成 8D 报告与对客脱敏回复口径",
                                       "run_registered_query",
                                       {"query_name": "complaint_8d_report",
                                        "complaint_id": complaint["object_ref"]},
                                       "8D 报告（D1–D8）与对客脱敏回复口径已合成")],
        },
        hitl_slots=[
            {"slot": "d8_close", "title": "8D 报告关闭确认",
             "approver_roles": ["质量总监", "QA经理"]},
            {"slot": "complaint_reply", "title": "对客回复确认",
             "approver_roles": ["业务经理"]},
        ],
    )

    # ---- S4 SPC 判异（spc_anomaly_v2 DAG；U4：spc_action 只由回放承载，不做种子）----
    alert = _find_event(
        events, lambda e: e["object_ref"] == "DEMO-EQ-002" and e["type"] == "alert"
        and (e.get("payload") or {}).get("kind") == "spc")
    ap = alert["payload"]
    cl, tol = float(spc["target"]), float(spc["tolerance"])
    lsl, usl = round(cl - tol, 1), round(cl + tol, 1)
    s04_nodes = [
        {"id": "intake", "label": "业务Agent · 意图识别", "agent": "business", "kind": "agent"},
        {"id": "spc_capability", "label": "PQE · 过程能力判定", "agent": "spc_capability",
         "kind": "agent"},
        {"id": "fanout", "label": "不良柏拉图取证", "agent": "", "kind": "fan_out",
         "condition": "spc_alert"},
        {"id": "spc_pareto", "label": "PQE · 不良柏拉图", "agent": "spc_pareto",
         "kind": "agent", "branch": "不良柏拉图"},
        {"id": "fanin", "label": "取证汇聚", "agent": "", "kind": "fan_in"},
        {"id": "hitl_spc_action", "label": "过程能力异常处置决策确认",
         "agent": "hitl_spc_action", "kind": "hitl", "slot": "spc_action"},
        {"id": "report_spc", "label": "报告Agent · 过程能力简报", "agent": "report_spc",
         "kind": "agent"},
    ]
    s04 = _envelope(
        "s04_spc_anomaly", "S4", "agent",
        f"SPC 判异智能体协同（{ap['param']} {ap['value']} 越限）",
        [_agent_of("quality"), _agent_of("manufacturing")],
        [
            _step(1, "判异预警进入，确认越限参数与控制限口径",
                  "run_registered_query",
                  {"query_name": "spc_alert_detail",
                   "equipment_id": alert["object_ref"]},
                  f"{ap['param']}实测 {ap['value']}，控制限 CL={cl} / LSL={lsl} / "
                  f"USL={usl}（容差 {tol}）"),
            _step(2, "按过程能力口径判定越限，并联不良柏拉图定位关键少数",
                  "run_registered_query",
                  {"query_name": "spc_series", "equipment_id": alert["object_ref"]},
                  f"序列判定越限（实测 {ap['value']} > USL {usl}），"
                  "转不良柏拉图并行取证"),
        ],
        {
            "answer": (f"{ap['param']} {ap['value']} 超出控制上限 USL {usl}"
                       f"（CL={cl} / LSL={lsl} / USL={usl}）；按 U4 裁决，"
                       "spc_action 处置决策由回放 DAG 承载（演示回放，"
                       "不发起真实工单、不写库）。"),
            "data_used": [
                f"{alert['event_id']}（events · alert 判异）",
                f"{alert['object_ref']}（equipment）",
                "spc_series（spc_series · 30 点序列）",
            ],
            "needs_hitl": True, "hitl_title": "过程能力异常处置决策确认",
            "hitl_slot": "spc_action", "data_gaps": [],
        },
        scenario_key="spc_anomaly_v2",
        trigger={"event_id": alert["event_id"], "type": "alert",
                 "object_ref": alert["object_ref"],
                 "summary": f"SPC 判异：{ap['param']} {ap['value']} 偏离（越限）"},
        dag={"nodes": s04_nodes,
             "edges": [
                 {"from": "intake", "to": "spc_capability"},
                 {"from": "spc_capability", "to": "fanout"},
                 {"from": "fanout", "to": "spc_pareto"},
                 {"from": "spc_pareto", "to": "fanin"},
                 {"from": "fanin", "to": "hitl_spc_action"},
                 {"from": "hitl_spc_action", "to": "report_spc"},
             ]},
        node_steps={
            "spc_pareto": [_step(1, "按不良类型汇总柏拉图，定位关键少数",
                                 "run_registered_query",
                                 {"query_name": "defect_pareto",
                                  "equipment_id": alert["object_ref"]},
                                 "不良柏拉图已产出：炉温相关不良占关键少数")],
            "report_spc": [_step(1, "汇总能力判定与取证结论，产出内部简报",
                                 "run_registered_query",
                                 {"query_name": "spc_brief"},
                                 "过程能力简报已产出（越限场景无对客模板）")],
        },
        hitl_slots=[
            {"slot": "spc_action", "title": "过程能力异常处置决策确认",
             "approver_roles": ["质量总监", "制造现场负责人"]},
        ],
    )

    # ---- S2 立项案例检索（单链四步，对齐 case-retrieval-ux-innovation I3/I6）----
    s02_event = _find_event(events, lambda e: e["scene"] == "S2")
    s02 = _envelope(
        "s02_case_rag", "S2", "agent", "立项案例检索智能体（MBOM / 上料类）",
        [_agent_of("rd_engineering")],
        [
            _step(1, "评审关键词先过受控词表，同一现象的不同写法归一到同一概念",
                  "search_knowledge", {"query": "上料错贴片 MBOM", "channel": "case"},
                  "术语归一：命中「上料错贴片」「MBOM 映射」两个受控词条"),
            _step(2, "归一后的概念经本体扩展同义与上下位词，扩大召回",
                  "search_knowledge", {"query": "上料错贴片·扩展", "channel": "case"},
                  "同义扩展：扩展「料站映射表未更新」「贴片偏移」"),
            _step(3, "召回前按调用者岗位 ACL 编译授权过滤，越权案例不进结果",
                  "search_knowledge", {"query": "上料错贴片·授权", "channel": "case"},
                  "授权过滤：按研发域 ACL 编译，无越权案例进入"),
            _step(4, "引用校验：结论只引用可见且文本哈希一致的案例",
                  "search_knowledge", {"query": "上料错贴片·引用", "channel": "case"},
                  "引用校验通过：命中案例均为可见且版本一致"),
        ],
        {
            "answer": ("立项视角检索收敛：MBOM / 上料类历史问题自动推给评审人，"
                       "命中「上料错贴片（料站与 MBOM 映射表未更新）」等案例，"
                       "检索过程四步可回放。"),
            "data_used": ["DEMO-CASE-008（cases · 上料错贴片）",
                          "DEMO-CASE-004（cases · MBOM 不一致）"],
            "needs_hitl": False, "data_gaps": [],
        },
        scenario_key="",
        trigger={"event_id": s02_event["event_id"],
                 "type": s02_event["type"],
                 "object_ref": s02_event["object_ref"],
                 "summary": "立项材料汇总完成，触发历史案例检索"},
        dag={"nodes": [
            {"id": "term_normalize", "label": "术语归一", "agent": "case_rag_agent",
             "kind": "agent"},
            {"id": "ontology_expand", "label": "本体扩展", "agent": "case_rag_agent",
             "kind": "agent"},
            {"id": "acl_filter", "label": "授权过滤", "agent": "case_rag_agent",
             "kind": "agent"},
            {"id": "citation_check", "label": "引用校验", "agent": "case_rag_agent",
             "kind": "agent"},
        ], "edges": [
            {"from": "term_normalize", "to": "ontology_expand"},
            {"from": "ontology_expand", "to": "acl_filter"},
            {"from": "acl_filter", "to": "citation_check"},
        ]},
    )

    # ---- S6 缺料采购（单链三步：缺口机械判定 → 供应商案例 → 催料制度）----
    shortage = _find_event(
        events, lambda e: e["object_ref"] == "DEMO-PO-002" and e["type"] == "alert"
        and (e.get("payload") or {}).get("kind") == "shortage")
    sp = shortage["payload"]
    s06 = _envelope(
        "s06_shortage_procurement", "S6", "agent",
        f"缺料采购智能体（{shortage['object_ref']} 缺口 {sp['gap_qty']:,} 片）",
        [_agent_of("procurement"), _agent_of("supply_chain")],
        [
            _step(1, "缺口按 T08 机械口径判定：未交付且料期晚于需料日",
                  "run_registered_query",
                  {"query_name": "shortage_check", "po_id": shortage["object_ref"]},
                  f"{shortage['object_ref']}（{sp['material']}）缺口 "
                  f"{sp['gap_qty']:,} 片：未交付且料期晚于需料日，判定成立"),
            _step(2, "推送供应商来料类历史案例，供采购谈判与催料参考",
                  "search_knowledge",
                  {"query": "电容容舞 来料批次超差", "channel": "case"},
                  "命中 DEMO-CASE-002（电容容舞引发啸叫 · 供应商来料批次容舞超差）"),
            _step(3, "查催料制度：任务挂采购员待办，超 48 小时升级采购主管",
                  "search_knowledge",
                  {"query": "催料任务谁来跟进", "channel": "faq"},
                  "L1 命中 FAQ #DEMO-FAQ-008：催料任务挂采购员待办，超 48h 升级"),
        ],
        {
            "answer": (f"缺料判定成立：{shortage['object_ref']}（{sp['material']}）"
                       f"缺口 {sp['gap_qty']:,} 片；供应商案例与催料制度已推送，"
                       "催料待办按只读回放展示，不发起写操作。"),
            "data_used": [
                f"{shortage['event_id']}（events · alert 缺料预警）",
                f"{shortage['object_ref']}（purchase_orders）",
                f"{sp['material']}（materials）",
                "DEMO-CASE-002（cases · 供应商来料）",
                "DEMO-FAQ-008（faqs · 催料制度）",
            ],
            "needs_hitl": False, "data_gaps": [],
        },
        scenario_key="",
        trigger={"event_id": shortage["event_id"], "type": "alert",
                 "object_ref": shortage["object_ref"],
                 "summary": f"缺料预警：{sp['material']} 缺口 {sp['gap_qty']:,} 片"},
        dag={"nodes": [
            {"id": "gap_check", "label": "缺口机械判定（T08 口径）",
             "agent": "shortage_agent", "kind": "agent"},
            {"id": "supplier_case", "label": "供应商案例检索",
             "agent": "shortage_agent", "kind": "agent"},
            {"id": "urge_faq", "label": "催料制度查询", "agent": "shortage_agent",
             "kind": "agent"},
        ], "edges": [
            {"from": "gap_check", "to": "supplier_case"},
            {"from": "supplier_case", "to": "urge_faq"},
        ]},
    )
    return [s05, s04, s02, s06]


def _case_hit(cases: list[dict], case_id: str, score: float, reason: str) -> dict:
    """一条检索命中（架构 §2.1 形状；标题/根因/部门从 cases 行派生，不手抄）。"""
    row = _row_by_id(cases, "case_id", case_id)
    return {"case_id": case_id, "title": row["title"], "root_cause": row["root_cause"],
            "dept": row["dept"], "redacted": bool(row["redacted"]),
            "score": score, "reason": reason}


def _trace5() -> list[dict]:
    """案例检索五步轨迹（同构 case_qa._trace_step：步序/中文标题/中文说明/条目）。"""
    return [
        {"step": 1, "title": "术语归一", "detail": "检索词先过受控词表，同一现象的不同写法归一到同一个概念",
         "items": ["归一化词条已应用于本次召回"]},
        {"step": 2, "title": "同义扩展", "detail": "归一后的概念经本体扩展同义与上下位词，扩大召回面",
         "items": ["同义词 / 上下位词已并入查询"]},
        {"step": 3, "title": "本体检索", "detail": "以扩展后的概念检索案例知识层（脱敏内容）",
         "items": ["召回结果已按相关度排序"]},
        {"step": 4, "title": "授权过滤", "detail": "过滤在召回前生效，权限范围外的案例不计入命中",
         "items": ["ACL 按调用者岗位编译"]},
        {"step": 5, "title": "引用校验", "detail": "结论只引用可见且文本哈希与内容版本双一致的案例",
         "items": ["引用标识仅含案例编号"]},
    ]


def _case_search_pkg(replay_id: str, scene: str, title: str, domain: str,
                     query: str, hits: list[dict], answer: str,
                     data_used: list[str], gain_detail: str,
                     extra_agents: list[str] | None = None) -> dict:
    """kind=case_search 回放包（架构 §2.1：trace / filter_explain / ontology_gain 同构）。"""
    return _envelope(
        replay_id, scene, "case_search", title,
        [_agent_of(domain)] + [_agent_of(d) for d in (extra_agents or [])],
        [_step(1, "按问句执行案例检索，全程留痕可回放", "search_knowledge",
               {"query": query, "channel": "case"},
               f"命中 {len(hits)} 条案例；过滤与增益见 trace / filter_explain")],
        {"answer": answer, "data_used": data_used,
         "needs_hitl": False, "data_gaps": []},
        query=query, trace=_trace5(), hits=hits,
        filter_explain=[
            "授权范围：按调用者岗位 ACL 编译（表级 + 行级双层）",
            "状态过滤：涉密 / 未结案 / 已撤权案例一律不可披露",
            "相关度闸门：低于阈值的召回已丢弃，不用无关案例凑结论",
        ],
        hidden_count=0, hidden_count_unit="case",
        ontology_gain={"before": 0, "after": len(hits), "detail": gain_detail},
        restricted=[],
    )


def _kbqa_pkg(replay_id: str, scene: str, title: str, domain: str,
              faqs: list[dict], faq_id: str) -> dict:
    """kind=kbqa 回放包（架构 §2.1：L1 命中直答，channel=faq 不伪装来源）。"""
    row = _row_by_id(faqs, "faq_id", faq_id)
    label = f"FAQ #{faq_id}"
    return _envelope(
        replay_id, scene, "kbqa", title, [_agent_of(domain)],
        [_step(1, "制度问题先过 FAQ 双闸（L1 命中即直答，不调 LLM）",
               "search_knowledge", {"query": row["question"], "channel": "faq"},
               f"L1 命中 {label}（channel=faq，直答不带LLM生成）")],
        {"answer": row["answer"],
         "data_used": [f"{faq_id}（faqs · L1 命中直答）"],
         "needs_hitl": False, "data_gaps": []},
        question=row["question"], answer=row["answer"], channel="faq",
        faq_id=faq_id,
        sources=[{"label": label, "source": "faqs.jsonl", "segment": 0}],
        degraded=False, confidence=1.0,
    )


def _build_rag_replays(events: list[dict], cases: list[dict],
                       faqs: list[dict]) -> list[dict]:
    """7 个 RAG 回放包（4 case_search + 3 kbqa，架构 §2.1 清单）。"""
    s05_cases = _case_search_pkg(
        "s05_same_root_cause_cases", "S5", "同根因历史案例检索（客诉 8D）", "quality",
        "烧录异常 客诉 静电接地 同根因",
        [_case_hit(cases, "DEMO-CASE-006", 0.93,
                   "同根因：烧录异常客诉，根因均为烧录站静电接地失效")],
        "命中同根因案例 1 条：烧录异常客诉（烧录站静电接地失效）——"
        "8D 根因结论有历史依据可引用、可审计。",
        ["DEMO-CASE-006（cases · 同根因）"],
        "「烧录异常」经本体归一并同义扩展后，同根因案例召回 0 → 1 条")
    s04_cases = _case_search_pkg(
        "s04_spc_similar_cases", "S4", "相似不良案例检索（SPC 判异）", "quality",
        "炉温 波动 虚焊 回流焊",
        [_case_hit(cases, "DEMO-CASE-001", 0.88,
                   "同现象：炉温波动导致虚焊，根因为炉温区漂移未按周期校准")],
        "命中相似不良案例 1 条：炉温波动导致虚焊（回流焊炉温区漂移，"
        "未按点检周期校准）——判异处置有历史依据。",
        ["DEMO-CASE-001（cases · 炉温波动虚焊）"],
        "「炉温波动」经本体归并后，相似不良案例召回 0 → 1 条",
        extra_agents=["manufacturing"])
    s02_cases = _case_search_pkg(
        "s02_case_search", "S2", "立项历史案例检索（上料 / MBOM 类）", "rd_engineering",
        "上料 错贴 MBOM 映射表",
        [_case_hit(cases, "DEMO-CASE-008", 0.91,
                   "同机制：料站与 MBOM 映射表未更新导致上料错贴片"),
         _case_hit(cases, "DEMO-CASE-004", 0.79,
                   "相关：测试程序版本与 MBOM 不一致导致 ICT 误判")],
        "命中历史案例 2 条：上料错贴片（料站与 MBOM 映射表未更新）为主，"
        "MBOM 不一致导致 ICT 误判为辅——评审人信服「历史问题库自动推给评审人」。",
        ["DEMO-CASE-008（cases · 上料错贴片）", "DEMO-CASE-004（cases · MBOM 不一致）"],
        "「上料错贴」经本体归并（含 MBOM 映射同义）后，案例召回 0 → 2 条")
    s06_cases = _case_search_pkg(
        "s06_supplier_cases", "S6", "供应商来料案例检索（电容容舞类）", "procurement",
        "电容 容舞 来料 批次超差",
        [_case_hit(cases, "DEMO-CASE-002", 0.9,
                   "同机制：供应商来料批次容舞超差引发啸叫")],
        "命中供应商来料案例 1 条：电容容舞引发啸叫（供应商来料批次容舞超差）——"
        "催料谈判有历史依据。",
        ["DEMO-CASE-002（cases · 电容容舞）"],
        "「电容容舞」经本体归并后，来料类案例召回 0 → 1 条")
    return [
        s05_cases, s04_cases, s02_cases, s06_cases,
        _kbqa_pkg("s01_intake_faq", "S1", "接单评审制度问答（会签发起）",
                  "supply_chain", faqs, "DEMO-FAQ-011"),
        _kbqa_pkg("s03_gate_faq", "S3", "阶段门准则问答（Go 的条件）",
                  "quality", faqs, "DEMO-FAQ-007"),
        _kbqa_pkg("s06_urge_faq", "S6", "催料制度问答（任务跟进）",
                  "procurement", faqs, "DEMO-FAQ-008"),
    ]


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
