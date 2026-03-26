"""
agent/task_manager/state.py — Task State Machine & Task Model

Task 生命周期状态机：
    PENDING → SCHEDULED → RUNNING → WAITING_VERIFICATION
                                            ↓
    COMPLETED ← VERIFIED ← VERIFICATION_FAILED ← (重试)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Task State
# ─────────────────────────────────────────────────────────────────────────────


class TaskState(str, Enum):
    """Task 生命周期状态枚举。"""
    PENDING = "pending"                    # 等待入队
    QUEUED = "queued"                      # 已入队，等待调度
    SCHEDULED = "scheduled"                # 已分配 Worker
    RUNNING = "running"                    # 正在执行
    WAITING_VERIFICATION = "waiting_verification"  # 等待验证
    VERIFIED = "verified"                  # 验证通过
    VERIFICATION_FAILED = "verification_failed"   # 验证失败
    RETRYING = "retrying"                  # 重新执行中
    COMPLETED = "completed"                # 最终完成
    FAILED = "failed"                      # 失败
    CANCELLED = "cancelled"                # 用户取消
    TIMEOUT = "timeout"                    # 超时


class TaskPriority(int, Enum):
    """Task 优先级（数字越小优先级越高）。"""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5


# ─────────────────────────────────────────────────────────────────────────────
# Task Config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TaskConfig:
    """Task 级别配置。"""
    max_steps: int = 50
    max_retries: int = 3
    timeout_seconds: int = 3600          # 1 小时默认超时
    checkpoint_interval: int = 5
    evidence_required: bool = True
    verification_required: bool = True
    workflow_id: str | None = None
    skill_ids: list[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.NORMAL
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Task Result
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    """Task 执行结果。"""
    status: str
    output: str = ""
    parsed_output: dict[str, Any] | None = None
    error: str | None = None
    duration_seconds: float = 0.0
    steps_completed: int = 0
    tool_calls_total: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    checkpoint_id: str | None = None
    final_state: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output": self.output[:200] if self.output else "",
            "parsed_output": self.parsed_output,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "steps_completed": self.steps_completed,
            "tool_calls_total": self.tool_calls_total,
            "evidence_refs": self.evidence_refs,
            "checkpoint_id": self.checkpoint_id,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Task
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Task:
    """
    Task — 长时间运行任务的基本单元。

    代表一个完整的分析任务（对应一个 Workflow 或单个 Skill）。

    生命周期：
        1. 创建 → PENDING
        2. 入队 → QUEUED
        3. 调度 → SCHEDULED
        4. 执行 → RUNNING
        5. 验证 → WAITING_VERIFICATION / VERIFIED
        6. 完成 → COMPLETED / FAILED / TIMEOUT
    """
    id: str
    description: str
    config: TaskConfig

    # 溯源
    workflow_id: str | None = None
    skill_ids: list[str] = field(default_factory=list)
    parent_task_id: str | None = None    # 父任务（子任务溯源）

    # 状态
    state: TaskState = TaskState.PENDING
    result: TaskResult | None = None

    # 执行追踪
    current_step: int = 0
    retry_count: int = 0
    worker_id: str | None = None          # 分配到的 Worker ID
    checkpoint_ids: list[str] = field(default_factory=list)

    # 时间戳
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None

    # 输入输出
    inputs: dict[str, Any] = field(default_factory=dict)
    error_history: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        description: str,
        inputs: dict[str, Any] | None = None,
        workflow_id: str | None = None,
        skill_ids: list[str] | None = None,
        config: TaskConfig | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        tags: list[str] | None = None,
        parent_task_id: str | None = None,
    ) -> "Task":
        """Task 工厂方法。"""
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        return cls(
            id=task_id,
            description=description,
            workflow_id=workflow_id,
            skill_ids=skill_ids or [],
            inputs=inputs or {},
            config=config or TaskConfig(
                priority=priority,
                tags=tags or [],
                workflow_id=workflow_id,
                skill_ids=skill_ids or [],
            ),
            parent_task_id=parent_task_id,
        )

    # ── 状态转换 ─────────────────────────────────────────────────────────

    def enqueue(self) -> None:
        self.state = TaskState.QUEUED

    def schedule(self, worker_id: str) -> None:
        self.state = TaskState.SCHEDULED
        self.worker_id = worker_id

    def start(self) -> None:
        self.state = TaskState.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()

    def waiting_verification(self) -> None:
        self.state = TaskState.WAITING_VERIFICATION

    def verify_pass(self) -> None:
        self.state = TaskState.VERIFIED

    def verify_fail(self) -> None:
        self.state = TaskState.VERIFICATION_FAILED

    def retry(self) -> bool:
        """尝试重试。返回是否允许继续重试。"""
        if self.retry_count >= self.config.max_retries:
            self.state = TaskState.FAILED
            return False
        self.state = TaskState.RETRYING
        self.retry_count += 1
        return True

    def complete(self, result: TaskResult) -> None:
        self.state = TaskState.COMPLETED
        self.result = result
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def fail(self, error: str, result: TaskResult | None = None) -> None:
        self.state = TaskState.FAILED
        self.error_history.append(error)
        if result:
            self.result = result
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def cancel(self) -> None:
        self.state = TaskState.CANCELLED
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def timeout(self) -> None:
        self.state = TaskState.TIMEOUT
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def add_checkpoint(self, checkpoint_id: str) -> None:
        self.checkpoint_ids.append(checkpoint_id)

    # ── 序列化 ─────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "state": self.state.value,
            "workflow_id": self.workflow_id,
            "skill_ids": self.skill_ids,
            "parent_task_id": self.parent_task_id,
            "config": {
                "max_steps": self.config.max_steps,
                "max_retries": self.config.max_retries,
                "timeout_seconds": self.config.timeout_seconds,
                "priority": self.config.priority.value,
                "tags": self.config.tags,
            },
            "current_step": self.current_step,
            "retry_count": self.retry_count,
            "worker_id": self.worker_id,
            "checkpoint_ids": self.checkpoint_ids,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "inputs": self.inputs,
            "error_history": self.error_history,
            "result": self.result.to_dict() if self.result else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        config_data = data.get("config", {})
        config = TaskConfig(
            max_steps=config_data.get("max_steps", 50),
            max_retries=config_data.get("max_retries", 3),
            timeout_seconds=config_data.get("timeout_seconds", 3600),
            priority=TaskPriority(config_data.get("priority", TaskPriority.NORMAL.value)),
            tags=config_data.get("tags", []),
        )
        result = None
        if data.get("result"):
            result = TaskResult(**data["result"])
        return cls(
            id=data["id"],
            description=data.get("description", ""),
            state=TaskState(data.get("state", TaskState.PENDING.value)),
            workflow_id=data.get("workflow_id"),
            skill_ids=data.get("skill_ids", []),
            parent_task_id=data.get("parent_task_id"),
            config=config,
            current_step=data.get("current_step", 0),
            retry_count=data.get("retry_count", 0),
            worker_id=data.get("worker_id"),
            checkpoint_ids=data.get("checkpoint_ids", []),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            inputs=data.get("inputs", {}),
            error_history=data.get("error_history", []),
            result=result,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Task":
        return cls.from_dict(json.loads(json_str))

    # ── 工具 ──────────────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """是否处于终态。"""
        return self.state in {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.TIMEOUT,
        }

    @property
    def is_runnable(self) -> bool:
        """是否可以运行。"""
        return self.state in {
            TaskState.PENDING,
            TaskState.QUEUED,
            TaskState.SCHEDULED,
            TaskState.RETRYING,
        }

    def duration_seconds(self) -> float | None:
        """计算已执行时长（秒）。"""
        if not self.started_at:
            return None
        end = self.completed_at or datetime.now(timezone.utc).isoformat()
        try:
            t_start = datetime.fromisoformat(self.started_at)
            t_end = datetime.fromisoformat(end)
            return (t_end - t_start).total_seconds()
        except (ValueError, TypeError):
            return None
