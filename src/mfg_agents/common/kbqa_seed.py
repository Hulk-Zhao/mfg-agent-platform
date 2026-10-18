"""导入企业制度知识库（企业制度 → Milvus 向量化）。

运行一次：``python -m mfg_agents.common.kbqa_seed``

链路：本地语料目录 ``data/kbqa_corpus/<类别>/*.md``（或 ``*.jsonl``）→ 按段落
切块（``max_len=400``，与 ``build_index.py`` 同策略）→ 写入 Milvus
``enterprise_faq`` collection，``metadata = {dept, dept_cn, source, chunk_index}``。

- 语料目录可用 ``ENTERPRISE_KB_DIR`` 覆盖（优先级高于默认的
  ``./data/kbqa_corpus``）；也兼容早期"黑马原项目 knowledge_base 目录"形态；
- BGE-M3 编码 / 混合检索 / Reranker 全部复用平台既有 ``tools/rag_tools.py``。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .rag_tools import add_kb_chunks

#: 默认语料目录（随仓库走，公网演示零依赖；可用 ENTERPRISE_KB_DIR 覆盖）
DEFAULT_KB_DIR = str(Path(os.getcwd()) / "data" / "kbqa_corpus")

#: 目录名 → 类别 key（原 8 类 + 制造主价值链 8 类 = 16 类）
FOLDER_TO_LABEL = {
    # ---- 原 8 类（职能后勤）----
    "policy": "policy_attendance",
    "it": "it_vpn",
    "finance": "finance_expense",
    "hr": "hr_onboarding",
    "admin": "admin_logistics",
    "engineering": "engineering_eq",
    "legal": "legal_contract",
    "sales": "sales_marketing",
    # ---- 新增 8 类（制造主价值链）----
    "quality": "quality_qms",
    "production": "production_mes",
    "scm": "scm_purchase",
    "eam": "equipment_eam",
    "ehs": "ehs_safety",
    "npi": "npi_rd",
    "wms": "warehouse_wms",
    "service": "service_after_sales",
}

#: 类别 key → (中文名, 说明)，与前端的"支持类别"面板一致
DEPT_CN = {
    "policy_attendance": ("考勤制度", "上下班/打卡/请假/年假"),
    "it_vpn": ("IT 支持", "VPN/密码/电脑/打印机"),
    "finance_expense": ("财务报销", "报销/发票/工资/差旅"),
    "hr_onboarding": ("人力资源", "入职/离职/社保/福利"),
    "admin_logistics": ("行政后勤", "食堂/班车/宿舍/会议室"),
    "engineering_eq": ("工程设备", "产线/维修/点检/备件"),
    "legal_contract": ("法务合同", "合同/公章/保密/知识产权"),
    "sales_marketing": ("销售市场", "报价/订单/客户/展会"),
    "quality_qms": ("品质制度", "检验/不良/8D/客诉"),
    "production_mes": ("生产制造", "排产/报工/换线/在制"),
    "scm_purchase": ("供应链采购", "请购/供应商/来料/招标"),
    "equipment_eam": ("设备管理", "点检/保养/备件/停机"),
    "ehs_safety": ("安全环保", "EHS/危化品/消防/劳保"),
    "npi_rd": ("研发 NPI", "EVT/DVT/PVT/变更"),
    "warehouse_wms": ("仓储物流", "收货/发料/盘点/呆滞"),
    "service_after_sales": ("客服售后", "退换/保修/SLA/上门"),
}

#: 前端"支持类别"面板顺序（与 FOLDER_TO_LABEL 的业务类顺序一致）
DEPT_ORDER = list(FOLDER_TO_LABEL.values())


def source_dir() -> Path:
    """语料目录：``ENTERPRISE_KB_DIR`` 优先，否则默认 ``./data/kbqa_corpus``。"""
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


def _load_md(folder: Path, dept: str, dept_cn: str) -> list[dict[str, Any]]:
    """读取目录下全部 ``*.md`` 并按段落切块。"""
    rows: list[dict[str, Any]] = []
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


def _load_jsonl(folder: Path, dept: str, dept_cn: str) -> list[dict[str, Any]]:
    """读取目录下全部 ``*.jsonl``（每行 ``{id, doc, meta}``）。

    缺 ``id`` 的行按 ``文件名_行号`` 生成；缺 ``doc`` 的行跳过。
    ``meta`` 中的 ``dept/dept_cn/source/chunk_index`` 由本函数统一补全，
    行内自带的其他字段（如 ``category``）保留。
    """
    rows: list[dict[str, Any]] = []
    for jsonl_file in sorted(folder.glob("*.jsonl")):
        text = jsonl_file.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[kbqa_seed] 跳过非法 JSON：{jsonl_file.name}:{i + 1}")
                continue
            if not obj.get("doc"):
                continue
            meta = dict(obj.get("meta") or {})
            meta.update({
                "dept": dept,
                "dept_cn": dept_cn,
                "source": jsonl_file.name,
                "chunk_index": i,
            })
            rows.append({
                "id": obj.get("id") or f"{folder.name}_{jsonl_file.stem}_{i}",
                "doc": obj["doc"],
                "meta": meta,
            })
    return rows


def load_chunks(directory: Path | str | None = None) -> list[dict[str, Any]]:
    """读取全部 ``*.md`` / ``*.jsonl`` 并切块，返回 ``[{id, doc, meta}, ...]``。

    按类别/文件排序，保证幂等顺序。
    """
    directory = Path(directory) if directory else source_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"企业知识库目录不存在: {directory}")

    rows: list[dict[str, Any]] = []
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        dept = FOLDER_TO_LABEL.get(folder.name, folder.name)
        dept_cn = DEPT_CN.get(dept, (folder.name, ""))[0]
        rows.extend(_load_md(folder, dept, dept_cn))
        rows.extend(_load_jsonl(folder, dept, dept_cn))
    return rows


def run_seed() -> int:
    """把企业制度知识块写入 Milvus ``enterprise_faq``，返回写入总数。"""
    rows = load_chunks()
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
