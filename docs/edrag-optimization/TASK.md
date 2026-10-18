# TASK.md — 制造平台企业问答助手（kbqa 通道）按 ADR-001 架构落地

> 依据同目录 DESIGN.md。勾选式跟踪；每项完成即勾并附验证证据。
> 实施范围：本仓库 `D:/tools/mfg-agent-platform`；分支 `feat/kbqa-faq-tier`。

## 任务拆解

- [x] **T1 新表双种子**（无依赖）
  - `data/seed_v3.sql` 追加 `kbqa_faq` / `kbqa_faq_draft` / `kbqa_query_log` DDL（SQLite 方言）
  - `data/seed_kbqa.sql` 追加同构 DDL（MySQL 方言，`INT AUTO_INCREMENT`）
  - 验收：两文件 DDL 均含三张表；SQLite 演示路径 init 后可 SELECT 到三张空表
- [x] **T2 kbqa_store 写读仓库**（依赖 T1）
  - `v2/services/kbqa_store.py`：SQLAlchemy `create_engine(settings.DB_URL)`；`text()` 参数化；faq_version 戳查询 / insert_faq / insert_draft / list_drafts / publish_draft / log_query / channel_stats
  - 纪律：不写方言分支 SQL；DB_URL 非 SQLite 时的行为与既有 `init_db` 一致
  - 验收：离线单测（tmp SQLite 文件库）覆盖全部方法；publish 幂等
- [x] **T3 契约 + 配置**（无依赖）
  - `v2/services/kbqa.py` 契约构造器（存量字段不动，新增 channel/confidence/latency_ms/faq_version）
  - `common/config.py` 新增 `KBQA_MATCH_THRESHOLD` / `KBQA_MARGIN_THRESHOLD` / `KBQA_ADMIN_TOKEN` / `KBQA_LOG_WRITES`（env 可覆盖）
  - 验收：契约序列化 round-trip 单测；配置默认值单测
- [x] **T4 FAQMatcher**（依赖 T2/T3）
  - `v2/services/kbqa_faq_match.py`：归一化；语料加载（kbqa_policy ∪ kbqa_faq，status=active）；中文二元组计分（title×3 / keywords×2 / content×1）；阈值+margin 决策；语料加载器与版本函数可注入
  - 验收：单测——命中/分数不足/margin 不足/语料为空/注入假语料的确定性打分快照
- [x] **T5 kbqa 服务改造**（依赖 T3/T4）
  - `answer()` 改：L1 命中 → 契约(channel=faq) 直接返回（不调 LLM）；未命中 → 现有 L2（逻辑不动）→ 契约(channel=rag)；L0 缓存只包 L2 且键含 faq_version；写 query_log
  - 验收：fake 依赖端到端单测（L1 命中 LLM 计数=0；L2 回落降级回放保留；缓存键含版本）
- [x] **T6 API 端点**（依赖 T5）
  - `POST /api/v2/kbqa/feedback`（rating≥4 且 channel=rag → draft pending）
  - `GET /api/v2/kbqa/drafts`、`POST /api/v2/kbqa/drafts/{id}/publish`（localhost 或 `X-FAQ-Admin-Token`，否则 403）
  - `GET /api/v2/kbqa/stats`（7 日 channel 分布 / 低置信占比 / top 待晋升）
  - `POST /api/v2/kbqa` 响应换契约序列化（存量字段不变）
  - 验收：TestClient 离线单测各端点（含 403/429/400 既有行为不回归）
- [x] **T7 前端 kb-screen**（依赖 T6）
  - channel 角标（制度直答/知识库检索/缓存回放）+ latency 展示 + 👍 反馈按钮（走 feedback 端点）
  - 纪律：零构建原生 JS 增量；存量渲染路径不动
  - 验收：静态检查关键函数存在；回答气泡回归（既有字段照旧渲染）
- [x] **T8 测试套件**（依赖 T4-T7，可并行）
  - `tests/v2/test_kbqa_faq_tier.py`：匹配/契约/缓存版本/端点/鉴权/stats，遵循 `test_kbqa_seed.py` 离线风格与 docstring 表格约定
  - 验收：新增 ≥20 条全绿；`pytest tests/v2/ -q` 全量不回归
- [x] **T9 验证与交付**（依赖 T8）
  - 全量回归 + ruff（如可用）；自审对照 DESIGN §7 八条
  - 端到端（SQLite 演示路径）：起服务 → L1 直答 → publish → 同问走 L1，留证据
  - 交付：变更摘要 + Conventional Commit（feat 分支）+ 遗留项清单

## 依赖顺序

```
T1 ─┐
T2 ─┼→ T4 → T5 → T6 → T7 → T8 → T9
T3 ─┘   （T8 可与 T4-T7 并行开发）
```

## 已确认决策（Hulk，2026-10-18）

1. 目标 = 制造平台 kbqa 通道（**不是** EduRAG，不改 EduRAG 代码）
2. 范围 = 迭代一全量（L1/契约/晋升数据面/测试；ACL 与语义匹配留二期）
3. publish 保护 = localhost + 可选 `KBQA_ADMIN_TOKEN`
