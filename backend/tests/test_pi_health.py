"""
Unit tests for PiHealthMonitor — connectivity detection, action queuing, drain on reconnect.
Tests use mocks (no real Pi needed).
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pi.models import PiResult
from resilience.pi_health import PiHealthMonitor


# ──────────────────────────── Fixtures ──────────────────────────

@pytest.fixture
def mock_pi_client():
    """Mock PiClient with configurable ping results."""
    client = MagicMock()
    client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=True, data={"uptime": "3d"}))
    client.execute = AsyncMock(return_value=PiResult(task_id="exec", ok=True))
    return client


@pytest.fixture
def monitor(mock_pi_client):
    return PiHealthMonitor(mock_pi_client, check_interval=1)


# ──────────────────────────── Health Check Tests ──────────────────────────

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_starts_offline(self, monitor):
        assert monitor.is_online is False

    @pytest.mark.asyncio
    async def test_detects_online(self, monitor):
        await monitor._check_health()
        assert monitor.is_online is True

    @pytest.mark.asyncio
    async def test_detects_offline(self, monitor, mock_pi_client):
        # First go online
        await monitor._check_health()
        assert monitor.is_online is True

        # Then fail
        mock_pi_client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=False))
        await monitor._check_health()
        assert monitor.is_online is False

    @pytest.mark.asyncio
    async def test_ping_exception_sets_offline(self, monitor, mock_pi_client):
        mock_pi_client.ping = AsyncMock(side_effect=ConnectionError("no route"))
        await monitor._check_health()
        assert monitor.is_online is False

    @pytest.mark.asyncio
    async def test_stores_health_data(self, monitor):
        await monitor._check_health()
        status = monitor.get_status()
        assert status["reachable"] is True
        assert status["last_check"] > 0
        assert status["health"] == {"uptime": "3d"}


# ──────────────────────────── Action Queue Tests ──────────────────────────

class TestActionQueue:
    def test_queue_action(self, monitor):
        ok = monitor.queue_action("gpio_write", {"pin": 17, "value": 1})
        assert ok is True
        assert monitor.get_status()["queue_size"] == 1

    def test_queue_full_rejects(self, monitor):
        for i in range(20):
            monitor.queue_action("gpio_write", {"pin": i, "value": 1})
        ok = monitor.queue_action("gpio_write", {"pin": 99, "value": 1})
        assert ok is False
        assert monitor.get_status()["queue_size"] == 20

    @pytest.mark.asyncio
    async def test_drain_on_reconnect(self, monitor, mock_pi_client):
        # Start offline
        mock_pi_client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=False))
        await monitor._check_health()
        assert monitor.is_online is False

        # Queue actions
        monitor.queue_action("gpio_write", {"pin": 17, "value": 1})
        monitor.queue_action("gpio_write", {"pin": 18, "value": 0})

        # Come back online
        mock_pi_client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=True))
        await monitor._check_health()

        assert monitor.is_online is True
        assert monitor.get_status()["queue_size"] == 0
        assert mock_pi_client.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_drain_handles_failures(self, monitor, mock_pi_client):
        """Failed queued actions shouldn't crash the drain."""
        mock_pi_client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=False))
        await monitor._check_health()

        monitor.queue_action("bad_tool", {})

        mock_pi_client.execute = AsyncMock(return_value=PiResult(task_id="x", ok=False, stderr="err"))
        mock_pi_client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=True))
        await monitor._check_health()

        assert monitor.is_online is True
        assert monitor.get_status()["queue_size"] == 0


# ──────────────────────────── Broadcast Tests ──────────────────────────

class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcasts_online(self, monitor, mock_pi_client):
        broadcast = AsyncMock()
        monitor.set_broadcast(broadcast)

        await monitor._check_health()  # offline -> online
        assert broadcast.called

    @pytest.mark.asyncio
    async def test_broadcasts_offline(self, monitor, mock_pi_client):
        broadcast = AsyncMock()
        monitor.set_broadcast(broadcast)

        # Go online first
        await monitor._check_health()
        broadcast.reset_mock()

        # Then go offline
        mock_pi_client.ping = AsyncMock(return_value=PiResult(task_id="ping", ok=False))
        await monitor._check_health()
        assert broadcast.called


# ──────────────────────────── Lifecycle Tests ──────────────────────────

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, monitor):
        await monitor.start()
        assert monitor._task is not None

        await monitor.stop()
        assert monitor._task is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
