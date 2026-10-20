"""本体增强 Agentic RAG：功能契约守护（PRD FR-01 ~ FR-07）。

本文件守护"升级后**新增**的行为契约"，与既有 ``test_case_search_e2e`` /
``test_case_contracts`` 的分工是：

| 文件 | 守什么 |
|---|---|
| 既有用例 | 权限前置 / 二次授权 / 引用不可编造（**红线**，不得回归） |
| 本文件 | 有界 Agentic 循环 / 大模型作答与降级 / 多跳关系 / 证据链 2.0 / 三方对照 |

四条守护主线：

1. **红队视角**：LLM 环节不得引入任何新的越权面——计划器输出的越界字段被丢弃，
   闭环里每一轮召回都完整走安全内核（不存在"合并成一次查询"的快路径）；
2. **刹车有效**：跳数 / 轮数 / 收益阈值三条终止判据都有用例，
   并且终止原因**如实写进证据链**；
3. **降级可靠**：LLM 不可达/输出不可解析时，问答必须仍然返回结果（模板兜底），
   而不是 5xx 或空白；
4. **不编数据**：证据链、关系链、对照数字全部由后端真实统计。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mfg_agents.v2.contracts.case_acl import Principal
from mfg_agents.v2.contracts.case_index import KnowledgeChunk
from mfg_agents.v2.services import case_agentic, case_qa_llm, case_relation
from mfg_agents.v2.services.case_qa_llm import AnswererError, LLMAnswerer, LLMPlanner
from mfg_agents.v2.services.case_relation import CaseRelationIndex, segments_in_text

#: 仓库根 → data/case_seed.json（与 tests/v2/conftest.py 同源）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_SEED = _PROJECT_ROOT / "data" / "case_seed.json"


@pytest.fixture(scope="module")
def shared_runtime(tmp_path_factory):
    """模块级隔离运行时（临时库 + 内存后端 + 幂等种子）。

    为什么不用 ``case_client``：本文件多数用例要**直调服务层**并注入替身
    （LLM 作答器 / 循环参数），HTTP 层看不见这些注入点。
    """
    from mfg_agents.common.config import settings
    from mfg_agents.v2.services import case_runtime as case_runtime_mod
    from mfg_agents.v2.services.case_runtime import CaseRuntime

    saved = {
        "CASE_DB": getattr(settings, "CASE_DB", None),
        "CASE_SEED_PATH": getattr(settings, "CASE_SEED_PATH", None),
        "CASE_VECTOR_BACKEND": getattr(settings, "CASE_VECTOR_BACKEND", None),
        "CASE_ANSWERER": getattr(settings, "CASE_ANSWERER", None),
    }
    settings.CASE_DB = str(tmp_path_factory.mktemp("case_agentic") / "case.db")
    settings.CASE_SEED_PATH = str(CASE_SEED)
    settings.CASE_VECTOR_BACKEND = "memory"
    # 服务层用例必须离线：作答器默认走模板（大模型路径由注入替身的用例覆盖）
    settings.CASE_ANSWERER = "template"

    case_runtime_mod.reset_case_runtime()
    case_relation.reset_relation_index()
    runtime = CaseRuntime.build(db_path=settings.CASE_DB)
    try:
        yield runtime
    finally:
        case_relation.reset_relation_index()
        case_runtime_mod.reset_case_runtime()
        for key, value in saved.items():
            setattr(settings, key, value)


# ============================================================
# 测试替身：一个"可控的 LLM"
# ============================================================

class _Msg:
    def __init__(self, text: str) -> None:
        self.content = text


class _FakeLLM:
    """最小可用的 LLM 替身：记录提示词，按预设脚本回复。"""

    def __init__(self, reply: str = "", *, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(str(prompt))
        if self.error is not None:
            raise self.error
        return _Msg(self.reply)


def _factory(llm: _FakeLLM):
    def build(**kwargs):  # noqa: ANN001, ANN202 - 与 get_llm 同签名风格
        return llm
    return build


def _chunks() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(case_id="C-1", chunk_type="phenomenon", content_version=3,
                       text="SMT-1 线锡膏印刷偏移，面积偏差 18%", is_knowledge_layer=True),
        KnowledgeChunk(case_id="C-2", chunk_type="countermeasure", content_version=1,
                       text="校准刮刀压力至 0.5MPa 后恢复", is_knowledge_layer=True),
    ]


# ============================================================
# 1. FR-01 大模型作答器：引用结构可信 + 降级
# ============================================================

def test_llm_answerer_maps_slice_index_to_real_citation():
    """模型只被允许引用"片段编号"，编号 → 真实三元组由服务端映射（引用不可编造）。"""
    llm = _FakeLLM('{"claims":[{"text":"刮刀压力偏移导致印刷偏移","refs":[1,2]}]}')
    answerer = LLMAnswerer(llm_factory=_factory(llm), max_chars=200)
    claims = list(answerer("印刷偏移怎么处理", _chunks()))

    assert len(claims) == 1
    assert claims[0].has_citation is True
    refs = {c.chunk_id for c in claims[0].citations}
    assert refs == {"C-1::phenomenon", "C-2::countermeasure"}, "编号必须映射到真实 chunk"
    assert {c.content_version for c in claims[0].citations} == {3, 1}


def test_llm_answerer_drops_claims_without_or_with_bogus_refs():
    """无引用断言、越界编号断言一律丢弃（不给模型编造引用的入口）。"""
    llm = _FakeLLM('{"claims":[{"text":"无引用结论"},'
                   '{"text":"乱指结论","refs":[9]},'
                   '{"text":"合法结论","refs":[1]}]}')
    answerer = LLMAnswerer(llm_factory=_factory(llm))
    claims = list(answerer("q", _chunks()))
    assert [c.text for c in claims] == ["合法结论"]


def test_llm_answerer_tolerates_json_fence_and_raises_on_garbage():
    """容忍 ```json 围栏；完全不可解析 → 抛 AnswererError（由调用方降级）。"""
    fenced = _FakeLLM('```json\n{"claims":[{"text":"结论","refs":[1]}]}\n```')
    assert len(list(LLMAnswerer(llm_factory=_factory(fenced))("q", _chunks()))) == 1

    garbage = _FakeLLM("抱歉，我无法回答这个问题。")
    with pytest.raises(AnswererError):
        list(LLMAnswerer(llm_factory=_factory(garbage))("q", _chunks()))


def test_llm_answerer_raises_on_llm_failure():
    """网络/鉴权异常必须转成 AnswererError，不允许冒泡成 5xx。"""
    broken = _FakeLLM(error=ConnectionError("connection refused"))
    with pytest.raises(AnswererError):
        list(LLMAnswerer(llm_factory=_factory(broken))("q", _chunks()))


def test_llm_answerer_respects_char_budget():
    """输出字数上限：截断而不是无限增长（演示节奏 + 成本护栏）。"""
    long_text = "。" .join(["这是一条很长的结论"] * 50)
    llm = _FakeLLM('{"claims":[{"text":"%s","refs":[1]}]}' % long_text)
    claims = list(LLMAnswerer(llm_factory=_factory(llm), max_chars=50)("q", _chunks()))
    assert claims and len(claims[0].text) <= 50


# ============================================================
# 2. FR-02 计划器：白名单（LLM 永远碰不到过滤器）
# ============================================================

def test_llm_planner_drops_forbidden_fields_and_records_them(tmp_path=None):
    """**A10 红队用例**：计划器返回 filter/expr 之类字段必须被丢弃并留痕。"""
    llm = _FakeLLM('{"intent":"cross_segment","subqueries":["包装段标签错印"],'
                   '"want_relation":true,"filter":{"dept":"quality"},'
                   '"expr":"status == active","sql":"select * from cases"}')
    planner = LLMPlanner(llm_factory=_factory(llm))
    plan = planner("整机段能追到插件段吗", "整机段能追到插件段吗", _ontology())

    assert plan.source == "llm"
    assert not hasattr(plan, "filter")
    assert set(plan.dropped_fields) >= {"filter", "expr", "sql"}, plan.dropped_fields
    # 计划对象本身不含任何过滤语义字段（结构上的保证，而非约定）
    assert not any(k in plan.to_dict() for k in
                   ("filter", "filters", "expr", "ast", "acl"))


def test_llm_planner_falls_back_to_deterministic_on_bad_output():
    """计划输出不可解析 → 退回确定性计划（体验不劣于升级前）。"""
    planner = LLMPlanner(llm_factory=_factory(_FakeLLM("不是 JSON")))
    plan = planner("锡膏印刷偏移怎么处置", "锡膏印刷偏移怎么处置", _ontology())
    assert plan.source == "llm-fallback"
    assert plan.queries, "退回后仍必须有可用检索式"
    assert plan.queries[0] == "锡膏印刷偏移怎么处置"


def test_deterministic_planner_does_not_split_on_bare_comma():
    """裸逗号不得参与切分：否则"追到波峰焊吗"这种元问句会吃掉检索预算。"""
    planner = case_agentic.DeterministicPlanner()
    plan = planner("整机段发现的异响，能追到插件段的波峰焊吗？", "归一化文本", _ontology())
    assert plan.intent == "cross_segment"
    assert len(plan.queries) == 1, plan.queries


def test_deterministic_planner_splits_real_composite():
    """真复合问句（并列两件事）才拆，且拆出的子问句数受上限约束。"""
    planner = case_agentic.DeterministicPlanner()
    plan = planner("锡膏印刷偏移怎么处置；另外回流焊虚焊的根因有哪些", "归一化", _ontology())
    assert plan.intent in ("composite", "countermeasure")
    assert 2 <= len(plan.queries) <= 4


def _ontology():
    from mfg_agents.v2.contracts.ontology import get_ontology_registry

    return get_ontology_registry()


# ============================================================
# 3. FR-03 本体关系：多跳可用 + 诚实跳过
# ============================================================

def test_segments_in_text_recognises_two_segments():
    assert set(segments_in_text("整机段的异响能追到插件段的波峰焊吗")) == {"ASSY", "DIP"}


def test_relation_index_links_defect_to_root_and_segment(shared_runtime):
    index = CaseRelationIndex.from_store(shared_runtime.store)
    assert index.case_count > 0
    neighbors = {node for node, _agg in index.neighbors(("segment", "DIP"), limit=20)}
    kinds = {node[0] for node in neighbors}
    assert "defect_mode" in kinds and "root_cause" in kinds


def test_relation_expansion_produces_cross_segment_terms(shared_runtime):
    """跨段问句必须能产出**可用的召回词**（否则多跳只是概念，不是能力）。"""
    index = CaseRelationIndex.from_store(shared_runtime.store)
    ontology = _ontology()
    question = "整机段发现的异响，能追到插件段的波峰焊吗？"
    expansion = index.expand(
        question=question,
        defect_codes=ontology.find_terms("defect_mode", question, limit=3),
        root_codes=ontology.find_terms("root_cause", question, limit=3),
        max_hops=3)
    assert expansion.entries, "关系图必须能走出相邻节点"
    assert expansion.terms, "必须产出可用召回词（否则多跳无效果）"
    assert expansion.hops_used >= 1
    assert any(e.kind == "segment" for e in expansion.entries), "应走到工艺段节点"


def test_relation_expansion_reports_unregistered_codes_honestly(shared_runtime):
    """未登记本体的编码 → 记进 skipped（没有可靠召回词），不硬编一个词凑命中。"""
    index = CaseRelationIndex.from_store(shared_runtime.store)
    expansion = index.expand(question="整机段", defect_codes=["DM-ASM-005"], max_hops=3)
    assert any("未登记本体" in s for s in expansion.skipped), expansion.skipped


def test_relation_expansion_stops_without_entry_point(shared_runtime):
    """问句没有任何受控码/段入口 → 不启动扩展，且如实说明。"""
    index = CaseRelationIndex.from_store(shared_runtime.store)
    expansion = index.expand(question="今天天气怎么样", max_hops=3)
    assert expansion.terms == []
    assert expansion.skipped and "无受控码" in expansion.skipped[0]


def test_relation_index_cache_invalidated_on_index_write(shared_runtime):
    """索引写入（入库/发布/撤权/重建）必须让关系图缓存失效，否则图会陈旧。"""
    first = case_relation.get_relation_index(shared_runtime.store)
    shared_runtime.index_service.rebuild()
    second = case_relation.get_relation_index(shared_runtime.store)
    assert first is not second, "重建索引后关系图缓存必须已失效"


# ============================================================
# 4. FR-02 有界循环：刹车判据（服务层）
# ============================================================

def _principal(roles=("pqe",), dept="quality", plant="P1",
               employment_type="employee", user_id="u-agentic") -> Principal:
    return Principal(user_id=user_id, roles=roles, dept=dept, plant=plant,
                     employment_type=employment_type, tenant="T001",
                     principal_version=1)


def test_loop_terminates_with_reason_and_records_evidence_chain(shared_runtime):
    """证据链必须给出**终止原因**，且每步带决策类型/关键数字/触发原因三件套。"""
    result = shared_runtime.qa.answer(
        _principal(), "整机段发现的异响，能追到插件段的波峰焊吗？", k=5)

    assert result.agentic, "必须产出 Agentic 循环摘要"
    assert result.agentic["termination"], "终止原因不得为空"
    assert result.agentic["rounds"] >= 1
    assert result.trace, "证据链不得为空"
    for step in result.trace:
        assert step["kind"] in ("deterministic", "agentic", "safe"), step
        assert isinstance(step["numbers"], dict)
        assert step["step"] >= 1
    assert any(s["kind"] == "agentic" for s in result.trace), "应有 Agentic 决策步骤"
    assert any(s["kind"] == "safe" for s in result.trace), "应有安全内核步骤"


def test_loop_gives_up_relations_for_off_topic_question(shared_runtime):
    """无关问题：判定为非案例域 → 不启动关系扩展（否则会"一本正经地胡说"）。"""
    result = shared_runtime.qa.answer(_principal(), "今天天气怎么样", k=5)
    assert result.answered is False
    assert "非案例域" in result.agentic["termination"]
    assert result.agentic["hops"] == 0
    assert not any(s["title"].startswith("本体关系扩展") for s in result.trace)


def test_loop_respects_hops_limit(shared_runtime, monkeypatch):
    """跳数硬上限生效：把上限调到 0，关系扩展一律不启动。"""
    from mfg_agents.common.config import settings

    monkeypatch.setattr(settings, "CASE_AGENTIC_MAX_HOPS", 0)
    result = shared_runtime.qa.answer(
        _principal(), "整机段发现的异响，能追到插件段的波峰焊吗？", k=5)
    assert result.agentic["hops"] == 0
    assert not any(s["title"].startswith("本体关系扩展") for s in result.trace)


def test_loop_respects_budget(shared_runtime, monkeypatch):
    """预算生效：主查询轮数受 MAX_ROUNDS 约束（拆出的第 2 路人马不放行）。"""
    from mfg_agents.common.config import settings

    monkeypatch.setattr(settings, "CASE_AGENTIC_MAX_ROUNDS", 1)
    monkeypatch.setattr(settings, "CASE_AGENTIC_MAX_HOPS", 0)
    result = shared_runtime.qa.answer(
        _principal(), "锡膏印刷偏移怎么处置；另外回流焊虚焊的常见根因有哪些", k=5)
    assert result.agentic["rounds"] == 1, "主预算 1 轮时不得出现第二轮召回"
    assert result.agentic["hops"] == 0
    assert not any("复合问句第 2 个子问句" in s["why"] for s in result.trace)


def test_agentic_disabled_degrades_to_single_round(shared_runtime, monkeypatch):
    """总开关关闭 = 升级前行为（单轮检索）——回滚开关必须真的能回滚。"""
    from mfg_agents.common.config import settings

    monkeypatch.setattr(settings, "CASE_AGENTIC_ENABLED", False)
    result = shared_runtime.qa.answer(_principal(), "锡膏印刷偏移怎么处置", k=5)
    assert result.agentic["enabled"] is False
    assert result.agentic["rounds"] == 1
    assert not any(s["title"].startswith("本体关系扩展") for s in result.trace)


def test_multi_round_merge_keeps_best_score_per_chunk_and_caps_per_case():
    """归并语义：同一 chunk 取最高分；同案例最多 2 个 chunk（与单轮口径一致）。"""
    from mfg_agents.v2.services.case_search import SearchResponse

    def hit(chunk_id, case_id, chunk_type, score):
        return {"chunk_id": chunk_id, "case_id": case_id, "chunk_type": chunk_type,
                "content_version": 1, "text": "t", "score": score, "title": "T",
                "subcategory": "x"}

    first = SearchResponse(hits=[hit("A::summary", "A", "summary", 0.4),
                                 hit("A::phenomenon", "A", "phenomenon", 0.3),
                                 hit("A::root_cause", "A", "root_cause", 0.25)],
                           candidates=5)
    second = SearchResponse(hits=[hit("A::summary", "A", "summary", 0.55),
                                  hit("B::summary", "B", "summary", 0.5)],
                            candidates=3)
    merged = case_agentic.merge_search_responses([first, second])
    by_chunk = {h["chunk_id"]: h["score"] for h in merged.hits}
    assert by_chunk["A::summary"] == 0.55, "同 chunk 必须取最高分"
    assert len([h for h in merged.hits if h["case_id"] == "A"]) == 2, "同案例 ≤2 chunk"
    assert merged.candidates == 8, "候选数按轮次汇总（真实统计口径）"


# ============================================================
# 5. FR-01 降级：LLM 挂了也必须答得出（用模板）
# ============================================================

def test_answerer_falls_back_to_template_when_llm_fails(shared_runtime, monkeypatch):
    """把 LLM 作答器换成"必失败"的替身 → 问答仍返回结果且标注降级。"""
    from mfg_agents.v2.services.case_qa import CaseQAService

    broken = LLMAnswerer(llm_factory=_factory(_FakeLLM(error=TimeoutError("boom"))))
    original = shared_runtime.qa._llm_answerer
    shared_runtime.qa._llm_answerer = broken
    try:
        result = shared_runtime.qa.answer(_principal(), "锡膏印刷偏移怎么处置", k=5)
    finally:
        shared_runtime.qa._llm_answerer = original

    assert result.answerer == "template(fallback)"
    assert "降级" in result.answerer_note
    assert result.answered is True, "降级后仍须给出带引用的结论"
    assert all(c["citations"] for c in result.claims)
    assert any("降级" in s["detail"] for s in result.trace), "降级必须写进证据链"


def test_template_mode_never_calls_llm(shared_runtime, monkeypatch):
    """CASE_ANSWERER=template：离线可跑，且作答器标识如实标注。"""
    from mfg_agents.v2.services.case_qa import CaseQAService

    called: list[int] = []

    class _Counting(LLMAnswerer):
        def __call__(self, question, chunks):
            called.append(1)
            return super().__call__(question, chunks)

    original = shared_runtime.qa._llm_answerer
    shared_runtime.qa._llm_answerer = None  # 模拟"未配置大模型"
    try:
        result = shared_runtime.qa.answer(_principal(), "锡膏印刷偏移怎么处置", k=5)
    finally:
        shared_runtime.qa._llm_answerer = original
    assert result.answerer == "template"
    assert not called
    assert result.answered is True


# ============================================================
# 6. FR-07 拒答引导：口语 → 受控术语
# ============================================================

def test_suggestions_rewrite_colloquial_to_controlled_term(shared_runtime):
    """问句里出现的是口语/别名 → 建议权威术语（"你说的 X 是不是 Y"）。"""
    onto = _ontology()
    pair = None
    for term in onto.terms("defect_mode"):
        for synonym in term.synonyms:
            # 别名要够长（短别名会误伤无关问句），且与权威名不同
            if len(synonym) >= 2 and synonym != term.name:
                pair = (term.name, synonym)
                break
        if pair:
            break
    assert pair, "词表里应存在可用于测试的别名"
    name, synonym = pair
    result = shared_runtime.qa.answer(_principal(), f"{synonym} 怎么处置", k=5)
    terms = {s["term"] for s in result.suggestions}
    assert name in terms, f"应建议权威术语 {name}，实际 {result.suggestions}"


def test_suggestions_are_generated_by_backend_not_frontend(shared_runtime):
    """建议由后端产出（前端只渲染）——离线/拒答路径也必须给得出建议结构。"""
    result = shared_runtime.qa.answer(_principal(), "焊点的雪花点怎么处理", k=5)
    for item in result.suggestions:
        assert item["kind"] in ("rewrite", "adjacent")
        assert item["term"] and item["why"]


# ============================================================
# 7. FR-06 三方对照（HTTP）
# ============================================================

def test_compare_endpoint_is_gated_by_demo_switch(case_client):
    """默认关闭：生产环境不允许出现第二/第三种检索行为（403 + 明确错误码）。"""
    from mfg_agents.common.config import settings

    settings.CASE_DEMO_COMPARE = False
    token = _session(case_client)
    resp = case_client.post("/api/v2/cases/compare", headers={"Authorization": f"Bearer {token}"},
                            json={"question": "整机段能追到插件段吗"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CASE_COMPARE_DISABLED"


def test_compare_endpoint_returns_three_variants_with_same_scope(case_client):
    """开启后：三组配置并列，且**可见范围完全相同**（对照的只能是检索智能）。"""
    from mfg_agents.common.config import settings

    settings.CASE_DEMO_COMPARE = True
    try:
        token = _session(case_client)
        resp = case_client.post("/api/v2/cases/compare",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"question": "整机段发现的异响，能追到插件段的波峰焊吗？",
                                      "k": 5})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert len(data["variants"]) == 3
        labels = [v["label"] for v in data["variants"]]
        assert labels[0].startswith("A") and labels[1].startswith("B") \
            and labels[2].startswith("C"), labels
        # 同一主体 → 三组的 hidden_count（"因权限不可见"的条数）必须一致：
        # 若不一致，说明对照把权限差异混进了检索差异。
        hidden = {v["hidden_count"] for v in data["variants"]}
        assert len(hidden) == 1, f"三组可见范围不一致：{hidden}"
        assert data["gains"]["summary"]
        assert data["notice"]
    finally:
        settings.CASE_DEMO_COMPARE = False


def test_compare_requires_question(case_client):
    from mfg_agents.common.config import settings

    settings.CASE_DEMO_COMPARE = True
    try:
        token = _session(case_client)
        resp = case_client.post("/api/v2/cases/compare",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"question": "   "})
        assert resp.status_code == 422
    finally:
        settings.CASE_DEMO_COMPARE = False


def _session(client, role: str = "pqe", dept: str = "quality",
             employment_type: str = "employee") -> str:
    resp = client.post("/api/v2/cases/session",
                       json={"role": role, "dept": dept, "plant": "P1",
                             "employment_type": employment_type,
                             "name": "agentic-probe"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


# ============================================================
# 8. FR-08 本体覆盖率（治理面）
# ============================================================

def test_coverage_endpoint_reports_real_alignment(case_client):
    """覆盖率必须是**真实比对**的结果：数字自洽，且未登记编码有样例可回填。"""
    token = _session(case_client)
    resp = case_client.get("/api/v2/cases/ontology/coverage",
                           headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["cases"] > 0
    assert data["target_rate"] == 0.95
    for vocab in ("defect_mode", "root_cause"):
        row = data["vocabularies"][vocab]
        assert row["used_codes"] == row["registered_codes"] + row["unregistered_codes"]
        assert 0.0 <= row["rate"] <= 1.0
        assert row["vocab_size"] > 0
        if row["unregistered_codes"]:
            assert row["meets_target"] is False
            assert row["unregistered_sample"], "未登记编码必须给样例（治理要能派工）"
    assert data["notes"], "覆盖率结论不得为空（治理看板的核心输出）"
    assert data["segments"], "段分布是判断系统性缺口的输入"


def test_coverage_is_not_case_content(case_client):
    """覆盖率响应**不得**包含案例正文/标题（治理面不能变成第二个读取接口）。"""
    token = _session(case_client)
    resp = case_client.get("/api/v2/cases/ontology/coverage",
                           headers={"Authorization": f"Bearer {token}"})
    text = resp.text
    assert "phenomenon" not in text and "root_cause_text" not in text
    assert "summary" not in text and "title" not in text


# ============================================================
# 9. FR-10 命中分布热力（可见范围 + 空白格）
# ============================================================

def test_distribution_is_visible_scope_and_has_blank_cells(shared_runtime):
    """热力图必须与检索同源：可见案例总数自洽，且给出真实空白格。"""
    data = shared_runtime.search.distribution(_principal(), action="query", top=6)
    assert data["visible_total"] > 0
    assert data["segments"], "段维度不得为空"
    assert data["defect_modes"], "缺陷模式维度不得为空"
    assert data["defect_modes_total"] >= len(data["defect_modes"])
    # 非零格必须与段/缺陷维度自洽（计数不会凭空出现）
    seg_keys = {s["key"] for s in data["segments"]}
    for cell in data["cells"]:
        assert cell["segment"] in seg_keys, "格子里的段必须出现在段列表里"
        assert cell["count"] > 0
    # 空白格 = 段 × 缺陷模式的笛卡尔积里计数为 0 的那些（真实统计，不是"没查"）
    zero = len(data["segments"]) * len(data["defect_modes"]) - len([
        c for c in data["cells"]
        if c["segment"] in seg_keys
        and c["defect_mode"] in {m["code"] for m in data["defect_modes"]}])
    assert len(data["blank_cells"]) == max(0, zero), \
        "空白格口径必须与段×模式笛卡尔积严格对齐"
    assert data["notes"], "口径说明不得为空"


def test_distribution_top_is_bounded_and_disclosed(shared_runtime):
    """缺陷模式按 top 截断，且必须**如实披露**总数与截断事实（不假装画全了）。"""
    small = shared_runtime.search.distribution(_principal(), action="query", top=2)
    assert len(small["defect_modes"]) == 2
    assert small["defect_modes_total"] >= 2
    if small["defect_modes_total"] > 2:
        assert any("截断" in n for n in small["notes"])


def test_distribution_respects_identity_visibility(case_client):
    """同一句话、不同身份 → 可见范围内的分布必须不同（热力图不能绕过权限）。"""
    token_a = _session(case_client, role="pqe", dept="quality")
    token_b = _session(case_client, role="manufacturing", dept="production",
                      employment_type="outsource")
    a = case_client.get("/api/v2/cases/distribution?top=6",
                        headers={"Authorization": f"Bearer {token_a}"}).json()["data"]
    b = case_client.get("/api/v2/cases/distribution?top=6",
                        headers={"Authorization": f"Bearer {token_b}"}).json()["data"]
    assert a["visible_total"] != b["visible_total"], "外包工与质量工程师的可见范围必须不同"
    assert a["visible_total"] > b["visible_total"]


def test_distribution_leaks_no_case_content(case_client):
    """分布接口只回聚合计数与编码，不得回案例正文（不能变成第二个读取面）。"""
    token = _session(case_client)
    body = case_client.get("/api/v2/cases/distribution?top=6",
                           headers={"Authorization": f"Bearer {token}"}).text
    assert "phenomenon" not in body and "root_cause_text" not in body
    assert "summary" not in body


# ============================================================
# 10. FR-09 本体缺口登记（申请 / 裁决 / 职责分离）
# ============================================================

def test_gap_request_enters_queue_not_vocabulary(shared_runtime):
    """登记申请**只入队列，不写词表**（N7：不做自动本体抽取）。"""
    gap = shared_runtime.ontology_gap.request(
        _principal(), vocab="defect_mode", term="雪花点", hint="焊点表面发白的斑点",
        source="ask")
    assert gap["status"] == "pending"
    assert "vocabulary_written" not in gap, "申请态不得出现'已写词表'字段（语义没有这一步）"
    assert "不自动" in gap["next_step"]
    # 词表里依然查不到这个词（批准前/后都不自动写入）
    onto = _ontology()
    assert onto.normalize("defect_mode", "雪花点") is None


def test_gap_request_is_idempotent_for_same_term(shared_runtime):
    """同一个词重复提交 → 返回既有待审申请（不产生第二张待办）。"""
    first = shared_runtime.ontology_gap.request(
        _principal(), vocab="root_cause", term="刮刀压力表未纳入点检")
    assert first["duplicated"] is False
    second = shared_runtime.ontology_gap.request(
        _principal(), vocab="root_cause", term="刮刀压力表未纳入点检")
    assert second["duplicated"] is True
    assert second["gap_id"] == first["gap_id"]


def test_gap_request_rejects_already_registered_term(shared_runtime):
    """已在词表里的词（含同义词命中）→ 明确拒绝并给出规范名（防重复造词）。"""
    from mfg_agents.v2.services.case_ontology_gap import GapAlreadyRegistered

    with pytest.raises(GapAlreadyRegistered) as err:
        shared_runtime.ontology_gap.request(
            _principal(), vocab="defect_mode", term="虚焊")
    assert "已在受控词表" in str(err.value)


def test_gap_request_validates_input(shared_runtime):
    """入参校验：未知词表 / 空术语 / 超长 → 400 类错误，不写脏数据。"""
    from mfg_agents.v2.services.case_ontology_gap import GapInvalid

    for kwargs in ({"vocab": "not_a_vocab", "term": "x"},
                   {"vocab": "defect_mode", "term": "   "},
                   {"vocab": "defect_mode", "term": "超" * 61}):
        with pytest.raises(GapInvalid):
            shared_runtime.ontology_gap.request(_principal(), **kwargs)


def test_gap_decide_requires_permission_and_separation(shared_runtime):
    """裁决三重门禁：需 case.ingest、不能批自己的、只有 pending 可裁决。"""
    from mfg_agents.v2.services.case_ontology_gap import (
        GapError, GapInvalid, GapTransitionError,
    )

    requester = _principal(roles=("pqe",))
    gap = shared_runtime.ontology_gap.request(
        requester, vocab="defect_mode", term="波峰焊锡面氧化膜")
    # ① 无 case.ingest 的角色：门口就被挡住
    with pytest.raises(GapError):
        shared_runtime.ontology_gap.decide(_principal(roles=("qa",)), gap["gap_id"],
                                           approve=True)
    # ② 申请人自己（pqe 持有 ingest）：职责分离
    with pytest.raises(GapError):
        shared_runtime.ontology_gap.decide(requester, gap["gap_id"], approve=True)
    # ③ 驳回必须写理由
    approver = _principal(roles=("qa_manager",), user_id="u-mgr-1")
    with pytest.raises(GapInvalid):
        shared_runtime.ontology_gap.decide(approver, gap["gap_id"], approve=False)
    # ④ 裁决后不能再裁决（状态机）
    decided = shared_runtime.ontology_gap.decide(
        approver, gap["gap_id"], approve=True, reason="与现场口语一致")
    assert decided["status"] == "approved"
    with pytest.raises(GapTransitionError):
        shared_runtime.ontology_gap.decide(approver, gap["gap_id"], approve=True)


def test_gap_endpoints_http_contract(case_client):
    """HTTP 契约：申请 → 列表（mine / pending）→ 裁决；错误码语义明确。"""
    token = _session(case_client, role="pqe", dept="quality")
    headers = {"Authorization": f"Bearer {token}"}
    created = case_client.post("/api/v2/cases/ontology/gap", headers=headers, json={
        "vocab": "defect_mode", "term": "锡珠残留边缘毛刺", "source": "ask"})
    assert created.status_code == 200, created.text
    gap_id = created.json()["data"]["gap_id"]

    listed = case_client.get("/api/v2/cases/ontology/gap", headers=headers)
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert gap_id in [g["gap_id"] for g in data["mine"]]
    assert gap_id in [g["gap_id"] for g in data["pending"]]
    assert isinstance(data["can_decide"], bool)

    # 已在词表 → 409 + 明确错误码
    dup = case_client.post("/api/v2/cases/ontology/gap", headers=headers,
                           json={"vocab": "defect_mode", "term": "虚焊"})
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "CASE_ONTOLOGY_ALREADY_REGISTERED"

    # 申请人自己裁决 → 403（职责分离）+ 状态不变
    self_decide = case_client.post(
        f"/api/v2/cases/ontology/gap/{gap_id}/decide", headers=headers,
        json={"approve": True, "reason": "自批"})
    assert self_decide.status_code == 403
    still = case_client.get("/api/v2/cases/ontology/gap", headers=headers)
    row = [g for g in still.json()["data"]["mine"] if g["gap_id"] == gap_id][0]
    assert row["status"] == "pending"


def test_gap_approval_does_not_write_vocabulary(case_client):
    """**批准 ≠ 写词表**：审批后词表里仍然没有这个词（人工登记才是唯一入口）。"""
    requester = _session(case_client, role="pqe", dept="quality")
    created = case_client.post("/api/v2/cases/ontology/gap",
                               headers={"Authorization": f"Bearer {requester}"},
                               json={"vocab": "defect_mode", "term": "回流焊气孔聚集"}).json()
    gap_id = created["data"]["gap_id"]
    approver = _session(case_client, role="qa_manager", dept="quality")
    decided = case_client.post(
        f"/api/v2/cases/ontology/gap/{gap_id}/decide",
        headers={"Authorization": f"Bearer {approver}"},
        json={"approve": True, "reason": "确认口径"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["data"]["status"] == "approved"
    assert decided.json()["data"]["vocabulary_written"] is False
    assert _ontology().normalize("defect_mode", "回流焊气孔聚集") is None


# ============================================================
# 11. 契约：ask 响应新增字段（前端依赖）
# ============================================================

def test_ask_response_exposes_agentic_contract(case_client):
    token = _session(case_client)
    resp = case_client.post("/api/v2/cases/ask",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"question": "锡膏印刷偏移怎么处置", "k": 3})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    for key in ("answered", "claims", "trace", "ontology_gain",
                "answerer", "answerer_note", "agentic", "suggestions"):
        assert key in data, f"ask 响应缺少契约字段 {key}"
    for step in data["trace"]:
        assert {"step", "title", "detail", "items", "kind", "numbers", "why"} <= set(step)
    assert data["agentic"]["termination"]
    for claim in data["claims"]:
        assert claim["citations"], "无引用断言必须被丢弃（BR-05 不得回归）"
