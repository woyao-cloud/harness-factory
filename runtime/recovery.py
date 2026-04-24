"""Error recovery — retry policies, circuit breakers, and pipeline recovery."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import auto, Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ── Error classification ─────────────────────────────────────────────────────


class ErrorCategory(Enum):
    RETRYABLE = auto()       # Transient: timeout, rate limit, network
    FATAL = auto()           # Permanent: auth failure, invalid schema
    DEGRADE = auto()         # Can proceed with degraded functionality
    UNKNOWN = auto()         # Treat as retryable with caution


class ErrorClassifier:
    """Categorize exceptions by type for recovery decisions.

    Extend via ``register`` for application-specific error types.
    """

    def __init__(self) -> None:
        self._rules: dict[type, ErrorCategory] = {
            # Network / I/O — retryable
            ConnectionError: ErrorCategory.RETRYABLE,
            TimeoutError: ErrorCategory.RETRYABLE,
            asyncio.TimeoutError: ErrorCategory.RETRYABLE,
            BrokenPipeError: ErrorCategory.RETRYABLE,
            ConnectionResetError: ErrorCategory.RETRYABLE,
            # Rate limiting — retryable with backoff
        }
        self._custom: list[tuple[Callable[[Exception], ErrorCategory | None], str]] = []

    def register(self, exc_type: type, category: ErrorCategory) -> None:
        self._rules[exc_type] = category

    def register_predicate(
        self, predicate: Callable[[Exception], ErrorCategory | None], name: str = ""
    ) -> None:
        self._custom.append((predicate, name))

    def classify(self, exc: Exception) -> tuple[ErrorCategory, str]:
        # Custom predicates first
        for predicate, name in self._custom:
            try:
                result = predicate(exc)
                if result is not None:
                    return result, name
            except Exception:
                continue

        # Type-based rules
        for exc_type, category in self._rules.items():
            if isinstance(exc, exc_type):
                return category, exc_type.__name__

        # Heuristics
        msg = str(exc).lower()
        if any(kw in msg for kw in ("rate limit", "too many", "429", "503")):
            return ErrorCategory.RETRYABLE, "rate_limit"
        if any(kw in msg for kw in ("timeout", "timed out", "deadline")):
            return ErrorCategory.RETRYABLE, "timeout"
        if any(kw in msg for kw in ("unauthorized", "403", "401", "invalid_api_key")):
            return ErrorCategory.FATAL, "auth_error"
        if any(kw in msg for kw in ("not found", "404")):
            return ErrorCategory.DEGRADE, "not_found"

        return ErrorCategory.UNKNOWN, "unclassified"


# ── Retry policy ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behaviour with exponential backoff + jitter."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: float = 0.1  # ±10% jitter
    retryable_categories: tuple[ErrorCategory, ...] = (
        ErrorCategory.RETRYABLE,
        ErrorCategory.UNKNOWN,
    )

    def delay(self, attempt: int) -> float:
        """Compute delay for the *attempt*-th retry (0-indexed)."""
        exp_delay = self.base_delay * (self.multiplier ** attempt)
        capped = min(exp_delay, self.max_delay)
        jitter_amount = capped * self.jitter * (2 * random.random() - 1)
        return max(0, capped + jitter_amount)


async def retry(
    fn: Callable[..., Awaitable[Any]],
    policy: RetryPolicy,
    classifier: ErrorClassifier | None = None,
    **kwargs: Any,
) -> Any:
    """Execute ``fn(**kwargs)`` with retry logic.

    Raises the last exception if all retries are exhausted.
    """
    classifier = classifier or ErrorClassifier()
    last_exc: Exception | None = None

    for attempt in range(policy.max_retries + 1):
        try:
            return await fn(**kwargs)
        except Exception as exc:
            last_exc = exc
            category, reason = classifier.classify(exc)

            if attempt >= policy.max_retries:
                logger.warning(
                    "Retry exhausted after %d/%d: %s [%s]",
                    attempt, policy.max_retries, reason, exc,
                )
                raise

            if category not in policy.retryable_categories:
                logger.warning(
                    "Non-retryable error on attempt %d: %s [%s]",
                    attempt, reason, exc,
                )
                raise

            delay = policy.delay(attempt)
            logger.info(
                "Retrying (%d/%d) after %.1fs — %s: %s",
                attempt + 1, policy.max_retries, delay, reason, exc,
            )
            await asyncio.sleep(delay)

    # Should not reach here, but belt and suspenders
    if last_exc is not None:
        raise last_exc


# ── Circuit breaker ──────────────────────────────────────────────────────────


class CircuitState(Enum):
    CLOSED = auto()    # Normal operation
    OPEN = auto()      # Failing — reject requests immediately
    HALF_OPEN = auto() # Testing if service recovered


@dataclass(frozen=True)
class CircuitStats:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    open_until: float = 0.0


class CircuitBreaker:
    """Three-state circuit breaker (Closed → Open → Half-Open → Closed).

    Usage::

        breaker = CircuitBreaker("anthropic_api", threshold=5, reset_timeout=30)

        async with breaker.protect():
            return await call_llm(prompt)

        if breaker.state is CircuitState.OPEN:
            fallback_cache()
    """

    def __init__(
        self,
        name: str,
        threshold: int = 5,
        reset_timeout: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self._name = name
        self._threshold = threshold
        self._reset_timeout = reset_timeout
        self._half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._open_until = 0.0
        self._half_open_trials = 0

    # ── State ────────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and time.time() >= self._open_until:
            self._state = CircuitState.HALF_OPEN
            self._half_open_trials = 0
            logger.info("Circuit %s → HALF_OPEN", self._name)
        return self._state

    @property
    def stats(self) -> CircuitStats:
        return CircuitStats(
            state=self.state,
            failure_count=self._failure_count,
            success_count=self._success_count,
            last_failure_time=self._last_failure_time,
            open_until=self._open_until,
        )

    # ── Operations ───────────────────────────────────────────────────────

    def on_success(self) -> None:
        if self._state is CircuitState.HALF_OPEN:
            self._half_open_trials += 1
            if self._half_open_trials >= self._half_open_max:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_trials = 0
                logger.info("Circuit %s → CLOSED (recovered)", self._name)

        self._success_count += 1
        if self._state is CircuitState.CLOSED:
            self._failure_count = max(0, self._failure_count - 1)  # gradual recovery

    def on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._open_until = time.time() + self._reset_timeout
            self._half_open_trials = 0
            logger.warning("Circuit %s → OPEN (half-open trial failed)", self._name)
        elif self._failure_count >= self._threshold and self._state is CircuitState.CLOSED:
            self._state = CircuitState.OPEN
            self._open_until = time.time() + self._reset_timeout
            logger.warning(
                "Circuit %s → OPEN (%d failures)", self._name, self._failure_count
            )

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_trials = 0

    # ── Context manager ──────────────────────────────────────────────────

    class _Protect:
        def __init__(self, breaker: CircuitBreaker, fallback: Callable | None):
            self._breaker = breaker
            self._fallback = fallback

        async def __aenter__(self):
            state = self._breaker.state
            if state is CircuitState.OPEN:
                if self._fallback:
                    return self._fallback()
                raise CircuitBreakerOpenError(self._breaker._name, state)
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                self._breaker.on_success()
            elif exc_type is CircuitBreakerOpenError:
                pass  # Wasn't really executed
            else:
                self._breaker.on_failure()
                raise  # Re-raise original error

    def protect(self, fallback: Callable | None = None) -> _Protect:
        """Context manager: ``async with breaker.protect(): ...``"""
        return self._Protect(self, fallback)


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str, state: CircuitState) -> None:
        super().__init__(f"Circuit '{name}' is {state.name}")
        self.breaker_name = name
        self.breaker_state = state


# ── Pipeline recovery wrapper ────────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryConfig:
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    enable_circuit_breakers: bool = True
    max_consecutive_failures: int = 5
    fallback_text: str = "I encountered an error. Please try again."


class PipelineRecovery:
    """Wraps pipeline operations with error recovery logic.

    Integrates ``ErrorClassifier``, ``RetryPolicy``, and ``CircuitBreaker``
    into a single recovery layer.

    Usage::

        recovery = PipelineRecovery()

        # Wrap an LLM call with retry + circuit breaker
        result = await recovery.call_with_recovery(
            "llm_complete",
            llm.complete,
            messages=msgs, tools=tools, config=cfg,
        )
    """

    def __init__(
        self,
        config: RecoveryConfig | None = None,
        classifier: ErrorClassifier | None = None,
    ) -> None:
        self._cfg = config or RecoveryConfig()
        self._classifier = classifier or ErrorClassifier()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._consecutive_failures = 0

    # ── Circuit breaker management ──────────────────────────────────────

    def get_breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name)
        return self._breakers[name]

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    # ── Core recovery call ──────────────────────────────────────────────

    async def call_with_recovery(
        self,
        component: str,
        fn: Callable[..., Awaitable[Any]],
        fallback_fn: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Execute a call with circuit breaker + retry + fallback.

        Args:
            component: Name for circuit breaker tracking.
            fn: Async callable to execute.
            fallback_fn: Optional fallback for when circuit is open.
            **kwargs: Passed to ``fn``.
        """
        breaker = self.get_breaker(component) if self._cfg.enable_circuit_breakers else None

        # Check circuit breaker first
        if breaker and breaker.state is CircuitState.OPEN:
            logger.warning("Circuit %s is OPEN — skipping call", component)
            if fallback_fn:
                return fallback_fn()
            raise CircuitBreakerOpenError(component, breaker.state)

        try:
            result = await retry(fn, self._cfg.retry_policy, self._classifier, **kwargs)
            if breaker:
                breaker.on_success()
            self._consecutive_failures = max(0, self._consecutive_failures - 1)
            return result

        except CircuitBreakerOpenError:
            raise

        except Exception as exc:
            if breaker:
                breaker.on_failure()
            self._consecutive_failures += 1

            category, reason = self._classifier.classify(exc)
            logger.error(
                "Recovery failed for %s: %s [%s] (consecutive: %d)",
                component, reason, exc, self._consecutive_failures,
            )

            # Degrade gracefully for DEGRADE errors
            if category is ErrorCategory.DEGRADE and fallback_fn:
                return fallback_fn()

            if self._consecutive_failures >= self._cfg.max_consecutive_failures:
                raise RuntimeError(
                    f"Too many consecutive failures ({self._consecutive_failures})"
                ) from exc

            raise

    # ── Error classifier passthrough ─────────────────────────────────────

    def classify(self, exc: Exception) -> tuple[ErrorCategory, str]:
        return self._classifier.classify(exc)
