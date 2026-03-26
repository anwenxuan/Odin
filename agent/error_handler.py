"""
agent/error_handler.py — Error Handler & Error Classifier

错误分类与恢复策略系统。

错误分为三类：
- RETRYABLE    : 可重试错误（rate_limit, timeout, network 等）
- RECOVERABLE  : 可恢复错误（tool_not_found, invalid_input 等）
- FATAL        : 致命错误（invalid_plan, auth_failed, sandbox_escape 等）

每类错误对应不同的恢复策略。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Error Types
# ─────────────────────────────────────────────────────────────────────────────


class ErrorType(str, Enum):
    """Agent 错误的分类类型。"""
    # RETRYABLE — 可重试
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    LLM_TEMPORARY = "llm_temp"
    TOOL_TEMPORARY = "tool_temp"

    # RECOVERABLE — 可恢复（换策略）
    TOOL_NOT_FOUND = "tool_not_found"
    INVALID_INPUT = "invalid_input"
    ASSERTION_FAILED = "assertion"
    EVIDENCE_MISSING = "evidence_missing"
    PARSE_ERROR = "parse_error"
    VERIFICATION_FAILED = "verification_failed"

    # FATAL — 致命（无法恢复）
    INVALID_PLAN = "invalid_plan"
    AUTH_FAILED = "auth_failed"
    SANDBOX_ESCAPE = "sandbox_escape"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    MAX_ITERATIONS = "max_iterations"

    # UNKNOWN
    UNKNOWN = "unknown"


class ErrorSeverity(str, Enum):
    """错误严重级别。"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Error Classification
# ─────────────────────────────────────────────────────────────────────────────


class ErrorClassifier:
    """
    错误分类器。

    根据错误特征将其分类为 RETRYABLE / RECOVERABLE / FATAL，
    并评估严重级别。
    """

    RETRYABLE: set[str] = {
        ErrorType.RATE_LIMIT.value,
        ErrorType.TIMEOUT.value,
        ErrorType.NETWORK.value,
        ErrorType.LLM_TEMPORARY.value,
        ErrorType.TOOL_TEMPORARY.value,
    }

    RECOVERABLE: set[str] = {
        ErrorType.TOOL_NOT_FOUND.value,
        ErrorType.INVALID_INPUT.value,
        ErrorType.ASSERTION_FAILED.value,
        ErrorType.EVIDENCE_MISSING.value,
        ErrorType.PARSE_ERROR.value,
        ErrorType.VERIFICATION_FAILED.value,
    }

    FATAL: set[str] = {
        ErrorType.INVALID_PLAN.value,
        ErrorType.AUTH_FAILED.value,
        ErrorType.SANDBOX_ESCAPE.value,
        ErrorType.PERMISSION_DENIED.value,
        ErrorType.RESOURCE_EXHAUSTED.value,
        ErrorType.MAX_ITERATIONS.value,
    }

    @classmethod
    def classify(cls, error: Exception | str) -> ErrorType:
        """根据错误内容分类。"""
        error_str = str(error).lower()

        if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
            return ErrorType.RATE_LIMIT
        if "timeout" in error_str or "timed out" in error_str or "504" in error_str:
            return ErrorType.TIMEOUT
        if "network" in error_str or "connection" in error_str or "dns" in error_str:
            return ErrorType.NETWORK
        if "tool" in error_str and ("not found" in error_str or "unknown" in error_str):
            return ErrorType.TOOL_NOT_FOUND
        if "invalid" in error_str and ("input" in error_str or "argument" in error_str or "param" in error_str):
            return ErrorType.INVALID_INPUT
        if "auth" in error_str or "api key" in error_str or "credential" in error_str:
            return ErrorType.AUTH_FAILED
        if "permission" in error_str or "denied" in error_str:
            return ErrorType.PERMISSION_DENIED
        if "parse" in error_str or "json" in error_str or "decode" in error_str:
            return ErrorType.PARSE_ERROR
        if "evidence" in error_str:
            return ErrorType.EVIDENCE_MISSING
        if "verification" in error_str or "assertion" in error_str:
            return ErrorType.VERIFICATION_FAILED
        if "iteration" in error_str or "max" in error_str:
            return ErrorType.MAX_ITERATIONS
        if "escape" in error_str or "sandbox" in error_str:
            return ErrorType.SANDBOX_ESCAPE

        return ErrorType.UNKNOWN

    @classmethod
    def category(cls, error_type: ErrorType) -> str:
        """返回错误类别：RETRYABLE / RECOVERABLE / FATAL / UNKNOWN。"""
        val = error_type.value
        if val in cls.RETRYABLE:
            return "RETRYABLE"
        if val in cls.RECOVERABLE:
            return "RECOVERABLE"
        if val in cls.FATAL:
            return "FATAL"
        return "UNKNOWN"

    @classmethod
    def severity(cls, error_type: ErrorType) -> ErrorSeverity:
        """评估错误严重级别。"""
        if error_type.value in {ErrorType.SANDBOX_ESCAPE.value, ErrorType.AUTH_FAILED.value}:
            return ErrorSeverity.CRITICAL
        if error_type.value in {ErrorType.RATE_LIMIT.value, ErrorType.MAX_ITERATIONS.value,
                                 ErrorType.RESOURCE_EXHAUSTED.value}:
            return ErrorSeverity.HIGH
        if error_type.value in {ErrorType.TIMEOUT.value, ErrorType.NETWORK.value,
                                 ErrorType.VERIFICATION_FAILED.value}:
            return ErrorSeverity.MEDIUM
        return ErrorSeverity.LOW


# ─────────────────────────────────────────────────────────────────────────────
# AgentError Record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentError:
    """
    Agent 执行中的错误记录。
    """
    id: str
    type: ErrorType
    category: str          # RETRYABLE / RECOVERABLE / FATAL
    message: str
    severity: ErrorSeverity
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    context: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    recovered: bool = False

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        context: dict[str, Any] | None = None,
    ) -> "AgentError":
        """从 Exception 构造 AgentError。"""
        error_type = ErrorClassifier.classify(exc)
        return cls(
            id=f"err-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{id(exc)}",
            type=error_type,
            category=ErrorClassifier.category(error_type),
            message=str(exc),
            severity=ErrorClassifier.severity(error_type),
            context=context or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "category": self.category,
            "message": self.message,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "context": self.context,
            "retry_count": self.retry_count,
            "recovered": self.recovered,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Recovery Strategies
# ─────────────────────────────────────────────────────────────────────────────


class RecoveryStrategy(str, Enum):
    """恢复策略枚举。"""
    RETRY = "retry"                       # 重试（指数退避）
    RETRY_WITH_BACKOFF = "retry_backoff" # 退避重试
    SKIP_TOOL = "skip_tool"              # 跳过失败的 tool
    REPLAN = "replan"                    # 重新规划
    DEGRADE = "degrade"                  # 降级处理（简化任务）
    ABORT = "abort"                      # 直接终止
    IGNORE = "ignore"                    # 忽略继续


class RecoveryPolicy:
    """
    针对特定 ErrorType 的恢复策略配置。
    """

    POLICIES: dict[str, RecoveryStrategy] = {
        ErrorType.RATE_LIMIT.value: RecoveryStrategy.RETRY_WITH_BACKOFF,
        ErrorType.TIMEOUT.value: RecoveryStrategy.RETRY,
        ErrorType.NETWORK.value: RecoveryStrategy.RETRY,
        ErrorType.LLM_TEMPORARY.value: RecoveryStrategy.RETRY,
        ErrorType.TOOL_TEMPORARY.value: RecoveryStrategy.RETRY,
        ErrorType.TOOL_NOT_FOUND.value: RecoveryStrategy.SKIP_TOOL,
        ErrorType.INVALID_INPUT.value: RecoveryStrategy.REPLAN,
        ErrorType.ASSERTION_FAILED.value: RecoveryStrategy.REPLAN,
        ErrorType.EVIDENCE_MISSING.value: RecoveryStrategy.REPLAN,
        ErrorType.PARSE_ERROR.value: RecoveryStrategy.RETRY,
        ErrorType.VERIFICATION_FAILED.value: RecoveryStrategy.RETRY,
        ErrorType.INVALID_PLAN.value: RecoveryStrategy.REPLAN,
        ErrorType.AUTH_FAILED.value: RecoveryStrategy.ABORT,
        ErrorType.SANDBOX_ESCAPE.value: RecoveryStrategy.ABORT,
        ErrorType.PERMISSION_DENIED.value: RecoveryStrategy.ABORT,
        ErrorType.RESOURCE_EXHAUSTED.value: RecoveryStrategy.DEGRADE,
        ErrorType.MAX_ITERATIONS.value: RecoveryStrategy.ABORT,
        ErrorType.UNKNOWN.value: RecoveryStrategy.RETRY,
    }

    @classmethod
    def get(cls, error_type: ErrorType) -> RecoveryStrategy:
        return cls.POLICIES.get(error_type.value, RecoveryStrategy.IGNORE)


# ─────────────────────────────────────────────────────────────────────────────
# Error Handler
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ErrorHandlingResult:
    """ErrorHandler 的处理结果。"""
    action: RecoveryStrategy
    should_continue: bool
    should_retry: bool
    retry_delay: float = 0.0
    message: str = ""
    fallback_value: Any = None


class ErrorHandler:
    """
    Agent 错误处理器。

    职责：
    1. 接收错误，记录并分类
    2. 根据 RecoveryPolicy 确定恢复策略
    3. 判断是否继续执行
    4. 触发相应的恢复动作

    使用方式：
        handler = ErrorHandler(allow_fallback=True)
        result = handler.handle(error, current_context)
        if result.should_continue:
            # 继续执行
        else:
            # 终止或降级
    """

    def __init__(
        self,
        allow_fallback: bool = True,
        max_retry_per_error: int = 3,
    ):
        self.allow_fallback = allow_fallback
        self.max_retry_per_error = max_retry_per_error
        self.error_log: list[AgentError] = []
        self._retry_counts: dict[str, int] = {}

    def handle(
        self,
        error: Exception | str,
        context: dict[str, Any] | None = None,
    ) -> ErrorHandlingResult:
        """
        处理错误，返回恢复决策。

        Args:
            error  : 发生的错误（Exception 或 str）
            context: 当前执行上下文

        Returns:
            ErrorHandlingResult，包含恢复决策
        """
        agent_error = AgentError.from_exception(
            error if isinstance(error, Exception) else Exception(error),
            context=context,
        )

        # 检查重试次数
        error_key = agent_error.type.value
        current_retry = self._retry_counts.get(error_key, 0)
        agent_error.retry_count = current_retry

        # 分类并确定策略
        policy = RecoveryPolicy.get(agent_error.type)

        # 如果超过最大重试次数，降级
        if current_retry >= self.max_retry_per_error:
            policy = RecoveryStrategy.DEGRADE if self.allow_fallback else RecoveryStrategy.ABORT

        # 根据策略生成处理结果
        result = self._apply_policy(agent_error, policy, context)

        # 记录错误
        agent_error.recovered = result.action in {RecoveryStrategy.IGNORE, RecoveryStrategy.RETRY}
        self.error_log.append(agent_error)

        # 增加重试计数
        if result.should_retry:
            self._retry_counts[error_key] = current_retry + 1

        logger.warning(
            "[ErrorHandler] type=%s category=%s policy=%s continue=%s retry=%s",
            agent_error.type.value,
            agent_error.category,
            policy.value,
            result.should_continue,
            result.should_retry,
        )

        return result

    def _apply_policy(
        self,
        error: AgentError,
        policy: RecoveryStrategy,
        context: dict[str, Any] | None,
    ) -> ErrorHandlingResult:
        """根据策略生成处理结果。"""
        ctx = context or {}

        if policy == RecoveryStrategy.RETRY:
            return ErrorHandlingResult(
                action=policy,
                should_continue=True,
                should_retry=True,
                retry_delay=1.0,
                message=f"Retrying after {error.type.value} error",
            )

        if policy == RecoveryStrategy.RETRY_WITH_BACKOFF:
            delay = min(2 ** error.retry_count, 120.0)
            return ErrorHandlingResult(
                action=policy,
                should_continue=True,
                should_retry=True,
                retry_delay=delay,
                message=f"Retrying with backoff after {error.type.value}, delay={delay}s",
            )

        if policy == RecoveryStrategy.SKIP_TOOL:
            tool_name = ctx.get("tool_name", "unknown")
            return ErrorHandlingResult(
                action=policy,
                should_continue=True,
                should_retry=False,
                message=f"Skipping tool '{tool_name}' due to {error.type.value}",
            )

        if policy == RecoveryStrategy.REPLAN:
            return ErrorHandlingResult(
                action=policy,
                should_continue=True,
                should_retry=False,
                message=f"Replanning due to {error.type.value}: {error.message[:100]}",
            )

        if policy == RecoveryStrategy.DEGRADE:
            return ErrorHandlingResult(
                action=policy,
                should_continue=True,
                should_retry=False,
                message=f"Degrading task due to {error.type.value}: simplifying approach",
            )

        if policy == RecoveryStrategy.ABORT:
            return ErrorHandlingResult(
                action=policy,
                should_continue=False,
                should_retry=False,
                message=f"Fatal error {error.type.value}: {error.message[:100]}",
            )

        # IGNORE / default
        return ErrorHandlingResult(
            action=RecoveryStrategy.IGNORE,
            should_continue=True,
            should_retry=False,
            message=f"Ignoring error: {error.message[:100]}",
        )

    def reset_retry_count(self, error_type: str) -> None:
        """重置某个错误类型的重试计数。"""
        self._retry_counts.pop(error_type, None)

    def reset_all(self) -> None:
        """重置所有错误状态。"""
        self._retry_counts.clear()

    def get_error_summary(self) -> dict[str, Any]:
        """获取错误摘要。"""
        return {
            "total_errors": len(self.error_log),
            "by_category": {
                cat: sum(1 for e in self.error_log if e.category == cat)
                for cat in ["RETRYABLE", "RECOVERABLE", "FATAL"]
            },
            "by_type": {
                et.value: sum(1 for e in self.error_log if e.type == et)
                for et in ErrorType
            },
            "retry_counts": dict(self._retry_counts),
        }
