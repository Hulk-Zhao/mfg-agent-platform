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


@pytest.fixture()
def runtime(tmp_path) -> CaseRuntime:
    return CaseRuntime.build(db_path=str(tmp_path / "case_e2e.db"))


def principal(runtime: CaseRuntime, roles, dept, *, plant="P1",
              employment_type="employee", max_cls=1, user_id="u-demo") -> Principal:
    payload = runtime.resolver.build_payload(
        user_id=user_id, roles=roles, dept=dept, plant=plant, tenant="T001",
        max_classification_rank=max_cls, employment_type=employment_type)
    return runtime.resolver.from_payload(payload, verify_pv=False)


def search_ids(runtime: CaseRuntime, person: Principal, k: int = 20):
    response = runtime.search.search(person, QUERY, action="query", k=k)
    return {hit["case_id"] for hit in response.hits}, response


# ---------------------------------------------------------------- 差异化

def test_quality_engineer_result_set(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("pqe",), "quality")
    ids, response = search_ids(runtime, person)

    assert CASE_QUALITY_OPEN <= ids, "质量案例（知识层）应对全员开放"
    assert CASE_QUALITY_DEPT in ids, "质量部应能看到限本部门的制程案例"
    assert CASE_ROLE_QUALITY in ids, "质量岗位经验应对质量部开放"
    assert CASE_ROLE_PROCESS not in ids
    assert CASE_ROLE_EQUIPMENT not in ids
    assert CASE_PLANT_P2 not in ids, "P1 工厂用户不得看到限 P2 的案例"
    assert CASE_REVIEW not in ids, "未结案案例永不进检索（BR-06）"
    assert CASE_SECRET not in ids, "机密案例对 max_cls=1 不可见（E1）"
    assert response.hidden_count >= 0


def test_process_engineer_result_set(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("product_engineering",), "rd_engineering")
    ids, _ = search_ids(runtime, person)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_ROLE_PROCESS in ids, "工艺岗位经验应只对工艺职能域开放"
    assert CASE_ROLE_EQUIPMENT not in ids
    assert CASE_ROLE_QUALITY not in ids
    assert CASE_QUALITY_DEPT not in ids, "非质量部看不到限质量部的案例"
    assert CASE_SECRET not in ids and CASE_REVIEW not in ids


def test_equipment_engineer_result_set(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("equipment",), "equipment")
    ids, response = search_ids(runtime, person)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_ROLE_EQUIPMENT in ids
    assert CASE_ROLE_PROCESS not in ids
    assert CASE_ROLE_QUALITY not in ids
    assert CASE_QUALITY_DEPT not in ids
    assert response.hidden_count >= 0


def test_gm_result_set_is_cls_only(runtime: CaseRuntime) -> None:
    """总经理有最高密级上限，但**部门/工厂维度照常否决**（可申请临时提权）。"""
    person = principal(runtime, ("gm",), "management", max_cls=3)
    ids, _ = search_ids(runtime, person)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_SECRET not in ids, "secret 由显式 DENY 规则否决，不受 max_cls 影响"
    assert CASE_QUALITY_DEPT not in ids
    assert CASE_ROLE_PROCESS not in ids
    assert CASE_ROLE_EQUIPMENT not in ids


def test_outsource_sees_only_open_knowledge(runtime: CaseRuntime) -> None:
    """外包工（同部门、同工厂、同角色）只拿 vc=0 的全员开放知识层。"""
    outsource = principal(runtime, ("equipment",), "equipment",
                          employment_type="outsource", user_id="u-outsource")
    ids, response = search_ids(runtime, outsource)

    assert CASE_QUALITY_OPEN <= ids
    assert CASE_ROLE_EQUIPMENT not in ids, "非正式员工不享受岗位经验案例"
    assert CASE_QUALITY_DEPT not in ids
    assert response.hidden_count >= 0


def test_result_sets_differ_across_principals(runtime: CaseRuntime) -> None:
    """同一 query，五个主体的结果集必须有差异（否则鉴权没生效）。"""
    people = {
        "quality": principal(runtime, ("pqe",), "quality", user_id="u1"),
        "process": principal(runtime, ("product_engineering",), "rd_engineering",
                             user_id="u2"),
        "equipment": principal(runtime, ("equipment",), "equipment", user_id="u3"),
        "gm": principal(runtime, ("gm",), "management", max_cls=3, user_id="u4"),
        "outsource": principal(runtime, ("equipment",), "equipment",
                               employment_type="outsource", user_id="u5"),
    }
    sets = {name: search_ids(runtime, person)[0] for name, person in people.items()}

    assert sets["quality"] != sets["equipment"]
    assert sets["quality"] != sets["process"]
    assert sets["process"] != sets["equipment"]
    assert sets["quality"] - sets["equipment"] == {
        CASE_QUALITY_DEPT, CASE_ROLE_QUALITY}
    assert sets["equipment"] - sets["process"] == {CASE_ROLE_EQUIPMENT}
    assert sets["process"] - sets["equipment"] == {CASE_ROLE_PROCESS}
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
def test_forbidden_cases_never_returned(runtime: CaseRuntime, roles, dept,
                                        plant, max_cls) -> None:
    person = principal(runtime, roles, dept, plant=plant, max_cls=max_cls)
    ids, response = search_ids(runtime, person)
    assert CASE_REVIEW not in ids
    assert CASE_SECRET not in ids
    assert response.hidden_count >= 0


def test_cross_plant_denied(runtime: CaseRuntime) -> None:
    p1 = principal(runtime, ("sqe",), "quality", plant="P1")
    p2 = principal(runtime, ("sqe",), "quality", plant="P2")
    p1_ids, _ = search_ids(runtime, p1)
    p2_ids, _ = search_ids(runtime, p2)
    assert CASE_PLANT_P2 not in p1_ids
    assert CASE_PLANT_P2 in p2_ids


def test_missing_plant_dimension_is_fail_closed(runtime: CaseRuntime) -> None:
    """缺 plant 维度按"空 = 无授权"：拿不到任何**依赖工厂维**的案例。"""
    person = principal(runtime, ("sqe",), "quality", plant="")
    ids, _ = search_ids(runtime, person)
    assert CASE_PLANT_P2 not in ids, "缺 plant 令牌 → 依赖 plant 的案例不可见"
    # 部门维仍有值，因此限部门的案例照常可见（各维度独立判定，互不代替）
    assert CASE_QUALITY_DEPT in ids


# ---------------------------------------------------------------- 动作门禁

def test_export_and_share_denied_at_gate(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("gm",), "management", max_cls=3)
    for action in ("export", "share"):
        response = runtime.search.search(person, QUERY, action=action, k=5)
        assert response.hits == []
        assert response.denied_code == "CASE_ACTION_DENIED"


def test_rbac_denied_role_gets_no_hits(runtime: CaseRuntime) -> None:
    """``admin_hr`` 无 ``case.cite``：请求 cite 一律空结果 + 拒绝码。"""
    person = principal(runtime, ("admin_hr",), "admin")
    response = runtime.search.search(person, QUERY, action="cite", k=5)
    assert response.hits == []
    assert response.denied_code == "CASE_ACTION_DENIED"


# ---------------------------------------------------------------- 无放宽路径

def test_short_candidates_never_padded(runtime: CaseRuntime) -> None:
    """候选不足时如实返回，**不补齐**、不放宽 filter。"""
    person = principal(runtime, ("equipment",), "equipment")
    response = runtime.search.search(person, "锡膏印刷偏移", k=20)
    assert len(response.hits) < 20
    assert "少于" in response.notice
    assert response.hidden_count >= 0


def test_zero_hits_returns_honest_empty(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("equipment",), "equipment")
    response = runtime.search.search(person, "完全不存在的关键词串 zzzqqq", k=5)
    # 排序仍然可能返回低分命中，但绝不能编造；若返回空必须给出提示
    if not response.hits:
        assert response.notice
        assert response.degraded is False


def test_second_stage_blocks_forged_index_metadata(runtime: CaseRuntime) -> None:
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
    runtime.index_service.index.upsert_chunks([forged])

    person = principal(runtime, ("equipment",), "equipment")
    response = runtime.search.search(person, "缩水 注塑", k=20)
    ids = {hit["case_id"] for hit in response.hits}
    assert CASE_QUALITY_DEPT not in ids, "二次授权必须拦住伪造的可见性元数据"
    assert response.second_stage_dropped >= 1


def test_hidden_count_never_negative(runtime: CaseRuntime) -> None:
    for roles, dept in [(("pqe",), "quality"), (("gm",), "management")]:
        person = principal(runtime, roles, dept)
        response = runtime.search.search(person, "虚焊", k=5)
        assert response.hidden_count >= 0


# ---------------------------------------------------------------- 单资源读取 / 引用

def test_read_case_denied_returns_none(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("equipment",), "equipment")
    assert runtime.search.read_case(person, CASE_QUALITY_DEPT) is None
    assert runtime.search.read_case(person, CASE_REVIEW) is None
    assert runtime.search.read_case(person, CASE_SECRET) is None
    assert runtime.search.read_case(person, CASE_ROLE_EQUIPMENT) is not None


def test_read_case_returns_knowledge_layer_only(runtime: CaseRuntime) -> None:
    """含客户/供应商的案例：知识层是脱敏文本，证据层只给"存在"标记。"""
    person = principal(runtime, ("pqe",), "quality")
    case = runtime.search.read_case(person, "QC-CC-2026-0110")
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

def test_answer_every_claim_has_citation(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("pqe",), "quality")
    result = runtime.qa.answer(person, "锡膏印刷偏移怎么处理", k=3)
    assert result.answered is True
    assert result.claims and result.citations
    for claim in result.claims:
        assert claim["citations"], "无引用的断言必须被丢弃"
        citation = claim["citations"][0]
        assert citation["case_id"] and citation["chunk_id"]
        assert int(citation["content_version"]) >= 1


def test_answer_refuses_when_no_permission(runtime: CaseRuntime) -> None:
    """外包工问岗位经验 → 命中不足 → 拒答而不是编造。"""
    outsource = principal(runtime, ("equipment",), "equipment",
                          employment_type="outsource", user_id="u-out")
    result = runtime.qa.answer(outsource, "工艺调机参数窗口怎么定 zzz", k=3)
    if not result.answered:
        assert result.reason
    else:
        # 若仍有开放案例命中，则必须是带引用的知识层内容
        assert all(claim["citations"] for claim in result.claims)


# ---------------------------------------------------------------- 入库管道

def test_ingest_requires_source_ticket(runtime: CaseRuntime) -> None:
    with pytest.raises(SourceTicketRequired):
        runtime.ingest.ingest({"title": "无来源草稿", "phenomenon": "测试"})
    assert runtime.store.get_case("无来源草稿") is None


def test_ingest_redacts_and_stays_in_review(runtime: CaseRuntime) -> None:
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
    result = runtime.ingest.ingest(draft)
    assert result.status == "review"
    case = runtime.store.get_case("QC-PD-2026-9901")
    assert case is not None
    blob = " ".join(str(case[key]) for key in
                    ("title", "summary", "phenomenon"))
    # 原始敏感值不得残留
    for raw in ("华辰电子", "恒信精工", "王建国", "13800138000"):
        assert raw not in blob, f"入库后仍残留原始敏感值: {raw}"
    # review 状态不可被检索
    person = principal(runtime, ("pqe",), "quality")
    ids, _ = search_ids(runtime, person)
    assert "QC-PD-2026-9901" not in ids


def test_audit_chain_is_valid_and_has_no_plaintext(runtime: CaseRuntime) -> None:
    person = principal(runtime, ("pqe",), "quality")
    runtime.search.search(person, "虚焊", k=3)
    chain = runtime.audit.verify_chain()
    assert chain["valid"] is True
    recent = runtime.audit.recent(limit=5)
    blob = str(recent)
    assert "虚焊" not in blob, "审计不得记录查询明文"
    assert "dept:quality" not in blob, "审计不得记录 filter 明文"
