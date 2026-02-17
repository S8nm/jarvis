"""
Jarvis Protocol — Circuit Breaker

Three-state circuit breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.
Wraps any async service call to prevent cascading failures.

Usage:
    cb = CircuitBreaker("ollama", failure_threshold=3, cooldown_sec=30)
    result = await cb.call(some_async_func, arg1, arg2)
"""
import asyncio
import logging
import time
from enum import Enum

logger = logging.getLogger("jarvis.resilience.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"       # Normal — requests pass through
    OPEN = "OPEN"           # Tripped — fast-fail all requests
    HALF_OPEN = "HALF_OPEN" # Probing — one request allowed to test recovery


class CircuitBreaker:
    """
    Per-service circuit breaker with configurable thresholds.

    - CLOSED: all calls pass. Failures increment counter.
    - OPEN: all calls fast-fail. After cooldown_sec, transitions to HALF_OPEN.
    - HALF_OPEN: one probe call allowed. Success -> CLOSED, failure -> OPEN.
    """

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_sec: float = 60.0):
        self.name = name
        self._failure_threshold = failure_threshold
        self._cooldown_sec = cooldown_sec

        self.state = CircuitState.CLOSED
        self._failures = 0
        self._successes_in_half_open = 0
        self._last_failure_time: float = 0.0
        self._last_state_change: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def call(self, func, *args, **kwargs):
        """
        Wrap an async function with circuit breaker logic.

        Raises CircuitOpenError if the circuit is OPEN and cooldown hasn't elapsed.
        """
        async with self._lock:
            allowed = self._check_allowed()

        if not allowed:
            raise CircuitOpenError(
                f"Circuit [{self.name}] is OPEN. "
                f"Retry after {self._time_until_probe():.0f}s."
            )

        try:
            result = await func(*args, **kwargs)
            await self._record_success()
            return result
        except Exception as exc:
            await self._record_failure()
            raise

    def _check_allowed(self) -> bool:
        """Check if a request should pass. Called under lock."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._cooldown_sec:
                self._transition(CircuitState.HALF_OPEN)
                self._successes_in_half_open = 0
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow one probe call at a time (lock ensures serialization)
            return True

        return False

    async def _record_success(self):
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._successes_in_half_open += 1
                # One success in half-open is enough to close
                self._transition(CircuitState.CLOSED)
                self._failures = 0
                self._successes_in_half_open = 0
            elif self.state == CircuitState.CLOSED:
                # Decay failure count on success
                self._failures = max(0, self._failures - 1)

    async def _record_failure(self):
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()

            if self.state == CircuitState.HALF_OPEN:
                # Probe failed — re-open
                self._transition(CircuitState.OPEN)
                self._successes_in_half_open = 0
            elif self.state == CircuitState.CLOSED:
                if self._failures >= self._failure_threshold:
                    self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState):
        old = self.state
        self.state = new_state
        self._last_state_change = time.monotonic()
        logger.info(f"Circuit [{self.name}]: {old.value} -> {new_state.value}")

    def _time_until_probe(self) -> float:
        """Seconds remaining before OPEN -> HALF_OPEN transition."""
        if self.state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self._cooldown_sec - elapsed)

    def get_status(self) -> dict:
        """Return status for health endpoint / frontend ServiceDot."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self._failures,
            "time_until_probe": round(self._time_until_probe(), 1),
        }

    def reset(self):
        """Manual reset — force back to CLOSED."""
        self._failures = 0
        self._successes_in_half_open = 0
        self._transition(CircuitState.CLOSED)


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an OPEN circuit."""
    pass
