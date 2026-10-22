"""Text2SQL 方言适配：MySQL 提示词纪律 + 数据源方言透传。"""
from __future__ import annotations

from types import SimpleNamespace

from mfg_agents.v2.contracts.data_source import MySQLDataSource, SQLiteDataSource
from mfg_agents.v2.prompts.text2sql import build_detail_prompt
from mfg_agents.v2.services.text2sql import Text2SQLService


def test_default_prompt_keeps_sqlite_example():
    prompt = build_detail_prompt("最近7天 OTD 最低的 5 个型号")
    assert "date('now')" in prompt
    assert "目标数据库：MySQL 8" not in prompt


def test_mysql_prompt_appends_dialect_rules_after_example():
    prompt = build_detail_prompt("最近7天 OTD 最低的 5 个型号", dialect="mysql")
    for token in ("目标数据库：MySQL 8", "CONCAT", "CURDATE()",
                  "禁止 strftime", "反引号", "LIMIT n OFFSET m"):
        assert token in prompt
    # 方言规则必须在 SQLite 示例之后（覆盖而非被示例带偏）
    assert prompt.index("目标数据库：MySQL 8") > prompt.index("date('now')")


def test_data_sources_declare_dialect():
    assert SQLiteDataSource(":memory:").dialect == "sqlite"
    mysql = MySQLDataSource("mysql+pymysql://ro:pw@127.0.0.1:3306/x")
    try:
        assert mysql.dialect == "mysql"
    finally:
        mysql.close()


class _FakeMetrics:
    def match(self, question):
        return None


class _FakeQueries:
    def match(self, question, params):
        return None


class _FakeSource:
    dialect = "mysql"

    def execute_readonly_sql(self, sql, params=None, row_cap=200):
        return []


class _FakeRegistry:
    def get(self, name=None):
        return _FakeSource()

    def execute_readonly_sql(self, sql, params=None, row_cap=200):
        return []


def test_service_passes_source_dialect_to_prompt(monkeypatch):
    captured: list[str] = []

    class _FakeLLM:
        def invoke(self, prompt):
            captured.append(str(prompt))
            return SimpleNamespace(content="SELECT 1")

    import mfg_agents.common.llm_factory as lf

    monkeypatch.setattr(lf, "llm_available", lambda: True)
    monkeypatch.setattr(lf, "get_llm", lambda **kwargs: _FakeLLM())

    service = Text2SQLService(_FakeRegistry(), _FakeMetrics(), _FakeQueries())
    service.query_detail("任意问题")

    assert captured, "LLM 未被调用"
    assert "目标数据库：MySQL 8" in captured[0]
