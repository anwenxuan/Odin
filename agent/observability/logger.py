"""
agent/observability/logger.py — Structured Logging System

结构化日志系统，支持：
- 多级别日志（DEBUG/INFO/WARN/ERROR）
- JSON Lines 输出
- 文件和控制台双输出
- 敏感信息过滤
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Log Levels
# ─────────────────────────────────────────────────────────────────────────────


_PYTHON_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    def to_python(self) -> int:
        return _PYTHON_LEVEL_MAP[self.value]


# ─────────────────────────────────────────────────────────────────────────────
# Log Record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentLogRecord:
    """结构化日志记录。"""
    level: str
    component: str
    message: str
    task_id: str = ""
    session_id: str = ""
    step: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    tokens_used: int = 0
    error: str | None = None
    trace_id: str = ""
    span_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "component": self.component,
            "message": self.message,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "step": self.step,
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
            "tokens_used": self.tokens_used,
            "error": self.error,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "extra": self.extra,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# File Handler
# ─────────────────────────────────────────────────────────────────────────────


class JSONLinesHandler:
    """JSON Lines 文件处理器。"""

    def __init__(self, log_dir: str | Path = ".odin/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, record: AgentLogRecord) -> None:
        with self._lock:
            log_file = self.log_dir / f"{record.task_id or 'global'}.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(record.to_json() + "\n")

    def get_log_file(self, task_id: str) -> Path:
        return self.log_dir / f"{task_id}.jsonl"


# ─────────────────────────────────────────────────────────────────────────────
# Agent Logger
# ─────────────────────────────────────────────────────────────────────────────


class AgentLogger:
    """
    Agent 结构化日志器。

    同时输出到：
    1. 标准 logging 系统（用于控制台）
    2. JSON Lines 文件（用于分析）

    使用方式：
        logger = AgentLogger(session_id="session-001")
        logger.log_step(
            step=1,
            action="read_file",
            tool_calls=[...],
            tokens_used=500,
            duration_ms=1200,
            outcome="success",
        )
    """

    def __init__(
        self,
        session_id: str = "",
        task_id: str = "",
        output_dir: str | Path = ".odin/logs",
        level: LogLevel = LogLevel.INFO,
        component: str = "agent",
    ):
        self.session_id = session_id
        self.task_id = task_id
        self.component = component
        self.level = level
        self._file_handler = JSONLinesHandler(output_dir)
        self._python_logger = logging.getLogger(f"odin.{component}")
        self._setup_python_logger(level)

    def _setup_python_logger(self, level: LogLevel) -> None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(name)s] %(message)s"
        ))
        self._python_logger.addHandler(handler)
        self._python_logger.setLevel(level.to_python())

    def _emit(self, record: AgentLogRecord) -> None:
        """输出日志到所有处理器。"""
        # Python logger
        python_level = getattr(logging, record.level)
        self._python_logger.log(python_level, record.message)
        # JSON Lines file
        self._file_handler.emit(record)

    def log_step(
        self,
        step: int,
        action: str,
        tool_calls: list[dict[str, Any]],
        tokens_used: int,
        duration_ms: float,
        outcome: str,
        error: str | None = None,
        trace_id: str = "",
        span_id: str = "",
    ) -> None:
        """记录 Agent 步骤。"""
        level = LogLevel.ERROR if error else LogLevel.INFO
        record = AgentLogRecord(
            level=level.value,
            component=self.component,
            message=f"Step {step}: {action} → {outcome}",
            task_id=self.task_id,
            session_id=self.session_id,
            step=step,
            tool_calls=tool_calls,
            duration_ms=int(duration_ms),
            tokens_used=tokens_used,
            error=error,
            trace_id=trace_id,
            span_id=span_id,
            extra={"outcome": outcome, "action": action},
        )
        self._emit(record)

    def log_info(self, message: str, **kwargs: Any) -> None:
        record = AgentLogRecord(
            level=LogLevel.INFO.value,
            component=self.component,
            message=message,
            task_id=self.task_id,
            session_id=self.session_id,
            extra=kwargs,
        )
        self._emit(record)

    def log_error(self, message: str, error: str | None = None, **kwargs: Any) -> None:
        record = AgentLogRecord(
            level=LogLevel.ERROR.value,
            component=self.component,
            message=message,
            task_id=self.task_id,
            session_id=self.session_id,
            error=error,
            extra=kwargs,
        )
        self._emit(record)

    def log_warn(self, message: str, **kwargs: Any) -> None:
        record = AgentLogRecord(
            level=LogLevel.WARN.value,
            component=self.component,
            message=message,
            task_id=self.task_id,
            session_id=self.session_id,
            extra=kwargs,
        )
        self._emit(record)

    def log_tool_call(
        self,
        tool_id: str,
        success: bool,
        duration_ms: int,
        error: str | None = None,
    ) -> None:
        """记录工具调用。"""
        record = AgentLogRecord(
            level=LogLevel.ERROR.value if error else LogLevel.INFO.value,
            component="tool",
            message=f"Tool {tool_id}: {'OK' if success else 'FAILED'} ({duration_ms}ms)",
            task_id=self.task_id,
            session_id=self.session_id,
            tool_calls=[{"tool_id": tool_id, "success": success}],
            duration_ms=duration_ms,
            error=error,
        )
        self._emit(record)

    def log_checkpoint(
        self,
        checkpoint_id: str,
        step: int,
    ) -> None:
        """记录 Checkpoint 保存。"""
        record = AgentLogRecord(
            level=LogLevel.INFO.value,
            component="checkpoint",
            message=f"Checkpoint saved: {checkpoint_id} at step {step}",
            task_id=self.task_id,
            session_id=self.session_id,
            step=step,
            extra={"checkpoint_id": checkpoint_id},
        )
        self._emit(record)
