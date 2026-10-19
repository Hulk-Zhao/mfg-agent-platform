"""T18 S5 智能体化守卫（纯 Python，禁网络 / 禁 node）。

守护 S5「最深样板」这条接缝——它把回放数据、组件底座与屏三层的契约同时用上，
任何一层漂移都会让屏上出现「看起来有智能、其实无出处」的内容：

1. **登记与取数**：屏脚本消费的两个回放 id 必须真实存在于 ``agent_replays`` /
   ``rag_replays``，且在 ``registry.json`` 的 s05 条目里登记；
2. **场景数据**：8D 结构图八步齐、几何落在画布内且两两不重叠；取证书签的
   ``key`` 必须命中追溯图谱七节点、``domain`` 必须命中回放包声明过的智能体域；
3. **镜像不漂移**：屏内 ``INLINE_SCENE_DATA`` 与 ``frontend/s05-trace.js`` 的
   ``agent_layer`` 逐值相等；
4. **屏结构**：五个挂载点（协同面板 / 取证书签 / 同根因案例 / 治理卡 / 8D 图）
   都在，组件调用走 ``SceneKit.agentPanel/knowledgeCard/replay``，治理待办走
   ``ctx.api.workflowPending()``；来源固定 ``replay``（不伪装实时）；
5. **零写与不编数**：屏内无写请求动词与治理写调用；8D 的 D4 根因从
   ``D.rows.cases`` 现取（屏内不得出现业务值字面量）；取不到回放走整块降级。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENE_FILE = REPO_ROOT / "src/mfg_agents/v2/demo/ui/scenes/s05-trace.js"
DATA_FILE = REPO_ROOT / "data/demo_storyline/frontend/s05-trace.js"
REGISTRY_JSON = REPO_ROOT / "data/demo_storyline/frontend/registry.json"
DATA_DIR = REPO_ROOT / "data/demo_storyline"

INLINE_PATTERN = r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});"


def _src() -> str:
    return SCENE_FILE.read_text(encoding="utf-8")


def _inline() -> dict:
    matched = re.search(INLINE_PATTERN, _src(), re.S)
    assert matched, "屏内未找到 INLINE_SCENE_DATA 镜像块"
    return json.loads(matched.group(1))


def _contract() -> dict:
    raw = DATA_FILE.read_text(encoding="utf-8")
    start = raw.index("= {") + 2
    end = raw.rindex("};") + 1
    return json.loads(raw[start:end])


def _replays(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (DATA_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---------- 1. 登记与取数 ----------

def test_screen_consumes_existing_replays():
    """屏消费的两个回放 id 真实存在，且场景数据里声明的一致。"""
    layer = _contract()["agent_layer"]
    agent_ids = {p["replay_id"] for p in _replays("agent_replays")}
    rag_ids = {p["replay_id"] for p in _replays("rag_replays")}
    assert layer["replays"]["agent"] in agent_ids
    assert layer["replays"]["cases"] in rag_ids


def test_registry_lists_replay_objects():
    """回放对象在 registry.json 顶层登记（s05 条目只登记 story 取数对象，不混入）。"""
    registry = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
    assert "agent_replays" in registry["objects"]
    assert "rag_replays" in registry["objects"]
    layer = _contract()["agent_layer"]
    assert registry["objects"]["agent_replays"]["key"] == "replay_id"
    assert layer["replays"]["agent"] in {p["replay_id"] for p in _replays("agent_replays")}


# ---------- 2. 场景数据 ----------

def test_eight_d_geometry_in_canvas_and_non_overlapping():
    """8D 八步齐、键唯一、矩形不越界、两两不重叠（真机黑块的常见根因）。"""
    d8 = _contract()["agent_layer"]["d8"]
    steps = d8["steps"]
    assert len(steps) == 8, f"8D 应为八步，实际 {len(steps)}"
    assert [s["key"] for s in steps] == [f"D{i}" for i in range(1, 9)]
    canvas = d8["canvas"]
    for st in steps:
        assert 0 <= st["x"] and st["x"] + st["w"] <= canvas["w"], (
            f"{st['key']} 横向越界：x={st['x']} w={st['w']}")
        assert 0 <= st["y"] and st["y"] + st["h"] <= canvas["h"], (
            f"{st['key']} 纵向越界")
    for i, a in enumerate(steps):
        for b in steps[i + 1:]:
            overlap = (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                       and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])
            assert not overlap, f"8D 节点重叠：{a['key']} × {b['key']}"


def test_graph_badges_hit_nodes_and_replay_agents():
    """取证书签必须命中七节点，且智能体域在回放包 agents[] 里真实声明。"""
    data = _contract()
    node_keys = {n["key"] for n in data["nodes"]}
    assert len(node_keys) == 7
    badges = data["agent_layer"]["graph_badges"]
    assert {b["key"] for b in badges} == node_keys, (
        "取证书签必须覆盖且仅覆盖七个追溯节点")
    agents = {a["domain"] for p in _replays("agent_replays")
              if p["replay_id"] == data["agent_layer"]["replays"]["agent"]
              for a in p["agents"]}
    for b in badges:
        assert b["domain"] in agents, f"书签 {b['key']} 的域 {b['domain']} 未在回放包声明"
        assert b["tool"], f"书签 {b['key']} 未标注工具"


# ---------- 3. 镜像不漂移 ----------

def test_agent_layer_mirror_matches_contract():
    """屏内镜像与契约文件的 agent_layer 逐值相等。"""
    assert _inline()["agent_layer"] == _contract()["agent_layer"]


# ---------- 4. 屏结构 ----------

def test_screen_mounts_agent_layer():
    """五个挂载点与三个组件调用都在（缺一个就少一块智能体可视化）。"""
    src = _src()
    for anchor in ("s05-agent-panel", "s05-graph-badges", "s05-case-panel",
                   "s05-hitl-panel", "s05-d8-panel"):
        assert anchor in src, f"屏内缺挂载点 {anchor}"
    assert "K.agentPanel(" in src
    assert "K.knowledgeCard(" in src
    assert "K.replay(" in src
    assert "ctx.api.workflowPending()" in src
    assert 'source: "replay"' in src, "来源必须固定 replay（不伪装实时）"


def test_screen_filters_demo_seed_tasks_only():
    """治理卡只展示演示种子工单（按 demo_seed_id 标记过滤，不混存量夹具）。"""
    src = _src()
    assert 'c.demo_seed_id === "s5_8d_v1"' in src, (
        "过滤判据必须是种子标记 demo_seed_id（与 api._ensure_8d_seed 幂等键同源）")
    assert '"d8_close"' in src and '"complaint_reply"' in src


def test_degrade_paths_exist():
    """回放缺失 / 待办读取失败都有整块降级路径（不编造内容）。"""
    src = _src()
    assert "演示回放数据未就绪" in src
    assert "治理待办读取失败，不展示编造工单" in src
    assert "data-sk-s05-agent-degrade" in src


# ---------- 5. 零写与不编数 ----------

def test_scene_has_no_write_path():
    """零写铁律：无写请求动词、无治理写调用（会签写链路归治理窗口）。"""
    src = _src()
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert verb not in src, f"屏内出现写请求动词 {verb}"
    for fn in ("workflowSelectAction", "workflowCosign", "workflowApprove",
               "workflowReject"):
        assert fn not in src, f"屏内出现治理写调用 {fn}"


def test_d4_root_cause_is_derived_not_hardcoded():
    """D4 根因从 cases 现取：案例号在场景数据里，屏内只做 id → 行 的查找。"""
    src = _src()
    assert "root_cause_case" in src, "D4 必须按场景数据给的案例号现取"
    assert "root_cause" in src
    assert "烧录站静电接地失效" not in src, (
        "屏内不得硬编码业务结论；应从 D.rows.cases 的 root_cause 读取")
    outside = re.sub(INLINE_PATTERN, "const INLINE_SCENE_DATA = {};", src, flags=re.S)
    assert "DEMO-CASE-006" not in outside, (
        "业务对象号只放场景数据镜像块（已由 test_s05 的禁抄数断言守护）")


def test_js_delimiters_are_balanced():
    """本机无 node：用括号/引号闭合巡检兜住「多一个括号」类事故。"""
    src = _src()
    # 去掉注释与字符串后再看括号平衡（字符串内的括号不参与 JS 语法结构）
    no_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    no_line = re.sub(r"(?<![:\"'])\s*//[^\n]*", "", no_block)
    stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', no_line)
    stripped = re.sub(r"'(?:\\.|[^'\\])*'", "''", stripped)
    for pair in (("(", ")"), ("{", "}"), ("[", "]")):
        assert stripped.count(pair[0]) == stripped.count(pair[1]), (
            f"括号不闭合：{pair[0]} {stripped.count(pair[0])} vs "
            f"{pair[1]} {stripped.count(pair[1])}")
