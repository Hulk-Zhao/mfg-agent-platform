"""直接测试图执行和事件队列。"""
import sys, time
sys.path.insert(0, "src")

from mfg_agents.events import init_queue, push, get_queue
from mfg_agents.graph.builder import app

init_queue()
q = get_queue()

print("队列初始化完成，开始跑图...")
t0 = time.time()

# 直接同步跑，看push事件是否到队列
final = app.invoke({"user_query": "A型号今天在SMT-1线停线了，什么时候能复线？"})

print(f"图跑完，耗时{time.time()-t0:.1f}s")
print(f"队列里有{q.qsize()}个事件")

# 把队列里的事件都倒出来
while not q.empty():
    print("  事件:", q.get())

print("FA summary:", final.get("fa_summary"))
print("report长度:", len(final.get("customer_report", "")))
