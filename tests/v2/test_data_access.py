"""数据速查「查询权限申请 + 演示自动审批」测试。

对应实现：
- ``v2/services/data_access.py``（申请 / 预检 / 演示代批 / 回收）；
- ``v2/services/text2sql/libraries.py`` 的运行时授权叠加层（``grant_table_access`` /
  ``table_acl`` / ``reset_table_grants``）；
- ``v2/demo/api.py`` 的三个端点与快照 ``requestable`` 标记。

覆盖点（对齐 ``test_case_auto_approve.py`` 的纪律）：
1. 申请 → 演示代批 → 授权按角色写入叠加层 → 快照/白名单即时放行；
2. 已可读（AlreadyAuthorized）/ 重复（Duplicate）/ 未知数据集拒绝；
3. 职责岗解析（fail-closed：无登记职责岗不可代批）；
4. 非申请人触发代批 → 拒；演示开关关闭 → 拒不在授权路径上放行；
5. TTL 到期惰性回收（移除叠加层 + 申请置 expired）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.services import data_access as da
from mfg_agents.v2.services.text2sql import table_acl


@pytest.fixture(autouse=True)
def _clean() -> None:
    """每个用例前后清空服务单例与授权叠加层（隔离，避免跨用例串授权）。"""
    da.reset_data_access()
    yield
    da.reset_data_access()


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


def _request(svc, table="material_inventory", roles=("product_engineering",),
             reason="演示：核对生产齐套与交付风险", actor="u-pe"):
    return svc.request_access(table=table, roles=list(roles), reason=reason,
                              actor=actor, dept="rd_engineering")


# ============================================================
# 1. 主链路：申请 → 代批 → 授权生效
# ============================================================


def test_request_then_demo_approve_grants_role_access():
    svc = da.get_data_access()
    row = _request(svc)
    assert row["status"] == "pending"
    assert row["request_id"].startswith("DAR-")
    # 申请阶段**不授权**：叠加层尚未包含申请人角色
    assert "product_engineering" not in table_acl("material_inventory")

    prev = svc.preview(table="material_inventory", roles=["product_engineering"], actor="u-pe")
    assert prev["available"] is True
    assert prev["approver_role"] in {"material_control", "warehouse_manager", "supply_chain_manager"}

    decided = svc.demo_approve(row["request_id"], actor="u-pe")
    assert decided["status"] == "approved"
    assert decided["expires_at"]                      # 带有效期
    assert decided["approver"].startswith("u-")       # 职责岗演示身份
    # 授权按**角色**落：叠加层已包含申请人角色
    assert "product_engineering" in table_acl("material_inventory")
    # 事件带 .demo 后缀（链上可一眼分辨"这不是真人点的"）
    assert any(e["action"] == "data.access.approved.demo" for e in svc.events())


def test_snapshot_blocks_authorized_after_grant(orch):
    """端到端：快照对无权限岗位先 fail-closed，代批后即时放行。"""
    from mfg_agents.v2.demo import api

    before = api.api_office_snapshot(roles="product_engineering")["data"]["blocks"]
    assert before["material_stock"]["authorized"] is False
    assert before["material_stock"].get("requestable") is True
    assert "query" not in before["material_stock"]    # fail-closed：不暴露查询名

    svc = da.get_data_access()
    row = _request(svc)
    svc.demo_approve(row["request_id"], actor="u-pe")

    after = api.api_office_snapshot(roles="product_engineering")["data"]["blocks"]
    assert after["material_stock"]["authorized"] is True
    assert after["material_stock"]["ok"] is True
    assert after["material_stock"]["count"] > 0


# ============================================================
# 2. 申请阶段的拒绝：已可读 / 重复
# ============================================================


def test_already_authorized_role_rejected():
    """已能查询的岗位（仓库）不该走申请通道。"""
    svc = da.get_data_access()
    with pytest.raises(da.AlreadyAuthorized):
        _request(svc, roles=("warehouse",), actor="u-wh")


def test_duplicate_pending_rejected():
    svc = da.get_data_access()
    _request(svc)
    with pytest.raises(da.DuplicateRequest):
        _request(svc)                                  # 同 (table, actor) 再申请


def test_unknown_table_rejected():
    svc = da.get_data_access()
    with pytest.raises(da.DataAccessError):
        _request(svc, table="no_such_table")


def test_short_reason_rejected():
    svc = da.get_data_access()
    with pytest.raises(da.DataAccessError):
        _request(svc, reason="短")


# ============================================================
# 3. 职责岗解析：fail-closed
# ============================================================


def test_preview_unavailable_when_no_owner_registered():
    """表登记在白名单但未登记归属职责岗 → 不可代批（fail-closed）。"""
    svc = da.get_data_access()
    prev = svc.preview(table="inventory", roles=["sqe"], actor="u-sqe")
    assert prev["available"] is False
    assert prev["reason"]


# ============================================================
# 4. 代批硬约束：非申请人 / 开关关闭
# ============================================================


def test_not_requester_cannot_trigger_demo_approve():
    svc = da.get_data_access()
    row = _request(svc, actor="u-pe")
    with pytest.raises(da.NotRequester):
        svc.demo_approve(row["request_id"], actor="u-other")
    assert row["request_id"] and svc.get(row["request_id"])["status"] == "pending"


def test_demo_disabled_blocks_auto_approve(monkeypatch):
    from mfg_agents.common.config import settings

    svc = da.get_data_access()
    row = _request(svc)                                # 申请本身不受开关影响
    monkeypatch.setattr(settings, "DATA_ACCESS_AUTO_APPROVE_DEMO", False)
    assert svc.preview(table="material_inventory",
                       roles=["product_engineering"], actor="u-pe")["available"] is False
    with pytest.raises(da.DemoDisabled):
        svc.demo_approve(row["request_id"], actor="u-pe")
    # 未授权：叠加层仍不含申请人角色
    assert "product_engineering" not in table_acl("material_inventory")


# ============================================================
# 5. TTL 到期回收
# ============================================================


def test_reap_expired_revokes_grant():
    svc = da.get_data_access()
    row = _request(svc)
    svc.demo_approve(row["request_id"], actor="u-pe")
    assert "product_engineering" in table_acl("material_inventory")

    # 手工把到期时间拨到过去，触发惰性回收
    svc._requests[row["request_id"]]["expires_at"] = "2000-01-01T00:00:00+00:00"  # noqa: SLF001
    assert svc.reap_expired() == 1
    assert "product_engineering" not in table_acl("material_inventory")
    assert svc.get(row["request_id"])["status"] == "expired"
