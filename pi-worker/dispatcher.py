#!/usr/bin/env python3
"""
JARVIS Pi Dispatcher — Secure task executor for Raspberry Pi worker.

Two modes:
  1. HTTP server: `python3 dispatcher.py --serve` (for PicoClaw gateway integration)
  2. CLI:         `python3 dispatcher.py --task '{"task_name":"gpio_read","args":{"pin":17}}'`

Security:
  - Allowlist-only tool execution
  - Pin/service/script allowlists
  - Binds to 127.0.0.1 only (never exposed to LAN)
  - No shell interpolation — all args are structured data
"""
import argparse
import importlib
import json
import logging
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ──────────────────────────── Setup ────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
TOOLS_DIR = BASE_DIR / "tools"
CONFIG_PATH = BASE_DIR / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("jarvis.pi")


def load_config() -> dict:
    """Load dispatcher config from config.json."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    logger.warning(f"Config not found at {CONFIG_PATH}, using defaults")
    return {
        "allowed_tools": [
            "gpio_read", "gpio_write", "i2c_scan",
            "service_status", "run_script", "system_info"
        ],
        "allowed_pins": [2, 3, 4, 17, 18, 22, 23, 24, 25, 27],
        "allowed_services": ["*"],
        "allowed_scripts": [],
        "bind_host": "127.0.0.1",
        "bind_port": 18790,
    }


CONFIG = load_config()


# ──────────────────────────── Task Execution ────────────────────────────

def execute_task(task: dict) -> dict:
    """Execute a single task and return a result dict."""
    task_id = task.get("task_id", "unknown")
    task_name = task.get("task_name", "")
    args = task.get("args", {})
    start = time.time()

    # Validate tool is allowed
    allowed = CONFIG.get("allowed_tools", [])
    if task_name not in allowed:
        return _error(task_id, f"Tool '{task_name}' not in allowlist", "forbidden", start)

    # Inject config constraints into args so tools can enforce them
    args["_config"] = {
        "allowed_pins": CONFIG.get("allowed_pins", []),
        "allowed_services": CONFIG.get("allowed_services", ["*"]),
        "allowed_scripts": CONFIG.get("allowed_scripts", []),
        "scripts_dir": str(BASE_DIR / "scripts"),
    }

    # Import and run the tool module
    try:
        module = importlib.import_module(f"tools.{task_name}")
        result_data = module.run(args)
        elapsed = (time.time() - start) * 1000

        return {
            "task_id": task_id,
            "ok": True,
            "stdout": result_data.get("stdout", ""),
            "stderr": "",
            "data": result_data.get("data", result_data),
            "elapsed_ms": round(elapsed, 2),
            "error_code": "",
        }

    except PermissionError as e:
        return _error(task_id, str(e), "forbidden", start)
    except ValueError as e:
        return _error(task_id, str(e), "tool_error", start)
    except FileNotFoundError as e:
        return _error(task_id, str(e), "not_found", start)
    except ImportError:
        return _error(task_id, f"Tool module 'tools.{task_name}' not found", "not_found", start)
    except Exception as e:
        logger.exception(f"Task {task_name} failed")
        return _error(task_id, str(e), "unknown", start)


def _error(task_id: str, message: str, code: str, start: float) -> dict:
    elapsed = (time.time() - start) * 1000
    return {
        "task_id": task_id,
        "ok": False,
        "stdout": "",
        "stderr": message,
        "data": None,
        "elapsed_ms": round(elapsed, 2),
        "error_code": code,
    }


# ──────────────────────────── HTTP Server ────────────────────────────

class DispatchHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for task execution."""

    def do_POST(self):
        if self.path != "/execute":
            self._respond(404, {"error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            task = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._respond(400, {"error": f"Invalid JSON: {e}"})
            return

        logger.info(f"Task: {task.get('task_name', '?')} (id={task.get('task_id', '?')})")
        result = execute_task(task)
        self._respond(200, result)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "tools": CONFIG.get("allowed_tools", [])})
        else:
            self._respond(404, {"error": "Not found"})

    def _respond(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress default HTTP logging, we use our own logger
        pass


def serve(host: str, port: int):
    """Start the dispatcher HTTP server."""
    server = HTTPServer((host, port), DispatchHandler)
    logger.info(f"JARVIS Pi Dispatcher listening on {host}:{port}")
    logger.info(f"Allowed tools: {CONFIG.get('allowed_tools', [])}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down dispatcher")
        server.shutdown()


# ──────────────────────────── CLI Mode ────────────────────────────

def cli_execute(task_json: str):
    """Execute a task from CLI argument and print JSON result."""
    try:
        task = json.loads(task_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "stderr": f"Invalid JSON: {e}", "error_code": "parse_error"}))
        sys.exit(1)

    result = execute_task(task)
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


# ──────────────────────────── Entry Point ────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JARVIS Pi Dispatcher")
    parser.add_argument("--serve", action="store_true", help="Run as HTTP server")
    parser.add_argument("--task", type=str, help="Execute a single task (JSON string)")
    parser.add_argument("--host", type=str, default=None, help="Bind host (default from config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default from config)")

    args = parser.parse_args()

    if args.task:
        cli_execute(args.task)
    elif args.serve:
        host = args.host or CONFIG.get("bind_host", "127.0.0.1")
        port = args.port or CONFIG.get("bind_port", 18790)
        serve(host, port)
    else:
        parser.print_help()
        sys.exit(1)
