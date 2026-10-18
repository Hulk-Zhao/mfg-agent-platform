"""CaseAccessAgent：把访问申请**路由**到职能部门并留痕。

铁律：**Agent 不得自动批准**。本 Agent 只做三件事：

1. 从案例的 ``category / defect_mode_code / root_cause_code / owner_dept`` 推断
   职能部门与建议理由模板；
2. 写入推送留痕（``case_audit`` 的 detail 只记 ``pushed_to`` 与理由摘要，
   **绝不记案例正文**）；
3. 返回路由结果，供前端显示"已推送给 XX 部门，等待审批"。

推断不出来就说"无法推断"，**不编造**——路由错了会让申请在错误的部门里躺三天，
这比"没推"更糟。
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from ..contracts.ontology import get_ontology_registry
from ..services.case_audit import CaseAuditLog
from ..services.case_store import CaseStore

logger = logging.getLogger(__name__)

__all__ = ["CaseAccessAgent", "DeptRoute"]

#: 缺陷模式前缀 → 职能部门（按受控词表的 code 前缀归类）。
_DEFECT_PREFIX_DEPT: dict[str, str] = {
    "DM-SMT": "quality",       # SMT 过程类 → 品质部（制程/焊接）
    "DM-MECH": "equipment",    # 机加/模具 → 设备工程部
    "DM-MAT": "quality",       # 材料 → 品质部
    "DM-TEST": "quality",
    "RC-EQP": "equipment",     # 岗位经验·设备域
    "RC-QA": "quality",
}
#: 案例分类 → 兜底职能部门。
_CATEGORY_DEPT: dict[str, str] = {
    "quality_case": "quality",
    "role_case": "quality",
}
#: 职能部门中文名（前端直接展示；英文 key 只做技术详情）。
DEPT_LABEL: dict[str, str] = {
    "quality": "品质部",
    "equipment": "设备工程部",
    "production": "制造部",
    "management": "总经理室",
}

#: 建议理由模板（按分类；不含案例正文）。
_REASON_TEMPLATE: dict[str, str] = {
    "quality_case": "申请引用该案例用于本岗位的异常分析与工艺改善",
    "role_case": "申请参考该职能经验，用于本岗位同类问题的处置",
}


class DeptRoute(dict):
    """路由结果（dict 子类，便于直接进响应）。"""


class CaseAccessAgent:
    """访问申请的路由 Agent（**只路由，不审批**）。"""

    name = "case_access_agent"

    def __init__(self, store: CaseStore, audit: CaseAuditLog) -> None:
        self._store = store
        self._audit = audit
        self._ontology = get_ontology_registry()

    # ---------- 路由 ----------

    def push(self, request_id: str) -> DeptRoute:
        """把申请推给职能部门（推断部门 → 记留痕 → 返回路由结果）。"""
        row = self._fetch(request_id)
        if row is None:
            return DeptRoute(ok=False, pushed_to="", reason="申请不存在")

        case = self._store.get_case(str(row["case_id"]))
        if case is None:
            return DeptRoute(ok=False, pushed_to="", reason="案例不存在，无法路由")

        dept, basis = self._infer_dept(case)
        if not dept:
            self._audit.append(
                actor="agent", action="case.access.push.denied",
                case_id=str(row["case_id"]),
                detail={"request_id": request_id, "reason": "无法推断职能部门"})
            return DeptRoute(
                ok=False, pushed_to="", basis=basis,
                reason="无法推断职能部门，请人工指定受理部门")

        suggested = _REASON_TEMPLATE.get(
            str(case.get("category") or ""),
            "申请引用该案例用于本岗位的异常处理")
        self._audit.append(
            actor="agent", action="case.access.pushed", case_id=str(row["case_id"]),
            detail={"request_id": request_id, "pushed_to": dept,
                    "suggested_reason": suggested, "basis": basis})
        logger.info("[case_access_agent] 申请 %s 路由至 %s（依据：%s）",
                    request_id, dept, basis)
        return DeptRoute(ok=True, pushed_to=dept, pushed_to_label=DEPT_LABEL.get(dept, dept),
                         basis=basis, suggested_reason=suggested,
                         reason=f"已推送给{DEPT_LABEL.get(dept, dept)}，等待审批")

    # ---------- 推断 ----------

    def _infer_dept(self, case: Mapping[str, Any]) -> tuple[str, str]:
        """推断职能部门，返回 ``(dept, 依据)``；推断不出返回 ``("", 依据)``。"""
        owner = str(case.get("owner_dept") or "").strip()
        if owner:
            return owner, f"案例归属部门 owner_dept={owner}"
        defect = str(case.get("defect_mode_code") or "").strip()
        for prefix, dept in _DEFECT_PREFIX_DEPT.items():
            if defect.startswith(prefix):
                return dept, f"缺陷模式 {defect} 属 {prefix} 序列"
        category = str(case.get("category") or "").strip()
        if category in _CATEGORY_DEPT:
            return _CATEGORY_DEPT[category], f"案例分类 {category} 默认受理部门"
        return "", "缺少 owner_dept / defect_mode_code / category，无法推断"

    def _fetch(self, request_id: str) -> dict[str, Any] | None:
        with self._store._conn() as conn:   # noqa: SLF001 - 只读
            row = conn.execute(
                "SELECT * FROM case_access_request WHERE request_id=?",
                (str(request_id),)).fetchone()
        return dict(row) if row else None