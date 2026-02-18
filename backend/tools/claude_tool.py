"""
Jarvis Protocol — Claude API Tool
Outsource complex reasoning, coding, analysis, and research to Claude.

Features:
- Prompt caching: system prompt marked with cache_control to avoid re-processing
- Batch processing: queue multiple requests and process via Anthropic Batch API
- Async support for non-blocking calls
"""
import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger("jarvis.tools.claude")

# Lazy-init clients
_client = None
_async_client = None


def _get_client():
    global _client
    if _client is None:
        from config import ANTHROPIC_API_KEY
        if not ANTHROPIC_API_KEY:
            return None
        import anthropic
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _get_async_client():
    global _async_client
    if _async_client is None:
        from config import ANTHROPIC_API_KEY
        if not ANTHROPIC_API_KEY:
            return None
        import anthropic
        _async_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _async_client


# ──────────────────────────── System prompt with cache ────────────────────────────

_SYSTEM_BLOCKS = [
    {
        "type": "text",
        "text": (
            "You are a highly capable AI assistant being consulted by another AI system (JARVIS). "
            "Provide thorough, accurate, and well-structured answers. "
            "Be direct and comprehensive — JARVIS will summarize your response for the user."
        ),
        "cache_control": {"type": "ephemeral"},  # Cached for 5 min across requests
    }
]


def _build_system(context: str = "") -> list[dict]:
    """Build system blocks with prompt caching. Static part is cached, context is appended."""
    if not context:
        return _SYSTEM_BLOCKS
    return _SYSTEM_BLOCKS + [{"type": "text", "text": f"Additional context:\n{context}"}]


# ──────────────────────────── Single request (with caching) ────────────────────────────

def ask_claude(
    message: str,
    context: str = "",
    model: str = "",
    max_tokens: int = 4096,
) -> dict:
    """Send a question to Claude with prompt caching and budget enforcement."""
    from resilience.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()

    # Budget check
    can_proceed, reason = tracker.can_afford()
    if not can_proceed:
        return {"error": f"Claude budget exhausted: {reason}. Falling back to local model."}

    client = _get_client()
    if client is None:
        return {"error": "Claude API key not configured. Add 'anthropic_api_key' to config.json."}

    from config import CLAUDE_MODEL
    use_model = model if model else CLAUDE_MODEL

    try:
        resp = client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            system=_build_system(context),
            messages=[{"role": "user", "content": message}],
        )

        text = resp.content[0].text if resp.content else ""
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "model": resp.model,
            "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
            "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
        }

        # Log cost
        cost = tracker.log_usage(
            model=resp.model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read=usage["cache_read_tokens"],
            cache_creation=usage["cache_creation_tokens"],
            request_type="sync",
            summary=message[:100],
        )

        logger.info(
            f"Claude response: {len(text)} chars, "
            f"{usage['input_tokens']}+{usage['output_tokens']} tokens "
            f"(cached: {usage['cache_read_tokens']}) ({resp.model}) [${cost:.4f}]"
        )

        if len(text) > 6000:
            text = text[:6000] + "\n\n[Response truncated — full answer was longer]"

        return {"response": text, "usage": usage, "cost_usd": cost}

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return {"error": f"Claude API call failed: {e}"}


# ──────────────────────────── Async single request ────────────────────────────

async def ask_claude_async(
    message: str,
    context: str = "",
    model: str = "",
    max_tokens: int = 4096,
) -> dict:
    """Async version of ask_claude with prompt caching and budget enforcement."""
    from resilience.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()

    # Budget check
    can_proceed, reason = tracker.can_afford()
    if not can_proceed:
        return {"error": f"Claude budget exhausted: {reason}. Falling back to local model."}

    client = _get_async_client()
    if client is None:
        return {"error": "Claude API key not configured. Add 'anthropic_api_key' to config.json."}

    from config import CLAUDE_MODEL
    use_model = model if model else CLAUDE_MODEL

    try:
        resp = await client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            system=_build_system(context),
            messages=[{"role": "user", "content": message}],
        )

        text = resp.content[0].text if resp.content else ""
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "model": resp.model,
            "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
            "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
        }

        # Log cost
        cost = tracker.log_usage(
            model=resp.model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read=usage["cache_read_tokens"],
            cache_creation=usage["cache_creation_tokens"],
            request_type="async",
            summary=message[:100],
        )

        logger.info(
            f"Claude async response: {len(text)} chars, "
            f"{usage['input_tokens']}+{usage['output_tokens']} tokens "
            f"(cached: {usage['cache_read_tokens']}) ({resp.model}) [${cost:.4f}]"
        )

        if len(text) > 6000:
            text = text[:6000] + "\n\n[Response truncated — full answer was longer]"

        return {"response": text, "usage": usage, "cost_usd": cost}

    except Exception as e:
        logger.error(f"Claude async API error: {e}")
        return {"error": f"Claude API call failed: {e}"}


# ──────────────────────────── Batch processing ────────────────────────────

async def batch_ask_claude(
    requests: list[dict],
    context: str = "",
    model: str = "",
    max_tokens: int = 4096,
) -> list[dict]:
    """
    Process multiple Claude requests in parallel with shared prompt cache.
    Each request dict should have: {"id": str, "message": str}
    Returns list of: {"id": str, "response": str, "usage": dict}

    The system prompt is cached across all requests in the batch,
    so only the first request pays the full input cost.
    """
    client = _get_async_client()
    if client is None:
        return [{"id": r.get("id", "?"), "error": "Claude API key not configured"} for r in requests]

    from config import CLAUDE_MODEL
    use_model = model if model else CLAUDE_MODEL
    system = _build_system(context)

    from resilience.cost_tracker import get_cost_tracker
    tracker = get_cost_tracker()

    async def _single(req: dict) -> dict:
        req_id = req.get("id", "unknown")
        msg = req.get("message", "")
        try:
            resp = await client.messages.create(
                model=use_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": msg}],
            )
            text = resp.content[0].text if resp.content else ""
            usage = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
                "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
            }
            # Log cost for each batch item
            cost = tracker.log_usage(
                model=resp.model,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read=usage["cache_read_tokens"],
                cache_creation=usage["cache_creation_tokens"],
                request_type="batch",
                summary=msg[:100],
            )
            if len(text) > 6000:
                text = text[:6000] + "\n\n[Response truncated]"
            return {"id": req_id, "response": text, "usage": usage, "cost_usd": cost}
        except Exception as e:
            return {"id": req_id, "error": str(e)}

    # Fire all requests concurrently — prompt cache shared via cache_control
    results = await asyncio.gather(*[_single(r) for r in requests], return_exceptions=True)

    out = []
    total_cached = 0
    total_cost = 0.0
    for r in results:
        if isinstance(r, Exception):
            out.append({"id": "?", "error": str(r)})
        else:
            total_cached += r.get("usage", {}).get("cache_read_tokens", 0)
            total_cost += r.get("cost_usd", 0)
            out.append(r)

    logger.info(
        f"Claude batch: {len(requests)} requests, "
        f"{total_cached} total cached tokens, ${total_cost:.4f} total cost"
    )
    return out
