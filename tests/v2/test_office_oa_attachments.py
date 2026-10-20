"""OA 材料登记（报销票据 / 出差报告 / 病假条）：虚拟登记 + 科目整理 + 单据绑定 + 补件。

演示态做"虚假上传"：**只登记材料清单（文件名/类别/大小/上传人），不接收、不保存原文件**。

覆盖点：
- 服务契约：``AttachmentStore`` 只登记元数据（virtual=true、无磁盘路径）、
  本人解析 / 大小与类别校验 / 路径清洗；
  ``organize_expense`` 二级科目拆分与必传/选传材料核对；
  报销带材料与缺必传提示；病假单"待补病假条"标记与本人补件（他人不可代补）；
- Agent：``organize_expense`` 工具、``submit_expense`` 绑定本次登记材料、
  ``supplement_leave_document`` 补挂病假条、他人材料 id 被剔除；
- HTTP：``/office/upload``（**JSON 登记，无文件字节**）、``/office/attachment/{id}``
  返回登记卡片（无原文件）、``/office/expense/organize``、``/office/leave/{id}/attachment``、
  ``/office/meta`` 材料类别与大小上限。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services.office import (
    AttachmentStore,
    DemoOfficeBackend,
    OfficeError,
    organize_expense,
    set_attachment_store,
)


@pytest.fixture
def store():
    """独立内存登记表（演示态：不落盘）。"""
    s = AttachmentStore()
    set_attachment_store(s)
    yield s
    set_attachment_store(None)


@pytest.fixture
def backend() -> DemoOfficeBackend:
    return DemoOfficeBackend()


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


# ============================================================
# 1. 材料登记表（虚拟：不接收、不保存原文件）
# ============================================================


def test_store_registers_metadata_only(store):
    meta = store.save("发票.pdf", "发票", "张三", size=8)
    assert meta["attachment_id"].startswith("ATT") and len(meta["attachment_id"]) == 8
    assert meta["kind"] == "发票" and meta["size"] == 8
    assert meta["uploader"] == "张三" and meta["virtual"] is True
    assert "path" not in meta and "stored_name" not in meta      # 未落盘
    full = store.get(meta["attachment_id"])
    assert full and full["filename"] == "发票.pdf"
    assert store.get("ATT99999") is None


def test_store_fabricates_size_when_absent(store):
    meta = store.save("出差报告.docx", "出差报告", "张三")
    assert 40_000 <= meta["size"] <= 1_900_000                 # 稳定假大小
    assert store.save("出差报告.docx", "出差报告", "张三")["size"] == meta["size"]


def test_store_sanitizes_filename(store):
    meta = store.save("../../etc/发票 单.pdf", "发票", "张三")
    # 取基名 + 清洗非法字符，防路径穿越
    assert "/" not in meta["filename"] and ".." not in meta["filename"]
    assert store.get(meta["attachment_id"])["filename"] == meta["filename"]


def test_store_validation(store):
    with pytest.raises(OfficeError):     # 类别非法
        store.save("a.pdf", "抬头", "张三")
    with pytest.raises(OfficeError):     # 上传人缺失
        store.save("a.pdf", "发票", "")
    with pytest.raises(OfficeError, match="超过上限"):
        store.save("big.pdf", "发票", "张三", size=store.MAX_BYTES + 1)


def test_store_resolve_only_own(store):
    a = store.save("a.pdf", "发票", "张三")
    b = store.save("b.pdf", "发票", "李四")
    metas, invalid = store.resolve([a["attachment_id"], b["attachment_id"], "ATT99999"], "张三")
    assert [m["attachment_id"] for m in metas] == [a["attachment_id"]]
    assert set(invalid) == {b["attachment_id"], "ATT99999"}      # 他人材料/臆造 id 剔除


# ============================================================
# 2. 整理报销科目 + 报销带材料
# ============================================================


def test_organize_expense_splits_by_note():
    out = organize_expense("差旅", 1286, "高铁 980 + 住宿 306", ["发票", "出差报告"])
    items = {i["subject"]: i["amount"] for i in out["items"]}
    assert items == {"城市间交通": 980.0, "住宿费": 306.0}
    assert out["required_kinds"] == ["发票"] and out["missing_required"] == []
    assert "出差报告" in out["optional_kinds"]


def test_organize_expense_missing_required_and_remainder():
    out = organize_expense("差旅", 1000, "打车 200", [])
    assert out["missing_required"] == ["发票"]
    items = {i["subject"]: i["amount"] for i in out["items"]}
    assert items["市内交通"] == 200.0 and items["其他差旅"] == 800.0


def test_organize_expense_no_amount_gives_single_line():
    out = organize_expense("办公用品", 320, "打印耗材", ["发票"])
    assert len(out["items"]) == 1 and out["items"][0]["subject"] == "办公耗材"
    assert out["items"][0]["amount"] == 320.0


def test_organize_expense_validation():
    with pytest.raises(OfficeError):
        organize_expense("餐饮", 100)
    with pytest.raises(OfficeError):
        organize_expense("差旅", 0)


def test_backend_expense_attachments_and_missing(backend):
    m = backend.submit_expense("差旅", 1286, "2026-10-15", "高铁 980 + 住宿 306", "张三")
    assert m["attachments"] == [] and m["missing_required"] == ["发票"]
    a = {"attachment_id": "ATT00001", "kind": "发票", "filename": "inv.pdf", "size": 10,
         "uploader": "张三", "uploaded_at": "2026-10-15 10:00:00", "virtual": True}
    b = {"attachment_id": "ATT00002", "kind": "出差报告", "filename": "trip.pdf", "size": 10,
         "uploader": "张三", "uploaded_at": "2026-10-15 10:00:00", "virtual": True}
    ok = backend.submit_expense("差旅", 1286, "2026-10-15", "高铁 980 + 住宿 306", "张三",
                                attachments=[a, b], subject="差旅")
    assert ok["attachment_count"] == 2 and ok["missing_required"] == []
    assert ok["attachments"][0]["kind"] == "发票" and ok["subject"] == "差旅"


# ============================================================
# 3. 病假条补件（提交后补充）
# ============================================================


def test_backend_leave_sick_note_pending_and_supplement(backend):
    lv = backend.apply_leave("病假", "2026-10-20", "2026-10-20", "张三", "感冒")
    assert lv["sick_note_required"] is True and lv["sick_note_pending"] is True
    note = {"attachment_id": "ATT00003", "kind": "病假条", "filename": "sick.jpg",
            "size": 10, "uploader": "张三", "uploaded_at": "2026-10-20 09:00:00",
            "virtual": True}
    out = backend.supplement_leave_document(lv["leave_id"], note, "张三")
    assert out["sick_note_pending"] is False
    assert out["attachment_count"] == 1 and out["supplements"][0]["kind"] == "病假条"

    # 已附病假条的病假：提交时即为 False
    lv2 = backend.apply_leave("病假", "2026-10-21", "2026-10-21", "张三", "复诊",
                              attachments=[note])
    assert lv2["sick_note_pending"] is False


def test_backend_leave_supplement_governance(backend):
    lv = backend.apply_leave("病假", "2026-10-20", "2026-10-20", "张三", "感冒")
    note = {"attachment_id": "ATT00003", "kind": "病假条", "filename": "sick.jpg", "size": 1,
            "uploader": "张三", "uploaded_at": "x"}
    with pytest.raises(OfficeError, match="本人"):
        backend.supplement_leave_document(lv["leave_id"], note, "李四")
    with pytest.raises(OfficeError, match="不存在"):
        backend.supplement_leave_document("QJ99999", note, "张三")
    with pytest.raises(OfficeError):
        backend.supplement_leave_document(lv["leave_id"], {}, "张三")


# ============================================================
# 4. Agent 工具：整理科目 / 绑定材料 / 补件
# ============================================================


def _agent(orch, backend, store):
    from mfg_agents.v2.agents.office import OfficeAssistantAgent
    ag = OfficeAssistantAgent(orchestrator=orch, backend=backend)
    ag.user_ctx = {"user_id": "u1", "name": "张三"}
    ag._pending_attachments = store.list_by_uploader("张三")
    return ag


def test_agent_organize_and_bind_attachments(orch, backend, store):
    store.save("inv.pdf", "发票", "张三")
    store.save("trip.pdf", "出差报告", "张三")
    ag = _agent(orch, backend, store)
    tools = ag._tools()

    org = tools["organize_expense"][0]("差旅", 1286, "高铁 980 + 住宿 306")
    assert org["ok"] is True and len(org["items"]) == 2
    assert org["missing_required"] == []              # 本次材料含发票

    sub = tools["submit_expense"][0]("差旅", 1286, "2026-10-15", "高铁 980 + 住宿 306")
    assert sub["ok"] is True and sub["expense"]["attachment_count"] == 2


def test_agent_rejects_foreign_attachment_ids(orch, backend, store):
    store.save("other.pdf", "发票", "李四")
    ag = _agent(orch, backend, store)
    tools = ag._tools()
    out = tools["submit_expense"][0]("交通", 60, "2026-10-15", "打车",
                                     attachment_ids=["ATT00001"])
    assert out["ok"] is True and out["expense"]["attachment_count"] == 0
    assert out["invalid_attachment_ids"] == ["ATT00001"]     # 他人材料被剔除


def test_agent_supplement_sick_note(orch, backend, store):
    store.save("sick.jpg", "病假条", "张三")
    ag = _agent(orch, backend, store)
    tools = ag._tools()
    # 模拟"先提交病假单（未附病假条）、后补件"：单据由后端直接建出
    lv = backend.apply_leave("病假", "2026-10-20", "2026-10-20", "张三", "感冒")
    assert lv["sick_note_pending"] is True
    out = tools["supplement_leave_document"][0](lv["leave_id"])
    assert out["ok"] is True and out["leave"]["sick_note_pending"] is False


# ============================================================
# 5. HTTP 端点（登记为 JSON：请求体不含文件字节）
# ============================================================


def test_http_register_attachment_view_organize_supplement(store):
    from fastapi.testclient import TestClient
    from mfg_agents.v2.demo.api import app, get_orchestrator
    client = TestClient(app)

    # 登记（JSON：只有文件名/类别/大小，无文件内容）
    r = client.post("/api/v2/office/upload",
                    json={"filename": "发票 2026-10.pdf", "kind": "发票",
                          "actor": "张三", "size": 21, "content_type": "application/pdf"})
    body = r.json()
    assert r.status_code == 200 and body["code"] == 0
    att = body["data"]
    assert att["kind"] == "发票" and att["uploader"] == "张三"
    assert att["virtual"] is True and att["size"] == 21

    # 查看登记：返回"未保存原文件"说明卡片（非原文件下载）
    view = client.get(f"/api/v2/office/attachment/{att['attachment_id']}")
    assert view.status_code == 200
    assert "未保存原文件" in view.text and att["filename"] in view.text

    # 未知材料 → 404
    assert client.get("/api/v2/office/attachment/ATT99999").status_code == 404

    # 非法类别 → 400
    bad = client.post("/api/v2/office/upload",
                      json={"filename": "a.pdf", "kind": "抬头", "actor": "张三"})
    assert bad.status_code == 400

    # 整理报销科目（确定性）
    org = client.post("/api/v2/office/expense/organize", json={
        "category": "差旅", "amount": 1286, "note": "高铁 980 + 住宿 306",
        "attachment_kinds": ["发票"]}).json()
    assert org["code"] == 0 and org["data"]["missing_required"] == []

    # 病假单补件（在 API 的办公后端上造一张本人病假单）
    lv = get_orchestrator().runtime.office.apply_leave(
        "病假", "2026-10-22", "2026-10-22", "张三", "牙痛")
    supp = client.post(f"/api/v2/office/leave/{lv['leave_id']}/attachment",
                       json={"attachment_id": att["attachment_id"], "actor": "张三"}).json()
    assert supp["code"] == 0 and supp["data"]["leave"]["attachment_count"] == 1
    # 他人补件 → 拒绝（材料不属于本人）
    den = client.post(f"/api/v2/office/leave/{lv['leave_id']}/attachment",
                      json={"attachment_id": att["attachment_id"], "actor": "李四"}).json()
    assert den["code"] == 1


def test_http_meta_exposes_attachment_kinds(orch):
    from fastapi.testclient import TestClient
    from mfg_agents.v2.demo.api import app
    client = TestClient(app)
    meta = client.get("/api/v2/office/meta").json()["data"]
    assert "发票" in meta["attachment_kinds"] and "病假条" in meta["attachment_kinds"]
    assert meta["attachment_max_mb"] > 0
    assert "发票" in meta["expense_attachment_kinds"]
    assert "病假条" in meta["leave_attachment_kinds"]
