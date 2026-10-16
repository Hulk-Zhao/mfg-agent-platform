import sys
sys.path.insert(0, "src")
from mfg_agents.graph.builder import app

# 用values模式看完整state
for i, chunk in enumerate(app.stream({"user_query": "A型号在SMT-1停线了"}, stream_mode="values")):
    print(f"=== STEP {i} ===")
    print("scenario:", chunk.get("scenario"))
    print("oee rows:", len(chunk.get("oee_data", {}).get("rows", [])))
    print("quality rows:", len(chunk.get("quality_data", {}).get("rows", [])))
    print("equipment rows:", len(chunk.get("equipment_data", {}).get("rows", [])))
    print("fa_summary:", chunk.get("fa_summary", "N/A"))
    print("report len:", len(chunk.get("customer_report", "")))
