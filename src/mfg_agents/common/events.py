"""全局事件队列：Agent内部工具调用时实时推送给前端。"""
import queue

# 每个请求一个队列，FastAPI生成器从这里读事件
_event_queue: queue.Queue | None = None


def init_queue():
    global _event_queue
    _event_queue = queue.Queue()


def push(event: dict):
    """Agent/工具调用时推送事件。"""
    if _event_queue is not None:
        _event_queue.put(event)


def get_queue() -> queue.Queue:
    return _event_queue
