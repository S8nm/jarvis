"""
JARVIS Pi Tool — PicoClaw Scheduled Tasks (Cron)
Manage scheduled tasks through PicoClaw's cron system.
"""
import subprocess
import json


def run(args: dict) -> dict:
    """Manage PicoClaw scheduled tasks.

    Args:
        action (str): "list" to list cron jobs, "add" to add one
        schedule (str): For add — natural language schedule (e.g. "every day at 9am check disk space")
    """
    action = args.get("action", "list").strip().lower()

    if action == "list":
        proc = subprocess.run(
            ["picoclaw", "cron", "list"],
            capture_output=True, text=True, timeout=10,
        )
        return {
            "stdout": proc.stdout.strip(),
            "data": {"jobs": proc.stdout.strip(), "action": "list"},
        }

    elif action == "add":
        schedule = args.get("schedule", "").strip()
        if not schedule:
            raise ValueError("Missing required arg: schedule (natural language description)")

        proc = subprocess.run(
            ["picoclaw", "cron", "add", schedule],
            capture_output=True, text=True, timeout=15,
        )
        return {
            "stdout": proc.stdout.strip(),
            "data": {
                "action": "add",
                "schedule": schedule,
                "result": proc.stdout.strip(),
                "exit_code": proc.returncode,
            },
        }

    else:
        raise ValueError(f"Unknown action: {action}. Use 'list' or 'add'.")
