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
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE  # Self-signed cert

        try:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            server_ws = await session.ws_connect(server_url, ssl=ssl_ctx)
            logger.info("Connected to PersonaPlex server")
        except Exception as e:
            logger.error(f"Failed to connect to PersonaPlex: {e}")
            await client_ws.close(message=b"PersonaPlex server unreachable")
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
    """Format a tool result as natural text for display. Truncates to max_len."""
    if "error" in result:
        return f" [Tool {tool_name} error: {result['error'][:200]}]"

    # Try to make the result readable
    if "result" in result:
        data = result["result"]
        if isinstance(data, str):
            text = data[:max_len]
        elif isinstance(data, dict):
            text = json.dumps(data, indent=None, default=str)[:max_len]
        else:
            text = str(data)[:max_len]
        return f" [{tool_name}: {text}]"

    # Generic formatting
    filtered = {k: v for k, v in result.items() if k not in ("elapsed_ms",)}
    text = json.dumps(filtered, indent=None, default=str)[:max_len]
    return f" [{tool_name}: {text}]"


async def start_bridge(tool_executor=None, agent=None, broadcast=None, port: int = BRIDGE_PORT):
    """Convenience function to start the bridge proxy."""
    bridge = PersonaPlexBridge(tool_executor=tool_executor, agent=agent, broadcast=broadcast)
    await bridge.start(port)
    return bridge
