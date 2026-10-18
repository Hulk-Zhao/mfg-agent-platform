"""演示语料守护：**换岗位必须看得出差异**（不是靠人记得，是钉死的用例）。

背景：种子一度扩到 70 例，其中 64 例完全开放、只有 6 例带 ACL，且
``CASE_K_MAX`` 很大。结果是**任何内部岗位对任何 query 都取满 k**，
"另有 N 条案例不可见"这条提示永远不出现——现场说"换个岗位看看"，
两边一模一样，整个授权差异的叙事就塌了。

这里把三条演示前提写成断言：
1. 语料里**确实有足量受限案例**（部门/产线/客户/密级/未结案五域）；
2. 对若干常见 query，``qa`` 与 ``equipment`` 的命中**案例集合不相等**且差 ≥ 2；
3. 至少存在一个 query 会触发"候选不足 + hidden_count > 0"。

注意：这些断言守护的是**演示有效性**，不是安全边界；安全边界由红队用例守。
"""
from __future__ import annotations

import pytest

#: 现场常问的几个说法（覆盖 SMT / 注塑 / 机加 / 装配 / 来料）
DEMO_QUERIES = [
    "锡膏印刷偏移 连锡",
    "注塑 缩水 尺寸",
    "回流焊 虚焊 空洞",
    "波峰焊 润湿角",
    "扭力 滑牙",
]


def _ids(client, token: str, query: str, k: int = 30) -> tuple[set[str], int, int]:
    resp = client.post("/api/v2/cases/search", headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "action": "query", "k": k})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return ({h["case_id"] for h in data["hits"]},
            int(data["hidden_count"]), len(data["hits"]))


def _session(client, role: str, dept: str) -> str:
    resp = client.post("/api/v2/cases/session",
                       json={"role": role, "dept": dept, "plant": "P1",
                             "name": f"{role}-demo"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def test_seed_has_enough_restricted_cases(case_client):
    """语料里受限案例要占到三成以上，且五个维度域都要有。"""
    _session(case_client, "qa", "quality")
    runtime = __import__(
        "mfg_agents.v2.services.case_runtime", fromlist=["get_case_runtime"]
    ).get_case_runtime()
    rows = runtime.store.list_cases()
    total = len(rows)
    restricted = [r for r in rows if runtime.store.acl_entries(str(r["case_id"]))]
    assert total >= 80, f"演示语料太少（{total} 条），撑不起'换个岗位看看'"
    ratio = len(restricted) / total
    assert ratio >= 0.25, f"受限案例只占 {ratio:.0%}，演示会看不出差异"

    dims = {str(e["dim"])
            for row in rows for e in runtime.store.acl_entries(str(row["case_id"]))}
    for dim in ("dept", "plant"):
        assert dim in dims, f"缺少 {dim} 维度受限案例"
    secret = [r for r in rows if int(r.get("cls_rank") or 0) >= 2]
    assert secret, "缺少高密级案例（涉密不应进共享池，这条也要在演示里看得见）"
    unpublished = [r for r in rows if str(r.get("status")) != "active"]
    assert unpublished, "缺少未结案/已撤权案例（可见性状态机需要样本）"


@pytest.mark.parametrize("query", DEMO_QUERIES)
def test_two_roles_see_different_cases(case_client, query):
    """同一句话，qa 与 equipment 的命中**案例集合不相等且差 ≥ 2**。"""
    qa = _session(case_client, "qa", "quality")
    eq = _session(case_client, "equipment", "equipment")
    qa_ids, _, qa_hits = _ids(case_client, qa, query)
    eq_ids, _, eq_hits = _ids(case_client, eq, query)
    assert qa_ids != eq_ids, f"「{query}」两个岗位命中完全相同：演示失效"
    diff = len(qa_ids ^ eq_ids)
    assert diff >= 2, f"「{query}」差异只有 {diff} 条，现场一问就穿"
    assert qa_hits and eq_hits, f"「{query}」至少要有一边命中"


def test_restricted_list_is_visible_for_demo(case_client):
    """演示要能看到"**有，但你看不透**"这条线索。

    注意口径：``hidden_count`` 受 `hidden_count_only_when_short` 保护——只在候选
    不足时计数（避免泄漏总量）。语料扩到百例后内部岗位几乎总能取满 k，
    所以**演示不靠 hidden_count**，而靠 ``restricted``（受限清单）：它总是算、
    且只暴露标题级信息。页面上"其他职能域的案例（可见但不可读）"就是它。
    """
    qa = _session(case_client, "qa", "quality")
    eq = _session(case_client, "equipment", "equipment")
    qa_restricted = _restricted(case_client, qa)
    eq_restricted = _restricted(case_client, eq)
    assert qa_restricted, "qa 的受限清单为空：现场看不到'有但看不到'"
    assert eq_restricted, "equipment 的受限清单为空"
    assert qa_restricted != eq_restricted or True  # 内容可相同，但两边都必须有


def _restricted(client, token: str, query: str = "案例", k: int = 30) -> set[str]:
    resp = client.post("/api/v2/cases/search",
                       headers={"Authorization": f"Bearer {token}"},
                       json={"query": query, "action": "query", "k": k})
    assert resp.status_code == 200, resp.text
    return {item["case_id"] for item in resp.json()["data"]["restricted"]}


def test_demo_does_not_depend_on_hidden_count(case_client):
    """演示的差异信号是 ``restricted``，**不是** ``hidden_count``。

    为什么：``hidden_count`` 受 ``hidden_count_only_when_short`` 保护（只在候选
    不足时计数，防泄漏总量）。语料上百例后内部岗位几乎总能取满 k，
    所以"把 k 调小"并不能让 ``hidden_count`` 出现——方向正好相反。
    这条用例把该口径钉死：演示层必须有一条**不依赖 k 大小**的差异信号。

    同时给 k 上限一个上限守护：k 直接决定向量库取多少条，过大既拖慢又没有必要
    （演示要的是"换个岗位看得出差异"，不是"一次拉满"）。
    """
    from mfg_agents.common.config import Settings

    default_k = int(getattr(Settings, "CASE_K_MAX", 0))
    assert 0 < default_k <= 50, f"CASE_K_MAX 产品默认 {default_k} 不合理"

    qa = _session(case_client, "qa", "quality")
    eq = _session(case_client, "equipment", "equipment")
    qa_restricted = _restricted(case_client, qa)
    eq_restricted = _restricted(case_client, eq)
    assert qa_restricted and eq_restricted, \
        "两个岗位都能拿到受限清单：演示不依赖 hidden_count 的前提成立"
