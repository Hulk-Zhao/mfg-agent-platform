"""T17 组件底座 · SceneKit.agentPanel / SceneKit.replay 守卫（纯 Python，禁网络 / 禁 node）。

守护的是**组件契约**这类「屏消费方照抄时会落空」的接缝——架构设计 §3.1 / §3.3
与共享知识 #1/#3/#4 的组件侧落地证据。本机没有 node，不能起 JS 运行时执行，
所以全部用**结构断言 + 内联夹具字段对读**钉住：

1. **导出契约**：`agentPanel` / `knowledgeCard` / `replay` 三个 API 都挂在
   ``window.SceneKit`` 导出对象上（屏只消费不造轮子）；
2. **入参 schema**：agentPanel 的关键键（title / agents / steps / conclusion /
   source 必填，dag / node_steps / hitl_task 可选）在实现里都有对应读取点，
   照架构文档 §6 的内联夹具（s05_complaint_8d 形态）逐字段对读；
3. **steps 六键同构铁律**（共享知识 #4）：组件常量 ``REPLAY_STEP_KEYS`` 与
   ``AgenticStep.to_dict()`` 键集**相等**（import 后端真实类比对，不是看起来像）；
4. **回放取数纪律**（架构 §3.3）：端点字符串存在、永不 reject、不透传
   err.message、schema_version 缺失/不符整块降级、离线镜像兜底；
5. **徽标类名分离**（共享知识 #3）：来源徽标 ``sk-source-badge`` 与
   ``.demo-flag`` 不互相占用（T12 守护判据不被劫持）；
6. **降级与零写**：steps 空数组整块降级文案在位；组件内无写请求动词、
   无治理写调用，HITL 操作位一律「只读演示」；
7. **括号闭合巡检**：本机无 node，用剥离扫描兜住「多一个括号」类事故
   （在 test_s01 的模式上增加正则字面量识别——scene-kit.js 的
   DEMO_BADGE_CLASS_RE 内含双引号，朴素算法会级联误吞）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))  # 与 tests/ 其它用例同口径

SCENES_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui" / "scenes"
SCENE_KIT = SCENES_DIR / "scene-kit.js"
DEMO_API = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py"

#: 回放端点（T16 落地；前端字符串 ↔ 后端路由的对账另见 test_t05_contract.py）
REPLAY_ENDPOINT = "/api/v2/demo/replays/"

#: agentPanel 内联夹具（架构文档 §6 JSON Schema 要点的 s05 客诉 8D 形态），
#: 用于逐字段对读「实现里有没有读这个键」。
PANEL_FIXTURE = {
    "title": "客诉 8D 多智能体协同（DEMO-CC-001）",
    "agents": [{"domain": "quality", "abbr": "QA", "name": "质量域智能体",
                "role": "客诉 8D 牵头：合成 D1–D8 骨架"}],
    "dag": {
        "nodes": [{"id": "fanout", "label": "四域并行取证", "agent": "",
                   "kind": "fan_out", "condition": "complaint_found"},
                  {"id": "pqe_process", "label": "PQE · 过程分析",
                   "agent": "pqe_process", "kind": "agent"},
                  {"id": "hitl_d8_close", "label": "8D 报告关闭确认",
                   "agent": "hitl_d8_close", "kind": "hitl", "slot": "d8_close"}],
        "edges": [{"from": "fanout", "to": "pqe_process"},
                  {"from": "pqe_process", "to": "hitl_d8_close"}],
    },
    "steps": [{"step": 1, "thought": "客诉单进入，需确认事实与影响面",
               "tool": "run_registered_query",
               "args": {"query_name": "complaint_detail"},
               "summary": "客诉 DEMO-CC-001：12 片烧录异常", "ok": True}],
    "node_steps": {"pqe_process": [{"step": 1, "thought": "确认过程系统性",
                                    "tool": "run_registered_query", "args": {"op": "30"},
                                    "summary": "工序 30 IPQC 抽检 100、不良 3", "ok": True}]},
    "conclusion": {"answer": "四域并行取证收敛", "data_used": ["DEMO-CC-001"],
                   "needs_hitl": True, "hitl_title": "8D 报告关闭确认",
                   "hitl_slot": "d8_close", "data_gaps": []},
    "source": "replay",
    "hitl_task": {"title": "8D 报告关闭确认", "slot": "d8_close",
                  "status": "waiting_hitl", "visibility": "domain",
                  "progress": "0 / 3", "required": True,
                  "my_action": "待我会签", "readonly_note": "写操作归治理窗口"},
}

#: 组件内不得出现的写请求动词与治理写调用（共享知识 #3 零写铁律）
WRITE_VERBS = ("POST", "PUT", "PATCH", "DELETE")
WRITE_CALLS = ("workflowSelectAction", "Cosign", "Approve", "Reject")


def _src() -> str:
    assert SCENE_KIT.exists(), f"缺少 scene-kit.js：{SCENE_KIT}"
    return SCENE_KIT.read_text(encoding="utf-8")


def _export_block(src: str) -> str:
    """抽出 ``const SceneKit = { ... };`` 导出对象体。"""
    matched = re.search(r"const SceneKit = \{(.*?)\n  \};", src, re.S)
    assert matched, "未找到 SceneKit 导出对象（写法被改？请同步更新本断言）"
    return matched.group(1)


def _function_body(src: str, signature: str) -> str:
    """按函数签名截取函数体（到下一个同级右花括号）。"""
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    begun = False
    for pos in range(i, len(src)):
        ch = src[pos]
        if ch == "{":
            depth += 1
            begun = True
        elif ch == "}":
            depth -= 1
            if begun and depth == 0:
                return src[start:pos + 1]
    raise AssertionError(f"函数体未闭合：{signature}")


# ============================================================
# 1. 导出契约：三个 API 都在 SceneKit 导出对象中
# ============================================================

def test_scene_kit_exports_three_component_apis():
    """agentPanel / knowledgeCard / replay 必须导出（屏只消费不造轮子）。"""
    block = _export_block(_src())
    for api in ("agentPanel", "knowledgeCard", "replay"):
        assert re.search(rf"(^|\s){api},", block), (
            f"SceneKit 导出对象缺少 {api}（T17 组件底座未挂载）")
    assert "window.SceneKit" in _src(), "scene-kit.js 未导出 window.SceneKit"


def _strip_comments(text: str) -> str:
    """去掉 // 与 /* */ 注释（占位符检查只看代码，注释里的示例不算）。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def test_component_apis_are_implemented_not_stubbed():
    """三个 API 必须是完整函数实现（禁 pass / TODO 占位）。"""
    src = _src()
    for signature in ("function agentPanel(opts)", "function knowledgeCard(opts)",
                      "function replay(replayId)"):
        assert signature in src, f"缺少实现：{signature}"
        body = _strip_comments(_function_body(src, signature))
        assert "TODO" not in body and "..." not in body, (
            f"{signature} 含占位符（完整实现纪律）")
        assert len(body.splitlines()) > 5, f"{signature} 实现过短（疑似桩函数）"


# ============================================================
# 2. agentPanel 入参 schema 关键键（内联夹具逐字段对读）
# ============================================================

@pytest.mark.parametrize("key", [
    "title", "agents", "dag", "steps", "node_steps", "conclusion", "source",
    "hitl_task",
])
def test_agent_panel_schema_key_is_read(key: str):
    """schema 每个键在实现里都有读取点（o.<key>），照抄方不会落空。"""
    src = _src()
    assert re.search(rf"\bo\.{re.escape(key)}\b", src), (
        f"agentPanel 未读取入参键 {key}（架构 §3.1 schema 缺读取点）")


def test_agent_panel_doc_block_declares_schema():
    """JSDoc 必须写明入参 schema（含可选键标注），与架构 §3.1 一致。"""
    src = _src()
    doc = re.search(r"/\*\*\n   \* 智能体协同面板（架构 §3\.1）(.*?)\*/", src, re.S)
    assert doc, "agentPanel 缺少 JSDoc（schema 文档化是组件契约的一部分）"
    text = doc.group(1)
    for key in ("title", "agents", "dag?", "steps", "node_steps?", "conclusion",
                "source", "hitl_task?"):
        assert key in text, f"agentPanel JSDoc 未声明 schema 键 {key}"


def test_agent_panel_fixture_fields_all_have_render_hooks():
    """内联夹具（s05 客诉 8D 形态）逐字段：实现里必须能读到。"""
    src = _src()
    fixture = PANEL_FIXTURE
    # 顶层键
    for key in fixture:
        assert re.search(rf"\bo\.{re.escape(key)}\b", src), f"夹具键 {key} 无读取点"
    # agents 行键
    for field in ("abbr", "name", "role"):
        assert f"ag.{field}" in src, f"agents[].{field} 无渲染读取"
    # dag 节点键：在拓扑条 + 并行分支两个渲染函数里都能读到
    dag_bodies = (_function_body(src, "function dagBarHtml(dag, nodeSteps)")
                  + _function_body(src, "function parallelBranchesHtml(dag, nodeSteps)"))
    for field in ("id", "label", "kind", "condition", "slot"):
        assert re.search(rf"\.{field}\b", dag_bodies), (
            f"dag 节点字段 {field} 无读取点")
    # edges 键
    assert "e.from" in src and "e.to" in src, "dag.edges 的 from/to 无读取点"
    # conclusion 键
    for field in ("answer", "data_used", "needs_hitl", "hitl_title", "hitl_slot",
                  "data_gaps"):
        assert f"conclusion.{field}" in src, f"conclusion.{field} 无读取点"
    # hitl_task 键
    for field in ("title", "slot", "status", "visibility", "progress", "required",
                  "my_action", "readonly_note"):
        assert f"hitl.{field}" in src, f"hitl_task.{field} 无读取点"


def test_agent_panel_renders_seven_layers():
    """渲染结构七层（架构 §3.1）：头/智能体行/DAG/步骤/结论/HITL/并行四列。"""
    src = _src()
    markers = [
        'data-sk-agent-panel="1"',       # 面板容器（点击委托的作用域锚）
        "sk-source-badge",               # 1 头部来源徽标
        "sk-agent-agents",               # 2 参与智能体行
        'class="sk-dag"',                # 3 DAG 拓扑条
        "sk-agent-step-set",             # 4 步骤时间线（主链 + 节点集）
        "sk-agent-conclusion",           # 5 结论卡
        "sk-agent-hitl",                 # 6 HITL 卡
        "sk-agent-parallel",             # 7 并行四列形态
        "sk-agent-branches",             # 7 分支列容器
        "sk-agent-converge",             # 7 汇聚箭头到 fan_in
    ]
    for marker in markers:
        assert marker in src, f"渲染结构缺少 {marker}"


# ============================================================
# 3. steps 六键同构铁律（共享知识 #4）
# ============================================================

def test_replay_step_keys_match_agentic_step_to_dict():
    """组件六键常量与后端 AgenticStep.to_dict() 键集**相等**（import 真实类）。"""
    from mfg_agents.v2.agents.agentic import AgenticStep

    expected = set(AgenticStep(step=1).to_dict().keys())
    src = _src()
    matched = re.search(r"const REPLAY_STEP_KEYS = \[(.*?)\];", src, re.S)
    assert matched, "scene-kit.js 缺少 REPLAY_STEP_KEYS 六键常量"
    declared = set(re.findall(r'"([a-z_]+)"', matched.group(1)))
    assert declared == expected, (
        f"组件六键 {sorted(declared)} 与 AgenticStep.to_dict() {sorted(expected)} 不一致"
        "（同构铁律：键集相等，不是字段相似）")


def test_step_renderer_reads_all_six_keys():
    """步骤渲染器必须读全六键（thought/tool/args/summary/ok/step）。"""
    src = _src()
    body = _function_body(src, "function stepDetailsHtml(s)")
    for field in ("step", "thought", "tool", "args", "summary", "ok"):
        assert re.search(rf"s\.{field}\b", body), (
            f"stepDetailsHtml 未读取 s.{field}（六键同构的渲染侧证据）")
    assert "pickReplayStep(" in src, (
        "组件应经 pickReplayStep 过滤六键（脏包未知键不得漏进界面）")


def test_failed_step_is_marked_red():
    """失败步骤红标（ok === false → is-failed + 「失败」文案）。"""
    src = _src()
    body = _function_body(src, "function stepDetailsHtml(s)")
    assert "s.ok === false" in body, "失败判定缺失（ok === false）"
    assert "is-failed" in body, "失败步骤缺少 is-failed 类（红标）"


# ============================================================
# 4. replay 取数纪律（架构 §3.3）
# ============================================================

def test_replay_endpoint_string_present():
    """回放端点字符串存在，且复用既有 fetchJson（6s 超时 + AbortController）。"""
    src = _src()
    assert REPLAY_ENDPOINT in src, (
        f"scene-kit.js 缺少回放端点字符串 {REPLAY_ENDPOINT}")
    body = _function_body(src, "function replay(replayId)")
    assert "fetchJson(url, 6000)" in body, "replay() 未复用既有 fetchJson（6s 超时）"
    assert "encodeURIComponent(id)" in body, "replayId 必须编码后拼入 URL"
    assert "window.DEMO_STORY_REPLAY" in body, (
        "缺少离线回放镜像兜底（window.DEMO_STORY_REPLAY）")


def test_replay_endpoint_reconciles_with_backend_route():
    """T05 端点对账模式：前端端点字符串 ↔ api.py 路由表。

    T16 路由落地前本用例**跳过**（待 T16 端点落地后启用）；T16 提交
    ``@app.get("/api/v2/demo/replays/{replay_id}")`` 后自动生效，与
    test_t05_contract.py::test_scene_kit_endpoint_matches_backend_route 同口径。
    """
    api_src = DEMO_API.read_text(encoding="utf-8")
    routes = re.findall(r'@app\.(?:get|post)\("(/api/v2/demo/[^"]*)"\)', api_src)
    hit = [route for route in routes if route.startswith(REPLAY_ENDPOINT)]
    if not hit:
        pytest.skip("待 T16 端点落地后启用：api.py 尚未注册 "
                    "GET /api/v2/demo/replays/{replay_id}")
    assert hit, "后端已注册路由但对账未命中（前端字符串被改？）"


def test_replay_never_rejects_and_validates_schema():
    """永不 reject、不透传 err.message；schema_version 缺失/不符 → 版本不兼容。"""
    src = _src()
    body = _function_body(src, "function replay(replayId)")
    assert "Promise.resolve(" in body, "降级路径必须直接 resolve，不能 reject"
    assert body.count(".catch(") >= 2, "replay() 缺少兜底 catch（异常会变未捕获 rejection）"
    assert 'error: "演示回放数据未就绪"' in body, "缺省错误文案必须是「演示回放数据未就绪」"
    assert 'error: "回放包版本不兼容"' in body, (
        "schema_version 缺失/不符必须降级为「回放包版本不兼容」（不渲染半包）"
    )
    assert 'REPLAY_SCHEMA_VERSION' in body and '"1.0"' in src, (
        "schema 版本校验缺失（共享知识 #1）")
    assert "degraded: true" in body, (
        "离线镜像命中必须标 degraded（与 story() 本地回落同口径）")
    assert "err.message" not in body, "不得透传 err.message（中文文案纪律）"


def test_replay_rejects_empty_id_without_touching_network():
    """空 id 直接降级，不打接口。"""
    src = _src()
    body = _function_body(src, "function replay(replayId)")
    assert 'error: "未指定回放 id"' in body, "空 id 必须有明确降级文案"
    head = body[:body.index("const accept")]
    assert "fetchJson" not in head, "空 id 不应触网"


# ============================================================
# 5. 徽标类名分离（共享知识 #3 / T12 判据）
# ============================================================

def test_source_badge_class_separated_from_demo_flag():
    """来源徽标 sk-source-badge 与 .demo-flag 类名分离（不互相占用）。"""
    src = _src()
    assert "sk-source-badge" in src, "缺少来源徽标类 sk-source-badge"
    badge = _function_body(src, "function agentSourceBadge(source)")
    assert "demo-flag" not in badge, (
        "来源徽标不得占用 .demo-flag（T12 守护判据会被劫持）")
    assert '"sk-source-badge "' in badge or "'sk-source-badge '" in badge, (
        "来源徽标必须带 sk-source-badge 类名")
    # demoBadge 仍是 .demo-flag 的唯一出口（未被组件改写）
    demo = _function_body(src, "function demoBadge(text)")
    assert "demo-flag" in demo, "demoBadge() 仍须产出 .demo-flag（唯一出口）"


def test_source_badge_is_not_removable_and_covers_both_sources():
    """徽标不可去除（无 opts 开关）；replay / live 两种来源都有文案。"""
    src = _src()
    badge = _function_body(src, "function agentSourceBadge(source)")
    assert "演示回放" in badge and "实时链路" in badge, "两种来源文案缺失"
    # 头部渲染无条件拼接徽标（不存在 hideBadge 之类开关）
    head = src[src.index("function agentPanel(opts)"):]
    assert "agentSourceBadge(o.source)" in head, "头部必须无条件挂来源徽标"
    assert "hideBadge" not in src and "showBadge" not in src, (
        "徽标不得有 opts 开关（不可去除纪律）")


# ============================================================
# 6. 降级路径与零写铁律
# ============================================================

def test_agent_panel_degrades_when_steps_empty():
    """steps 空数组 → 整块降级文案，不渲染半面板。"""
    src = _src()
    assert "回放数据缺失，不展示编造步骤" in src, (
        "缺少 steps 空数组降级文案（架构 §3.1 / §3.4）")
    body = _function_body(src, "function agentPanel(opts)")
    guard = body[:body.index("const agents")]
    assert "!steps.length" in guard or "!steps || !steps.length" in guard, (
        "steps 空数组必须在渲染前整块拦截（不渲染半面板）")
    assert 'data-sk-agent-degrade="1"' in guard, "降级块必须带标记（可截图取证）"


def test_component_has_zero_write_calls():
    """组件内零写：无写请求动词、无治理写调用（共享知识 #3）。"""
    src = _src()
    for verb in WRITE_VERBS:
        assert not re.search(rf"\b{verb}\b", src), (
            f"scene-kit.js 出现写请求动词 {verb}（组件零写铁律）")
    for call in WRITE_CALLS:
        assert call not in src, f"scene-kit.js 出现治理写调用 {call}（零写铁律）"


def test_hitl_card_actions_are_readonly():
    """HITL 操作位一律「只读演示」；HITL 卡内无按钮。"""
    src = _src()
    body = _function_body(src, "function agentPanel(opts)")
    hitl_section = body[body.index('data-sk-agent-hitl="1"'):]
    assert '<span class="sk-readonly">只读演示</span>' in hitl_section, (
        "HITL 操作位必须渲染 sk-readonly「只读演示」")
    assert "<button" not in hitl_section, "HITL 卡内不得出现按钮（零写）"
    assert "写操作归治理窗口" in hitl_section, (
        "readonly_note 必须说明写链路归治理窗口")


def test_hitl_visibility_is_rendered_as_is():
    """可见性照实呈现 mine/domain/不可见（不为叙事好看而点亮）。"""
    src = _src()
    assert "我的会签（mine）" in src and "域内可见（domain）" in src, (
        "HITL 可见性口径缺失（mine / domain 照实呈现）")


# ============================================================
# 7. DAG / 并行分支数据驱动
# ============================================================

def test_dag_kind_vocabulary_complete():
    """五种 DAG kind 全覆盖（与场景 YAML steps 对回）。"""
    src = _src()
    labels = re.search(r"const DAG_KIND_LABELS = \{(.*?)\};", src, re.S)
    assert labels, "缺少 DAG_KIND_LABELS 类型词典"
    for kind in ("agent", "fan_out", "fan_in", "hitl", "workflow"):
        assert f"{kind}:" in labels.group(1), f"DAG kind {kind} 缺标签"


def test_parallel_branches_are_data_driven():
    """并行四列自 dag.edges 的 fan_out 出边推导（非 S5 硬编码）。"""
    src = _src()
    body = _function_body(src, "function parallelBranchesHtml(dag, nodeSteps)")
    assert 'String(e.from || "")' in body and 'String(e.to || "")' in body, (
        "分支必须自 dag.edges 推导（数据驱动）")
    assert 'kind || "") === "fan_out"' in body, "必须定位 fan_out 节点"
    assert 'kind || "") === "fan_in"' in body, "汇聚目标必须定位 fan_in 节点"
    assert "nodeSteps[id]" in body, "分支列内容必须取自 node_steps"
    assert "本分支回放步骤缺失" in body, "分支缺步骤时须诚实降级"


def test_dag_node_without_steps_is_disabled_with_note():
    """无 node_steps 的节点禁用展开并说明。"""
    src = _src()
    body = _function_body(src, "function dagBarHtml(dag, nodeSteps)")
    assert "disabled" in body, "无步骤节点必须禁用"
    assert "无独立步骤" in body, "无步骤节点必须给出说明"
    assert "title=\"点击查看该节点的回放步骤\"" in body, (
        "可展开节点必须有可发现性提示")


def test_conclusion_data_gaps_empty_is_explicit():
    """data_gaps 空数组显式「无数据缺口」（诚实声明）。"""
    src = _src()
    assert "无数据缺口" in src, "data_gaps 为空时必须显式「无数据缺口」"


# ============================================================
# 8. 括号 / 字符串闭合巡检（本机无 node 的兜底）
# ============================================================

def _strip_js(src: str) -> str:
    """去掉注释 / 字符串 / 正则字面量，留下括号骨架（只用于闭合巡检）。

    在 tests/demo/test_s01.py 的同名剥离上增加**正则字面量识别**：
    scene-kit.js 的 ``DEMO_BADGE_CLASS_RE = /class="[^"]*.../`` 内含双引号，
    朴素算法会把正则里的引号当作字符串起始、级联误吞后续代码括号，
    对含正则的文件必须走本增强版。
    """
    out = []
    i = 0
    n = len(src)
    prev = ""  # 前一个非空白字符（判断 / 是否处于值位置）
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and (prev in "=(,:[!&|?{;" or prev.isalpha()):
            # 值位置的 / 视为正则字面量起点；字符类内的 / 不算结束；跨行即止损
            j = i + 1
            in_class = False
            while j < n:
                c = src[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                elif c == "\n":
                    break
                j += 1
            if j < n and src[j] == "/":
                i = j + 1
                while i < n and src[i].isalpha():  # regex flags
                    i += 1
                continue
        if not ch.isspace():
            prev = ch
        out.append(ch)
        i += 1
    return "".join(out)


def test_js_delimiters_are_balanced():
    """括号/字符串闭合巡检（不是语法分析器，只挡「多一个括号」类事故）。"""
    code = _strip_js(_src())
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for ch in code:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            assert stack and stack[-1] == pairs[ch], (
                f"scene-kit.js 括号不闭合：遇到 {ch}，栈顶 {stack[-1] if stack else '空'}")
            stack.pop()
    assert not stack, f"scene-kit.js 存在未闭合的 {stack}"
    assert "function" in code, "未扫到函数定义，剥离逻辑可能失效"
    for name in ("agentPanel", "knowledgeCard", "replay"):
        assert name in code, f"剥离结果中未见到 {name}（实现被吞？）"


# ============================================================
# 9. injectStyle 样式块（sk- 前缀 + CSS 变量纪律）
# ============================================================

def test_style_block_has_sk_agent_prefixes():
    """agentPanel 样式类一律 sk-agent-/sk-dag-/sk-source- 前缀，经 injectStyle 自注入。"""
    src = _src()
    css = re.search(r"const CSS = `(.*?)`;", src, re.S)
    assert css, "未找到 CSS 模板字符串（injectStyle 的数据源）"
    text = css.group(1)
    for cls in ("sk-source-badge", "sk-source-badge-replay", "sk-source-badge-live",
                "sk-agent-panel", "sk-agent-agents", "sk-agent-chip",
                "sk-dag", "sk-dag-node", "sk-dag-arrow",
                "sk-agent-step", "sk-agent-step-set", "sk-agent-conclusion",
                "sk-agent-hitl", "sk-agent-parallel", "sk-agent-branch"):
        assert f".{cls}" in text, f"样式块缺少 .{cls}（sk- 前缀纪律）"
    assert "function injectStyle()" in src, "样式必须经 injectStyle() 自注入"
    assert "sk-degrade" in text, "降级必须复用既有 sk-degrade 结构"


def test_new_styles_reuse_css_variables_no_dark_hardcode():
    """新样式只取 app.css 变量，不得硬编码深色底（T05 既有断言同口径）。"""
    src = _src()
    css = re.search(r"const CSS = `(.*?)`;", src, re.S).group(1)
    new_block = css[css.index("智能体协同组件（T17"):]
    assert "--border" in new_block and "--primary" in new_block, (
        "新样式必须复用 app.css 的 CSS 变量（--border / --primary）")
    assert not re.search(r"background:\s*#[0-9a-fA-F]{3,8}", new_block), (
        "新样式出现硬编码底色（light/dark 主题切换会静默劣化）")
    assert not re.search(r"color:\s*#[0-9a-fA-F]{3,8}", new_block), (
        "新样式出现硬编码字色（应取 --text / --text-2 / --text-3 变量）")
