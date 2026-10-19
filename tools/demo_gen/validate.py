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

# ---------- 回放包契约（T16，架构 §2.1 / 共享知识 #1/#4） ----------
REPLAY_SCHEMA_VERSION = "1.0"
REPLAY_SCENES = {"S1", "S2", "S3", "S4", "S5", "S6"}
REPLAY_KINDS = {"agent", "case_search", "kbqa"}
#: steps 六键与 AgenticStep.to_dict() 全同（多一键/少一键都算漂移）
REPLAY_STEP_KEYS = {"step", "thought", "tool", "args", "summary", "ok"}
NODE_KINDS = {"agent", "fan_out", "fan_in", "workflow", "hitl"}
SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "scenarios"


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


def check_replays(data: dict[str, list[dict]]) -> list[str]:
    """回放包自洽（T16 第五类）：信封 / steps 同构 / 引用 / DAG↔YAML。

    回放包是「智能体轨迹与 RAG 检索结果」的演示快照，前端把它当真实链路
    同构渲染——所以包的**形状**必须与真实契约逐键相等，引用的案例 / FAQ /
    事件 / 对象必须真实存在，DAG 拓扑必须与场景 YAML 对得上。任何一项断链，
    屏上就会出现「看起来有智能、其实无出处」的内容，必须在数据层拦住。
    """
    errors: list[str] = []
    packs = list(data.get("agent_replays", [])) + list(data.get("rag_replays", []))
    if not packs:
        return ["agent_replays / rag_replays 为空（T16 生成或落盘失败）"]

    event_ids = {e["event_id"] for e in data.get("events", [])}
    case_ids = {c["case_id"] for c in data.get("cases", [])}
    faq_ids = {f["faq_id"] for f in data.get("faqs", [])}
    known_ids = _collect_demo_ids(data)
    seen: set[str] = set()

    for pack in packs:
        rid = str(pack.get("replay_id") or "?")

        # 1) 信封完整
        if rid in seen:
            errors.append(f"回放包 [{rid}] replay_id 重复")
        seen.add(rid)
        if pack.get("schema_version") != REPLAY_SCHEMA_VERSION:
            errors.append(f"回放包 [{rid}].schema_version="
                          f"{pack.get('schema_version')!r}，应为 {REPLAY_SCHEMA_VERSION!r}")
        if pack.get("scene") not in REPLAY_SCENES:
            errors.append(f"回放包 [{rid}].scene={pack.get('scene')!r} 非法")
        if pack.get("kind") not in REPLAY_KINDS:
            errors.append(f"回放包 [{rid}].kind={pack.get('kind')!r} 非法")
        if pack.get("source") != "replay":
            errors.append(f"回放包 [{rid}].source={pack.get('source')!r}，P0 只允许 replay")
        if not str(pack.get("title") or "").strip():
            errors.append(f"回放包 [{rid}].title 为空")
        agents = pack.get("agents") or []
        if not agents:
            errors.append(f"回放包 [{rid}].agents 为空")
        for agent in agents:
            for key in ("domain", "abbr", "name", "role"):
                if not str(agent.get(key) or "").strip():
                    errors.append(f"回放包 [{rid}].agents[{agent.get('domain')}].{key} 为空")

        # 2) steps 六键同构（主链 + 各节点步骤）
        if not pack.get("steps"):
            errors.append(f"回放包 [{rid}].steps 为空")
        step_groups = [("steps", pack.get("steps") or [])]
        for node_id, node_steps in (pack.get("node_steps") or {}).items():
            step_groups.append((f"node_steps[{node_id}]", node_steps or []))
        for where, steps in step_groups:
            for idx, step in enumerate(steps):
                keys = set(step)
                if keys != REPLAY_STEP_KEYS:
                    errors.append(
                        f"回放包 [{rid}].{where}[{idx}] 键集不符："
                        f"多 {sorted(keys - REPLAY_STEP_KEYS)} / "
                        f"缺 {sorted(REPLAY_STEP_KEYS - keys)}")

        # 3) 引用完整
        kind = pack.get("kind")
        if kind == "case_search":
            for hit in pack.get("hits") or []:
                if hit.get("case_id") not in case_ids:
                    errors.append(f"回放包 [{rid}] 命中案例 {hit.get('case_id')} 不存在于 cases")
        if kind == "kbqa" and pack.get("faq_id") not in faq_ids:
            errors.append(f"回放包 [{rid}] faq_id={pack.get('faq_id')} 不存在于 faqs")
        trigger = pack.get("trigger") or {}
        if kind == "agent" and trigger.get("event_id") not in event_ids:
            errors.append(f"回放包 [{rid}] trigger.event_id={trigger.get('event_id')} "
                          "不存在于 events")
        for token in _demo_tokens(pack.get("conclusion") or {}):
            if token not in known_ids:
                errors.append(f"回放包 [{rid}].conclusion 引用 {token} 不存在于任何对象表")

        # 4) DAG ↔ 场景 YAML 对回（仅 kind=agent 且声明了 scenario_key）
        scenario_key = str(pack.get("scenario_key") or "")
        if kind == "agent" and scenario_key:
            errors.extend(_check_dag_against_yaml(rid, scenario_key, pack))

    return errors


def _collect_demo_ids(data: dict[str, list[dict]]) -> set[str]:
    """全部对象表里可被引用的 id（*_id / object_ref），用于 data_used 对账。"""
    ids: set[str] = set()
    for name, rows in data.items():
        if name.startswith("_"):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if isinstance(value, str) and (key.endswith("_id") or key == "object_ref"):
                    ids.add(value)
    return ids


def _demo_tokens(obj) -> set[str]:
    """从结论 JSON 里抽出 DEMO-* 业务对象号（如 DEMO-CASE-006 / DEMO-LOT-002）。"""
    import re as _re
    text = json.dumps(obj, ensure_ascii=False)
    return set(_re.findall(r"DEMO-[A-Z]+-[0-9A-Z]+", text))


def _yaml_module():
    """pyyaml 懒加载（生成器/校验器保持零新依赖；缺失时对回检查降级跳过）。"""
    try:
        import yaml  # noqa: PLC0415 —— 可选依赖，缺失时返回 None
    except ImportError:  # pragma: no cover
        return None
    return yaml


def _check_dag_against_yaml(rid: str, scenario_key: str, pack: dict) -> list[str]:
    """回放 DAG 与 scenarios/<key>.yaml 的节点 / 分支 / HITL 槽位逐项对回。"""
    errors: list[str] = []
    path = SCENARIOS_DIR / f"{scenario_key}.yaml"
    if not path.exists():
        return [f"回放包 [{rid}] 声明的场景 {scenario_key} 不存在（{path}）"]
    yaml = _yaml_module()
    if yaml is None:  # pragma: no cover —— 无 pyyaml 环境下跳过本项（其它四类仍生效）
        return errors
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    expected: dict[str, dict] = {}
    for step in spec.get("steps") or []:
        sid = step.get("id")
        expected[sid] = {
            "id": sid,
            "label": step.get("label") or step.get("title") or step.get("prompt") or "",
            "agent": step.get("agent") or "",
            "kind": step.get("type") or "agent",
        }
        for branch in step.get("branches") or []:
            bid = branch.get("id")
            expected[bid] = {
                "id": bid,
                "label": branch.get("label") or "",
                "agent": branch.get("agent") or "",
                "kind": branch.get("type") or "agent",
            }

    dag = pack.get("dag") or {}
    actual = {node.get("id"): node for node in dag.get("nodes") or []}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"回放包 [{rid}] DAG 缺 YAML 节点：{missing}")
    if extra:
        errors.append(f"回放包 [{rid}] DAG 多出 YAML 没有的节点：{extra}")
    for sid in sorted(set(expected) & set(actual)):
        want, got = expected[sid], actual[sid]
        if got.get("kind") not in NODE_KINDS:
            errors.append(f"回放包 [{rid}] 节点 {sid}.kind={got.get('kind')!r} 非法")
        if want["kind"] != got.get("kind"):
            errors.append(f"回放包 [{rid}] 节点 {sid}.kind "
                          f"YAML={want['kind']} 回放={got.get('kind')}")
        if want["agent"] != (got.get("agent") or ""):
            errors.append(f"回放包 [{rid}] 节点 {sid}.agent "
                          f"YAML={want['agent']!r} 回放={got.get('agent')!r}")
        # 标签：YAML 给了 label/title/prompt 才比对（fan_out/fan_in 的展开口径由回放命名）
        if want["label"] and want["label"] != got.get("label"):
            errors.append(f"回放包 [{rid}] 节点 {sid}.label "
                          f"YAML={want['label']!r} 回放={got.get('label')!r}")

    # HITL 槽位：集合与审批岗位都要对回（YAML 的 step 是节点 id `hitl_xxx`，
    # 回放包的 slot 是治理槽位 `xxx`——按去掉 `hitl_` 前缀对齐）
    yaml_hitl = {str(h.get("step") or "").removeprefix("hitl_"): h
                 for h in (spec.get("hitl") or [])}
    replay_hitl = {h.get("slot"): h for h in (pack.get("hitl_slots") or [])}
    if set(yaml_hitl) != set(replay_hitl):
        errors.append(f"回放包 [{rid}] HITL 槽位不对回："
                      f"YAML={sorted(yaml_hitl)} 回放={sorted(replay_hitl)}")
    for slot in sorted(set(yaml_hitl) & set(replay_hitl)):
        want_roles = set(yaml_hitl[slot].get("approver_roles") or [])
        got_roles = set(replay_hitl[slot].get("approver_roles") or [])
        if want_roles != got_roles:
            errors.append(f"回放包 [{rid}] HITL {slot} 审批岗位不对回："
                          f"YAML={sorted(want_roles)} 回放={sorted(got_roles)}")

    # fan_out 分支与 condition
    for step in spec.get("steps") or []:
        if step.get("type") != "fan_out":
            continue
        node = actual.get(step.get("id")) or {}
        if step.get("condition") and step.get("condition") != node.get("condition"):
            errors.append(f"回放包 [{rid}] fan_out 条件不对回："
                          f"YAML={step.get('condition')!r} 回放={node.get('condition')!r}")
        want_branches = {b.get("id") for b in (step.get("branches") or [])}
        got_branches = {n.get("id") for n in dag.get("nodes") or [] if n.get("branch")}
        if want_branches != got_branches:
            errors.append(f"回放包 [{rid}] fan_out 分支不对回："
                          f"YAML={sorted(want_branches)} 回放={sorted(got_branches)}")
    return errors


CHECKS = {
    "references": check_references,
    "metrics": check_metrics,
    "timeline": check_timeline,
    "enums": check_enums,
    "replays": check_replays,
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
        elif args.inject == "replays":
            # T16 注入：命中一个不存在的案例编号 → check_replays 必须报出断引用
            data["rag_replays"][0]["hits"][0]["case_id"] = "DEMO-CASE-999"
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
