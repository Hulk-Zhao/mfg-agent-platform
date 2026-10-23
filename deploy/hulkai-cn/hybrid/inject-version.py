#!/usr/bin/env python3
"""静态托管时的版本号注入器（在云服务器上执行）。

## 为什么要这个脚本

后端自己托管前端时，v2/demo/api.py:164-192 的 demo_ui_index() 会在返回
index.html 前动态把 JS/CSS 的 `?v=` 改写为文件 mtime，从而根治浏览器缓存。

一旦前端改成 Nginx 静态托管，这段动态逻辑就不存在了：index.html 里写死的
`app.js?v=perf4` 之类的静态版本号再也不会变——结果是**你改了前端代码，
客户刷新看到的还是旧版本**，而且极难排查。

这个脚本在每次部署时复刻同样的注入动作，用 mtime 覆盖所有版本号。

## 用法

    cd /var/www/mfg-ui && python3 inject-version.py

## 设计取舍

注入后 Index.html 里的版本号会变成 mtime 数字。因此 Nginx 配置里
静态资源可以放心配置长缓存（immutable）——版本号变了就是新 URL，
版本号没变就说明文件内容没变。这套逻辑与后端托管完全等价。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 与 api.py:172 严格一致。后端清单若变动，这里必须同步，
# 否则"后端托管正常、静态托管缓存不失效"这类诡异差异又会回来。
CORE_ASSETS = (
    "demo-data.js", "terms.js", "app.js", "screens.js",
    "reports-screen.js", "help-screen.js", "kb-screen.js",
    "office-screen.js", "case-screen.js", "float-help.js",
    "app.css", "aging.css", "aging-mode.js", "theme-switcher.js",
)

# scenes/ 下的场景文件不在后端清单里（后端托管时改这些文件同样不会
# 自动失效缓存）。既然是静态托管，顺手补上，省得改了场景还得手动清缓存。
EXTRA_ASSETS = tuple(f"scenes/{p.name}" for p in sorted(
    Path(__file__).resolve().parent.joinpath("scenes").glob("*.js")
)) if Path(__file__).resolve().parent.joinpath("scenes").is_dir() else ()

INDEX = Path(__file__).resolve().parent / "index.html"


def stamp(name: str) -> int:
    fp = INDEX.parent / name
    return int(fp.stat().st_mtime) if fp.exists() else 0


def inject(html: str, assets: tuple[str, ...]) -> tuple[str, list[str]]:
    """对每个资源执行与后端同款的正则替换，返回新 HTML 与未命中的资源列表。"""
    missed = []
    for asset in assets:
        v = stamp(asset)
        pattern = rf"([\"'])([.\w/-]*{re.escape(asset)})(?:\?v=[0-9A-Za-z_.\-]+)?\1"
        new_html, n = re.subn(pattern, rf"\g<1>\g<2>?v={v}\g<1>", html)
        if n == 0:
            missed.append(asset)
        html = new_html
    return html, missed


def main() -> int:
    if not INDEX.exists():
        print(f"FATAL: 找不到 {INDEX}", file=sys.stderr)
        return 1

    original = INDEX.read_text(encoding="utf-8")
    html, missed = inject(original, CORE_ASSETS + EXTRA_ASSETS)

    INDEX.write_text(html, encoding="utf-8")

    # 幂等说明：重复执行时版本号已是最新 mtime，内容自然不会变化，
    # 这是正常情况而非失败。因此判据只看"命中率"，不看"内容是否变化"。
    # 只有核心资源一个都没命中，才说明正则真的失效了。
    total = len(CORE_ASSETS) + len(EXTRA_ASSETS)
    applied = total - len(missed)
    print(f"已更新 {INDEX}：{applied}/{total} 个资源引用带上了版本号")

    if len([a for a in CORE_ASSETS if a in missed]) == len(CORE_ASSETS):
        print("FATAL: 核心资源一个都没命中，index.html 结构可能已大改，"
              "请核对 CORE_ASSETS 清单与 api.py:172 是否同步", file=sys.stderr)
        return 1

    if missed:
        # 未命中不一定是错：资源可能已被删除或改名。但要让人看见，
        # 不能静默——这正是"缓存永远不失效"这类问题的典型掩护方式。
        print("提示：以下资源在 index.html 中未找到引用（如已下线可忽略）：")
        for m in missed:
            print(f"  - {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
