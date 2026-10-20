"""CaseRelationIndex：**本体关系索引**（PRD FR-03，多跳关系推理的数据底座）。

## 为什么需要它

现有检索链路只会"读一句话、改一句话、搜一次"。跨段/跨案例的问题
（"整机段的异响能不能追到插件段的波峰焊"）之所以答不了，不是模型不够强，
而是**检索没有路可走**：案例之间明明共享着同一批受控码
（缺陷模式 / 根因 / 工艺段），却没有任何一层把它们连起来。

本模块把"案例元数据里的受控码共现"显式建成一张**可遍历的关系图**，
让 Agentic 循环（``case_agentic``）沿关系走 ≤3 跳，把"看不见的关系"
变成"可用的召回词"。

## 三条纪律（与既有裁定一致，不新增红线）

1. **关系是人工受控码的推论，不是模型猜的**：边只来自关系库里已登记的
   ``defect_mode_code`` / ``root_cause_code`` / ``line_code``，不做自动关系发现
   （承接 N7）。同义扩展/模糊匹配一律不做。
2. **只产出"召回词"，不产出"过滤条件"**：本模块的所有输出只允许被拼进
   ``query_text``；它**没有**、也**不得有**任何 ``FilterNode``/AST 产物
   （裁定 A10：编译器签名里没有 query_text，关系层也拿不到过滤器）。
3. **诚实呈现**：走不通就如实说走不通。未登记本体的编码没有可靠召回词
   → 记进 ``skipped``（"无可用召回词"），不硬编一个词凑命中；
   某类关系在语料里为空 → 记进 ``skipped``（"语料中无该关系"）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..contracts.ontology import OntologyRegistry, get_ontology_registry
from .case_store import VISIBLE_STATUS, CaseStore

__all__ = [
    "SEGMENT_LABELS",
    "SEGMENT_RECALL_TERMS",
    "SEGMENT_QUESTION_TERMS",
    "RelationEdge",
    "RelationExpansion",
    "CaseRelationIndex",
    "ontology_coverage",
    "segment_of",
    "segment_label",
    "segments_in_text",
    "get_relation_index",
    "reset_relation_index",
]

#: 产线前缀 → 工艺段**归组键**（同组视为同一工艺段，如 DIP/THT 同属插件段）。
SEGMENT_GROUP: dict[str, str] = {
    "SMT": "SMT", "DIP": "DIP", "THT": "DIP",
    "ASSY": "ASSY", "ASM": "ASSY", "装配": "ASSY",
    "TEST": "TEST", "AGING": "AGING", "CELL": "CELL",
    "PKG": "PKG", "注塑": "INJ", "INJ": "INJ",
    "机加": "MCH", "MCH": "MCH", "来料": "MAT",
}

#: 段归组键 → 中文段名（展示用；与前端 SEGMENT_PREFIX 同口径）。
SEGMENT_LABELS: dict[str, str] = {
    "SMT": "SMT 段", "DIP": "插件段", "ASSY": "整机组装段",
    "TEST": "测试段", "AGING": "老化段", "CELL": "电芯组包段",
    "PKG": "包装段", "INJ": "注塑段", "MCH": "机加段", "MAT": "来料段",
}

#: 段归组键 → 可用于**召回文本**的词（demo 字符后端只吃字面，因此给的是
#: 现场语料里真实会出现的写法：产线前缀 / 段口语名）。
SEGMENT_RECALL_TERMS: dict[str, tuple[str, ...]] = {
    "SMT": ("SMT", "贴片", "锡膏", "回流焊"),
    "DIP": ("DIP", "插件", "波峰焊", "THT"),
    "ASSY": ("整机", "装配", "组装", "ASSY"),
    "TEST": ("测试", "EOL", "功能测试"),
    "AGING": ("老化", "AGING"),
    "CELL": ("电芯", "组包", "CELL"),
    "PKG": ("包装", "装箱", "PKG"),
    "INJ": ("注塑", "INJ"),
    "MCH": ("机加", "CNC", "MCH"),
    "MAT": ("来料", "IQC", "供应商"),
}

#: 问句里出现的段口语 → 段归组键（问句侧识别，**只用于决定走哪条关系**）。
SEGMENT_QUESTION_TERMS: dict[str, str] = {
    "smt": "SMT", "贴片": "SMT", "锡膏": "SMT", "回流焊": "SMT", "spi": "SMT",
    "插件": "DIP", "dip": "DIP", "波峰焊": "DIP", "波峰": "DIP", "tht": "DIP",
    "整机": "ASSY", "装配": "ASSY", "组装": "ASSY", "总装": "ASSY",
    "assy": "ASSY", "asm": "ASSY",
    "测试": "TEST", "eol": "TEST", "功能全测": "TEST",
    "老化": "AGING", "aging": "AGING",
    "包装": "PKG", "装箱": "PKG", "贴标": "PKG", "pkg": "PKG",
    "电芯": "CELL", "组包": "CELL", "cell": "CELL",
    "注塑": "INJ", "inject": "INJ",
    "机加": "MCH", "cnc": "MCH", "mch": "MCH",
    "来料": "MAT", "iqc": "MAT", "供应商": "MAT", "进料": "MAT",
}

#: 关系名（trace 与结果卡上的"关系来源"文案，唯一口径）。
VIA_SAME_CASE_ROOT = "同案例根因"
VIA_SAME_CASE_DEFECT = "同案例缺陷模式"
VIA_DEFECT_SEGMENT = "同缺陷模式跨段"
VIA_ROOT_SEGMENT = "同根因跨段"
VIA_SEGMENT_DEFECT = "同段缺陷模式"
VIA_SEGMENT_ROOT = "同段根因"


def segment_of(line_code: Any) -> str:
    """产线码 → 段归组键（空/未知 → ""）。"""
    raw = str(line_code or "").strip()
    if not raw:
        return ""
    prefix = raw.split("-")[0]
    return SEGMENT_GROUP.get(prefix, "")


def segment_label(group: str) -> str:
    """段归组键 → 中文段名（未知键原样回显，不编名字）。"""
    key = str(group or "").strip()
    return SEGMENT_LABELS.get(key, key)


def segments_in_text(text: Any) -> list[str]:
    """问句里提到的段（长词优先，确定性；命中即算，不做语义猜测）。"""
    haystack = str(text or "").lower()
    if not haystack:
        return []
    hits: list[tuple[int, str]] = []
    for term, group in SEGMENT_QUESTION_TERMS.items():
        if term and term in haystack and group not in [g for _n, g in hits]:
            hits.append((len(term), group))
    hits.sort(key=lambda item: (-item[0], item[1]))
    ordered: list[str] = []
    for _length, group in hits:
        if group not in ordered:
            ordered.append(group)
    return ordered


@dataclass(frozen=True)
class RelationEdge:
    """一条关系边（**只用于召回文本与展示，不参与任何权限判定**）。"""

    hop: int
    #: 关系名（中文，唯一口径）
    via: str
    #: 目标节点类型：defect_mode / root_cause / segment
    kind: str
    #: 目标节点码（受控码 或 段归组键）
    code: str
    #: 中文展示名（本体已登记用权威名；未登记用码本身）
    label: str
    #: **可加入查询文本的召回词**（空 = 该节点无可靠召回词，已在 skipped 说明）
    term: str
    #: 支撑该关系的案例数（真实统计，用于排序与展示）
    support: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"hop": self.hop, "via": self.via, "kind": self.kind,
                "code": self.code, "label": self.label, "term": self.term,
                "support": self.support}


@dataclass
class RelationExpansion:
    """一次关系扩展的结果（供 Agentic 循环与 trace 使用）。"""

    entries: list[RelationEdge] = field(default_factory=list)
    #: 去重后的召回词（顺序即优先级）
    terms: list[str] = field(default_factory=list)
    #: 诚实记录"没走通"的部分：无可用召回词 / 语料中无该关系
    skipped: list[str] = field(default_factory=list)
    hops_used: int = 0
    #: 一行中文摘要（trace 的 detail 直接用它，避免前端拼文案）
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [e.to_dict() for e in self.entries],
                "terms": list(self.terms), "skipped": list(self.skipped),
                "hops_used": int(self.hops_used), "summary": self.summary}


@dataclass
class _EdgeAgg:
    """邻接表条目聚合（同一对节点可能由多条案例支撑）。"""

    via: str
    support: int = 0
    case_ids: list[str] = field(default_factory=list)


class CaseRelationIndex:
    """案例本体关系图（**从关系库现算，不落库、不新增表**）。

    为什么现算：语料只有几百例，建图是毫秒级；一旦落库就要处理"入库/发布/撤权
    后的图更新"一致性问题——那是新的一类 bug 来源。现算 + 指纹缓存已够用。
    """

    def __init__(self, cases: Iterable[Mapping[str, Any]],
                 ontology: Optional[OntologyRegistry] = None) -> None:
        self._ontology = ontology or get_ontology_registry()
        self._adj: dict[tuple[str, str], dict[tuple[str, str], _EdgeAgg]] = {}
        self._case_codes: dict[str, tuple[str, str, str]] = {}
        self._segments_of_code: dict[tuple[str, str], set[str]] = {}
        self._built_cases = 0
        for case in cases or ():
            self._add_case(case)

    # ---------- 建图 ----------

    def _add_case(self, case: Mapping[str, Any]) -> None:
        case_id = str(case.get("case_id") or "")
        if not case_id:
            return
        defect = str(case.get("defect_mode_code") or "")
        root = str(case.get("root_cause_code") or "")
        group = segment_of(case.get("line_code"))
        self._case_codes[case_id] = (defect, root, group)
        self._built_cases += 1

        def link(src: tuple[str, str], dst: tuple[str, str], via: str) -> None:
            if not src[1] or not dst[1] or src == dst:
                return
            bucket = self._adj.setdefault(src, {})
            agg = bucket.get(dst)
            if agg is None:
                agg = _EdgeAgg(via=via)
                bucket[dst] = agg
            agg.support += 1
            if case_id not in agg.case_ids:
                agg.case_ids.append(case_id)

        if defect and root:
            link(("defect_mode", defect), ("root_cause", root), VIA_SAME_CASE_ROOT)
            link(("root_cause", root), ("defect_mode", defect), VIA_SAME_CASE_DEFECT)
        if defect and group:
            link(("defect_mode", defect), ("segment", group), VIA_DEFECT_SEGMENT)
            link(("segment", group), ("defect_mode", defect), VIA_SEGMENT_DEFECT)
            self._segments_of_code.setdefault(
                ("defect_mode", defect), set()).add(group)
        if root and group:
            link(("root_cause", root), ("segment", group), VIA_ROOT_SEGMENT)
            link(("segment", group), ("root_cause", root), VIA_SEGMENT_ROOT)
            self._segments_of_code.setdefault(
                ("root_cause", root), set()).add(group)

    @classmethod
    def from_store(cls, store: CaseStore,
                   ontology: Optional[OntologyRegistry] = None) -> "CaseRelationIndex":
        """从关系库现算（只取可见状态的案例；撤权/未结案不参与建图）。"""
        cases = store.list_cases(status=VISIBLE_STATUS)
        return cls(cases, ontology=ontology)

    # ---------- 查询 ----------

    @property
    def case_count(self) -> int:
        return self._built_cases

    def code_segments(self, kind: str, code: str) -> tuple[str, ...]:
        """某受控码在语料中出现过的段（**跨段判定的唯一口径**）。"""
        return tuple(sorted(self._segments_of_code.get((kind, str(code or "")), set())))

    def label_of(self, kind: str, code: str) -> tuple[str, str]:
        """节点 → (中文展示名, 可用召回词)。未登记本体 → (码本身, "")。"""
        if kind in ("defect_mode", "root_cause", "countermeasure"):
            term = self._ontology.term(kind, code)
            if term is not None:
                return term.name, term.name
            return str(code or ""), ""
        if kind == "segment":
            return segment_label(code), ""
        return str(code or ""), ""

    def neighbors(self, node: tuple[str, str], limit: int = 8
                  ) -> list[tuple[tuple[str, str], _EdgeAgg]]:
        """一跳邻居（按支撑案例数降序 → 码序，确定性）。"""
        bucket = self._adj.get(node) or {}
        rows = sorted(bucket.items(),
                      key=lambda item: (-item[1].support, item[0][0], item[0][1]))
        return rows[:max(0, int(limit))]

    # ---------- 扩展 ----------

    def expand(self, *, question: str,
               defect_codes: Sequence[str] = (),
               root_codes: Sequence[str] = (),
               max_hops: int = 3, per_hop_limit: int = 8,
               term_limit: int = 6) -> RelationExpansion:
        """从问句的受控码 / 段出发，沿关系走了 ≤max_hops 跳，产出召回词。

        与权限无关：只产出"还能搜什么词"，能不能看到由安全内核照常决定。
        """
        result = RelationExpansion()
        hops = max(1, int(max_hops))
        entries: list[RelationEdge] = []
        visited: set[tuple[str, str]] = set()
        frontier: list[tuple[str, str]] = []
        for code in (defect_codes or ()):
            if code:
                node = ("defect_mode", str(code))
                if node not in visited:
                    visited.add(node)
                    frontier.append(node)
        for code in (root_codes or ()):
            if code:
                node = ("root_cause", str(code))
                if node not in visited:
                    visited.add(node)
                    frontier.append(node)
        for group in segments_in_text(question):
            node = ("segment", group)
            if node not in visited:
                visited.add(node)
                frontier.append(node)

        if not frontier:
            result.summary = "问句未命中受控码，也没有可识别的工艺段，关系扩展未启动"
            result.skipped.append("问句无受控码/段入口")
            return result

        used_hops = 0
        for hop in range(1, hops + 1):
            if not frontier:
                break
            used_hops = hop
            next_frontier: list[tuple[str, str]] = []
            for node in frontier:
                rows = self.neighbors(node, limit=per_hop_limit)
                if not rows:
                    kind, code = node
                    name, _t = self.label_of(kind, code)
                    result.skipped.append(f"{name}：语料中无该关系的相邻节点")
                for target, agg in rows:
                    if target in visited:
                        continue
                    if target[0] == "segment":
                        # 段节点不带本体权威名：召回词统一由 SEGMENT_RECALL_TERMS
                        # 注入（那是现场语料真实出现的写法），这里不重复造词。
                        name = segment_label(target[1])
                        term = ""
                    else:
                        name, term = self.label_of(target[0], target[1])
                    visited.add(target)
                    next_frontier.append(target)
                    if not term and target[0] != "segment":
                        result.skipped.append(
                            f"{name}（{target[0]}:{target[1]}）未登记本体，无可用召回词")
                    entries.append(RelationEdge(
                        hop=hop, via=agg.via, kind=target[0], code=target[1],
                        label=name, term=term, support=agg.support))
            frontier = next_frontier
            if not frontier:
                break

        result.hops_used = used_hops
        result.entries = entries

        # 召回词优先级：① 本体权威名（最具体）② 段召回词（覆盖面最广）
        terms: list[str] = []
        seg_groups: list[str] = []
        for edge in sorted(entries, key=lambda e: (-e.support, e.hop,
                                                   e.kind, e.code)):
            if edge.kind == "segment":
                if edge.code not in seg_groups:
                    seg_groups.append(edge.code)
            elif edge.term and edge.term not in terms:
                terms.append(edge.term)
        for group in seg_groups:
            for word in SEGMENT_RECALL_TERMS.get(group, ()):
                if word and word not in terms:
                    terms.append(word)
        result.terms = terms[:max(1, int(term_limit))]

        segs = [segment_label(g) for g in seg_groups]
        parts: list[str] = []
        if entries:
            parts.append(f"沿本体关系走 {used_hops} 跳，命中 {len(entries)} 个相邻节点")
        if result.terms:
            parts.append("扩展召回词：" + "、".join(result.terms))
        if segs:
            parts.append("关联工艺段：" + "、".join(segs))
        if result.skipped:
            parts.append(f"{len(result.skipped)} 个节点无可用召回词（未登记本体）")
        result.summary = "；".join(parts) or "关系扩展未产出可用召回词"
        return result

    # ---------- 命中案例的关系来源标注 ----------

    def relations_for_cases(self, case_ids: Sequence[str],
                            limit: int = 3) -> dict[str, list[str]]:
        """给命中案例标注"它与其他命中案例的关系来源"（结果卡展示用）。

        口径：只看**本次命中集合内部**的共享受控码——不做跨集合推断，
        因此文案里的"跨段"是真实由语料支撑的（段取自 `code_segments`）。
        """
        wanted = {str(c) for c in case_ids or () if c}
        if not wanted:
            return {}
        buckets: dict[tuple[str, str], list[str]] = {}
        for case_id in sorted(wanted):
            defect, root, _group = self._case_codes.get(case_id, ("", "", ""))
            for key in (("defect_mode", defect), ("root_cause", root)):
                if key[1]:
                    buckets.setdefault(key, []).append(case_id)
        out: dict[str, list[str]] = {}
        for (kind, code), members in sorted(buckets.items()):
            if len(members) < 2:
                continue
            name, _t = self.label_of(kind, code)
            segments = self.code_segments(kind, code)
            tag = "同缺陷模式" if kind == "defect_mode" else "同根因"
            desc = f"{tag} {name}"
            if len(segments) > 1:
                desc += "（跨段：" + "/".join(segment_label(g) for g in segments) + "）"
            for case_id in members:
                bucket = out.setdefault(case_id, [])
                if desc not in bucket:
                    bucket.append(desc)
        for case_id, labels in out.items():
            out[case_id] = labels[:max(1, int(limit))]
        return out


_CACHE: dict[str, Any] = {"fingerprint": None, "index": None}


def ontology_coverage(cases: Iterable[Mapping[str, Any]],
                      ontology: Optional[OntologyRegistry] = None,
                      *, sample_limit: int = 20) -> dict[str, Any]:
    """**语料 ↔ 本体对齐度**（PRD FR-08：本体覆盖率看板的数据面）。

    为什么必须把它做成一等指标：关系扩展走不走得动，取决于"案例里的受控码
    在不在词表里"。覆盖率低 → 术语归一落空 → 既扩不出召回词，也没法沿关系走。
    这个数字如果不可观测，「本体是资产」就只是一句宣传语。

    口径（全部来自真实数据，不估算）：

    - ``used``：语料里**出现过的编码**（去重）；
    - ``registered``：其中能在受控词表里命中的；
    - ``rate`` = registered / used；
    - ``unregistered_sample``：未登记编码样例（**只给码，不给案例内容**），
      供质量体系按单回填；
    - ``segments``：工艺段案例分布（判断"某段本体覆盖是否系统性缺失"）。

    注意：本函数只读"可见状态案例的受控码"，不返回任何案例正文，
    也不参与任何权限判定——它是治理视图的输入，不是检索面。
    """
    registry = ontology or get_ontology_registry()
    rows = list(cases or ())
    out: dict[str, Any] = {
        "cases": len(rows),
        "target_rate": 0.95,
        "vocabularies": {},
        "segments": {},
        "notes": [],
    }
    for vocab, field in (("defect_mode", "defect_mode_code"),
                         ("root_cause", "root_cause_code")):
        used: set[str] = set()
        with_code = 0
        for case in rows:
            code = str(case.get(field) or "").strip()
            if not code:
                continue
            with_code += 1
            used.add(code)
        registered = sorted(c for c in used if registry.term(vocab, c))
        missing = sorted(c for c in used if not registry.term(vocab, c))
        rate = (len(registered) / len(used)) if used else 0.0
        out["vocabularies"][vocab] = {
            "vocab_size": len(registry.terms(vocab)),
            "cases_with_code": with_code,
            "used_codes": len(used),
            "registered_codes": len(registered),
            "unregistered_codes": len(missing),
            "rate": round(rate, 4),
            "meets_target": rate >= out["target_rate"],
            "unregistered_sample": missing[:max(0, int(sample_limit))],
        }
    segments: dict[str, int] = {}
    for case in rows:
        group = segment_of(case.get("line_code"))
        if group:
            segments[segment_label(group)] = segments.get(segment_label(group), 0) + 1
    out["segments"] = dict(sorted(segments.items(), key=lambda kv: -kv[1]))
    for vocab, payload in out["vocabularies"].items():
        if not payload["meets_target"]:
            out["notes"].append(
                f"{vocab} 覆盖率 {payload['rate']:.1%} < 目标 {out['target_rate']:.0%}："
                f"{payload['unregistered_codes']} 个编码未登记本体，"
                "这些案例的术语归一会落空（关系扩展也扩不出召回词），需质量体系回填")
    if not out["notes"]:
        out["notes"].append("缺陷模式与根因的覆盖率均达标")
    return out


def get_relation_index(store: CaseStore,
                       ontology: Optional[OntologyRegistry] = None
                       ) -> CaseRelationIndex:
    """按案例总数做指纹缓存的单例（**入库/发布后调用 ``reset_relation_index``**）。"""
    fingerprint = store.count()
    if _CACHE.get("fingerprint") == fingerprint and _CACHE.get("index") is not None:
        return _CACHE["index"]  # type: ignore[return-value]
    index = CaseRelationIndex.from_store(store, ontology=ontology)
    _CACHE["fingerprint"] = fingerprint
    _CACHE["index"] = index
    return index


def reset_relation_index() -> None:
    """清空关系图缓存（入库 / 发布 / 撤权 / 测试注入后必调）。"""
    _CACHE["fingerprint"] = None
    _CACHE["index"] = None
