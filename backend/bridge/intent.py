"""
PersonaPlex Bridge — Intent Detection (v2)
Detects tool-calling intent from USER input (not JARVIS output).
Uses strict multi-word phrases to avoid false positives.
Falls back to Ollama only when a high-confidence phrase is detected.
"""
import json
import logging
import re
import aiohttp

from bridge.config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger("jarvis.bridge.intent")

# High-confidence intent phrases — these are multi-word or specific enough
# to avoid matching normal conversation. Each maps to a likely tool category.
_INTENT_PHRASES = {
    # Weather (must have "weather" or explicit outdoor-temp phrasing)
    r"\b(?:what(?:'s| is) the weather|weather (?:today|tomorrow|forecast|like)|how(?:'s| is) (?:it|the weather) outside|temperature outside)\b": "weather",
    # Calendar / schedule
    r"\b(?:what(?:'s| is) on my (?:calendar|schedule)|add (?:to|an?) (?:my )?(?:calendar|schedule)|create (?:an? )?event|my (?:calendar|schedule) (?:today|tomorrow|this week))\b": "calendar",
    r"\b(?:do i have (?:any )?(?:events|meetings)|what(?:'s| is) (?:today|tomorrow)(?:'s)? schedule)\b": "calendar",
    # Notes / reminders
    r"\b(?:(?:add|create|make|take|write) (?:a )?note|remind me|(?:my|list|show) (?:all )?notes|note(?:s)? about|delete (?:the |my )?note)\b": "notes",
    # Raspberry Pi (very specific terms)
    r"\b(?:raspberry pi|pi (?:status|info|health|temperature|temp)|gpio|i2c|picoclaw|pi worker|is the pi (?:online|running|up))\b": "pi",
    r"\b(?:turn (?:on|off) (?:the )?(?:led|pin|gpio)|check (?:the )?pi)\b": "pi",
    # Vision / camera
    r"\b(?:look at (?:this|that)|what (?:do you |can you )?see|activate (?:the )?camera|take a (?:look|photo|picture))\b": "vision",
    # Files / scripts
    r"\b(?:create (?:a )?(?:file|script)|run (?:the |my )?script|generate (?:a )?(?:script|code)|list (?:my )?files|read (?:the )?file)\b": "files",
    # Memory
    r"\b(?:remember (?:that|this)|do you remember|what do you know about|forget (?:about|that))\b": "memory",
}

# Compiled patterns for speed
_COMPILED_INTENTS = [(re.compile(pat, re.IGNORECASE), cat) for pat, cat in _INTENT_PHRASES.items()]


def detect_tool_intent(user_text: str, jarvis_text: str = "") -> str | None:
    """Check if user's text contains a tool-calling intent.

    Returns the tool category string if intent detected, None otherwise.
    Checks user text first (primary), then JARVIS text for action patterns.
    """
    # Primary: check user's actual request
    for pattern, category in _COMPILED_INTENTS:
        if pattern.search(user_text):
            logger.info(f"Intent detected in user text: category={category}")
            return category

    # Secondary: check if JARVIS is promising to do something it can't
    if jarvis_text:
        jarvis_action_patterns = [
            (r"let me (?:check|look up|pull up|get) (?:the |your )?(?:weather|calendar|notes|schedule|pi)", None),
            (r"i(?:'ll| will) (?:check|look up|pull up|get|set up|create) (?:the |your |that |a )?", None),
            (r"checking (?:the |your )?(?:weather|notes|calendar|schedule|pi)", None),
            (r"activating (?:the )?camera", "vision"),
        ]
        for pat, cat in jarvis_action_patterns:
            if re.search(pat, jarvis_text, re.IGNORECASE):
                # If a category was specified use it, otherwise try to guess from context
                if cat:
                    return cat
                # Try matching user text categories more loosely now
                for upattern, ucategory in _COMPILED_INTENTS:
                    if upattern.search(user_text + " " + jarvis_text):
                        return ucategory
                return "general"

    return None


async def extract_tool_call(user_text: str, category: str = "general") -> dict | None:
    """Use Ollama to extract a structured tool call from user's natural language.

    Args:
        user_text: What the USER said (not what JARVIS said)
        category: The detected intent category for context

    Returns:
        Dict with 'tool' and 'args' keys, or None if no tool call detected
    """
    # Map categories to relevant tools to narrow the LLM's choices
    category_tools = {
        "weather": "weather.current, weather.forecast",
        "calendar": "calendar.create, calendar.list, calendar.today, calendar.delete",
        "notes": "notes.add, notes.list, notes.search, notes.delete",
        "pi": "pi.ping, pi.system_info, pi.gpio_read, pi.gpio_write, pi.i2c_scan, pi.service_status, pi.run_script, pi.picoclaw, pi.picoclaw_cron",
        "vision": "vision.look",
        "files": "files.read, files.write, files.list, files.delete, scripts.generate, scripts.run, scripts.list",
        "memory": "memory.store, memory.recall, memory.forget",
        "general": "weather.current, weather.forecast, notes.add, notes.list, notes.search, calendar.create, calendar.list, calendar.today, vision.look, files.read, files.write, files.list, scripts.generate, scripts.run, memory.store, memory.recall, pi.ping, pi.system_info, pi.gpio_read, pi.gpio_write, pi.picoclaw",
    }
    tools = category_tools.get(category, category_tools["general"])

    prompt = f"""You are a tool-call extractor. The user said the following to their AI assistant JARVIS. Determine the best tool to execute their request.

Available tools: {tools}

Tool argument formats:
- weather.current: {{"location": "city"}} (optional, defaults to configured location)
- weather.forecast: {{"location": "city", "days": 3}}
- notes.add: {{"content": "...", "tag": "personal"}}
- notes.list: {{"tag": "optional_filter"}}
- notes.search: {{"query": "search text"}}
- notes.delete: {{"id": number}}
- calendar.create: {{"title": "...", "start_time": "...", "calendar": "personal"}}
- calendar.list: {{"days": 7, "calendar": "all"}}
- calendar.today: {{}}
- vision.look: {{"prompt": "describe what to look for"}}
- files.read: {{"path": "filename"}}
- files.write: {{"path": "filename", "content": "..."}}
- files.list: {{"path": "."}}
- scripts.generate: {{"filename": "...", "content": "...", "language": "python"}}
- scripts.run: {{"filename": "..."}}
- memory.store: {{"content": "fact to remember", "category": "general"}}
- memory.recall: {{"query": "what to look up"}}
- memory.forget: {{"query": "what to forget"}}
- pi.ping: {{}}
- pi.system_info: {{"check": "all"}}
- pi.gpio_read: {{"pin": number}}
- pi.gpio_write: {{"pin": number, "value": 0_or_1}}
- pi.picoclaw: {{"message": "natural language command"}}

User said: "{user_text}"

Output ONLY a JSON object with "tool" and "args" keys. No explanation. If unclear, output "NONE"."""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 200},
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Ollama returned {resp.status}")
                    return None
                data = await resp.json()
                response = data.get("response", "").strip()

        if not response or response.upper() == "NONE":
            return None

        # Parse JSON — handle markdown wrapping
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            tool_call = json.loads(json_match.group())
            if "tool" in tool_call:
                logger.info(f"Extracted tool call: {tool_call}")
                return tool_call

    except Exception as e:
        logger.error(f"Tool extraction failed: {e}")

    return None
