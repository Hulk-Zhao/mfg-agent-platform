"""初始化向量库知识条目（历史案例 / FA规则 / 设备台账 / 工艺标准 / SOP）。

运行一次：``python -m mfg_agents.common.seed``

数据源：``data/knowledge/*.jsonl``（每行一条 ``{id, doc, meta}``），
五类知识（由 ``meta.type`` 区分）写入同一 ``failure_cases`` collection：

- ``cases_history.jsonl``  历史案例（type=case）
- ``fa_rules.jsonl``       FA 判定规则（type=fa_rule）
- ``equipment_ledger.jsonl`` 设备台账（type=equipment）
- ``process_standards.jsonl`` 工艺标准（type=process_std）
- ``sop.jsonl``            现场作业 SOP（type=sop）

目录可用环境变量 ``KNOWLEDGE_DIR`` 覆盖，默认按运行目录解析
``./data/knowledge``（与 ``rag_tools`` 的 chroma 持久化目录约定一致）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .rag_tools import add_case

#: meta.type → 中文展示名（同时用于计数打印）
TYPE_LABELS = {
    "case": "历史案例",
    "fa_rule": "FA规则",
    "equipment": "设备台账",
    "process_std": "工艺标准",
    "sop": "SOP",
}


def knowledge_dir() -> Path:
    """知识库数据目录：``KNOWLEDGE_DIR`` 优先，否则 ``./data/knowledge``。"""
    return Path(os.getenv("KNOWLEDGE_DIR") or Path(os.getcwd()) / "data" / "knowledge")


def load_cases(directory: Path | str | None = None) -> list[dict[str, Any]]:
    """读取目录下全部 ``*.jsonl`` 知识条目（按文件名排序，保证幂等顺序）。

    返回 ``[{"id": ..., "doc": ..., "meta": {...}}, ...]``。
    非法行（缺 id/doc）跳过并计入 ``skipped``，避免整批失败。
    """
    directory = Path(directory) if directory else knowledge_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"知识库目录不存在: {directory}")

    rows: list[dict[str, Any]] = []
    for fp in sorted(directory.glob("*.jsonl")):
        for lineno, line in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[seed] 跳过非法 JSON：{fp.name}:{lineno}")
                continue
            if not obj.get("id") or not obj.get("doc"):
                print(f"[seed] 跳过缺 id/doc：{fp.name}:{lineno}")
                continue
            meta = obj.get("meta") or {}
            meta.setdefault("type", "case")
            rows.append({"id": obj["id"], "doc": obj["doc"], "meta": meta})
    return rows


def run_seed() -> int:
    """把 ``data/knowledge/*.jsonl`` 全部条目写入向量库，返回写入总数。"""
    cases = load_cases()
    for c in cases:
        add_case(c["id"], c["doc"], c["meta"])

    counts: dict[str, int] = {}
    for c in cases:
        t = str(c["meta"].get("type", "case"))
        counts[t] = counts.get(t, 0) + 1
    detail = " / ".join(
        f"{TYPE_LABELS.get(t, t)} {n}" for t, n in sorted(counts.items()))
    print(f"[seed] 已写入 {len(cases)} 条知识（{detail}）")
    return len(cases)


if __name__ == "__main__":
    run_seed()
