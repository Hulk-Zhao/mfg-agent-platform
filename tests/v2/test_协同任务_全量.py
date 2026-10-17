"""协同任务全量测试：6 个跨域协同场景。

按《跨域协同场景调研与设计》(docs/跨域协同场景调研与设计.md) 的功能需求，对"协同任务"
做全量验证，覆盖 5 个维度：

1. 场景 YAML 结构符合调研规格 §1.6（domains / fan_out 分支数 / HITL 槽位）；
2. 6 条标准触发问句经 TwoLevelRouter 命中 L0 且 scenario_key 正确；
3. HITL 可审批性（防"无人可批"）：槽位已注册、存在 *.approve 持有者、审批角色可解析；
4. 治理一致性（防"岗位→治理角色"漂移）：demo 登录岗位的治理角色 key 全部已注册，
   且前端 ROLE_LABEL 覆盖这些 key；
5. 前端产物 JS 语法校验。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mfg_agents.common.config import settings
from mfg_agents.common.llm_factory import reset_probe_cache
from mfg_agents.v2.agents.report import _BRIEF_SPEC
from mfg_agents.v2.contracts.permissions import get_permission_registry
from mfg_agents.v2.core.orchestrator import V2Orchestrator, V2Runtime

ROOT = Path(__file__).resolve().parents[2]
SCEN_DIR = ROOT / "src" / "mfg_agents" / "v2" / "scenarios"
UI_DIR = ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"

USER_CTX = {"user_id": "u-gm", "dept": "management", "roles": ["coordinator"]}

# 6 个协同场景的调研规格（§1.6）：domains / fan_out 分支数 / HITL 槽位
EXPECTED = {
    "line_stop_v2": {
        "domains": {"manufacturing", "quality", "supply_chain", "equipment"},
        "branches": 3, "slots": {"restart", "eta"}},
    "incoming_quality_v2": {
        "domains": {"quality", "supply_chain"},
        "branches": 3, "slots": {"disposition"}},
    "customer_complaint_v2": {
        "domains": {"quality", "rd_engineering", "supply_chain"},
        "branches": 4, "slots": {"d8_close", "complaint_reply"}},
    "delivery_status_v2": {
        "domains": {"supply_chain", "manufacturing"},
        "branches": 6, "slots": {"delivery_commit"}},
    "equipment_warning_v2": {
        "domains": {"manufacturing", "supply_chain", "rd_engineering"},
        "branches": 4, "slots": {"maintenance_window"}},
    "order_change_impact_v2": {
        "domains": {"supply_chain", "manufacturing"},
        "branches": 3, "slots": {"delivery_commit"}},
}

# 6 条标准触发问句 → 期望场景/域（L0 触发词路由证据）
ROUTES = [
    ("A型号在SMT-1停线，何时复线？", "line_stop_v2", "manufacturing"),
    ("今天锡膏批次有问题", "incoming_quality_v2", "quality"),
    ("华为投诉BATCH-A-1008", "customer_complaint_v2", "quality"),
    ("A型号能发多少货", "delivery_status_v2", "supply_chain"),
    ("SMT-1设备参数异常吗", "equipment_warning_v2", "manufacturing"),
    ("A型号插单，订单变更影响评估", "order_change_impact_v2", None),
]


def _load(key: str) -> dict:
    return yaml.safe_load((SCEN_DIR / f"{key}.yaml").read_text(encoding="utf-8"))


def _demo_role_gov_keys() -> list[str]:
    """解析 demo-data.js 中 ROLES[].roles 的治理角色 key（单一数据源）。

    仅取 ROLES 数组内的 roles 字段；ROLE_GROUPS 也用 roles: [...]，但其值是岗位 id
    （如 qd/mfg），不是治理角色 key，必须排除。
    """
    text = (UI_DIR / "demo-data.js").read_text(encoding="utf-8")
    block = re.search(r"const ROLES = \[(.*?)\n  \];", text, re.S)
    assert block, "未找到 demo-data.js 的 ROLES 数组"
    keys: list[str] = []
    for grp in re.findall(r"roles:\s*\[([^\]]*)\]", block.group(1)):
        keys += re.findall(r'"([^"]+)"', grp)
    return keys


@pytest.fixture()
def orchestrator(monkeypatch, tmp_path) -> V2Orchestrator:
    """离线 orchestrator：LLM 探针强制 False，工作流库指向临时 DB。"""
    reset_probe_cache()
    for mod in ("mfg_agents.common.llm_factory",
                "mfg_agents.v2.core.router",
                "mfg_agents.v2.core.node_registry"):
        monkeypatch.setattr(f"{mod}.llm_available", lambda: False)
    monkeypatch.setattr(settings, "WORKFLOW_DB",
                        f"sqlite:///{tmp_path / 'workflow.db'}")
    return V2Orchestrator(V2Runtime.create_default())


# ---------- 1. 场景结构符合调研规格 §1.6 ----------

@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_scenario_structure_matches_spec(key):
    doc = _load(key)
    spec = EXPECTED[key]
    assert set(doc["domains"]) == spec["domains"], f"{key} domains 与调研规格漂移"
    fanouts = [s for s in doc["steps"] if s.get("type") == "fan_out"]
    assert fanouts, f"{key} 缺少 fan_out（多智能体并行取证）"
    assert len(fanouts[0]["branches"]) == spec["branches"], f"{key} fan_out 分支数漂移"
    slots = {s["id"].replace("hitl_", "", 1)
             for s in doc["steps"] if s.get("type") == "hitl"}
    assert slots == spec["slots"], f"{key} HITL 槽位漂移"


# ---------- 2. 6 场景 L0 路由矩阵 ----------

@pytest.mark.parametrize("query,scenario,domain", ROUTES)
def test_l0_routing_matrix_all_six(orchestrator, query, scenario, domain):
    result = orchestrator.handle(query, USER_CTX)
    assert result.route["level"] == "L0", f"{query!r} 未命中 L0"
    assert result.scenario_key == scenario
    if domain:
        assert result.route["domain"] == domain


# ---------- 3. 6 场景端到端可运行（防"场景能路由但跑不起来"） ----------

@pytest.mark.parametrize("query,scenario,domain", ROUTES)
def test_scenario_end_to_end_runs(orchestrator, query, scenario, domain):
    """每个场景必须能完整跑完：出报告 + 建工单 + HITL 卡片数与规格一致。

    该用例覆盖 order_change_impact_v2 的 fan_out 分支声明方式 / workflow key 等
    结构性缺陷（此前该场景在 DAG 构建期即抛 KeyError，路由测试无法发现）。
    """
    result = orchestrator.handle(query, USER_CTX)
    assert result.scenario_key == scenario
    assert result.fallback is None, f"{scenario} 未执行成功（fallback={result.fallback}）"
    assert result.timings, f"{scenario} 无节点执行记录"
    assert len(result.hitl) == len(EXPECTED[scenario]["slots"]), \
        f"{scenario} HITL 卡片数 {len(result.hitl)} 与规格 {len(EXPECTED[scenario]['slots'])} 不符"
    assert len(result.workflow_tasks) >= 1, f"{scenario} 未创建协同工单"
    assert result.internal_brief, f"{scenario} 未生成内部简报"
    if "customer_report" in _load(scenario)["outputs"]:
        assert result.report, f"{scenario} 声明了 customer_report 但未生成报告正文"
    for card in result.hitl:
        assert card.get("actions"), f"{scenario} HITL 卡片无可选动作（无法人工决策）"
    # 简报字段必须来自本场景声明（防"报告 handler 串场景"，如订单变更误用停线口径）
    if scenario != "line_stop_v2":  # line_stop 沿用 V1 报告 handler（字段逐字节等价基线）
        spec_labels = {label for label, _ in _BRIEF_SPEC.get(scenario, [])}
        assert spec_labels, f"{scenario} 未在 _BRIEF_SPEC 声明简报字段（简报将为空或串场景）"
        mirrored = {k.replace("#structured", "") for k in result.internal_brief}
        extra = mirrored - spec_labels - {"数据缺口"}
        assert not extra, f"{scenario} 简报混入非本场景字段：{sorted(extra)}"


# ---------- 4. HITL 可审批性（防"无人可批"） ----------

def test_hitl_slots_are_approvable():
    reg = get_permission_registry()
    for key in EXPECTED:
        doc = _load(key)
        for step in doc["steps"]:
            if step.get("type") != "hitl":
                continue
            slot = step["id"].replace("hitl_", "", 1)
            assert reg.is_known_slot(slot), f"{key}:{step['id']} 槽位未注册"
            approve_perm = f"hitl.{slot}.approve"
            assert reg.permission(approve_perm) is not None, f"{slot} 缺 approve 权限"
            assert reg.roles_with_permission(approve_perm), \
                f"{slot} 无 approve 持有者（会签齐备后无人可终批）"
            for name in step.get("approver_roles", []):
                assert reg.normalize_role(name), \
                    f"{key}:{step['id']} 审批角色 {name!r} 未注册（fail-closed 将无法审批）"


# ---------- 4. 治理一致性（防"岗位→治理角色"漂移） ----------

def test_demo_login_roles_map_to_registered_governance_roles():
    reg = get_permission_registry()
    keys = _demo_role_gov_keys()
    assert keys, "未解析到 demo 登录岗位的治理角色 key"
    for k in keys:
        assert reg.role(k) is not None, f"登录岗位治理角色 {k!r} 未在 permissions.yaml 注册"


def test_frontend_role_label_covers_governance_roles():
    screens = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    m = re.search(r"const ROLE_LABEL = \{(.*?)\};", screens, re.S)
    assert m, "未找到前端 ROLE_LABEL"
    label_keys = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", m.group(1)))
    for k in _demo_role_gov_keys():
        assert k in label_keys, f"前端 ROLE_LABEL 缺治理角色 {k!r}"


# ---------- 5. 前端产物 JS 语法 ----------

@pytest.mark.parametrize("fname", ["demo-data.js", "app.js", "screens.js"])
def test_frontend_js_syntax(fname):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 JS 语法校验")
    proc = subprocess.run([node, "--check", str(UI_DIR / fname)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"{fname} 语法错误：{proc.stderr}"
