"""
JARVIS Pi Tool — Systemd Service Status
Check the status of a systemd service.
"""
import subprocess


def run(args: dict) -> dict:
    """Check if a systemd service is active.

    Args:
        service (str): Service name (e.g. "ssh", "nginx")
        _config.allowed_services (list): Enforced service allowlist ("*" = all)
    """
    service = args.get("service", "").strip()
    if not service:
        raise ValueError("Missing required arg: service")

    # Validate against allowlist
    allowed = args.get("_config", {}).get("allowed_services", ["*"])
    if "*" not in allowed and service not in allowed:
        raise PermissionError(f"Service '{service}' not in allowlist")

    # Sanitize: only allow alphanumeric, dash, underscore, dot, @
    if not all(c.isalnum() or c in "-_.@" for c in service):
        raise ValueError(f"Invalid service name: {service}")

    # Check active state
    try:
        active = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5
        )
        is_active = active.stdout.strip()
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"systemctl timed out checking {service}")

    # Get more details
    try:
        show = subprocess.run(
            ["systemctl", "show", service,
             "--property=ActiveState,SubState,MainPID,MemoryCurrent,Description"],
            capture_output=True, text=True, timeout=5
        )
        props = {}
        for line in show.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
    except Exception:
        props = {}

    return {
        "stdout": f"{service}: {is_active}",
        "data": {
            "service": service,
            "active": is_active == "active",
            "state": is_active,
            "sub_state": props.get("SubState", ""),
            "pid": int(props.get("MainPID", 0)),
            "memory": props.get("MemoryCurrent", ""),
            "description": props.get("Description", ""),
        },
    }
