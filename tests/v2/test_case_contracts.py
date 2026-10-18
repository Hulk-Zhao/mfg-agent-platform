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


def _search(client, token: str, query: str = "锡膏印刷偏移 连锡", k: int = 200):
    # k 取足够大（种子扩到 70 例后可见 chunk 已 >120），让"候选不足"路径
    # （hidden_count / notice）在 HTTP 层仍可被触发；与 top-k 截断无关。
    return client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "action": "query", "k": k})


def _search_payload(client, token: str, query: str = "案例", k: int = 50) -> dict:
    resp = client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "action": "query", "k": k})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


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


def test_b5_meta_exposes_demo_warnings(case_client, monkeypatch):
    """AUTH_SECRET 未配置必须出现在 meta 里（前端黄字），不能只写日志。

    `monkeypatch.setattr` 把 AUTH_SECRET 清空：别的用例（数据范围/令牌重签）
    可能在同进程里改过这个全局设置，本用例必须自证，不依赖执行顺序。
    """
    from mfg_agents.common.config import settings

    monkeypatch.setattr(settings, "AUTH_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "CASE_DEMO_MODE", True, raising=False)
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


# ---------------------------------------------------------------- 批量优化与状态守护


def test_batch_reads_match_per_case_reads(case_client):
    """批量接口与逐条接口必须**逐字段一致**（优化不能改判定结果）。

    背景：`_restricted()` 改成批量取案例与 ACL 行（一次连接两条查询）后，
    性能从"每次检索上百次建连"降到两次。如果批量与逐条有任何字段差异，
    受限清单就会和真实 ACL 不一致——那是安全结论出错，不是性能问题。
    """
    _session(case_client, "qa", dept="quality")
    runtime = _runtime()
    ids = [str(row["case_id"]) for row in runtime.store.list_cases()]
    assert len(ids) >= 5, "案例太少，测试失效"

    batch_rows = runtime.store.eval_rows_many(ids)
    for case_id in ids:
        assert [dict(r) for r in batch_rows.get(case_id, [])] == \
            runtime.store.eval_rows(case_id), f"{case_id} 的批量 ACL 行与逐条不一致"

    batch_cases = runtime.store.cases_by_ids(ids)
    for case_id in ids:
        assert dict(batch_cases.get(case_id) or {}) == \
            (runtime.store.get_case(case_id) or {}), \
            f"{case_id} 的批量案例行与逐条不一致"


def test_restricted_matches_per_case_recomputation(case_client):
    """受限清单 = 硬维度候选里**可披露存在且当前主体不可见**的那些。

    "独立"指的是**独立于批量实现**：这条用例逐条调 ``eval_rows`` + ``authorize``，
    与生产侧 ``_restricted()`` 用的 ``eval_rows_many`` 路径不同，因此能证明
    批量优化没有改变判定结果。硬维度那一段仍复用生产同一个
    ``compile_hard_only``（刻意如此：它就是 B10 的根因所在——硬维度的
    ``cls_rank ≤ max`` 对 rank-3 主体是放行的，所以**披露性必须单独判**）。

    B10 修复后：涉密 / 未结案 / 已撤权案例连 ``case_id`` 都不进清单。
    """
    from mfg_agents.v2.security.acl_compiler import get_acl_compiler
    from mfg_agents.v2.security.acl_evaluator import get_acl_evaluator
    from mfg_agents.v2.services.case_search import _is_disclosable

    token = _session(case_client, "qa", dept="quality")
    runtime = _runtime()
    principal = runtime.resolver.from_token(token)

    data = _search_payload(case_client, token)
    visible = {h["case_id"] for h in data["hits"]}

    index = runtime.index_service.index
    hard = get_acl_compiler().compile_hard_only(principal, "query")
    evaluator = get_acl_evaluator()
    expected = set()
    for hit in index.search("", hard, 100000):
        case_id = str(hit.case_id)
        if case_id in visible or case_id in expected:
            continue
        case = runtime.store.get_case(case_id)
        if not case or not _is_disclosable(case):
            continue          # B10：不可披露的，连 case_id 都不给
        if evaluator.authorize(principal, runtime.store.eval_rows(case_id),
                               "query").allow:
            continue
        expected.add(case_id)

    got = {item["case_id"] for item in data["restricted"]}
    assert got <= expected, f"受限清单多出了不该出现的案例：{got - expected}"
    assert expected <= got, f"受限清单漏掉了应出现的案例：{expected - got}"
    assert all(item.get("requestable") is True for item in data["restricted"]), \
        "受限清单只应收录可申请的案例（标记不构成过滤）"


def test_global_state_is_not_mutated_by_case_flows(case_client):
    """守护用例：跑完检索/问答/引用后，全局配置与单例不得被改动。

    为什么需要这条：曾经出现"单跑全绿、全量一片红"的现象，本质是**测试污染**
    （某个模块改了 `settings` 或单例，后续模块读到被改过的值）。这类故障不会
    在被污染的那个模块里报错，只会表现为下游莫名其妙地红，极难定位。
    这条用例把"跑完之后世界没变"变成可断言的事实。
    """
    from mfg_agents.common.config import settings
    from mfg_agents.v2.contracts.ontology import get_ontology_registry
    from mfg_agents.v2.security.acl_compiler import get_acl_compiler
    from mfg_agents.v2.security.acl_evaluator import get_acl_evaluator
    from mfg_agents.v2.security.case_rbac import get_case_rbac

    watched = ("CASE_DB", "CASE_ENABLED", "CASE_DEMO_MODE", "CASE_VECTOR_BACKEND",
               "CASE_EXPOSE_HIDDEN_COUNT", "CASE_RERANK", "CASE_SEED_PATH",
               "CASE_DEFAULT_TENANT", "CASE_K_DEFAULT", "CASE_K_MAX",
               "CASE_REDACTION_EXTRA_TERMS", "CASE_ACCESS_GRANT_TTL_DAYS",
               "AUTH_SECRET")
    before = {name: getattr(settings, name, None) for name in watched}
    singles_before = {
        "compiler_policy": id(get_acl_compiler().policy),
        "evaluator": id(get_acl_evaluator()),
        "rbac": id(get_case_rbac()),
        "ontology": id(get_ontology_registry()),
    }

    token = _session(case_client, "qa", dept="quality")
    _search(case_client, token)
    case_client.post("/api/v2/cases/ask", headers=_auth(token),
                     json={"question": "锡膏印刷偏移怎么处置", "k": 5})
    case_client.post(f"/api/v2/cases/{OPEN_CASE}/cite", headers=_auth(token),
                     json={"chunk_type": "summary"})

    after = {name: getattr(settings, name, None) for name in watched}
    changed = {k: (before[k], after[k]) for k in watched if before[k] != after[k]}
    assert not changed, f"案例链路改写了全局配置：{changed}"

    singles_after = {
        "compiler_policy": id(get_acl_compiler().policy),
        "evaluator": id(get_acl_evaluator()),
        "rbac": id(get_case_rbac()),
        "ontology": id(get_ontology_registry()),
    }
    assert singles_after == singles_before, \
        f"案例链路替换了全局单例：{singles_after} != {singles_before}"


# ---------------------------------------------------------------- C2：拒答阈值标定


#: 无关问句（现场真会问的外行问题）——必须**全部拒答**
IRRELEVANT_QUESTIONS: tuple[str, ...] = (
    "食堂几点开饭", "今天午饭吃什么", "明天股市会涨吗", "现在外面下雨吗",
    "周末去哪儿玩比较好", "帮我写一首诗", "最近的油价是多少",
    "孩子放学几点回家", "附近有什么好吃的",
)

#: 相关问句（制造域真实提问）——必须**全部作答**且引用合法
RELEVANT_QUESTIONS: tuple[str, ...] = (
    "锡膏印刷偏移连锡怎么处理", "回流焊后焊点空洞率高怎么办",
    "波峰焊润湿角不足怎么改善", "注塑件缩水率波动怎么控制",
    "钢网开孔堵塞导致连锡", "虚焊和空焊有什么区别",
    "焊接后引脚连锡的根因", "锡膏黏度波动引发印刷拖影",
)


@pytest.mark.parametrize("question", IRRELEVANT_QUESTIONS)
def test_c2_irrelevant_questions_are_refused(case_client, question):
    """C2 标定结果钉死：无关问句**必须全部拒答**。

    背景：语料从 15 例扩到 99 例后，demo 级字符重合度的分数分布整体上移，
    绝对阈值 0.18 失效——「食堂几点开饭」拿到 0.205 后被煞有介事地答了一遍
    （引用合法、内容无关）。现在闸门是"绝对下限 + 相对余量"双条件（OR），
    阈值由 ``tools/calibrate_answer_thresholds.py`` 标定。

    ⚠️ 换真实 embedding 后这套阈值会全废，必须重跑标定脚本并同步本用例。
    """
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                            json={"question": question, "k": 10})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["answered"] is False, (
        f"无关问题「{question}」被作答了（score 分布漂移，需重跑标定）："
        f"{(data.get('answer') or '')[:60]}")
    assert "拒绝作答" in (data.get("reason") or "")


@pytest.mark.parametrize("question", RELEVANT_QUESTIONS)
def test_c2_relevant_questions_are_answered(case_client, question):
    """相关问句必须全部作答，且每条结论都挂得上引用（拒答优先也不能拒真问题）。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                            json={"question": question, "k": 10})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["answered"] is True, (
        f"相关问题「{question}」被拒答了：{data.get('reason')}")
    assert data["citations"], "作答但没有任何引用"
    for claim in data["claims"]:
        assert claim["citations"], "存在无引用的结论"
        for cite in claim["citations"]:
            assert cite["case_id"] and cite["chunk_id"] and \
                int(cite["content_version"]) >= 1


def test_unregistered_person_scanner_flags_but_never_redacts():
    """兜底扫描器：**要能报出未登记人名，也绝不能误伤技术术语**。

    背景（QA 裁决②）：脱敏是 denylist，只能防**已知**名字；新名字第一次出现
    必然先漏一次，且**失效是静默的**。所以补一条"疑似人名扫描"，
    但**只产待办、不自动脱敏**——弱规则误伤会把正常技术表述替换成 `<PERSON>`，
    污染知识层比漏一个名字严重得多。

    这条用例把两件事都钉住：**能抓到**（联系人张三丰）+ **不误伤**（张紧并/间隙补偿）。
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "scan_unregistered_person_names",
        pathlib.Path(__file__).resolve().parents[2]
        / "tools" / "scan_unregistered_person_names.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)

    texts = {
        "leak.phenomenon": "客户现场联系人张三丰反馈回流后连锡，处理人李建国已到现场。",
        "tech.summary": "工序由张紧并缩短清洗周期后恢复，间隙补偿到位，温度未变。",
    }
    findings = scanner.scan(texts)
    names = {item["name"] for item in findings}
    assert "张三丰" in names, f"未登记人名没被抓到：{findings}"
    assert "李建国" in names, f"'处理人李建国'这种上下文没被抓到：{findings}"
    for noise in ("张紧并", "间隙补", "温度未"):
        assert not any(noise in name for name in names), \
            f"技术术语被误判成人名：{sorted(names)}"
    # 只产待办：扫描**不得**修改任何文本
    assert texts["leak.phenomenon"].startswith("客户现场联系人张三丰"), "扫描器改了输入文本"
