import requests
import json

r = requests.post("http://localhost:8003/api/chat/stream",
                  json={"query": "A型号今天在SMT-1线停线了，客户急单，什么时候能复线？"},
                  stream=True, timeout=120)
for line in r.iter_lines():
    if line:
        ev = json.loads(line)
        t = ev.get("type")
        if t == "agent_end":
            print(f"[AGENT] {ev.get('label')}: {ev.get('summary','')[:80]}")
        elif t == "tool_start":
            print(f"  [TOOL_START] {ev.get('tool')}: {ev.get('detail','')[:80]}")
        elif t == "tool_end":
            print(f"  [TOOL_END] {ev.get('tool')}: {ev.get('detail','')[:80]}")
        elif t == "llm_start":
            print(f"  [LLM] {ev.get('detail','')[:80]}")
        elif t == "final":
            print(f"[FINAL] report length={len(ev.get('report',''))}")
        elif t == "error":
            print(f"[ERROR] {ev.get('message')}")
