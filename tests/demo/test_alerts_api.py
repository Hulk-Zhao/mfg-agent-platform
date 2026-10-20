"""预警中心（/api/v2/alerts）契约测试。

守四件事：
1. **真实数据驱动**：四类预警由当前只读数据机械推导，与各看板同源可对账
   （SPC 越限 == SPC 看板能力不足特性数；订单临期 == 订单看板风险订单数）；
2. **fail-closed**：无令牌 401（业务数据不接受匿名读取）；
3. **绝不编造**：真实预警只能给「判定依据」，不得凭库中不存在的处置生命周期
   编出处置步骤 / 响应时间线；等级 / 状态必须落在白名单内；
4. **现场推送**（第五类）：真相源是治理库的现场看板推送表，状态/等级是可复算的
   机械映射，且与 ``/api/v2/site-board`` 同源对账、客户名同一套脱敏规则；
   前端 ``ALERT_TYPES`` / ``typeCls`` 字面量必须与后端一致。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

_DB = Path(__file__).resolve().parents[2] / "data" / "mfg_agent.db"
_UI = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"

_TYPES = ("SPC越限", "缺料", "订单临期", "设备告警", "现场推送")
_LEVELS = {"P1", "P2"}
_STATUSES = {"待处置", "处置中", "已闭环"}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


def _token(client, *roles: str) -> str:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _get(client, path: str, *roles: str) -> dict:
    headers = {"Authorization": f"Bearer {_token(client, *roles)}"}
    resp = client.get(path, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- fail-closed

def test_alerts_requires_token(client):
    """业务预警不接受匿名读取：缺令牌一律 401（不是 200 空数据）。"""
    resp = client.get("/api/v2/alerts")
    assert resp.status_code == 401


# ---------------------------------------------------------------- 真实数据驱动

def test_alerts_from_current_data(client):
    """预警条目来自当前数据：四类齐全、字段合法、计数自洽。"""
    data = _get(client, "/api/v2/alerts", "gm")
    assert data["degraded"] is False, data.get("degraded_reason")
    assert data["types"] == list(_TYPES)
    alerts = data["alerts"]
    assert alerts, "当前数据集下应能推导出预警"

    seen = {a["type"] for a in alerts}
    assert seen <= set(_TYPES)
    for a in alerts:
        for key in ("id", "type", "level", "status", "title", "detail",
                    "basis", "tags", "time", "owner"):
            assert a.get(key), f"预警字段缺失: {key} @ {a.get('id')}"
        assert a["level"] in _LEVELS
        assert a["status"] in _STATUSES

    counts = data["counts"]
    for t in _TYPES:
        assert counts[t] == len([a for a in alerts if a["type"] == t])


def test_alerts_never_fabricate_lifecycle(client):
    """库里没有预警处置生命周期表 → 真实预警只给判定依据，不编处置步骤/时间线。"""
    data = _get(client, "/api/v2/alerts", "gm")
    for a in data["alerts"]:
        assert a["basis"], "真实预警必须给出可复算的判定依据"
        assert not a.get("steps"), "不得编造处置步骤（库中无此事实）"
        assert not a.get("timeline"), "不得编造响应时间线（库中无此事实）"
    assert len(data.get("rules") or []) == 5, "五类预警的判定口径都应随载荷回传"


def test_site_board_alerts_state_level_and_parity(client):
    """现场推送：状态/等级是可复算映射，且与 ``/api/v2/site-board`` 未闭环行同源对账。"""
    from mfg_agents.v2.services import site_board

    data = _get(client, "/api/v2/alerts", "gm")
    alerts = [a for a in data["alerts"] if a["type"] == "现场推送"]
    open_rows = [r for r in site_board.list_pushes()
                 if r.get("status") in ("pushed", "acked")]
    assert len(alerts) == len(open_rows), "预警与现场看板队列口径漂移"
    assert data["counts"]["现场推送"] == len(alerts)

    by_id = {a["id"]: a for a in alerts}
    for row in open_rows:
        a = by_id.get(str(row["push_id"]))
        assert a, f"推送行 {row['push_id']} 未出现在现场推送预警里"
        assert a["status"] == ("待处置" if row["status"] == "pushed" else "处置中")
        assert a["level"] == ("P1" if row["source"] == "complaint_8d" else "P2")
        assert a["entity"]["push_id"] == row["push_id"]
        assert a["entity"]["stage"] == row["stage"]


def test_site_board_alert_customer_masked_by_role(client):
    """现场推送的客户名与 /api/v2/site-board 同一套脱敏：非对客岗位看代号，销售看真名。

    注意：推送队列读的是**治理库真实状态**（现场屏的接收 / 闭环会写库），带客户的
    推送可能已被闭环而不进预警清单 —— 因此期望值按**当前未闭环行**推导，
    不写死某一家客户名（写死会随测试写入状态而漂移）。
    """
    from mfg_agents.v2.services import site_board

    open_named = {str(r.get("customer") or "").strip()
                  for r in site_board.list_pushes()
                  if r.get("status") in ("pushed", "acked")
                  and str(r.get("customer") or "").strip()}

    masked = _get(client, "/api/v2/alerts", "manufacturing")
    titles = " ".join(a["title"] for a in masked["alerts"] if a["type"] == "现场推送")
    # 真名绝不泄漏给非对客岗位（无论当前有没有带客户的推送，这条都必须成立）
    for name in open_named:
        assert name not in titles, f"非对客岗位不应看到客户真名：{name}"
    # 有带客户的推送时，必须替换为「客户 X」代号
    if open_named:
        assert "客户 " in titles, "非对客岗位应看到客户代号"

    seen = _get(client, "/api/v2/alerts", "sales")
    real = " ".join(a["title"] for a in seen["alerts"] if a["type"] == "现场推送")
    # 对客岗位应看到真名（与订单看板同一套 keep_roles）
    for name in open_named:
        assert name in real, f"对客岗位应看到客户真名：{name}"


def test_frontend_alert_types_match_backend():
    """前端 ALERT_TYPES / typeCls 字面量必须与后端完全一致（筛选片与配色靠它驱动）。"""
    from mfg_agents.v2.services.alerts import ALERT_TYPES

    src = (_UI / "screens.js").read_text(encoding="utf-8")
    types_block = re.search(r"const ALERT_TYPES = \[(.*?)\];", src, re.S)
    assert types_block, "未找到前端 ALERT_TYPES 字面量"
    front = re.findall(r'"([^"]+)"', types_block.group(1))
    assert front == list(ALERT_TYPES), "前端预警类型与后端漂移"

    cls_block = re.search(r"const typeCls = \{(.*?)\};", src, re.S)
    assert cls_block, "未找到前端 typeCls 映射"
    for t in ALERT_TYPES:
        assert f'"{t}"' in cls_block.group(1), f"typeCls 缺少「{t}」的配色"


def test_frontend_site_board_write_actions_present():
    """现场推送卡片必须带写操作入口（现场接收 / 闭环），且闭环结论走必填弹窗。"""
    src = (_UI / "screens.js").read_text(encoding="utf-8")
    for token in ("data-site-ack", "data-site-close", "siteBoardAck(",
                  "siteBoardFeedback(", "现场处置结论（必填）"):
        assert token in src, f"预警中心现场推送缺少：{token}"


def test_spc_alerts_match_spc_board(client):
    """SPC 越限预警条数 == SPC 看板判定为「不合格/偏低」的特性数（同源可对账）。"""
    data = _get(client, "/api/v2/alerts", "gm")
    board = _get(client, "/api/v2/dashboard/spc?window=window", "gm")
    bad = [c for c in board["capabilities"] if c.get("verdict") in ("不合格", "偏低")]
    spc_alerts = [a for a in data["alerts"] if a["type"] == "SPC越限"]
    assert len(spc_alerts) == len(bad), "预警与 SPC 看板口径漂移"


def test_order_alerts_match_orders_board(client):
    """订单临期预警条数 == 订单看板风险订单清单条数（同一窗口、同一聚合）。"""
    data = _get(client, "/api/v2/alerts", "gm")
    board = _get(client, "/api/v2/dashboard/orders?window=window", "gm")
    order_alerts = [a for a in data["alerts"] if a["type"] == "订单临期"]
    assert len(order_alerts) == len(board["risk_orders"]), "预警与订单看板口径漂移"


def test_equipment_alerts_match_abnormal_logs(client):
    """设备告警条数 == equipment_log 中 is_abnormal=1 的记录数（直接对账原始表）。"""
    data = _get(client, "/api/v2/alerts", "gm")
    conn = sqlite3.connect(_DB)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM equipment_log WHERE is_abnormal = 1").fetchone()[0]
    finally:
        conn.close()
    assert len([a for a in data["alerts"] if a["type"] == "设备告警"]) == total


# ---------------------------------------------------------------- 客户字段脱敏

def test_order_customer_masked_for_warehouse(client):
    """预警全员可见 ≠ 客户真名全员可见：非对客岗位看到「客户 X」代号。"""
    data = _get(client, "/api/v2/alerts", "warehouse")
    titles = " ".join(a["title"] for a in data["alerts"] if a["type"] == "订单临期")
    assert "客户 " in titles, "非对客岗位应看到客户代号"
    assert "华为终端" not in titles, "非对客岗位不应看到客户真名"


def test_order_customer_visible_for_sales(client):
    """对客岗位（销售）可见客户真名——与订单看板同一套 keep_roles。"""
    data = _get(client, "/api/v2/alerts", "sales")
    titles = " ".join(a["title"] for a in data["alerts"] if a["type"] == "订单临期")
    assert "客户 " not in titles or "华为终端" in titles, titles


# ---------------------------------------------------------------- 降级不崩

def test_build_alerts_degrade_without_raising():
    """数据源整体故障时只降级不抛（端点绝不 500）——前端据此回落演示快照。

    「现场推送」的真相源是治理库（``site_board_push``），与业务只读库无关，
    因此业务库故障时它**仍如实展示**（这是设计而非漏判）——断言按"其余四类为空"收敛。
    """
    from mfg_agents.v2.services.alerts import build_alerts

    class Broken:
        def execute_readonly_sql(self, *a, **k):
            raise RuntimeError("db down")

        def run_query(self, *a, **k):
            raise RuntimeError("db down")

    data = build_alerts(Broken(), ["gm"])
    assert data["degraded"] is True
    assert data["degraded_reason"]
    assert [a for a in data["alerts"] if a["type"] != "现场推送"] == []
