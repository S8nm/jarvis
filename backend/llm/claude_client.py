"""
Jarvis Protocol — Claude LLM Client

Streaming client for Claude API, matching the Ollama LLMClient interface.
Used when the router selects Claude as the primary backend.

Interface parity with LLMClient:
  - stream_response(user_input) -> AsyncGenerator[str, None]
  - stream_response_from_messages(messages) -> AsyncGenerator[str, None]
  - check_health() -> bool
"""
import logging
from typing import AsyncGenerator

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, MAX_CONTEXT_MESSAGES
from llm.prompts import JARVIS_SYSTEM_PROMPT

logger = logging.getLogger("jarvis.llm.claude")

# Re-use the system prompt from the Ollama path for persona consistency
_SYSTEM_BLOCKS = [
    {
        "type": "text",
        "text": JARVIS_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


class ClaudeLLMClient:
    """Async streaming client for Claude API as a primary LLM backend."""

    def __init__(self, cost_tracker=None):
        self._client = None
        self.model = CLAUDE_MODEL
        self._cost_tracker = cost_tracker
        self._request_count = 0
        self._error_count = 0

    def _get_client(self):
        if self._client is None:
            if not ANTHROPIC_API_KEY:
                return None
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        return self._client

    async def check_health(self) -> bool:
        """Check if Claude API is reachable."""
        if not ANTHROPIC_API_KEY:
            return False
        try:
            client = self._get_client()
            if client is None:
                return False
            # Light check — just verify we can create a client
            return True
        except Exception:
            return False

    async def stream_response(self, user_input: str, conversation_history: list[dict] = None) -> AsyncGenerator[str, None]:
        """
        Stream Claude response token by token.
        Matches the Ollama LLMClient.stream_response() interface.
        """
        messages = []
        if conversation_history:
            # Convert to Claude format: only role + content
            for msg in conversation_history[-(MAX_CONTEXT_MESSAGES * 2):]:
                if msg.get("role") in ("user", "assistant") and msg.get("content"):
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })

        messages.append({"role": "user", "content": user_input})

        async for token in self._stream_messages(messages):
            yield token

    async def stream_response_from_messages(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """
        Stream response from pre-built messages.
        Matches the Ollama LLMClient.stream_response_from_messages() interface.
        """
        # Messages may include a system message as first element — extract it
        system_text = ""
        chat_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_text = msg.get("content", "")
            else:
                chat_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        async for token in self._stream_messages(chat_messages, system_override=system_text):
            yield token

    async def _stream_messages(self, messages: list[dict], system_override: str = "") -> AsyncGenerator[str, None]:
        """Core streaming method — sends messages to Claude and yields tokens."""
        self._request_count += 1

        client = self._get_client()
        if client is None:
            yield "Claude API key not configured, sir. Please add it to config.json."
            return

        system = _SYSTEM_BLOCKS
        if system_override:
            system = [{"type": "text", "text": system_override, "cache_control": {"type": "ephemeral"}}]

        try:
            async with client.messages.stream(
                model=self.model,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text

                # Get usage for cost tracking (inside async with to avoid stale stream)
                response = await stream.get_final_message()
                if self._cost_tracker and response.usage:
                    self._cost_tracker.log_usage(
                        model=response.model,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        cache_read=getattr(response.usage, "cache_read_input_tokens", 0),
                        cache_creation=getattr(response.usage, "cache_creation_input_tokens", 0),
                        request_type="stream",
                        summary="router-direct",
                    )
            self._error_count = 0

        except Exception as e:
            self._error_count += 1
            logger.error(f"Claude streaming error: {e}")
            yield f"I encountered an issue with the Claude backend, sir. {e}"

    async def close(self):
        """Clean up the Anthropic client."""
        if self._client:
            await self._client.close()
            self._client = None

    def get_stats(self) -> dict:
        return {
            "model": self.model,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "api_key_configured": bool(ANTHROPIC_API_KEY),
        }
