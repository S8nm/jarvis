"""
Jarvis Protocol — PiClient
Executes tasks on a Raspberry Pi worker via SSH or PicoClaw gateway (tunneled).

Two transport modes:
  A) SSH: runs `ssh pi 'python3 ~/jarvis-pi/dispatcher.py --task <json>'`
  B) Gateway: HTTP POST to localhost tunnel -> Pi's PicoClaw gateway on 127.0.0.1:18790

Security:
  - Gateway never exposed to LAN (SSH tunnel only)
  - SSH key auth, no passwords
  - All tasks logged to local ledger
"""
import asyncio
import json
import logging
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from pi.models import PiTask, PiResult

logger = logging.getLogger("jarvis.pi.client")


class PiClient:
    """Controls a Raspberry Pi worker node from the PC."""

    def __init__(self, config: dict):
        self.host: str = config.get("host", "")
        self.user: str = config.get("user", "jarvis")
        ssh_key_raw = config.get("ssh_key", "")
        self.ssh_key: str = str(Path(ssh_key_raw).expanduser()) if ssh_key_raw else ""
        self.ssh_port: int = config.get("ssh_port", 22)
        self.transport: str = config.get("transport", "ssh")  # "ssh" or "gateway"
        self.gateway_port: int = config.get("gateway_port", 18790)  # Pi-side
        self.tunnel_local_port: int = config.get("tunnel_local_port", 18790)  # PC-side
        self.dispatcher_path: str = config.get("dispatcher_path", "~/jarvis-pi/dispatcher.py")
        self.max_retries: int = config.get("max_retries", 2)
        self.connect_timeout: int = config.get("connect_timeout", 5)

        # SSH tunnel process (for gateway mode)
        self._tunnel_proc: Optional[subprocess.Popen] = None

        # Task ledger (SQLite on PC for audit)
        self._ledger_path = Path(config.get("ledger_path", "data/pi_tasks.db"))
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_ledger()

    def _init_ledger(self):
        """Initialize the task ledger database."""
        conn = sqlite3.connect(str(self._ledger_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pi_tasks (
                task_id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                args TEXT,
                transport TEXT,
                ok INTEGER,
                stdout TEXT,
                stderr TEXT,
                error_code TEXT,
                elapsed_ms REAL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _log_to_ledger(self, task: PiTask, result: PiResult):
        """Record task execution in the audit ledger."""
        try:
            conn = sqlite3.connect(str(self._ledger_path))
            conn.execute(
                """INSERT OR REPLACE INTO pi_tasks
                   (task_id, task_name, args, transport, ok, stdout, stderr, error_code, elapsed_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task.task_id, task.task_name, json.dumps(task.args),
                 self.transport, int(result.ok), result.stdout[:1000],
                 result.stderr[:1000], result.error_code, result.elapsed_ms,
                 datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Ledger write failed: {e}")

    # ────────────────────── Main API ──────────────────────

    async def execute(self, task: PiTask) -> PiResult:
        """Execute a task on the Pi with retry logic."""
        logger.info(f"Pi task: {task.task_name} (id={task.task_id}, transport={self.transport})")

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.transport == "gateway":
                    result = await self._execute_gateway(task)
                else:
                    result = await self._execute_ssh(task)

                self._log_to_ledger(task, result)
                if result.ok or result.error_code == "tool_error":
                    # Tool errors are legitimate (bad args, etc), don't retry
                    return result

                last_error = result.stderr
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Pi task attempt {attempt+1} failed: {e}")

            if attempt < self.max_retries:
                wait = (attempt + 1) * 2  # 2s, 4s backoff
                logger.info(f"Retrying in {wait}s...")
                await asyncio.sleep(wait)

        result = PiResult.error(task.task_id, f"All retries failed: {last_error}", "unreachable")
        self._log_to_ledger(task, result)
        return result

    async def ping(self) -> PiResult:
        """Check if the Pi is reachable."""
        task = PiTask(task_name="system_info", args={"check": "uptime"})
        return await self.execute(task)

    async def get_health(self) -> dict:
        """Get Pi health metrics."""
        task = PiTask(task_name="system_info", args={"check": "all"})
        result = await self.execute(task)
        if result.ok and result.data:
            return result.data
        return {"reachable": False, "error": result.stderr}

    # ────────────────────── SSH Transport ──────────────────────

    async def _execute_ssh(self, task: PiTask) -> PiResult:
        """Execute a task via SSH remote command."""
        task_json = json.dumps(task.to_json())
        # Escape single quotes in JSON for shell
        escaped = task_json.replace("'", "'\\''")

        ssh_cmd = [
            "ssh",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-p", str(self.ssh_port),
        ]
        if self.ssh_key:
            ssh_cmd.extend(["-i", self.ssh_key])

        ssh_cmd.extend([
            f"{self.user}@{self.host}",
            f"python3 {self.dispatcher_path} --task '{escaped}'"
        ])

        start = time.time()
        try:
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(None, lambda: subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=task.timeout + self.connect_timeout,
            ))
            elapsed = (time.time() - start) * 1000

            if proc.returncode == 255:
                return PiResult.error(task.task_id, "SSH connection failed", "unreachable")

            # Try to parse JSON from stdout
            try:
                raw = json.loads(proc.stdout.strip())
                result = PiResult.from_json(raw)
                result.elapsed_ms = elapsed
                return result
            except json.JSONDecodeError:
                return PiResult(
                    task_id=task.task_id,
                    ok=proc.returncode == 0,
                    stdout=proc.stdout[:2000],
                    stderr=proc.stderr[:2000],
                    elapsed_ms=elapsed,
                    error_code="parse_error" if proc.returncode == 0 else "tool_error",
                )

        except subprocess.TimeoutExpired:
            elapsed = (time.time() - start) * 1000
            return PiResult.error(task.task_id, f"SSH timeout after {task.timeout}s", "timeout")
        except FileNotFoundError:
            return PiResult.error(task.task_id, "SSH client not found", "config_error")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return PiResult.error(task.task_id, str(e), "unknown")

    # ────────────────────── Gateway Transport ──────────────────────

    async def _execute_gateway(self, task: PiTask) -> PiResult:
        """Execute a task via PicoClaw gateway (through SSH tunnel)."""
        await self._ensure_tunnel()

        url = f"http://127.0.0.1:{self.tunnel_local_port}/execute"
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=task.timeout + 2) as client:
                resp = await client.post(url, json=task.to_json(), timeout=task.timeout + 2)
                elapsed = (time.time() - start) * 1000

                if resp.status_code == 200:
                    raw = resp.json()
                    result = PiResult.from_json(raw)
                    result.elapsed_ms = elapsed
                    return result
                else:
                    return PiResult(
                        task_id=task.task_id,
                        ok=False,
                        stderr=f"Gateway HTTP {resp.status_code}: {resp.text[:500]}",
                        elapsed_ms=elapsed,
                        error_code="gateway_error",
                    )

        except httpx.ConnectError:
            return PiResult.error(task.task_id, "Gateway unreachable (tunnel down?)", "unreachable")
        except httpx.TimeoutException:
            return PiResult.error(task.task_id, f"Gateway timeout after {task.timeout}s", "timeout")
        except Exception as e:
            return PiResult.error(task.task_id, str(e), "unknown")

    async def _ensure_tunnel(self):
        """Open SSH tunnel to Pi's gateway if not already open."""
        if self._tunnel_proc and self._tunnel_proc.poll() is None:
            return  # Tunnel still alive

        logger.info(f"Opening SSH tunnel: localhost:{self.tunnel_local_port} -> Pi:{self.gateway_port}")
        tunnel_cmd = [
            "ssh",
            "-N",  # No remote command
            "-L", f"{self.tunnel_local_port}:127.0.0.1:{self.gateway_port}",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-p", str(self.ssh_port),
        ]
        if self.ssh_key:
            tunnel_cmd.extend(["-i", self.ssh_key])
        tunnel_cmd.append(f"{self.user}@{self.host}")

        try:
            self._tunnel_proc = subprocess.Popen(
                tunnel_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            # Wait a moment for tunnel to establish
            await asyncio.sleep(1)
            if self._tunnel_proc.poll() is not None:
                stderr = self._tunnel_proc.stderr.read().decode()[:200]
                logger.error(f"SSH tunnel failed to start: {stderr}")
                self._tunnel_proc = None
            else:
                logger.info("SSH tunnel established")
        except Exception as e:
            logger.error(f"Failed to create SSH tunnel: {e}")
            self._tunnel_proc = None

    def close_tunnel(self):
        """Close the SSH tunnel."""
        if self._tunnel_proc:
            self._tunnel_proc.terminate()
            self._tunnel_proc = None
            logger.info("SSH tunnel closed")

    # ────────────────────── Stats ──────────────────────

    def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        """Get recent task executions from the ledger."""
        try:
            conn = sqlite3.connect(str(self._ledger_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pi_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []
