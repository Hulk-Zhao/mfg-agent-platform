"""TwoLevelRouter 测试：L0 快路径 / LLM 不可用降级 / 超范围兜底。"""
import pytest

from mfg_agents.v2.contracts.scenario import ScenarioRegistry
from mfg_agents.v2.core.router import TwoLevelRouter
from mfg_agents.tools.llm_factory import reset_probe_cache

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
