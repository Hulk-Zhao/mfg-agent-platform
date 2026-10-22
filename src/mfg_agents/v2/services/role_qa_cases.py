"""岗位问答案例（R2 可答性兜底）。

背景
----
``data/colleague.yaml`` 给 31 个岗位配了推荐问题，但推荐问题只是**配置层声明**：
点击后走正常路由（Text2SQL / 场景 DAG / Agentic），模型生成的 SQL 与工具循环
受模型波动影响——同一个问题今天能答、明天可能落「人工兜底」或
「已在最大步数内完成部分取证」。管理岗最常问的「要我拍板的升级事项」在真机就
落过 ``no such column: risk_level``。

本模块是**可答性兜底**（不是主链路）：为每个岗位的每条推荐问题预置一份
经取证的完整答案（结论 + 事实 + 缺口 + 下一步）。运行时先走真实链路，
只有真实链路**未能形成完整结论**时才用案例补齐，并在结果里标明案例编号与
口径来源——不冒充实时结论，也不编造数字。

数据源：``data/role_qa_cases.yaml``（配置即代码，与 colleague.yaml 同目录）。
"""
from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

__all__ = ["RoleQACase", "RoleQACases", "get_role_qa_cases"]

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "data" / "role_qa_cases.yaml"

# 归一化：去空白与中英文标点，避免「这个月准交率完成多少？」与无问号版本不匹配
_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:（）()「」【】\"'“”‘’~～…—-]+")


def normalize_question(text: Any) -> str:
    return _PUNCT.sub("", str(text or ""))


class RoleQACase:
    """一条岗位问答案例（不可变视图）。"""

    __slots__ = ("case_id", "role", "question", "conclusion", "facts", "gaps",
                 "next_steps", "sources", "key")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.case_id = str(raw.get("id") or "")
        self.role = str(raw.get("role") or "")
        self.question = str(raw.get("question") or "")
        self.conclusion = str(raw.get("conclusion") or "").strip()
        self.facts = [str(x) for x in (raw.get("facts") or []) if str(x).strip()]
        self.gaps = [str(x) for x in (raw.get("gaps") or []) if str(x).strip()]
        self.next_steps = [str(x) for x in (raw.get("next_steps") or [])
                           if str(x).strip()]
        self.sources = [str(x) for x in (raw.get("sources") or []) if str(x).strip()]
        self.key = normalize_question(self.question)

    def report(self) -> str:
        """组装完整回复：结论 → 依据 → 缺口 → 下一步（业务语言）。"""
        lines: list[str] = []
        if self.conclusion:
            lines.append(self.conclusion)
        if self.facts:
            lines.append("依据：")
            lines.extend(f"· {f}" for f in self.facts)
        if self.gaps:
            lines.append("数据缺口（如实标注，不编造）：")
            lines.extend(f"· {g}" for g in self.gaps)
        if self.next_steps:
            lines.append("建议下一步：")
            lines.extend(f"· {s}" for s in self.next_steps)
        return "\n".join(lines)

    def to_provenance(self) -> list[dict[str, Any]]:
        """取证链（前端「分析过程与数据来源」直接可读）。"""
        prov: list[dict[str, Any]] = [{
            "channel": "compute",
            "formula": "岗位案例 " + self.case_id + "："
                       + ("；".join(self.sources) or "预置案例口径"),
        }]
        prov.extend({"channel": "data_gap", "note": g} for g in self.gaps)
        return prov


class RoleQACases:
    """案例目录（启动期加载一次；文件缺失/损坏时为空目录，不阻塞问数）。"""

    def __init__(self, path: Path = _PATH) -> None:
        self.path = path
        self._by_key: dict[tuple[str, str], RoleQACase] = {}
        self._load()

    def _load(self) -> None:
        # 案例库是**兜底层**：文件缺失/格式损坏都不得反噬问数主链路，
        # 最差退化为空目录（真实链路照常跑，只是没有兜底）。
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[role_qa] 案例库加载失败，本次运行不启用兜底: %s", exc)
            return
        cases = raw.get("cases") if isinstance(raw, dict) else None
        for item in cases or []:
            if not isinstance(item, dict):
                continue
            case = RoleQACase(item)
            if case.role and case.key and case.conclusion:
                self._by_key[(case.role, case.key)] = case

    def __len__(self) -> int:
        return len(self._by_key)

    def match(self, role: Any, question: Any) -> Optional[RoleQACase]:
        """按（岗位, 问题）精确匹配；岗位为空不匹配（避免跨岗位串答案）。"""
        role_key = str(role or "").strip()
        if not role_key:
            return None
        return self._by_key.get((role_key, normalize_question(question)))

    def roles(self) -> set[str]:
        return {r for r, _ in self._by_key}

    def questions_of(self, role: str) -> list[str]:
        return [c.question for (r, _), c in self._by_key.items() if r == role]


_CACHE: Optional[RoleQACases] = None


def get_role_qa_cases() -> RoleQACases:
    global _CACHE
    if _CACHE is None:
        _CACHE = RoleQACases()
    return _CACHE
