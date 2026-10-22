"""R7 质量追溯三路并查成稿（服务层契约）。

守四件事：
1. **三路串行**：生产记录 / 来料检验 / 设备状态 + 同批在制工单，数字全部来自查询；
2. **缺口不编造**：单路失败记 data_gap 并继续，批次主记录全空才判 no_data；
3. **空结果 ≠ 正常**：无不良记录与无设备日志必须分别如实陈述；
4. **成稿可复核**：正文含批次档案、三路事实、影响面、缺口与人工确认提示。
"""
from __future__ import annotations

from mfg_agents.v2.services.quality_trace_report import (
    VERDICT_NO_DATA,
    VERDICT_PARTIAL,
    VERDICT_TRACED,
    compose_quality_trace,
)

_LOCK = {
    "batch_no": "BATCH-A-1008", "part_no": "PCB-022", "part_name": "A型号PCB",
    "supplier": "深南电路", "inspect_status": "pass", "warehouse": "电子料仓",
    "bin_location": "A-03-02", "qty": 1000, "qualified_qty": 980,
}
_DEFECT = [{
    "batch_no": "BATCH-A-1008", "product_model": "A型号", "line_code": "SMT-2",
    "process_stage": "SMT", "defect_type": "贴片偏移", "defected_qty": 46,
}]
_EQUIP = [
    {"equipment_id": "SMT-2-01", "line_code": "SMT-2", "log_time": "2026-10-15 09:12:00",
     "alarm_code": "ALM-TEMP", "param_name": "回流焊温度", "param_value": "268",
     "is_abnormal": 1},
    {"equipment_id": "SMT-2-01", "line_code": "SMT-2", "log_time": "2026-10-15 09:20:00",
     "alarm_code": "", "param_name": "贴装压力", "param_value": "3.2",
     "is_abnormal": 0},
]
_WIP = [{"wo_no": "WO-2026-0451", "product_model": "A型号", "line_code": "SMT-2",
         "process_step": "SMT-贴片"}]


def _run_factory(overrides: dict | None = None):
    rows = {
        "batch_trace_full": [_LOCK],
        "incoming_batch_by_no": [_LOCK],
        "quality_defect_scope_by_batch": _DEFECT,
        "equipment_log_by_batch": _EQUIP,
        "inprocess_wo_by_batch": _WIP,
    }
    rows.update(overrides or {})

    def _run(name: str, params: dict):
        value = rows.get(name, [])
        if isinstance(value, Exception):
            raise value
        return value
    return _run


def test_full_three_source_trace():
    rep = compose_quality_trace("BATCH-A-1008", _run_factory())
    assert rep.verdict == VERDICT_TRACED
    assert rep.locked["supplier"] == "深南电路"
    assert rep.locked["line_code"] == "SMT-2"
    assert rep.abnormal_count == 1
    assert len(rep.wip_orders) == 1
    text = rep.report
    assert "BATCH-A-1008" in text and "深南电路" in text
    assert "贴片偏移 46 件" in text
    assert "到货 1000 件，合格 980 件" in text
    assert "异常 1 条" in text and "ALM-TEMP" in text
    assert "在制工单 1 张" in text and "WO-2026-0451" in text
    assert "人工确认" in text
    # 溯源：四查都留有记录（含行数），可审计
    assert len(rep.provenance) == 5
    assert all("query" in p for p in rep.provenance)


def test_single_source_failure_records_gap_and_continues():
    rep = compose_quality_trace(
        "BATCH-A-1008",
        _run_factory({"equipment_log_by_batch": RuntimeError("审核拦截: 表级权限不足")}))
    assert rep.verdict == VERDICT_PARTIAL
    assert any("设备状态查询失败" in g for g in rep.data_gaps)
    # 其余两路照常成稿（没有因一路失败而整体放弃）
    assert "贴片偏移 46 件" in rep.report
    assert "到货 1000 件" in rep.report
    assert "设备状态：查询失败（数据缺口，未推定无异常）" in rep.report


def test_no_batch_record_is_no_data_not_fake_normal():
    rep = compose_quality_trace(
        "BATCH-NOT-EXIST",
        _run_factory({"batch_trace_full": [], "incoming_batch_by_no": [],
                      "quality_defect_scope_by_batch": []}))
    assert rep.verdict == VERDICT_NO_DATA
    assert "批次主记录不存在" in "；".join(rep.data_gaps)
    assert "无法追溯" in rep.report


def test_empty_defects_is_gap_not_normality():
    rep = compose_quality_trace(
        "BATCH-A-1008",
        _run_factory({"quality_defect_scope_by_batch": []}))
    assert "无该批次的生产不良记录" in "；".join(rep.data_gaps)
    assert "无该批次不良记录（数据缺口，未推定正常）" in rep.report


def test_missing_batch_no():
    rep = compose_quality_trace("   ", _run_factory())
    assert rep.verdict == VERDICT_NO_DATA
    assert rep.data_gaps == ["未提供批次号"]
    assert "未提供批次号" in rep.report


def test_incoming_only_still_locks_batch():
    """生产记录缺失但来料记录在：批次仍可锁定，缺口如实标注。"""
    rep = compose_quality_trace(
        "BATCH-A-1008",
        _run_factory({"batch_trace_full": [],
                      "quality_defect_scope_by_batch": []}))
    assert rep.locked["supplier"] == "深南电路"
    assert rep.verdict == VERDICT_PARTIAL
    assert "无该批次的生产不良记录" in "；".join(rep.data_gaps)
