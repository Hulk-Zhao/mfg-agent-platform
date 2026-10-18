"""案例助手 · 原文不泄漏测试（直读索引与关系库，不经 API）。

为什么要在 API 之外再测一层：API 测试证明的是"**返回体**里没有敏感原文"，
但泄漏可能发生在更早的环节——写进索引的那一份文本本身就没脱敏。
一旦落进向量库，任何未来的检索/导出/日志复制都会把原文带出去，
而且**改代码也洗不掉已经写进去的数据**。

因此本文件绕开 API，直接检查三处落地形态：

1. ``index.texts()`` —— 真正被 embed 的文本（memory 后端）；
2. ``case_chunk.text`` —— 关系库里的 chunk 原文（**与后端无关**，换 Milvus 也成立）；
3. ``case_evidence`` —— 证据层元数据（uri 等），它**不允许**出现在上面两处。

判据用脱敏词表 + 结构正则，不硬编码具体值：词表改了测试自动跟上。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REDACTION_RULES = _PROJECT_ROOT / "src" / "mfg_agents" / "v2" / "data" / "redaction_rules.yaml"

#: 证据层 uri 前缀（出现即说明证据层内容混进了知识层）
_EVIDENCE_URI_RE = re.compile(r"minio://|s3://|file://")

#: 商务信息结构（脱敏后应被占位符替换，原文不应残留）
_ORDER_RE = re.compile(r"(?:订单号|订单|PO|SO|WO)[:：\s#-]*[A-Za-z]{0,4}-?\d{4,12}")
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_DRAWING_RE = re.compile(r"(?:图号|图纸编号|DWG)[:：\s#-]*[A-Za-z]{0,3}-?\d{3,10}")


def _literal_pii() -> list[str]:
    data = yaml.safe_load(_REDACTION_RULES.read_text(encoding="utf-8"))
    values: list[str] = []
    for rule in data.get("rules") or []:
        if rule.get("type") == "literal":
            values.extend(str(v) for v in rule.get("values") or [])
    return [v for v in values if v]


def _assert_clean(text: str, origin: str) -> None:
    """一段文本必须同时满足：无词表原文、无订单号、无手机号、无图号、无证据 uri。"""
    assert text is not None
    leaked = [v for v in _literal_pii() if v in text]
    assert not leaked, f"{origin} 含未脱敏原文：{leaked[:5]}"
    assert not _ORDER_RE.search(text), f"{origin} 含原始订单号：{_ORDER_RE.search(text).group(0)}"
    assert not _PHONE_RE.search(text), f"{origin} 含手机号：{_PHONE_RE.search(text).group(0)}"
    assert not _DRAWING_RE.search(text), f"{origin} 含原始图号：{_DRAWING_RE.search(text).group(0)}"
    assert not _EVIDENCE_URI_RE.search(text), f"{origin} 含证据层 uri"


@pytest.fixture(scope="module")
def runtime(case_client):
    """复用 case_client 已经装好的隔离运行时（同一个库、同一份种子）。"""
    from mfg_agents.v2.services.case_runtime import get_case_runtime

    return get_case_runtime()


# ---------------------------------------------------------------- 索引层


def test_index_texts_are_fully_redacted(runtime):
    """真正被 embed 的文本里不得出现任何敏感原文。"""
    index = runtime.index_service.index
    if not hasattr(index, "texts"):
        pytest.skip(f"后端 {getattr(index, 'backend', '?')} 不提供 texts() 遍历，"
                    "关系库断言（下一条）仍然覆盖")
    texts = list(index.texts())
    assert texts, "索引为空，测试失效"
    for text in texts:
        _assert_clean(text, "索引文档")


def test_every_indexed_document_is_knowledge_layer(runtime):
    """知识层与证据层的类型隔离（ADR-003 闸门 4）：索引里只允许知识层。"""
    index = runtime.index_service.index
    if not hasattr(index, "rows"):
        pytest.skip("后端不提供 rows() 遍历")
    rows = list(index.rows())
    assert rows, "索引为空，测试失效"
    for row in rows:
        assert row.get("is_knowledge_layer") in (True, 1), \
            f"索引里出现非知识层文档：{row.get('chunk_id')}"


def test_index_rows_expose_no_acl_token_plaintext(runtime):
    """索引元数据里可以有 acl_tokens（过滤要用），但**不能**出现在文本里。

    这条是在守"过滤字段"与"可读文本"的边界：令牌进 metadata 是设计，
    进 text 就意味着用户搜得出来、也看得见。
    """
    index = runtime.index_service.index
    if not hasattr(index, "rows") or not hasattr(index, "texts"):
        pytest.skip("后端不提供 rows()/texts()")
    for row, text in zip(index.rows(), index.texts()):
        for token in row.get("acl_tokens") or []:
            assert token not in text, f"令牌 {token} 出现在文档正文里"


# ---------------------------------------------------------------- 关系库层（与后端无关）


def _all_case_ids(store) -> list[str]:
    return [str(row["case_id"]) for row in store.list_cases()]


def test_chunk_table_stores_hash_not_text(runtime):
    """``case_chunk`` **根本不存正文**——只有 ``text_hash``。

    这比"存了但脱敏了"更强：关系库里没有原文，任何 SQL 导出、备份、
    运维误操作都带不出敏感内容。同时校验 hash 与索引文档同源
    （``sha256(text)[:32]``），保证"关系库是索引的镜像"这条不变量成立。
    """
    import hashlib

    store = runtime.store
    case_ids = _all_case_ids(store)
    assert case_ids, "案例表为空，测试失效"

    chunk_rows: list[dict] = []
    for case_id in case_ids:
        chunk_rows.extend(store.chunk_rows(case_id))
    assert chunk_rows, "chunk 表为空，测试失效"

    # ① 表结构锁定：列集合里没有可存正文的列（新增列必须过这一关）
    expected_columns = {"chunk_id", "case_id", "chunk_type", "content_version",
                        "vector_status", "text_hash", "indexed_at"}
    for row in chunk_rows:
        assert set(row) == expected_columns, \
            f"case_chunk 表结构变了（差集 {set(row) ^ expected_columns}），请确认没有引入正文列"

    # ② text_hash 形态合法（sha256 前 32 位十六进制）
    hashes = {str(row["text_hash"]) for row in chunk_rows if row.get("text_hash")}
    for value in hashes:
        assert re.fullmatch(r"[0-9a-f]{32}", value), f"text_hash 形态异常：{value}"

    # ③ 与索引文档同源（memory 后端可遍历时才校验）
    #    只比对 ``vector_status='indexed'`` 的行：未结案/已撤权案例的 chunk 行是
    #    "待索引"映射（``stale``），索引里本来就不该有它们（BR-06），这不是漂移。
    index = runtime.index_service.index
    if not (hasattr(index, "texts") and hasattr(index, "rows")):
        pytest.skip(f"后端 {getattr(index, 'backend', '?')} 不可遍历，① ② 已覆盖")
    indexed_hashes = {str(row["text_hash"]) for row in chunk_rows
                      if str(row.get("vector_status") or "") == "indexed"
                      and row.get("text_hash")}
    assert indexed_hashes, "没有任何已索引的 chunk，测试失效"
    index_hashes = {
        hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:32]
        for text in index.texts()
    }
    assert indexed_hashes <= index_hashes, \
        "存在与索引文档不匹配的 text_hash（关系库与索引已漂移）"


def test_evidence_rows_never_reach_knowledge_layer(runtime):
    """证据层存在（有 uri），但它的 uri 不得出现在任何知识层文本里。"""
    store = runtime.store
    evidence: list[dict] = []
    for case_id in _all_case_ids(store):
        evidence.extend(store.evidence_rows(case_id))
    assert evidence, "种子里的证据层元数据没导进来，测试失效"

    uris = [str(row["uri"]) for row in evidence if row.get("uri")]
    assert uris, "证据层没有 uri，测试失效"
    for uri in uris:
        assert _EVIDENCE_URI_RE.search(uri), f"证据层 uri 形态异常：{uri}"

    index = runtime.index_service.index
    if not hasattr(index, "texts"):
        pytest.skip(f"后端 {getattr(index, 'backend', '?')} 不提供 texts()")
    chunk_blob = "\n".join(str(text or "") for text in index.texts())
    for uri in uris:
        assert uri not in chunk_blob, f"证据层 uri 出现在知识层：{uri}"
    assert not _EVIDENCE_URI_RE.search(chunk_blob)


# ---------------------------------------------------------------- 入库路径（脱敏在写之前）


def test_ingested_pii_is_redacted_before_indexing(case_client, runtime):
    """脱敏发生在**写库之前**：入库时正文已被替换，发布后索引里也只有占位符。

    这是最容易出事的路径——入库是唯一"用户内容进系统"的入口。
    """
    literals = _literal_pii()
    raw_customer = literals[0]
    raw_person = "王建国"
    raw_phone = "13800001111"

    token_body = {"role": "quality_director", "dept": "quality", "plant": "P1",
                  "name": "qd-入库", "user_id": "u-leak-ingest"}
    resp = case_client.post("/api/v2/cases/session", json=token_body)
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    draft = {
        "title": f"客诉：{raw_customer} 反馈连锡",
        "phenomenon": f"{raw_customer} 产线反馈连锡，联系人 {raw_person} 电话 {raw_phone}，"
                      f"订单号 PO-2026-000123，图号 DWG-778899",
        "root_cause_text": "刮刀压力偏低",
        "countermeasure_text": "恢复压力并加首件确认",
        "source_ticket_type": "8d",
        "source_ticket_id": "8D-2026-8888",
        "defect_mode_code": "DM-SMT-012",
    }
    created = case_client.post("/api/v2/cases/ingest", headers=headers, json={"draft": draft})
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    case_id = data["case_id"]
    assert data["status"] == "review"
    assert set(data["redaction_kinds"]) & {"customer", "person", "phone"}

    # 发布（模拟审批通过；这里直接走状态机 + 索引同步，等价于 publish_case）
    runtime.store.transition(case_id, "active")
    runtime.index_service.sync_case(case_id)

    # ① 关系库：chunk 只有哈希，不含正文（比"脱敏后入库"更强的一层保险）
    chunks = runtime.store.chunk_rows(case_id)
    assert chunks, "发布后应有 chunk"
    for row in chunks:
        assert "text" not in row, "case_chunk 不应有正文列"

    # ② 索引：被 embed 的文本同样干净
    index = runtime.index_service.index
    if hasattr(index, "texts"):
        text = "\n".join(index.texts())
        _assert_clean(text, "索引（含新入库案例）")
        assert raw_customer not in text
        assert raw_person not in text
        assert raw_phone not in text
        assert "PO-2026-000123" not in text
