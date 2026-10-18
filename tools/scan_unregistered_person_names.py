"""**未登记疑似人名扫描**——只产待办清单，**绝不自动脱敏**。

为什么需要它（QA 裁决②）
------------------------
脱敏靠 `redaction_rules.yaml` 的词表 + `CASE_REDACTION_EXTRA_TERMS`，
两者都是**denylist**：只能防**已知**名字。于是新名字第一次出现时**必然先漏一次**，
而且**失效是静默的**——名字直接进索引，没有报错、没有告警。
`detect()` 已经在，缺的只是"没命中、但疑似人名"的兜底扫描。

为什么**只产待办、不自动脱敏**
------------------------------
弱规则（"姓氏 + 一到两字"）**必然误伤**：技术文本里"孔距"、"间隙"、"间隙补偿"
都可能落进模式。自动脱敏会把正常技术表述替换成 `<PERSON>`，**污染知识层**——
那比漏一个名字严重得多（知识层被污染后，所有引用都在引用一段被改过的话）。
所以这里**只报清单**，由人决定要不要登记进 `CASE_REDACTION_EXTERMS`。

用法
----
    .venv/Scripts/python.exe tools/scan_unregistered_person_names.py
    .venv/Scripts/python.exe tools/scan_unregistered_person_names.py --json

退出码：有疑似且未登记的命中 → 1（便于挂到 CI / 提交前钩子上提醒）。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

#: 常见姓氏（复姓单列）。**刻意保守**：只在"疑似"层面用，不用于自动脱敏。
SURNAMES = (
    "欧阳", "司马", "上官", "诸葛", "东方", "独孤", "慕容", "令狐",
    "王", "李", "张", "刘", "陈", "杨", "黄", "赵", "吴", "周", "徐",
    "孙", "马", "朱", "胡", "郭", "何", "林", "罗", "郑", "梁", "谢",
    "宋", "唐", "许", "韩", "冯", "邓", "曹", "彭", "曾", "肖", "田",
    "董", "袁", "潘", "蒋", "蔡", "余", "杜", "叶", "程", "苏", "魏",
    "吕", "丁", "任", "沈", "姚", "卢", "姜", "崔", "钟", "谭", "陆",
    "汪", "范", "金", "石", "廖", "贾", "夏", "韦", "付", "方", "白",
    "邹", "孟", "熊", "秦", "邱", "江", "尹", "薛", "闫", "段", "雷",
    "侯", "龙", "史", "陶", "黎", "贺", "顾", "毛", "郝", "龚", "邵",
)

#: 疑似人名：**姓氏 + 1~2 个汉字**。
PERSON_RE = re.compile(rf"(?:{'|'.join(SURNAMES)})[一-鿿]{{1,2}}")

#: ★ 必须**带职务上下文**才算疑似——这是把误伤压到可用的关键。
#: 第一版只用"姓氏 + 1~2 字"，在真实语料上打出 **82 处误报**
#: （"段锡膏""周期后""张紧并"…），恰好证明了**绝不能自动脱敏**。
#: 真实的泄漏形态是"客户现场联系人张三丰反馈…"——人名前面一定有职务词，
#: 所以把判定收紧到"职务词 + 分隔符 + 人名"，误报就降到个位数。
ROLE_CONTEXT = (
    "联系人", "负责人", "责任人", "对接人", "接口人", "处理人", "作业者",
    "工程师", "技术员", "操作员", "主管", "经理", "组长", "班长", "师傅",
    "老师", "博士", "经理助理", "客户经理", "设备经理",
)
ROLE_RE = re.compile(
    rf"(?:{'|'.join(ROLE_CONTEXT)})\s*[：:是为]?\s*({PERSON_RE.pattern})")

#: 职务词本身已在脱敏词表里命中时不必再报（由 detect() 负责）。
TECHNICAL_STOPWORDS = (
    "间隙", "孔距", "孔径", "弹性", "刚性", "柔性", "张力", "压力", "温度",
    "湿度", "振动", "精度", "公差", "波动", "漂移", "偏移", "磨损", "腐蚀",
    "时间", "空间", "能量", "质量", "重量", "体积", "面积", "比例", "概率",
)


def scan(texts: dict[str, str]) -> list[dict[str, Any]]:
    """扫描 → 疑似人名清单（已排除技术术语与已登记词）。"""
    import os

    from mfg_agents.v2.services.redaction import get_redaction_service

    redaction = get_redaction_service()
    registered = {t for rule in redaction._rules if rule.type == "literal"  # noqa: SLF001
                  for t in rule.values}
    extra = {t.strip() for t in
             os.environ.get("CASE_REDACTION_EXTRA_TERMS", "").split(",")
             if t.strip()}

    out: list[dict[str, Any]] = []
    for location, text in texts.items():
        for match in ROLE_RE.finditer(text):
            value = match.group(1)
            if value in registered or value in extra:
                continue
            if any(stop in value for stop in TECHNICAL_STOPWORDS):
                continue
            out.append({"where": location, "name": value,
                        "context": text[max(0, match.start() - 6):
                                        match.end() + 14]})
    return out


def _collect_texts() -> dict[str, str]:
    """收集待扫描文本：种子语料（与运行时入库后的库都算入口）。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    texts: dict[str, str] = {}
    seed = root / "data" / "case_seed.json"
    if seed.is_file():
        payload = json.loads(seed.read_text(encoding="utf-8"))
        cases = payload.get("cases") if isinstance(payload, dict) else payload
        for case in cases or ():
            for field in ("title", "summary", "phenomenon",
                           "root_cause_text", "countermeasure_text"):
                value = str(case.get(field) or "")
                if value:
                    texts[f"{case.get('case_id')}.{field}"] = value
    return texts


def main(argv: list[str]) -> int:
    findings = scan(_collect_texts())
    if "--json" in argv:
        print(json.dumps({"findings": findings, "count": len(findings)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"疑似未登记人名：{len(findings)} 处")
        for item in findings:
            print(f"  {item['where']:<34} {item['name']}  …{item['context']}…")
        if findings:
            print("\n这些名字**未登记**在 redaction_rules.yaml / CASE_REDACTION_EXTRA_TERMS。")
            print("本工具**不自动脱敏**（弱规则会误伤技术术语，污染知识层更严重）。")
            print("处置：人工确认后填入 CASE_REDACTION_EXTRA_TERMS（逗号分隔）。")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
