"""POC 评测：案例检索的**质量指标**（K1 / K3 / K10 / MRR）+ 拒答闸门表现。

⚠️ **这是评测脚本，不是 PR 门禁**（`exit 0`，除非显式 `--strict`）。
它衡量的是"语料 × embedding"的函数结果——换 embedding、扩语料都会让数字漂，
所以**不该进单元测试**。单元测试守的是不变量（见
`tests/v2/test_case_answer_recall.py::test_gate_never_refuses_what_recall_found`：
"闸门只拦含糊，不拦召回不到"）。

四个指标
--------
| 指标 | 含义 | 为什么看它 |
| --- | --- | --- |
| **K1** | 目标案例排在第 1 的比例 | 演示现场最直观的"准不准" |
| **K3** | 目标案例进前 3 的比例 | 人工翻 2–3 条时能不能找到 |
| **K10** | 目标案例进前 10 的比例 | 上限召回 |
| **MRR** | 平均倒数排名（命中位次的倒数） | 单指标概括排序质量 |

另外输出**拒答闸门**表现：可回答率、每条的 `best / median / margin / top-1`。
这一列用来**重选单元测试的样本**：换 embedding 后从"top-1 稳定命中"的案例里挑
（不要随机抽，语料一换就变成随机红）。

标注集
------
默认用 `tests/v2/test_case_answer_recall.py::SAMPLE_CASES`（12 条固定样本，
query = 该案例 `phenomenon` 前 22 字）。也可以导入人工标注集：

```json
[{"query": "锡膏印刷偏移怎么处理", "expect_case_id": "QC-CC-2026-0087"},
 {"query": "焊接后焊点发黑", "expect_case_id": ["QC-PD-2025-0008"]}]
```

用法::

    python tools/poc_case_eval.py                     # 用内置样本
    python tools/poc_case_eval.py --labels my.json    # 用人工标注集
    python tools/poc_case_eval.py --json              # 机器可读
    python tools/poc_case_eval.py --strict            # 低于阈值退 1（夜间任务用）
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import tempfile
from typing import Any, Optional

#: 可接受阈值（--strict 时生效；默认值是 demo 后端实测值，不是行业标杆）。
THRESHOLDS = {"recall_rate": 0.80, "k1": 0.60, "k3": 0.80, "k10": 0.95,
               "answer_rate": 0.80}

QUERY_CHARS = 22

#: 实际检索深度（喂给 search / answer 的 k）。
#: ⚠️ 它与指标名里的 **K1 / K3 / K10 是两件事**：指标名是固定口径的排名刻度，
#: 检索深度只是"取多少条"。曾因为两者共用一个 `k` 参数，导致
#: ``evaluate(labels, k=5)`` 会把 **k10 静默变成 k5**——名字说是 A、算出是 B。
#: 所以这里分成两个名字：``RETRIEVE_K``（深度）与字面量 10（K10 口径）。
RETRIEVE_K = 10


def _default_labels() -> list[dict[str, Any]]:
    """从**单元测试文件** import ``SAMPLE_CASES``（不复制常量）。

    为什么必须 import 而不是各写一份：两份常量早晚会漂——测试改了样本、
    评测还在测旧样本，指标就变成"另一个集合的数字"，而且没人会发现。
    这里是 P1 那条教训（"挪走必须与落地同时做"）的延伸。
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    spec_path = root / "tests" / "v2" / "test_case_answer_recall.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("_case_answer_recall_spec", spec_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"找不到样本常量来源：{spec_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case_ids = list(getattr(module, "SAMPLE_CASES", ()))
    if not case_ids:
        raise SystemExit(f"{spec_path} 里没有 SAMPLE_CASES，评测无法进行")
    return [{"expect_case_id": cid} for cid in case_ids]


def _runtime():
    root = pathlib.Path(__file__).resolve().parents[1]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="poc-eval-"))
    os.environ.setdefault("CASE_DB", str(tmp / "case.db"))
    os.environ.setdefault("CASE_SEED_PATH", str(root / "data" / "case_seed.json"))
    os.environ.setdefault("CASE_VECTOR_BACKEND", "memory")
    os.environ.setdefault("CASE_DEMO_MODE", "true")
    from mfg_agents.v2.services.case_runtime import get_case_runtime, reset_case_runtime

    reset_case_runtime()
    return get_case_runtime()


def _principal(runtime):
    from mfg_agents.v2.contracts.case_acl import Principal

    return Principal(roles=("pqe",), dept="quality", plant="P1",
                      user_id="u-poc-eval", tenant="T001")


def evaluate(labels: list[dict[str, Any]],
              search_k: int = RETRIEVE_K) -> dict[str, Any]:
    """在给定标注集上跑检索与问答，返回质量指标（**不是 PR 门禁**）。

    ⚠️ ``k`` 只控制"取回多少条候选"，**不改变任何指标的名称与口径**：
    K1/K3/K10 是**固定的三个切点**，与 ``k`` 无关。早前 K10 直接写成
    ``r["rank"] <= k``，默认值恰好是 10 所以看不出问题，但一旦有人把 k 调成 5，
    指标会静默变成"K5"却仍叫 K10——指标名与口径脱钩比数字错更难发现。
    """
    runtime = _runtime()
    person = _principal(runtime)
    rows: list[dict[str, Any]] = []
    for item in labels:
        expects = item.get("expect_case_id")
        expects = [expects] if isinstance(expects, str) else list(expects or ())
        query = str(item.get("query") or "")
        if not query and expects:
            case = runtime.store.get_case(expects[0])
            query = str((case or {}).get("phenomenon") or "")[:QUERY_CHARS]
        if not query or not expects:
            rows.append({"query": query, "expect": expects, "rank": None,
                         "note": "标注不完整，已跳过"})
            continue
        resp = runtime.search.search(person, query, action="read", k=search_k)
        ids = [h["case_id"] for h in resp.hits]
        scores = [float(h.get("score") or 0.0) for h in resp.hits]
        rank = next((i + 1 for i, cid in enumerate(ids) if cid in expects), None)
        top1 = ids[0] if ids else ""
        asked = runtime.qa.answer(person, query, k=search_k, action="read")
        rows.append({
            "query": query[:28], "expect": expects, "rank": rank, "top1": top1,
            "hit_top1": bool(ids and ids[0] in expects),
            "best": round(max(scores), 4) if scores else 0.0,
            "median": round(statistics.median(scores), 4) if scores else 0.0,
            "margin": round((max(scores) - statistics.median(scores)), 4) if scores else 0.0,
            "answered": bool(asked.answered),
        })
    # ★ 分母纪律：召回失败必须算进分母，不能被"过滤掉"。
    #
    # 早前这里写的是 ``scored = [r for r in rows if r.get("rank")]``——而 rank 为
    # None 恰恰表示"目标案例**没被召回**"。于是 12 条样本里 4 条召回失败的被
    # 静默排除，k1/k3/k10/mrr 全部算出 **1.0**。
    #
    # 这个指标是**结构性说谎**：召回退化 → 更多样本 rank=None → 被排除得更多 →
    # 剩下的样本更"干净" → 指标反而**变好**。指标与质量反相关，比没有指标更糟。
    #
    # 正确口径：召回未命中 = 排名视为 +∞（k1/k3/k10 记未命中、MRR 记 0），
    # 分母是全部**有完整标注**的样本。
    scored = [r for r in rows if r.get("note") is None]
    n = len(scored)
    total = n

    def _rank(r: dict[str, Any]) -> Optional[int]:
        value = r.get("rank")
        return int(value) if value else None

    hit_rank = [_rank(r) for r in scored]
    recalled = [rk for rk in hit_rank if rk is not None]
    return {
        "samples": total,
        "scored": len(recalled),
        "missed": n - len(recalled),
        "search_k": int(search_k),
        "recall_rate": round(len(recalled) / n, 4) if n else 0.0,
        "k1": round(sum(1 for rk in hit_rank if rk == 1) / n, 4) if n else 0.0,
        "k3": round(sum(1 for rk in hit_rank if rk is not None and rk <= 3) / n, 4) if n else 0.0,
        # 字面量 10：K10 这个名字必须与口径绑定，不受实际检索深度影响。
        # 若检索深度 < 10，输出里的 "search_k" 会显式告诉读数的人这一点。
        "k10": round(sum(1 for rk in hit_rank
                         if rk is not None and rk <= 10) / n, 4) if n else 0.0,
        # 召回未命中的样本贡献 0，而不是被剔除
        "mrr": round(sum((1.0 / rk) if rk else 0.0 for rk in hit_rank) / n, 4) if n else 0.0,
        "answer_rate": round(sum(1 for r in rows if r.get("answered")) / total, 4) if total else 0.0,
        "rows": rows,
    }


def main(argv: list[str]) -> int:
    labels = _default_labels()
    if "--labels" in argv:
        path = pathlib.Path(argv[argv.index("--labels") + 1])
        labels = json.loads(path.read_text(encoding="utf-8"))

    report = evaluate(labels)
    metrics = {key: report[key] for key in
               ("recall_rate", "k1", "k3", "k10", "mrr", "answer_rate")}

    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        # 检索指标与作答率**用同一个分母**（全部有完整标注的样本），且召回未命中
        # 按未命中计入。早前这里写着"两个分母不同是设计正确"并只统计召回成功的
        # 样本，那条注释描述的是**已修正的错误口径**——别再照它改回去：
        # 排除召回失败样本会让指标与质量**反相关**（召回越差指标越好看）。
        print(f"样本 {report['samples']} 条"
              f"（检索指标分母=全部有标注样本 {report['samples']} ·"
              f"召回失败 {report['missed']} 条已按未命中计入 ·"
              f" 实际检索深度 search_k={report['search_k']}"
              f"（K10 按字面量 10 口径计算）· "
              f" 回答率分母=全部 {report['samples']}）")
        print(f"{'query':<30} {'rank':>5} {'best':>7} {'med':>7} {'mrg':>7}  作答")
        for row in report["rows"]:
            print(f"{row['query']:<30} {str(row.get('rank') or '-'):>5} "
                  f"{row.get('best', 0):>7} {row.get('median', 0):>7} "
                  f"{row.get('margin', 0):>7}  {'是' if row.get('answered') else '否'}")
        print("\n=== 质量指标 ===")
        for key, value in metrics.items():
            flag = "OK " if value >= THRESHOLDS.get(key, 0) else "低 "
            print(f"  {flag}{key:<12} {value}")
        print("\n提示：这是**质量评估**，不是 PR 门禁。换 embedding / 扩语料后跑一次看趋势；"
              "单元测试只守不变量（见 tests/v2/test_case_answer_recall.py）。")
        print("拒答闸门阈值（answer_min_score / answer_margin / "
              "answer_score_without_terms）的标定入口："
              "python tools/calibrate_answer_thresholds.py")

    if "--strict" in argv:
        bad = [k for k, v in metrics.items() if v < THRESHOLDS.get(k, 0)]
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
