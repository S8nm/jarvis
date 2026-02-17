import cv2
import numpy as np
import base64
import logging
from ultralytics import YOLO
from pathlib import Path

logger = logging.getLogger("jarvis.vision")

class ObjectDetector:
    def __init__(self, model_name="yolov8n.pt"):
        self.model_path = Path(__file__).parent.parent.parent / "models" / model_name
        # Ensure models directory exists
        self.model_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Loading YOLO model: {model_name}")
        try:
            self.model = YOLO(str(self.model_path) if self.model_path.exists() else model_name)
            logger.info("YOLO model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            self.model = None

    def detect_objects(self, image_data_b64):
        if not self.model:
            return []

        try:
            # Decode base64 image
            if "," in image_data_b64:
                image_data_b64 = image_data_b64.split(",")[1]

            img_bytes = base64.b64decode(image_data_b64)
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None:
                return []

            h, w = frame.shape[:2]

            # Run inference
            results = self.model(frame, verbose=False)

            detections = []
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    label = self.model.names[cls]

                    if conf > 0.55:  # Higher threshold = fewer false positives
                        # Normalize to 0-1 range so frontend can scale to any container size
                        detections.append({
                            "label": label,
                            "confidence": round(conf, 2),
                            "box": [
                                round(x1 / w, 4),
                                round(y1 / h, 4),
                                round(x2 / w, 4),
                                round(y2 / h, 4)
                            ]
                        })

            return detections

        except Exception as e:
            logger.error(f"Detection error: {e}")
            return []
