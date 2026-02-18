"""
Jarvis Protocol — Text-to-Speech
Uses Piper TTS for local, fast speech synthesis.
British male voice for JARVIS personality.

Improvements inspired by:
- Priler/jarvis: multi-backend fallback chain, voice packs
- sukeesh/Jarvis: graceful degradation pattern
"""
import asyncio
import io
import logging
import re
import wave
from typing import Optional

from config import PIPER_VOICE, PIPER_SPEAKER_ID, PIPER_SPEECH_RATE, SAMPLE_RATE

logger = logging.getLogger("jarvis.tts")


class TextToSpeech:
    """
    Local TTS with multi-tier fallback chain:
    1. Piper TTS (fast, high quality, local)
    2. Windows SAPI (built-in, decent quality)
    3. Silent mode (logs output, no audio)
    """

    def __init__(self):
        self._voice = None
        self._synthesize_fn = None
        self._is_speaking = False
        self._backend_name = "none"

    def initialize(self):
        """Load TTS with fallback chain."""
        # Try Piper first
        if self._try_init_piper():
            return

        # Fall back to Windows SAPI
        if self._try_init_sapi():
            return

        # Last resort: silent mode
        logger.warning("No TTS backend available — running in silent mode")
        self._synthesize_fn = self._synthesize_silent
        self._backend_name = "silent"

    def _try_init_piper(self) -> bool:
        """Try initializing Piper TTS."""
        try:
            import piper
            logger.info(f"Loading Piper voice: {PIPER_VOICE}")
            self._voice = piper.PiperVoice.load(
                PIPER_VOICE,
                config_path=None,
                use_cuda=True
            )
            self._synthesize_fn = self._synthesize_piper
            self._backend_name = "piper_cuda"
            logger.info("Piper TTS initialized with CUDA")
            return True
        except Exception as e:
            logger.info(f"Piper CUDA failed: {e}")

        # Try Piper without CUDA
        try:
            import piper
            self._voice = piper.PiperVoice.load(
                PIPER_VOICE,
                config_path=None,
                use_cuda=False
            )
            self._synthesize_fn = self._synthesize_piper
            self._backend_name = "piper_cpu"
            logger.info("Piper TTS initialized on CPU")
            return True
        except Exception as e:
            logger.info(f"Piper CPU also failed: {e}")
            return False

    def _try_init_sapi(self) -> bool:
        """Try initializing Windows SAPI."""
        try:
            import subprocess
            result = subprocess.run(
                ['powershell', '-Command',
                 'Add-Type -AssemblyName System.Speech; '
                 '$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                 '$synth.GetInstalledVoices().Count'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and int(result.stdout.strip()) > 0:
                self._synthesize_fn = self._synthesize_sapi
                self._backend_name = "windows_sapi"
                logger.info("Windows SAPI TTS initialized")
                return True
        except Exception as e:
            logger.info(f"Windows SAPI not available: {e}")
        return False

    async def speak(self, text: str) -> Optional[bytes]:
        """
        Synthesize text to speech and play it.
        Returns the audio bytes (WAV format) if available.
        """
        if not text or not text.strip():
            return None

        # Clean text for better TTS output
        clean_text = self._clean_for_speech(text)
        if not clean_text:
            return None

        self._is_speaking = True
        logger.info(f"Speaking ({self._backend_name}): {clean_text[:80]}...")

        try:
            loop = asyncio.get_running_loop()
            audio_bytes = await loop.run_in_executor(
                None, self._synthesize_fn, clean_text
            )

            if audio_bytes:
                # Play audio in background thread
                await loop.run_in_executor(None, self._play_audio, audio_bytes)

            return audio_bytes
        except Exception as e:
            logger.error(f"TTS error: {e}")
            return None
        finally:
            self._is_speaking = False

    def _clean_for_speech(self, text: str) -> str:
        """Clean text for more natural TTS output."""
        # Remove markdown formatting
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
        text = re.sub(r'`(.*?)`', r'\1', text)        # Inline code
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)  # Code blocks
        # Remove tool blocks
        text = re.sub(r'```tool\s*\n?.*?\n?\s*```', '', text, flags=re.DOTALL)
        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)
        # Clean whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _synthesize_piper(self, text: str) -> Optional[bytes]:
        """Synthesize using Piper TTS."""
        try:
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)  # 16-bit
                wav.setframerate(22050)

                audio_gen = self._voice.synthesize_stream_raw(text)
                for audio_chunk in audio_gen:
                    wav.writeframes(audio_chunk)

            return buf.getvalue()
        except Exception as e:
            logger.error(f"Piper synthesis failed: {e}")
            # Fall back to SAPI for this utterance
            return self._synthesize_sapi(text)

    def _synthesize_sapi(self, text: str) -> Optional[bytes]:
        """Synthesize using Windows SAPI (fallback)."""
        try:
            import subprocess
            safe_text = text[:500]
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$synth.SelectVoiceByHints('Male', 30, 0, 'en-GB'); "
                "$synth.Rate = 1; "
                "$text = [Console]::In.ReadToEnd(); "
                "$synth.Speak($text)"
            )
            subprocess.run(
                ['powershell', '-Command', script],
                input=safe_text, capture_output=True, text=True, timeout=30
            )
            logger.info("Spoke via Windows SAPI")
            return None
        except Exception as e:
            logger.warning(f"Windows SAPI failed: {e}")
            return self._synthesize_silent(text)

    def _synthesize_silent(self, text: str) -> Optional[bytes]:
        """Silent mode — just log the output."""
        logger.info(f"[TTS SILENT]: {text[:200]}")
        return None

    def _play_audio(self, audio_bytes: bytes):
        """Play WAV audio bytes through the default output device."""
        try:
            import sounddevice as sd
            import numpy as np

            buf = io.BytesIO(audio_bytes)
            with wave.open(buf, 'rb') as wav:
                rate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

            sd.play(audio, samplerate=rate)
            sd.wait()  # Block until playback finishes
        except Exception as e:
            logger.warning(f"Audio playback failed: {e}")

    def stop_speaking(self):
        """Interrupt current speech."""
        self._is_speaking = False
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def backend_name(self) -> str:
        return self._backend_name
