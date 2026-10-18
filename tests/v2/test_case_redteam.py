"""案例助手 · 红队测试（HTTP 层）。

为什么必须打接口而不是只测服务层：真正会被攻击的是**网络边界**。
本文件守四类事情：

1. **身份只能来自令牌**——请求体里塞 ``roles`` / ``dept`` / ``tenant`` / ``filter``
   一律无效（请求模型根本没有这些字段），且产出的 ``filter_hash`` 与干净请求逐字节相同；
2. **越权一律查不到**——不是"查到了再过滤"，也不是"403 告诉你没权限"，而是
   根本进不了候选集；详情接口对"无权"与"不存在"返回**逐字节相同**的 404；
3. **不泄漏内部结构**——响应里不出现 ``acl_tokens`` / ``visibility_class`` /
   ``is_knowledge_layer`` 等索引内部字段，也不出现维度令牌 ``dept:quality``；
4. **审计不是第二个泄漏面**——只落哈希与计数，查询原文与正文都不落库。

真值表（``_VISIBILITY``）是刻意写死的：授权一旦被人改松，测试必须红，
由人来确认那是一次产品决策，而不是一次手滑。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REDACTION_RULES = _PROJECT_ROOT / "src" / "mfg_agents" / "v2" / "data" / "redaction_rules.yaml"

#: 索引内部字段：绝不能出现在对外响应里（出现即等于把 ACL 结构送给前端）
_FORBIDDEN_KEYS = {
    "acl_tokens", "acl_version", "visibility_class", "is_knowledge_layer",
    "chunk_hash", "embedding", "vector", "acl_entries",
}

#: 权限真值表：case_id → {主体标签: 是否可读}
#: 主体标签见 ``_SUBJECTS``。这张表就是产品口径，改它需要评审。
_VISIBILITY: dict[str, dict[str, bool]] = {
    # 全员开放的质量案例
    "QC-CC-2026-0087": {"qa": True, "equipment": True, "gm": True,
                        "qa_p2": True, "qd": True, "outsource": True},
    # 部门域案例：只有 dept=quality 能看到（跨工厂仍可见——各维度独立判定）
    "QC-PD-2026-0055": {"qa": True, "equipment": False, "gm": False,
                        "qa_p2": True, "qd": True, "outsource": False},
    # 工厂域案例：只有 plant=P2 能看到
    "QC-MD-2026-0021": {"qa": False, "equipment": False, "gm": False,
                        "qa_p2": True, "qd": False, "outsource": False},
    # 岗位经验案例（质量域）
    "RC-QA-2026-0009": {"qa": True, "equipment": False, "gm": False,
                        "qa_p2": True, "qd": True, "outsource": False},
    # 审核中：不进共享池（BR-06）
    "QC-CC-2026-0115": {"qa": False, "equipment": False, "gm": False,
                        "qa_p2": False, "qd": False, "outsource": False},
    # 密级=secret：任何人不可见，需专门通道（E1）
    "QC-PD-2026-0099": {"qa": False, "equipment": False, "gm": False,
                        "qa_p2": False, "qd": False, "outsource": False},
    # 待复核：可见但降权（状态权重 .4）
    "QC-PD-2025-0008": {"qa": True, "equipment": True, "gm": True,
                        "qa_p2": True, "qd": True, "outsource": True},
}

_SUBJECTS: dict[str, dict[str, object]] = {
    "qa": {"role": "qa", "dept": "quality", "plant": "P1"},
    "equipment": {"role": "equipment", "dept": "equipment", "plant": "P1"},
    "gm": {"role": "gm", "dept": "management", "plant": "P1"},
    "qa_p2": {"role": "qa", "dept": "quality", "plant": "P2"},
    "qd": {"role": "quality_director", "dept": "quality", "plant": "P1"},
    "outsource": {"role": "manufacturing", "dept": "production", "plant": "P1",
                  "employment_type": "outsource"},
}


# ---------------------------------------------------------------- 工具


def _session(client, role: str, dept: str = "", plant: str = "P1",
             employment_type: str = "employee", user_id: str = "",
             max_classification_rank: int = 1) -> str:
    """选岗登录 → 案例令牌。角色未识别时接口返回 400（不签发）。"""
    body: dict[str, object] = {
        "role": role, "dept": dept, "plant": plant,
        "employment_type": employment_type,
        "max_classification_rank": max_classification_rank,
        "name": f"{role}-测试",
    }
    if user_id:
        body["user_id"] = user_id
    resp = client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _token(client, label: str) -> str:
    spec = dict(_SUBJECTS[label])
    return _session(client, **spec)  # type: ignore[arg-type]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _search(client, token: str, query: str = "虚焊", k: int = 10,
            action: str = "query", extra: dict | None = None):
    body: dict[str, object] = {"query": query, "k": k, "action": action}
    if extra:
        body.update(extra)
    return client.post("/api/v2/cases/search", json=body, headers=_auth(token))


def _ids(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [h["case_id"] for h in resp.json()["data"]["hits"]]


def _walk_keys(node, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(str(key))
            _walk_keys(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_keys(item, found)


def _walk_items(node, found: list[tuple[str, object]] | None = None,
                parent: str = "") -> list[tuple[str, object]]:
    """摊平 ``(键, 值)`` 对，用于"这个字段的值是否合法出现"这类断言。"""
    out = found if found is not None else []
    if isinstance(node, dict):
        for key, value in node.items():
            out.append((str(key), value))
            _walk_items(value, out, str(key))
    elif isinstance(node, list):
        for item in node:
            _walk_items(item, out, parent)
    return out


def _literal_pii() -> list[str]:
    """从脱敏规则文件里取 literal 词表（不硬编码：词表改了测试自动跟上）。"""
    data = yaml.safe_load(_REDACTION_RULES.read_text(encoding="utf-8"))
    values: list[str] = []
    for rule in data.get("rules") or []:
        if rule.get("type") == "literal":
            values.extend(str(v) for v in rule.get("values") or [])
    return [v for v in values if v]


# ---------------------------------------------------------------- 1. 身份边界


@pytest.mark.parametrize(("method", "path", "body"), [
    ("post", "/api/v2/cases/search", {"query": "虚焊", "k": 5}),
    ("post", "/api/v2/cases/ask", {"question": "虚焊怎么处置", "k": 5}),
    ("post", "/api/v2/cases/ingest", {"draft": {}}),
    ("get", "/api/v2/cases/meta", None),
    ("get", "/api/v2/cases/QC-CC-2026-0087", None),
    ("get", "/api/v2/cases/audit/verify", None),
])
def test_every_case_endpoint_is_fail_closed_without_token(case_client, method, path, body):
    """无令牌一律 401，且错误码统一（不因端点不同而给出不同"线索"）。"""
    resp = getattr(case_client, method)(path, json=body) if body is not None \
        else getattr(case_client, method)(path)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["code"] == "CASE_TOKEN_MISSING"


def test_portal_token_version_1_is_rejected(case_client):
    """门户令牌（tv=1，无维度）在案例域必须整体失效，不允许"部分可用"。"""
    portal = case_client.post("/api/v2/auth/login", json={"roles": ["qa"]})
    assert portal.status_code == 200, portal.text
    token = portal.json()["data"]["token"]

    resp = _search(case_client, token)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["code"] == "CASE_TOKEN_SCHEMA_LEGACY"


def test_tampered_token_signature_is_rejected(case_client):
    """改一个字符就必须验签失败（篡改 dept 等于篡改载荷）。"""
    token = _token(case_client, "qa")
    body, _, signature = token.rpartition(".")
    tampered = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + signature
    resp = _search(case_client, tampered)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["code"] in {"CASE_TOKEN_INVALID", "CASE_TOKEN_SCHEMA_LEGACY"}


@pytest.mark.parametrize("role", ["superadmin", "quality_engineer", "god", ""])
def test_unknown_role_cannot_mint_token(case_client, role):
    """角色必须能被权限注册表归一化；未知角色不发令牌（不是"发个空令牌"）。"""
    resp = case_client.post("/api/v2/cases/session",
                            json={"role": role, "dept": "quality", "plant": "P1"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "CASE_TOKEN_INVALID"


def test_unknown_employment_type_cannot_mint_token(case_client):
    """未识别的用工类型 fail-closed（不做"当作正式员工"的兜底）。"""
    resp = case_client.post("/api/v2/cases/session", json={
        "role": "qa", "dept": "quality", "plant": "P1",
        "employment_type": "contractor",
    })
    assert resp.status_code == 400, resp.text


def test_anonymous_subject_gets_denied_by_policy(case_client):
    """主体级显式 deny：匿名身份不发 401，而是策略层直接拒绝（default-deny）。"""
    token = _session(case_client, "qa", dept="quality", user_id="anonymous")
    resp = _search(case_client, token)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACTION_DENIED"


def test_revoked_principal_version_is_rejected(case_client):
    """撤权即失效：pv 提升后，旧令牌的第一次请求就该 401（不能等过期）。"""
    from mfg_agents.v2.services.principal_resolver import get_principal_version_store

    token = _session(case_client, "qa", dept="quality", user_id="u-revoke-probe")
    assert _search(case_client, token).status_code == 200

    get_principal_version_store().revoke("u-revoke-probe")
    resp = _search(case_client, token)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["code"] == "CASE_PRINCIPAL_REVOKED"


# ---------------------------------------------------------------- 2. 注入无效


def test_request_body_cannot_inject_filter(case_client):
    """请求体里塞 filter/where 必须完全无效（请求模型没有该字段）。

    判据不是"看起来一样"，而是 ``filter_hash`` 逐字节相同——它由服务端按
    令牌编译得出，注入任何过滤条件都改变不了它。
    """
    token = _token(case_client, "equipment")
    clean = _search(case_client, token, query="虚焊", k=5)
    dirty = _search(case_client, token, query="虚焊", k=5, extra={
        "filter": {"op": "ALL", "children": []},
        "where": {"tenant": "T999"},
        "acl_tokens": ["role:gm"],
        "visibility_class": 0,
        "is_knowledge_layer": True,
        "order_by": "score desc",
    })
    assert dirty.status_code == 200, dirty.text
    assert dirty.json()["data"]["filter_hash"] == clean.json()["data"]["filter_hash"]
    assert [h["case_id"] for h in dirty.json()["data"]["hits"]] == \
        [h["case_id"] for h in clean.json()["data"]["hits"]]


def test_request_body_cannot_inject_identity(case_client):
    """请求体里塞 roles/dept/tenant/密级 都不生效：身份只认令牌。"""
    token = _token(case_client, "equipment")
    clean = _search(case_client, token, query="注塑件缩水", k=10)
    dirty = _search(case_client, token, query="注塑件缩水", k=10, extra={
        "roles": ["gm", "quality_director"],
        "dept": "quality",
        "plant": "P2",
        "tenant": "T001",
        "max_classification_rank": 5,
        "user_id": "u-forged",
    })
    assert dirty.status_code == 200, dirty.text
    assert dirty.json()["data"]["filter_hash"] == clean.json()["data"]["filter_hash"]
    assert "QC-PD-2026-0055" not in _ids(dirty)


# ---------------------------------------------------------------- 3. 越权一律查不到


@pytest.mark.parametrize("case_id", sorted(_VISIBILITY))
@pytest.mark.parametrize("label", sorted(_SUBJECTS))
def test_detail_permission_matrix(case_client, case_id, label):
    """详情接口的真值表：有权 200，无权 404（不是 403）。"""
    token = _token(case_client, label)
    resp = case_client.get(f"/api/v2/cases/{case_id}", headers=_auth(token))
    expected = _VISIBILITY[case_id][label]
    if expected:
        assert resp.status_code == 200, f"{case_id} / {label} 应可读：{resp.text}"
        assert resp.json()["data"]["case_id"] == case_id
    else:
        assert resp.status_code == 404, f"{case_id} / {label} 应不可读：{resp.text}"
        assert resp.json()["detail"]["code"] == "CASE_NOT_FOUND_OR_NOT_VISIBLE"


def test_forbidden_and_missing_are_indistinguishable(case_client):
    """"无权"与"不存在"的响应必须逐字节相同，否则就是存在性预言机。"""
    token = _token(case_client, "qa")
    forbidden = case_client.get("/api/v2/cases/QC-MD-2026-0021", headers=_auth(token))
    missing = case_client.get("/api/v2/cases/QC-XX-9999-0000", headers=_auth(token))
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


def test_search_never_returns_forbidden_case(case_client):
    """检索侧：无权案例不出现在结果里；同时用另一个主体证明"不是没数据"。"""
    equipment = _ids(_search(case_client, _token(case_client, "equipment"),
                             query="注塑件缩水", k=20))
    quality = _ids(_search(case_client, _token(case_client, "qa"),
                           query="注塑件缩水", k=20))
    assert "QC-PD-2026-0055" not in equipment
    assert "QC-PD-2026-0055" in quality


def test_short_result_is_reported_not_backfilled(case_client):
    """候选不足时如实返回 + 说明"不放宽过滤补齐"，绝不回填。"""
    resp = _search(case_client, _token(case_client, "outsource"),
                   query="锡膏印刷偏移 虚焊 连锡", k=20)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data["hits"]) <= 20
    if len(data["hits"]) < 20:
        assert "不放宽过滤补齐" in data["notice"]
        assert data["hidden_count"] >= 1


@pytest.mark.parametrize(("label", "action"), [
    ("qa", "export"), ("equipment", "export"), ("outsource", "export"),
    ("qa", "share"), ("gm", "share"), ("qd", "share"),
])
def test_export_and_share_are_gated(case_client, label, action):
    """export / share 是门禁拒绝（403 + 明确错误码），不是"检索到再拦一道"。"""
    token = _token(case_client, label)
    resp = _search(case_client, token, action=action)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACTION_DENIED"


def test_ingest_requires_permission(case_client):
    """qa 没有 case.ingest：入库入口必须 403（前端隐藏按钮不算控制）。"""
    token = _token(case_client, "qa")
    resp = case_client.post("/api/v2/cases/ingest", headers=_auth(token),
                            json={"draft": {"title": "越权入库"}})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CASE_ACTION_DENIED"


# ---------------------------------------------------------------- 4. 不泄漏内部结构


def test_response_exposes_no_acl_internals(case_client):
    """检索与详情响应里不得出现 ACL/向量内部字段与维度令牌明文。"""
    token = _token(case_client, "qa")
    payloads = [
        _search(case_client, token, query="锡膏印刷偏移", k=10).json(),
        case_client.get("/api/v2/cases/QC-CC-2026-0087", headers=_auth(token)).json(),
    ]
    for payload in payloads:
        keys: set[str] = set()
        _walk_keys(payload, keys)
        leaked = keys & _FORBIDDEN_KEYS
        assert not leaked, f"响应泄漏内部字段：{leaked}"
        text = json.dumps(payload, ensure_ascii=False)
        assert "dept:quality" not in text
        assert "role:qa" not in text


def test_search_payload_has_no_evidence_layer_fields(case_client):
    """知识层与证据层类型隔离：检索结果里不得出现证据层内容字段。"""
    token = _token(case_client, "qa")
    data = _search(case_client, token, query="锡膏印刷偏移", k=10).json()["data"]
    for hit in data["hits"]:
        for key in ("evidence", "evidence_uri", "attachment", "drawing_no",
                    "raw_ticket_body", "photo"):
            assert key not in hit, f"命中里出现证据层字段 {key}"


def test_redacted_fields_contain_no_pii(case_client):
    """脱敏是入库前固化的：对外文本里不得出现客户/供应商/人名的原始写法。"""
    token = _token(case_client, "qa")
    literals = _literal_pii()
    assert literals, "脱敏词表为空，测试失效"

    texts = [json.dumps(_search(case_client, token, query="客户 供应商 异响", k=20).json(),
                        ensure_ascii=False)]
    for case_id in ("QC-CC-2026-0110", "QC-MD-2026-0014"):
        resp = case_client.get(f"/api/v2/cases/{case_id}", headers=_auth(token))
        if resp.status_code == 200:
            texts.append(json.dumps(resp.json(), ensure_ascii=False))

    blob = "\n".join(texts)
    hits = [v for v in literals if v in blob]
    assert not hits, f"响应中出现未脱敏的敏感原文：{hits}"


# ---------------------------------------------------------------- 5. 审计不落明文


def test_audit_chain_holds_no_plaintext(case_client):
    """审计只记哈希与计数：查询原文、维度令牌、案例正文都不得落库。"""
    from mfg_agents.common.config import settings

    marker = "ZQX-UNIQUE-QUERY-4711"
    token = _token(case_client, "qa")
    assert _search(case_client, token, query=f"{marker} 虚焊", k=5).status_code == 200

    chain = case_client.get("/api/v2/cases/audit/verify", headers=_auth(token))
    assert chain.status_code == 200, chain.text
    assert chain.json()["data"]["chain"]["valid"] is True

    conn = sqlite3.connect(str(settings.CASE_DB))
    try:
        rows = conn.execute("SELECT * FROM case_audit").fetchall()
        assert rows, "审计表为空，测试失效"
        blob = json.dumps([tuple(r) for r in rows], ensure_ascii=False)
    finally:
        conn.close()

    assert marker not in blob, "查询原文落进了审计表"
    assert "dept:quality" not in blob, "维度令牌落进了审计表"
    assert "面积偏差" not in blob, "案例正文落进了审计表"


# ================================================================
# 以下是任务 #4 追加的部分：种子数据覆盖不到的维度（客户维 / 已撤权），
# 以及 query 文本注入、k 放大、审计累积、证据层泄漏四类不变量。
# ================================================================

#: 追加案例的前缀（在隔离库里现场造，不改 data/case_seed.json，
#: 避免"为了测试改种子"污染演示数据）
CUST_PREFIX = "QA-CUST-"
WD_PREFIX = "QA-WD-"
_SEC_PREFIX = "QA-SEC-"

_CUSTOMER_TOKEN = "CUST-A"


def _mk_case(store, index, case_id: str, *, acl, status_target="active",
             cls_rank=1, title="", summary="") -> str:
    """在隔离库里造一条案例并推到目标状态（含索引同步）。"""
    store.create_case({
        "case_id": case_id,
        "case_code": case_id,
        "title": title or f"测试案例 {case_id}",
        "summary": summary or f"{case_id} 的摘要：锡膏印刷偏移与连焊的处置经验",
        "phenomenon": f"{case_id} 现象：SPI 检出面积偏差，回流后连锡",
        "root_cause_text": f"{case_id} 根因：刮刀压力偏低",
        "countermeasure_text": f"{case_id} 对策：恢复压力并加首件确认",
        "category": "quality_case",
        "subcategory": "process_defect",
        "cls_rank": cls_rank,
        "status": "draft",
        "content_version": 1,
        "source_ticket_type": "8d",
        "source_ticket_id": f"8D-{case_id}",
        "plant": "P1",
        "line_code": "SMT-1",
    })
    from mfg_agents.v2.services.case_index import compute_acl_tokens, compute_visibility_class

    tokens = store.set_acl(case_id, acl, allow_widen=True)
    store.set_visibility(case_id, compute_visibility_class(compute_acl_tokens(acl)), tokens)
    if status_target in ("active", "withdrawn"):
        store.transition(case_id, "review")
        store.transition(case_id, "active")
    if status_target == "withdrawn":
        store.transition(case_id, "withdrawn")
    index.sync_case(case_id)
    return case_id


@pytest.fixture(scope="module")
def extra_cases(case_client):
    """补三类种子没有的案例：客户维 3 条 / 已撤权 3 条 / 高密级 3 条。

    为什么要造而不是改种子：种子是演示数据，测试需要的是"边界样本"，
    两者混在一起会让"演示数据长什么样"变成测试的副产品。
    """
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    rt = get_case_runtime()
    ids = {"customer": [], "withdrawn": [], "secret": []}
    for i in range(3):
        ids["customer"].append(_mk_case(
            rt.store, rt.index_service, f"{CUST_PREFIX}{i:04d}",
            acl=[{"dim": "cus", "value": _CUSTOMER_TOKEN, "effect": "allow"}],
            title=f"客诉：{_CUSTOMER_TOKEN} 专属案例 {i}"))
        ids["withdrawn"].append(_mk_case(
            rt.store, rt.index_service, f"{WD_PREFIX}{i:04d}", acl=[],
            status_target="withdrawn", title=f"已撤权案例 {i}"))
        ids["secret"].append(_mk_case(
            rt.store, rt.index_service, f"{_SEC_PREFIX}{i:04d}", acl=[],
            cls_rank=2, title=f"密级 L2 案例 {i}"))
    return ids


# ---------------------------------------------------------------- 六类越权（各 ≥3 条）


def _assert_invisible(client, token: str, case_id: str, *, query: str) -> None:
    """两路验证：详情 404 + 检索结果里没有它。"""
    detail = client.get(f"/api/v2/cases/{case_id}", headers=_auth(token))
    assert detail.status_code == 404, f"{case_id} 详情竟然可见：{detail.text}"
    assert detail.json()["detail"]["code"] == "CASE_NOT_FOUND_OR_NOT_VISIBLE"

    resp = _search(client, token, query=query, k=50)
    assert resp.status_code == 200, resp.text
    assert case_id not in _ids(resp), f"{case_id} 出现在检索结果里"


@pytest.mark.parametrize("role,dept,case_id", [
    ("equipment", "equipment", "QC-PD-2026-0055"),      # dept:quality
    ("gm", "management", "QC-PD-2026-0055"),            # 职级高也不穿透部门域
    ("manufacturing", "production", "RC-QA-2026-0009"),  # 岗位经验（质量域）
])
def test_cross_department_cases_are_invisible(case_client, role, dept, case_id):
    token = _session(case_client, role, dept=dept)
    _assert_invisible(case_client, token, case_id, query="注塑件缩水 工艺参数 SPI 判定表")


@pytest.mark.parametrize("role,dept,case_id", [
    ("qa", "quality", "QC-MD-2026-0021"),
    ("gm", "management", "QC-MD-2026-0021"),
    ("quality_director", "quality", "QC-MD-2026-0021"),
])
def test_cross_plant_cases_are_invisible(case_client, role, dept, case_id):
    """P1 主体读不到 P2 专属案例（工厂维度独立判定）。"""
    token = _session(case_client, role, dept=dept, plant="P1")
    _assert_invisible(case_client, token, case_id, query="供应商五金件尺寸超差")


@pytest.mark.parametrize("case_id", ["QC-PD-2026-0099"])
@pytest.mark.parametrize("role,dept,rank", [
    ("qa", "quality", 1), ("quality_director", "quality", 3), ("gm", "management", 3),
])
def test_secret_classification_is_invisible_to_everyone(case_client, case_id, role, dept, rank):
    """密级 L3 = secret：**不因主体密级高而放开**（E1 专门通道）。"""
    token = _session(case_client, role, dept=dept, max_classification_rank=rank)
    _assert_invisible(case_client, token, case_id, query="客户定制工艺窗口异常")


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("role,dept", [("qa", "quality"), ("equipment", "equipment"), ("gm", "management")])
def test_customer_dimension_cases_are_invisible_without_token(case_client, extra_cases, index, role, dept):
    """客户维：没有 customer:<id> 令牌就等于查不到（不是"查到了再过滤"）。"""
    case_id = extra_cases["customer"][index]
    token = _session(case_client, role, dept=dept)
    _assert_invisible(case_client, token, case_id, query=f"{_CUSTOMER_TOKEN} 专属案例")


def test_customer_dimension_grants_when_token_present(case_client, extra_cases):
    """反向验证：同岗位、同部门，只多一个客户令牌 → 立刻可见。

    这条是"差异来自授权而不是数据缺失"的证明。
    """
    case_id = extra_cases["customer"][0]
    body = {"role": "qa", "dept": "quality", "plant": "P1",
            "customer_ids": [_CUSTOMER_TOKEN], "name": "qa-客户"}
    resp = case_client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["token"]
    assert f"cus:{_CUSTOMER_TOKEN}" in resp.json()["data"]["principal"]["principal_tokens"]

    detail = case_client.get(f"/api/v2/cases/{case_id}", headers=_auth(token))
    assert detail.status_code == 200, detail.text


def test_unknown_acl_dimension_is_rejected_at_write_time():
    """写错的维度名必须**当场报错**，不能静默忽略。

    静默忽略的后果是造出一条"谁都看不见"的案例：所有越权测试都能过，
    但正向测试会 404，排查成本极高。所以入口处 fail-fast。
    """
    from mfg_agents.v2.services.case_runtime import get_case_runtime
    from mfg_agents.v2.services.case_store import CaseStoreError

    rt = get_case_runtime()
    target = "RT-ACL-BAD-DIM"
    rt.store.create_case({
        "case_id": target, "case_code": target, "title": "维度名非法",
        "summary": "维度名校验", "phenomenon": "维度名校验", "status": "draft",
        "source_ticket_type": "8d", "source_ticket_id": "8D-RT-ACL-BAD-DIM",
        "category": "quality_case", "cls_rank": 1,
    })
    with pytest.raises(CaseStoreError, match="ACL 维度名非法"):
        rt.store.set_acl(target, [{"dim": "customer", "value": "CUST-A"}],
                         allow_widen=True)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("role,dept", [("qa", "quality"), ("gm", "management"), ("quality_director", "quality")])
def test_withdrawn_cases_are_invisible_to_everyone(case_client, extra_cases, index, role, dept):
    """已撤权是终态：任何角色都查不到（BR-04，索引即时失效）。"""
    case_id = extra_cases["withdrawn"][index]
    token = _session(case_client, role, dept=dept, max_classification_rank=3)
    _assert_invisible(case_client, token, case_id, query="已撤权案例 锡膏")


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("role,dept,rank", [
    ("qa", "quality", 1), ("gm", "management", 1), ("quality_director", "quality", 2),
])
def test_review_cases_are_invisible(case_client, index, role, dept, rank):
    """未结案（review）不进共享池；高密级主体也读不到。"""
    if index == 0:
        case_id = "QC-CC-2026-0115"
    else:
        # 另外两条走"刚入库的 review 案例"（避免依赖种子里恰好只有 1 条 review）
        token_ingest = _session(case_client, "quality_director", dept="quality",
                                user_id=f"u-review-{index}")
        draft = {
            "title": f"待审案例 {index}",
            "phenomenon": "SPI 面积偏差，回流后连锡",
            "root_cause_text": "刮刀压力偏低",
            "countermeasure_text": "恢复压力",
            "source_ticket_type": "8d", "source_ticket_id": f"8D-REVIEW-{index}",
            "defect_mode_code": "DM-SMT-012",
        }
        resp = case_client.post("/api/v2/cases/ingest", headers=_auth(token_ingest),
                                json={"draft": draft})
        assert resp.status_code == 200, resp.text
        case_id = resp.json()["data"]["case_id"]
    token = _session(case_client, role, dept=dept, max_classification_rank=rank,
                     user_id=f"u-reviewer-{role}-{index}")
    _assert_invisible(case_client, token, case_id, query="待审案例 锡膏印刷")


# ---------------------------------------------------------------- query 文本注入


@pytest.mark.parametrize("payload", [
    '" || 1==1 --',
    '"; drop table "case"; --',
    "锡膏\u200b印刷\u200c偏移",              # 零宽字符
    "锡膏印刷“偏移”",                        # 全角引号
    "A" * 2000,                              # 超长值
    "tenant=T999 dept=management cls_rank=9",
])
def test_query_text_cannot_influence_filter(case_client, payload):
    """query 文本只影响召回打分，**不影响过滤条件**。

    判据：``filter_hash`` 与普通 query 完全一致。过滤条件只由 Principal 决定
    （架构裁定 A10：``compile()`` 签名里根本没有 query_text）。
    """
    token = _session(case_client, "qa", dept="quality", user_id="u-qinj")
    baseline = _search(case_client, token, query="锡膏印刷偏移", k=5)
    assert baseline.status_code == 200, baseline.text
    probe = _search(case_client, token, query=payload, k=5)
    assert probe.status_code == 200, probe.text
    assert probe.json()["data"]["filter_hash"] == baseline.json()["data"]["filter_hash"]


def test_very_long_query_is_not_a_500(case_client):
    """超长 query 不能把服务打挂（要么正常返回、要么 4xx，绝不是 5xx）。"""
    token = _session(case_client, "qa", dept="quality")
    resp = _search(case_client, token, query="锡膏" * 3000, k=3)
    assert resp.status_code < 500, resp.text


# ---------------------------------------------------------------- 候选不足不放宽


def test_k_larger_than_visible_returns_all_without_backfill(case_client):
    """k 放大到远超可见量时：返回条数 == 实际可见条数，且 notice 如实说明。"""
    from mfg_agents.common.config import settings

    token = _session(case_client, "manufacturing", dept="production",
                     employment_type="outsource", user_id="u-k50")
    resp = _search(case_client, token, query="锡膏印刷偏移 虚焊 连锡", k=50)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data["hits"]) < 50, "外协人员不可能有 50 条可见案例（否则此用例失效）"
    assert len(data["hits"]) <= int(getattr(settings, "CASE_K_MAX", 20))
    assert "不放宽过滤补齐" in data["notice"]
    assert data["hidden_count"] > 0
    # 没有补齐 = 每次命中都能单独读出来（说明它们真的在授权范围内）
    for hit in data["hits"][:3]:
        ok = case_client.get(f"/api/v2/cases/{hit['case_id']}", headers=_auth(token))
        assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------- 审计累积 + 证据层


def test_audit_chain_survives_twenty_searches(case_client):
    """连续 20 次检索后链仍完整；且审计行不含 query 明文与令牌明文。"""
    token = _session(case_client, "qa", dept="quality", user_id="u-audit20")
    for i in range(20):
        resp = _search(case_client, token, query=f"锡膏印刷偏移 批次{i:02d}", k=3)
        assert resp.status_code == 200, resp.text

    verify = case_client.get("/api/v2/cases/audit/verify?limit=50", headers=_auth(token))
    assert verify.status_code == 200, verify.text
    chain = verify.json()["data"]["chain"]
    assert chain["valid"] is True, chain
    assert chain["rows"] >= 20

    blob = json.dumps(verify.json(), ensure_ascii=False)
    assert "批次00" not in blob, "查询明文出现在审计响应里"
    assert "dept:quality" not in blob, "维度令牌明文出现在审计响应里"


def test_search_response_has_no_evidence_layer_payload(case_client):
    """检索响应里不得出现证据层内容（正文/附件/图号/原始商务单号/原始工单号）。

    R1 裁定（team-lead）：``source_ticket_id`` 是**来源单据号**，8D 单号在客诉
    场景里可反查到客户与批次，**不得出现在检索响应**。检索只给
    ``source_ticket_type`` + 不可反推的 ``source_ticket_ref``；真号只在详情
    接口（``X-Case-Trace: full``）返回。这条断言曾在返工中被错误地"解释"成
    "溯源字段必须出现"，现已恢复为强断言。
    """
    token = _session(case_client, "qa", dept="quality")
    payload = _search(case_client, token, query="锡膏印刷偏移 连锡", k=20).json()

    forbidden_keys = {"evidence", "evidence_uri", "evidence_content", "attachment",
                      "attachments", "photo", "photos", "drawing_no", "raw_ticket_body"}
    keys: set[str] = set()
    _walk_keys(payload, keys)
    assert not (keys & forbidden_keys), f"检索响应出现证据层字段：{keys & forbidden_keys}"

    blob = json.dumps(payload, ensure_ascii=False)
    assert "evidence" not in blob
    for value in _literal_pii():
        assert value not in blob, f"检索响应出现未脱敏原文：{value}"

    # ★ R1：原始工单号一个都不许出现；引用句柄必须是 st_ 开头
    assert "source_ticket_id" not in keys, "命中载荷仍带 source_ticket_id（R1 违规）"
    assert not re.search(r"8D-\d{4}-\d{4}", blob), \
        f"检索响应出现原始工单号：{re.search(r'8D-[0-9]{4}-[0-9]{4}', blob).group(0)}"
    refs = [v for k, v in _walk_items(payload) if k == "source_ticket_ref" and v]
    assert refs, "命中载荷缺少 source_ticket_ref"
    for ref in refs:
        assert re.fullmatch(r"st_[0-9a-f]{10}", str(ref)), f"ref 形态不对：{ref}"

    assert not re.search(r"(?:订单号|订单|PO|SO|WO)[:：\s#-]*[A-Za-z]{0,4}-?\d{4,12}", blob), \
        "检索响应出现原始商务单号"
    assert not re.search(r"1[3-9]\d{9}", blob), "检索响应出现手机号"
    assert not re.search(r"(?:图号|图纸编号|DWG)[:：\s#-]*[A-Za-z]{0,3}-?\d{3,10}", blob), \
        "检索响应出现原始图号"
    for prefix in ("minio://", "s3://", "file://"):
        assert prefix not in blob, f"检索响应出现证据层 uri 前缀：{prefix}"
