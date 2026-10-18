# DESIGN.md — 制造平台企业问答助手（kbqa 通道）按 ADR-001 架构落地

> 替代 2026-10-18  earlier EduRAG 版设计（目标已由 Hulk 明确：不改 EduRAG，
> 制造平台自己的企业问答助手按 ADR-001 架构走）。上游决策：`docs/adr/ADR-001-企业问答助手检索架构.md`。
> 实施范围：本仓库 `D:/tools/mfg-agent-platform`。

## 0. 结论与范围

**通道界定**：「企业问答助手」= 现有 kbqa 通道（`POST /api/v2/kbqa`，企业制度问答，Milvus `enterprise_faq` + LLM）。
不含 office 通道（`/api/v2/office/chat`，带工具调用的企业助手 Agent——形态不同，另行评估）。

**迭代一（本期）**：把 kbqa 从「纯 RAG」升级为 ADR-001 的 L0/L1/L2 三层 + 统一契约 + 晋升数据面：

| 层 | 现状 | 本期动作 |
|---|---|---|
| L0 缓存 | Redis `mfg:kbqa:sha1(query\|top_k)` TTL 1800，无版本 | 键加 `faq_version` 命名空间；只包 L2 昂贵路径 |
| L1 FAQ 前置 | **缺失**（`kbqa_policy` 结构化权威表现在只在 Text2SQL 侧） | 新增 FAQMatcher：`kbqa_policy ∪ kbqa_faq` 词面计分 + 阈值/margin |
| L2 RAG 兜底 | search_kb + LLM + 降级回放 | 逻辑不动，仅纳入契约 |
| 契约 | `{answer, tags, sources, category, degraded}` | 增补 `channel/confidence/latency_ms/faq_version`（存量字段不动，前端无感） |
| 晋升数据面 | 无 | 新表 `kbqa_faq` / `kbqa_faq_draft` / `kbqa_query_log` + feedback/drafts/publish/stats 端点 |
| 前端 | kb-screen 无通道标识、无反馈 | 加 channel 角标 + latency + 👍 |

**不在本期**：L1 语义匹配（embedding，度量驱动）；ACL（见 §6 既有口径）；office/chat/cases 契约统一；前端大改。

## 1. 现状事实（已核实）

| # | 事实 | 位置 |
|---|---|---|
| F1 | kbqa 全链路：限流 → Redis 缓存 → `search_kb`（BGE-M3 混合召回+Reranker）→ LLM（deepseek-flash）→ 不可用时降级回放片段 | `v2/services/kbqa.py`、`common/rag_tools.py:260` |
| F2 | 缓存键 `mfg:kbqa:{sha1(query\|top_k)}`，TTL 1800s，**无版本维度** | `kbqa.py:71-84` |
| F3 | `kbqa_policy` 结构化表：`id/dept/dept_cn/policy_no/category/title/content/keywords`，SQLite+MySQL 双种子，**全员可读** | `data/seed_v3.sql:111`、`data/seed_kbqa.sql` |
| F4 | Text2SQL 侧已有 `kbqa_policy_search` 命名查询（LIKE 关键词），未接入问答主链路 | `v2/data/queries.yaml:129` |
| F5 | 只读数据通道 `DataSourceRegistry.execute_readonly_sql`（dashboard 范式） | `v2/services/dashboard.py:7` |
| F6 | 业务库写路径无现成服务范式（DataSource 只读；workflow/case/spc 各自为政） | — |
| F7 | 前端零构建，kb-screen.js；测试约定 `tests/v2/test_kbqa_seed.py`（离线 + docstring 表格） | — |
| F8 | 平台 health 已诚实化（5 探针，不拉编排器） | `v2/services/health.py` |
| F9 | `search_kb` 无 metadata 过滤参数（未来 ACL 的已知限制） | `rag_tools.py:260` |

## 2. 方案权衡

**D1 L1 匹配怎么实现**
- A. SQL LIKE 计分（命名查询）——多关键词加权在 SQL 里笨拙且无法细粒度单测。**否决**
- B. Python 语料加载（一次只读 SQL 取全量，百行级）+ 中文二元组计分 + 阈值/margin——**采纳**：sub-ms、逻辑可离线单测、与 Milvus 解耦（Milvus 挂了 L1 照常工作）、零新依赖
- C. L1 上 embedding 语义匹配——二期选项：当前语料百条级词面已够，且会给每问加编码开销；留待度量证明

**D2 缓存版本化**
- A. 缩短 TTL——窗口仍在。**否决**
- B. 键内嵌 `faq_version = policy:{count}:{max_id}|faq:{count}:{max_id}`（一次轻量查询取得，两表百行级）——**采纳**：任何 FAQ 写操作即时换命名空间，旧键自然不可达，无需主动失效扫描
- L0 只包 L2：L1 每问全量重算（sub-ms），不占缓存正确性面

**D3 晋升数据落点**
- A. 写进 `kbqa_policy` 主数据——污染制度主数据（policy_no 命名空间/部门分类）。**否决**
- B. 新表 `kbqa_faq`（晋升对）+ `kbqa_faq_draft`（待审）+ `kbqa_query_log`（度量）——**采纳**：主数据干净、晋升可审计、KG 判据有取数处

**D4 管理端点保护**（Hulk 已确认）：仅 127.0.0.1 可调；配置 `KBQA_ADMIN_TOKEN` 后任意来源需令牌。

## 3. 目标链路

```
POST /api/v2/kbqa
 → 限流（现有不动）
 → 归一化 query（strip/lower/全半角/空白折叠）
 → L1 FAQMatcher
     DataSourceRegistry.execute_readonly_sql 取 kbqa_policy ∪ kbqa_faq（status=active）
     + faq_version 戳
     中文二元组计分（title×3 / keywords×2 / content×1，query 词覆盖度；同题直中=1.0）
     命中条件：score ≥ FAQ_MATCH_THRESHOLD 且 (top1 - top2) ≥ FAQ_MARGIN_THRESHOLD
     命中 → 契约(channel=faq, confidence=score, sources=[policy_no/faq_id])，**不调 LLM**
 → L2 RAG（现有 search_kb + LLM，降级回放保留）
     未命中/低分 → 契约(channel=rag；RAG 也无命中 → channel=miss)
     L2 结果经 L0 缓存：mfg:kbqa:v2:{faq_version}:{sha1(norm|top_k)}
 → 统一契约序列化（存量字段不动，新增 channel/confidence/latency_ms/faq_version）
 → 写 kbqa_query_log（同步，毫秒级）

POST /api/v2/kbqa/feedback {query, answer, rating, session_id, channel}
     rating≥4 且 channel=rag → kbqa_faq_draft(pending)
GET  /api/v2/kbqa/drafts            （localhost 或 X-FAQ-Admin-Token）
POST /api/v2/kbqa/drafts/{id}/publish
     → kbqa_faq INSERT(active) → faq_version 变 → L0 旧键不可达 → 同问立即走 L1
GET  /api/v2/kbqa/stats
     近 7 日 channel 分布 / 低置信 RAG 占比 / top 待晋升问题（ADR 的 KG 判据取数处）
```

## 4. 数据模型（新表，双种子幂等）

```sql
-- SQLite（data/seed_v3.sql 追加，IF NOT EXISTS + INSERT IGNORE 惯例）
CREATE TABLE IF NOT EXISTS kbqa_faq (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  keywords VARCHAR(200),
  origin VARCHAR(32) NOT NULL DEFAULT 'manual',   -- manual / rag_promoted
  source_ref VARCHAR(60),                          -- 关联 policy_no 或 query_log.id
  dept VARCHAR(40), dept_cn VARCHAR(30),
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  created_by VARCHAR(64), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kbqa_faq_draft (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL, answer TEXT NOT NULL,
  origin VARCHAR(32) NOT NULL DEFAULT 'rag_feedback',
  query_log_id BIGINT, status VARCHAR(16) NOT NULL DEFAULT 'pending',
  reviewer VARCHAR(64), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kbqa_query_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id VARCHAR(36), question TEXT NOT NULL,
  channel VARCHAR(16) NOT NULL, confidence REAL, top_score REAL,
  latency_ms INTEGER, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

MySQL 侧 `data/seed_kbqa.sql` 同构（`INT AUTO_INCREMENT PRIMARY KEY`）。方言差异只存在于 DDL，运行期 SQL 全部标准参数化，不出现方言分支。

**读写路径**：新增 `v2/services/kbqa_store.py`——SQLAlchemy `create_engine(settings.DB_URL)`（同 `init_db` 范式），`text()` + 参数化；只碰上面三张 kbqa 新表 + 读 `kbqa_policy`，不碰主数据。需守 `platform_schema.is_sqlite_target()` 判定纪律（memory 硬规则）；本期 DDL 双文件已含方言，运行期不写方言分支 SQL。

## 5. 契约与配置

响应契约（`kbqa.py` 出口统一构造，存量字段零改动）：

```python
{
  "answer": str, "tags": [...], "sources": [...], "category": {...}, "degraded": bool,  # 存量
  "channel": "faq" | "faq_cached" | "rag" | "miss" | "rag_unavailable",              # 新增
  "confidence": float | None,      # L1=覆盖度分；L2=None
  "latency_ms": int,               # 新增
  "faq_version": str,              # 新增（排查缓存问题的钥匙）
}
```

配置（`common/config.py` settings，env 可覆盖）：

| 项 | 默认 | 说明 |
|---|---|---|
| `KBQA_MATCH_THRESHOLD` | 0.70 | **定标值**：0.62 时单个 2 字符关键词命中即 0.667 可独自过闸（"酒店发票抬头怎么写"误答差旅制度）；0.70 要求关键词之外还有标题/内容佐证 |
| `KBQA_MARGIN_THRESHOLD` | 0.15 | top1-top2 差，防"两个都能答"时乱指 |
| `KBQA_ADMIN_TOKEN` | 空 | 空 = 仅 localhost 可调管理端点 |
| `KBQA_LOG_WRITES` | true | query_log 同步写开关 |

## 6. 为什么 ACL 不在本期（不是遗漏）

kbqa 知识口径既定「公开制度全员可读」（`seed_v3.sql:107` 注释、`libraries.py:57`），且 `kbqa_policy.dept_cn` 列已在。若未来出现部门隔离制度：L1 语料加载按 dept 过滤、L2 需先给 `search_kb` 加 metadata 过滤（F9 已知限制）——两者都是增量，不推翻本期结构。**不预先造没有数据支撑的权限映射**（mfg 行级范围同款教训）。

## 7. 验收标准

1. `pytest tests/v2/ -q` 全绿（不破现有基线）；新增 kbqa 测试 ≥20 条，全部离线（fake DataSource/fake search_kb/fake LLM）
2. 「年假怎么休」类查询：`channel=faq`，LLM 调用计数为 0（单测 fake 计数）
3. margin 不足 / 分数不足 → 落 L2（单测）；`search_kb` 抛错时 L1 路径不受影响（单测）
4. publish 后 `faq_version` 变化、旧缓存键不可达、同问命中 `channel=faq`（单测 + 端到端）
5. `/api/v2/kbqa/stats` 返回 channel 分布；feedback rating≥4 且 channel=rag → draft pending（单测）
6. 管理端点：非 localhost 无 token → 403（单测）
7. 前端 kb-screen：channel 角标 + latency + 👍 可见；存量回答渲染不回归
8. 端到端（本机 SQLite 演示路径）：起服务 → 制度直答 → publish 一条 FAQ → 同问走 L1，截图级证据

## 8. 风险预演

| 风险 | 概率 | 缓解 |
|---|---|---|
| 二元组计分误命中（词面像但不是一问） | 高 | 阈值+margin 双闸；title/keywords 高权重 content 低权重；初始阈值标注待定标；stats 端点提供误命中观测 |
| 新表只加了一个种子路径（SQLite 有 MySQL 无或反之） | 中 | 两个种子文件同构追加；测试断言两方言 DDL 存在性 |
| query_log 同步写拖慢每问 | 低 | 毫秒级本地 INSERT；`KBQA_LOG_WRITES` 开关；超量再异步 |
| publish 端点被滥灌 FAQ | 中 | localhost+token（已定）；publish 写 kbqa_faq 不碰 kbqa_policy 主数据 |
| L1 抢答导致 L2 使用率下降、RAG 能力闲置 | 中 | stats 端点监控 channel 分布；阈值定标后复核 |
| 契约新字段打破前端旧渲染 | 低 | 存量字段零改动；前端只做增量读取；回归测试守 kb-screen 关键路径 |

## 9. 任务依赖

```
T1 新表双种子 ─┐
T2 kbqa_store ─┼→ T4 FAQMatcher → T5 kbqa 服务契约化 → T6 api 端点 → T7 前端 → T8 测试 → T9 验证交付
T3 契约+配置  ─┘        （T8 可与 T4-T7 并行）
```

分支：`feat/kbqa-faq-tier`（Conventional Commits；提交前全量回归）。
