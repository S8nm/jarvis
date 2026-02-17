"""
Jarvis Protocol — Central Configuration

Supports optional config.json override for user-customizable settings.
Inspired by Microsoft JARVIS's externalized YAML configuration pattern.
"""
import json
import os
from pathlib import Path

# ──────────────────────────── Paths ────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
BACKEND_DIR = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
SANDBOX_DIR = PROJECT_ROOT / "sandbox"
MODELS_DIR = PROJECT_ROOT / "models"
PIPER_DIR = MODELS_DIR / "piper"
LOGS_DIR = DATA_DIR / "logs"

# Create directories
for d in [DATA_DIR, SANDBOX_DIR, MODELS_DIR, PIPER_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ──────────────────────────── User Config Override ────────────────────────────
# Load user-specific settings from config.json if it exists
_user_config = {}
_config_path = PROJECT_ROOT / "config.json"
if _config_path.exists():
    try:
        _user_config = json.loads(_config_path.read_text(encoding="utf-8"))
    except Exception:
        pass  # Fall back to defaults


def _cfg(key: str, default):
    """Get a config value, preferring user override from config.json."""
    return _user_config.get(key, default)


# ──────────────────────────── Server ────────────────────────────
HOST = _cfg("host", "127.0.0.1")
PORT = _cfg("port", 8765)
WS_PATH = "/ws"

# ──────────────────────────── Audio ────────────────────────────
SAMPLE_RATE = 16000
CHANNELS = 1
MIC_DEVICE = _cfg("mic_device", 1)           # Mic In (Elgato Wave:3) — index 1 in sounddevice
AUDIO_CHUNK_MS = 30                            # WebRTC VAD frame size (10, 20, or 30 ms)
VAD_AGGRESSIVENESS = _cfg("vad_aggressiveness", 2)   # 0-3, higher = more aggressive filtering
SILENCE_LIMIT_SEC = _cfg("silence_limit", 1.0)       # seconds of silence to end utterance
MIN_UTTERANCE_SEC = _cfg("min_utterance", 0.5)       # minimum utterance duration
AUDIO_GAIN = _cfg("audio_gain", 10.0)                # software gain multiplier for quiet mics

# ──────────────────────────── Wake Word ────────────────────────────
WAKE_WORD_MODEL = _cfg("wake_word_model", "hey_jarvis")
WAKE_SENSITIVITY = _cfg("wake_sensitivity", 0.3)     # 0.0 - 1.0 (lower = more sensitive)

# ──────────────────────────── STT (faster-whisper) ────────────────────────────
WHISPER_MODEL_SIZE = _cfg("whisper_model", "base.en")
WHISPER_DEVICE = _cfg("whisper_device", "cpu")
WHISPER_COMPUTE_TYPE = _cfg("whisper_compute_type", "int8")
WHISPER_LANGUAGE = _cfg("whisper_language", "en")
WHISPER_BEAM_SIZE = _cfg("whisper_beam_size", 5)

# ──────────────────────────── LLM (Ollama) ────────────────────────────
OLLAMA_BASE_URL = _cfg("ollama_url", "http://127.0.0.1:11434")
OLLAMA_MODEL = _cfg("ollama_model", "llama3.1:8b")
OLLAMA_TIMEOUT = _cfg("ollama_timeout", 120)
MAX_CONTEXT_MESSAGES = _cfg("max_context_messages", 20)   # rolling conversation window

# ──────────────────────────── TTS (Piper) ────────────────────────────
PIPER_VOICE = _cfg("piper_voice", "en_GB-alan-medium")
PIPER_SPEAKER_ID = _cfg("piper_speaker_id", 0)
PIPER_SPEECH_RATE = _cfg("piper_speech_rate", 1.0)

# ──────────────────────────── PersonaPlex (Full-Duplex Voice) ────────────────────────────
_personaplex_cfg = _cfg("personaplex", {})
PERSONAPLEX_ENABLED = _personaplex_cfg.get("enabled", False) if isinstance(_personaplex_cfg, dict) else False
PERSONAPLEX_HOST = _personaplex_cfg.get("host", "localhost") if isinstance(_personaplex_cfg, dict) else "localhost"
PERSONAPLEX_PORT = _personaplex_cfg.get("port", 8998) if isinstance(_personaplex_cfg, dict) else 8998
PERSONAPLEX_BRIDGE_PORT = _personaplex_cfg.get("bridge_port", 8999) if isinstance(_personaplex_cfg, dict) else 8999

# ──────────────────────────── Vision (Phase 2) ────────────────────────────
CAMERA_DEVICE_ID = _cfg("camera_device", 0)
VISION_MODEL = _cfg("vision_model", "llava:13b")
VISION_RESOLUTION = tuple(_cfg("vision_resolution", [1280, 720]))

# ──────────────────────────── Memory ────────────────────────────
MEMORY_AUTO_EXTRACT = _cfg("memory_auto_extract", True)   # Auto-extract facts from conversations
MEMORY_MAX_FACTS = _cfg("memory_max_facts", 100)          # Max stored memories before pruning
