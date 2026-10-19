"""T16 回放数据管线守卫（纯 Python，禁网络 / 禁 node）。

守护「智能体轨迹 + RAG 检索回放包」这条演示数据管线的五个接缝：

1. **信封与计数**：4 个 agent 包 + 7 个 RAG 包的 replay_id 唯一、schema 版本固定
   ``1.0``、scene/kind/source 合法（架构 §2.1 / 共享知识 #1）；
2. **steps 六键同构铁律**：每个 step 的键集与 ``AgenticStep.to_dict()`` **相等**
   （import 后端真实类比对，不是看起来像）——回放与真链路共用同一渲染组件的前提；
3. **引用完整**：命中案例 ∈ cases、faq_id ∈ faqs、trigger 事件 ∈ events、结论里
   出现的 ``DEMO-*`` 对象号必须真实存在于某张对象表（屏上零编造的最后防线）；
4. **DAG ↔ 场景 YAML 对回**：以 ``validate.py`` 五类校验子进程实跑（含节点 / 分支 /
   HITL 槽位 / fan_out 条件对回），退出码必须 0；
5. **可复现与端点**：``gen.py`` 连跑两次字节级一致（固定种子纪律）；回放端点
   TestClient 实测 200 信封形状 + 未知 id 404 口径；隔离红线脚本可复跑（R6）。

本机无 node，JS 侧组件由 T17 的 ``test_agent_panel.py`` / ``test_knowledge_card.py``
用结构断言守护；本文件只管**数据与接口**。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
AGENT_REPLAYS = DATA_DIR / "agent_replays.jsonl"
RAG_REPLAYS = DATA_DIR / "rag_replays.jsonl"
GEN = REPO_ROOT / "tools" / "demo_gen" / "gen.py"
VALIDATE = REPO_ROOT / "tools" / "demo_gen" / "validate.py"
CHECK_ISOLATION = REPO_ROOT / "tools" / "demo_gen" / "check_isolation.py"
REGISTRY_JSON = DATA_DIR / "frontend" / "registry.json"

REPLAY_STEP_KEYS = {"step", "thought", "tool", "args", "summary", "ok"}
EXPECTED_AGENT_IDS = {
    "s05_complaint_8d", "s04_spc_anomaly", "s02_case_rag", "s06_shortage_procurement",
}
EXPECTED_RAG_IDS = {
    "s05_same_root_cause_cases", "s04_spc_similar_cases", "s02_case_search",
    "s06_supplier_cases", "s01_intake_faq", "s03_gate_faq", "s06_urge_faq",
}


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _packages() -> list[dict]:
    return _load(AGENT_REPLAYS) + _load(RAG_REPLAYS)


def _all_demo_ids() -> set[str]:
    """全部对象表里可被引用的 id（*_id / object_ref）。"""
    ids: set[str] = set()
    for path in DATA_DIR.glob("*.jsonl"):
        if path.stem in {"agent_replays", "rag_replays"}:
            continue
        for row in _load(path):
            for key, value in row.items():
                if isinstance(value, str) and (key.endswith("_id") or key == "object_ref"):
                    ids.add(value)
    return ids


# ---------- 1. 信封与计数 ----------

def test_replay_counts_and_ids():
    """4 个 agent 包 + 7 个 RAG 包，replay_id 与架构 §2.1 清单一致。"""
    agents = _load(AGENT_REPLAYS)
    rags = _load(RAG_REPLAYS)
    assert len(agents) == 4, f"agent 回放包应为 4 个，实际 {len(agents)}"
    assert len(rags) == 7, f"RAG 回放包应为 7 个，实际 {len(rags)}"
    assert {p["replay_id"] for p in agents} == EXPECTED_AGENT_IDS
    assert {p["replay_id"] for p in rags} == EXPECTED_RAG_IDS


def test_envelope_fields_are_valid():
    """信封八键齐、schema 版本固定、来源只允许 replay（不伪装实时）。"""
    packages = _packages()
    seen: set[str] = set()
    for pack in packages:
        rid = pack["replay_id"]
        assert rid not in seen, f"replay_id 重复：{rid}"
        seen.add(rid)
        assert pack["schema_version"] == "1.0", f"{rid} schema_version 必须为 1.0"
        assert pack["scene"] in {f"S{i}" for i in range(1, 7)}, f"{rid} scene 非法"
        assert pack["kind"] in {"agent", "case_search", "kbqa"}, f"{rid} kind 非法"
        assert pack["source"] == "replay", f"{rid} P0 只允许 source=replay"
        assert str(pack.get("title") or "").strip(), f"{rid} title 为空"
        assert pack.get("agents"), f"{rid} agents 为空"
        for agent in pack["agents"]:
            for key in ("domain", "abbr", "name", "role"):
                assert str(agent.get(key) or "").strip(), f"{rid} agents.{key} 为空"


def test_agent_domains_hit_real_config():
    """回放包 agents[].domain 必须命中真实域配置（生成器镜像不许漂移）。"""
    from mfg_agents.v2.agents.agentic import DOMAIN_AGENT_CONFIGS

    for pack in _packages():
        for agent in pack["agents"]:
            assert agent["domain"] in DOMAIN_AGENT_CONFIGS, (
                f"{pack['replay_id']} 引用未知智能体域 {agent['domain']!r}")
            cfg = DOMAIN_AGENT_CONFIGS[agent["domain"]]
            assert agent["abbr"] == cfg["abbr"], (
                f"{pack['replay_id']} {agent['domain']}.abbr 与真实配置不一致")
            assert agent["name"] == cfg["name"], (
                f"{pack['replay_id']} {agent['domain']}.name 与真实配置不一致")


# ---------- 2. steps 六键同构 ----------

def test_steps_keys_equal_agentic_step_contract():
    """每个 step 键集 == AgenticStep.to_dict() 键集（共享知识 #4 的同构铁律）。"""
    from mfg_agents.v2.agents.agentic import AgenticStep

    expected = set(AgenticStep(step=1).to_dict())
    assert expected == REPLAY_STEP_KEYS, "AgenticStep 契约变了：先同步回放包 schema"

    for pack in _packages():
        groups = [("steps", pack.get("steps") or [])]
        for node_id, steps in (pack.get("node_steps") or {}).items():
            groups.append((f"node_steps[{node_id}]", steps or []))
        for where, steps in groups:
            assert steps, f"{pack['replay_id']}.{where} 为空（回放包必须有轨迹）"
            for idx, step in enumerate(steps):
                assert set(step) == expected, (
                    f"{pack['replay_id']}.{where}[{idx}] 键集不符：{sorted(step)}")


def test_agent_replays_have_dag_and_hitl_slots():
    """kind=agent 的包必须有 dag；有 HITL 槽位的包必须画出 HITL 节点并挂槽位名。

    S5（客诉 8D）/ S4（SPC 判异）有治理闭环；S2（案例检索）/ S6（缺料采购）
    是单链流程、不涉人工决断——两类都要显式对账，不能「有槽位没画」或
    「没槽位乱画」。
    """
    with_hitl = {"s05_complaint_8d", "s04_spc_anomaly"}
    packages = _load(AGENT_REPLAYS)
    assert {p["replay_id"] for p in packages if p.get("hitl_slots")} == with_hitl
    for pack in packages:
        dag = pack.get("dag") or {}
        assert dag.get("nodes"), f"{pack['replay_id']} 缺 DAG 节点"
        assert dag.get("edges"), f"{pack['replay_id']} 缺 DAG 边"
        hitl_nodes = [n for n in dag["nodes"] if n.get("kind") == "hitl"]
        hitl_slots = pack.get("hitl_slots") or []
        if hitl_slots:
            assert hitl_nodes, f"{pack['replay_id']} 缺 HITL 节点（治理闭环没画出来）"
            for node in hitl_nodes:
                assert node.get("slot"), (
                    f"{pack['replay_id']} HITL 节点 {node.get('id')} 未挂槽位")
            assert {n["slot"] for n in hitl_nodes} == {h["slot"] for h in hitl_slots}, (
                f"{pack['replay_id']} HITL 节点槽位与 hitl_slots 不一致")
        else:
            assert not hitl_nodes, f"{pack['replay_id']} 无 HITL 槽位却画了 HITL 节点"


# ---------- 3. 引用完整 ----------

def test_case_hits_resolve_to_real_cases():
    """命中案例必须真实存在于 cases.jsonl（屏上零编造 case_id）。"""
    case_ids = {row["case_id"] for row in _load(DATA_DIR / "cases.jsonl")}
    hits = 0
    for pack in _load(RAG_REPLAYS):
        if pack["kind"] != "case_search":
            continue
        assert pack.get("hits"), f"{pack['replay_id']} 无命中（演示应命中至少一条）"
        assert pack.get("trace"), f"{pack['replay_id']} 缺检索 trace"
        for hit in pack["hits"]:
            assert hit["case_id"] in case_ids, (
                f"{pack['replay_id']} 命中 {hit['case_id']} 不存在于 cases")
            hits += 1
    assert hits >= 4, "案例回放包命中总数异常"


def test_kbqa_faqs_resolve_and_channel_is_honest():
    """FAQ 编号真实存在、channel=faq 不伪装来源、答案与 FAQ 原文一致。"""
    faqs = {row["faq_id"]: row for row in _load(DATA_DIR / "faqs.jsonl")}
    assert "DEMO-FAQ-011" in faqs, "U2 裁决的接单评审 FAQ（DEMO-FAQ-011）缺失"
    for pack in _load(RAG_REPLAYS):
        if pack["kind"] != "kbqa":
            continue
        assert pack["channel"] == "faq", f"{pack['replay_id']} channel 必须为 faq"
        row = faqs[pack["faq_id"]]
        assert pack["answer"] == row["answer"], (
            f"{pack['replay_id']} 答案与 faqs.jsonl 原文不一致（回放不许改写制度）")
        assert pack["sources"] and pack["sources"][0]["label"] == f"FAQ #{pack['faq_id']}"


def test_trigger_and_conclusion_references_resolve():
    """trigger 事件真实存在；结论里的 DEMO-* 号都能在对象表里找到。"""
    import re

    event_ids = {row["event_id"] for row in _load(DATA_DIR / "events.jsonl")}
    known = _all_demo_ids()
    for pack in _load(AGENT_REPLAYS):
        trigger = pack.get("trigger") or {}
        assert trigger.get("event_id") in event_ids, (
            f"{pack['replay_id']} 触发事件 {trigger.get('event_id')} 不存在")
        tokens = set(re.findall(r"DEMO-[A-Z]+-[0-9A-Z]+",
                                json.dumps(pack["conclusion"], ensure_ascii=False)))
        missing = sorted(t for t in tokens if t not in known)
        assert not missing, f"{pack['replay_id']} 结论引用不存在的对象：{missing}"


# ---------- 4. 校验器（含 DAG ↔ YAML） ----------

def test_validate_py_all_checks_pass():
    """validate.py 五类校验全 PASS（含 DAG↔YAML、HITL 槽位、fan_out 分支对回）。"""
    proc = subprocess.run(
        [sys.executable, str(VALIDATE)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, (
        f"validate.py 未通过：\n{proc.stdout}\n{proc.stderr}")
    assert "[PASS] replays" in proc.stdout


def test_validate_inject_replays_detects_broken_reference():
    """注入不存在的 case_id，校验器必须报出（防「永远全绿的空壳校验」）。"""
    proc = subprocess.run(
        [sys.executable, str(VALIDATE), "--inject", "replays"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 1, "注入断引用后校验器仍返回 0（校验器失效）"
    assert "DEMO-CASE-999" in proc.stdout


# ---------- 5. 可复现 / 端点 / 隔离 ----------

def test_generation_is_byte_deterministic(tmp_path):
    """gen.py 连跑两次，两个回放文件字节级一致（固定种子纪律）。"""
    snapshots = []
    for run in ("a", "b"):
        out = tmp_path / run
        proc = subprocess.run(
            [sys.executable, str(GEN), "--out", str(out)],
            capture_output=True, text=True, cwd=REPO_ROOT)
        assert proc.returncode == 0, f"gen.py 运行失败：\n{proc.stderr}"
        snapshots.append({
            name: (out / name).read_bytes()
            for name in ("agent_replays.jsonl", "rag_replays.jsonl")
        })
    for name in ("agent_replays.jsonl", "rag_replays.jsonl"):
        assert snapshots[0][name] == snapshots[1][name], (
            f"{name} 两次生成不一致（固定种子纪律被破坏）")
    # 与仓库现有产物一致（防「本地能生、仓库里是旧的」）
    for name in ("agent_replays.jsonl", "rag_replays.jsonl"):
        assert snapshots[0][name] == (DATA_DIR / name).read_bytes(), (
            f"{name} 仓库产物与生成器输出不一致，请重跑 gen.py 并提交")


def test_replay_endpoint_returns_package_and_404():
    """TestClient 实测：200 带信封与 schema_version；未知 id → 404 固定口径。"""
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    client = TestClient(app)  # 不用 with：避开 lifespan（不建库、不预热）
    resp = client.get("/api/v2/demo/replays/s05_complaint_8d")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_demo"] is True and body["label"] == "演示数据"
    assert body["replay"] == "s05_complaint_8d"
    assert body["schema_version"] == "1.0"
    assert body["package"]["replay_id"] == "s05_complaint_8d"
    assert body["package"]["steps"], "回放包 steps 为空"
    assert "notice" not in body, "正常包不应带降级 notice"

    missing = client.get("/api/v2/demo/replays/not_a_replay")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "demo replay not found"


def test_loader_exposes_replays():
    """装载层三方对账：OBJECTS 含两个回放对象，get_replay 可查、未知抛错。"""
    from mfg_agents.v2.demo import loader

    assert "agent_replays" in loader.OBJECTS and "rag_replays" in loader.OBJECTS
    package = loader.get_replay("s05_complaint_8d")
    assert package["replay_id"] == "s05_complaint_8d"
    with pytest.raises(loader.DemoDataError):
        loader.get_replay("not_a_replay")


def test_registry_entries_match_disk():
    """registry.json 的 objects 条目 key/fields 与磁盘 jsonl 首行实读一致。"""
    registry = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
    assert registry["api"].get("replay") == "/api/v2/demo/replays/{replay_id}"
    for name in ("agent_replays", "rag_replays"):
        entry = registry["objects"][name]
        first = _load(DATA_DIR / f"{name}.jsonl")[0]
        assert entry["key"] == "replay_id"
        assert sorted(entry["fields"]) == sorted(first.keys()), (
            f"{name} registry fields 与 jsonl 首行漂移")


def test_isolation_scan_still_passes():
    """隔离红线脚本可复跑（R6：种子内容全虚构，不触发 C 类真实企业名词）。"""
    proc = subprocess.run(
        [sys.executable, str(CHECK_ISOLATION)],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, (
        f"隔离扫描未通过：\n{proc.stdout}\n{proc.stderr}")


# ---------- 6. 种子钩子结构（不触库，只钉口径） ----------

def test_8d_seed_hook_uses_marker_not_wf_id():
    """种子幂等按 demo_seed_id 标记判定（绕开 416/211 条存量夹具），SLA 72h。"""
    src = (REPO_ROOT / "src/mfg_agents/v2/demo/api.py").read_text(encoding="utf-8")
    assert "_8D_SEED_MARK" in src and "s5_8d_v1" in src
    assert 'demo_seed_id' in src, "种子必须带 demo_seed_id 标记（A3）"
    seed_fn = src.split("def _ensure_8d_seed", 1)[1].split("\ndef ", 1)[0]
    assert 'get("demo_seed_id")' in seed_fn, (
        "幂等判定必须用 context.demo_seed_id，不能用 wf_id（会被存量夹具误判）")
    assert "sla_hours=72" in seed_fn, "U6 裁决：种子 SLA 取 72h"
    assert "_ensure_8d_seed()" in src.split("@app.get(\"/api/v2/workflow/pending\")", 1)[1], (
        "pending 读端点入口必须调用种子钩子")


def test_no_spc_action_seed():
    """U4 裁决：S4 的 spc_action 不做真实种子工单（由回放 DAG 承载）。"""
    src = (REPO_ROOT / "src/mfg_agents/v2/demo/api.py").read_text(encoding="utf-8")
    seed_fn = src.split("def _ensure_8d_seed", 1)[1].split("\ndef ", 1)[0]
    assert 'wf_id="hitl_spc_action"' not in seed_fn, "U4 裁决禁止为 spc_action 建种子工单"


def test_scene_kit_replay_endpoint_uses_double_quotes():
    """端点对账前置：双引号整串端点（T05 解析器只认双引号，单引号会漏检）。"""
    src = (REPO_ROOT / "src/mfg_agents/v2/demo/ui/scenes/scene-kit.js").read_text(
        encoding="utf-8")
    assert '"/api/v2/demo/replays/"' in src
    assert "'/api/v2/demo/replays/'" not in src, (
        "T16 端点已落地，不得再保留单引号过渡写法（T05 对账会漏检）")
