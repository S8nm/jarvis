"""
JARVIS Pi Tool — PicoClaw Agent
Send natural language commands to PicoClaw running on the Pi.
PicoClaw can execute shell commands, manage files, search the web, and schedule tasks.

Supports provider selection and automatic fallback:
  groq → cerebras → gemini → nvidia → github → ollama (local) → openai (paid)
"""
import subprocess
import json
import copy
import os
from pathlib import Path

# Ollama runs on the PC (JARVIS brain) and is accessible on LAN
OLLAMA_PC_URL = os.environ.get("OLLAMA_PC_URL", "http://192.168.8.225:11434/v1")
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
CEREBRAS_API_URL = "https://api.cerebras.ai/v1"
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
GITHUB_API_URL = "https://models.inference.ai.azure.com"
GITHUB_API_KEY = os.environ.get("GITHUB_API_KEY", "")

# Provider configs: model + which PicoClaw provider key to use
# "ollama" is routed through the "openai" provider with a swapped api_base
PROVIDERS = {
    "groq":            {"model": "llama-3.1-8b-instant",       "picoclaw_provider": "groq"},
    "cerebras":        {"model": "gpt-oss-120b",               "picoclaw_provider": "openai",
                        "api_base_override": CEREBRAS_API_URL, "api_key_override": CEREBRAS_API_KEY},
    "gemini":          {"model": "gemini-2.0-flash",           "picoclaw_provider": "gemini"},
    "nvidia":          {"model": "meta/llama-3.1-8b-instruct", "picoclaw_provider": "openai",
                        "api_base_override": NVIDIA_API_URL, "api_key_override": NVIDIA_API_KEY},
    "github":          {"model": "Meta-Llama-3.1-8B-Instruct", "picoclaw_provider": "openai",
                        "api_base_override": GITHUB_API_URL, "api_key_override": GITHUB_API_KEY},
    "ollama":          {"model": "llama3.1:8b",                "picoclaw_provider": "openai",
                        "api_base_override": OLLAMA_PC_URL, "api_key_override": "ollama"},
    "openai":          {"model": "gpt-4o-mini",                "picoclaw_provider": "openai"},
    "openai-advanced": {"model": "gpt-4o",                     "picoclaw_provider": "openai"},
}

FALLBACK_CHAIN = ["groq", "cerebras", "gemini", "nvidia", "github", "ollama", "openai"]

CONFIG_PATH = Path.home() / ".picoclaw" / "config.json"

# Cached original OpenAI provider config (for restore after Ollama swap)
_original_openai_provider = None


def _load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def _save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)


def _set_provider(cfg: dict, provider_key: str) -> dict:
    """Update config to use a specific provider and model.
    For Ollama: temporarily swaps the OpenAI provider's endpoint to Ollama's."""
    global _original_openai_provider

    prov = PROVIDERS.get(provider_key, PROVIDERS["groq"])
    cfg["agents"]["defaults"]["model"] = prov["model"]
    cfg["agents"]["defaults"]["provider"] = prov["picoclaw_provider"]

    # If this provider needs an api_base override (i.e. Ollama via OpenAI slot)
    if "api_base_override" in prov:
        # Save original OpenAI config before swapping
        if _original_openai_provider is None and "openai" in cfg.get("providers", {}):
            _original_openai_provider = copy.deepcopy(cfg["providers"]["openai"])

        cfg.setdefault("providers", {})["openai"] = {
            "api_key": prov.get("api_key_override", "ollama"),
            "api_base": prov["api_base_override"],
        }
    else:
        # Restore original OpenAI config if we previously swapped it
        if _original_openai_provider is not None and prov["picoclaw_provider"] == "openai":
            cfg.setdefault("providers", {})["openai"] = copy.deepcopy(_original_openai_provider)

    return cfg


def _restore_config(cfg: dict, original_model: str, original_provider: str):
    """Restore the config to its original state."""
    global _original_openai_provider
    cfg["agents"]["defaults"]["model"] = original_model
    cfg["agents"]["defaults"]["provider"] = original_provider
    if _original_openai_provider is not None:
        cfg.setdefault("providers", {})["openai"] = copy.deepcopy(_original_openai_provider)
        _original_openai_provider = None
    _save_config(cfg)


def _run_picoclaw(message: str, timeout: int) -> tuple:
    """Run PicoClaw and return (stdout, stderr, returncode)."""
    proc = subprocess.run(
        ["picoclaw", "agent", "-m", message],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def _is_rate_limited(output: str, stderr: str) -> bool:
    """Check if the response indicates a rate limit error."""
    combined = (output + stderr).lower()
    return "rate_limit" in combined or "rate limit" in combined or "429" in combined


def _is_provider_error(output: str, stderr: str) -> bool:
    """Check if the response indicates an auth, billing, or connection error."""
    combined = (output + stderr).lower()
    return any(k in combined for k in [
        "402", "401", "insufficient", "billing", "quota",
        "connection refused", "connect error", "timed out",
        "not a valid model", "model_not_found",
    ])


def run(args: dict) -> dict:
    """Execute a command through PicoClaw agent with provider fallback.

    Args:
        message (str): Natural language instruction for PicoClaw
        provider (str): Provider to use: "groq" (default), "cerebras", "gemini", "nvidia", "github", "ollama", "openai", "openai-advanced"
        timeout (int): Execution timeout in seconds (default: 60)
    """
    message = args.get("message", "").strip()
    if not message:
        raise ValueError("Missing required arg: message")

    timeout = min(int(args.get("timeout", 60)), 300)
    requested_provider = args.get("provider", "").strip().lower()

    cfg = _load_config()
    original_model = cfg["agents"]["defaults"].get("model", "")
    original_provider = cfg["agents"]["defaults"].get("provider", "")

    try:
        # If a specific provider was requested, use it directly (no fallback)
        if requested_provider and requested_provider in PROVIDERS:
            _set_provider(cfg, requested_provider)
            _save_config(cfg)
            output, stderr, rc = _run_picoclaw(message, timeout)
            return {
                "stdout": output[:4000],
                "data": {
                    "response": output[:4000],
                    "provider_used": requested_provider,
                    "exit_code": rc,
                    "stderr": stderr[:1000] if stderr else "",
                },
            }

        # Default: try fallback chain
        last_output = ""
        last_stderr = ""
        for provider_key in FALLBACK_CHAIN:
            _set_provider(cfg, provider_key)
            _save_config(cfg)

            try:
                output, stderr, rc = _run_picoclaw(message, timeout)
                last_output = output
                last_stderr = stderr
            except subprocess.TimeoutExpired:
                last_output = f"Timeout on {provider_key}"
                last_stderr = ""
                continue

            # Success (no rate limit / provider error)
            if not _is_rate_limited(output, stderr) and not _is_provider_error(output, stderr):
                return {
                    "stdout": output[:4000],
                    "data": {
                        "response": output[:4000],
                        "provider_used": provider_key,
                        "exit_code": rc,
                        "stderr": stderr[:1000] if stderr else "",
                    },
                }

            # Failed — try next provider
            continue

        # All providers failed
        return {
            "stdout": "All providers exhausted. Last error: " + last_output[:500],
            "data": {
                "response": "All providers failed",
                "provider_used": "none",
                "exit_code": 1,
                "stderr": last_stderr[:1000] if last_stderr else "",
            },
        }

    except FileNotFoundError:
        raise FileNotFoundError("PicoClaw not installed. Run: sudo wget -O /usr/local/bin/picoclaw ...")

    finally:
        # Restore original config so default stays as groq
        try:
            _restore_config(cfg, original_model, original_provider)
        except Exception:
            pass
