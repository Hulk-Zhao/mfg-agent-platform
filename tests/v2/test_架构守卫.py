"""架构守卫：持久化后端 / 配置解耦 / 健康探针 / 导航收敛。

这些用例守护的是**架构体检报告中 P0-1、P0-3、P1-1、P1-2 四条硬伤**的修复，
不是业务功能。删改前请先读对应模块的 docstring——每一条都对应一次真实事故：

1. ``WorkflowEngine`` 拿到 MySQL URL 时，旧实现会把整条 URL 当文件路径交给
   ``sqlite3.connect``：Linux 静默建出怪文件（数据分裂），Windows 抛 OSError。
2. ``ensure_platform_schema`` 用 ``"sqlite" in db_url`` 判方言，裸路径被误判成
   MySQL → SQLAlchemy 报 ``Could not parse SQLAlchemy URL``。
3. ``WORKFLOW_DB`` 默认继承 ``DB_URL``，切 MySQL 时连带把治理库带偏。
4. ``/api/v2/health`` 硬编码 ok，依赖全挂仍报健康 → 前端绿灯误导。
5. 31 个岗位导航完全同构，一线岗位与总经理菜单一致。

全部离线、无外网、无 LLM 依赖。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mfg_agents.common import config as config_mod
from mfg_agents.common.config import settings
from mfg_agents.common.platform_schema import is_sqlite_target, sqlite_path_from_url
from mfg_agents.v2.services import health as health_mod
from mfg_agents.v2.services.workflow import (
    WorkflowBackendError,
    WorkflowEngine,
    assert_sqlite_backed,
)

UI_DIR = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"
REPO_ROOT = Path(__file__).resolve().parents[2]


# ============================================================
# 1. 治理写路径只接受 SQLite：非 SQLite 必须 fail-fast
# ============================================================

@pytest.mark.parametrize("bad_url", [
    "mysql+pymysql://root:demo123@localhost:3306/mfg_agent",
    "postgresql://user:pw@localhost/db",
    "oracle+cx_oracle://u:p@h/xe",
    "MYSQL+PYMYSQL://root:pw@h:3306/db",      # 大小写不应绕过校验
])
def test_workflow_engine_rejects_non_sqlite(bad_url):
    """把 MySQL URL 静默当文件路径会分裂数据，必须启动即失败。"""
    with pytest.raises(WorkflowBackendError) as exc:
        WorkflowEngine(bad_url)
    # 报错必须可操作：指出替代方案，而不是只丢一句 NotImplemented
    assert "WORKFLOW_DB" in str(exc.value)


@pytest.mark.parametrize("good_url", [
    "sqlite:///:memory:",
    "sqlite:///{tmp}/platform.db",
    "{tmp}/bare.db",
    "{tmp}/sqlite-data/nested.db",            # 路径含 "sqlite" 字样不得被误判
])
def test_workflow_engine_accepts_sqlite_forms(good_url, tmp_path):
    """SQLite 的四种合法写法都必须可用（含裸路径与多层不存在的目录）。"""
    engine = WorkflowEngine(good_url.format(tmp=str(tmp_path).replace("\\", "/")))
    assert engine.db_path


def test_workflow_engine_creates_parent_dirs(tmp_path):
    """建表阶段就要建父目录——旧实现只在连接阶段建，导致启动即崩。"""
    target = tmp_path / "a" / "b" / "c" / "platform.db"
    assert not target.parent.exists()
    WorkflowEngine(f"sqlite:///{target.as_posix()}")
    assert target.exists()


def test_assert_sqlite_backed_accepts_bare_path():
    """裸路径没有协议头，按 SQLite 处理（不能因缺少 "sqlite" 字样被拒）。"""
    assert_sqlite_backed("/var/lib/mfg/platform.db")
    assert_sqlite_backed(":memory:")


# ============================================================
# 2. 方言判定：子串匹配的两类误判都要挡住
# ============================================================

@pytest.mark.parametrize("url,expected", [
    ("sqlite:///data/x.db", True),
    ("sqlite:///:memory:", True),
    ("SQLite:///data/x.db", True),
    ("/var/lib/mfg/platform.db", True),          # 裸路径
    ("C:/data/platform.db", True),               # Windows 裸路径
    (":memory:", True),
    ("/opt/sqlite-data/x.db", True),             # 含 "sqlite" 字样但仍是裸路径
    ("mysql+pymysql://u:p@h/db", False),
    ("postgresql://u:p@h/db", False),
])
def test_is_sqlite_target(url, expected):
    """旧实现用 ``"sqlite" in db_url``：裸路径被误判为 MySQL，含该字样的路径被误判为 SQLite。"""
    assert is_sqlite_target(url) is expected


def test_sqlite_path_from_url_keeps_bare_path():
    assert sqlite_path_from_url("/tmp/x.db") == "/tmp/x.db"
    # sqlite:/// 前缀（10 字符）被剥离，故四斜杠形式得到绝对路径
    assert sqlite_path_from_url("sqlite:////tmp/x.db") == "/tmp/x.db"
    assert sqlite_path_from_url("sqlite:///:memory:") == ":memory:"


# ============================================================
# 3. WORKFLOW_DB 不继承非 SQLite 的 DB_URL
# ============================================================

def test_workflow_db_does_not_inherit_mysql(monkeypatch):
    """DB_URL 切 MySQL 时，治理库必须回落 SQLite，而不是把 URL 当路径。"""
    monkeypatch.delenv("WORKFLOW_DB", raising=False)
    resolved = config_mod._resolve_workflow_db(
        "mysql+pymysql://root:demo123@localhost:3307/mfg_agent")
    assert is_sqlite_target(resolved), f"治理库仍被带偏：{resolved}"


def test_workflow_db_follows_sqlite_db_url(monkeypatch):
    """DB_URL 本身是 SQLite 时沿用同一库（保持既有"本地单库"行为）。"""
    monkeypatch.delenv("WORKFLOW_DB", raising=False)
    assert config_mod._resolve_workflow_db("sqlite:///data/custom.db") == "sqlite:///data/custom.db"


def test_workflow_db_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("WORKFLOW_DB", "sqlite:///opt/gov.db")
    assert config_mod._resolve_workflow_db("mysql+pymysql://u:p@h/d") == "sqlite:///opt/gov.db"


def test_blank_env_falls_back_to_default(monkeypatch):
    """``.env`` 里写 ``DB_URL=`` 时 os.getenv 返回空串——空串会被 sqlite3 当成匿名临时库。"""
    monkeypatch.setenv("DB_URL", "")
    assert config_mod._env_or("DB_URL", "sqlite:///fallback.db") == "sqlite:///fallback.db"
    monkeypatch.setenv("DB_URL", "   ")
    assert config_mod._env_or("DB_URL", "sqlite:///fallback.db") == "sqlite:///fallback.db"
    monkeypatch.setenv("DB_URL", "sqlite:///real.db")
    assert config_mod._env_or("DB_URL", "sqlite:///fallback.db") == "sqlite:///real.db"


# ============================================================
# 4. 健康探针：结构化、不抛异常、能识别降级
# ============================================================

def _patch_all_healthy(monkeypatch) -> None:
    """把所有探针替换为"就绪"，使断言不受本机中间件启停影响（用例必须可脱离环境跑）。

    没有这层隔离时，"本机 Milvus 恰好没起"会让 offline/full 的断言随机失败——
    测试依赖环境状态是 CI 上最难查的一类 flaky。
    """
    ok = {
        "_probe_business_db": lambda: ("ok", "业务库就绪"),
        "_probe_workflow_db": lambda: ("ok", "治理库就绪"),
        "_probe_redis": lambda: ("ok", "缓存就绪"),
        "_probe_vector": lambda: ("ok", "向量库就绪"),
        "_probe_llm": lambda: ("ok", "LLM 就绪"),
    }
    for name, fn in ok.items():
        monkeypatch.setattr(health_mod, name, fn)


def test_health_report_shape(monkeypatch):
    _patch_all_healthy(monkeypatch)
    report = health_mod.build_health_report()
    assert report["status"] in {"ok", "degraded"}
    assert report["mode"] in {"full", "degraded", "offline"}
    assert set(report["checks"]) == {"business_db", "workflow_db", "redis", "vector", "llm"}
    for name, item in report["checks"].items():
        assert set(item) >= {"state", "detail", "ms"}, f"{name} 缺少字段"
        assert item["state"] in {"ok", "down", "disabled"}, f"{name} 状态非法"


def test_health_never_raises_even_if_every_probe_fails(monkeypatch):
    """健康检查自身不允许 500——任一探针抛异常都必须收敛为 down。"""
    def boom():
        raise RuntimeError("probe exploded")

    for name in ("_probe_business_db", "_probe_workflow_db",
                 "_probe_redis", "_probe_vector", "_probe_llm"):
        monkeypatch.setattr(health_mod, name, boom)

    report = health_mod.build_health_report()
    assert report["status"] == "degraded"
    assert set(report["degraded_components"]) == set(report["checks"])
    for item in report["checks"].values():
        assert item["state"] == "down"
        assert "probe exploded" in item["detail"]


def test_health_marks_degraded_when_component_down(monkeypatch):
    """单点依赖挂掉不能再报 ok——这正是旧实现最大的问题。"""
    _patch_all_healthy(monkeypatch)
    monkeypatch.setattr(health_mod, "_probe_vector",
                        lambda: ("down", "Milvus 不可达"))
    report = health_mod.build_health_report()
    assert report["status"] == "degraded"
    assert "vector" in report["degraded_components"]
    assert report["mode"] == "degraded"


def test_health_reports_offline_mode_without_llm(monkeypatch):
    """未配 LLM = README 支持的离线降级形态：status ok，但 mode 不能是 full。"""
    _patch_all_healthy(monkeypatch)
    monkeypatch.setattr(health_mod, "_probe_llm",
                        lambda: ("disabled", "未配置 LLM"))
    report = health_mod.build_health_report()
    assert report["status"] == "ok"
    assert report["mode"] == "offline"


def test_health_reports_full_mode_when_all_ready(monkeypatch):
    _patch_all_healthy(monkeypatch)
    report = health_mod.build_health_report()
    assert report["status"] == "ok"
    assert report["mode"] == "full"
    assert report["degraded_components"] == []


def test_health_endpoint_does_not_bootstrap_orchestrator(monkeypatch):
    """探针绝不允许触发编排器初始化（建库 + 加载 7 个场景 + 连向量库）。"""
    from mfg_agents.v2.demo import api as api_mod

    def forbidden():
        raise AssertionError("health 端点不得调用 get_orchestrator()")

    monkeypatch.setattr(api_mod, "get_orchestrator", forbidden)
    report = api_mod.health()
    assert report["status"] in {"ok", "degraded"}


# ============================================================
# 5. 导航按岗位收敛
# ============================================================

def _nav_items() -> list[dict]:
    """从 app.js 抽取 NAV_ITEMS 字面量（键无引号，无法用 json 解析）。

    只取 id 与 roles，够做结构断言；顺序与渲染无关紧要。
    """
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const NAV_ITEMS = \[(.*?)\n  \];", src, re.S)
    assert block, "未找到 NAV_ITEMS 定义"
    items = []
    for chunk in re.split(r"\n\s*\{ id:", block.group(1)):
        if not chunk.strip():
            continue
        name = re.match(r'\s*"([a-z_]+)"', chunk)
        roles = re.search(r"roles:\s*\[([^\]]*)\]", chunk)
        items.append({
            "id": name.group(1) if name else None,
            "roles": re.findall(r'"([a-z_]+)"', roles.group(1)) if roles else [],
        })
    return [i for i in items if i["id"]]


def _role_ids() -> list[str]:
    """岗位 id 列表（demo-data.js 的 uid 统一为 ``u-<roleId>``）。"""
    src = (UI_DIR / "demo-data.js").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'uid: "u-([a-z]+)"', src)))


def test_nav_role_ids_are_valid():
    """roles 里引用的岗位必须真实存在——拼错会导致该岗位菜单静默变少。"""
    valid = set(_role_ids())
    unknown = sorted({r for item in _nav_items() for r in item["roles"]} - valid)
    assert not unknown, f"NAV_ITEMS 引用了不存在的岗位：{unknown}"


def test_nav_is_not_uniform_across_roles():
    """31 个岗位不得看到完全相同的菜单（这正是修复前的问题）。"""
    items = _nav_items()
    roles = _role_ids()
    seen = set()
    for role in roles:
        visible = tuple(i["id"] for i in items
                        if not i["roles"] or role in i["roles"])
        seen.add(visible)
    assert len(seen) > 1, "所有岗位菜单完全相同，导航收敛未生效"


def test_every_role_keeps_core_screens():
    """收敛不能把菜单收敛空：每个岗位至少保留 3 项，且工作台 / 问数全岗可见。

    ``hitl`` **刻意不再算全岗核心屏**。它原先在这里的原因是"当时它确实全员可见"，
    而不是设计原则——实测下来行政人事点进去只有一句"本岗位不参与治理审批"，
    给一个注定空转的入口比不给更糟。它的权威判据是 HITL 槽位权限，
    由 ``test_待办可见性.py::test_hitl_nav_matches_governance_permissions``
    与 ``permissions.yaml`` 交叉校验；这里只守住"别又变成全员可见"。
    """
    items = _nav_items()
    universal = {i["id"] for i in items if not i["roles"]}
    assert {"dashboard", "chat"} <= universal, "全员核心屏不应被限制"

    hitl = next(i for i in items if i["id"] == "hitl")
    assert hitl["roles"], "审批确认中心必须声明 roles，否则退化成全员可见"
    assert len(hitl["roles"]) < len(_role_ids()), "审批确认中心不该全员可见"

    for role in _role_ids():
        visible = {i["id"] for i in items if not i["roles"] or role in i["roles"]}
        assert len(visible) >= 3, f"{role} 可见屏过少（{len(visible)}）"


def test_operational_boards_are_restricted():
    """运营看板必须带岗位约束；否则又退化成"全员可见"。"""
    items = {i["id"]: i for i in _nav_items()}
    for board in ("orders", "mfgboard", "spc", "reports"):
        assert items[board]["roles"], f"{board} 未声明 roles，等于全员可见"

    # HR 不应看到 SPC 过程能力看板（架构体检报告 P0-2 的合规质疑点）
    assert "hr" not in items["spc"]["roles"]
    # 总经理必须能看到全部运营看板
    for board in ("orders", "mfgboard", "spc", "reports"):
        assert "gm" in items[board]["roles"]


def test_dashboard_kpi_cards_are_labelled_as_demo():
    """首页指标卡必须在 UI 上标注为演示配置。

    这批卡来自 ``DEMO.ROLE_KPIS``（31 岗位 × 3 张，硬编码展示配置），
    与真实链路是**两套口径**：OTD 演示配置写 92.4%（为了好看），
    而 ``/dashboard/orders`` 按 seed 数据真算出来是 71.4%。

    为什么不"顺便接上真实值"：93 张卡里只有 16 张的 ``drill.kpi`` 能对上真实看板
    指标（gm 1/3、qd 0/3、wh 0/3），接一半会让首页真假混杂——比全假更难向用户解释。
    要治本得重写 93 张卡的指标定义，那是产品决策，不是工程问题。

    所以这里守的是"**必须说清楚**"：用户点下钻看到与首页卡片不一致的数字时，
    界面上有说明告诉他这两者不是一个口径。
    """
    src = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    start = src.index("function render_dashboard")
    block = src[start:start + 4000]
    assert 'UI.demoFlag("指标卡为岗位视角演示配置' in block, (
        "首页指标卡未标注演示来源——用户会把它当成真实数据")


def test_quick_entries_follow_nav_visibility():
    """首页里的跨屏入口必须跟侧边栏用同一套岗位可见性。

    行政人事的侧边栏已经没有「订单履约看板」和「审批确认中心」，
    但首页快捷入口曾把这两个照原样渲染出来——菜单里没有、首页却能点，
    点下去不是 403 就是被静默回落到别的屏。
    """
    src = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    start = src.index("function quickEntries(ctx)")
    assert "visibleNav" in src[start:start + 900], "快捷入口未按岗位可见性过滤"

    head = src.index('人工在环待确认')
    assert "hitlVisible" in src[head:head + 500], (
        "待办中心的「前往审批中心」未按可见性隐藏")


def test_kpi_drill_entry_respects_visibility():
    """KPI 卡的「下钻分解」入口必须先判断目标屏对该岗位是否可见。

    KPI 卡按岗位配置、屏按权限收敛，两边不联动：行政人事的首张卡带
    ``drill: {screen:"orders"}``，而 orders 屏它根本看不到——点下去只会
    吃一个 403 再跳「无权查看」。明知不可点还摆个可点的样子，比不给更糟。
    """
    src = (UI_DIR / "screens.js").read_text(encoding="utf-8")
    start = src.index("function dashKpiCard(")
    block = src[start:start + 600]
    assert "kpiDrillVisible(ctx" in block, "KPI 卡未按可见性判定下钻入口"
    # 映射表必须把 manufacturing / quality 指到 mfgboard（它们是 Tab 不是独立屏）
    assert "SCREEN_TO_NAV" in src, "缺少下钻 screen → 屏 id 的映射"
    m = re.search(r"SCREEN_TO_NAV\s*=\s*\{(.*?)\}", src, re.S)
    assert m, "SCREEN_TO_NAV 解析失败"
    body = m.group(1)
    assert "manufacturing: \"mfgboard\"" in body
    assert "quality: \"mfgboard\"" in body


def test_render_nav_uses_visible_nav():
    """渲染入口必须走 visibleNav()——防止有人改回 NAV_ITEMS.forEach 绕过收敛。"""
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    render = re.search(r"function renderNav\(\) \{(.*?)\n  \}", src, re.S)
    assert render, "未找到 renderNav 实现"
    body = render.group(1)
    assert "visibleNav()" in body, "renderNav 未使用岗位过滤"
    assert "NAV_ITEMS.forEach" not in body, "renderNav 仍在遍历全量导航"


def test_frontend_health_probe_accepts_degraded():
    """前端探针必须接受 degraded，否则后端一降级就被误判为离线并退回演示数据。"""
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    assert 'data.status === "degraded"' in src, "探针未接受 degraded 状态"


def test_nav_check_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    proc = subprocess.run([node, "--check", str(UI_DIR / "app.js")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"app.js 语法错误：{proc.stderr}"


# ============================================================
# 5b. 案例屏折叠态必须进 state（2026-10-20「案例单击后就自动收缩」事故护栏）
#
# 事故链路：`<details>` 的开合只活在 DOM 上，而 render 每次整块重写 innerHTML，
# open 只按 S.chatView / 硬编码默认值计算 ⇒ 用户手动展开的旧轮，以及卡内的
# 「展开 N 个片段 / 相关案例 / 技术详情」，只要点一下案例（openDetail → repaint）
# 就全部被重画成收起，现场看到的就是「案例单击后自动收缩」。
# 修法：折叠态落 S.folds，每处 `<details>` 用 fold(S, key, 默认值) 渲染，
# 并在捕获阶段接 toggle 事件回写——三件事缺一件，事故就复现。
# ============================================================

def test_case_screen_details_carry_state_key():
    """案例屏每处 `<details>` 都必须挂 fold(S, ...)，否则 repaint 会收回用户展开的内容。"""
    src = (UI_DIR / "case-screen.js").read_text(encoding="utf-8")
    real, offenders = 0, []
    for chunk in src.split("<details")[1:]:
        head = chunk.split(">", 1)[0]
        if 'class="case-' not in head:     # 注释/文档里提到的 `<details>` 不算真实标签
            continue
        real += 1
        if "fold(S" not in head:
            offenders.append(head[:90].strip())
    assert real >= 8, f"只认出 {real} 处真实 <details>，断言失去意义"
    assert not offenders, (
        "这些 <details> 没挂 fold(S, key, 默认值)：repaint 后会被收回 -> " + repr(offenders))


def test_case_screen_writes_fold_state_back():
    """toggle 事件必须在捕获阶段监听并回写 S.folds（toggle 不冒泡，写不回就白改）。"""
    src = (UI_DIR / "case-screen.js").read_text(encoding="utf-8")
    m = re.search(r'addEventListener\("toggle".*?\}, true\);', src, re.S)
    assert m, "未找到捕获阶段的 toggle 监听：折叠态无法回写"
    assert ".folds" in m.group(0), "toggle 回调未写入 state.folds：下一次 repaint 仍按默认值重画"
    assert "data-fold" in m.group(0), "toggle 回调必须按 data-fold 认键，否则对不上渲染时的键"


def test_case_screen_chat_is_chronological_bubbles():
    """案例问答已改为制度助手式大对话框（2026-10-20）：时间序气泡 + 底部输入条。

    旧形态（新提问置顶、逐轮 <details> 折叠）已废弃——大对话框要求
    「问-答」按时间序滚动感（最新在底部），检索细节改为每轮回答下方
    默认收起的「智能体检索」抽屉（class="case-agent"，仍走 fold(S, ...)）。
    """
    src = (UI_DIR / "case-screen.js").read_text(encoding="utf-8")
    # 气泡布局：用户右气泡 + 智能体左气泡必须存在
    assert "case-msg-user" in src and "case-msg-bot" in src, "聊天必须使用气泡布局（case-msg-*）"
    # 旧的置顶折叠轮次不允许回归
    assert "case-chat-turn" not in src, "旧「置顶折叠轮次」已废弃，不得回归"
    # 大对话框形态标志：页签切换 + 底部固定输入条
    assert 'data-act="view-' in src, "缺少智能问答/案例检索页签"
    assert '"view-ask"' in src and '"view-search"' in src, "缺少 ask/search 视图切换动作"
    assert "case-inputbar" in src and 'id="case-question"' in src, "缺少底部输入条"
    # 智能体检索细节抽屉（本体增强链路摘要 + 按需展开）
    assert "case-agent" in src and "case-pipe-step" in src, "缺少智能体检索抽屉/管线摘要"


# ============================================================
# 6. POST 必须有超时（2026-10-18「思考中」事故护栏）
#
# 事故链路：kbqa 提问走 _post（无超时）→ 后端冷启动/Milvus 悬挂
# （MilvusClient 未设 timeout，pymilvus 默认无限等）→ fetch 永远等待 →
# 前端「思考中…」静止转圈 6 分钟，S.sending 锁死后续全部提问。
# 两层都必须有超时预算，缺一层事故就复现。
# ============================================================

def test_post_has_abort_timeout():
    """_post 必须带 AbortController——_get 一直有，_post 曾是漏的。"""
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    post = re.search(r"async _post\(path, body[^)]*\) \{(.*?)\n    \},", src, re.S)
    assert post, "未找到 _post 实现（签名是否改为带 timeoutMs？）"
    body = post.group(1)
    assert "AbortController" in body, "_post 无超时保护：悬挂的 POST 会永远转圈"
    assert "signal: ctrl.signal" in body, "_post 的 fetch 未挂 signal，超时形同虚设"


def test_kbqa_query_has_explicit_timeout_budget():
    """kbqaQuery 必须传显式超时：对齐后端最慢路径（rag=Milvus 15s+LLM 60s）。

    按调用点断言而非函数体正则——函数体含对象字面量 ``{ ... },``，
    非贪婪匹配会在 ``},`` 处提前截断（第一版断言就这么翻的车）。
    """
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    call = re.search(r'_post\(\s*"/api/v2/kbqa"[^;]*\)', src)
    assert call, "未找到 kbqaQuery 的 _post 调用（路径改了？同步更新本断言）"
    assert re.search(r",\s*\d{4,}\s*\)", call.group(0)), (
        f"kbqaQuery 未传超时预算：默认 12s 会误杀后端 rag 路径"
        f"（LLM timeout=60s）。实际调用：{call.group(0)!r}")


def test_kbqa_pending_has_visible_timer_and_retry():
    """pending 气泡必须显示等待秒数 + 失败可重试（用户能区分"在动"与"死了"）。"""
    src = (UI_DIR / "kb-screen.js").read_text(encoding="utf-8")
    assert "pendingSince" in src, "pending 消息无起始时间戳，无法显示等待秒数"
    assert "思考中… ${secs}s" in src, "pending 气泡未渲染等待秒数"
    assert 'err.name === "AbortError"' in src, "未区分超时错误：AbortError 会是英文原文"
    assert "kbqa-retry" in src, "失败气泡无重试入口，用户只能手打一遍问题"


def test_nav_check_kb_screen_js_syntax():
    """kb-screen.js 语法检查（此前只有 app.js 有守护，改过的文件没人验）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    proc = subprocess.run([node, "--check", str(UI_DIR / "kb-screen.js")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"kb-screen.js 语法错误：{proc.stderr}"


def test_milvus_client_has_timeout():
    """MilvusClient 必须设 timeout：pymilvus 默认 None=无限等，半死时挂住线程。"""
    src = (REPO_ROOT / "src" / "mfg_agents" / "common" / "rag_tools.py").read_text(
        encoding="utf-8")
    m = re.search(r"MilvusClient\(uri=self\._uri([^)]*)\)", src)
    assert m, "未找到 MilvusClient 构造（adapter 改名了？同步更新本断言）"
    assert "timeout=" in m.group(1), (
        "MilvusClient 未设 timeout：Milvus 半死时 search 永久挂起，"
        "叠加前端无超时即复现 2026-10-18「思考中」事故")


# ============================================================
# 7. 真实配置不得是 MySQL（守住 .env 现状，避免误改产生事故）
# ============================================================

def test_current_workflow_db_is_sqlite():
    """当前生效配置里治理库必须是 SQLite；若失败，说明 .env 把 DB_URL 配成了 MySQL
    但忘了设 WORKFLOW_DB——这正是本次修复要防的场景。"""
    assert is_sqlite_target(settings.WORKFLOW_DB), (
        f"WORKFLOW_DB 不是 SQLite：{settings.WORKFLOW_DB!r}（引擎将在启动时拒绝）")
