"""
Jarvis Protocol — Vision / Camera Tool
Captures frames from webcam and analyzes them using a local Vision-Language Model.
Camera is OFF by default — only activated on explicit user request.
"""
import asyncio
import base64
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from config import (
    CAMERA_DEVICE_ID, VISION_MODEL, VISION_RESOLUTION,
    OLLAMA_BASE_URL, OLLAMA_TIMEOUT, DATA_DIR
)

logger = logging.getLogger("jarvis.tools.vision")

# Lazy import
_cv2 = None


def _ensure_cv2():
    global _cv2
    if _cv2 is None:
        try:
            import cv2
            _cv2 = cv2
        except ImportError:
            logger.warning("OpenCV (cv2) not installed — vision disabled")
            _cv2 = False


class VisionTool:
    """Camera capture + VLM analysis."""

    def __init__(self):
        self._camera = None
        self._active = False

    def is_available(self) -> bool:
        _ensure_cv2()
        return _cv2 is not None and _cv2 is not False

    def activate_camera(self) -> bool:
        """Turn on the camera."""
        _ensure_cv2()
        if not _cv2 or _cv2 is False:
            logger.error("OpenCV not available")
            return False

        try:
            self._camera = _cv2.VideoCapture(CAMERA_DEVICE_ID)
            if not self._camera.isOpened():
                logger.error("Cannot open camera")
                return False

            self._camera.set(_cv2.CAP_PROP_FRAME_WIDTH, VISION_RESOLUTION[0])
            self._camera.set(_cv2.CAP_PROP_FRAME_HEIGHT, VISION_RESOLUTION[1])
            self._active = True
            logger.info("Camera activated")
            return True
        except Exception as e:
            logger.error(f"Camera activation failed: {e}")
            return False

    def deactivate_camera(self):
        """Turn off the camera."""
        if self._camera:
            self._camera.release()
            self._camera = None
        self._active = False
        logger.info("Camera deactivated")

    def capture_frame(self) -> Optional[bytes]:
        """Capture a single frame as JPEG bytes."""
        if not self._active or not self._camera:
            # Try to auto-activate
            if not self.activate_camera():
                return None

        _ensure_cv2()
        try:
            ret, frame = self._camera.read()
            if not ret:
                logger.error("Failed to read frame")
                self.deactivate_camera()
                return None

            # Encode as JPEG
            _, buffer = _cv2.imencode('.jpg', frame, [_cv2.IMWRITE_JPEG_QUALITY, 85])
            jpeg_bytes = buffer.tobytes()

            logger.info(f"Captured frame: {len(jpeg_bytes)} bytes")
            return jpeg_bytes
        except Exception as e:
            logger.error(f"Frame capture failed: {e}")
            self.deactivate_camera()
            return None

    def save_frame(self, jpeg_bytes: bytes, filename: Optional[str] = None) -> str:
        """Save a captured frame to disk."""
        captures_dir = DATA_DIR / "captures"
        captures_dir.mkdir(exist_ok=True)

        if not filename:
            filename = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

        path = captures_dir / filename
        path.write_bytes(jpeg_bytes)
        logger.info(f"Frame saved: {path}")
        return str(path)

    async def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str = "Describe what you see in this image in detail."
    ) -> str:
        """Analyze an image using the local Vision-Language Model via Ollama."""
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image]
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 512,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
                resp = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                    timeout=OLLAMA_TIMEOUT
                )

                if resp.status_code == 200:
                    data = resp.json()
                    analysis = data.get("message", {}).get("content", "")
                    logger.info(f"Vision analysis complete: {len(analysis)} chars")
                    return analysis
                else:
                    error = f"VLM request failed: HTTP {resp.status_code}"
                    logger.error(error)
                    return error

        except httpx.ConnectError:
            return "Vision model unavailable — Ollama may not be running or the vision model isn't pulled."
        except httpx.TimeoutException:
            return "Vision analysis timed out. The image may be too complex."
        except Exception as e:
            logger.error(f"Vision analysis error: {e}")
            return f"Vision analysis error: {str(e)}"

    async def capture_and_analyze(
        self,
        prompt: str = "Describe what you see in detail."
    ) -> dict:
        """Capture a frame and analyze it. Full pipeline."""
        frame = self.capture_frame()
        if not frame:
            return {
                "success": False,
                "error": "Failed to capture frame from camera"
            }

        try:
            # Save the frame
            saved_path = self.save_frame(frame)

            # Analyze
            analysis = await self.analyze_image(frame, prompt)

            return {
                "success": True,
                "analysis": analysis,
                "saved_path": saved_path,
                "image_size": len(frame)
            }
        finally:
            # Deactivate camera after use
            self.deactivate_camera()

    @property
    def is_active(self) -> bool:
        return self._active
