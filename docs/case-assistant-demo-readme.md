# 案例助手 · 可运行演示骨架（README）

> 状态：演示骨架已落地，可本地起服务、可用 curl 验证「不同岗位检索同一批案例得到不同结果、越权一律查不到」。
> 本文只描述**如何跑**、**哪些是可替换契约（接入点）**、**与架构文档有哪些偏差**。设计与取舍依据见
> `docs/case-assistant-ontology-vector-auth-architecture.md` 与 `docs/case-assistant-product-research-prd.md`。

---

## 1. 一句话说明

同一句 query，`qa`（质量）、`equipment`（设备）、`gm`（总经理）、`outsource`（外协）拿到的案例条数与内容各不相同；
**授权发生在检索之前**（过滤条件下推给向量库），返回前再用关系库 ACL 做**第二次授权**；
无权访问的案例在 API 层统一表现为 `404 CASE_NOT_FOUND_OR_NOT_VISIBLE`，不泄漏"存在性"。

---

## 2. 快速开始

```bash
# 1) 依赖：无需新增任何第三方包（仅标准库 + 项目已有的 fastapi/pydantic/pyyaml）
# 2) 启动演示服务
python -m uvicorn mfg_agents.v2.demo.api:app --host 127.0.0.1 --port 8123

# 3) 选岗登录（仅 CASE_DEMO_MODE=true 时开放；生产由 SSO 提供身份）
curl -s -X POST http://127.0.0.1:8123/api/v2/cases/session \
  -H 'Content-Type: application/json' \
  -d '{"role":"qa","dept":"quality","plant":"P1","name":"王工"}'
# → data.token（tv=2 令牌），data.principal.principal_tokens（维度令牌，调试用）

# 4) 检索（把 $TOKEN 换成上一步的 data.token）
curl -s -X POST http://127.0.0.1:8123/api/v2/cases/search \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"query":"锡膏印刷偏移 虚焊 连锡","k":10,"action":"query"}'
```

### 环境变量（全部有默认值，`CASE_*` 前缀）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CASE_ENABLED` | `true` | 总开关；关闭后案例域接口 503 |
| `CASE_DEMO_MODE` | `true` | `/session` 选岗接口开关；**生产必须为 false** |
| `CASE_VECTOR_BACKEND` | `memory` | `memory`（默认，纯内存）/ `chroma` / `milvus` |
| `CASE_DB` | `data/case.db` | 案例关系库（SQLite） |
| `CASE_CHROMA_DIR` | `data/chroma_case` | chroma 持久化目录（仅 backend=chroma 使用） |
| `CASE_SEED_PATH` | `data/case_seed.json` | 种子数据；置空则不导入 |
| `CASE_EXPOSE_HIDDEN_COUNT` | `true` | 是否返回「另有 N 条因权限不可见」 |
| `CASE_RERANK` | `deterministic` | 重排适配器选择（`bge` 为生产预留位） |
| `CASE_DEFAULT_TENANT` | `T001` | 演示态默认租户 |
| `CASE_K_DEFAULT` / `CASE_K_MAX` | `5` / `20` | 返回条数默认值 / 上限（超限**钳制**，不报错） |

---

## 3. 演示用的岗位矩阵（实测）

种子 `data/case_seed.json` 共 15 条，覆盖：全员开放质量案例 8 条、部门域案例（`dept:quality`）1 条、
工厂域案例（`plant:P2`）1 条、审核中 1 条、机密（`cls_rank=3`）1 条、岗位经验案例 3 条（工艺/设备/质量各 1）、
待复核 1 条（`deprecated`，降权但仍可见）。

`query = "锡膏印刷偏移 虚焊 连锡"`、`k = 25`（服务端钳制到 20）实测：

| 主体 | 可见命中 | hidden_count | notice |
| --- | --- | --- | --- |
| `qa` / dept=quality / P1 | 20 | 0 | — |
| `equipment` / dept=equipment / P1 | 18 | 16 | 结果少于 20 条（已如实返回，不放宽过滤补齐）；另有 16 条因权限范围不可见 |
| `outsource`（外协）/ dept=production / P1 | 16 | 20 | 同上（另有 20 条不可见） |
| `gm` / dept=management / P1 | 16 | 20 | 同上（另有 20 条不可见） |

要点：
- `k=10` 时 `hidden_count` 恒为 0——这是架构 §7.6 的**刻意优化**（只在候选不足时才做两次 count 的差值），
  避免"结果够了还告诉你还有多少"，减少信息泄漏面。想看差异请用 `k=25`。
- `gm` 也有 20 条不可见：**部门域案例不因"职级高"自动穿透**（各维度独立判定，`dept:quality` 的案例对
  `dept:management` 就是不可见）。这是产品口径，不是漏洞。
- `outsource` 只走快路径（`visibility_class=0`），因此看不到任何带业务 ACL 的案例；`etype:outsource` 的
  令牌里**照样有** `dept/plant` 令牌，但快路径不消费它们——这是**收紧**，不是放宽。

---

## 4. 代码落点

```
src/mfg_agents/
├─ common/
│  ├─ case_schema.py          # 案例库 DDL / 连接（check_same_thread=False）/ 幂等建表
│  └─ config.py               # 【追加】CASE_* 配置项
├─ v2/
│  ├─ contracts/
│  │  ├─ case_acl.py          # 安全内核契约：Op / FilterNode / FALSE_FILTER / Principal / 三重自检
│  │  ├─ case_index.py        # KnowledgeChunk / EvidenceContent / SearchHit / CaseVectorIndex(ABC)
│  │  ├─ ontology.py          # OntologyRegistry（加载期 fail-fast，只做精确归一化）
│  │  └─ adapters/
│  │     ├─ case_memory.py    # 默认后端：纯内存 + 确定性哈希 embedding（零依赖）
│  │     ├─ case_chroma.py    # 惰性 import chromadb；四条已知限制已整形
│  │     └─ case_milvus.py    # 惰性 import pymilvus；未装依赖时给出安装提示
│  ├─ security/
│  │  ├─ filter_translator.py # FilterNode → milvus expr / chroma where / python predicate
│  │  ├─ acl_compiler.py      # 五阶段编译：动作门禁→显式拒绝→主体校验→RBAC→硬/软维度
│  │  ├─ acl_evaluator.py     # 返回前二次授权（独立代码路径，行集集合运算，不复用编译器）
│  │  └─ case_rbac.py         # 角色→动作矩阵（独立注册表，构造期自检）
│  ├─ services/
│  │  ├─ case_store.py        # 关系库读写 + ACL 条目 + 状态机 + 种子导入
│  │  ├─ case_index.py        # chunk 规划 / metadata 构建 / 索引同步（唯一装配点）
│  │  ├─ redaction.py         # 脱敏（literal + regex，可注入 NER）
│  │  ├─ case_ingest.py       # 入库管道：来源校验→词表归一→脱敏→复检→待审→建索引
│  │  ├─ case_search.py       # 检索主流程（16 步）
│  │  ├─ case_qa.py           # 带引用问答（引用校验失败即拒答）
│  │  ├─ case_audit.py        # 哈希链审计（只记哈希与计数，不记正文）
│  │  ├─ principal_resolver.py# 令牌→权威主体（tv=2 / pv 撤销 / 用工类型白名单）
│  │  └─ case_runtime.py      # 运行时装配 + 种子的幂等导入
│  ├─ data/
│  │  ├─ case_policy.yaml     # 动作策略 / 允许规则 / 拒绝规则 / 阈值
│  │  ├─ case_roles.yaml      # 32 角色 → case.* 动作
│  │  ├─ redaction_rules.yaml # 客户/供应商/人名/单号/图纸号/电话
│  │  └─ ontology/*.yaml      # 缺陷模式 24 + 根因 16 + 对策 13 = 53 条受控词表
│  └─ demo/
│     ├─ api.py               # 【追加】/api/v2/cases/* 路由
│     └─ ui/
│        ├─ case-screen.js    # 【新增】案例助手演示屏（零构建，原生 JS）
│        └─ app.js / app.css  # 【追加】导航项 `.case-` 样式
data/case_seed.json           # 15 条种子案例
tests/v2/
├─ conftest.py                # 【新增】case_client 夹具（隔离库 + TestClient）
├─ test_case_acl_compiler.py  # 编译器五阶段与签名
├─ test_case_filter_translator.py
├─ test_case_search_e2e.py    # 服务层端到端
├─ test_case_redteam.py       # 【新增】HTTP 层红队：注入/越权/不泄漏
└─ test_case_http_e2e.py      # 【新增】HTTP 层完整链路
```

---

## 5. 可替换契约（接入点清单）

后续接 Milvus / SSO / 工单系统 / LLM，**只换适配器，不改调用方**。

| 契约 | 位置 | 现在 | 生产替换方式 |
| --- | --- | --- | --- |
| 向量库 | `CaseVectorIndex`（ABC） | `MemoryCaseIndex` | 设 `CASE_VECTOR_BACKEND=chroma\|milvus`；或实现同一 ABC 后注册 |
| 向量库装配 | `case_index.create_case_index(backend)` | 唯一装配点 | 只改这里 |
| 权限编译 | `AclCompiler.compile(principal, action, policy, env)` | YAML 策略驱动 | 换 policy 文件即可；**签名刻意不含 query_text**（A6） |
| 二次授权 | `AclEvaluator.authorize(...)` | 关系库行集 | 可换成策略引擎（OPA 等），**不得复用编译器的代码路径** |
| 身份来源 | `PrincipalResolver.from_token(jwt)` | 演示令牌（tv=2） | 换成 SSO/IdP 的 JWT 校验；保留 `tv/pv` 语义 |
| 选岗登录 | `POST /api/v2/cases/session` | 前端选岗位 | 生产置 `CASE_DEMO_MODE=false` 后该端点 403 |
| 重排 | `CaseSearchService(reranker=...)` | 确定性加权 | 注入 `bge` 适配器 |
| 问答 | `Answerer`（Protocol） | `TemplateAnswerer`（模板拼装） | 注入 LLM，**必须走 `_assert_knowledge_layer` 与引用校验** |
| 脱敏补充 | `RedactionService(extra_detectors=...)` | 规则（literal+regex） | 注入 NER / 词典服务 |
| 工单回写 | `POST /api/v2/cases/{id}/cite` | 返回引用三元组 + 来源单据 + 审计 | 换成真实工单系统客户端；**保留** `chunk_type`/`content_version` 入参与 `409 CASE_CITATION_STALE` 语义 |
| 审计 | `CaseAuditLog` | SQLite 哈希链 | 换成 WORM 存储；`verify_chain()` 语义不变 |

---

## 5.1 响应契约增补（前端演示屏依赖）

| 字段 | 出处 | 说明 |
| --- | --- | --- |
| `filter_explain: string[]` | `POST /cases/search` | 人类可读的过滤条件描述，如 `status ∈ [active, deprecated, …]`、`cls_rank ≤ 1`、`(visibility_class = 0 或 acl_tokens 任一命中（…）)`。**刻意不返回原始表达式/AST**：前端拿不到结构，就没有"在前端重算过滤"的余地 |
| `filter_hash: string` | `POST /cases/search` | AST 的确定性指纹。同一身份、同一动作，无论 query 怎么变，hash 必须一致（红队有 6 组注入参数断言这一点） |
| `cls_rank: int` | 命中项与详情 | 案例密级（0/1/2/3），前端只展示，不参与判断 |
| `status_badge: string` | 命中项 | 服务端给口径（"待复核（降权）"等），前端只管配色 |
| `hidden_count` / `notice` / `degraded` | `POST /cases/search` | 差异化证据三件套，必须显著展示 |
| `hidden_count_unit: "case"` | `POST /cases/search` / `GET /cases/meta` | `hidden_count` 的单位是**案例数**（按 `case_id` 去重），不是 chunk 数 |
| `source_ticket_type` / `source_ticket_ref` | 检索/问答命中项 | **不返回 `source_ticket_id`**（R1）；`ref = "st_" + sha256(id)[:10]`，稳定可对账、不可反推 |
| `source_ticket_id` + `X-Case-Trace: full` | `GET /cases/{id}` | 只有通过 `case.read` 且 `cls_rank` 达标的调用方才能拿到真号，响应头显式标注 |
| `answered` / `reason` | `POST /cases/ask` | 拒答时 `answered=false` 且 `reason` 给出分数与阈值，前端原样照抄 |
| `citation` / `chunk_type` / `source_ticket` | `POST /cases/{id}/cite` | 引用三元组 + 来源单据（`ticket_ref` 恒有，`ticket_id` 需 `case.read`） |
| `demo_warnings: string[]` | `GET /cases/meta` | 演示态风险（`AUTH_SECRET` 未配置 / `CASE_DEMO_MODE=true`），前端黄字常显 |

获取顺序也有约束：**`/cases/meta` 需要令牌**，所以登录前的岗位下拉用前端内置的 32 岗位兜底表
（`FALLBACK_ROLES`，纯展示），建立会话后再用 `meta.rbac.roles`（含每个岗位的动作集合）整体替换。

---

## 6. 与架构文档的偏差（需评审确认）

1. **案例权限用独立注册表，未并入 `v2/data/permissions.yaml`**
   `case_roles.yaml` + `security/case_rbac.py`。原因：`permissions.yaml` 是既有业务域（编排/工具）的
   权威注册表，扩容它会把案例域的角色变更风险传导到既有链路；且任务要求"不得修改 permissions.yaml"。
   代价：两处角色需要人工保持一致 —— 构造期已强制 `PermissionRegistry.normalize_role()` 校验，
   未识别角色**加载失败**（fail-closed），不会静默漂移。

2. **受控词表 53 条，不是架构文档设想的 100–200 条**
   全部同义词从 `data/knowledge/*.jsonl` 真实写法归纳（锡膏偏移 / 立碑 / 虚焊 / 连锡 / 抛料 / 缺件 /
   引脚氧化 / 空洞 / 焊球不良 / 元件偏移 …）。演示语料只覆盖 SMT + 注塑 + 机加三段，
   词表按语料定规模是刻意的：**多出来的条目无法被验证，就是负资产**。

3. **`pipeline_stage` 不落表**
   `case` 表不存流水线阶段字段，授权只认 `status`。原因：`pipeline_stage` 是"数据生产流水线的位置"，
   与"这份内容此刻能不能被人看到"是两件事，混在一起会出现"stage=draft 但 status=active"的矛盾态。

4. **`case_audit` 增补 `chunk_id` 列**
   架构文档的审计字段集里没有它，但"引用到工单"必须能定位到具体 chunk（否则复现不了引用内容），
   因此加列并纳入 `row_hash` 计算。

5. **`fast_path_only` 的语义是"收紧"而非"降级"**
   `etype != employee` 的主体只走 `visibility_class=0` 快路径。文档 §6 只说"快/慢路径"，没说谁走哪条；
   这里把"非正式员工不享受默认开放"显式化，行为是**更严**的（不是性能妥协）。

6. **`export` / `share` 是"门禁拒绝"而非"检索后拒绝"**
   `export` 只对 `gm` / `quality_director` 开放且仍返回 403（需走审批通道）；`share` 无任何角色持有，
   对所有人恒定 403。文档 §7.5 描述的是产品意图，这里落成最保守的可执行形态。

7. **`ingest` 的 publish 是最小实现**
   `CaseIngestService.publish(case_id, approver=...)` 只强制记录审批人，**未接 HITL 三签**（工艺/质量/领域专家）。
   接口形状已按多签设计（`approver` 为必填的显式参数），接审批流时只需把单参数换成签名列表。

8. **问答加了"相关度闸门"（服务层新增行为，非文档要求）**
   引用校验只能保证"没编造出处"，**保证不了"答的是这个问题"**：模板/LLM 都能就着最像的 3 条案例说出一段话，
   于是问"食堂几点开饭"也会得到一段锡膏印刷的答案，而且引用全部合法——"拒答"这条路等于死代码。
   现在 `CaseQAService.answer` 在检索后加一道闸：最高命中分低于策略里的 `low_confidence_threshold`（0.18）
   → 直接拒答并说明"最高匹配度 X 低于阈值 Y"。实测：「锡膏印刷偏移怎么处置」0.62 → 作答；
   「食堂几点开饭」0.136、「今天天气怎么样」0.086 → 拒答。

9. **对比模式需要显式带 `dept`**
   部门是**授权维度**而非展示字段。对比两个岗位时若两边 `dept` 都留空，两边的部门域案例都不可见，
   差异会被抹平，看起来像"没差异"。因此对比面板为每个岗位提供可编辑的部门输入（前端只给建议默认值，
   判定仍在服务端）。

10. **`create_case` 给 `tenant` 补缺省值（红队测试暴露的坑）**
    编译出的硬维度是 `tenant == principal.tenant`；案例 `tenant` 若存空串，这条案例**谁都看不见**——
    所有越权测试都能过，正向测试却 404，排查成本极高。现在缺省落到 `CASE_DEFAULT_TENANT`（默认 `T001`）。

11. **`set_acl` 对维度名 fail-fast（同上）**
    `dim` 必须是 `TOKEN_DIMS`（`role/dept/plant/line/prj/cus/sup/pf/etype`）之一。写错维度名
    （例如把 `cus` 写成 `customer`）不会命中任何主体令牌，等于静默造出一条不可见案例，
    因此在写入入口直接 `CaseStoreError`。**这两个坑是同一类故障：静默变成"谁都不许看"。**

12. **`/cite` 精确到用户点的那一段（引用可溯源的前提）**
    早期实现恒回 `{case_id}::summary`，而卡片上写着 `phenomenon` —— 复核时对不上内容，引用等于没有。
    现在请求体接受 `chunk_type` / `content_version`（前端从命中卡片带回），并：
    版本不匹配 → `409 CASE_CITATION_STALE`（不复述旧引用）；片段类型非法 → `400`（不是 500）；
    回执附带 `source_ticket`（类型 + 不可反推的 `ticket_ref`），从案例能对回原始单据。

13. **R1 · 原始来源单号不进检索/问答响应（QA 验收裁定的返工）**
    `source_ticket_id` 是**来源单据号**，8D/客诉单号在真实工厂可反查到客户、批次与产线。
    检索响应是宽面（几十条、会被复制/截图/导出），因此：
    命中载荷只给 `source_ticket_type` + `source_ticket_ref = "st_" + sha256(id)[:10]`（稳定、可对账、
    不可反推）；真号只在 `GET /cases/{id}` 返回，且响应头显式标注 `X-Case-Trace: full`。
    依据：team-lead 裁定 R1；对应测试 `test_case_contracts.py::test_r1_*` +
    `test_case_redteam.py::test_search_response_has_no_evidence_layer_payload`（已恢复强断言）。

14. **R2 · `hidden_count` 的单位改为案例数**
    一个案例有 2–4 个 chunk，按 chunk 计数会把"藏了 4 个案例"报成"16 条"，现场一被追问就穿。
    现在两条计数路径都按 `case_id` 去重（内存后端用 `count_cases()`，其他后端回退到检索后去重），
    响应带 `hidden_count_unit: "case"`，`meta` 里写明口径。

15. **R3 · 引用校验改成"文本 + 版本双一致"**
    `case_chunk.text_hash` 一直有列却没用上。只比版本是纸糊的护栏：`update_fields()` 改文本不 bump 版本，
    "文本被改、版本没变"的引用会一路放行。现在校验时用当前案例重建该段文本、比对
    `sha256(text)[:32]` 与写入时固化的 `text_hash`，不一致即**丢弃该断言**。

16. **B2 · 已发布案例拒绝重复 ingest**
    `create_case` 是 upsert，重复 ingest 会把 `status` 写回 `review`、把 `content_version` 固定为 1，
    并让 `sync_case` 把案例**移出索引**——别人正在引用的案例会凭空消失。现在：
    `status=active` → `409 CASE_ALREADY_ACTIVE`（提示走"新增 content_version + 复审"）；
    `draft/review/rejected` → 允许覆盖但必须 bump `content_version`。

17. **B3 · 种子导入与入库同走脱敏管道；新增 denylist / `CASE_REDACTION_EXTRA_TERMS`**
    种子是人工维护的 JSON，一次复制粘贴就可能带进真名——如果只有入库管道有脱敏，
    种子就是绕过所有护栏的后门。现在 `seed_from_json(redactor=...)` 逐字段 `redact()` + `verify_no_leak()`，
    残留即**拒绝导入**（`CaseStoreError`）。词表外的真名由 `redaction_rules.yaml` 的 `denylist` 段
    与 `CASE_REDACTION_EXTRA_TERMS`（逗号分隔）兜住，替换为 `<PERSON>` / `<ORG>`。
    入库复检失败的错误码由 `CASE_REDACTION_FAILED` 改为 **`CASE_REDACTION_LEAK`**（与架构文档 §9.2 有差异，见 §6.18）。

18. **错误码与架构文档的一处差异**
    架构 §9.2 写的是 `CASE_REDACTION_FAILED`；实现改为 `CASE_REDACTION_LEAK`（语义更准：不是"脱敏过程报错"，
    而是"脱敏后仍有残留"）。改动已在本文件登记，架构文档未改（遵守"不改已有 docs"约束）。

19. **B1 · 404 两条路径做等价工作量**
    "不存在"若只查一次 `get_case` 就返回，而"存在但无权"多走 `eval_rows` + 判定 + 审计三次 DB 往返，
    耗时差本身就是存在性预言机（QA 实测 30.9ms vs 3.3ms，≈9.4×）。现在不存在路径同样执行
    `eval_rows`（空集）→ `authorize` → 同构审计写入；实测 P50 比值 **1.02×**，body 逐字节一致（106 bytes）。
    审计里**不落**被探测的 `case_id`（不把可能不存在的 id 填进共享流水）。

20. **B5 · `AUTH_SECRET` 未配置"显式且刺眼"**
    `meta.demo_warnings` 返回风险清单，前端在身份条下方黄字常显；`CASE_DEMO_MODE=false` 时
    `/cases/session` 返回 **503 `CASE_DEMO_DISABLED`**（原为 403 —— 403 会让人以为是权限配置问题，
    而事实是"这条通道在部署形态上就不该存在"）。

---

## 7. 已知限制

- **`MemoryCaseIndex` 的检索质量是"演示级"**：确定性哈希 embedding（中文 1–2 gram → blake2b → 256 维带符号 →
  L2 归一化）只有字面重合度，没有语义近邻。它的作用是**让授权差异可观测、让测试可确定性复现**，
  不用于评价召回质量。评召回请切 `chroma` / `milvus` 并换真实 embedding。
- **`CASE_VECTOR_BACKEND=chroma|milvus` 未在本机实测**：两者都是惰性 import，缺依赖时给出带安装提示的
  `RuntimeError`；四条 Chroma 限制（`$and` 至少两个操作数、`where={}` 报错、空数组用 `__ANY__` 哨兵、
  `$contains` 需单值展开 `$or`）已在适配器里整形，但没有真实服务端跑过。
- **`hidden_count` 在候选充足时恒为 0**（`hidden_count_only_when_short`），见 §3。
  **单位是案例数**（按 `case_id` 去重），响应里带 `hidden_count_unit: "case"`，`meta` 里也声明了口径。
- **`text_hash` 只有写入侧维护**：`update_fields()` 改文本不会 bump `content_version`、也不会重算
  `text_hash`（因此 R3 的"文本 + 版本双一致"校验会**丢弃**这类引用，而不是放行）。这是刻意的：
  绕过写入管道的修改就应该让引用失效；生产若要改内容，必须走 `ingest`/`publish` 通道。

---

## 8. 前端演示屏（`/demo-ui` → 侧边栏「案例助手」）

零构建（原生 JS + 单一 CSS 命名空间 `case-`），不引入任何打包工具。

**操作路径**：选岗位 → 点「建立案例会话」→ 检索 → 点卡片看全文 → 引用到工单；
另有「问答」「岗位对比」「审计」三个页签。

屏幕信息架构（顺序即优先级）：**身份条**（岗位/部门/工厂/用工类型/可见密级 → 建立会话 →
服务端签发的维度令牌 `role:… dept:… plant:… etype:…` 与 `tv/pv`，等宽小字常显）→
**检索区**（关键词 + 动作 + k + 分类下拉）→ **结果区**（⭐ **命中按案例折叠**：同一案例的多个 chunk
合成一张卡，右上角角标"2 个片段"，展开可逐段查看并"引用此段"——列表要按**案例**读，
否则 20 张卡其实只有 10 个案例，现场一问就穿。卡片含 title / category / `status` 徽标 /
`cls_rank` 密级 / 命中片段 / `chunk_type` / `score`（2 位）/ `case_id`）→
**问答 tab** → **岗位对比 tab** → **审计 tab**。

五条展示纪律（写在 `case-screen.js` 文件头，改代码前先读）：

1. **没有、也不接受自定义过滤条件**：请求模型里没有 `filter` 字段，页面上明写"传了会被忽略"；
   页面只**展示**服务端给的 `filter_hash` 与 `filter_explain`（人类可读描述，不含原始表达式）；
2. **`hidden_count` 与 `notice` 必须显著**：产品差异化证据，不折叠、不省略（黄色 notice 条常驻顶部）；
3. **候选不足如实展示**：`notice` 原样显示（"不放宽过滤补齐"），绝不补齐、不"优化"；
4. **问答拒答照抄后端**：`answered=false` 时原样展示 `reason`，前端**不编造兜底答案**；
5. **后端离线不编数据 / 身份只来自服务端**：离线时整屏只显示"需要后端"，绝不回落 `demo-data.js`；
   `/cases/session` 签发 tv=2 令牌，前端自造角色会被 `normalize_role` 挡掉；
   401 只在**本屏**提示"会话已失效，请重新选择身份"，不触发全局登出（案例屏用独立 `mfg_case_token`）。

「岗位对比」是最有说服力的一屏：同一句话、两个岗位、同一个索引，并排给出命中数、不可见数、
候选 chunk 数与差集。实测 `qa`(quality) vs `equipment`(equipment)（同一 query「锡膏印刷偏移 连锡」，k=50）：
`qa` 20 张卡 / 10 个案例 / 不可见 12，`equipment` 18 张卡 / 9 个案例 / 不可见 16，差集为
`qa` 独有 `RC-QA-2026-0009`、`QC-PD-2026-0055`，`equipment` 独有 `RC-EQP-2026-0005`。

> 截图与交互回归脚本：`_shots/shoot_cases.js`（Playwright，装在
> `~/.workbuddy/binaries/node/workspace`，非全局）。它不在测试集里，属手动验收工具；
> 产出 `01_identity_gate.png` / `02_qa_search.png` / `03_equipment_search.png`。

---

## 9. 验证方式

```bash
# 全量回归（案例域 241 条 + 既有全部用例，共 936 条）
python -m pytest tests/ -q --basetemp=./tmp_pt

# 禁止实现自检：应无命中（"先召回后过滤 / 去掉 filter 重试 / where={}"）
grep -rn "without_filter" src/ tests/
grep -rn "filter=None\|filter={}" src/mfg_agents/v2/
```

> `--basetemp` 不是可选项：Windows 上 pytest 结束会批量删除系统临时目录里的用例产物，
> 某些环境的批量删除保护会把整条命令 SIGKILL（拿不到汇总行）。指向项目内目录即可。
>
> **另一个坑**：`--basetemp` 目录若已积累上百个子目录，pytest 启动清场时会触发
> `safe-delete` 的"批量删除需人工确认"保护（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`, count>50），
> 表现为整场 setup error。跑之前 `rm -rf ./tmp_pt` 即可。

六个测试文件各守一段：

| 文件 | 守什么 |
| --- | --- |
| `test_case_acl_compiler.py` | 编译器五阶段、`compile()` 签名无 query_text、跨维 OR 必炸 |
| `test_case_filter_translator.py` | 20+ 条恶意值/恶意字段、空 AST、`expr` 长度上限、fail-closed |
| `test_case_search_e2e.py` | 服务层端到端：差异化、二次授权拦住伪造元数据、审计链 |
| `test_case_redteam.py` | **HTTP 层红队 120 条**：无令牌/空 Bearer/篡改签名 → 401；tv 缺失或 tv=1 → `CASE_TOKEN_SCHEMA_LEGACY`；pv 撤权重放 → `CASE_PRINCIPAL_REVOKED`；六类越权（跨部门/跨工厂/跨密级/客户维/未结案 review/已撤权 withdrawn）各 ≥3 条且两路验证（详情 404 + k=50 检索里没有）；`export`/`share` → 403；非法动作 4xx 非 500；存在性不泄漏；k=50 不放宽；query 注入不改 `filter_hash`；20 次检索后审计链仍 valid 且无明文；响应不含证据层字段/原始客户名/订单号 |
| `test_case_http_e2e.py` | **HTTP 层**完整链路：session→meta→search→detail→cite→ask→ingest→audit；含"无关问题必须拒答""入库落 review 且发布前检索不到"，以及引用契约（`chunk_type` 精确匹配、`source_ticket` 回执、`409 CASE_CITATION_STALE`、非法片段 400） |
| `test_case_no_raw_leak.py` | **绕开 API 直读落地形态**：索引 `texts()`/`rows()` 全部脱敏、每个 chunk `is_knowledge_layer is True`、`case_chunk` 只有 `text_hash` 没有正文列、证据层 `uri` 不出现在知识层 |

---

## 10. B8：探测流量治理（限流，不是审计采样）

**问题**：B1 让"不存在"与"存在但无权"两条路径做等价工作量，代价是**每个 404 都写一行
审计并 commit**。外部扫 `QA-0001..QA-9999` 就是 1 万行 + 1 万次 fsync，审计链可被灌爆。

**为什么不用采样**：直觉解法是"负路径审计按 1/10 采样"，但这会把 B1 刚消掉的时间差请回来
——被采样的 404 更快，攻击者照样能用计时区分。治一个病、添一个病。

**采用**：入口限流（`security/case_rate_limit.py`）。
- 桶键 = `(user_id, client_ip)`：单用 user 挡不住共享账号，单用 IP 会被 NAT 后的工厂出口误伤；
- Redis 在线 → 复用 `core.cache.rate_limit_exceeded`（跨实例共享，生产唯一正确形态）；
- Redis 不可用（demo 默认）→ 进程内固定窗口（**单实例有效，边界写明，不假装是分布式方案**）；
- 阈值 `CASE_RATE_LIMIT_PER_MIN`，默认 60；`0` 或负数 = 关闭（仅本地调试）；
- 作用于 `search` / `ask` / `read` / `cite` 四个入口，超限返回 **429 `CASE_RATE_LIMITED`**。

**不变式**：限流只掐流量，不动审计语义——到达服务层的请求该记一条不少，被 429 挡在门外的
本就没进服务层（记了反而是假审计）。`tests/v2/test_case_rate_limit.py` 钉住这一条：

```
10 passed
```

实测（`CASE_RATE_LIMIT_PER_MIN=5`）：前 5 次 `404` 并写审计，第 6 次起 `429`，
`audit/verify` 仍 `valid: True`。

> 测试注意：限流桶含 `user_id`，Redis 在线时计数**跨进程累积**。用例必须用唯一 `user_id`，
> 否则上一轮跑过的计数会让本轮一开始就被拦成 429——那是测试污染，不是被测行为。
