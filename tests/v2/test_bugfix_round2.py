"""Round 2 修复回归：QA 报告的 5 个 Bug 修复行为锁定（工程师侧）。

对应 QA 证据测试（tests/v2/test_qa_*.py）的安全行为断言；
本文件从工程侧独立锁死修复，防止回归。QA 侧 4 个"当前行为锚定"测试
（BUG-4 内存库、逗号 join 泄露、事件数 21、别名脱敏两参调用）的翻转
由 QA 同步更新，本文件不触碰。
"""
import sqlite3
import xml.etree.ElementTree as ElementTree

import pytest

from mfg_agents.v2.contracts.adapters.wecom_crypto import (
    WeComCryptoError,
    decrypt_msg,
    encrypt_msg,
)
from mfg_agents.v2.services.text2sql import (
    FieldMasker,
    SQLAuditError,
    SQLAuditor,
    extract_tables,
)
from mfg_agents.v2.services.workflow import WorkflowEngine

MFG = {"dept": "manufacturing", "roles": ["manufacturing"]}
WAREHOUSE = {"dept": "warehouse", "roles": ["warehouse"]}
QUALITY = {"dept": "quality", "roles": ["quality"]}


# ---------- BUG-1：表级 ACL 绕过变体必须全部拦截 ----------

class TestBug1AclBypassFixed:
    @pytest.mark.parametrize("sql", [
        "SELECT complaint_no FROM(customer_complaint)",
        "SELECT complaint_no FROM\n(customer_complaint)",
        "SELECT complaint_no FROM `customer_complaint`",
        'SELECT complaint_no FROM "customer_complaint"',
        "SELECT c.complaint_no FROM inventory i, customer_complaint c",
        "SELECT complaint_no FROM [customer_complaint]",
        "select complaint_no from( customer_complaint )",
    ])
    def test_bypass_variants_blocked(self, sql):
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, MFG)

    def test_comma_join_blocked_for_warehouse(self):
        sql = ("SELECT c.complaint_no FROM inventory i, "
               "customer_complaint c")
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, WAREHOUSE)

    @pytest.mark.parametrize("sql,expected", [
        ("SELECT * FROM inventory", {"inventory"}),
        ("SELECT * FROM inventory i JOIN quality_defect q "
         "ON i.product_model = q.product_model",
         {"inventory", "quality_defect"}),
        ("SELECT * FROM a, b, c", {"a", "b", "c"}),
        ("SELECT * FROM (SELECT * FROM customer_complaint) x",
         {"customer_complaint"}),
        ("WITH c AS (SELECT * FROM line_stop_event) SELECT * FROM c",
         {"line_stop_event", "c"}),
        ("SELECT * FROM inventory -- FROM customer_complaint",
         {"inventory"}),
    ])
    def test_extract_tables_coverage(self, sql, expected):
        assert extract_tables(sql) == expected

    def test_legitimate_queries_still_pass(self):
        """合法注册查询的 SQL 形态不得被误拦（防修复过严）。"""
        sql = ("SELECT defect_type, SUM(defect_qty) AS total_qty "
               "FROM quality_defect WHERE line_code = :line "
               "GROUP BY defect_type")
        assert SQLAuditor.audit(sql, QUALITY) == []


# ---------- BUG-2：脱敏别名绕过必须修复 ----------

class TestBug2AliasMaskingFixed:
    def test_alias_masked_with_sql_context(self):
        rows = [{"batch_no": "B1", "s": "千住金属"}]
        masked_rows, masked = FieldMasker.mask(
            rows, QUALITY, "SELECT supplier AS s FROM material_batch")
        assert masked is True
        assert masked_rows[0]["s"] == "供应商***"

    def test_table_qualified_column_masked(self):
        rows = [{"supplier": "千住金属"}]
        masked_rows, masked = FieldMasker.mask(
            rows, QUALITY, "SELECT m.supplier FROM material_batch m")
        assert masked is True
        assert masked_rows[0]["supplier"] == "供应商***"

    def test_procurement_role_sees_original_via_alias(self):
        rows = [{"s": "千住金属"}]
        masked_rows, masked = FieldMasker.mask(
            rows, {"roles": ["procurement"]},
            "SELECT supplier AS s FROM material_batch")
        assert masked is False
        assert masked_rows[0]["s"] == "千住金属"

    def test_alias_map_parsing(self):
        mapping = FieldMasker.alias_map(
            "SELECT batch_no, supplier AS s, m.unit_price AS p, "
            "SUM(defect_qty) AS total FROM material_batch m")
        assert mapping["s"] == "supplier"
        assert mapping["p"] == "unit_price"
        assert mapping["batch_no"] == "batch_no"
        assert mapping["total"] == "defect_qty"

    def test_end_to_end_alias_masking(self, monkeypatch):
        """端到端：明细通道 LLM 桩返回带别名 SQL → 脱敏生效。"""
        from mfg_agents.v2.contracts.data_source import (
            DataSourceRegistry,
            SQLiteDataSource,
        )
        from mfg_agents.v2.services.text2sql import (
            MetricLibrary,
            QueryLibrary,
            Text2SQLService,
        )

        class _Resp:
            content = "SELECT supplier AS s FROM material_batch"

        class _LLM:
            def invoke(self, prompt):
                return _Resp()

        monkeypatch.setattr("mfg_agents.common.llm_factory.llm_available",
                            lambda: True)
        monkeypatch.setattr("mfg_agents.common.llm_factory.get_llm",
                            lambda *a, **k: _LLM())
        queries = QueryLibrary.load("src/mfg_agents/v2/data/queries.yaml")
        registry = DataSourceRegistry()
        registry.register(SQLiteDataSource("data/mfg_agent.db", name="dev"))
        service = Text2SQLService(
            registry, MetricLibrary.load("src/mfg_agents/v2/data/metrics"),
            queries)
        result = service.query_detail("各批次供应商是谁", QUALITY)
        assert result.masked is True
        assert all(r["s"] == "供应商***" for r in result.rows)


# ---------- BUG-3：roles=None 必须 fail-closed 抛 SQLAuditError ----------

class TestBug3NullRolesFixed:
    @pytest.mark.parametrize("ctx", [
        None, {}, {"roles": None}, {"dept": None, "roles": None},
        {"dept": "", "roles": []},
    ])
    def test_null_roles_fail_closed(self, ctx):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit("SELECT * FROM inventory", ctx)


# ---------- BUG-4：:memory: 工作流库必须可用 ----------

class TestBug4MemoryDbFixed:
    def test_memory_db_url_usable(self):
        engine = WorkflowEngine("sqlite:///:memory:")
        task = engine.create_task("impact_assessment", "t",
                                  assignees=["计划"])
        assert task.status == "pending"
        started = engine.advance(task.task_uid, "start")
        assert started.status == "running"
        assert engine.get(task.task_uid).title == "t"

    def test_memory_db_hitl_and_query(self):
        engine = WorkflowEngine("sqlite:///:memory:")
        hitl = engine.create_task("hitl_restart", "复线决策", hitl=True)
        assert hitl.status == "waiting_hitl"
        assert len(engine.pending_hitl()) == 1
        approved = engine.approve(hitl.task_uid, "王制造")
        assert approved.status == "running"

    def test_memory_db_metric_seed_present(self):
        """内存库同样播种指标库（与文件库行为一致）。"""
        engine = WorkflowEngine("sqlite:///:memory:")
        conn = engine._connect()
        rows = conn.execute(
            "SELECT id FROM platform_metric_def").fetchall()
        assert any(r["id"] == "otd_rate" for r in rows)


# ---------- BUG-5：SSE 路径 tool 事件必须全部到达 ----------

class TestBug5ToolEventsFixed:
    @pytest.fixture()
    def orchestrator(self, monkeypatch, tmp_path):
        from mfg_agents.common.config import settings
        from mfg_agents.common.llm_factory import reset_probe_cache
        from mfg_agents.v2.core.orchestrator import (
            V2Orchestrator,
            V2Runtime,
        )
        reset_probe_cache()
        for mod in ("mfg_agents.common.llm_factory",
                    "mfg_agents.v2.core.router",
                    "mfg_agents.v2.core.node_registry"):
            monkeypatch.setattr(f"{mod}.llm_available", lambda: False)
        monkeypatch.setattr(settings, "WORKFLOW_DB",
                            f"sqlite:///{tmp_path / 'workflow.db'}")
        return V2Orchestrator(V2Runtime.create_default())

    def test_tool_events_reach_contextvar_queue(self, orchestrator):
        """bind_queue + QueueSink()（无参）：worker 线程 tool 事件必须到达。"""
        from mfg_agents.v2.core.events import QueueSink, bind_queue, unbind
        q, token = bind_queue()
        try:
            orchestrator.handle("A型号在SMT-1停线，何时复线？",
                                {"roles": ["manufacturing", "coordinator"]},
                                sink=QueueSink())
        finally:
            unbind(token)
        events = []
        while not q.empty():
            events.append(q.get())
        types = [e["type"] for e in events]
        assert types.count("tool_start") == 6
        assert types.count("tool_end") == 6
        assert types.count("agent_start") == 10
        assert len(events) == 33

    def test_explicit_queue_sink_works(self, orchestrator):
        """显式持有队列引用的 QueueSink：不经 contextvar 也可用。"""
        import queue as queue_mod
        from mfg_agents.v2.core.events import QueueSink
        q: queue_mod.Queue = queue_mod.Queue()
        orchestrator.handle("A型号在SMT-1停线，何时复线？",
                            {"roles": ["manufacturing", "coordinator"]},
                            sink=QueueSink(q))
        events = []
        while not q.empty():
            events.append(q.get())
        assert sum(1 for e in events if e["type"] == "tool_start") == 6

    def test_callback_path_not_broken(self, orchestrator):
        """CLI callback 路径不回归（33 事件基线）。"""
        seen: list[dict] = []
        orchestrator.handle("A型号在SMT-1停线，何时复线？",
                            {"roles": ["manufacturing", "coordinator"]},
                            sink=_Sink(seen.append))
        assert len(seen) == 33


class _Sink:
    def __init__(self, callback):
        self._callback = callback

    def emit(self, event):
        self._callback(event)


# ---------- OBS-2：截断密文异常契约统一 ----------

class TestObs2TruncatedCiphertext:
    def test_truncated_ciphertext_raises_crypto_error(self):
        cipher = encrypt_msg("abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                             "wx5823bf96d3bd56c7", "hello")
        truncated = cipher[:20]  # 非 16 倍数
        with pytest.raises(WeComCryptoError):
            decrypt_msg("abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                        "wx5823bf96d3bd56c7", truncated)
