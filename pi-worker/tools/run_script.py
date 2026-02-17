"""
JARVIS Pi Tool — Run Script
Execute an allowed script from the scripts/ directory.
"""
import subprocess
from pathlib import Path


def run(args: dict) -> dict:
    """Execute a script from the scripts/ directory.

    Args:
        script (str): Script filename (no path traversal allowed)
        script_args (list): Arguments to pass to the script
        timeout (int): Execution timeout in seconds (default: 30)
        _config.allowed_scripts (list): Enforced script allowlist (empty = all in scripts/)
        _config.scripts_dir (str): Path to scripts directory
    """
    script_name = args.get("script", "").strip()
    if not script_name:
        raise ValueError("Missing required arg: script")

    script_args = args.get("script_args", [])
    timeout = int(args.get("timeout", 30))
    timeout = min(timeout, 120)  # Hard cap at 2 minutes

    config = args.get("_config", {})
    scripts_dir = Path(config.get("scripts_dir", "./scripts")).resolve()

    # Prevent path traversal
    script_path = (scripts_dir / script_name).resolve()
    if not str(script_path).startswith(str(scripts_dir)):
        raise PermissionError(f"Path traversal detected: {script_name}")

    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_name}")

    # Check allowlist
    allowed = config.get("allowed_scripts", [])
    if allowed and script_name not in allowed:
        raise PermissionError(f"Script '{script_name}' not in allowlist")

    # Determine interpreter
    suffix = script_path.suffix.lower()
    if suffix == ".py":
        cmd = ["python3", str(script_path)] + [str(a) for a in script_args]
    elif suffix == ".sh":
        cmd = ["bash", str(script_path)] + [str(a) for a in script_args]
    elif script_path.stat().st_mode & 0o111:
        # Executable file
        cmd = [str(script_path)] + [str(a) for a in script_args]
    else:
        raise ValueError(f"Unknown script type: {suffix}. Supported: .py, .sh, or executable")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(scripts_dir),
        )
        return {
            "stdout": proc.stdout[:4000],
            "data": {
                "script": script_name,
                "exit_code": proc.returncode,
                "stdout": proc.stdout[:4000],
                "stderr": proc.stderr[:2000],
            },
        }
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Script '{script_name}' timed out after {timeout}s")
