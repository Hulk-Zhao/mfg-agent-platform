"""Text2SQLService 八道防线测试：指标库/注册查询/明细探查 + 审核链 + 脱敏。"""
import pytest

from mfg_agents.v2.contracts.data_source import DataSourceRegistry, SQLiteDataSource
from mfg_agents.v2.services.text2sql import (
    SENSITIVE_COLUMNS,
    TABLE_ACL,
    FieldMasker,
    MetricLibrary,
    QueryLibrary,
    ResultValidator,
    SQLAuditError,
    SQLAuditor,
    Text2SQLService,
)

DB_PATH = "data/mfg_agent.db"
QUERIES_FILE = "src/mfg_agents/v2/data/queries.yaml"
METRICS_DIR = "src/mfg_agents/v2/data/metrics"

MANAGER_CTX = {"user_id": "u1", "dept": "manufacturing",
               "roles": ["manufacturing", "coordinator"]}
PROCUREMENT_CTX = {"user_id": "u2", "dept": "procurement",
                   "roles": ["procurement"]}


@pytest.fixture()
def service() -> Text2SQLService:
    queries = QueryLibrary.load(QUERIES_FILE)
    metrics = MetricLibrary.load(METRICS_DIR)
    registry = DataSourceRegistry()
    registry.register(SQLiteDataSource(DB_PATH, name="dev", queries={
        name: str(spec["sql"]) for name, spec in queries._queries.items()}))
    return Text2SQLService(registry, metrics, queries)


# ---------- 通道①：指标库 ----------

def test_metric_channel_otd(service):
    result = service.query_metric("otd_rate", user_ctx=MANAGER_CTX)
    assert result.channel == "metric"
    assert result.confidence == 0.95
    assert result.sql.upper().startswith("SELECT")
    assert result.rows and "otd_rate_pct" in result.rows[0]
    assert result.provenance["metric_id"] == "otd_rate"
    assert result.provenance["tables"] == ["shipment"]


def test_metric_keyword_match_pipeline(service):
    """统一入口：问题命中指标关键词 → 走指标库。"""
    result = service.query("现在的OTD准时交付率是多少", MANAGER_CTX)
    assert result.channel == "metric"


# ---------- 通道②：注册查询 ----------

def test_registered_channel_with_params(service):
    result = service.query_registered(
        "mfg_oee", {"line": "SMT-1", "model": "A型号"}, MANAGER_CTX)
    assert result.channel == "registered"
    assert len(result.rows) == 2
    assert result.provenance["query_name"] == "mfg_oee"
    assert result.provenance["params"] == {"line": "SMT-1", "model": "A型号"}
    assert result.rows[-1]["oee"] == 32.1


def test_registered_keyword_match_pipeline(service):
    result = service.query("SMT-1线今天有什么设备告警", MANAGER_CTX)
    assert result.channel == "registered"


# ---------- 审核链 ----------

@pytest.mark.parametrize("sql", [
    "DROP TABLE inventory",
    "DELETE FROM line_stop_event",
    "INSERT INTO inventory VALUES (1,'X','Y',1,1)",
    "UPDATE shipment SET qty = 0",
    "SELECT 1; DROP TABLE inventory",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA journal_mode=WAL",
    "SELECT * FROM t INTO new_t",
])
def test_regex_and_structure_guard_blocks(sql):
    with pytest.raises(SQLAuditError):
        SQLAuditor.audit(sql, MANAGER_CTX)


def test_table_permission_blocks_unauthorized_table():
    """表级权限：制造角色查客诉表必须被拒（fail-closed）。"""
    sql = "SELECT * FROM customer_complaint"
    with pytest.raises(SQLAuditError, match="权限"):
        SQLAuditor.audit(sql, {"dept": "manufacturing",
                               "roles": ["manufacturing"]})


def test_table_permission_blocks_unknown_table():
    with pytest.raises(SQLAuditError, match="未注册"):
        SQLAuditor.audit("SELECT * FROM secret_table", MANAGER_CTX)


def test_registered_query_permission_denied_degraded(service):
    """权限不足的注册查询 → fallback manual_ticket（不静默放行）。"""
    result = service.query_registered(
        "supply_batches_today", {}, {"dept": "manufacturing",
                                     "roles": ["manufacturing"]})
    assert result.fallback == "manual_ticket"
    assert "审核拦截" in result.warnings[0]


def test_valid_select_passes_audit():
    sql = ("SELECT defect_type, SUM(defect_qty) AS total_qty "
           "FROM quality_defect WHERE line_code = 'SMT-1' GROUP BY defect_type")
    assert SQLAuditor.audit(sql, MANAGER_CTX) == []


# ---------- 结果校验 / 脱敏 ----------

def test_result_validator_warnings():
    assert any("为空" in w for w in ResultValidator.validate([], 200))
    many = [{"a": 1}] * 200
    assert any("上限" in w for w in ResultValidator.validate(many, 200))
    assert ResultValidator.validate([{"a": 1}], 200) == []


def test_field_masking_by_role(service):
    rows = [{"supplier": "千住金属", "unit_price": 100}]
    masked_rows, masked = FieldMasker.mask(rows, {"roles": ["manufacturing"]})
    assert masked is True
    assert masked_rows[0]["supplier"] == SENSITIVE_COLUMNS["supplier"]["mask"]
    visible, masked2 = FieldMasker.mask(rows, {"roles": ["procurement"]})
    assert masked2 is False
    assert visible[0]["supplier"] == "千住金属"


def test_registered_query_masking_end_to_end(service):
    """供应链查询：制造角色看到脱敏供应商名；采购角色看到原文。"""
    r1 = service.query_registered("supply_batches_today", {}, MANAGER_CTX)
    assert r1.masked is True
    assert r1.rows[0]["supplier"] == "供应商***"
    r2 = service.query_registered("supply_batches_today", {}, PROCUREMENT_CTX)
    assert r2.masked is False
    assert r2.rows[0]["supplier"] == "千住金属"


# ---------- 通道③：明细探查（LLM 不可用 → 人工兜底） ----------

def test_detail_channel_fallback_when_llm_unavailable(service, monkeypatch):
    monkeypatch.setattr("mfg_agents.common.llm_factory.llm_available",
                        lambda: False)
    result = service.query_detail("查一下所有表的行数", MANAGER_CTX)
    assert result.channel == "fallback"
    assert result.fallback == "manual_ticket"
    assert result.confidence == 0.0


def test_detail_channel_generates_and_audits(service, monkeypatch):
    """LLM 可用时：生成 SQL → 审核链 → 只读执行（桩 LLM 返回合法 SELECT）。"""

    class _Resp:
        content = "SELECT COUNT(*) AS cnt FROM inventory"

    class _LLM:
        def invoke(self, prompt):
            return _Resp()

    monkeypatch.setattr("mfg_agents.common.llm_factory.llm_available",
                        lambda: True)
    monkeypatch.setattr("mfg_agents.common.llm_factory.get_llm",
                        lambda *a, **k: _LLM())
    result = service.query_detail("库存一共有多少条记录", MANAGER_CTX)
    assert result.channel == "detail"
    assert result.confidence == 0.6
    assert result.rows[0]["cnt"] == 2


def test_detail_channel_blocks_llm_generated_ddl(service, monkeypatch):
    """LLM 生成的 DDL 必须在生成阶段即被 V1 只读守卫拦截 → 人工兜底。"""

    class _Resp:
        content = "DROP TABLE inventory"

    class _LLM:
        def invoke(self, prompt):
            return _Resp()

    monkeypatch.setattr("mfg_agents.common.llm_factory.llm_available",
                        lambda: True)
    monkeypatch.setattr("mfg_agents.common.llm_factory.get_llm",
                        lambda *a, **k: _LLM())
    result = service.query_detail("把库存表删了", MANAGER_CTX)
    assert result.fallback == "manual_ticket"
    assert "SQL 生成失败" in result.warnings[0]


def test_detail_channel_audit_blocks_unauthorized_table(service, monkeypatch):
    """LLM 生成的合法 SELECT 但越权表 → 审核链（表级权限）拦截。"""

    class _Resp:
        content = "SELECT complaint_no FROM customer_complaint"

    class _LLM:
        def invoke(self, prompt):
            return _Resp()

    restricted = {"user_id": "u3", "dept": "manufacturing",
                  "roles": ["manufacturing"]}
    monkeypatch.setattr("mfg_agents.common.llm_factory.llm_available",
                        lambda: True)
    monkeypatch.setattr("mfg_agents.common.llm_factory.get_llm",
                        lambda *a, **k: _LLM())
    result = service.query_detail("查一下客诉单号", restricted)
    assert result.fallback == "manual_ticket"
    assert "审核链拦截" in result.warnings[0]
    assert result.provenance["blocked"]


def test_table_acl_contains_all_demo_tables():
    """表白名单必须覆盖 8 张模拟表（fail-closed 的完整性）。"""
    expected = {"production_output", "line_stop_event", "quality_defect",
                "material_batch", "equipment_log", "inventory", "shipment",
                "customer_complaint"}
    assert expected <= set(TABLE_ACL)
