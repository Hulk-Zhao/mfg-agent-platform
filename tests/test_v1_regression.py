"""V1 回归测试（离线可跑）：保护 V1 既有行为，零破坏的守门测试。

覆盖原 V1 演示脚本（test_e2e/test_events/test_chunk/test_sql）验证的行为：
1. build_graph() 编译出的图可调用（V1 图保留可运行）；
2. business_agent 五场景关键词路由规则；
3. sql_tools 只读守卫：DDL/DML 注入必须拦截，仅 SELECT 放行；
4. 四个域 Agent 对本地 SQLite 的查询结论（与 V1 演示输出同构）。
"""
from mfg_agents.agents.business import SCENE_RULES, business_agent
from mfg_agents.agents.equipment import equipment_agent
from mfg_agents.agents.manufacturing import ManufacturingAgent
from mfg_agents.agents.quality import quality_agent
from mfg_agents.agents.supply_chain import supply_chain_agent
from mfg_agents.graph.builder import build_graph
from mfg_agents.tools.sql_tools import _extract_sql


def test_v1_graph_compiles():
    """V1 LangGraph 图必须保持可编译可调用（演进不重写）。"""
    app = build_graph()
    assert app is not None
    assert app.get_graph() is not None


def test_v1_business_routing_five_scenarios():
    """五场景关键词路由规则保持成立。"""
    cases = {
        "A型号在SMT-1停线了，什么时候能复线？": "line_stop",
        "今天锡膏批次有问题": "incoming_quality",
        "华为投诉BATCH-A-1008": "customer_complaint",
        "A型号能发多少货": "delivery_risk",
        "SMT-1设备参数异常吗": "equipment_warning",
    }
    for query, expected in cases.items():
        assert business_agent({"user_query": query})["scenario"] == expected


def test_v1_business_entity_extraction():
    """型号/线体/紧急度抽取规则保持成立。"""
    state = business_agent({"user_query": "A型号在SMT-1停线，客户急单"})
    assert state["product_model"] == "A型号"
    assert state["line_code"] == "SMT-1"
    assert state["urgency"] == "high"


def test_v1_scene_rules_nonempty():
    """SCENE_RULES 资产保持可用（V2 路由器 L0 快路径复用同一份规则）。"""
    assert len(SCENE_RULES) == 5


def test_v1_sql_guard_blocks_ddl_dml():
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


def test_v1_sql_guard_multistatement_limitation():
    """V1 已知边界：多语句只取首个 SELECT（DROP 被截掉）。
    V2 SQLAuditor.regex_guard 已补齐该缺口（tests/v2/test_text2sql.py 覆盖）。"""
    sql = _extract_sql("SELECT * FROM t; DROP TABLE t")
    assert sql == "SELECT * FROM t"


def test_v1_sql_guard_allows_select():
    """只读守卫：合法 SELECT 放行（含 markdown 包裹与思考标签剥离）。"""
    sql = _extract_sql("<think>...</think>\n```sql\nSELECT id FROM inventory\n```")
    assert sql == "SELECT id FROM inventory"


def test_v1_domain_agents_conclusions():
    """四域 Agent 对本地 SQLite 的结论与 V1 演示输出同构（离线）。"""
    state = {"product_model": "A型号", "line_code": "SMT-1"}
    mfg = ManufacturingAgent().run(state)
    assert mfg["confirmed_stop"] is True
    assert "32.1" in mfg["production_summary"]

    quality = quality_agent(state)
    assert quality["dominant_defect"]["defect_type"] == "锡膏偏移"
    assert "77" in quality["quality_summary"]

    supply = supply_chain_agent(state)
    assert "无来料异常" in supply["supply_summary"]

    equipment = equipment_agent(state)
    assert len(equipment["abnormal_alarms"]) == 3
    assert "刮刀压力" in equipment["equipment_summary"]
