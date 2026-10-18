"""案例检索端到端单测：不同岗位 → 不同结果集，且越权一律查不到。

对照 PRD §7（可见性的三个层次）、架构 §7.5（16 步执行顺序）、§7.6（候选不足处理）。

本文件守护三件事：

1. **差异化**：同一 query，质量/工艺/设备/总经理/外包工拿到**不同**案例集，
   且差异符合"质量案例全员共享 + 岗位经验按职能域 + 部门/工厂/密级/状态各自否决"；
2. **越权为零**：未结案（review）、机密（cls_rank=3）、跨部门、跨工厂案例
   对无权主体**一条都查不到**，且 ``hidden_count >= 0``；
3. **没有放宽路径**：候选不足时不补齐、不去掉 filter；二次授权能拦住伪造的索引元数据。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.contracts.case_acl import Principal
from mfg_agents.v2.contracts.case_index import KnowledgeChunk
from mfg_agents.v2.services.case_ingest import SourceTicketRequired
from mfg_agents.v2.services.case_runtime import CaseRuntime

# 全部 15 条种子案例（用于断言"谁看不到什么"）
CASE_QUALITY_OPEN = {
    "QC-CC-2026-0087",  # 客诉 · 印刷偏移
    "QC-PD-2026-0031",  # 制程 · 虚焊
    "QC-MD-2026-0014",  # 材料 · 焊盘氧化
    "QC-PD-2026-0042",  # 制程 · 连锡
    "QC-EF-2026-0007",  # 设备 · 抛料
    "QC-CC-2026-0102",  # 客诉 · 焊球
    "QC-CC-2026-0110",  # 客诉 · 客户/供应商（知识层脱敏）
    "QC-PD-2025-0008",  # 待复核（降权但可见）
}
CASE_QUALITY_DEPT = "QC-PD-2026-0055"   # 限 dept:quality
CASE_PLANT_P2 = "QC-MD-2026-0021"       # 限 plant:P2
CASE_ROLE_PROCESS = "RC-PRC-2026-0003"
CASE_ROLE_EQUIPMENT = "RC-EQP-2026-0005"
CASE_ROLE_QUALITY = "RC-QA-2026-0009"
CASE_REVIEW = "QC-CC-2026-0115"         # 未结案
CASE_SECRET = "QC-PD-2026-0099"         # 机密

QUERY = "锡膏印刷偏移 虚焊 连锡 抛料 焊球不良 缩水 尺寸超差 毛刺"


@pytest.fixture(scope="module")
def shared_runtime(tmp_path_factory) -> CaseRuntime:
    """模块级共享运行时（建一次 ≈ 6s，整份文件从 318s 降到 ~12s）。

    为什么拆：``CaseRuntime.build()`` 会把 99 例种子完整跑一遍（脱敏 + 切片 +
    哈希嵌入）。原先夹具是函数级，写用例每条都重建一遍，而 ``--durations`` 显示
    开销几乎全在 ``setup``（每条 ~11s），用例本身 < 1s。

    **写操作用例也共享**，代价是"写完必须自清理"（见 :func:`_restore_case`）。
    这个代价换来的是一个真实的失败模式：**将来若有写用例忘了清理，残留会毒化
    后续用例，表现为随机的 ACL 断言失败——那正是本项目反复出现过的"假红"。**

    所以残留检查放在**本夹具的 teardown**（``yield`` 之后）而不是一条普通用例：
    teardown 在模块内**所有**用例跑完之后才执行，与谁定义在前无关。
    早前把它写成普通用例（定义在文件开头），结果它校验的是"写用例还没跑过的
    干净状态"，**恒真**——忘了清理也不会报警。
    """
    runtime = CaseRuntime.build(
        db_path=str(tmp_path_factory.mktemp("case_e2e_shared") / "case_e2e.db"))
    yield runtime
    # ⚠️ B15：这里的断言失败在 pytest 里报的是 **ERROR 而不是 FAILED**
    # （QA 做过故障注入：故意留一条不清理的写用例 → `30 passed, 1 error`）。
    # 退出码仍是 1，直接跑 pytest 没问题；但**看板 / 汇总脚本若按 `failed`
    # 关键字过滤，残留守卫抓到的回归会被静默漏掉**。CI 一律以退出码为准。
    #
    # teardown 只**报错**不修复：修复会掩盖"是谁没清理"。
    residue = sorted(_seed_case_ids() ^ {str(r["case_id"])
                                         for r in runtime.store.list_cases()})
    assert not residue, (
        f"模块收尾：库里的案例集合与种子不一致，多出/缺少 {residue}。"
        "写用例必须调用 _restore_case(rt, case_id) 还原自己造成的状态")


def _seed_case_ids() -> set[str]:
    """种子文件里的 case_id 集合（残留守卫的期望值）。"""
    import json
    from pathlib import Path

    seed = Path(__file__).resolve().parents[2] / "data" / "case_seed.json"
    payload = json.loads(seed.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return {str(c.get("case_id")) for c in (cases or ())}


def _resync_case(runtime: CaseRuntime, case_id: str) -> None:
    """**只重算索引**，不动案例本身。

    用于"案例本来就存在、但索引被本用例改动过"的场景（例如伪造索引元数据）。
    ⚠️ 绝不能顺手删案例主行——那条案例是种子里的，删了会被残留守卫报成"缺少"。
    （残留守卫第二次运行时正好抓到了这个错误用法。）
    """
    runtime.index_service.sync_case(str(case_id))


def _restore_case(runtime: CaseRuntime, case_id: str) -> None:
    """把**本用例新建的**案例彻底移除：关联行 + 主表行 + 索引。

    共享模块级运行时后，所有写用例都必须调用它，否则残留会毒化后续用例。
    抽成命名 helper 而不是三处各写一遍——重复的清理逻辑迟早会漏改一处。
    已有案例只索引被改的，用 :func:`_resync_case`，**别用这个**。
    """
    target = str(case_id)
    runtime.store._purge(target)              # noqa: SLF001 - 清 ACL/chunk/证据行
    # ⚠️ `_purge` **不清 `case` 主表行**（它设计给"重放同一条种子"用），
    # 所以自清理必须自己删主行——否则案例壳子留着，残留守卫会报出来
    # （这条正是残留守卫第一次运行就抓到的东西）。
    with runtime.store._conn() as conn:       # noqa: SLF001 - 测试自清理
        conn.execute('DELETE FROM "case" WHERE case_id=?', (target,))
    runtime.index_service.sync_case(target)


def principal(rt: CaseRuntime, roles, dept, *, plant="P1",
              employment_type="employee", max_cls=1, user_id="u-demo") -> Principal:
    payload = rt.resolver.build_payload(
        user_id=user_id, roles=roles, dept=dept, plant=plant, tenant="T001",
        max_classification_rank=max_cls, employment_type=employment_type)
    return rt.resolver.from_payload(payload, verify_pv=False)


#: 种子案例已扩充到 99 条：本文件的断言是「谁能看到什么」（ACL 语义），
#: 与 top-k 截断无关，因此用足够大的 k 取回全部可见案例，避免排序把
#: 精选案例挤出结果集而误报。k 不再等于默认上限 20。
_K_ALL = 200


def search_ids(rt: CaseRuntime, person: Principal, k: int = _K_ALL):
    response = rt.search.search(person, QUERY, action="query", k=k)
    return {hit["case_id"] for hit in response.hits}, response


# ---------------------------------------------------------------- 差异化

def test_quality_engineer_result_set(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("pqe",), "quality")
    ids, response = search_ids(shared_runtime, person)

    assert CASE_QUALITY_OPEN <= ids, "质量案例（知识层）应对全员开放"
    assert CASE_QUALITY_DEPT in ids, "质量部应能看到限本部门的制程案例"
    assert CASE_ROLE_QUALITY in ids, "质量岗位经验应对质量部开放"
    assert CASE_ROLE_PROCESS not in ids
    assert CASE_ROLE_EQUIPMENT not in ids
    assert CASE_PLANT_P2 not in ids, "P1 工厂用户不得看到限 P2 的案例"
    assert CASE_REVIEW not in ids, "未结案案例永不进检索（BR-06）"
    assert CASE_SECRET not in ids, "机密案例对 max_cls=1 不可见（E1）"
    assert response.hidden_count >= 0


def test_process_engineer_result_set(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("product_engineering",), "rd_engineering")
    ids, _ = search_ids(shared_runtime, person)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_ROLE_PROCESS in ids, "工艺岗位经验应只对工艺职能域开放"
    assert CASE_ROLE_EQUIPMENT not in ids
    assert CASE_ROLE_QUALITY not in ids
    assert CASE_QUALITY_DEPT not in ids, "非质量部看不到限质量部的案例"
    assert CASE_SECRET not in ids and CASE_REVIEW not in ids


def test_equipment_engineer_result_set(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("equipment",), "equipment")
    ids, response = search_ids(shared_runtime, person)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_ROLE_EQUIPMENT in ids
    assert CASE_ROLE_PROCESS not in ids
    assert CASE_ROLE_QUALITY not in ids
    assert CASE_QUALITY_DEPT not in ids
    assert response.hidden_count >= 0


def test_gm_sees_all_except_hard_gates(shared_runtime: CaseRuntime) -> None:
    """〔口径更新 2026-10-19〕总经理跨部门/跨厂通行（case_policy ``cross_dept_roles``）。

    旧口径「部门/工厂维度照常否决」已被产品裁定（"总经理应该权限都有"）推翻；
    现在 gm 的结果集只被**硬门禁**收窄：涉密（显式 DENY，E1）、未结案（BR-06）。
    完整的不变量矩阵见 ``test_case_cross_dept.py``。
    """
    person = principal(shared_runtime, ("gm",), "management", max_cls=3)
    ids, _ = search_ids(shared_runtime, person)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_SECRET not in ids, "secret 由显式 DENY 规则否决，不受 max_cls / 豁免影响"
    assert CASE_QUALITY_DEPT in ids, "总经理跨部门通行"
    assert CASE_ROLE_PROCESS in ids, "总经理跨部门通行（岗位经验域）"
    assert CASE_ROLE_EQUIPMENT in ids, "总经理跨部门通行（岗位经验域）"


def test_outsource_sees_only_open_knowledge(shared_runtime: CaseRuntime) -> None:
    """外包工（同部门、同工厂、同角色）只拿 vc=0 的全员开放知识层。"""
    outsource = principal(shared_runtime, ("equipment",), "equipment",
                          employment_type="outsource", user_id="u-outsource")
    ids, response = search_ids(shared_runtime, outsource)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_ROLE_EQUIPMENT not in ids, "非正式员工不享受岗位经验案例"
    assert CASE_QUALITY_DEPT not in ids
    assert response.hidden_count >= 0


def test_result_sets_differ_across_principals(shared_runtime: CaseRuntime) -> None:
    """同一 query，五个主体的结果集必须有差异（否则鉴权没生效）。"""
    people = {
        "quality": principal(shared_runtime, ("pqe",), "quality", user_id="u1"),
        "process": principal(shared_runtime, ("product_engineering",), "rd_engineering",
                             user_id="u2"),
        "equipment": principal(shared_runtime, ("equipment",), "equipment", user_id="u3"),
        "gm": principal(shared_runtime, ("gm",), "management", max_cls=3, user_id="u4"),
        "outsource": principal(shared_runtime, ("equipment",), "equipment",
                               employment_type="outsource", user_id="u5"),
    }
    sets = {name: search_ids(shared_runtime, person)[0] for name, person in people.items()}

    # 这里断言的是**不变量**而不是逐条清单：演示语料会随需要扩充，
    # 硬编码"哪几条差"必然漂移；真正要守住的是"差异存在、且双向存在"。
    assert sets["quality"] != sets["equipment"]
    assert sets["quality"] != sets["process"]
    assert sets["process"] != sets["equipment"]
    assert sets["quality"] - sets["equipment"], "质量岗应看到设备岗看不到的案例"
    assert sets["equipment"] - sets["quality"], "设备岗应看到质量岗看不到的案例"
    assert sets["equipment"] - sets["process"], "设备岗应看到工艺岗看不到的案例"
    assert sets["process"] - sets["equipment"], "工艺岗应看到设备岗看不到的案例"
    # 岗位经验域的三条基准案例仍按归属部门可见（语料扩充不应破坏老口径）
    assert CASE_QUALITY_DEPT in sets["quality"]
    assert CASE_QUALITY_DEPT not in sets["equipment"]
    assert CASE_ROLE_EQUIPMENT in sets["equipment"]
    assert CASE_ROLE_EQUIPMENT not in sets["process"]
    # 外包工的结果集是所有"全员开放"案例的子集
    assert sets["outsource"] <= sets["equipment"]


# ---------------------------------------------------------------- 越权为零

@pytest.mark.parametrize("roles,dept,plant,max_cls", [
    (("pqe",), "quality", "P1", 1),
    (("equipment",), "equipment", "P1", 1),
    (("product_engineering",), "rd_engineering", "P1", 1),
    (("gm",), "management", "P1", 3),
    (("quality_director",), "quality", "P1", 3),
    (("maintenance_tech",), "equipment", "P1", 0),
    (("customer_service",), "service", "P1", 1),
    (("admin_hr",), "admin", "P1", 1),
])
def test_forbidden_cases_never_returned(shared_runtime: CaseRuntime, roles, dept,
                                        plant, max_cls) -> None:
    person = principal(shared_runtime, roles, dept, plant=plant, max_cls=max_cls)
    ids, response = search_ids(shared_runtime, person)
    assert CASE_REVIEW not in ids
    assert CASE_SECRET not in ids
    assert response.hidden_count >= 0


def test_cross_plant_denied(shared_runtime: CaseRuntime) -> None:
    p1 = principal(shared_runtime, ("sqe",), "quality", plant="P1")
    p2 = principal(shared_runtime, ("sqe",), "quality", plant="P2")
    p1_ids, _ = search_ids(shared_runtime, p1)
    p2_ids, _ = search_ids(shared_runtime, p2)
    assert CASE_PLANT_P2 not in p1_ids
    assert CASE_PLANT_P2 in p2_ids


def test_missing_plant_dimension_is_fail_closed(shared_runtime: CaseRuntime) -> None:
    """缺 plant 维度按"空 = 无授权"：拿不到任何**依赖工厂维**的案例。"""
    person = principal(shared_runtime, ("sqe",), "quality", plant="")
    ids, _ = search_ids(shared_runtime, person)
    assert CASE_PLANT_P2 not in ids, "缺 plant 令牌 → 依赖 plant 的案例不可见"
    # 部门维仍有值，因此限部门的案例照常可见（各维度独立判定，互不代替）
    assert CASE_QUALITY_DEPT in ids


# ---------------------------------------------------------------- 动作门禁

def test_export_and_share_denied_at_gate(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("gm",), "management", max_cls=3)
    for action in ("export", "share"):
        response = shared_runtime.search.search(person, QUERY, action=action, k=5)
        assert response.hits == []
        assert response.denied_code == "CASE_ACTION_DENIED"


def test_rbac_denied_role_gets_no_hits(shared_runtime: CaseRuntime) -> None:
    """``admin_hr`` 无 ``case.cite``：请求 cite 一律空结果 + 拒绝码。"""
    person = principal(shared_runtime, ("admin_hr",), "admin")
    response = shared_runtime.search.search(person, QUERY, action="cite", k=5)
    assert response.hits == []
    assert response.denied_code == "CASE_ACTION_DENIED"


# ---------------------------------------------------------------- 无放宽路径

def test_short_candidates_never_padded(shared_runtime: CaseRuntime) -> None:
    """候选不足时如实返回，**不补齐**、不放宽 filter。"""
    person = principal(shared_runtime, ("equipment",), "equipment")
    response = shared_runtime.search.search(person, "锡膏印刷偏移 虚焊 连锡 抛料 焊球不良 缩水 尺寸超差", k=_K_ALL)
    assert len(response.hits) < _K_ALL
    assert "少于" in response.notice
    assert response.hidden_count >= 0


def test_zero_hits_returns_honest_empty(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("equipment",), "equipment")
    response = shared_runtime.search.search(person, "完全不存在的关键词串 zzzqqq", k=5)
    # 排序仍然可能返回低分命中，但绝不能编造；若返回空必须给出提示
    if not response.hits:
        assert response.notice
        assert response.degraded is False


def test_second_stage_blocks_forged_index_metadata(shared_runtime: CaseRuntime) -> None:
    """伪造索引元数据（把受限案例标成 vc=0）仍会被二次授权拦住。

    这是 BR-01 后半的核心用例：即使 filter 翻译错误或索引被污染，
    关系库权威 ACL 的第二次判定不会漏放。
    """
    forged = KnowledgeChunk(
        case_id=CASE_QUALITY_DEPT,          # 真实 ACL 为 dept:quality
        chunk_type="summary",
        content_version=1,
        text="伪造的索引元数据：把受限案例标为全员可见",
        metadata={
            "tenant": "T001", "category": "quality_case",
            "status": "active", "cls_rank": 0, "visibility_class": 0,
            "is_knowledge_layer": True, "acl_tokens": [],
            "owner_dept": "quality", "plant": "P1",
            "defect_mode_code": "DM-ASM-051", "root_cause_code": "RC-GRP-PARAM",
            "verified": True, "last_verified_at": "2026-09-01",
            "title": "伪造条目",
        },
    )
    runtime = shared_runtime
    runtime.index_service.index.upsert_chunks([forged])
    try:
        person = principal(runtime, ("equipment",), "equipment")
        response = runtime.search.search(person, "缩水 注塑", k=_K_ALL)
        ids = {hit["case_id"] for hit in response.hits}
        assert CASE_QUALITY_DEPT not in ids, "二次授权必须拦住伪造的可见性元数据"
        assert response.second_stage_dropped >= 1
    finally:
        # 伪造的 chunk 被覆盖回真实内容（该案例本就存在，只重算索引、不删案例）
        _resync_case(runtime, CASE_QUALITY_DEPT)


def test_hidden_count_never_negative(shared_runtime: CaseRuntime) -> None:
    for roles, dept in [(("pqe",), "quality"), (("gm",), "management")]:
        person = principal(shared_runtime, roles, dept)
        response = shared_runtime.search.search(person, "虚焊", k=5)
        assert response.hidden_count >= 0


# ---------------------------------------------------------------- 单资源读取 / 引用

def test_read_case_denied_returns_none(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("equipment",), "equipment")
    assert shared_runtime.search.read_case(person, CASE_QUALITY_DEPT) is None
    assert shared_runtime.search.read_case(person, CASE_REVIEW) is None
    assert shared_runtime.search.read_case(person, CASE_SECRET) is None
    assert shared_runtime.search.read_case(person, CASE_ROLE_EQUIPMENT) is not None


def test_read_case_returns_knowledge_layer_only(shared_runtime: CaseRuntime) -> None:
    """含客户/供应商的案例：知识层是脱敏文本，证据层只给"存在"标记。"""
    person = principal(shared_runtime, ("pqe",), "quality")
    case = shared_runtime.search.read_case(person, "QC-CC-2026-0110")
    assert case is not None
    assert case["layer"] == "knowledge"
    assert case["evidence_available"] is True
    blob = " ".join(str(case[key]) for key in
                    ("title", "summary", "phenomenon", "root_cause_text",
                     "countermeasure_text"))
    assert "客户甲" in blob or "供应商甲" in blob
    # 证据层内容/链接不得出现在详情里
    assert "minio://" not in blob
    assert "uri" not in case


# ---------------------------------------------------------------- 问答引用

def test_answer_every_claim_has_citation(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("pqe",), "quality")
    result = shared_runtime.qa.answer(person, "锡膏印刷偏移怎么处理", k=3)
    assert result.answered is True
    assert result.claims and result.citations
    for claim in result.claims:
        assert claim["citations"], "无引用的断言必须被丢弃"
        citation = claim["citations"][0]
        assert citation["case_id"] and citation["chunk_id"]
        assert int(citation["content_version"]) >= 1


def test_answer_refuses_when_no_permission(shared_runtime: CaseRuntime) -> None:
    """外包工问岗位经验 → 命中不足 → 拒答而不是编造。"""
    outsource = principal(shared_runtime, ("equipment",), "equipment",
                          employment_type="outsource", user_id="u-out")
    result = shared_runtime.qa.answer(outsource, "工艺调机参数窗口怎么定 zzz", k=3)
    if not result.answered:
        assert result.reason
    else:
        # 若仍有开放案例命中，则必须是带引用的知识层内容
        assert all(claim["citations"] for claim in result.claims)


# ---------------------------------------------------------------- 入库管道

def test_ingest_requires_source_ticket(shared_runtime: CaseRuntime) -> None:
    """无来源单据必须被拒（BR-11）。这条不写任何数据，可安全共享运行时。"""
    with pytest.raises(SourceTicketRequired):
        shared_runtime.ingest.ingest({"title": "无来源草稿", "phenomenon": "测试"})
    assert shared_runtime.store.get_case("无来源草稿") is None


def test_ingest_redacts_and_stays_in_review(shared_runtime: CaseRuntime) -> None:
    """含客户名/人名的草稿：入库前固化脱敏，落 review 且**不可检索**。"""
    draft = {
        "case_id": "QC-PD-2026-9901",
        "source_ticket_type": "process",
        "source_ticket_id": "PD-2026-9901",
        "title": "华辰电子 反馈按键卡滞（责任人王建国）",
        "summary": "客户华辰电子反馈按键卡滞，供应商恒信精工 批次 2026-07 有问题",
        "phenomenon": "按键卡滞，操作员王建国发现，联系电话 13800138000",
        "root_cause_text": "供应商来料尺寸偏大",
        "countermeasure_text": "供应商 8D 并加严来料检验",
        "root_cause": "供应商来料不良",
        "defect_mode": "尺寸超差",
        "owner_dept": "quality",
        "plant": "P1",
        "cls_rank": 1,
        "acl": [{"dim": "dept", "value": "quality", "effect": "allow"}],
    }
    runtime = shared_runtime
    case_id = str(draft["case_id"])
    try:
        result = runtime.ingest.ingest(draft)
        assert result.status == "review"
        case = runtime.store.get_case(case_id)
        assert case is not None
        blob = " ".join(str(case[key]) for key in
                        ("title", "summary", "phenomenon"))
        # 原始敏感值不得残留
        for raw in ("华辰电子", "恒信精工", "王建国", "13800138000"):
            assert raw not in blob, f"入库后仍残留原始敏感值: {raw}"
        # review 状态不可被检索
        person = principal(runtime, ("pqe",), "quality")
        ids, _ = search_ids(runtime, person)
        assert case_id not in ids
    finally:
        _restore_case(runtime, case_id)


def test_audit_chain_is_valid_and_has_no_plaintext(shared_runtime: CaseRuntime) -> None:
    person = principal(shared_runtime, ("pqe",), "quality")
    shared_runtime.search.search(person, "虚焊", k=3)
    chain = shared_runtime.audit.verify_chain()
    assert chain["valid"] is True
    recent = shared_runtime.audit.recent(limit=5)
    blob = str(recent)
    assert "虚焊" not in blob, "审计不得记录查询明文"
    assert "dept:quality" not in blob, "审计不得记录 filter 明文"
