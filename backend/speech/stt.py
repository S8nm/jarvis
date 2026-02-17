"""
Jarvis Protocol — Speech-to-Text
Uses faster-whisper for local GPU-accelerated transcription.
Includes adaptive VAD with ring buffer pre-roll (inspired by Priler/jarvis).

Key improvements over naive approach:
- Ring buffer keeps 2s of pre-roll audio so speech onset is never clipped
- Adaptive silence threshold calibrates to ambient noise on startup
- Energy-based VAD pre-filters silence before expensive processing
"""
import asyncio
import collections
import logging
import numpy as np
import queue
import time
from dataclasses import dataclass
from typing import Optional

from config import (
    SAMPLE_RATE, MIC_DEVICE, WHISPER_MODEL_SIZE, WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE, WHISPER_LANGUAGE, WHISPER_BEAM_SIZE,
    SILENCE_LIMIT_SEC, MIN_UTTERANCE_SEC
)

logger = logging.getLogger("jarvis.stt")


@dataclass
class TranscriptionResult:
    text: str
    confidence: float
    language: str
    duration: float


class AudioRingBuffer:
    """
    Fixed-size ring buffer for audio pre-roll (inspired by Priler/jarvis).
    Keeps the last N seconds of audio so we never miss speech onset.
    """
    def __init__(self, max_seconds: float = 2.0, chunk_duration: float = 0.1):
        self._max_chunks = int(max_seconds / chunk_duration)
        self._buffer: collections.deque = collections.deque(maxlen=self._max_chunks)

    def append(self, chunk: np.ndarray):
        self._buffer.append(chunk.copy())

    def flush(self) -> list[np.ndarray]:
        """Return all buffered chunks and clear the buffer."""
        chunks = list(self._buffer)
        self._buffer.clear()
        return chunks

    def clear(self):
        self._buffer.clear()

    @property
    def size(self) -> int:
        return len(self._buffer)


class SpeechToText:
    """
    Records audio from microphone with VAD, then transcribes using faster-whisper.
    Features adaptive noise floor calibration and ring buffer pre-roll.
    """

    def __init__(self):
        self._model = None
        self._is_recording = False
        self._audio_level_callback = None
        self._ambient_noise_level: float = 0.02  # Will be calibrated on first use
        self._noise_calibrated: bool = False

    def set_audio_level_callback(self, callback):
        """Set a callback that receives audio levels during recording.
        callback(rms: float, is_speech: bool)"""
        self._audio_level_callback = callback

    def initialize(self):
        """Load the Whisper model onto GPU."""
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Loading Whisper model: {WHISPER_MODEL_SIZE} on {WHISPER_DEVICE}")
            self._model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE
            )
            logger.info("Whisper model loaded successfully")
        except ImportError:
            logger.warning("faster-whisper not installed — STT disabled")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            # Try CPU fallback
            try:
                from faster_whisper import WhisperModel
                logger.info("Attempting CPU fallback for Whisper...")
                self._model = WhisperModel(
                    "base",
                    device="cpu",
                    compute_type="int8"
                )
                logger.info("Whisper loaded on CPU (base model)")
            except Exception as e2:
                logger.error(f"CPU fallback also failed: {e2}")

    def calibrate_noise_floor(self, duration: float = 1.0) -> float:
        """
        Measure ambient noise level for adaptive threshold (inspired by Priler/jarvis).
        Records a short sample and sets the threshold above the noise floor.
        """
        try:
            import sounddevice as sd
        except ImportError:
            return self._ambient_noise_level

        chunk_samples = int(SAMPLE_RATE * 0.1)
        num_chunks = int(duration / 0.1)
        rms_values = []
        audio_queue = queue.Queue()

        def callback(indata, frames, time_info, status):
            audio_queue.put(indata[:, 0].copy())

        try:
            with sd.InputStream(
                device=MIC_DEVICE,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=chunk_samples,
                callback=callback
            ):
                for _ in range(num_chunks):
                    try:
                        chunk = audio_queue.get(timeout=0.5)
                        # Apply same gain as recording
                        boosted = chunk * 10.0
                        rms = np.sqrt(np.mean(boosted ** 2))
                        rms_values.append(rms)
                    except queue.Empty:
                        continue

            if rms_values:
                # Set threshold at 2.5x the ambient noise (headroom for reliable detection)
                avg_noise = np.mean(rms_values)
                p90_noise = np.percentile(rms_values, 90)
                self._ambient_noise_level = max(p90_noise * 2.5, avg_noise * 3.0, 0.01)
                self._noise_calibrated = True
                logger.info(
                    f"Noise floor calibrated — Avg: {avg_noise:.6f}, "
                    f"P90: {p90_noise:.6f}, Threshold set: {self._ambient_noise_level:.6f}"
                )
            return self._ambient_noise_level

        except Exception as e:
            logger.warning(f"Noise calibration failed: {e}")
            return self._ambient_noise_level

    async def record_and_transcribe(self) -> Optional[TranscriptionResult]:
        """
        Record audio until silence is detected, then transcribe.
        Returns TranscriptionResult or None if recording failed.
        """
        if not self._model:
            logger.error("Whisper model not loaded")
            return None

        # Calibrate noise floor on first use
        if not self._noise_calibrated:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.calibrate_noise_floor)

        # Record audio in a thread to avoid blocking
        loop = asyncio.get_running_loop()
        audio_data = await loop.run_in_executor(None, self._record_utterance)

        if audio_data is None or len(audio_data) < int(MIN_UTTERANCE_SEC * SAMPLE_RATE):
            logger.info("Recording too short, ignoring")
            return None

        # Transcribe in a thread
        result = await loop.run_in_executor(None, self._transcribe, audio_data)
        return result

    def _record_utterance(self) -> Optional[np.ndarray]:
        """
        Record from microphone until silence is detected.
        Uses adaptive energy-based VAD with ring buffer pre-roll.
        The ring buffer ensures we capture the beginning of speech
        even if VAD detection has a slight delay.
        """
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed")
            return None

        # Log audio device info
        try:
            default_device = sd.query_devices(kind='input')
            logger.info(f"Recording with device: {default_device['name']}")
        except Exception as e:
            logger.warning(f"Could not query audio device: {e}")

        self._is_recording = True
        logger.info("Recording started — listening for speech...")

        chunk_duration = 0.1  # 100ms chunks
        chunk_samples = int(SAMPLE_RATE * chunk_duration)

        # Adaptive threshold from calibration
        silence_threshold = self._ambient_noise_level
        max_silence_chunks = int(SILENCE_LIMIT_SEC / chunk_duration)
        max_record_seconds = 30
        max_chunks = int(max_record_seconds / chunk_duration)
        grace_period_chunks = int(3.0 / chunk_duration)  # 3 seconds grace (was 2)

        # Ring buffer: keeps 2s of pre-roll so we never clip speech onset
        ring_buffer = AudioRingBuffer(max_seconds=2.0, chunk_duration=chunk_duration)

        logger.info(
            f"Recording params — Threshold: {silence_threshold:.4f} "
            f"(calibrated={self._noise_calibrated}), "
            f"Max silence: {SILENCE_LIMIT_SEC}s, Grace: 3.0s"
        )

        chunks = []
        silence_chunks = 0
        speech_detected = False
        audio_queue = queue.Queue()
        # Track recent RMS for adaptive threshold adjustment during recording
        recent_rms = collections.deque(maxlen=50)

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            audio_queue.put(indata[:, 0].copy())

        try:
            with sd.InputStream(
                device=MIC_DEVICE,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=chunk_samples,
                callback=callback
            ):
                logger.info(f"Microphone stream opened (device={MIC_DEVICE or 'default'})")
                chunk_count = 0
                empty_count = 0

                for _ in range(max_chunks):
                    if not self._is_recording:
                        break

                    try:
                        chunk = audio_queue.get(timeout=0.5)
                        empty_count = 0
                    except queue.Empty:
                        empty_count += 1
                        if empty_count >= 10:
                            logger.error("No audio data from microphone — device may be unavailable")
                            break
                        continue

                    chunk_count += 1

                    # Software gain boost for low-volume mics
                    chunk_boosted = np.clip(chunk * 10.0, -1.0, 1.0)
                    rms = np.sqrt(np.mean(chunk_boosted ** 2))
                    recent_rms.append(rms)

                    # Broadcast audio level to frontend
                    if self._audio_level_callback:
                        try:
                            self._audio_level_callback(rms, speech_detected)
                        except Exception:
                            pass

                    # Log every ~1 second
                    if chunk_count % 10 == 0:
                        rms_raw = np.sqrt(np.mean(chunk ** 2))
                        logger.info(
                            f"Audio RMS — Raw: {rms_raw:.6f}, Boosted: {rms:.6f}, "
                            f"Threshold: {silence_threshold:.4f}, Speech: {speech_detected}"
                        )

                    if rms > silence_threshold:
                        if not speech_detected:
                            logger.info(f"Speech detected! RMS: {rms:.4f} > threshold: {silence_threshold:.4f}")
                            # Flush ring buffer pre-roll into chunks (captures speech onset)
                            pre_roll = ring_buffer.flush()
                            if pre_roll:
                                chunks.extend(pre_roll)
                                logger.info(f"Pre-roll: recovered {len(pre_roll)} chunks ({len(pre_roll)*chunk_duration:.1f}s)")
                        speech_detected = True
                        silence_chunks = 0
                        chunks.append(chunk_boosted)
                    elif speech_detected:
                        # Still recording but silence detected
                        silence_chunks += 1
                        chunks.append(chunk_boosted)  # Keep silence for natural speech
                        if silence_chunks >= max_silence_chunks:
                            logger.info("End of utterance detected (silence)")
                            break
                    else:
                        # No speech yet — buffer in ring buffer for pre-roll
                        ring_buffer.append(chunk_boosted)

                        if chunk_count > grace_period_chunks:
                            # Adapt threshold downward if we're not hearing anything
                            # This helps with quiet environments or quiet speakers
                            if len(recent_rms) >= 20:
                                current_noise = np.percentile(list(recent_rms), 90)
                                new_threshold = max(current_noise * 2.0, 0.008)
                                if new_threshold < silence_threshold * 0.8:
                                    logger.info(
                                        f"Adapting threshold down: {silence_threshold:.4f} -> {new_threshold:.4f}"
                                    )
                                    silence_threshold = new_threshold

                            if chunk_count > grace_period_chunks + 20:
                                logger.info("No speech detected after extended grace period")
                                break

        except Exception as e:
            logger.error(f"Recording error: {e}", exc_info=True)
            return None
        finally:
            self._is_recording = False

        if not chunks or not speech_detected:
            logger.warning(f"Recording ended — Chunks: {len(chunks)}, Speech: {speech_detected}")
            return None

        audio = np.concatenate(chunks)
        duration = len(audio) / SAMPLE_RATE
        logger.info(f"Recorded {duration:.1f}s of audio ({len(chunks)} chunks)")
        return audio

    def _transcribe(self, audio: np.ndarray) -> Optional[TranscriptionResult]:
        """Run Whisper transcription on audio data."""
        try:
            start_time = time.time()

            segments, info = self._model.transcribe(
                audio,
                language=WHISPER_LANGUAGE,
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=300,  # Increased from 200 for better boundary detection
                )
            )

            text_parts = []
            confidences = []
            for segment in segments:
                text_parts.append(segment.text.strip())
                confidences.append(segment.avg_logprob)

            full_text = " ".join(text_parts).strip()
            avg_confidence = np.mean(confidences) if confidences else -1.0
            confidence_score = min(1.0, max(0.0, 1.0 + avg_confidence))

            elapsed = time.time() - start_time
            logger.info(
                f"Transcribed in {elapsed:.1f}s: '{full_text}' "
                f"(confidence: {confidence_score:.2f})"
            )

            return TranscriptionResult(
                text=full_text,
                confidence=confidence_score,
                language=info.language,
                duration=info.duration
            )

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None

    def stop_recording(self):
        """Stop current recording."""
        self._is_recording = False
