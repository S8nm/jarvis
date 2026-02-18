"""
Jarvis Protocol — FastAPI Server
WebSocket bridge between Python backend and Electron frontend.

Improvements:
- Tool execution stats endpoint
- Memory stats endpoint
- Graceful degradation on init failures
"""
import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import HOST, PORT, PERSONAPLEX_ENABLED, PERSONAPLEX_BRIDGE_PORT, TELEGRAM_BOT_TOKEN
from agent import JarvisAgent
from resilience.cost_tracker import get_cost_tracker
from resilience.pi_health import PiHealthMonitor

# ──────────────────────────── Logging ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("jarvis.server")

# ──────────────────────────── Globals ────────────────────────────
agent = JarvisAgent()
detector = None  # Will be initialized in lifespan
telegram_bot = None  # Will be initialized in lifespan
pi_health: PiHealthMonitor | None = None  # Will be initialized in lifespan
connected_clients: Set[WebSocket] = set()


async def broadcast(message: str):
    """Broadcast a message to all connected WebSocket clients."""
    disconnected = set()
    for ws in list(connected_clients):
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    connected_clients.difference_update(disconnected)


# ──────────────────────────── Lifecycle ────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info("=" * 60)
    logger.info("  J.A.R.V.I.S. Protocol — Initializing")
    logger.info("=" * 60)

    agent.set_broadcast(broadcast)

    # Initialize agent (loads models — this may take a moment)
    try:
        await agent.initialize()
        logger.info("Agent initialized successfully")
    except Exception as e:
        logger.error(f"Agent initialization failed: {e}")
        logger.info("Server will start in degraded mode (text-only, no local models)")

    # Start wake word detection
    try:
        agent.start_wake_detection()
    except Exception as e:
        logger.warning(f"Wake word detection not started: {e}")

    # Initialize Object Detector (YOLO)
    global detector
    try:
        from vision.detector import ObjectDetector
        logger.info("Loading Vision Module (YOLO)...")
        # Run init in executor to avoid blocking startup (downloading weights)
        loop = asyncio.get_running_loop()
        detector = await loop.run_in_executor(None, ObjectDetector)
        logger.info("Vision Module active")
    except Exception as e:
        logger.error(f"Vision Module unavailable: {e}")

    # Start PersonaPlex Server + Bridge (full-duplex voice proxy)
    bridge = None
    pp_launcher = None
    if PERSONAPLEX_ENABLED:
        # Auto-start PersonaPlex server if not already running
        try:
            from bridge.launcher import PersonaPlexLauncher
            pp_launcher = PersonaPlexLauncher()
            pp_ready = await pp_launcher.ensure_running(timeout=120)
            if pp_ready:
                logger.info("PersonaPlex server is ready")
            else:
                logger.warning("PersonaPlex server not available — bridge will retry on client connect")
        except Exception as e:
            logger.warning(f"PersonaPlex launcher error: {e}")

        # Start the bridge proxy regardless — it handles retries to PersonaPlex
        try:
            from bridge.personaplex_bridge import start_bridge
            from tools.registry import execute_tool
            bridge = await start_bridge(
                tool_executor=execute_tool,
                agent=agent,
                broadcast=broadcast,
                port=PERSONAPLEX_BRIDGE_PORT,
            )
            logger.info(f"PersonaPlex Bridge active on port {PERSONAPLEX_BRIDGE_PORT}")
        except Exception as e:
            logger.warning(f"PersonaPlex Bridge not started: {e}")

    # Start Telegram Bot
    global telegram_bot
    if TELEGRAM_BOT_TOKEN:
        try:
            from telegram_bot import JarvisTelegramBot
            telegram_bot = JarvisTelegramBot(agent=agent, broadcast=broadcast)
            await telegram_bot.start()
        except Exception as e:
            logger.warning(f"Telegram bot not started: {e}")

    # Start Pi Health Monitor
    global pi_health
    try:
        from pi.config import is_pi_enabled
        if is_pi_enabled():
            from tools.registry import _get_pi_client
            pi_client = _get_pi_client()
            if pi_client:
                pi_health = PiHealthMonitor(pi_client)
                pi_health.set_broadcast(broadcast)
                await pi_health.start()
                logger.info("Pi health monitor active")
    except Exception as e:
        logger.warning(f"Pi health monitor not started: {e}")

    logger.info("=" * 60)
    logger.info(f"  J.A.R.V.I.S. Online — ws://{HOST}:{PORT}/ws")
    if bridge:
        logger.info(f"  PersonaPlex Bridge — ws://localhost:{PERSONAPLEX_BRIDGE_PORT}/api/chat")
    if telegram_bot and telegram_bot._running:
        logger.info("  Telegram Bot — active")
    if pi_health:
        logger.info("  Pi Health Monitor — active")
    logger.info("=" * 60)

    yield  # App is running

    # Shutdown
    logger.info("J.A.R.V.I.S. shutting down...")
    if pi_health:
        await pi_health.stop()
    if telegram_bot:
        await telegram_bot.stop()
    if bridge:
        await bridge.stop()
    if pp_launcher:
        await pp_launcher.stop()
    await agent._claude_client.close()
    agent.stop_wake_detection()
    logger.info("Goodbye, sir.")


# ──────────────────────────── App ────────────────────────────
app = FastAPI(
    title="J.A.R.V.I.S. Protocol",
    description="Local AI Assistant Backend",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "app://."
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────── Routes ────────────────────────────
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    status = await agent.get_status()
    return {"status": "online", "agent": status}


@app.get("/dashboard")
async def dashboard_data():
    """Get dashboard data for frontend panels (notes, calendar, etc.)."""
    try:
        from tools.registry import get_dashboard_data
        return {"status": "ok", "data": get_dashboard_data()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/stats")
async def tool_stats():
    """Get tool execution statistics."""
    try:
        from tools.registry import get_execution_stats
        stats = get_execution_stats()
        stats["llm"] = agent.llm.get_stats()
        stats["claude"] = agent._claude_client.get_stats()
        stats["router"] = agent._router.get_stats()
        return {"status": "ok", "data": stats}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/memories")
async def list_memories():
    """Get stored long-term memories."""
    try:
        from memory import recall_memories, get_memory_summary
        return {
            "status": "ok",
            "summary": get_memory_summary(),
            "memories": recall_memories(limit=50)
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/pi/status")
async def pi_status():
    """Get Raspberry Pi / PicoClaw worker status and recent task history."""
    try:
        from pi.config import is_pi_enabled, get_pi_config
        from tools.registry import _get_pi_client, get_execution_stats

        enabled = is_pi_enabled()
        if not enabled:
            return {"status": "ok", "data": {"enabled": False}}

        cfg = get_pi_config()
        client = _get_pi_client()

        # Recent tasks from SQLite ledger
        recent_tasks = client.get_recent_tasks(5) if client else []

        # Filter execution stats for pi.* tools
        all_stats = get_execution_stats()
        pi_tool_stats = {
            k: v for k, v in all_stats.get("per_tool", {}).items()
            if k.startswith("pi.")
        }

        # Aggregate totals across all pi tools
        total_calls = sum(s["calls"] for s in pi_tool_stats.values())
        total_successes = sum(s["successes"] for s in pi_tool_stats.values())
        avg_ms = (
            sum(s["avg_ms"] * s["calls"] for s in pi_tool_stats.values()) / total_calls
            if total_calls > 0 else 0
        )

        return {
            "status": "ok",
            "data": {
                "enabled": True,
                "host": cfg.get("host", ""),
                "transport": cfg.get("transport", "ssh"),
                "recent_tasks": recent_tasks,
                "stats": {
                    "total_calls": total_calls,
                    "total_successes": total_successes,
                    "success_rate": round(total_successes / total_calls * 100, 1) if total_calls else 0,
                    "avg_ms": round(avg_ms, 1),
                },
                "per_tool": pi_tool_stats,
            }
        }
    except Exception as e:
        logger.warning(f"Pi status error: {e}")
        return {"status": "ok", "data": {"enabled": False, "error": str(e)}}


@app.get("/api/cost/report")
async def cost_report():
    """Get Claude API cost tracking report."""
    try:
        tracker = get_cost_tracker()
        return {"status": "ok", "data": tracker.get_report()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/router/stats")
async def router_stats():
    """Get intent router classification statistics."""
    try:
        return {
            "status": "ok",
            "data": {
                "router": agent._router.get_stats(),
                "claude": agent._claude_client.get_stats(),
                "ollama": agent.llm.get_stats(),
                "rate_limiter": agent._rate_limiter.get_status(),
            }
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/pi/health")
async def pi_health_status():
    """Get Pi health monitor status including queue and connectivity."""
    if pi_health:
        return {"status": "ok", "data": pi_health.get_status()}
    return {"status": "ok", "data": {"reachable": False, "monitor_active": False}}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Main WebSocket endpoint for frontend communication."""
    await ws.accept()
    connected_clients.add(ws)
    logger.info(f"Client connected. Total: {len(connected_clients)}")

    # Send current state to new client
    try:
        status = await agent.get_status()
        await ws.send_text(json.dumps({
            "type": "init",
            "data": {
                **status,
                "conversation": agent.conversation_log
            }
        }))
    except Exception as e:
        logger.error(f"Failed to send init: {e}")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {raw[:100]}")
                continue

            msg_type = msg.get("type", "")
            data = msg.get("data", {})

            if msg_type == "text_input":
                text = data.get("text", "").strip()
                if text:
                    asyncio.create_task(agent.handle_text_input(text))

            elif msg_type == "voice_trigger":
                # Manual trigger of voice recording (instead of wake word)
                asyncio.create_task(agent.handle_voice_interaction())

            elif msg_type == "stop_speaking":
                agent.tts.stop_speaking()

            elif msg_type == "clear_history":
                agent.llm.clear_history()
                agent.conversation_log.clear()
                await ws.send_text(json.dumps({
                    "type": "history_cleared",
                    "data": {}
                }))

            elif msg_type == "recalibrate_mic":
                # New: trigger noise floor recalibration
                asyncio.create_task(_recalibrate_mic(ws))

            elif msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong", "data": {}}))

            elif msg_type == "object_detection_frame":
                image_data = data.get("image", "")
                if not detector:
                    await ws.send_text(json.dumps({
                        "type": "detection_result",
                        "data": []
                    }))
                elif image_data:
                    # Run detection in thread pool to avoid blocking async loop
                    loop = asyncio.get_running_loop()
                    try:
                        results = await loop.run_in_executor(None, detector.detect_objects, image_data)
                        await ws.send_text(json.dumps({
                            "type": "detection_result",
                            "data": results
                        }))
                    except Exception as e:
                        logger.error(f"Detection failed: {e}")

            else:
                logger.warning(f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        connected_clients.discard(ws)
        logger.info(f"Client removed. Total: {len(connected_clients)}")


async def _recalibrate_mic(ws: WebSocket):
    """Recalibrate the microphone noise floor."""
    try:
        loop = asyncio.get_running_loop()
        threshold = await loop.run_in_executor(None, agent.stt.calibrate_noise_floor)
        await ws.send_text(json.dumps({
            "type": "mic_calibrated",
            "data": {"threshold": round(threshold, 6)}
        }))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "error",
            "data": {"message": f"Calibration failed: {e}"}
        }))


# ──────────────────────────── Entry Point ────────────────────────────
@app.post("/restart")
async def restart_server():
    """Trigger a graceful server restart."""
    logger.info("Restart requested — shutting down for reload...")

    async def _do_restart():
        await asyncio.sleep(0.5)
        import os
        import signal
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_do_restart())
    return {"status": "restarting"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=True,
        reload_dirs=[".", "llm", "speech", "tools", "vision", "bridge", "resilience"],
        log_level="info"
    )
