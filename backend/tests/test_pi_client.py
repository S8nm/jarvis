"""
Unit tests for PiClient — SSH and Gateway transport, retry logic, tunnel management.
Tests use mocks (no real Pi needed).
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pi.models import PiTask, PiResult
from pi.client import PiClient


# ──────────────────────────── Fixtures ──────────────────────────

@pytest.fixture
def pi_config(tmp_path):
    """Minimal Pi config for testing."""
    return {
        "host": "192.168.1.100",
        "user": "jarvis",
        "ssh_key": "",
        "ssh_port": 22,
        "transport": "ssh",
        "gateway_port": 18790,
        "tunnel_local_port": 18790,
        "dispatcher_path": "~/jarvis-pi/dispatcher.py",
        "max_retries": 1,
        "connect_timeout": 5,
        "ledger_path": str(tmp_path / "test_tasks.db"),
    }


@pytest.fixture
def client(pi_config):
    return PiClient(pi_config)


@pytest.fixture
def sample_task():
    return PiTask(task_name="system_info", args={"check": "uptime"}, task_id="test-001")


@pytest.fixture
def success_result():
    return {
        "task_id": "test-001",
        "ok": True,
        "stdout": "up 3 days",
        "stderr": "",
        "data": {"uptime": "up 3 days"},
        "elapsed_ms": 42,
        "error_code": "",
    }


# ──────────────────────────── Model Tests ──────────────────────────

class TestPiTask:
    def test_creates_with_defaults(self):
        task = PiTask(task_name="gpio_read", args={"pin": 17})
        assert task.task_name == "gpio_read"
        assert task.args == {"pin": 17}
        assert len(task.task_id) == 8
        assert task.timeout == 10

    def test_to_json(self):
        task = PiTask(task_name="test", args={"x": 1}, task_id="abc")
        j = task.to_json()
        assert j["task_name"] == "test"
        assert j["task_id"] == "abc"
        assert j["args"] == {"x": 1}
        assert j["timeout"] == 10


class TestPiResult:
    def test_from_json(self, success_result):
        result = PiResult.from_json(success_result)
        assert result.ok is True
        assert result.task_id == "test-001"
        assert result.data == {"uptime": "up 3 days"}

    def test_error_factory(self):
        result = PiResult.error("t1", "Connection refused", "unreachable")
        assert result.ok is False
        assert result.error_code == "unreachable"
        assert "Connection refused" in result.stderr

    def test_from_json_defaults(self):
        result = PiResult.from_json({})
        assert result.ok is False
        assert result.task_id == ""


# ──────────────────────────── SSH Transport Tests ──────────────────────────

class TestSSHTransport:
    @pytest.mark.asyncio
    async def test_ssh_success(self, client, sample_task, success_result):
        """Test successful SSH execution."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(success_result)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = await client._execute_ssh(sample_task)

        assert result.ok is True
        assert result.task_id == "test-001"
        # Verify SSH command structure
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "ssh"
        assert "-o" in call_args
        assert "jarvis@192.168.1.100" in call_args

    @pytest.mark.asyncio
    async def test_ssh_connection_failed(self, client, sample_task):
        """Test SSH connection failure (rc=255)."""
        mock_proc = MagicMock()
        mock_proc.returncode = 255
        mock_proc.stdout = ""
        mock_proc.stderr = "Connection refused"

        with patch("subprocess.run", return_value=mock_proc):
            result = await client._execute_ssh(sample_task)

        assert result.ok is False
        assert result.error_code == "unreachable"

    @pytest.mark.asyncio
    async def test_ssh_timeout(self, client, sample_task):
        """Test SSH timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 15)):
            result = await client._execute_ssh(sample_task)

        assert result.ok is False
        assert result.error_code == "timeout"

    @pytest.mark.asyncio
    async def test_ssh_not_found(self, client, sample_task):
        """Test SSH client not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = await client._execute_ssh(sample_task)

        assert result.ok is False
        assert result.error_code == "config_error"

    @pytest.mark.asyncio
    async def test_ssh_parse_error(self, client, sample_task):
        """Test non-JSON stdout from SSH."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "not json output"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = await client._execute_ssh(sample_task)

        assert result.error_code == "parse_error"

    @pytest.mark.asyncio
    async def test_ssh_key_included(self, pi_config, sample_task, success_result):
        """Test SSH key flag is included when configured."""
        pi_config["ssh_key"] = "/home/user/.ssh/jarvis_pi"
        client = PiClient(pi_config)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(success_result)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            await client._execute_ssh(sample_task)

        call_args = mock_run.call_args[0][0]
        assert "-i" in call_args
        assert "/home/user/.ssh/jarvis_pi" in call_args


# ──────────────────────────── Retry Logic Tests ──────────────────────────

class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_on_failure(self, client, sample_task):
        """Test that execute retries on transient failures."""
        fail_result = PiResult(task_id="test-001", ok=False, stderr="transient error", error_code="unknown")
        success = PiResult(task_id="test-001", ok=True, stdout="up 3 days")

        call_count = 0

        async def mock_ssh(task):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return fail_result
            return success

        client._execute_ssh = mock_ssh

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.execute(sample_task)

        assert result.ok is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_tool_error(self, client, sample_task):
        """Tool errors (bad args) should not be retried."""
        tool_err = PiResult(task_id="test-001", ok=False, stderr="invalid pin", error_code="tool_error")
        call_count = 0

        async def mock_ssh(task):
            nonlocal call_count
            call_count += 1
            return tool_err

        client._execute_ssh = mock_ssh
        result = await client.execute(sample_task)

        assert result.ok is False
        assert call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, client, sample_task):
        """All retries fail -> final error result."""
        async def mock_ssh(task):
            return PiResult(task_id="test-001", ok=False, stderr="down", error_code="unreachable")

        client._execute_ssh = mock_ssh

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.execute(sample_task)

        assert result.ok is False
        assert "All retries failed" in result.stderr


# ──────────────────────────── Ledger Tests ──────────────────────────

class TestLedger:
    def test_ledger_initialized(self, client, tmp_path):
        """Test that the SQLite ledger is created."""
        assert client._ledger_path.exists()

    def test_recent_tasks_empty(self, client):
        """Test recent tasks returns empty list on fresh db."""
        tasks = client.get_recent_tasks()
        assert tasks == []

    @pytest.mark.asyncio
    async def test_ledger_records_execution(self, client, sample_task, success_result):
        """Test that task execution is recorded in the ledger."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(success_result)
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            await client.execute(sample_task)

        tasks = client.get_recent_tasks()
        assert len(tasks) == 1
        assert tasks[0]["task_name"] == "system_info"


# ──────────────────────────── Gateway Transport Tests ──────────────────────────

class TestGatewayTransport:
    @pytest.mark.asyncio
    async def test_gateway_success(self, pi_config, sample_task, success_result):
        """Test successful gateway execution."""
        pi_config["transport"] = "gateway"
        client = PiClient(pi_config)
        # Pre-set tunnel as alive
        client._tunnel_proc = MagicMock()
        client._tunnel_proc.poll.return_value = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = success_result

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await client._execute_gateway(sample_task)

        assert result.ok is True
        assert result.task_id == "test-001"


# ──────────────────────────── Tunnel Tests ──────────────────────────

class TestTunnel:
    def test_close_tunnel(self, client):
        """Test tunnel cleanup."""
        client._tunnel_proc = MagicMock()
        client.close_tunnel()
        assert client._tunnel_proc is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
