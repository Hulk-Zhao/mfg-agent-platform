"""ScenarioRegistry 与 DataSource 测试（含 fail-fast 与只读边界）。"""
import sqlite3

import pytest

from mfg_agents.v2.contracts.data_source import (
    DataSourceRegistry,
    SQLiteDataSource,
)
from mfg_agents.v2.contracts.scenario import (
    ScenarioLoadError,
    ScenarioNotFound,
    ScenarioRegistry,
)

SCENARIOS_DIR = "src/mfg_agents/v2/scenarios"
DB_PATH = "data/mfg_agent.db"


class TestScenarioRegistry:
    def test_load_get_list(self):
        registry = ScenarioRegistry(SCENARIOS_DIR)
        loaded = registry.load()
        assert "line_stop_v2" in loaded
        meta = registry.get("line_stop_v2")
        assert meta.name == "停线应急协同"
        assert meta.version == 1
        assert len(meta.steps) == 9
        assert {s["id"] for s in meta.steps} >= {
            "business", "confirm_stop", "fanout", "fanin", "fa",
            "impact_wf", "hitl_restart", "hitl_eta", "report"}

    def test_trigger_words_and_hitl_points(self):
        registry = ScenarioRegistry(SCENARIOS_DIR)
        registry.load()
        meta = registry.get("line_stop_v2")
        assert "停线" in meta.trigger_words()
        assert {h["step"] for h in meta.hitl} == {"hitl_restart", "hitl_eta"}

    def test_match_trigger_words(self):
        registry = ScenarioRegistry(SCENARIOS_DIR)
        registry.load()
        assert registry.match_trigger_words("SMT-1停线了") == "line_stop_v2"
        assert registry.match_trigger_words("今天天气不错") is None

    def test_get_unknown_scenario_raises(self):
        registry = ScenarioRegistry(SCENARIOS_DIR)
        registry.load()
        with pytest.raises(ScenarioNotFound):
            registry.get("not_exist")

    def test_missing_dir_fail_fast(self, tmp_path):
        registry = ScenarioRegistry(tmp_path / "nope")
        with pytest.raises(ScenarioLoadError, match="不存在"):
            registry.load()

    def test_invalid_yaml_fail_fast(self, tmp_path):
        """加载失败必须 fail-fast（启动即报错），不允许静默跳过。"""
        (tmp_path / "bad.yaml").write_text("key: [unclosed", encoding="utf-8")
        registry = ScenarioRegistry(tmp_path)
        with pytest.raises(ScenarioLoadError, match="解析失败"):
            registry.load()

    def test_missing_required_fields_fail_fast(self, tmp_path):
        (tmp_path / "bad.yaml").write_text("name: 缺key的场景\n", encoding="utf-8")
        registry = ScenarioRegistry(tmp_path)
        with pytest.raises(ScenarioLoadError, match="必填字段"):
            registry.load()

    def test_step_without_id_fail_fast(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            "key: x\nname: x\nsteps:\n  - {type: agent, agent: business}\n",
            encoding="utf-8")
        registry = ScenarioRegistry(tmp_path)
        with pytest.raises(ScenarioLoadError, match="id"):
            registry.load()

    def test_duplicate_key_fail_fast(self, tmp_path):
        content = ("key: dup\nname: 场景A\nsteps:\n"
                   "  - {id: a, type: agent, agent: business}\n")
        (tmp_path / "a.yaml").write_text(content, encoding="utf-8")
        (tmp_path / "b.yaml").write_text(content, encoding="utf-8")
        registry = ScenarioRegistry(tmp_path)
        with pytest.raises(ScenarioLoadError, match="冲突"):
            registry.load()


class TestSQLiteDataSource:
    @pytest.fixture()
    def source(self):
        return SQLiteDataSource(DB_PATH, name="dev",
                                queries={"t1": "SELECT 1 AS one"})

    def test_list_tables(self, source):
        tables = source.list_tables()
        assert {"production_output", "line_stop_event", "quality_defect"} <= set(tables)

    def test_run_query_parameterized(self, source):
        rows = source.execute_readonly_sql(
            "SELECT line_code, oee FROM production_output "
            "WHERE line_code = :line AND oee < 50", {"line": "SMT-1"})
        assert len(rows) == 1
        assert rows[0]["line_code"] == "SMT-1"

    def test_run_query_registry(self, source):
        registry = DataSourceRegistry()
        registry.register(source, default=True)
        assert registry.run_query("t1") == [{"one": 1}]
        with pytest.raises(KeyError):
            registry.run_query("missing_query")

    def test_readonly_connection_rejects_write(self, source):
        """只读边界：mode=ro 连接下任何写操作必须失败。"""
        with pytest.raises(sqlite3.OperationalError):
            source.execute_readonly_sql(
                "INSERT INTO inventory (product_model, warehouse, on_hand_qty, "
                "reserved_qty) VALUES ('X', 'Y', 1, 1)")
        with pytest.raises(sqlite3.OperationalError):
            source.execute_readonly_sql("DROP TABLE inventory")

    def test_row_cap_applied(self, source):
        rows = source.execute_readonly_sql(
            "SELECT * FROM production_output", row_cap=2)
        assert len(rows) <= 2

    def test_registry_get_and_list(self, source):
        registry = DataSourceRegistry()
        registry.register(source, default=True)
        assert registry.list() == ["dev"]
        assert registry.get() is source
        assert registry.get("dev") is source
        with pytest.raises(KeyError):
            registry.get("other")
