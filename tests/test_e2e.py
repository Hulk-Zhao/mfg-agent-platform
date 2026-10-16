from mfg_agents.graph.builder import app

result = app.invoke({"user_query": "A型号今天在SMT-1线停线了，客户急单，什么时候能复线？"})
print("=== SCENARIO ===", result.get("scenario"))
print("=== CONFIRMED_STOP ===", result.get("confirmed_stop"))
print("=== PRODUCTION ===", result.get("production_summary"))
print("=== QUALITY ===", result.get("quality_summary"))
print("=== SUPPLY ===", result.get("supply_summary"))
print("=== EQUIPMENT ===", result.get("equipment_summary"))
print("=== FA ===", result.get("fa_summary"))
print("=== VERIFICATION ===", result.get("verification_items"))
print("\n=== CUSTOMER_REPORT ===")
print(result.get("customer_report"))
