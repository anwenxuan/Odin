"""
agent/task_manager/scheduler.py — Task Scheduler

任务调度器，负责从 TaskQueue 中取出任务并分配给 Worker。

支持多种调度策略：
- FIFO       : 先进先出
- Priority   : 按优先级
- LoadBalance: 负载均衡（选择最空闲的 Worker）
- DAG        : 按 Workflow 依赖关系调度
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agent.task_manager.state import Task, TaskState, TaskPriority
from agent.task_manager.queue import TaskQueue, QueueEntry

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling Policy
# ─────────────────────────────────────────────────────────────────────────────


class SchedulingPolicy(str, Enum):
    """调度策略枚举。"""
    FIFO = "fifo"                     # 先进先出
    PRIORITY = "priority"             # 按优先级
    LOAD_BALANCE = "load_balance"     # 负载均衡
    DAG = "dag"                        # 按依赖关系


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler Metrics
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SchedulerMetrics:
    """调度器指标。"""
    tasks_scheduled: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_cancelled: int = 0
    avg_wait_time_seconds: float = 0.0
    last_scheduled_at: str | None = None

    def record_schedule(self) -> None:
        self.tasks_scheduled += 1
        self.last_scheduled_at = datetime.now(timezone.utc).isoformat()

    def record_complete(self) -> None:
        self.tasks_completed += 1

    def record_fail(self) -> None:
        self.tasks_failed += 1

    def record_cancel(self) -> None:
        self.tasks_cancelled += 1


# ─────────────────────────────────────────────────────────────────────────────
# Task Scheduler
# ─────────────────────────────────────────────────────────────────────────────


class TaskScheduler:
    """
    任务调度器。

    核心职责：
    1. 从 TaskQueue 中取任务（按策略）
    2. 分配给合适的 Worker
    3. 维护调度指标

    支持模式：
    - 同步模式（手动调用 schedule_one）
    - 异步模式（自动调度循环）

    使用方式（同步）：
        scheduler = TaskScheduler(queue, worker_pool)
        while True:
            task = scheduler.schedule_one()
            if task is None:
                break
            worker_pool.submit(task)

    使用方式（异步）：
        scheduler = TaskScheduler(queue, worker_pool)
        await scheduler.run()
    """

    def __init__(
        self,
        queue: TaskQueue,
        worker_pool: Any,       # WorkerPool — 避免循环导入
        policy: SchedulingPolicy = SchedulingPolicy.PRIORITY,
        poll_interval: float = 1.0,
    ):
        self.queue = queue
        self.worker_pool = worker_pool
        self.policy = policy
        self.poll_interval = poll_interval
        self.metrics = SchedulerMetrics()
        self._running = False
        self._task_map: dict[str, Task] = {}     # task_id → Task（运行中）
        self._dag_dependencies: dict[str, list[str]] = {}  # task_id → deps

    def set_dag_dependencies(
        self,
        task_id: str,
        depends_on: list[str],
    ) -> None:
        """设置 Task 依赖关系（用于 DAG 调度）。"""
        self._dag_dependencies[task_id] = depends_on

    def _can_schedule(self, task: Task) -> bool:
        """检查 Task 是否可以调度（依赖是否满足）。"""
        if self.policy != SchedulingPolicy.DAG:
            return True
        deps = self._dag_dependencies.get(task.id, [])
        for dep_id in deps:
            dep_task = self._task_map.get(dep_id)
            if dep_task is None:
                continue
            if not dep_task.is_terminal or dep_task.state != TaskState.COMPLETED:
                return False
        return True

    def schedule_one(self) -> Task | None:
        """
        同步取出一个可调度的 Task。

        Returns:
            Task 或 None（无可调度任务）
        """
        for _ in range(self.queue.size()):
            entry = self.queue.dequeue()
            if entry is None:
                return None

            task = entry.task

            # 检查依赖
            if not self._can_schedule(task):
                # 依赖未满足，放回队列
                self.queue.requeue(task)
                time.sleep(0.1)
                continue

            # 负载均衡：选择最空闲的 Worker
            if self.policy == SchedulingPolicy.LOAD_BALANCE:
                worker = self.worker_pool.idlest_worker()
                if worker is None:
                    self.queue.requeue(task)
                    return None
            else:
                worker = self.worker_pool.idlest_worker()

            # 分配 Worker
            task.schedule(worker.id if worker else "unassigned")
            self._task_map[task.id] = task
            self.metrics.record_schedule()

            logger.info(
                "[Scheduler] Scheduled task=%s worker=%s policy=%s",
                task.id,
                worker.id if worker else None,
                self.policy.value,
            )
            return task

        return None

    def on_task_completed(self, task: Task) -> None:
        """Task 完成后通知调度器。"""
        self._task_map.pop(task.id, None)
        if task.state == TaskState.COMPLETED:
            self.metrics.record_complete()
        elif task.state == TaskState.FAILED:
            self.metrics.record_fail()
        elif task.state == TaskState.CANCELLED:
            self.metrics.record_cancel()
        logger.info(
            "[Scheduler] Task %s %s — stats: scheduled=%d completed=%d failed=%d",
            task.id,
            task.state.value,
            self.metrics.tasks_scheduled,
            self.metrics.tasks_completed,
            self.metrics.tasks_failed,
        )

    def on_task_cancelled(self, task_id: str) -> None:
        """Task 取消时通知调度器。"""
        self._task_map.pop(task_id, None)

    async def run_async(self) -> None:
        """
        异步运行调度循环。
        自动从队列取任务并提交给 WorkerPool。
        """
        self._running = True
        logger.info("[Scheduler] Starting async scheduler loop, policy=%s", self.policy.value)

        while self._running:
            try:
                task = self.schedule_one()
                if task:
                    asyncio.create_task(self.worker_pool.submit_async(task))
                else:
                    await asyncio.sleep(self.poll_interval)
            except Exception:
                logger.exception("[Scheduler] Schedule loop error")
                await asyncio.sleep(self.poll_interval * 2)

    def stop(self) -> None:
        """停止调度循环。"""
        self._running = False
        logger.info("[Scheduler] Stopped")

    def get_pending_count(self) -> int:
        return len(self._task_map)

    def get_metrics(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "pending_tasks": len(self._task_map),
            "queue_size": self.queue.size(),
            "tasks_scheduled": self.metrics.tasks_scheduled,
            "tasks_completed": self.metrics.tasks_completed,
            "tasks_failed": self.metrics.tasks_failed,
            "tasks_cancelled": self.metrics.tasks_cancelled,
            "last_scheduled_at": self.metrics.last_scheduled_at,
        }
