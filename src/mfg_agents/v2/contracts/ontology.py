"""OntologyRegistry：受控词表注册表（**加载期 fail-fast，运行期只读**）。

架构位置：§5.3 / §5.4（裁定 A7：关系库 + 受控词表 YAML，**不上图库**）。

口径（PRD §12 / §2）：首期是**人工维护的受控词表 + 显式关系**，不做自动本体抽取、
不做自动关系发现、不强制图数据库。词条新增走"申请 + 质量体系审批"，因此
``normalize()`` **不猜测、不自动新增**——未命中就是 ``None``。

三条纪律：

1. **加载期 fail-fast**：code 重复 / 父级不存在 / 同义词跨词条重复 / status 非法 /
   缺 ``name`` → 启动即失败（配置错误不能留到运行时）。
2. **``normalize`` 只做确定性对齐**：归一化文本（去空白、全半角、小写）后**精确匹配**
   name 或 synonyms，不做模糊、不做编辑距离（模糊匹配会把"立碑"匹配到"墓碑"之外的
   噪声，属于"自动新增"的变体）。
3. **``find_terms`` 用于查询改写**：按词条长度从长到短在文本中扫子串，确定性、
   可复现。它只影响召回（``query_text`` 侧），**永远不参与生成权限 filter**
   （裁定 A10：编译器签名里没有 query_text）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "OntologyError",
    "OntologyTerm",
    "OntologyRegistry",
    "DEFAULT_ONTOLOGY_DIR",
    "get_ontology_registry",
    "reset_ontology_registry",
]

#: ``v2/data/ontology/``（本模块在 v2/contracts/ 下 → ../data/ontology/）
DEFAULT_ONTOLOGY_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "ontology"
)

#: 词表 -> 文件名（缺文件即失败，不静默空词表）。
VOCAB_FILES: dict[str, str] = {
    "defect_mode": "defect_modes.yaml",
    "root_cause": "root_causes.yaml",
    "countermeasure": "countermeasures.yaml",
}

VALID_STATUS: tuple[str, ...] = ("active", "deprecated")


class OntologyError(Exception):
    """词表加载/校验失败（配置错误必须显式暴露）。"""


@dataclass(frozen=True)
class OntologyTerm:
    """受控词条。

    Attributes:
        code: 稳定编码（如 ``DM-SMT-012``），业务只存它。
        name: 中文名（权威展示名）。
        parent: 父级 code（可空；深度 ≤ 2，裁定 A7）。
        synonyms: 同义词（必须来自语料里真实出现过的写法）。
        status: ``active`` / ``deprecated``。
        approved_by / approved_at: 审批留痕（词表治理要求）。
    """

    code: str
    name: str
    parent: str = ""
    synonyms: tuple[str, ...] = ()
    status: str = "active"
    approved_by: str = ""
    approved_at: str = ""

    def all_labels(self) -> tuple[str, ...]:
        """全部可作为归一化输入的写法（name + synonyms）。"""
        return (self.name, *self.synonyms)


def _normalize_text(text: Any) -> str:
    """归一化：去空白（含全角空格）、转小写、去常见分隔符。"""
    raw = str(text or "")
    cleaned = raw.replace("\u3000", " ").strip().lower()
    return " ".join(cleaned.split())


class OntologyRegistry:
    """多词表注册表（构造即校验；只读）。"""

    def __init__(self, terms_by_vocab: dict[str, list[OntologyTerm]]) -> None:
        self._vocab: dict[str, tuple[OntologyTerm, ...]] = {}
        self._by_code: dict[str, dict[str, OntologyTerm]] = {}
        self._by_label: dict[str, dict[str, str]] = {}

        for vocab, terms in (terms_by_vocab or {}).items():
            key = str(vocab or "").strip()
            if not key:
                raise OntologyError("词表名不能为空")
            if not terms:
                raise OntologyError(f"词表 {key} 为空（空词表会让归一化全部失败）")
            by_code: dict[str, OntologyTerm] = {}
            for term in terms:
                if not term.code:
                    raise OntologyError(f"词表 {key} 存在空 code")
                if term.code in by_code:
                    raise OntologyError(f"词表 {key} 的 code 重复: {term.code}")
                if not term.name:
                    raise OntologyError(f"词表 {key} 的 {term.code} 缺少 name")
                if term.status not in VALID_STATUS:
                    raise OntologyError(
                        f"词表 {key} 的 {term.code} status 非法: {term.status!r}"
                        f"（合法值：{list(VALID_STATUS)}）")
                by_code[term.code] = term
            # 父级必须存在，且深度 ≤ 2（裁定 A7：不上图库的前提）
            for term in by_code.values():
                if not term.parent:
                    continue
                if term.parent == term.code:
                    raise OntologyError(f"词表 {key} 的 {term.code} 父级指向自身")
                if term.parent not in by_code:
                    raise OntologyError(
                        f"词表 {key} 的 {term.code} 父级不存在: {term.parent}")
                grand = by_code[term.parent].parent
                if grand:
                    raise OntologyError(
                        f"词表 {key} 的 {term.code} 关系深度超过 2 跳"
                        f"（{term.code} → {term.parent} → {grand}），首期不上图库")
            # 同义词全局唯一（防"同义爆炸"）
            by_label: dict[str, str] = {}
            for term in by_code.values():
                for label in term.all_labels():
                    norm = _normalize_text(label)
                    if not norm:
                        raise OntologyError(f"词表 {key} 的 {term.code} 存在空同义词")
                    existed = by_label.get(norm)
                    if existed and existed != term.code:
                        raise OntologyError(
                            f"词表 {key} 的同义词 {label!r} 同时属于 "
                            f"{existed} 与 {term.code}（同义爆炸，必须人工归并）")
                    by_label[norm] = term.code
            self._vocab[key] = tuple(sorted(by_code.values(), key=lambda t: t.code))
            self._by_code[key] = by_code
            self._by_label[key] = by_label

        logger.info("[ontology] 受控词表加载完成: %s",
                    {k: len(v) for k, v in self._vocab.items()})

    # ---------- 加载 ----------

    @classmethod
    def from_dir(cls, root: Optional[str | Path] = None) -> "OntologyRegistry":
        """扫描目录加载全部词表（任一文件格式错 → 抛 ``OntologyError``）。"""
        import yaml  # pyyaml 既有依赖，零新增依赖

        base = Path(root) if root else DEFAULT_ONTOLOGY_DIR
        if not base.is_dir():
            raise OntologyError(f"词表目录不存在: {base}")
        terms_by_vocab: dict[str, list[OntologyTerm]] = {}
        for vocab, filename in VOCAB_FILES.items():
            target = base / filename
            if not target.is_file():
                raise OntologyError(f"词表文件缺失: {target}")
            try:
                raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                raise OntologyError(f"词表解析失败: {target}（{exc}）") from exc
            if not isinstance(raw, dict):
                raise OntologyError(f"词表格式非法（应为 mapping）: {target}")
            declared = str(raw.get("vocabulary") or "").strip()
            if declared != vocab:
                raise OntologyError(
                    f"词表 {target} 的 vocabulary 字段应为 {vocab!r}，实际 {declared!r}")
            terms: list[OntologyTerm] = []
            for item in (raw.get("terms") or []):
                if not isinstance(item, dict):
                    raise OntologyError(f"词表 {target} 的词条格式非法: {item!r}")
                synonyms_raw = item.get("synonyms") or []
                if isinstance(synonyms_raw, str):
                    synonyms_raw = [synonyms_raw]
                terms.append(OntologyTerm(
                    code=str(item.get("code", "")).strip(),
                    name=str(item.get("name", "")).strip(),
                    parent=str(item.get("parent") or "").strip(),
                    synonyms=tuple(str(s).strip() for s in synonyms_raw if str(s).strip()),
                    status=str(item.get("status", "active")).strip().lower(),
                    approved_by=str(item.get("approved_by", "")),
                    approved_at=str(item.get("approved_at", "")),
                ))
            terms_by_vocab[vocab] = terms
        return cls(terms_by_vocab)

    # ---------- 查询 ----------

    def vocabularies(self) -> tuple[str, ...]:
        return tuple(sorted(self._vocab))

    def terms(self, vocab: str) -> tuple[OntologyTerm, ...]:
        """某词表的全部词条（未知词表 → 空元组）。"""
        return self._vocab.get(str(vocab or "").strip(), ())

    def term(self, vocab: str, code: str) -> Optional[OntologyTerm]:
        return self._by_code.get(str(vocab or "").strip(), {}).get(str(code or "").strip())

    def normalize(self, vocab: str, text: Any) -> Optional[str]:
        """自由文本 → 受控 code；未命中返回 ``None``（不猜测、不自动新增）。"""
        key = str(vocab or "").strip()
        labels = self._by_label.get(key)
        if not labels:
            return None
        return labels.get(_normalize_text(text))

    def expand_synonyms(self, vocab: str, code: str) -> tuple[str, ...]:
        """受控 code → 同义词元组（含权威名；供确定性查询改写）。"""
        term = self.term(vocab, code)
        if term is None:
            return ()
        return tuple(term.all_labels())

    def validate_code(self, vocab: str, code: Any) -> bool:
        """code 是否受控（含 deprecated 词条——历史案例仍可能引用它们）。"""
        return self.term(vocab, code) is not None

    def find_terms(self, vocab: str, text: Any,
                   limit: int = 8) -> tuple[str, ...]:
        """在文本中扫描词表写法，返回命中的 code（长词优先，确定性）。

        仅用于**查询改写/召回侧**，不参与任何权限判定（裁定 A10）。
        """
        haystack = _normalize_text(text)
        if not haystack:
            return ()
        labels = self._by_label.get(str(vocab or "").strip())
        if not labels:
            return ()
        hits: list[tuple[int, str]] = []
        for label, code in labels.items():
            if len(label) < 2:
                continue  # 单字标签噪声太大
            pos = haystack.find(label)
            if pos >= 0:
                hits.append((len(label), code))
                _ = pos
        hits.sort(key=lambda item: (-item[0], item[1]))
        out: list[str] = []
        for _length, code in hits:
            if code not in out:
                out.append(code)
            if len(out) >= max(1, int(limit)):
                break
        return tuple(out)

    def to_meta(self) -> dict[str, Any]:
        """``/api/v2/cases/meta`` 用的词表投影。"""
        return {
            vocab: [
                {"code": t.code, "name": t.name, "parent": t.parent,
                 "synonyms": list(t.synonyms), "status": t.status}
                for t in terms
            ]
            for vocab, terms in self._vocab.items()
        }

    def count(self) -> int:
        return sum(len(v) for v in self._vocab.values())


_REGISTRY: Optional[OntologyRegistry] = None


def get_ontology_registry() -> OntologyRegistry:
    """词表单例（首次调用时加载；失败即抛错，不降级为空词表）。"""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = OntologyRegistry.from_dir()
    return _REGISTRY


def reset_ontology_registry() -> None:
    """清空单例（测试用）。"""
    global _REGISTRY
    _REGISTRY = None
