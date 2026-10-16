from mfg_agents.tools.sql_tools import text_to_sql

for q in [
    "查询SMT-1线A型号今天的OEE、计划产出、实际产出",
    "查询SMT-1线A型号当前ongoing的停线事件",
    "查询SMT-1线A型号近2小时的品质不良记录，按defect_type分组",
]:
    print(f"\n=== Q: {q} ===")
    r = text_to_sql(q)
    print("SQL:", r.get("sql"))
    print("ERROR:", r.get("error"))
    print("ROWS:", r.get("rows"))
