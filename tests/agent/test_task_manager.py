"""
tests/agent/test_task_manager.py — Task Manager Integration Tests

Imports bypass agent/__init__.py to avoid cascade import issues with existing code.
"""
# Import submodules directly to bypass agent/__init__.py
import sys, os
_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, _root)

import pytest
import tempfile

from agent.task_manager.state import TaskState, Task, TaskConfig, TaskResult, TaskPriority
from agent.task_manager.queue import TaskQueue, QueueEntry
from agent.task_manager.scheduler import TaskScheduler, SchedulingPolicy
from agent.task_manager.worker import Worker, WorkerPool, WorkerStatus


class TestTaskState:
    def test_task_state_values(self):
        assert TaskState.PENDING.value == "pending"
        assert TaskState.RUNNING.value == "running"
        assert TaskState.COMPLETED.value == "completed"
        assert TaskState.FAILED.value == "failed"


class TestTaskPriority:
    def test_priority_order(self):
        assert TaskPriority.CRITICAL < TaskPriority.HIGH
        assert TaskPriority.HIGH < TaskPriority.NORMAL
        assert TaskPriority.NORMAL < TaskPriority.LOW


class TestTaskConfig:
    def test_defaults(self):
        cfg = TaskConfig()
        assert cfg.max_steps == 50
        assert cfg.max_retries == 3
        assert cfg.timeout_seconds == 3600
        assert cfg.priority == TaskPriority.NORMAL
        assert cfg.checkpoint_interval == 5

    def test_custom(self):
        cfg = TaskConfig(
            max_steps=100,
            max_retries=5,
            priority=TaskPriority.HIGH,
            tags=["security", "urgent"],
        )
        assert cfg.max_steps == 100
        assert cfg.max_retries == 5
        assert cfg.priority == TaskPriority.HIGH
        assert "security" in cfg.tags


class TestTaskResult:
    def test_to_dict(self):
        result = TaskResult(
            status="completed",
            output="analysis complete",
            parsed_output={"findings": []},
            steps_completed=10,
            tool_calls_total=25,
            evidence_refs=["MEU-001", "MEU-002"],
        )
        d = result.to_dict()
        assert d["status"] == "completed"
        assert d["steps_completed"] == 10
        assert len(d["evidence_refs"]) == 2


class TestTask:
    def test_create_factory(self):
        task = Task.create(
            description="Analyze SQL injection",
            inputs={"repo": "my-repo"},
            priority=TaskPriority.HIGH,
            tags=["security"],
        )
        assert task.id.startswith("task-")
        assert task.description == "Analyze SQL injection"
        assert task.state == TaskState.PENDING
        assert task.config.priority == TaskPriority.HIGH

    def test_state_transitions(self):
        task = Task.create(description="test task")
        assert task.is_runnable is True
        assert task.is_terminal is False

        task.enqueue()
        assert task.state == TaskState.QUEUED

        task.schedule("worker-1")
        assert task.state == TaskState.SCHEDULED
        assert task.worker_id == "worker-1"

        task.start()
        assert task.state == TaskState.RUNNING
        assert task.started_at is not None

        result = TaskResult(status="completed", steps_completed=5)
        task.complete(result)
        assert task.state == TaskState.COMPLETED
        assert task.is_terminal is True

    def test_retry_logic(self):
        task = Task.create(description="test", config=TaskConfig(max_retries=2))
        # max_retries=2 → retry_count 0→1→2 fails when >= 2
        ok1 = task.retry()
        assert ok1 is True
        assert task.retry_count == 1
        assert task.state == TaskState.RETRYING

        ok2 = task.retry()
        assert ok2 is True
        assert task.retry_count == 2
        assert task.state == TaskState.RETRYING

        ok3 = task.retry()
        assert ok3 is False
        assert task.retry_count == 2
        assert task.state == TaskState.FAILED

    def test_fail_and_cancel(self):
        task = Task.create(description="failing task")
        task.fail("some error")
        assert task.state == TaskState.FAILED
        assert "some error" in task.error_history

        task2 = Task.create(description="cancelled task")
        task2.cancel()
        assert task2.state == TaskState.CANCELLED
        assert task2.is_terminal is True

    def test_to_dict_and_from_dict(self):
        task = Task.create(
            description="serialization test",
            inputs={"key": "value"},
            priority=TaskPriority.CRITICAL,
        )
        task.start()
        d = task.to_dict()
        assert d["description"] == "serialization test"
        assert d["state"] == "running"
        assert d["config"]["priority"] == 1

        restored = Task.from_dict(d)
        assert restored.description == "serialization test"
        assert restored.state == TaskState.RUNNING

    def test_add_checkpoint(self):
        task = Task.create(description="checkpoint test")
        task.add_checkpoint("ckpt-001")
        task.add_checkpoint("ckpt-002")
        assert len(task.checkpoint_ids) == 2

    def test_duration_calculation(self):
        task = Task.create(description="timing test")
        task.start()
        dur = task.duration_seconds()
        assert dur is not None
        assert dur >= 0


class TestTaskQueue:
    def test_enqueue_dequeue(self, tmp_path):
        db_path = tmp_path / "queue.db"
        queue = TaskQueue(db_path=str(db_path))

        task = Task.create(description="q-test")
        entry = queue.enqueue(task, priority=TaskPriority.HIGH)
        assert entry.priority == 2
        assert queue.size() == 1

        dequeued = queue.dequeue()
        assert dequeued is not None
        assert dequeued.task.id == task.id
        assert queue.size() == 0

    def test_priority_order(self, tmp_path):
        db_path = tmp_path / "priority_queue.db"
        queue = TaskQueue(db_path=str(db_path))

        # CRITICAL=1, NORMAL=3, LOW=4 — queue sorts ASC, so CRITICAL first
        t_low = Task.create(description="low", priority=TaskPriority.LOW)
        t_high = Task.create(description="high", priority=TaskPriority.CRITICAL)
        t_normal = Task.create(description="normal", priority=TaskPriority.NORMAL)

        queue.enqueue(t_low)
        queue.enqueue(t_high)
        queue.enqueue(t_normal)

        first = queue.dequeue()
        second = queue.dequeue()
        third = queue.dequeue()

        # Verify exact priorities
        assert first.task.description == "high", f"Expected high (CRITICAL=1), got {first.task.description}"
        assert first.priority == 1  # CRITICAL

        assert second.task.description == "normal"
        assert second.priority == 3  # NORMAL

        assert third.task.description == "low"
        assert third.priority == 4  # LOW

    def test_cancel(self, tmp_path):
        db_path = tmp_path / "cancel_test.db"
        queue = TaskQueue(db_path=str(db_path))

        task = Task.create(description="to cancel")
        queue.enqueue(task)
        assert queue.size() == 1

        queue.cancel(task.id)
        assert queue.size() == 0  # Soft delete

    def test_requeue(self, tmp_path):
        db_path = tmp_path / "requeue_test.db"
        queue = TaskQueue(db_path=str(db_path))

        task = Task.create(description="requeue test")
        queue.enqueue(task)
        queue.dequeue()

        # Requeue after failure
        queue.requeue(task)
        assert queue.size() == 1

    def test_peek(self, tmp_path):
        db_path = tmp_path / "peek_test.db"
        queue = TaskQueue(db_path=str(db_path))

        for i in range(5):
            queue.enqueue(Task.create(description=f"task-{i}"))

        peeked = queue.peek(3)
        assert len(peeked) == 3
        assert queue.size() == 5  # peek doesn't remove

    def test_clear(self, tmp_path):
        db_path = tmp_path / "clear_test.db"
        queue = TaskQueue(db_path=str(db_path))

        for i in range(3):
            queue.enqueue(Task.create(description=f"clear-{i}"))

        cleared = queue.clear()
        assert cleared == 3
        assert queue.is_empty()


class TestTaskScheduler:
    def test_init(self, tmp_path):
        db_path = tmp_path / "scheduler_test.db"
        queue = TaskQueue(db_path=str(db_path))
        pool = WorkerPool.create(size=2)
        scheduler = TaskScheduler(
            queue,
            pool,
            policy=SchedulingPolicy.PRIORITY,
        )
        assert scheduler.policy == SchedulingPolicy.PRIORITY
        assert scheduler.get_pending_count() == 0

    def test_schedule_one(self, tmp_path):
        db_path = tmp_path / "schedule_test.db"
        queue = TaskQueue(db_path=str(db_path))
        pool = WorkerPool.create(size=2)
        scheduler = TaskScheduler(queue, pool)

        task = Task.create(description="scheduled task")
        queue.enqueue(task, priority=TaskPriority.NORMAL)

        scheduled = scheduler.schedule_one()
        assert scheduled is not None
        assert scheduled.description == "scheduled task"
        assert scheduler.metrics.tasks_scheduled == 1

    def test_on_task_completed(self, tmp_path):
        db_path = tmp_path / "completed_test.db"
        queue = TaskQueue(db_path=str(db_path))
        pool = WorkerPool.create(size=1)
        scheduler = TaskScheduler(queue, pool)

        task = Task.create(description="to complete")
        task.start()
        task.complete(TaskResult(status="completed"))

        scheduler.on_task_completed(task)
        assert scheduler.metrics.tasks_completed == 1

    def test_get_metrics(self, tmp_path):
        db_path = tmp_path / "metrics_test.db"
        queue = TaskQueue(db_path=str(db_path))
        pool = WorkerPool.create(size=2)
        scheduler = TaskScheduler(queue, pool)

        metrics = scheduler.get_metrics()
        assert "policy" in metrics
        assert "queue_size" in metrics
        assert metrics["tasks_scheduled"] == 0


class TestWorker:
    def test_create(self):
        worker = Worker.create(name="test-worker")
        assert worker.id.startswith("worker-")
        assert worker.status == WorkerStatus.IDLE
        assert worker.tasks_completed == 0

    def test_to_dict(self):
        worker = Worker.create(name="dict-worker")
        d = worker.to_dict()
        assert "id" in d
        assert "status" in d
        assert d["status"] == "idle"


class TestWorkerPool:
    def test_create(self):
        pool = WorkerPool.create(size=3)
        assert len(pool.workers) == 3
        assert all(w.status == WorkerStatus.IDLE for w in pool.workers)

    def test_idlest_worker(self):
        pool = WorkerPool.create(size=3)
        idlest = pool.idlest_worker()
        assert idlest is not None
        assert idlest.status == WorkerStatus.IDLE

    def test_get_stats(self):
        pool = WorkerPool.create(size=3)
        stats = pool.get_stats()
        assert stats["size"] == 3
        assert stats["idle"] == 3
        assert stats["running"] == 0
