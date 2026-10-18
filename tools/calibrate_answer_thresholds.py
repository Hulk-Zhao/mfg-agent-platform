"""标定问答拒答阈值（`answer_min_score` / `answer_margin`）。

为什么需要这个脚本
------------------
`low_confidence_threshold` 是**绝对阈值**，而 demo 后端的检索是
blake2b 1–2gram 字符重合度（0.6 余弦 + 0.4 关键词）——**分数分布会随语料规模
一起漂移**。15 例语料时 0.18 是安全线；扩到 ~100 例后，无关问题也能摸到 0.20，
于是"食堂几点开饭"这种问题会被煞有介事地答一遍（引用合法、内容无关）。
闸门没坏，是**标定失效**。

本脚本做什么
------------
用**当前语料**跑两组真实问句，各自统计 top1 分数分布：
- **无关组**（食堂 / 天气 / 股市 / 闲聊…）：`answer_min_score` 取其 **95 分位**
  ——宁可略高，**拒答优先于误答**；
- **相关组**（锡膏印刷偏移 / 连锡 / 虚焊…）：可接受的最低相关分取其 **5 分位**；
- 两个阈值之间必须留出**可观的间隔**（≥ `MIN_GAP`，默认 0.05），否则就是过拟合到
  当前语料，脚本会直接报失败而不是给出一个"刚好能用"的值。

⚠️ **接真实 embedding（BGE-M3 等）后分数分布完全不同，两个阈值必须重跑本脚本
重新标定**。demo 级字符重合度的数字不能带到生产。

用法：
    .venv/Scripts/python.exe tools/calibrate_answer_thresholds.py            # 打印分布与建议
    .venv/Scripts/python.exe tools/calibrate_answer_thresholds.py --json     # 机器可读
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import tempfile
from typing import Any, Optional

#: 无关问句（现场真的会问的"外行问题"，必须全部拒答）
IRRELEVANT: tuple[str, ...] = (
    "食堂几点开饭", "今天午饭吃什么", "明天股市会涨吗", "现在外面下雨吗",
    "周末去哪儿玩比较好", "帮我写一首诗", "最近的油价是多少",
    "孩子放学几点回家", "明天要带伞吗", "附近有什么好吃的",
    "今晚的球赛几点开始", "今年的春节是哪一天",
)

#: 相关问句（制造域真实提问，必须全部作答）
RELEVANT: tuple[str, ...] = (
    "锡膏印刷偏移连锡怎么处理", "回流焊后焊点空洞率高怎么办",
    "波峰焊润湿角不足怎么改善", "注塑件缩水率波动怎么控制",
    "自动扭力枪滑牙是什么原因", "钢网开孔堵塞导致连锡",
    "虚焊和空焊有什么区别", "焊接后引脚连锡的根因",
    "锡膏黏度波动引发印刷拖影", "注塑工艺窗口怎么设定",
    "来料熔指波动怎么办", "螺钉滑牙的判定标准",
)

#: 两组分布之间必须留出的间隔（过拟合信号）
MIN_GAP = 0.05


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[idx]


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "min": round(min(values), 4) if values else 0.0,
        "p25": round(_percentile(values, 0.25), 4),
        "median": round(statistics.median(values), 4) if values else 0.0,
        "p75": round(_percentile(values, 0.75), 4),
        "max": round(max(values), 4) if values else 0.0,
        "p95": round(_percentile(values, 0.95), 4),
    }


def main(argv: list[str]) -> int:
    seed = pathlib.Path(__file__).resolve().parents[1] / "data" / "case_seed.json"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="calib-"))
    os.environ.setdefault("CASE_DB", str(tmp / "case.db"))
    os.environ["CASE_SEED_PATH"] = str(seed)
    os.environ.setdefault("CASE_VECTOR_BACKEND", "memory")
    os.environ.setdefault("CASE_DEMO_MODE", "true")
    os.environ.setdefault("CASE_K_MAX", "30")

    from mfg_agents.v2.contracts.case_acl import Principal
    from mfg_agents.v2.services.case_runtime import get_case_runtime, reset_case_runtime

    reset_case_runtime()
    runtime = get_case_runtime()
    # tenant 必须给：缺租户维度会被编译器 fail-closed（这也是一条真实不变式）
    principal = Principal(roles=("qa",), dept="quality", plant="P1",
                          user_id="u-calibration", tenant="T001")

    ontology = runtime.qa._ontology  # noqa: SLF001 - 标定脚本需要同一份词表

    def ontology_hits(text: str) -> int:
        """问题里命中的受控词条数（缺陷模式 / 根因 / 对策）。

        这是**语义闸门**：demo 级字符重合度无法把「食堂几点开饭」和
        「扭力滑牙」分开（前者 top1 反而更高），真正的判别信号是
        "这句话里有没有制造术语"。
        """
        n = 0
        for vocab in ("defect_mode", "root_cause", "countermeasure"):
            n += len(ontology.find_terms(vocab, text, limit=3))
        return n

    def probe(question: str) -> dict[str, Any]:
        res = runtime.qa.answer(principal, question, k=10, action="read")
        scores = sorted((float(h.get("score") or 0.0) for h in (res.search.hits or [])),
                        reverse=True)
        top1 = scores[0] if scores else 0.0
        median = statistics.median(scores) if scores else 0.0
        return {
            "question": question,
            "terms": ontology_hits(question),
            "top1": round(top1, 4),
            "median": round(median, 4),
            "margin": round(top1 - median, 4),
            "answered": bool(res.answered),
            "hits": len(scores),
        }

    irrelevant = [probe(q) for q in IRRELEVANT]
    relevant = [probe(q) for q in RELEVANT]

    irr_top1 = [row["top1"] for row in irrelevant]
    rel_top1 = [row["top1"] for row in relevant]
    rel_margin = [row["margin"] for row in relevant]
    irr_margin = [row["margin"] for row in irrelevant]

    # 建议值：无关组 p95 作绝对下限；相关组 p5 作"可接受的最低相关分"
    suggest_min = _percentile(irr_top1, 0.95)
    relevant_floor = _percentile(rel_top1, 0.05)
    suggest_margin = _percentile(rel_margin, 0.05)

    report = {
        "corpus": str(seed),
        "cases": len(runtime.store.list_cases()),
        "note": "demo 级字符重合度后端；接真实 embedding 后必须重跑本脚本",
        "irrelevant": {"rows": irrelevant, "top1": _stats(irr_top1),
                       "margin": _stats(irr_margin)},
        "relevant": {"rows": relevant, "top1": _stats(rel_top1),
                     "margin": _stats(rel_margin)},
        "suggest": {
            "answer_min_score": round(suggest_min, 4),
            "answer_margin": round(suggest_margin, 4),
            "relevant_floor_p5": round(relevant_floor, 4),
            "gap_between_groups": round(relevant_floor - suggest_min, 4),
            "gap_ok": (relevant_floor - suggest_min) >= MIN_GAP,
            "margin_gap_ok": (_percentile(rel_margin, 0.05)
                              - _percentile(irr_margin, 0.95)) >= 0.0,
        },
    }
    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("语料案例数:", report["cases"])
        for label, block in (("无关组", report["irrelevant"]),
                             ("相关组", report["relevant"])):
            print(f"\n=== {label} top1 分数分布 ===")
            print(json.dumps(block["top1"], ensure_ascii=False))
            print(f"=== {label} margin 分布 ===")
            print(json.dumps(block["margin"], ensure_ascii=False))
            for row in block["rows"]:
                print(f"  {row['answered'] and '作答' or '拒答'}  "
                      f"terms={row['terms']}  top1={row['top1']:<7} "
                      f"median={row['median']:<7} margin={row['margin']:<7} "
                      f"{row['question']}")
        print("\n=== 建议阈值 ===")
        print(json.dumps(report["suggest"], ensure_ascii=False, indent=2))
    return 0 if report["suggest"]["gap_ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
