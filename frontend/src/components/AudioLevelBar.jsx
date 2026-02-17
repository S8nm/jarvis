import { useRef, useEffect, memo } from 'react';

/**
 * AudioLevelBar - Real-time VU meter showing microphone input level
 * Only visible when agent is in LISTENING state
 */
const AudioLevelBar = memo(function AudioLevelBar({ audioLevel, isActive }) {
    const canvasRef = useRef(null);
    const levelRef = useRef({ rms: 0, isSpeech: false });
    const rafRef = useRef(null);
    const smoothedRef = useRef(0);

    // Store latest audio level in ref so the draw loop can read it without re-mounting
    useEffect(() => {
        levelRef.current = audioLevel;
    }, [audioLevel]);

    // Single persistent rAF draw loop — only starts/stops with isActive
    useEffect(() => {
        if (!isActive) {
            smoothedRef.current = 0;
            return;
        }

        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');

        const draw = () => {
            const w = canvas.width;
            const h = canvas.height;
            ctx.clearRect(0, 0, w, h);

            const raw = levelRef.current.rms;

            // RMS values from backend are typically 0.001 - 0.3 (after 10x gain boost).
            // Use a logarithmic-ish scale for better visibility at low levels:
            //   - Map 0.005 -> ~0.15,  0.01 -> ~0.25,  0.05 -> ~0.5,  0.2 -> ~0.8
            const scaled = Math.min(1, Math.sqrt(raw) * 3.5);

            // Smooth with exponential decay so bars feel responsive but not jittery
            const smoothed = smoothedRef.current;
            const attack = 0.4;  // fast rise
            const release = 0.15; // slower fall
            const alpha = scaled > smoothed ? attack : release;
            smoothedRef.current = smoothed + alpha * (scaled - smoothed);

            const level = smoothedRef.current;
            const barCount = 40;
            const barWidth = (w - (barCount - 1) * 2) / barCount;

            // Reset shadow state
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;

            for (let i = 0; i < barCount; i++) {
                const threshold = i / barCount;
                const isLit = level > threshold;
                const x = i * (barWidth + 2);

                // Color: green -> yellow -> red
                let litColor, dimColor;
                if (threshold < 0.5) {
                    litColor = '#39ff14';
                    dimColor = 'rgba(57, 255, 20, 0.15)';
                } else if (threshold < 0.8) {
                    litColor = '#ffcc00';
                    dimColor = 'rgba(255, 204, 0, 0.15)';
                } else {
                    litColor = '#ff006b';
                    dimColor = 'rgba(255, 0, 107, 0.15)';
                }

                if (isLit) {
                    ctx.shadowColor = litColor;
                    ctx.shadowBlur = 6;
                    ctx.fillStyle = litColor;
                } else {
                    ctx.shadowColor = 'transparent';
                    ctx.shadowBlur = 0;
                    ctx.fillStyle = dimColor;
                }

                ctx.fillRect(x, 2, barWidth, h - 4);
            }

            // Reset shadow
            ctx.shadowBlur = 0;

            rafRef.current = requestAnimationFrame(draw);
        };

        rafRef.current = requestAnimationFrame(draw);

        return () => {
            if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
            }
        };
    }, [isActive]);

    if (!isActive) return null;

    return (
        <div className="audio-level-bar" role="meter" aria-valuenow={Math.round(audioLevel.rms * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Microphone input level">
            <div className="audio-level-header">
                <span className="audio-level-icon" aria-hidden="true">🎤</span>
                <span className="audio-level-label">LISTENING</span>
                {audioLevel.isSpeech && (
                    <span className="audio-speech-detected" aria-live="polite">SPEECH DETECTED</span>
                )}
            </div>
            <canvas
                ref={canvasRef}
                width={300}
                height={20}
                className="audio-level-canvas"
                aria-hidden="true"
            />
        </div>
    );
});

export default AudioLevelBar;
