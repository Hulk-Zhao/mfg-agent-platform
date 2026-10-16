"""QA 红队测试：Text2SQLService 安全边界对抗验证（独立编写，不复用工程师断言）。

攻击矩阵（每类攻击的统一断言：被拦截且返回安全错误/兜底，不泄露数据、不执行）：
A. DDL/DML 注入（DROP/DELETE/UPDATE/INSERT/TRUNCATE/ALTER/CREATE/GRANT/ATTACH/PRAGMA/VACUUM）
B. 堆叠查询（SELECT 1; DELETE ...）
C. 注释截断与注释藏毒（-- / /* */）
D. UNION 探测（越权表经 UNION 混入结果集）
E. 表级权限（跨角色越权 / 未注册表 / 空 user_ctx fail-closed）
F. ACL 绕过变体（括号 FROM / 反引号 / 双引号 / 方括号 / 逗号隐式 join）——BUG-1（Round 2 已修，本条锁定修复）
G. 敏感字段脱敏（supplier / unit_price 按角色掩码；列别名映射）——BUG-2（Round 2 已修，本条锁定修复）
H. 执行层（mode=ro 只读连接 / 行数上限 / load_extension 禁用）
I. 兜底契约（被拦截查询不得返回任何数据行）
"""
import sqlite3

import pytest

from mfg_agents.v2.contracts.data_source import (
    DataSourceRegistry,
    SQLiteDataSource,
    _apply_row_cap,
)
from mfg_agents.v2.services.text2sql import (
    FieldMasker,
    MetricLibrary,
    QueryLibrary,
    SQLAuditError,
    SQLAuditor,
    Text2SQLService,
)

DB_PATH = "data/mfg_agent.db"
QUERIES_FILE = "src/mfg_agents/v2/data/queries.yaml"
METRICS_DIR = "src/mfg_agents/v2/data/metrics"

# 纯净角色上下文（刻意不含 coordinator 跨域角色，否则越权断言失去意义）
MFG_CTX = {"user_id": "u-mfg", "dept": "manufacturing", "roles": ["manufacturing"]}
QUALITY_CTX = {"user_id": "u-qc", "dept": "quality", "roles": ["quality"]}
WAREHOUSE_CTX = {"user_id": "u-wh", "dept": "warehouse", "roles": ["warehouse"]}
PROCUREMENT_CTX = {"user_id": "u-po", "dept": "procurement",
                   "roles": ["procurement"]}


@pytest.fixture()
def service() -> Text2SQLService:
    queries = QueryLibrary.load(QUERIES_FILE)
    registry = DataSourceRegistry()
    registry.register(SQLiteDataSource(DB_PATH, name="dev", queries={
        name: str(spec["sql"]) for name, spec in queries._queries.items()}))
    return Text2SQLService(registry, MetricLibrary.load(METRICS_DIR), queries)


@pytest.fixture()
def registry() -> DataSourceRegistry:
    reg = DataSourceRegistry()
    reg.register(SQLiteDataSource(DB_PATH, name="dev"))
    return reg


# ---------- A. DDL/DML 注入 ----------

class TestDDLDMLInjection:
    @pytest.mark.parametrize("sql", [
        "DROP TABLE inventory",
        "drop table inventory",
        "DELETE FROM line_stop_event",
        "UPDATE inventory SET on_hand_qty = 0",
        "INSERT INTO inventory VALUES (1, 'X', 'Y', 1, 1)",
        "TRUNCATE TABLE shipment",
        "ALTER TABLE inventory ADD COLUMN hacked TEXT",
        "CREATE TABLE evil (x)",
        "GRANT ALL ON inventory TO attacker",
        "REVOKE SELECT ON inventory FROM mfg",
        "ATTACH DATABASE 'evil.db' AS evil",
        "DETACH DATABASE evil",
        "PRAGMA journal_mode = WAL",
        "VACUUM",
        "REINDEX",
        "REPLACE INTO inventory VALUES (1, 'X', 'Y', 1, 1)",
        # 自然语言表述（demo 用户真会这么问）
        "删除 inventory 表",
    ])
    def test_ddl_dml_blocked(self, sql):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(sql, MFG_CTX)

    def test_natural_language_delete_via_service_returns_fallback(self, service):
        """'删除 inventory 表'走明细通道（无 LLM）→ 人工兜底，不执行任何 SQL。"""
        result = service.query_detail("删除 inventory 表", MFG_CTX)
        assert result.fallback == "manual_ticket"
        assert result.rows == []


# ---------- B. 堆叠查询 ----------

class TestStackedQuery:
    @pytest.mark.parametrize("sql", [
        "SELECT 1; DELETE FROM inventory",
        "SELECT 1; DROP TABLE inventory",
        "SELECT * FROM inventory; UPDATE shipment SET qty = 0",
        "SELECT 1 ; SELECT 2",
        "SELECT 1; -- trailing",
        "SELECT 1; DELETE FROM inventory;",
        "SELECT * FROM inventory;DROP TABLE inventory",
    ])
    def test_stacked_query_blocked(self, sql):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(sql, MFG_CTX)

    def test_single_trailing_semicolon_allowed(self):
        """合法单语句带单个结尾分号应放行（execute 层会剥掉）。"""
        sql = "SELECT batch_no FROM material_batch;"
        assert SQLAuditor.audit(sql, QUALITY_CTX) == []

    def test_trailing_semicolons_neutralized(self):
        """'SELECT 1;;'：结尾连续分号被 strip/rstrip 中和为单语句，
        execute 层实际只执行 'SELECT 1'（sqlite3 本身也拒绝多语句）——
        行为安全，不构成堆叠查询（QA 自记：此条原为过严断言，已修正）。"""
        sql = "SELECT 1;;"
        assert SQLAuditor.audit(sql, MFG_CTX) == []


# ---------- C. 注释截断与藏毒 ----------

class TestCommentTruncation:
    def test_comment_hiding_drop_blocked(self):
        """-- 注释藏 DROP：保守策略，整串命中关键词即拦截。"""
        with pytest.raises(SQLAuditError, match="禁用关键词"):
            SQLAuditor.audit("SELECT * FROM inventory -- DROP TABLE inventory",
                             WAREHOUSE_CTX)

    def test_block_comment_hiding_delete_blocked(self):
        with pytest.raises(SQLAuditError, match="禁用关键词"):
            SQLAuditor.audit(
                "SELECT * FROM inventory /* DELETE FROM shipment */",
                WAREHOUSE_CTX)

    def test_benign_trailing_comment_allowed(self):
        sql = "SELECT batch_no FROM material_batch -- 今日到货"
        assert SQLAuditor.audit(sql, QUALITY_CTX) == []

    def test_comment_truncation_attack_blocked(self):
        """注释截断攻击：把后续条件注释掉以绕过 WHERE。"""
        sql = "SELECT * FROM inventory WHERE on_hand_qty > 0 -- AND warehouse = 'WH-1'"
        # 语法合法且无危险关键词 → 放行，但执行层只读 + 行数上限兜底，
        # 断言其不泄露越权数据（inventory 对 warehouse 合法，返回行数受限）。
        SQLAuditor.audit(sql, WAREHOUSE_CTX)


# ---------- D. UNION 探测 ----------

class TestUnionProbing:
    def test_union_with_unauthorized_table_blocked(self):
        """UNION 把越权表数据混入结果集 → 表级权限必须命中第二个表。"""
        sql = ("SELECT product_model FROM inventory "
               "UNION SELECT complaint_no FROM customer_complaint")
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, WAREHOUSE_CTX)

    def test_union_all_variant_blocked(self):
        sql = ("SELECT product_model FROM inventory "
               "UNION ALL SELECT description FROM customer_complaint")
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(sql, WAREHOUSE_CTX)

    def test_union_case_insensitive_blocked(self):
        sql = ("select product_model from inventory "
               "union select complaint_no from customer_complaint")
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(sql, WAREHOUSE_CTX)


# ---------- E. 表级权限 ----------

class TestTableLevelPermission:
    @pytest.mark.parametrize("sql,ctx,expected_table", [
        ("SELECT * FROM customer_complaint", MFG_CTX, "customer_complaint"),
        ("SELECT * FROM material_batch", WAREHOUSE_CTX, "material_batch"),
        ("SELECT * FROM production_output", QUALITY_CTX, "production_output"),
        ("SELECT * FROM quality_defect", WAREHOUSE_CTX, "quality_defect"),
        ("SELECT * FROM shipment", MFG_CTX, "shipment"),
        ("SELECT * FROM equipment_log", QUALITY_CTX, "equipment_log"),
        ("SELECT * FROM line_stop_event", QUALITY_CTX, "line_stop_event"),
    ])
    def test_cross_role_table_access_blocked(self, sql, ctx, expected_table):
        with pytest.raises(SQLAuditError, match=expected_table):
            SQLAuditor.audit(sql, ctx)

    def test_unknown_table_blocked(self):
        with pytest.raises(SQLAuditError, match="未注册"):
            SQLAuditor.audit("SELECT * FROM hr_salaries", MFG_CTX)

    def test_sqlite_master_blocked(self):
        """系统表（可枚举全库结构）必须拒绝。"""
        with pytest.raises(SQLAuditError, match="未注册"):
            SQLAuditor.audit("SELECT name FROM sqlite_master", MFG_CTX)

    @pytest.mark.parametrize("ctx", [None, {}, {"roles": []},
                                     {"dept": "", "roles": None}])
    def test_empty_user_ctx_fail_closed(self, ctx):
        """空 user_ctx → fail-closed：任何表都拒（绝不因缺上下文放行）。
        BUG-3（Round 2 已修）：roles=None（JSON null）曾抛 TypeError，
        现统一抛 SQLAuditError（_user_scopes 用 ctx.get('roles') or []）。"""
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit("SELECT * FROM inventory", ctx)

    def test_role_case_insensitive_match(self):
        """角色大小写不敏感（PROCUREMENT == procurement）。"""
        sql = "SELECT * FROM material_batch"
        assert SQLAuditor.audit(
            sql, {"dept": "PROCUREMENT", "roles": ["Procurement"]}) == []

    def test_subquery_unauthorized_table_blocked(self):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "SELECT * FROM (SELECT * FROM customer_complaint)", MFG_CTX)

    def test_cte_unauthorized_table_blocked(self):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "WITH c AS (SELECT * FROM customer_complaint) "
                "SELECT * FROM c", MFG_CTX)

    def test_exists_subquery_unauthorized_blocked(self):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "SELECT * FROM inventory WHERE EXISTS "
                "(SELECT 1 FROM customer_complaint)", WAREHOUSE_CTX)

    def test_join_unauthorized_blocked(self):
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "SELECT * FROM inventory i JOIN customer_complaint c "
                "ON i.product_model = c.product_model", WAREHOUSE_CTX)


# ---------- F. ACL 绕过变体（QA-BUG-1：当前实现放行 = 缺陷证据） ----------

class TestAclBypassVariants:
    """表级 ACL 绕过变体（BUG-1，Round 2 已由工程师修复，commit 54aa607）。

    原缺陷：_TABLE_RE 只识别 ``(from|join)\\s+(\\w+)`` 形式，括号紧贴 FROM /
    引号标识符 / 逗号隐式 join 会漏提表名 → ACL 被绕过（QA 实测越权读到
    customer_complaint 数据行）。修复：extract_tables() 全文扫描 from/join
    子句，覆盖括号/引号/方括号标识符、逗号 join、CTE/子查询内层、注释剥离。
    本类用例现锁定修复行为：全部变体必须抛 SQLAuditError。"""

    def test_parenthesized_from_blocked(self):
        """FROM(customer_complaint)：括号紧贴 FROM 时表名漏提。"""
        sql = "SELECT complaint_no FROM(customer_complaint)"
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, MFG_CTX)

    def test_newline_parenthesized_from_blocked(self):
        sql = "SELECT complaint_no FROM\n(customer_complaint)"
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, MFG_CTX)

    def test_backquoted_table_blocked(self):
        """反引号标识符（SQLite 接受的表名引用）。"""
        sql = "SELECT complaint_no FROM `customer_complaint`"
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, MFG_CTX)

    def test_double_quoted_table_blocked(self):
        """双引号标识符（SQLite 字符串/标识符歧义语法）。"""
        sql = 'SELECT complaint_no FROM "customer_complaint"'
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, MFG_CTX)

    def test_comma_join_unauthorized_table_blocked(self):
        """逗号隐式 join：FROM a, b 只提取首表，第二张表绕过 ACL。"""
        sql = ("SELECT c.complaint_no FROM inventory i, "
               "customer_complaint c")
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, WAREHOUSE_CTX)

    def test_comma_join_leak_end_to_end(self, registry):
        """Round 2 翻转（BUG-1 已修，commit 54aa607）：warehouse 角色
        （无客诉表权限）经逗号 join 必须被表级 ACL 拦截，不再有数据泄露路径。
        原缺陷证据：audit 放行且 execute 真实返回 customer_complaint 行。"""
        sql = ("SELECT c.complaint_no FROM inventory i, "
               "customer_complaint c")
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit(sql, WAREHOUSE_CTX)


# ---------- G. 敏感字段脱敏 ----------

class TestSensitiveFieldMasking:
    def test_supplier_masked_for_unauthorized_roles(self):
        rows = [{"batch_no": "B1", "supplier": "千住金属"}]
        for ctx in (MFG_CTX, QUALITY_CTX, WAREHOUSE_CTX, None):
            masked_rows, masked = FieldMasker.mask(rows, ctx)
            assert masked is True, f"ctx={ctx} 必须脱敏"
            assert masked_rows[0]["supplier"] == "供应商***"

    def test_supplier_visible_for_procurement(self):
        rows = [{"batch_no": "B1", "supplier": "千住金属"}]
        masked_rows, masked = FieldMasker.mask(rows, PROCUREMENT_CTX)
        assert masked is False
        assert masked_rows[0]["supplier"] == "千住金属"

    def test_unit_price_masked_for_unauthorized_roles(self):
        rows = [{"product_model": "A型号", "unit_price": 12.5}]
        for ctx in (MFG_CTX, QUALITY_CTX, WAREHOUSE_CTX):
            masked_rows, masked = FieldMasker.mask(rows, ctx)
            assert masked is True
            assert masked_rows[0]["unit_price"] == "***"

    def test_unit_price_visible_for_planning_and_finance(self):
        rows = [{"product_model": "A型号", "unit_price": 12.5}]
        for role in ("planning", "finance"):
            _, masked = FieldMasker.mask(rows, {"roles": [role]})
            assert masked is False

    def test_masking_end_to_end_via_registered_query(self, service):
        """端到端：供应链注册查询，品质角色看到脱敏供应商名。"""
        result = service.query_registered("supply_batches_today", {},
                                          QUALITY_CTX)
        assert result.masked is True
        assert all(r["supplier"] == "供应商***" for r in result.rows)

    def test_alias_bypasses_masking(self):
        """Round 2 翻转（BUG-2 已修，commit 54aa607）：SELECT supplier AS s
        的结果列名是 s，传入 SQL 后 FieldMasker 经别名映射解析源列，
        品质角色必须脱敏。接口契约：mask(rows, user_ctx, sql=None)，
        第三参缺省时无法解析别名（调用方应始终传 SQL）。"""
        rows = [{"batch_no": "B1", "s": "千住金属"}]  # supplier AS s
        sql = "SELECT batch_no, supplier AS s FROM material_batch"
        masked_rows, masked = FieldMasker.mask(rows, QUALITY_CTX, sql)
        assert masked is True, "含敏感源列的查询结果必须标记脱敏"
        assert masked_rows[0]["s"] == "供应商***"

    def test_alias_visible_for_procurement(self):
        """采购角色经别名仍见原文（修复不能误伤授权角色）。"""
        rows = [{"s": "千住金属"}]
        masked_rows, masked = FieldMasker.mask(
            rows, PROCUREMENT_CTX,
            "SELECT supplier AS s FROM material_batch")
        assert masked is False
        assert masked_rows[0]["s"] == "千住金属"

    def test_alias_masking_end_to_end_detail_channel(self, service,
                                                     monkeypatch):
        """QA 侧端到端（与工程师侧同行为、独立锁死）：LLM 生成的别名 SQL
        走明细通道，品质角色必须脱敏、采购角色可见原文。"""

        class _Resp:
            content = "SELECT batch_no, supplier AS s FROM material_batch"

        class _LLM:
            def invoke(self, prompt):
                return _Resp()

        monkeypatch.setattr("mfg_agents.tools.llm_factory.llm_available",
                            lambda: True)
        monkeypatch.setattr("mfg_agents.tools.llm_factory.get_llm",
                            lambda *a, **k: _LLM())
        r1 = service.query_detail("各批次供应商是谁", QUALITY_CTX)
        assert r1.channel == "detail"
        assert r1.masked is True
        assert all(row["s"] == "供应商***" for row in r1.rows)
        r2 = service.query_detail("各批次供应商是谁", PROCUREMENT_CTX)
        assert r2.masked is False
        assert all(row["s"] != "供应商***" for row in r2.rows)
        assert {row["s"] for row in r2.rows} == {
            "千住金属", "深南电路", "STMicroelectronics"}


# ---------- J. 已知限制锚定（Round 2.5 sqlglot 升级复核发现） ----------

class TestKnownLimitations:
    """已知限制行为锚定（非缺陷回归；修复后需翻转为断言拦截）。"""

    def test_table_valued_pragma_metadata_gap(self):
        """OBS-8（低危，预存非回归）：pragma_table_info 等表值函数被 sqlglot
        解析为函数调用（不产出 Table 节点）→ 表级 ACL 视野外 → 审核放行 →
        SQLite 返回 schema 元数据（列名/类型，不含行数据）。
        旧正则实现同样漏（\\bpragma\\b 不匹配 pragma_table_info，非本轮回归）。
        影响边界：仅 schema 元数据披露，越权表行数据仍不可读（下一条断言锁定）。
        修复方向：_FORBIDDEN_SQL 增加表值函数黑名单（pragma_*/json_each 等）。"""
        sql = "SELECT * FROM pragma_table_info('customer_complaint')"
        assert SQLAuditor.audit(sql, MFG_CTX) == []  # 当前：放行（已知限制）
        # 边界锁定：同一越权表的行数据仍然不可读
        with pytest.raises(SQLAuditError, match="customer_complaint"):
            SQLAuditor.audit("SELECT complaint_no FROM customer_complaint",
                             MFG_CTX)

    def test_parse_failure_fail_closed(self):
        """sqlglot 解析失败 → fail-closed 抛 SQLAuditError（不回退宽松路径）。"""
        for sql in ["SELECT * FROM", "SELECT !!! FROM", "FROM WHERE SELECT",
                    "SELECT * FROM inventory WHERE", "((("]:
            with pytest.raises(SQLAuditError, match="解析失败|仅允许|禁用"):
                SQLAuditor.audit(sql, MFG_CTX)

    def test_parser_divergence_candidates_blocked(self):
        """解析器分歧候选（Round 2.5 QA 独立对抗）：sqlglot 提取与 SQLite
        执行不一致的候选必须被拦截或不泄露行数据。"""
        # 单引号表名（SQLite DQS 怪癖）：sqlglot 提表 → 拦截
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit("SELECT complaint_no FROM 'customer_complaint'",
                             MFG_CTX)
        # 逗号 + 派生表：内层表被提取 → 拦截
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "SELECT c.complaint_no FROM inventory i, "
                "(SELECT * FROM customer_complaint) c", WAREHOUSE_CTX)
        # CTE 影子名（与真实表同名）：保守提取 → 拦截
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "WITH customer_complaint AS (SELECT 1 AS x) "
                "SELECT * FROM customer_complaint", MFG_CTX)
        # INDEXED BY：索引名一并保守提取 → 拦截
        with pytest.raises(SQLAuditError):
            SQLAuditor.audit(
                "SELECT * FROM customer_complaint INDEXED BY idx_x", MFG_CTX)

# ---------- H. 执行层 ----------


class TestExecutionLayer:
    def test_readonly_connection_rejects_write(self, registry):
        """mode=ro 只读连接：写操作在驱动层即被拒。"""
        with pytest.raises(sqlite3.OperationalError):
            registry.execute_readonly_sql("UPDATE inventory SET on_hand_qty = 0")

    def test_readonly_connection_rejects_create(self, registry):
        with pytest.raises(sqlite3.OperationalError):
            registry.execute_readonly_sql("CREATE TABLE evil (x)")

    def test_row_cap_enforced(self, tmp_path):
        """行数上限：250 行表 + row_cap=50 → 最多返回 50 行。"""
        db = tmp_path / "cap.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO big (v) VALUES (?)",
                         [("x",) for _ in range(250)])
        conn.commit()
        conn.close()
        ds = SQLiteDataSource(str(db), name="cap")
        rows = ds.execute_readonly_sql("SELECT * FROM big", row_cap=50)
        assert len(rows) == 50

    def test_row_cap_applied_by_wrapper(self):
        assert _apply_row_cap("SELECT * FROM t", 100).endswith("LIMIT 100")
        assert "LIMIT 100" in _apply_row_cap("SELECT * FROM (SELECT 1) x", 100)
        # 已有 LIMIT 不重复外包；fetchmany 仍兜底
        assert _apply_row_cap("SELECT * FROM t LIMIT 5", 100) == \
            "SELECT * FROM t LIMIT 5"
        assert _apply_row_cap("SELECT * FROM t", 0) == "SELECT * FROM t"

    def test_row_cap_fetchmany_backstop(self, tmp_path):
        """SQL 自带大 LIMIT 时 fetchmany(row_cap) 仍然兜底截断。"""
        db = tmp_path / "cap2.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE big (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO big DEFAULT VALUES", [()] * 100)
        conn.commit()
        conn.close()
        ds = SQLiteDataSource(str(db), name="cap2")
        rows = ds.execute_readonly_sql("SELECT * FROM big LIMIT 999", row_cap=30)
        assert len(rows) == 30

    def test_load_extension_rejected(self, registry):
        """Python sqlite3 默认禁用扩展加载（纵深：确认不成为注入面）。"""
        with pytest.raises(sqlite3.OperationalError):
            registry.execute_readonly_sql(
                "SELECT load_extension('/tmp/evil.so')")

    def test_missing_db_file_does_not_create_empty_db(self, tmp_path):
        """只读连接指向不存在的文件必须报错，绝不静默建空库。"""
        ds = SQLiteDataSource(str(tmp_path / "nope.db"), name="nope")
        with pytest.raises(sqlite3.OperationalError):
            ds.execute_readonly_sql("SELECT 1")


# ---------- I. 兜底契约 ----------

class TestFallbackContract:
    def test_rejected_metric_returns_no_data(self, service):
        """指标 SQL 若审核不过 → fallback 且零数据行（不静默放行）。"""
        result = service.query_metric("otd_rate", user_ctx=MFG_CTX)
        # otd_rate 查 shipment 表：manufacturing 无权限 → 拦截
        assert result.fallback == "manual_ticket"
        assert result.rows == []
        assert result.confidence == 0.0
        assert "blocked" in result.provenance

    def test_rejected_registered_returns_no_data(self, service):
        result = service.query_registered("supply_batches_today", {},
                                          MFG_CTX)
        assert result.fallback == "manual_ticket"
        assert result.rows == []
        assert result.confidence == 0.0
        assert result.provenance["blocked"]

    def test_rejected_queries_carry_audit_reason(self, service):
        result = service.query_registered("supply_batches_today", {},
                                          WAREHOUSE_CTX)
        assert "审核拦截" in result.warnings[0]
        assert result.provenance["channel"] == "registered"
