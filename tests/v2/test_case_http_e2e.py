"""案例助手 · 端到端 HTTP 流程测试。

覆盖一条完整的产品链路（不是服务层拼装）：
    选岗登录 → 元信息 → 检索 → 详情 → 引用 → 问答 → 入库 → 审计校验

断言的落点是"用户能观察到的事实"：
- 同一句话，质量与设备拿到的结果**集合不同**（差异来自授权，不是没数据）；
- 候选不足时如实返回 + 说明不放宽，``hidden_count`` 给出不可见条数；
- 引用返回可复现的三元组 (case_id, chunk_id, content_version)；
- 问答要么每条结论带引用，要么明确拒答（含"不够相关"的拒答）；
- 入库必然落 ``review`` 且**正文已脱敏**，在发布前检索不到。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REDACTION_RULES = _PROJECT_ROOT / "src" / "mfg_agents" / "v2" / "data" / "redaction_rules.yaml"

OPEN_CASE = "QC-CC-2026-0087"          # 全员开放
DEPT_CASE = "QC-PD-2026-0055"          # 仅 dept:quality
DEPRECATED_CASE = "QC-PD-2025-0008"    # 待复核（可见但降权）
PII_CASE = "QC-CC-2026-0110"           # 含客户/供应商（入库时已脱敏）


def _session(client, role: str, dept: str = "", plant: str = "P1",
             employment_type: str = "employee", user_id: str = "") -> str:
    body: dict[str, object] = {
        "role": role, "dept": dept, "plant": plant,
        "employment_type": employment_type, "name": f"{role}-e2e",
    }
    if user_id:
        body["user_id"] = user_id
    resp = client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _search(client, token: str, query: str, k: int = 10, action: str = "query"):
    return client.post("/api/v2/cases/search", headers=_auth(token),
                       json={"query": query, "k": k, "action": action})


def _unique(ids):
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- 完整链路


def test_full_flow_session_meta_search_detail_cite_audit(case_client):
    token = _session(case_client, "qa", dept="quality", user_id="u-flow")

    meta = case_client.get("/api/v2/cases/meta", headers=_auth(token))
    assert meta.status_code == 200, meta.text
    meta_data = meta.json()["data"]
    assert meta_data["principal"]["roles"] == ["qa"]
    assert "role:qa" in meta_data["principal"]["principal_tokens"]
    assert meta_data["health"]["ready"] is True
    assert meta_data["health"]["backend"] == "memory"

    hits = _search(case_client, token, "锡膏印刷偏移 虚焊 连锡", k=10)
    assert hits.status_code == 200, hits.text
    data = hits.json()["data"]
    assert data["hits"], "质量工程师应能看到开放质量案例"
    assert data["filter_hash"] and data["query_hash"]
    top = data["hits"][0]
    assert top["citation"] == {
        "case_id": top["case_id"],
        "chunk_id": top["chunk_id"],
        "content_version": top["content_version"],
    }

    detail = case_client.get(f"/api/v2/cases/{top['case_id']}", headers=_auth(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["case_id"] == top["case_id"]
    assert body["phenomenon"] or body["summary"]

    cited = case_client.post(f"/api/v2/cases/{top['case_id']}/cite", headers=_auth(token),
                             json={"target_ticket_type": "8d",
                                   "target_ticket_id": "8D-E2E-0001"})
    assert cited.status_code == 200, cited.text
    citation = cited.json()["data"]["citation"]
    assert citation["case_id"] == top["case_id"]
    assert citation["chunk_id"].startswith(f"{top['case_id']}::")
    assert int(citation["content_version"]) == int(top["content_version"])
    assert cited.json()["data"]["layer"] == "knowledge"
    chain = case_client.get("/api/v2/cases/audit/verify", headers=_auth(token))
    assert chain.status_code == 200, chain.text
    audit = chain.json()["data"]
    assert audit["chain"]["valid"] is True
    actions = [row["action"] for row in audit["recent"]]
    assert "case.query" in actions
    assert "case.cite" in actions


def test_meta_exposes_action_matrix_and_vocabulary(case_client):
    token = _session(case_client, "qa", dept="quality", user_id="u-meta")
    data = case_client.get("/api/v2/cases/meta", headers=_auth(token)).json()["data"]

    codes = {item["key"] for item in data["rbac"]["actions"]}
    assert codes == {"case.query", "case.preview", "case.read", "case.cite",
                     "case.export", "case.share", "case.ingest"}

    qa_grant = [r for r in data["rbac"]["roles"] if r["key"] == "qa"]
    assert qa_grant
    assert qa_grant[0]["actions"] == ["case.query", "case.preview",
                                      "case.read", "case.cite"]

    vocab = data["ontology"]
    assert set(vocab) == {"defect_mode", "root_cause", "countermeasure"}
    assert len(vocab["defect_mode"]) >= 20
    assert all("code" in term and "name" in term for term in vocab["defect_mode"])
    assert data["ontology_count"] == sum(len(v) for v in vocab.values())


# ---------------------------------------------------------------- 差异化检索


def test_same_query_different_roles_get_different_sets(case_client):
    """同一 query、同一索引：质量看得到部门域案例，设备看得到同一批开放案例。"""
    query = "注塑件缩水 工艺参数"
    quality = _unique([h["case_id"] for h in
                       _search(case_client, _session(case_client, "qa", dept="quality"),
                               query, k=20).json()["data"]["hits"]])
    equipment = _unique([h["case_id"] for h in
                         _search(case_client, _session(case_client, "equipment", dept="equipment"),
                                 query, k=20).json()["data"]["hits"]])

    assert DEPT_CASE in quality, "质量工程师应命中本部门案例"
    assert DEPT_CASE not in equipment, "设备工程师不得命中质量部门域案例"
    assert set(quality) != set(equipment), "两个岗位的结果集必须不同"


def test_hidden_count_and_notice_when_result_is_short(case_client):
    """候选不足：如实返回 + 明确说明不放宽 + 给出不可见条数。"""
    token = _session(case_client, "equipment", dept="equipment")
    data = _search(case_client, token, "锡膏印刷偏移 虚焊 连锡", k=20).json()["data"]

    assert len(data["hits"]) < 20, "设备工程师不应拿满 20 条（否则此用例失去意义）"
    assert data["hidden_count"] > 0
    assert "另有" in data["notice"] and "不可见" in data["notice"]
    assert "不放宽过滤补齐" in data["notice"]
    assert data["degraded"] is False


def test_deprecated_case_is_visible_with_status_badge(case_client):
    """待复核案例仍可见（不是删除），但状态徽标必须是"待复核"。"""
    token = _session(case_client, "qa", dept="quality")
    data = _search(case_client, token, "助焊剂残留 焊点发黑", k=10).json()["data"]
    hit = next((h for h in data["hits"] if h["case_id"] == DEPRECATED_CASE), None)
    assert hit is not None, "待复核案例应仍可被检索到（降权而非下线）"
    assert hit["status"] == "deprecated"
    # 徽标文案由服务端给出（前端不自造），允许带降权说明
    assert hit["status_badge"].startswith("待复核")


def test_search_respects_plant_dimension(case_client):
    """工厂维度独立生效：P1 主体搜不到 P2 专属案例，P2 主体能搜到。"""
    query = "供应商五金件尺寸超差"
    p1 = _unique([h["case_id"] for h in
                  _search(case_client, _session(case_client, "qa", dept="quality", plant="P1"),
                          query, k=20).json()["data"]["hits"]])
    p2 = _unique([h["case_id"] for h in
                  _search(case_client, _session(case_client, "qa", dept="quality", plant="P2"),
                          query, k=20).json()["data"]["hits"]])
    assert "QC-MD-2026-0021" not in p1
    assert "QC-MD-2026-0021" in p2


# ---------------------------------------------------------------- 问答


def test_ask_returns_citations_for_every_claim(case_client):
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                            json={"question": "锡膏印刷偏移怎么处置", "k": 3, "action": "read"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["answered"] is True
    assert data["claims"], "应给出至少一条结论"
    for claim in data["claims"]:
        assert claim["citations"], "无引用的结论必须被丢弃"
        citation = claim["citations"][0]
        assert citation["case_id"] and citation["chunk_id"]
        assert int(citation["content_version"]) >= 1
    assert data["dropped_claims"] == 0
    assert data["search"]["hits"], "问答必须基于检索结果，不是凭空生成"


def test_ask_refuses_when_nothing_is_relevant(case_client):
    """无关问题必须拒答——引用合法 ≠ 答的是这个问题。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                            json={"question": "食堂几点开饭", "k": 3, "action": "read"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["answered"] is False
    assert "低于阈值" in data["reason"]
    assert data["claims"] == []
    assert data["citations"] == []


def test_ask_refuses_for_role_without_read_permission(case_client):
    """外协人员读不到部门域案例：问答只能基于开放案例作答或拒答。"""
    token = _session(case_client, "manufacturing", dept="production",
                     employment_type="outsource")
    resp = case_client.post("/api/v2/cases/ask", headers=_auth(token),
                            json={"question": "注塑件缩水怎么调", "k": 3, "action": "read"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    if data["answered"]:
        for claim in data["claims"]:
            assert claim["citations"]
            for citation in claim["citations"]:
                assert citation["case_id"] != DEPT_CASE
    else:
        assert data["reason"]


# ---------------------------------------------------------------- 入库


def test_ingest_writes_redacted_review_case(case_client):
    """入库：来源必填 → 正文脱敏固化 → 落 review → 发布前检索不到。"""
    rules = yaml.safe_load(_REDACTION_RULES.read_text(encoding="utf-8"))
    customers = next(r for r in rules["rules"] if r["id"] == "customer-whitelist")
    raw_customer = customers["values"][0]

    token = _session(case_client, "quality_director", dept="quality", user_id="u-ingest")
    draft = {
        "title": f"客诉：{raw_customer} 反馈回流连锡",
        "phenomenon": f"{raw_customer} 产线反馈锡膏印刷偏移并连锡，联系人 王建国 13800001111",
        "root_cause_text": "刮刀压力偏低且回温不足",
        "countermeasure_text": "恢复压力 6kg 并强制回温 4h",
        "source_ticket_type": "8d",
        "source_ticket_id": "8D-2026-7777",
        "defect_mode_code": "DM-SMT-012",
    }
    resp = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                            json={"draft": draft})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["status"] == "review", "入库不得自动发布"
    assert set(data["redaction_kinds"]) & {"customer", "person", "phone"}
    assert "HITL" in data["next_step"]
    new_id = data["case_id"]
    assert new_id

    # 发布前：不进索引（检索不到），详情也 404
    found = _unique([h["case_id"] for h in
                     _search(case_client, token, f"{raw_customer} 连锡", k=20)
                     .json()["data"]["hits"]])
    assert new_id not in found
    assert case_client.get(f"/api/v2/cases/{new_id}",
                           headers=_auth(token)).status_code == 404


def test_ingest_rejects_draft_without_source_ticket(case_client):
    token = _session(case_client, "quality_director", dept="quality")
    resp = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                            json={"draft": {"title": "无来源草稿", "phenomenon": "测试"}})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "CASE_SOURCE_TICKET_REQUIRED"


# ---------------------------------------------------------------- 脱敏原文


def test_detail_text_is_redacted_not_masked_at_query_time(case_client):
    """脱敏在入库时固化：库里/响应里都没有原始客户名，只有占位符。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.get(f"/api/v2/cases/{PII_CASE}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]

    rules = yaml.safe_load(_REDACTION_RULES.read_text(encoding="utf-8"))
    literals = [str(v) for rule in rules["rules"]
                if rule.get("type") == "literal" for v in rule.get("values") or []]
    text = " ".join(str(body.get(key) or "") for key in
                    ("summary", "phenomenon", "root_cause_text", "countermeasure_text"))
    assert text.strip(), "详情正文为空，测试失效"
    leaked = [v for v in literals if v in text]
    assert not leaked, f"详情里出现未脱敏原文：{leaked}"
    assert "客户甲" in text or "某企业" in text


# ---------------------------------------------------------------- 引用三元组（可溯源）


def test_cite_honours_clicked_chunk_type(case_client):
    """引用必须指向用户点的那一段，而不是"总是 summary"。

    这是"可溯源"的底线：卡片上写着 ``phenomenon``，引用三元组却回 ``summary``，
    复核时就对不上原始内容，引用等于没有。
    """
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post("/api/v2/cases/search", headers=_auth(token),
                            json={"query": "锡膏印刷偏移 连锡", "action": "query", "k": 25})
    assert resp.status_code == 200, resp.text
    hits = resp.json()["data"]["hits"]
    target = next((h for h in hits if h.get("chunk_type") == "phenomenon"), hits[0])
    assert target.get("chunk_type"), "命中项缺少 chunk_type，测试失效"

    cited = case_client.post(
        f"/api/v2/cases/{target['case_id']}/cite", headers=_auth(token),
        json={"target_ticket_type": "8d", "target_ticket_id": "8D-E2E-0002",
              "chunk_type": target["chunk_type"],
              "content_version": int(target["content_version"])})
    assert cited.status_code == 200, cited.text
    data = cited.json()["data"]
    assert data["citation"]["chunk_id"] == f"{target['case_id']}::{target['chunk_type']}"
    assert data["chunk_type"] == target["chunk_type"]
    assert int(data["citation"]["content_version"]) == int(target["content_version"])


def test_cite_returns_source_ticket_for_traceability(case_client):
    """引用回执要带**来源单据**：从案例能对回 8D/客诉单，才叫溯源。"""
    token = _session(case_client, "qa", dept="quality")
    cited = case_client.post(f"/api/v2/cases/{OPEN_CASE}/cite", headers=_auth(token),
                             json={"target_ticket_type": "8d",
                                   "target_ticket_id": "8D-E2E-0003"})
    assert cited.status_code == 200, cited.text
    source = cited.json()["data"]["source_ticket"]
    assert source["ticket_type"], "来源单据类型为空"
    assert source["ticket_id"], "来源单据号为空（引用无法溯源）"


def test_cite_rejects_stale_content_version(case_client):
    """内容版本已变 → 409，不返回"看起来对"的旧引用。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post(f"/api/v2/cases/{OPEN_CASE}/cite", headers=_auth(token),
                            json={"chunk_type": "summary", "content_version": 999})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "CASE_CITATION_STALE"


def test_cite_rejects_unknown_chunk_type(case_client):
    """非法片段类型是 4xx（不是 500），且错误码可判别。"""
    token = _session(case_client, "qa", dept="quality")
    resp = case_client.post(f"/api/v2/cases/{OPEN_CASE}/cite", headers=_auth(token),
                            json={"chunk_type": "../../evidence"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "CASE_CITE_INVALID_CHUNK"
