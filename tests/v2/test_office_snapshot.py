"""制造数据速查（企业综合助手 · 首屏快照 + 关键词问答）测试。

对应实现：
- ``v2/demo/api.py::api_office_snapshot``（GET /api/v2/office/snapshot）；
- ``v2/agents/office.py`` 的 6 个制造数据只读工具（转发统一注册查询 + TABLE_ACL）；
- ``data/seed_inventory.sql`` 的前缀覆盖扩充。

覆盖点：
1. 端点契约：四块结构 / 四块查询名 / count==len(rows) / degraded 标记；
2. 岗位表级权限：gm 与默认岗位（coordinator）四块放行、无权限岗位（sqe）四块 fail-closed；
3. 单块降级：某块查询异常只置该块 ok=false，其余块不受影响、端点仍 code=0；
4. 数据契约：seed 前缀覆盖（料号/型号 A–Z + 0–9 全开头）、各块字段契约、
   来料批次块用「全量扫描」（守护"近期 10 条"回归）；
5. 关键词问答链路（脚本化 LLM，确定性）：精确料号 / 模糊词 / 数字前缀 / 型号 ATP /
   批号追溯 / 不合格筛选 / 未知编码 / 越权 fail-closed / Agent ReAct 循环。
"""
from __future__ import annotations

import json
import string

import pytest

from mfg_agents.v2.agents.office import OFFICE_TOOLS_META, OfficeAssistantAgent
from mfg_agents.v2.services.office import DemoOfficeBackend

# A–Z 全字母 + 0–9 全数字开头（首字符集合）
_ALNUM_PREFIX = set(string.ascii_uppercase) | set(string.digits)

# 四块（key → 预期查询名）
_EXPECTED_QUERIES = {
    "material_stock": "material_stock_all",
    "shortage": "material_shortage",
    "finished_stock": "finished_stock_all",
    "incoming_batch": "incoming_batch_scan",
}


# ============================================================
# 复用：脚本化 LLM（与 test_office_agent.py 同范式，不走网络）
# ============================================================


class _Resp:
    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _ScriptedLLM:
    def __init__(self, script: list[_Resp]):
        self._script = list(script)
        self._i = 0

    def invoke(self, messages):  # noqa: ANN001, ARG002
        idx = min(self._i, len(self._script) - 1)
        self._i += 1
        return self._script[idx]


def _final(answer: str, **extra) -> _Resp:
    payload = {"answer": answer, "data_used": [], "needs_hitl": False,
               "data_gaps": [], **extra}
    return _Resp(content=json.dumps(payload, ensure_ascii=False))


@pytest.fixture(scope="module")
def orch():
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator()


@pytest.fixture
def backend() -> DemoOfficeBackend:
    return DemoOfficeBackend()


def _agent(orch, backend, script) -> OfficeAssistantAgent:
    agent = OfficeAssistantAgent(orchestrator=orch, backend=backend)
    agent._llm = _ScriptedLLM(script)
    return agent


def _agent_tool(orch, backend, tool: str, roles: list[str]):
    """装配 Agent 并取指定工具的可调用实现（已设置 user_ctx 供 ACL 核验）。"""
    agent = _agent(orch, backend, [_final("ok")])
    agent.user_ctx = {"user_id": "u-1", "name": "张三", "roles": roles}
    return agent._tools()[tool][0]


def _first_chars(rows: list[dict], field: str) -> set[str]:
    return {str(r[field])[0].upper() for r in rows if r.get(field)}


# ============================================================
# 1. 端点契约（直接函数调用，无需起 HTTP 服务）
# ============================================================


def test_snapshot_gm_all_blocks_authorized(orch):
    """gm 读全域：四块全部 authorized/ok、查询名正确、count==len(rows)、不降级。"""
    from mfg_agents.v2.demo import api

    out = api.api_office_snapshot(roles="gm")
    assert out["code"] == 0 and out["message"] == "ok"
    data = out["data"]
    assert data["roles"] == ["gm"]
    assert data["degraded"] is False
    assert "registered_query" in data["source"]

    blocks = data["blocks"]
    assert set(blocks) == set(_EXPECTED_QUERIES)
    for key, qname in _EXPECTED_QUERIES.items():
        b = blocks[key]
        assert b["authorized"] is True
        assert b["ok"] is True
        assert b["query"] == qname
        assert b["message"] == "ok"
        assert b["count"] == len(b["rows"]) > 0
        assert isinstance(b["note"], str) and b["note"]


def test_snapshot_roles_parsing_and_default_fallback():
    """roles 支持逗号+空格分隔；为空时回落 DEFAULT_USER_CTX（manufacturing/coordinator）。"""
    from mfg_agents.v2.demo import api

    parsed = api.api_office_snapshot(roles=" gm , warehouse ")["data"]
    assert parsed["roles"] == ["gm", "warehouse"]      # strip + 排序

    fallback = api.api_office_snapshot(roles=None)["data"]
    assert set(fallback["roles"]) == {"manufacturing", "coordinator"}
    # coordinator 为跨域取证角色 → 三张库存表均放行
    assert all(b["authorized"] is True for b in fallback["blocks"].values())


def test_snapshot_no_permission_role_fail_closed(orch):
    """无库存表权限的岗位（sqe）：四块全部 fail-closed，不返回任何数据、不暴露查询名。"""
    from mfg_agents.v2.demo import api

    blocks = api.api_office_snapshot(roles="sqe")["data"]["blocks"]
    for key in _EXPECTED_QUERIES:
        b = blocks[key]
        assert b["authorized"] is False
        assert b["ok"] is False
        assert b["rows"] == [] and b["count"] == 0
        assert "无权限" in b["message"]
        assert "query" not in b          # fail-closed：越权不暴露查询名/数据


def test_snapshot_single_block_failure_degrades(monkeypatch):
    """某块查询抛异常：仅该块 ok=false（其余块仍 ok），端点不 500、degraded=true。"""
    from mfg_agents.v2.demo import api

    class _Reg:
        def run_query(self, name, params=None):  # noqa: ARG002
            if name == "material_shortage":
                raise RuntimeError("模拟查询故障")
            return [{"part_no": "X", "available_qty": 1, "safety_stock": 2}]

    class _Orch:
        class runtime:  # noqa: N801
            data_registry = _Reg()

    monkeypatch.setattr(api, "get_orchestrator", lambda: _Orch())
    out = api.api_office_snapshot(roles="gm")

    assert out["code"] == 0                       # 单块降级绝不拖垮整页
    blocks = out["data"]["blocks"]
    assert blocks["shortage"]["authorized"] is True
    assert blocks["shortage"]["ok"] is False
    assert blocks["shortage"]["rows"] == [] and blocks["shortage"]["count"] == 0
    assert "暂不可用" in blocks["shortage"]["message"]
    for key in ("material_stock", "finished_stock", "incoming_batch"):
        assert blocks[key]["ok"] is True and blocks[key]["count"] == 1
    assert out["data"]["degraded"] is True


# ============================================================
# 2. 数据契约（seed_inventory.sql 前缀覆盖 + 各块字段）
# ============================================================


def test_seed_material_part_prefix_covers_all_alnum(orch):
    """材料料号以 A–Z / 0–9 各开头至少一条（速查检索完整性演示底座）。"""
    rows = orch.runtime.data_registry.run_query("material_stock_all")
    assert _first_chars(rows, "part_no") == _ALNUM_PREFIX


def test_seed_finished_model_prefix_covers_all_alnum(orch):
    """成品型号以 A–Z / 0–9 各开头至少一条。"""
    rows = orch.runtime.data_registry.run_query("finished_stock_all")
    assert _first_chars(rows, "product_model") == _ALNUM_PREFIX


def test_registered_queries_field_contract(orch):
    """四块查询返回行含各自关键字段（口径字段契约）。"""
    reg = orch.runtime.data_registry

    ms = reg.run_query("material_stock_all")
    assert ms and {"part_no", "available_qty", "safety_stock"} <= set(ms[0])

    sh = reg.run_query("material_shortage")
    assert sh and {"part_no", "available_qty", "safety_stock",
                   "shortage_qty"} <= set(sh[0])
    assert all(int(r["available_qty"]) < int(r["safety_stock"]) for r in sh)

    fs = reg.run_query("finished_stock_all")
    assert fs and {"product_model", "available_qty"} <= set(fs[0])

    ib = reg.run_query("incoming_batch_scan")
    assert ib and {"batch_no", "inspect_status"} <= set(ib[0])
    assert {r["inspect_status"] for r in ib} <= {"pass", "fail", "pending",
                                                "deviation"}


def test_table_acl_allowlists_present(orch):
    """三张库存表均在 TABLE_ACL 白名单内（非空，fail-closed 前提）。"""
    from mfg_agents.v2.services.text2sql import TABLE_ACL

    for table in ("material_inventory", "finished_inventory", "incoming_batch"):
        assert TABLE_ACL.get(table)
    # 无权限岗位确实不在白名单（守护 fail-closed 用例前提）
    for table in ("material_inventory", "finished_inventory", "incoming_batch"):
        assert "sqe" not in TABLE_ACL[table]


def test_incoming_batch_block_uses_full_scan(orch):
    """来料批次块必须用「全量扫描（上限 60）」而非「近期 10 条」——
    速查卡是在返回行上做关键词过滤，退回 LIMIT 10 会让更早批次搜不到。"""
    from mfg_agents.v2.demo import api

    b = api.api_office_snapshot(roles="gm")["data"]["blocks"]["incoming_batch"]
    assert b["query"] == "incoming_batch_scan"
    assert b["count"] > 10                       # 守护 LIMIT 10 回归
    assert "全量" in b["note"]
    # 与表内总行数一致（未因窗口截断）
    total = orch.runtime.data_registry.run_query("incoming_batch_scan")
    assert b["count"] == len(total)


# ============================================================
# 3. 关键词问答链路：工具实现（确定性，直接调用）
# ============================================================


def test_tool_material_stock_exact_part_no(orch, backend):
    """精确料号（数字前缀）走 material_stock_by_part，返回库位。"""
    fn = _agent_tool(orch, backend, "query_material_stock", ["gm"])
    res = fn(part_no="9V-BAT-CLIP")
    assert res["ok"] is True and res["query"] == "material_stock_by_part"
    assert res["count"] > 0
    assert all(r["part_no"] == "9V-BAT-CLIP" for r in res["rows"])
    assert res["rows"][0]["bin_location"] == "A-07-09"


def test_tool_material_stock_keyword_alpha_prefix(orch, backend):
    """字母前缀料号用关键词模糊命中。"""
    fn = _agent_tool(orch, backend, "query_material_stock", ["gm"])
    res = fn(keyword="ZENER-BZX84-51")
    assert res["ok"] is True and res["query"] == "material_stock_search"
    assert any(r["part_no"] == "ZENER-BZX84-51" for r in res["rows"])


def test_tool_material_stock_exact_miss_falls_back_to_search(orch, backend):
    """给的是规格片段（非完整料号）→ 精确无果退化为模糊搜索。"""
    fn = _agent_tool(orch, backend, "query_material_stock", ["gm"])
    res = fn(part_no="BZX84")
    assert res["ok"] is True and res["query"] == "material_stock_search"
    assert any(r["part_no"] == "ZENER-BZX84-51" for r in res["rows"])


def test_tool_material_stock_unknown_code_returns_empty(orch, backend):
    """未知编码 → 空结果 + ok=true（不崩、不编造）。"""
    fn = _agent_tool(orch, backend, "query_material_stock", ["gm"])
    res = fn(keyword="NO-SUCH-CODE-XYZ")
    assert res["ok"] is True and res["count"] == 0 and res["rows"] == []


def test_tool_finished_stock_digit_prefix_model_atp(orch, backend):
    """数字前缀型号 → ATP = 在库-预留。"""
    fn = _agent_tool(orch, backend, "query_finished_stock", ["gm"])
    res = fn(model="5G-网关型号")
    assert res["ok"] is True and res["query"] == "finished_stock_by_model"
    assert all(r["product_model"] == "5G-网关型号" for r in res["rows"])
    assert any(int(r["available_qty"]) == 1100 for r in res["rows"])   # 2000-900


def test_tool_incoming_batch_by_no_and_unqualified_filter(orch, backend):
    """批号精确查（含 IQC 结论）；unqualified_only 只留不合格/让步接收。"""
    fn = _agent_tool(orch, backend, "query_incoming_batch", ["gm"])

    by_no = fn(batch_no="MAT-BCP-1058")
    assert by_no["query"] == "incoming_batch_by_no"
    assert any(r["batch_no"] == "MAT-BCP-1058"
               and r["inspect_status"] == "pass" for r in by_no["rows"])

    bad = fn(unqualified_only=True)
    assert bad["query"] == "incoming_batch_scan" and bad["count"] >= 1
    assert all(r["inspect_status"] in ("fail", "deviation")
               for r in bad["rows"])


def test_tool_batch_trace_by_no(orch, backend):
    """批次追溯（来料综合 → 检验 → 不良线索）按批号可查。"""
    fn = _agent_tool(orch, backend, "query_batch_trace", ["gm"])
    res = fn(batch_no="MAT-BCP-1058")
    assert res["ok"] is True and res["count"] >= 1
    assert res["rows"][0]["batch_no"] == "MAT-BCP-1058"


def test_tool_material_stock_no_permission_fail_closed(orch, backend):
    """无权限岗位调用数据工具 → 结构化"无权限"，不返回任何行。"""
    fn = _agent_tool(orch, backend, "query_material_stock", ["sqe"])
    res = fn()
    assert res["ok"] is False and "无权限" in res["error"]
    assert "rows" not in res


# ============================================================
# 4. Agent ReAct 循环：关键词问句 → 数据只读工具（脚本化 LLM）
# ============================================================


def test_agent_keyword_question_calls_material_stock(orch, backend):
    """问「ZENER-BZX84-51 还有多少库存」→ 调 query_material_stock，只读、不触发 HITL。"""
    script = [
        _Resp(tool_calls=[{"name": "query_material_stock", "id": "c1",
                           "args": {"keyword": "ZENER-BZX84-51"}}]),
        _final("ZENER-BZX84-51 在电子料仓 A-06-08，可用量 60000 个。",
               data_used=["query_material_stock(keyword=ZENER-BZX84-51)"]),
    ]
    out = _agent(orch, backend, script).run(
        "ZENER-BZX84-51 还有多少库存",
        user_ctx={"user_id": "u-1", "name": "张三", "roles": ["gm"]})

    assert [s["tool"] for s in out["steps"]] == ["query_material_stock"]
    # 工具返回 {"ok": true, ...}（OfficeAssistantAgent 据此如实标记步骤状态）
    assert out["steps"][0]["ok"] is True
    assert '"ok": true' in out["steps"][0]["summary"]
    assert "ZENER-BZX84-51" in out["answer"]
    assert out["needs_hitl"] is False


def test_agent_question_digit_prefix_finished_model(orch, backend):
    """问数字前缀型号可发量 → 调 query_finished_stock。"""
    script = [
        _Resp(tool_calls=[{"name": "query_finished_stock", "id": "c1",
                           "args": {"model": "5G-网关型号"}}]),
        _final("5G-网关型号 深圳成品仓可发 1100 台。",
               data_used=["query_finished_stock(model=5G-网关型号)"]),
    ]
    out = _agent(orch, backend, script).run(
        "5G-网关型号现在能发多少",
        user_ctx={"user_id": "u-1", "name": "张三", "roles": ["gm"]})

    assert [s["tool"] for s in out["steps"]] == ["query_finished_stock"]
    assert out["steps"][0]["ok"] is True


def test_agent_data_tool_denied_for_low_permission(orch, backend):
    """低权限岗位走 Agent 问库存 → 工具结构化返回无权限（step.ok=False），不绕过。

    工具不抛异常（审计不误记为崩溃），但步骤状态据 payload 的 ok=false 如实标失败。
    """
    script = [
        _Resp(tool_calls=[{"name": "query_material_stock", "id": "c1",
                           "args": {}}]),
        _final("当前岗位无权限查询材料库存，请走物控/仓储岗。"),
    ]
    out = _agent(orch, backend, script).run(
        "帮我查下材料库存", user_ctx={"user_id": "u-2", "name": "李四",
                                    "roles": ["sqe"]})

    assert [s["tool"] for s in out["steps"]] == ["query_material_stock"]
    assert out["steps"][0]["ok"] is False      # 越权被如实标记为失败
    assert "无权限" in out["steps"][0]["summary"]


# ============================================================
# 5. 工具元数据一致性（能力即工具：meta 与实现同步）
# ============================================================


def test_office_meta_matches_implemented_tools(orch):
    agent = OfficeAssistantAgent(orchestrator=orch)
    meta = {m["name"] for m in OFFICE_TOOLS_META}
    assert meta == set(agent._tools())
    assert {"query_material_stock", "query_finished_stock",
            "query_incoming_batch", "query_stock_aging",
            "query_shortage", "query_batch_trace"} <= meta
