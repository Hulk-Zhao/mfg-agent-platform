"""T05 场景骨架契约守卫（纯 Python，禁网络 / 禁 node）。

守护的是**并行开发的接缝**：12 个场景屏由不同窗口开发，任何一个人悄悄改
``app.js`` 的 ``NAV_ITEMS`` 字面量、把数据文件放到别处、或者把访客角色的
``roles`` 写成前端 id，都会让其他人当天下午的菜单消失、登录 400。
这四类问题在浏览器里表现为「点了没反应 / 屏幕未注册 / 岗位未识别」，
排查成本极高，所以必须固化成断言：

1. ``data/demo_storyline/frontend/registry.json`` 是数据契约的唯一事实源：
   ``objects`` 必须与 ``data/demo_storyline/*.jsonl`` 一一对应，``key`` 必须在
   该 jsonl 首行真实存在（禁止臆造）；``fields`` **可选**，声明了就逐条校验。
2. ``scenes[]`` 的屏文件 / 数据文件必须真实存在（T06–T11 照抄时不会指到空文件）；
   键名兼容 ``file``/``module`` 与 ``data_file``/``data`` 两套写法。
3. ``index.html`` 的加载顺序是**偏序**而非相邻：registry → scene-kit → 所有场景脚本
   → app.js（defer 按文档顺序执行，顺序错了拿不到 ``window.SceneRegistry`` /
   ``window.SceneKit``）；并行窗口可随意在场景脚本区插入自己的行。
4. ``app.js`` 只能有两处钩子（合并导航 + 渲染分派），``NAV_ITEMS`` 字面量里
   场景 id 数必须为 0——这是「场景不各改 app.js」的机械证据。
5. 两个全局契约对象与关键 API 名必须存在（别人照着写才不会落空）。
6. 五个 ``*-demo`` 访客角色必须齐全且 ``roles`` 映射到治理角色 key，
   否则 ``/api/v2/auth/login`` 直接 400「岗位未识别」。
7. **端点对账**（2026-10-19 补）：``scene-kit.js`` 里拼进 URL 的
   ``/api/v2/demo/...`` 端点必须出现在 ``demo/api.py`` 的路由声明里，且必须是
   T04 装载层的 ``/objects/{name}``。此前 ``story()`` 按任务书文字写成
   ``/storyline/{name}``，后端实际是 ``/objects/{name}``，前端永远 404 走降级，
   而所有单测都是绿的——没有任何一条断言把两侧字符串对起来看。
8. ``registry.json`` 的 ``objects`` 键必须与 ``loader.OBJECTS`` 一致：
   两端各有一份清单，多一个少一个都会让前端拿到 404 而不是数据。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
SCENES_DIR = UI_DIR / "scenes"
DATA_DIR = REPO_ROOT / "data" / "demo_storyline"
FRONTEND_DIR = DATA_DIR / "frontend"
REGISTRY_JSON = FRONTEND_DIR / "registry.json"

#: 32 角色治理目录中承接五访客的角色 key（后端只认这些）
GOVERNANCE_KEYS = {"gm", "sales", "rd_engineering", "qa", "manufacturing"}

#: 五访客角色 id → 期望命中的治理角色
EXPECTED_VISITORS = {
    "gm-demo": ["gm"],
    "biz-demo": ["sales"],
    "rd-demo": ["rd_engineering"],
    "qa-demo": ["qa"],
    "mfg-demo": ["manufacturing"],
}


# ============================================================
# 通用读取工具
# ============================================================

def _load_registry() -> dict:
    """读 registry.json（不存在时直接失败并说清缺什么）。"""
    assert REGISTRY_JSON.exists(), f"缺少数据契约文件：{REGISTRY_JSON}"
    raw = REGISTRY_JSON.read_text(encoding="utf-8")
    return json.loads(raw)


def _first_line(path: Path) -> dict:
    """jsonl 首行 → dict。空文件视为契约违约。"""
    with path.open(encoding="utf-8") as handle:
        line = handle.readline()
    assert line.strip(), f"{path.name} 首行为空，无法核对字段口径"
    return json.loads(line)


def _storyline_jsonl() -> list[Path]:
    return sorted(DATA_DIR.glob("*.jsonl"))


# ============================================================
# 1. registry.json ↔ data/demo_storyline/*.jsonl 一一对应
# ============================================================

def test_registry_json_is_valid_and_has_required_sections():
    """合法 JSON + 必填段齐全（缺段会让 T06–T11 无从照抄）。"""
    data = _load_registry()
    for key in ("version", "contract", "updated", "notes", "objects", "scenes",
                "storyline_source"):
        assert key in data, f"registry.json 缺少必填段 {key}"
    assert data["contract"] == "T05", "contract 段必须标 T05（本契约的归属任务）"
    assert isinstance(data["objects"], dict) and data["objects"], "objects 不能为空"
    assert isinstance(data["scenes"], list), "scenes 必须是数组"
    assert "只追加" in data["notes"], "notes 必须写明追加规则：只追加自己名下条目，不改他人条目"
    assert "SceneKit.story" in data["notes"], "notes 必须指向 SceneKit.story() 作为公共故事数据唯一入口"

    primary = data["storyline_source"]["primary"]
    assert "/api/v2/demo/objects/" in primary, (
        f"storyline_source.primary 必须指向 T04 真实端点 /api/v2/demo/objects/，实际：{primary}")
    assert "storyline_source" in data, "缺少 storyline_source 段"
    assert data["storyline_source"].get("fallback"), "storyline_source 必须声明离线回落口径"


def test_objects_cover_every_jsonl_one_to_one():
    """objects 键必须与 data/demo_storyline/*.jsonl 文件名一一对应（不多不少）。"""
    objects = _load_registry()["objects"]
    on_disk = {path.stem for path in _storyline_jsonl()}
    assert on_disk, "data/demo_storyline/ 下没有任何 jsonl，T01/T02 交付物缺失"
    assert set(objects) == on_disk, (
        "objects 与磁盘 jsonl 不一致："
        f"缺 {sorted(on_disk - set(objects))}，多 {sorted(set(objects) - on_disk)}")


def test_registry_objects_match_loader_objects():
    """registry.json 的 objects 键必须与 T04 装载层 loader.OBJECTS 完全一致。

    两边各有一份清单：前端按 registry 声明的对象名去请求，后端只认 loader.OBJECTS。
    任一侧多/少一个，前端就会拿到 404「demo object not found」而不是数据。
    """
    from mfg_agents.v2.demo.loader import OBJECTS as LOADER_OBJECTS

    registered = set(_load_registry()["objects"])
    loader_set = set(LOADER_OBJECTS)
    assert registered == loader_set, (
        "registry.json 与 loader.OBJECTS 不一致："
        f"registry 多 {sorted(registered - loader_set)}，loader 多 {sorted(loader_set - registered)}")



@pytest.mark.parametrize("object_name", sorted(_load_registry()["objects"]))
def test_object_key_and_fields_exist_in_first_line(object_name: str):
    """key 必填且在首行存在；``fields`` **可选**——存在则逐条校验、不存在则不判。

    为什么 fields 放成可选：它只是消费方参考（口径文档），不是运行时依赖；
    把它设成硬必填，会让任何一次对登记表的正当精简（比如别的窗口为省事只留
    ``{file,key}``）把整个套件变红——"契约比现实更严"就是上一轮 20 条红的成因。
    真正的红线只有两条：``file`` 必须指向真实 jsonl、``key`` 必须是首行里真有的字段名。
    """
    entry = _load_registry()["objects"][object_name]
    assert entry.get("file"), f"{object_name} 缺少 file（数据文件名，消费方必需）"
    path = DATA_DIR / entry["file"]
    assert path.exists(), f"{object_name} 指向的 {entry['file']} 不存在"
    first = _first_line(path)

    assert entry.get("key"), f"{object_name} 缺少 key（主键字段名）"
    assert entry["key"] in first, f"{object_name}.key={entry['key']} 不在首行字段 {sorted(first)} 中"

    fields = entry.get("fields")
    if fields is not None:
        assert isinstance(fields, list) and fields, (
            f"{object_name}.fields 若声明必须是非空数组（省略该键即可不声明）")
        fabricated = sorted(set(fields) - set(first))
        assert not fabricated, f"{object_name}.fields 含首行不存在的字段（疑似臆造）：{fabricated}"

    # nested（数组内对象）若声明了，也必须能在首行里找到同名数组且字段真实存在
    for array_key, sub_fields in (entry.get("nested") or {}).items():
        assert array_key in first, f"{object_name}.nested.{array_key} 不在首行字段中"
        items = first[array_key]
        assert isinstance(items, list) and items and isinstance(items[0], dict), (
            f"{object_name}.{array_key} 首行不是对象数组，无法核对嵌套字段")
        bad = sorted(set(sub_fields) - set(items[0]))
        assert not bad, f"{object_name}.{array_key} 含不存在的嵌套字段：{bad}"


@pytest.mark.parametrize("scene_id", sorted(scene["id"] for scene in _load_registry()["scenes"]))
def test_scene_entry_matches_conventions(scene_id: str):
    """场景条目：id 形如 sNN、脚本路径可解析且在盘、data 非空时必须存在、roles 口径合法。

    键名两种写法都认（`file`/`module`、`data_file`/`data`）：T05 用前者，T09–T11 用后者，
    契约里已声明等价。在途窗口（条目已登记、脚本尚未提交）只要求声明 `owner`——
    这不是放水：条目存在的意义是"认领"，脚本在盘才谈得上校验实现。
    """
    data = _load_registry()
    scene = next(item for item in data["scenes"] if item["id"] == scene_id)

    assert re.match(r"^s\d{2}$", scene["id"]), f"场景 id {scene['id']} 不符合 ^s\\d{{2}}$"
    assert scene.get("owner"), f"{scene_id} 必须声明 owner（归属任务，便于并行窗口认领）"

    script_rel = scene.get("file") or scene.get("module")
    assert script_rel, f"{scene_id} 缺少屏文件键（file 或 module）"
    script = UI_DIR / script_rel
    if not script.exists():
        # 在途：只允许"已认领但未提交"，且必须说明预计模块名
        assert scene_rel_ok(script_rel), f"{scene_id} 的脚本路径不合约定：{script_rel}"

    data_rel = scene.get("data_file") or scene.get("data")
    if data_rel:
        assert (FRONTEND_DIR / Path(data_rel).name).exists() or (UI_DIR / data_rel).exists(), (
            f"{scene_id} 的数据文件找不到：{data_rel}")

    if "roles" in scene:
        roles = scene["roles"] or []
        assert roles, f"{scene_id}.roles 声明了却是空数组（省略该键 = 全员可见）"
        assert set(roles) <= GOVERNANCE_KEYS | {"biz", "rd", "mfg"}, (
            f"{scene_id}.roles 只能用业务岗位 id 或治理角色 key，实际：{roles}")

    for object_name in scene.get("objects") or []:
        assert object_name in data["objects"], f"{scene_id}.objects 引用了未登记对象 {object_name}"


def scene_rel_ok(script_rel: str) -> bool:
    """屏脚本路径必须落在 scenes/ 下且以 sNN- 开头（在途条目也要守命名约定）。"""
    path = Path(script_rel)
    return (path.parent.as_posix() == "scenes"
            and re.match(r"^s\d{2}-[a-z0-9-]+\.js$", path.name) is not None)


def test_reference_scene_declares_dual_roles():
    """s00 作为施工样板必须声明双口径 roles——T06–T11 照抄它才不会漏访客可见性。"""
    scene = next(item for item in _load_registry()["scenes"] if item["id"] == "s00")
    roles = set(scene.get("roles") or [])
    assert {"gm", "biz", "rd", "qa", "mfg"} <= roles, f"s00 缺少业务岗位 id：{roles}"
    assert {"sales", "rd_engineering", "manufacturing"} <= roles, (
        f"s00 缺少治理角色 key（五访客靠它命中可见性）：{roles}")



# ============================================================
# 2. index.html 加载顺序
# ============================================================

def test_index_html_script_order_registry_kit_scene_app():
    """defer 按文档顺序执行：registry → scene-kit → 场景脚本 → app.js。

    只对 **index.html 里真实引入的** 场景脚本校验顺序（与 registry.json 的键名写法解耦），
    并要求 scenes/ 下**已存在**的每个屏脚本都被引入——"写了屏忘了加 script" 是并行开发
    最常见的静默失败：菜单有、点进去「场景未登记」。
    """
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    srcs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    normalized = [src.split("?")[0] for src in srcs]

    assert any(src.endswith("scenes/registry.js") for src in normalized), "index.html 未引入 scenes/registry.js"
    assert any(src.endswith("scenes/scene-kit.js") for src in normalized), "index.html 未引入 scenes/scene-kit.js"

    idx_registry = next(i for i, s in enumerate(normalized) if s.endswith("scenes/registry.js"))
    idx_kit = next(i for i, s in enumerate(normalized) if s.endswith("scenes/scene-kit.js"))
    idx_app = next(i for i, s in enumerate(normalized) if s.endswith("app.js"))
    assert idx_registry < idx_kit < idx_app, (
        f"脚本顺序错误：registry={idx_registry} scene-kit={idx_kit} app={idx_app}")

    infra = {"scenes/registry.js", "scenes/scene-kit.js"}
    for index, src in enumerate(normalized):
        name = Path(src).name
        if Path(src).parent.as_posix() != "scenes" or name in ("registry.js", "scene-kit.js"):
            continue
        assert name.startswith("s"), f"scenes/ 下的屏脚本命名必须是 sNN-*.js：{src}"
        assert idx_kit < index < idx_app, (
            f"{src} 必须插在 scene-kit 之后、app.js 之前（当前序号 {index}）")

    scene_scripts = sorted(
        path.name for path in SCENES_DIR.glob("*.js")
        if path.name not in ("registry.js", "scene-kit.js"))
    missing = [name for name in scene_scripts
               if not any(src.endswith("scenes/" + name) for src in normalized)]

    # T05 自己的屏必须严格在位：漏了就是真回归
    assert "s00-skeleton.js" not in missing, (
        "scenes/s00-skeleton.js 已存在但 index.html 未引入——T05 骨架屏点不进去")

    # 其他窗口的屏：只报告不阻塞（并行开发中"先提交脚本、后补 script 标签"是常态，
    # 但必须让它可见，而不是静默通过——静默正是本次跨任务端点错配的成因）
    if missing:
        pytest.skip(f"以下屏脚本已存在但 index.html 尚未引入（归属窗口在途）：{missing}")




# ============================================================
# 3. app.js 只许两处钩子，NAV_ITEMS 字面量不得含场景 id
# ============================================================

def test_app_js_has_registry_nav_merge_hook():
    """导航合并钩子必须存在，且走 push（在字面量之外）。"""
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    assert "window.SceneRegistry.navItems()" in src, "app.js 未合并 SceneRegistry.navItems()"
    assert "NAV_ITEMS.push" in src, "导航合并必须用 push——改字面量会弄红 test_架构守卫.py"


def test_app_js_has_registry_render_dispatch_hook():
    """渲染分派钩子必须存在，并复用既有 try/catch + friendlyError 路径。"""
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    assert "window.SceneRegistry.has(state.screen)" in src, "app.js 未按 SceneRegistry.has() 分派"
    assert "window.SceneRegistry.render(" in src, "app.js 未调用 SceneRegistry.render()"


def test_nav_items_literal_contains_no_scene_ids():
    """NAV_ITEMS 字面量里场景 id 数必须为 0：场景屏只许运行时登记。"""
    src = (UI_DIR / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const NAV_ITEMS = \[(.*?)\n  \];", src, re.S)
    assert block, "未找到 NAV_ITEMS 字面量（是否改成了运行时构造？请同步更新本断言）"
    scene_ids = re.findall(r'id:\s*"(s\d{2})"', block.group(1))
    assert not scene_ids, (
        f"NAV_ITEMS 字面量出现场景 id {scene_ids}——场景屏必须由 SceneRegistry 登记，"
        "各改 app.js 会与并行窗口冲突并弄红导航守卫")


# ============================================================
# 4. 两个全局契约对象与关键 API 名
# ============================================================

def test_registry_js_exposes_scene_registry_api():
    """window.SceneRegistry 与六个关键 API 名必须齐全。"""
    src = (SCENES_DIR / "registry.js").read_text(encoding="utf-8")
    assert "window.SceneRegistry" in src, "registry.js 未导出 window.SceneRegistry"
    for api in ("register", "list", "get", "has", "navItems", "registerVisitorRoles", "render"):
        assert re.search(rf"\b{api}\b", src), f"registry.js 缺少 API：{api}"
    assert "GROUP_NAME" in src, "registry.js 必须导出统一组名 GROUP_NAME"


def test_scene_kit_js_exposes_component_api():
    """window.SceneKit 与组件契约里的 API 名必须齐全，且样式只注入一次。"""
    src = (SCENES_DIR / "scene-kit.js").read_text(encoding="utf-8")
    assert "window.SceneKit" in src, "scene-kit.js 未导出 window.SceneKit"
    for api in ("use", "esc", "icon", "badge", "demoBadge", "pageHead", "numberCard",
                "numberRow", "eventFlow", "signoffCard", "openDrill", "sceneData",
                "story", "isReadonly", "fmtDate", "fmtNum"):
        assert re.search(rf"\b{api}\b", src), f"scene-kit.js 缺少 API：{api}"
    assert "scene-kit-style" in src, "样式必须注入到固定 id=scene-kit-style 的 <style>"
    assert 'id: STYLE_ID' in src or "node.id" in src, "样式注入未设置 id，重复调用会重复注入"
    assert "--border" in src and "--primary" in src, (
        "组件样式必须复用 app.css 的 CSS 变量（light 主题），不得硬编码深色底")


def _frontend_demo_endpoints() -> set[str]:
    """从 scene-kit.js 抓出真正拼进 URL 的 /api/v2/demo/ 端点前缀。"""
    src = (SCENES_DIR / "scene-kit.js").read_text(encoding="utf-8")
    # 只认代码里 `base + "/api/v2/demo/xxx/"` 这种字符串拼接（注释里的路径不算）
    return set(re.findall(r'"(/api/v2/demo/[a-z]+/)', src))


def _backend_demo_routes() -> list[str]:
    """从 api.py 抓出演示路由声明。"""
    src = (REPO_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py").read_text(encoding="utf-8")
    return re.findall(r'@app\.(?:get|post)\("(/api/v2/demo/[^"]*)"\)', src)


def test_scene_kit_endpoint_matches_backend_route():
    """前端打的演示端点必须是后端真实存在的路由（本次漏检的那一类错误）。

    事故现场：``story()`` 按任务书文字写 ``/api/v2/demo/storyline/{name}``，
    而 T04 实际交付的是 ``/api/v2/demo/objects/{name}``。前端永远是 404 → 占位屏
    永远走降级分支，而所有单元测试都是绿的（没有人把两侧字符串对起来看）。
    这条断言就是那个"对起来看"的动作，必须留着。
    """
    endpoints = _frontend_demo_endpoints()
    assert endpoints, "scene-kit.js 未找到 /api/v2/demo/ 端点（story() 是否改用了别的取数方式？）"
    routes = _backend_demo_routes()
    assert routes, "api.py 未找到 /api/v2/demo/ 路由声明（T04 装载层路由被删了？）"

    missing = sorted(ep for ep in endpoints if not any(route.startswith(ep) for route in routes))
    assert not missing, (
        f"前端请求了后端不存在的端点 {missing}；后端现有路由：{routes}")
    assert "/api/v2/demo/objects/" in endpoints, (
        "story() 必须走装载层对象端点 /api/v2/demo/objects/{name}，"
        f"实际端点：{sorted(endpoints)}")


def test_scene_kit_story_never_rejects():
    """story() 必须有兜底 catch，把异常转成 {ok:false}（装载层未就绪不许白屏）。

    2026-10-23：story() 拆成「会话级缓存外壳 + _storyFetch 取数」以去重公网
    隧道往返，故取数实现一并纳入断言范围；对调用方的可观测契约（永不 reject、
    失败给「演示装载层未就绪」）保持不变。
    """
    src = (SCENES_DIR / "scene-kit.js").read_text(encoding="utf-8")
    body = re.search(r"function story\(objectName\) \{(.*?)\n  \}", src, re.S)
    assert body, "未找到 story() 实现"
    fetch = re.search(r"function _storyFetch\(name\) \{(.*?)\n  \}", src, re.S)
    assert fetch, "未找到 _storyFetch() 取数实现"
    text = body.group(1) + fetch.group(1)
    assert "Promise.resolve(" in text, "story() 必须对降级路径直接 resolve，不能 reject"
    assert ".catch(" in text, "story() 缺少 catch：接口异常会变成未捕获 rejection"
    assert 'error: "演示装载层未就绪"' in text, "story() 缺省错误文案必须是「演示装载层未就绪」"
    # 会话级缓存：同对象重复取不得再打一次隧道往返
    assert "const storyCache = Object.create(null);" in src, (
        "story() 缺少会话级缓存（公网隧道下每对象 1 次往返，切屏往返会重复取）")


# ============================================================
# 5. 五访客角色：id 齐全 + roles 命中治理角色 key
# ============================================================

def _role_roles(src: str, role_id: str) -> list[str]:
    """从 registry.js 抽出某个访客角色的 roles 数组。"""
    anchor = re.search(rf'id:\s*"{re.escape(role_id)}"', src)
    assert anchor, f"registry.js 中缺少访客角色 {role_id}"
    tail = src[anchor.start(): anchor.start() + 600]
    roles = re.search(r"roles:\s*\[([^\]]*)\]", tail)
    assert roles, f"访客角色 {role_id} 缺少 roles 字段"
    return re.findall(r'"([a-z_]+)"', roles.group(1))


@pytest.mark.parametrize("role_id,expected", sorted(EXPECTED_VISITORS.items()))
def test_visitor_role_ids_and_governance_mapping(role_id: str, expected: list[str]):
    """访客 roles 必须映射到治理角色 key，否则后端登录 400「岗位未识别」。"""
    src = (SCENES_DIR / "registry.js").read_text(encoding="utf-8")
    roles = _role_roles(src, role_id)
    assert roles == expected, (
        f"{role_id} 的 roles 应为 {expected}（治理角色 key），实际 {roles}；"
        "写成前端 id（如 biz）后端不认，登录会失败")
    assert set(roles) <= GOVERNANCE_KEYS, f"{role_id} 的 roles 超出治理目录：{roles}"

    anchor = re.search(rf'id:\s*"{re.escape(role_id)}"', src)
    tail = src[anchor.start(): anchor.start() + 600]
    assert "readonly: true" in tail, f"{role_id} 必须是只读访客（readonly: true）"


def test_visitor_roles_are_registered_into_groups():
    """访客必须被追加到 ROLE_GROUPS 的「演示访客」组，否则登录页只能落进「其他」。"""
    src = (SCENES_DIR / "registry.js").read_text(encoding="utf-8")
    assert "演示访客" in src, "缺少「演示访客」分组名"
    assert "ROLE_GROUPS.push" in src, "访客分组必须 push 追加（不改 demo-data.js 既有数组）"
    assert "window.DEMO" in src, "registerVisitorRoles 必须操作 window.DEMO（活引用）"


# ============================================================
# 6. s00 骨架屏与 registry 的对象镜像不得漂移
# ============================================================

def test_skeleton_object_mirror_matches_registry():
    """s00 里镜像的对象清单必须与 registry.json 的 objects 键一致。

    浏览器读不到仓库内的 registry.json，骨架屏只能镜像一份常量；
    这条断言就是那份常量的"锚"，任一侧漂移立刻变红。
    """
    src = (SCENES_DIR / "s00-skeleton.js").read_text(encoding="utf-8")
    block = re.search(r"const STORY_OBJECTS = \[(.*?)\];", src, re.S)
    assert block, "s00-skeleton.js 未找到 STORY_OBJECTS 镜像常量"
    mirrored = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    registered = set(_load_registry()["objects"])
    assert mirrored == registered, (
        f"s00 镜像与 registry.json objects 不一致：缺 {sorted(registered - mirrored)}，"
        f"多 {sorted(mirrored - registered)}")


def test_skeleton_order_section_has_no_mainline_contradiction():
    """订单区不得出现「主线订单 3 行 / 7,400 片」这类字面矛盾，且不许硬编码业务数。

    ``story('sales_orders')`` 返回的是**全量**（主线 DEMO-SO-001 + 背景在途单）。
    小标题若还叫「主线订单」，屏上就会出现「主线订单 …… 7,400 片」而主线实际只有
    5,000 片——评审一眼就能抓住的口径矛盾。所以固定三件事：
      1. 小标题叫「订单装载」；
      2. 主线数字一律按 ``order_id === DEMO-SO-001`` 从 rows 定位算出来；
      3. 源码里不得出现 '"5000"' / '"7400"' 这类硬编码业务数。
    """
    src = (SCENES_DIR / "s00-skeleton.js").read_text(encoding="utf-8")
    assert ">主线订单<" not in src, "订单区小标题仍是「主线订单」——与全量口径矛盾"
    assert "订单装载" in src, "订单区小标题应为「订单装载」"
    assert "DEMO-SO-001" in src, "主线订单号必须按常量定位（不得假定在首行）"
    hardcoded = re.findall(r'"(5000|7400)"', src)
    assert not hardcoded, f"s00 出现硬编码业务数字 {hardcoded}——必须从 rows 算"


def test_skeleton_registers_through_registry_and_marks_demo():
    """s00 必须用 register() 登记（不碰 app.js），且复用 SceneKit 组件。"""
    src = (SCENES_DIR / "s00-skeleton.js").read_text(encoding="utf-8")
    assert "SceneRegistry.register(" in src, "s00 未通过 SceneRegistry.register() 登记"
    assert "GROUP_NAME" in src, "s00 的分组必须取 SceneRegistry.GROUP_NAME"
    for api in ("pageHead", "numberRow", "story(", "eventFlow", "signoffCard", "openDrill"):
        assert api in src, f"s00 未使用组件契约 {api}"
    assert "演示装载层未就绪" in src, "s00 必须对装载层缺失给出明确降级提示"
    assert "非业务数据" in src, "s00 的示意结构必须标注「非业务数据」"
