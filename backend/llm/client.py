"""
Jarvis Protocol — Ollama LLM Client
Handles streaming chat completions from local Ollama server.

Improvements inspired by:
- Microsoft JARVIS: model availability checks, fallback to chitchat
- Priler/jarvis: pluggable backend pattern, health monitoring
"""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

import httpx

from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, MAX_CONTEXT_MESSAGES
from llm.prompts import build_messages

logger = logging.getLogger("jarvis.llm")

# Fallback models to try if primary isn't available
FALLBACK_MODELS = [
    "llama3.1:8b",
    "llama3:8b",
    "mistral:7b",
    "gemma2:9b",
    "phi3:mini",
]


class LLMClient:
    """Async client for local Ollama LLM inference with retry and fallback."""

    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.model = OLLAMA_MODEL
        self.conversation_history: list[dict] = []
        self._available_models: list[str] = []
        self._last_health_check: float = 0
        self._healthy: bool = False
        self._request_count: int = 0
        self._error_count: int = 0

    async def check_health(self) -> bool:
        """Check if Ollama server is reachable (cached for 30s)."""
        now = time.time()
        if now - self._last_health_check < 30 and self._healthy:
            return True

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/api/tags", timeout=5)
                self._healthy = resp.status_code == 200
                if self._healthy:
                    self._available_models = [
                        m["name"] for m in resp.json().get("models", [])
                    ]
                self._last_health_check = now
                return self._healthy
        except Exception:
            self._healthy = False
            return False

    async def check_model_available(self) -> bool:
        """Check if the configured model is pulled."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/api/tags", timeout=5)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    return any(m["name"].startswith(self.model.split(":")[0])
                               for m in models)
                return False
        except Exception:
            return False

    async def _find_available_model(self) -> Optional[str]:
        """Find the best available model, trying fallbacks if primary isn't available."""
        await self.check_health()

        # Check if primary model is available
        primary_base = self.model.split(":")[0]
        for available in self._available_models:
            if available.startswith(primary_base):
                return self.model

        # Try fallbacks
        for fallback in FALLBACK_MODELS:
            fallback_base = fallback.split(":")[0]
            for available in self._available_models:
                if available.startswith(fallback_base):
                    logger.warning(f"Primary model '{self.model}' unavailable, using fallback: {available}")
                    return available

        return None

    async def stream_response(self, user_input: str) -> AsyncGenerator[str, None]:
        """
        Stream a response from Ollama token by token.
        Yields each token as it arrives.
        """
        messages = build_messages(self.conversation_history, user_input)

        full_response = ""
        async for token in self._stream_with_retry(messages):
            full_response += token
            yield token

        # Store in conversation history and auto-trim to prevent unbounded growth
        self.conversation_history.append({"role": "user", "content": user_input})
        self.conversation_history.append({"role": "assistant", "content": full_response})
        self._auto_trim_history()

    async def get_response(self, user_input: str) -> str:
        """Get a complete (non-streaming) response."""
        full = ""
        async for token in self.stream_response(user_input):
            full += token
        return full

    async def stream_response_from_messages(self, messages: list[dict], save_to_history: bool = False) -> AsyncGenerator[str, None]:
        """
        Stream a response from pre-built messages.
        Used for tool result summarization where we need custom message structure.
        If save_to_history is True, appends the full response to conversation_history.
        """
        full_response = ""
        async for token in self._stream_with_retry(messages):
            full_response += token
            yield token

        if save_to_history and full_response:
            self.conversation_history.append({"role": "assistant", "content": full_response})
            self._auto_trim_history()

    async def _stream_with_retry(self, messages: list[dict], max_retries: int = 2) -> AsyncGenerator[str, None]:
        """Stream response with automatic retry on transient failures."""
        self._request_count += 1

        # Check if we need to find an available model
        model = self.model
        if self._error_count > 2:
            available = await self._find_available_model()
            if available:
                model = available
            else:
                yield "I'm unable to connect to the language core, sir. Please ensure Ollama is running."
                return

        for attempt in range(max_retries + 1):
            try:
                token_count = 0
                async for token in self._do_stream(messages, model):
                    token_count += 1
                    yield token

                # Success — reset error counter
                if token_count > 0:
                    self._error_count = 0
                return

            except httpx.ConnectError:
                self._error_count += 1
                if attempt < max_retries:
                    wait = (attempt + 1) * 2
                    logger.warning(f"Ollama connection failed, retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                else:
                    logger.error("Cannot connect to Ollama after retries")
                    yield "Connection to the language core has been lost, sir. I'll keep trying."

            except httpx.TimeoutException:
                self._error_count += 1
                if attempt < max_retries:
                    logger.warning(f"LLM request timed out, retrying (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)
                else:
                    yield "I apologize, sir. The request took longer than expected."

            except Exception as e:
                self._error_count += 1
                logger.error(f"LLM error (attempt {attempt + 1}): {e}")
                if attempt >= max_retries:
                    yield "An issue occurred with the language core, sir."

    async def _do_stream(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        """Perform the actual streaming request to Ollama."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": 1024,
            }
        }

        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=OLLAMA_TIMEOUT
            ) as response:
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                        if data.get("done", False):
                            break
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse stream chunk: {e}")
                        continue

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history.clear()
        logger.info("Conversation history cleared")

    def _auto_trim_history(self):
        """Trim conversation_history to prevent unbounded memory growth.
        Keeps the most recent MAX_CONTEXT_MESSAGES * 2 entries (generous buffer
        since build_messages already trims what's sent to the LLM).
        """
        max_entries = MAX_CONTEXT_MESSAGES * 2  # 40 entries = 20 user/assistant pairs
        if len(self.conversation_history) > max_entries:
            trimmed = len(self.conversation_history) - max_entries
            self.conversation_history = self.conversation_history[-max_entries:]
            logger.debug(f"Auto-trimmed {trimmed} old messages from conversation_history")

    def get_stats(self) -> dict:
        """Get LLM client statistics."""
        return {
            "model": self.model,
            "healthy": self._healthy,
            "available_models": self._available_models,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "history_length": len(self.conversation_history),
        }
