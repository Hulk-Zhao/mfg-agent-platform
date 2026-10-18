"""企业问答助手 L1 前置匹配器（ADR-001 FAQ 前置层）。

定位：把「重复问题」（年假怎么休 / 报销流程 / VPN 连不上）在**不调 LLM、
不碰 Milvus** 的前提下直接给出权威答案。语料 = ``kbqa_policy`` 制度主数据
∪ ``kbqa_faq`` 晋升对（由 ``kbqa_store.faq_corpus`` 装载，本模块不碰库）。

评分设计（为什么不用 embedding / 不用 softmax(BM25)）：

- 语料百行级、每次全量重算 sub-ms，词面计分足够；embedding 会给每问加
  一次编码开销，留给二期度量证明需要时再上（ADR-001 D1-C）；
- softmax(BM25) 的绝对分随语料漂移（EduRAG 的坑），本模块用**可解释的
  加权命中**：关键词×2（长词更有信息量）、标题×3、内容×1（弱信号封顶），
  归一到 0..1，跨查询可比；
- 双闸决策：``score >= threshold`` **且** ``top1 - top2 >= margin``——
  两条都能答时不许乱指，回落 L2 更安全（ADR-001 §5）。

依赖：纯标准库，零第三方；语料与阈值全部可注入，离线可测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------- 归一化与切分

_LATIN_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def normalize(text: str) -> str:
    """归一化：去首尾空白、全角→半角、小写、内部空白折叠。

    缓存键与匹配都用归一化后的串——"年假 怎么休"与"年假怎么休"同问。
    """
    if not text:
        return ""
    out = []
    for ch in text.strip():
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII → 半角
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip().lower()


def _tokens(text: str) -> list[str]:
    """查询切分：拉丁/数字整词（≥2 字符）+ CJK 二元组。

    不做分词（不引 jieba）：二元组对短查询足够稳，且单字 CJK 候选会全库
    误命中（"的""是"），故单字 run 直接丢弃。
    """
    out: list[str] = []
    for m in _LATIN_RE.finditer(text):
        if len(m.group(0)) >= 2:
            out.append(m.group(0))
    for m in _CJK_RE.finditer(text):
        run = m.group(0)
        if len(run) == 1:
            continue
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _split_keywords(raw: Any) -> list[str]:
    """keywords 列（逗号分隔，中英文逗号都认）→ 去重列表。"""
    if not raw:
        return []
    parts = re.split(r"[,，]", str(raw))
    seen: list[str] = []
    for p in parts:
        kw = p.strip().lower()
        if len(kw) >= 2 and kw not in seen:
            seen.append(kw)
    return seen


# ---------------------------------------------------------------- 评分


@dataclass
class MatchResult:
    """一次匹配的结果。``row`` 为命中行（未命中为 None）。"""

    hit: bool
    row: Optional[dict[str, Any]] = None
    score: float = 0.0
    runner_up: float = 0.0
    margin: float = 0.0
    reason: str = ""


def score_row(query_norm: str, tokens: list[str], row: dict[str, Any]) -> float:
    """单行评分：同题直中 1.0；否则关键词×2 + 标题×3 + 内容×1，归一到 0..1。"""
    if not tokens:
        return 0.0
    # 同题直中：归一化后与行标题全等 → 满分（FAQ 行的问法就是它的标题，
    # 这条规则保证晋升/人工维护的问答对永远匹配得到自己的问题）
    if normalize(str(row.get("question") or "")) == query_norm:
        return 1.0
    title = str(row.get("question") or "").lower()
    content = str(row.get("answer") or "").lower()
    keywords = _split_keywords(row.get("keywords"))

    # 关键词命中：长词更有信息量（2×len），单项封顶 6 防止长词表刷分
    kw_raw = sum(2.0 * len(kw) for kw in keywords if kw in query_norm)
    kw_score = min(6.0, kw_raw)
    # 标题命中：标题二元组/拉丁词出现在查询里 → 强信号
    title_score = 3.0 if any(t in query_norm for t in _tokens(title)) else 0.0
    # 内容弱命中：查询候选有多大比例落在正文（封顶 1，单独到不了阈值）
    content_hits = sum(1 for t in tokens if t in content)
    content_score = min(1.0, content_hits / len(tokens))

    return min(1.0, (kw_score + title_score + content_score) / 6.0)


@dataclass
class FAQMatcher:
    """L1 匹配器。语料行结构见 ``kbqa_store.faq_corpus``。

    ``corpus`` / ``threshold`` / ``margin`` 均可注入——测试用假语料钉行为，
    生产用库装载 + settings 阈值。
    """

    corpus: list[dict[str, Any]] = field(default_factory=list)
    threshold: float = 0.70
    margin: float = 0.15

    def match(self, query: str) -> MatchResult:
        """对归一化后的查询取 top2，应用双闸决策。"""
        query_norm = normalize(query)
        tokens = _tokens(query_norm)
        if not tokens or not self.corpus:
            return MatchResult(hit=False, reason="empty_query_or_corpus")

        scored = sorted(
            ((score_row(query_norm, tokens, row), row) for row in self.corpus),
            key=lambda item: item[0], reverse=True)
        top_score, top_row = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        gap = top_score - runner_up

        if top_score < self.threshold:
            return MatchResult(hit=False, score=top_score, runner_up=runner_up,
                               margin=gap, reason="below_threshold")
        if gap < self.margin:
            return MatchResult(hit=False, score=top_score, runner_up=runner_up,
                               margin=gap, reason="ambiguous_margin")
        return MatchResult(hit=True, row=top_row, score=top_score,
                           runner_up=runner_up, margin=gap, reason="hit")
