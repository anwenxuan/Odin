"""
agent/task_manager/worker.py — Worker & Worker Pool

Worker 负责实际执行 Task。
WorkerPool 管理多个 Worker，支持：
- 并行执行多个 Task
- Worker 生命周期管理
- 负载监控
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Worker Status
# ─────────────────────────────────────────────────────────────────────────────


class WorkerStatus(str, Enum):
    """Worker 状态枚举。"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# ─────────────────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Worker:
    """
    单个 Worker。

    Worker 是 Task 的执行单元，在一个独立线程中运行。
    每个 Worker 按顺序执行分配给它的 Task。
    """
    id: str
    name: str

    # 状态
    status: WorkerStatus = WorkerStatus.IDLE
    current_task_id: str | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_heartbeat: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # 统计
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_duration_seconds: float = 0.0
    current_task_started_at: str | None = None

    # 内部
    _thread: threading.Thread | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @classmethod
    def create(cls, name: str | None = None) -> "Worker":
        """Worker 工厂方法。"""
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        return cls(
            id=worker_id,
            name=name or f"Worker-{worker_id}",
        )

    def is_idle(self) -> bool:
        return self.status == WorkerStatus.IDLE

    def is_running(self) -> bool:
        return self.status == WorkerStatus.RUNNING

    def update_heartbeat(self) -> None:
        self.last_heartbeat = datetime.now(timezone.utc).isoformat()

    def start_thread(
        self,
        target: Callable[["Worker"], None],
    ) -> None:
        """在线程中启动 Worker。"""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=target, args=(self,), daemon=True)
        self._thread.start()
        logger.info("[Worker %s] Started", self.id)

    def stop(self, timeout: float = 10.0) -> None:
        """优雅停止 Worker。"""
        self.status = WorkerStatus.STOPPING
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self.status = WorkerStatus.STOPPED
        logger.info("[Worker %s] Stopped", self.id)

    def run_loop(
        self,
        task_queue: Any,         # TaskQueue
        executor_fn: Callable[[Any], Any],  # 执行函数
    ) -> None:
        """
        Worker 的主循环（在独立线程中运行）。

        Args:
            task_queue : TaskQueue 实例（每个 Worker 有独立引用）
            executor_fn: 执行单个 Task 的函数
        """
        logger.info("[Worker %s] Entering run loop", self.id)

        while not self._stop_event.is_set():
            self.status = WorkerStatus.IDLE

            # 从队列取任务
            entry = task_queue.dequeue(worker_id=self.id)
            if entry is None:
                # 队列为空，短暂等待
                self._stop_event.wait(timeout=1.0)
                continue

            task = entry.task
            self.status = WorkerStatus.RUNNING
            self.current_task_id = task.id
            self.current_task_started_at = datetime.now(timezone.utc).isoformat()
            self.update_heartbeat()

            logger.info(
                "[Worker %s] Processing task=%s",
                self.id,
                task.id,
            )

            t0 = time.monotonic()
            try:
                task.start()
                result = executor_fn(task)

                duration = time.monotonic() - t0
                self.total_duration_seconds += duration

                if result.status in {"completed", "verified"}:
                    task.complete(result)
                    self.tasks_completed += 1
                    logger.info(
                        "[Worker %s] Task %s completed in %.1fs",
                        self.id,
                        task.id,
                        duration,
                    )
                else:
                    task.fail(result.error or "unknown", result)
                    self.tasks_failed += 1
                    logger.warning(
                        "[Worker %s] Task %s failed: %s",
                        self.id,
                        task.id,
                        result.error,
                    )

            except Exception as exc:
                duration = time.monotonic() - t0
                self.tasks_failed += 1
                task.fail(str(exc))
                logger.exception(
                    "[Worker %s] Task %s exception after %.1fs",
                    self.id,
                    task.id,
                    duration,
                )

            finally:
                self.current_task_id = None
                self.current_task_started_at = None
                self.update_heartbeat()

        logger.info("[Worker %s] Run loop exited", self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "current_task_id": self.current_task_id,
            "started_at": self.started_at,
            "last_heartbeat": self.last_heartbeat,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "total_duration_seconds": self.total_duration_seconds,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Worker Pool
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class WorkerPool:
    """
    Worker 池。

    管理一组 Worker，提供：
    - 动态 Worker 数量控制
    - 负载均衡（idlest_worker）
    - 统一的生命周期管理

    使用方式：
        pool = WorkerPool(size=3)
        pool.start(task_queue, executor_fn)
        # ...
        pool.stop()
    """

    size: int = 3
    workers: list[Worker] = field(default_factory=list)
    _running: bool = field(default=False, repr=False)

    @classmethod
    def create(
        cls,
        size: int = 3,
        names: list[str] | None = None,
    ) -> "WorkerPool":
        pool = cls(size=size)
        pool.workers = [
            Worker.create(name=names[i] if names and i < len(names) else None)
            for i in range(size)
        ]
        logger.info("[WorkerPool] Created with %d workers", size)
        return pool

    def start(
        self,
        task_queue: Any,
        executor_fn: Callable[[Any], Any],
    ) -> None:
        """启动所有 Worker。"""
        if self._running:
            return
        self._running = True

        for worker in self.workers:
            worker.start_thread(
                lambda w: w.run_loop(task_queue, executor_fn)
            )
        logger.info("[WorkerPool] All %d workers started", len(self.workers))

    def stop(self, timeout: float = 10.0) -> None:
        """停止所有 Worker。"""
        self._running = False
        for worker in self.workers:
            worker.stop(timeout=timeout)
        logger.info("[WorkerPool] All workers stopped")

    def idlest_worker(self) -> Worker | None:
        """
        返回最空闲的 Worker。

        空闲定义：当前未执行任务（IDLE 状态）。
        如果所有 Worker 都在忙，返回负载最轻的那个。
        """
        idle = [w for w in self.workers if w.is_idle()]
        if idle:
            return idle[0]
        # 所有都在忙，返回任务完成数最多的（经验最丰富的）
        running = [w for w in self.workers if w.is_running()]
        if running:
            return min(running, key=lambda w: (
                w.total_duration_seconds,
                w.tasks_completed,
            ))
        return None

    def get_stats(self) -> dict[str, Any]:
        """获取 Worker 池统计信息。"""
        return {
            "size": len(self.workers),
            "running": sum(1 for w in self.workers if w.is_running()),
            "idle": sum(1 for w in self.workers if w.is_idle()),
            "workers": [w.to_dict() for w in self.workers],
            "total_completed": sum(w.tasks_completed for w in self.workers),
            "total_failed": sum(w.tasks_failed for w in self.workers),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Async Worker Pool
# ─────────────────────────────────────────────────────────────────────────────


class AsyncWorkerPool:
    """
    异步 Worker 池（基于 asyncio）。

    适用于异步环境下的任务执行。
    """

    def __init__(self, size: int = 3):
        self.size = size
        self.workers: list[str] = [
            f"async-worker-{uuid.uuid4().hex[:8]}" for _ in range(size)
        ]
        self._running = False
        self._busy: set[str] = set()
        self._results: dict[str, Any] = {}

    async def submit_async(self, task: Any) -> Any:
        """
        异步提交任务。
        返回 Task 执行结果。
        """
        worker_id = self._acquire_worker()
        try:
            result = await self._execute_task(worker_id, task)
            self._results[task.id] = result
            return result
        finally:
            self._release_worker(worker_id)

    async def _execute_task(self, worker_id: str, task: Any) -> Any:
        """实际执行任务（子类可重写）。"""
        from agent.task_manager.state import TaskResult
        await asyncio.sleep(0.1)  # placeholder
        return TaskResult(status="completed")

    def _acquire_worker(self) -> str:
        for wid in self.workers:
            if wid not in self._busy:
                self._busy.add(wid)
                return wid
        # 全部忙，使用 round-robin
        wid = self.workers[0]
        self._busy.add(wid)
        return wid

    def _release_worker(self, worker_id: str) -> None:
        self._busy.discard(worker_id)

    def get_stats(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "busy": len(self._busy),
            "idle": self.size - len(self._busy),
            "results_collected": len(self._results),
        }
