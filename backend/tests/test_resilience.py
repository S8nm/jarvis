"""
Unit tests for resilience primitives — circuit breaker, rate limiter, tool timeout.
Tests use mocks (no real services needed).
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from resilience.circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError
from resilience.rate_limiter import SlidingWindowRateLimiter
from resilience.tool_timeout import with_timeout, get_tool_timeout


# ──────────────────────────── Circuit Breaker Tests ──────────────────────────

class TestCircuitBreakerStates:
    """Test circuit breaker state transitions."""

    @pytest.fixture
    def cb(self):
        return CircuitBreaker("test_service", failure_threshold=3, cooldown_sec=1.0)

    @pytest.mark.asyncio
    async def test_starts_closed(self, cb):
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_stays_closed(self, cb):
        async def ok():
            return "ok"

        result = await cb.call(ok)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb._failures == 0

    @pytest.mark.asyncio
    async def test_single_failure_stays_closed(self, cb):
        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.CLOSED
        assert cb._failures == 1

    @pytest.mark.asyncio
    async def test_trips_to_open_after_threshold(self, cb):
        async def fail():
            raise ConnectionError("down")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN
        assert cb._failures == 3

    @pytest.mark.asyncio
    async def test_open_circuit_fast_fails(self, cb):
        """OPEN circuit raises CircuitOpenError without calling the function."""
        call_count = 0

        async def fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("down")

        # Trip the circuit
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN
        assert call_count == 3

        # Next call should fast-fail
        with pytest.raises(CircuitOpenError):
            await cb.call(fail)

        assert call_count == 3  # Function was NOT called

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_cooldown(self, cb):
        """After cooldown, OPEN transitions to HALF_OPEN on next attempt."""
        async def fail():
            raise ConnectionError("down")

        # Trip the circuit
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Fast-forward past cooldown by patching the failure time
        cb._last_failure_time = time.monotonic() - 2.0  # 2s ago, cooldown is 1s

        # Next call should go through (circuit transitions to HALF_OPEN)
        async def still_fail():
            raise ConnectionError("still down")

        with pytest.raises(ConnectionError):
            await cb.call(still_fail)

        # Probe failed, should be back to OPEN
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self, cb):
        """Successful probe in HALF_OPEN closes the circuit."""
        async def fail():
            raise ConnectionError("down")

        # Trip the circuit
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(fail)

        # Fast-forward past cooldown
        cb._last_failure_time = time.monotonic() - 2.0

        async def recover():
            return "recovered"

        result = await cb.call(recover)
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED
        assert cb._failures == 0

    @pytest.mark.asyncio
    async def test_failure_decay_on_success(self, cb):
        """Failures decrement on successful calls."""
        async def fail():
            raise ValueError("err")

        async def ok():
            return "ok"

        # Two failures (below threshold)
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(fail)
        assert cb._failures == 2

        # One success decays failure count
        await cb.call(ok)
        assert cb._failures == 1
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reset(self, cb):
        """Manual reset forces circuit to CLOSED."""
        async def fail():
            raise ConnectionError("down")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failures == 0

    def test_get_status(self, cb):
        status = cb.get_status()
        assert status["name"] == "test_service"
        assert status["state"] == "CLOSED"
        assert status["failures"] == 0
        assert "time_until_probe" in status


# ──────────────────────────── Rate Limiter Tests ──────────────────────────

class TestRateLimiter:
    @pytest.fixture
    def limiter(self):
        rl = SlidingWindowRateLimiter()
        rl.configure("test", max_requests=3, window_sec=1.0)
        return rl

    def test_allows_within_limit(self, limiter):
        for _ in range(3):
            allowed, info = limiter.check("test")
            assert allowed is True
        assert info["remaining"] == 0

    def test_blocks_over_limit(self, limiter):
        for _ in range(3):
            limiter.check("test")

        allowed, info = limiter.check("test")
        assert allowed is False
        assert info["remaining"] == 0
        assert "retry_after" in info
        assert info["retry_after"] > 0

    def test_allows_after_window_expires(self, limiter):
        """Requests are allowed after the window slides past."""
        # Fill the window
        for _ in range(3):
            limiter.check("test")

        # Manually expire the window entries
        window = limiter._windows["test"]
        expired_time = time.monotonic() - 2.0
        for i in range(len(window)):
            window[i] = expired_time

        allowed, info = limiter.check("test")
        assert allowed is True

    def test_independent_sources(self, limiter):
        """Different sources have independent windows."""
        limiter.configure("other", max_requests=2, window_sec=1.0)

        # Fill "test"
        for _ in range(3):
            limiter.check("test")

        # "other" should still be allowed
        allowed, _ = limiter.check("other")
        assert allowed is True

    def test_default_limit_for_unknown_source(self, limiter):
        """Unknown source uses default limit of 15."""
        for _ in range(15):
            allowed, _ = limiter.check("unknown_source")
            assert allowed is True

        allowed, _ = limiter.check("unknown_source")
        assert allowed is False

    def test_get_status(self, limiter):
        limiter.check("test")
        limiter.check("test")
        status = limiter.get_status()
        assert "test" in status
        assert status["test"]["active"] == 2
        assert status["test"]["limit"] == 3

    def test_reset_source(self, limiter):
        for _ in range(3):
            limiter.check("test")

        limiter.reset("test")
        allowed, _ = limiter.check("test")
        assert allowed is True

    def test_reset_all(self, limiter):
        limiter.check("test")
        limiter.configure("other", max_requests=5, window_sec=1.0)
        limiter.check("other")

        limiter.reset()
        assert limiter._windows == {}


# ──────────────────────────── Tool Timeout Tests ──────────────────────────

class TestToolTimeout:
    @pytest.mark.asyncio
    async def test_returns_result_within_timeout(self):
        async def fast():
            return {"status": "ok"}

        result = await with_timeout(fast(), timeout_sec=5.0, tool_name="test_tool")
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_timeout_returns_error_dict(self):
        async def slow():
            await asyncio.sleep(10)
            return {"status": "ok"}

        result = await with_timeout(slow(), timeout_sec=0.1, tool_name="slow_tool")
        assert isinstance(result, dict)
        assert "error" in result
        assert result["error_type"] == "timeout"
        assert result["timeout_seconds"] == 0.1
        assert "slow_tool" in result["error"]

    @pytest.mark.asyncio
    async def test_uses_config_timeout_when_zero(self):
        """When timeout_sec=0, looks up from config by tool_name."""
        async def fast():
            return "ok"

        # get_tool_timeout should return a sensible default
        timeout = get_tool_timeout("notes.add")
        assert timeout > 0

        result = await with_timeout(fast(), timeout_sec=0, tool_name="notes.add")
        assert result == "ok"

    def test_get_tool_timeout_builtin_patterns(self):
        assert get_tool_timeout("notes.add") == 5
        assert get_tool_timeout("notes.list") == 5
        assert get_tool_timeout("pi.system_info") == 60
        assert get_tool_timeout("pi.gpio_read") == 60
        assert get_tool_timeout("claude.ask") == 120
        assert get_tool_timeout("vision.look") == 45
        assert get_tool_timeout("weather.current") == 10

    def test_get_tool_timeout_unknown_uses_default(self):
        timeout = get_tool_timeout("some_unknown_tool")
        assert timeout == 30  # default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
