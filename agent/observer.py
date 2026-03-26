"""
agent/observer.py — Agent Observer System

观察者模式实现，用于监控 Agent 执行过程中的各种事件，
并将状态变更注入到 Memory 系统。

支持的事件：
- runtime_start / runtime_complete / runtime_recovered
- plan_created
- step_start / step_complete / step_error
- checkpoint_saved
- tool_call / tool_result
- evidence_added
- error_occurred
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Event Types
# ─────────────────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    """Agent 执行事件枚举。"""
    # Runtime lifecycle
    RUNTIME_START = "runtime_start"
    RUNTIME_COMPLETE = "runtime_complete"
    RUNTIME_RECOVERED = "runtime_recovered"

    # Planner
    PLAN_CREATED = "plan_created"
    PLAN_REFINED = "plan_refined"

    # Step lifecycle
    STEP_START = "step_start"
    STEP_COMPLETE = "step_complete"
    STEP_ERROR = "step_error"

    # Tool
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # Evidence
    EVIDENCE_ADDED = "evidence_added"
    EVIDENCE_VERIFIED = "evidence_verified"

    # Checkpoint
    CHECKPOINT_SAVED = "checkpoint_saved"
    CHECKPOINT_RESTORED = "checkpoint_restored"

    # Error
    ERROR_OCCURRED = "error_occurred"


# ─────────────────────────────────────────────────────────────────────────────
# Event Record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Event:
    """通用事件记录。"""
    type: EventType
    data: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentObserver Protocol
# ─────────────────────────────────────────────────────────────────────────────


class AgentObserver(ABC):
    """
    Agent 执行观察者抽象接口。

    实现此接口即可订阅 Agent 执行事件。
    用于：
    - Memory 注入
    - 监控告警
    - 调试日志
    - 性能分析
    - 自定义验证
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """观察者名称（用于日志）。"""
        ...

    @abstractmethod
    def on_event(
        self,
        event_type: str,
        data: dict[str, Any],
        state: Any,
    ) -> None:
        """
        接收事件通知。

        Args:
            event_type: 事件类型（EventType 枚举值）
            data      : 事件数据
            state     : 当前 RuntimeState
        """
        ...

    def on_start(self) -> None:
        """观察者启动时调用。"""
        pass

    def on_stop(self) -> None:
        """观察者停止时调用。"""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Built-in Observers
# ─────────────────────────────────────────────────────────────────────────────


class NullObserver(AgentObserver):
    """空观察者（不做任何事）。"""

    @property
    def name(self) -> str:
        return "null"

    def on_event(
        self,
        event_type: str,
        data: dict[str, Any],
        state: Any,
    ) -> None:
        pass


class LoggingObserver(AgentObserver):
    """
    日志观察者。

    将所有事件记录到标准 logging 系统。
    """

    def __init__(self, level: int = logging.INFO):
        self._level = level
        self._logger = logging.getLogger("odin.agent.observer")

    @property
    def name(self) -> str:
        return "logging"

    def on_event(
        self,
        event_type: str,
        data: dict[str, Any],
        state: Any,
    ) -> None:
        task_id = getattr(state, "task_id", "?")
        self._logger.log(
            self._level,
            "[%s] %s: %s",
            task_id,
            event_type,
            _summarize_data(data),
        )


class MemoryInjectionObserver(AgentObserver):
    """
    Memory 注入观察者。

    将执行过程中的关键事件和中间结果注入到 WorkingMemory。
    这是 Agent 长期运行能力的关键组件。
    """

    def __init__(self, memory_store: Any | None = None):
        self._logger = logging.getLogger("odin.agent.observer.memory")
        self._memory_store = memory_store
        self._step_summaries: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "memory_injection"

    def on_event(
        self,
        event_type: str,
        data: dict[str, Any],
        state: Any,
    ) -> None:
        handler_name = f"_handle_{event_type}"
        handler = getattr(self, handler_name, None)
        if handler:
            try:
                handler(data, state)
            except Exception:
                self._logger.exception("Memory injection handler failed for %s", event_type)

    def _handle_step_complete(self, data: dict[str, Any], state: Any) -> None:
        """步骤完成时，提取关键信息存入 Memory。"""
        step_data = {
            "step": data.get("step"),
            "status": data.get("status"),
            "observation": data.get("observation", "")[:200],
            "evidence_refs": data.get("evidence_refs", []),
            "duration_ms": data.get("duration_ms"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._step_summaries.append(step_data)

        if len(self._step_summaries) > 50:
            self._step_summaries = self._step_summaries[-50:]

        if self._memory_store:
            try:
                self._memory_store.append_step_summary(step_data)
            except Exception:
                self._logger.warning("Failed to persist step summary to memory store")

    def _handle_plan_created(self, data: dict[str, Any], state: Any) -> None:
        """计划创建时，存入 Memory。"""
        if self._memory_store:
            try:
                self._memory_store.set_current_plan(data.get("plan", []))
            except Exception:
                pass

    def _handle_checkpoint_saved(self, data: dict[str, Any], state: Any) -> None:
        """Checkpoint 保存时，记录元信息。"""
        if self._memory_store:
            try:
                self._memory_store.record_checkpoint(
                    checkpoint_id=data.get("checkpoint_id"),
                    step=data.get("step"),
                )
            except Exception:
                pass

    def get_step_summaries(self) -> list[dict[str, Any]]:
        return list(self._step_summaries)


class MetricsObserver(AgentObserver):
    """
    指标收集观察者。

    收集执行指标供 Prometheus 等监控系统使用。
    """

    def __init__(self):
        self._logger = logging.getLogger("odin.agent.observer.metrics")
        self._counters: dict[str, int] = {}
        self._histograms: dict[str, list[float]] = {}

    @property
    def name(self) -> str:
        return "metrics"

    def on_event(
        self,
        event_type: str,
        data: dict[str, Any],
        state: Any,
    ) -> None:
        # 计数
        self._counters[event_type] = self._counters.get(event_type, 0) + 1

        # 直方图
        if event_type in {EventType.STEP_COMPLETE.value, EventType.TOOL_RESULT.value}:
            duration = data.get("duration_ms", 0)
            if duration > 0:
                self._histograms.setdefault(event_type, []).append(duration)

    def get_counters(self) -> dict[str, int]:
        return dict(self._counters)

    def get_histograms(self) -> dict[str, list[float]]:
        return dict(self._histograms)


# ─────────────────────────────────────────────────────────────────────────────
# Observer Manager
# ─────────────────────────────────────────────────────────────────────────────


class ObserverManager:
    """
    观察者管理器。

    负责管理所有观察者，并广播事件。
    """

    def __init__(self):
        self._observers: list[AgentObserver] = []

    def register(self, observer: AgentObserver) -> None:
        """注册观察者。"""
        self._observers.append(observer)
        observer.on_start()
        logger.info("Registered observer: %s", observer.name)

    def unregister(self, observer: AgentObserver) -> None:
        """注销观察者。"""
        self._observers.remove(observer)
        observer.on_stop()
        logger.info("Unregistered observer: %s", observer.name)

    def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        state: Any,
    ) -> None:
        """向所有观察者广播事件。"""
        for obs in self._observers:
            try:
                obs.on_event(event_type, data, state)
            except Exception:
                logger.exception("Observer %s raised during %s", obs.name, event_type)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _summarize_data(data: dict[str, Any], max_len: int = 80) -> str:
    """将 data 字典压缩为单行摘要字符串。"""
    if not data:
        return "{}"
    parts = []
    for key, value in list(data.items())[:5]:
        if isinstance(value, str):
            parts.append(f"{key}={value[:max_len]}")
        elif isinstance(value, (list, dict)):
            parts.append(f"{key}=<{type(value).__name__}>")
        else:
            parts.append(f"{key}={value}")
    return " ".join(parts)
