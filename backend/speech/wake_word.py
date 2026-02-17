"""
Jarvis Protocol — Wake Word Detection
Uses openWakeWord for local, always-on wake word detection.
Listens for "Hey Jarvis" / "Jarvis" to trigger listening state.
"""
import asyncio
import logging
import numpy as np
import queue
import threading
from typing import Callable, Optional

logger = logging.getLogger("jarvis.wake")

# Lazy imports to avoid issues if not installed
_oww = None
_sd = None


def _ensure_imports():
    global _oww, _sd
    if _oww is None:
        try:
            import openwakeword
            from openwakeword.model import Model as OWWModel
            _oww = OWWModel
            openwakeword.utils.download_models()
        except ImportError:
            logger.warning("openwakeword not installed, wake word detection disabled")
            _oww = False
    if _sd is None:
        try:
            import sounddevice
            _sd = sounddevice
        except ImportError:
            logger.warning("sounddevice not installed, audio capture disabled")
            _sd = False


class WakeWordDetector:
    """
    Continuously monitors the microphone for the wake word.
    Runs in a background thread to avoid blocking the async event loop.
    """

    def __init__(self, sensitivity: float = 0.5, on_wake: Optional[Callable] = None):
        self.sensitivity = sensitivity
        self.on_wake = on_wake
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._model = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def initialize(self):
        """Load the wake word model."""
        _ensure_imports()
        if _oww and _oww is not False:
            try:
                self._model = _oww(
                    wakeword_models=["hey_jarvis"],
                    inference_framework="onnx"
                )
                logger.info("Wake word model loaded: hey_jarvis")
            except Exception as e:
                logger.warning(f"Could not load hey_jarvis model, using default: {e}")
                try:
                    self._model = _oww(inference_framework="onnx")
                    logger.info("Wake word model loaded with defaults")
                except Exception as e2:
                    logger.error(f"Failed to load any wake word model: {e2}")
                    self._model = None
        else:
            logger.warning("Wake word detection unavailable — will use text-only mode")

    def start(self, loop: asyncio.AbstractEventLoop):
        """Start listening for the wake word in a background thread."""
        if self._running:
            return
        self._loop = loop
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Wake word detector started")

    def stop(self):
        """Stop listening and wait for the audio stream to fully close."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)  # Longer timeout for Windows audio device release
            self._thread = None
        logger.info("Wake word detector stopped")

    def _listen_loop(self):
        """Background thread: continuously read mic and check for wake word."""
        _ensure_imports()
        if not _sd or _sd is False or not self._model:
            logger.warning("Cannot start audio capture — dependencies missing")
            return

        # Log available audio devices
        try:
            devices = _sd.query_devices()
            logger.info("Available audio devices:")
            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    logger.info(f"  [{i}] {dev['name']} (inputs: {dev['max_input_channels']})")
            default_device = _sd.query_devices(kind='input')
            logger.info(f"Using default input device: {default_device['name']}")
        except Exception as e:
            logger.warning(f"Could not query audio devices: {e}")

        chunk_samples = 1280  # ~80ms at 16kHz
        audio_queue = queue.Queue()
        chunk_count = 0

        def audio_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            audio_queue.put(indata[:, 0].copy())

        try:
            from config import MIC_DEVICE
            with _sd.InputStream(
                device=MIC_DEVICE,
                samplerate=16000,
                channels=1,
                dtype="float32",
                blocksize=chunk_samples,
                callback=audio_callback
            ):
                logger.info(f"Microphone stream opened for wake word detection (device={MIC_DEVICE or 'default'})")
                logger.info(f"Wake word sensitivity threshold: {self.sensitivity}")

                while self._running:
                    try:
                        audio_chunk = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    chunk_count += 1

                    # Calculate RMS before gain boost
                    rms_raw = np.sqrt(np.mean(audio_chunk ** 2))

                    # Software gain boost (increased for low-volume mics)
                    audio_chunk = audio_chunk * 10.0

                    # Calculate RMS after gain boost
                    rms_boosted = np.sqrt(np.mean(audio_chunk ** 2))

                    # Log audio levels every 50 chunks (~4 seconds)
                    if chunk_count % 50 == 0:
                        logger.info(f"Audio levels - Raw RMS: {rms_raw:.6f}, Boosted RMS: {rms_boosted:.6f}")

                    # Feed to model
                    audio_int16 = (np.clip(audio_chunk, -1.0, 1.0) * 32767).astype(np.int16)
                    self._model.predict(audio_int16)

                    # Check predictions
                    for model_name, score in self._model.prediction_buffer.items():
                        latest_score = score[-1] if len(score) > 0 else 0
                        # Log high scores even if below threshold
                        if latest_score > 0.1:
                            logger.debug(f"Model: {model_name}, Score: {latest_score:.3f}")
                        if latest_score > self.sensitivity:
                            logger.info(f"Wake word detected! Model: {model_name}, Score: {latest_score:.2f}")
                            self._model.reset()
                            if self.on_wake and self._loop:
                                self._loop.call_soon_threadsafe(
                                    asyncio.ensure_future,
                                    self._async_on_wake()
                                )
        except Exception as e:
            logger.error(f"Wake word listener error: {e}", exc_info=True)

    async def _async_on_wake(self):
        """Async wrapper for the wake callback."""
        if self.on_wake:
            if asyncio.iscoroutinefunction(self.on_wake):
                await self.on_wake()
            else:
                self.on_wake()
