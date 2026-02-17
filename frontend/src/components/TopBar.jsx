import { memo } from 'react';
import { useConnection, useSystem } from '../contexts/JarvisContext';
import GlitchText from './GlitchText';
import CountUp from './CountUp';

const TopBar = memo(function TopBar() {
    const { agentState } = useConnection();
    const { status } = useSystem();

    const sys = status?.system || {};
    const gpu = status?.system?.gpu || {};

    return (
        <div className="hud-top-bar" role="banner">
            <div className="hud-brand">
                <span className="brand-icon" aria-hidden="true">◈</span>
                <GlitchText speed={0.7} enableShadows enableOnHover>
                    J.A.R.V.I.S.
                </GlitchText>
                <span className="brand-version">v4.7</span>
            </div>
            <div className="hud-quick-stats" aria-label="System metrics">
                <div className="quick-stat">
                    <span className="stat-label">STATE</span>
                    <span className={`stat-value state-${agentState.toLowerCase()}`}>{agentState}</span>
                </div>
                <div className="quick-stat">
                    <span className="stat-label">CPU</span>
                    <span className="stat-value">
                        <CountUp to={Math.round(sys.cpu_percent || 0)} duration={1} suffix="%" />
                    </span>
                </div>
                <div className="quick-stat">
                    <span className="stat-label">MEM</span>
                    <span className="stat-value">
                        <CountUp to={Math.round(sys.memory_percent || 0)} duration={1} suffix="%" />
                    </span>
                </div>
                <div className="quick-stat">
                    <span className="stat-label">GPU</span>
                    <span className="stat-value">
                        {gpu.name
                            ? <CountUp to={Math.round(gpu.utilization || 0)} duration={1} suffix="%" />
                            : 'N/A'}
                    </span>
                </div>
                <div className="quick-stat">
                    <span className="stat-label">VRAM</span>
                    <span className="stat-value">
                        {gpu.vram_used_gb
                            ? <><CountUp to={gpu.vram_used_gb} duration={1} decimals={1} />/{gpu.vram_total_gb?.toFixed(0)}G</>
                            : 'N/A'}
                    </span>
                </div>
                <div className="quick-stat">
                    <span className="stat-label">LLM</span>
                    <span className="stat-value" style={{ color: status?.ollama_connected ? 'var(--neon-green)' : 'var(--accent-alert)' }}>
                        {status?.ollama_connected ? 'ONLINE' : 'OFFLINE'}
                    </span>
                </div>
            </div>
        </div>
    );
});

export default TopBar;
