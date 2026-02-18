"""
Jarvis Protocol — Sliding Window Rate Limiter

Per-source rate limiting to prevent rapid-fire queries from overwhelming backends.

Usage:
    limiter = SlidingWindowRateLimiter()
    limiter.configure("voice", max_requests=5, window_sec=60)
    allowed, info = limiter.check("voice")
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from config import _cfg

logger = logging.getLogger("jarvis.resilience.rate_limiter")


# Load defaults from config.json -> "rate_limits": {"voice": 5, "text": 15, ...}
_rate_cfg = _cfg("rate_limits", {})
if not isinstance(_rate_cfg, dict):
    _rate_cfg = {}

_DEFAULT_LIMITS = {
    "voice": _rate_cfg.get("voice", 5),
    "text": _rate_cfg.get("text", 15),
    "telegram": _rate_cfg.get("telegram", 10),
}

_DEFAULT_WINDOW_SEC = 60.0


class SlidingWindowRateLimiter:
    """
    Per-source sliding window rate limiter.
    Each source (voice, text, telegram) has its own independent window.
    """

    def __init__(self):
        self._windows: dict[str, deque] = {}
        self._limits: dict[str, int] = dict(_DEFAULT_LIMITS)
        self._window_secs: dict[str, float] = {}

    def configure(self, source: str, max_requests: int, window_sec: float = 60.0):
        """Set rate limit for a source."""
        self._limits[source] = max_requests
        self._window_secs[source] = window_sec

    def check(self, source: str) -> tuple[bool, dict]:
        """
        Check if a request from source is allowed.

        Returns:
            (allowed, info) where info contains remaining/retry_after/limit.
        """
        now = time.monotonic()
        limit = self._limits.get(source, 15)  # default 15/min
        window_sec = self._window_secs.get(source, _DEFAULT_WINDOW_SEC)
        cutoff = now - window_sec

        if source not in self._windows:
            self._windows[source] = deque()

        window = self._windows[source]

        # Evict expired entries
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= limit:
            retry_after = (window[0] + window_sec) - now
            logger.warning(
                f"Rate limited: {source} ({len(window)}/{limit} in {window_sec}s)"
            )
            return False, {
                "remaining": 0,
                "retry_after": round(max(0.0, retry_after), 1),
                "limit": limit,
                "source": source,
            }

        window.append(now)
        return True, {
            "remaining": limit - len(window),
            "limit": limit,
            "source": source,
        }

    def get_status(self) -> dict:
        """Current usage per source for dashboard."""
        now = time.monotonic()
        status = {}
        for source, window in self._windows.items():
            cutoff = now - self._window_secs.get(source, _DEFAULT_WINDOW_SEC)
            active = sum(1 for t in window if t >= cutoff)
            limit = self._limits.get(source, 15)
            status[source] = {
                "active": active,
                "limit": limit,
                "utilization": round(active / limit, 2) if limit else 0,
            }
        return status

    def reset(self, source: str = ""):
        """Reset a source or all sources."""
        if source:
            self._windows.pop(source, None)
        else:
            self._windows.clear()
