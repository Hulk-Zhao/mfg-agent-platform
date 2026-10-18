"""待办可见性守卫：谁能看到哪条工单。

与 ``test_数据范围.py`` 的分工：那个守「聚合看板能不能看」，这个守「逐条待办谁看得到」。
两者判据不同（业务域 vs 治理槽位），所以分开守。

体检时 `/api/v2/workflow/*` 的真实状态：

- ``GET /workflow/pending`` 的 ``role`` 来自 **query**，且不传就返回全量 621 条；
- ``GET /workflow/{task_id}`` 完全**无鉴权**，拿到 ID 就能读全文（客户名 / 批次号）；
- MCP 的 ``workflow.pending`` 允许 ``args.roles`` **覆盖** ``user_ctx.roles``。

三条通道叠加起来等于「自证式权限」：客户端说自己是质量总监，它就是。

本文件守四件事：

1. **身份以令牌为准**：无令牌 401；``role`` 只能收窄，扩宽 403；
2. **跨域不可见**：仓库岗看不到 8D，业务岗看不到停机窗口；详情取不到返回
   **404 而非 403**（403 会确认「ID 存在，只是你看不到」）；
3. **无治理职责 = 空队列**：不是 403，也不是「已全部处理」——是「本来就没有你的活」；
4. **三条通道同一口径**：HTTP 读接口 / HTTP 详情 / MCP 工具，任一处放松都必须红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mfg_agents.v2.services import workflow_scope

_SRC = Path(__file__).resolve().parents[2] / "src" / "mfg_agents"
_UI = _SRC / "v2" / "demo" / "ui"

#: 角色 → 治理槽位（改这里 = 改授权口径，必须过评审）
_EXPECTED_SLOTS = {
    "gm": {"restart", "eta", "disposition", "d8_close", "complaint_reply",
           "delivery_commit", "maintenance_window"},
    "admin_hr": set(),
    "warehouse": {"delivery_commit"},
    "qa_manager": {"d8_close", "disposition"},
}


@pytest.fixture(scope="module")
def engine():
    from mfg_agents.v2.demo.api import get_orchestrator

    return get_orchestrator().runtime.workflow


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


@pytest.fixture(scope="module")
def hitl_tasks(engine):
    tasks = engine.list_tasks(status="waiting_hitl")
    if not tasks:
        pytest.skip("库里没有 waiting_hitl 工单（先跑 seed）")
    return tasks


def _hdr(client, *roles: str) -> dict[str, str]:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


# ============================================================
# 1. 判据层：角色 → 治理槽位
# ============================================================

@pytest.mark.parametrize("role,expected", sorted(_EXPECTED_SLOTS.items()))
def test_slot_keys_for(engine, role, expected):
    """判据来自权限目录，不引入第二份配置。

    快照是刻意的：``permissions.yaml`` 一改，这里就红，逼人确认那是有意为之。
    """
    assert workflow_scope.slot_keys_for(engine.permission_registry, [role]) == expected


def test_hr_has_no_governance_responsibility(engine):
    """行政人事不持有任何 HITL 槽位权限——这是「待办必空」的根因，不是巧合。"""
    assert workflow_scope.slot_keys_for(
        engine.permission_registry, ["admin_hr", "hr"]) == set()


def test_unknown_role_yields_empty_slots(engine):
    """识别不了的角色不能当作「有权限」，否则拼错一个 key 就能看到全部。"""
    assert workflow_scope.slot_keys_for(
        engine.permission_registry, ["not_a_role", ""]) == set()


# ============================================================
# 2. 分类层：mine / domain / hidden
# ============================================================

def test_governed_task_cross_domain_is_hidden(engine, hitl_tasks):
    """跨域必须 hidden：仓库岗看 8D 关闭工单 = 数据泄漏。"""
    d8 = next((t for t in hitl_tasks if t.slot == "d8_close"), None)
    if d8 is None:
        pytest.skip("库里没有 d8_close 工单")
    assert workflow_scope.classify(engine, d8, ["warehouse"]) == workflow_scope.VIS_HIDDEN
    assert workflow_scope.classify(engine, d8, ["qa_manager"]) in (
        workflow_scope.VIS_MINE, workflow_scope.VIS_DOMAIN)


def test_ungoverned_task_is_shared_pool(engine, hitl_tasks):
    """未纳管的历史存量归入无主池：有治理职责的可见（否则永远沉底），HR 不可见。"""
    legacy = next((t for t in hitl_tasks if not t.is_governed()), None)
    if legacy is None:
        pytest.skip("库里没有未纳管工单")
    assert workflow_scope.classify(engine, legacy, ["gm"]) == workflow_scope.VIS_DOMAIN
    assert workflow_scope.classify(engine, legacy, ["admin_hr"]) == workflow_scope.VIS_HIDDEN


def test_no_governance_role_sees_nothing(engine, hitl_tasks):
    """无治理职责 → 全部 hidden，**包括无主池**。

    放松这条，HR 会重新看到全厂的 8D 与停机工单。
    """
    assert all(
        workflow_scope.classify(engine, t, ["admin_hr"]) == workflow_scope.VIS_HIDDEN
        for t in hitl_tasks)


def test_visible_tasks_drops_hidden(engine, hitl_tasks):
    pairs = workflow_scope.visible_tasks(engine, hitl_tasks, ["warehouse"])
    assert pairs, "仓库岗至少能看到交付承诺与无主池"
    assert all(v in (workflow_scope.VIS_MINE, workflow_scope.VIS_DOMAIN)
               for _, v in pairs)
    assert len(pairs) < len(hitl_tasks), "仓库岗不该看到全部工单"


# ============================================================
# 3. HTTP 读接口：fail-closed
# ============================================================

def test_pending_requires_token(client):
    """无令牌一律 401（原实现无令牌返回全量 621 条）。"""
    assert client.get("/api/v2/workflow/pending").status_code == 401


def test_pending_hr_gets_empty_queue(client):
    """HR 拿到的是空队列 **且 200**：不是「没资格看」，是「本来就没有你的活」。"""
    resp = client.get("/api/v2/workflow/pending", headers=_hdr(client, "admin_hr"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["scope"]["slots"] == []
    assert body["scope"]["counts"]["visible"] == 0


def test_pending_cannot_widen_scope(client):
    """`role` 只能收窄。能扩宽的话，fail-closed 就退化成自证式权限。"""
    resp = client.get("/api/v2/workflow/pending?role=pqe",
                      headers=_hdr(client, "warehouse"))
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["requested"] == ["pqe"]
    assert detail["granted"] == ["warehouse"]


def test_pending_narrowing_own_role_ok(client):
    """传自己的角色（相等）不算扩宽。前端就是这么调的。"""
    resp = client.get("/api/v2/workflow/pending?role=warehouse",
                      headers=_hdr(client, "warehouse"))
    assert resp.status_code == 200


def test_pending_unknown_role_is_400(client):
    resp = client.get("/api/v2/workflow/pending?role=随便谁",
                      headers=_hdr(client, "gm"))
    assert resp.status_code == 400


def test_pending_cross_domain_not_leaked(client):
    """响应里不得出现跨域工单——这是“HR 看到品质 8D”的直接回归点。"""
    resp = client.get("/api/v2/workflow/pending", headers=_hdr(client, "warehouse"))
    assert resp.status_code == 200
    slots = {((t.get("governance") or {}).get("slot") or "") for t in resp.json()["data"]}
    assert slots <= {"delivery_commit", ""}, f"仓库岗看到了跨域工单：{slots}"


def test_pending_scope_does_not_leak_hidden_count(client):
    """不返回「还有 N 条你看不到」：数量也是信息，会把权限变成可探测面。"""
    resp = client.get("/api/v2/workflow/pending", headers=_hdr(client, "warehouse"))
    counts = resp.json()["scope"]["counts"]
    assert set(counts) == {"mine", "domain", "visible"}


@pytest.mark.parametrize("role,expect_status", [
    (None, 401),            # 无令牌
    ("admin_hr", 404),      # 跨治理域 → 404（不是 403，避免确认 ID 存在）
    ("warehouse", 404),
])
def test_task_detail_visibility(client, engine, hitl_tasks, role, expect_status):
    d8 = next((t for t in hitl_tasks if t.slot == "d8_close"), None)
    if d8 is None:
        pytest.skip("库里没有 d8_close 工单")
    headers = _hdr(client, role) if role else {}
    resp = client.get(f"/api/v2/workflow/{d8.task_uid}", headers=headers)
    assert resp.status_code == expect_status


def test_task_detail_same_domain_ok(client, engine, hitl_tasks):
    d8 = next((t for t in hitl_tasks if t.slot == "d8_close"), None)
    if d8 is None:
        pytest.skip("库里没有 d8_close 工单")
    resp = client.get(f"/api/v2/workflow/{d8.task_uid}",
                      headers=_hdr(client, "qa_manager"))
    assert resp.status_code == 200
    assert resp.json()["data"]["task_uid"] == d8.task_uid


# ============================================================
# 4. MCP 通道：与 HTTP 同一口径
# ============================================================

@pytest.fixture(scope="module")
def mcp_tools():
    from mfg_agents.v2.demo.api import get_orchestrator
    from mfg_agents.v2.mcp_tools.tools import build_platform_tools

    return {t.name: t for t in build_platform_tools(get_orchestrator())}


def test_mcp_pending_requires_user(mcp_tools):
    from mfg_agents.v2.services.workflow import PermissionDenied

    with pytest.raises(PermissionDenied):
        mcp_tools["workflow.pending"].handler({}, {"user_id": ""})


def test_mcp_pending_cannot_widen(mcp_tools):
    """原实现 `args.get("roles") or user_ctx.get("roles")`：模型写个角色就把身份覆盖了。"""
    from mfg_agents.v2.services.workflow import PermissionDenied

    with pytest.raises(PermissionDenied):
        mcp_tools["workflow.pending"].handler(
            {"roles": ["pqe"]}, {"user_id": "u-1", "roles": ["warehouse"]})


def test_mcp_pending_respects_governance(mcp_tools):
    hr = mcp_tools["workflow.pending"].handler(
        {}, {"user_id": "u-1", "roles": ["admin_hr"]})
    assert hr["total"] == 0

    wh = mcp_tools["workflow.pending"].handler(
        {}, {"user_id": "u-1", "roles": ["warehouse"]})
    assert wh["total"] > 0
    assert all(t["slot"] in ("delivery_commit", "")
               for t in wh["tasks"])


def test_platform_describe_has_no_global_pending_count(mcp_tools):
    """能力发现接口不该顺带汇报全公司治理负载。"""
    out = mcp_tools["platform.describe"].handler({}, {})
    assert "workflow_pending_total" not in out
    assert out["workflow_pending_visible"] == 0


# ============================================================
# 5. 全局守卫：没有第二个裸奔端点
# ============================================================

def test_all_workflow_get_endpoints_require_identity():
    """``api.py`` 里每个 ``/workflow/*`` GET 都必须先 ``_require_identity``。

    这条兜住「以后再加一个读接口忘了鉴权」——新增端点不会自动被前 4 组覆盖。
    """
    src = (_SRC / "v2" / "demo" / "api.py").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=@app\.(?:get|post|put|delete)\()", src)
    checked = 0
    for block in blocks:
        m = re.search(r'@app\.get\("([^"]*workflow[^"]*)"\)', block)
        if not m:
            continue
        checked += 1
        assert "_require_identity(request)" in block, (
            f"{m.group(1)} 没有校验令牌：所有读接口都必须 fail-closed")
    assert checked >= 2, "没扫到 workflow GET 端点，正则可能失效了"


# ============================================================
# 6. 前端契约：不许再拉全量
# ============================================================

def test_frontend_never_calls_pending_without_roles():
    """``workflowPending()`` 不带参数 = 全量队列。

    后端虽然会按令牌兜住，但前端这么写说明作者仍以为「不传就是我的」——
    这种误解迟早会被搬到别的接口上，所以在这一层也守一道。
    """
    for name in ("screens.js", "app.js"):
        src = (_UI / name).read_text(encoding="utf-8")
        assert "workflowPending()" not in src, (
            f"{name} 存在不带角色的待办请求（等于请求全量队列）")


def test_hitl_loader_uses_visibility_field():
    """审批中心必须按后端给的 `visibility` 分区，而不是自己猜 `my_action`。"""
    src = (_UI / "screens.js").read_text(encoding="utf-8")
    start = src.index("async function loadHitlTasks")
    block = src[start:start + 2000]
    assert 'visibility === "mine"' in block
    assert "_readonly: true" in block


def test_todo_loader_passes_governance_roles():
    """工作台「待确认事项」必须带角色，否则 HR 会看到全厂待办。"""
    src = (_UI / "screens.js").read_text(encoding="utf-8")
    start = src.index("async function loadDashboardTodos")
    block = src[start:start + 1400]
    assert "workflowPending(govRoles(ctx))" in block


def test_workflow_requests_use_scoped_getter():
    """待办请求走 `_getScoped`（401 静默补签），否则后端一重启就整片 401。"""
    src = (_UI / "app.js").read_text(encoding="utf-8")
    for method in ("workflowPending", "workflowTask"):
        start = src.index(f"{method}(")
        block = src[start:start + 400]
        assert "_getScoped(" in block, f"{method} 未走补签重试"


# ============================================================
# 7. 导航与治理职责必须一致（两份数据交叉校验）
# ============================================================

def _frontend_role_map() -> dict[str, list[str]]:
    """解析 ``demo-data.js`` 的 ROLES：前端岗位 id → 权限角色 key。

    不用 eval JS：这里只需要 ``id`` 与紧随其后的 ``roles`` 数组，按 id 位置切片
    比写个 JS 解析器稳。
    """
    src = (_UI / "demo-data.js").read_text(encoding="utf-8")
    start = src.index("const ROLES = [")
    end = src.index("\n  ];", start)
    block = src[start:end]
    hits = [(m.start(), m.group(1))
            for m in re.finditer(r'\bid:\s*"([a-z]+)"', block)]
    out: dict[str, list[str]] = {}
    for i, (pos, rid) in enumerate(hits):
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(block)
        seg = block[pos:nxt]
        m = re.search(r"roles:\s*\[([^\]]*)\]", seg)
        out[rid] = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    return out


def _permission_map() -> dict[str, list[str]]:
    import yaml

    data = yaml.safe_load(
        (_SRC / "v2" / "data" / "permissions.yaml").read_text(encoding="utf-8"))
    return {r["key"]: list(r.get("permissions") or []) for r in data["roles"]}


def _nav_item_roles(item_id: str) -> list[str] | None:
    """取 ``app.js`` 里某个 NAV_ITEMS 条目的 roles（省略则返回 None = 全员可见）。"""
    src = (_UI / "app.js").read_text(encoding="utf-8")
    i = src.index(f'{{ id: "{item_id}"')
    j = src.index("}", i)
    m = re.search(r"roles:\s*\[([^\]]*)\]", src[i:j], re.S)
    if not m:
        return None
    return re.findall(r'"([^"]+)"', m.group(1))


def test_hitl_nav_matches_governance_permissions():
    """「审批确认中心」的可见岗位，必须与「持有 HITL 槽位权限的岗位」完全一致。

    两侧都是数据驱动、但驱动源不同（导航写前端岗位 id，判据读 permissions.yaml），
    所以必然有一天会有人改了权限目录却忘了改菜单。不一致的两个方向都糟：

    - 菜单多一个 → 点进去只有「本岗位不参与治理审批」，让人白跑一趟；
    - 菜单少一个 → 有权限却找不到入口，比报错更难发现。

    行政人事（admin_hr）当前不持有任何 ``hitl.*`` 权限，是这条规则最典型的样本。
    """
    front = _frontend_role_map()
    perms = _permission_map()
    expected = {
        rid for rid, keys in front.items()
        if any(p.startswith("hitl.") for k in keys for p in perms.get(k, []))
    }
    actual = _nav_item_roles("hitl")
    assert actual is not None, "审批确认中心必须声明 roles（否则全员可见）"
    assert set(actual) == expected, (
        f"导航与权限目录不一致：菜单多出 {sorted(set(actual) - expected)}，"
        f"缺少 {sorted(expected - set(actual))}")
    assert "hr" not in expected, "行政人事不持有 HITL 权限，不该出现在审批菜单里"


def test_nav_groups_are_contiguous():
    """同一个分组在 NAV_ITEMS 里必须连续出现。

    ``renderNav()`` 是「遇到 group 变化就输出组标题」，同组被拆开会让侧边栏出现
    两个同名的组标题。调 item 的 group 时忘了挪位置就会踩到，且肉眼很难第一时间发现。
    """
    src = (_UI / "app.js").read_text(encoding="utf-8")
    start = src.index("const NAV_ITEMS = [")
    end = src.index("\n  ];", start)
    groups = re.findall(r'group:\s*"([^"]+)"', src[start:end])
    assert groups, "没解析到分组，正则可能失效"
    seen: list[str] = []
    last = None
    for g in groups:
        if g != last:
            assert g not in seen, f"分组「{g}」被拆成了多段"
            seen.append(g)
            last = g
