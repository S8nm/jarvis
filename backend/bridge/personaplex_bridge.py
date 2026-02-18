"""
PersonaPlex Bridge — WebSocket Proxy (v2)
Sits between the frontend client and PersonaPlex server.
Transparently passes audio frames, intercepts text tokens for tool routing.

Key change in v2: Tracks BOTH user text and JARVIS text separately.
Intent detection runs on user text (what was asked), not JARVIS text (what was said).

Protocol:
  0x00 = handshake (server → client, once)
  0x01 = audio (bidirectional, Opus encoded)
  0x02 = text tokens (server → client, UTF-8)
  0x03 = control (bidirectional: start, endTurn, pause, restart)
  0x04 = metadata (JSON encoded)
  0x05 = error (UTF-8 encoded)
  0x06 = ping
"""
import asyncio
from datetime import datetime
import json
import logging
import ssl
import time
import urllib.parse

import aiohttp
from aiohttp import web

from bridge.config import (
    PERSONAPLEX_HOST,
    PERSONAPLEX_PORT,
    PERSONAPLEX_SSL,
    PERSONAPLEX_SSL_CERT,
    BRIDGE_PORT,
    VOICE_PROMPT,
    TEXT_PERSONA,
    INTENT_BUFFER_TIMEOUT_SEC,
)
from bridge.intent import detect_tool_intent, extract_tool_call

logger = logging.getLogger("jarvis.bridge")

# Message kinds
KIND_HANDSHAKE = 0x00
KIND_AUDIO = 0x01
KIND_TEXT = 0x02
KIND_CONTROL = 0x03
KIND_METADATA = 0x04
KIND_ERROR = 0x05
KIND_PING = 0x06


class PersonaPlexBridge:
    """WebSocket proxy between client and PersonaPlex server with tool interception."""

    def __init__(self, tool_executor=None, agent=None, broadcast=None):
        """
        Args:
            tool_executor: async callable(tool_name, args) -> dict
                           Typically tools.registry.execute_tool
            agent: JarvisAgent instance — used to sync voice conversations to backend
            broadcast: async callable(message_str) — sends JSON to all frontend clients
        """
        self.tool_executor = tool_executor
        self.agent = agent
        self.broadcast = broadcast
        self._app = None
        self._runner = None
        self._voice_active = False

    async def _notify_frontend(self, msg_type: str, data: dict):
        """Send a message to all connected frontend clients via the main JARVIS WebSocket."""
        if not self.broadcast:
            return
        try:
            import json
            from datetime import datetime
            message = json.dumps({
                "type": msg_type,
                "data": data,
                "timestamp": datetime.now().isoformat()
            }, default=str)
            await self.broadcast(message)
        except Exception as e:
            logger.debug(f"Frontend notify failed: {e}")

    async def start(self, port: int = BRIDGE_PORT):
        """Start the bridge proxy server."""
        self._app = web.Application()
        self._app.router.add_get("/api/chat", self._handle_client)
        self._app.router.add_get("/health", self._health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"PersonaPlex Bridge running on port {port}")

    async def stop(self):
        """Stop the bridge proxy."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("PersonaPlex Bridge stopped")

    async def _health(self, request):
        return web.json_response({"status": "ok", "service": "personaplex-bridge"})

    async def _handle_client(self, request):
        """Handle an incoming WebSocket connection from the frontend."""
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)
        logger.info(f"Client connected from {request.remote}")

        # Notify backend that voice is now active
        self._voice_active = True
        if self.agent:
            self.agent.personaplex_active = True
        await self._notify_frontend("personaplex_status", {"active": True, "status": "connecting"})

        # Build PersonaPlex server URL with query params
        params = {
            "voice_prompt": request.query.get("voice_prompt", VOICE_PROMPT),
            "text_prompt": request.query.get("text_prompt", TEXT_PERSONA),
        }
        if "seed" in request.query:
            params["seed"] = request.query["seed"]

        query = urllib.parse.urlencode(params)
        scheme = "wss" if PERSONAPLEX_SSL else "ws"
        server_url = f"{scheme}://{PERSONAPLEX_HOST}:{PERSONAPLEX_PORT}/api/chat?{query}"

        # Connect to PersonaPlex server
        ssl_ctx = None
        if PERSONAPLEX_SSL:
            ssl_ctx = ssl.create_default_context()
            if PERSONAPLEX_SSL_CERT:
                import os
                if os.path.exists(PERSONAPLEX_SSL_CERT):
                    ssl_ctx = ssl.create_default_context(cafile=PERSONAPLEX_SSL_CERT)
                else:
                    logger.warning(f"PersonaPlex SSL cert not found: {PERSONAPLEX_SSL_CERT}, using system trust store")
            else:
                # Local self-signed: disable verification (logged as warning)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                logger.warning("PersonaPlex SSL: No cert path configured, verification disabled for self-signed cert")

        # Connect to PersonaPlex with retry logic
        session = None
        server_ws = None
        max_retries = 3
        retry_delays = [1, 3, 5]

        for attempt in range(max_retries):
            try:
                session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
                server_ws = await session.ws_connect(server_url, ssl=ssl_ctx)
                logger.info(f"Connected to PersonaPlex server (attempt {attempt + 1})")
                break
            except Exception as e:
                logger.warning(f"PersonaPlex connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if session:
                    await session.close()
                    session = None
                if attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    await self._notify_frontend("personaplex_status", {
                        "active": True, "status": "reconnecting",
                        "message": f"Retrying in {delay}s... (attempt {attempt + 2}/{max_retries})"
                    })
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Failed to connect to PersonaPlex after {max_retries} attempts")
                    await client_ws.close(message=b"PersonaPlex server unreachable after retries")
                    return client_ws

        # Shared state for this connection
        close_event = asyncio.Event()

        # Separate buffers for user speech and JARVIS speech
        user_text_buffer = []      # Transcription of what user said (from client 0x02)
        jarvis_text_buffer = []    # What PersonaPlex/JARVIS generated (from server 0x02)
        buffer_lock = asyncio.Lock()
        last_jarvis_text_time = [0.0]
        # Track when user was last speaking (audio frames from client)
        last_user_audio_time = [0.0]

        # Turn tracking: accumulate full turns for backend sync
        current_user_turn = []
        current_jarvis_turn = []

        client_audio_count = [0]

        async def client_to_server():
            """Forward client messages to PersonaPlex server."""
            try:
                async for msg in client_ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        data = msg.data
                        if len(data) > 0:
                            kind = data[0]
                            if kind == KIND_AUDIO:
                                client_audio_count[0] += 1
                                last_user_audio_time[0] = time.time()
                                if client_audio_count[0] <= 5:
                                    logger.info(f"Client audio #{client_audio_count[0]}: {len(data)} bytes")
                            elif kind == KIND_TEXT:
                                # Client is sending transcribed text (if frontend does STT)
                                text = data[1:].decode("utf-8", errors="replace")
                                async with buffer_lock:
                                    user_text_buffer.append(text)
                                    current_user_turn.append(text)
                        await server_ws.send_bytes(data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                close_event.set()

        async def server_to_client():
            """Forward PersonaPlex messages to client, intercepting text tokens."""
            try:
                async for msg in server_ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        data = msg.data
                        if len(data) == 0:
                            continue

                        kind = data[0]

                        if kind == KIND_HANDSHAKE:
                            logger.info("Handshake from PersonaPlex → forwarding to client")
                            await client_ws.send_bytes(data)
                            await self._notify_frontend("personaplex_status", {"active": True, "status": "connected"})
                            await self._notify_frontend("state_change", {"state": "LISTENING"})

                        elif kind == KIND_AUDIO:
                            # Pass audio through unchanged
                            await client_ws.send_bytes(data)

                        elif kind == KIND_TEXT:
                            # Intercept JARVIS text token
                            text = data[1:].decode("utf-8", errors="replace")
                            async with buffer_lock:
                                jarvis_text_buffer.append(text)
                                current_jarvis_turn.append(text)
                                last_jarvis_text_time[0] = time.time()
                            # Still forward text to client for display
                            await client_ws.send_bytes(data)
                            # Also stream to main conversation panel
                            await self._notify_frontend("response_chunk", {"token": text, "source": "personaplex"})

                        else:
                            # Control, metadata, error, ping — pass through
                            await client_ws.send_bytes(data)

                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                close_event.set()

        async def intent_monitor():
            """Periodically check buffered text for tool-calling intent.
            Uses USER text for intent detection, not JARVIS text.
            """
            while not close_event.is_set():
                await asyncio.sleep(0.3)

                async with buffer_lock:
                    if not jarvis_text_buffer and not user_text_buffer:
                        continue

                    # Wait for a pause in JARVIS text generation
                    if last_jarvis_text_time[0] > 0:
                        elapsed = time.time() - last_jarvis_text_time[0]
                        if elapsed < INTENT_BUFFER_TIMEOUT_SEC:
                            continue

                    # Grab and clear buffers
                    user_accumulated = "".join(user_text_buffer)
                    jarvis_accumulated = "".join(jarvis_text_buffer)
                    user_text_buffer.clear()
                    jarvis_text_buffer.clear()

                if not jarvis_accumulated.strip() and not user_accumulated.strip():
                    continue

                # Detect intent from USER text primarily, JARVIS text secondarily
                category = detect_tool_intent(user_accumulated, jarvis_accumulated)
                if not category:
                    # Sync turn to backend even without tool intent
                    await _sync_turn_to_backend(user_accumulated, jarvis_accumulated)
                    continue

                logger.info(f"Tool intent detected: category={category}, user='{user_accumulated[:80]}', jarvis='{jarvis_accumulated[:80]}'")

                # Use Ollama to extract structured tool call from user's words
                # If no user text, try extracting from the combined context
                extract_text = user_accumulated if user_accumulated.strip() else jarvis_accumulated
                tool_call = await extract_tool_call(extract_text, category)

                if tool_call and self.tool_executor:
                    tool_name = tool_call.get("tool", "")
                    tool_args = tool_call.get("args", {})
                    logger.info(f"Executing tool: {tool_name}({tool_args})")

                    try:
                        result = await self.tool_executor(tool_name, tool_args)
                        result_text = _format_tool_result(tool_name, result)
                        result_msg = b"\x02" + result_text.encode("utf-8")
                        await client_ws.send_bytes(result_msg)
                        logger.info(f"Tool result sent: {result_text[:100]}...")

                        # Sync the tool interaction to backend
                        await _sync_turn_to_backend(
                            user_accumulated,
                            jarvis_accumulated + f"\n[Tool: {tool_name} → {result_text}]"
                        )
                    except Exception as e:
                        logger.error(f"Tool execution failed: {e}")
                        error_msg = b"\x02" + f" [Tool error: {e}]".encode("utf-8")
                        await client_ws.send_bytes(error_msg)
                else:
                    # No tool extracted — still sync the turn
                    await _sync_turn_to_backend(user_accumulated, jarvis_accumulated)

        async def _sync_turn_to_backend(user_text: str, jarvis_text: str):
            """Sync a voice conversation turn to the backend agent for context continuity."""
            if not self.agent:
                return
            try:
                if user_text.strip():
                    self.agent.conversation_log.append({
                        "role": "user",
                        "content": user_text.strip(),
                        "timestamp": datetime.now().isoformat(),
                        "source": "voice_personaplex",
                    })
                    self.agent.llm.conversation_history.append({
                        "role": "user",
                        "content": user_text.strip(),
                    })
                if jarvis_text.strip():
                    self.agent.conversation_log.append({
                        "role": "assistant",
                        "content": jarvis_text.strip(),
                        "timestamp": datetime.now().isoformat(),
                        "source": "voice_personaplex",
                    })
                    self.agent.llm.conversation_history.append({
                        "role": "assistant",
                        "content": jarvis_text.strip(),
                    })
                # Notify frontend: turn complete, update conversation
                await self._notify_frontend("response_complete", {
                    "conversation": self.agent.conversation_log
                })
            except Exception as e:
                logger.debug(f"Backend sync failed (non-critical): {e}")

        # Run all three loops concurrently
        tasks = [
            asyncio.create_task(client_to_server()),
            asyncio.create_task(server_to_client()),
            asyncio.create_task(intent_monitor()),
        ]

        try:
            await close_event.wait()
        finally:
            # Sync any remaining buffered text before closing
            async with buffer_lock:
                remaining_user = "".join(user_text_buffer)
                remaining_jarvis = "".join(jarvis_text_buffer)
                user_text_buffer.clear()
                jarvis_text_buffer.clear()
            if remaining_user.strip() or remaining_jarvis.strip():
                await _sync_turn_to_backend(remaining_user, remaining_jarvis)

            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await server_ws.close()
            await session.close()
            await client_ws.close()

            # Notify frontend that voice session ended
            self._voice_active = False
            if self.agent:
                self.agent.personaplex_active = False
            await self._notify_frontend("personaplex_status", {"active": False, "status": "disconnected"})
            await self._notify_frontend("state_change", {"state": "IDLE"})
            # Send updated conversation to frontend
            if self.agent:
                await self._notify_frontend("response_complete", {"conversation": self.agent.conversation_log})
            logger.info("Bridge session closed")

        return client_ws


def _format_tool_result(tool_name: str, result: dict, max_len: int = 500) -> str:
    """Format a tool result as natural conversational text.

    Instead of raw JSON, produces speech-friendly summaries that PersonaPlex
    can display and the user can read naturally.
    """
    if "error" in result:
        return f" I encountered an issue with that: {result['error'][:200]}."

    data = result.get("result", result)

    # Weather — extract key fields
    if "weather" in tool_name:
        if isinstance(data, dict):
            temp = data.get("temperature", data.get("temp", ""))
            desc = data.get("description", data.get("condition", data.get("weather", "")))
            location = data.get("location", data.get("city", ""))
            humidity = data.get("humidity", "")
            parts = []
            if location:
                parts.append(f"In {location}")
            if temp:
                parts.append(f"it's currently {temp}°")
            if desc:
                parts.append(f"with {desc.lower()}" if parts else str(desc))
            if humidity:
                parts.append(f"Humidity is {humidity}%")
            if parts:
                return " " + ". ".join(parts) + "."
        if isinstance(data, str):
            return f" {data[:max_len]}"

    # Notes — list or confirmation
    if "notes" in tool_name:
        if isinstance(data, list):
            if not data:
                return " You don't have any notes matching that."
            count = len(data)
            preview = ", ".join(
                n.get("content", str(n))[:60] for n in data[:3]
            )
            suffix = f" and {count - 3} more" if count > 3 else ""
            return f" You have {count} note{'s' if count != 1 else ''}: {preview}{suffix}."
        if isinstance(data, dict) and "id" in data:
            return f" Note saved successfully."
        if isinstance(data, str):
            return f" {data[:max_len]}"

    # Calendar
    if "calendar" in tool_name:
        if isinstance(data, list):
            if not data:
                return " Your calendar is clear."
            count = len(data)
            events = []
            for ev in data[:3]:
                title = ev.get("title", ev.get("summary", "event"))
                start = ev.get("start_time", ev.get("start", ""))
                events.append(f"{title} at {start}" if start else title)
            return f" You have {count} event{'s' if count != 1 else ''}: {'; '.join(events)}."
        if isinstance(data, str):
            return f" {data[:max_len]}"

    # Pi status
    if "pi." in tool_name:
        if isinstance(data, dict):
            if "reachable" in data or "online" in data:
                is_up = data.get("reachable", data.get("online", False))
                return f" The Raspberry Pi is {'online and responding' if is_up else 'currently unreachable'}."
            # System info
            cpu = data.get("cpu_percent", "")
            mem = data.get("memory_percent", "")
            temp = data.get("temperature", data.get("cpu_temp", ""))
            parts = []
            if cpu:
                parts.append(f"CPU at {cpu}%")
            if mem:
                parts.append(f"memory at {mem}%")
            if temp:
                parts.append(f"temperature {temp}°C")
            if parts:
                return f" Pi status: {', '.join(parts)}."
        if isinstance(data, str):
            return f" {data[:max_len]}"

    # Memory
    if "memory" in tool_name:
        if isinstance(data, str):
            return f" {data[:max_len]}"
        if isinstance(data, dict):
            if "stored" in str(data).lower() or "saved" in str(data).lower():
                return " I've stored that in my memory."
            if "recalled" in str(data).lower() or "content" in data:
                content = data.get("content", str(data))
                return f" From my memory: {str(content)[:max_len]}"

    # Files / scripts
    if "files" in tool_name or "scripts" in tool_name:
        if isinstance(data, str):
            return f" {data[:max_len]}"
        if isinstance(data, dict):
            if data.get("is_new"):
                return f" File created successfully: {data.get('path', 'unknown')}."
            return f" Done: {json.dumps(data, indent=None, default=str)[:max_len]}"

    # Vision
    if "vision" in tool_name:
        if isinstance(data, str):
            return f" {data[:max_len]}"

    # Generic fallback — still conversational
    if isinstance(data, str):
        return f" {data[:max_len]}"
    if isinstance(data, dict):
        filtered = {k: v for k, v in data.items() if k not in ("elapsed_ms",)}
        text = json.dumps(filtered, indent=None, default=str)[:max_len]
        return f" Result: {text}"
    return f" Done."


async def start_bridge(tool_executor=None, agent=None, broadcast=None, port: int = BRIDGE_PORT):
    """Convenience function to start the bridge proxy."""
    bridge = PersonaPlexBridge(tool_executor=tool_executor, agent=agent, broadcast=broadcast)
    await bridge.start(port)
    return bridge
