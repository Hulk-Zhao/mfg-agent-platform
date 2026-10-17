"""prompt 迁移金标准测试（护 prompts 集中化不变量，§6.3）。

逐字节断言关键 prompt 字符串与迁移前**完全相等**，并校验
``node_registry`` 对外符号（``FORBIDDEN_WORDS`` 等）仍等于 ``prompts`` 的再导出。
"""
from mfg_agents.v2.core import node_registry
from mfg_agents.v2.prompts import (
    DOMAIN_GROUPS,
    build_detail_prompt,
    build_fa_prompt,
    build_l1_prompt,
    build_report_prompt,
)
from mfg_agents.v2.prompts import report as prompts_report
from mfg_agents.v2.prompts import routing as prompts_routing
from mfg_agents.v2.core.shared_context import SharedContext


def test_domain_groups_bytes():
    assert DOMAIN_GROUPS == {
        "supply_chain": "供应链域：订单/计划/物控/采购/库存/发货/交期/ATP",
        "rd_engineering": "研发工程域：BOM/ECN/NPI/图纸/工艺/测试",
        "quality": "质量域：来料检验IQC/不良分布/客诉/8D/SPC/CPK",
        "manufacturing": "制造运营域：停线/OEE/产出/设备告警/工艺参数/复线",
        "function": "职能支撑域：行政/财务/审批/开票/费用",
        "out_of_scope": "闲聊或超出平台范围",
    }


def test_build_l1_prompt_bytes():
    expected = (
        "你是制造业多智能体平台的意图分类器。将用户问题分类到以下六个类别之一"
        "（闭集，禁止输出其他值）：\n"
        "- supply_chain: 供应链域：订单/计划/物控/采购/库存/发货/交期/ATP\n"
        "- rd_engineering: 研发工程域：BOM/ECN/NPI/图纸/工艺/测试\n"
        "- quality: 质量域：来料检验IQC/不良分布/客诉/8D/SPC/CPK\n"
        "- manufacturing: 制造运营域：停线/OEE/产出/设备告警/工艺参数/复线\n"
        "- function: 职能支撑域：行政/财务/审批/开票/费用\n"
        "- out_of_scope: 闲聊或超出平台范围\n"
        "\n"
        "用户问题：测试问题\n"
        '只输出 JSON：{"domain":"<类别>","confidence":<0~1>}'
    )
    assert build_l1_prompt("测试问题") == expected
    assert prompts_routing.format_groups() == expected.split("\n\n")[0].split("：\n", 1)[1]


def test_build_report_prompt_bytes():
    expected = (
        "你是制造业业务接口人。基于以下内部数据，生成给客户的正式回复（200字以内）。\n\n"
        "【内部数据】\n{'停线确认': True}\n\n"
        "【规则】\n1. 禁止出现：设备故障、供应商来料不良、品质不良超标、停线、根因、维修\n"
        "2. 统一替换为：产线例行校准、批次质量复核、工艺参数优化、计划内暂停\n"
        "3. 必须包含：当前状态、预计恢复时间（基于ETA换算）、补救措施、发货影响\n"
        "4. 语气专业诚恳，不推诿\n5. 只输出报告正文，不要标题"
    )
    assert build_report_prompt({"停线确认": True}) == expected


def test_build_fa_prompt_bytes():
    expected = (
        "你是制造业Failure Analysis专家。基于以下信息判定异常等级并预估复线时间。\n\n"
        "【当前异常】\n- 线体：SMT-1\n"
        "- 主导不良：锡膏偏移\n"
        "- 设备异常：['刮刀压力=0.8MPa']\n\n"
        "【历史相似案例】\n- case1\n\n"
        '请输出（JSON格式）：{"rating":"A/B/C/D","root_cause":"一句话",'
        '"eta_hours":2,"verification":["..."]}\n只输出JSON。'
    )
    actual = build_fa_prompt(
        "SMT-1", "锡膏偏移",
        [{"param_name": "刮刀压力", "param_value": "0.8MPa"}], "- case1")
    assert actual == expected


def test_apply_forbidden_words_replaces():
    assert prompts_report._apply_forbidden_words(
        "本次停线，设备故障与供应商来料不良") == "本次计划内暂停，产线例行校准与批次质量复核"


def test_fa_template_rule_based():
    rating, eta, root, verif = prompts_report._fa_template(
        {"total_qty": 77, "defect_type": "锡膏偏移"},
        [{"param_name": "刮刀压力", "param_value": "0.8MPa"}])
    assert rating == "B"
    assert eta == 2
    assert "刮刀压力=0.8MPa" in root and "锡膏偏移" in root
    assert len(verif) == 3


def test_customer_report_template_no_stop():
    ctx = SharedContext()
    assert "运行正常" in prompts_report._customer_report_template(
        {"停线确认": False}, ctx)


def test_customer_report_template_stop_uses_eta():
    ctx = SharedContext()
    ctx.merge("fa", {"eta_hours": 4}, source="test")
    text = prompts_report._customer_report_template({"停线确认": True}, ctx)
    assert "4 小时" in text


def test_build_detail_prompt_reuses_schema_prompt():
    from mfg_agents.tools.sql_tools import SCHEMA_PROMPT
    assert build_detail_prompt("库存有多少") == f"{SCHEMA_PROMPT}\n\n问题：库存有多少"


def test_node_registry_forbidden_words_is_prompts_reexport():
    """对外符号不变：node_registry.FORBIDDEN_WORDS == prompts.report.FORBIDDEN_WORDS。"""
    assert node_registry.FORBIDDEN_WORDS == prompts_report.FORBIDDEN_WORDS
    assert node_registry.FORBIDDEN_WORDS == {
        "设备故障": "产线例行校准",
        "供应商来料不良": "批次质量复核",
        "品质不良超标": "工艺参数优化",
        "停线": "计划内暂停",
        "根因": "主因分析",
        "维修": "维护校准",
    }
    # node_registry 仍暴露模板函数（迁移后由 prompts 再导出）
    assert node_registry._apply_forbidden_words is prompts_report._apply_forbidden_words
    assert node_registry._fa_template is prompts_report._fa_template
    assert node_registry._customer_report_template is prompts_report._customer_report_template
