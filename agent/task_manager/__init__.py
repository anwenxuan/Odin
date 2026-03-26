"""
agent/task_manager/ — Task Manager

Task Manager 负责管理长时间运行任务的生命周期。

目录结构：
    task_manager/
        __init__.py       — 公共接口
        state.py          — Task 状态机与 Task 模型
        queue.py          — 优先级队列
        scheduler.py       — 任务调度器
        worker.py          — Worker 池执行器
"""

from agent.task_manager.state import (
    TaskState,
    Task,
    TaskConfig,
    TaskResult,
    TaskPriority,
)
from agent.task_manager.queue import (
    TaskQueue,
    QueueEntry,
)
from agent.task_manager.scheduler import (
    TaskScheduler,
    SchedulingPolicy,
)
from agent.task_manager.worker import (
    Worker,
    WorkerPool,
    WorkerStatus,
)

__all__ = [
    # state
    "TaskState",
    "Task",
    "TaskConfig",
    "TaskResult",
    "TaskPriority",
    # queue
    "TaskQueue",
    "QueueEntry",
    # scheduler
    "TaskScheduler",
    "SchedulingPolicy",
    # worker
    "Worker",
    "WorkerPool",
    "WorkerStatus",
]
