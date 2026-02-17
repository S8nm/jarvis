import React, { useRef, useEffect, useMemo } from 'react';

const STATES = {
    IDLE:      { color: [0, 170, 255], pulse: 0.5,  particles: 20, speed: 0.3, label: '● STANDBY' },
    LISTENING: { color: [57, 255, 20],  pulse: 1.2,  particles: 40, speed: 0.8, label: '◉ LISTENING' },
    THINKING:  { color: [255, 204, 0],  pulse: 2.0,  particles: 60, speed: 1.5, label: '⟳ PROCESSING' },
    EXECUTING: { color: [255, 150, 0],  pulse: 1.5,  particles: 50, speed: 1.2, label: '▶ EXECUTING' },
    SPEAKING:  { color: [180, 0, 255],  pulse: 1.0,  particles: 35, speed: 0.6, label: '◈ SPEAKING' },
    ERROR:     { color: [255, 0, 50],   pulse: 3.0,  particles: 15, speed: 0.2, label: '✕ ERROR' },
};

/**
 * Canvas-based animated JARVIS orb with particles, rings, and waveform.
 */
export default function Orb({ state = 'IDLE' }) {
    const canvasRef = useRef(null);
    const stateRef = useRef(state);
    const particlesRef = useRef([]);
    const timeRef = useRef(0);
    const rafRef = useRef(null);

    const config = STATES[state] || STATES.IDLE;

    useEffect(() => {
        stateRef.current = state;
    }, [state]);

    // Initialize particles
    useEffect(() => {
        const particles = [];
        for (let i = 0; i < 80; i++) {
            particles.push({
                angle: Math.random() * Math.PI * 2,
                radius: 40 + Math.random() * 80,
                baseRadius: 40 + Math.random() * 80,
                speed: (0.2 + Math.random() * 0.5) * (Math.random() > 0.5 ? 1 : -1),
                size: 1 + Math.random() * 2.5,
                opacity: 0.3 + Math.random() * 0.7,
                phase: Math.random() * Math.PI * 2,
                drift: Math.random() * 0.3,
            });
        }
        particlesRef.current = particles;
    }, []);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const size = 300;
        canvas.width = size * 2;  // Hi-DPI
        canvas.height = size * 2;
        ctx.scale(2, 2);
        const cx = size / 2;
        const cy = size / 2;

        const draw = () => {
            const t = timeRef.current;
            const st = STATES[stateRef.current] || STATES.IDLE;
            const [r, g, b] = st.color;

            ctx.clearRect(0, 0, size, size);

            // === Outer glow ===
            const glowSize = 120 + Math.sin(t * st.pulse) * 15;
            const glow = ctx.createRadialGradient(cx, cy, 30, cx, cy, glowSize);
            glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0.15)`);
            glow.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, 0.05)`);
            glow.addColorStop(1, 'transparent');
            ctx.fillStyle = glow;
            ctx.fillRect(0, 0, size, size);

            // === Rotating rings ===
            ctx.save();
            ctx.translate(cx, cy);

            // Ring 1 — outer dashed
            ctx.save();
            ctx.rotate(t * 0.2 * st.speed);
            ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, 0.25)`;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([8, 12]);
            ctx.beginPath();
            ctx.arc(0, 0, 110, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.restore();

            // Ring 2 — segmented arc
            ctx.save();
            ctx.rotate(-t * 0.35 * st.speed);
            const segments = 8;
            for (let i = 0; i < segments; i++) {
                const a = (i / segments) * Math.PI * 2;
                const gap = 0.08;
                const arcLen = (Math.PI * 2 / segments) - gap;
                const brightness = 0.3 + 0.3 * Math.sin(t * 2 + i);
                ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${brightness})`;
                ctx.lineWidth = 3;
                ctx.beginPath();
                ctx.arc(0, 0, 90, a, a + arcLen);
                ctx.stroke();
            }
            ctx.restore();

            // Ring 3 — inner solid with notches
            ctx.save();
            ctx.rotate(t * 0.5 * st.speed);
            ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, 0.5)`;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(0, 0, 65, 0, Math.PI * 2);
            ctx.stroke();

            // Notch markers
            for (let i = 0; i < 12; i++) {
                const a = (i / 12) * Math.PI * 2;
                const inner = 60;
                const outer = 70;
                ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${i % 3 === 0 ? 0.8 : 0.3})`;
                ctx.lineWidth = i % 3 === 0 ? 2 : 1;
                ctx.beginPath();
                ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
                ctx.lineTo(Math.cos(a) * outer, Math.sin(a) * outer);
                ctx.stroke();
            }
            ctx.restore();

            // === Waveform ring (voice reactive) ===
            ctx.save();
            ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, 0.6)`;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            const wavePoints = 64;
            for (let i = 0; i <= wavePoints; i++) {
                const a = (i / wavePoints) * Math.PI * 2;
                const wave = Math.sin(a * 6 + t * 3 * st.speed) * (4 + st.pulse * 3)
                           + Math.sin(a * 3 - t * 2) * 2;
                const wr = 78 + wave;
                const x = Math.cos(a) * wr;
                const y = Math.sin(a) * wr;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.closePath();
            ctx.stroke();
            ctx.restore();

            // === Particles ===
            const particles = particlesRef.current;
            const activeCount = st.particles;
            for (let i = 0; i < particles.length; i++) {
                const p = particles[i];
                if (i >= activeCount) continue;

                p.angle += p.speed * st.speed * 0.01;
                const breathe = Math.sin(t * p.drift + p.phase) * 10;
                const pr = p.baseRadius + breathe;

                const px = Math.cos(p.angle) * pr;
                const py = Math.sin(p.angle) * pr;

                const alpha = p.opacity * (0.5 + 0.5 * Math.sin(t * 1.5 + p.phase));
                ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
                ctx.beginPath();
                ctx.arc(px, py, p.size, 0, Math.PI * 2);
                ctx.fill();
            }

            // === Core ===
            const coreSize = 32 + Math.sin(t * st.pulse) * 4;
            const coreGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, coreSize);
            coreGrad.addColorStop(0, `rgba(255, 255, 255, 0.95)`);
            coreGrad.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, 0.8)`);
            coreGrad.addColorStop(0.7, `rgba(${r}, ${g}, ${b}, 0.3)`);
            coreGrad.addColorStop(1, 'transparent');
            ctx.fillStyle = coreGrad;
            ctx.beginPath();
            ctx.arc(0, 0, coreSize, 0, Math.PI * 2);
            ctx.fill();

            // Core inner white dot
            ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
            ctx.beginPath();
            ctx.arc(0, 0, 6, 0, Math.PI * 2);
            ctx.fill();

            ctx.restore();

            timeRef.current += 0.016; // ~60fps
            rafRef.current = requestAnimationFrame(draw);
        };

        rafRef.current = requestAnimationFrame(draw);
        return () => {
            if (rafRef.current) cancelAnimationFrame(rafRef.current);
        };
    }, []);

    return (
        <div className="orb-container">
            <canvas
                ref={canvasRef}
                style={{ width: 300, height: 300 }}
            />
            <div className="orb-state-label">
                <span className={`state-text ${state.toLowerCase()}`}>{config.label}</span>
            </div>
        </div>
    );
}
