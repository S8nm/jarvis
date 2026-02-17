"""
Jarvis Protocol — Tool Execution Timeout

Wraps async tool calls with configurable per-tool timeouts.
Prevents hung tools from blocking the agent indefinitely.

Usage:
    result = await with_timeout(some_coro(), timeout_sec=30, tool_name="pi.system_info")
"""
import asyncio
import fnmatch
import logging

from config import _cfg

logger = logging.getLogger("jarvis.resilience.tool_timeout")


# Default timeouts (seconds) per tool prefix/pattern.
# config.json "tool_timeouts" overrides these.
_BUILTIN_TIMEOUTS = {
    "notes.*": 5,
    "calendar.*": 5,
    "files.*": 10,
    "scripts.*": 30,
    "weather.*": 10,
    "memory.*": 5,
    "vision.*": 45,
    "pi.*": 60,
    "claude.*": 120,
    "default": 30,
}

# Merge with user config
_timeout_cfg = _cfg("tool_timeouts", {})
if not isinstance(_timeout_cfg, dict):
    _timeout_cfg = {}


def get_tool_timeout(tool_name: str) -> float:
    """
    Get the timeout for a specific tool.
    Checks config.json overrides first, then built-in patterns.
    """
    # Exact match in user config
    if tool_name in _timeout_cfg:
        return float(_timeout_cfg[tool_name])

    # Glob pattern match in user config (e.g. "pi_*": 60)
    for pattern, timeout in _timeout_cfg.items():
        if pattern != "default" and fnmatch.fnmatch(tool_name, pattern):
            return float(timeout)

    # User default
    if "default" in _timeout_cfg:
        return float(_timeout_cfg["default"])

    # Built-in pattern match
    for pattern, timeout in _BUILTIN_TIMEOUTS.items():
        if pattern != "default" and fnmatch.fnmatch(tool_name, pattern):
            return float(timeout)

    return float(_BUILTIN_TIMEOUTS["default"])


async def with_timeout(coro, timeout_sec: float = 0, tool_name: str = "unknown"):
    """
    Wrap an awaitable with a timeout.

    If timeout_sec is 0, looks up the timeout from config by tool_name.
    Returns the coroutine result or a structured error dict on timeout.
    """
    if timeout_sec <= 0:
        timeout_sec = get_tool_timeout(tool_name)

    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.error(f"Tool '{tool_name}' timed out after {timeout_sec}s")
        return {
            "error": f"Tool '{tool_name}' timed out after {timeout_sec}s",
            "error_type": "timeout",
            "timeout_seconds": timeout_sec,
        }
