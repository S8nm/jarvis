"""
JARVIS Pi Tool — System Info
Get system health metrics from the Pi (CPU, memory, disk, temperature, uptime).
"""
import os
import subprocess


def run(args: dict) -> dict:
    """Get Pi system information.

    Args:
        check (str): What to check — "uptime", "cpu", "memory", "disk", "temp", "all"
    """
    check = args.get("check", "all").lower()

    data = {}

    if check in ("uptime", "all"):
        data["uptime"] = _get_uptime()

    if check in ("cpu", "all"):
        data["cpu"] = _get_cpu()

    if check in ("memory", "all"):
        data["memory"] = _get_memory()

    if check in ("disk", "all"):
        data["disk"] = _get_disk()

    if check in ("temp", "all"):
        data["temperature"] = _get_temp()

    if check in ("all",):
        data["hostname"] = _cmd("hostname").strip()
        data["kernel"] = _cmd("uname -r").strip()

    # Build a human-readable summary
    parts = []
    if "uptime" in data:
        parts.append(f"Uptime: {data['uptime']}")
    if "temperature" in data:
        parts.append(f"Temp: {data['temperature']}C")
    if "memory" in data:
        m = data["memory"]
        parts.append(f"RAM: {m.get('used_mb', '?')}/{m.get('total_mb', '?')}MB")
    if "cpu" in data:
        parts.append(f"CPU: {data['cpu'].get('load_1m', '?')} load")

    return {
        "stdout": " | ".join(parts) if parts else "OK",
        "data": data,
    }


def _cmd(command: str) -> str:
    """Run a shell command and return stdout."""
    try:
        result = subprocess.run(
            command.split(), capture_output=True, text=True, timeout=5
        )
        return result.stdout
    except Exception:
        return ""


def _get_uptime() -> str:
    raw = _cmd("uptime -p").strip()
    return raw if raw else "unknown"


def _get_cpu() -> dict:
    try:
        load = os.getloadavg()
        return {
            "load_1m": round(load[0], 2),
            "load_5m": round(load[1], 2),
            "load_15m": round(load[2], 2),
            "cores": os.cpu_count() or 0,
        }
    except (OSError, AttributeError):
        return {"load_1m": 0, "cores": os.cpu_count() or 0}


def _get_memory() -> dict:
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        available = info.get("MemAvailable", 0) // 1024
        used = total - available
        return {
            "total_mb": total,
            "available_mb": available,
            "used_mb": used,
            "percent": round(used / total * 100, 1) if total > 0 else 0,
        }
    except Exception:
        return {}


def _get_disk() -> dict:
    try:
        stat = os.statvfs("/")
        total = stat.f_blocks * stat.f_frsize // (1024 * 1024)
        free = stat.f_bfree * stat.f_frsize // (1024 * 1024)
        used = total - free
        return {
            "total_mb": total,
            "used_mb": used,
            "free_mb": free,
            "percent": round(used / total * 100, 1) if total > 0 else 0,
        }
    except Exception:
        return {}


def _get_temp() -> float:
    """Read CPU temperature from thermal zone."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return 0.0
