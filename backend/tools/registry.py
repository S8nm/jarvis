"""
Jarvis Protocol — Tool Registry
Central registry that maps tool names to implementations.
The LLM outputs structured tool calls; this module routes and executes them.

Improvements inspired by:
- sukeesh/Jarvis: catch-all exception handling, graceful degradation
- Microsoft JARVIS: structured logging of success/failure cases
- Priler/jarvis: pluggable backend pattern
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from functools import wraps
from typing import Callable

from tools import notes, calendar_tool, files, scripts, weather
from tools.vision import VisionTool

logger = logging.getLogger("jarvis.tools.registry")

# Singleton vision tool instance
_vision = VisionTool()

# ────────────────────────── Execution Logging ──────────────────────────
# Inspired by Microsoft JARVIS's comprehensive case recording

_execution_log: list[dict] = []
_MAX_LOG_SIZE = 200


def _log_execution(tool_name: str, args: dict, result: dict, elapsed: float, success: bool):
    """Record tool execution for debugging and analytics."""
    entry = {
        "tool": tool_name,
        "args_summary": {k: str(v)[:100] for k, v in args.items()},
        "success": success,
        "elapsed_ms": round(elapsed * 1000, 1),
        "timestamp": datetime.now().isoformat(),
        "error": result.get("error") if not success else None,
    }
    _execution_log.append(entry)
    if len(_execution_log) > _MAX_LOG_SIZE:
        _execution_log.pop(0)

    level = logging.INFO if success else logging.WARNING
    logger.log(level,
        f"Tool {'OK' if success else 'FAIL'}: {tool_name} ({elapsed*1000:.0f}ms)"
        f"{' — ' + str(result.get('error', ''))[:80] if not success else ''}"
    )


def get_execution_stats() -> dict:
    """Get tool execution statistics for dashboard."""
    if not _execution_log:
        return {"total": 0, "success_rate": 0, "recent": []}

    total = len(_execution_log)
    successes = sum(1 for e in _execution_log if e["success"])
    recent = _execution_log[-5:]

    # Per-tool stats
    tool_stats = {}
    for entry in _execution_log:
        name = entry["tool"]
        if name not in tool_stats:
            tool_stats[name] = {"calls": 0, "successes": 0, "avg_ms": 0, "total_ms": 0}
        tool_stats[name]["calls"] += 1
        tool_stats[name]["total_ms"] += entry["elapsed_ms"]
        if entry["success"]:
            tool_stats[name]["successes"] += 1

    for name, stats in tool_stats.items():
        stats["avg_ms"] = round(stats["total_ms"] / stats["calls"], 1)
        stats["success_rate"] = round(stats["successes"] / stats["calls"] * 100, 1)
        del stats["total_ms"]

    return {
        "total": total,
        "success_rate": round(successes / total * 100, 1),
        "recent": recent,
        "per_tool": tool_stats,
    }


# ────────────────────────── Tool Definitions ──────────────────────────
# These are provided to the LLM so it knows what tools are available

TOOL_DEFINITIONS = """
You have access to the following tools. When the user's request requires a tool, output a JSON block with the tool call. Use exactly this format:

```tool
{"tool": "tool_name", "args": {"param1": "value1", "param2": "value2"}}
```

Available tools:

1. **notes.add** — Add a mental note
   Args: content (string, required), tag (string, optional, default "general")
   Example: {"tool": "notes.add", "args": {"content": "Buy groceries", "tag": "personal"}}

2. **notes.list** — List notes
   Args: tag (string, optional), limit (int, optional, default 20)

3. **notes.search** — Search notes
   Args: query (string, required)

4. **notes.delete** — Delete a note
   Args: id (int, required)

5. **calendar.create** — Create a calendar event
   Args: title (string), start_time (string, e.g. "2026-02-17 10:00" or "tomorrow"), end_time (string, optional), calendar (string, "personal" or "uni"), location (string, optional), description (string, optional)
   Example: {"tool": "calendar.create", "args": {"title": "CS101 Lecture", "start_time": "2026-02-17 10:00", "end_time": "2026-02-17 11:30", "calendar": "uni"}}

6. **calendar.list** — List upcoming events
   Args: calendar (string, optional), days_ahead (int, optional, default 7)

7. **calendar.today** — Get today's events
   Args: none

8. **calendar.delete** — Delete an event
   Args: id (int, required)

9. **calendar.export** — Export calendar as ICS
   Args: calendar (string, optional)

10. **vision.look** — Activate camera, capture, and analyze what's visible
    Args: prompt (string, optional, what to focus on)
    Example: {"tool": "vision.look", "args": {"prompt": "What code is on this screen?"}}

11. **files.read** — Read a file
    Args: path (string, required)

12. **files.write** — Write/create a file (goes to sandbox by default)
    Args: path (string), content (string)

13. **files.list** — List directory contents
    Args: path (string, optional, defaults to sandbox)

14. **files.delete** — Delete a file
    Args: path (string, required)

15. **scripts.generate** — Generate and save a script
    Args: filename (string), content (string), language (string), description (string, optional)

16. **scripts.run** — Execute a Python script from sandbox
    Args: path (string, required), args (list of strings, optional)

17. **scripts.list** — List generated scripts
    Args: none

18. **weather.current** — Get current weather conditions
    Args: location (string, optional, auto-detected if omitted)
    Example: {"tool": "weather.current", "args": {"location": "London"}}

19. **weather.forecast** — Get weather forecast for upcoming days
    Args: location (string, optional), days (int, optional, default 3, max 3)

20. **memory.store** — Save an important fact to long-term memory
    Args: content (string, required), category (string, optional: personal/preference/schedule/work/technical)
    Example: {"tool": "memory.store", "args": {"content": "User prefers dark mode", "category": "preference"}}

21. **memory.recall** — Search long-term memories
    Args: query (string, optional), category (string, optional), limit (int, optional, default 10)

22. **memory.forget** — Delete a specific memory
    Args: id (int, required)

--- Raspberry Pi Worker Tools (requires Pi to be configured and reachable) ---

23. **pi.ping** — Check if the Raspberry Pi worker is reachable
    Args: none

24. **pi.system_info** — Get Pi system health (CPU, memory, disk, temperature, uptime)
    Args: check (string, optional: "all", "uptime", "cpu", "memory", "disk", "temp")
    Example: {"tool": "pi.system_info", "args": {"check": "all"}}

25. **pi.gpio_read** — Read a GPIO pin value on the Pi
    Args: pin (int, required, BCM pin number)
    Example: {"tool": "pi.gpio_read", "args": {"pin": 17}}

26. **pi.gpio_write** — Set a GPIO pin value on the Pi
    Args: pin (int, required), value (int, 0 or 1)
    Example: {"tool": "pi.gpio_write", "args": {"pin": 17, "value": 1}}

27. **pi.i2c_scan** — Scan I2C bus on the Pi for connected devices
    Args: bus (int, optional, default 1)

28. **pi.service_status** — Check if a systemd service is running on the Pi
    Args: service (string, required)
    Example: {"tool": "pi.service_status", "args": {"service": "ssh"}}

29. **pi.run_script** — Execute a script on the Pi (from allowed scripts directory)
    Args: script (string, filename), script_args (list, optional), timeout (int, optional)

30. **pi.picoclaw** — Send a natural language command to PicoClaw AI agent on the Pi
    PicoClaw can execute shell commands, manage files, search the web, and automate tasks on the Pi.
    This is your most powerful Pi tool — use it for complex tasks that don't fit a specific tool above.
    Has automatic fallback: Groq → Cerebras → Gemini → NVIDIA → GitHub → Ollama (local) → OpenAI (paid).
    Args: message (string, required — natural language instruction),
          provider (string, optional — force: "groq", "cerebras", "gemini", "nvidia", "github", "ollama", "openai", "openai-advanced"),
          timeout (int, optional, default 60)
    Example: {"tool": "pi.picoclaw", "args": {"message": "Check which processes are using the most CPU"}}
    Example: {"tool": "pi.picoclaw", "args": {"message": "Analyze system logs for errors", "provider": "openai-advanced"}}
    Use "openai-advanced" (GPT-4o) for complex analysis tasks. Default chain handles rate limits automatically.

31. **pi.picoclaw_cron** — Manage scheduled tasks on the Pi via PicoClaw
    Args: action (string: "list" or "add"), schedule (string, required for add — natural language schedule)
    Example: {"tool": "pi.picoclaw_cron", "args": {"action": "add", "schedule": "every day at 9am check disk usage and alert if above 80%"}}

RULES:
- Only output a tool call when the user's request clearly needs one.
- You can chain multiple tool calls by outputting multiple ```tool blocks.
- After a tool executes, you'll receive the result and should summarize it naturally as JARVIS.
- For dangerous operations (file deletes, script execution), confirm with the user first.
- Camera is OFF by default. Only use vision.look when the user explicitly asks you to see/look.
- When the user tells you something personal or asks you to remember something, use memory.store.
- Pi tools (pi.*) are only available when the Raspberry Pi is configured. Use pi.ping to check connectivity first.
- For hardware tasks (GPIO, I2C), always use the Pi tools — never attempt hardware access from the PC.
"""


# ────────────────────────── Tool Router ──────────────────────────

def _catch_all(func: Callable) -> Callable:
    """
    Decorator that catches all exceptions from tool handlers.
    Inspired by sukeesh/Jarvis's catch_all_exceptions pattern.
    Returns a structured error dict instead of raising.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyError as e:
            return {"error": f"Missing required argument: {e}", "error_type": "missing_arg"}
        except TypeError as e:
            return {"error": f"Invalid argument type: {e}", "error_type": "type_error"}
        except Exception as e:
            logger.error(f"Tool handler error: {e}", exc_info=True)
            return {"error": f"Tool execution failed: {e}", "error_type": "internal"}
    return wrapper


async def execute_tool(tool_name: str, args: dict) -> dict:
    """Execute a tool by name and return the result. Includes timing and error handling."""
    logger.info(f"Executing tool: {tool_name} with args: {args}")
    start_time = time.time()

    # Dispatch table
    tool_map = {
        # Notes
        "notes.add": _catch_all(lambda a: notes.add_note(a["content"], a.get("tag", "general"))),
        "notes.list": _catch_all(lambda a: {"notes": notes.list_notes(a.get("tag"), a.get("limit", 20))}),
        "notes.search": _catch_all(lambda a: {"notes": notes.search_notes(a["query"])}),
        "notes.delete": _catch_all(lambda a: {"success": notes.delete_note(a["id"])}),

        # Calendar
        "calendar.create": _catch_all(lambda a: calendar_tool.create_event(
            title=a["title"],
            start_time=a["start_time"],
            end_time=a.get("end_time"),
            description=a.get("description", ""),
            calendar=a.get("calendar", "personal"),
            location=a.get("location", "")
        )),
        "calendar.list": _catch_all(lambda a: {"events": calendar_tool.list_events(
            calendar=a.get("calendar"),
            days_ahead=a.get("days_ahead", 7)
        )}),
        "calendar.today": _catch_all(lambda a: {"events": calendar_tool.get_today_events()}),
        "calendar.delete": _catch_all(lambda a: {"success": calendar_tool.delete_event(a["id"])}),
        "calendar.export": _catch_all(lambda a: _export_calendar(a)),

        # Vision
        "vision.look": _catch_all(lambda a: _vision.capture_and_analyze(a.get("prompt", "Describe what you see."))),

        # Files
        "files.read": _catch_all(lambda a: files.read_file(a["path"])),
        "files.write": _catch_all(lambda a: files.write_file(a["path"], a["content"])),
        "files.list": _catch_all(lambda a: files.list_directory(a.get("path"))),
        "files.delete": _catch_all(lambda a: files.delete_file(a["path"])),

        # Scripts
        "scripts.generate": _catch_all(lambda a: scripts.generate_script(
            filename=a["filename"],
            content=a["content"],
            language=a.get("language", "python"),
            description=a.get("description", "")
        )),
        "scripts.run": _catch_all(lambda a: scripts.execute_python_script(a["path"], args=a.get("args"))),
        "scripts.list": _catch_all(lambda a: {"scripts": scripts.list_scripts()}),

        # Weather
        "weather.current": _catch_all(lambda a: weather.get_current_weather(a.get("location", ""))),
        "weather.forecast": _catch_all(lambda a: weather.get_forecast(a.get("location", ""), a.get("days", 3))),

        # Memory (new — inspired by Microsoft JARVIS + sukeesh/Jarvis)
        "memory.store": _catch_all(lambda a: _store_memory(a)),
        "memory.recall": _catch_all(lambda a: _recall_memory(a)),
        "memory.forget": _catch_all(lambda a: _forget_memory(a)),

        # Raspberry Pi worker tools
        "pi.ping": lambda a: _pi_execute("system_info", {"check": "uptime"}),
        "pi.system_info": lambda a: _pi_execute("system_info", a),
        "pi.gpio_read": lambda a: _pi_execute("gpio_read", a),
        "pi.gpio_write": lambda a: _pi_execute("gpio_write", a),
        "pi.i2c_scan": lambda a: _pi_execute("i2c_scan", a),
        "pi.service_status": lambda a: _pi_execute("service_status", a),
        "pi.run_script": lambda a: _pi_execute("run_script", a),
        "pi.picoclaw": lambda a: _pi_execute("picoclaw", a),
        "pi.picoclaw_cron": lambda a: _pi_execute("picoclaw_cron", a),
    }

    try:
        if tool_name in tool_map:
            handler = tool_map[tool_name]
            result = handler(args)
            if asyncio.iscoroutine(result):
                result = await result
            elapsed = time.time() - start_time
            success = "error" not in result if isinstance(result, dict) else True
            _log_execution(tool_name, args, result if isinstance(result, dict) else {}, elapsed, success)
            return result
        else:
            elapsed = time.time() - start_time
            result = {"error": f"Unknown tool: {tool_name}", "error_type": "unknown_tool"}
            _log_execution(tool_name, args, result, elapsed, False)
            return result

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Tool execution error [{tool_name}]: {e}", exc_info=True)
        result = {"error": str(e), "error_type": "execution_error"}
        _log_execution(tool_name, args, result, elapsed, False)
        return result


# ────────────────────────── Memory Tool Handlers ──────────────────────────

def _store_memory(args: dict) -> dict:
    from memory import store_memory
    return store_memory(
        content=args["content"],
        category=args.get("category", "general"),
        source="user_request",
        importance=args.get("importance", 2)
    )


def _recall_memory(args: dict) -> dict:
    from memory import recall_memories
    memories = recall_memories(
        query=args.get("query", ""),
        category=args.get("category"),
        limit=args.get("limit", 10)
    )
    return {"memories": memories, "count": len(memories)}


def _forget_memory(args: dict) -> dict:
    from memory import delete_memory
    success = delete_memory(args["id"])
    return {"success": success}


# ────────────────────────── Pi Worker ──────────────────────────

_pi_client = None


def _get_pi_client():
    """Lazy-initialize the Pi client singleton."""
    global _pi_client
    if _pi_client is None:
        try:
            from pi.config import get_pi_config, is_pi_enabled
            if not is_pi_enabled():
                return None
            from pi.client import PiClient
            _pi_client = PiClient(get_pi_config())
        except Exception as e:
            logger.warning(f"Pi client init failed: {e}")
            return None
    return _pi_client


async def _pi_execute(task_name: str, args: dict) -> dict:
    """Execute a task on the Raspberry Pi worker."""
    client = _get_pi_client()
    if client is None:
        return {"error": "Pi not configured. Set 'pi.host' in config.json.", "error_type": "config_error"}

    from pi.models import PiTask
    task = PiTask(task_name=task_name, args=args)
    result = await client.execute(task)

    if result.ok:
        return {
            "result": result.data if result.data else result.stdout,
            "elapsed_ms": result.elapsed_ms,
        }
    else:
        return {
            "error": result.stderr,
            "error_code": result.error_code,
            "elapsed_ms": result.elapsed_ms,
        }


# ────────────────────────── Helper Functions ──────────────────────────

def _export_calendar(args):
    ics = calendar_tool.export_ics(args.get("calendar"))
    from config import DATA_DIR
    path = DATA_DIR / f"calendar_{args.get('calendar', 'all')}.ics"
    path.write_text(ics)
    return {"success": True, "path": str(path), "events_exported": ics.count("BEGIN:VEVENT")}


def parse_tool_calls(text: str) -> list[dict]:
    """
    Extract tool call JSON blocks from LLM output.
    Looks for ```tool ... ``` blocks.
    """
    pattern = r'```tool\s*\n?(.*?)\n?\s*```'
    matches = re.findall(pattern, text, re.DOTALL)

    tool_calls = []
    for match in matches:
        try:
            data = json.loads(match.strip())
            if "tool" in data:
                tool_calls.append(data)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse tool call JSON: {e}")
    return tool_calls


def strip_tool_blocks(text: str) -> str:
    """Remove tool call blocks from text, leaving only the natural language response."""
    cleaned = re.sub(r'```tool\s*\n?.*?\n?\s*```', '', text, flags=re.DOTALL)
    return cleaned.strip()


def get_dashboard_data() -> dict:
    """Get aggregated data for the frontend dashboard tiles."""
    data = {
        "notes": notes.get_notes_summary(),
        "calendar": calendar_tool.get_calendar_summary(),
        "camera_active": _vision.is_active,
        "scripts": len(scripts.list_scripts()),
        "tool_stats": get_execution_stats(),
    }

    # Memory stats (graceful degradation)
    try:
        from memory import get_memory_summary
        data["memory"] = get_memory_summary()
    except Exception:
        data["memory"] = {"total_memories": 0}

    # Weather (non-blocking — uses cache, skip if fetch fails)
    try:
        data["weather"] = weather.get_weather_summary()
    except Exception:
        data["weather"] = {"available": False}

    # Pi worker status
    try:
        from pi.config import is_pi_enabled
        data["pi"] = {"enabled": is_pi_enabled()}
    except Exception:
        data["pi"] = {"enabled": False}

    return data
