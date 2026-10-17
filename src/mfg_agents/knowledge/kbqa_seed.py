"""导入企业制度知识库（方案 B：原项目知识库 → Milvus 向量化迁移）。

运行一次：``python -m mfg_agents.knowledge.kbqa_seed``

链路：原项目 ``data/knowledge_base`` 下 8 个部门目录的 ``*.md`` → 按段落切块
（``max_len=400``，与 ``build_index.py`` 同策略）→ 写入 Milvus ``enterprise_faq``
collection，``metadata = {dept, dept_cn, source, chunk_index}``。

- BGE-M3 编码 / 混合检索 / Reranker 全部复用平台既有 ``tools/rag_tools.py``，
  不需要原项目的 bge-small-zh / ChromaDB / BERT；
- 源目录可用 ``ENTERPRISE_KB_DIR`` 覆盖（默认指向黑马原项目路径）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..tools.rag_tools import add_kb_chunks

#: 默认源目录（原项目 knowledge_base；可用 ENTERPRISE_KB_DIR 覆盖）
DEFAULT_KB_DIR = r"E:\黑马资料\assistant\data\knowledge_base"

#: 目录名 → 类别 key（与原项目 build_index.py FOLDER_TO_LABEL 一致）
FOLDER_TO_LABEL = {
    "policy": "policy_attendance",
    "it": "it_vpn",
    "finance": "finance_expense",
    "hr": "hr_onboarding",
    "admin": "admin_logistics",
    "engineering": "engineering_eq",
    "legal": "legal_contract",
    "sales": "sales_marketing",
}

#: 类别 key → (中文名, 说明)，与前端的"支持类别"面板一致（保持原项目文案）
DEPT_CN = {
    "policy_attendance": ("考勤制度", "上下班/打卡/请假/年假"),
    "it_vpn": ("IT 支持", "VPN/密码/电脑/打印机"),
    "finance_expense": ("财务报销", "报销/发票/工资/差旅"),
    "hr_onboarding": ("人力资源", "入职/离职/社保/福利"),
    "admin_logistics": ("行政后勤", "食堂/班车/宿舍/会议室"),
    "engineering_eq": ("工程设备", "产线/维修/点检/备件"),
    "legal_contract": ("法务合同", "合同/公章/保密/知识产权"),
    "sales_marketing": ("销售市场", "报价/订单/客户/展会"),
}

#: 前端"支持类别"面板顺序（与原项目 LABELS 的业务类顺序一致）
DEPT_ORDER = list(FOLDER_TO_LABEL.values())


def source_dir() -> Path:
    """知识库源目录：``ENTERPRISE_KB_DIR`` 优先，否则原项目默认路径。"""
    return Path(os.getenv("ENTERPRISE_KB_DIR") or DEFAULT_KB_DIR)


def chunk_text(text: str, max_len: int = 400) -> list[str]:
    """按段落分块，超长段落按句切（与原项目 build_index.py 同策略）。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    for p in paragraphs:
        if len(p) <= max_len:
            chunks.append(p)
        else:
            sentences = p.replace("。", "。\n").split("\n")
            cur = ""
            for s in sentences:
                if len(cur) + len(s) <= max_len:
                    cur += s
                else:
                    if cur:
                        chunks.append(cur)
                    cur = s
            if cur:
                chunks.append(cur)
    return chunks


def load_chunks(directory: Path | str | None = None) -> list[dict[str, Any]]:
    """读取全部 ``*.md`` 并切块，返回 ``[{id, doc, meta}, ...]``（按类别/文件排序）。"""
    directory = Path(directory) if directory else source_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"企业知识库目录不存在: {directory}")

    rows: list[dict[str, Any]] = []
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        dept = FOLDER_TO_LABEL.get(folder.name, folder.name)
        dept_cn = DEPT_CN.get(dept, (folder.name, ""))[0]
        for md_file in sorted(folder.glob("*.md")):
            for i, chunk in enumerate(chunk_text(md_file.read_text(encoding="utf-8"))):
                rows.append({
                    "id": f"{folder.name}_{md_file.stem}_{i}",
                    "doc": chunk,
                    "meta": {
                        "dept": dept,
                        "dept_cn": dept_cn,
                        "source": md_file.name,
                        "chunk_index": i,
                    },
                })
    return rows


def run_seed() -> int:
    """把企业制度知识块写入 Milvus ``enterprise_faq``，返回写入总数。"""
    rows = load_chunks()
    docs = [r["doc"] for r in rows]
    # 分批写入：单次 BGE-M3 编码批量过大会吃内存
    batch = 64
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        add_kb_chunks(
            ids=[r["id"] for r in part],
            documents=[r["doc"] for r in part],
            metadatas=[r["meta"] for r in part],
        )
    depts: dict[str, int] = {}
    for r in rows:
        key = r["meta"]["dept_cn"]
        depts[key] = depts.get(key, 0) + 1
    detail = " / ".join(f"{k} {n}" for k, n in sorted(depts.items()))
    print(f"[kbqa_seed] 已写入 {len(rows)} 个制度知识块（{detail}）")
    return len(rows)


if __name__ == "__main__":
    run_seed()
