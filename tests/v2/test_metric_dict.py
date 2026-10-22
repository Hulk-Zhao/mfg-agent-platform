"""R8 口径词典服务契约（D12 三源汇总）。

守四件事：
1. 三源（注册查询 / 指标库 / 看板 KPI）**都在**，且计数与各自定义源一致；
2. 每条词条有 id / 口径说明 / 引用方列表，id 唯一；
3. 同名口径**不合并**（来源不同分别成条，避免制造新的「唯一权威定义」）；
4. 引用方来自源码扫描，不是第二份手写映射。
"""
from __future__ import annotations

from pathlib import Path

from mfg_agents.v2.services import metric_dict as md
from mfg_agents.v2.services.dashboard import DRILL_DEFS
from mfg_agents.v2.services.text2sql.libraries import MetricLibrary, QueryLibrary

DATA = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "data"


def test_three_sources_are_present_and_counted():
    bundle = md.build_dictionary()
    by = bundle["coverage"]["by_source"]
    assert by[md.SOURCE_QUERY] == len(QueryLibrary.load(DATA / "queries.yaml").all())
    assert by[md.SOURCE_METRIC] == len(MetricLibrary.load(DATA / "metrics").all())
    assert by[md.SOURCE_BOARD] == len(DRILL_DEFS)
    assert bundle["count"] == sum(by.values())
    assert bundle["coverage"]["gaps"] == []
    assert "三源汇总" in bundle["coverage"]["note"]


def test_every_entry_has_required_fields_and_unique_id():
    entries = md.build_dictionary()["entries"]
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    for entry in entries:
        assert entry["id"] and entry["name"]
        assert entry["source"] in md.SOURCE_LABELS
        assert entry["source_label"] == md.SOURCE_LABELS[entry["source"]]
        assert isinstance(entry["definition"], str)
        assert isinstance(entry["refs"], list) and entry["refs"]


def test_registered_query_entries_cover_yaml():
    entries = {e["id"]: e for e in md.build_dictionary()["entries"]}
    for name in QueryLibrary.load(DATA / "queries.yaml").all():
        entry = entries[f"query.{name}"]
        assert entry["source"] == md.SOURCE_QUERY
        assert entry["formula"]  # 注册查询的公式即其 SQL


def test_dashboard_entries_cover_drill_defs():
    entries = {e["id"]: e for e in md.build_dictionary()["entries"]}
    for key, defn in DRILL_DEFS.items():
        entry_id = f"board.{defn['screen']}.{key}"
        assert entry_id in entries, entry_id
        assert entries[entry_id]["definition"] == defn["definition"]
        assert entries[entry_id]["target"] == defn["target"]


def test_same_name_is_not_merged_across_sources():
    entries = md.build_dictionary()["entries"]
    incoming = [e for e in entries if e["name"] == "来料批次合格率"]
    assert len(incoming) >= 2, "看板同名口径必须分别成条"
    assert len({e["id"] for e in incoming}) == len(incoming)


def test_search_get_and_resolve():
    hits = md.search_dictionary("OTD")
    ids = {e["id"] for e in hits}
    assert "metric.otd_rate" in ids
    assert "board.orders.otd" in ids

    assert md.get_entry("query.batch_trace_full")["source"] == md.SOURCE_QUERY
    assert md.resolve_entry("otd_rate")["id"] == "metric.otd_rate"
    assert md.resolve_entry("batch_trace_full")["id"] == "query.batch_trace_full"
    assert md.resolve_entry("no_such_metric_or_query") is None


def test_refs_come_from_source_scan():
    refs = md.get_entry("query.batch_trace_full")["refs"]
    assert any("quality_trace_report.py" in ref for ref in refs), refs
    # 词典自身不是引用方（避免自证）
    assert not any("services/metric_dict.py" in ref for ref in refs)
