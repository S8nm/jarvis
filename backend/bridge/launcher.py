"""
PersonaPlex Launcher — Auto-start the Moshi server if not already running.

Checks if PersonaPlex (Moshi) is reachable on the configured port.
If not, spawns it as a subprocess and waits for it to become ready.
"""
import asyncio
import logging
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

from bridge.config import PERSONAPLEX_HOST, PERSONAPLEX_PORT, PERSONAPLEX_SSL

logger = logging.getLogger("jarvis.bridge.launcher")

# Default relative path from project root to PersonaPlex install
_DEFAULT_PERSONAPLEX_DIR = "../../personaplex"


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP port is listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


class PersonaPlexLauncher:
    """Manages the lifecycle of an external PersonaPlex (Moshi) server process."""

    def __init__(self, personaplex_dir: str | None = None):
        from config import PROJECT_ROOT, _cfg

        # Resolve PersonaPlex directory
        configured_dir = _cfg("personaplex_dir", "")
        if personaplex_dir:
            self._dir = Path(personaplex_dir).resolve()
        elif configured_dir:
            self._dir = (PROJECT_ROOT / configured_dir).resolve()
        else:
            self._dir = (PROJECT_ROOT / _DEFAULT_PERSONAPLEX_DIR).resolve()

        self._process: subprocess.Popen | None = None
        self._started_by_us = False

    @property
    def venv_python(self) -> Path:
        """Path to PersonaPlex's virtualenv Python."""
        if sys.platform == "win32":
            return self._dir / "venv" / "Scripts" / "python.exe"
        return self._dir / "venv" / "bin" / "python"

    @property
    def ssl_dir(self) -> Path:
        return self._dir / "ssl_certs"

    def is_installed(self) -> bool:
        """Check if PersonaPlex is installed at the expected location."""
        return self.venv_python.exists()

    def is_running(self) -> bool:
        """Check if PersonaPlex server is already reachable."""
        return _is_port_open(PERSONAPLEX_HOST, PERSONAPLEX_PORT, timeout=2.0)

    async def ensure_running(self, timeout: float = 120.0) -> bool:
        """Ensure PersonaPlex server is running. Start it if needed.

        Args:
            timeout: Max seconds to wait for server to become ready.

        Returns:
            True if server is reachable, False if startup failed.
        """
        # Already running?
        if self.is_running():
            logger.info(f"PersonaPlex already running on {PERSONAPLEX_HOST}:{PERSONAPLEX_PORT}")
            return True

        # Not installed?
        if not self.is_installed():
            logger.warning(
                f"PersonaPlex not installed at {self._dir} — "
                f"voice will run without PersonaPlex server"
            )
            return False

        # Start it
        logger.info(f"Starting PersonaPlex server from {self._dir}...")
        try:
            cmd = [
                str(self.venv_python),
                "-m", "moshi.server",
                "--host", PERSONAPLEX_HOST,
                "--port", str(PERSONAPLEX_PORT),
            ]

            # Add SSL if configured
            if PERSONAPLEX_SSL and self.ssl_dir.exists():
                cmd.extend(["--ssl", str(self.ssl_dir)])

            # Spawn as detached subprocess
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            self._process = subprocess.Popen(
                cmd,
                cwd=str(self._dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
            self._started_by_us = True
            logger.info(f"PersonaPlex process started (PID {self._process.pid}), waiting for server...")

        except Exception as e:
            logger.error(f"Failed to start PersonaPlex: {e}")
            return False

        # Wait for server to become ready
        poll_interval = 2.0
        elapsed = 0.0
        while elapsed < timeout:
            # Check if process died
            if self._process.poll() is not None:
                rc = self._process.returncode
                stderr = ""
                try:
                    stderr = self._process.stderr.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                logger.error(f"PersonaPlex process exited with code {rc}: {stderr}")
                self._process = None
                self._started_by_us = False
                return False

            if self.is_running():
                logger.info(
                    f"PersonaPlex server ready on {PERSONAPLEX_HOST}:{PERSONAPLEX_PORT} "
                    f"(took {elapsed:.0f}s)"
                )
                return True

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed % 10 < poll_interval:
                logger.info(f"Waiting for PersonaPlex... ({elapsed:.0f}s / {timeout:.0f}s)")

        logger.error(f"PersonaPlex did not become ready within {timeout}s")
        return False

    async def stop(self):
        """Stop the PersonaPlex server if we started it."""
        if not self._process or not self._started_by_us:
            return

        logger.info("Stopping PersonaPlex server...")
        try:
            if sys.platform == "win32":
                self._process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self._process.terminate()

            # Give it a few seconds to shut down gracefully
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("PersonaPlex didn't stop gracefully, killing...")
                self._process.kill()
                self._process.wait(timeout=5)

            logger.info("PersonaPlex server stopped")
        except Exception as e:
            logger.warning(f"Error stopping PersonaPlex: {e}")
        finally:
            self._process = None
            self._started_by_us = False
