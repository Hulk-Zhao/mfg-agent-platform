# SPC 过程能力看板增量增强 · 共享事实基线（主理人核实）

> 本文件是主理人（齐活林）在分派任务前**逐条核实过的事实**，架构师/工程师/QA 直接采信，无需重复探查。
> 核实时间：2026-10-20。

> ## ⚠️ 并发编辑警告（2026-10-20 08:20 更新，必须先读）
>
> **本仓库存在并发编辑会话**：`src/mfg_agents/v2/demo/ui/screens.js` 的 mtime = **2026-10-20 08:13:56**（本基线编写期间被改），
> `app.css` mtime = **08:20:58**。`git status` 显示工作区有**大量他人未提交的独立改动**
> （README.md / init.sql / seed_v3.sql / init_db.py / agentic.py / cache.py / orchestrator.py / router.py /
> data_scope.yaml / api.py / app.js / case-screen.js / demo-data.js / float-help.js / help-screen.js /
> index.html / kb-screen.js / office-screen.js / reports-screen.js / docs/* 等）。
>
> **纪律**：
> 1. **不要依赖本文件里的行号**——已漂移。所有定位一律用「**函数名 + 唯一锚点字符串**」grep 定位，命中后**读实际代码**再改。
> 2. **只改 SPC 相关代码**：不要顺手格式化/重排他人改动区域，不要动 `docs/edrag-optimization/`（已被删除）等无关文件。
> 3. **不要 git add/commit 他人的改动**；本次交付只提交自己新增/修改的文件。
> 4. 修改 `screens.js` / `app.css` 前**先重新 grep 定位**，改完跑 `node --check`。
> 5. 改 `seed_v3.sql` / `init.sql` / `init_db.py` / `data_scope.yaml` / `api.py` 前同样先重读当前内容（这些文件也已被他人改过）。
>
> 行号快照（2026-10-20 08:20，仅供参考，**不可直接使用**）：
> `openOrderModalShell` 1018 / `openOrderGroupModal` 1267 / `DRILL_FILTER_SCOPE` 3633 / `drillFilterScope` 3655 /
> `mfgMfgScopeChips` 4199 / `mfgDrillL2Body` 4935 / `mfgBind` 5291 / `SPC_VERDICT` 5776 / `spcBody` 5779 /
> `spcCapCard` 5800 / `spcParetoCard` 5837 / `render_spc` 5862 / `loadSpcDashboard` 5894


## 1. SPC 屏现状（前端 `src/mfg_agents/v2/demo/ui/screens.js`）

| 位置 | 内容 |
|------|------|
| `render_spc` 5699-5725 | 屏入口。HTML 只有 page-head + `#spc-crumb` + `#spc-body`。**零工具栏**（无 `data-window` / `data-stage` / `data-pcat`） |
| `loadSpcDashboard` 5731-5784 | 调 `ctx.api.spcDashboard()` → `GET /api/v2/dashboard/spc`（`app.js:915-916`，`_getScoped(path, 8000)`）。**无 query**。失败降级 `ctx.DEMO.SPC_DASHBOARD`（`demo-data.js:3816-3914`） |
| `spcBody` 5616-5633 | KPI 卡组（逐特性 Cpk）+ 数据缺口卡 + 过程能力明细表 + 不良柏拉图 + TOP5 + 口径说明 |
| `spcCapCard` 5637-5671 | 明细表行：`data-drill-entity` + `data-kpi="spc_capability"` + `data-screen="spc"` → 点行直下 L3 |
| `spcParetoCard` 5674-5697 | 柏拉图行：`data-drill-entity` + `data-kpi="spc_pareto"` → 点行直下 L3 |
| `SPC_VERDICT` 5613 | verdict→tone 映射，与后端 `_verdict_status` 同口径 |
| `BOARD_CFG.spc` 3620-3621 | `body:"spc-body"`, `crumb:"spc-crumb"`, `win:"spc-window"`, `flag:"spc-flag"`, `gen:"spc-gen"` |
| `app.js:53-54` | NAV_ITEMS：`{id:"spc", label:"SPC 过程能力看板", group:"运营视图", roles:["gm","mfg","qd","pqe","sqe","qa","pe","te","ie"]}` |
| `screens.js:328` | `SCREEN_TO_NAV = { orders:"orders", spc:"spc", manufacturing:"mfgboard", quality:"mfgboard" }` |

**全仓 grep 零命中**：`spcWindow` / `spcStage` / `spcProdCat` / `spcCat` / `spcTab` —— SPC 专属 state 变量一个都没有。

## 2. 后端现状

| 位置 | 内容 |
|------|------|
| `services/dashboard.py:1535` | `build_spc_dashboard(registry) -> dict` —— **无参**，窗口写死 `DEMO_WINDOW`（`params={"start":DEMO_WINDOW["start"],"end":DEMO_WINDOW["end"]}`） |
| `dashboard.py:1539` | `win = "date(measured_at) BETWEEN :start AND :end"` |
| `dashboard.py:1498-1532` | `_spc_caps_from_rows(rows)` —— 按 `subgroup_no` 分组 → `core.spc.compute_capability`；缺 USL/LSL → `data_gap=True` 不编造 |
| `dashboard.py:1483-1491` | `_SPC_NOTES` 5 条口径说明 |
| `dashboard.py:1554-1563` | 柏拉图取数走 **`quality_defect` 全量**（不带窗口/维度过滤） |
| `dashboard.py:1985-1998` | `DRILL_DEFS["spc_capability"]`：table=`spc_measurement`, date_expr=`date(measured_at)`, num_expr=越限条数, den_expr=`COUNT(*)`, group_cols=`["characteristic"]`, dimension="特性", entity_cols=`[["characteristic","characteristic"]]` |
| `dashboard.py:2000-2014` | `DRILL_DEFS["spc_pareto"]`：table=`quality_defect`, date_expr=`date(inspect_time)`, group_cols=`["defect_type"]` |
| `demo/api.py:1790-1799` | `api_dashboard_spc` —— **不接任何 query**，调 `build_spc_dashboard(registry)` |
| `demo/api.py:1568-1598` | `_dashboard_payload(request, dashboard, builder, window=None, **extra)` —— `window=None` 时**不传 window 给 builder**（注释明写 spc 属此类）；`window` 非 None 时才传 `window=window` |
| `demo/api.py:1590-1598` | `_WINDOW_KEYS=("window","day","month")`；`_checked_window` 非法 → 400 |
| `demo/api.py:1543-1566` | `_scope_guard` —— fail-closed，无令牌 401 / 岗位无权 403，**绝不降级 200 空数据** |
| `services/board_qa.py:28-34` | `SCREEN_TABLES["spc"] = ("spc_measurement",)` |
| `services/board_qa.py:54-74` | `_build_screen`：`screen=="spc"` → `build_spc_dashboard(registry)`，**不传 window**（改 window 口径时这里也要同步） |
| `services/data_scope.py` | `get_data_scope().apply(dashboard, data, roles)` 做脱敏 |

## 3. 数据层现状（**本次最大改动点**）

### 3.1 `spc_measurement` —— 能力指数数据源
- 建表：`data/seed_v3.sql:489-503`（`CREATE TABLE IF NOT EXISTS`），字段仅
  `id, characteristic, subgroup_no, seq, value, usl, lsl, measured_at, source, ingested_by, ingested_at`
- **无 product_model / process_stage / product_category / line_code 任何维度列**
- 镜像 DDL：`src/mfg_agents/v2/services/spc_store.py:21-39` 的 `_DDL`（+ `MEASUREMENTS_TABLE="spc_measurement"`），`append_measurements` 的 `_INSERT_SQL` 也是**硬编码 10 列**（行 40-46）
- 种子：`data/seed_v3.sql:505+`，**3 特性 × 5 子组 × 5 值 = 75 行**
  - 特性：`焊点高度(mm)`（usl 0.75/lsl 0.45，Cpk≈1.46 合格）/ `锡膏厚度(μm)`（Cpk≈1.15 偏低）/ `回流焊峰值温度(℃)`（Cpk≈0.58 不合格）
- **实测现库**（`data/mfg_agent.db`）：75 行，列同上，`date(measured_at)` 只有 `2026-10-13` / `2026-10-14` 两天，3 个特性
- `data/init.sql` 与 `src/mfg_agents/common/init_db.py` **都不含** spc_measurement（只有 seed_v3.sql + spc_store.py 两处 DDL）

### 3.2 `quality_defect` —— 柏拉图数据源（**已有维度列**）
- 建表 `data/init.sql:37-50`：`id, batch_no, product_model, line_code, defect_type, defect_qty, total_qty, inspect_time, process_stage, defect_category, product_category`
- ✅ **已含 `process_stage` / `defect_category` / `product_category` 三列** → 柏拉图侧筛选有现成列可用
- 种子：`data/seed_v3.sql:30`（基础行）、`data/seed_v3.sql:383`（追加行），另有 `data/seed_quality_pareto.sql` / `seed_quality_pareto_pcat.sql`

### 3.3 关键矛盾（架构师必须裁决）
> `quality_defect` 靠 **`line_code` 前缀 + `product_model` 归属** 折出工段/产品分类（`_MFG_STAGE_LINE_LIKE` / `_MFG_CAT_MODELS`）；
> 但 **`spc_measurement` 连 `line_code` 都没有**，无法照抄该映射法 → 能力指数侧的工段/产品分类**只能从表结构层面补列**。

### 3.4 时间窗口常量（`dashboard.py:24-45`）
```
DEMO_TODAY  = "2026-10-15"     # 红线日，"当日"口径右端点
DEMO_WINDOW = {start:"2026-10-08", end:"2026-10-14", days:7, label:"最近 7 天（演示窗口）"}
WINDOWS = {
  "window": {start:"2026-10-08", end:"2026-10-14", label:"最近7天"},   # 默认键
  "day":    {start:"2026-10-15", end:"2026-10-15", label:"当日"},
  "month":  {start:"2026-10-01", end:"2026-10-15", label:"当月"},
}
```
**⚠️ 致命点**：现有 SPC 种子只有 10-13/10-14 →
- "当日"（10-15）→ **0 行**（空看板）
- "当月"（10-01~10-15）→ 仅 2 天数据
- "最近7天"（10-08~10-14）→ 仅 2 天数据
→ **"补充数据"是刚性需求**，且新种子必须覆盖 10-01 ~ 10-15 全窗口（尤其含 10-15 当日）。

## 4. 可直接复用的参考实现

### 4.1 工具栏（订单 / 制造）
- 订单 `screens.js:869-893`：`WINDOW_KEYS=["day","month","window"]`、`WINDOW_CN={day:"当日",month:"当月",window:"最近7天"}`、`st.ordersWindow`、`data-window`；事件 900-910（换口径时若在 drill 中**只改 `drill.window`，不复位 drill 层级**）
- 制造 `mfgMfgScopeChips(UI, st, fs)` `screens.js:4199-4218`：`data-mstage` chips + `<select class="chip-select" data-pcat>` 下拉；白名单 `MFG_STAGE_CHIPS=["SMT","PCM","PACK","整机"]`、`MFG_PCAT_OPTIONS=["消费电子","智能电源","储能电源","车载"]`（3605-3610）；事件 5490-5508（改 `st.mfgMStage`/`st.mfgProdCat`，drill 态同步写 `drill.stage`/`drill.prodCat`）
- 品质 `mfgScopeChips` 4233-4250（`data-stage`/`data-cat`）

### 4.2 2 级弹窗
- 订单：`openOrderModalShell` 1018-1047（建 overlay + 骨架 + 返回 `{body, close}`）→ `openOrderGroupModal` 1267-1299（调 `ctx.api.ordersGroup`，403 如实拒绝、离线降级、弹窗内保留 3 级入口）→ `orderGroupModalBody` 1205-1263
- 制造：`openMfgDrillModal`（同族，含工段·工序·工位 + 逐日 + 原始记录 + "进入 N 级看板"入口）
- 三率：`openStdTeamModal` 4092-4145 + `stdTeamModalBody` 3983-4089
- CSS：`.std-modal*` / `.std-cause*` 在 `app.css:2958-3060`（含 `.std-cause-bar i.ok-fill` 在 5359）

### 4.3 2 级行点击的分流机制（**改 SPC 弹窗必须遵守**）
`mfgDrillL2Body` 4801-4894 已按屏分流（4816-4837）：
```
term 或 masked → 不可点
isOrders (d.screen==="orders")        → data-order-group="1"  → 弹窗
isMfgModal (d.screen==="manufacturing") → data-mfg-modal="3"     → 弹窗
其余（含 spc）                          → data-drill-entity      → 直下 L3
```
`mfgBind`（5160-5295）绑定顺序与排除链（**踩坑高发区**）：
1. `[data-std-team]`（5181-5191）→ `openStdTeamModal`
2. `[data-drill-pareto]` → `goPareto`（L2 柏拉图类型下钻）
3. `[data-order-group]`（5239-5250）→ `openOrderGroupModal`
4. `[data-mfg-modal]`（5251-5266）→ `openMfgDrillModal`
5. `[data-kpi]:not([data-drill-entity]):not([data-drill-pareto]):not([data-order-group]):not([data-mfg-modal])`（5269-5270）→ L2
6. `[data-drill-step]:not([data-mfg-modal])` → L4/L5
7. `[data-drill-entity]:not([data-drill-step]):not([data-mfg-modal])` → L3

→ 新增 SPC 弹窗必须：**新增 `data-spc-modal` 钩子 + 在 5/6/7 三条排除选择器里加上 `:not([data-spc-modal])`**，
否则点行会同时触发展开下钻与弹窗（历史上订单/制造都踩过，注释里写明"必须先于…否则点行会同时触发"）。

### 4.4 板级筛选 → 下钻口径贯通
- `DRILL_FILTER_SCOPE`（screens.js:3633-3660）：**当前完全没有 `spc:*` 条目** → `drillFilterScope(drill)` 对 spc 返回 `{}`（下钻不吃板级筛选）→ **必须登记**
- `drillFilterScope(drill)` 3660-3666：`if (drill.stdStage) return {window:true}` 优先级最高
- `BOARD_FILTER_ALL = {window:true, stage:true, category:true, product_category:true}`
- 下钻取筛选值 `screens.js:5314-5319`：
  ```js
  const fs = drillFilterScope(drill);
  const onBoard = !drill.host;
  const stage = drill.stage || (drill.screen === "manufacturing" ? (ss.mfgMStage||"") : (onBoard && fs.stage ? (ss.mfgStage||"") : ""));
  const category = drill.category || (onBoard && fs.category ? (ss.mfgCat||"") : "");
  const prodCat = drill.prodCat || (drill.screen === "manufacturing" ? (ss.mfgProdCat||"") : "");
  ```
- 后端 scope 折叠：`dashboard.py` `_mfg_scope(stage, product_category, ...)`（474-495）生成**常量 WHERE 片段 + 命名绑定参数**，**绝不让用户字符串进 SQL**；`_drill_scope`（498-525）、`_quality_scope`（526-）
- 常量白名单：`MFG_STAGES=("SMT","PCM","PACK","整机")`（447）、`MFG_PRODUCT_CATEGORIES`（448）、`QUALITY_STAGES`（435）、`QUALITY_CATEGORIES`（436）、`PRODUCT_CATEGORIES`（431）
- 映射表：`_MFG_STAGE_LINE_LIKE = {"SMT":"SMT-%","PCM":"DIP-%","PACK":"PACK-%","整机":"ASSY-%"}`、`_MFG_CAT_MODELS = {"消费电子":("A型号","E型号","G型号"), ...}`（441-470）

### 4.5 下钻 scope 折叠机制（SPC 需新增第三种形态）
- `DRILL_DEFS` 每项可声明两种 scope：
  - `"scope": {"stage_col": ..., "pcat_col": ...}` → `_drill_scope(defn, stage, product_category, params)`（dashboard.py:498-516）转调 `_mfg_scope`（474-495），**按产线前缀/型号集合**过滤（`SMT-%` / `A型号` 之类）
  - `"q_scope": {"stage_col": ..., "category_col": ...}` → `_quality_scope(defn, stage, category, params)`（526-545），**按列精确匹配**（`process_stage = :f_stage`）
- 两者都是**常量 SQL 片段 + 命名绑定参数**，用户字符串绝不进 SQL；未声明 scope 的指标原样返回 → **既有行为逐字段不变**
- `_l2_detail(registry, defn, items, base_where, params, dates, state)`（547+）：2 级补充数据（整体汇总/逐日/原始记录），**只对声明了板级筛选的指标调用** → SPC 若要 L2 也带"整体汇总/逐日/原始记录"，同样要在 DRILL_DEFS 里声明 scope
- `_quality_scope_cn(stage, category)`（518-524）：范围中文标签（"工段 SMT · 类别 X"）→ SPC 需同款标签
- **SPC 的处境**：`spc_measurement` 若增列 `process_stage`（工段）+ `product_category`，语义上等价于 `q_scope` 的**精确匹配**形态，但表名/列名不同 → 需第三种 scope 形态（如 `"spc_scope"`）或复用 `q_scope` 并让 `stage_col`/`category_col` 可配置（`q_scope` 本就是可配置列名，可行）

## 5. 载荷缓存（可复用）
- `st.ordersPayload`（写 1006 / 读 1128）：订单弹窗离线兜底
- `st.stdPayload = {key: win, data: payload}`（写 5602 / 读 5113、5122）：三率 2 级下钻零往返
- → SPC 可照此加 `st.spcPayload`（键建议 `{window, stage, prodCat}` 组合）

## 6. 脱敏与数据范围
- `src/mfg_agents/v2/data/data_scope.yaml` `spc:` 块（145-158）：`name: SPC 过程能力看板`, `screen: spc`, `allow_roles: [gm, manufacturing, quality_director, pqe, sqe, qa, product_engineering, test_engineer, ie_engineer]`（9 个）
- **fail-closed 守护**：`tests/v2/test_数据范围.py` —— 新增看板/新暴露字段必须登记 mask 路径，否则测试变红
- 前端岗位 id（31 个 `gm`/`mfg`/`wh`/`hr`）≠ 权限角色 key（32 个 `gm`/`manufacturing`/`warehouse`/`admin_hr`）

## 7. 测试现状
- `tests/v2/` 共 81 个 py 文件；**SPC 相关仅 `test_mcp_tools_spc.py`**（工具层，非看板层）→ 看板层 SPC 无专项测试
- 看板相关既有测试：`test_dashboard_drill.py`、`test_orders_detail.py`、`test_order_group_detail.py`、`test_std_team_detail.py`、`test_数据范围.py`、`test_待办可见性.py`、`test_架构守卫.py`
- 全量基线：`pytest tests/ -q` = **1608 passed**（2026-10-20 口径）
- 测试摘要不可靠（`[safe-delete]` 吞 summary）→ 拿失败清单用 `--junitxml=logs/junit.xml`
- **kbqa 测试必须隔离 6380 Redis 缓存**
- **本仓库有并发编辑会话**（另一 Agent 会中途改 screens.js 等 UI 文件）→ 全量测试遇到「单跑即绿」的 UI 文件失败，先查文件 mtime 是否落在测试运行窗口内再下结论
- 写代码文件用 Write 工具，别 `cat > <<EOF`（反斜杠偶发转义坏正则）
- pytest 会留 `.venv` 子进程占 SQLite，重跑前先 pkill uvicorn 与 `.venv` 进程

## 8. 环境
- node 可用：`C:/Users/22953/.workbuddy/binaries/node/versions/22.22.2-6/node.exe --check <file>`（JS 改完必须真跑语法检查）
- Python venv：`.venv`（仓库内已有）
- Chrome：`C:/Program Files/Google/Chrome/Application/chrome.exe`；playwright-cli 每条命令回收会话
- 端口：MySQL→3307、Milvus→19531、Redis→6380
- 演示前端基址是竞速选出的（`app.js::probe()` 并行探测先到先得）→ 本机若挂着旧实例，新端点会 404
- 沙箱回收后台进程：起服务 + 浏览器压同一条 Bash；用 `netstat -ano | grep :PORT` 定位再 taskkill

## 9. 提交纪律
- 提交前**勿混入**他人未提交的独立工作（`data/seed_inventory.sql`、`docs/case-assistant-*`、`oa-collaboration-split-plan/`、`tools/`）
- 记忆约定：新增跨会话有效的事实要写进 `.workbuddy/memory/MEMORY.md`，日常进展写 `.workbuddy/memory/YYYY-MM-DD.md`（append-only）

## 10. 后端待改点（已二次核实，后端文件 mtime 早于并发窗口，**行号稳定可用**）
- `dashboard.py:1535` `def build_spc_dashboard(registry) -> dict:` —— 需改签名为可接 `window` / `stage` / `product_category`
  - `1538` `params = {"start": DEMO_WINDOW["start"], "end": DEMO_WINDOW["end"]}` → 需按 window 解析
  - `1539` `win = "date(measured_at) BETWEEN :start AND :end"`
  - `1541-1543` meas 取数 SELECT（**需补 process_stage/product_category 列**）
  - `1546-1551` over 取数（越限条数）
  - `1554-1557` pareto 取数走 `quality_defect`（**当前无任何 WHERE**，需加窗口+维度过滤）
  - `1560-1563` defect_rate_rows（TOP5 用）
- `api.py:1568-1586` `_dashboard_payload(request, dashboard, builder, window=None, **extra)`
  - 文档字符串 1573-1574 明写「``None`` 表示该看板不接口径参数（orders / spc）」→ 需更新注释并让 spc 走 `window=window` 分支
  - `1579-1582` `if window is not None: data = builder(registry, window=window, **extra) else: data = builder(registry, **extra)`
- `api.py:1791-1799` `api_dashboard_spc(request)` —— **不接任何 Query 参数** → 需加 `window` / `stage` / `product_category`（用 `_checked_window` 校验 window，非法 → 400）
- `api.py:1589-1590` `_WINDOW_KEYS = ("window", "day", "month")`；`_checked_window` 在 1595-1598
- `board_qa.py:33` `SCREEN_TABLES["spc"] = ("spc_measurement",)`
- `board_qa.py:70-71` `if screen == "spc": return dash.build_spc_dashboard(registry)` —— **不传 window**（问数通道，改口径后需同步，否则问数与看板口径不一致）
- `board_qa.py:113` ask_ctx 形状注释已写 `"screen": "manufacturing|quality|orders|spc"`、`"filters": {stage/category/prod_cat}` → 前端 SPC 的问数 filters 应传工段/产品分类

## 11. 分类枚举的唯一事实源（dashboard.py:429-495，**已核实原文**）
```python
PRODUCT_CATEGORIES = ["消费电子", "智能电源", "储能电源", "车载"]
QUALITY_STAGES = ("SMT", "PCM", "PACK", "整机")
QUALITY_CATEGORIES = ("性能不良", "外观不良")
MFG_STAGES: tuple[str, ...] = ("SMT", "PCM", "PACK", "整机")
MFG_PRODUCT_CATEGORIES: tuple[str, ...] = tuple(PRODUCT_CATEGORIES)
_MFG_STAGE_LINE_LIKE = {"SMT": "SMT-%", "PCM": "DIP-%", "PACK": "PACK-%", "整机": "ASSY-%"}
_MFG_CAT_MODELS = {
    "消费电子": ("A型号", "E型号", "G型号"),
    "智能电源": ("B型号", "F型号", "H型号"),
    "储能电源": ("C型号", "I型号", "J型号"),
    "车载": ("D型号", "K型号", "L型号"),
}
MFG_STAGE_FREE_KPIS = ("test_fpy",)
```
- `_scope_cn(stage, product_category)`（497-505）：板级筛选中文标签 → "产品分类 X · 工段 Y"
- `_mfg_scope(stage, product_category, *, has_line, line_col, model_col)`（507-528）：**常量 WHERE 片段 + 命名绑定参数**，接口层已按白名单裁定，非法值 400
- 前端对应白名单 `MFG_STAGE_CHIPS` / `MFG_PCAT_OPTIONS`（screens.js 3605-3610），注释明写"与后端 MFG_PRODUCT_CATEGORIES / MFG_STAGES 同源"
- → **SPC 新增的工段/产品分类枚举应复用 `MFG_STAGES` / `MFG_PRODUCT_CATEGORIES`，不另起炉灶**（前端 chips 同理复用 `MFG_STAGE_CHIPS` / `MFG_PCAT_OPTIONS`）
- `quality_defect.process_stage` / `product_category` 的取值必须与 `MFG_STAGES` / `MFG_PRODUCT_CATEGORIES` 一致（`seed_quality_pareto_pcat.sql` 已在铺这批值）

## 12. 前端 CSS 可复用类（app.css，已核实）
- `.chip` 544 / `.chip:hover` 558 / `.chip.active` 560
- `.chip-row` 567 / `.chip-select` 571 / `.chip-select:hover` 588 / `.chip-select:focus` 589 / `.chip-select option` 590
- `.chip-row-label` 2541 / `.chip-row-note` 2542
- `.std-modal*` 2958-3060 / `.std-cause*` 3021-3044 / `.std-cause-bar i.ok-fill` 5359
- ⚠️ `.spc-pareto`（screens.js `spcParetoCard` 用了这个 class）**在 app.css 中未定义** → 既有小瑕疵，可顺手补或不管
- 主题：`.std-modal*` 全走 CSS 变量（浅色主题必须浅底深字）
