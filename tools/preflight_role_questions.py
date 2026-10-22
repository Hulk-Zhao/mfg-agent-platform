"""岗位同事推荐问题可答性预检（诊断脚本，不入库）。

读取 colleague.yaml 的 31 岗位 × 推荐问题，在进程内直接调用编排器
（不经 HTTP，改完代码立即生效），结果落 logs/role_qa_preflight.json。

判定口径（与「完整可靠回复」对齐）：
- ok：非 degraded、非 fallback=manual_ticket、report 非空、非闲聊误判；
- incomplete：其余（执行失败 / 数据缺口 / 人工兜底 / 空报告 / 步数用尽）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COLLEAGUE_YAML = ROOT / "src/mfg_agents/v2/data/colleague.yaml"
DEMO_DATA = ROOT / "src/mfg_agents/v2/demo/ui/demo-data.js"
OUT = ROOT / "logs/role_qa_preflight.json"


def load_roles() -> dict[str, dict]:
    text = DEMO_DATA.read_text(encoding="utf-8")
    roles = {}
    for m in re.finditer(
        r'\{ id: "([^"]+)",\s*name: "([^"]+)",\s*abbr: "([^"]*)",\s*uid: "([^"]*)",\s*'
        r'user: "([^"]*)",\s*dept: "([^"]+)",\s*roles: \[([^\]]*)\]',
        text,
    ):
        roles[m.group(1)] = {
            "name": m.group(2),
            "user": m.group(5),
            "dept": m.group(6),
            "roles": [r.strip().strip('"') for r in m.group(7).split(",") if r.strip()],
        }
    return roles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", default="", help="逗号分隔岗位 id，默认全部")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    from mfg_agents.v2.core.orchestrator import create_default_orchestrator

    orch = create_default_orchestrator()
    colleagues = yaml.safe_load(COLLEAGUE_YAML.read_text(encoding="utf-8"))["colleagues"]
    roles = load_roles()
    only = {r.strip() for r in args.roles.split(",") if r.strip()}
    tasks = []
    for c in colleagues:
        rid = c["role"]
        if only and rid not in only:
            continue
        meta = roles[rid]
        for q in c.get("questions") or []:
            tasks.append((rid, meta, q))
    print(f"岗位 {len({t[0] for t in tasks})} 个，问题 {len(tasks)} 条")
    sys.stdout.flush()

    def ask(rid: str, meta: dict, question: str) -> dict:
        uc = {"user_id": f"u-{rid}", "name": meta.get("user", ""),
              "dept": meta.get("dept", ""), "roles": meta.get("roles", []),
              "colleague_role": rid}
        t0 = time.time()
        try:
            d = orch.handle(question, uc).to_dict()
        except Exception as exc:  # noqa: BLE001 - 诊断脚本：异常即不可答
            return {"role": rid, "question": question, "status": "incomplete",
                    "error": str(exc)[:300], "elapsed_s": round(time.time() - t0, 1)}
        report = str(d.get("report") or "").strip()
        strategy = (d.get("route") or {}).get("strategy")
        ok = (bool(report) and not d.get("degraded")
              and d.get("fallback") != "manual_ticket"
              and "已在最大步数内" not in report
              and strategy != "chitchat")
        return {
            "role": rid, "role_name": meta.get("name", ""), "question": question,
            "status": "ok" if ok else "incomplete",
            "strategy": strategy,
            "case_id": (d.get("route") or {}).get("case_id"),
            "scenario_key": d.get("scenario_key"),
            "fallback": d.get("fallback"),
            "degraded": bool(d.get("degraded")),
            "degraded_reasons": d.get("degraded_reasons") or [],
            "report": report[:800],
            "elapsed_s": round(time.time() - t0, 1),
        }

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(ask, rid, meta, q): (rid, q) for rid, meta, q in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            tag = r["status"]
            if r.get("case_id"):
                tag += "(case)"
            print(f"[{i}/{len(tasks)}] {r['role']:6s} {tag:12s} "
                  f"{r['elapsed_s']:6.1f}s  {r['question'][:26]}  "
                  f"{(r.get('error') or (' / '.join(r.get('degraded_reasons') or [])))[:60]}")
            sys.stdout.flush()

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n可答 {ok}/{len(results)}，结果已写 {OUT}")


if __name__ == "__main__":
    main()
