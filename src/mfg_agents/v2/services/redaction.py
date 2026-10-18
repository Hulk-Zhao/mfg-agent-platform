"""RedactionService：规则脱敏（**入库前固化**）+ 漏脱复检。

架构位置：§7.3（管道顺序）、§8.2 步 3-5、PRD BR-03。

三条纪律（不可妥协）：

1. **脱敏在入库前固化**，向量库里只有脱敏后文本——不是"索引原文、查询时打码"。
   查询时打码意味着原文仍在索引、缓存、日志里，等于没脱。
2. **漏脱校验失败 = 拒绝入库**，不降级放行（:func:`verify_no_leak` 重跑全部识别器）。
3. **命中明细只留在内存**（``hits``）；落库/落审计只能记 ``kind`` 与计数，
   **不得记录被命中的原始值**——否则脱敏表本身成了新的泄漏面。

规则来源：``v2/data/redaction_rules.yaml``（客户/供应商/人名/手机号/订单号/图号）。
模型通道（NER）是 P0 生产项，本 demo 只做规则通道，接入点见
``RedactionService(extra_detectors=[...])``。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "RedactionError",
    "RedactionRule",
    "RedactionHit",
    "RedactionResult",
    "RedactionService",
    "DEFAULT_REDACTION_RULES_PATH",
    "get_redaction_service",
    "reset_redaction_service",
]

#: ``v2/data/redaction_rules.yaml``（本模块在 v2/services/ 下 → ../data/）
DEFAULT_REDACTION_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "redaction_rules.yaml"
)


class RedactionError(Exception):
    """脱敏配置错误（规则缺失/正则非法 → 启动即失败，不静默跳过）。"""


@dataclass(frozen=True)
class RedactionRule:
    """一条脱敏规则。

    Attributes:
        id: 规则 id（审计只记它）。
        kind: ``customer`` / ``supplier`` / ``person`` / ``phone`` / ``order_no`` /
            ``drawing_no`` 等（审计只记它）。
        type: ``literal`` 或 ``regex``。
        replacement: 替换文本（占位符）。
        values: literal 规则的值列表。
        pattern: regex 规则的正则源串。
    """

    id: str
    kind: str
    type: str = "regex"
    replacement: str = ""
    values: tuple[str, ...] = ()
    pattern: str = ""


@dataclass(frozen=True)
class RedactionHit:
    """一次命中（**内存态**，含原始值；禁止落库/落日志）。"""

    rule_id: str
    kind: str
    matched: str
    replacement: str
    count: int = 1


@dataclass(frozen=True)
class RedactionResult:
    """脱敏结果。

    Attributes:
        text: 脱敏后文本（入库用）。
        hits: 命中的规则明细（内存态）。
    """

    text: str
    hits: tuple[RedactionHit, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.hits)

    def kinds(self) -> tuple[str, ...]:
        """命中的敏感类别（**可落审计**：只有类别，没有原值）。"""
        seen: list[str] = []
        for hit in self.hits:
            if hit.kind not in seen:
                seen.append(hit.kind)
        return tuple(seen)


class RedactionService:
    """规则通道脱敏 + 漏脱复检。"""

    def __init__(self, rules: Sequence[RedactionRule],
                 extra_detectors: Optional[Sequence[
                     Callable[[str], Sequence[RedactionHit]]]] = None) -> None:
        if not rules:
            raise RedactionError("脱敏规则为空：无规则的脱敏等于没脱敏")
        self._rules: tuple[RedactionRule, ...] = tuple(rules)
        self._compiled: dict[str, re.Pattern[str]] = {}
        for rule in self._rules:
            if rule.type == "regex":
                try:
                    self._compiled[rule.id] = re.compile(rule.pattern)
                except re.error as exc:
                    raise RedactionError(
                        f"脱敏规则 {rule.id} 的正则非法: {rule.pattern!r}（{exc}）") from exc
            elif rule.type == "literal":
                if not rule.values:
                    raise RedactionError(f"脱敏规则 {rule.id} 是 literal 但 values 为空")
            else:
                raise RedactionError(
                    f"脱敏规则 {rule.id} 的 type 非法: {rule.type!r}")
        # 模型通道接入点（生产注入 NER；demo 为空）
        self._extra_detectors = tuple(extra_detectors or ())

    # ---------- 加载 ----------

    @classmethod
    def from_yaml(cls, path: Optional[str | Path] = None) -> "RedactionService":
        import yaml  # pyyaml 既有依赖

        target = Path(path) if path else DEFAULT_REDACTION_RULES_PATH
        if not target.is_file():
            raise RedactionError(f"脱敏规则文件不存在: {target}")
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise RedactionError(f"脱敏规则解析失败: {target}（{exc}）") from exc
        if not isinstance(raw, dict):
            raise RedactionError(f"脱敏规则格式非法（应为 mapping）: {target}")
        rules: list[RedactionRule] = []
        for item in (raw.get("rules") or []):
            if not isinstance(item, dict):
                raise RedactionError(f"脱敏规则条目格式非法: {item!r}")
            rule_id = str(item.get("id", "")).strip()
            if not rule_id:
                raise RedactionError("脱敏规则条目缺少 id")
            values = item.get("values") or []
            if isinstance(values, str):
                values = [values]
            rules.append(RedactionRule(
                id=rule_id,
                kind=str(item.get("kind", "unknown")).strip() or "unknown",
                type=str(item.get("type", "regex")).strip().lower(),
                replacement=str(item.get("replacement", "")),
                values=tuple(str(v) for v in values if str(v)),
                pattern=str(item.get("pattern", "")),
            ))
        # denylist 与环境变量补充词表**排在 YAML 词表之前**：它们是最具体的
        # 显式声明，必须先命中，否则会被更宽的正则/短词抢先替换（张三吞掉张三丰）。
        rules = cls._denylist_rules(raw) + rules + cls._env_extra_rules()
        service = cls(rules)
        logger.info("[redaction] 脱敏规则加载完成: %s（%d 条）", target, len(rules))
        return service

    # ---------- 补充黑名单 ----------

    @staticmethod
    def _denylist_rules(raw: Mapping[str, Any]) -> list["RedactionRule"]:
        """``denylist`` 段 → literal 规则（**排在最前面**，最长优先）。

        词表覆盖不到的"词表外真名"只能靠显式声明兜住。denylist 命中后替换为
        ``<PERSON>`` / ``<ORG>``：视觉上与常规中文占位符区分，一眼看出
        "这是黑名单命中，需要人工复核这一条"。
        """
        out: list[RedactionRule] = []
        section = raw.get("denylist") or {}
        if not isinstance(section, Mapping):
            return out
        for kind, item in section.items():
            item = item if isinstance(item, Mapping) else {}
            values = item.get("values") or []
            if isinstance(values, str):
                values = [values]
            values = tuple(str(v).strip() for v in values if str(v).strip())
            if not values:
                continue
            replacement = str(item.get("replacement") or "").strip()
            if not replacement:
                replacement = "<PERSON>" if str(kind) == "person" else "<ORG>"
            out.append(RedactionRule(
                id=f"denylist-{kind}", kind=str(kind), type="literal",
                replacement=replacement, values=values, pattern=""))
        # 最长优先：避免"张三"先命中把"张三丰"截成"<PERSON>丰"
        out.sort(key=lambda rule: max((len(v) for v in rule.values), default=0),
                 reverse=True)
        return out

    @staticmethod
    def _env_extra_rules() -> list["RedactionRule"]:
        """``CASE_REDACTION_EXTRA_TERMS``（逗号分隔）→ 额外人名词表。

        运维在现场发现新名字时，改环境变量比改 YAML 更快；两者叠加，
        任何一边配了都生效。
        """
        import os

        raw = os.getenv("CASE_REDACTION_EXTRA_TERMS", "") or ""
        try:  # 允许配置在 settings 里（测试用 setattr 注入）
            from ...common.config import settings

            raw = str(getattr(settings, "CASE_REDACTION_EXTRA_TERMS", "") or raw)
        except Exception:  # noqa: BLE001 - 配置读取失败不应挡住脱敏加载
            pass
        terms = tuple(t.strip() for t in raw.split(",") if t.strip())
        if not terms:
            return []
        return [RedactionRule(
            id="env-extra-person", kind="person", type="literal",
            replacement="<PERSON>",
            values=tuple(sorted(terms, key=len, reverse=True)), pattern="")]

    # ---------- 识别 ----------

    def detect(self, text: str) -> list[RedactionHit]:
        """只识别不替换（供 ``verify_no_leak`` 与审计统计）。"""
        content = str(text or "")
        hits: list[RedactionHit] = []
        for rule in self._rules:
            if rule.type == "literal":
                # 最长优先，避免"华辰"先替换掉"华辰电子"的前缀
                for value in sorted(rule.values, key=len, reverse=True):
                    count = content.count(value)
                    if count:
                        hits.append(RedactionHit(rule_id=rule.id, kind=rule.kind,
                                                 matched=value,
                                                 replacement=rule.replacement,
                                                 count=count))
                continue
            pattern = self._compiled[rule.id]
            for match in pattern.finditer(content):
                hits.append(RedactionHit(rule_id=rule.id, kind=rule.kind,
                                         matched=match.group(0),
                                         replacement=rule.replacement, count=1))
        for detector in self._extra_detectors:
            for hit in detector(content) or ():
                hits.append(hit)
        return hits

    # ---------- 脱敏 ----------

    def redact(self, text: str) -> RedactionResult:
        """按规则顺序替换（literal 最长优先 → regex），返回脱敏文本 + 命中明细。"""
        content = str(text or "")
        if not content:
            return RedactionResult(text="", hits=())
        hits: list[RedactionHit] = []
        for rule in self._rules:
            if rule.type == "literal":
                for value in sorted(rule.values, key=len, reverse=True):
                    if value and value in content:
                        occurrences = content.count(value)
                        content = content.replace(value, rule.replacement)
                        hits.append(RedactionHit(rule_id=rule.id, kind=rule.kind,
                                                 matched=value,
                                                 replacement=rule.replacement,
                                                 count=occurrences))
                continue
            pattern = self._compiled[rule.id]
            matched_values: list[str] = []

            def _sub(match: "re.Match[str]", _rule: RedactionRule = rule,
                     _bag: list[str] = matched_values) -> str:
                _bag.append(match.group(0))
                return _rule.replacement

            content = pattern.sub(_sub, content)
            if matched_values:
                hits.append(RedactionHit(rule_id=rule.id, kind=rule.kind,
                                         matched=matched_values[0],
                                         replacement=rule.replacement,
                                         count=len(matched_values)))
        for detector in self._extra_detectors:
            for hit in detector(content) or ():
                if hit.matched:
                    content = content.replace(hit.matched, hit.replacement)
                    hits.append(hit)
        return RedactionResult(text=content, hits=tuple(hits))

    def redact_fields(self, fields: Mapping[str, str]) -> tuple[dict[str, str],
                                                               tuple[RedactionHit, ...]]:
        """批量脱敏（案例的 title/summary/phenomenon/root_cause/countermeasure）。"""
        out: dict[str, str] = {}
        all_hits: list[RedactionHit] = []
        for key, value in (fields or {}).items():
            result = self.redact(str(value or ""))
            out[str(key)] = result.text
            all_hits.extend(result.hits)
        return out, tuple(all_hits)

    # ---------- 漏脱复检 ----------

    def verify_no_leak(self, text: str) -> list[RedactionHit]:
        """对**脱敏后**文本重跑全部识别器；返回残留命中（空 = 通过）。

        这是管道纪律 3 的实现：命中即拒绝入库，不降级放行。
        """
        return self.detect(text)

    def verify_fields(self, fields: Mapping[str, str]) -> dict[str, list[RedactionHit]]:
        """批量复检；返回 ``{字段: 残留命中}``（只包含有残留的字段）。"""
        problems: dict[str, list[RedactionHit]] = {}
        for key, value in (fields or {}).items():
            residuals = self.verify_no_leak(str(value or ""))
            if residuals:
                problems[str(key)] = residuals
        return problems


_SERVICE: Optional[RedactionService] = None


def get_redaction_service() -> RedactionService:
    """脱敏服务单例。"""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = RedactionService.from_yaml()
    return _SERVICE


def reset_redaction_service() -> None:
    """清空单例（测试用）。"""
    global _SERVICE
    _SERVICE = None
