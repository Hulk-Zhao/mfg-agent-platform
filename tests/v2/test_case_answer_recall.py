"""问答闸门 vs 召回质量：**归因守护**（QA 裁决③）。

背景
----
问答拒答闸门（C2）有三个条件：绝对下限、相对余量、术语闸门。它解决的是
"该拒的拒住"。但它可能**误伤**——把本来答得上的问题拒掉，而这在演示里比误答更难发现
（页面只显示"未检索到足够相关的案例"）。所以必须有另一条守护回答：
**每一次拒答，是不是都能由"召回没命中"解释？**

三条断言（缺一不可）
--------------------
1. **归因断言（本文件存在的全部理由）**：对每一条被拒的样本，断言**目标案例不在 top-1 命中里**
   —— 拒答**只能**由"召回没命中"解释，**不能**由"闸门拦下了本该答得上的"解释。
   区分这两种归因很重要：前者是检索后端的 A 类问题（已在 README 声明
   demo 级哈希 embedding 不能用来评价召回），后者是闸门配置错误（B 类，必须现在修）。
2. **样本显式化**：12 个 case_id 写死在本文件里（`SAMPLE_CASES`），**不从语料随机抽**。
   语料变动时**显式失败**提醒更新样本，而不是静默重抽——理由与我把
   `test_result_sets_differ_across_principals` 从"硬编码差集"改成"断言不变量"时一样：
   "差哪几条"在语料一换就会漂。

**归属声明**
本文件守的是**闸门契约（代码不变量）**：闸门拒答时，目标案例不得是 top-1 命中。
判据：换 embedding / 调 `k` / 换语料都不会让它变红；只有"闸门配置写错"才会红。
**可回答率是质量指标**，不进门禁，见 `tools/poc_case_eval.py`（手动/夜间运行，退出码 0）。

> 这条判据要能**反过来用**：以后新增守护用例，提交人要在 PR 里写一句
> 「本条属于**不变量** / **质量指标**，理由是 ___」。写不出来就说明没想清楚，
> 该进 POC 而不是进单测。

**本文件守的是"闸门契约"，不是召回质量。**
可回答率（≥ N/12）这类**质量指标已移到 `tools/poc_case_eval.py`**——
它是"语料 × embedding"的函数，换 embedding 就漂，不该当 PR 门禁。
本文件的归因断言对 embedding 变化**不敏感**：embedding 变好 → 拒答变少 → 不红；
变差 → 目标掉出 top-1 → 断言不触发 → 也不红。**唯一会红的是"闸门拒了、而正确案例恰好排第 1"**，
那在任何 embedding 下都是闸门配置错误。

⚠️ **更换 embedding（或换语料）后必须重选 `SAMPLE_CASES`**：
先跑 `python tools/poc_case_eval.py`，从"top-1 稳定命中"的案例里挑，
把 case_id 写回本文件常量；**不要从语料随机抽**（语料一换就变成随机红）。
"""
from __future__ import annotations

import pytest

#: 固定样本（cls_rank < 2、status=active、现象描述 ≥22 字）。
#: 语料变动导致某条样本不再适用时，本文件会**显式失败**提醒更新——这是有意的。
SAMPLE_CASES: tuple[str, ...] = (
    "QC-CC-2026-0087",
    "QC-PD-2026-0031",
    "QC-MD-2026-0014",
    "QC-PD-2026-0042",
    "QC-EF-2026-0007",
    "QC-CC-2026-0102",
    "QC-PD-2026-0055",
    "QC-MD-2026-0021",
    "RC-PRC-2026-0003",
    "RC-QA-2026-0009",
    "QC-CC-2026-0120",
    "QC-CC-2026-0121",
)

#: query 取案例 phenomenon 的前 N 个字（模拟"用户把现场描述贴进来"）。
QUERY_CHARS = 22


def _sessions(client):
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    runtime = get_case_runtime()
    token = client.post("/api/v2/cases/session", json={
        "role": "pqe", "dept": "quality", "plant": "P1", "name": "recall-probe",
    }).json()["data"]["token"]
    return runtime, {"Authorization": f"Bearer {token}"}


def _query_of(runtime, case_id: str) -> str:
    case = runtime.store.get_case(case_id)
    assert case is not None, (
        f"样本案例 {case_id} 不存在——语料已变动，请更新 SAMPLE_CASES")
    assert str(case.get("status") or "") == "active", (
        f"样本案例 {case_id} 状态是 {case.get('status')!r}，不再是 active，请更新 SAMPLE_CASES")
    assert int(case.get("cls_rank") or 0) < 2, (
        f"样本案例 {case_id} 密级变成了 L{case.get('cls_rank')}，请更新 SAMPLE_CASES")
    text = str(case.get("phenomenon") or "")
    assert len(text) >= QUERY_CHARS, f"样本案例 {case_id} 现象描述过短，无法构造 query"
    return text[:QUERY_CHARS]


@pytest.mark.parametrize("case_id", SAMPLE_CASES)
def test_gate_never_refuses_what_recall_found(case_client, case_id):
    """核心断言：拒答时，目标案例**必须不在命中里**。

    如果目标案例明明排进了 top-1 却被闸门拒了，那就是**闸门误拒**——
    这正是我们要防的回归（margin / 术语闸门收紧过头）。
    """
    runtime, headers = _sessions(case_client)
    query = _query_of(runtime, case_id)

    asked = case_client.post("/api/v2/cases/ask", headers=headers,
                             json={"question": query, "k": 10})
    assert asked.status_code == 200, asked.text
    data = asked.json()["data"]

    if data["answered"]:
        # 答了就要有合法引用（BR-05）
        assert data["citations"], f"{case_id} 作答但无引用"
        return

    # 拒答 → 归因必须是"召回没命中"
    searched = case_client.post("/api/v2/cases/search", headers=headers,
                                json={"query": query, "action": "read", "k": 10})
    hits = searched.json()["data"]["hits"]
    hit_ids = [h["case_id"] for h in hits]
    assert hit_ids, f"{case_id} 被拒且零命中：这条拒答无法归因，请查检索链路"
    assert hit_ids[0] != case_id, (
        f"闸门误拒：目标案例 {case_id} 明明是 top-1 命中却被拒答。"
        f"top5={hit_ids[:5]}；这是闸门配置问题，不是召回问题。")
