"""TwoLevelRouter 测试：L0 快路径 / LLM 不可用降级 / 超范围兜底。"""
import pytest

from mfg_agents.v2.contracts.scenario import ScenarioRegistry
from mfg_agents.v2.core.router import TwoLevelRouter
from mfg_agents.common.llm_factory import reset_probe_cache

SCENARIOS_DIR = "src/mfg_agents/v2/scenarios"


@pytest.fixture()
def router() -> TwoLevelRouter:
    registry = ScenarioRegistry(SCENARIOS_DIR)
    registry.load()
    return TwoLevelRouter(registry)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """测试默认 LLM 不可用（离线 CI 环境行为）。"""
    reset_probe_cache()
    monkeypatch.setattr("mfg_agents.v2.core.router.llm_available", lambda: False)


def test_l0_hit_by_scenario_trigger_words(router):
    """L0：场景触发词命中，毫秒级直接返回，不低置信。"""
    result = router.route("A型号在SMT-1停线，何时复线？")
    assert result.level == "L0"
    assert result.scenario_key == "line_stop_v2"
    assert result.confidence == 1.0
    assert result.low_confidence is False
    assert result.entities["line_code"] == "SMT-1"
    assert result.entities["product_model"] == "A型号"


def test_l0_hit_by_v1_scene_rules(router):
    """L0：V1 SCENE_RULES 关键词命中（S3 已迁移 → scenario=customer_complaint_v2）。

    说明（§8.3 唯一预期内断言翻转）：原断言 ``scenario_key is None`` 编码的是
    "场景**尚未迁移**"的过渡态；本次迁移客户投诉场景后，该问题（含"投诉/华为/
    不良率"触发词）应按设计命中 ``customer_complaint_v2``。level/domain 保持不变。
    """
    result = router.route("华为投诉BATCH-A-1008，不良率超标")
    assert result.level == "L0"
    assert result.scenario_key == "customer_complaint_v2"
    assert result.domain == "quality"


def test_degraded_fuzzy_match_when_llm_unavailable(router):
    """降级：LLM 不可用 + L0 未命中时，域组关键词模糊匹配并标记低置信。

    V3.1 注：原用例"排产计划和物料齐套情况"在触发词扩充（齐套/缺料加入
    delivery_status_v2）后已被 L0 快路径直达——那是行为改善而非回归。
    本用例改用不含任何场景触发词的供应链问法，继续锁定降级机制本身。
    """
    result = router.route("帮我看看物料准备方面还有什么安排")
    assert result.level == "degrade"
    assert result.low_confidence is True
    assert result.domain == "supply_chain"


def test_degraded_out_of_scope(router):
    """降级：无任何关键词命中 → 超出平台范围（人工兜底）。"""
    result = router.route("今天天气怎么样")
    assert result.scenario_key is None
    assert result.low_confidence is True


def test_route_result_serializable(router):
    result = router.route("A型号在SMT-1停线了")
    data = result.to_dict()
    assert data["level"] == "L0"
    assert isinstance(data["entities"], dict)


def test_entities_extracted_for_degraded_path(router):
    """降级路径同样抽取实体（复用 V1 business_agent 规则）。"""
    result = router.route("DIP-2线的B型号产出怎么样")
    assert result.entities["line_code"] == "DIP-2"
    assert result.entities["product_model"] == "B型号"


# ---------------------------------------------------------------- R10 大单进度


def test_business_big_order_progress(router):
    """「5000 件大单做到哪了」必须回到单张单的进度，不能被 open digest 吞掉。"""
    result = router.route("5000 件大单做到哪了")
    assert result.strategy == "business"
    assert result.domain == "orders"
    assert result.entities["min_qty"] == 5000
    assert result.entities["order_no"] == ""


def test_business_extracts_order_no_and_model(router):
    result = router.route("大单进度 SO-2026-15331 做到哪了")
    assert result.strategy == "business"
    assert result.entities["order_no"] == "SO-2026-15331"
    model = router.route("A型号的大单进度如何")
    assert model.strategy == "business"
    assert model.entities["model"] == "A型号"


def test_business_does_not_swallow_open_digest(router):
    """「经营情况 / 整体情况」仍是开放式汇总，不是对象级大单进度。"""
    assert router.route("本月经营情况怎么样").strategy == "digest"
    # 只提单号、没有「大单/做到哪」→ 不判 business（留给对象级数据链路）
    assert router.route("SO-2026-15331 的进度").strategy != "business"


# ---------------------------------------------------------------- R11 呆滞料


def test_aging_route_defaults_to_180_days(router):
    result = router.route("超 180 天没动的料有哪些")
    assert result.strategy == "aging"
    assert result.entities["days"] == 180


def test_aging_extracts_custom_days(router):
    assert router.route("库龄超过 90 天的物料").strategy == "aging"
    assert router.route("库龄超过 90 天的物料").entities["days"] == 90
    assert router.route("呆滞料处置建议").entities["days"] == 180


def test_aging_does_not_swallow_general_inventory(router):
    assert router.route("材料库存还有多少").strategy != "aging"


# ---------------------------------------------------------------- R7：批次追溯快路径


def test_quality_trace_fast_path_preempts_scenarios(router):
    """批次号 + 追溯词 → 质量域 agentic（不让场景/库存快路径抢走）。
    R7 真机根因：批次号被忽略时会答成另一批次（MAT-SP-1015）。"""
    result = router.route("BATCH-A-1008 这个批次的完整来龙去脉帮我串一下")
    assert result.strategy == "trace"
    assert result.domain == "quality"
    assert result.entities["batch_no"] == "BATCH-A-1008"
    assert result.confidence >= 0.9


def test_quality_trace_fast_path_requires_both_signals(router):
    """只有追溯词 / 只有批次号都不触发（防误吞普通问数）。"""
    no_batch = router.route("帮我把这批的不良追溯一下")
    assert no_batch.strategy != "trace"

    no_trace = router.route("BATCH-A-1008 这个批次怎么样")
    assert no_trace.strategy != "trace"


def test_quality_trace_fast_path_accepts_mat_batch(router):
    result = router.route("MAT-SP-1015 追溯一下")
    assert result.strategy == "trace"
    assert result.entities["batch_no"] == "MAT-SP-1015"
