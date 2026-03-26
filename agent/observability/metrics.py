"""
agent/observability/metrics.py — Prometheus Metrics

Prometheus 指标收集系统。

核心指标：
- agent_steps_total          — Agent 步骤总数
- agent_steps_by_outcome     — 按结果分类的步骤数
- tool_calls_total           — 工具调用总数
- tool_call_duration_seconds — 工具调用耗时
- token_usage_total          — Token 使用量
- task_completed_total       — 任务完成数
- task_failed_total          — 任务失败数
- verification_pass_rate     — 验证通过率
- checkpoint_saves_total     — Checkpoint 保存次数
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Metric Types
# ─────────────────────────────────────────────────────────────────────────────


class MetricType(str):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class MetricSample:
    """单个指标样本。"""
    name: str
    value: float
    labels: dict[str, str]
    timestamp: str


# ─────────────────────────────────────────────────────────────────────────────
# Simple Metrics Store (no external dependencies)
# ─────────────────────────────────────────────────────────────────────────────


class SimpleMetrics:
    """
    轻量级指标收集器。

    不依赖 prometheus_client，在没有外部 Pushgateway 时使用。
    数据存储在内存中，可通过 get_metrics() 导出。
    """

    def __init__(self):
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._histogram_buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self._labels: dict[str, dict[str, str]] = {}
        self._lock = Lock()

    # ── Counter ─────────────────────────────────────────────────────────

    def inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """增加 Counter。"""
        key = self._make_key(name, labels)
        with self._lock:
            self._counters[key] += value
            self._labels[key] = labels or {}

    def get_counter(self, name: str, labels: dict[str, str] | None = None) -> float:
        key = self._make_key(name, labels)
        with self._lock:
            return self._counters.get(key, 0.0)

    # ── Gauge ───────────────────────────────────────────────────────────

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """设置 Gauge 值。"""
        key = self._make_key(name, labels)
        with self._lock:
            self._gauges[key] = value
            self._labels[key] = labels or {}

    def get_gauge(self, name: str, labels: dict[str, str] | None = None) -> float:
        key = self._make_key(name, labels)
        with self._lock:
            return self._gauges.get(key, 0.0)

    # ── Histogram ──────────────────────────────────────────────────────

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """记录直方图值。"""
        key = self._make_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)
            self._labels[key] = labels or {}

    def histogram_stats(self, name: str, labels: dict[str, str] | None = None) -> dict[str, float]:
        """计算直方图统计。"""
        key = self._make_key(name, labels)
        with self._lock:
            values = self._histograms.get(key, [])
            if not values:
                return {"count": 0, "sum": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            return {
                "count": n,
                "sum": sum(sorted_vals),
                "avg": sum(sorted_vals) / n,
                "min": sorted_vals[0],
                "max": sorted_vals[-1],
                "p50": sorted_vals[int(n * 0.5)],
                "p95": sorted_vals[min(int(n * 0.95), n - 1)],
                "p99": sorted_vals[min(int(n * 0.99), n - 1)],
            }

    # ── Export ────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, Any]:
        """导出所有指标。"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    k: self.histogram_stats(k)
                    for k in self._histograms.keys()
                },
            }

    def _make_key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"


# ─────────────────────────────────────────────────────────────────────────────
# Agent Metrics
# ─────────────────────────────────────────────────────────────────────────────


class AgentMetrics:
    """
    Agent 指标收集器。

    统一管理所有 Agent 相关的 Prometheus 风格指标。

    使用方式：
        metrics = AgentMetrics()

        # 记录步骤
        metrics.inc_step(outcome="success", task_type="vulnerability_research")

        # 记录工具调用
        metrics.observe_tool("read_file", duration_seconds=0.5, success=True)

        # 记录 Token
        metrics.inc_tokens(input_tokens=1000, output_tokens=200, model="gpt-4o-mini")

        # 获取所有指标
        all_metrics = metrics.get_all()
    """

    METRIC_NAMES = [
        "agent_steps_total",
        "agent_step_duration_seconds",
        "tool_calls_total",
        "tool_call_duration_seconds",
        "token_usage_total",
        "task_completed_total",
        "task_failed_total",
        "verification_passed_total",
        "verification_failed_total",
        "checkpoint_saves_total",
        "memory_usage_bytes",
        "active_tasks_gauge",
    ]

    def __init__(self, pushgateway_url: str | None = None):
        self.pushgateway_url = pushgateway_url
        self._store = SimpleMetrics()
        self._start_time = time.monotonic()

    # ── Step Metrics ───────────────────────────────────────────────────

    def inc_step(self, outcome: str = "success", task_type: str = "") -> None:
        """记录 Agent 步骤（Counter）。"""
        labels = {"outcome": outcome}
        if task_type:
            labels["task_type"] = task_type
        self._store.inc("agent_steps_total", labels=labels)

    def observe_step_duration(self, duration_seconds: float, outcome: str = "success") -> None:
        """记录步骤耗时（Histogram）。"""
        self._store.observe("agent_step_duration_seconds", duration_seconds, labels={"outcome": outcome})

    # ── Tool Metrics ───────────────────────────────────────────────────

    def inc_tool_call(self, tool_id: str, success: bool = True) -> None:
        """记录工具调用（Counter）。"""
        self._store.inc("tool_calls_total", labels={"tool_id": tool_id, "success": str(success)})

    def observe_tool_duration(self, tool_id: str, duration_seconds: float, success: bool) -> None:
        """记录工具调用耗时（Histogram）。"""
        self._store.observe(
            "tool_call_duration_seconds",
            duration_seconds,
            labels={"tool_id": tool_id, "success": str(success)},
        )

    # ── Token Metrics ──────────────────────────────────────────────────

    def inc_tokens(self, input_tokens: int, output_tokens: int, model: str = "") -> None:
        """记录 Token 使用量（Counter）。"""
        labels_input = {"type": "input", "model": model} if model else {"type": "input"}
        labels_output = {"type": "output", "model": model} if model else {"type": "output"}
        self._store.inc("token_usage_total", float(input_tokens), labels=labels_input)
        self._store.inc("token_usage_total", float(output_tokens), labels=labels_output)

    # ── Task Metrics ───────────────────────────────────────────────────

    def inc_task_completed(self, task_type: str = "") -> None:
        labels = {"task_type": task_type} if task_type else {}
        self._store.inc("task_completed_total", labels=labels)

    def inc_task_failed(self, task_type: str = "", reason: str = "") -> None:
        labels: dict[str, str] = {}
        if task_type:
            labels["task_type"] = task_type
        if reason:
            labels["reason"] = reason
        self._store.inc("task_failed_total", labels=labels)

    # ── Verification Metrics ───────────────────────────────────────────

    def inc_verification(self, passed: bool) -> None:
        if passed:
            self._store.inc("verification_passed_total")
        else:
            self._store.inc("verification_failed_total")

    # ── Checkpoint Metrics ──────────────────────────────────────────────

    def inc_checkpoint_save(self) -> None:
        self._store.inc("checkpoint_saves_total")

    # ── Gauge ──────────────────────────────────────────────────────────

    def set_active_tasks(self, count: int) -> None:
        self._store.set("active_tasks_gauge", float(count))

    def set_memory_usage(self, bytes_used: int) -> None:
        self._store.set("memory_usage_bytes", float(bytes_used))

    # ── Export ────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, Any]:
        """导出所有指标。"""
        uptime = time.monotonic() - self._start_time
        return {
            **self._store.get_all(),
            "_uptime_seconds": uptime,
            "_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_prometheus_format(self) -> str:
        """
        导出 Prometheus text format。

        可直接发送到 Pushgateway。
        """
        lines: list[str] = []
        all_metrics = self._store.get_all()

        # Counters
        for key, value in all_metrics["counters"].items():
            metric_name = key.split("{")[0]
            lines.append(f"# TYPE {metric_name} counter")
            lines.append(f"{key} {value}")

        # Gauges
        for key, value in all_metrics["gauges"].items():
            metric_name = key.split("{")[0]
            lines.append(f"# TYPE {metric_name} gauge")
            lines.append(f"{key} {value}")

        # Histograms
        for key in all_metrics["histograms"]:
            metric_name = key.split("{")[0]
            stats = all_metrics["histograms"][key]
            lines.append(f"# TYPE {metric_name} histogram")
            for label_str, label_dict in list(self._store._labels.items()):
                if label_str.startswith(metric_name):
                    for bound in self._store._histogram_buckets:
                        bucket_labels = {**label_dict, "le": str(bound)}
                        label_part = ",".join(f'{k}="{v}"' for k, v in bucket_labels.items())
                        lines.append(f"{metric_name}_bucket{{{label_part}}} {stats['count']}")
                    lines.append(f"{metric_name}_bucket{{le=\"+Inf\",{label_part}}} {stats['count']}")
                    lines.append(f"{metric_name}_sum{label_part[1:]} {stats['sum']:.2f}")
                    lines.append(f"{metric_name}_count{label_part[1:]} {stats['count']}")
                    break

        lines.append(f"# uptime_seconds {time.monotonic() - self._start_time:.2f}")
        return "\n".join(lines)
