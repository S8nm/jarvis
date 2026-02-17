"""
Jarvis Protocol — Pi Health Monitor

Periodic connectivity check for the Raspberry Pi worker.
Queues actions when Pi is offline and drains them on reconnect.

Usage:
    monitor = PiHealthMonitor(pi_client, check_interval=60)
    await monitor.start()
    # ...later:
    monitor.queue_action("gpio_write", {"pin": 17, "value": 1})
"""
import asyncio
import logging
import time
from typing import Optional, Callable

from config import _cfg

logger = logging.getLogger("jarvis.resilience.pi_health")

_resilience_cfg = _cfg("resilience", {})
if not isinstance(_resilience_cfg, dict):
    _resilience_cfg = {}

_DEFAULT_CHECK_INTERVAL = _resilience_cfg.get("pi_health_check_interval", 60)
_MAX_QUEUE_SIZE = 20


class PiHealthMonitor:
    """
    Periodically pings the Pi, detects outages, queues actions when offline.

    - ONLINE: actions execute normally via PiClient
    - OFFLINE: actions queued (up to _MAX_QUEUE_SIZE)
    - RECONNECT: queued actions drained automatically
    """

    def __init__(self, pi_client, check_interval: int = 0):
        self._pi_client = pi_client
        self._check_interval = check_interval or _DEFAULT_CHECK_INTERVAL
        self._is_online = False
        self._last_check: float = 0.0
        self._last_health: dict = {}
        self._offline_queue: list[dict] = []
        self._task: Optional[asyncio.Task] = None
        self._broadcast: Optional[Callable] = None

    @property
    def is_online(self) -> bool:
        return self._is_online

    def set_broadcast(self, fn: Callable):
        """Set the WebSocket broadcast function for status updates."""
        self._broadcast = fn

    async def start(self):
        """Start the periodic health check loop as a background task."""
        self._task = asyncio.create_task(self._health_loop())
        logger.info(f"Pi health monitor started (interval: {self._check_interval}s)")

    async def stop(self):
        """Stop the health check loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Pi health monitor stopped")

    async def _health_loop(self):
        """Background loop: ping Pi periodically."""
        while True:
            try:
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Pi health check error: {e}")
            await asyncio.sleep(self._check_interval)

    async def _check_health(self):
        """Single health check: ping the Pi and update state."""
        was_online = self._is_online

        try:
            result = await self._pi_client.ping()
            self._is_online = result.ok
            self._last_check = time.time()

            if result.ok and result.data:
                self._last_health = result.data

        except Exception as e:
            self._is_online = False
            logger.debug(f"Pi ping failed: {e}")

        # State transition: offline -> online
        if not was_online and self._is_online:
            logger.info("Pi is back online")
            await self._broadcast_status("online")
            await self._drain_queue()

        # State transition: online -> offline
        elif was_online and not self._is_online:
            logger.warning("Pi went offline")
            await self._broadcast_status("offline")

    def queue_action(self, task_name: str, args: dict) -> bool:
        """
        Queue an action for when Pi comes back online.
        Returns False if queue is full.
        """
        if len(self._offline_queue) >= _MAX_QUEUE_SIZE:
            logger.warning(f"Pi action queue full ({_MAX_QUEUE_SIZE}), dropping: {task_name}")
            return False

        self._offline_queue.append({"task_name": task_name, "args": args})
        logger.info(f"Pi action queued: {task_name} (queue: {len(self._offline_queue)})")
        return True

    async def _drain_queue(self):
        """Execute queued actions after Pi reconnect."""
        if not self._offline_queue:
            return

        queue = self._offline_queue.copy()
        self._offline_queue.clear()
        logger.info(f"Draining {len(queue)} queued Pi actions")

        from pi.models import PiTask

        for action in queue:
            try:
                task = PiTask(
                    task_name=action["task_name"],
                    args=action.get("args", {}),
                )
                result = await self._pi_client.execute(task)
                if not result.ok:
                    logger.warning(f"Queued action {action['task_name']} failed: {result.stderr}")
            except Exception as e:
                logger.warning(f"Queued action {action['task_name']} error: {e}")

    async def _broadcast_status(self, status: str):
        """Send Pi health update via WebSocket."""
        if not self._broadcast:
            return
        import json
        try:
            await self._broadcast(json.dumps({
                "type": "pi_health",
                "data": self.get_status() | {"event": status},
            }))
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return status for health endpoint / frontend."""
        return {
            "reachable": self._is_online,
            "last_check": self._last_check,
            "queue_size": len(self._offline_queue),
            "health": self._last_health,
        }
