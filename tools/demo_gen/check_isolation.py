#!/usr/bin/env python3
"""演示实例隔离自检（T12）。

演示实例（`/demo-ui` + `/api/v2/demo/*`）必须与任何真实环境物理隔离。本脚本在
**静态层**断言三件事，与运行时的 `config_demo.assert_isolated()`（api.py lifespan）
形成「启动兜底 + 提交前扫描」双层：

  A. 无外部连接串 —— 出现的每个 URL 主机必须在白名单内（本地回环 / 容器服务名 /
     保留示例域 / SVG 命名空间 / 密钥为空的 LLM 端点）；RFC1918 私网地址一律违规。
  B. 无凭据泄漏 —— 高熵密钥字面量（sk-/AKIA/ghp_/私钥…）、URL 内嵌密码、
     敏感变量（PASSWORD/API_KEY/SECRET/TOKEN）非演示取值。
  C. 无真实企业名词 —— 真实厂名黑名单；电话段只许虚构段（词表 phone_segments）；
     演示数据的 name 字段必须取自虚构词表（tools/demo_gen/wordlist.json）。

**扫描范围分层（2026-10-19 人工裁决，见计划文档 T12 特殊备注）**：
生态链演示包（`data/demo_storyline`、`v2/demo/scenes`、`v2/demo` 配置与 api.py）与
`tools/` **必须全绿**；T01–T14 之前既有的旧演示 UI `ui/demo-data.js`、
`ui/screens.js`、`ui/app.js` 用真实行业品牌作语义素材（且 9 个既有 `tests/v2` 用例
依赖这些串），整改需越出 T12「可碰文件」清单，故单独列为
「遗留·超范围」**照实打印但不计入本节点验收**（不静默、不隐藏）。

用法：
    uv run python tools/demo_gen/check_isolation.py [--root .]
    uv run python tools/demo_gen/check_isolation.py --self-test   # 合成坏内容验证三类检测器会报出
退出码：0 = 范围内全绿；1 = 范围内有违规（逐条打印 file:line 与命中片段）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# ---------------------------------------------------------------- 扫描范围

SCAN_DIRS = (
    "src/mfg_agents/v2/demo",     # 演示 UI / API / 装载层 / 演示配置
    "data/demo_storyline",        # 合成演示数据集 + 场景派生数据契约
    "tools/demo_gen",             # 演示数据生成器与校验器
    "deploy",                     # 演示中间件配置（本机容器）
)
SCAN_FILES = (".env", ".env.example", "docker-compose.yml", "docker-compose.infra.yml")

TEXT_SUFFIXES = {
    ".py", ".js", ".json", ".jsonl", ".md", ".html", ".css", ".yml", ".yaml",
    ".txt", ".sh", ".bat", ".sql", ".ini", ".cfg", ".example", ".env", ".svg",
}
EXCLUDE_DIR_PARTS = {"__pycache__", "node_modules", ".git", ".pytest_cache"}
EXCLUDE_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".pyc", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".woff", ".woff2", ".ttf", ".zip", ".pdf", ".pt", ".bin",
}
# 非 UTF-8 文本 / 超大文件（模型权重等）不参与扫描
MAX_TEXT_BYTES = 4 * 1024 * 1024

# ---------------------------------------------------------------- A. 连接串

ALLOWED_URL_HOSTS = {
    # 本地回环 / 本机容器
    "localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal",
    # docker-compose 服务名（仅容器网内可见，非外部）
    "mysql", "redis", "milvus",
    # 保留示例域（RFC 2606）与非网络资源
    "example.com", "www.w3.org",
    # LLM 供应商端点（密钥为空，见 .env DS_KEY=）
    "api.deepseek.com",
}
ALLOWED_URL_HOST_SUFFIXES = (".example.com",)

# 只认 ASCII 的 URL 字符集，避免把紧跟其后的中文正文一起吞进来
URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[A-Za-z0-9\-._~%!$&'()*+,;=:@/?#\[\]]*")
CRED_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://([^/\s:@]+):([^/\s:@]+)@")
PRIVATE_IP_RE = re.compile(
    r"(?<![\d.])("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(?![\d.])"
)

# ---------------------------------------------------------------- B. 凭据

HIGH_ENTROPY_CRED = (
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "疑似 OpenAI/DeepSeek API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "疑似 AWS Access Key ID"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "疑似 GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "疑似 GitHub 细粒度 token"),
    (re.compile(r"\bxox[abpsr]-[A-Za-z0-9\-]{10,}\b"), "疑似 Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "疑似私钥"),
)

SECRET_ASSIGN_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:PASSWORD|PASSWD|API_?KEY|SECRET|TOKEN))[ \t]*[=:][ \t]*(\S+)"
)
# 演示实例的合法取值：空值 / 演示口令 / 演示 HMAC 密钥（.env AUTH_SECRET，非生产）/
# 企业微信官方文档公开测试向量（im_simulator.py：QDG6eK / wx5823bf96d3bd56c7，非真实凭据）
ALLOWED_SECRET_VALUES = {
    "demo123", "demo", "9f3c1a7b52de48c0a1b6f2e7d4c89053",
    "QDG6eK", "wx5823bf96d3bd56c7",
    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
}
PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:your|xxx+|\*+|\.{3,}|<.*>|change|todo|placeholder|none|null|changeme|sk-x+)"
)

# ---------------------------------------------------------------- C. 真实企业名词

REAL_BRAND_BLACKLIST = (
    "富士康", "鸿海", "台积电", "立讯精密", "立讯", "比亚迪", "华为", "宁德时代",
    "深南电路", "千住金属", "中芯国际", "长江存储", "和硕", "仁宝", "纬创", "光宝",
    "欧菲光", "闻泰", "蓝思科技", "歌尔股份", "韦尔股份", "中航锂电", "欣兴电子",
    "健鼎科技", "STMicroelectronics", "TSMC", "SAMSUNG", "Qualcomm",
)
PHONE_CN_RE = re.compile(r"(?<![\d\-])1[3-9]\d{9}(?![\d\-])")
PHONE_400_RE = re.compile(r"(?<![\d\-])400[-\s]?\d{3}[-\s]?\d{4}(?![\d\-])")
PHONE_LANDLINE_RE = re.compile(r"(?<![\d\-])0\d{2,3}-\d{7,8}(?![\d\-])")
ALLOWED_PHONE_PREFIXES = ("1380000", "1390000")  # 词表 phone_segments 的虚构段

# 演示数据集中「企业/设备名」字段，取值必须来自虚构词表
DATA_NAME_CHECKS = (
    ("customers.jsonl", "name"),
    ("suppliers.jsonl", "name"),
    ("equipment.jsonl", "name"),
)

# host 规则豁免：该文件是隔离黑名单的定义处，其字符串是「待检特征」而非连接串
HOST_RULE_EXEMPT_FILE = "config_demo.py"
HOST_RULE_EXEMPT_MARKER = "_FORBIDDEN_PREFIXES"

# 本文件自身是「真实品牌黑名单 + 电话号段 + 合成自测样本」的定义处，其字面量为
# 待检特征而非真实内容，不是企业名词的检查对象（同 config_demo.py 的 host 豁免）。
SELF_EXEMPT_FILE = "check_isolation.py"

# 「遗留·超范围」精确路径（相对仓库根，逐字符比对，不用文件名兜底——避免误豁免
# 未来同名文件）：T01–T14 之前既有的旧演示 UI。经 2026-10-19 人工裁决列为超范围，
# 照实打印但不计入 T12 验收；整改（改 ui/ + 9 个 tests/v2 用例）由独立节点承接。
OUT_OF_SCOPE_PATHS = (
    "src/mfg_agents/v2/demo/ui/demo-data.js",
    "src/mfg_agents/v2/demo/ui/screens.js",
    "src/mfg_agents/v2/demo/ui/app.js",
)


class Violation(tuple):
    """一条违规证据：(规则, 相对路径, 行号, 命中片段)。"""

    def __new__(cls, rule: str, rel: str, lineno: int, snippet: str):
        return super().__new__(cls, (rule, rel, lineno, snippet))

    rule = property(lambda self: self[0])
    rel = property(lambda self: self[1])
    lineno = property(lambda self: self[2])
    snippet = property(lambda self: self[3])

    def __str__(self) -> str:
        loc = f"{self.rel}:{self.lineno}" if self.lineno else self.rel
        return f"[{self.rule}] {loc} :: {self.snippet}"


def _wordlist(root: Path) -> dict:
    path = root / "tools" / "demo_gen" / "wordlist.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_files(root: Path):
    for rel_dir in SCAN_DIRS:
        base = root / rel_dir
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIR_PARTS for part in path.parts):
                continue
            if path.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            yield path
    for rel_file in SCAN_FILES:
        path = root / rel_file
        if path.is_file():
            yield path


def _read_lines(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _url_host(url: str) -> str:
    body = url.split("://", 1)[1]
    body = body.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in body:
        body = body.rsplit("@", 1)[1]
    return body.split(":", 1)[0].strip().lower()


def _host_allowed(host: str) -> bool:
    if not host:
        return True
    if host in ALLOWED_URL_HOSTS:
        return True
    return any(host.endswith(sfx) for sfx in ALLOWED_URL_HOST_SUFFIXES)


def _check_line_connection(rel: str, lineno: int, line: str) -> list[Violation]:
    out: list[Violation] = []
    # 本文件是检测正则与合成自测样本的定义处：样本里的 10.1.2.3 / 非白名单主机
    # 是「待检特征」而非真实连接串，整文件豁免（同 config_demo.py 的 host 豁免）。
    if rel.endswith(SELF_EXEMPT_FILE):
        return out
    if rel.endswith(HOST_RULE_EXEMPT_FILE) and HOST_RULE_EXEMPT_MARKER in line:
        return out
    for m in URL_RE.finditer(line):
        url = m.group(0)
        host = _url_host(url)
        if not _host_allowed(host):
            out.append(Violation("A.连接串", rel, lineno, f"非白名单主机 {host!r} ← {url}"))
    for m in PRIVATE_IP_RE.finditer(line):
        out.append(Violation("A.连接串", rel, lineno, f"RFC1918 私网地址 {m.group(1)!r}"))
    return out


def _check_line_credentials(rel: str, lineno: int, line: str) -> list[Violation]:
    out: list[Violation] = []
    # 同上：本文件的 sk-… 自测样本是「待检特征」而非真实密钥。
    if rel.endswith(SELF_EXEMPT_FILE):
        return out
    for pattern, why in HIGH_ENTROPY_CRED:
        for m in pattern.finditer(line):
            out.append(Violation("B.凭据", rel, lineno, f"{why}: {m.group(0)[:24]}…"))
    for m in CRED_URL_RE.finditer(line):
        pwd = m.group(2)
        if pwd not in ALLOWED_SECRET_VALUES and not PLACEHOLDER_RE.match(pwd):
            out.append(Violation("B.凭据", rel, lineno, f"URL 内嵌非演示密码 ← {m.group(0)}"))
    for m in SECRET_ASSIGN_RE.finditer(line):
        name, value = m.group(1), m.group(2).strip().strip("'\"")
        if not value:
            continue
        if value in ALLOWED_SECRET_VALUES or PLACEHOLDER_RE.match(value):
            continue
        if value.startswith("${") or value.startswith("$"):
            continue  # 由环境变量注入，非明文
        out.append(Violation("B.凭据", rel, lineno, f"{name} 非演示取值: {value[:24]}"))
    return out


def _check_line_enterprise(rel: str, lineno: int, line: str) -> list[Violation]:
    out: list[Violation] = []
    # 本文件是黑名单字面量（跨多行）与合成自测样本的定义处：整文件豁免 C 类检查。
    # 只豁免「定义处」而非放松规则——把定义本身当命中会让脚本永远无法全绿。
    if rel.endswith(SELF_EXEMPT_FILE):
        return out
    for brand in REAL_BRAND_BLACKLIST:
        if brand in line:
            out.append(Violation("C.企业名词", rel, lineno, f"真实品牌 {brand!r}"))
    for m in PHONE_CN_RE.finditer(line):
        if not m.group(0).startswith(ALLOWED_PHONE_PREFIXES):
            out.append(Violation("C.企业名词", rel, lineno, f"真实手机号段 {m.group(0)!r}"))
    for m in PHONE_400_RE.finditer(line):
        out.append(Violation("C.企业名词", rel, lineno, f"400 客服号 {m.group(0)!r}"))
    for m in PHONE_LANDLINE_RE.finditer(line):
        out.append(Violation("C.企业名词", rel, lineno, f"座机号 {m.group(0)!r}"))
    return out


def is_out_of_scope(rel: str) -> bool:
    """是否落在「遗留·超范围」清单（旧演示 UI，经人裁决不计入本节点验收）。"""
    return rel in OUT_OF_SCOPE_PATHS


def _check_data_name_fields(root: Path, allowed_names: set[str]) -> list[Violation]:
    out: list[Violation] = []
    for filename, field in DATA_NAME_CHECKS:
        path = root / "data" / "demo_storyline" / filename
        if not path.exists():
            continue
        for lineno, line in enumerate(_read_lines(path), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                out.append(Violation("C.企业名词", f"data/demo_storyline/{filename}", lineno,
                                     "无法解析的 JSONL 行"))
                continue
            value = row.get(field)
            if value is not None and value not in allowed_names:
                out.append(Violation("C.企业名词", f"data/demo_storyline/{filename}", lineno,
                                     f"{field}={value!r} 不在虚构词表内"))
    return out


def scan(root: Path) -> list[Violation]:
    """扫描演示实例配置与代码，返回全部违规证据（空列表 = 全绿）。"""
    root = Path(root).resolve()
    out: list[Violation] = []
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in (".env",):
            continue
        for lineno, line in enumerate(_read_lines(path), start=1):
            out.extend(_check_line_connection(rel, lineno, line))
            out.extend(_check_line_credentials(rel, lineno, line))
            out.extend(_check_line_enterprise(rel, lineno, line))
    wl = _wordlist(root)
    allowed_names = set(wl.get("companies", [])) | set(wl.get("suppliers", [])) | set(wl.get("equipment", []))
    out.extend(_check_data_name_fields(root, allowed_names))
    return out


# ---------------------------------------------------------------- 自测样本（合成坏内容）

#: 三类检测器各喂一条合成坏内容，要求逐条都能报出——防「永远全绿的空壳脚本」。
SELF_TEST_SAMPLES = (
    ("A.连接串", "db = 'mysql://root:pw@10.1.2.3:3306/main'", _check_line_connection),
    ("B.凭据", "API_KEY = 'sk-abcdefghijklmnopqrstuvwxyz0123456789'", _check_line_credentials),
    ("C.企业名词", "客户：富士康精密 与 中芯国际 对接", _check_line_enterprise),
)


def run_self_test() -> int:
    """用合成坏内容验证三类检测器都会报出；任一没报出即失败（退出码 1）。"""
    failed = False
    for rule, sample, checker in SELF_TEST_SAMPLES:
        hits = checker("__self_test__.txt", 1, sample)
        if hits:
            print(f"[PASS] 自测 {rule}：报出 {len(hits)} 处（预期）")
        else:
            print(f"[FAIL] 自测 {rule}：未报出合成坏内容 {sample!r}")
            failed = True
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="演示实例隔离自检（T12）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--self-test", action="store_true",
                    help="用合成坏内容验证 A/B/C 三类检测器都能报出（不扫工作树）")
    args = ap.parse_args()
    if args.self_test:
        return run_self_test()

    violations = scan(Path(args.root))
    in_scope = [v for v in violations if not is_out_of_scope(v.rel)]
    legacy = [v for v in violations if is_out_of_scope(v.rel)]

    if legacy:
        by_file: dict[str, int] = {}
        for v in legacy:
            by_file[v.rel] = by_file.get(v.rel, 0) + 1
        summary = "、".join(f"{rel} {count} 条" for rel, count in sorted(by_file.items()))
        print(f"[遗留·超范围] 旧演示 UI 真实品牌命中 {len(legacy)} 条（{summary}）——"
              "非生态链演示包，已列入 T12 特殊备注，待独立节点整改，不计入本节点验收。")
        for v in legacy:
            print(f"  - {v}")

    if not in_scope:
        print("[PASS] A.连接串 / B.凭据 / C.企业名词 —— 演示实例三项范围内全绿")
        return 0

    print(f"[FAIL] 隔离自检发现 {len(in_scope)} 条范围内违规：")
    for v in in_scope:
        print(f"  - {v}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
