"""能力服务：无状态，被所有域组复用（ARCHITECTURE-V2.md §3.4）。"""

from .text2sql import (
    MetricLibrary,
    QueryLibrary,
    QueryResult,
    SQLAuditor,
    Text2SQLService,
)
from .notification import (
    BoardNotificationChannel,
    IMGroupNotificationChannel,
    LogNotificationChannel,
    MultiChannel,
    NotificationChannel,
    NotificationService,
)
from .workflow import WorkflowEngine, WorkflowTask, WorkflowTransitionError

__all__ = [
    "Text2SQLService",
    "QueryResult",
    "SQLAuditor",
    "MetricLibrary",
    "QueryLibrary",
    "NotificationService",
    "NotificationChannel",
    "LogNotificationChannel",
    "BoardNotificationChannel",
    "IMGroupNotificationChannel",
    "MultiChannel",
    "WorkflowEngine",
    "WorkflowTask",
    "WorkflowTransitionError",
]
