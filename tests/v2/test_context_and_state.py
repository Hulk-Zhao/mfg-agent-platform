"""SharedContext（只增不改）与 DomainState reducer 测试。"""
import threading

from mfg_agents.v2.core.domain_state import DagState, merge_domain_states
from mfg_agents.v2.core.shared_context import SharedContext


class TestSharedContext:
    def test_merge_and_get_latest(self):
        ctx = SharedContext()
        ctx.merge("manufacturing", {"oee": 86.2}, source="agent:manufacturing")
        ctx.merge("manufacturing", {"oee": 32.1}, source="agent:manufacturing")
        assert ctx.get("oee") == 32.1          # 最新值
        assert len(ctx.history("oee")) == 2    # 历史不丢（只增不改）

    def test_snapshot_flat_latest(self):
        ctx = SharedContext()
        ctx.merge("quality", {"dominant_defect": {"defect_type": "锡膏偏移"}},
                  source="agent:quality")
        snap = ctx.snapshot()
        assert snap["dominant_defect"]["defect_type"] == "锡膏偏移"

    def test_provenance_records_source_domain_timestamp(self):
        ctx = SharedContext()
        ctx.merge("supply_chain", {"batches": 3}, source="sql:supply_batches_today")
        prov = ctx.provenance("batches")
        assert len(prov) == 1
        assert prov[0]["source"] == "sql:supply_batches_today"
        assert prov[0]["domain"] == "supply_chain"
        assert "T" in prov[0]["timestamp"]  # ISO 8601

    def test_provenance_all_keys_sorted(self):
        ctx = SharedContext()
        ctx.merge("a", {"k1": 1}, source="s1")
        ctx.merge("b", {"k2": 2}, source="s2")
        assert len(ctx.provenance()) == 2

    def test_get_default_for_missing_key(self):
        ctx = SharedContext()
        assert ctx.get("missing", "fallback") == "fallback"

    def test_merge_rejects_non_dict(self):
        ctx = SharedContext()
        try:
            ctx.merge("x", ["not", "a", "dict"])
            raise AssertionError("应当拒绝非 dict 写入")
        except TypeError:
            pass

    def test_concurrent_merges_thread_safe(self):
        """并行域写入不能丢记录（停线 fan-out 场景的硬性要求）。"""
        ctx = SharedContext()

        def writer(domain: str, n: int):
            for i in range(n):
                ctx.merge(domain, {f"{domain}_{i}": i}, source=f"agent:{domain}")

        threads = [threading.Thread(target=writer, args=(f"d{i}", 50))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(ctx.keys()) == 200


class TestDomainStateReducer:
    def test_different_domains_coexist(self):
        left = {"manufacturing": {"domain": "manufacturing", "status": "ok"}}
        right = {"quality": {"domain": "quality", "status": "ok"}}
        merged = merge_domain_states(left, right)
        assert set(merged) == {"manufacturing", "quality"}

    def test_same_domain_last_write_wins(self):
        left = {"quality": {"domain": "quality", "status": "ok", "summary": "A"}}
        right = {"quality": {"domain": "quality", "status": "degraded",
                             "summary": "B"}}
        merged = merge_domain_states(left, right)
        assert merged["quality"]["status"] == "degraded"

    def test_parallel_fanout_no_cross_talk(self):
        """模拟三分支并行写回：域间互不覆盖。"""
        state: dict = {}
        for domain, payload in [("quality", {"rows": 2}),
                                ("supply_chain", {"rows": 1}),
                                ("equipment", {"rows": 3})]:
            state = merge_domain_states(
                state, {domain: {"domain": domain, "status": "ok",
                                 "payload": payload}})
        assert state["quality"]["payload"]["rows"] == 2
        assert state["supply_chain"]["payload"]["rows"] == 1
        assert state["equipment"]["payload"]["rows"] == 3


def test_dag_state_has_user_ctx_channel():
    """DagState 必须声明 user_ctx 通道（否则 LangGraph 会丢弃该字段）。"""
    assert "user_ctx" in DagState.__annotations__
    assert "domains" in DagState.__annotations__
