"""
agent/retry.py — Retry Manager

重试策略引擎，支持：
- Exponential Backoff（指数退避）
- Linear Backoff（线性退避）
- Fixed Retry（固定重试）
- Circuit Breaker（熔断器）

与 ErrorHandler 配合使用，根据错误类型选择合适的重试策略。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────────────────
# Backoff Strategies
# ─────────────────────────────────────────────────────────────────────────────


class BackoffStrategy(ABC):
    """重试间隔策略抽象基类。"""

    @abstractmethod
    def delay(self, attempt: int) -> float:
        """计算第 attempt 次重试的延迟（秒）。"""
        ...

    @abstractmethod
    def reset(self) -> None:
        """重置策略状态。"""
        ...


class NoBackoff(BackoffStrategy):
    """无退避：立即重试。"""

    def delay(self, attempt: int) -> float:
        return 0.0

    def reset(self) -> None:
        pass


class FixedBackoff(BackoffStrategy):
    """固定间隔重试。"""

    def __init__(self, delay: float = 1.0):
        self._delay = delay

    def delay(self, attempt: int) -> float:
        return self._delay

    def reset(self) -> None:
        pass


class LinearBackoff(BackoffStrategy):
    """线性递增重试间隔。"""

    def __init__(self, base_delay: float = 1.0, max_delay: float = 60.0):
        self._base = base_delay
        self._max = max_delay

    def delay(self, attempt: int) -> float:
        return min(self._base * attempt, self._max)

    def reset(self) -> None:
        pass


class ExponentialBackoff(BackoffStrategy):
    """
    指数退避重试。

    策略：
    - 第 1 次：base * 1 = base
    - 第 2 次：base * 2 = 2 * base
    - 第 3 次：base * 4 = 4 * base
    - ... 最大 max_delay
    - 添加随机抖动 ±jitter 防止惊群效应
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 120.0,
        multiplier: float = 2.0,
        jitter: float = 0.5,
    ):
        self._base = base_delay
        self._max = max_delay
        self._multiplier = multiplier
        self._jitter = jitter

    def delay(self, attempt: int) -> float:
        exp_delay = min(self._base * (self._multiplier ** (attempt - 1)), self._max)
        jitter_range = exp_delay * self._jitter
        jittered = exp_delay + random.uniform(-jitter_range, jitter_range)
        return max(0.0, jittered)

    def reset(self) -> None:
        pass


class FibonacciBackoff(BackoffStrategy):
    """斐波那契退避（更平滑的增长曲线）。"""

    def __init__(self, base_delay: float = 1.0, max_delay: float = 120.0):
        self._base = base_delay
        self._max = max_delay
        self._fib_cache: list[float] = [0, 1]

    def _fib(self, n: int) -> float:
        while len(self._fib_cache) <= n:
            self._fib_cache.append(self._fib_cache[-1] + self._fib_cache[-2])
        return self._fib_cache[n]

    def delay(self, attempt: int) -> float:
        return min(self._base * self._fib(attempt + 1), self._max)

    def reset(self) -> None:
        self._fib_cache = [0, 1]


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker
# ─────────────────────────────────────────────────────────────────────────────


class CircuitState(str, Enum):
    CLOSED = "closed"     # 正常，允许请求通过
    OPEN = "open"         # 熔断，拒绝所有请求
    HALF_OPEN = "half_open"  # 半开，放量测试


@dataclass
class CircuitBreaker:
    """
    熔断器。

    防止持续对故障服务发起请求。
    - CLOSED    : 正常状态，统计失败率
    - OPEN      : 超过阈值后打开，所有请求直接失败
    - HALF_OPEN : 一段时间后半开，尝试放行少量请求测试
    """

    name: str = "default"
    failure_threshold: int = 5     # 连续失败 N 次后熔断
    success_threshold: int = 2     # 半开后连续成功 N 次后恢复
    timeout_seconds: float = 60.0   # OPEN 持续时间

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        """检查当前状态（可能因超时转为 HALF_OPEN）。"""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info("[CircuitBreaker %s] State → HALF_OPEN (timeout)", self.name)
        return self._state

    def record_success(self) -> None:
        """记录一次成功。"""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                logger.info("[CircuitBreaker %s] State → CLOSED (recovered)", self.name)
        elif self._state == CircuitState.CLOSED:
            self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self) -> None:
        """记录一次失败。"""
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning("[CircuitBreaker %s] State → OPEN (half-open failed)", self.name)
        elif self._state == CircuitState.CLOSED:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "[CircuitBreaker %s] State → OPEN (failure_count=%d)",
                    self.name,
                    self._failure_count,
                )

    def allow_request(self) -> bool:
        """判断是否允许请求通过。"""
        return self.state != CircuitState.OPEN

    def __str__(self) -> str:
        return f"CircuitBreaker({self.name}, state={self.state.value}, failures={self._failure_count})"


# ─────────────────────────────────────────────────────────────────────────────
# Retry Manager
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RetryStats:
    """重试统计信息。"""
    total_attempts: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_retries: int = 0
    by_strategy: dict[str, int] = field(default_factory=dict)

    def record_attempt(self, success: bool, used_retry: bool, strategy: str = "default") -> None:
        self.total_attempts += 1
        if success:
            self.total_successes += 1
        else:
            self.total_failures += 1
        if used_retry:
            self.total_retries += 1
        self.by_strategy[strategy] = self.by_strategy.get(strategy, 0) + 1


class RetryManager:
    """
    重试管理器。

    提供便捷的异步/同步重试接口，支持：
    - 不同错误类型使用不同退避策略
    - 熔断器保护
    - 统计与监控

    使用方式（异步）：
        result = await retry_manager.run(
            coro_function,
            error_types=(RateLimitError, TimeoutError),
            strategy=ExponentialBackoff(max_delay=60),
        )

    使用方式（同步）：
        result = retry_manager.run_sync(
            lambda: api.call(),
            error_types=(TimeoutError,),
            max_attempts=5,
        )
    """

    def __init__(
        self,
        max_attempts: int = 3,
        default_strategy: BackoffStrategy | None = None,
    ):
        self.max_attempts = max_attempts
        self.default_strategy = default_strategy or ExponentialBackoff()
        self.strategies: dict[str, BackoffStrategy] = {
            "rate_limit": ExponentialBackoff(base_delay=2.0, max_delay=120.0),
            "timeout": LinearBackoff(base_delay=1.0, max_delay=30.0),
            "network": ExponentialBackoff(base_delay=1.0, max_delay=60.0),
            "default": self.default_strategy,
        }
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
        self.stats = RetryStats()

    def set_strategy(self, name: str, strategy: BackoffStrategy) -> None:
        """注册命名策略。"""
        self.strategies[name] = strategy

    def get_circuit_breaker(self, name: str) -> CircuitBreaker:
        """获取或创建命名熔断器。"""
        if name not in self.circuit_breakers:
            self.circuit_breakers[name] = CircuitBreaker(name=name)
        return self.circuit_breakers[name]

    def _get_strategy(self, error_type: str | None) -> BackoffStrategy:
        """根据错误类型获取策略。"""
        if error_type and error_type in self.strategies:
            return self.strategies[error_type]
        return self.default_strategy

    async def run(
        self,
        coro: Callable[[], Awaitable[T]],
        error_types: tuple[type[Exception], ...] | None = None,
        strategy: BackoffStrategy | None = None,
        max_attempts: int | None = None,
        on_retry: Callable[[Exception, int], None] | None = None,
    ) -> T:
        """
        异步执行带重试的协程。

        Args:
            coro       : 异步协程
            error_types: 需要重试的异常类型（None = 所有异常）
            strategy   : 退避策略（None = 使用 default_strategy）
            max_attempts: 最大尝试次数
            on_retry   : 每次重试前的回调（可用于记录日志）

        Returns:
            协程的返回值

        Raises:
            最后一次尝试的异常
        """
        backoff = strategy or self.default_strategy
        attempts = max_attempts or self.max_attempts
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = await coro()
                self.stats.record_attempt(success=True, used_retry=attempt > 1)
                backoff.reset()
                return result
            except Exception as exc:
                last_error = exc
                if error_types and not isinstance(exc, error_types):
                    raise

                if attempt >= attempts:
                    self.stats.record_attempt(success=False, used_retry=attempt > 1)
                    raise

                delay = backoff.delay(attempt)
                if delay > 0:
                    await asyncio.sleep(delay)

                if on_retry:
                    on_retry(exc, attempt)

                logger.warning(
                    "Retry attempt %d/%d after %.1fs: %s",
                    attempt,
                    attempts,
                    delay,
                    str(exc)[:100],
                )

        if last_error:
            raise last_error
        raise RuntimeError("Retry loop exited unexpectedly")

    def run_sync(
        self,
        func: Callable[[], T],
        error_types: tuple[type[Exception], ...] | None = None,
        strategy: BackoffStrategy | None = None,
        max_attempts: int | None = None,
        on_retry: Callable[[Exception, int], None] | None = None,
    ) -> T:
        """同步版本的重试。"""
        backoff = strategy or self.default_strategy
        attempts = max_attempts or self.max_attempts
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = func()
                self.stats.record_attempt(success=True, used_retry=attempt > 1)
                backoff.reset()
                return result
            except Exception as exc:
                last_error = exc
                if error_types and not isinstance(exc, error_types):
                    raise

                if attempt >= attempts:
                    self.stats.record_attempt(success=False, used_retry=attempt > 1)
                    raise

                delay = backoff.delay(attempt)
                if delay > 0:
                    time.sleep(delay)

                if on_retry:
                    on_retry(exc, attempt)

        if last_error:
            raise last_error
        raise RuntimeError("Retry loop exited unexpectedly")
