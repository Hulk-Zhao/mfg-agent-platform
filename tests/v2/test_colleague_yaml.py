"""colleague.yaml 守卫（D3 + 架构质疑 Q5）：岗位 id ⊆ 前端 ROLES，工具 ⊆ 智能体工具面。

钉死「严禁第三套角色 key」：前端 demo-data.js 31 岗位 / 治理 permissions.yaml
32 角色已靠 PermissionRegistry aliases 归一化，colleague.yaml 只准引用前端 id。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from mfg_agents.v2.agents.agentic import AGENT_TOOLS_META

_REPO_ROOT = Path(__file__).resolve().parents[2]
_YAML = _REPO_ROOT / "src" / "mfg_agents" / "v2" / "data" / "colleague.yaml"


def _frontend_role_ids() -> set[str]:
    src = (_REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
           / "demo-data.js").read_text(encoding="utf-8")
    m = re.search(r"const ROLES = \[(.*?)\n  \];", src, re.S)
    assert m, "demo-data.js ROLES 字面量未找到（正则需同步）"
    return set(re.findall(r'\{ id: "(\w+)"', m.group(1)))


@pytest.fixture(scope="module")
def colleagues() -> list[dict]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data.get("colleagues"), list)
    return data["colleagues"]


def test_role_ids_subset_of_frontend_roles(colleagues):
    """只准引用前端岗位 id——严禁造第三套角色 key（Q5）。"""
    ids = [c["role"] for c in colleagues]
    assert len(ids) == len(set(ids)), "role id 重复"
    unknown = set(ids) - _frontend_role_ids()
    assert not unknown, f"colleague.yaml 引用了前端不存在的岗位 id: {unknown}"


def test_covers_all_frontend_roles(colleagues):
    """31 岗位全覆盖（每个部门都能配一位 AI 同事）。"""
    missing = _frontend_role_ids() - {c["role"] for c in colleagues}
    assert not missing, f"未配同事的岗位: {missing}"


def test_tools_within_agent_tool_surface(colleagues):
    """tools 必须存在于 AGENT_TOOLS_META（防配置漂移成空头支票）。"""
    known = {t["name"] for t in AGENT_TOOLS_META}
    for c in colleagues:
        bad = set(c.get("tools") or []) - known
        assert not bad, f"{c['role']} 引用了不存在的工具: {bad}"


def test_entry_shape(colleagues):
    """每条同事配置形状完备（切换器直接消费，缺字段即烂尾）。"""
    for c in colleagues:
        role = c.get("role")
        assert c.get("name") and c.get("greeting"), role
        assert len(c.get("scenarios") or []) >= 2, role
        assert len(c.get("questions") or []) >= 2, role
        assert len(c.get("tools") or []) >= 1, role
