"""B8：探测流量治理（限流）—— 审计链不被灌爆，同时不牺牲可追溯性。

**先说清楚为什么不采样**
B1 要求"不存在"与"存在但无权"两条路径做等价工作量（查询次数、判定调用、
审计写入都对齐），否则耗时差就是存在性预言机。补平之后代价是**每个 404 都写
一行审计并 commit**：外部扫 ``QA-0001..QA-9999`` 就是 1 万行 + 1 万次 fsync。

直觉解法是"负路径审计按 1/10 采样"。本文件把这个方案否掉了：少写一行审计，
B1 消掉的时间差就又回来了（采样过的 404 更快），治一个病添一个病。
因此治理落在**入口限流**——探测流量在到达服务层之前就被 429 挡住，
审计链该记的都记到，一条不少。

| 组 | 钉什么 |
| --- | --- |
| U | 进程内固定窗口的计数语义（纯函数层，不碰 HTTP） |
| H | 限流真的把探测流量挡在服务层之外（HTTP 层） |
| I | 限流**不得**牺牲可追溯性：到达服务层的请求必须留审计 |
| J | 限流不得误伤正常作业：正常检索/读取不受影响 |
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from mfg_agents.common.config import settings
from mfg_agents.v2.security import case_rate_limit

OPEN_CASE = "QC-CC-2026-0087"


# ---------------------------------------------------------------- 工具


def _session(client, role: str, dept: str = "", plant: str = "P1",
              user_id: str = "") -> str:
    body = {"role": role, "dept": dept, "plant": plant, "name": f"{role}-probe"}
    # user_id 每次唯一：限流桶 = (user_id, ip)。Redis 在线时限流计数**跨进程
    # 累积**，复用固定 user_id 会让本轮用例一开始就被上一轮流量的残留计数
    # 拦成 429 —— 那是测试污染，不是被测行为。
    body["user_id"] = user_id or f"probe-{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/v2/cases/session", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def tight_limit():
    """把阈值压到 5 次/分钟，并把进程内窗口清零（Redis 不在线时走本地计数）。"""
    saved = settings.CASE_RATE_LIMIT_PER_MIN
    settings.CASE_RATE_LIMIT_PER_MIN = 5
    case_rate_limit.reset_case_rate_limit()
    try:
        yield 5
    finally:
        settings.CASE_RATE_LIMIT_PER_MIN = saved
        case_rate_limit.reset_case_rate_limit()


def _audit_rows() -> int:
    conn = sqlite3.connect(str(settings.CASE_DB))
    try:
        return int(conn.execute("SELECT COUNT(1) FROM case_audit").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------- U：计数语义


def test_local_window_counts_then_blocks() -> None:
    case_rate_limit.reset_case_rate_limit()
    assert not case_rate_limit._local_exceeded("b1", limit=2, window_s=60)
    assert not case_rate_limit._local_exceeded("b1", limit=2, window_s=60)
    assert case_rate_limit._local_exceeded("b1", limit=2, window_s=60)
    case_rate_limit.reset_case_rate_limit()


def test_local_window_rolls_over() -> None:
    """窗口过期后计数归零（否则限流会永久锁死用户）。"""
    case_rate_limit.reset_case_rate_limit()
    assert not case_rate_limit._local_exceeded("b2", limit=1, window_s=60)
    assert case_rate_limit._local_exceeded("b2", limit=1, window_s=60)
    assert not case_rate_limit._local_exceeded("b2", limit=1, window_s=0)
    case_rate_limit.reset_case_rate_limit()


def test_bucket_is_user_and_ip() -> None:
    """桶键必须同时含 user 与 IP：单用 user 挡不住共享账号，单用 IP 会被 NAT 误伤。"""
    bucket = case_rate_limit.case_rate_limit_bucket(user_id="u-qa", client_ip="10.0.0.7")
    assert "u-qa" in bucket and "10.0.0.7" in bucket
    other = case_rate_limit.case_rate_limit_bucket(user_id="u-qa", client_ip="10.0.0.8")
    assert other != bucket


def test_non_positive_limit_disables_guard() -> None:
    """阈值 ≤0 视为关闭限流（仅本地调试用）。"""
    assert not case_rate_limit.check_case_rate_limit("b3", limit=0)


# ---------------------------------------------------------------- H：HTTP 层


def test_probe_flood_is_blocked_at_the_gate(case_client, tight_limit) -> None:
    """扫不存在的案例：前 N 次 404，之后 429 —— 审计链不会被灌爆。"""
    token = _session(case_client, "qa", dept="quality")
    headers = _auth(token)
    statuses = [case_client.get(f"/api/v2/cases/QA-{i:04d}", headers=headers).status_code
                for i in range(20)]
    assert 404 in statuses, "前几次应当是正常的 404（否则限流把正常路径也堵了）"
    assert 429 in statuses, f"探测流量没有被限流拦住：{statuses}"
    limited = [s for s in statuses if s == 429]
    # 越限后全部拦截，不允许出现"429 之后又 404"的漏口
    first_429 = statuses.index(429)
    assert all(s == 429 for s in statuses[first_429:])


def test_rate_limited_response_carries_stable_code(case_client, tight_limit) -> None:
    token = _session(case_client, "qa", dept="quality")
    headers = _auth(token)
    body = None
    for i in range(20):
        resp = case_client.get(f"/api/v2/cases/QB-{i:04d}", headers=headers)
        if resp.status_code == 429:
            body = resp.json()
            break
    assert body is not None
    assert body["detail"]["code"] == "CASE_RATE_LIMITED"


def test_limit_is_per_subject(case_client, tight_limit) -> None:
    """一个主体被限流，不应牵连另一个主体。"""
    a = _auth(_session(case_client, "qa", dept="quality"))
    b = _auth(_session(case_client, "equipment", dept="equipment"))
    for i in range(12):
        case_client.get(f"/api/v2/cases/QC-{i:04d}", headers=a)
    resp_b = case_client.get(f"/api/v2/cases/{OPEN_CASE}", headers=b)
    assert resp_b.status_code == 200, resp_b.text


# ---------------------------------------------------------------- I：可追溯性


def test_limiting_never_drops_audit_rows(case_client, tight_limit, monkeypatch) -> None:
    """限流不得牺牲审计：到达服务层的每个请求都必须留下痕迹。

    反过来验也成立——被 429 挡掉的那几次**不应**进审计（它们根本没到服务层，
    写了才是假的）。

    这里把负路径采样率调到 1.0 再断言全量：``case_audit`` 对 404（案例不存在）
    默认按 ``CASE_AUDIT_NOTFOUND_SAMPLE_RATE=0.1`` **采样**记录，这是刻意的安全设计
    ——否则用不存在的 ID 就能灌爆审计表。不关掉采样就断言"每个 404 都记账"，
    测的就不是"审计有没有丢"，而是"采样是不是 100%"。
    """
    monkeypatch.setattr(settings, "CASE_AUDIT_NOTFOUND_SAMPLE_RATE", 1.0,
                        raising=False)
    token = _session(case_client, "qa", dept="quality")
    headers = _auth(token)
    before = _audit_rows()
    reached = 0
    for i in range(20):
        resp = case_client.get(f"/api/v2/cases/QD-{i:04d}", headers=headers)
        if resp.status_code == 404:
            reached += 1
    added = _audit_rows() - before
    assert reached > 0, "本用例的前提是至少有一个请求真正到达服务层，否则它什么也没验到"
    assert added == reached, (
        f"审计行数 {added} != 实际到达服务层的 404 数 {reached}："
        "要么漏记（不可追溯），要么把被限流的请求也记了（造假）")


def test_chain_stays_valid_after_flood(case_client, tight_limit) -> None:
    token = _session(case_client, "qa", dept="quality")
    headers = _auth(token)
    for i in range(20):
        case_client.get(f"/api/v2/cases/QE-{i:04d}", headers=headers)
    resp = case_client.get("/api/v2/cases/audit/verify", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["chain"]["valid"] is True


# ---------------------------------------------------------------- J：不误伤


def test_normal_workflow_is_unaffected(case_client) -> None:
    """默认阈值下，正常作业（检索 + 详情 + 引用）不受影响。"""
    token = _session(case_client, "qa", dept="quality")
    headers = _auth(token)
    for _ in range(10):
        resp = case_client.post("/api/v2/cases/search",
                                json={"query": "锡膏印刷偏移 连锡", "k": 10},
                                headers=headers)
        assert resp.status_code == 200, resp.text
    detail = case_client.get(f"/api/v2/cases/{OPEN_CASE}", headers=headers)
    assert detail.status_code == 200, detail.text
    cite = case_client.post(f"/api/v2/cases/{OPEN_CASE}/cite",
                            json={"chunk_type": "summary"}, headers=headers)
    assert cite.status_code == 200, cite.text
