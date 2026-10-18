"""数据范围守卫：四个看板端点的按岗位裁决与字段脱敏。

为什么单独一个文件：menus 层的导航收敛（``app.js`` 的 ``NAV_ITEMS[].roles``）
只是**展示归属**，挡不住直接调接口。这里守的是**服务端**的那一半：

1. 目录自检：角色写错 / 路径写错 / pseudo 未定义 → 启动即失败，不静默降级；
2. fail-closed：未声明的看板一律拒绝；无令牌 401；无权限 403（不是 200 空数据）；
3. 契约：``api.py`` 里每个 ``/api/v2/dashboard/*`` 端点都必须在目录里登记；
4. 脱敏：客户名按岗位替换为稳定代号，**同时移除下钻键**（只藏名字等于没藏）；
5. 口径一致：同一份数据里同一客户在风险清单与排行中的代号必须相同。

快照断言（``_EXPECTED``）是刻意的：授权集合一旦被人改松，测试必须红，
由人来确认那是一次产品决策，而不是一次手滑。
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from mfg_agents.v2.services.data_scope import (
    DataScopeError,
    DataScopeRegistry,
    get_data_scope,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "mfg_agents"

#: 当前的产品口径（改这里 = 改授权，必须过评审）
_EXPECTED = {
    "manufacturing": {
        "gm", "manufacturing", "equipment", "equipment_manager", "maintenance_tech",
        "quality_director", "pqe", "product_engineering", "ie_engineer",
        "test_engineer", "rd_manager", "rd_engineering", "me_engineer",
        "rd_test_engineer", "npi_engineer", "project_manager", "ehs",
    },
    "quality": {
        "gm", "manufacturing", "equipment", "equipment_manager", "maintenance_tech",
        "quality_director", "pqe", "product_engineering", "ie_engineer",
        "test_engineer", "rd_manager", "rd_engineering", "me_engineer",
        "rd_test_engineer", "npi_engineer", "project_manager", "ehs",
    },
    "orders": {
        "gm", "manufacturing", "sales", "customer_service", "project_manager",
        "planning", "material_control", "supply_chain_manager",
        "procurement_manager", "procurement", "warehouse_manager", "warehouse",
        "finance_manager", "finance",
    },
    "spc": {
        "gm", "manufacturing", "quality_director", "pqe", "sqe", "qa",
        "product_engineering", "test_engineer", "ie_engineer",
    },
}

#: 承担对客 / 收款责任、可见客户真名的岗位
_CUSTOMER_VISIBLE = {"gm", "sales", "customer_service", "project_manager",
                     "finance", "finance_manager"}


@pytest.fixture(scope="module")
def registry():
    return get_data_scope(force_reload=True)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


def _token(client, *roles: str) -> str:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _get(client, path: str, roles: tuple[str, ...]):
    headers = {"Authorization": f"Bearer {_token(client, *roles)}"}
    return client.get(path, headers=headers)


# ============================================================
# 1. 目录自检（配置写错必须在启动时炸，而不是演示现场返回空）
# ============================================================

def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "scope.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_registry_rejects_unknown_role(tmp_path):
    p = _write(tmp_path, """
version: 1
pseudo: {customers: 客户}
dashboards:
  orders:
    allow_roles: [gm, not_a_real_role]
""")
    with pytest.raises(DataScopeError, match="未识别角色"):
        DataScopeRegistry.from_yaml(p)


def test_registry_rejects_unparsable_mask_path(tmp_path):
    p = _write(tmp_path, """
version: 1
pseudo: {customers: 客户}
dashboards:
  orders:
    allow_roles: [gm]
    mask:
      - path: "risk_orders[[].customer"
        pseudo: customers
""")
    with pytest.raises(DataScopeError, match="无法解析"):
        DataScopeRegistry.from_yaml(p)


def test_registry_rejects_undefined_pseudo_prefix(tmp_path):
    p = _write(tmp_path, """
version: 1
pseudo: {customers: 客户}
dashboards:
  orders:
    allow_roles: [gm]
    mask:
      - path: risk_orders[].customer
        pseudo: suppliers
""")
    with pytest.raises(DataScopeError, match="未在 pseudo 表中定义"):
        DataScopeRegistry.from_yaml(p)


def test_registry_rejects_mask_without_replacement(tmp_path):
    p = _write(tmp_path, """
version: 1
dashboards:
  orders:
    allow_roles: [gm]
    mask:
      - path: risk_orders[].customer
""")
    with pytest.raises(DataScopeError, match="pseudo 或 literal"):
        DataScopeRegistry.from_yaml(p)


# ============================================================
# 2. 授权口径快照 + 结构性约束
# ============================================================

def test_allow_sets_match_product_decision(registry):
    assert set(registry.keys()) == set(_EXPECTED)
    for key, expected in _EXPECTED.items():
        assert set(registry.get(key).allow_roles) == expected, key


def test_manufacturing_and_quality_share_role_set(registry):
    """两个 tab 同属一屏：角色集合不一致会让「切 tab 就 403」，属于设计缺陷。"""
    assert (set(registry.get("manufacturing").allow_roles)
            == set(registry.get("quality").allow_roles))


def test_unknown_dashboard_is_denied(registry):
    """fail-closed：没登记过的看板谁也用不了（含总经理）。"""
    assert registry.get("nope") is None
    assert registry.is_allowed("nope", ["gm"]) is False


def test_admin_hr_cannot_see_any_dashboard(registry):
    """原始质疑：「HR 凭什么看 SPC / 稼动率」——现在服务端有答案了。"""
    for key in _EXPECTED:
        assert registry.is_allowed(key, ["admin_hr"]) is False, key


def test_every_allow_role_is_a_permission_role(registry):
    from mfg_agents.v2.contracts.permissions import get_permission_registry

    known = {r["key"] for r in get_permission_registry().describe()["roles"]}
    for key in registry.keys():
        unknown = set(registry.get(key).allow_roles) - known
        assert not unknown, f"{key} 引用了权限目录外的角色: {unknown}"


def test_api_endpoints_are_all_declared(registry):
    """``api.py`` 里新增看板端点却忘了登记 → 该端点对所有人 403。

    这条守卫的意义：让「新增看板」这件事无法悄悄绕过数据范围目录。
    """
    source = (_SRC / "v2" / "demo" / "api.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'@app\.get\("/api/v2/dashboard/([a-z_]+)"\)', source))
    declared.discard("drill")          # 下钻按 screen 复用看板裁决，不单独登记
    assert declared, "未扫到任何看板端点，正则失效？"
    missing = declared - set(registry.keys())
    assert not missing, f"这些看板端点未在 data_scope.yaml 登记: {missing}"


# ============================================================
# 3. 接口裁决：401 / 403 / 200
# ============================================================

@pytest.mark.parametrize("dashboard", sorted(_EXPECTED))
def test_dashboard_requires_token(client, dashboard):
    """业务聚合不接受匿名读取：缺令牌一律 401（不是 200 空数据）。"""
    resp = client.get(f"/api/v2/dashboard/{dashboard}")
    assert resp.status_code == 401


@pytest.mark.parametrize("dashboard", sorted(_EXPECTED))
def test_dashboard_denied_for_hr(client, dashboard):
    resp = _get(client, f"/api/v2/dashboard/{dashboard}", ("admin_hr",))
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["dashboard"] == dashboard
    assert "行政人事" in detail["message"]
    assert detail["allowed_roles"], "403 必须告诉对方谁能看"


@pytest.mark.parametrize("dashboard", sorted(_EXPECTED))
def test_dashboard_allowed_for_gm(client, dashboard):
    resp = _get(client, f"/api/v2/dashboard/{dashboard}", ("gm",))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["scope"] == {"dashboard": dashboard, "roles": ["gm"], "masked": []}


def test_hr_drill_denied(client):
    """看板进不去、下钻却能拿 = 后门。下钻必须与看板同源裁决。"""
    resp = _get(client,
                "/api/v2/dashboard/drill?screen=spc&kpi=spc_capability",
                ("admin_hr",))
    assert resp.status_code == 403


# ============================================================
# 4. 字段脱敏
# ============================================================

def _customers(payload) -> list[str]:
    return [r["customer"] for r in (payload.get("risk_orders") or [])]


def test_order_customer_masked_for_warehouse(client):
    resp = _get(client, "/api/v2/dashboard/orders", ("warehouse",))
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"]["masked"], "仓库岗位必须报出已脱敏字段"
    names = _customers(body["data"])
    assert names, "样本数据应有风险订单"
    assert all(n.startswith("客户 ") for n in names), names
    # 等级被替换为占位符，不泄露合同分级
    assert {r["customer_level"] for r in body["data"]["risk_orders"]} == {"—"}


def test_order_customer_visible_for_sales(client):
    body = _get(client, "/api/v2/dashboard/orders", ("sales",)).json()
    assert body["scope"]["masked"] == []
    assert all(n and not n.startswith("客户 ") for n in _customers(body["data"]))


def test_masked_customer_code_is_consistent_within_payload(client):
    """同一客户在风险清单与排行里必须是同一代号，否则一比对就还原。"""
    body = _get(client, "/api/v2/dashboard/orders", ("warehouse",)).json()
    data = body["data"]
    risk = {r["customer"] for r in data["risk_orders"]}
    otd = next((r for r in data["rankings"] if r["key"] == "otd"), None)
    assert otd is not None
    ranked = {i["name"] for i in otd["items"]}
    assert ranked <= risk, f"排行代号未复用风险清单代号：{ranked - risk}"


def test_masked_rows_lose_drill_entity(client):
    """名字藏了、下钻键还在 = 点一下就还原真名。键必须一起删。"""
    data = _get(client, "/api/v2/dashboard/orders", ("warehouse",)).json()["data"]
    otd = next(r for r in data["rankings"] if r["key"] == "otd")
    for item in otd["items"]:
        assert item.get("masked") is True, item
        assert "entity" not in item, item


def test_unmasked_rows_keep_drill_entity(client):
    """反向约束：不该脱敏的维度不能被误伤（齐套率是型号维度，与客户无关）。"""
    data = _get(client, "/api/v2/dashboard/orders", ("warehouse",)).json()["data"]
    kit = next(r for r in data["rankings"] if r["key"] == "kit_rate")
    assert kit["items"], "齐套率应有排行项"
    for item in kit["items"]:
        assert item.get("entity"), item
        assert not item.get("masked")


def test_drill_rows_masked_consistently(client):
    """下钻明细与排行同源脱敏：否则「排行看不到、下钻表全列出来」。"""
    resp = _get(client,
                "/api/v2/dashboard/drill?screen=orders&kpi=otd",
                ("warehouse",))
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"]["masked"], "下钻载荷必须报出脱敏字段"
    rows = body["data"]["rows"]
    assert rows, "OTD L2 应有分解行"
    for row in rows:
        assert row["name"].startswith("客户 "), row
        assert "entity" not in row, row


def test_drill_rows_visible_for_sales(client):
    body = _get(client,
                "/api/v2/dashboard/drill?screen=orders&kpi=otd",
                ("sales",)).json()
    assert body["scope"]["masked"] == []
    assert all("·" in r["name"] for r in body["data"]["rows"][:3])


def test_mask_does_not_touch_other_dashboards(registry):
    """脱敏规则只作用于声明它的看板：SPC 不应因订单规则被误改。"""
    payload = {"screen": "spc", "kpis": [{"key": "cpk", "customer": "华为终端"}]}
    out, labels = registry.apply("spc", copy.deepcopy(payload), ["warehouse"])
    assert out == payload and labels == []


# ============================================================
# 5. 前端契约（403 不得被降级成演示数据）
# ============================================================

_UI = _SRC / "v2" / "demo" / "ui"


def _fn_block(source: str, header: str, size: int = 2200) -> str:
    start = source.index(header)
    return source[start:start + size]


@pytest.mark.parametrize("fn", ["loadOrdersDashboard", "loadSpcDashboard",
                                "loadMfgDashboard", "loadMfgDrill"])
def test_loader_handles_permission_denied(fn):
    """403 必须单独拦下并如实展示「无权查看」。

    否则它会落进"请求失败 → 切演示数据"的兜底：界面看起来一切正常、数据是编的，
    比直接报错严重得多（和健康检查那个假阳性同一类）。
    """
    src = (_UI / "screens.js").read_text(encoding="utf-8")
    block = _fn_block(src, f"async function {fn}")
    assert 'kind === "permission_denied"' in block, f"{fn} 少了 403 分支"
    assert "scopedDenied" in block, f"{fn} 未走统一的无权限占位"


@pytest.mark.parametrize("method", ["manufacturingDashboard", "qualityDashboard",
                                    "ordersDashboard", "spcDashboard"])
def test_dashboard_api_uses_scoped_getter(method):
    """看板请求走 `_getScoped`（401 静默补签一次），不直接 `_get`。

    后端重启会重签 AUTH_SECRET，所有令牌一起失效；直接 `_get` 会把 401 抛给
    看板的降级兜底，于是"刚重启后端"看到的是演示数据却标着真实链路。
    """
    src = (_UI / "app.js").read_text(encoding="utf-8")
    block = _fn_block(src, f"{method}()", 240)
    assert "_getScoped(" in block, f"{method} 未走补签重试"


def test_set_role_signs_token_before_render():
    """换岗位必须先补签令牌再渲染，否则首屏请求撞 401 → 回落演示数据。"""
    src = (_UI / "app.js").read_text(encoding="utf-8")
    block = _fn_block(src, "async function setRole", 1200)
    assert "await ensureSession()" in block
    assert block.index("await ensureSession()") < block.index("refreshScreen()")
