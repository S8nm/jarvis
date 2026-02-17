"""
Jarvis Protocol — Pi Connection Configuration
Reads Pi settings from the project config.json under the "pi" key.
"""
import logging
from config import _cfg

logger = logging.getLogger("jarvis.pi.config")


def get_pi_config() -> dict:
    """Load Pi connection settings from config.json."""
    pi = _cfg("pi", {})
    if not pi:
        logger.info("No 'pi' section in config.json — Pi integration disabled")
        return {}
    return {
        "host": pi.get("host", ""),
        "user": pi.get("user", "jarvis"),
        "ssh_key": pi.get("ssh_key", ""),
        "ssh_port": pi.get("ssh_port", 22),
        "transport": pi.get("transport", "ssh"),
        "gateway_port": pi.get("gateway_port", 18790),
        "tunnel_local_port": pi.get("tunnel_local_port", 18790),
        "dispatcher_path": pi.get("dispatcher_path", "~/jarvis-pi/dispatcher.py"),
        "max_retries": pi.get("max_retries", 2),
        "connect_timeout": pi.get("connect_timeout", 5),
        "ledger_path": pi.get("ledger_path", "data/pi_tasks.db"),
    }


def is_pi_enabled() -> bool:
    """Check if Pi integration is configured."""
    pi = _cfg("pi", {})
    return bool(pi.get("host"))
