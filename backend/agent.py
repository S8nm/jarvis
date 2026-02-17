"""
Jarvis Protocol — Agent State Machine (Phase 2+)
Core orchestrator: IDLE -> LISTENING -> THINKING -> EXECUTING -> SPEAKING loop.

Improvements:
- Interaction queue instead of dropping concurrent requests (from Priler/jarvis IPC pattern)
- Memory extraction after each interaction (from Microsoft JARVIS pipeline)
- Conversation summarization when context grows too large
"""
import asyncio
import json
import logging
import psutil
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

from config import WAKE_SENSITIVITY, OLLAMA_MODEL, MAX_CONTEXT_MESSAGES, PERSONAPLEX_ENABLED
from llm.client import LLMClient
from llm.claude_client import ClaudeLLMClient
from llm.prompts import get_greeting_prompt, build_tool_result_messages
from llm.router import IntentRouter, RouteDecision
from resilience import SlidingWindowRateLimiter
from resilience.cost_tracker import get_cost_tracker
from speech.stt import SpeechToText
from speech.tts import TextToSpeech
from speech.wake_word import WakeWordDetector
from tools.registry import execute_tool, parse_tool_calls, strip_tool_blocks, get_dashboard_data

logger = logging.getLogger("jarvis.agent")


class AgentState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class JarvisAgent:
    """
    The central agent that manages the voice interaction loop and tool routing.
    Communicates with the frontend via a broadcast callback.
    Now includes an interaction queue and memory integration.
    """

    def __init__(self):
        self.state = AgentState.IDLE
        self.llm = LLMClient()
        self.stt = SpeechToText()
        self.tts = TextToSpeech()
        self.wake_detector: Optional[WakeWordDetector] = None

        # Router + Claude backend + rate limiter
        try:
            self._cost_tracker = get_cost_tracker()
        except Exception:
            self._cost_tracker = None
        self._router = IntentRouter(cost_tracker=self._cost_tracker)
        self._claude_client = ClaudeLLMClient(cost_tracker=self._cost_tracker)
        self._rate_limiter = SlidingWindowRateLimiter()

        # Broadcast function set by main.py to send to all WebSocket clients
        self._broadcast: Optional[Callable] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # PersonaPlex voice state — when True, backend STT/TTS should stay quiet
        self.personaplex_active = False

        # Conversation log for the UI
        self.conversation_log: list[dict] = []

        # Latest transcript for display
        self.last_transcript: str = ""
        self.last_confidence: float = 0.0

        # GPU metrics cache (avoid spawning nvidia-smi every 2s)
        self._gpu_cache: dict = {}
        self._gpu_cache_time: float = 0

        # Interaction queue instead of dropping concurrent requests
        self._interaction_lock = asyncio.Lock()
        self._text_queue: asyncio.Queue = asyncio.Queue(maxsize=5)
        self._queue_processor_task: Optional[asyncio.Task] = None

    def set_broadcast(self, broadcast_fn: Callable):
        """Set the function used to broadcast messages to all connected clients."""
        self._broadcast = broadcast_fn

    def _on_audio_level(self, rms: float, is_speech: bool):
        """Called from STT recording thread with audio level data."""
        if self._broadcast and self._loop:
            try:
                self._loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._broadcast_message("audio_level", {
                        "rms": round(min(rms, 1.0), 4),
                        "is_speech": is_speech
                    })
                )
            except Exception:
                pass

    async def initialize(self):
        """Initialize all sub-systems."""
        logger.info("Initializing Jarvis agent...")

        # Store event loop reference for thread-safe callbacks
        self._loop = asyncio.get_running_loop()

        # Initialize subsystems — skip voice stack when PersonaPlex handles it
        if PERSONAPLEX_ENABLED:
            logger.info("PersonaPlex enabled — skipping STT/TTS/wake word init")
        else:
            self.stt.initialize()
            self.stt.set_audio_level_callback(self._on_audio_level)
            self.tts.initialize()

            # Set up wake word detector
            self.wake_detector = WakeWordDetector(
                sensitivity=WAKE_SENSITIVITY,
                on_wake=self._on_wake_word
            )
            self.wake_detector.initialize()

        # Start the text input queue processor
        self._queue_processor_task = asyncio.create_task(self._process_text_queue())

        logger.info("Jarvis agent initialized")

        # Send greeting (text only when PersonaPlex is active — no TTS)
        await self._send_greeting()

    async def _send_greeting(self):
        """Send an initial greeting when Jarvis starts up."""
        await self._set_state(AgentState.THINKING)

        greeting_prompt = get_greeting_prompt()
        full_response = ""

        async for token in self.llm.stream_response(greeting_prompt):
            full_response += token
            await self._broadcast_message("response_chunk", {"token": token})

        self.conversation_log.append({
            "role": "assistant",
            "content": full_response,
            "timestamp": datetime.now().isoformat()
        })

        await self._broadcast_message("response_complete", {
            "text": full_response,
            "conversation": self.conversation_log
        })

        # Speak the greeting (only if not using PersonaPlex)
        if not PERSONAPLEX_ENABLED:
            await self._set_state(AgentState.SPEAKING)
            await self.tts.speak(full_response)

        # Send initial dashboard data
        await self._send_dashboard_update()

        await self._set_state(AgentState.IDLE)

    def start_wake_detection(self):
        """Start listening for wake word."""
        if PERSONAPLEX_ENABLED:
            logger.info("PersonaPlex enabled — wake word detection skipped")
            return
        if self.wake_detector:
            loop = asyncio.get_running_loop()
            self.wake_detector.start(loop)
            logger.info("Wake word detection active")

    def stop_wake_detection(self):
        """Stop listening for wake word and clean up background tasks."""
        if self.wake_detector:
            self.wake_detector.stop()
        if self._queue_processor_task and not self._queue_processor_task.done():
            self._queue_processor_task.cancel()

    async def _on_wake_word(self):
        """Callback when wake word is detected."""
        if self.personaplex_active:
            logger.info("Wake word detected but PersonaPlex voice is active — ignoring")
            return
        logger.info("Wake word detected!")
        await self.handle_voice_interaction()

    async def handle_voice_interaction(self):
        """Full voice interaction loop: listen -> transcribe -> think -> speak -> listen again.
        Keeps looping until silence (no speech detected) ends the conversation."""
        if self.personaplex_active:
            logger.info("PersonaPlex voice active — skipping backend voice interaction")
            return
        if self._interaction_lock.locked():
            logger.warning("Already processing an interaction, ignoring voice trigger")
            return

        async with self._interaction_lock:
            try:
                # Stop wake detection during interaction and wait for mic release
                if self.wake_detector:
                    self.wake_detector.stop()
                    await asyncio.sleep(0.5)  # Give Windows time to release the audio device

                # Conversation loop — keep listening after each response
                while True:
                    # -- LISTENING --
                    await self._set_state(AgentState.LISTENING)
                    await self._broadcast_message("listening_started", {})

                    result = await self.stt.record_and_transcribe()

                    if not result or not result.text.strip():
                        logger.info("No speech detected — ending conversation loop")
                        await self._broadcast_message("listening_ended", {"text": "", "confidence": 0})
                        break  # Exit loop, go back to wake word detection

                    self.last_transcript = result.text
                    self.last_confidence = result.confidence

                    await self._broadcast_message("transcript", {
                        "text": result.text,
                        "confidence": result.confidence,
                        "language": result.language,
                        "duration": result.duration
                    })

                    # Add user message to log
                    self.conversation_log.append({
                        "role": "user",
                        "content": result.text,
                        "timestamp": datetime.now().isoformat()
                    })

                    # -- THINKING + EXECUTING + SPEAKING --
                    await self._process_text(result.text, source="voice")

                    # Brief pause before listening again
                    await asyncio.sleep(0.3)

            except Exception as e:
                logger.error(f"Voice interaction error: {e}")
                await self._set_state(AgentState.ERROR)
                await self._broadcast_message("error", {"message": str(e)})
            finally:
                # Restart wake detection
                if self.wake_detector:
                    self.wake_detector.start(asyncio.get_running_loop())
                if self.state != AgentState.IDLE:
                    await self._set_state(AgentState.IDLE)

    async def handle_text_input(self, text: str):
        """Handle text input from the UI (non-voice). Queues if busy."""
        if self._interaction_lock.locked():
            # Queue the input instead of dropping it
            try:
                self._text_queue.put_nowait(text)
                logger.info(f"Text input queued (queue size: {self._text_queue.qsize()})")
                await self._broadcast_message("input_queued", {
                    "text": text,
                    "queue_size": self._text_queue.qsize()
                })
            except asyncio.QueueFull:
                logger.warning("Text input queue full, dropping oldest")
                try:
                    self._text_queue.get_nowait()  # Drop oldest
                    self._text_queue.put_nowait(text)
                except Exception:
                    pass
            return

        async with self._interaction_lock:
            try:
                await self._execute_text_input(text)
            except Exception as e:
                logger.error(f"Text input error: {e}")
                await self._set_state(AgentState.ERROR)
                await self._broadcast_message("error", {"message": str(e)})
            finally:
                if self.state != AgentState.IDLE:
                    await self._set_state(AgentState.IDLE)

    async def _process_text_queue(self):
        """Background task that processes queued text inputs when the agent is idle."""
        while True:
            try:
                text = await self._text_queue.get()
                # Wait for current interaction to finish
                async with self._interaction_lock:
                    try:
                        logger.info(f"Processing queued input: {text[:50]}")
                        await self._execute_text_input(text)
                    except Exception as e:
                        logger.error(f"Queued text error: {e}")
                    finally:
                        if self.state != AgentState.IDLE:
                            await self._set_state(AgentState.IDLE)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                await asyncio.sleep(1)

    async def _execute_text_input(self, text: str):
        """Execute a text input (shared logic for direct and queued inputs)."""
        self.last_transcript = text
        self.last_confidence = 1.0

        await self._broadcast_message("transcript", {
            "text": text,
            "confidence": 1.0,
            "language": "en",
            "duration": 0
        })

        self.conversation_log.append({
            "role": "user",
            "content": text,
            "timestamp": datetime.now().isoformat()
        })

        await self._process_text(text)

    async def _process_text(self, text: str, source: str = "text"):
        """Process user text through router -> appropriate LLM backend -> tools -> respond."""
        await self._set_state(AgentState.THINKING)

        # -- Rate limit check --
        allowed, limit_info = self._rate_limiter.check(source)
        if not allowed:
            logger.warning(f"Rate limited ({source}): {limit_info}")
            await self._broadcast_message("rate_limited", limit_info)
            msg = "I'm receiving requests quite rapidly, sir. Please allow me a moment."
            self.conversation_log.append({
                "role": "assistant", "content": msg,
                "timestamp": datetime.now().isoformat()
            })
            await self._broadcast_message("response_complete", {
                "text": msg, "conversation": self.conversation_log
            })
            return

        # -- Route the query --
        decision = await self._router.classify(text, self.conversation_log)
        logger.info(
            f"Route: {decision.target} | {decision.intent_type} | "
            f"conf={decision.confidence:.2f} | {decision.reason} "
            f"({decision.classification_ms:.1f}ms)"
        )
        await self._broadcast_message("route_decision", {
            "target": decision.target,
            "intent_type": decision.intent_type,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "classification_ms": decision.classification_ms,
            "tool_hint": decision.tool_hint,
        })

        # -- Dispatch based on route --
        if decision.target == "tool_direct":
            final_response, tools_used = await self._handle_direct_tool(text, decision)
        elif decision.target == "claude":
            final_response, tools_used = await self._handle_claude_response(text)
        else:
            final_response, tools_used = await self._handle_ollama_response(text)

        # -- Shared: update conversation log --
        log_entry = {
            "role": "assistant",
            "content": final_response,
            "timestamp": datetime.now().isoformat(),
        }
        if tools_used:
            log_entry["tools_used"] = tools_used
        self.conversation_log.append(log_entry)

        # -- Shared: broadcast completion --
        complete_data = {
            "text": final_response,
            "conversation": self.conversation_log,
            "route": decision.target,
        }
        if tools_used:
            complete_data["tools_used"] = tools_used
        await self._broadcast_message("response_complete", complete_data)

        # -- Shared: TTS (skip if PersonaPlex handles voice) --
        if not self.personaplex_active:
            await self._set_state(AgentState.SPEAKING)
            await self.tts.speak(final_response)

        # -- Shared: dashboard update after tool use --
        if tools_used:
            await self._send_dashboard_update()

        # -- Post-interaction: memory extraction (async, non-blocking) --
        asyncio.create_task(self._extract_memories(text, final_response))

        # -- Post-interaction: conversation management --
        max_log = MAX_CONTEXT_MESSAGES * 3
        if len(self.conversation_log) > max_log:
            self.conversation_log = self.conversation_log[-MAX_CONTEXT_MESSAGES:]
            logger.info(f"Hard-trimmed conversation_log to {MAX_CONTEXT_MESSAGES} entries")
        elif len(self.conversation_log) > MAX_CONTEXT_MESSAGES + 10:
            asyncio.create_task(self._maybe_summarize_conversation())

    # ──────────────────────────── Route Handlers ────────────────────────────

    async def _handle_ollama_response(self, text: str) -> tuple[str, list[str]]:
        """Handle query via Ollama (existing path, fast/free)."""
        full_response = ""
        async for token in self.llm.stream_response(text):
            full_response += token
            await self._broadcast_message("response_chunk", {"token": token})

        tool_calls = parse_tool_calls(full_response)
        if not tool_calls:
            return full_response, []

        # Execute tools and summarize via Ollama
        tools_used, tool_results = await self._execute_tool_calls(tool_calls)
        summary = await self._summarize_tool_results(
            text, full_response, tool_results, backend="ollama"
        )
        return summary or strip_tool_blocks(full_response), tools_used

    async def _handle_claude_response(self, text: str) -> tuple[str, list[str]]:
        """Handle query via Claude (complex reasoning, analysis, planning)."""
        full_response = ""
        async for token in self._claude_client.stream_response(text, self.conversation_log):
            full_response += token
            await self._broadcast_message("response_chunk", {"token": token})

        tool_calls = parse_tool_calls(full_response)
        if not tool_calls:
            # Sync to Ollama history so it stays aware of Claude turns
            self.llm.conversation_history.append({"role": "user", "content": text})
            self.llm.conversation_history.append({"role": "assistant", "content": full_response})
            return full_response, []

        # Execute tools, summarize via Ollama (free) to save Claude costs
        tools_used, tool_results = await self._execute_tool_calls(tool_calls)
        summary = await self._summarize_tool_results(
            text, full_response, tool_results, backend="ollama"
        )
        final = summary or strip_tool_blocks(full_response)

        # Sync to Ollama history
        self.llm.conversation_history.append({"role": "user", "content": text})
        self.llm.conversation_history.append({"role": "assistant", "content": final})
        return final, tools_used

    async def _handle_direct_tool(self, text: str, decision: RouteDecision) -> tuple[str, list[str]]:
        """Handle direct tool execution (skip LLM entirely, router matched a tool)."""
        tool_name = decision.tool_hint
        tool_args = decision.tool_args_hint

        await self._set_state(AgentState.EXECUTING)
        await self._broadcast_message("tool_executing", {
            "tool": tool_name, "args": tool_args
        })

        result = await execute_tool(tool_name, tool_args)
        await self._broadcast_message("tool_result", {
            "tool": tool_name, "result": result
        })

        # Use Ollama for a brief natural-language summary (fast, free)
        summary = await self._summarize_tool_results(
            text, "I'll check that for you, sir.",
            [{"tool": tool_name, "result": result}], backend="ollama"
        )
        return summary or f"Done, sir. The {tool_name} tool has completed.", [tool_name]

    # ──────────────────────────── Shared Helpers ────────────────────────────

    async def _execute_tool_calls(self, tool_calls: list[dict]) -> tuple[list[str], list[dict]]:
        """Execute tool calls and broadcast progress."""
        await self._set_state(AgentState.EXECUTING)

        tools_used = []
        tool_results = []
        for tc in tool_calls:
            tool_name = tc["tool"]
            tool_args = tc.get("args", {})
            tools_used.append(tool_name)

            await self._broadcast_message("tool_executing", {
                "tool": tool_name, "args": tool_args
            })

            result = await execute_tool(tool_name, tool_args)
            tool_results.append({"tool": tool_name, "result": result})

            await self._broadcast_message("tool_result", {
                "tool": tool_name, "result": result
            })

        return tools_used, tool_results

    async def _summarize_tool_results(self, user_text: str, llm_response: str,
                                       tool_results: list[dict],
                                       backend: str = "ollama") -> str:
        """Send tool results back to an LLM for natural-language summary."""
        await self._set_state(AgentState.THINKING)

        summary_messages = build_tool_result_messages(
            self.conversation_log, user_text, llm_response, tool_results
        )

        await self._broadcast_message("response_clear", {})

        summary = ""
        if backend == "claude":
            async for token in self._claude_client.stream_response_from_messages(summary_messages):
                summary += token
                await self._broadcast_message("response_chunk", {"token": token})
        else:
            async for token in self.llm.stream_response_from_messages(
                summary_messages, save_to_history=True
            ):
                summary += token
                await self._broadcast_message("response_chunk", {"token": token})

        return summary

    async def _extract_memories(self, user_input: str, assistant_response: str):
        """Extract memorable facts from the conversation turn (runs in background).
        Uses a standalone LLM call that does NOT pollute the main conversation_history.
        """
        try:
            from memory import build_extraction_prompt, store_memory
            prompt = build_extraction_prompt(user_input, assistant_response)
            if not prompt:
                return

            # Use a standalone LLM call (NOT self.llm.get_response which adds to history)
            messages = [
                {"role": "system", "content": "You extract memorable facts from conversations. Output a JSON array of objects with 'content' and 'category' keys. If nothing is worth remembering, output an empty array []."},
                {"role": "user", "content": prompt},
            ]
            extraction = ""
            async for token in self.llm.stream_response_from_messages(messages, save_to_history=False):
                extraction += token

            try:
                import json
                facts = json.loads(extraction)
                if isinstance(facts, list):
                    for fact in facts[:3]:
                        if isinstance(fact, dict) and "content" in fact:
                            store_memory(
                                content=fact["content"],
                                category=fact.get("category", "general"),
                                source="auto_extraction"
                            )
            except (json.JSONDecodeError, Exception):
                pass
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Memory extraction failed (non-critical): {e}")

    async def _maybe_summarize_conversation(self):
        """Summarize old conversation messages to compress context.
        Uses standalone LLM call to avoid polluting conversation_history.
        """
        try:
            from memory import summarize_conversation, store_summary
            prompt = summarize_conversation(self.conversation_log, MAX_CONTEXT_MESSAGES)
            if not prompt:
                return

            # Standalone LLM call — does NOT add to conversation_history
            messages = [
                {"role": "system", "content": "Summarize the following conversation concisely, preserving key facts and context."},
                {"role": "user", "content": prompt},
            ]
            summary = ""
            async for token in self.llm.stream_response_from_messages(messages, save_to_history=False):
                summary += token
            if summary:
                # Count messages being summarized
                num_summarized = len(self.conversation_log) - MAX_CONTEXT_MESSAGES
                store_summary(summary, num_summarized)
                # Trim the conversation log
                self.conversation_log = self.conversation_log[-MAX_CONTEXT_MESSAGES:]
                logger.info(f"Conversation summarized: {num_summarized} messages compressed")
        except ImportError:
            # Memory module not available — just trim
            self.conversation_log = self.conversation_log[-MAX_CONTEXT_MESSAGES:]
        except Exception as e:
            logger.warning(f"Conversation summarization failed: {e}")
            self.conversation_log = self.conversation_log[-MAX_CONTEXT_MESSAGES:]

    async def _send_dashboard_update(self):
        """Send updated dashboard data to frontend."""
        try:
            data = get_dashboard_data()
            await self._broadcast_message("dashboard_update", data)
        except Exception as e:
            logger.warning(f"Dashboard update error: {e}")

    async def _set_state(self, new_state: AgentState):
        """Update agent state and notify frontend."""
        old_state = self.state
        self.state = new_state
        logger.info(f"State: {old_state} -> {new_state}")
        await self._broadcast_message("state_change", {
            "state": new_state.value,
            "previous": old_state.value
        })

    async def _broadcast_message(self, msg_type: str, data: dict):
        """Send a message to all connected WebSocket clients."""
        if self._broadcast:
            message = json.dumps({
                "type": msg_type,
                "data": data,
                "timestamp": datetime.now().isoformat()
            }, default=str)
            await self._broadcast(message)

    def _get_gpu_info_cached(self) -> dict:
        """Get GPU info with 5-second cache to avoid spawning nvidia-smi too often."""
        now = time.time()
        if now - self._gpu_cache_time < 5 and self._gpu_cache:
            return self._gpu_cache

        gpu_info = {}
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                parts = [p.strip() for p in result.stdout.strip().split(",")]
                if len(parts) >= 7:
                    gpu_info = {
                        "name": parts[0],
                        "utilization": float(parts[1]),
                        "vram_used_gb": round(float(parts[2]) / 1024, 2),
                        "vram_total_gb": round(float(parts[3]) / 1024, 2),
                        "temperature": float(parts[4]),
                        "power_draw": float(parts[5]),
                        "power_limit": float(parts[6]),
                    }
        except Exception:
            pass

        self._gpu_cache = gpu_info
        self._gpu_cache_time = now
        return gpu_info

    async def get_status(self) -> dict:
        """Get current agent status for health checks."""
        ollama_ok = await self.llm.check_health()

        # Include dashboard data
        dashboard = {}
        try:
            dashboard = get_dashboard_data()
        except Exception:
            pass

        # Get real system metrics (non-blocking, per-CPU for accuracy)
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1, percpu=False)
            memory = psutil.virtual_memory()
            net_io = psutil.net_io_counters()
            disk_io = psutil.disk_io_counters()
            boot_time = psutil.boot_time()
            uptime_seconds = time.time() - boot_time

            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            seconds = int(uptime_seconds % 60)
            uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            # GPU metrics via nvidia-smi (cached for 5s to avoid spawning subprocess every 2s)
            gpu_info = self._get_gpu_info_cached()

            system_metrics = {
                "cpu_percent": round(cpu_percent, 1),
                "memory_percent": round(memory.percent, 1),
                "memory_used_gb": round(memory.used / (1024**3), 2),
                "memory_total_gb": round(memory.total / (1024**3), 2),
                "memory_available_gb": round(memory.available / (1024**3), 2),
                "network_sent_mb": round(net_io.bytes_sent / (1024**2), 2),
                "network_recv_mb": round(net_io.bytes_recv / (1024**2), 2),
                "disk_read_mb": round(disk_io.read_bytes / (1024**2), 2) if disk_io else 0,
                "disk_write_mb": round(disk_io.write_bytes / (1024**2), 2) if disk_io else 0,
                "uptime": uptime_str,
                "uptime_seconds": int(uptime_seconds),
                "gpu": gpu_info,
            }
        except Exception as e:
            logger.warning(f"Could not get system metrics: {e}")
            system_metrics = {}

        claude_ok = await self._claude_client.check_health()

        return {
            "state": self.state.value,
            "ollama_connected": ollama_ok,
            "claude_connected": claude_ok,
            "stt_ready": self.stt._model is not None,
            "tts_ready": self.tts._synthesize_fn is not None,
            "wake_word_active": self.wake_detector is not None and self.wake_detector._running,
            "conversation_length": len(self.conversation_log),
            "last_transcript": self.last_transcript,
            "current_llm": self.llm.model if hasattr(self.llm, 'model') else OLLAMA_MODEL,
            "text_queue_size": self._text_queue.qsize(),
            "dashboard": dashboard,
            "system": system_metrics,
            "router": self._router.get_stats(),
            "claude": self._claude_client.get_stats(),
            "rate_limiter": self._rate_limiter.get_status(),
        }
