"""
agent/config.py — Shared Agent Configuration

Extracted to a separate module to avoid circular imports between
agent.runtime and agent.planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# AgentConfig — shared config types
# ─────────────────────────────────────────────────────────────────────────────


class OutputFormat(str):
    JSON = "json"
    MARKDOWN = "markdown"


class SandboxMode(str):
    PROCESS = "process"
    DOCKER = "docker"
    VM = "vm"


class RuntimeStatus(str):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    WAITING_VERIFICATION = "waiting_verification"
    VERIFIED = "verified"
    RETRYING = "retrying"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Re-export RuntimeConfig for convenience
from agent.runtime import RuntimeConfig

__all__ = [
    "RuntimeConfig",
    "OutputFormat",
    "SandboxMode",
    "RuntimeStatus",
]
