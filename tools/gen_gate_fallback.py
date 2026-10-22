"""从 data/gate_reasons.yaml 生成 demo-data.js 的离线闸门理由兜底块（R3）。

为什么要生成器而不是手抄：离线演示（后端未起）时前端拿不到
``/api/v2/governance/gates``，闸门理由只能内置一份；手抄必然与 yaml 漂移
（本项目已因「手改交付数据」吃过 P0）。因此：

- 唯一事实源仍是 ``src/mfg_agents/v2/data/gate_reasons.yaml``；
- 本脚本把它的 key/name/gate_class/why 原样渲染成 JS 数组，写入
  ``demo-data.js`` 的 ``<gate-reasons:begin/end>`` 标记之间；
- ``tests/demo/test_gate_reasons.py`` 会重跑本函数并与文件比对（字节级），
  手改 JS 块或改 yaml 后忘记重跑生成器都会变红。

用法：``python tools/gen_gate_fallback.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "src" / "mfg_agents" / "v2" / "data" / "gate_reasons.yaml"
JS_PATH = ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui" / "demo-data.js"

BEGIN = "  /* <gate-reasons:begin>"
END = "  /* <gate-reasons:end> */"

REQUIRED_FIELDS = ("key", "name", "gate_class", "why")


def _js_str(text: str) -> str:
    """JSON 兼容转义即可（内容为中文说明，不含控制字符）。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_block(yaml_path: Path = YAML_PATH) -> str:
    """渲染兜底块文本（含 begin/end 标记），不落盘。"""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    gates = raw.get("gates") or []
    if not gates:
        raise SystemExit("gate_reasons.yaml 无 gates 条目，拒绝生成空兜底块")

    lines = [
        f"{BEGIN} 由 tools/gen_gate_fallback.py 从 data/gate_reasons.yaml 生成，禁手改 */",
        "  // 离线演示（后端不可达）时的闸门理由兜底：与 /api/v2/governance/gates",
        "  // 的 data.gates 同构，前端用同一段 find 逻辑渲染，无需分支。",
        "  const GATE_REASONS = [",
    ]
    for g in gates:
        missing = [f for f in REQUIRED_FIELDS if not str(g.get(f) or "").strip()]
        if missing:
            raise SystemExit(f"gate_reasons.yaml 条目缺字段 {missing}: {g}")
        lines.append(
            "    { key: %s, name: %s, gate_class: %s, why: %s },"
            % (_js_str(g["key"]), _js_str(g["name"]),
               _js_str(g["gate_class"]), _js_str(g["why"]))
        )
    lines.append("  ];")
    lines.append(END)
    return "\n".join(lines) + "\n"


def apply(js_path: Path = JS_PATH, yaml_path: Path = YAML_PATH) -> bool:
    """把兜底块写入 js 文件（替换已有标记区）。返回是否发生变更。"""
    block = render_block(yaml_path)
    src = js_path.read_text(encoding="utf-8")

    if BEGIN in src and END in src:
        head = src[: src.index(BEGIN)]
        tail = src[src.index(END) + len(END):]
        new = head + block.rstrip("\n") + tail
    else:
        # 首次插入：放在 window.DEMO 导出之前（同作用域内可见）
        anchor = "  window.DEMO = {"
        if anchor not in src:
            raise SystemExit("demo-data.js 未找到 window.DEMO 导出锚点，拒绝盲插")
        new = src.replace(anchor, block + "\n" + anchor, 1)
        # 导出清单补一项（幂等：已存在则不加）
        if "GATE_REASONS," not in new:
            new = new.replace("    ROLES, ROLE_GROUPS,",
                              "    GATE_REASONS, ROLES, ROLE_GROUPS,", 1)

    if new == src:
        return False
    js_path.write_bytes(new.encode("utf-8"))
    return True


def main() -> int:
    changed = apply()
    print("gate fallback written" if changed else "gate fallback up-to-date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
