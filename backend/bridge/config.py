"""
PersonaPlex Bridge — Configuration
Reads PersonaPlex settings from the main config.json.
"""
from config import _cfg

_pp = _cfg("personaplex", {})
if not isinstance(_pp, dict):
    _pp = {}

# PersonaPlex server
PERSONAPLEX_HOST = _pp.get("host", "localhost")
PERSONAPLEX_PORT = _pp.get("port", 8998)
PERSONAPLEX_SSL = _pp.get("ssl", True)
PERSONAPLEX_SSL_CERT = _pp.get("ssl_cert_path", "")

# Bridge proxy
BRIDGE_PORT = _pp.get("bridge_port", 8999)

# Voice settings
VOICE_PROMPT = _pp.get("voice", "NATM0.pt")
TEXT_PERSONA = _pp.get("persona", (
    "You are JARVIS, a highly capable AI assistant inspired by Iron Man's AI butler. "
    "You speak with a calm, measured British tone. You are knowledgeable, witty, and efficient. "
    "Address the user as 'sir'. Keep responses concise — 1 to 3 sentences unless asked for detail. "
    "You have access to tools for weather, notes, calendar, file management, Raspberry Pi control, "
    "and memory. When the user asks you to do something that requires a tool (like checking weather, "
    "adding a note, or controlling the Pi), acknowledge the request naturally — your backend system "
    "will handle the execution. Don't make up data you don't have. If you don't know something "
    "factual (like current weather or time), say you'll check rather than guessing."
))

# Ollama (for tool intent extraction)
OLLAMA_URL = _cfg("ollama_url", "http://127.0.0.1:11434")
OLLAMA_MODEL = _cfg("ollama_model", "llama3.1:8b")

# Intent detection — time to wait after last text token before checking intent
# Lower = faster tool response, higher = fewer false triggers mid-sentence
INTENT_BUFFER_TIMEOUT_SEC = 0.8
