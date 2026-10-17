"""公共基础设施层（mfg_agents/common）行为基线。

历史：这些用例原在 ``tests/test_v1_regression.py``，守护的是 V1 时期遗留的两块资产——
场景关键词路由规则与 SQL 只读守卫。V1 原型层（agents/ graph/ ui/ api.py）移除后，
这两块资产已归入 ``mfg_agents/common/`` 且仍被 V2 主链路运行时依赖
（V2 路由 L0 快路径复用 ``business.SCENE_RULES``；Text2SQL 复用 ``sql_tools._extract_sql``），
故基线随资产一并迁入本文件，继续守护。

命名去除了 ``v1_`` 前缀：它们守护的是公共层契约，不再属于任何一版原型。

不随迁的部分（已随 V1 原型层废弃）：
- V1 LangGraph 图编译（``graph.builder.build_graph``）——V2 的场景 DAG 由
  tests/v2/test_yaml_dag.py、test_scenario_registry.py 覆盖；
- V1 四域 Agent 直调结论——等价语义改由
  tests/v2/test_e2e_v2.py::test_domain_conclusions_baseline 以固化基线守护。
"""
from mfg_agents.common.business import SCENE_RULES, business_agent
from mfg_agents.common.events import get_queue, init_queue, push
from mfg_agents.common.sql_tools import _extract_sql


def test_business_routing_five_scenarios():
    """五场景关键词路由规则保持成立（V2 路由 L0 快路径依赖）。"""
    cases = {
        "A型号在SMT-1停线了，什么时候能复线？": "line_stop",
        "今天锡膏批次有问题": "incoming_quality",
        "华为投诉BATCH-A-1008": "customer_complaint",
        "A型号能发多少货": "delivery_risk",
        "SMT-1设备参数异常吗": "equipment_warning",
    }
    for query, expected in cases.items():
        assert business_agent({"user_query": query})["scenario"] == expected


def test_business_entity_extraction():
    """型号/线体/紧急度抽取规则保持成立（V2 路由器实体抽取依赖）。"""
    state = business_agent({"user_query": "A型号在SMT-1停线，客户急单"})
    assert state["product_model"] == "A型号"
    assert state["line_code"] == "SMT-1"
    assert state["urgency"] == "high"


def test_scene_rules_nonempty():
    """SCENE_RULES 资产保持可用（V2 路由器 L0 快路径复用同一份规则）。"""
    assert len(SCENE_RULES) == 5


def test_sql_guard_blocks_ddl_dml():
    """只读守卫：DDL/DML 注入必须拦截（安全边界回归）。"""
    for malicious in [
        "DROP TABLE production_output",
        "DELETE FROM line_stop_event",
        "INSERT INTO inventory VALUES (9,'X','Y',1,1)",
        "UPDATE shipment SET qty = 0",
    ]:
        try:
            _extract_sql(malicious)
            raise AssertionError(f"未拦截非法 SQL: {malicious}")
        except ValueError:
            pass


def test_sql_guard_multistatement_limitation():
    """已知边界：多语句只取首个 SELECT（DROP 被截掉）。
    V2 SQLAuditor.regex_guard 已补齐该缺口（tests/v2/test_text2sql.py 覆盖）。"""
    sql = _extract_sql("SELECT * FROM t; DROP TABLE t")
    assert sql == "SELECT * FROM t"


def test_sql_guard_allows_select():
    """只读守卫：合法 SELECT 放行（含 markdown 包裹与思考标签剥离）。"""
    sql = _extract_sql("<think>...</think>\n```sql\nSELECT id FROM inventory\n```")
    assert sql == "SELECT id FROM inventory"


def test_event_queue_singleton_semantics():
    """事件队列契约：init_queue 幂等 + push 入队 + get 取出。

    迁入自 tests/v2/test_qa_v1_equivalence.py（原 test_v1_event_queue_singleton_semantics）。
    该文件的另外两个用例依赖 V1 LangGraph 图，随 V1 原型层一并废弃。
    """
    init_queue()
    init_queue()  # 幂等，不炸
    q = get_queue()
    before = q.qsize()
    push({"type": "qa_probe", "detail": "x"})
    assert q.qsize() == before + 1
    ev = q.get()
    assert ev["type"] == "qa_probe"
