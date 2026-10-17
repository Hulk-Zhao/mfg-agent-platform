"""企业制度问答服务（方案 B：Milvus ``enterprise_faq`` + 平台统一 LLM）。

独立轻量 RAG 通道：**不接入** TwoLevelRouter / orchestrator 的制造场景链路——
企业问答是员工自助型的独立助手（左侧边栏入口），检索 top-k 制度片段 →
``get_llm``（deepseek-flash）生成带出处回答。

数据面：
- 知识块由 ``mfg_agents.common.kbqa_seed`` 导入，metadata = {dept, dept_cn,
  source, chunk_index}；
- 检索复用 ``tools/rag_tools.py``（BGE-M3 混合召回 + Reranker，无需 BERT/
  bge-small）。
"""
from __future__ import annotations

from typing import Any

from ...common.kbqa_seed import DEPT_CN, DEPT_ORDER
from ...common.llm_factory import get_llm, llm_available
from ...common.rag_tools import search_kb

#: 前端"支持类别"面板数据（保持原项目文案与顺序）
KBQA_DEPTS: list[dict[str, str]] = [
    {"key": key, "name": DEPT_CN[key][0], "desc": DEPT_CN[key][1]}
    for key in DEPT_ORDER
]

_SYSTEM_PROMPT = """你是企业咨询助手。根据以下制度片段回答员工问题。
规则：
1. 仅基于片段回答，片段中没有就说"未在制度中找到相关内容"，禁止编造
2. 简洁友好，150字以内
3. 末尾标注出处，格式：【来源：{cn} · 文件名 第N段】，引用多个片段时全部列出
======
{context}"""


def _tags_and_sources(hits: list[dict[str, Any]]) -> tuple[list[dict[str, str]],
                                                          list[dict[str, Any]]]:
    """由检索命中生成气泡标签（部门 + 出处）与结构化来源列表。"""
    depts: list[str] = []
    for h in hits:
        cn = (h.get("metadata") or {}).get("dept_cn")
        if cn and cn not in depts:
            depts.append(str(cn))

    tags: list[dict[str, str]] = [
        {"text": name, "cls": "first" if i == 0 else ""}
        for i, name in enumerate(depts)
    ]

    sources: list[dict[str, Any]] = []
    for h in hits:
        meta = h.get("metadata") or {}
        seg = int(meta.get("chunk_index") or 0) + 1
        src = str(meta.get("source") or "")
        label = f"{src} 第{seg}段"
        if label not in [s["label"] for s in sources]:
            sources.append({
                "label": label, "source": src, "segment": seg,
                "dept_cn": str(meta.get("dept_cn") or ""),
                "score": h.get("score"),
            })
    tags += [{"text": f"📄 {s['label']}", "cls": "src"} for s in sources]
    return tags, sources


def answer(query: str, top_k: int = 3) -> dict[str, Any]:
    """检索制度片段并生成带出处回答。

    返回：``{answer, tags, sources, category, degraded}``。
    LLM 不可用（离线/无 key）时降级为直接回放最相关片段（``degraded=True``），
    保证无外网也能演示。
    """
    hits = search_kb(query, n_results=top_k)
    tags, sources = _tags_and_sources(hits)

    top_dept = (hits[0].get("metadata") or {}).get("dept_cn") if hits else None
    category = {"name": str(top_dept or "其他")}

    if not hits:
        return {
            "answer": "未在制度中找到相关内容，可以换个问法，"
                      "或咨询考勤、报销、IT、人事等业务问题。",
            "tags": tags, "sources": sources, "category": category,
            "degraded": not llm_available(),
        }

    if not llm_available():
        # 降级：无 LLM 时回放最相关片段，仍标注出处
        top = hits[0]
        seg = int((top.get("metadata") or {}).get("chunk_index") or 0) + 1
        src = (top.get("metadata") or {}).get("source") or ""
        return {
            "answer": f"{top['document']}\n\n（离线降级：仅回放最相关片段）",
            "tags": tags, "sources": sources, "category": category,
            "degraded": True,
        }

    ctx_parts = []
    for h in hits:
        meta = h.get("metadata") or {}
        seg = int(meta.get("chunk_index") or 0) + 1
        ctx_parts.append(f"[{meta.get('source', '')} 第{seg}段]\n{h['document']}")
    context = "\n---\n".join(ctx_parts)

    llm = get_llm(temperature=0.3, thinking="disabled", max_tokens=400, timeout=60)
    msgs = [
        ("system", _SYSTEM_PROMPT.format(cn=category["name"], context=context)),
        ("human", query),
    ]
    resp = llm.invoke(msgs)
    text = getattr(resp, "content", None) or str(resp)
    if isinstance(text, list):  # 部分模型返回分段 content
        text = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part)
                       for part in text)
    return {
        "answer": str(text).strip(),
        "tags": tags, "sources": sources, "category": category,
        "degraded": False,
    }
