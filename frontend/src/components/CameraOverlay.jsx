import { useRef, useEffect, useState, memo } from 'react';
import Webcam from 'react-webcam';
import { useActions } from '../contexts/JarvisContext';

const videoConstraints = {
    width: 640,
    height: 480,
    facingMode: "user"
};

/**
 * Camera PiP Overlay with AI Object Detection.
 * Floats as a picture-in-picture panel over the HUD.
 * Bounding box coords are normalized 0-1 from the backend.
 */
const CameraOverlay = memo(function CameraOverlay({ isActive, detections }) {
    const { sendMessage } = useActions();
    const webcamRef = useRef(null);
    const videoRef = useRef(null);
    const [videoSize, setVideoSize] = useState({ width: 0, height: 0 });

    useEffect(() => {
        const checkSize = () => {
            const video = webcamRef.current?.video;
            if (video && video.videoWidth) {
                setVideoSize({
                    width: video.clientWidth,
                    height: video.clientHeight
                });
            }
        };

        const interval = setInterval(checkSize, 500);
        checkSize();
        return () => clearInterval(interval);
    }, [isActive]);

    useEffect(() => {
        let interval;
        if (isActive) {
            interval = setInterval(() => {
                try {
                    const imageSrc = webcamRef.current?.getScreenshot();
                    if (imageSrc) {
                        sendMessage('object_detection_frame', { image: imageSrc });
                    }
                } catch (e) {
                    console.warn("Camera capture failed", e);
                }
            }, 300);
        }
        return () => clearInterval(interval);
    }, [isActive, sendMessage]);

    if (!isActive) return null;

    return (
        <div className="camera-pip" aria-label="Live camera feed">
            <div className="camera-pip-header">
                <span className="camera-pip-dot" aria-hidden="true" />
                <span>LIVE FEED</span>
                <span className="camera-pip-count">
                    {detections.length > 0 ? `${detections.length} DETECTED` : 'SCANNING'}
                </span>
            </div>

            <div className="camera-pip-video" ref={videoRef}>
                <Webcam
                    audio={false}
                    ref={webcamRef}
                    screenshotFormat="image/jpeg"
                    videoConstraints={videoConstraints}
                    style={{
                        width: '100%',
                        height: '100%',
                        objectFit: 'contain',
                        display: 'block'
                    }}
                />

                {detections.map((d, i) => {
                    const [nx1, ny1, nx2, ny2] = d.box;
                    const left = nx1 * 100;
                    const top = ny1 * 100;
                    const width = (nx2 - nx1) * 100;
                    const height = (ny2 - ny1) * 100;

                    return (
                        <div
                            key={`${d.label}-${i}`}
                            className="detection-box"
                            style={{
                                position: 'absolute',
                                left: `${left}%`,
                                top: `${top}%`,
                                width: `${width}%`,
                                height: `${height}%`,
                            }}
                        >
                            <div className="detection-label">
                                {d.label.toUpperCase()} {Math.round(d.confidence * 100)}%
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
});

export default CameraOverlay;
