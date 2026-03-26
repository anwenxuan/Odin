"""
agent/observability/tracer.py — Distributed Tracing

基于 OpenTelemetry 的分布式链路追踪系统。

核心概念：
- Tracer    : 追踪器实例（对应一个组件）
- Span      : 单个操作单元（LLM 调用、工具执行、步骤）
- Trace     : 完整请求链路（从 Task 到完成的所有 Spans）
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Span Kind
# ─────────────────────────────────────────────────────────────────────────────


class SpanKind(str, Enum):
    """Span 类型。"""
    INTERNAL = "internal"
    LLM = "llm"                    # LLM API 调用
    TOOL = "tool"                  # 工具执行
    AGENT = "agent"               # Agent 步骤
    TASK = "task"                # Task 生命周期
    SANDBOX = "sandbox"          # 沙箱执行


# ─────────────────────────────────────────────────────────────────────────────
# Span
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Span:
    """
    单个追踪 Span。

    代表一个有时间跨度的操作单元。
    """
    name: str
    span_id: str
    trace_id: str
    parent_span_id: str = ""
    kind: SpanKind = SpanKind.INTERNAL

    # 时间
    start_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    end_time: str | None = None
    duration_ms: int = 0

    # 标签
    tags: dict[str, Any] = field(default_factory=dict)

    # 事件
    events: list[dict[str, Any]] = field(default_factory=list)

    # 状态
    status: str = "OK"           # OK / ERROR
    error_message: str | None = None

    @classmethod
    def create(
        cls,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str = "",
        kind: SpanKind = SpanKind.INTERNAL,
    ) -> "Span":
        return cls(
            name=name,
            span_id=f"span-{uuid.uuid4().hex[:16]}",
            trace_id=trace_id or f"trace-{uuid.uuid4().hex[:16]}",
            parent_span_id=parent_span_id,
            kind=kind,
        )

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append({
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attributes": attributes or {},
        })

    def end(self, status: str = "OK", error_message: str | None = None) -> None:
        self.end_time = datetime.now(timezone.utc).isoformat()
        self.status = status
        self.error_message = error_message
        try:
            t_start = datetime.fromisoformat(self.start_time)
            t_end = datetime.fromisoformat(self.end_time)
            self.duration_ms = int((t_end - t_start).total_seconds() * 1000)
        except (ValueError, TypeError):
            self.duration_ms = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "tags": self.tags,
            "events": self.events,
            "status": self.status,
            "error_message": self.error_message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Context Variable for Current Span
# ─────────────────────────────────────────────────────────────────────────────


_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)
_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Tracer
# ─────────────────────────────────────────────────────────────────────────────


class AgentTracer:
    """
    Agent 链路追踪器。

    提供基于 OpenTelemetry 风格的内嵌链路追踪。

    使用方式：
        tracer = AgentTracer(component="agent-runtime")

        with tracer.start_span("run_task", SpanKind.TASK) as span:
            span.set_tag("task_id", "task-001")

            with tracer.start_span("llm_call", SpanKind.LLM) as llm_span:
                llm_span.set_tag("model", "gpt-4o-mini")
                # ... LLM 调用 ...
                llm_span.add_event("tokens_used", {"input": 500, "output": 200})

            with tracer.start_span("tool_execution", SpanKind.TOOL) as tool_span:
                tool_span.set_tag("tool_id", "read_file")
                # ... 工具执行 ...

        # 获取完整 trace
        trace = tracer.get_trace(span.trace_id)
    """

    def __init__(self, component: str = "agent", service_name: str = "odin"):
        self.component = component
        self.service_name = service_name
        self._spans: dict[str, list[Span]] = {}   # trace_id → spans
        self._current_span_stack: list[Span] = []
        self._lock = __import__("threading").Lock()

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        trace_id: str | None = None,
        parent_span_id: str = "",
        tags: dict[str, Any] | None = None,
    ) -> Span:
        """
        开始一个新的 Span。

        使用 with 语句自动管理生命周期：
            with tracer.start_span("my_operation", SpanKind.AGENT) as span:
                span.set_tag("key", "value")
                # ... 操作 ...
        """
        # 获取当前 trace_id（继承或新建）
        current_trace = _current_trace_id.get()
        if trace_id:
            effective_trace = trace_id
        elif current_trace:
            effective_trace = current_trace
        else:
            effective_trace = f"trace-{uuid.uuid4().hex[:16]}"
            _current_trace_id.set(effective_trace)

        parent_id = ""
        if self._current_span_stack:
            parent_id = self._current_span_stack[-1].span_id

        span = Span.create(
            name=name,
            trace_id=effective_trace,
            parent_span_id=parent_id or parent_span_id,
            kind=kind,
        )

        if tags:
            for k, v in tags.items():
                span.set_tag(k, v)

        with self._lock:
            self._spans.setdefault(effective_trace, []).append(span)
            self._current_span_stack.append(span)

        _current_span.set(span)

        logger.debug(
            "[Tracer %s] Start span: %s (trace=%s parent=%s)",
            self.component,
            name,
            span.trace_id,
            span.parent_span_id,
        )

        return span

    def end_span(
        self,
        span: Span,
        status: str = "OK",
        error_message: str | None = None,
    ) -> None:
        """结束一个 Span。"""
        span.end(status=status, error_message=error_message)

        with self._lock:
            if self._current_span_stack and self._current_span_stack[-1] is span:
                self._current_span_stack.pop()

        # 设置新的 current span
        if self._current_span_stack:
            _current_span.set(self._current_span_stack[-1])
        else:
            _current_span.set(None)
            _current_trace_id.set("")

        logger.debug(
            "[Tracer %s] End span: %s (duration=%dms status=%s)",
            self.component,
            span.name,
            span.duration_ms,
            span.status,
        )

    def get_current_span(self) -> Span | None:
        """获取当前活动的 Span。"""
        return _current_span.get()

    def get_trace(self, trace_id: str) -> list[Span]:
        """获取完整 Trace 的所有 Spans。"""
        return list(self._spans.get(trace_id, []))

    def trace_llm_call(
        self,
        name: str = "llm_call",
        model: str = "",
        **tags: Any,
    ) -> Span:
        """专门用于追踪 LLM 调用。"""
        span = self.start_span(name, SpanKind.LLM, tags={"model": model, **tags})
        return span

    def trace_tool_call(
        self,
        tool_id: str,
        **tags: Any,
    ) -> Span:
        """专门用于追踪工具调用。"""
        span = self.start_span(f"tool:{tool_id}", SpanKind.TOOL, tags={"tool_id": tool_id, **tags})
        return span

    def trace_agent_step(
        self,
        step: int,
        **tags: Any,
    ) -> Span:
        """专门用于追踪 Agent 步骤。"""
        span = self.start_span(f"step_{step}", SpanKind.AGENT, tags={"step": step, **tags})
        return span

    def export_trace(self, trace_id: str) -> dict[str, Any]:
        """
        导出 Trace 为可序列化格式。

        可发送到 OpenTelemetry Collector。
        """
        spans = self.get_trace(trace_id)
        if not spans:
            return {}

        # 构建树结构（按父子关系）
        span_map = {s.span_id: s for s in spans}
        root_spans = [s for s in spans if not s.parent_span_id]

        return {
            "service_name": self.service_name,
            "trace_id": trace_id,
            "total_spans": len(spans),
            "total_duration_ms": max((s.duration_ms for s in spans), default=0),
            "root_spans": [s.to_dict() for s in root_spans],
            "all_spans": [s.to_dict() for s in spans],
        }

    def get_stats(self) -> dict[str, Any]:
        """获取追踪统计。"""
        total_spans = sum(len(v) for v in self._spans.values())
        error_spans = sum(
            1 for spans in self._spans.values()
            for s in spans if s.status == "ERROR"
        )
        return {
            "total_traces": len(self._spans),
            "total_spans": total_spans,
            "error_spans": error_spans,
            "error_rate": error_spans / max(total_spans, 1),
        }
