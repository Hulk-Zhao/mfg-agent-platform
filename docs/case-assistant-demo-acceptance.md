# 案例助手 Demo · 独立验收报告

- 验收人：严过关（QA）
- 验收对象：`D:/tools/mfg-agent-platform` 案例助手 demo（后端服务层 + HTTP 层 + 前端演示屏）
- 验收基线：`docs/case-assistant-ontology-vector-auth-architecture.md`（§4 权限 / §6.3 向量库 / §7.5 执行顺序 / §7.6 候选不足）、`docs/case-assistant-demo-readme.md`
- 验收方式：**不采信实现者自述**；每项给命令、真实输出、结论。未修改 `src/` 下任何业务代码，全部探针与脚本在 `_qa/`。
- 快照纪律：验收期间实现者仍在改动源码（详见附录 A），本文结论以**附录 A 的 sha256 清单**为准；凡在改动前后结论不同的项，均已单独标注。

> ## ⚠️ 报告状态：返工前基线（待复验）
>
> 本报告的基线是 **快照 B（16:03，`_qa/snapshot_final.json`）**。之后 team-lead 已下返工令，
> 实现者正在改 `src/` 与 `tests/`，其中 **R1（`source_ticket_id` 不进出响应）**、**R3（引用校验改为
> 文本 hash + 版本双一致）**、**B1（404 时间对齐）**、**B7（删除 `_shots/`）**、`unknown ACL dim`
> fail-fast 等，正是本报告第 4 / 5 / 7 / 9 / 10 项的整改对象。
>
> **因此：第 4、7 项的"有条件/不通过"结论仅代表返工前状态，不得直接用于上线评审。**
> **第 5 项（铁律④）已用新探针复验并更正为"通过"** —— 原文的"文本漂移漏检"在 16:03 快照上
> 已不成立，是我的口径旧了（详见 §5 的更正说明）。
> 实现者交付新 sha256 清单后，我会以新基线复验第 1、4、5、7、9、10 项并更新本报告（见附录 C 的复验清单与判据）。
> 第 2、3、6、8 项属"授权链路"，返工不改这三条铁律的实现，结论预期保持成立，但仍会回归。
>
> **返工中间态观测已记录**：§7.3 记录了对 16:15 版 `case_search.py`（`f84eb464f51c3d34`）的
> 复查结果——B1 的耗时比值已从 9.4× 降到 1.27×，但**两条路径的 SQL 查询次数仍不等**（差
> 1 次 connect + 1 条 `SELECT case_acl`），且新增了"每个 404 写一行审计"的放大面（B8）。
> 这两条都是复验判据，不是最终结论。

---

---

## 0. 结论表（10 项）

| # | 验收项 | 结论 | 一句话证据 |
|---|---|---|---|
| 1 | 全量回归 | **通过** | `pytest tests/ -q` → **936 passed**（0 failed / 0 skipped），169s；案例域 6 个文件单独跑 **241 passed**（不依赖其它模块的执行顺序） |
| 2 | 铁律① 过滤先于召回 | **通过**（附口径缺陷） | 唯一检索调用点 `case_search.py:199` 且 filter 位置参数必传、全库无 `filter=None`；反例探针：可见集 1 条案例、k=50 → 只返回该 1 条（2 个 chunk），`candidates=4`、`hidden_count=4`，受限案例零命中；适配器 `k=1` 用受限案例原文做 query 仍只返回可见案例 |
| 3 | 铁律② 候选不足不补齐 | **通过** | k=20 时返回 18 条，`notice="结果少于 20 条（已如实返回，不放宽过滤补齐）；另有 16 条因权限范围不可见"`；`case_search.py` 内唯一 `except` 在 `hidden_count` 计数分支，无任何去 filter 重试 |
| 4 | 铁律③ 证据层/原文不进索引·缓存·日志·LLM | **有条件通过** | 索引文档 / 关系库 5 个知识层字段 / `case_chunk` / 审计 / 运行日志 / `data/chroma` 对 17 个 literal + 订单号/图号/手机号/`minio://` 正则**全部零命中**；植入式入库验证 6 类敏感值全部落占位符。**但**：未登录词表的人名（"张三丰"）原样落索引；`source_ticket_id`（`8D-2026-0087`）出现在检索响应 |
| 5 | 铁律④ 引用三元组 / 无引用不输出 | **通过**（口径已更正，见 §5） | `/ask` 5 条断言全部带 `(case_id, chunk_id, content_version)` 且 `chunk_id` 前缀自洽；"食堂几点开饭" 0.136 < 阈值 0.18 → 拒答；`content_version` +1 → 5→3；**只改文本不 bump 版本 → 5→4（`case_chunk.text_hash` 不一致被拒）**，按断言粒度丢弃而非整段拒答 |
| 6 | 铁律⑤ export/share 默认拒绝 | **通过** | 6 主体 ×（export / share / ask+export）共 18 次全部 `403 CASE_ACTION_DENIED`；`case_roles.yaml` 32 角色中 `share` 持有人 **0 个**，`export` 仅 `gm` / `quality_director` 且同样 403 |
| 7 | 铁律⑥ 无权与不存在不可区分 | **不通过** | 状态码一致（404）、响应头一致、**响应体逐字节一致（均 106 字节）**；但耗时 **P50 26.6–30.9ms vs 3.3–3.4ms（7.8–9.4 倍）**、P95 35.9–47.9ms vs 4.9–5.2ms → 稳定的存在性侧信道（两轮各 50 次采样） |
| 8 | 铁律⑦ filter 只由 Principal 决定 | **通过** | 9 个请求体字段注入 + 6 类 query 注入（`|| 1==1`、`DROP TABLE`、零宽字符、全角引号、5000 字、分号注入）全部 `200`，`filter_hash` 与干净请求**完全一致**（`27aaa46f…`），注入后 `case` 表仍在、15 行未变 |
| 9 | 前端真跑（Playwright） | **通过**（附控制台噪声） | 自建会话→检索→切 equipment→同 query 再检索：命中 20→18，`hidden_count=16` 与 `notice` **确实渲染在页面上**（橙色提示条），截图见 `_qa/shots/`；控制台 2 条 `ERR_CONNECTION_REFUSED`（来自 `app.js:75` 硬编码 8004 探测回退，非案例屏） |
| 10 | 代码审查（实现者没说的坑） | **有条件通过** | 16 步顺序与 §7.5 一致；`AclEvaluator` 独立读关系库行集、不碰 AST/向量 metadata；`review` 不入索引。**但**：重复 ingest 会把 `active` 静默退回 `review` 并踢出索引（demo 幂等性坑）；`case_qa` docstring 声称的"二次授权"实际只查了 status |

**合计：通过 7 项 · 有条件通过 2 项 · 不通过 1 项 · demo 阻塞 0 项。**

> **口径更正（第 5 项）**：上一版本报告把铁律④记为"有条件通过"，依据是 15:47 的探针输出
> `ask_after_text_change_no_version → dropped_claims: 0`。该观测在**当时的代码上成立**，但我把它
> 写进了以 16:03 快照为基线的报告里 —— 而 `case_chunk.text_hash` 的写入与校验是在这之间落地的。
> 返工复验时我用 `_qa/probe_r3_citation.py` 重测：同样的"改文本不 bump 版本"，现在 **5→4，丢弃 1 条**，
> `verify_citation` 由 `True` 变 `False`，且**仍然作答**（不是整段拒答）。故第 5 项改为"通过"。
> 这说明我的报告也有"基线漂移"问题——责任在我，已按新证据更正，并把探针留在 `_qa/` 里可复跑。

---

## 1. 全量回归

```bash
.venv/Scripts/python.exe -m pytest tests/ -q --basetemp=./_qa/pt_final2
```

```
936 passed in 169.04s (0:02:49)      # 16:03 快照（_qa/snapshot_final.json，运行期间零漂移）
```

> 实现者自述 "848 passed"：15:44 我实测确实是 848 passed，但那是**旧快照**；此后实现者补了
> `test_case_no_raw_leak.py` 等用例，我最终在 16:03 快照上实测为 936 passed（用例数变了，不是回归漂移）。

案例域单独跑（验证不依赖执行顺序）：

```bash
.venv/Scripts/python.exe -m pytest tests/v2/test_case_acl_compiler.py tests/v2/test_case_filter_translator.py \
  tests/v2/test_case_search_e2e.py tests/v2/test_case_redteam.py tests/v2/test_case_http_e2e.py \
  tests/v2/test_case_no_raw_leak.py -q --basetemp=./_qa/pt_case
```

```
241 passed in 68.75s
```

**过程中抓到的问题（已由实现者修复，保留记录）**：15:50 单独跑 `test_case_redteam.py` 时有 **2 failed**，
而全量 848 passed —— 同一节点"单跑必失败、全跑通过"，属测试间状态耦合：

```
FAILED tests/v2/test_case_redteam.py::test_customer_dimension_grants_when_token_present
FAILED tests/v2/test_case_redteam.py::test_search_response_has_no_evidence_layer_payload
2 failed, 118 passed in 12.35s
```

- 前者根因：案例 ACL 用了 `dim="customer"`，而主体令牌是 `cus:CUST-A`，两者永不相等 →
  案例变成"谁都看不见"。实现者随后把用例改成 `cus`，并在 `case_store.set_acl` 增加维度名
  fail-fast（`ACL 维度名非法`），这个修复是对的。
- 后者根因见第 4 项：`source_ticket_id` 出现在响应体。实现者随后**改写了断言**而不是改实现 ——
  这条我按"口径变更，待批准"处理，不当作已修复。

---

## 2. 铁律①：权限过滤必须先于向量召回

### 2.1 代码证据

`src/mfg_agents/v2/contracts/adapters/case_memory.py:124-146`：

```python
predicate = to_predicate(filter)          # ① 空 filter 直接 raise
query_vector = embed_text(query)
for chunk, row, vector, grams in self._store.values():
    if not predicate(row):                # ② 过滤在打分之前
        continue
    score = 0.6 * _cosine(...) + 0.4 * _keyword_score(...)
```

**不是"先取 topN 再过滤"**：不满足权限的 chunk 根本不参与相似度计算。

### 2.2 唯一检索调用点

```bash
grep -rn "_index\.search(\|index\.search(" src/mfg_agents/
grep -rn "filter=None\|filter={}\|without_filter" src/mfg_agents/
```

```
src/mfg_agents/v2/services/case_search.py:199:        candidates = self._index_service.index.search(
（第二条 grep 无输出）
```

全项目只有一处检索调用点，`filter_node` 是**位置参数必传**；不存在任何"允许不传 filter"的入口。

### 2.3 反例探针（可见集缩到 1 条，k=50）

构造 2 条案例：`QA-OPEN-0001`（无 ACL）+ `QA-SCOPED-0002`（`dept:quality`）。equipment 身份只能看 1 条。

```bash
.venv/Scripts/python.exe _qa/probe_single_visible.py
```

```json
"equipment_k50": { "hits": 2, "distinct_cases": ["QA-OPEN-0001"], "candidates": 4,
  "hidden_count": 4, "notice": "结果少于 50 条（已如实返回，不放宽过滤补齐）；另有 4 条因权限范围不可见",
  "scoped_leaked": false },
"adapter_k1": { "query": "锡膏印刷偏移，部门专属处置。", "returned": ["QA-OPEN-0001"], "leaked": false }
```

- `hits=2` 而不是 1，是因为 `hits` 的**单位是 chunk**（同 case 去重上限 2 个 chunk），去重后 `distinct_cases=1`。
- `candidates=4` = 可见案例的 4 个 chunk 全部参与打分；`hidden_count=4` = 受限案例的 4 个 chunk。
- 适配器 `k=1` 时，用**受限案例的原文**做 query，返回的仍是可见案例 —— 这是"过滤在打分前"最直接的证明
  （若实现为"先召回后过滤"，此处必然返回 0 条）。

### 2.4 `hidden_count` 口径：返工后已改为"案例数"（R2，原为 chunk）

返工前我抓到的问题是：同一句话里 `hidden_count=16`，但真实"看不见的**案例**"只有 6 条——
chunk 数被当成"条"报给前端。`_hidden_count` 现在的实现（`case_search.py`）已改为两条路径都按
`case_id` 去重再相减，并在响应里声明 `hidden_count_unit`：

```json
"hidden_count_unit": { "hard_only_chunks": 52, "visible_chunks": 36, "chunk_diff": 16,
  "total_cases": 15, "visible_cases": 9, "case_diff": 6 }
"short_candidates": { "k": 20, "hits": 18, "hidden_count": 4,
  "notice": "结果少于 20 条（已如实返回，不放宽过滤补齐）；另有 4 条因权限范围不可见" }
```

同一场景下 `hidden_count` 从 **16（chunk）** 变成 **4（案例）**：`chunk_diff=16` 与 `case_diff=6`
的差额说明"按 chunk 数报数"确实会虚高；4 = 软维度不可见的案例数（硬维度已先排除涉密/未结案）。
**口径已自洽，本条不再是缺陷。**

---

## 3. 铁律②：候选不足不得去掉 filter 重试

```bash
.venv/Scripts/python.exe _qa/probe_service.py
```

```json
"short_candidates": { "k": 20, "hits": 18,
  "notice": "结果少于 20 条（已如实返回，不放宽过滤补齐）；另有 4 条因权限范围不可见",
  "padded": false, "hidden_count": 4 }
```

代码侧（含 `except` 分支）确认无降级检索：

```bash
grep -n "fallback\|retry\|放宽\|except" src/mfg_agents/v2/services/case_search.py
```

```
25:  ``0 < hits < k`` → 如实返回 + ``notice``。代码里不存在任何"放宽重试"分支。
128:        except ValueError:            # _parse_date，与检索无关
132:    except ValueError:
459:        except Exception as exc:      # hidden_count 计数失败 → 返回 0，不影响主流程
479:                parts.append(f"结果少于 {k} 条（已如实返回，不放宽过滤补齐）")
```

唯一与检索相关的 `except` 只包住 `hidden_count` 的计数查询，**catch 之后没有第二次 `search()` 调用**。

---

## 4. 铁律③：证据层永不进向量库 / 缓存 / 日志 / LLM 上下文

### 4.1 落地形态全量导出 + 敏感值 grep

```bash
.venv/Scripts/python.exe _qa/probe_leak_sweep.py      # 导出到 _qa/dump/
```

导出面：`index_documents.txt`（真正被 embed 的文本）、`case_knowledge.txt`（关系库 5 个知识层字段）、
`table_case.json` / `table_case_chunk.json` / `table_case_evidence.json` / `table_case_audit.json`、
`runtime.log`（DEBUG 全量）、仓库 `logs/`、`data/chroma/chroma.sqlite3`。

判据：`redaction_rules.yaml` 的 17 个 literal（华辰电子/恒信精工/王建国…）+ 订单号/图号/手机号/`minio://` 正则。

```json
"index_documents":     {"literal_hits": {}, "regex_hits": {}},
"case_knowledge":      {"literal_hits": {}, "regex_hits": {}},
"table_case_chunk":    {"literal_hits": {}, "regex_hits": {}},
"table_case_audit":    {"literal_hits": {}, "regex_hits": {}},
"runtime_log":         {"literal_hits": {}, "regex_hits": {}},
"repo_logs":           {"literal_hits": {}, "regex_hits": {}},
"data_chroma_sqlite":  {"literal_hits": {}, "regex_hits": {}}
```

`table_case` 唯一命中是 `raw_ticket_id: 8D-2026-0087 / 8D-2026-0102 / 8D-2026-0110`（= `source_ticket_id`，见 4.3）；

> **假阳性说明**（避免下一个人重复排查）：对 `data/case.db` 直接做手机号正则扫描时，
> `case_audit.row_hash`（sha256 十六进制串）会偶然命中 `1[3-9]\d{9}`
> （实测 `560a83b8…8e17444193481de…`）。这是哈希子串，不是手机号；判据应限定在
> `case` 表的 5 个文本字段与 `case_chunk`/索引文档上。
`table_case_evidence` 命中 `minio://`（**设计允许**：证据层只存 uri + 密级，不存内容）。

### 4.2 植入式验证（入库管道真的在入库前固化脱敏）

向 `POST /api/v2/cases/ingest` 投一条含 6 类敏感值的草稿：

| 植入值 | 索引文档里 | 关系库里 |
|---|---|---|
| 华辰电子（客户） | 否 | 否（`客户甲`） |
| 恒信精工（供应商） | 否 | 否（`供应商甲`） |
| 王建国（人名） | 否 | 否（`责任人甲`） |
| 订单号 PO-2026123456 | 否 | 否（`[订单号已脱敏]`） |
| 图号 DWG-998877 | 否 | 否（`[图号已脱敏]`） |
| 手机 13812345678 | 否 | 否（`[联系方式已脱敏]`） |

### 4.3 三条"有条件"的缺口

**(a) `source_ticket_id` 原始工单号出现在检索响应**

```bash
.venv/Scripts/python.exe -c "..."   # _qa/probe_leak_sweep 之外的独立探针
{"case_id": "QC-CC-2026-0087", "source_ticket_type": "8d", "source_ticket_id": "8D-2026-0087"}
```

实现者原 red-team 用例 `test_search_response_has_no_evidence_layer_payload` 明确断言
`8D-\d{4}-\d{4}` 不得出现，且**单跑必然失败**。15:51 之后该用例被改写为
"`source_ticket_id` 属于知识层溯源字段、必须出现"，断言被删除。架构文档中我只找到
`source_ticket_id` 作为 BR-11 非空约束 / `derivedFrom` 溯源关系（§6 表、§8.2、事件载荷），
**没有"检索响应必须回传原始工单号"的条款**。→ 需要产品/架构补一句裁定，否则这条属于
"测试被改成适配实现"。

**(b) 白名单式脱敏的覆盖面缺口**

```
"planted_unknown_person_hits": { "index_documents": true, "case_knowledge": true }
```

"张三丰"（不在 `person-whitelist` 里）**原样进入了向量索引**。文档已承认 NER 是 P0 生产项，
demo 用规则通道；但演示现场的语料若出现词表外人名/公司名，会直接落索引且日志、导出全链路可见。

**(c) 种子导入绕过脱敏管道**

`CaseStore.seed_from_json` 直接 `create_case`，**不经过 `RedactionService`**。当前 15 条种子本身干净
（我逐条 grep 过），所以 demo 无泄漏；但只要换一份带客户名的种子，就会绕过 4.2 的全部保护。
→ 生产前应把种子导入也接到同一条管道（或至少加一道 `verify_fields` 断言）。

---

## 5. 铁律④：每个断言必须带引用三元组

```bash
.venv/Scripts/python.exe _qa/probe_service.py
```

```json
"ask_relevant": { "answered": true, "claims": 5, "all_claims_have_citation": true,
  "citation_fields_ok": true, "dropped_claims": 0 },
"ask_irrelevant": { "answered": false, "claims": 0,
  "reason": "未检索到足够相关的案例（最高匹配度 0.136 低于阈值 0.18），已拒绝作答：不拿无关案例凑结论" },
"ask_after_version_bump": { "target_case": "QC-CC-2026-0087", "answered": true,
  "claims": 3, "dropped_claims": 2 }
```

- 没有"无引用仍输出"的断言：`Citation` 结构 + `_citation_ok` 双重校验，`chunk_id` 必须
  `startswith(f"{case_id}::")`，`content_version` 必须与关系库当前版本相等。
- `content_version` +1 后，该案例的 2 条断言被丢弃、其余 3 条保留 —— **按断言粒度丢弃，不是整段拒答**，
  与 §7.8"不一致 → 丢弃该断言"一致。

**返工复验（口径更正，本条由"有条件"改为"通过"）**：

```bash
.venv/Scripts/python.exe _qa/probe_r3_citation.py
```

```json
"target": { "case_id": "QC-CC-2026-0087", "chunk_id": "QC-CC-2026-0087::summary",
            "content_version": 1, "text_hash_recorded": "e41315f5f8dcdec9000d071a5e8d1a18" },
"verify_before": true,
"after_update": { "content_version_in_db": 1, "version_unchanged": true, "summary_changed": true },
"hash_check": { "recorded": "e41315f5…", "recomputed": "f6a6dbc01c75f4a5812c2683abc64319",
                "equal": false },
"verify_after": false,
"after": { "answered": true, "claims": 4, "dropped": 1, "dropped_delta": 1,
           "target_still_cited": false },
"verdict": "PASS(版本+文本双一致)"
```

- 只改 `summary` 文本、**不** bump `content_version`：`verify_citation` 由 `True` 变 `False`，
  该条断言被丢弃（5→4），其余 4 条保留，**仍然作答** —— 按断言粒度丢弃，不是整段拒答。
- 服务端日志同步出现 `[case_index] 引用校验失败：QC-CC-2026-0087::summary 的文本哈希与写入时
  不一致（版本一致但文本被改，拒绝该引用）`，可审计。
- **纠正上一版报告的错误**：15:47 的探针输出 `dropped_claims: 0` 在**当时**的代码上成立，但
  `case_chunk.text_hash` 的写入 + 比对是在那之后落地的；我把它写进了以 16:03 快照为基线的报告，
  属于**我的口径错误**（基线漂移），现已按 16:03 及之后的行为更正。

**仍然存在的两点（不阻塞本条结论）**：

1. `update_fields()` 改文本仍**不** bump `content_version`。引用安全由 `text_hash` 兜住了，
   但版本号本身不再代表内容——审计/对账、缓存失效、`If-Match` 之类的乐观锁都会踩到。
   改动成本很低（`update_fields` 里对文本列自动 `content_version = content_version + 1`）。
2. **存量库迁移风险**：`verify_citation` 里 `if not recorded: return False` —— 若升级前的库里
   `case_chunk.text_hash` 全为空串，升级后**所有断言都会被丢弃**，表现为"答不上来"而不是报错。
   本次演示库是重新种子化的（`data/case.db`：62 个 chunk，`text_hash` 为空 **0** 个），所以现场无感；
   但上线时要带一次回填。

另外 `case_qa._citation_ok` 的注释写"再用二次授权路径确认该案例仍然可见（防并发撤权）"，
实际只判断了 `status in (active, deprecated, superseded, archived)`，**没有调用 `AclEvaluator`、
没有比对 ACL 行**。注释与实现不一致，容易被下一个人当成已有保障（这条记在 §10.4）。

---

## 6. 铁律⑤：export / share 默认拒绝

```bash
.venv/Scripts/python.exe _qa/probe_http.py
```

```
export_share_http:
  qa / equipment / gm / outsource / quality_director / project_manager
  × export / share / ask-export  → 全部 {"status": 403, "code": "CASE_ACTION_DENIED"}
```

角色表侧（`case_roles.yaml`，32 个角色）：

```
roles: 32
share holders: []
export holders: ['gm', 'quality_director']
```

即：`gm` 与 `quality_director` 名义上持有 `case.export`，动作门禁仍短路为 `FALSE_FILTER`（403）；
`share` 对所有人恒定 403。门禁在检索之前，不是"检索完再拒绝"，不会返回任何命中。

---

## 7. 铁律⑥：无权与不存在不可区分 —— **不通过**

### 7.1 状态码 / 响应头 / 响应体

```bash
bash _qa/item7b.sh
```

```
headers_equal(ignoring date): True
body_bytes_equal: True 106 106
```

```
--- 存在但无权 ---
HTTP/1.1 404 Not Found
content-length: 106
{"detail":{"code":"CASE_NOT_FOUND_OR_NOT_VISIBLE","message":"案例不存在，或你没有查看权限"}}
--- 完全不存在 ---
HTTP/1.1 404 Not Found
content-length: 106
{"detail":{"code":"CASE_NOT_FOUND_OR_NOT_VISIBLE","message":"案例不存在，或你没有查看权限"}}
```

这一层做到位了。

### 7.2 时间差（这才是问题）

50 次交替采样：

第一轮（15:57 快照）：
```
forbidden    n=50 P50=  30.9ms P95=  47.9ms max= 152.1ms
nonexistent  n=50 P50=   3.3ms P95=   4.9ms max=  29.0ms
authorized   n=50 P50=  34.8ms P95=  66.9ms max= 147.5ms
```
第二轮（16:07 最终快照，复测同一结论）：
```
forbidden    n=50 P50=  26.6ms P95=  35.9ms max=  59.2ms
nonexistent  n=50 P50=   3.4ms P95=   5.2ms max=  46.7ms
authorized   n=50 P50=  28.4ms P95=  40.9ms max=  61.0ms
```

- **P50 比值 ≈ 9.4×，P95 比值 ≈ 9.8×**，远超我设定的 3 倍红线。
- `forbidden` 与 `authorized` 的耗时几乎一样（30.9 vs 34.8ms），所以攻击者区分不了
  "有权"和"无权"，但能非常稳定地区分"**存在**"和"**不存在**"—— 这就是存在性预言机。

**根因**（读码即可确认，`case_search.read_case`）：

```python
case = self._store.get_case(case_id)
if case is None:
    return None                                  # ← 不存在：1 次 SELECT 就返回
decision = self._evaluator.authorize(principal, self._store.eval_rows(case_id), action)  # ← 多 2 次 SELECT
if not decision.allow:
    self._audit.append(...)                      # ← 再加 1 次链尾查询 + 1 次 INSERT + commit
    return None
```

**影响**：可用来枚举"某个 case_id 是否存在"（含 `QC-PD-2026-XXXX` 这类可预测编号），
与"不可区分"这条铁律的产品意图相反。**demo 演示不阻塞**（没人会现场掐表），**上线前必须修**。
修法建议：不存在路径也执行一次 `eval_rows`（空行 → fail-closed）+ 同构审计写入，把两条路径的
DB 往返次数对齐；或在 API 层对 `read_case` 的 404 路径加固定工作量的补偿查询。

### 7.3 返工中间态观测（`case_search.py` = `f84eb464f51c3d34`，16:15）

> ⚠️ 这是实现者返工**进行中**的一版，不是最终基线。记录它是因为它揭示了一个
> "只测耗时测不出来"的问题，也决定了我在附录 C 里把判据从"耗时"加严到"耗时 + 结构"。

实现者按 B1 改了 `read_case`：不存在路径也走 `eval_rows` + `authorize` + 审计写入。
进程内 60 次交替采样（`_qa/probe_b1_parity.py`）：

```
nonexistent  n=60 P50=  21.1ms P95=  26.9ms     ← 旧版 3.3ms，补了审计写入
forbidden    n=60 P50=  26.7ms P95=  32.6ms
ratio        P50 1.27×  P95 1.21×              ← 旧版 7.8–9.4×，**已达标**
```

**但"不可区分"仍然不成立。** 我用 `sqlite3.Connection.set_trace_callback` 抓两条路径真正下发的
SQL（确定性证据，不受机器负载影响）：

| 路径 | connect | 语句数 | 语句序列（省略参数） |
|---|---|---|---|
| 不存在 | 3 | 6 | `SELECT case` → `SELECT case` → `SELECT row_hash` → `BEGIN` → `INSERT case_audit` → `COMMIT` |
| 存在但无权 | 4 | 7 | `SELECT case` → `SELECT case` → **`SELECT case_acl`** → `SELECT row_hash` → `BEGIN` → `INSERT case_audit` → `COMMIT` |

差在第 3 条：`case_store.eval_rows()` 的实现是

```python
case = self.get_case(case_id)
if case is None:
    return []            # ← 不存在时提前返回，acl_entries() 根本没执行
rows = self.acl_entries(case_id)
```

所以"不存在也走 `eval_rows`"只走到了空转的那一半——**查询次数仍然不等**。
耗时 1.27× 是"两条路径都变慢后分母变大"带来的比值收敛，不是真的对齐；机器一忙或换更快的存储，
比值会重新张开。**因此我把 B1 的判据定为：结构等价（两条路径 connect/execute 计数相同 + SQL 模板一致）
为主，P50 ≤ 1.5× 为辅。**

**副作用（新，记为 B8）**：两条路径都 `INSERT case_audit` + `COMMIT` 后，负路径 P50 从 3.3ms
升到 21ms，且**每个 404 都写一行审计**。`GET /api/v2/cases/{id}` 扫 `QA-0001..QA-9999` 就是
9999 行审计 + 9999 次 fsync —— 审计链可被外部探测流量"灌水"，构成廉价的存储/IO 放大面。
demo 可接受，**上线前需限流或对 `not_found` 审计采样/聚合**。

---

## 8. 铁律⑦：filter 只由 Principal 决定

```bash
.venv/Scripts/python.exe _qa/probe_http.py
```

基线：`filter_hash = 27aaa46fda65f579bf05824d739f5f8834b1d299fc2a880d5d2b3f0b16c81af1`

| 注入位置 | 测试值 | status | filter_hash 一致 |
|---|---|---|---|
| body.filter | `{"tenant":"T999"}` | 200 | ✅ |
| body.where | `{"acl_tokens":{"$contains":"dept:quality"}}` | 200 | ✅ |
| body.acl_tokens | `["dept:management","role:gm"]` | 200 | ✅ |
| body.visibility_class | `0` | 200 | ✅ |
| body.roles | `["gm"]` | 200 | ✅ |
| body.dept | `"management"` | 200 | ✅ |
| body.tenant | `"T999"` | 200 | ✅ |
| body.user_id | `"u-evil"` | 200 | ✅ |
| body.max_classification_rank | `3` | 200 | ✅ |
| query `锡膏印刷偏移 \|\| 1==1` | — | 200 | ✅ |
| query `'}; DROP TABLE case; --` | — | 200 | ✅ |
| query 零宽字符 `锡膏\u200b\u200c印刷偏移` | — | 200 | ✅ |
| query 全角引号 `“锡膏印刷偏移”` | — | 200 | ✅ |
| query 5000 字符 | — | 200 | ✅ |
| query `锡膏; DELETE FROM case_acl; --` | — | 200 | ✅ |

注入后库结构完好：

```json
"tables_after_sqli": ["case","case_acl","case_audit","case_chunk","case_evidence","sqlite_sequence"],
"case_rows_after_sqli": 15
```

结构原因也成立：`CaseSearchRequest` 里**没有** `filter` 字段（pydantic 默认忽略多余字段），
`AclCompiler.compile(principal, action, policy, env)` 签名里没有任何 query 入参。

令牌侧 fail-closed（同一探针）：

```json
"token_failclosed": {
  "legacy_tv1":   {"status": 401, "code": "CASE_TOKEN_SCHEMA_LEGACY"},
  "missing_pv":   {"status": 401, "code": "CASE_PRINCIPAL_REVOKED"},
  "tampered_sig": {"status": 401, "code": "CASE_TOKEN_INVALID"},
  "garbage":      {"status": 401, "code": "CASE_TOKEN_INVALID"}
}
```

---

## 9. 前端真跑（Playwright）

一条命令内完成"起服务 + 浏览器操作"（沙箱会回收后台进程）：

```bash
bash _qa/item9_ui.sh
```

说明：本机没有系统 Chrome，`playwright-cli` 默认 `channel=chrome` 启动失败
（`Chromium distribution 'chrome' is not found`），改用已安装的 **Microsoft Edge**（同为 Chromium 内核）：

```bash
playwright-cli open --browser msedge
playwright-cli run-code "$(cat _qa/ui_flow.js)"
```

真实返回（`_qa/ui_result.txt`）：

```json
{"gate_loaded":true,"qa_token_visible":4,
 "qa":{"status":200,"hits_in_json":20,"hidden_count_in_json":0,"hit_cards_in_dom":20,
       "notice_text_in_dom":"(页面上没有 notice 元素)"},
 "equipment":{"status":200,"hits_in_json":18,"hidden_count_in_json":16,
   "hit_cards_in_dom":18,
   "notice_text_in_dom":"! | 另有 16 条因权限范围不可见（只在候选不足时计数，避免泄漏总量） | 结果少于 20 条（已如实返回，不放宽过滤补齐）；另有 16 条因权限范围不可见"},
 "result_count_changed":true,
 "console_errors":["[console] Failed to load resource: net::ERR_CONNECTION_REFUSED",
                   "[console] Failed to load resource: net::ERR_CONNECTION_REFUSED"]}
```

- **hidden_count / notice 确实渲染在页面上**（橙色提示条，肉眼可读），不是只在 JSON 里；
  见截图 `_qa/shots/03_equipment_search.png`。
- 切换身份后结果条数**真的变了**：20 → 18，且 `RC-QA-2026-0009`（岗位经验案例）只出现在 qa 结果里。
- 控制台 2 条 error：来自 `app.js:75` 硬编码候选基址 `http://127.0.0.1:8004` 的探测失败回退，
  与案例屏无关（案例接口全部同源 8124，`uvicorn` 日志无 5xx）。**演示时 F12 会看到红色报错**，
  属于观感问题；更要紧的是：若 8004 上恰好有别的服务，前端会把它当后端用。
- 服务端日志确认无 500/503。

**截图路径（我重拍的，非实现者那 5 张）**

| 文件 | 内容 |
|---|---|
| `_qa/shots/01_identity_gate.png` | 案例屏身份门禁（未建会话） |
| `_qa/shots/02_qa_search.png` | qa 身份检索：20 条、无 notice 条 |
| `_qa/shots/03_equipment_search.png` | equipment 身份同 query：18 条 + "另有 16 条不可见" |

**实现者自述的 5 张截图不可复现**：`_shots/ui_check.py` 里用的是 `#case-login`，而当前 DOM 的按钮是
`#case-session`（`grep -rn "case-login" src/mfg_agents/v2/demo/ui/` 无结果）；且 `_shots/*.png` 生成于
15:40，`case-screen.js / app.js / app.css / index.html` 在 15:46–15:48 又被改过。
按现版本脚本重跑会在第 4 步 `page.click("#case-login")` 处超时。

---

## 10. 代码审查

### 10.1 16 步顺序 vs 架构 §7.5

| §7.5 步骤 | 代码落点 | 一致 |
|---|---|---|
| 1 verify_token / 2 pv | 上游 `principal_resolver.from_token` + `search()` 内 pv 比对 | ✅ |
| 3 动作门禁 / 4 显式 DENY / 5 RBAC | `AclCompiler.compile_explained` 阶段 0/1/2 | ✅ |
| 6 编译 FilterAST | 同上阶段 3/4/5 | ✅ |
| 7 翻译为后端 filter | `filter_translator.to_predicate/to_chroma_where` | ✅ |
| 8 过滤下推召回 | `case_search.py:196` `index.search(query, filter_node, …)` | ✅ |
| 9 Rerank | `_default_rerank`（可注入） | ✅ |
| 10 同 case 去重 ≤2 | `_dedupe_by_case` | ✅ |
| 11 二次授权（独立路径） | `_second_stage` → `AclEvaluator` | ✅ |
| 12 排序 | `_sort` | ✅ |
| 13 组装上下文 + 引用校验 | **search 只组装，引用校验在 `case_qa`** | ⚠️ 见下 |
| 14 hidden_count | `_hidden_count`（只硬维度计数） | ✅ |
| 15 审计（只哈希） | `_audit.append` | ✅ |
| 16 返回 | `SearchResponse.to_dict` | ✅ |

第 13 步偏差：`/cases/search` 返回的 `citation.content_version` 取自**向量索引 metadata**，没有回查关系库；
关系库版本已 bump 而索引未重建时，检索侧会给出"过期引用"。`/cases/ask` 会拦住（该断言被丢弃），
但 `/cases/search` + 前端"引用"按钮不会。属于口径问题，不是越权。

### 10.2 AclEvaluator 是否独立

是。`acl_evaluator.authorize(principal, case_acl_rows, action, env)` 只吃
`CaseStore.eval_rows()`（`case_acl` 行 + 5 个系统标量行），**完全不碰 `FilterNode`、不读向量 metadata**，
做的是行集集合运算。二次授权与前置编译是两套独立实现，"一个 filter 翻译 bug 同时打穿两层"的风险被规避。

一处隐性耦合值得记：evaluator 用的是 `case.visibility_class` **列**（`eval_rows` 里作为系统行），
而 `case_store.set_acl()` 只 bump `acl_version`、**不重算 `visibility_class`**。若哪天有人绕过
`set_visibility` 直接 `set_acl`，关系库会认为该案例仍是 `vc=0`（全员可见）。当前方向上是"变松"，
但前置过滤器读的是索引 metadata（由 `acl_entries` 现算），会把它拦住；两层 AND 下没有实际越权，
只是这条依赖关系没有任何断言保护。建议 `set_acl` 内部同步调用 `set_visibility`。

### 10.3 `case_ingest` 会不会把 draft/review 写进索引

不会。`sync_case()` 先 `replace_chunks` 写映射行（`vector_status=pending`），随后
`if status not in VISIBLE_STATUS: index.delete_by_case(); set_chunk_status("stale")`。
实测生命周期：

```
after ingest:  status=review   indexed_chunks=[]
after publish: status=active   written=4  indexed=[…::countermeasure, …::phenomenon, …::root_cause, …::summary]
```

**但抓到一个实现者没说的坑**：

```
after re-ingest: status=review  indexed=[]   detail visible=False
```

对同一 `source_ticket_id` 重复 ingest（工单系统重投事件、人工补录），`create_case` 的
`ON CONFLICT DO UPDATE` 会把**已经发布的 `active` 案例静默改回 `review` 并踢出索引**，
没有任何告警、没有版本号保护、没有"已发布则拒绝覆盖"的判断。demo 里不会发生（没人重复点），
生产里这是典型的数据抖动源。

### 10.4 `case_qa` 引用校验会不会误删全部断言

**不会误删，也不会漏检文本漂移**（返工后复验，见 §5）：
`valid = [c for c in claim.citations if self._citation_ok(c)]`，只要有一条引用失效就丢整条断言
（对模板生成的单引用断言等价）。文本变化但 `content_version` 不变时，`case_chunk.text_hash`
比对失败 → 该条断言被丢弃，其余保留（实测 5→4）。
`chunk_id` 用 `case_id::chunk_type` 推导，chunk 类型固定 4 类，不存在"轻微改写导致 chunk_id 漂移"的问题。

**仍存在的两处**：① `_citation_ok` 里那段"再用二次授权路径确认该案例仍然可见（防并发撤权）"
只判断 `status`，没有走 `AclEvaluator`（注释比实现强）；② `update_fields` 不 bump
`content_version`，版本号语义漂移（`text_hash` 兜住了引用安全，但兜不住版本号本身）。

### 10.5 `principal_resolver` 老令牌 / 缺字段 fail-closed

生效，且我按 §4.6 逐条验证过（见第 8 节表格）：`tv<2` → 401 `CASE_TOKEN_SCHEMA_LEGACY`；
缺 `pv` → 401 `CASE_PRINCIPAL_REVOKED`（0 ≠ 服务端 1）；签名篡改 → 401；
`tv=2` 但缺维度字段 → 不 401，按"空 = 无授权"处理（`principal_tokens` 少一项 → 受限案例不可见）。

---

## 11. 遗留风险清单

### A. demo 可接受（演示前建议口头说明）

| # | 风险 | 依据 |
|---|---|---|
| A1 | `hidden_count` 单位是 chunk，文案说"条"（16 vs 6 条案例） | 第 2.4 节 |
| A2 | 检索响应带 `source_ticket_id`（`8D-2026-0087`） | 第 4.3(a)；需产品/架构一句裁定 |
| A3 | 前端控制台 2 条 `ERR_CONNECTION_REFUSED`（8004 探测回退） | 第 9 节 |
| A4 | `_shots/` 5 张截图与其脚本已与当前 UI 不一致 | 第 9 节；用 `_qa/shots/` 替代 |
| A5 | `CASE_VECTOR_BACKEND=chroma/milvus` 未实测（惰性 import，缺依赖报错） | README 已声明；我只验了 memory 后端 |
| A6 | 召回质量是哈希 embedding（字面重合），不能用于评价召回率 | README 已声明，代码注释也写了 |
| A7 | `hidden_count` 只在候选不足时计算（k=10 恒为 0） | 设计如此（§7.6），但演示时容易被问"为什么没差异"，建议演示统一用 k=20 |

### B. 上线前必须修

| # | 风险 | 影响 | 建议 |
|---|---|---|---|
| B1 | **404 时间侧信道 9.4×**（返工中间态已降到 1.27×，但查询次数仍差 1 次 connect + 1 条 `SELECT case_acl`） | 存在性枚举 | 两条路径**结构等价**：不存在时也执行 `acl_entries()`（返回空集），使 connect/execute 计数与 SQL 模板一致；单纯"耗时达标"不算修好（见 §7.3） |
| B2 | 重复 ingest 把 `active` 静默退回 `review` 并踢出索引 | 数据抖动、案例"消失" | `create_case` upsert 前判断：已发布案例只允许"更新内容 + bump version"，不允许降级状态 |
| B3 | `update_fields` 改文本**不 bump `content_version`**（引用安全由 `text_hash` 兜住，但版本号本身不再代表内容）；存量库 `text_hash` 为空串时升级后会丢弃**全部**断言 | 审计/对账、缓存失效、乐观锁失真；老库升级表现为"答不上来"而非报错 | 文本列写入时自动 `content_version + 1`；上线前带一次 `text_hash` 回填 |
| B4 | 脱敏是白名单 literal，词表外人名/公司名直落索引（实测"张三丰"进入索引） | PIPL/商业秘密 | 接 NER 或至少加"疑似人名/机构名"兜底规则；种子导入接同一管道 |
| B5 | `AUTH_SECRET` 未配置 → 进程级随机密钥（`[auth] 未配置 AUTH_SECRET…`） | 重启后令牌全失效；多副本互不认 | 部署清单强制 `AUTH_SECRET` |
| B6 | 存在性 / `hidden_count` / 审计等口径未写成可执行断言（除 red-team 外） | 回归易失守 | 把第 2.4、4.3(a)、7.2 三条固化为用例 |
| B7 | `AclEvaluator` 依赖 `case.visibility_class` 列，但 `set_acl` 不重算 | 未来改动易越权 | `set_acl` 内同步 `set_visibility` |
| B8 | 补 B1 后**每个 404 都写一行审计**（`INSERT + COMMIT`/次） | 探测流量可放大审计表与 IO（扫 1 万个 id = 1 万行 + 1 万次 fsync）；负路径 P50 3.3ms → 21ms | 对 `not_found` 审计限流/采样或按 `(actor, 时间窗)` 聚合成计数；限流器前置 |

### C. 架构层观察（不影响本次验收）

- 两条并存的检索栈：新的 `case_knowledge`（有 ACL）与旧的 `failure_cases`（MCP `knowledge_search`
  → `common/rag_tools.search_cases`，**无任何 ACL**，返回 `document` 全文）。旧栈语料是另一份
  （`data/knowledge/*.jsonl`，synthetic），当前不构成案例域泄漏；但"案例助手"上线后若有人把
  案例灌进旧栈，ACL 就被绕过了。建议在 README/上线清单里显式写上"旧栈不得承载案例正文"。

---

## 12. 演示可信度评分

### **81 / 100**

扣分项（合计 -19）：

| 扣分 | 理由 |
|---|---|
| −8 | 铁律⑥没做到"不可区分"：body 一致但耗时 9.4×，是稳定的存在性预言机。这条是本次唯一的"不通过" |
| −5 | 证据链瑕疵：`source_ticket_id` 泄漏断言被"改测试"而不是改实现；`_shots/` 5 张截图与脚本对不上当前 UI |
| −1 | 引用链的**次级**瑕疵：`_citation_ok` 注释声称"二次授权"但只查 status；`update_fields` 不 bump `content_version`（引用安全已由 `text_hash` 兜住，故只扣 1 分） |
| −3 | 重复 ingest 会让已发布案例静默下线（数据治理坑） |
| −2 | `hidden_count` 用 chunk 数冒充"条"，演示现场会被问穿 |

> 上一版此项记为 −4（"引用校验只比版本号"）。返工复验证明 16:03 快照已实现"版本 + 文本 hash 双一致"，
> 依据不成立，故改为 −1。相应总分 **78 → 81**。这是我主动更正自己的扣分，不是给实现者加分。

加分理由（为什么仍有 81）：**demo 要证明的四件事我都独立复现了**——
① 同一 query 不同岗位结果不同（20 vs 18，差集可复现）；
② 授权发生在召回之前（反例探针在适配器层直接证明）；
③ 候选不足如实返回、不补齐（notice 原文一致）；
④ 越权一律 404 且响应体逐字节一致、export/share 一律 403、请求体/query 注入改不动 `filter_hash`。
另外 `review` 不入索引、证据层内容不进索引/日志/审计、审计链完整（`chain_valid=true`，37 行）都成立。
**这套 demo 可以拿去演示"权限前置 + 差异可见"，但不能拿它宣称"已经可以上线"。**

---

## 附录 A：验收快照（sha256 前 16 位）

完整清单见 `_qa/snapshot_final.json`（162 个文件）。

| 文件 | sha256[:16] |
|---|---|
| `src/mfg_agents/v2/services/case_search.py` | `e140842a66552b24` |
| `src/mfg_agents/v2/services/case_store.py` | `de49b96bfd1e001e` |
| `src/mfg_agents/v2/services/case_qa.py` | `ea1133d7dd57fc53` |
| `src/mfg_agents/v2/security/acl_evaluator.py` | `0e550e062c7f4729` |
| `src/mfg_agents/v2/demo/api.py` | `c9cab237607818bc` |
| `src/mfg_agents/v2/services/case_ingest.py` | `325be47052da85f0` |
| `src/mfg_agents/v2/services/principal_resolver.py` | `aab4c4fc097b2b7e` |

**快照漂移记录**（实现者在验收窗口内持续改动，故本文标注时点）：

| 时间 | 变动 | 对结论的影响 |
|---|---|---|
| 15:44 前 | 初版 | 全量 848 passed |
| 15:45–15:51 | `case_search.py` / `filter_translator.py` / `case_store.py`(+维度校验) / `test_case_redteam.py`(改写断言) / 新增 `test_case_no_raw_leak.py` | 15:50 单跑的 2 个失败消失；`source_ticket_id` 断言被移除 |
| 15:56 | 快照 A | 全量 **932 passed**；案例域单跑 237 passed |
| 16:00–16:03 | `api.py` / `test_case_http_e2e.py` | 快照 B（最终）：全量 **936 passed（169s，运行期间零漂移）**；案例域单跑 **241 passed** |

## 附录 B：证据文件索引

| 文件 | 内容 |
|---|---|
| `_qa/pytest_final2.log` / `_qa/pytest_final.log` | 全量回归日志 |
| `_qa/pt_case2/`（配合 §1 的命令） | 案例域 6 个文件单独运行，241 passed |
| `_qa/probe_service.json` | 服务层探针（铁律①②④⑤、差异化矩阵、filter_hash） |
| `_qa/probe_single_visible.py` 输出 | 可见集=1 的反例（铁律①） |
| `_qa/probe_http_final.json`（另有 `probe_http.json` 为 15:49 旧快照） | HTTP 探针（铁律⑤⑥⑦、令牌 fail-closed、审计明文） |
| `_qa/probe_leak_sweep.py` / `_qa/probe_leak.json` / `_qa/dump/` | 铁律③ 全量导出与 greps |
| `_qa/item7b.sh` / `_qa/timing_404b.txt` | 404 逐字节对比 + 50 次时间采样 |
| `_qa/item9_ui.sh` / `_qa/ui_flow.js` / `_qa/ui_result.txt` | 前端真跑脚本与返回 |
| `_qa/shots/01_identity_gate.png`、`02_qa_search.png`、`03_equipment_search.png` | 前端截图（我重拍） |
| `_qa/uvicorn_*.log` | 真实 uvicorn 日志（无 5xx） |
| `_qa/snapshot_final.json` | 验收快照 sha256 清单 |
| `_qa/probe_b1_parity.py` / `.json` | 404 两条路径的 **SQL 语句级**对比（`set_trace_callback` 抓取）+ 进程内耗时采样；B1 结构性判据的证据来源 |
| `_qa/probe_r3_citation.py` / `.json` | 改文本不 bump 版本 → `verify_citation` 由 True 变 False、断言 5→4 的复验探针（R3 / 铁律④） |

---

## 附录 C：返工后复验清单（待执行）

实现者交付新基线（`src/**` + `tests/v2/**` + `data/case_seed.json` 的 sha256 前 16 位）后，按下列判据复验；
只有全绿才把对应项从"有条件/不通过"改为"通过"。

| 复验项 | 命令 / 方法 | 判据 |
|---|---|---|
| 全量 + 单跑 | `pytest tests/ -q`，再单独 `pytest tests/v2/test_case_*.py -q` | 两者都 0 failed；**单跑必须与全量同绿**（上次全量掩盖单跑失败是返工项之一） |
| R1 工单号 | `bash _qa/item7b.sh` 的 body 抓取 + `grep -E '8D-[0-9]{4}-[0-9]{4}'` 搜 `/search`、`/ask` 响应 | `/search`、`/ask` 响应体 **零命中**；`source_ticket_ref` 形如 `st_[0-9a-f]{10}` 且稳定（同 case 两次请求一致）；`GET /cases/{id}` 仅在 `read` 且有权限时返回真号并带 `X-Case-Trace: full` |
| R3 引用校验 | `python _qa/probe_r3_citation.py`（已执行 ✅） | 改 `summary` 但**不** bump `content_version` → `verify_citation` False、断言 **5→4（丢 1）**、仍作答；bump 版本 → 5→3（丢 2）。**已通过**：`PASS(版本+文本双一致)` |
| B1 时间/结构对齐 | `python _qa/probe_b1_parity.py`（确定性 SQL 计数）+ `bash _qa/item7b.sh`（HTTP 层 50 次交替） | **① 结构等价（主判据）**：不存在 / 存在但无权两条路径 `connect` 次数与 `execute` 次数相同，SQL 模板一致（只差绑定参数）；**② 时序（辅判据）**：状态码/响应头/响应体逐字节一致（106 字节不能破），P50 比值 ≤ 1.5× |
| B8 404 审计放大 | `grep -c "case.*denied" data/case.db` 前后对比 / `probe_b1_parity.py` 的 `execute` 计数 | demo 只记录；**上线前**确认有限流或 `not_found` 审计采样，否则回填 B 类风险 |
| 未知 ACL 维 | 直接 `store.set_acl(dim="customer")` | 抛 `CaseStoreError`（含"ACL 维度名非法"），且 `cus` 维正向可见 |
| 重复 ingest | 我的生命周期探针：ingest → publish → 再 ingest 同 source | 已发布案例 **不得**被静默退回 `review`；至少要有显式拒绝或版本保护 |
| 前端 | `bash _qa/item9_ui.sh`（playwright-cli + msedge） | qa / equipment 命中数不同、`hidden_count` 与 `notice` 渲染在页面、控制台 0 条 error（8004 探测回退若已修则应为 0） |
| 截图 | 确认 `_shots/` 已删、README 指向 `_qa/shots/` | 对外证据只有一个来源，且可复现 |

> 复验只跑一轮；若新基线仍有不通过项，我会直接出"遗留问题清单"并明确"阻塞上线/不阻塞 demo"，不再进入第三轮。
