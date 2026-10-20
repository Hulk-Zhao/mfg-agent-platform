"""现场看板推送层契约测试（T01+T02 骨架：客诉 → 责任工段 推送链路）。

覆盖五类不可回归的口径：
1. **归因**：显式工段 → 产线前缀 → 批次反推的三层顺序，判不出来返回空串不猜值；
2. **映射漂移**：``site_board.STAGE_LINE_PREFIX`` 与 ``dashboard._MFG_STAGE_LINE_LIKE``
   逐项等价（两处各写一份，靠本用例防漂移）；
3. **状态机**：``pushed → acked → closed``，非法流转拒绝，重推不打回现场进度；
4. **投递**：通道只对工段白名单落库、非工段 target 如实跳过，两类 data_gap 兜底；
5. **DAG 分派步骤（T03）**：客诉 8D 场景跑完后现场看板确有该工段的推送行，
   且 8D 工单责任人按归因工段动态追加。

测试隔离：推送行统一落 ``tmp_path`` 的临时治理库（``BoardNotificationChannel(db_path=…)``）；
批次反推读的是演示库（``settings.DB_URL``）——那是只读的种子数据，不污染。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mfg_agents.v2.core.shared_context import SharedContext
from mfg_agents.v2.services import site_board
from mfg_agents.v2.services.notification import (
    BoardNotificationChannel,
    LogNotificationChannel,
    MultiChannel,
    NotificationService,
)

#: 演示库里的客诉种子（``common/init_db.py``：CS-20261012-01 / BATCH-A-1008 /
#: 华为终端 / A型号；该批次的过程不良 line_code=SMT-2 → 工段 SMT）。
SEED_COMPLAINT = {
    "complaint_no": "CS-20261012-01",
    "customer": "华为终端",
    "product_model": "A型号",
    "batch_no": "BATCH-A-1008",
    "defect_desc": "贴片元件偏移导致功能失效，不良率约2.3%",
    "status": "open",
}


@pytest.fixture()
def board_db(tmp_path) -> str:
    """临时治理库（推送行落这里，不碰仓库内的 data/ 库）。"""
    return str(tmp_path / "wf_site_board.db")


def _board_notifier(board_db: str) -> NotificationService:
    """按 orchestrator 默认装配的同一组合方式构造通知服务。"""
    return NotificationService(MultiChannel([
        LogNotificationChannel(),
        BoardNotificationChannel(db_path=board_db),
    ]))


# ---------------- 一、归因 ----------------


def test_stage_prefix_matches_dashboard_like_patterns():
    """漂移守卫：工段 → 产线前缀与制造看板的 LIKE 模式必须逐项一致。"""
    from mfg_agents.v2.services import dashboard

    derived = {stage: f"{prefix}%"
               for prefix, stage in site_board.STAGE_LINE_PREFIX.items()}
    assert derived == dashboard._MFG_STAGE_LINE_LIKE  # noqa: SLF001 - 交叉校验用


def test_resolve_stage_three_layer_order():
    """显式工段优先于产线前缀；前缀大写不敏感；认不出就返回空串（不猜）。"""
    assert site_board.resolve_stage(process_stage="PACK") == "PACK"
    assert site_board.resolve_stage(process_stage="SMT", line_code="DIP-2") == "SMT"
    assert site_board.resolve_stage(line_code="DIP-2") == "PCM"
    assert site_board.resolve_stage(line_code="assy-7") == "整机"
    assert site_board.resolve_stage(line_code="LINE-9") == ""
    assert site_board.resolve_stage() == ""
    assert site_board.STAGES == ("SMT", "PCM", "PACK", "整机")


def test_resolve_stage_from_batch_reads_seed_db():
    """批次反推：按该批次不良量最大的行所属工段判定；未知批次返回空串。"""
    main = site_board._main_db_path()  # noqa: SLF001 - 判定测试前置条件
    if not main or not Path(main).exists():
        pytest.skip(f"演示库不可用（{main or '非 SQLite 目标'}），批次反推无从验证")

    assert site_board.resolve_stage_from_batch("BATCH-A-1008") == "SMT"
    assert site_board.resolve_stage(batch_no="BATCH-A-1008") == "SMT"
    assert site_board.resolve_stage_from_batch("BATCH-NOT-EXIST") == ""


# ---------------- 二、客诉 → 工段 全链路 ----------------


def test_dispatch_complaint_end_to_end(board_db):
    """客诉归因到 SMT → 推送行落库 → 现场 ack → 闭环，且非法流转被拒。"""
    result = site_board.dispatch_complaint(
        _board_notifier(board_db), SEED_COMPLAINT, main_db_path=None)

    assert result["ok"] is True
    assert result["stage"] == "SMT"
    assert len(result["push_ids"]) == 1

    rows = site_board.list_by_stage("SMT", db_path=board_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["push_id"] == result["push_ids"][0]
    assert row["status"] == "pushed"
    assert row["complaint_no"] == "CS-20261012-01"
    assert row["batch_no"] == "BATCH-A-1008"
    assert row["source"] == "complaint_8d"
    # 客户身份走受控列（读接口按岗位脱敏它），**不进正文**——正文是自由文本，
    # 脱敏规则改不动；正文只留"哪批 / 什么缺陷 / 怎么处置"。
    assert row["customer"] == "华为终端"
    assert "华为终端" not in row["body"]
    assert "BATCH-A-1008" in row["body"]

    acked = site_board.ack(row["push_id"], actor="邵允川", note="已围堵首件",
                           db_path=board_db)
    assert acked["status"] == "acked"
    assert acked["ack_by"] == "邵允川"
    assert acked["ack_at"]

    closed = site_board.close(row["push_id"], actor="邵允川",
                              feedback="首批隔离完成，措施已落地", db_path=board_db)
    assert closed["status"] == "closed"
    assert closed["feedback"] == "首批隔离完成，措施已落地"

    with pytest.raises(site_board.InvalidSiteBoardTransition):
        site_board.ack(row["push_id"], actor="重复接收", db_path=board_db)


def test_dispatch_without_stage_returns_data_gap(board_db):
    """线索不足（无工段/产线/批次）时如实返回 data_gap，不推错工段、不抛错。"""
    result = site_board.dispatch_complaint(
        _board_notifier(board_db), {"complaint_no": "CS-NO-HINT", "customer": "某客户"})

    assert result["ok"] is False
    assert "未能归因到工段" in result["data_gap"]
    assert result["push_ids"] == []
    for stage in site_board.STAGES:
        assert site_board.list_by_stage(stage, db_path=board_db) == []


def test_dispatch_without_board_channel_reports_data_gap(board_db):
    """通道未接现场看板时不得静默成功——配置错误必须显式暴露。"""
    notifier = NotificationService(LogNotificationChannel())
    result = site_board.dispatch_complaint(notifier, SEED_COMPLAINT)

    assert result["ok"] is False
    assert result["stage"] == "SMT"
    assert "未接入" in result["data_gap"]
    assert result["push_ids"] == []


# ---------------- 三、通道过滤与幂等 ----------------


def test_channel_only_pushes_stage_targets(board_db):
    """非工段 target（岗位角色）如实跳过，工段 target 落一条推送行。"""
    channel = BoardNotificationChannel(db_path=board_db)
    record = channel.send("P1", ["质量总监", "SMT", "QA"], "客诉处置提醒", "请现场确认")

    assert record["via"] == "site_board"
    assert record["skipped"] == ["质量总监", "QA"]
    assert [item["stage"] for item in record["pushed"]] == ["SMT"]

    rows = site_board.list_by_stage("SMT", db_path=board_db)
    assert len(rows) == 1
    assert rows[0]["source"] == "notification"


def test_push_message_idempotent_keeps_site_progress(board_db):
    """重推（重试/重放）只刷新内容，不打回现场已推进的状态。"""
    pid = site_board.push_message(stage="SMT", title="初版", body="初版正文",
                                  ref_id="CS-1", db_path=board_db)
    site_board.ack(pid, actor="现场", db_path=board_db)
    site_board.push_message(stage="SMT", title="修订版", body="修订版正文",
                            ref_id="CS-1", db_path=board_db)

    row = site_board.get_push(pid, db_path=board_db)
    assert row["status"] == "acked"
    assert row["title"] == "修订版"
    assert len(site_board.list_by_stage("SMT", db_path=board_db)) == 1


def test_generic_notice_dedup_by_content(board_db):
    """无业务引用的通知按内容指纹去重：同内容一行，异内容各行。"""
    channel = BoardNotificationChannel(db_path=board_db)
    channel.send("P1", ["SMT"], "停线告知", "SMT-2 预计 30 分钟复线")
    channel.send("P1", ["SMT"], "停线告知", "SMT-2 预计 30 分钟复线")
    channel.send("P1", ["SMT"], "停线告知", "SMT-2 预计 60 分钟复线")

    assert len(site_board.list_by_stage("SMT", db_path=board_db)) == 2


# ---------------- 四、非法输入与缺项 ----------------


def test_invalid_stage_and_missing_push_rejected(board_db):
    with pytest.raises(site_board.SiteBoardError):
        site_board.push_message(stage="未知工段", title="x", db_path=board_db)
    with pytest.raises(site_board.SiteBoardError):
        site_board.list_by_stage("未知工段", db_path=board_db)
    with pytest.raises(site_board.PushNotFound):
        site_board.get_push("SB-none-SMT", db_path=board_db)


def test_default_runtime_wires_board_channel():
    """装配守卫：默认运行时的通知通道必须并联现场看板通道（掉了就没推送行）。"""
    from mfg_agents.v2.core.orchestrator import V2Runtime

    channel = V2Runtime.create_default().notifier.channel
    assert isinstance(channel, MultiChannel)
    assert any(isinstance(c, BoardNotificationChannel) for c in channel.channels)


# ---------------- 五、DAG 分派步骤（T03） ----------------

USER_CTX = {"user_id": "u-gm", "dept": "management", "roles": ["coordinator"]}


def test_batch_regex_matches_quality_agent():
    """漂移守卫：现场分派的批号识别与 QA 牵头必须同口径（否则两边认不同批次）。"""
    from mfg_agents.v2.agents.quality import _BATCH_RE

    assert site_board._BATCH_RE.pattern == _BATCH_RE.pattern  # noqa: SLF001


class _FakeQueryResult:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows


class _FakeText2SQL:
    """只回固定客诉行的替身（真实 SQL 通道由 test_scenarios_v2 覆盖）。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def query_registered(self, query_id: str, params: dict, user_ctx: dict):
        self.calls.append(str(query_id))
        return _FakeQueryResult(self.rows)


class _FakeTask:
    def __init__(self, query: str = "", entities: dict | None = None) -> None:
        self.query = query
        self.entities = entities or {}
        self.user_ctx = dict(USER_CTX)


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


def test_site_dispatch_handler_pushes_and_exposes_stage(board_db):
    """分派节点：归因到 SMT → 落推送行，并把工段写进 payload 供 8D 工单追加责任人。"""
    t2s = _FakeText2SQL([{
        "complaint_no": "CS-20261012-01", "customer": "华为终端",
        "product_model": "A型号", "batch_no": "BATCH-A-1008",
        "defect_desc": "贴片元件偏移导致功能失效，不良率约2.3%", "status": "open",
    }])
    handler = site_board.make_site_dispatch(_board_notifier(board_db), t2s)
    sink = _RecordingSink()
    out = handler(_FakeTask(), SharedContext(), sink)

    assert out["domain"] == "site_board"
    assert out["status"] == "ok"
    assert out["payload"]["site_stage"] == "SMT"
    assert out["payload"]["site_assignees"] == ["SMT"]
    assert out["payload"]["site_complaint_no"] == "CS-20261012-01"
    assert "data_gap" not in out["payload"]

    rows = site_board.list_by_stage("SMT", db_path=board_db)
    assert [r["push_id"] for r in rows] == ["SB-CS-20261012-01-SMT"]
    assert rows[0]["source"] == "complaint_8d"
    assert any(e.get("type") == "tool_end" for e in sink.events)


def test_site_dispatch_handler_reports_gap_without_clue(board_db):
    """线索不足（无客诉单、无批号、无产线）时如实标 data_gap，不落任何推送行。"""
    handler = site_board.make_site_dispatch(_board_notifier(board_db),
                                           _FakeText2SQL([]))
    out = handler(_FakeTask(query="客户投诉了"), SharedContext(), _RecordingSink())

    assert out["status"] == "degraded"
    assert out["payload"]["data_gap"] and "现场分派" in out["payload"]["data_gap"][0]
    for stage in site_board.STAGES:
        assert site_board.list_by_stage(stage, db_path=board_db) == []


@pytest.fixture()
def complaint_scenario_run(tmp_path, monkeypatch):
    """在**隔离治理库**上跑一次真实客诉 8D 场景（返回编排结果与看板库路径）。

    不隔离的话推送行会写进仓库的 ``data/mfg_agent.db``，测试之间互相污染。
    """
    from mfg_agents.common.config import settings
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator

    board_db = str(tmp_path / "wf_complaint.db")
    monkeypatch.setattr(settings, "WORKFLOW_DB", board_db)
    result = create_default_orchestrator().handle("华为投诉BATCH-A-1008", USER_CTX)
    return result, board_db


def test_complaint_scenario_reaches_site_board(complaint_scenario_run):
    """端到端：客诉走完 8D 场景后，SMT 现场看板同时收到处置令与整改工单通知。"""
    result, board_db = complaint_scenario_run

    assert result.error is None
    assert result.scenario_key == "customer_complaint_v2"
    assert "dispatch_site" in result.timings, "分派节点未进入执行路径"

    sources = {r["source"] for r in site_board.list_by_stage("SMT", db_path=board_db)}
    assert sources == {"complaint_8d", "notification"}
    complaint_row = site_board.get_push("SB-CS-20261012-01-SMT", board_db)
    assert complaint_row["status"] == "pushed"
    assert complaint_row["complaint_no"] == "CS-20261012-01"

    # 8D 工单责任人 = 静态清单 + 归因工段（现场责任落到具体工段）
    notified = result.workflow_tasks[0]["notified"]
    assert {"QA", "PQE", "SQE", "工程", "业务"} <= set(notified)
    assert "SMT" in notified

    # 分派成功不额外制造数据缺口：唯一真实缺口仍是工程根因（既有基线不破）
    gaps = [p for p in result.provenance if p.get("channel") == "data_gap"]
    assert len(gaps) == 1 and gaps[0]["domain"] == "rd_engineering"


# ---------------- 六、接口层（T04：读 + 现场回写 + 按岗位脱敏） ----------------

SEED_ROW = {
    "stage": "SMT", "title": "[客诉] CS-20261012-01 · SMT 现场处置",
    "body": "型号 A型号｜批次 BATCH-A-1008\n缺陷 贴片元件偏移\n请 SMT 现场确认围堵",
    "source": "complaint_8d", "ref_id": "CS-20261012-01",
    "complaint_no": "CS-20261012-01", "batch_no": "BATCH-A-1008",
    "customer": "华为终端",
}


@pytest.fixture(scope="module")
def board_client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


@pytest.fixture()
def board_api(tmp_path, monkeypatch, board_client):
    """接口测试的隔离现场：治理库指向临时文件 + 预置一条客诉推送行。"""
    from mfg_agents.common.config import settings

    db_path = str(tmp_path / "wf_api.db")
    monkeypatch.setattr(settings, "WORKFLOW_DB", db_path)
    push_id = site_board.push_message(**SEED_ROW, db_path=db_path)
    return board_client, db_path, push_id


def _auth(board_client, *roles: str) -> dict[str, str]:
    resp = board_client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def test_requires_token_and_denies_outsider(board_api):
    """业务数据不接受匿名读取：无令牌 401；岗位不在范围 403（不降级成 200 空数据）。"""
    client, _, _ = board_api
    assert client.get("/api/v2/site-board").status_code == 401
    resp = client.get("/api/v2/site-board", headers=_auth(client, "admin_hr"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["dashboard"] == "site_board"
    assert resp.json()["detail"]["allowed_roles"], "403 必须告诉对方谁能看"

    deny = client.post(f"/api/v2/site-board/SB-x-SMT/ack", json={},
                       headers=_auth(client, "admin_hr"))
    assert deny.status_code == 403, "回写端点与读端点同一套裁决"


def test_unknown_stage_and_status_rejected(board_api):
    """脏参不静默当「全部」：未知工段 / 未知状态一律 400。"""
    client, _, _ = board_api
    headers = _auth(client, "manufacturing")
    assert client.get("/api/v2/site-board?stage=XXX", headers=headers).status_code == 400
    assert client.get("/api/v2/site-board?status=nope", headers=headers).status_code == 400


def test_customer_masked_for_site_role_visible_for_gm(board_api):
    """客户名按岗位脱敏：制造岗位看到「客户 A」，总经理看到真名（写接口同样脱敏）。"""
    client, _, push_id = board_api
    mine = client.get("/api/v2/site-board?stage=SMT", headers=_auth(client, "manufacturing"))
    assert mine.status_code == 200
    body = mine.json()
    assert body["scope"]["masked"], "制造岗位必须报出已脱敏字段"
    assert len(body["data"]["rows"]) == 1
    assert body["data"]["rows"][0]["customer"].startswith("客户 ")

    gm = client.get("/api/v2/site-board?stage=SMT", headers=_auth(client, "gm"))
    assert gm.json()["scope"]["masked"] == []
    assert gm.json()["data"]["rows"][0]["customer"] == "华为终端"

    acked = client.post(f"/api/v2/site-board/{push_id}/ack", json={},
                        headers=_auth(client, "manufacturing"))
    assert acked.status_code == 200
    assert acked.json()["data"]["rows"][0]["customer"].startswith("客户 "), \
        "回写响应不得绕过脱敏"


def test_ack_then_feedback_closes_the_loop(board_api):
    """现场接收 → 处置闭环；空结论 400、未接收就闭环 409、不存在的推送行 404。"""
    client, _, push_id = board_api
    headers = _auth(client, "manufacturing")

    early = client.post(f"/api/v2/site-board/{push_id}/feedback",
                        json={"feedback": "已围堵"}, headers=headers)
    assert early.status_code == 409, "未接收不得直接闭环"

    empty = client.post(f"/api/v2/site-board/{push_id}/feedback",
                        json={"feedback": "   "}, headers=headers)
    assert empty.status_code == 400

    ack = client.post(f"/api/v2/site-board/{push_id}/ack",
                      json={"note": "已围堵首件"}, headers=headers)
    assert ack.status_code == 200
    row = ack.json()["data"]["rows"][0]
    assert row["status"] == "acked" and row["ack_by"]
    assert row["ack_at"]

    done = client.post(f"/api/v2/site-board/{push_id}/feedback",
                       json={"feedback": "首批隔离完成，措施已落地"}, headers=headers)
    assert done.status_code == 200
    closed = done.json()["data"]["rows"][0]
    assert closed["status"] == "closed"
    assert closed["feedback"] == "首批隔离完成，措施已落地"

    again = client.post(f"/api/v2/site-board/{push_id}/ack", json={}, headers=headers)
    assert again.status_code == 409, "闭环后不得再次接收"

    missing = client.post("/api/v2/site-board/SB-nope-SMT/ack", json={}, headers=headers)
    assert missing.status_code == 404
