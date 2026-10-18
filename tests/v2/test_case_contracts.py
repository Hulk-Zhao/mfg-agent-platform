"""案例助手 · 契约钉死测试（R1/R2/R3 + B1-B5 回归）。

为什么单独一个文件：这些断言钉的是**口径**，不是某条功能路径。
口径变了（比如"hidden_count 是案例数"被改回 chunk 数），
现象是"演示现场被追问一句就穿"，而不是"某接口报错"——
所以它们必须集中、成组、每条都写明"为什么这条不能松"。

| 组 | 钉什么 |
| --- | --- |
| R1 | 检索/问答响应**不得**出现原始来源单号；详情接口才是受控的窄面 |
| R2 | `hidden_count` 的单位是**案例数**（按 case_id 去重），不是 chunk 数 |
| R3 | 引用校验要**文本 + 版本双一致**，只比版本等于没校验 |
| B1 | 404 不存在路径与"存在但无权"路径做等价工作量（时间侧信道） |
| B2 | 已发布案例不得被重复 ingest 静默打回 review / 踢出索引 |
| B3 | 种子与入库同走脱敏管道；词表外人名由 denylist 兜住 |
| B4 | 改 ACL 必须重算 `visibility_class`，否则快路径误放行 |
| B5 | `AUTH_SECRET` 未配置必须"显式且刺眼"；演示开关关闭时 503 |
"""
from __future__ import annotations

import re

import pytest

OPEN_CASE = "QC-CC-2026-0087"          # 全员开放
DEPT_CASE = "QC-PD-2026-0055"          # 仅 dept:quality
_TICKET_RE = re.compile(r"8D-\d{4}-\d{4}")


# ---------------------------------------------------------------- 工具


def _session(client, role: str, dept: str = "", plant: str = "P1", **extra) -> str:
    body = {"role": role, "dept": dept, "plant": plant, "name": f"{role}-contract"}
    body.update(extra)
    resp = client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _search(client, token: str, query: str = "锡膏印刷偏移 连锡", k: int = 50):
    return client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "action": "query", "k": k})


def _runtime():
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    return get_case_runtime()


# ---------------------------------------------------------------- R1：原始单号


def test_r1_search_hits_never_carry_raw_ticket_id(case_client):
    """命中载荷里不许有 `source_ticket_id`，也不许有任何 `8D-YYYY-NNNN` 形态的串。

    为什么这条不能松：8D / 客诉单号在真实工厂可反查到客户、批次与产线，
    属于"可关联到具体主体的标识"。检索响应是面向前端的宽面（一次几十条、
    会被复制到 IM、导出、截图），单号一旦在这里出现，脱敏就形同虚设。
    """
    token = _session(case_client, "qa", dept="quality")
    resp = _search(case_client, token)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    hits = payload["data"]["hits"]
    assert hits, "没有命中，测试失效"

    for hit in hits:
        assert "source_ticket_id" not in hit, \
            f"{hit['case_id']} 的命中载荷带了原始单号"
        assert hit.get("source_ticket_ref"), f"{hit['case_id']} 缺少 source_ticket_ref"
        assert re.fullmatch(r"st_[0-9a-f]{10}", hit["source_ticket_ref"]), \
            f"ref 形态不对：{hit['source_ticket_ref']}"
        assert hit.get("source_ticket_type"), "来源单据类型应当保留（只有类型，没有号）"

    blob = resp.text
    assert not _TICKET_RE.search(blob), \
        f"检索响应出现原始工单号：{_TICKET_RE.search(blob).group(0)}"


def test_r1_ask_response_never_carries_raw_ticket_id(case_client):
    """问答响应同样不许出现原始单号（它会把检索命中整段嵌回来）。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                            json={"question": "锡膏印刷偏移怎么处置", "k": 5})
    assert resp.status_code == 200, resp.text
    assert not _TICKET_RE.search(resp.text), "问答响应出现原始工单号"


def test_r1_ticket_ref_is_stable_and_not_reversible(case_client):
    """ref 必须稳定（同一单号永远同一 ref）且不可反推（不等于单号、不含单号）。"""
    token = _session(case_client, "qa", dept="quality")
    first = _search(case_client, token).json()["data"]["hits"]
    second = _search(case_client, token).json()["data"]["hits"]
    refs_first = {h["case_id"]: h["source_ticket_ref"] for h in first}
    refs_second = {h["case_id"]: h["source_ticket_ref"] for h in second}
    assert refs_first == refs_second, "同一身份两次检索的 ref 不一致（不可对账）"

    from mfg_agents.v2.services.case_search import source_ticket_ref

    raw = "8D-2026-0087"
    ref = source_ticket_ref(raw)
    assert ref == source_ticket_ref(raw), "ref 不稳定"
    assert raw not in ref and ref not in raw
    assert source_ticket_ref("") == ""


def test_r1_detail_is_the_only_full_trace_surface(case_client):
    """详情接口才是受控窄面：有权 → 给真号 + `X-Case-Trace: full`。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.get(f"/api/v2/cases/{OPEN_CASE}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Case-Trace") == "full"
    body = resp.json()["data"]
    assert body["source_ticket_id"], "详情应给出原始单号（溯源需要）"
    assert body["trace"] == "full"


def test_r1_unauthorized_detail_does_not_leak_ticket(case_client):
    """无权时 404 且响应体里没有单号（不能靠 404 的 body 反推）。"""
    token = _session(case_client, "equipment", dept="equipment")
    resp = case_client.get(f"/api/v2/cases/{DEPT_CASE}", headers=_auth(token))
    assert resp.status_code == 404, resp.text
    assert not _TICKET_RE.search(resp.text)
    assert "source_ticket_id" not in resp.text


# ---------------------------------------------------------------- R2：hidden_count 口径


def test_r2_hidden_count_is_case_count_not_chunk_count(case_client):
    """`hidden_count` 必须等于"案例数"差，而不是 chunk 数差。"""
    from mfg_agents.v2.security.acl_compiler import get_acl_compiler

    token = _session(case_client, "equipment", dept="equipment")
    resp = _search(case_client, token)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["hidden_count_unit"] == "case"
    assert data["hidden_count"] > 0, "这个身份应当有不可见案例，测试失效"

    runtime = _runtime()
    principal = runtime.resolver.from_token(token)
    index = runtime.index_service.index
    compiler = get_acl_compiler()
    hard = compiler.compile_hard_only(principal, "query")

    from mfg_agents.v2.security.filter_translator import filter_hash  # noqa: F401
    full, _reason = compiler.compile_explained(principal, "query")

    cases_hard = len({h.case_id for h in index.search("", hard, 100000)})
    cases_full = len({h.case_id for h in index.search("", full, 100000)})
    assert data["hidden_count"] == max(0, cases_hard - cases_full)

    # 反例：chunk 差必然 ≥ 案例差，且这里严格大于（说明口径确实换了）
    chunks_hard = index.count(hard)
    chunks_full = index.count(full)
    assert chunks_hard - chunks_full > cases_hard - cases_full, \
        "chunk 差与案例差相等，无法区分口径，测试失效"


def test_r2_meta_declares_hidden_count_unit(case_client):
    """口径要写在 meta 里，不能只靠文档口头约定。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.get("/api/v2/cases/meta", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["hidden_count_unit"] == "case"
    assert "案例数" in data["hidden_count_note"]


# ---------------------------------------------------------------- R3：引用双一致


def test_r3_verify_citation_rejects_text_change_without_version_bump(case_client):
    """绕过管道改文本（**不 bump 版本**）→ 引用必须失效。

    这里刻意用裸 SQL：``update_fields`` 已经会 bump ``content_version``（见下一条），
    要复现"版本没变、文本变了"只能模拟真正的绕过行为（直接改库）。
    """
    _session(case_client, "qa", dept="quality")   # 初始化运行时
    runtime = _runtime()
    version = int(runtime.store.get_case(OPEN_CASE)["content_version"])
    assert runtime.index_service.verify_citation(OPEN_CASE, "summary", version) is True
    original = str(runtime.store.get_case(OPEN_CASE)["summary"])
    try:
        with runtime.store._conn() as conn:   # noqa: SLF001 - 测试要模拟绕过管道
            conn.execute('UPDATE "case" SET summary=? WHERE case_id=?',
                         ("被人从数据库里直接改过的摘要", OPEN_CASE))
        assert int(runtime.store.get_case(OPEN_CASE)["content_version"]) == version, \
            "裸 SQL 不应改变版本号（本测试的前提）"
        assert runtime.index_service.verify_citation(
            OPEN_CASE, "summary", version) is False, \
            "文本变了但引用校验仍通过——引用与用户读到的内容已经不是同一段"
    finally:
        with runtime.store._conn() as conn:   # noqa: SLF001
            conn.execute('UPDATE "case" SET summary=? WHERE case_id=?',
                         (original, OPEN_CASE))


def test_r3_update_fields_bumps_content_version(case_client):
    """改知识层文本必须 bump 版本：版本号要能代表内容（QA 建议项）。"""
    _session(case_client, "qa", dept="quality")
    runtime = _runtime()
    original = str(runtime.store.get_case(OPEN_CASE)["countermeasure_text"])
    v0 = int(runtime.store.get_case(OPEN_CASE)["content_version"])
    try:
        runtime.store.update_fields(OPEN_CASE, countermeasure_text="改过的一条对策")
        v1 = int(runtime.store.get_case(OPEN_CASE)["content_version"])
        assert v1 == v0 + 1, f"改文本未 bump 版本：{v0} → {v1}"
        # 版本变了 → 旧的三元组失效（新内容需要重新引用）
        assert runtime.index_service.verify_citation(
            OPEN_CASE, "summary", v0) is False
    finally:
        runtime.store.update_fields(OPEN_CASE, countermeasure_text=original)
        runtime.index_service.sync_case(OPEN_CASE)   # 还原索引里的版本，别污染后续用例


def test_r3_missing_text_hash_is_backfilled_not_dropped(case_client):
    """老库的 ``text_hash`` 是空串时：回填，而不是把断言全丢掉。

    否则升级后会出现"问什么都答不上来且不报错"的静默故障。
    """
    _session(case_client, "qa", dept="quality")
    runtime = _runtime()
    chunk_id = f"{OPEN_CASE}::summary"
    version = int(runtime.store.get_case(OPEN_CASE)["content_version"])
    original = ""
    for row in runtime.store.chunk_rows(OPEN_CASE):
        if str(row.get("chunk_id")) == chunk_id:
            original = str(row.get("text_hash") or "")
    assert original, "基线就该有 text_hash，测试失效"
    try:
        with runtime.store._conn() as conn:   # noqa: SLF001 - 模拟老库空值
            conn.execute("UPDATE case_chunk SET text_hash='' WHERE chunk_id=?",
                         (chunk_id,))
        assert runtime.index_service.verify_citation(
            OPEN_CASE, "summary", version) is True, "缺 hash 不该直接丢弃断言"
        backfilled = [row["text_hash"] for row in runtime.store.chunk_rows(OPEN_CASE)
                      if str(row["chunk_id"]) == chunk_id][0]
        assert backfilled == original, "缺 hash 时未回填"
    finally:
        runtime.store.backfill_chunk_text_hash(OPEN_CASE, chunk_id, original)


def test_r3_ask_drops_claim_when_text_tampered(case_client):
    """端到端：文本被篡改后，问答里对应的断言必须被丢弃（dropped_claims > 0）。"""
    token = _session(case_client, "qa", dept="quality")
    ok = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                          json={"question": "锡膏印刷偏移怎么处置", "k": 5})
    assert ok.status_code == 200, ok.text
    baseline = ok.json()["data"]["dropped_claims"]
    assert baseline == 0, f"基线应当是 0 丢弃，实际 {baseline}"

    runtime = _runtime()
    original = str(runtime.store.get_case(OPEN_CASE)["phenomenon"])
    try:
        # 裸 SQL：绕过管道，版本不变、文本变（update_fields 现在会 bump 版本）
        with runtime.store._conn() as conn:   # noqa: SLF001
            conn.execute('UPDATE "case" SET phenomenon=? WHERE case_id=?',
                         ("数据库里被改掉的现象描述", OPEN_CASE))
        tampered = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                                    json={"question": "锡膏印刷偏移怎么处置", "k": 5})
        assert tampered.status_code == 200, tampered.text
        dropped = tampered.json()["data"]["dropped_claims"]
        assert dropped > 0, "文本被篡改后仍有断言未被丢弃：引用校验只看版本"
    finally:
        with runtime.store._conn() as conn:   # noqa: SLF001
            conn.execute('UPDATE "case" SET phenomenon=? WHERE case_id=?',
                         (original, OPEN_CASE))


# ---------------------------------------------------------------- B1：404 时间侧信道


def test_b1_missing_case_path_does_the_same_work(case_client):
    """不存在路径必须也走 eval_rows + 判定 + 审计（否则耗时差就是存在性预言机）。"""
    token = _session(case_client, "qa", dept="quality")
    runtime = _runtime()
    before = len(runtime.audit.recent(limit=200))
    resp = case_client.get("/api/v2/cases/NO-SUCH-CASE-0001", headers=_auth(token))
    assert resp.status_code == 404, resp.text
    rows_after = runtime.audit.recent(limit=200)
    assert len(rows_after) == before + 1, \
        "不存在路径没有写审计：两条路径工作量不对齐（时间侧信道）"
    latest = rows_after[0]
    assert latest["action"] == "case.read.denied"
    assert not latest.get("case_id")


def test_b1_missing_and_denied_bodies_are_byte_identical(case_client):
    """两种 404 的响应体必须逐字节一致（否则 body 本身就是预言机）。"""
    eq = _session(case_client, "equipment", dept="equipment")
    missing = case_client.get("/api/v2/cases/NO-SUCH-CASE-0002", headers=_auth(eq))
    denied = case_client.get(f"/api/v2/cases/{DEPT_CASE}", headers=_auth(eq))
    assert missing.status_code == denied.status_code == 404
    assert missing.content == denied.content


def test_b1_eval_rows_runs_acl_query_for_missing_case(case_client):
    """结构等价：不存在时也必须真的执行 ``SELECT ... FROM case_acl``。

    只看耗时会漏掉这类问题——机器一忙，比值就抖。判据是**语句模板一致**。
    """
    _session(case_client, "qa", dept="quality")
    runtime = _runtime()
    seen: list[str] = []

    class _Tracer:
        """捕获连接上实际下发的 SQL（不改变任何行为）。"""

        def __init__(self, target: list[str]) -> None:
            self._target = target

        def __call__(self, statement: str) -> None:
            self._target.append(" ".join(statement.split()))

    import sqlite3

    from mfg_agents.common.case_schema import connect

    original_connect = connect

    def _traced(path):
        conn = original_connect(path)
        conn.set_trace_callback(_Tracer(seen))
        return conn

    import mfg_agents.common.case_schema as schema_module
    import mfg_agents.v2.services.case_store as store_module

    store_module.connect = _traced           # type: ignore[assignment]
    try:
        runtime.store.eval_rows("NO-SUCH-CASE-7777")
    finally:
        store_module.connect = original_connect  # type: ignore[assignment]

    joined = "\n".join(seen)
    assert "FROM case_acl" in joined, \
        f"不存在路径没有查 case_acl（结构不等价）：{joined}"
    assert isinstance(sqlite3.Connection, type)   # 保持 import 有意义


# ---------------------------------------------------------------- B2：重复 ingest


def _draft(case_id: str, **over) -> dict:
    draft = {
        "case_id": case_id,
        "title": "重复入库探测：锡膏印刷偏移",
        "phenomenon": "SPI 面积偏差，回流后连锡",
        "root_cause_text": "刮刀压力偏低",
        "countermeasure_text": "恢复压力并加首件确认",
        "source_ticket_type": "8d",
        "source_ticket_id": "8D-2026-9001",
        "defect_mode_code": "DM-SMT-012",
    }
    draft.update(over)
    return draft


def test_b2_reingest_of_active_case_is_rejected(case_client):
    """已发布案例重复 ingest → 409，且索引条数不变（不许静默踢出索引）。"""
    qa = _session(case_client, "qa", dept="quality")   # noqa: F841
    token = _session(case_client, "pqe", dept="quality", max_classification_rank=1)
    runtime = _runtime()
    case_id = "QC-RT-B2-0001"

    created = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                               json={"draft": _draft(case_id)})
    assert created.status_code == 200, created.text
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)
    chunks_before = [c for c in runtime.index_service.index.chunk_ids()
                     if c.startswith(case_id)]
    assert chunks_before, "发布后应当有索引 chunk，测试失效"

    again = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                             json={"draft": _draft(case_id, title="第二次入库")})
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "CASE_ALREADY_ACTIVE"
    assert runtime.store.get_case(case_id)["status"] == "active"
    chunks_after = [c for c in runtime.index_service.index.chunk_ids()
                    if c.startswith(case_id)]
    assert chunks_after == chunks_before, "重复 ingest 把 active 案例的索引改了"


def test_b2_reingest_of_review_case_bumps_version(case_client):
    """review 状态允许覆盖，但必须 bump `content_version`（否则引用三元组骗人）。"""
    token = _session(case_client, "pqe", dept="quality", max_classification_rank=1)
    runtime = _runtime()
    case_id = "QC-RT-B2-0002"

    first = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                             json={"draft": _draft(case_id)})
    assert first.status_code == 200, first.text
    v1 = int(runtime.store.get_case(case_id)["content_version"])

    second = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                              json={"draft": _draft(case_id, title="第二次入库：换了标题")})
    assert second.status_code == 200, second.text
    v2 = int(runtime.store.get_case(case_id)["content_version"])
    assert v2 == v1 + 1, f"覆盖未 bump 版本：{v1} → {v2}"


# ---------------------------------------------------------------- B3：脱敏覆盖


def test_b3_extra_terms_catch_off_vocabulary_person(case_client):
    """词表外的真名（张三丰）必须被 denylist/扩展词表兜住，且不进索引。"""
    from mfg_agents.common.config import settings
    from mfg_agents.v2.services.redaction import (
        get_redaction_service,
        reset_redaction_service,
    )

    saved = settings.CASE_REDACTION_EXTRA_TERMS
    settings.CASE_REDACTION_EXTRA_TERMS = "张三丰"
    reset_redaction_service()
    try:
        token = _session(case_client, "pqe", dept="quality", max_classification_rank=1)
        runtime = _runtime()
        from mfg_agents.v2.services.case_ingest import CaseIngestService

        ingest = CaseIngestService(runtime.store, runtime.index_service,
                                   redaction=get_redaction_service())
        draft = _draft("QC-RT-B3-0001",
                       phenomenon="客户现场联系人张三丰反馈：回流后连锡，电话 13900002222")
        result = ingest.ingest(draft)
        assert "person" in result.redaction_kinds

        case = runtime.store.get_case(result.case_id)
        blob = " ".join(str(case.get(f) or "") for f in
                        ("title", "summary", "phenomenon", "root_cause_text",
                         "countermeasure_text"))
        assert "张三丰" not in blob, f"入库文本里仍有词表外人名：{blob[:120]}"
        assert "<PERSON>" in blob, "扩展词表应替换为 <PERSON> 占位符"
        assert "13900002222" not in blob

        # 发布后索引里同样干净
        runtime.store.transition(result.case_id, "active")
        runtime.index_service.sync_case(result.case_id)
        texts = "\n".join(runtime.index_service.index.texts())
        assert "张三丰" not in texts, "索引文本里仍有词表外人名"
    finally:
        settings.CASE_REDACTION_EXTRA_TERMS = saved
        reset_redaction_service()


def test_b3_seed_import_runs_through_redaction(case_client):
    """种子导入必须走脱敏管道：直接塞一个真名进种子，导入后库里不能有它。"""
    import json
    from pathlib import Path

    token = _session(case_client, "qa", dept="quality")   # noqa: F841
    runtime = _runtime()
    seed = {
        "tenant": "T001",
        "cases": [{
            "case_id": "QC-RT-B3-SEED",
            "title": "种子脱敏探测",
            "summary": "联系人 王建国 反馈锡膏偏移",
            "phenomenon": "华辰电子 产线 连锡",
            "root_cause_text": "刮刀压力偏低",
            "countermeasure_text": "恢复压力",
            "category": "quality_case", "subcategory": "process_defect",
            "cls_rank": 0, "status": "active",
            "source_ticket_type": "8d", "source_ticket_id": "8D-2026-9100",
            "acl": [],
        }],
    }
    path = Path(runtime.store.db_path).parent / "seed_b3.json"
    path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    runtime.store.seed_from_json(path)
    case = runtime.store.get_case("QC-RT-B3-SEED")
    blob = f"{case['summary']} {case['phenomenon']}"
    assert "王建国" not in blob and "华辰电子" not in blob, \
        f"种子绕过脱敏直接写库：{blob}"


# ---------------------------------------------------------------- B4：ACL 重算可见性


def test_b4_set_acl_recomputes_visibility_class(case_client):
    """vc=0（全员开放）的案例一旦被加上业务 ACL，vc 必须变 1，且别人立刻查不到。"""
    token = _session(case_client, "qa", dept="quality")   # noqa: F841
    runtime = _runtime()
    case_id = "QC-RT-B4-0001"
    runtime.store.create_case({
        "case_id": case_id, "case_code": case_id, "title": "委外转质量域",
        "summary": "锡膏印刷偏移的处置经验", "phenomenon": "连锡",
        "root_cause_text": "刮刀压力偏低", "countermeasure_text": "恢复压力",
        "category": "quality_case", "subcategory": "process_defect",
        "cls_rank": 1, "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-2026-9200",
    })
    runtime.store.set_visibility(case_id, 0, [])
    assert int(runtime.store.get_case(case_id)["visibility_class"]) == 0

    runtime.store.set_acl(case_id, [{"dim": "dept", "value": "quality"}],
                          allow_widen=True)
    assert int(runtime.store.get_case(case_id)["visibility_class"]) == 1, \
        "改了 ACL 却没重算 visibility_class：快路径会误放行"

    runtime.store.transition(case_id, "review")
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    eq = _session(case_client, "equipment", dept="equipment")
    detail = case_client.get(f"/api/v2/cases/{case_id}", headers=_auth(eq))
    assert detail.status_code == 404, "加 ACL 后设备工程师仍能看到案例"

    qa = _session(case_client, "qa", dept="quality")
    assert case_client.get(f"/api/v2/cases/{case_id}",
                           headers=_auth(qa)).status_code == 200


# ---------------------------------------------------------------- B5：演示态告警


def test_b5_meta_exposes_demo_warnings(case_client):
    """AUTH_SECRET 未配置必须出现在 meta 里（前端黄字），不能只写日志。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.get("/api/v2/cases/meta", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["data"]["demo_warnings"]
    assert isinstance(warnings, list) and warnings
    assert any("AUTH_SECRET" in w for w in warnings)
    assert any("CASE_DEMO_MODE" in w for w in warnings)


def test_b5_session_returns_503_when_demo_disabled(case_client):
    """演示开关关闭 → 503 `CASE_DEMO_DISABLED`（不是 403，避免被当成权限问题）。"""
    from mfg_agents.common.config import settings

    saved = settings.CASE_DEMO_MODE
    settings.CASE_DEMO_MODE = False
    try:
        resp = case_client.post("/api/v2/cases/session",
                                json={"role": "qa", "dept": "quality"})
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["code"] == "CASE_DEMO_DISABLED"
    finally:
        settings.CASE_DEMO_MODE = saved


# ---------------------------------------------------------------- 越权不变式


@pytest.mark.parametrize("case_id", [DEPT_CASE, "QC-RT-NOT-EXIST-9999"])
def test_unknown_case_and_denied_case_share_status_code(case_client, case_id):
    """存在但无权 / 根本不存在 → 同状态码同 body（404 语义不能被 R1 改坏）。"""
    eq = _session(case_client, "equipment", dept="equipment")
    resp = case_client.get(f"/api/v2/cases/{case_id}", headers=_auth(eq))
    assert resp.status_code == 404, f"{case_id} 对该身份可见，用例前提不成立：{resp.text}"
    assert resp.json()["detail"]["code"] == "CASE_NOT_FOUND_OR_NOT_VISIBLE"
