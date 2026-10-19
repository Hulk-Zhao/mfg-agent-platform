"""T19 五场景智能体层守卫（纯 Python，禁网络 / 禁 node）。

S5 已由 ``test_s05_agent.py`` 单独守；本文件守另外五个场景的**同一套接入模式**，
按屏参数化，避免五份重复代码：

1. **数据契约**：各屏 ``agent_layer`` 在契约文件与屏内镜像里逐值相等，声明的回放 id
   真实存在于 ``agent_replays`` / ``rag_replays``；
2. **屏结构**：``hydrateAgentBlock`` 接入、``replay()`` 取数、``agentPanel`` 渲染、
   来源固定 ``replay``（不伪装实时），且各自有降级标记；
3. **零写**：屏内无写请求动词与治理写调用（写链路归治理窗口）；
4. **屏特有纪律**：
   - S1/S3：kbqa 回放 ``channel=faq`` 且答案与 faqs.jsonl 原文一致；
   - S2：关键词检索（``renderCaseList``）保留为降级路径，回放未就绪时回落；
   - S6：三件回放（智能体 / 案例 / 制度）都在；
   - S4：T09 口径不抹平（两组数据源并列的说明仍在）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
SCENES_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui" / "scenes"

SCREENS = {
    "s01": ("s01-intake", "S1"),
    "s02": ("s02-npi-kickoff", "S2"),
    "s03": ("s03-stage-gate", "S3"),
    "s04": ("s04-batch-ecn", "S4"),
    "s06": ("s06-procurement", "S6"),
}


def _screen_src(slug: str) -> str:
    return (SCENES_DIR / f"{slug}.js").read_text(encoding="utf-8")


def _inline(slug: str) -> dict:
    src = _screen_src(slug)
    m = re.search(r"const INLINE_SCENE_DATA\s*=\s*(\{.*?\n  \});", src, re.S)
    assert m, f"{slug} 未找到 INLINE_SCENE_DATA 镜像块"
    return json.loads(m.group(1))


def _contract(slug: str) -> dict:
    raw = (DATA_DIR / "frontend" / f"{slug}.js").read_text(encoding="utf-8")
    # 各屏 wrapper 不同（`const SCENE_DATA_OBJECT = {` / `window.SCENE_DATA["s0N"] = {`），
    # 统一取**最后一个** `= {` 作为对象起点，再做字符串/括号感知的匹配扫描。
    start = raw.rindex("= {") + 2
    depth, i, in_str, esc = 0, start, False, False
    while i < len(raw):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    return json.loads(raw[start:i + 1])


def _replay_ids() -> tuple[set[str], set[str]]:
    def load(name: str) -> list[dict]:
        return [json.loads(line) for line in
                (DATA_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    return ({p["replay_id"] for p in load("agent_replays")},
            {p["replay_id"] for p in load("rag_replays")})


def _declared_ids(layer: dict) -> list[str]:
    ids: list[str] = []
    if layer.get("replay"):
        ids.append(layer["replay"])
    for value in (layer.get("replays") or {}).values():
        if value:
            ids.append(value)
    return ids


@pytest.mark.parametrize("sid", sorted(SCREENS))
def test_agent_layer_mirror_matches_contract(sid):
    slug = SCREENS[sid][0]
    assert _inline(slug)["agent_layer"] == _contract(slug)["agent_layer"], (
        f"{sid} 的 agent_layer 在屏内镜像与契约文件之间漂移")


@pytest.mark.parametrize("sid", sorted(SCREENS))
def test_declared_replays_exist(sid):
    slug = SCREENS[sid][0]
    agent_ids, rag_ids = _replay_ids()
    declared = _declared_ids(_contract(slug)["agent_layer"])
    assert declared, f"{sid} 未声明任何回放 id"
    for rid in declared:
        assert rid in agent_ids | rag_ids, f"{sid} 声明的回放 {rid} 不存在"


@pytest.mark.parametrize("sid", sorted(SCREENS))
def test_screen_renders_replay_panel_with_degrade(sid):
    slug = SCREENS[sid][0]
    src = _screen_src(slug)
    assert "hydrateAgentBlock" in src, f"{sid} 未接入智能体层"
    assert re.search(r"(K|kit)\.replay\(", src), f"{sid} 未走 SceneKit.replay()"
    assert re.search(r"(K|kit)\.agentPanel\(", src), f"{sid} 未渲染 agentPanel"
    assert 'source: "replay"' in src, f"{sid} 来源必须固定 replay"
    assert f"data-sk-{sid}-agent-degrade" in src, f"{sid} 缺降级标记"


@pytest.mark.parametrize("sid", sorted(SCREENS))
def test_no_write_path(sid):
    slug = SCREENS[sid][0]
    src = _screen_src(slug)
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert verb not in src, f"{sid} 出现写请求动词 {verb}"
    for fn in ("workflowSelectAction", "workflowCosign", "workflowApprove",
               "workflowReject"):
        assert fn not in src, f"{sid} 出现治理写调用 {fn}"


@pytest.mark.parametrize("sid", ["s01", "s03"])
def test_kbqa_replays_are_faq_channel(sid):
    slug = SCREENS[sid][0]
    rid = _contract(slug)["agent_layer"]["replay"]
    faqs = {row["faq_id"]: row for row in
            [json.loads(line) for line in
             (DATA_DIR / "faqs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]}
    packages = [json.loads(line) for line in
                (DATA_DIR / "rag_replays.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    pkg = [p for p in packages if p["replay_id"] == rid][0]
    assert pkg["channel"] == "faq"
    assert pkg["answer"] == faqs[pkg["faq_id"]]["answer"]
    assert "channel=faq" in _screen_src(slug), f"{sid} 屏未标注来源通道"


def test_s02_keyword_fallback_kept():
    """S2 升级后关键词检索仍在（三级降级中的第二级，不许被回放覆盖掉）。"""
    src = _screen_src(SCREENS["s02"][0])
    assert "renderCaseList(" in src
    assert 'data-sk-slot="cases"' in src
    assert 'data-sk-slot="agent"' in src


def test_s06_uses_three_replays():
    layer = _contract(SCREENS["s06"][0])["agent_layer"]
    assert set(layer["replays"]) == {"agent", "cases", "faq"}


def test_s04_keeps_two_sources_apart():
    """S4 的 T09 纪律：判异序列与判异事件两组数据源并列，不抹平。"""
    src = _screen_src(SCREENS["s04"][0])
    assert "不抹平" in src
