"""R6 齐套催交状态机契约（服务层）。

守五件事：
1. **状态机严格**：draft → dispatched → supplier_replied → arrival_confirmed → closed，
   非法流转拒绝（不"尽力而为"）；关闭必须写结论。
2. **幂等**：同（料号 × 供应商 × 工单集）重复派发返回既有行，不产生第二条待办。
3. **双库分工**（D10）：演示库 → ``writeback_mode=demo`` 且行带「演示回写」事实；
   治理库 → 不带演示标记。
4. **缺口不编造**：注册查询失败 → ``FollowupDataError``，绝不返回空清单冒充"齐套"。
5. **超 SLA 升级**：复用本模块 SLA 字段，不新造计时器。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mfg_agents.v2.services import procurement_followup as svc

_SHORTAGE_ROWS = [
    {"part_no": "IC-100", "part_name": "主控芯片", "shortage_qty": 3200,
     "available_qty": 800, "safety_stock": 4000},
    {"part_no": "SP-001", "part_name": "无铅锡膏SAC305", "shortage_qty": 150,
     "available_qty": 50, "safety_stock": 200},
]
_SUPPLIER_ROWS = [
    {"supplier": "深南电路", "part_no": "IC-100", "part_name": "主控芯片",
     "batch_cnt": 2, "fail_cnt": 1},
    {"supplier": "华强电子", "part_no": "IC-100", "part_name": "主控芯片",
     "batch_cnt": 5, "fail_cnt": 0},
    {"supplier": "千住金属", "part_no": "SP-001", "part_name": "无铅锡膏SAC305",
     "batch_cnt": 3, "fail_cnt": 1},
]


def _run_query(name: str, params: dict) -> list[dict]:
    return {"material_shortage": _SHORTAGE_ROWS,
            "supplier_followup": _SUPPLIER_ROWS}[name]


@pytest.fixture()
def demo_db(tmp_path):
    return str(tmp_path / "demo_governance.db")


@pytest.fixture()
def gov_db(tmp_path):
    return str(tmp_path / "governance.db")


# ---------------------------------------------------------------- 缺口清单


def test_build_gaps_maps_supplier_and_owner():
    plan = svc.build_followup_gaps(_run_query)
    assert plan["total_gaps"] == 2 and not plan["truncated"]
    ic = next(g for g in plan["gaps"] if g["part_no"] == "IC-100")
    # 供应商取批次最多的一家（华强电子 5 > 深南电路 2），不按字母序猜
    assert ic["supplier"] == "华强电子"
    assert ic["owner"] == svc.DEFAULT_OWNER == "采购员"
    assert ic["shortage_qty"] == 3200
    assert "未做 BOM 逐单齐套" in plan["basis"]


def test_build_gaps_respects_limit_and_reports_truncation():
    plan = svc.build_followup_gaps(_run_query, limit=1)
    assert len(plan["gaps"]) == 1 and plan["total_gaps"] == 2 and plan["truncated"]


def test_build_gaps_enriches_supplier_from_stock():
    """supplier_followup 只覆盖有来料批次的料号；缺供应商时按库存表补全。"""
    def _run(name: str, params: dict):
        if name == "material_stock_by_part":
            assert params["part_no"] == "BOX-298"
            return [{"supplier": "裕同科技"}, {"supplier": "裕同科技"},
                    {"supplier": "合兴包装"}]
        if name == "material_shortage":
            return [{"part_no": "BOX-298", "part_name": "彩盒", "shortage_qty": 2057,
                     "available_qty": 1000, "safety_stock": 3057}]
        return []

    plan = svc.build_followup_gaps(_run)
    assert plan["gaps"][0]["supplier"] == "裕同科技"   # 出现次数最多
    assert plan["missing_supplier"] == []


def test_build_gaps_fails_honestly_on_query_error():
    def _boom(name: str, params: dict):
        raise RuntimeError("表级权限不足")

    with pytest.raises(svc.FollowupDataError):
        svc.build_followup_gaps(_boom)


# ---------------------------------------------------------------- 幂等


def test_followup_id_is_deterministic():
    a = svc.followup_id_for("IC-100", "华强电子", "本周开工批次")
    b = svc.followup_id_for("IC-100", "华强电子", "本周开工批次")
    c = svc.followup_id_for("IC-100", "深南电路", "本周开工批次")
    assert a == b and a != c and a.startswith("fo-")


def test_dispatch_is_idempotent(demo_db):
    plan = svc.build_followup_gaps(_run_query)
    first = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                   db_path=demo_db)
    second = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                    db_path=demo_db)
    assert len(first) == len(second) == 2
    assert [r["followup_id"] for r in first] == [r["followup_id"] for r in second]
    assert all(r["status"] == "dispatched" for r in second)
    assert len(svc.list_followups(db_path=demo_db)) == 2


def test_plan_then_dispatch_records_two_history_steps(demo_db):
    plan = svc.build_followup_gaps(_run_query)
    drafted = svc.plan_followups("本周开工批次", plan["gaps"], actor="t", db_path=demo_db)
    assert drafted and all(r["status"] == "draft" for r in drafted)
    dispatched = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                        db_path=demo_db)
    history = dispatched[0]["history"]
    assert [h["to"] for h in history] == ["draft", "dispatched"]


# ---------------------------------------------------------------- 状态机


def test_full_happy_path_and_demo_writeback(demo_db):
    plan = svc.build_followup_gaps(_run_query)
    row = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                 db_path=demo_db)[0]
    fid = row["followup_id"]
    assert row["writeback_mode"] == "demo" and row["demo_writeback"] is True
    assert "未写回 ERP" in row["writeback_label"]

    row = svc.transition(fid, "reply", actor="t", note="周五到货 2000", db_path=demo_db)
    assert row["status"] == "supplier_replied" and row["reply_at"]

    row = svc.transition(fid, "arrival", actor="t", note="到货 2000",
                         arrival_qty=2000, db_path=demo_db)
    assert row["status"] == "arrival_confirmed" and row["arrival_qty"] == 2000
    assert row["demo_writeback"] is True   # 演示态事实贯穿到关闭

    row = svc.transition(fid, "close", actor="t", note="缺口已补齐", db_path=demo_db)
    assert row["status"] == "closed" and row["closed_at"]
    assert row["history"][-1]["to"] == "closed"


def test_governance_db_not_marked_demo(gov_db):
    plan = svc.build_followup_gaps(_run_query)
    row = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                 db_path=gov_db)[0]
    assert row["writeback_mode"] == "governance"
    assert row["demo_writeback"] is False


def test_illegal_transition_rejected(demo_db):
    plan = svc.build_followup_gaps(_run_query)
    row = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                 db_path=demo_db)[0]
    with pytest.raises(svc.InvalidFollowupTransition):
        svc.transition(row["followup_id"], "close", actor="t", note="跳步关闭",
                       db_path=demo_db)
    with pytest.raises(svc.InvalidFollowupTransition):
        svc.transition(row["followup_id"], "arrival", actor="t", db_path=demo_db)


def test_reply_and_close_require_note(demo_db):
    plan = svc.build_followup_gaps(_run_query)
    row = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                 db_path=demo_db)[0]
    with pytest.raises(svc.FollowupError):
        svc.transition(row["followup_id"], "reply", actor="t", note="  ", db_path=demo_db)
    svc.transition(row["followup_id"], "reply", actor="t", note="周五到", db_path=demo_db)
    svc.transition(row["followup_id"], "arrival", actor="t", db_path=demo_db)
    with pytest.raises(svc.FollowupError):
        svc.transition(row["followup_id"], "close", actor="t", note="", db_path=demo_db)


def test_unknown_action_and_missing_row(demo_db):
    with pytest.raises(svc.FollowupError):
        svc.transition("fo-nope", "teleport", db_path=demo_db)
    with pytest.raises(svc.FollowupNotFound):
        svc.transition("fo-does-not-exist", "reply", note="x", db_path=demo_db)


# ---------------------------------------------------------------- SLA 升级


def test_escalate_overdue_only_past_due(demo_db):
    plan = svc.build_followup_gaps(_run_query)
    rows = svc.dispatch_followups("本周开工批次", plan["gaps"], actor="t",
                                  db_path=demo_db)
    repo = svc.SQLiteProcurementFollowupRepository(demo_db)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    future = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(timespec="seconds")
    repo.update(rows[0]["followup_id"], {"sla_due_at": past})
    repo.update(rows[1]["followup_id"], {"sla_due_at": future})

    escalated = svc.escalate_overdue(db_path=demo_db)
    assert [r["followup_id"] for r in escalated] == [rows[0]["followup_id"]]
    assert escalated[0]["status"] == "escalated"
    assert svc.get_followup(rows[1]["followup_id"], db_path=demo_db)["status"] == "dispatched"


# ---------------------------------------------------------------- 契约/库域


def test_repository_contract_is_abstract():
    with pytest.raises(TypeError):
        svc.ProcurementFollowupRepository()  # type: ignore[abstract]


def test_resolve_db_scope_by_path(demo_db, gov_db):
    _, demo_scope = svc.resolve_followup_db(demo_db)
    _, gov_scope = svc.resolve_followup_db(gov_db)
    assert demo_scope == "demo" and gov_scope == "governance"


def test_resolve_db_explicit_env(tmp_path, monkeypatch):
    target = str(tmp_path / "custom_followup.db")
    monkeypatch.setenv("MFG_FOLLOWUP_DB", target)
    path, scope = svc.resolve_followup_db()
    assert path == target and scope == "governance"


def test_resolve_db_rejects_mysql_url(monkeypatch):
    monkeypatch.setenv("MFG_FOLLOWUP_DB", "mysql+pymysql://u:p@host/db")
    with pytest.raises(Exception):
        svc.resolve_followup_db()
