# DELIVERY.md — 迭代一交付摘要（feat/kbqa-faq-tier）

> 提交：`c05cdd3` feat(kbqa): FAQ 前置层 + 晋升管线落地 ADR-001 三层检索架构
> 日期：2026-10-18 ｜ 状态：✅ 已交付，全量回归绿

## 1. 交付内容

企业问答助手（`/api/v2/kbqa`）从「每问必过 RAG」改为 **L1 制度直答 → L2 RAG 兜底**：

| 层 | 组件 | 行为 |
|---|---|---|
| L1 | `kbqa_faq_match.FAQMatcher` | kbqa_policy ∪ kbqa_faq 语料，中文二元组 + 关键词×2/标题×3/内容×1 加权（封顶 1.0）；双闸决策 `score≥0.70 且 top1-top2≥0.15`；归一化同题直中=1.0。命中不调 LLM |
| L0 | `_cache_key` 版本命名空间 | `mfg:kbqa:v2:{faq_version}:{sha1}`；FAQ 写操作即换命名空间，旧缓存自然失效，无主动清理 |
| L2 | 既有 `search_kb` + LLM | 逻辑不动；整层故障 → `channel=rag_unavailable` 诚实降级且**不缓存**；无命中 → `channel=miss` |
| 度量 | `kbqa_query_log` + `/stats` | 每次问答落 channel/confidence/latency；写失败只吞不抛 |

晋升管线：👍 反馈（rating≥4 且 channel=rag）→ `kbqa_faq_draft(pending)` → 管理端点 publish → `kbqa_faq(active)` → 同问立即走 L1。

## 2. 变更文件（15 个，+1546/-54）

| 文件 | 性质 | 说明 |
|---|---|---|
| `docs/adr/ADR-001-企业问答助手检索架构.md` | 新增 | 选型决策记录（FAQ+RAG vs 知识图谱 + KG 启用判据） |
| `docs/edrag-optimization/DESIGN.md` / `TASK.md` | 新增 | 设计与勾选式任务跟踪 |
| `src/mfg_agents/v2/services/kbqa_faq_match.py` | 新增 | L1 匹配器（归一化/计分/双闸） |
| `src/mfg_agents/v2/services/kbqa_store.py` | 新增 | 三表读写仓库 + 版本戳 + 度量统计；运行期 SQL 无方言分支 |
| `src/mfg_agents/v2/services/kbqa.py` | 重写 | 契约构造器 + L1/L2 编排 + 遥测 |
| `src/mfg_agents/v2/demo/api.py` | 修改 | feedback/drafts/publish/stats 四端点 + 管理保护 + session_id |
| `src/mfg_agents/v2/demo/ui/kb-screen.js` / `app.css` / `app.js` | 修改 | channel 角标 + 耗时 + 👍 反馈（零构建原生 JS 增量） |
| `data/init.sql` / `data/seed_v3.sql` | 修改 | 三张新表 DDL（MySQL/SQLite 双方言，幂等） |
| `src/mfg_agents/common/config.py` | 修改 | 4 个 KBQA_* 配置项（env 可覆盖） |
| `tests/v2/test_kbqa_faq_tier.py` | 新增 | 28 条离线用例（M 匹配/C 契约/S 仓库/A 端点/R 回归） |
| `tests/v2/test_kbqa_seed.py` | 修改 | 缓存委派测试隔离 L1；限流测试适配 session_id 签名 |

## 3. 验证证据

- **全量回归**：`pytest tests/ -q` → **1096 passed / 0 failed / 0 error**（junit XML 复核，非摘要行）；其中 kbqa 41 条全绿
- **端到端（真实服务 + SQLite 库）**：
  1. 「年假怎么休」→ `channel=faq` confidence=1.0 latency=7ms，标签 `考勤制度 / QP-HR-003`，未调 LLM
  2. 「今天天气怎么样」→ `channel=rag`（L2 真实链路：Milvus 在线 + LLM 作答）
  3. 反馈入草稿 → `draft_id=1 status=pending`
  4. 管理端点鉴权：错误令牌 403 / 无令牌 403
  5. publish → `faq_id=1`；重复 publish → 409
  6. 发布后同问 → `channel=faq` confidence=1.0，`faq_version=policy:48:48|faq:1:1`（版本戳已换）
  7. `/stats` → `{faq:11, miss:6, rag:2, rag_unavailable:4, total:23}`，rag 低置信占比 100%（阈值定标取数处）
- **静态检查**：py_compile 全过；ruff 不在项目工具链（CI 仅 pytest），未跑

## 4. 已知限制与遗留项（迭代二候选，非缺陷）

1. **ACL 未做**：kbqa 口径=公开制度全员可读；`dept_cn` 列已备，`search_kb` 无 metadata 过滤是已知限制。等权限维度明确后做
2. **正式 FAQ 审核流未做**：当前 publish 即生效（单人管理态），无二审/留痕
3. **阈值 0.70 是定标值不是真理**：上线后用 `/stats` 的真实分布复核（尤其 rag_low_confidence_ratio）
4. **晋升 FAQ 无 dept 归属**：反馈来的草稿 dept_cn 为空，角标显示「其他」；后续可在 publish 时人工补
5. **行级数据范围与 kbqa 无关**：kbqa 不走 data_scope.yaml（公开制度通道），待办/看板的三层差异化不受影响

## 5. 兼容性

- 契约**存量字段零改动**（answer/tags/sources/category/degraded 语义不变），前端旧渲染路径不回归
- 缓存键升位 `v2`，旧 `mfg:kbqa:sha1(...)` 键自然过期（TTL 1800s），无迁移
- 双种子 DDL 均 `IF NOT EXISTS`，重复执行幂等
